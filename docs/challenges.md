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

### 17. 读取不到浏览器 cookie 时整条 B站链路报错
- **问题**：Chrome 正在运行或没有读取权限时，所有 B站请求都失败，连"未登录"的提示都出不来。
- **原因**：yt-dlp 的 `cookiesfrombrowser` 是惰性的——创建 `YoutubeDL` 时不读 cookie，第一次 `urlopen` 才读，失败时抛 `CookieLoadError`（`YoutubeDLError` 的子类），所以只在构造处捕获没用。另外 cookie 在进程内只读一次，用户在 Chrome 登录后刷新页面也不会生效。
- **解决**：在 `_ydl_fetch_json` 里捕获 `CookieLoadError`，记一次警告，置 `cookies_unavailable`，丢弃当前 `YoutubeDL`，不带 cookie 重建后重试；`ydl_options()` 同步去掉 `cookiesfrombrowser`，下载也随之降级为游客。`is_logged_in(force=True)`（状态接口使用）会丢弃 cookie jar 并重新读取，登录后刷新即可生效。
- **代码**：`backend/unstuck/sources/bilibili/client.py`（`_ydl_fetch_json`、`is_logged_in`）

### 18. ffmpeg 拉 B站视频流截帧返回 403
- **问题**：本地没有下载视频时，直接用 `ffmpeg -ss … -i <B站 mp4 地址>` 截实时画面，报 `Server returned 403 Forbidden`；而浏览器 `<video>` 播放同一地址正常。
- **原因**：B站 CDN 拒绝 ffmpeg 默认的 User-Agent（`Lavf/…`），带浏览器 UA 的请求才放行。
- **解决**：`extract_frame` 新增可选 `headers` 参数，在 `-i` 之前加上 `-user_agent` 和 `-headers`（Referer 等）；`live_frame_description` 对在线流传入 B站的浏览器 UA + Referer，本地文件仍不带任何头。
- **代码**：`backend/unstuck/media.py`、`backend/unstuck/tutor/frame.py`

### 19. B站视频下载中途断开，换了下载方式也一直失败
- **问题**：B站视频/音频下载经常中途断掉（只下了 0.95 MB / 16.5 MB），或者一直报 `Remote end closed connection`；yt-dlp 重试 10 次要 3 分钟，一次导入甚至拖了 43 分钟。
- **原因**：两层问题叠加。一是 B站 CDN 会切断长时间的单连接下载（yt-dlp 默认下载和 `ffmpeg -c copy` 都被截断，浏览器播放不受影响，因为它按片段请求）。二是同一个播放地址背后个别 CDN 节点会间歇性出故障：固定在同一字节处 `IncompleteRead`（总是 953,844 字节），或者 10 次重试全部被拒绝连接；而重新获取的播放地址马上就能正常返回所有分段。yt-dlp 一直重试同一个地址，所以逃不出坏节点。
- **解决**：不再用 yt-dlp 下载，自己写了按 1 MB 分段的 Range 下载器：收到一半的数据也保留，下一次从当前偏移继续；连续失败 3 次（指数退避 2、4 秒）就重新获取一个新的播放地址，403（地址过期）立即换地址，最多换 4 次，写入 `.part` 临时文件，成功后才改名。只下载一个 720p 的 MP4（H.264 + AAC），转写时用 ffmpeg 解出音频，截帧时直接用同一个文件，下载只做一次。另外 CDN 速度不稳定，个别分片很慢（偶尔一个 1 MB 要 5 秒），所以分片用 4 路并行下载（共用同一个播放地址，同时失败只会换一次地址），8 MB 从 2.4 秒降到 0.7 秒。
- **代码**：`backend/unstuck/sources/bilibili/download.py`、`backend/unstuck/sources/bilibili/source.py`

### 20. 没有字幕的 B站视频，本地语音转写直接报错，且结果是繁体字
- **问题**：没有字幕的 B站视频，语音转写阶段直接报错 `open() got an unexpected keyword argument 'metadata_errors'`；绕过之后，转写结果是繁体字，还有错字。
- **原因**：第一阶段测试过的视频都有字幕，本地转写从没真正跑过；faster-whisper 1.2.1 解码音频时调用的 PyAV 参数 `metadata_errors` 在 PyAV 19 中已被移除。Whisper small 对普通话常输出繁体。
- **解决**：自己用 ffmpeg 把音频解码成 16 kHz 单声道波形（681 秒音频约 0.4 秒）再交给 Whisper，彻底绕开 PyAV；先用 `detect_language` 检测语言，检测到中文时加上简体中文提示词（`initial_prompt`）并固定 `language="zh"`。
- **代码**：`backend/unstuck/ingest/transcribe.py`

### 21. 导入新的一集时，播放器偶尔加载失败，差点用完重试次数
- **问题**：在选集列表里点「导入」第 4 集，页面立即跳转，但视频好几秒都加载不出来；后台日志显示播放地址被请求了 5 次（正常 1–2 次），说明 `<video>` 连续报错 3 次，第 3 次刷新地址才成功，再失败一次就会显示「B站视频加载失败」。
- **原因**：B站给的播放地址大约一半落在 Akamai 镜像（`upos-hz-mirrorakam.akamaized.net`）。导入时后台正用 4 路并行下载同一个视频，这时浏览器再请求，CDN 会把这种突发请求直接回 403；过一会儿同样的地址就能正常返回 206（和 Referer 无关，换不同 Referer 结果一样）。播放器出错后立刻换地址重试，正好又撞上限流，几次重试在一秒内就用光了。
- **解决**：播放器刷新地址前先等待，按 1、2、4 秒指数退避（最多 8 秒），而不是立即重试；切换视频时清掉未执行的重试计时器，避免旧视频的地址覆盖新视频。
- **代码**：`frontend/src/components/BiliPlayer.tsx`、`frontend/src/lib/retry.ts`

### 22. 没有字幕的长视频，要等 20 分钟才能提问
- **问题**：一节 1 小时 45 分的吴恩达英文课（中英字幕是压在画面里的，B站没有字幕轨），导入后要先下载完整个 216 MB 的视频（那天 CDN 只有约 0.7 MB/s，用了 5 分钟），再把整段音频用 Whisper 转写完（本机 CPU 约 10 倍实时速度，约 10 分钟），才能开始提问。
- **原因**：流程是"全部下载 → 全部转写 → 一次性写入字幕并开放提问"，等待时间和视频长度成正比；而转写其实只需要音频，完整视频只有截关键帧才用得到。
- **解决**：
  1. B站转写只下载 DASH 里码率最低的音频轨（这节课 52 MB，约为 MP4 的四分之一；DASH 需要带 B站 Referer，后端本来就带）；完整 MP4 推迟到"下载视频"阶段，那时已经可以提问了。
  2. 转写改成每次用 ffmpeg 解码 10 分钟音频（内存也从整段 400 MB 降到每段约 38 MB），Whisper 的结果本来就是边算边出的生成器，转写满 1 分钟就先写入字幕、重建索引并开放提问，之后每 5 分钟音频更新一次；进度存在 `videos.transcribed_until`，程序重启后从断点继续转写。
  3. 提问的位置还没转写到时，告诉 AI"字幕目前只到 [mm:ss]"，让它依靠当前画面回答并说明情况。
  - 实测：同一节课从导入到能提问，由约 20 分钟降到 55 秒，转写约 7 倍实时速度，一直跑在播放进度前面。
- **备注**：用 Apple GPU 的 mlx-whisper 可以再快几倍，但只能在 Mac 上用，很多用户会用 Windows，所以默认仍用跨平台的 faster-whisper（CPU）。
- **代码**：`backend/unstuck/ingest/transcribe.py`、`backend/unstuck/ingest/stages.py`、`backend/unstuck/sources/bilibili/source.py`、`backend/unstuck/tutor/context.py`

### 23. 下载速度取决于B站分配的节点，多开连接反而更慢
- **问题**：只下载音频后，52 MB 有时仍要 2 分多钟（约 0.36 MB/s）。
- **原因**：实测同一时刻：同一节点 4 路并行 1.4 MB/s，8 路 0.84 MB/s，16 路 0.57 MB/s，节点会限流，多开连接没用；但B站给每个视频流都附带了备用节点（`backupUrl` / `backup_url`），同一时刻备用节点有 12 MB/s，节点之间差了近 10 倍，而且哪个快会随时间变化。
- **解决**：下载器拿到主地址和备用地址后，先同时向每个节点请求开头 512 KB，谁最先完整返回就用谁下载；之后每次因为失败而换地址时也重新比一次。音频和 MP4 下载都适用。实测导入到能提问从 162 秒降到 55 秒。
- **代码**：`backend/unstuck/sources/bilibili/download.py`（`_fastest`）、`backend/unstuck/sources/bilibili/parse.py`
