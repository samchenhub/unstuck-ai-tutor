# Unstuck Phase 2 — Bilibili Source Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Unstuck import and play Bilibili videos (single videos and parts of multi-part courses) with the same experience as YouTube: instant playback, auto-pause on ask, current-second awareness, live frame, clickable timestamps.

**Architecture:** A new `unstuck/sources/` layer gives the pipeline, tutor and API one interface per video source. YouTube keeps its current module behind a thin adapter; Bilibili gets a client (WBI-signed web APIs, Chrome login cookies via yt-dlp) plus pure parsing functions. Bilibili videos play in the browser's `<video>` from a fresh 720p MP4 URL fetched through the backend; an optional last task adds 1080p via a backend stream proxy and dash.js.

**Tech Stack:** Python 3.12 / FastAPI / SQLite (existing), yt-dlp (cookies + downloads), React 19 + TS + Vite (existing), optional dash.js.

Spec: `docs/superpowers/specs/2026-10-10-unstuck-bilibili-design.md` (Chinese). Phase 1 code is on `main`.

## Global Constraints

- Supported Bilibili links: `bilibili.com/video/BV…`, `?p=N`, `b23.tv` short links, and a bare BV id. Paid courses (`/cheese/`), bangumi, collections, favourites and space pages are rejected with a clear Chinese message.
- Bilibili video id format: `{BVID}_p{N}` (BVID = `BV` + 10 alphanumerics), e.g. `BV1Ex4y1G7nG_p1`; single videos are `_p1`. YouTube ids stay 11 characters.
- One import = one part. Other parts are imported on demand from the parts list.
- Subtitles: prefer uploader Chinese subtitles, then `ai-zh`, fetched via the **WBI-signed** `x/player/wbi/v2` (never the unsigned `x/player/v2`, which returns other videos' subtitles). Reject subtitles that do not match the video (see Task 4) and fall back to Whisper.
- Login: read Chrome cookies through yt-dlp (`BILIBILI_COOKIES_BROWSER`, default `chrome`; empty = no login). Not logged in → keep working, degraded, and show the warning text: `B站未登录：画质较低、字幕将用语音转写（较慢）。在 Chrome 登录 bilibili.com 后刷新即可恢复。`
- Playback: 720p MP4 (`platform=html5&fnval=1`) played directly by `<video>`; the URL expires (~120 min) and the player refetches it on error and resumes at the same second. 1080p (Task 10) is optional and must silently fall back to 720p.
- Bilibili risk control (HTTP 412 / JSON code -352/-412): back off 2 s, 4 s, then `IngestError("BILIBILI_BLOCKED", "B站暂时限制了访问，请稍后重试")`.
- YouTube behaviour must not change; all Phase 1 tests keep passing.
- Unit tests never hit the network (fake fetchers / monkeypatching). Never read or print `backend/.env`.
- **Every task that runs into a non-obvious problem appends an entry to `docs/challenges.md`** (format defined in Task 1). User-facing copy is Chinese.

## File Structure

```
docs/challenges.md                         NEW  technical challenges log (Chinese)
backend/
  .env.example                             + BILIBILI_COOKIES_BROWSER
  unstuck/config.py                        + bilibili_cookies_browser
  unstuck/models.py                        Video: + source, course_id, part, part_title, parts_total, thumbnail
  unstuck/db/schema.py                     + VIDEO_COLUMNS_ADDED
  unstuck/db/__init__.py                   + _migrate_videos (idempotent ALTER TABLE)
  unstuck/db/repo.py                       insert_video(+source, course_id, part); new updatable fields
  unstuck/ingest/files.py                  NEW  save_info / load_info / find_video_file (moved from youtube.py, re-exported there)
  unstuck/ingest/stages.py                 use sources.for_video(...)
  unstuck/media.py                         extract_frame accepts a URL too
  unstuck/sources/__init__.py              NEW  resolve(url) / for_video / for_name / bilibili_status
  unstuck/sources/base.py                  NEW  VideoRef, VideoMeta, Playback, Part, Source protocol
  unstuck/sources/youtube.py               NEW  adapter over unstuck/ingest/youtube.py
  unstuck/sources/bilibili/__init__.py     NEW  (empty)
  unstuck/sources/bilibili/parse.py        NEW  pure: URLs, ids, WBI, subtitles, playurl, parts, meta
  unstuck/sources/bilibili/client.py       NEW  BiliClient: HTTP via yt-dlp cookies, backoff, signed calls, login check
  unstuck/sources/bilibili/source.py       NEW  BilibiliSource implementing Source
  unstuck/tutor/frame.py                   live frame from the online stream when no local file
  unstuck/api/app.py                       resolve via sources; + playback, parts, bilibili/status
  tests/test_migrations.py, test_sources.py, test_bili_parse.py, test_bili_client.py, test_bili_source.py   NEW
  tests/test_integration_bilibili.py       NEW  slow, real login
frontend/src/
  lib/videoId.ts (+ .test.ts)              NEW  route parsing, source detection, openVideo
  lib/library.ts (+ .test.ts)              NEW  group parts into course cards, thumbnails
  lib/api.ts                               + fields, Playback, Part, BilibiliStatus, api.playback/parts/bilibiliStatus
  components/playerHandle.ts               NEW  PlayerHandle type
  components/YouTubePlayer.tsx             NEW  (current Player.tsx body)
  components/BiliPlayer.tsx                NEW  <video> + URL refresh
  components/Player.tsx                    picks the player by id
  components/PartsList.tsx                 NEW  multi-part course list with import buttons
  components/Library.tsx, ImportView.tsx, VideoPage.tsx, App.tsx, index.css
```

Deviation from the spec, on purpose: `unstuck/ingest/youtube.py` stays where it is and `sources/youtube.py` is a thin adapter over it (the spec said "move"). Moving would break every Phase 1 YouTube test import for no behavioural gain; the adapter gives the same interface. Task 4 also tightens the spec's subtitle-mismatch rule (explained there).

---

### Task 1: Technical challenges log

**Files:**
- Create: `docs/challenges.md`

**Interfaces:** Produces the file every later task appends to.

- [ ] **Step 1: Create the file**

`docs/challenges.md`:

````markdown
# 技术难点记录

开发 Unstuck 时遇到的不明显的问题，以及怎么查出来、怎么解决的。每条格式：

- **问题**：现象是什么
- **原因**：根本原因，以及怎么定位的
- **解决**：最终方案
- **代码**：相关文件

---

## 第一阶段

### 1. 免费模型限流时，一个视频的导入会卡几个小时
- **问题**：智谱免费模型经常返回 429（"该模型当前访问量过大"）。画面理解阶段遇到持续限流时，每一帧要两分钟才失败，300 帧的视频可以卡住好几个小时，也无法停止。
- **原因**：两层重试叠加。模型客户端自己会退避重试约 62 秒，画面理解外面又包了一层重试，所以每帧失败两次才放弃。
- **解决**：客户端重试用尽后抛出 `ModelBusyError`；画面理解遇到它就立刻停止，剩下的帧保持"等待"状态，阶段以 `MODEL_BUSY` 结束，点"重试"只处理未完成的帧。提问路径（实时截帧、回答）只重试 2 次，避免用户看着"思考中…"等两分钟。
- **代码**：`backend/unstuck/llm/openai_compat.py`、`backend/unstuck/llm/retry.py`（`give_up_on`）、`backend/unstuck/ingest/vision.py`、`backend/unstuck/tutor/frame.py`

### 2. 流式回答在退避时仍占着并发名额
- **问题**：并发上限是 2。一个被限流的流式回答在退避等待时一直占着名额，导入任务的模型调用被饿死。
- **原因**：`with semaphore:` 包住了"带重试的创建流"，等待也在锁里。
- **解决**：名额只在"创建流"这一次请求里获取，失败立即释放；流创建成功后一直持有到流结束或被关闭（`finally` 释放）。用 `max_concurrency=1` 的测试证明退避期间名额是空闲的。
- **代码**：`backend/unstuck/llm/openai_compat.py`（`chat_stream`）

### 3. 模型输出的 JSON 里，LaTeX 被悄悄破坏
- **问题**：画面理解返回的公式 `\theta`、`\frac` 存进数据库后变成了制表符、换页符。
- **原因**：模型常常不转义反斜杠，而 `\t`、`\f`、`\b`、`\n`、`\r` 恰好是合法的 JSON 转义，严格解析不会报错，只会悄悄改掉内容。
- **解决**：解析前，对以 `b f n r t u` 开头的常见 LaTeX 命令（白名单）补一个反斜杠；提示词里要求转义；同时保留"先严格解析、再找代码块、最后用 json_repair 修复"的顺序，并且只从最外层的 `{` / `[` 开始解析，避免抓到字符串里的 `[0]` 这类片段。
- **代码**：`backend/unstuck/jsonfix.py`

### 4. 屏幕上的代码含 ``` 时，JSON 被截断
- **问题**：OCR 文字里有 Markdown 代码块时，整条画面描述解析失败。
- **原因**：先用正则找 ``` 代码块再解析，会从字符串内部的 ``` 开始截取。
- **解决**：先对整段文本做严格解析，失败后才找代码块，而且代码块必须从行首开始。
- **代码**：`backend/unstuck/jsonfix.py`

### 5. Safari 里用中文输入法选字时，按回车会把消息发出去
- **问题**：拼音选字按回车，半截消息被发送。
- **原因**：Safari 的事件顺序是先 `compositionend` 再 `keydown`，此时 `isComposing` 已经是 false。
- **解决**：自己记录 `compositionstart/compositionend` 状态，并且同时检查 `isComposing` 和 `keyCode === 229`。
- **代码**：`frontend/src/components/ChatPanel.tsx`

### 6. 回答中途断开，已经生成的内容丢失
- **问题**：浏览器关掉或网络断开时，已经流式输出的一半回答没有保存。
- **原因**：客户端断开时，Python 生成器收到的是 `GeneratorExit`，普通的 `except Exception` 捕获不到。
- **解决**：单独处理 `GeneratorExit`，把已生成的内容以"未完成"保存后再抛出；构造对话历史时去掉空回答和没有回答的提问，保证"用户/助手"严格交替。
- **代码**：`backend/unstuck/tutor/answer.py`

### 7. 用中文问英文视频，模型用英文回答
- **问题**：系统提示里写了"用提问的语言回答"，但上下文几乎都是英文时模型会跟着英文走。
- **解决**：根据问题里的文字（假名、韩文、汉字）在用户消息末尾追加一句明确的语言指令。
- **代码**：`backend/unstuck/tutor/answer.py`（`_language_instruction`）

### 8. 重启后嵌入模型被加载两次
- **问题**：重启时如果有导入在进行，bge-m3（约 2 GB）会加载两次，并发上限也失效。
- **原因**：导入线程、预热线程和请求线程同时第一次调用 `get_llm()`，各自构建了一个客户端。
- **解决**：双重检查加锁，保证只构建一次。
- **代码**：`backend/unstuck/api/app.py`

### 9. 评测里"字幕+画面"组可能和对照组完全一样
- **问题**：视频下载失败降级为"只用字幕"后，评测的"字幕+画面"组实际上也看不到画面，对比失去意义。
- **解决**：评测前检查每个视频必须是 `visual_mode == "full"`，否则拒绝运行。
- **代码**：`backend/unstuck/evaluation.py`、`eval/run_eval.py`

## 第二阶段：B站视频来源（调研阶段）

### 10. yt-dlp 拿不到 B站的 AI 字幕
- **问题**：已登录状态下，yt-dlp 返回的 `subtitles` 也是空的。
- **原因**：直接调 B站播放器接口发现视频其实有 `ai-zh` 字幕，只是 yt-dlp 没有取。
- **解决**：自己调用 B站接口获取字幕列表和字幕 JSON（`body: [{from, to, content}]`）。
- **代码**：`backend/unstuck/sources/bilibili/client.py`、`parse.py`

### 11. 旧接口返回了别的视频的字幕
- **问题**：导数课视频拿到的是"去京都吃米其林"的字幕，长度也对不上。
- **原因**：未签名的旧接口 `x/player/v2` 偶尔会返回其他视频的 AI 字幕。
- **解决**：改用带 WBI 签名的 `x/player/wbi/v2`（实测多次结果一致、内容正确），再加一道校验：字幕结束时间必须接近视频时长，否则丢弃并改用语音转写。
- **代码**：`backend/unstuck/sources/bilibili/client.py`、`parse.py`（`subtitle_matches`）

### 12. WBI 签名
- **问题**：新接口需要 `w_rid` 签名参数。
- **解决**：从 `nav` 接口拿 `img_key` 和 `sub_key`，按固定的打乱表取 32 位 mixin key；参数加 `wts`、按 key 排序、过滤 `!'()*` 后拼成查询串，再加上 mixin key 取 MD5。用公开的示例值（mixin key `ea1db124af3c7062474693fa704f4ff8`，`w_rid` `8f6f2b5b3d485fe1886cec6a0be8c5d4`）做单元测试。
- **代码**：`backend/unstuck/sources/bilibili/parse.py`

### 13. Chrome 里"看起来登录了"，接口却说没登录
- **问题**：Chrome 里有 15 个 B站 cookie，但 `nav` 接口返回 `isLogin: false`。
- **原因**：关键的 `SESSDATA` cookie 不存在（登录已过期），其他 cookie 只是访客信息。
- **解决**：一律用 `nav` 接口判断是否登录，而不是看有没有 cookie；未登录时提示用户在 Chrome 重新登录。
- **代码**：`backend/unstuck/sources/bilibili/client.py`（`is_logged_in`）

### 14. B站嵌入播放器拿不到当前时间
- **问题**：`player.bilibili.com` 没有公开的 JS 接口，无法读取当前秒数，也无法跳转。
- **解决**：通过 `x/player/wbi/playurl` 的 `platform=html5&fnval=1` 拿到带音频的 720p MP4 地址，直接用浏览器的 `<video>` 播放。实测 CDN 支持 Range 请求、不检查 Referer、地址约 120 分钟过期，过期后重新获取并回到原位置。
- **代码**：`backend/unstuck/sources/bilibili/parse.py`（`mp4_playback`）、`frontend/src/components/BiliPlayer.tsx`

### 15. 1080p 浏览器无法直接请求
- **问题**：1080p 只有 DASH 格式（音视频两路流），而且 CDN 要求 Referer 是 bilibili.com，否则返回 403；浏览器网页无法设置这个 Referer。
- **解决**：后端转发 Range 请求并带上 B站 Referer，前端用 dash.js 播放；任何失败都退回 720p。
- **代码**：`backend/unstuck/api/app.py`（stream 接口）、`frontend/src/components/BiliPlayer.tsx`

### 16. B站搜索接口很快被风控
- **问题**：连续几次搜索后返回 HTTP 412。
- **解决**：产品里不使用搜索；所有 B站请求遇到 412 都退避重试（2 秒、4 秒），仍失败则提示稍后重试。
- **代码**：`backend/unstuck/sources/bilibili/client.py`

## 第二阶段：B站视频来源（实现阶段）

（实现过程中遇到的新问题追加在这里。）
````

- [ ] **Step 2: Commit**

```bash
git add docs/challenges.md
git commit -m "docs: add technical challenges log"
```

---

### Task 2: Video source fields, DB migration and settings

**Files:**
- Modify: `backend/unstuck/models.py`, `backend/unstuck/db/schema.py`, `backend/unstuck/db/__init__.py`, `backend/unstuck/db/repo.py`, `backend/unstuck/config.py`, `backend/.env.example`
- Test: `backend/tests/test_migrations.py` (new), `backend/tests/test_config.py`

**Interfaces:**
- Produces: `Video` gains `source: str = "youtube"`, `course_id: str | None = None`, `part: int | None = None`, `part_title: str = ""`, `parts_total: int = 1`, `thumbnail: str = ""` (appended after `created_at`); `repo.insert_video(conn, video_id, url, *, source="youtube", course_id=None, part=None) -> bool`; `repo.update_video` also accepts `part_title`, `parts_total`, `thumbnail`; `Settings.bilibili_cookies_browser: str = "chrome"`; `db.schema.VIDEO_COLUMNS_ADDED: dict[str, str]`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_migrations.py`:

```python
import sqlite3

from unstuck.db import connect, repo

OLD_VIDEOS_SQL = """
CREATE TABLE videos (
    id TEXT PRIMARY KEY, url TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL DEFAULT '', duration REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'importing', ask_ready INTEGER NOT NULL DEFAULT 0,
    stage_states TEXT NOT NULL DEFAULT '{}', visual_mode TEXT NOT NULL DEFAULT 'full',
    outline TEXT, error_code TEXT, error_message TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def test_old_database_is_migrated(tmp_path):
    db = tmp_path / "old.db"
    raw = sqlite3.connect(db)
    raw.executescript(OLD_VIDEOS_SQL)
    raw.execute("INSERT INTO videos (id, url, title) VALUES ('dQw4w9WgXcQ', 'u', 'Old video')")
    raw.commit()
    raw.close()

    conn = connect(db, 64)
    video = repo.get_video(conn, "dQw4w9WgXcQ")
    assert video.title == "Old video"
    assert video.source == "youtube"
    assert video.course_id is None and video.part is None
    assert video.part_title == "" and video.parts_total == 1 and video.thumbnail == ""
    conn.close()


def test_migration_is_idempotent(tmp_path):
    db = tmp_path / "x.db"
    connect(db, 64).close()
    conn = connect(db, 64)
    names = [r["name"] for r in conn.execute("PRAGMA table_info(videos)")]
    assert names.count("source") == 1
    conn.close()


def test_insert_and_update_bilibili_video(conn):
    assert repo.insert_video(
        conn, "BV1Ex4y1G7nG_p2", "https://www.bilibili.com/video/BV1Ex4y1G7nG?p=2",
        source="bilibili", course_id="BV1Ex4y1G7nG", part=2,
    )
    repo.update_video(conn, "BV1Ex4y1G7nG_p2", part_title="极限", parts_total=7, thumbnail="https://i0.hdslb.com/a.jpg")
    video = repo.get_video(conn, "BV1Ex4y1G7nG_p2")
    assert (video.source, video.course_id, video.part) == ("bilibili", "BV1Ex4y1G7nG", 2)
    assert (video.part_title, video.parts_total, video.thumbnail) == ("极限", 7, "https://i0.hdslb.com/a.jpg")
```

Append to `backend/tests/test_config.py`:

```python
def test_bilibili_cookie_browser_setting(monkeypatch, tmp_path):
    monkeypatch.setenv("UNSTUCK_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("BILIBILI_COOKIES_BROWSER", raising=False)
    assert load_settings(env_file=False).bilibili_cookies_browser == "chrome"
    monkeypatch.setenv("BILIBILI_COOKIES_BROWSER", "")
    assert load_settings(env_file=False).bilibili_cookies_browser == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_migrations.py tests/test_config.py -q`
Expected: FAIL (`Video` has no attribute `source`, `insert_video` got an unexpected keyword, `Settings` has no `bilibili_cookies_browser`).

- [ ] **Step 3: Implement**

`backend/unstuck/models.py` — append to `Video` (after `created_at`):

```python
    source: str = "youtube"
    course_id: str | None = None
    part: int | None = None
    part_title: str = ""
    parts_total: int = 1
    thumbnail: str = ""
```

`backend/unstuck/db/schema.py` — add below `SCHEMA`:

```python
# Columns added after Phase 1. connect() adds any that are missing, so old databases keep working.
VIDEO_COLUMNS_ADDED = {
    "source": "TEXT NOT NULL DEFAULT 'youtube'",
    "course_id": "TEXT",
    "part": "INTEGER",
    "part_title": "TEXT NOT NULL DEFAULT ''",
    "parts_total": "INTEGER NOT NULL DEFAULT 1",
    "thumbnail": "TEXT NOT NULL DEFAULT ''",
}
```

`backend/unstuck/db/__init__.py` — import `VIDEO_COLUMNS_ADDED`, call `_migrate_videos(conn)` right after `conn.executescript(SCHEMA)`, and add:

```python
def _migrate_videos(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(videos)")}
    for name, ddl in VIDEO_COLUMNS_ADDED.items():
        if name in existing:
            continue
        try:
            conn.execute(f"ALTER TABLE videos ADD COLUMN {name} {ddl}")
        except sqlite3.OperationalError as exc:
            # Another connection may have added it a moment ago (startup races).
            if "duplicate column" not in str(exc):
                raise
```

`backend/unstuck/db/repo.py`:
- add `"part_title", "parts_total", "thumbnail"` to `_VIDEO_UPDATABLE`;
- in `_video(row)` add `source=row["source"], course_id=row["course_id"], part=row["part"], part_title=row["part_title"], parts_total=row["parts_total"], thumbnail=row["thumbnail"],`;
- replace `insert_video` with:

```python
def insert_video(
    conn: sqlite3.Connection, video_id: str, url: str, *,
    source: str = "youtube", course_id: str | None = None, part: int | None = None,
) -> bool:
    """Insert a new video row. Returns False if it already existed."""
    with conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO videos (id, url, source, course_id, part) VALUES (?, ?, ?, ?, ?)",
            (video_id, url, source, course_id, part),
        )
    return cur.rowcount == 1
```

`backend/unstuck/config.py` — add field `bilibili_cookies_browser: str = "chrome"` (after `llm_max_concurrency`) and in `load_settings`:

```python
        bilibili_cookies_browser=os.getenv("BILIBILI_COOKIES_BROWSER", "chrome").strip(),
```

`backend/.env.example` — append:

```dotenv
# Read the Bilibili login from this browser's cookies (chrome / safari / edge / firefox). Empty = no login.
BILIBILI_COOKIES_BROWSER=chrome
```

(`.env.example` is matched by the user's global gitignore; use `git add -f backend/.env.example`.)

- [ ] **Step 4: Run the full suite**

Run: `cd backend && uv run pytest -q`
Expected: all pass (previous 270 + new).

- [ ] **Step 5: Commit**

```bash
git add backend/unstuck backend/tests
git add -f backend/.env.example
git commit -m "feat(db): video source fields with idempotent migration"
```

---

### Task 3: Source abstraction (YouTube behind it)

**Files:**
- Create: `backend/unstuck/sources/__init__.py`, `backend/unstuck/sources/base.py`, `backend/unstuck/sources/youtube.py`, `backend/unstuck/ingest/files.py`
- Modify: `backend/unstuck/ingest/youtube.py` (move 3 helpers out, re-export), `backend/unstuck/ingest/stages.py`, `backend/unstuck/tutor/frame.py`, `backend/unstuck/api/app.py`
- Test: `backend/tests/test_sources.py` (new), `backend/tests/test_stages.py`

**Interfaces:**
- Consumes: Task 2 fields.
- Produces:
  - `sources.base`: `VideoRef(source, video_id, url, course_id=None, part=None)`, `VideoMeta(title, channel, duration, part_title="", parts_total=1, thumbnail="")`, `Playback(kind, url="", expires_at=0.0, duration=0.0)`, `Part(part, title, duration, video_id)`, `Source` protocol: `name`, `fetch_info(video_id) -> dict`, `check_info(info, max_duration_s) -> VideoMeta`, `fetch_captions(info) -> list[Cue] | None`, `download_video(video_id, out_dir) -> Path`, `download_audio(video_id, out_dir) -> Path`, `playback(video_id, quality=720) -> Playback`, `parts(video_id) -> list[Part]`.
  - `sources.resolve(url: str, settings: Settings) -> VideoRef` (YouTube only in this task), `sources.for_video(video, settings) -> Source`, `sources.for_name(name, settings) -> Source`.
  - `ingest.files.save_info / load_info / find_video_file` (same signatures as before; still importable from `ingest.youtube`).

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_sources.py`:

```python
import pytest

from unstuck import sources
from unstuck.errors import IngestError
from unstuck.ingest import youtube as yt
from unstuck.models import Video
from unstuck.sources.base import Playback, VideoMeta


def make_video(video_id="dQw4w9WgXcQ", source="youtube"):
    return Video(
        id=video_id, url="u", title="", channel="", duration=0.0, status="ready", ask_ready=True,
        stage_states={}, visual_mode="full", outline=None, error_code=None, error_message=None,
        created_at="", source=source,
    )


def test_resolve_youtube(settings):
    ref = sources.resolve("https://youtu.be/dQw4w9WgXcQ", settings)
    assert (ref.source, ref.video_id, ref.url) == ("youtube", "dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ")


def test_resolve_rejects_garbage(settings):
    with pytest.raises(IngestError) as err:
        sources.resolve("https://vimeo.com/1", settings)
    assert err.value.code == "INVALID_URL"


def test_for_video_youtube_adapter_delegates(monkeypatch, settings):
    src = sources.for_video(make_video(), settings)
    assert src.name == "youtube"
    monkeypatch.setattr(yt, "fetch_info", lambda vid: {"id": vid, "title": "T", "channel": "C", "duration": 12})
    info = src.fetch_info("dQw4w9WgXcQ")
    assert src.check_info(info, 7200) == VideoMeta(title="T", channel="C", duration=12.0)
    assert src.playback("dQw4w9WgXcQ") == Playback(kind="youtube")
    assert [p.video_id for p in src.parts("dQw4w9WgXcQ")] == ["dQw4w9WgXcQ"]


def test_for_name_unknown(settings):
    with pytest.raises(ValueError):
        sources.for_name("vimeo", settings)
```

Append to `backend/tests/test_stages.py` (reuse its `ctx` fixture and imports; add `from unstuck.sources.base import VideoMeta` at the top):

```python
class FakeSource:
    name = "fake"

    def __init__(self):
        self.downloaded = []

    def fetch_info(self, video_id):
        return {"id": video_id, "duration": 60}

    def check_info(self, info, max_duration_s):
        return VideoMeta(title="课程", channel="UP", duration=60.0, part_title="第一集", parts_total=3, thumbnail="https://x/y.jpg")

    def fetch_captions(self, info):
        return [Cue(i * 5.0, (i + 1) * 5.0, f"第{i}句。") for i in range(12)]

    def download_video(self, video_id, out_dir):
        self.downloaded.append(video_id)
        return out_dir / "video.mp4"

    def download_audio(self, video_id, out_dir):
        raise AssertionError("captions exist; audio must not be downloaded")


def test_stages_use_the_videos_source(ctx, monkeypatch):
    fake = FakeSource()
    monkeypatch.setattr(stages.sources, "for_video", lambda video, settings: fake)

    stages.stage_metadata(ctx)
    video = repo.get_video(ctx.conn, VID)
    assert (video.title, video.part_title, video.parts_total, video.thumbnail) == ("课程", "第一集", 3, "https://x/y.jpg")

    stages.stage_transcript(ctx)
    assert len(repo.get_cues(ctx.conn, VID)) == 12

    stages.stage_download(ctx)
    assert fake.downloaded == [VID]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_sources.py tests/test_stages.py -q`
Expected: FAIL (`No module named 'unstuck.sources'`).

- [ ] **Step 3: Implement**

`backend/unstuck/ingest/files.py` — move these three functions out of `ingest/youtube.py` unchanged (with their imports `json`, `Path`, and the `_VIDEO_SUFFIXES` constant):

```python
import json
from pathlib import Path

_VIDEO_SUFFIXES = {".mp4", ".webm", ".mkv", ".mov"}


def save_info(info: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")


def load_info(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def find_video_file(video_dir: Path) -> Path | None:
    for path in sorted(video_dir.glob("video.*")):
        if path.suffix in _VIDEO_SUFFIXES and path.is_file():
            return path
    return None
```

In `backend/unstuck/ingest/youtube.py` delete those three functions and `_VIDEO_SUFFIXES`, and add near the imports:

```python
from unstuck.ingest.files import find_video_file, load_info, save_info  # noqa: F401  (re-exported)
```

`backend/unstuck/sources/base.py`:

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from unstuck.models import Cue


@dataclass(frozen=True)
class VideoRef:
    """What a pasted link resolves to."""

    source: str
    video_id: str
    url: str
    course_id: str | None = None
    part: int | None = None


@dataclass(frozen=True)
class VideoMeta:
    title: str
    channel: str
    duration: float
    part_title: str = ""
    parts_total: int = 1
    thumbnail: str = ""


@dataclass(frozen=True)
class Playback:
    """How the browser plays a video: "youtube" (IFrame player) or "mp4" (a direct, expiring URL)."""

    kind: str
    url: str = ""
    expires_at: float = 0.0
    duration: float = 0.0


@dataclass(frozen=True)
class Part:
    part: int
    title: str
    duration: float
    video_id: str


class Source(Protocol):
    name: str

    def fetch_info(self, video_id: str) -> dict: ...

    def check_info(self, info: dict, max_duration_s: int) -> VideoMeta: ...

    def fetch_captions(self, info: dict) -> list[Cue] | None: ...

    def download_video(self, video_id: str, out_dir: Path) -> Path: ...

    def download_audio(self, video_id: str, out_dir: Path) -> Path: ...

    def playback(self, video_id: str, quality: int = 720) -> Playback: ...

    def parts(self, video_id: str) -> list[Part]: ...
```

`backend/unstuck/sources/youtube.py`:

```python
from pathlib import Path

from unstuck.ingest import youtube as yt
from unstuck.models import Cue
from unstuck.sources.base import Part, Playback, VideoMeta, VideoRef

NAME = "youtube"


def parse(url: str) -> VideoRef:
    video_id = yt.parse_video_id(url)
    return VideoRef(source=NAME, video_id=video_id, url=yt.watch_url(video_id))


class YouTubeSource:
    """Adapter over unstuck.ingest.youtube; looked up at call time so tests can monkeypatch it."""

    name = NAME

    def fetch_info(self, video_id: str) -> dict:
        return yt.fetch_info(video_id)

    def check_info(self, info: dict, max_duration_s: int) -> VideoMeta:
        meta = yt.check_video_info(info, max_duration_s)
        return VideoMeta(title=meta.title, channel=meta.channel, duration=meta.duration)

    def fetch_captions(self, info: dict) -> list[Cue] | None:
        return yt.fetch_captions(info)

    def download_video(self, video_id: str, out_dir: Path) -> Path:
        return yt.download_video(video_id, out_dir)

    def download_audio(self, video_id: str, out_dir: Path) -> Path:
        return yt.download_audio(video_id, out_dir)

    def playback(self, video_id: str, quality: int = 720) -> Playback:
        return Playback(kind="youtube")

    def parts(self, video_id: str) -> list[Part]:
        return [Part(part=1, title="", duration=0.0, video_id=video_id)]
```

`backend/unstuck/sources/__init__.py`:

```python
from unstuck.config import Settings
from unstuck.models import Video
from unstuck.sources import youtube
from unstuck.sources.base import Source, VideoRef


def resolve(url: str, settings: Settings) -> VideoRef:
    """Turn a pasted link into a VideoRef; raises IngestError (INVALID_URL, …)."""
    return youtube.parse(url)


def for_name(name: str, settings: Settings) -> Source:
    if name == youtube.NAME:
        return youtube.YouTubeSource()
    raise ValueError(f"unknown video source {name!r}")


def for_video(video: Video, settings: Settings) -> Source:
    return for_name(video.source, settings)
```

`backend/unstuck/ingest/stages.py` — replace the youtube calls:

```python
from unstuck import sources
from unstuck.db import repo
from unstuck.errors import IngestError
from unstuck.ingest import files, indexer, keyframes, outline, timeline, transcribe, vision
from unstuck.ingest.pipeline import SKIPPED, StageContext, StageFn
```

```python
def _source(ctx: StageContext):
    return sources.for_video(repo.get_video(ctx.conn, ctx.video_id), ctx.settings)


def stage_metadata(ctx: StageContext) -> None:
    src = _source(ctx)
    info = src.fetch_info(ctx.video_id)
    meta = src.check_info(info, ctx.settings.max_duration_s)
    files.save_info(info, ctx.video_dir / INFO_FILE)
    repo.update_video(
        ctx.conn, ctx.video_id,
        title=meta.title, channel=meta.channel, duration=meta.duration,
        part_title=meta.part_title, parts_total=meta.parts_total, thumbnail=meta.thumbnail,
    )


def stage_transcript(ctx: StageContext) -> None:
    src = _source(ctx)
    info = files.load_info(ctx.video_dir / INFO_FILE)
    cues = src.fetch_captions(info)
    if not cues:
        log.info("no captions for %s, transcribing audio", ctx.video_id)
        audio = src.download_audio(ctx.video_id, ctx.video_dir)
        cues = transcribe.transcribe(audio, ctx.settings.whisper_model)
    # ... rest unchanged ...


def stage_download(ctx: StageContext) -> None:
    try:
        _source(ctx).download_video(ctx.video_id, ctx.video_dir)
    # ... rest unchanged ...
```

and in `stage_keyframes` use `files.find_video_file(ctx.video_dir)`.

`backend/unstuck/tutor/frame.py`: `from unstuck.ingest import files` and `files.find_video_file(video_dir)` (Task 6 adds the stream fallback).

`backend/unstuck/api/app.py` — import `from unstuck import sources` (drop the `youtube` import) and change the import route:

```python
    @app.post("/api/videos", status_code=202)
    def import_video(req: ImportRequest, conn=Depends(get_conn)):
        try:
            ref = sources.resolve(req.url, settings)
        except IngestError as exc:
            raise _error(400, exc.code, exc.message) from exc
        created = repo.insert_video(
            conn, ref.video_id, ref.url, source=ref.source, course_id=ref.course_id, part=ref.part
        )
        video = repo.get_video(conn, ref.video_id)
        if created or video.status == "failed":
            return start_import(conn, ref.video_id)
        return asdict(video)
```

- [ ] **Step 4: Run the full suite**

Run: `cd backend && uv run pytest -q`
Expected: all pass, including every Phase 1 YouTube test (they still monkeypatch `unstuck.ingest.youtube`).

- [ ] **Step 5: Commit**

```bash
git add backend/unstuck backend/tests
git commit -m "refactor: route ingest, tutor and API through a video source layer"
```

---

### Task 4: Bilibili pure logic

**Files:**
- Create: `backend/unstuck/sources/bilibili/__init__.py` (empty), `backend/unstuck/sources/bilibili/parse.py`
- Test: `backend/tests/test_bili_parse.py`

**Interfaces:**
- Consumes: `VideoMeta`, `Playback`, `Part` (Task 3), `Cue`, `IngestError`, `fmt_ts`.
- Produces (all in `unstuck.sources.bilibili.parse`):
  - `BVID_RE`, `SHORT_HOSTS: frozenset[str]`, `is_bilibili_host(host: str) -> bool`
  - `make_video_id(bvid, part) -> str`, `split_video_id(video_id) -> tuple[str, int]` (ValueError if not a Bilibili id), `watch_url(bvid, part) -> str`
  - `parse_url(url) -> tuple[str, int]` (raises `IngestError` `INVALID_URL` / `UNSUPPORTED_URL`)
  - `mixin_key(img_url, sub_url) -> str`, `wbi_sign(params: dict, key: str, wts: int) -> dict`
  - `info_from_view(view: dict, bvid: str, part: int) -> dict` (keys: `source, bvid, part, cid, title, channel, duration, part_title, parts_total, thumbnail`; raises `VIDEO_UNAVAILABLE` if the part does not exist), `meta_from_info(info, max_duration_s) -> VideoMeta` (raises `TOO_LONG` / `VIDEO_UNAVAILABLE`)
  - `pick_subtitle_url(subtitles: list[dict]) -> str | None`, `subtitle_to_cues(body: list[dict]) -> list[Cue]`, `subtitle_matches(cues, duration) -> bool`
  - `mp4_playback(data: dict, duration: float, now: float) -> Playback` (raises `PLAYBACK_UNAVAILABLE`)
  - `parts_from_view(view: dict, bvid: str) -> list[Part]`

Subtitle-match rule (tightens the spec's rule): accept only if the last cue ends no later than `duration + 5` **and** no earlier than `0.8 × duration`. The mismatched subtitle seen in research ended at 791 s on a 1272 s video — 62% — which the spec's "covers ≥ 50%" rule would have accepted. A lecture with a very long silent ending is rejected too; the cost is only a slower Whisper transcription, not wrong content.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_bili_parse.py`:

```python
import hashlib

import pytest

from unstuck.errors import IngestError
from unstuck.models import Cue
from unstuck.sources.base import Part, Playback, VideoMeta
from unstuck.sources.bilibili import parse

BV = "BV1Ex4y1G7nG"
VIEW = {
    "bvid": BV, "title": "导数1：导数的概念", "pic": "http://i0.hdslb.com/bfs/archive/a.jpg",
    "owner": {"name": "数学老师"}, "duration": 1500,
    "pages": [
        {"cid": 111, "page": 1, "part": "导数的概念", "duration": 1272},
        {"cid": 222, "page": 2, "part": "求导法则", "duration": 228},
    ],
}


@pytest.mark.parametrize("url,expected", [
    (f"https://www.bilibili.com/video/{BV}", (BV, 1)),
    (f"https://www.bilibili.com/video/{BV}/?spm_id_from=333&p=3", (BV, 3)),
    (f"https://m.bilibili.com/video/{BV}?p=2", (BV, 2)),
    (f"bilibili.com/video/{BV}", (BV, 1)),
])
def test_parse_url(url, expected):
    assert parse.parse_url(url) == expected


@pytest.mark.parametrize("url,code", [
    ("https://www.bilibili.com/cheese/play/ss607570738", "UNSUPPORTED_URL"),
    ("https://www.bilibili.com/bangumi/play/ep1", "UNSUPPORTED_URL"),
    ("https://space.bilibili.com/123456", "UNSUPPORTED_URL"),
    ("https://www.bilibili.com/list/watchlater", "UNSUPPORTED_URL"),
    ("https://www.bilibili.com/video/av989935671", "INVALID_URL"),
    (f"https://www.bilibili.com/video/{BV}?p=0", "INVALID_URL"),
    ("https://www.bilibili.com/", "INVALID_URL"),
])
def test_parse_url_rejects(url, code):
    with pytest.raises(IngestError) as err:
        parse.parse_url(url)
    assert err.value.code == code


def test_video_ids():
    assert parse.make_video_id(BV, 3) == f"{BV}_p3"
    assert parse.split_video_id(f"{BV}_p3") == (BV, 3)
    assert parse.watch_url(BV, 3) == f"https://www.bilibili.com/video/{BV}?p=3"
    with pytest.raises(ValueError):
        parse.split_video_id("dQw4w9WgXcQ")


def test_hosts():
    assert parse.is_bilibili_host("www.bilibili.com")
    assert parse.is_bilibili_host("b23.tv")
    assert not parse.is_bilibili_host("notbilibili.com")


def test_wbi_matches_published_example():
    key = parse.mixin_key(
        "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png",
        "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png",
    )
    assert key == "ea1db124af3c7062474693fa704f4ff8"
    signed = parse.wbi_sign({"foo": "114", "bar": "514", "zab": 1919810}, key, 1702204169)
    assert signed["w_rid"] == "8f6f2b5b3d485fe1886cec6a0be8c5d4"
    assert signed["wts"] == "1702204169"


def test_wbi_filters_reserved_characters():
    signed = parse.wbi_sign({"q": "a!b'c(d)e*"}, "k", 1)
    assert signed["q"] == "abcde"
    assert signed["w_rid"] == hashlib.md5(b"q=abcde&wts=1k").hexdigest()


def test_info_and_meta_from_view():
    info = parse.info_from_view(VIEW, BV, 2)
    assert info == {
        "source": "bilibili", "bvid": BV, "part": 2, "cid": 222, "title": "导数1：导数的概念",
        "channel": "数学老师", "duration": 228.0, "part_title": "求导法则", "parts_total": 2,
        "thumbnail": "https://i0.hdslb.com/bfs/archive/a.jpg",
    }
    assert parse.meta_from_info(info, 7200) == VideoMeta(
        title="导数1：导数的概念", channel="数学老师", duration=228.0, part_title="求导法则",
        parts_total=2, thumbnail="https://i0.hdslb.com/bfs/archive/a.jpg",
    )


def test_single_part_has_no_part_title():
    view = {**VIEW, "pages": [VIEW["pages"][0]]}
    assert parse.info_from_view(view, BV, 1)["part_title"] == ""


def test_missing_part_and_too_long():
    with pytest.raises(IngestError) as err:
        parse.info_from_view(VIEW, BV, 9)
    assert err.value.code == "VIDEO_UNAVAILABLE"
    with pytest.raises(IngestError) as err:
        parse.meta_from_info(parse.info_from_view(VIEW, BV, 1), 600)
    assert err.value.code == "TOO_LONG"


def test_pick_subtitle_prefers_uploader_chinese():
    subs = [
        {"lan": "ai-zh", "subtitle_url": "//aisubtitle.hdslb.com/ai.json"},
        {"lan": "zh-CN", "subtitle_url": "//i0.hdslb.com/human.json"},
        {"lan": "en-US", "subtitle_url": "//i0.hdslb.com/en.json"},
    ]
    assert parse.pick_subtitle_url(subs) == "https://i0.hdslb.com/human.json"
    assert parse.pick_subtitle_url(subs[:1]) == "https://aisubtitle.hdslb.com/ai.json"
    assert parse.pick_subtitle_url([{"lan": "ai-zh", "subtitle_url": ""}]) is None
    assert parse.pick_subtitle_url([]) is None


def test_subtitle_to_cues():
    body = [{"from": 0, "to": 1.5, "content": " 大家好 "}, {"from": 2, "to": 3, "content": ""}]
    assert parse.subtitle_to_cues(body) == [Cue(0.0, 1.5, "大家好")]


@pytest.mark.parametrize("last_end,ok", [(1271.1, True), (1100.0, True), (791.78, False), (1290.0, False)])
def test_subtitle_matches(last_end, ok):
    cues = [Cue(0.0, 2.0, "a"), Cue(last_end - 1, last_end, "b")]
    assert parse.subtitle_matches(cues, 1272.0) is ok


def test_subtitle_matches_empty():
    assert parse.subtitle_matches([], 100.0) is False


def test_mp4_playback():
    data = {"durl": [{"url": "https://upos.bilivideo.com/x.mp4?deadline=1791671421&upsig=a"}]}
    assert parse.mp4_playback(data, 1272.0, now=0) == Playback(
        kind="mp4", url="https://upos.bilivideo.com/x.mp4?deadline=1791671421&upsig=a",
        expires_at=1791671421.0, duration=1272.0,
    )
    no_deadline = {"durl": [{"url": "https://upos.bilivideo.com/x.mp4"}]}
    assert parse.mp4_playback(no_deadline, 10.0, now=100.0).expires_at == 100.0 + 3600
    with pytest.raises(IngestError) as err:
        parse.mp4_playback({"durl": []}, 10.0, now=0)
    assert err.value.code == "PLAYBACK_UNAVAILABLE"


def test_parts_from_view():
    assert parse.parts_from_view(VIEW, BV) == [
        Part(part=1, title="导数的概念", duration=1272.0, video_id=f"{BV}_p1"),
        Part(part=2, title="求导法则", duration=228.0, video_id=f"{BV}_p2"),
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_bili_parse.py -q`
Expected: FAIL (`No module named 'unstuck.sources.bilibili'`).

- [ ] **Step 3: Implement**

`backend/unstuck/sources/bilibili/parse.py`:

```python
"""Pure Bilibili logic: links, ids, WBI signing and API-response parsing (no network)."""

import hashlib
import re
import urllib.parse

from unstuck.errors import IngestError
from unstuck.models import Cue
from unstuck.sources.base import Part, Playback, VideoMeta
from unstuck.timefmt import fmt_ts

BVID_RE = re.compile(r"BV[0-9A-Za-z]{10}")
_VIDEO_ID_RE = re.compile(r"(BV[0-9A-Za-z]{10})_p([1-9][0-9]{0,3})")
_SITE_HOSTS = frozenset({"bilibili.com", "www.bilibili.com", "m.bilibili.com"})
SHORT_HOSTS = frozenset({"b23.tv", "www.b23.tv"})

INVALID_MESSAGE = "不是有效的 B站视频链接"
COLLECTION_MESSAGE = "暂不支持合集、收藏夹或 UP 主主页，请粘贴单个视频链接"
_UNSUPPORTED_SECTIONS = {
    "cheese": "暂不支持 B站付费课程",
    "bangumi": "暂不支持番剧和影视，请粘贴单个视频链接",
    "list": COLLECTION_MESSAGE,
    "medialist": COLLECTION_MESSAGE,
    "festival": COLLECTION_MESSAGE,
}

# Fixed permutation used by Bilibili's web client to derive the WBI mixin key.
_MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39,
    12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63,
    57, 62, 11, 36, 20, 34, 44, 52,
]
_WBI_STRIPPED = set("!'()*")

SUBTITLE_END_TOLERANCE_S = 5.0
SUBTITLE_MIN_END_RATIO = 0.8
DEFAULT_URL_LIFETIME_S = 3600.0


def is_bilibili_host(host: str) -> bool:
    return host in _SITE_HOSTS or host in SHORT_HOSTS or host.endswith(".bilibili.com")


def make_video_id(bvid: str, part: int) -> str:
    return f"{bvid}_p{part}"


def split_video_id(video_id: str) -> tuple[str, int]:
    match = _VIDEO_ID_RE.fullmatch(video_id)
    if not match:
        raise ValueError(f"not a Bilibili video id: {video_id!r}")
    return match.group(1), int(match.group(2))


def watch_url(bvid: str, part: int) -> str:
    return f"https://www.bilibili.com/video/{bvid}?p={part}"


def parse_url(url: str) -> tuple[str, int]:
    """Parse a full bilibili.com video link into (bvid, part). b23.tv links must be resolved first."""
    text = url.strip()
    try:
        parsed = urllib.parse.urlparse(text if "://" in text else f"https://{text}")
        host = (parsed.hostname or "").lower()
    except ValueError as exc:
        raise IngestError("INVALID_URL", INVALID_MESSAGE) from exc
    segments = [s for s in parsed.path.split("/") if s]
    if host == "space.bilibili.com":
        raise IngestError("UNSUPPORTED_URL", COLLECTION_MESSAGE)
    if segments and segments[0] in _UNSUPPORTED_SECTIONS:
        raise IngestError("UNSUPPORTED_URL", _UNSUPPORTED_SECTIONS[segments[0]])
    if host not in _SITE_HOSTS or len(segments) < 2 or segments[0] != "video" or not BVID_RE.fullmatch(segments[1]):
        raise IngestError("INVALID_URL", INVALID_MESSAGE)
    page = (urllib.parse.parse_qs(parsed.query).get("p") or ["1"])[0]
    if not page.isdigit() or not 1 <= int(page) <= 9999:
        raise IngestError("INVALID_URL", INVALID_MESSAGE)
    return segments[1], int(page)


def _wbi_file_key(url: str) -> str:
    return url.rsplit("/", 1)[-1].split(".")[0]


def mixin_key(img_url: str, sub_url: str) -> str:
    raw = _wbi_file_key(img_url) + _wbi_file_key(sub_url)
    return "".join(raw[i] for i in _MIXIN_KEY_ENC_TAB)[:32]


def wbi_sign(params: dict, key: str, wts: int) -> dict:
    """Return params plus `wts` and `w_rid`, all stringified, as Bilibili's WBI endpoints expect."""
    values = {**params, "wts": wts}
    cleaned = {k: "".join(ch for ch in str(v) if ch not in _WBI_STRIPPED) for k, v in sorted(values.items())}
    query = urllib.parse.urlencode(cleaned)
    return {**cleaned, "w_rid": hashlib.md5((query + key).encode()).hexdigest()}


def _https(url: str) -> str:
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http://"):
        return "https://" + url.removeprefix("http://")
    return url


def info_from_view(view: dict, bvid: str, part: int) -> dict:
    pages = view.get("pages") or []
    page = next((p for p in pages if p.get("page") == part), None)
    if page is None:
        raise IngestError("VIDEO_UNAVAILABLE", f"这个视频没有第 {part} 集")
    return {
        "source": "bilibili",
        "bvid": bvid,
        "part": part,
        "cid": page["cid"],
        "title": view.get("title") or "",
        "channel": (view.get("owner") or {}).get("name") or "",
        "duration": float(page.get("duration") or 0),
        "part_title": (page.get("part") or "") if len(pages) > 1 else "",
        "parts_total": len(pages),
        "thumbnail": _https(view.get("pic") or ""),
    }


def meta_from_info(info: dict, max_duration_s: int) -> VideoMeta:
    duration = float(info.get("duration") or 0)
    if duration <= 0:
        raise IngestError("VIDEO_UNAVAILABLE", "无法获取视频时长，视频可能不可用")
    if duration > max_duration_s:
        raise IngestError("TOO_LONG", f"视频时长 {fmt_ts(duration)} 超过上限 {fmt_ts(max_duration_s)}，暂不支持导入")
    return VideoMeta(
        title=info["title"], channel=info["channel"], duration=duration,
        part_title=info["part_title"], parts_total=info["parts_total"], thumbnail=info["thumbnail"],
    )


def pick_subtitle_url(subtitles: list[dict]) -> str | None:
    """Prefer an uploader-made Chinese track, then the AI Chinese one, then anything."""

    def rank(sub: dict) -> int:
        lan = sub.get("lan") or ""
        if lan.startswith("zh"):
            return 0
        if lan.startswith("ai-zh"):
            return 1
        return 2

    usable = sorted((s for s in subtitles if s.get("subtitle_url")), key=rank)
    return _https(usable[0]["subtitle_url"]) if usable else None


def subtitle_to_cues(body: list[dict]) -> list[Cue]:
    cues = [Cue(float(item["from"]), float(item["to"]), str(item.get("content") or "").strip()) for item in body]
    return [c for c in cues if c.text and c.end >= c.start]


def subtitle_matches(cues: list[Cue], duration: float) -> bool:
    """Reject subtitles that cannot belong to this video (Bilibili sometimes serves another video's)."""
    if not cues or duration <= 0:
        return False
    last_end = max(c.end for c in cues)
    return SUBTITLE_MIN_END_RATIO * duration <= last_end <= duration + SUBTITLE_END_TOLERANCE_S


def mp4_playback(data: dict, duration: float, now: float) -> Playback:
    durl = data.get("durl") or []
    if not durl or not durl[0].get("url"):
        raise IngestError("PLAYBACK_UNAVAILABLE", "B站没有返回可播放的视频地址")
    url = durl[0]["url"]
    deadline = (urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("deadline") or [""])[0]
    expires_at = float(deadline) if deadline.isdigit() else now + DEFAULT_URL_LIFETIME_S
    return Playback(kind="mp4", url=url, expires_at=expires_at, duration=duration)


def parts_from_view(view: dict, bvid: str) -> list[Part]:
    return [
        Part(part=p["page"], title=p.get("part") or "", duration=float(p.get("duration") or 0),
             video_id=make_video_id(bvid, p["page"]))
        for p in view.get("pages") or []
    ]
```

- [ ] **Step 4: Run tests**

Run: `cd backend && uv run pytest tests/test_bili_parse.py -q` then `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/unstuck/sources/bilibili backend/tests/test_bili_parse.py
git commit -m "feat(bilibili): link parsing, WBI signing and API response parsing"
```

---

### Task 5: Bilibili client and source

**Files:**
- Create: `backend/unstuck/sources/bilibili/client.py`, `backend/unstuck/sources/bilibili/source.py`
- Modify: `backend/unstuck/sources/__init__.py`
- Test: `backend/tests/test_bili_client.py`, `backend/tests/test_bili_source.py`, `backend/tests/test_sources.py`

**Interfaces:**
- Consumes: Task 4 `parse.*`, Task 3 base types.
- Produces:
  - `BiliClient(cookies_browser: str = "", fetch_json: Callable[[str], dict] | None = None, sleep=time.sleep, clock=time.time)` with `ydl_options() -> dict`, `get(url) -> dict`, `nav() -> dict`, `is_logged_in() -> bool` (cached 300 s), `view(bvid) -> dict`, `player(bvid, cid) -> dict`, `playurl_mp4(bvid, cid) -> dict`, `subtitle(url) -> dict`
  - `client_for(settings) -> BiliClient` (one per cookies browser per process)
  - `BilibiliSource(client, clock=time.time)` implementing `Source` with `name = "bilibili"`
  - `sources.resolve(url, settings, follow_redirect=...)` now also returns Bilibili refs (`source="bilibili"`, `video_id=BV…_pN`, `course_id=BV…`, `part=N`); unknown links → `IngestError("INVALID_URL", "不是有效的 YouTube 或 B站视频链接")`
  - `sources.for_name("bilibili", settings)`; `sources.bilibili_status(settings) -> {"logged_in": bool, "cookies_browser": str}`

API error mapping inside `BiliClient` (JSON `code` field): `0` → data; `-352`, `-412` → `BILIBILI_BLOCKED`; `-404`, `62002`, `62004`, `62012` → `VIDEO_UNAVAILABLE` "视频不存在或不可访问（可能已删除、仅会员可见或有地区限制）"; anything else → `BILIBILI_ERROR` "B站接口返回错误（{code}）". HTTP 412/429/5xx and network errors back off 2 s, 4 s, then `BILIBILI_BLOCKED`; other HTTP errors → `BILIBILI_ERROR` "访问 B站接口失败，请稍后重试". A signed call that fails (other than `VIDEO_UNAVAILABLE`) refreshes the WBI key once and retries.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_bili_client.py`:

```python
import pytest

from unstuck.errors import IngestError
from unstuck.sources.bilibili.client import BiliClient

NAV = {"code": -101, "data": {"isLogin": False, "wbi_img": {
    "img_url": "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png",
    "sub_url": "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png"}}}


class HTTPStatusError(Exception):
    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.status = status


class FakeFetch:
    def __init__(self, routes):
        self.routes = routes  # path fragment -> list of responses (dict or Exception), last one repeats
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        for fragment, responses in self.routes.items():
            if fragment in url:
                item = responses.pop(0) if len(responses) > 1 else responses[0]
                if isinstance(item, Exception):
                    raise item
                return item
        raise AssertionError(f"unexpected url {url}")


def make(routes, clock=lambda: 1702204169.0):
    sleeps = []
    client = BiliClient("chrome", fetch_json=FakeFetch(routes), sleep=sleeps.append, clock=clock)
    return client, sleeps


def test_signed_player_call_has_wbi_params():
    client, _ = make({"/x/web-interface/nav": [NAV], "/x/player/wbi/v2": [{"code": 0, "data": {"subtitle": {}}}]})
    client.player("BV1Ex4y1G7nG", 111)
    url = client._fetch_json.urls[-1]
    assert "/x/player/wbi/v2?" in url and "w_rid=" in url and "wts=1702204169" in url and "cid=111" in url


def test_backoff_on_risk_control_then_success():
    view = {"code": 0, "data": {"title": "t"}}
    client, sleeps = make({"/x/web-interface/view": [HTTPStatusError(412), HTTPStatusError(412), view]})
    assert client.view("BV1Ex4y1G7nG") == {"title": "t"}
    assert sleeps == [2.0, 4.0]


def test_backoff_exhausted_is_blocked():
    client, sleeps = make({"/x/web-interface/view": [HTTPStatusError(412)]})
    with pytest.raises(IngestError) as err:
        client.view("BV1Ex4y1G7nG")
    assert err.value.code == "BILIBILI_BLOCKED"
    assert sleeps == [2.0, 4.0]


@pytest.mark.parametrize("code,expected", [(-404, "VIDEO_UNAVAILABLE"), (62002, "VIDEO_UNAVAILABLE"),
                                           (-352, "BILIBILI_BLOCKED"), (-400, "BILIBILI_ERROR")])
def test_json_error_codes(code, expected):
    client, _ = make({"/x/web-interface/view": [{"code": code, "message": "x"}]})
    with pytest.raises(IngestError) as err:
        client.view("BV1Ex4y1G7nG")
    assert err.value.code == expected


def test_other_http_errors_are_not_retried():
    client, sleeps = make({"/x/web-interface/view": [HTTPStatusError(404)]})
    with pytest.raises(IngestError) as err:
        client.view("BV1Ex4y1G7nG")
    assert err.value.code == "BILIBILI_ERROR"
    assert sleeps == []


def test_signed_call_refreshes_key_once_on_failure():
    routes = {"/x/web-interface/nav": [NAV], "/x/player/wbi/playurl": [{"code": -403, "message": "x"}, {"code": 0, "data": {"durl": []}}]}
    client, _ = make(routes)
    assert client.playurl_mp4("BV1Ex4y1G7nG", 111) == {"durl": []}
    assert sum("/x/web-interface/nav" in u for u in client._fetch_json.urls) == 2


def test_login_state_is_cached():
    now = [1000.0]
    client, _ = make({"/x/web-interface/nav": [{"code": 0, "data": {"isLogin": True, "wbi_img": NAV["data"]["wbi_img"]}}]},
                     clock=lambda: now[0])
    assert client.is_logged_in() is True
    assert client.is_logged_in() is True
    assert sum("nav" in u for u in client._fetch_json.urls) == 1
    now[0] += 301
    client.is_logged_in()
    assert sum("nav" in u for u in client._fetch_json.urls) == 2


def test_login_check_failure_means_logged_out():
    client, _ = make({"/x/web-interface/nav": [HTTPStatusError(404)]})
    assert client.is_logged_in() is False


def test_ydl_options_use_browser_cookies():
    assert BiliClient("chrome").ydl_options()["cookiesfrombrowser"] == ("chrome",)
    assert "cookiesfrombrowser" not in BiliClient("").ydl_options()
```

`backend/tests/test_bili_source.py`:

```python
import pytest

from unstuck.models import Cue
from unstuck.sources.bilibili.client import BiliClient
from unstuck.sources.bilibili.source import BilibiliSource
from tests.test_bili_client import NAV, FakeFetch

BV = "BV1Ex4y1G7nG"
VIEW = {"code": 0, "data": {"bvid": BV, "title": "导数", "pic": "http://i0.hdslb.com/a.jpg", "owner": {"name": "UP"},
                            "pages": [{"cid": 111, "page": 1, "part": "概念", "duration": 100},
                                      {"cid": 222, "page": 2, "part": "法则", "duration": 50}]}}
PLAYER = {"code": 0, "data": {"subtitle": {"subtitles": [{"lan": "ai-zh", "subtitle_url": "//aisubtitle.hdslb.com/s.json"}]}}}
GOOD_SUB = {"body": [{"from": 0, "to": 2, "content": "大家好"}, {"from": 90, "to": 99, "content": "再见"}]}
WRONG_SUB = {"body": [{"from": 0, "to": 2, "content": "去京都"}, {"from": 30, "to": 40, "content": "米其林"}]}
PLAYURL = {"code": 0, "data": {"durl": [{"url": "https://upos.bilivideo.com/v.mp4?deadline=1791671421"}]}}


def source(routes):
    return BilibiliSource(BiliClient("chrome", fetch_json=FakeFetch(routes), sleep=lambda s: None, clock=lambda: 1.0e9))


def base_routes(sub):
    return {"/x/web-interface/nav": [NAV], "/x/web-interface/view": [VIEW], "/x/player/wbi/v2": [PLAYER],
            "aisubtitle.hdslb.com": [sub], "/x/player/wbi/playurl": [PLAYURL]}


def test_fetch_info_and_check():
    src = source(base_routes(GOOD_SUB))
    info = src.fetch_info(f"{BV}_p1")
    assert (info["cid"], info["duration"], info["parts_total"]) == (111, 100.0, 2)
    assert src.check_info(info, 7200).part_title == "概念"


def test_captions_from_ai_subtitles():
    src = source(base_routes(GOOD_SUB))
    cues = src.fetch_captions(src.fetch_info(f"{BV}_p1"))
    assert cues == [Cue(0.0, 2.0, "大家好"), Cue(90.0, 99.0, "再见")]


def test_mismatched_subtitles_are_rejected():
    src = source(base_routes(WRONG_SUB))
    assert src.fetch_captions(src.fetch_info(f"{BV}_p1")) is None


def test_no_subtitles_and_errors_return_none():
    routes = base_routes(GOOD_SUB)
    routes["/x/player/wbi/v2"] = [{"code": 0, "data": {"subtitle": {"subtitles": []}}}]
    src = source(routes)
    assert src.fetch_captions(src.fetch_info(f"{BV}_p1")) is None
    routes["/x/player/wbi/v2"] = [RuntimeError("network down")]
    assert source(routes).fetch_captions({"bvid": BV, "cid": 111, "duration": 100.0}) is None


def test_playback_and_parts():
    src = source(base_routes(GOOD_SUB))
    pb = src.playback(f"{BV}_p2")
    assert (pb.kind, pb.url, pb.duration) == ("mp4", "https://upos.bilivideo.com/v.mp4?deadline=1791671421", 50.0)
    assert [p.video_id for p in src.parts(f"{BV}_p2")] == [f"{BV}_p1", f"{BV}_p2"]


def test_downloads_use_cookies_and_part_url(monkeypatch, tmp_path):
    calls = []

    class FakeYDL:
        def __init__(self, opts):
            calls.append(opts)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download):
            calls.append(url)
            return {"ext": "mp4"}

        def prepare_filename(self, info):
            return str(tmp_path / "video.mp4")

    monkeypatch.setattr("unstuck.sources.bilibili.source.YoutubeDL", FakeYDL)
    src = source(base_routes(GOOD_SUB))
    assert src.download_video(f"{BV}_p2", tmp_path) == tmp_path / "video.mp4"
    opts, url = calls
    assert opts["cookiesfrombrowser"] == ("chrome",) and opts["noplaylist"] is True
    assert url == f"https://www.bilibili.com/video/{BV}?p=2"


def test_download_errors_become_download_failed(monkeypatch, tmp_path):
    from unstuck.errors import IngestError

    class Boom:
        def __init__(self, opts):
            pass

        def __enter__(self):
            raise OSError("disk full")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("unstuck.sources.bilibili.source.YoutubeDL", Boom)
    with pytest.raises(IngestError) as err:
        source(base_routes(GOOD_SUB)).download_audio(f"{BV}_p1", tmp_path)
    assert err.value.code == "DOWNLOAD_FAILED"
```

Append to `backend/tests/test_sources.py`:

```python
def test_resolve_bilibili_links(settings):
    ref = sources.resolve("https://www.bilibili.com/video/BV1Ex4y1G7nG/?p=3", settings)
    assert (ref.source, ref.video_id, ref.course_id, ref.part) == ("bilibili", "BV1Ex4y1G7nG_p3", "BV1Ex4y1G7nG", 3)
    assert ref.url == "https://www.bilibili.com/video/BV1Ex4y1G7nG?p=3"
    assert sources.resolve("BV1Ex4y1G7nG", settings).video_id == "BV1Ex4y1G7nG_p1"


def test_resolve_b23_short_link(settings):
    seen = []

    def follow(url):
        seen.append(url)
        return "https://www.bilibili.com/video/BV1Ex4y1G7nG?p=2&share_source=copy"

    ref = sources.resolve("https://b23.tv/AbCdEf", settings, follow_redirect=follow)
    assert ref.video_id == "BV1Ex4y1G7nG_p2" and seen == ["https://b23.tv/AbCdEf"]


def test_resolve_bilibili_errors(settings):
    with pytest.raises(IngestError) as err:
        sources.resolve("https://www.bilibili.com/cheese/play/ss1", settings)
    assert err.value.code == "UNSUPPORTED_URL"
    with pytest.raises(IngestError) as err:
        sources.resolve("https://example.com/x", settings)
    assert err.value.code == "INVALID_URL" and "B站" in err.value.message


def test_for_name_bilibili(settings):
    assert sources.for_name("bilibili", settings).name == "bilibili"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_bili_client.py tests/test_bili_source.py tests/test_sources.py -q`
Expected: FAIL (missing modules / functions).

- [ ] **Step 3: Implement**

`backend/unstuck/sources/bilibili/client.py`:

```python
"""Bilibili web-API client: login cookies from the user's browser (via yt-dlp), backoff, WBI signing."""

import json
import logging
import threading
import time
import urllib.parse
from collections.abc import Callable

from unstuck.errors import IngestError
from unstuck.sources.bilibili import parse

log = logging.getLogger(__name__)

API = "https://api.bilibili.com"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
HEADERS = {"Referer": "https://www.bilibili.com/", "User-Agent": USER_AGENT}
BACKOFF_S = (2.0, 4.0)
LOGIN_CACHE_S = 300.0
_RETRY_STATUS = {412, 429, 500, 502, 503, 504}
_UNAVAILABLE_CODES = {-404, 62002, 62004, 62012}
_BLOCKED_CODES = {-352, -412}
BLOCKED_MESSAGE = "B站暂时限制了访问，请稍后重试"
UNAVAILABLE_MESSAGE = "视频不存在或不可访问（可能已删除、仅会员可见或有地区限制）"


def _status(exc: Exception) -> int | None:
    return getattr(exc, "status", None) or getattr(exc, "code", None)


def _retryable(exc: Exception) -> bool:
    return _status(exc) in _RETRY_STATUS or isinstance(exc, (OSError, TimeoutError)) or type(exc).__name__ == "TransportError"


class BiliClient:
    def __init__(
        self,
        cookies_browser: str = "",
        fetch_json: Callable[[str], dict] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ):
        self.cookies_browser = cookies_browser
        self._fetch_json = fetch_json or self._ydl_fetch_json
        self._sleep = sleep
        self._clock = clock
        self._ydl = None
        self._lock = threading.Lock()
        self._mixin: str | None = None
        self._login: tuple[float, bool] | None = None

    def ydl_options(self) -> dict:
        opts = {"quiet": True, "no_warnings": True, "noprogress": True, "http_headers": HEADERS}
        if self.cookies_browser:
            opts["cookiesfrombrowser"] = (self.cookies_browser,)
        return opts

    def _ydl_fetch_json(self, url: str) -> dict:
        from yt_dlp import YoutubeDL

        with self._lock:  # one shared cookie jar; Bilibili calls are few, so serialising them is fine
            if self._ydl is None:
                self._ydl = YoutubeDL(self.ydl_options())
            with self._ydl.urlopen(url) as resp:
                return json.loads(resp.read().decode("utf-8"))

    def get(self, url: str) -> dict:
        """GET a JSON document, backing off on risk control and network errors."""
        for attempt in range(len(BACKOFF_S) + 1):
            try:
                return self._fetch_json(url)
            except Exception as exc:
                if not _retryable(exc):
                    log.warning("bilibili request failed: %s (%s)", url.split("?")[0], exc)
                    raise IngestError("BILIBILI_ERROR", "访问 B站接口失败，请稍后重试") from exc
                if attempt == len(BACKOFF_S):
                    raise IngestError("BILIBILI_BLOCKED", BLOCKED_MESSAGE) from exc
                self._sleep(BACKOFF_S[attempt])
        raise AssertionError("unreachable")

    def _data(self, body: dict) -> dict:
        code = body.get("code", 0)
        if code == 0:
            return body.get("data") or {}
        if code in _BLOCKED_CODES:
            raise IngestError("BILIBILI_BLOCKED", BLOCKED_MESSAGE)
        if code in _UNAVAILABLE_CODES:
            raise IngestError("VIDEO_UNAVAILABLE", UNAVAILABLE_MESSAGE)
        raise IngestError("BILIBILI_ERROR", f"B站接口返回错误（{code}）")

    def _url(self, path: str, params: dict) -> str:
        return f"{API}{path}?{urllib.parse.urlencode(params)}"

    def nav(self) -> dict:
        # nav answers code -101 when logged out but still carries the WBI keys, so no _data() here.
        return self.get(f"{API}/x/web-interface/nav").get("data") or {}

    def _mixin_key(self, refresh: bool = False) -> str:
        if self._mixin is None or refresh:
            wbi = self.nav().get("wbi_img") or {}
            self._mixin = parse.mixin_key(wbi.get("img_url", ""), wbi.get("sub_url", ""))
        return self._mixin

    def _signed(self, path: str, params: dict) -> dict:
        for refresh in (False, True):
            signed = parse.wbi_sign(params, self._mixin_key(refresh), int(self._clock()))
            try:
                return self._data(self.get(self._url(path, signed)))
            except IngestError as exc:
                if refresh or exc.code == "VIDEO_UNAVAILABLE":
                    raise
                log.info("signed call to %s failed (%s); refreshing WBI key", path, exc.code)
        raise AssertionError("unreachable")

    def is_logged_in(self) -> bool:
        now = self._clock()
        if self._login and now - self._login[0] < LOGIN_CACHE_S:
            return self._login[1]
        try:
            logged_in = bool(self.nav().get("isLogin"))
        except Exception:
            log.warning("bilibili login check failed", exc_info=True)
            logged_in = False
        self._login = (now, logged_in)
        return logged_in

    def view(self, bvid: str) -> dict:
        return self._data(self.get(self._url("/x/web-interface/view", {"bvid": bvid})))

    def player(self, bvid: str, cid: int) -> dict:
        return self._signed("/x/player/wbi/v2", {"bvid": bvid, "cid": cid})

    def playurl_mp4(self, bvid: str, cid: int) -> dict:
        params = {"bvid": bvid, "cid": cid, "qn": 64, "fnval": 1, "platform": "html5", "high_quality": 1}
        return self._signed("/x/player/wbi/playurl", params)

    def subtitle(self, url: str) -> dict:
        return self.get(url)
```

`backend/unstuck/sources/bilibili/source.py`:

```python
import functools
import logging
import time
from pathlib import Path

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, YoutubeDLError

from unstuck.config import Settings
from unstuck.errors import IngestError
from unstuck.models import Cue
from unstuck.sources.base import Part, Playback, VideoMeta
from unstuck.sources.bilibili import parse
from unstuck.sources.bilibili.client import BiliClient

log = logging.getLogger(__name__)

NAME = "bilibili"
# Analysis only needs pictures; H.264 keeps OpenCV scene detection happy.
_VIDEO_FORMAT = "bv*[height<=720][vcodec^=avc1]/bv*[height<=720]/b[height<=720]/b"
_AUDIO_FORMAT = "ba/b"


@functools.lru_cache(maxsize=4)
def _client(cookies_browser: str) -> BiliClient:
    return BiliClient(cookies_browser)


def client_for(settings: Settings) -> BiliClient:
    return _client(settings.bilibili_cookies_browser)


class BilibiliSource:
    name = NAME

    def __init__(self, client: BiliClient, clock=time.time):
        self.client = client
        self._clock = clock

    def fetch_info(self, video_id: str) -> dict:
        bvid, part = parse.split_video_id(video_id)
        return parse.info_from_view(self.client.view(bvid), bvid, part)

    def check_info(self, info: dict, max_duration_s: int) -> VideoMeta:
        return parse.meta_from_info(info, max_duration_s)

    def fetch_captions(self, info: dict) -> list[Cue] | None:
        """Bilibili AI/uploader subtitles; None (never an exception) falls back to Whisper."""
        try:
            subtitles = ((self.client.player(info["bvid"], info["cid"]).get("subtitle") or {}).get("subtitles")) or []
            url = parse.pick_subtitle_url(subtitles)
            if url is None:
                log.info("no Bilibili subtitles for %s", info["bvid"])
                return None
            cues = parse.subtitle_to_cues(self.client.subtitle(url).get("body") or [])
        except Exception:
            log.warning("Bilibili subtitles failed for %s, falling back to transcription", info.get("bvid"), exc_info=True)
            return None
        if not parse.subtitle_matches(cues, float(info["duration"])):
            log.warning("Bilibili subtitles do not match %s (wrong video?); using transcription", info["bvid"])
            return None
        return cues

    def _download(self, video_id: str, out_dir: Path, fmt: str, stem: str, failure: str) -> Path:
        bvid, part = parse.split_video_id(video_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        opts = {**self.client.ydl_options(), "format": fmt, "noplaylist": True,
                "outtmpl": str(out_dir / f"{stem}.%(ext)s")}
        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(parse.watch_url(bvid, part), download=True)
                return Path(ydl.prepare_filename(info))
        except (DownloadError, OSError, YoutubeDLError) as exc:
            log.warning("%s (%s): %s", failure, video_id, exc)
            raise IngestError("DOWNLOAD_FAILED", failure) from exc

    def download_video(self, video_id: str, out_dir: Path) -> Path:
        return self._download(video_id, out_dir, _VIDEO_FORMAT, "video", "视频下载失败")

    def download_audio(self, video_id: str, out_dir: Path) -> Path:
        return self._download(video_id, out_dir, _AUDIO_FORMAT, "audio", "音频下载失败")

    def playback(self, video_id: str, quality: int = 720) -> Playback:
        info = self.fetch_info(video_id)
        data = self.client.playurl_mp4(info["bvid"], info["cid"])
        return parse.mp4_playback(data, info["duration"], now=self._clock())

    def parts(self, video_id: str) -> list[Part]:
        bvid, _part = parse.split_video_id(video_id)
        return parse.parts_from_view(self.client.view(bvid), bvid)
```

`backend/unstuck/sources/__init__.py` (replace):

```python
import urllib.request
from collections.abc import Callable
from urllib.parse import urlparse

from unstuck.config import Settings
from unstuck.errors import IngestError
from unstuck.models import Video
from unstuck.sources import youtube
from unstuck.sources.base import Source, VideoRef
from unstuck.sources.bilibili import parse as bili
from unstuck.sources.bilibili.client import USER_AGENT
from unstuck.sources.bilibili.source import NAME as BILIBILI, BilibiliSource, client_for

INVALID_MESSAGE = "不是有效的 YouTube 或 B站视频链接"


def _host(text: str) -> str:
    try:
        return (urlparse(text if "://" in text else f"https://{text}").hostname or "").lower()
    except ValueError:
        return ""


def _follow_redirect(url: str) -> str:
    """Resolve a b23.tv short link to the bilibili.com page it redirects to."""
    try:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=10) as resp:
            return resp.geturl()
    except Exception as exc:
        raise IngestError("INVALID_URL", "B站短链接解析失败，请粘贴完整的视频链接") from exc


def resolve(url: str, settings: Settings, follow_redirect: Callable[[str], str] = _follow_redirect) -> VideoRef:
    """Turn a pasted link into a VideoRef; raises IngestError (INVALID_URL / UNSUPPORTED_URL)."""
    text = url.strip()
    if bili.BVID_RE.fullmatch(text):
        text = bili.watch_url(text, 1)
    host = _host(text)
    if host in bili.SHORT_HOSTS:
        text = follow_redirect(text if "://" in text else f"https://{text}")
        host = _host(text)
    if host and bili.is_bilibili_host(host):
        bvid, part = bili.parse_url(text)
        return VideoRef(source=BILIBILI, video_id=bili.make_video_id(bvid, part),
                        url=bili.watch_url(bvid, part), course_id=bvid, part=part)
    try:
        return youtube.parse(text)
    except IngestError as exc:
        raise IngestError("INVALID_URL", INVALID_MESSAGE) from exc


def for_name(name: str, settings: Settings) -> Source:
    if name == youtube.NAME:
        return youtube.YouTubeSource()
    if name == BILIBILI:
        return BilibiliSource(client_for(settings))
    raise ValueError(f"unknown video source {name!r}")


def for_video(video: Video, settings: Settings) -> Source:
    return for_name(video.source, settings)


def bilibili_status(settings: Settings) -> dict:
    browser = settings.bilibili_cookies_browser
    return {"logged_in": bool(browser) and client_for(settings).is_logged_in(), "cookies_browser": browser}
```

- [ ] **Step 4: Run the full suite**

Run: `cd backend && uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/unstuck/sources backend/tests
git commit -m "feat(bilibili): API client with login cookies, backoff and WBI; Bilibili source"
```

---

### Task 6: API endpoints and live frame from the stream

**Files:**
- Modify: `backend/unstuck/api/app.py`, `backend/unstuck/tutor/frame.py`, `backend/unstuck/media.py`
- Test: `backend/tests/test_api.py`, `backend/tests/test_frame.py`

**Interfaces:**
- Consumes: `sources.for_video`, `sources.bilibili_status`, `Playback`, `Part`.
- Produces:
  - `GET /api/videos/{id}/playback?quality=720` → `asdict(Playback)`; source errors → 502 `{detail: {code, message}}`
  - `GET /api/videos/{id}/parts` → `[{part, title, duration, video_id, imported}]`; source errors → 502
  - `GET /api/bilibili/status` → `{logged_in, cookies_browser}`
  - `media.extract_frame(video: Path | str, t, out_path)` — accepts a URL
  - `live_frame_description` uses the source's 720p URL when no local file exists (non-YouTube only)

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_api.py` (add `from unstuck import sources` and `from unstuck.sources.base import Part, Playback` at the top):

```python
class FakeBiliSource:
    name = "bilibili"

    def playback(self, video_id, quality=720):
        return Playback(kind="mp4", url="https://upos.bilivideo.com/v.mp4", expires_at=123.0, duration=50.0)

    def parts(self, video_id):
        return [Part(1, "概念", 100.0, "BV1Ex4y1G7nG_p1"), Part(2, "法则", 50.0, "BV1Ex4y1G7nG_p2")]


def test_import_bilibili_link(client, runner):
    r = client.post("/api/videos", json={"url": "https://www.bilibili.com/video/BV1Ex4y1G7nG?p=2"})
    assert r.status_code == 202
    body = r.json()
    assert (body["id"], body["source"], body["course_id"], body["part"]) == ("BV1Ex4y1G7nG_p2", "bilibili", "BV1Ex4y1G7nG", 2)
    assert runner.submitted == ["BV1Ex4y1G7nG_p2"]


def test_import_rejects_paid_course(client):
    r = client.post("/api/videos", json={"url": "https://www.bilibili.com/cheese/play/ss1"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "UNSUPPORTED_URL"


def test_playback_and_parts(client, conn, monkeypatch):
    repo.insert_video(conn, "BV1Ex4y1G7nG_p2", "u", source="bilibili", course_id="BV1Ex4y1G7nG", part=2)
    monkeypatch.setattr(sources, "for_video", lambda video, settings: FakeBiliSource())
    pb = client.get("/api/videos/BV1Ex4y1G7nG_p2/playback").json()
    assert pb == {"kind": "mp4", "url": "https://upos.bilivideo.com/v.mp4", "expires_at": 123.0, "duration": 50.0}
    parts = client.get("/api/videos/BV1Ex4y1G7nG_p2/parts").json()
    assert [(p["part"], p["imported"]) for p in parts] == [(1, False), (2, True)]


def test_playback_source_error_is_502(client, conn, monkeypatch):
    repo.insert_video(conn, "BV1Ex4y1G7nG_p1", "u", source="bilibili", course_id="BV1Ex4y1G7nG", part=1)

    class Broken(FakeBiliSource):
        def playback(self, video_id, quality=720):
            raise IngestError("BILIBILI_BLOCKED", "B站暂时限制了访问，请稍后重试")

    monkeypatch.setattr(sources, "for_video", lambda video, settings: Broken())
    r = client.get("/api/videos/BV1Ex4y1G7nG_p1/playback")
    assert r.status_code == 502 and r.json()["detail"]["code"] == "BILIBILI_BLOCKED"


def test_bilibili_status(client, monkeypatch):
    monkeypatch.setattr(sources, "bilibili_status", lambda settings: {"logged_in": False, "cookies_browser": "chrome"})
    assert client.get("/api/bilibili/status").json() == {"logged_in": False, "cookies_browser": "chrome"}
```

(If `IngestError` is not yet imported in `test_api.py`, add `from unstuck.errors import IngestError`.)

Append to `backend/tests/test_frame.py` (add `from unstuck import sources` and `from unstuck.sources.base import Playback`):

```python
def test_bilibili_without_local_file_uses_stream_url(conn, settings, sample_video, monkeypatch):
    repo.insert_video(conn, "BV1Ex4y1G7nG_p1", "u", source="bilibili", course_id="BV1Ex4y1G7nG", part=1)

    class StreamSource:
        def playback(self, video_id, quality=720):
            return Playback(kind="mp4", url=str(sample_video))  # ffmpeg reads a URL or a path alike

    monkeypatch.setattr(sources, "for_video", lambda video, s: StreamSource())
    llm = FakeLLM(vision_responses=["白板上写着导数定义"])
    assert live_frame_description(conn, llm, settings, "BV1Ex4y1G7nG_p1", 1.0, "这是什么") == "白板上写着导数定义"


def test_youtube_without_local_file_still_returns_none(conn, settings):
    repo.insert_video(conn, VID, "u")
    assert live_frame_description(conn, FakeLLM(), settings, VID, 1.0, "q") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_api.py tests/test_frame.py -q`
Expected: FAIL (404s for the new routes; frame returns None for Bilibili).

- [ ] **Step 3: Implement**

`backend/unstuck/media.py` — change the signature and the `-i` argument:

```python
def extract_frame(video: Path | str, t: float, out_path: Path) -> Path:
    """Grab the frame at t seconds as a JPEG using ffmpeg; `video` may be a local file or an http(s) URL."""
```

(`"-i", str(video)` in the command; rest unchanged.)

`backend/unstuck/tutor/frame.py` — replace the "find the local file" part:

```python
from unstuck import media, sources
from unstuck.ingest import files
```

```python
    video_dir = settings.video_dir(video_id)
    frame_input = _frame_input(conn, settings, video_id, video_dir)
    if frame_input is None:
        return None
    image = video_dir / "live" / f"{second}.jpg"
    try:
        media.extract_frame(frame_input, float(second), image)
        # ... unchanged ...
```

```python
def _frame_input(conn, settings: Settings, video_id: str, video_dir) -> "Path | str | None":
    """The downloaded file if there is one; otherwise the online stream for sources that allow it."""
    local = files.find_video_file(video_dir)
    if local is not None:
        return local
    video = repo.get_video(conn, video_id)
    if video is None or video.source == "youtube":
        return None
    try:
        return sources.for_video(video, settings).playback(video_id).url or None
    except Exception:
        log.warning("no stream URL for live frame of %s", video_id, exc_info=True)
        return None
```

(add `from pathlib import Path` and use `Path | str | None` without quotes.)

`backend/unstuck/api/app.py` — add after the chats route:

```python
    def source_call(fn):
        try:
            return fn()
        except IngestError as exc:
            raise _error(502, exc.code, exc.message) from exc

    @app.get("/api/videos/{video_id}/playback")
    def playback(video_id: str, quality: int = 720, conn=Depends(get_conn)):
        video = require_video(conn, video_id)
        return asdict(source_call(lambda: sources.for_video(video, settings).playback(video_id, quality)))

    @app.get("/api/videos/{video_id}/parts")
    def parts(video_id: str, conn=Depends(get_conn)):
        video = require_video(conn, video_id)
        items = source_call(lambda: sources.for_video(video, settings).parts(video_id))
        imported = {v.id for v in repo.list_videos(conn)}
        return [{**asdict(p), "imported": p.video_id in imported} for p in items]

    @app.get("/api/bilibili/status")
    def bilibili_status():
        return sources.bilibili_status(settings)
```

- [ ] **Step 4: Run the full suite**

Run: `cd backend && uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/unstuck backend/tests
git commit -m "feat(api): playback, parts and Bilibili login endpoints; live frame from the stream"
```

---

### Task 7: Frontend — ids, API types and the two players

**Files:**
- Create: `frontend/src/lib/videoId.ts`, `frontend/src/lib/videoId.test.ts`, `frontend/src/components/playerHandle.ts`, `frontend/src/components/YouTubePlayer.tsx`, `frontend/src/components/BiliPlayer.tsx`
- Modify: `frontend/src/lib/api.ts`, `frontend/src/components/Player.tsx`, `frontend/src/App.tsx`, `frontend/src/index.css`

**Interfaces:**
- Consumes: the Task 6 endpoints.
- Produces:
  - `videoId.ts`: `type VideoSource = 'youtube' | 'bilibili'`, `sourceOfId(id: string): VideoSource | null`, `parseRoute(hash: string): string | null`, `biliPart(id: string): { bvid: string; part: number } | null`, `openVideo(id: string): void`
  - `api.ts`: `Video` + `source`, `course_id`, `part`, `part_title`, `parts_total`, `thumbnail`; `interface Playback { kind: 'youtube' | 'mp4'; url: string; expires_at: number; duration: number }`; `interface Part { part: number; title: string; duration: number; video_id: string; imported: boolean }`; `interface BilibiliStatus { logged_in: boolean; cookies_browser: string }`; `api.playback(id, quality = 720)`, `api.parts(id)`, `api.bilibiliStatus()`
  - `playerHandle.ts`: `PlayerHandle` (moved; `Player.tsx` re-exports it so existing imports keep working)
  - `<Player videoId>` picks `YouTubePlayer` or `BiliPlayer`

- [ ] **Step 1: Write the failing test**

`frontend/src/lib/videoId.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { biliPart, parseRoute, sourceOfId } from './videoId'

describe('sourceOfId', () => {
  it('recognises both id formats', () => {
    expect(sourceOfId('dQw4w9WgXcQ')).toBe('youtube')
    expect(sourceOfId('BV1Ex4y1G7nG_p3')).toBe('bilibili')
    expect(sourceOfId('BV1Ex4y1G7nG')).toBeNull()
    expect(sourceOfId('nope')).toBeNull()
  })
})

describe('parseRoute', () => {
  it('reads #/v/<id> for both sources', () => {
    expect(parseRoute('#/v/dQw4w9WgXcQ')).toBe('dQw4w9WgXcQ')
    expect(parseRoute('#/v/BV1Ex4y1G7nG_p12')).toBe('BV1Ex4y1G7nG_p12')
    expect(parseRoute('#/v/BV1Ex4y1G7nG_p0')).toBeNull()
    expect(parseRoute('#/')).toBeNull()
  })
})

describe('biliPart', () => {
  it('splits a Bilibili id', () => {
    expect(biliPart('BV1Ex4y1G7nG_p3')).toEqual({ bvid: 'BV1Ex4y1G7nG', part: 3 })
    expect(biliPart('dQw4w9WgXcQ')).toBeNull()
  })
})
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd frontend && npm test`
Expected: FAIL (cannot resolve `./videoId`).

- [ ] **Step 3: Implement**

`frontend/src/lib/videoId.ts`:

```ts
export type VideoSource = 'youtube' | 'bilibili'

const YOUTUBE_ID = /^[A-Za-z0-9_-]{11}$/
const BILIBILI_ID = /^(BV[0-9A-Za-z]{10})_p([1-9][0-9]{0,3})$/
const ROUTE = /^#\/v\/([^/]+)$/

export function sourceOfId(id: string): VideoSource | null {
  if (BILIBILI_ID.test(id)) return 'bilibili'
  if (YOUTUBE_ID.test(id)) return 'youtube'
  return null
}

export function parseRoute(hash: string): string | null {
  const match = ROUTE.exec(hash)
  return match && sourceOfId(match[1]) ? match[1] : null
}

export function biliPart(id: string): { bvid: string; part: number } | null {
  const match = BILIBILI_ID.exec(id)
  return match ? { bvid: match[1], part: Number(match[2]) } : null
}

export function openVideo(id: string): void {
  window.location.hash = `#/v/${id}`
}
```

`frontend/src/lib/api.ts`:
- add to `Video`: `source: 'youtube' | 'bilibili'`, `course_id: string | null`, `part: number | null`, `part_title: string`, `parts_total: number`, `thumbnail: string`;
- add:

```ts
export interface Playback {
  kind: 'youtube' | 'mp4'
  url: string
  expires_at: number
  duration: number
}

export interface Part {
  part: number
  title: string
  duration: number
  video_id: string
  imported: boolean
}

export interface BilibiliStatus {
  logged_in: boolean
  cookies_browser: string
}
```

- add to the `api` object:

```ts
  playback: (id: string, quality = 720) => request<Playback>(`/api/videos/${id}/playback?quality=${quality}`),
  parts: (id: string) => request<Part[]>(`/api/videos/${id}/parts`),
  bilibiliStatus: () => request<BilibiliStatus>('/api/bilibili/status'),
```

`frontend/src/components/playerHandle.ts`:

```ts
export interface PlayerHandle {
  getCurrentTime(): number
  seekTo(seconds: number): void
  pause(): void
}
```

`frontend/src/components/YouTubePlayer.tsx`: the current `Player.tsx` body unchanged except the component is `export const YouTubePlayer = forwardRef<PlayerHandle, { videoId: string }>(function YouTubePlayer(...)` and `PlayerHandle` is imported from `./playerHandle`.

`frontend/src/components/BiliPlayer.tsx`:

```tsx
import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react'
import { api } from '../lib/api'
import type { PlayerHandle } from './playerHandle'

// An expired or broken stream URL fires `error`; fetch a fresh one and resume, but not forever.
const MAX_REFRESHES = 3

export const BiliPlayer = forwardRef<PlayerHandle, { videoId: string }>(function BiliPlayer({ videoId }, ref) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const resumeAt = useRef<number | null>(null)
  const refreshes = useRef(0)
  const [src, setSrc] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(
    async (resume: number | null) => {
      try {
        const playback = await api.playback(videoId)
        resumeAt.current = resume
        setSrc(playback.url)
        setError(null)
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
      }
    },
    [videoId],
  )

  useEffect(() => {
    refreshes.current = 0
    setSrc(null)
    void load(null)
  }, [load])

  const onError = () => {
    if (refreshes.current >= MAX_REFRESHES) {
      setError('B站视频加载失败，请刷新页面重试')
      return
    }
    refreshes.current += 1
    void load(videoRef.current?.currentTime ?? 0)
  }

  const onLoadedMetadata = () => {
    const video = videoRef.current
    if (video && resumeAt.current !== null) {
      video.currentTime = resumeAt.current
      resumeAt.current = null
      void video.play().catch(() => {})
    }
  }

  useImperativeHandle(
    ref,
    () => ({
      getCurrentTime: () => videoRef.current?.currentTime ?? 0,
      seekTo: (seconds: number) => {
        const video = videoRef.current
        if (!video) return
        video.currentTime = seconds
        void video.play().catch(() => {})
      },
      pause: () => videoRef.current?.pause(),
    }),
    [],
  )

  return (
    <>
      <div className="player" hidden={error !== null}>
        {src && (
          <video
            ref={videoRef}
            src={src}
            controls
            playsInline
            preload="metadata"
            onError={onError}
            onLoadedMetadata={onLoadedMetadata}
            onPlaying={() => {
              refreshes.current = 0
            }}
          />
        )}
      </div>
      {error !== null && <div className="player-error">{error}</div>}
    </>
  )
})
```

`frontend/src/components/Player.tsx` (replace):

```tsx
import { forwardRef } from 'react'
import { sourceOfId } from '../lib/videoId'
import { BiliPlayer } from './BiliPlayer'
import type { PlayerHandle } from './playerHandle'
import { YouTubePlayer } from './YouTubePlayer'

export type { PlayerHandle }

export const Player = forwardRef<PlayerHandle, { videoId: string }>(function Player({ videoId }, ref) {
  return sourceOfId(videoId) === 'bilibili' ? (
    <BiliPlayer ref={ref} videoId={videoId} />
  ) : (
    <YouTubePlayer ref={ref} videoId={videoId} />
  )
})
```

`frontend/src/App.tsx`: replace `readVideoId` with `() => parseRoute(window.location.hash)` (import from `./lib/videoId`) and `open` with `openVideo`.

`frontend/src/index.css` — after the `.player iframe` rule:

```css
.player video {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  background: #000;
}
```

- [ ] **Step 4: Run tests and build**

Run: `cd frontend && npm test && npm run build`
Expected: tests pass; build clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(frontend): Bilibili ids and player backed by refreshable stream URLs"
```

---

### Task 8: Frontend — library courses, parts list, import box

**Files:**
- Create: `frontend/src/lib/library.ts`, `frontend/src/lib/library.test.ts`, `frontend/src/components/PartsList.tsx`
- Modify: `frontend/src/components/Library.tsx`, `frontend/src/components/ImportView.tsx`, `frontend/src/components/VideoPage.tsx`, `frontend/src/index.css`

**Interfaces:**
- Consumes: Task 7 `api.*`, `openVideo`, `biliPart`.
- Produces: `library.ts`: `type LibraryItem = { kind: 'video'; video: Video } | { kind: 'course'; courseId: string; title: string; channel: string; thumbnail: string; partsTotal: number; parts: Video[]; latest: Video }`, `groupLibrary(videos: Video[]): LibraryItem[]`, `thumbnailFor(video: Video): string`; `<PartsList video={video} />`.

- [ ] **Step 1: Write the failing test**

`frontend/src/lib/library.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import type { Video } from './api'
import { groupLibrary, thumbnailFor } from './library'

function video(over: Partial<Video>): Video {
  return {
    id: 'dQw4w9WgXcQ', url: '', title: 'T', channel: 'C', duration: 60, status: 'ready', ask_ready: true,
    stage_states: {}, visual_mode: 'full', outline: null, error_code: null, error_message: null, created_at: '',
    source: 'youtube', course_id: null, part: null, part_title: '', parts_total: 1, thumbnail: '', ...over,
  }
}

describe('groupLibrary', () => {
  it('groups parts of one course into a card placed where its newest part is', () => {
    const p3 = video({ id: 'BV1Ex4y1G7nG_p3', source: 'bilibili', course_id: 'BV1Ex4y1G7nG', part: 3, parts_total: 7, title: '导数' })
    const yt = video({ id: 'aaaaaaaaaaa' })
    const p1 = video({ id: 'BV1Ex4y1G7nG_p1', source: 'bilibili', course_id: 'BV1Ex4y1G7nG', part: 1, parts_total: 7, title: '导数' })
    const single = video({ id: 'BV1im411d7Eu_p1', source: 'bilibili', course_id: 'BV1im411d7Eu', part: 1, parts_total: 1 })

    const items = groupLibrary([p3, yt, p1, single]) // newest first, as the API returns them

    expect(items.map((i) => i.kind)).toEqual(['course', 'video', 'video'])
    const course = items[0]
    if (course.kind !== 'course') throw new Error('expected course')
    expect(course.parts.map((v) => v.part)).toEqual([1, 3])
    expect(course.latest.id).toBe('BV1Ex4y1G7nG_p3')
    expect(course.partsTotal).toBe(7)
  })
})

describe('thumbnailFor', () => {
  it('uses YouTube thumbnails or the stored Bilibili cover', () => {
    expect(thumbnailFor(video({ id: 'aaaaaaaaaaa' }))).toBe('https://i.ytimg.com/vi/aaaaaaaaaaa/mqdefault.jpg')
    expect(thumbnailFor(video({ source: 'bilibili', thumbnail: 'https://i0.hdslb.com/a.jpg' }))).toBe('https://i0.hdslb.com/a.jpg')
  })
})
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd frontend && npm test`
Expected: FAIL (cannot resolve `./library`).

- [ ] **Step 3: Implement**

`frontend/src/lib/library.ts`:

```ts
import type { Video } from './api'

export type LibraryItem =
  | { kind: 'video'; video: Video }
  | {
      kind: 'course'
      courseId: string
      title: string
      channel: string
      thumbnail: string
      partsTotal: number
      parts: Video[]
      latest: Video
    }

/** Videos arrive newest first; parts of one multi-part course become a single card at its newest part. */
export function groupLibrary(videos: Video[]): LibraryItem[] {
  const items: LibraryItem[] = []
  const courses = new Map<string, Extract<LibraryItem, { kind: 'course' }>>()
  for (const video of videos) {
    if (video.source !== 'bilibili' || !video.course_id || video.parts_total <= 1) {
      items.push({ kind: 'video', video })
      continue
    }
    const existing = courses.get(video.course_id)
    if (existing) {
      existing.parts.push(video)
      continue
    }
    const course = {
      kind: 'course' as const,
      courseId: video.course_id,
      title: video.title,
      channel: video.channel,
      thumbnail: video.thumbnail,
      partsTotal: video.parts_total,
      parts: [video],
      latest: video,
    }
    courses.set(video.course_id, course)
    items.push(course)
  }
  for (const course of courses.values()) course.parts.sort((a, b) => (a.part ?? 0) - (b.part ?? 0))
  return items
}

export function thumbnailFor(video: Video): string {
  return video.source === 'youtube' ? `https://i.ytimg.com/vi/${video.id}/mqdefault.jpg` : video.thumbnail
}
```

`frontend/src/components/ImportView.tsx` — in `ImportForm`: placeholder `粘贴 YouTube 或 B站视频链接`, aria-label `YouTube 或 B站视频链接`.

`frontend/src/components/Library.tsx` (replace the body):

```tsx
import { useEffect, useState } from 'react'
import { api, type BilibiliStatus, type Video } from '../lib/api'
import { groupLibrary, thumbnailFor } from '../lib/library'
import { formatTime } from '../lib/timestamps'
import { ImportForm } from './ImportView'

const STATUS_LABELS: Record<Video['status'], string> = {
  importing: '导入中',
  ready: '可以学习',
  failed: '导入失败',
}
const SOURCE_LABELS: Record<Video['source'], string> = { youtube: 'YouTube', bilibili: 'B站' }
export const BILIBILI_LOGIN_WARNING =
  'B站未登录：画质较低、字幕将用语音转写（较慢）。在 Chrome 登录 bilibili.com 后刷新即可恢复。'

function Card({ thumbnail, source, title, meta, onClick }: {
  thumbnail: string; source: Video['source']; title: string; meta: string; onClick: () => void
}) {
  return (
    <button type="button" className="video-card" onClick={onClick}>
      <span className="thumb">
        {thumbnail ? <img src={thumbnail} alt="" loading="lazy" referrerPolicy="no-referrer" /> : null}
        <span className={`badge badge-${source}`}>{SOURCE_LABELS[source]}</span>
      </span>
      <span className="video-title">{title}</span>
      <span className="muted">{meta}</span>
    </button>
  )
}

export function Library({ onOpen }: { onOpen: (id: string) => void }) {
  const [videos, setVideos] = useState<Video[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [bili, setBili] = useState<BilibiliStatus | null>(null)

  useEffect(() => {
    api
      .listVideos()
      .then(setVideos)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)))
    api.bilibiliStatus().then(setBili).catch(() => setBili(null))
  }, [])

  return (
    <main className="library">
      <section className="hero">
        <h1>卡住了？暂停提问。</h1>
        <p className="muted">粘贴一个 YouTube 或 B站教学视频链接。边看边问，AI 会结合你正在看的那一秒和当前画面来讲解。</p>
        <ImportForm onImported={onOpen} />
        {bili && !bili.logged_in && <div className="notice warning">{BILIBILI_LOGIN_WARNING}</div>}
      </section>
      <section>
        <h2>我的视频</h2>
        {error && <div className="form-error">{error}</div>}
        {videos?.length === 0 && <p className="muted">还没有导入视频。</p>}
        <ul className="video-list">
          {videos &&
            groupLibrary(videos).map((item) =>
              item.kind === 'video' ? (
                <li key={item.video.id}>
                  <Card
                    thumbnail={thumbnailFor(item.video)}
                    source={item.video.source}
                    title={item.video.title || item.video.id}
                    meta={[item.video.channel, item.video.duration ? formatTime(item.video.duration) : '', STATUS_LABELS[item.video.status]]
                      .filter(Boolean)
                      .join(' · ')}
                    onClick={() => onOpen(item.video.id)}
                  />
                </li>
              ) : (
                <li key={item.courseId}>
                  <Card
                    thumbnail={item.thumbnail}
                    source="bilibili"
                    title={item.title || item.courseId}
                    meta={[item.channel, `已导入 ${item.parts.length}/${item.partsTotal} 集`].filter(Boolean).join(' · ')}
                    onClick={() => onOpen(item.latest.id)}
                  />
                </li>
              ),
            )}
        </ul>
      </section>
    </main>
  )
}
```

`frontend/src/components/PartsList.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { api, type Part, type Video } from '../lib/api'
import { formatTime } from '../lib/timestamps'
import { biliPart, openVideo } from '../lib/videoId'

export function PartsList({ video }: { video: Video }) {
  const [parts, setParts] = useState<Part[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<number | null>(null)

  useEffect(() => {
    let cancelled = false
    api
      .parts(video.id)
      .then((list) => {
        if (!cancelled) setParts(list)
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      })
    return () => {
      cancelled = true
    }
  }, [video.id])

  const importPart = async (part: Part) => {
    const ids = biliPart(part.video_id)
    if (!ids) return
    setBusy(part.part)
    setError(null)
    try {
      const imported = await api.importVideo(`https://www.bilibili.com/video/${ids.bvid}?p=${ids.part}`)
      openVideo(imported.id)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(null)
    }
  }

  return (
    <section className="parts">
      <h2>选集（共 {video.parts_total} 集）</h2>
      {error && <div className="form-error">{error}</div>}
      <ol>
        {parts?.map((part) => (
          <li key={part.part} className={part.video_id === video.id ? 'current' : undefined}>
            <span className="ts">P{part.part}</span>
            <span className="part-title">{part.title}</span>
            <span className="muted">{formatTime(part.duration)}</span>
            {part.video_id === video.id ? (
              <span className="muted">正在看</span>
            ) : part.imported ? (
              <button type="button" className="btn btn-small" onClick={() => openVideo(part.video_id)}>
                观看
              </button>
            ) : (
              <button type="button" className="btn btn-small" disabled={busy !== null} onClick={() => void importPart(part)}>
                {busy === part.part ? '导入中…' : '导入'}
              </button>
            )}
          </li>
        ))}
      </ol>
    </section>
  )
}
```

`frontend/src/components/VideoPage.tsx`:
- import `PartsList`;
- under the heading, when `video.parts_total > 1`: `<p className="muted">P{video.part} · {video.part_title}</p>` (render the channel line as before);
- after the outline: `{video.source === 'bilibili' && video.parts_total > 1 && <PartsList video={video} />}`.

`frontend/src/index.css` — append:

```css
.thumb {
  position: relative;
  display: block;
}

.thumb img {
  display: block;
  width: 100%;
  aspect-ratio: 16 / 9;
  object-fit: cover;
  background: var(--surface-2);
}

.badge {
  position: absolute;
  top: 8px;
  left: 8px;
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 600;
  color: #fff;
}

.badge-youtube {
  background: #c4302b;
}

.badge-bilibili {
  background: #00a1d6;
}

.notice.warning {
  margin: 0 0 24px;
  color: var(--text);
  background: color-mix(in srgb, #f5b301 22%, var(--surface));
}

.parts h2 {
  margin: 20px 0 6px;
  font-size: 16px;
}

.parts ol {
  display: grid;
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.parts li {
  display: grid;
  grid-template-columns: 48px minmax(0, 1fr) auto auto;
  align-items: center;
  gap: 10px;
  padding: 6px 8px;
  border-radius: 6px;
}

.parts li.current {
  background: var(--surface-2);
}

.parts .ts {
  color: var(--accent);
  font-variant-numeric: tabular-nums;
}

.parts .part-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
```

Remove the now-unused `.video-card img` rule if `.thumb img` replaces it (check with grep; keep whichever the markup uses).

- [ ] **Step 4: Run tests and build**

Run: `cd frontend && npm test && npm run build`
Expected: pass, clean build.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(frontend): course cards, parts list and Bilibili login warning"
```

---

### Task 9: Real-world verification and docs

**Files:**
- Create: `backend/tests/test_integration_bilibili.py`
- Modify: `README.md`, `docs/challenges.md`

**Interfaces:** none new.

- [ ] **Step 1: Write the slow test**

`backend/tests/test_integration_bilibili.py`:

```python
"""Real Bilibili import + answer with the user's Chrome login.

Run: cd backend && UNSTUCK_IT_BILI_URL="https://www.bilibili.com/video/BV…?p=1" uv run pytest -m slow -s tests/test_integration_bilibili.py
"""

import os
from dataclasses import replace

import pytest

from unstuck import sources
from unstuck.config import load_settings
from unstuck.db import connect, repo
from unstuck.ingest.pipeline import Pipeline, StageContext
from unstuck.llm.factory import build_llm
from unstuck.tutor.answer import stream_answer

URL = os.getenv("UNSTUCK_IT_BILI_URL")

pytestmark = [pytest.mark.slow, pytest.mark.skipif(not URL, reason="set UNSTUCK_IT_BILI_URL")]


def test_import_play_and_answer_bilibili(tmp_path):
    settings = replace(load_settings(), data_dir=tmp_path)
    print("bilibili login:", sources.bilibili_status(settings))
    ref = sources.resolve(URL, settings)
    llm = build_llm(settings)
    conn = connect(settings.db_path, settings.embed_dim)
    try:
        repo.insert_video(conn, ref.video_id, ref.url, source=ref.source, course_id=ref.course_id, part=ref.part)
        Pipeline().run(StageContext(conn=conn, llm=llm, settings=settings, video_id=ref.video_id))
        video = repo.get_video(conn, ref.video_id)
        print("stages:", video.stage_states, "error:", video.error_code, video.error_message)
        assert video.status == "ready" and video.source == "bilibili" and video.duration > 0
        assert repo.get_cues(conn, ref.video_id)

        playback = sources.for_video(video, settings).playback(ref.video_id)
        assert playback.kind == "mp4" and playback.url.startswith("https://")

        events = list(stream_answer(conn, llm, settings, ref.video_id, "这一段在讲什么？", min(60.0, video.duration / 2)))
        print("answer:", "".join(e.get("text", "") for e in events))
        assert events[-1]["type"] == "done", events[-1]
    finally:
        conn.close()
```

- [ ] **Step 2: Confirm it is excluded by default**

Run: `cd backend && uv run pytest -q`
Expected: all pass, the new test deselected.

- [ ] **Step 3: Update the README**

In `README.md`:
- intro: "Unstuck is a one-on-one AI tutor that sits next to a YouTube **or Bilibili** lecture…";
- add a section after "Run it locally":

```markdown
## Bilibili

Paste a `bilibili.com/video/BV…` link (or a `b23.tv` share link). Multi-part courses import one part at a time; the video page lists the other parts with an import button.

Unstuck reads your Bilibili login from Chrome (`BILIBILI_COOKIES_BROWSER` in `backend/.env`; macOS asks once for Keychain access to "Chrome Safe Storage"). Logged in, it uses Bilibili's AI subtitles and 720p playback; logged out it still works, but transcribes with Whisper and plays at lower quality. Bilibili videos play straight from Bilibili's CDN in the browser's own player, so watching starts immediately.

This relies on Bilibili's unofficial web APIs; it is for personal use and demos.
```

- [ ] **Step 4: Run the real test (controller does this with the user's login)**

Run: `cd backend && UNSTUCK_IT_BILI_URL="<a 5–25 min Bilibili lecture with AI subtitles>" uv run pytest -m slow -s tests/test_integration_bilibili.py`
Expected: PASS; printed stages all `done`, an answer in Chinese.

- [ ] **Step 5: Append implementation findings to `docs/challenges.md`**

Add an entry under "第二阶段：B站视频来源（实现阶段）" for every non-obvious problem met while implementing Tasks 2–9 (if none, write one line saying so).

- [ ] **Step 6: Commit**

```bash
git add backend/tests/test_integration_bilibili.py README.md docs/challenges.md
git commit -m "test(bilibili): slow end-to-end test; docs for Bilibili support"
```

The controller then does a manual browser walkthrough: import a multi-part course part → plays immediately → ask (auto-pause, @time) → timestamp seek → parts list → import the next part → library shows one course card with "已导入 2/N 集" → log out of Bilibili in Chrome → warning appears and import still works.

---

### Task 10 (optional): 1080p via DASH with automatic 720p fallback

**Do this task only if Tasks 1–9 are done and verified.** If it starts costing more than one more review round, stop and keep 720p (user's instruction).

**Files:**
- Modify: `backend/unstuck/sources/base.py`, `backend/unstuck/sources/bilibili/parse.py`, `backend/unstuck/sources/bilibili/client.py`, `backend/unstuck/sources/bilibili/source.py`, `backend/unstuck/api/app.py`
- Create: `frontend/src/lib/mpd.ts`, `frontend/src/lib/mpd.test.ts`
- Modify: `frontend/src/components/BiliPlayer.tsx`, `frontend/src/lib/api.ts`, `frontend/package.json`
- Test: `backend/tests/test_bili_parse.py`, `backend/tests/test_api.py`

**Interfaces:**
- Produces:
  - `base.SegmentBase(initialization: str, index_range: str)`, `base.DashStream(url, codecs, mime_type, bandwidth, width, height, segment_base)`; `Playback` gains `video: DashStream | None = None`, `audio: DashStream | None = None`
  - `parse.dash_playback(data, duration, now) -> Playback | None` (H.264 `codecid == 7`, highest `id` ≤ 80, highest-bandwidth audio; None if no H.264 track)
  - `BiliClient.playurl_dash(bvid, cid)` (signed, `qn=80, fnval=16, fourk=0`)
  - `BilibiliSource.playback(video_id, quality)`: `quality >= 1080` tries DASH first and falls back to MP4 on any error or None
  - `GET /api/videos/{id}/stream/{video|audio}`: proxies the DASH stream with Bilibili's Referer, forwarding `Range`; caches the playback per video until 60 s before expiry; on an upstream 403 refreshes once
  - `mpd.ts`: `buildMpd(playback: Playback, videoUrl: string, audioUrl: string): string`

- [ ] **Step 1: Backend tests**

Append to `backend/tests/test_bili_parse.py`:

```python
DASH = {"dash": {
    "video": [
        {"id": 80, "codecid": 12, "codecs": "hev1.1.6.L120.90", "base_url": "https://up/v80h.m4s", "bandwidth": 900000,
         "width": 1920, "height": 1080, "mime_type": "video/mp4", "segment_base": {"initialization": "0-900", "index_range": "901-3000"}},
        {"id": 80, "codecid": 7, "codecs": "avc1.640032", "base_url": "https://up/v80.m4s?deadline=1791671421", "bandwidth": 1200000,
         "width": 1920, "height": 1080, "mime_type": "video/mp4", "segment_base": {"initialization": "0-1000", "index_range": "1001-4000"}},
        {"id": 64, "codecid": 7, "codecs": "avc1.640028", "base_url": "https://up/v64.m4s", "bandwidth": 700000,
         "width": 1280, "height": 720, "mime_type": "video/mp4", "segment_base": {"initialization": "0-800", "index_range": "801-3000"}},
    ],
    "audio": [
        {"id": 30216, "codecs": "mp4a.40.2", "base_url": "https://up/a1.m4s", "bandwidth": 67000, "mime_type": "audio/mp4",
         "segment_base": {"initialization": "0-700", "index_range": "701-2000"}},
        {"id": 30280, "codecs": "mp4a.40.2", "base_url": "https://up/a3.m4s", "bandwidth": 190000, "mime_type": "audio/mp4",
         "segment_base": {"initialization": "0-700", "index_range": "701-2100"}},
    ],
}}


def test_dash_playback_picks_h264_1080_and_best_audio():
    pb = parse.dash_playback(DASH, 1272.0, now=0)
    assert pb.kind == "dash" and pb.duration == 1272.0 and pb.expires_at == 1791671421.0
    assert (pb.video.url, pb.video.codecs, pb.video.height) == ("https://up/v80.m4s?deadline=1791671421", "avc1.640032", 1080)
    assert pb.video.segment_base.initialization == "0-1000" and pb.video.segment_base.index_range == "1001-4000"
    assert pb.audio.url == "https://up/a3.m4s"


def test_dash_playback_without_h264_is_none():
    data = {"dash": {"video": [DASH["dash"]["video"][0]], "audio": DASH["dash"]["audio"]}}
    assert parse.dash_playback(data, 10.0, now=0) is None
```

Append to `backend/tests/test_api.py` a proxy test using a fake upstream:

```python
def test_stream_proxy_forwards_range(client, conn, monkeypatch):
    from unstuck.api import app as app_module
    from unstuck.sources.base import DashStream, SegmentBase

    repo.insert_video(conn, "BV1Ex4y1G7nG_p1", "u", source="bilibili", course_id="BV1Ex4y1G7nG", part=1)
    stream = DashStream(url="https://up/v.m4s", codecs="avc1", mime_type="video/mp4", bandwidth=1, width=1920,
                        height=1080, segment_base=SegmentBase("0-1", "2-3"))

    class DashSource(FakeBiliSource):
        def playback(self, video_id, quality=720):
            return Playback(kind="dash", expires_at=9e9, duration=10.0, video=stream, audio=stream)

    seen = {}

    class Upstream:
        status = 206
        headers = {"Content-Type": "video/mp4", "Content-Range": "bytes 0-3/100", "Content-Length": "4"}

        def __init__(self):
            self.chunks = [b"abcd", b""]

        def read(self, n):
            return self.chunks.pop(0)

        def close(self):
            pass

    def fake_open(url, headers):
        seen["url"], seen["headers"] = url, headers
        return Upstream()

    monkeypatch.setattr(sources, "for_video", lambda video, settings: DashSource())
    monkeypatch.setattr(app_module, "_open_upstream", fake_open)
    r = client.get("/api/videos/BV1Ex4y1G7nG_p1/stream/video", headers={"Range": "bytes=0-3"})
    assert r.status_code == 206 and r.content == b"abcd"
    assert r.headers["content-range"] == "bytes 0-3/100"
    assert seen["url"] == "https://up/v.m4s"
    assert seen["headers"]["Range"] == "bytes=0-3" and seen["headers"]["Referer"] == "https://www.bilibili.com/"
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd backend && uv run pytest tests/test_bili_parse.py tests/test_api.py -q`
Expected: FAIL (`dash_playback` missing, `DashStream` missing, 404 for the stream route).

- [ ] **Step 3: Backend implementation**

`backend/unstuck/sources/base.py` — add before `Playback`, and extend `Playback`:

```python
@dataclass(frozen=True)
class SegmentBase:
    initialization: str
    index_range: str


@dataclass(frozen=True)
class DashStream:
    url: str
    codecs: str
    mime_type: str
    bandwidth: int
    width: int
    height: int
    segment_base: SegmentBase
```

and in `Playback` add `video: DashStream | None = None` and `audio: DashStream | None = None`, updating its docstring to mention `"dash"`.

`backend/unstuck/sources/bilibili/parse.py` — add:

```python
def _dash_stream(item: dict) -> DashStream:
    seg = item.get("segment_base") or item.get("SegmentBase") or {}
    return DashStream(
        url=item.get("base_url") or item.get("baseUrl"),
        codecs=item.get("codecs") or "",
        mime_type=item.get("mime_type") or item.get("mimeType") or "",
        bandwidth=int(item.get("bandwidth") or 0),
        width=int(item.get("width") or 0),
        height=int(item.get("height") or 0),
        segment_base=SegmentBase(
            initialization=seg.get("initialization") or seg.get("Initialization") or "",
            index_range=seg.get("index_range") or seg.get("indexRange") or "",
        ),
    )


def dash_playback(data: dict, duration: float, now: float) -> Playback | None:
    """Best H.264 (browser-safe) video up to 1080p plus the best audio, or None."""
    dash = data.get("dash") or {}
    videos = [v for v in dash.get("video") or [] if v.get("codecid") == 7 and int(v.get("id") or 0) <= 80]
    audios = dash.get("audio") or []
    if not videos or not audios:
        return None
    video = _dash_stream(max(videos, key=lambda v: (int(v["id"]), int(v.get("bandwidth") or 0))))
    audio = _dash_stream(max(audios, key=lambda a: int(a.get("bandwidth") or 0)))
    deadline = (urllib.parse.parse_qs(urllib.parse.urlparse(video.url).query).get("deadline") or [""])[0]
    expires_at = float(deadline) if deadline.isdigit() else now + DEFAULT_URL_LIFETIME_S
    return Playback(kind="dash", expires_at=expires_at, duration=duration, video=video, audio=audio)
```

(import `DashStream, SegmentBase` from `unstuck.sources.base`.)

`backend/unstuck/sources/bilibili/client.py` — add:

```python
    def playurl_dash(self, bvid: str, cid: int) -> dict:
        return self._signed("/x/player/wbi/playurl", {"bvid": bvid, "cid": cid, "qn": 80, "fnval": 16, "fourk": 0})
```

`backend/unstuck/sources/bilibili/source.py` — replace `playback`:

```python
    def playback(self, video_id: str, quality: int = 720) -> Playback:
        info = self.fetch_info(video_id)
        if quality >= 1080:
            try:
                dash = parse.dash_playback(self.client.playurl_dash(info["bvid"], info["cid"]), info["duration"], now=self._clock())
                if dash is not None:
                    return dash
            except Exception:
                log.warning("1080p unavailable for %s, using 720p", video_id, exc_info=True)
        data = self.client.playurl_mp4(info["bvid"], info["cid"])
        return parse.mp4_playback(data, info["duration"], now=self._clock())
```

`backend/unstuck/api/app.py` — module level:

```python
import time
import urllib.error
import urllib.request

from fastapi import Request
from fastapi.responses import StreamingResponse

from unstuck.sources.bilibili.client import HEADERS as BILIBILI_HEADERS

_PASS_HEADERS = ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges")
_CHUNK = 256 * 1024


def _open_upstream(url: str, headers: dict):
    """Open the CDN response (urllib raises HTTPError for 4xx/5xx)."""
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30)
```

inside `create_app` (after the `playback` route):

```python
    dash_cache: dict[str, Playback] = {}
    dash_lock = threading.Lock()

    def dash_playback(video: Video, refresh: bool = False) -> Playback:
        with dash_lock:
            cached = dash_cache.get(video.id)
            if refresh or cached is None or cached.expires_at - 60 < time.time():
                cached = source_call(lambda: sources.for_video(video, settings).playback(video.id, 1080))
                if cached.kind != "dash":
                    raise _error(409, "NO_DASH", "这个视频没有 1080p 流")
                dash_cache[video.id] = cached
            return cached

    @app.get("/api/videos/{video_id}/stream/{kind}")
    def stream(video_id: str, kind: str, request: Request, conn=Depends(get_conn)):
        if kind not in ("video", "audio"):
            raise _error(404, "NOT_FOUND", "未知的流")
        video = require_video(conn, video_id)
        headers = dict(BILIBILI_HEADERS)
        if "range" in request.headers:
            headers["Range"] = request.headers["range"]
        for refresh in (False, True):
            pb = dash_playback(video, refresh)
            url = (pb.video if kind == "video" else pb.audio).url
            try:
                upstream = _open_upstream(url, headers)
                break
            except urllib.error.HTTPError as exc:
                if exc.code != 403 or refresh:  # 403 = expired URL; refresh once
                    raise _error(502, "STREAM_FAILED", "B站视频流获取失败") from exc

        def body():
            try:
                while chunk := upstream.read(_CHUNK):
                    yield chunk
            finally:
                upstream.close()

        out_headers = {k: upstream.headers[k] for k in _PASS_HEADERS if upstream.headers.get(k)}
        return StreamingResponse(body(), status_code=upstream.status, headers=out_headers)
```

(import `Playback` from `unstuck.sources.base`.)

Run: `cd backend && uv run pytest -q` → all pass.

- [ ] **Step 4: Frontend MPD builder with a test**

`frontend/src/lib/mpd.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import type { Playback } from './api'
import { buildMpd } from './mpd'

const pb: Playback = {
  kind: 'dash', url: '', expires_at: 0, duration: 1272.5,
  video: { url: 'x', codecs: 'avc1.640032', mime_type: 'video/mp4', bandwidth: 1200000, width: 1920, height: 1080,
           segment_base: { initialization: '0-1000', index_range: '1001-4000' } },
  audio: { url: 'y', codecs: 'mp4a.40.2', mime_type: 'audio/mp4', bandwidth: 190000, width: 0, height: 0,
           segment_base: { initialization: '0-700', index_range: '701-2100' } },
}

describe('buildMpd', () => {
  it('describes both streams through the local proxy', () => {
    const mpd = buildMpd(pb, 'http://localhost:5173/api/videos/BV_p1/stream/video', 'http://localhost:5173/api/videos/BV_p1/stream/audio')
    expect(mpd).toContain('mediaPresentationDuration="PT1272.5S"')
    expect(mpd).toContain('<BaseURL>http://localhost:5173/api/videos/BV_p1/stream/video</BaseURL>')
    expect(mpd).toContain('codecs="avc1.640032"')
    expect(mpd).toContain('<SegmentBase indexRange="1001-4000"><Initialization range="0-1000"/></SegmentBase>')
    expect(mpd).toContain('<BaseURL>http://localhost:5173/api/videos/BV_p1/stream/audio</BaseURL>')
  })
})
```

`frontend/src/lib/api.ts`: extend `Playback`:

```ts
export interface DashStream {
  url: string
  codecs: string
  mime_type: string
  bandwidth: number
  width: number
  height: number
  segment_base: { initialization: string; index_range: string }
}

export interface Playback {
  kind: 'youtube' | 'mp4' | 'dash'
  url: string
  expires_at: number
  duration: number
  video?: DashStream | null
  audio?: DashStream | null
}
```

`frontend/src/lib/mpd.ts`:

```ts
import type { DashStream, Playback } from './api'

function escapeXml(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;')
}

function representation(id: string, stream: DashStream, baseUrl: string): string {
  const size = stream.width ? ` width="${stream.width}" height="${stream.height}"` : ''
  return (
    `<AdaptationSet mimeType="${stream.mime_type}" segmentAlignment="true">` +
    `<Representation id="${id}" codecs="${escapeXml(stream.codecs)}" bandwidth="${stream.bandwidth}"${size}>` +
    `<BaseURL>${escapeXml(baseUrl)}</BaseURL>` +
    `<SegmentBase indexRange="${stream.segment_base.index_range}"><Initialization range="${stream.segment_base.initialization}"/></SegmentBase>` +
    `</Representation></AdaptationSet>`
  )
}

/** A static, single-period MPD for Bilibili's SegmentBase DASH streams, served through the backend proxy. */
export function buildMpd(playback: Playback, videoUrl: string, audioUrl: string): string {
  if (!playback.video || !playback.audio) throw new Error('not a DASH playback')
  return (
    `<?xml version="1.0" encoding="UTF-8"?>` +
    `<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static" profiles="urn:mpeg:dash:profile:isoff-on-demand:2011" ` +
    `minBufferTime="PT2S" mediaPresentationDuration="PT${playback.duration}S"><Period>` +
    representation('video', playback.video, videoUrl) +
    representation('audio', playback.audio, audioUrl) +
    `</Period></MPD>`
  )
}
```

Run: `cd frontend && npm test` → pass.

- [ ] **Step 5: dash.js in BiliPlayer with silent fallback**

Run: `cd frontend && npm install dashjs@4` (record the exact version installed in the report).

In `BiliPlayer.tsx`:
- add state `mode: 'dash' | 'mp4'` starting at `'dash'`;
- in `load`: if `mode === 'dash'`, call `api.playback(videoId, 1080)`; if it returns `kind === 'dash'`, build the MPD with `buildMpd(pb, `${location.origin}/api/videos/${videoId}/stream/video`, `${location.origin}/api/videos/${videoId}/stream/audio`)`, make a Blob URL (`application/dash+xml`), `const dashjs = await import('dashjs')`, create `dashjs.MediaPlayer().create()`, `player.initialize(videoRef.current, blobUrl, false)`, and on `dashjs.MediaPlayer.events.ERROR` (or any thrown error) destroy it and switch `mode` to `'mp4'`; otherwise (kind `mp4` or any error) switch to `'mp4'`;
- in `mp4` mode keep the Task 7 behaviour (`src` on the `<video>`), including resume after refresh;
- always render the `<video>` element (no `src` in dash mode, so dash.js can attach to it);
- destroy the dash.js player and revoke the Blob URL on unmount / video change.

Run: `cd frontend && npm test && npm run build` → pass, clean build (dash.js lazy-loaded into its own chunk).

- [ ] **Step 6: Manual check (controller)**

Open a Bilibili video: the player reports `videoHeight === 1080` (`document.querySelector('video').videoHeight` in the browser pane), seeking and asking work; stop the backend's stream route (e.g. temporarily make `playurl_dash` raise) → the player silently plays 720p.

- [ ] **Step 7: Record and commit**

Append any non-obvious findings to `docs/challenges.md`, then:

```bash
git add backend frontend docs/challenges.md
git commit -m "feat(bilibili): optional 1080p via DASH proxy and dash.js with 720p fallback"
```

---

**Done when:** Tasks 1–9 pass (`uv run pytest`, `npm test`, `npm run build`), the slow Bilibili test passes with the user's login, the browser walkthrough succeeds, and `docs/challenges.md` is up to date. Task 10 is a bonus.
