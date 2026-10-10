# Unstuck 第二阶段：B站视频来源 — 设计文档

> 日期：2026-10-10 · 状态：待用户审阅
> 前置：第一阶段（`2026-10-09-unstuck-design.md`）已完成并合并到 main。

## 1. 目标

让 Unstuck 支持 B站视频，**使用体验和现在的 YouTube 一样**：粘贴链接后立即可以观看；提问时视频自动暂停，AI 知道当前第几秒、看得到当前画面；回答里的时间戳可以点击跳转。

### 本期范围
- 支持三种链接：`bilibili.com/video/BV…`、带分P参数的 `?p=N`、手机 App 分享的 `b23.tv` 短链接
- 普通视频，以及分P课程中的单集（一次只导入链接对应的那一集，其他集在视频页一键导入）
- 打开即播放：720p MP4 直接播放 B站视频流，后台照常导入分析
- 字幕优先使用 B站 AI 字幕；没有字幕或字幕对不上时，用本地 Whisper 转写
- 读取 Chrome 里的 B站登录状态；未登录时提醒，坚持不登录也照常使用，但会降级
- 可选：1080p（音视频分离流，后端转发 + dash.js 播放），出问题自动退回 720p
- 新增 `docs/challenges.md`，记录开发中遇到的技术难点和解决办法

### 明确不做
- 付费课程（课堂 / cheese）
- 弹幕
- 合集、UP 主主页或收藏夹整批导入（只接受单个视频的链接）
- 修改 YouTube 的播放方式

## 2. 调研结论（已在真实环境验证）

| 事项 | 结论 |
|---|---|
| 视频信息和下载 | yt-dlp 支持 B站，视频 ID 为 BV 号；分P课程每一集有独立的 cid |
| 未登录时的字幕 | B站不返回任何字幕，只能用 Whisper |
| 登录方式 | yt-dlp 可以读取 Chrome 的 cookie；关键 cookie 是 `SESSDATA`，可用 `x/web-interface/nav` 接口的 `isLogin` 字段检查 |
| AI 字幕 | 登录后视频有 `ai-zh` 字幕，但 **yt-dlp 拿不到**，需要自己调用接口 |
| 旧版接口的问题 | 未签名的 `x/player/v2` 有时会返回**别的视频的字幕**（实测：导数课返回了京都美食的字幕） |
| 正确的接口 | 带 WBI 签名的 `x/player/wbi/v2`，实测多次结果一致，字幕时长和视频匹配 |
| 720p 播放 | `x/player/wbi/playurl` 加上 `platform=html5&fnval=1`，返回带音频的 MP4（实测 21 分钟视频约 52 MB），CDN 支持 Range 请求，不检查 Referer，浏览器 `<video>` 可以直接播放和跳转；地址 120 分钟后过期 |
| 1080p | 只能通过 DASH 获取（`fnval=16`），音视频分为两路流，带 `segment_base`；CDN **要求 Referer 是 bilibili.com**，否则返回 403，所以浏览器无法直接请求，必须由后端转发 |
| 风控 | 短时间内多次搜索会触发 HTTP 412 |
| 嵌入播放器 | `player.bilibili.com` 没有公开的 JS 接口可以读取当前时间或跳转，因此不使用 |

## 3. 结构

### 3.1 后端：新增"视频来源"层

```
backend/unstuck/sources/
  base.py       统一接口与数据结构
  youtube.py    原 ingest/youtube.py 的逻辑迁移至此，行为不变
  bilibili.py   B站客户端：WBI 签名、视频信息、AI 字幕、播放地址、分P、登录状态
  __init__.py   根据链接或视频记录选择来源（for_url / for_video）
```

`base.py` 定义：
- `VideoRef(source, video_id, url, course_id=None, part=None)`：链接解析结果
- `Playback`：`kind` 为 `youtube`、`mp4` 或 `dash`；`mp4` 带 `url` 和 `expires_at`；`dash` 带音视频地址和 `segment_base`
- `Part(part, title, duration, video_id)`：分P列表项
- 来源接口：`parse(url) -> VideoRef`、`fetch_info(ref) -> dict`、`check_info(info, max_duration) -> VideoMeta`、`fetch_captions(ref, info) -> list[Cue] | None`、`download_video(ref, out_dir)`、`download_audio(ref, out_dir)`、`playback(ref, quality) -> Playback`、`parts(ref) -> list[Part]`

导入的 8 个步骤（`ingest/stages.py`）不再直接调用 YouTube 模块，而是调用 `sources.for_video(video)` 得到的来源。`IngestError` 错误码和流水线逻辑不变。

### 3.2 视频 ID 规则
- YouTube：11 位 ID（不变）
- B站：`{BV号}_p{集数}`，例如 `BV1Ex4y1G7nG_p1`。单集视频是 `_p1`。确定 ID 不需要联网查询。
- `b23.tv` 短链接需要先访问一次，跟随跳转得到真实链接后再解析。这是唯一需要在导入接口里联网的情况；失败时返回 `INVALID_URL`。

### 3.3 数据库
`videos` 表新增列：`source`（`youtube` 或 `bilibili`，默认 `youtube`）、`course_id`、`part`、`part_title`、`parts_total`。
启动时自动迁移：列不存在时执行 `ALTER TABLE ADD COLUMN`，旧数据的来源为 `youtube`。迁移可以重复执行。

### 3.4 B站客户端细节
- 所有请求复用同一个带 Chrome cookie 的会话（yt-dlp 的 `cookiesfrombrowser`），并带上 B站的 Referer 和常见浏览器的 User-Agent。配置项 `BILIBILI_COOKIES_BROWSER`，默认 `chrome`，留空表示不使用登录。
- WBI 签名：从 `nav` 接口获取 `img_key` 和 `sub_key`，按固定的混淆表生成 `mixin_key`，参数加上 `wts` 后按 key 排序，取 MD5 作为 `w_rid`。密钥在进程内缓存；签名请求失败时刷新一次密钥再重试。
- 风控（HTTP 412）和网络错误：退避重试（2 秒、4 秒），仍然失败就报 `IngestError("BILIBILI_BLOCKED", "B站暂时限制了访问，请稍后重试")`。
- 登录状态：`is_logged_in()` 调用 `nav` 接口，结果缓存 5 分钟。

### 3.5 各导入步骤的 B站行为
| 步骤 | B站行为 |
|---|---|
| metadata | 通过 `x/web-interface/view` 获取标题、UP 主、分P列表；根据 `part` 找到对应集的 cid、时长和标题。超过 2 小时拒绝（`TOO_LONG`）。视频不存在或不可访问时报 `VIDEO_UNAVAILABLE`。信息写入 `info.json`（包含 bvid、cid、part 等） |
| transcript | 调用 `x/player/wbi/v2` 获取字幕列表，优先使用 `ai-zh` 或人工中文字幕，再下载字幕 JSON（`body: [{from, to, content}]`）并转换为 Cue。**校验**：最后一条字幕的结束时间超过视频时长 5 秒以上，或字幕覆盖时长不到视频的 50%，判定为"对不上"，丢弃后改用 Whisper |
| download | 下载 720p 视频（优先 avc 编码），只用于抽取关键帧。失败时降级为 `transcript_only`，与 YouTube 相同 |
| 其余步骤 | 与 YouTube 相同 |

### 3.6 新增和修改的 API
- `POST /api/videos`：同时接受 YouTube 和 B站链接。付费课程、合集或主页链接返回 400，并说明原因。
- `GET /api/videos/{id}/playback?quality=1080|720`：YouTube 返回 `{"kind": "youtube"}`；B站返回 `mp4`（地址和过期时间），请求 1080 且可用时返回 `dash`。每次调用都重新签名获取新地址。
- `GET /api/videos/{id}/parts`：分P列表 `[{part, title, duration, video_id, imported}]`。非分P视频返回只有一项的列表。
- `GET /api/bilibili/status`：返回 `{logged_in: bool, cookies_browser: str}`。
- `GET /api/videos/{id}/stream/{video|audio}`（仅 1080p 使用）：后端带 B站 Referer 转发 Range 请求，地址过期时自动重新获取。
- 实时截帧（`tutor/frame.py`）：本地还没有视频文件时，B站视频改用 `ffmpeg -ss t -i <720p 地址>` 直接从在线流截取当前画面；失败时照常退回到最近的关键帧。

## 4. 前端

- **导入框**：提示文字改为"粘贴 YouTube 或 B站视频链接"。B站未登录时，下方显示黄色提醒："B站未登录：画质较低、字幕将用语音转写（较慢）。在 Chrome 登录 bilibili.com 后刷新即可恢复。"
- **视频库**：同一个分P课程的多集合并为一张课程卡片（显示"已导入 3/30 集"，点击进入最近导入的一集）；其他视频各一张卡片；卡片标明来源（YouTube / B站）。
- **播放器**：`Player` 按来源选择实现，对外接口仍是 `PlayerHandle`（`getCurrentTime` / `seekTo` / `pause`）：
  - `YouTubePlayer`：现有实现，不改
  - `BiliPlayer`：使用浏览器的 `<video>` 播放 `playback` 返回的 MP4。地址即将过期或播放出错时，重新请求 `playback`，然后跳回原来的时间继续播放
  - 1080p（可选）：`BiliPlayer` 优先请求 `quality=1080`；返回 `dash` 时，用后端转发地址生成 MPD，交给 dash.js 播放。任何失败都静默退回 720p
- **选集列表**：分P课程在大纲旁边显示选集列表。已导入的集可以直接点击进入；未导入的集有"导入"按钮，点击后导入并跳转过去；当前集高亮。
- **路由**：`#/v/<id>` 同时支持 11 位 YouTube ID 和 `BV…_pN` 格式。
- 聊天面板、导入进度、大纲、时间戳跳转等其他部分不做改动。

## 5. 出错处理

| 情况 | 处理 |
|---|---|
| 未登录或登录过期 | 提醒，照常导入；画质变低，字幕改用 Whisper |
| AI 字幕对不上视频 | 丢弃，改用 Whisper |
| 没有 AI 字幕 | 使用 Whisper |
| 风控（412） | 退避重试，仍失败则报 `BILIBILI_BLOCKED` |
| 视频不可用（已删除、会员专享、地区限制） | `VIDEO_UNAVAILABLE`，并给出明确提示 |
| 付费课程、合集或主页链接 | 400，提示"暂不支持，请粘贴单个视频链接" |
| 播放地址过期 | 前端自动重新获取，并恢复播放位置 |
| 1080p 失败 | 静默退回 720p |
| b23.tv 短链接解析失败 | `INVALID_URL` |

## 6. 测试

1. **单元测试**（不联网，使用录制的 B站接口响应作为测试数据）：
   - 链接解析：BV 号、`?p=`、b23 短链接（模拟跳转）、付费和合集链接被拒绝
   - WBI 签名：用固定的 key 和时间戳得到固定的 `w_rid`
   - 字幕 JSON 转换为 Cue；字幕"对不上"的判断（超长、覆盖不足、正常）
   - 播放地址解析（mp4 / dash）、分P列表、登录状态
   - 数据库迁移：旧库迁移后数据完整，迁移可以重复执行
   - 各导入步骤对 B站来源的分派（monkeypatch 来源实现）
   - API：`playback`、`parts`、`bilibili/status`、拒绝付费链接
   - 前端：路由 ID 格式、按来源选择播放器、地址过期后的刷新逻辑
2. **慢速集成测试**（标记为 `slow`，默认不运行）：使用真实登录，导入一个真实的 B站视频并提问；链接通过环境变量 `UNSTUCK_IT_BILI_URL` 提供。
3. **浏览器手动走查**：导入一个分P课程，测试立即播放、提问自动暂停、时间戳跳转、选集、导入下一集、地址过期后恢复（可以通过缩短过期时间来模拟）。

## 7. 难点记录（`docs/challenges.md`）

新建 `docs/challenges.md`，每条记录包括：**问题、原因（怎么查出来的）、解决办法、相关代码位置**。
- 先补充第一阶段已经遇到的难点：免费模型 429 限流和退避、流式回答占用并发名额、LaTeX 反斜杠在 JSON 里被破坏、Safari 中文输入法回车、回答中断时保存已生成内容、只看字幕的视频导致评测失真
- 本期调研中遇到的难点：yt-dlp 拿不到 AI 字幕、旧接口返回别的视频的字幕、WBI 签名、Chrome 登录 cookie 缺失、1080p 必须带 B站 Referer、B站搜索风控
- 实现过程中，**每个任务遇到不明显的问题，都要在这个文件里追加一条**

## 8. 已知限制

- 依赖 B站非公开的 Web 接口（WBI 签名、playurl）。B站改版时需要跟着调整。仅供个人使用和演示。
- 登录依赖 Chrome 的 cookie。macOS 第一次读取时会弹出钥匙串授权。
- 720p 首次加载约 6 秒（实测），比 YouTube 慢。
- B站视频的分析数据和下载的视频文件都存在本地，长课程会占用磁盘空间。
