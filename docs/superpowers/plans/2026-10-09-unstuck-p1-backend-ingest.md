# Unstuck Part 1 — Backend Foundation & Ingest Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Paste a YouTube video ID into the backend and get a resumable 8-stage import that produces a multimodal timeline (transcript cues, keyframes with vision descriptions, 30–60 s segments, vector index, outline) stored in SQLite.

**Architecture:** A Python package `unstuck` under `backend/`. Pure logic (caption parsing, keyframe planning, segmentation, JSON repair, pipeline state machine) is separated from thin IO wrappers (yt-dlp, ffmpeg, PySceneDetect, faster-whisper, model APIs) so the logic is unit-tested with a `FakeLLM` and no network. All model calls go through one `LLM` protocol.

**Tech Stack:** Python 3.12, uv, pytest, SQLite + sqlite-vec, yt-dlp, ffmpeg, PySceneDetect, faster-whisper, imagehash, json-repair, openai SDK (OpenAI-compatible Zhipu endpoint), sentence-transformers (BAAI/bge-m3).

This is Part 1 of 4. Order: **P1 backend ingest → P2 tutor & API → P3 frontend → P4 integration & eval.** Spec: `docs/superpowers/specs/2026-10-09-unstuck-design.md`.

## Global Constraints

- Backend: Python 3.11+ (pinned to 3.12 via `.python-version`), FastAPI. Frontend: React + TypeScript + Vite.
- Storage: SQLite; vector search via sqlite-vec.
- All model calls go through the `unstuck.llm` interface (`chat` / `chat_stream` / `vision` / `embed`); switching to Claude/Gemini must only need config changes.
- Default models (verified free on open.bigmodel.cn pricing page, 2026-10-09): text `glm-4.7-flash`, vision `glm-4.6v-flash`, base URL `https://open.bigmodel.cn/api/paas/v4/`; local embedding `BAAI/bge-m3` (1024 dims).
- Local, single user, no login, no cloud deployment.
- Videos longer than 2 hours (7200 s) are rejected.
- Keyframes: scene min 5 s, max 45 s (force split), perceptual-hash dedupe, at most one frame per 10 s on average, at most 300 frames per video.
- Segments: target 30–60 s; cut preferably at sentence ends, then at scene changes.
- Unit tests never call external APIs or download models; they use `FakeLLM`.
- Only borrow ideas from the reference projects; never copy their code.
- User-facing error messages are in Chinese (the spec's UI language).

## File Structure (Part 1)

```
.gitignore
backend/
  .python-version              3.12
  pyproject.toml               deps, pytest config (slow marker)
  .env.example                 model/API config template
  scripts/smoke_llm.py         manual check of real model endpoints
  unstuck/
    __init__.py
    config.py                  Settings dataclass + load_settings()
    timefmt.py                 fmt_ts / parse_ts
    errors.py                  IngestError(code, message)
    models.py                  shared dataclasses (Cue, Keyframe, Segment, Video, Chat…)
    jsonfix.py                 loads_lenient(): tolerant JSON parsing
    media.py                   extract_frame() via ffmpeg
    db/
      __init__.py              connect(): sqlite + sqlite-vec + schema
      schema.py                DDL
      repo.py                  all table access functions
    llm/
      __init__.py              re-exports
      base.py                  LLM protocol
      fake.py                  FakeLLM for tests
      retry.py                 with_retry() exponential backoff
      openai_compat.py         OpenAI-compatible client (Zhipu by default)
      embedder.py              local bge-m3 embedder
      factory.py               build_llm(settings)
    ingest/
      __init__.py
      captions.py              json3 → cues; sentence-end detection
      youtube.py               URL parsing, metadata checks, captions, downloads
      transcribe.py            faster-whisper fallback
      keyframes.py             scene planning, phash dedupe, extraction
      vision.py                frame → FrameDescription with JSON repair
      timeline.py              cues + keyframes → segments
      indexer.py               embed segments into sqlite-vec; search
      outline.py               chapter outline generation; current_chapter()
      pipeline.py              stage state machine (resume, downstream reset)
      stages.py                the 8 real stage functions
  tests/
    __init__.py
    conftest.py                settings / conn / fake_llm / sample_video fixtures
    factories.py               make_keyframe()
    test_*.py
```

Note vs spec: the spec lists `backend/ingest/…`; we nest everything under the `unstuck` package (`backend/unstuck/ingest/…`) so generic names like `db`/`api` don't collide on `sys.path`. We also add a `cues` table (the spec's data model needs somewhere to keep the transcript between stages) and `media.py` (ffmpeg frame grab shared by ingest and tutor).

---

### Task 1: Backend scaffold, config, time formatting, errors

**Files:**
- Create: `.gitignore`, `backend/.python-version`, `backend/pyproject.toml`, `backend/.env.example`
- Create: `backend/unstuck/__init__.py`, `backend/unstuck/config.py`, `backend/unstuck/timefmt.py`, `backend/unstuck/errors.py`
- Create: `backend/tests/__init__.py`, `backend/tests/test_timefmt.py`, `backend/tests/test_config.py`

**Interfaces:**
- Produces: `Settings` (frozen dataclass: `data_dir: Path, llm_base_url, llm_api_key, chat_model, vision_model, embed_model: str, embed_dim: int, whisper_model: str, max_duration_s: int = 7200, llm_disable_thinking: bool = True, llm_max_concurrency: int = 2`, property `db_path`, method `video_dir(video_id) -> Path`); `load_settings(env_file: bool = True) -> Settings`; `fmt_ts(seconds: float) -> str`; `parse_ts(text: str) -> float`; `IngestError(code: str, message: str)` with `.code`, `.message`.

- [ ] **Step 1: Create project files**

`.gitignore` (repo root):

```gitignore
.DS_Store
.env
data/
__pycache__/
.venv/
.pytest_cache/
node_modules/
frontend/dist/
```

`backend/.python-version`:

```
3.12
```

`backend/pyproject.toml`:

```toml
[project]
name = "unstuck"
version = "0.1.0"
description = "Video-aware one-on-one AI tutor"
requires-python = ">=3.11"
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.30",
  "openai>=1.40",
  "python-dotenv>=1.0",
  "sqlite-vec>=0.1.6",
  "yt-dlp>=2025.1.1",
  "scenedetect[opencv]>=0.6.4",
  "faster-whisper>=1.0",
  "imagehash>=4.3",
  "Pillow>=10.0",
  "sentence-transformers>=3.0",
  "json-repair>=0.30",
]

[dependency-groups]
dev = ["pytest>=8.0", "httpx>=0.27"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["unstuck"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["slow: real network / real models; run with `-m slow`"]
addopts = "-m 'not slow'"
```

`backend/.env.example`:

```dotenv
# Copy to backend/.env and fill in.
LLM_API_KEY=your-zhipu-api-key
LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4/
CHAT_MODEL=glm-4.7-flash
VISION_MODEL=glm-4.6v-flash
# GLM thinking mode slows answers down; set to 0 for models that reject the `thinking` field.
LLM_DISABLE_THINKING=1
LLM_MAX_CONCURRENCY=2
EMBED_MODEL=BAAI/bge-m3
EMBED_DIM=1024
WHISPER_MODEL=small
UNSTUCK_DATA_DIR=data
```

`backend/unstuck/__init__.py` and `backend/tests/__init__.py`: empty files.

- [ ] **Step 2: Install dependencies**

Prerequisites on the machine: `uv`, `ffmpeg` (`brew install uv ffmpeg`).

Run: `cd backend && uv sync`
Expected: creates `backend/.venv` and `uv.lock`, ends without errors.

- [ ] **Step 3: Write the failing tests**

`backend/tests/test_timefmt.py`:

```python
import pytest

from unstuck.timefmt import fmt_ts, parse_ts


@pytest.mark.parametrize(
    "seconds,expected",
    [(0, "00:00"), (59.9, "00:59"), (215, "03:35"), (3725, "1:02:05"), (-3, "00:00")],
)
def test_fmt_ts(seconds, expected):
    assert fmt_ts(seconds) == expected


@pytest.mark.parametrize(
    "text,expected",
    [("03:35", 215.0), ("1:02:05", 3725.0), ("0:07", 7.0), (" 12:00 ", 720.0)],
)
def test_parse_ts(text, expected):
    assert parse_ts(text) == expected


@pytest.mark.parametrize("bad", ["", "abc", "1:2:3:4", "03:75", "1:60:00"])
def test_parse_ts_rejects_invalid(bad):
    with pytest.raises(ValueError):
        parse_ts(bad)
```

`backend/tests/test_config.py`:

```python
from unstuck.config import load_settings

ENV_KEYS = (
    "LLM_BASE_URL", "LLM_API_KEY", "CHAT_MODEL", "VISION_MODEL", "EMBED_MODEL",
    "EMBED_DIM", "WHISPER_MODEL", "MAX_DURATION_S", "LLM_DISABLE_THINKING",
    "LLM_MAX_CONCURRENCY",
)


def test_defaults(monkeypatch, tmp_path):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("UNSTUCK_DATA_DIR", str(tmp_path))

    s = load_settings(env_file=False)

    assert s.chat_model == "glm-4.7-flash"
    assert s.vision_model == "glm-4.6v-flash"
    assert s.llm_base_url == "https://open.bigmodel.cn/api/paas/v4/"
    assert s.embed_model == "BAAI/bge-m3"
    assert s.embed_dim == 1024
    assert s.max_duration_s == 7200
    assert s.llm_disable_thinking is True
    assert s.db_path == tmp_path / "unstuck.db"
    assert s.video_dir("abc") == tmp_path / "videos" / "abc"


def test_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("UNSTUCK_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CHAT_MODEL", "claude-x")
    monkeypatch.setenv("EMBED_DIM", "8")
    monkeypatch.setenv("LLM_DISABLE_THINKING", "0")

    s = load_settings(env_file=False)

    assert s.chat_model == "claude-x"
    assert s.embed_dim == 8
    assert s.llm_disable_thinking is False
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_timefmt.py tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'unstuck.timefmt'` (and `unstuck.config`).

- [ ] **Step 5: Implement**

`backend/unstuck/timefmt.py`:

```python
def fmt_ts(seconds: float) -> str:
    """Format seconds as mm:ss, or h:mm:ss for an hour or more."""
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def parse_ts(text: str) -> float:
    """Parse mm:ss or h:mm:ss into seconds."""
    parts = text.strip().split(":")
    if not 2 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f"invalid timestamp: {text!r}")
    nums = [int(p) for p in parts]
    if any(n >= 60 for n in nums[1:]):
        raise ValueError(f"invalid timestamp: {text!r}")
    if len(nums) == 2:
        nums = [0, *nums]
    hours, minutes, secs = nums
    return float(hours * 3600 + minutes * 60 + secs)
```

`backend/unstuck/errors.py`:

```python
class IngestError(Exception):
    """An import failure with a stable machine-readable code and a user-facing message."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
```

`backend/unstuck/config.py`:

```python
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    llm_base_url: str
    llm_api_key: str
    chat_model: str
    vision_model: str
    embed_model: str
    embed_dim: int
    whisper_model: str
    max_duration_s: int = 7200
    llm_disable_thinking: bool = True
    llm_max_concurrency: int = 2

    @property
    def db_path(self) -> Path:
        return self.data_dir / "unstuck.db"

    def video_dir(self, video_id: str) -> Path:
        return self.data_dir / "videos" / video_id


def _flag(value: str) -> bool:
    return value.strip().lower() not in ("0", "false", "no", "")


def load_settings(env_file: bool = True) -> Settings:
    if env_file:
        load_dotenv()
    return Settings(
        data_dir=Path(os.getenv("UNSTUCK_DATA_DIR", "data")).expanduser().absolute(),
        llm_base_url=os.getenv("LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/"),
        llm_api_key=os.getenv("LLM_API_KEY", ""),
        chat_model=os.getenv("CHAT_MODEL", "glm-4.7-flash"),
        vision_model=os.getenv("VISION_MODEL", "glm-4.6v-flash"),
        embed_model=os.getenv("EMBED_MODEL", "BAAI/bge-m3"),
        embed_dim=int(os.getenv("EMBED_DIM", "1024")),
        whisper_model=os.getenv("WHISPER_MODEL", "small"),
        max_duration_s=int(os.getenv("MAX_DURATION_S", "7200")),
        llm_disable_thinking=_flag(os.getenv("LLM_DISABLE_THINKING", "1")),
        llm_max_concurrency=int(os.getenv("LLM_MAX_CONCURRENCY", "2")),
    )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_timefmt.py tests/test_config.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add .gitignore backend/
git commit -m "feat(backend): scaffold unstuck package with config and time helpers"
```

---

### Task 2: Database layer (schema, sqlite-vec, repository)

**Files:**
- Create: `backend/unstuck/models.py`, `backend/unstuck/db/__init__.py`, `backend/unstuck/db/schema.py`, `backend/unstuck/db/repo.py`
- Create: `backend/tests/conftest.py`, `backend/tests/test_repo.py`

**Interfaces:**
- Consumes: `Settings` (Task 1).
- Produces (models.py, all `@dataclass(frozen=True)` except `Video`):
  - `Cue(start: float, end: float, text: str)`
  - `Scene(start: float, end: float)`
  - `NewKeyframe(t: float, image_path: str, phash: str)`
  - `FrameDescription(kind: str, summary: str, ocr_text: str, change_note: str)`
  - `Keyframe(id: int, video_id: str, t: float, image_path: str, phash: str, summary: str, ocr_text: str, kind: str, change_note: str, status: str)` — status `pending|ok|failed`
  - `Segment(start: float, end: float, transcript: str, keyframe_ids: list[int] = [], id: int | None = None)`
  - `Chat(id: int, video_id: str, role: str, content: str, t_at_ask: float | None, complete: bool, created_at: str)`
  - `Video(id, url, title, channel, duration: float, status: str, ask_ready: bool, stage_states: dict[str, str], visual_mode: str, outline: list[dict] | None, error_code: str | None, error_message: str | None, created_at: str)`
- Produces (db): `connect(db_path: Path, embed_dim: int) -> sqlite3.Connection`
- Produces (repo): `insert_video(conn, video_id, url) -> bool`, `get_video(conn, video_id) -> Video | None`, `list_videos(conn) -> list[Video]`, `update_video(conn, video_id, **fields) -> None`, `replace_cues(conn, video_id, cues)`, `get_cues(conn, video_id) -> list[Cue]`, `replace_keyframes(conn, video_id, frames: list[NewKeyframe]) -> list[Keyframe]`, `get_keyframes(conn, video_id) -> list[Keyframe]` (ordered by t), `save_frame_description(conn, keyframe_id, desc: FrameDescription | None)`, `replace_segments(conn, video_id, segments) -> list[Segment]` (with ids), `get_segments(conn, video_id) -> list[Segment]` (ordered by start), `replace_vectors(conn, video_id, items: list[tuple[int, list[float]]])`, `search_vectors(conn, video_id, query: list[float], k: int) -> list[tuple[int, float]]`, `add_chat(conn, video_id, role, content, t_at_ask, complete=True) -> int`, `list_chats(conn, video_id) -> list[Chat]`, `recent_chats(conn, video_id, limit) -> list[Chat]` (chronological), `get_frame_cache(conn, video_id, second) -> str | None`, `put_frame_cache(conn, video_id, second, description)`.
- Produces (tests/conftest.py fixtures): `settings` (tmp data dir, `embed_dim=64`), `conn`, `fake_llm` (added in Task 3).

- [ ] **Step 1: Write the shared test fixtures**

`backend/tests/conftest.py`:

```python
import pytest

from unstuck.config import Settings
from unstuck.db import connect


@pytest.fixture
def settings(tmp_path):
    return Settings(
        data_dir=tmp_path,
        llm_base_url="http://fake.invalid",
        llm_api_key="",
        chat_model="fake-chat",
        vision_model="fake-vision",
        embed_model="fake-embed",
        embed_dim=64,
        whisper_model="tiny",
        llm_disable_thinking=False,
    )


@pytest.fixture
def conn(settings):
    c = connect(settings.db_path, settings.embed_dim)
    yield c
    c.close()
```

- [ ] **Step 2: Write the failing tests**

`backend/tests/test_repo.py`:

```python
import pytest

from unstuck.db import repo
from unstuck.models import Cue, FrameDescription, NewKeyframe, Segment

VID = "abcdefghijk"
OTHER = "zzzzzzzzzzz"


def unit(dim, i):
    v = [0.0] * dim
    v[i] = 1.0
    return v


def test_vec_extension_loaded(conn):
    (version,) = conn.execute("select vec_version()").fetchone()
    assert version.startswith("v")


def test_insert_and_get_video(conn):
    assert repo.insert_video(conn, VID, "https://youtu.be/abcdefghijk") is True
    assert repo.insert_video(conn, VID, "https://youtu.be/abcdefghijk") is False
    v = repo.get_video(conn, VID)
    assert v.status == "importing"
    assert v.stage_states == {}
    assert v.visual_mode == "full"
    assert v.outline is None
    assert v.ask_ready is False


def test_get_missing_video(conn):
    assert repo.get_video(conn, "nope") is None


def test_update_video_json_and_bool_fields(conn):
    repo.insert_video(conn, VID, "u")
    repo.update_video(
        conn, VID,
        title="T",
        stage_states={"metadata": "done"},
        outline=[{"start": 0.0, "title": "Intro", "summary": ""}],
        ask_ready=True,
    )
    v = repo.get_video(conn, VID)
    assert v.title == "T"
    assert v.stage_states == {"metadata": "done"}
    assert v.outline[0]["title"] == "Intro"
    assert v.ask_ready is True


def test_update_video_rejects_unknown_field(conn):
    repo.insert_video(conn, VID, "u")
    with pytest.raises(ValueError):
        repo.update_video(conn, VID, bogus=1)


def test_list_videos_newest_first(conn):
    repo.insert_video(conn, "aaaaaaaaaaa", "u")
    repo.insert_video(conn, "bbbbbbbbbbb", "u")
    assert [v.id for v in repo.list_videos(conn)] == ["bbbbbbbbbbb", "aaaaaaaaaaa"]


def test_cues_replace(conn):
    repo.replace_cues(conn, VID, [Cue(0, 2, "hello"), Cue(2, 4, "world")])
    repo.replace_cues(conn, VID, [Cue(0, 1, "only")])
    assert repo.get_cues(conn, VID) == [Cue(0.0, 1.0, "only")]


def test_keyframes_and_descriptions(conn):
    frames = repo.replace_keyframes(
        conn, VID, [NewKeyframe(20.0, "/f/2.jpg", "bb"), NewKeyframe(1.0, "/f/1.jpg", "aa")]
    )
    assert sorted(f.t for f in frames) == [1.0, 20.0]
    assert {f.status for f in frames} == {"pending"}
    first, second = repo.get_keyframes(conn, VID)
    repo.save_frame_description(conn, first.id, FrameDescription("formula", "Euler", "e^{i\\pi}", "first"))
    repo.save_frame_description(conn, second.id, None)

    got = repo.get_keyframes(conn, VID)
    assert got[0].t == 1.0 and got[0].status == "ok"
    assert got[0].kind == "formula" and got[0].ocr_text == "e^{i\\pi}"
    assert got[1].status == "failed"


def test_segments_and_vector_search(conn, settings):
    dim = settings.embed_dim
    segs = repo.replace_segments(conn, VID, [Segment(0, 30, "a", [1]), Segment(30, 60, "b")])
    assert all(s.id is not None for s in segs)
    assert repo.get_segments(conn, VID)[0].keyframe_ids == [1]

    repo.replace_vectors(conn, VID, [(segs[0].id, unit(dim, 0)), (segs[1].id, unit(dim, 1))])
    hits = repo.search_vectors(conn, VID, unit(dim, 1), k=2)
    assert hits[0][0] == segs[1].id
    assert hits[0][1] <= hits[1][1]


def test_vector_search_is_scoped_to_video(conn, settings):
    dim = settings.embed_dim
    (a,) = repo.replace_segments(conn, VID, [Segment(0, 30, "a")])
    (b,) = repo.replace_segments(conn, OTHER, [Segment(0, 30, "b")])
    repo.replace_vectors(conn, VID, [(a.id, unit(dim, 0))])
    repo.replace_vectors(conn, OTHER, [(b.id, unit(dim, 0))])
    assert [sid for sid, _ in repo.search_vectors(conn, VID, unit(dim, 0), k=5)] == [a.id]


def test_replace_segments_drops_old_vectors(conn, settings):
    dim = settings.embed_dim
    (a,) = repo.replace_segments(conn, VID, [Segment(0, 30, "a")])
    repo.replace_vectors(conn, VID, [(a.id, unit(dim, 0))])
    repo.replace_segments(conn, VID, [Segment(0, 10, "c")])
    assert repo.search_vectors(conn, VID, unit(dim, 0), k=5) == []


def test_chats_recent_in_chronological_order(conn):
    for i in range(5):
        repo.add_chat(conn, VID, "user" if i % 2 == 0 else "assistant", f"m{i}", 10.0)
    assert [c.content for c in repo.recent_chats(conn, VID, limit=3)] == ["m2", "m3", "m4"]
    assert len(repo.list_chats(conn, VID)) == 5


def test_incomplete_chat_flag(conn):
    repo.add_chat(conn, VID, "assistant", "partial", 3.0, complete=False)
    (chat,) = repo.list_chats(conn, VID)
    assert chat.complete is False
    assert chat.t_at_ask == 3.0


def test_frame_cache(conn):
    assert repo.get_frame_cache(conn, VID, 12) is None
    repo.put_frame_cache(conn, VID, 12, "desc")
    repo.put_frame_cache(conn, VID, 12, "newer")
    assert repo.get_frame_cache(conn, VID, 12) == "newer"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_repo.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'unstuck.db'`.

- [ ] **Step 4: Implement models**

`backend/unstuck/models.py`:

```python
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Scene:
    start: float
    end: float


@dataclass(frozen=True)
class NewKeyframe:
    t: float
    image_path: str
    phash: str


@dataclass(frozen=True)
class FrameDescription:
    kind: str
    summary: str
    ocr_text: str
    change_note: str


@dataclass(frozen=True)
class Keyframe:
    id: int
    video_id: str
    t: float
    image_path: str
    phash: str
    summary: str
    ocr_text: str
    kind: str
    change_note: str
    status: str


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    transcript: str
    keyframe_ids: list[int] = field(default_factory=list)
    id: int | None = None


@dataclass(frozen=True)
class Chat:
    id: int
    video_id: str
    role: str
    content: str
    t_at_ask: float | None
    complete: bool
    created_at: str


@dataclass
class Video:
    id: str
    url: str
    title: str
    channel: str
    duration: float
    status: str
    ask_ready: bool
    stage_states: dict[str, str]
    visual_mode: str
    outline: list[dict] | None
    error_code: str | None
    error_message: str | None
    created_at: str
```

- [ ] **Step 5: Implement schema and connect**

`backend/unstuck/db/schema.py`:

```python
SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL DEFAULT '',
    duration REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'importing',
    ask_ready INTEGER NOT NULL DEFAULT 0,
    stage_states TEXT NOT NULL DEFAULT '{}',
    visual_mode TEXT NOT NULL DEFAULT 'full',
    outline TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS cues (
    video_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    t_start REAL NOT NULL,
    t_end REAL NOT NULL,
    text TEXT NOT NULL,
    PRIMARY KEY (video_id, idx)
);

CREATE TABLE IF NOT EXISTS keyframes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL,
    t REAL NOT NULL,
    image_path TEXT NOT NULL,
    phash TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    ocr_text TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'other',
    change_note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending'
);
CREATE INDEX IF NOT EXISTS idx_keyframes_video_t ON keyframes (video_id, t);

CREATE TABLE IF NOT EXISTS segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL,
    t_start REAL NOT NULL,
    t_end REAL NOT NULL,
    transcript TEXT NOT NULL,
    keyframe_ids TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_segments_video_start ON segments (video_id, t_start);

CREATE TABLE IF NOT EXISTS chats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    t_at_ask REAL,
    complete INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_chats_video ON chats (video_id, id);

CREATE TABLE IF NOT EXISTS frame_cache (
    video_id TEXT NOT NULL,
    second INTEGER NOT NULL,
    description TEXT NOT NULL,
    PRIMARY KEY (video_id, second)
);
"""


def vec_table_sql(dim: int) -> str:
    return (
        "CREATE VIRTUAL TABLE IF NOT EXISTS vec_segments USING vec0("
        "segment_id integer primary key, "
        "video_id text partition key, "
        f"embedding float[{dim}])"
    )
```

`backend/unstuck/db/__init__.py`:

```python
import sqlite3
from pathlib import Path

import sqlite_vec

from unstuck.db.schema import SCHEMA, vec_table_sql


def connect(db_path: Path, embed_dim: int) -> sqlite3.Connection:
    """Open the database, load sqlite-vec and make sure all tables exist.

    check_same_thread=False because FastAPI iterates streaming responses on
    worker threads; each connection is still used by one request at a time.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.execute(vec_table_sql(embed_dim))
    conn.commit()
    return conn
```

- [ ] **Step 6: Implement the repository**

`backend/unstuck/db/repo.py`:

```python
import json
import sqlite3

from sqlite_vec import serialize_float32

from unstuck.models import Chat, Cue, FrameDescription, Keyframe, NewKeyframe, Segment, Video

_VIDEO_UPDATABLE = {
    "title", "channel", "duration", "status", "ask_ready", "stage_states",
    "visual_mode", "outline", "error_code", "error_message",
}


# ---- videos ---------------------------------------------------------------

def _video(row: sqlite3.Row) -> Video:
    return Video(
        id=row["id"],
        url=row["url"],
        title=row["title"],
        channel=row["channel"],
        duration=row["duration"],
        status=row["status"],
        ask_ready=bool(row["ask_ready"]),
        stage_states=json.loads(row["stage_states"]),
        visual_mode=row["visual_mode"],
        outline=json.loads(row["outline"]) if row["outline"] else None,
        error_code=row["error_code"],
        error_message=row["error_message"],
        created_at=row["created_at"],
    )


def insert_video(conn: sqlite3.Connection, video_id: str, url: str) -> bool:
    """Insert a new video row. Returns False if it already existed."""
    with conn:
        cur = conn.execute("INSERT OR IGNORE INTO videos (id, url) VALUES (?, ?)", (video_id, url))
    return cur.rowcount == 1


def get_video(conn: sqlite3.Connection, video_id: str) -> Video | None:
    row = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
    return _video(row) if row else None


def list_videos(conn: sqlite3.Connection) -> list[Video]:
    rows = conn.execute("SELECT * FROM videos ORDER BY created_at DESC, rowid DESC").fetchall()
    return [_video(r) for r in rows]


def update_video(conn: sqlite3.Connection, video_id: str, **fields) -> None:
    unknown = set(fields) - _VIDEO_UPDATABLE
    if unknown:
        raise ValueError(f"cannot update video fields: {sorted(unknown)}")
    if not fields:
        return
    columns, values = [], []
    for key, value in fields.items():
        if key in ("stage_states", "outline") and value is not None:
            value = json.dumps(value, ensure_ascii=False)
        if key == "ask_ready":
            value = int(bool(value))
        columns.append(f"{key} = ?")
        values.append(value)
    with conn:
        conn.execute(f"UPDATE videos SET {', '.join(columns)} WHERE id = ?", (*values, video_id))


# ---- cues -----------------------------------------------------------------

def replace_cues(conn: sqlite3.Connection, video_id: str, cues: list[Cue]) -> None:
    with conn:
        conn.execute("DELETE FROM cues WHERE video_id = ?", (video_id,))
        conn.executemany(
            "INSERT INTO cues (video_id, idx, t_start, t_end, text) VALUES (?, ?, ?, ?, ?)",
            [(video_id, i, c.start, c.end, c.text) for i, c in enumerate(cues)],
        )


def get_cues(conn: sqlite3.Connection, video_id: str) -> list[Cue]:
    rows = conn.execute(
        "SELECT t_start, t_end, text FROM cues WHERE video_id = ? ORDER BY idx", (video_id,)
    ).fetchall()
    return [Cue(r["t_start"], r["t_end"], r["text"]) for r in rows]


# ---- keyframes ------------------------------------------------------------

def _keyframe(row: sqlite3.Row) -> Keyframe:
    return Keyframe(
        id=row["id"], video_id=row["video_id"], t=row["t"], image_path=row["image_path"],
        phash=row["phash"], summary=row["summary"], ocr_text=row["ocr_text"], kind=row["kind"],
        change_note=row["change_note"], status=row["status"],
    )


def replace_keyframes(conn: sqlite3.Connection, video_id: str, frames: list[NewKeyframe]) -> list[Keyframe]:
    with conn:
        conn.execute("DELETE FROM keyframes WHERE video_id = ?", (video_id,))
        conn.executemany(
            "INSERT INTO keyframes (video_id, t, image_path, phash) VALUES (?, ?, ?, ?)",
            [(video_id, f.t, f.image_path, f.phash) for f in frames],
        )
    return get_keyframes(conn, video_id)


def get_keyframes(conn: sqlite3.Connection, video_id: str) -> list[Keyframe]:
    rows = conn.execute("SELECT * FROM keyframes WHERE video_id = ? ORDER BY t", (video_id,)).fetchall()
    return [_keyframe(r) for r in rows]


def save_frame_description(conn: sqlite3.Connection, keyframe_id: int, desc: FrameDescription | None) -> None:
    with conn:
        if desc is None:
            conn.execute("UPDATE keyframes SET status = 'failed' WHERE id = ?", (keyframe_id,))
        else:
            conn.execute(
                "UPDATE keyframes SET kind = ?, summary = ?, ocr_text = ?, change_note = ?, status = 'ok' "
                "WHERE id = ?",
                (desc.kind, desc.summary, desc.ocr_text, desc.change_note, keyframe_id),
            )


# ---- segments & vectors ---------------------------------------------------

def _segment_ids(conn: sqlite3.Connection, video_id: str) -> list[int]:
    return [r[0] for r in conn.execute("SELECT id FROM segments WHERE video_id = ?", (video_id,))]


def replace_segments(conn: sqlite3.Connection, video_id: str, segments: list[Segment]) -> list[Segment]:
    """Replace a video's segments (and drop their vectors). Returns segments with ids."""
    with conn:
        conn.executemany(
            "DELETE FROM vec_segments WHERE segment_id = ?", [(i,) for i in _segment_ids(conn, video_id)]
        )
        conn.execute("DELETE FROM segments WHERE video_id = ?", (video_id,))
        for seg in segments:
            conn.execute(
                "INSERT INTO segments (video_id, t_start, t_end, transcript, keyframe_ids) VALUES (?, ?, ?, ?, ?)",
                (video_id, seg.start, seg.end, seg.transcript, json.dumps(seg.keyframe_ids)),
            )
    return get_segments(conn, video_id)


def get_segments(conn: sqlite3.Connection, video_id: str) -> list[Segment]:
    rows = conn.execute(
        "SELECT * FROM segments WHERE video_id = ? ORDER BY t_start", (video_id,)
    ).fetchall()
    return [
        Segment(
            start=r["t_start"], end=r["t_end"], transcript=r["transcript"],
            keyframe_ids=json.loads(r["keyframe_ids"]), id=r["id"],
        )
        for r in rows
    ]


def replace_vectors(conn: sqlite3.Connection, video_id: str, items: list[tuple[int, list[float]]]) -> None:
    with conn:
        conn.executemany(
            "DELETE FROM vec_segments WHERE segment_id = ?", [(i,) for i in _segment_ids(conn, video_id)]
        )
        conn.executemany(
            "INSERT INTO vec_segments (segment_id, video_id, embedding) VALUES (?, ?, ?)",
            [(sid, video_id, serialize_float32(vec)) for sid, vec in items],
        )


def search_vectors(conn: sqlite3.Connection, video_id: str, query: list[float], k: int) -> list[tuple[int, float]]:
    rows = conn.execute(
        "SELECT segment_id, distance FROM vec_segments "
        "WHERE embedding MATCH ? AND k = ? AND video_id = ? ORDER BY distance",
        (serialize_float32(query), k, video_id),
    ).fetchall()
    return [(r["segment_id"], r["distance"]) for r in rows]


# ---- chats ----------------------------------------------------------------

def _chat(row: sqlite3.Row) -> Chat:
    return Chat(
        id=row["id"], video_id=row["video_id"], role=row["role"], content=row["content"],
        t_at_ask=row["t_at_ask"], complete=bool(row["complete"]), created_at=row["created_at"],
    )


def add_chat(
    conn: sqlite3.Connection, video_id: str, role: str, content: str,
    t_at_ask: float | None, complete: bool = True,
) -> int:
    with conn:
        cur = conn.execute(
            "INSERT INTO chats (video_id, role, content, t_at_ask, complete) VALUES (?, ?, ?, ?, ?)",
            (video_id, role, content, t_at_ask, int(complete)),
        )
    return cur.lastrowid


def list_chats(conn: sqlite3.Connection, video_id: str) -> list[Chat]:
    rows = conn.execute("SELECT * FROM chats WHERE video_id = ? ORDER BY id", (video_id,)).fetchall()
    return [_chat(r) for r in rows]


def recent_chats(conn: sqlite3.Connection, video_id: str, limit: int) -> list[Chat]:
    rows = conn.execute(
        "SELECT * FROM chats WHERE video_id = ? ORDER BY id DESC LIMIT ?", (video_id, limit)
    ).fetchall()
    return [_chat(r) for r in reversed(rows)]


# ---- frame cache ----------------------------------------------------------

def get_frame_cache(conn: sqlite3.Connection, video_id: str, second: int) -> str | None:
    row = conn.execute(
        "SELECT description FROM frame_cache WHERE video_id = ? AND second = ?", (video_id, second)
    ).fetchone()
    return row["description"] if row else None


def put_frame_cache(conn: sqlite3.Connection, video_id: str, second: int, description: str) -> None:
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO frame_cache (video_id, second, description) VALUES (?, ?, ?)",
            (video_id, second, description),
        )
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_repo.py -v`
Expected: all PASS. If `test_vec_extension_loaded` fails with `AttributeError: ... enable_load_extension`, the Python build lacks extension support — run `uv python install 3.12` and `uv sync --reinstall` so uv's own CPython (which supports it) is used instead of a system Python.

- [ ] **Step 8: Commit**

```bash
git add backend/
git commit -m "feat(db): sqlite schema with sqlite-vec index and repository functions"
```

---

### Task 3: LLM layer (protocol, FakeLLM, retry, real clients)

**Files:**
- Create: `backend/unstuck/llm/__init__.py`, `base.py`, `fake.py`, `retry.py`, `openai_compat.py`, `embedder.py`, `factory.py`
- Create: `backend/scripts/smoke_llm.py`
- Modify: `backend/tests/conftest.py` (add `fake_llm` fixture)
- Test: `backend/tests/test_llm_fake.py`, `backend/tests/test_retry.py`

**Interfaces:**
- Consumes: `Settings`.
- Produces:
  - `LLM` Protocol: `chat(messages: list[dict], *, json_mode: bool = False) -> str`, `chat_stream(messages: list[dict]) -> Iterator[str]`, `vision(prompt: str, image_path: Path) -> str`, `embed(texts: list[str]) -> list[list[float]]`
  - `FakeLLM(chat_responses=None, vision_responses=None, embed_dim=64)`; `.calls: list[tuple[str, object]]` where entries are `("chat", messages)`, `("chat_stream", messages)`, `("vision", (prompt, image_path_str))`, `("embed", texts)`. Queued response items may be a `str`, an `Exception` (raised), or for `chat_stream` a `list` of chunks/exceptions. Empty queue → defaults `"fake answer"` and a valid vision JSON.
  - `with_retry(fn, *, attempts=4, base_delay=1.0, sleep=None, retry_on=(Exception,))`
  - `OpenAICompatLLM`, `BgeM3Embedder`, `build_llm(settings) -> LLM`

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_llm_fake.py`:

```python
import math

import pytest

from unstuck.llm import FakeLLM


def test_chat_returns_queued_then_default():
    llm = FakeLLM(chat_responses=["one"])
    assert llm.chat([{"role": "user", "content": "hi"}]) == "one"
    assert llm.chat([]) == "fake answer"
    assert llm.calls[0] == ("chat", [{"role": "user", "content": "hi"}])


def test_chat_raises_queued_exception():
    llm = FakeLLM(chat_responses=[RuntimeError("boom")])
    with pytest.raises(RuntimeError):
        llm.chat([])


def test_stream_chunks_then_midstream_error():
    llm = FakeLLM(chat_responses=[["Hel", "lo", RuntimeError("cut")]])
    got = []
    with pytest.raises(RuntimeError):
        for chunk in llm.chat_stream([]):
            got.append(chunk)
    assert got == ["Hel", "lo"]


def test_stream_plain_string_is_chunked():
    llm = FakeLLM(chat_responses=["hello world, this is long"])
    chunks = list(llm.chat_stream([]))
    assert len(chunks) > 1
    assert "".join(chunks) == "hello world, this is long"


def test_vision_default_is_valid_json_and_records_call():
    llm = FakeLLM()
    assert '"summary"' in llm.vision("describe", "/tmp/x.jpg")
    assert llm.calls == [("vision", ("describe", "/tmp/x.jpg"))]


def test_embed_is_deterministic_and_normalized():
    llm = FakeLLM(embed_dim=16)
    a, b = llm.embed(["dot product", "dot product"])
    assert a == b
    assert len(a) == 16
    assert math.isclose(sum(x * x for x in a), 1.0)


def test_embed_shared_words_are_closer():
    llm = FakeLLM(embed_dim=64)
    q, near, far = llm.embed(["dot product geometry", "the dot product", "eigenvalues of matrices"])
    dot = lambda u, v: sum(x * y for x, y in zip(u, v))
    assert dot(q, near) > dot(q, far)
```

`backend/tests/test_retry.py`:

```python
import pytest

from unstuck.llm.retry import with_retry


def test_returns_first_success():
    calls = []

    def fn():
        calls.append(1)
        return "ok"

    assert with_retry(fn, sleep=lambda s: None) == "ok"
    assert len(calls) == 1


def test_retries_with_exponential_backoff():
    outcomes = iter([ValueError(), ValueError(), "ok"])
    delays = []

    def fn():
        value = next(outcomes)
        if isinstance(value, Exception):
            raise value
        return value

    assert with_retry(fn, attempts=4, base_delay=1.0, sleep=delays.append) == "ok"
    assert delays == [1.0, 2.0]


def test_gives_up_after_attempts():
    delays = []

    def fn():
        raise ValueError("always")

    with pytest.raises(ValueError):
        with_retry(fn, attempts=3, base_delay=0.5, sleep=delays.append)
    assert delays == [0.5, 1.0]


def test_does_not_retry_unlisted_errors():
    delays = []

    def fn():
        raise KeyError("nope")

    with pytest.raises(KeyError):
        with_retry(fn, retry_on=(ValueError,), sleep=delays.append)
    assert delays == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_llm_fake.py tests/test_retry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'unstuck.llm'`.

- [ ] **Step 3: Implement protocol, retry and FakeLLM**

`backend/unstuck/llm/base.py`:

```python
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol


class LLM(Protocol):
    """Every model call in Unstuck goes through this interface."""

    def chat(self, messages: list[dict], *, json_mode: bool = False) -> str: ...

    def chat_stream(self, messages: list[dict]) -> Iterator[str]: ...

    def vision(self, prompt: str, image_path: Path) -> str: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...
```

`backend/unstuck/llm/retry.py`:

```python
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def with_retry(
    fn: Callable[[], T],
    *,
    attempts: int = 4,
    base_delay: float = 1.0,
    sleep: Callable[[float], None] | None = None,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
) -> T:
    """Call fn, retrying listed errors with exponential backoff (base, 2*base, 4*base…)."""
    sleep = sleep or time.sleep
    for attempt in range(attempts):
        try:
            return fn()
        except retry_on:
            if attempt == attempts - 1:
                raise
            sleep(base_delay * 2**attempt)
    raise AssertionError("unreachable")
```

`backend/unstuck/llm/fake.py`:

```python
import hashlib
import math
import re
from collections.abc import Iterator
from pathlib import Path

DEFAULT_CHAT = "fake answer"
DEFAULT_VISION = '{"kind": "other", "summary": "fake frame", "ocr_text": "", "change_note": ""}'


def _bag_of_words(text: str, dim: int) -> list[float]:
    vec = [0.0] * dim
    for word in re.findall(r"\w+", text.lower()):
        vec[int(hashlib.md5(word.encode()).hexdigest(), 16) % dim] += 1.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


class FakeLLM:
    """Deterministic stand-in for real models in tests.

    Queued responses are consumed in order; an Exception item is raised instead
    of returned. For chat_stream a list item is yielded chunk by chunk, raising
    any Exception element (simulates a stream that dies midway).
    """

    def __init__(self, chat_responses=None, vision_responses=None, embed_dim: int = 64):
        self.chat_responses = list(chat_responses or [])
        self.vision_responses = list(vision_responses or [])
        self.embed_dim = embed_dim
        self.calls: list[tuple[str, object]] = []

    @staticmethod
    def _pop(queue: list, default):
        if not queue:
            return default
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def chat(self, messages: list[dict], *, json_mode: bool = False) -> str:
        self.calls.append(("chat", messages))
        return self._pop(self.chat_responses, DEFAULT_CHAT)

    def chat_stream(self, messages: list[dict]) -> Iterator[str]:
        self.calls.append(("chat_stream", messages))
        item = self._pop(self.chat_responses, DEFAULT_CHAT)
        chunks = item if isinstance(item, list) else [item[i : i + 8] for i in range(0, len(item), 8)]
        for chunk in chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk

    def vision(self, prompt: str, image_path: Path) -> str:
        self.calls.append(("vision", (prompt, str(image_path))))
        return self._pop(self.vision_responses, DEFAULT_VISION)

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(("embed", list(texts)))
        return [_bag_of_words(t, self.embed_dim) for t in texts]
```

`backend/unstuck/llm/__init__.py`:

```python
from unstuck.llm.base import LLM
from unstuck.llm.fake import FakeLLM

__all__ = ["LLM", "FakeLLM"]
```

Append to `backend/tests/conftest.py`:

```python
from unstuck.llm import FakeLLM


@pytest.fixture
def fake_llm(settings):
    return FakeLLM(embed_dim=settings.embed_dim)
```

(Move the new import up with the other imports at the top of the file.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_llm_fake.py tests/test_retry.py -v`
Expected: all PASS.

- [ ] **Step 5: Implement the real clients and factory**

`backend/unstuck/llm/embedder.py`:

```python
import threading


class BgeM3Embedder:
    """Local BAAI/bge-m3 embeddings via sentence-transformers (loaded lazily, ~2 GB download once)."""

    def __init__(self, model_name: str = "BAAI/bge-m3", batch_size: int = 16):
        self._model_name = model_name
        self._batch_size = batch_size
        self._model = None
        self._lock = threading.Lock()

    def embed(self, texts: list[str]) -> list[list[float]]:
        with self._lock:
            if self._model is None:
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(self._model_name)
            vectors = self._model.encode(texts, normalize_embeddings=True, batch_size=self._batch_size)
        return [v.tolist() for v in vectors]
```

`backend/unstuck/llm/openai_compat.py`:

```python
import base64
import mimetypes
import threading
from collections.abc import Iterator
from pathlib import Path

import openai
from openai import OpenAI

from unstuck.llm.embedder import BgeM3Embedder
from unstuck.llm.retry import with_retry

RETRYABLE = (
    openai.RateLimitError,
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.InternalServerError,
)


class OpenAICompatLLM:
    """Chat + vision over any OpenAI-compatible endpoint (Zhipu by default); embeddings stay local."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        chat_model: str,
        vision_model: str,
        embedder: BgeM3Embedder,
        extra_body: dict | None = None,
        max_concurrency: int = 2,
    ):
        self._client = OpenAI(base_url=base_url, api_key=api_key, max_retries=0, timeout=120)
        self._chat_model = chat_model
        self._vision_model = vision_model
        self._embedder = embedder
        self._extra_body = extra_body
        self._slots = threading.Semaphore(max_concurrency)

    def _complete(self, model: str, messages: list[dict], **kwargs) -> str:
        def call() -> str:
            with self._slots:
                resp = self._client.chat.completions.create(
                    model=model, messages=messages, extra_body=self._extra_body, **kwargs
                )
            return resp.choices[0].message.content or ""

        return with_retry(call, retry_on=RETRYABLE)

    def chat(self, messages: list[dict], *, json_mode: bool = False) -> str:
        kwargs = {"response_format": {"type": "json_object"}} if json_mode else {}
        return self._complete(self._chat_model, messages, **kwargs)

    def chat_stream(self, messages: list[dict]) -> Iterator[str]:
        with self._slots:
            stream = with_retry(
                lambda: self._client.chat.completions.create(
                    model=self._chat_model, messages=messages, stream=True, extra_body=self._extra_body
                ),
                retry_on=RETRYABLE,
            )
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content

    def vision(self, prompt: str, image_path: Path) -> str:
        path = Path(image_path)
        mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        data_url = f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": prompt},
                ],
            }
        ]
        return self._complete(self._vision_model, messages)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._embedder.embed(texts)
```

`backend/unstuck/llm/factory.py`:

```python
from unstuck.config import Settings
from unstuck.llm.base import LLM
from unstuck.llm.embedder import BgeM3Embedder
from unstuck.llm.openai_compat import OpenAICompatLLM


def build_llm(settings: Settings) -> LLM:
    extra_body = {"thinking": {"type": "disabled"}} if settings.llm_disable_thinking else None
    return OpenAICompatLLM(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        chat_model=settings.chat_model,
        vision_model=settings.vision_model,
        embedder=BgeM3Embedder(settings.embed_model),
        extra_body=extra_body,
        max_concurrency=settings.llm_max_concurrency,
    )
```

`backend/scripts/smoke_llm.py`:

```python
"""Manual check that the configured real models respond.

Usage: cd backend && uv run python scripts/smoke_llm.py
Needs backend/.env with LLM_API_KEY. Downloads bge-m3 on first run.
"""

import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

from unstuck.config import load_settings
from unstuck.llm.factory import build_llm


def main() -> None:
    settings = load_settings()
    llm = build_llm(settings)

    print("chat:", llm.chat([{"role": "user", "content": "用一句话解释什么是导数。"}]))
    print("stream:", "".join(llm.chat_stream([{"role": "user", "content": "Say hi in 3 words."}])))

    with tempfile.TemporaryDirectory() as tmp:
        img = Path(tmp) / "slide.png"
        canvas = Image.new("RGB", (640, 360), "white")
        ImageDraw.Draw(canvas).text((40, 150), "E = m c^2   and   for i in range(3): print(i)", fill="black")
        canvas.save(img)
        print("vision:", llm.vision("Transcribe all text in this image.", img))

    vec = llm.embed(["hello world"])[0]
    print("embed dim:", len(vec), "(expected", settings.embed_dim, ")")


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Smoke-test the real models (manual, needs API key)**

Run: `cd backend && cp .env.example .env` then put a real Zhipu key in `LLM_API_KEY`, then `uv run python scripts/smoke_llm.py`
Expected: four printed results; `embed dim: 1024`. If the API rejects the `thinking` field, set `LLM_DISABLE_THINKING=0` in `.env`. If a model code is rejected, check https://docs.bigmodel.cn/cn/guide/start/pricing.md for the current free model names and update `.env` and the defaults in `config.py` (and `test_config.py`).

- [ ] **Step 7: Run the whole suite and commit**

Run: `cd backend && uv run pytest -v`
Expected: all PASS.

```bash
git add backend/
git commit -m "feat(llm): unified LLM interface with FakeLLM, retry, Zhipu client and bge-m3 embedder"
```

---

### Task 4: Caption parsing and sentence-end detection

**Files:**
- Create: `backend/unstuck/ingest/__init__.py` (empty), `backend/unstuck/ingest/captions.py`
- Test: `backend/tests/test_captions.py`

**Interfaces:**
- Consumes: `Cue`.
- Produces: `parse_json3(data: dict) -> list[Cue]`; `sentence_end_flags(cues: list[Cue], pause: float = 0.8) -> list[bool]` (one flag per cue; last cue always True).

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_captions.py`:

```python
import pytest

from unstuck.ingest.captions import parse_json3, sentence_end_flags
from unstuck.models import Cue


def test_parse_manual_json3():
    data = {
        "events": [
            {"tStartMs": 0, "dDurationMs": 2000, "segs": [{"utf8": "Hello there."}]},
            {"tStartMs": 2500, "dDurationMs": 1500, "segs": [{"utf8": "Second\nline"}]},
        ]
    }
    assert parse_json3(data) == [Cue(0.0, 2.0, "Hello there."), Cue(2.5, 4.0, "Second line")]


def test_skips_events_without_text():
    data = {
        "events": [
            {"tStartMs": 0, "dDurationMs": 100},
            {"tStartMs": 0, "dDurationMs": 100, "segs": [{"utf8": "\n"}]},
            {"tStartMs": 1000, "dDurationMs": 500, "segs": [{"utf8": "ok"}]},
        ]
    }
    assert parse_json3(data) == [Cue(1.0, 1.5, "ok")]


def test_auto_captions_estimate_end_from_word_offsets():
    data = {
        "events": [
            {"tStartMs": 0, "dDurationMs": 5000,
             "segs": [{"utf8": "so"}, {"utf8": " today", "tOffsetMs": 400}, {"utf8": " we", "tOffsetMs": 900}]},
            {"tStartMs": 3000, "dDurationMs": 4000,
             "segs": [{"utf8": "learn"}, {"utf8": " vectors", "tOffsetMs": 500}]},
        ]
    }
    first, second = parse_json3(data)
    assert first.text == "so today we"
    assert first.start == 0.0 and first.end == pytest.approx(1.2)
    assert second.text == "learn vectors"
    assert second.start == 3.0 and second.end == pytest.approx(3.8)


def test_overlapping_cues_are_clipped_to_next_start():
    data = {
        "events": [
            {"tStartMs": 0, "dDurationMs": 3000, "segs": [{"utf8": "one"}]},
            {"tStartMs": 2000, "dDurationMs": 1000, "segs": [{"utf8": "two"}]},
        ]
    }
    assert parse_json3(data) == [Cue(0.0, 2.0, "one"), Cue(2.0, 3.0, "two")]


def test_sentence_end_by_punctuation_and_pause():
    cues = [
        Cue(0, 1, "This is one."),
        Cue(1, 2, "and this"),      # 1.0 s pause follows
        Cue(3, 4, "continues"),     # 0.1 s gap follows
        Cue(4.1, 5, "所以是这样"),
        Cue(5, 6, "对吗？"),
        Cue(6, 7, "end"),           # last cue
    ]
    assert sentence_end_flags(cues, pause=0.8) == [True, True, False, False, True, True]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_captions.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'unstuck.ingest'`.

- [ ] **Step 3: Implement**

`backend/unstuck/ingest/captions.py`:

```python
import re

from unstuck.models import Cue

# Auto captions carry per-word offsets but no word durations; assume the last word lasts this long.
WORD_TAIL_S = 0.3
SENTENCE_END_RE = re.compile(r"[.!?。！？…][\"'”’)\]]*$")


def parse_json3(data: dict) -> list[Cue]:
    """Convert a YouTube json3 caption track into cues with non-overlapping times."""
    raw: list[list] = []
    for event in data.get("events", []):
        segs = event.get("segs") or []
        text = re.sub(r"\s+", " ", "".join(s.get("utf8", "") for s in segs)).strip()
        if not text:
            continue
        start = event.get("tStartMs", 0) / 1000
        end = start + event.get("dDurationMs", 0) / 1000
        offsets = [s["tOffsetMs"] for s in segs if "tOffsetMs" in s]
        if offsets:
            end = min(end, start + max(offsets) / 1000 + WORD_TAIL_S)
        raw.append([start, end, text])

    for current, nxt in zip(raw, raw[1:]):
        if current[1] > nxt[0]:
            current[1] = nxt[0]
    return [Cue(start, max(end, start), text) for start, end, text in raw]


def sentence_end_flags(cues: list[Cue], pause: float = 0.8) -> list[bool]:
    """A cue ends a sentence if it ends with punctuation or is followed by a pause.

    Auto captions have no punctuation, so the pause rule carries them.
    """
    flags = []
    for i, cue in enumerate(cues):
        if i == len(cues) - 1:
            flags.append(True)
            continue
        gap = cues[i + 1].start - cue.end
        flags.append(bool(SENTENCE_END_RE.search(cue.text)) or gap >= pause)
    return flags
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_captions.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/
git commit -m "feat(ingest): parse YouTube json3 captions and detect sentence ends"
```

---

### Task 5: YouTube access and Whisper fallback

**Files:**
- Create: `backend/unstuck/ingest/youtube.py`, `backend/unstuck/ingest/transcribe.py`
- Test: `backend/tests/test_youtube.py`

**Interfaces:**
- Consumes: `IngestError`, `fmt_ts`, `parse_json3`, `Cue`.
- Produces:
  - `parse_video_id(url: str) -> str` (raises `IngestError("INVALID_URL", …)`)
  - `VideoMeta(id: str, title: str, channel: str, duration: float)` dataclass
  - `check_video_info(info: dict, max_duration_s: int) -> VideoMeta` (codes `LIVE_NOT_SUPPORTED`, `TOO_LONG`, `VIDEO_UNAVAILABLE`)
  - `pick_caption_track(info: dict) -> tuple[str, str] | None` → `(json3_url, "manual" | "auto")`
  - IO: `fetch_info(video_id) -> dict`, `save_info(info, path)`, `load_info(path) -> dict`, `fetch_captions(info) -> list[Cue] | None`, `download_video(video_id, out_dir) -> Path` (raises `IngestError("DOWNLOAD_FAILED", …)`), `download_audio(video_id, out_dir) -> Path`, `find_video_file(video_dir) -> Path | None`
  - `transcribe(audio_path: Path, model_name: str) -> list[Cue]`

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_youtube.py`:

```python
import pytest

from unstuck.errors import IngestError
from unstuck.ingest.youtube import check_video_info, find_video_file, parse_video_id, pick_caption_track


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/watch?v=dQw4w9WgXcQ&t=42s",
        "https://youtu.be/dQw4w9WgXcQ?si=abc",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://www.youtube.com/embed/dQw4w9WgXcQ",
        "https://m.youtube.com/watch?feature=share&v=dQw4w9WgXcQ",
        "  dQw4w9WgXcQ ",
    ],
)
def test_parse_video_id(url):
    assert parse_video_id(url) == "dQw4w9WgXcQ"


@pytest.mark.parametrize("url", ["", "https://vimeo.com/123", "https://www.youtube.com/watch?v=short"])
def test_parse_video_id_invalid(url):
    with pytest.raises(IngestError) as err:
        parse_video_id(url)
    assert err.value.code == "INVALID_URL"


def test_check_video_info_ok():
    meta = check_video_info(
        {"id": "dQw4w9WgXcQ", "title": "T", "channel": "C", "duration": 212, "live_status": "not_live"},
        max_duration_s=7200,
    )
    assert (meta.id, meta.title, meta.channel, meta.duration) == ("dQw4w9WgXcQ", "T", "C", 212.0)


def test_check_video_info_uses_uploader_when_no_channel():
    meta = check_video_info({"id": "x", "title": "T", "uploader": "U", "duration": 10}, 7200)
    assert meta.channel == "U"


@pytest.mark.parametrize(
    "info,code",
    [
        ({"id": "x", "duration": 100, "live_status": "is_live"}, "LIVE_NOT_SUPPORTED"),
        ({"id": "x", "duration": 0, "live_status": "is_upcoming"}, "LIVE_NOT_SUPPORTED"),
        ({"id": "x", "duration": 7201}, "TOO_LONG"),
        ({"id": "x"}, "VIDEO_UNAVAILABLE"),
    ],
)
def test_check_video_info_rejects(info, code):
    with pytest.raises(IngestError) as err:
        check_video_info(info, max_duration_s=7200)
    assert err.value.code == code


def fmt(ext, url):
    return {"ext": ext, "url": url}


def test_prefers_manual_track_in_video_language():
    info = {
        "language": "en",
        "subtitles": {"en": [fmt("vtt", "m-vtt"), fmt("json3", "m-json3")]},
        "automatic_captions": {"en": [fmt("json3", "a-json3")]},
    }
    assert pick_caption_track(info) == ("m-json3", "manual")


def test_falls_back_to_original_language_auto_track():
    info = {
        "language": "en",
        "subtitles": {"live_chat": [fmt("json", "chat")]},
        "automatic_captions": {
            "fr": [fmt("json3", "a-fr")],
            "en": [fmt("json3", "a-en")],
            "en-orig": [fmt("json3", "a-en-orig")],
        },
    }
    assert pick_caption_track(info) == ("a-en-orig", "auto")


def test_chinese_auto_track():
    info = {"language": "zh-Hans", "subtitles": {}, "automatic_captions": {"zh-Hans": [fmt("json3", "a-zh")]}}
    assert pick_caption_track(info) == ("a-zh", "auto")


def test_manual_track_in_other_language_beats_nothing():
    info = {"subtitles": {"de": [fmt("json3", "m-de")]}, "automatic_captions": {}}
    assert pick_caption_track(info) == ("m-de", "manual")


def test_no_tracks():
    assert pick_caption_track({"subtitles": {}, "automatic_captions": {}}) is None


def test_find_video_file(tmp_path):
    assert find_video_file(tmp_path) is None
    (tmp_path / "video.mp4.part").write_bytes(b"x")
    assert find_video_file(tmp_path) is None
    (tmp_path / "video.mp4").write_bytes(b"x")
    assert find_video_file(tmp_path) == tmp_path / "video.mp4"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_youtube.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'unstuck.ingest.youtube'`.

- [ ] **Step 3: Implement youtube.py**

`backend/unstuck/ingest/youtube.py`:

```python
import json
import re
from dataclasses import dataclass
from pathlib import Path

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from unstuck.errors import IngestError
from unstuck.ingest.captions import parse_json3
from unstuck.models import Cue
from unstuck.timefmt import fmt_ts

_ID = r"[A-Za-z0-9_-]{11}"
_URL_ID_RE = re.compile(rf"(?:[?&]v=|youtu\.be/|/shorts/|/embed/|/live/)({_ID})")
_BARE_ID_RE = re.compile(rf"^{_ID}$")

_QUIET = {"quiet": True, "no_warnings": True, "noprogress": True}
# Prefer H.264 so OpenCV (scene detection) can always decode it; video-only, no merge needed.
_VIDEO_FORMAT = "bv*[height<=720][vcodec^=avc1]/bv*[height<=720]/b[height<=720]/b"
_AUDIO_FORMAT = "ba[ext=m4a]/ba/b"


@dataclass(frozen=True)
class VideoMeta:
    id: str
    title: str
    channel: str
    duration: float


def watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def parse_video_id(url: str) -> str:
    text = url.strip()
    if _BARE_ID_RE.match(text):
        return text
    match = _URL_ID_RE.search(text)
    if not match:
        raise IngestError("INVALID_URL", "不是有效的 YouTube 视频链接")
    return match.group(1)


def check_video_info(info: dict, max_duration_s: int) -> VideoMeta:
    if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
        raise IngestError("LIVE_NOT_SUPPORTED", "不支持直播或尚未开始的直播")
    duration = float(info.get("duration") or 0)
    if duration <= 0:
        raise IngestError("VIDEO_UNAVAILABLE", "无法获取视频时长，视频可能不可用")
    if duration > max_duration_s:
        raise IngestError(
            "TOO_LONG",
            f"视频时长 {fmt_ts(duration)} 超过上限 {fmt_ts(max_duration_s)}，暂不支持导入",
        )
    return VideoMeta(
        id=info["id"],
        title=info.get("title") or "",
        channel=info.get("channel") or info.get("uploader") or "",
        duration=duration,
    )


def _json3_url(tracks: dict, key: str) -> str | None:
    for fmt in tracks.get(key) or []:
        if fmt.get("ext") == "json3":
            return fmt.get("url")
    return None


def pick_caption_track(info: dict) -> tuple[str, str] | None:
    """Pick the best json3 caption track: manual in the video's language, then the
    original-language auto track, then any manual track."""
    lang = info.get("language") or "en"
    base = lang.split("-")[0]
    manual = {k: v for k, v in (info.get("subtitles") or {}).items() if k != "live_chat"}
    auto = info.get("automatic_captions") or {}

    for key in dict.fromkeys([lang, base, "en"]):
        if url := _json3_url(manual, key):
            return url, "manual"
    auto_keys = [f"{lang}-orig", lang, f"{base}-orig", base]
    auto_keys += [k for k in auto if k.endswith("-orig")]
    for key in dict.fromkeys(auto_keys):
        if url := _json3_url(auto, key):
            return url, "auto"
    for key in manual:
        if url := _json3_url(manual, key):
            return url, "manual"
    return None


def fetch_info(video_id: str) -> dict:
    try:
        with YoutubeDL({**_QUIET, "skip_download": True}) as ydl:
            info = ydl.extract_info(watch_url(video_id), download=False)
            return ydl.sanitize_info(info)
    except DownloadError as exc:
        message = str(exc)
        if "Private video" in message:
            raise IngestError("VIDEO_UNAVAILABLE", "这是私有视频，无法导入") from exc
        raise IngestError("VIDEO_UNAVAILABLE", f"无法获取视频信息：{message[:200]}") from exc


def save_info(info: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")


def load_info(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def fetch_captions(info: dict) -> list[Cue] | None:
    track = pick_caption_track(info)
    if track is None:
        return None
    url, _kind = track
    with YoutubeDL(_QUIET) as ydl:
        data = json.loads(ydl.urlopen(url).read().decode("utf-8"))
    return parse_json3(data) or None


def _download(video_id: str, out_dir: Path, fmt: str, stem: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    opts = {**_QUIET, "format": fmt, "outtmpl": str(out_dir / f"{stem}.%(ext)s")}
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(watch_url(video_id), download=True)
        return Path(ydl.prepare_filename(info))


def download_video(video_id: str, out_dir: Path) -> Path:
    try:
        return _download(video_id, out_dir, _VIDEO_FORMAT, "video")
    except DownloadError as exc:
        raise IngestError("DOWNLOAD_FAILED", f"视频下载失败：{str(exc)[:200]}") from exc


def download_audio(video_id: str, out_dir: Path) -> Path:
    try:
        return _download(video_id, out_dir, _AUDIO_FORMAT, "audio")
    except DownloadError as exc:
        raise IngestError("DOWNLOAD_FAILED", f"音频下载失败：{str(exc)[:200]}") from exc


def find_video_file(video_dir: Path) -> Path | None:
    for path in sorted(video_dir.glob("video.*")):
        if path.suffix not in (".part", ".ytdl") and path.is_file():
            return path
    return None
```

`backend/unstuck/ingest/transcribe.py`:

```python
from pathlib import Path

from unstuck.models import Cue


def transcribe(audio_path: Path, model_name: str) -> list[Cue]:
    """Speech-to-text fallback for videos without captions (runs locally on CPU)."""
    from faster_whisper import WhisperModel

    model = WhisperModel(model_name, device="auto", compute_type="int8")
    segments, _info = model.transcribe(str(audio_path), vad_filter=True)
    return [Cue(s.start, s.end, s.text.strip()) for s in segments if s.text.strip()]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_youtube.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/
git commit -m "feat(ingest): YouTube URL parsing, metadata checks, captions, downloads, whisper fallback"
```

---

### Task 6: Keyframe planning and extraction

**Files:**
- Create: `backend/unstuck/media.py`, `backend/unstuck/ingest/keyframes.py`
- Modify: `backend/tests/conftest.py` (add `sample_video` fixture)
- Test: `backend/tests/test_keyframes.py`, `backend/tests/test_media.py`

**Interfaces:**
- Consumes: `Scene`, `NewKeyframe`.
- Produces:
  - `media.extract_frame(video_path: Path, t: float, out_path: Path) -> Path` (raises `RuntimeError` / `subprocess.CalledProcessError`)
  - `plan_sample_times(scenes: list[Scene], duration: float, *, min_len=5.0, max_len=45.0, avg_interval=10.0, max_frames=300) -> list[float]`
  - `dedupe_by_phash(frames: list[NewKeyframe], threshold: int = 5) -> list[NewKeyframe]`
  - `detect_scenes(video_path: Path) -> list[Scene]`
  - `extract_keyframes(video_path: Path, duration: float, out_dir: Path) -> list[NewKeyframe]`

- [ ] **Step 1: Add the synthetic video fixture**

Append to `backend/tests/conftest.py` (imports go to the top):

```python
import shutil
import subprocess


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory):
    """A 3-second generated test video; tests using it are skipped without ffmpeg."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    out = tmp_path_factory.mktemp("media") / "video.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "testsrc=duration=3:size=320x240:rate=10", "-pix_fmt", "yuv420p", str(out)],
        check=True,
    )
    return out
```

- [ ] **Step 2: Write the failing tests**

`backend/tests/test_keyframes.py`:

```python
from unstuck.ingest.keyframes import dedupe_by_phash, extract_keyframes, plan_sample_times
from unstuck.models import NewKeyframe, Scene


def test_short_first_scene_merges_and_long_scene_splits():
    scenes = [Scene(0, 3), Scene(3, 20), Scene(20, 100)]
    # [0,3] is shorter than 5 s → merged into [0,20]; [20,100] is 80 s > 45 s → two 40 s pieces.
    assert plan_sample_times(scenes, duration=100) == [1.0, 21.0, 61.0]


def test_short_middle_scene_merges_into_previous():
    scenes = [Scene(0, 10), Scene(10, 12), Scene(12, 30)]
    assert plan_sample_times(scenes, duration=30) == [1.0, 13.0]


def test_cap_of_one_frame_per_ten_seconds():
    scenes = [Scene(i * 6.0, (i + 1) * 6.0) for i in range(5)]  # five 6 s scenes, 30 s total
    # cap = 30 // 10 = 3 frames, picked evenly from [1, 7, 13, 19, 25]
    assert plan_sample_times(scenes, duration=30) == [1.0, 7.0, 19.0]


def test_no_scenes_means_whole_video_split_by_max_len():
    assert plan_sample_times([], duration=90) == [1.0, 46.0]


def test_hard_cap_of_300_frames():
    scenes = [Scene(i * 6.0, (i + 1) * 6.0) for i in range(1000)]
    assert len(plan_sample_times(scenes, duration=6000)) == 300


def test_tiny_scene_samples_its_midpoint():
    assert plan_sample_times([Scene(0, 1.0)], duration=1.0) == [0.5]


def kf(t, phash):
    return NewKeyframe(t=t, image_path=f"/f/{t}.jpg", phash=phash)


def test_dedupe_by_phash_compares_with_last_kept():
    frames = [
        kf(1, "0000000000000000"),
        kf(2, "0000000000000001"),  # distance 1 → duplicate of frame 1
        kf(3, "ffffffffffffffff"),  # distance 64 → kept
        kf(4, "fffffffffffffff0"),  # distance 4 from frame 3 → duplicate
    ]
    assert [f.t for f in dedupe_by_phash(frames, threshold=5)] == [1, 3]


def test_extract_keyframes_on_real_video(sample_video, tmp_path):
    frames = extract_keyframes(sample_video, duration=3.0, out_dir=tmp_path / "frames")
    assert len(frames) == 1  # 3 s video → cap of one frame
    assert (tmp_path / "frames").exists()
    assert len(frames[0].phash) == 16
```

`backend/tests/test_media.py`:

```python
import pytest

from unstuck.media import extract_frame


def test_extract_frame(sample_video, tmp_path):
    out = extract_frame(sample_video, 1.5, tmp_path / "sub" / "f.jpg")
    assert out.exists() and out.stat().st_size > 0


def test_extract_frame_past_end_raises(sample_video, tmp_path):
    with pytest.raises(RuntimeError):
        extract_frame(sample_video, 10.0, tmp_path / "late.jpg")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_keyframes.py tests/test_media.py -v`
Expected: FAIL with `ModuleNotFoundError` for `unstuck.ingest.keyframes` / `unstuck.media`.

- [ ] **Step 4: Implement media.py**

`backend/unstuck/media.py`:

```python
import subprocess
from pathlib import Path


def extract_frame(video_path: Path, t: float, out_path: Path) -> Path:
    """Grab the frame at t seconds as a JPEG using ffmpeg."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.unlink(missing_ok=True)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", f"{t:.3f}", "-i", str(video_path),
        "-frames:v", "1", "-q:v", "3", str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RuntimeError(f"ffmpeg produced no frame at {t:.1f}s")
    return out_path
```

- [ ] **Step 5: Implement keyframes.py**

`backend/unstuck/ingest/keyframes.py`:

```python
import logging
import math
import shutil
from pathlib import Path

import imagehash
from PIL import Image

from unstuck import media
from unstuck.models import NewKeyframe, Scene

log = logging.getLogger(__name__)


def _merge_short(scenes: list[Scene], min_len: float) -> list[list[float]]:
    merged: list[list[float]] = []
    for scene in scenes:
        if merged and (scene.end - scene.start < min_len or merged[-1][1] - merged[-1][0] < min_len):
            merged[-1][1] = scene.end
        else:
            merged.append([scene.start, scene.end])
    return merged


def plan_sample_times(
    scenes: list[Scene],
    duration: float,
    *,
    min_len: float = 5.0,
    max_len: float = 45.0,
    avg_interval: float = 10.0,
    max_frames: int = 300,
) -> list[float]:
    """Choose the timestamps to grab keyframes at.

    Scenes shorter than min_len are merged into a neighbour, scenes longer than
    max_len are split evenly, one frame is sampled ~1 s into each piece, and the
    result is thinned evenly to at most one frame per avg_interval seconds
    (and never more than max_frames).
    """
    spans = _merge_short(scenes, min_len) if scenes else [[0.0, duration]]
    pieces: list[tuple[float, float]] = []
    for start, end in spans:
        n = max(1, math.ceil((end - start) / max_len))
        step = (end - start) / n
        pieces.extend((start + i * step, start + (i + 1) * step) for i in range(n))
    times = [start + min(1.0, (end - start) / 2) for start, end in pieces]

    cap = min(max_frames, max(1, int(duration // avg_interval)))
    if len(times) > cap:
        times = [times[int(i * len(times) / cap)] for i in range(cap)]
    return times


def dedupe_by_phash(frames: list[NewKeyframe], threshold: int = 5) -> list[NewKeyframe]:
    """Drop frames whose perceptual hash is within threshold bits of the last kept frame."""
    kept: list[NewKeyframe] = []
    for frame in frames:
        if kept:
            distance = imagehash.hex_to_hash(frame.phash) - imagehash.hex_to_hash(kept[-1].phash)
            if distance <= threshold:
                continue
        kept.append(frame)
    return kept


def detect_scenes(video_path: Path) -> list[Scene]:
    from scenedetect import ContentDetector, detect

    return [Scene(start.get_seconds(), end.get_seconds()) for start, end in detect(str(video_path), ContentDetector())]


def extract_keyframes(video_path: Path, duration: float, out_dir: Path) -> list[NewKeyframe]:
    shutil.rmtree(out_dir, ignore_errors=True)
    times = plan_sample_times(detect_scenes(video_path), duration)
    candidates: list[NewKeyframe] = []
    for t in times:
        try:
            path = media.extract_frame(video_path, t, out_dir / f"{int(t * 1000):010d}.jpg")
        except Exception:
            log.warning("skipping keyframe at %.1fs", t, exc_info=True)
            continue
        with Image.open(path) as img:
            phash = str(imagehash.phash(img))
        candidates.append(NewKeyframe(t=t, image_path=str(path), phash=phash))

    kept = dedupe_by_phash(candidates)
    kept_paths = {f.image_path for f in kept}
    for frame in candidates:
        if frame.image_path not in kept_paths:
            Path(frame.image_path).unlink(missing_ok=True)
    return kept
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_keyframes.py tests/test_media.py -v`
Expected: all PASS (the two ffmpeg-based tests are SKIPPED if ffmpeg is missing — install it with `brew install ffmpeg` so they run).

- [ ] **Step 7: Commit**

```bash
git add backend/
git commit -m "feat(ingest): keyframe planning with scene min/max rules, phash dedupe and ffmpeg extraction"
```

---

### Task 7: Vision descriptions with JSON repair

**Files:**
- Create: `backend/unstuck/jsonfix.py`, `backend/unstuck/ingest/vision.py`
- Create: `backend/tests/factories.py`
- Test: `backend/tests/test_jsonfix.py`, `backend/tests/test_vision.py`

**Interfaces:**
- Consumes: `LLM`, `with_retry`, `Keyframe`, `FrameDescription`.
- Produces:
  - `loads_lenient(raw: str) -> dict | list | None`
  - `KINDS: frozenset[str]`, `build_vision_prompt(previous_summary: str) -> str`, `parse_vision_output(raw: str) -> FrameDescription`
  - `describe_pending(llm, frames: list[Keyframe], save: Callable[[int, FrameDescription | None], None], *, attempts=2, base_delay=2.0, sleep=None) -> int` (returns number of failed frames)
  - `tests/factories.py`: `make_keyframe(id, t, *, status="ok", summary="", ocr_text="", kind="other", video_id="abcdefghijk") -> Keyframe`

- [ ] **Step 1: Write the test factory**

`backend/tests/factories.py`:

```python
from unstuck.models import Keyframe


def make_keyframe(
    id: int, t: float, *, status: str = "ok", summary: str = "", ocr_text: str = "",
    kind: str = "other", video_id: str = "abcdefghijk",
) -> Keyframe:
    return Keyframe(
        id=id, video_id=video_id, t=t, image_path=f"/frames/{id}.jpg", phash="0" * 16,
        summary=summary, ocr_text=ocr_text, kind=kind, change_note="", status=status,
    )
```

- [ ] **Step 2: Write the failing tests**

`backend/tests/test_jsonfix.py`:

```python
from unstuck.jsonfix import loads_lenient


def test_plain_json():
    assert loads_lenient('{"a": 1}') == {"a": 1}


def test_fenced_json():
    assert loads_lenient('```json\n{"a": 1}\n```') == {"a": 1}


def test_surrounding_text():
    assert loads_lenient('Here you go: {"a": 1} hope it helps') == {"a": 1}


def test_truncated_json_is_repaired():
    assert loads_lenient('{"a": 1, "b": "hel') == {"a": 1, "b": "hel"}


def test_list():
    assert loads_lenient("[1, 2]") == [1, 2]


def test_no_json():
    assert loads_lenient("no json here") is None
```

`backend/tests/test_vision.py`:

```python
from tests.factories import make_keyframe
from unstuck.ingest.vision import describe_pending, parse_vision_output
from unstuck.llm import FakeLLM


def test_parse_clean_json():
    d = parse_vision_output('{"kind": "code", "summary": "A loop", "ocr_text": "for i in x:", "change_note": "new"}')
    assert (d.kind, d.summary, d.ocr_text, d.change_note) == ("code", "A loop", "for i in x:", "new")


def test_parse_fenced_and_chatty():
    d = parse_vision_output('Sure!\n```json\n{"kind": "slide", "summary": "Agenda"}\n```')
    assert d.kind == "slide" and d.summary == "Agenda" and d.ocr_text == ""


def test_parse_truncated():
    d = parse_vision_output('{"kind": "formula", "summary": "Pythagoras", "ocr_text": "a^2 + b^2 = c^')
    assert d.kind == "formula"
    assert d.summary == "Pythagoras"
    assert d.ocr_text.startswith("a^2 + b^2")


def test_unknown_kind_becomes_other():
    assert parse_vision_output('{"kind": "meme", "summary": "x"}').kind == "other"


def test_non_json_output_becomes_summary():
    d = parse_vision_output("The slide shows a matrix multiplication example.")
    assert d.kind == "other"
    assert d.summary == "The slide shows a matrix multiplication example."


def test_describe_pending_skips_ok_frames_and_chains_previous_summary():
    frames = [
        make_keyframe(1, 1.0, status="ok", summary="Title slide"),
        make_keyframe(2, 20.0, status="pending"),
        make_keyframe(3, 40.0, status="failed"),
    ]
    llm = FakeLLM(vision_responses=[
        '{"kind": "formula", "summary": "Pythagoras", "ocr_text": "a^2+b^2=c^2", "change_note": "formula appears"}',
        '{"kind": "code", "summary": "Python loop", "ocr_text": "for i in range(3):", "change_note": ""}',
    ])
    saved = {}

    failed = describe_pending(llm, frames, saved.__setitem__, sleep=lambda s: None)

    assert failed == 0
    assert set(saved) == {2, 3}
    assert saved[2].kind == "formula"
    prompts = [args[0] for name, args in llm.calls if name == "vision"]
    assert "Title slide" in prompts[0]
    assert "Pythagoras" in prompts[1]


def test_one_failing_frame_does_not_stop_the_rest():
    frames = [make_keyframe(1, 1.0, status="pending"), make_keyframe(2, 20.0, status="pending")]
    llm = FakeLLM(vision_responses=[
        RuntimeError("429"), RuntimeError("429"),
        '{"kind": "slide", "summary": "Agenda", "ocr_text": "", "change_note": ""}',
    ])
    saved = {}

    failed = describe_pending(llm, frames, saved.__setitem__, attempts=2, sleep=lambda s: None)

    assert failed == 1
    assert saved[1] is None
    assert saved[2].summary == "Agenda"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_jsonfix.py tests/test_vision.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 4: Implement jsonfix.py**

`backend/unstuck/jsonfix.py`:

```python
import json
import re

from json_repair import repair_json

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)(?:```|$)", re.S)


def loads_lenient(raw: str) -> dict | list | None:
    """Parse JSON from model output: strip code fences and chatter, repair truncation."""
    text = raw.strip()
    fence = _FENCE_RE.search(text)
    if fence:
        text = fence.group(1).strip()
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not starts:
        return None
    text = text[min(starts):]
    try:
        obj, _end = json.JSONDecoder().raw_decode(text)
        return obj
    except json.JSONDecodeError:
        pass
    repaired = repair_json(text, return_objects=True)
    if isinstance(repaired, (dict, list)) and repaired:
        return repaired
    return None
```

- [ ] **Step 5: Implement vision.py**

`backend/unstuck/ingest/vision.py`:

```python
import logging
import time
from collections.abc import Callable
from pathlib import Path

from unstuck.jsonfix import loads_lenient
from unstuck.llm.base import LLM
from unstuck.llm.retry import with_retry
from unstuck.models import FrameDescription, Keyframe

log = logging.getLogger(__name__)

KINDS = frozenset({"formula", "code", "diagram", "slide", "talking_head", "other"})
_FIELDS = ("kind", "summary", "ocr_text", "change_note")

VISION_PROMPT = """You are indexing one frame of an educational video so a tutor can answer questions about it later.
Return ONLY a JSON object with exactly these keys:
- "kind": one of "formula", "code", "diagram", "slide", "talking_head", "other"
- "summary": 1-3 sentences on what the frame shows and teaches
- "ocr_text": all readable on-screen text; write formulas in LaTeX and keep code exactly as shown; "" if none
- "change_note": what changed compared with the previous frame; "" if nothing notable

Previous frame summary: {previous}"""


def build_vision_prompt(previous_summary: str) -> str:
    return VISION_PROMPT.format(previous=previous_summary or "(none, this is the first frame)")


def parse_vision_output(raw: str) -> FrameDescription:
    obj = loads_lenient(raw)
    if not isinstance(obj, dict) or not any(key in obj for key in _FIELDS):
        return FrameDescription(kind="other", summary=raw.strip()[:2000], ocr_text="", change_note="")
    kind = str(obj.get("kind", "other")).strip().lower()
    return FrameDescription(
        kind=kind if kind in KINDS else "other",
        summary=str(obj.get("summary", "")).strip(),
        ocr_text=str(obj.get("ocr_text", "")).strip(),
        change_note=str(obj.get("change_note", "")).strip(),
    )


def describe_pending(
    llm: LLM,
    frames: list[Keyframe],
    save: Callable[[int, FrameDescription | None], None],
    *,
    attempts: int = 2,
    base_delay: float = 2.0,
    sleep: Callable[[float], None] | None = None,
) -> int:
    """Describe every frame that is not yet 'ok', in time order, passing the previous
    frame's summary for context. A frame that keeps failing is saved as None
    (status 'failed') and the loop moves on. Returns the number of failed frames."""
    sleep = sleep or time.sleep
    previous = ""
    failed = 0
    for frame in sorted(frames, key=lambda f: f.t):
        if frame.status == "ok":
            previous = frame.summary
            continue
        prompt = build_vision_prompt(previous)
        try:
            raw = with_retry(
                lambda: llm.vision(prompt, Path(frame.image_path)),
                attempts=attempts, base_delay=base_delay, sleep=sleep,
            )
        except Exception:
            log.warning("vision failed for keyframe %s at %.1fs", frame.id, frame.t, exc_info=True)
            save(frame.id, None)
            failed += 1
            continue
        description = parse_vision_output(raw)
        save(frame.id, description)
        previous = description.summary
    return failed
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_jsonfix.py tests/test_vision.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/
git commit -m "feat(ingest): keyframe vision descriptions with tolerant JSON parsing and per-frame failure"
```

---

### Task 8: Timeline segmentation

**Files:**
- Create: `backend/unstuck/ingest/timeline.py`
- Test: `backend/tests/test_timeline.py`

**Interfaces:**
- Consumes: `Cue`, `Keyframe`, `Segment`, `sentence_end_flags`.
- Produces: `build_segments(cues: list[Cue], keyframes: list[Keyframe], duration: float, *, min_len=30.0, max_len=60.0, cut_eligible=45.0, cut_tolerance=1.5, min_tail=10.0) -> list[Segment]` (segments without ids; `keyframe_ids` = keyframes whose `t` falls in the segment).

Rules (pin these down in tests): a segment closes after a cue when its length ≥ 30 s and the cue ends a sentence; or when length ≥ 45 s and a keyframe (scene change) lies within 1.5 s of the cue end; a new segment is forced before a cue that would push length past 60 s; a final tail shorter than 10 s merges into the previous segment; with no cues the video is cut into 45 s windows with empty transcripts.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_timeline.py`:

```python
from tests.factories import make_keyframe
from unstuck.ingest.timeline import build_segments
from unstuck.models import Cue


def cues_every(n, length=5.0, texts=None):
    texts = texts or {}
    return [Cue(i * length, (i + 1) * length, texts.get(i, f"w{i}")) for i in range(n)]


def spans(segments):
    return [(s.start, s.end) for s in segments]


def test_cuts_at_first_sentence_end_after_30s():
    cues = cues_every(20, texts={3: "too early.", 8: "first.", 15: "second."})
    assert spans(build_segments(cues, [], 100)) == [(0, 45), (45, 80), (80, 100)]


def test_forces_cut_at_60s_without_sentence_ends():
    assert spans(build_segments(cues_every(30), [], 150)) == [(0, 60), (60, 120), (120, 150)]


def test_scene_change_cuts_after_45s_and_assigns_keyframes():
    frames = [make_keyframe(1, 36.0), make_keyframe(2, 51.0)]
    segments = build_segments(cues_every(20), frames, 100)
    assert spans(segments) == [(0, 50), (50, 100)]
    assert segments[0].keyframe_ids == [1]
    assert segments[1].keyframe_ids == [2]


def test_short_tail_merges_into_previous():
    cues = cues_every(13, texts={11: "done."})
    assert spans(build_segments(cues, [], 65)) == [(0, 65)]


def test_transcript_joins_cue_text():
    cues = cues_every(3, texts={0: "Hello", 1: "vector", 2: "world."})
    (segment,) = build_segments(cues, [], 15)
    assert segment.transcript == "Hello vector world."


def test_keyframe_before_first_cue_goes_to_first_segment():
    cues = [Cue(5.0, 10.0, "late start.")]
    (segment,) = build_segments(cues, [make_keyframe(7, 1.0)], 10)
    assert segment.keyframe_ids == [7]


def test_no_cues_makes_fixed_windows():
    segments = build_segments([], [make_keyframe(3, 50.0)], 100)
    assert spans(segments) == [(0, 45), (45, 90), (90, 100)]
    assert all(s.transcript == "" for s in segments)
    assert segments[1].keyframe_ids == [3]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_timeline.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'unstuck.ingest.timeline'`.

- [ ] **Step 3: Implement**

`backend/unstuck/ingest/timeline.py`:

```python
import bisect

from unstuck.ingest.captions import sentence_end_flags
from unstuck.models import Cue, Keyframe, Segment

EMPTY_WINDOW_S = 45.0


def _near_cut(t: float, cut_times: list[float], tolerance: float) -> bool:
    i = bisect.bisect_left(cut_times, t - tolerance)
    return i < len(cut_times) and cut_times[i] <= t + tolerance


def _group_cues(
    cues: list[Cue], cut_times: list[float], *, min_len: float, max_len: float,
    cut_eligible: float, cut_tolerance: float, min_tail: float,
) -> list[list[int]]:
    flags = sentence_end_flags(cues)
    groups: list[list[int]] = []
    current: list[int] = []
    for i, cue in enumerate(cues):
        if current and cue.end - cues[current[0]].start > max_len:
            groups.append(current)
            current = []
        current.append(i)
        length = cue.end - cues[current[0]].start
        if (length >= min_len and flags[i]) or (
            length >= cut_eligible and _near_cut(cue.end, cut_times, cut_tolerance)
        ):
            groups.append(current)
            current = []
    if current:
        groups.append(current)

    if len(groups) > 1:
        tail = groups[-1]
        if cues[tail[-1]].end - cues[tail[0]].start < min_tail:
            groups[-2].extend(groups.pop())
    return groups


def _attach_keyframes(spans: list[tuple[float, float, str]], keyframes: list[Keyframe]) -> list[Segment]:
    starts = [start for start, _end, _text in spans]
    ids: list[list[int]] = [[] for _ in spans]
    for frame in sorted(keyframes, key=lambda f: f.t):
        index = max(0, bisect.bisect_right(starts, frame.t) - 1)
        ids[index].append(frame.id)
    return [
        Segment(start=start, end=end, transcript=text, keyframe_ids=frame_ids)
        for (start, end, text), frame_ids in zip(spans, ids)
    ]


def build_segments(
    cues: list[Cue],
    keyframes: list[Keyframe],
    duration: float,
    *,
    min_len: float = 30.0,
    max_len: float = 60.0,
    cut_eligible: float = 45.0,
    cut_tolerance: float = 1.5,
    min_tail: float = 10.0,
) -> list[Segment]:
    """Cut the transcript into 30–60 s segments, preferring sentence ends, then scene changes."""
    if not cues:
        spans = []
        start = 0.0
        while start < duration:
            spans.append((start, min(start + EMPTY_WINDOW_S, duration), ""))
            start += EMPTY_WINDOW_S
        return _attach_keyframes(spans, keyframes)

    cut_times = sorted(f.t for f in keyframes)
    groups = _group_cues(
        cues, cut_times, min_len=min_len, max_len=max_len, cut_eligible=cut_eligible,
        cut_tolerance=cut_tolerance, min_tail=min_tail,
    )
    spans = [
        (cues[g[0]].start, cues[g[-1]].end, " ".join(cues[i].text for i in g))
        for g in groups
    ]
    return _attach_keyframes(spans, keyframes)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_timeline.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/
git commit -m "feat(ingest): segment timeline at sentence ends and scene changes (30-60s)"
```

---

### Task 9: Vector indexer

**Files:**
- Create: `backend/unstuck/ingest/indexer.py`
- Test: `backend/tests/test_indexer.py`

**Interfaces:**
- Consumes: `repo.get_segments`, `repo.get_keyframes`, `repo.replace_vectors`, `repo.search_vectors`, `LLM.embed`.
- Produces: `segment_text(segment: Segment, frames_by_id: dict[int, Keyframe]) -> str`; `build_index(conn, llm, video_id) -> int` (count indexed); `search(conn, llm, video_id, query: str, k: int) -> list[int]` (segment ids, best first).

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_indexer.py`:

```python
from tests.factories import make_keyframe
from unstuck.db import repo
from unstuck.ingest.indexer import build_index, search, segment_text
from unstuck.models import Segment

VID = "abcdefghijk"


def test_segment_text_includes_only_ok_frames():
    segment = Segment(0, 30, "we talk", [1, 2])
    frames = {
        1: make_keyframe(1, 5.0, summary="Slide on vectors", ocr_text="v = (1, 2)"),
        2: make_keyframe(2, 10.0, status="failed", summary="ignored"),
    }
    text = segment_text(segment, frames)
    assert "we talk" in text
    assert "Slide on vectors" in text
    assert "v = (1, 2)" in text
    assert "ignored" not in text


def test_build_index_and_search(conn, fake_llm):
    repo.replace_segments(conn, VID, [
        Segment(0, 30, "introduction to the course"),
        Segment(30, 60, "eigenvalues and eigenvectors explained"),
        Segment(60, 90, "summary and goodbye"),
    ])
    assert build_index(conn, fake_llm, VID) == 3

    segments = repo.get_segments(conn, VID)
    assert search(conn, fake_llm, VID, "what are eigenvectors", k=1) == [segments[1].id]


def test_build_index_with_no_segments(conn, fake_llm):
    assert build_index(conn, fake_llm, VID) == 0
    assert search(conn, fake_llm, VID, "anything", k=3) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_indexer.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`backend/unstuck/ingest/indexer.py`:

```python
import sqlite3

from unstuck.db import repo
from unstuck.llm.base import LLM
from unstuck.models import Keyframe, Segment

EMBED_BATCH = 32


def segment_text(segment: Segment, frames_by_id: dict[int, Keyframe]) -> str:
    """Text that represents a segment in the vector index: transcript + what was on screen."""
    parts = [segment.transcript]
    for frame_id in segment.keyframe_ids:
        frame = frames_by_id.get(frame_id)
        if frame and frame.status == "ok":
            parts.append(frame.summary)
            parts.append(frame.ocr_text)
    return "\n".join(p for p in parts if p)


def build_index(conn: sqlite3.Connection, llm: LLM, video_id: str) -> int:
    segments = repo.get_segments(conn, video_id)
    frames_by_id = {f.id: f for f in repo.get_keyframes(conn, video_id)}
    texts = [segment_text(s, frames_by_id) or " " for s in segments]
    vectors: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        vectors.extend(llm.embed(texts[i : i + EMBED_BATCH]))
    repo.replace_vectors(conn, video_id, [(s.id, v) for s, v in zip(segments, vectors)])
    return len(segments)


def search(conn: sqlite3.Connection, llm: LLM, video_id: str, query: str, k: int) -> list[int]:
    (vector,) = llm.embed([query])
    return [segment_id for segment_id, _distance in repo.search_vectors(conn, video_id, vector, k)]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_indexer.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/
git commit -m "feat(ingest): embed segments (transcript + frame text) into sqlite-vec"
```

---

### Task 10: Outline generation

**Files:**
- Create: `backend/unstuck/ingest/outline.py`
- Test: `backend/tests/test_outline.py`

**Interfaces:**
- Consumes: `loads_lenient`, `parse_ts`, `fmt_ts`, `LLM.chat`, `Segment`.
- Produces: `build_outline_messages(segments, title) -> list[dict]`; `parse_outline(raw: str, duration: float) -> list[dict]` (each `{"start": float, "title": str, "summary": str}`, sorted, first start 0.0); `generate_outline(llm, segments, title, duration) -> list[dict]`; `current_chapter(outline: list[dict] | None, t: float) -> int | None`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_outline.py`:

```python
from unstuck.ingest.outline import current_chapter, generate_outline, parse_outline
from unstuck.llm import FakeLLM
from unstuck.models import Segment


def test_parse_outline_normalizes_entries():
    raw = """```json
{"chapters": [
  {"start": "02:00", "title": "Matrices", "summary": "m"},
  {"start": 5, "title": "Vectors", "summary": "v"},
  {"start": 999, "title": "Beyond the end", "summary": ""},
  {"start": 60, "summary": "no title"}
]}
```"""
    outline = parse_outline(raw, duration=300)
    assert [c["title"] for c in outline] == ["Vectors", "Matrices"]
    assert outline[0]["start"] == 0.0
    assert outline[1] == {"start": 120.0, "title": "Matrices", "summary": "m"}


def test_parse_outline_accepts_bare_list():
    outline = parse_outline('[{"start": 0, "title": "Only"}]', duration=60)
    assert outline == [{"start": 0.0, "title": "Only", "summary": ""}]


def test_parse_outline_garbage():
    assert parse_outline("sorry, I cannot", duration=60) == []


def test_current_chapter():
    outline = [{"start": 0.0}, {"start": 120.0}, {"start": 200.0}]
    assert current_chapter(outline, 0) == 0
    assert current_chapter(outline, 119.9) == 0
    assert current_chapter(outline, 120) == 1
    assert current_chapter(outline, 250) == 2
    assert current_chapter([], 10) is None
    assert current_chapter(None, 10) is None


def test_generate_outline_sends_timestamped_transcript():
    llm = FakeLLM(chat_responses=['{"chapters": [{"start": 0, "title": "Intro", "summary": "s"}]}'])
    segments = [Segment(0, 30, "hello there"), Segment(30, 60, "dot products")]

    outline = generate_outline(llm, segments, title="Linear Algebra", duration=60)

    assert outline == [{"start": 0.0, "title": "Intro", "summary": "s"}]
    (_name, messages), = llm.calls
    prompt = messages[-1]["content"]
    assert "Linear Algebra" in prompt
    assert "[00:30] dot products" in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_outline.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`backend/unstuck/ingest/outline.py`:

```python
from unstuck.jsonfix import loads_lenient
from unstuck.llm.base import LLM
from unstuck.models import Segment
from unstuck.timefmt import fmt_ts, parse_ts

SEGMENT_CHARS = 300

OUTLINE_PROMPT = """Split this educational video into 3-12 chapters using its timestamped transcript.
Return ONLY JSON: {{"chapters": [{{"start": <start time in seconds, a number>, "title": "<short title>", "summary": "<one sentence>"}}]}}
Write titles and summaries in the same language as the transcript. The first chapter starts at 0.

Video title: {title}
Transcript:
{lines}"""


def build_outline_messages(segments: list[Segment], title: str) -> list[dict]:
    lines = "\n".join(f"[{fmt_ts(s.start)}] {s.transcript[:SEGMENT_CHARS]}" for s in segments)
    return [{"role": "user", "content": OUTLINE_PROMPT.format(title=title, lines=lines)}]


def _start_seconds(value) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return parse_ts(value)
        except ValueError:
            try:
                return float(value)
            except ValueError:
                return None
    return None


def parse_outline(raw: str, duration: float) -> list[dict]:
    obj = loads_lenient(raw)
    if isinstance(obj, dict):
        obj = obj.get("chapters")
    if not isinstance(obj, list):
        return []
    chapters = []
    for item in obj:
        if not isinstance(item, dict) or not str(item.get("title", "")).strip():
            continue
        start = _start_seconds(item.get("start"))
        if start is None or start < 0 or start >= duration:
            continue
        chapters.append(
            {"start": start, "title": str(item["title"]).strip(), "summary": str(item.get("summary", "")).strip()}
        )
    chapters.sort(key=lambda c: c["start"])
    if chapters:
        chapters[0]["start"] = 0.0
    return chapters


def generate_outline(llm: LLM, segments: list[Segment], title: str, duration: float) -> list[dict]:
    raw = llm.chat(build_outline_messages(segments, title), json_mode=True)
    return parse_outline(raw, duration)


def current_chapter(outline: list[dict] | None, t: float) -> int | None:
    if not outline:
        return None
    index = None
    for i, chapter in enumerate(outline):
        if chapter["start"] <= t:
            index = i
    return index
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_outline.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/
git commit -m "feat(ingest): generate and normalize chapter outline"
```

---

### Task 11: Pipeline state machine

**Files:**
- Create: `backend/unstuck/ingest/pipeline.py`
- Test: `backend/tests/test_pipeline.py`

**Interfaces:**
- Consumes: `repo.get_video`, `repo.update_video`, `IngestError`, `LLM`, `Settings`.
- Produces:
  - `STAGES = ("metadata", "transcript", "download", "keyframes", "vision", "timeline", "index", "outline")`, `CRITICAL = {"metadata", "transcript"}`, `SKIPPED = "skipped"`
  - `StageContext(conn, llm, settings, video_id)` dataclass with property `video_dir -> Path` (created on access)
  - `StageFn = Callable[[StageContext], str | None]` — return `"skipped"` or `None` (= done); raise to fail
  - `Pipeline(stages: Mapping[str, StageFn] | None = None)` (None → real stages from Task 12); `Pipeline.run(ctx) -> None`

Behaviour: stage states are `pending|running|done|failed|skipped`. `run` resumes at the first stage that is not `done`/`skipped`, resets that stage and every later stage to `pending`, then runs them in order. A failure in a critical stage stops the run with `status="failed"` and `error_code/error_message`. A failure in any other stage is recorded and the run continues; the video ends `status="ready"` with the last error kept in `error_code/error_message`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_pipeline.py`:

```python
import pytest

from unstuck.db import repo
from unstuck.errors import IngestError
from unstuck.ingest.pipeline import SKIPPED, STAGES, Pipeline, StageContext

VID = "abcdefghijk"


@pytest.fixture
def ctx(conn, fake_llm, settings):
    repo.insert_video(conn, VID, "https://youtu.be/abcdefghijk")
    return StageContext(conn=conn, llm=fake_llm, settings=settings, video_id=VID)


def recording(order, overrides=None):
    overrides = overrides or {}

    def make(name):
        def stage(ctx):
            order.append(name)
            if name in overrides:
                return overrides[name](ctx)
            return None

        return stage

    return {name: make(name) for name in STAGES}


def raise_(exc):
    def fn(ctx):
        raise exc

    return fn


def test_fresh_run_executes_all_stages(ctx):
    order = []
    Pipeline(recording(order)).run(ctx)
    video = repo.get_video(ctx.conn, VID)
    assert order == list(STAGES)
    assert video.status == "ready"
    assert set(video.stage_states.values()) == {"done"}


def test_resumes_after_completed_stages(ctx):
    repo.update_video(ctx.conn, VID, stage_states={"metadata": "done", "transcript": "done", "download": "done"})
    order = []
    Pipeline(recording(order)).run(ctx)
    assert order == ["keyframes", "vision", "timeline", "index", "outline"]


def test_rerunning_a_stage_resets_downstream_stages(ctx):
    states = {name: "done" for name in STAGES}
    states["vision"] = "failed"
    repo.update_video(ctx.conn, VID, stage_states=states)
    order = []
    Pipeline(recording(order)).run(ctx)
    assert order == ["vision", "timeline", "index", "outline"]


def test_running_state_left_by_a_crash_is_resumed(ctx):
    repo.update_video(ctx.conn, VID, stage_states={"metadata": "done", "transcript": "running"})
    order = []
    Pipeline(recording(order)).run(ctx)
    assert order[0] == "transcript"


def test_critical_failure_stops_the_run(ctx):
    order = []
    stages = recording(order, {"transcript": raise_(IngestError("NO_TRANSCRIPT", "没有字幕"))})
    Pipeline(stages).run(ctx)
    video = repo.get_video(ctx.conn, VID)
    assert order == ["metadata", "transcript"]
    assert video.status == "failed"
    assert video.error_code == "NO_TRANSCRIPT"
    assert video.error_message == "没有字幕"
    assert video.stage_states["transcript"] == "failed"
    assert video.stage_states["download"] == "pending"


def test_non_critical_failure_continues(ctx):
    order = []
    Pipeline(recording(order, {"vision": raise_(RuntimeError("model down"))})).run(ctx)
    video = repo.get_video(ctx.conn, VID)
    assert order == list(STAGES)
    assert video.status == "ready"
    assert video.stage_states["vision"] == "failed"
    assert video.stage_states["outline"] == "done"
    assert video.error_code == "STAGE_FAILED"
    assert "model down" in video.error_message


def test_skipped_stages_are_recorded_and_not_rerun(ctx):
    order = []
    Pipeline(recording(order, {"keyframes": lambda c: SKIPPED})).run(ctx)
    assert repo.get_video(ctx.conn, VID).stage_states["keyframes"] == "skipped"

    order.clear()
    Pipeline(recording(order)).run(ctx)
    assert order == []
    assert repo.get_video(ctx.conn, VID).status == "ready"


def test_retry_clears_previous_error(ctx):
    Pipeline(recording([], {"metadata": raise_(IngestError("VIDEO_UNAVAILABLE", "x"))})).run(ctx)
    assert repo.get_video(ctx.conn, VID).status == "failed"
    Pipeline(recording([])).run(ctx)
    video = repo.get_video(ctx.conn, VID)
    assert video.status == "ready"
    assert video.error_code is None


def test_stage_context_creates_video_dir(ctx, settings):
    assert ctx.video_dir == settings.video_dir(VID)
    assert ctx.video_dir.is_dir()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_pipeline.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'unstuck.ingest.pipeline'`.

- [ ] **Step 3: Implement**

`backend/unstuck/ingest/pipeline.py`:

```python
import logging
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from unstuck.config import Settings
from unstuck.db import repo
from unstuck.errors import IngestError
from unstuck.llm.base import LLM

log = logging.getLogger(__name__)

STAGES = ("metadata", "transcript", "download", "keyframes", "vision", "timeline", "index", "outline")
CRITICAL = frozenset({"metadata", "transcript"})
SKIPPED = "skipped"
_FINISHED = ("done", SKIPPED)


@dataclass
class StageContext:
    conn: sqlite3.Connection
    llm: LLM
    settings: Settings
    video_id: str

    @property
    def video_dir(self) -> Path:
        path = self.settings.video_dir(self.video_id)
        path.mkdir(parents=True, exist_ok=True)
        return path


StageFn = Callable[[StageContext], str | None]


def _describe(stage: str, exc: Exception) -> tuple[str, str]:
    if isinstance(exc, IngestError):
        return exc.code, exc.message
    return "STAGE_FAILED", f"{stage} 阶段失败：{exc}"[:500]


class Pipeline:
    def __init__(self, stages: Mapping[str, StageFn] | None = None):
        if stages is None:
            from unstuck.ingest.stages import DEFAULT_STAGES

            stages = DEFAULT_STAGES
        missing = set(STAGES) - set(stages)
        if missing:
            raise ValueError(f"missing stage functions: {sorted(missing)}")
        self._stages = dict(stages)

    def run(self, ctx: StageContext) -> None:
        video = repo.get_video(ctx.conn, ctx.video_id)
        if video is None:
            raise ValueError(f"unknown video {ctx.video_id}")
        states = {name: video.stage_states.get(name, "pending") for name in STAGES}
        start = next((i for i, name in enumerate(STAGES) if states[name] not in _FINISHED), None)
        if start is None:
            repo.update_video(ctx.conn, ctx.video_id, status="ready")
            return

        for name in STAGES[start:]:
            states[name] = "pending"
        repo.update_video(
            ctx.conn, ctx.video_id, status="importing", stage_states=states, error_code=None, error_message=None
        )

        last_error: tuple[str, str] | None = None
        for name in STAGES[start:]:
            states[name] = "running"
            repo.update_video(ctx.conn, ctx.video_id, stage_states=states)
            try:
                result = self._stages[name](ctx)
                states[name] = SKIPPED if result == SKIPPED else "done"
            except Exception as exc:
                log.exception("stage %s failed for %s", name, ctx.video_id)
                states[name] = "failed"
                code, message = _describe(name, exc)
                if name in CRITICAL:
                    repo.update_video(
                        ctx.conn, ctx.video_id, status="failed", stage_states=states,
                        error_code=code, error_message=message,
                    )
                    return
                last_error = (code, message)
            repo.update_video(ctx.conn, ctx.video_id, stage_states=states)

        code, message = last_error or (None, None)
        repo.update_video(
            ctx.conn, ctx.video_id, status="ready", stage_states=states, error_code=code, error_message=message
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_pipeline.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/
git commit -m "feat(ingest): resumable pipeline state machine with critical/non-critical stages"
```

---

### Task 12: Real stage implementations

**Files:**
- Create: `backend/unstuck/ingest/stages.py`
- Test: `backend/tests/test_stages.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `stage_metadata`, `stage_transcript`, `stage_download`, `stage_keyframes`, `stage_vision`, `stage_timeline`, `stage_index`, `stage_outline` (each `(ctx: StageContext) -> str | None`), and `DEFAULT_STAGES: dict[str, StageFn]`.

Behaviour:
- `metadata`: `fetch_info` → `check_video_info` → save `info.json` in the video dir → store title/channel/duration.
- `transcript`: captions from `info.json`; if none, download audio and run Whisper; if still nothing, raise `IngestError("NO_TRANSCRIPT", …)`. Stores cues, then **immediately builds a transcript-only timeline and index and sets `ask_ready=True`** so the user can start asking before vision finishes.
- `download`: download the video; on `IngestError` set `visual_mode="transcript_only"` (graceful degrade, stage still `done`); on success `visual_mode="full"`.
- `keyframes` / `vision`: return `"skipped"` in `transcript_only` mode. `vision` raises `IngestError("VISION_PARTIAL", …)` if some frames failed (non-critical; a retry re-describes only failed frames).
- `timeline`: rebuild segments from cues + keyframes. `index`: re-embed. `outline`: generate and store.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_stages.py`:

```python
from pathlib import Path

import pytest

from unstuck.db import repo
from unstuck.errors import IngestError
from unstuck.ingest import stages, vision, youtube
from unstuck.ingest.pipeline import SKIPPED, StageContext
from unstuck.llm import FakeLLM
from unstuck.models import Cue, NewKeyframe, Segment

VID = "abcdefghijk"


@pytest.fixture
def ctx(conn, fake_llm, settings):
    repo.insert_video(conn, VID, "https://youtu.be/abcdefghijk")
    repo.update_video(conn, VID, duration=60.0, title="Vectors 101")
    return StageContext(conn=conn, llm=fake_llm, settings=settings, video_id=VID)


def sentences(n):
    return [Cue(i * 5.0, (i + 1) * 5.0, f"sentence number {i}.") for i in range(n)]


def test_metadata_stores_info(ctx, monkeypatch):
    info = {"id": VID, "title": "Dot products", "channel": "3B", "duration": 300}
    monkeypatch.setattr(youtube, "fetch_info", lambda video_id: info)
    stages.stage_metadata(ctx)
    video = repo.get_video(ctx.conn, VID)
    assert (video.title, video.channel, video.duration) == ("Dot products", "3B", 300.0)
    assert youtube.load_info(ctx.video_dir / "info.json") == info


def test_metadata_rejects_too_long(ctx, monkeypatch):
    monkeypatch.setattr(youtube, "fetch_info", lambda video_id: {"id": VID, "duration": 99999})
    with pytest.raises(IngestError) as err:
        stages.stage_metadata(ctx)
    assert err.value.code == "TOO_LONG"


def test_transcript_from_captions_enables_asking(ctx, monkeypatch):
    youtube.save_info({"id": VID}, ctx.video_dir / "info.json")
    cues = sentences(12)
    monkeypatch.setattr(youtube, "fetch_captions", lambda info: cues)
    monkeypatch.setattr(youtube, "download_audio", lambda *a: pytest.fail("must not download audio"))

    stages.stage_transcript(ctx)

    assert repo.get_cues(ctx.conn, VID) == cues
    assert repo.get_video(ctx.conn, VID).ask_ready is True
    segments = repo.get_segments(ctx.conn, VID)
    assert segments
    assert repo.search_vectors(ctx.conn, VID, ctx.llm.embed(["sentence"])[0], k=1)


def test_transcript_falls_back_to_whisper(ctx, monkeypatch, tmp_path):
    youtube.save_info({"id": VID}, ctx.video_dir / "info.json")
    monkeypatch.setattr(youtube, "fetch_captions", lambda info: None)
    monkeypatch.setattr(youtube, "download_audio", lambda video_id, out_dir: tmp_path / "audio.m4a")
    monkeypatch.setattr(stages.transcribe, "transcribe", lambda path, model: sentences(3))

    stages.stage_transcript(ctx)

    assert len(repo.get_cues(ctx.conn, VID)) == 3


def test_transcript_with_nothing_fails(ctx, monkeypatch, tmp_path):
    youtube.save_info({"id": VID}, ctx.video_dir / "info.json")
    monkeypatch.setattr(youtube, "fetch_captions", lambda info: None)
    monkeypatch.setattr(youtube, "download_audio", lambda video_id, out_dir: tmp_path / "audio.m4a")
    monkeypatch.setattr(stages.transcribe, "transcribe", lambda path, model: [])
    with pytest.raises(IngestError) as err:
        stages.stage_transcript(ctx)
    assert err.value.code == "NO_TRANSCRIPT"


def test_download_failure_degrades_to_transcript_only(ctx, monkeypatch):
    def fail(video_id, out_dir):
        raise IngestError("DOWNLOAD_FAILED", "blocked")

    monkeypatch.setattr(youtube, "download_video", fail)
    stages.stage_download(ctx)
    assert repo.get_video(ctx.conn, VID).visual_mode == "transcript_only"
    assert stages.stage_keyframes(ctx) == SKIPPED
    assert stages.stage_vision(ctx) == SKIPPED


def test_keyframes_stage_stores_frames(ctx, monkeypatch):
    (ctx.video_dir / "video.mp4").write_bytes(b"fake")
    frames = [NewKeyframe(1.0, "/f/1.jpg", "0" * 16)]
    monkeypatch.setattr(stages.keyframes, "extract_keyframes", lambda path, duration, out_dir: frames)
    stages.stage_keyframes(ctx)
    assert [f.t for f in repo.get_keyframes(ctx.conn, VID)] == [1.0]


def test_vision_partial_failure_raises_after_saving_the_rest(ctx, monkeypatch):
    monkeypatch.setattr(vision.time, "sleep", lambda s: None)
    repo.replace_keyframes(ctx.conn, VID, [NewKeyframe(1.0, "/f/1.jpg", "a"), NewKeyframe(20.0, "/f/2.jpg", "b")])
    ctx.llm = FakeLLM(vision_responses=[
        RuntimeError("429"), RuntimeError("429"),
        '{"kind": "slide", "summary": "ok", "ocr_text": "", "change_note": ""}',
    ])
    with pytest.raises(IngestError) as err:
        stages.stage_vision(ctx)
    assert err.value.code == "VISION_PARTIAL"
    assert [f.status for f in repo.get_keyframes(ctx.conn, VID)] == ["failed", "ok"]


def test_timeline_and_index_use_keyframes(ctx):
    repo.replace_cues(ctx.conn, VID, sentences(12))
    (frame,) = repo.replace_keyframes(ctx.conn, VID, [NewKeyframe(10.0, "/f/1.jpg", "a")])
    stages.stage_timeline(ctx)
    assert frame.id in repo.get_segments(ctx.conn, VID)[0].keyframe_ids
    stages.stage_index(ctx)
    assert repo.search_vectors(ctx.conn, VID, ctx.llm.embed(["sentence"])[0], k=1)


def test_outline_stage_stores_outline(ctx):
    repo.replace_segments(ctx.conn, VID, [Segment(0, 30, "intro"), Segment(30, 60, "dot products")])
    ctx.llm = FakeLLM(chat_responses=['{"chapters": [{"start": 0, "title": "Intro", "summary": "s"}]}'])
    stages.stage_outline(ctx)
    assert repo.get_video(ctx.conn, VID).outline == [{"start": 0.0, "title": "Intro", "summary": "s"}]


def test_default_stages_cover_all():
    from unstuck.ingest.pipeline import STAGES

    assert set(stages.DEFAULT_STAGES) == set(STAGES)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_stages.py -v`
Expected: FAIL with `ImportError: cannot import name 'stages'`.

- [ ] **Step 3: Implement**

`backend/unstuck/ingest/stages.py`:

```python
import logging

from unstuck.db import repo
from unstuck.errors import IngestError
from unstuck.ingest import indexer, keyframes, outline, timeline, transcribe, vision, youtube
from unstuck.ingest.pipeline import SKIPPED, StageContext, StageFn

log = logging.getLogger(__name__)

INFO_FILE = "info.json"


def _rebuild_timeline(ctx: StageContext) -> None:
    video = repo.get_video(ctx.conn, ctx.video_id)
    segments = timeline.build_segments(
        repo.get_cues(ctx.conn, ctx.video_id), repo.get_keyframes(ctx.conn, ctx.video_id), video.duration
    )
    repo.replace_segments(ctx.conn, ctx.video_id, segments)


def _transcript_only(ctx: StageContext) -> bool:
    return repo.get_video(ctx.conn, ctx.video_id).visual_mode == "transcript_only"


def stage_metadata(ctx: StageContext) -> None:
    info = youtube.fetch_info(ctx.video_id)
    meta = youtube.check_video_info(info, ctx.settings.max_duration_s)
    youtube.save_info(info, ctx.video_dir / INFO_FILE)
    repo.update_video(ctx.conn, ctx.video_id, title=meta.title, channel=meta.channel, duration=meta.duration)


def stage_transcript(ctx: StageContext) -> None:
    info = youtube.load_info(ctx.video_dir / INFO_FILE)
    cues = youtube.fetch_captions(info)
    if not cues:
        log.info("no captions for %s, transcribing audio", ctx.video_id)
        audio = youtube.download_audio(ctx.video_id, ctx.video_dir)
        cues = transcribe.transcribe(audio, ctx.settings.whisper_model)
    if not cues:
        raise IngestError("NO_TRANSCRIPT", "没有字幕，语音转写也没有得到内容")
    repo.replace_cues(ctx.conn, ctx.video_id, cues)
    # Transcript-only timeline + index so the learner can start asking right away;
    # the timeline/index stages rebuild it with visual information later.
    _rebuild_timeline(ctx)
    indexer.build_index(ctx.conn, ctx.llm, ctx.video_id)
    repo.update_video(ctx.conn, ctx.video_id, ask_ready=True)


def stage_download(ctx: StageContext) -> None:
    try:
        youtube.download_video(ctx.video_id, ctx.video_dir)
    except IngestError:
        log.warning("download failed for %s, continuing transcript-only", ctx.video_id, exc_info=True)
        repo.update_video(ctx.conn, ctx.video_id, visual_mode="transcript_only")
        return
    repo.update_video(ctx.conn, ctx.video_id, visual_mode="full")


def stage_keyframes(ctx: StageContext) -> str | None:
    if _transcript_only(ctx):
        return SKIPPED
    video_path = youtube.find_video_file(ctx.video_dir)
    if video_path is None:
        raise IngestError("DOWNLOAD_FAILED", "找不到已下载的视频文件")
    duration = repo.get_video(ctx.conn, ctx.video_id).duration
    frames = keyframes.extract_keyframes(video_path, duration, ctx.video_dir / "frames")
    repo.replace_keyframes(ctx.conn, ctx.video_id, frames)
    return None


def stage_vision(ctx: StageContext) -> str | None:
    if _transcript_only(ctx):
        return SKIPPED
    failed = vision.describe_pending(
        ctx.llm,
        repo.get_keyframes(ctx.conn, ctx.video_id),
        lambda frame_id, desc: repo.save_frame_description(ctx.conn, frame_id, desc),
    )
    if failed:
        raise IngestError("VISION_PARTIAL", f"{failed} 张关键帧的画面理解失败，可以重试")
    return None


def stage_timeline(ctx: StageContext) -> None:
    _rebuild_timeline(ctx)


def stage_index(ctx: StageContext) -> None:
    indexer.build_index(ctx.conn, ctx.llm, ctx.video_id)


def stage_outline(ctx: StageContext) -> None:
    video = repo.get_video(ctx.conn, ctx.video_id)
    chapters = outline.generate_outline(
        ctx.llm, repo.get_segments(ctx.conn, ctx.video_id), video.title, video.duration
    )
    if not chapters:
        raise IngestError("OUTLINE_FAILED", "大纲生成失败，可以重试")
    repo.update_video(ctx.conn, ctx.video_id, outline=chapters)


DEFAULT_STAGES: dict[str, StageFn] = {
    "metadata": stage_metadata,
    "transcript": stage_transcript,
    "download": stage_download,
    "keyframes": stage_keyframes,
    "vision": stage_vision,
    "timeline": stage_timeline,
    "index": stage_index,
    "outline": stage_outline,
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_stages.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the whole suite**

Run: `cd backend && uv run pytest -v`
Expected: all PASS, nothing touches the network.

- [ ] **Step 6: Commit**

```bash
git add backend/
git commit -m "feat(ingest): implement the eight pipeline stages with early transcript-only indexing"
```

---

**Part 1 done when:** `uv run pytest` is green and `scripts/smoke_llm.py` prints chat/vision/embedding results. Next: `2026-10-09-unstuck-p2-tutor-api.md`.
