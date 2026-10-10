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
