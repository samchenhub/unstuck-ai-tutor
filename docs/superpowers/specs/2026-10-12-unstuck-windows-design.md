# Unstuck — Windows support

Status: approved by the user 2026-10-12 (QR-code Bilibili login: yes; CI on Windows/macOS/Linux: yes; real Windows test by the user: the next day).

## Goal

A Windows user can double-click a launcher, import YouTube and Bilibili videos (logged in), and ask questions, with the same behaviour as on macOS. Every push is tested on Windows, macOS and Linux.

## Findings (macOS-only today)

- Launcher: `start.sh` / `Unstuck.command` are bash (brew hints, `lsof`, `open`, `trap`).
- `os.pwrite` (Bilibili ranged downloader) does not exist on Windows.
- Windows refuses to rename or delete a file another process has open: the download's `.part → final` rename while ffmpeg reads the partial audio (streaming transcription), and `audio.m4a` cleanup.
- Text defaults to the ANSI code page on Windows; Chinese logs and files need UTF-8.
- Bilibili login reads Chrome's cookies through yt-dlp. On Windows, Chrome's app-bound cookie encryption makes them unreadable for other programs; Firefox still works, Chrome/Edge do not.

## Design

1. **CI** (`.github/workflows/ci.yml`): matrix windows-latest / macos-latest / ubuntu-latest. Backend: uv + Python 3.12, ffmpeg installed per OS, `uv run pytest -q`. Frontend: Node 20, `npm ci`, `npm test`, `npm run build`. Badge in the README.
2. **Portable file I/O**: positioned writes through a per-download file object guarded by a lock (seek + write) instead of `os.pwrite`; rename/delete retried briefly on `PermissionError` (Windows sharing violations) and, for clean-up, tolerated.
3. **Windows launcher**: `Unstuck.bat` (double-click) runs `start.ps1`, which checks uv / Node / ffmpeg (printing the `winget install` command for anything missing), creates `backend/.env` from the example on first run, sets `PYTHONUTF8=1`, starts backend (:8000) and frontend (:5173), opens the browser, and stops both when the window closes or Ctrl+C is pressed.
4. **QR-code Bilibili login (all platforms)**: Bilibili's web passport QR flow (`passport.bilibili.com/x/passport-login/web/qrcode/generate` → poll `…/qrcode/poll`), the cookies it sets are saved to `data/bilibili_cookies.txt` (Netscape format, never logged) and used by the client before any browser. The library shows「扫码登录B站」when not logged in: a QR code (rendered in the browser from the login URL), status (等待扫码 / 已扫码，请在手机上确认 / 已登录 / 二维码已过期), and a 退出登录 action that deletes the saved cookies. Reading the browser's cookies stays as the fallback (macOS default `chrome`).
5. **yt-dlp** minimum raised to 2026.6.9 (cookie-leak fix CVE-2026-50019).
6. **README**: Windows install (winget commands), double-click launcher, QR login.
