# Windows 真机试用清单

在一台 Windows 10/11 电脑上按顺序做，哪一步不对就把窗口里的报错（截图或复制文字）发回来。

## 1. 安装依赖（只需一次）

打开「终端」或「PowerShell」，逐行运行，装完后**关掉这个窗口再开一个新的**：

```powershell
winget install -e --id astral-sh.uv
winget install -e --id OpenJS.NodeJS.LTS
winget install -e --id Gyan.FFmpeg
```

## 2. 下载代码

任选一种：

- **不用 Git**：浏览器打开 https://github.com/samchenhub/unstuck-ai-tutor/archive/refs/heads/main.zip ，下载后右键 →「全部解压缩」。
- **用 Git**：
  ```powershell
  cd $HOME
  git clone https://github.com/samchenhub/unstuck-ai-tutor.git
  ```

## 3. 第一次启动

在文件资源管理器里打开下载好的文件夹（`unstuck-ai-tutor` 或 `unstuck-ai-tutor-main`），双击 `Unstuck.bat`。

- [ ] 窗口提示「已创建 backend\.env」：用记事本打开 `backend\.env`，填入 `LLM_API_KEY`（智谱）和 `GROQ_API_KEY`，保存。
- [ ] 再双击 `Unstuck.bat`：第一次会安装依赖（几分钟），之后浏览器自动打开 http://localhost:5173 。
- [ ] 窗口里的中文提示没有乱码。

## 4. B站扫码登录

- [ ] 首页黄色提示条里点「扫码登录」，出现二维码。
- [ ] 用手机B站 App 扫码：文字变成「已扫码，请在手机上确认登录」。
- [ ] 手机上确认后，提示条消失，显示「已扫码登录B站 · 退出登录」。

## 5. 导入和提问

- [ ] 导入一个有字幕的B站视频（例如课程类视频）：立刻能播放，几秒后可以提问。
- [ ] 导入一个没有字幕的视频：用 Groq 转写，十几秒内可以提问，电脑不会明显发热。
- [ ] 导入一个 YouTube 视频（如 https://www.youtube.com/watch?v=fNk_zzaMoSs ）。
- [ ] 暂停后提问：视频自动暂停，回答里有公式和可点击的时间点，点时间点能跳转。

## 6. 关闭

- [ ] 关掉 `Unstuck.bat` 打开的黑色窗口后，浏览器里的页面刷新会显示连不上（说明后台已经停了）。
- [ ] 打开「任务管理器」，确认没有残留的 `python.exe` / `node.exe`（属于 Unstuck 的）。
- [ ] 再次双击 `Unstuck.bat`，之前导入的视频和登录状态都还在。
