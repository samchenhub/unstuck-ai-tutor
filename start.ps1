# Start Unstuck on Windows: backend (:8000) + frontend (:5173), then open the browser.
# Double-click Unstuck.bat, or run: powershell -ExecutionPolicy Bypass -File start.ps1
# Press Ctrl+C or close the window to stop both.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Set-Location $PSScriptRoot
$env:PYTHONUTF8 = '1'  # Chinese text in logs and files, whatever the Windows code page

function Need($command, $wingetId) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        Write-Host "缺少 $command，请先安装（装完后重新打开这个窗口）：winget install -e --id $wingetId" -ForegroundColor Yellow
        exit 1
    }
}
Need uv 'astral-sh.uv'
Need node 'OpenJS.NodeJS.LTS'
Need ffmpeg 'Gyan.FFmpeg'

if (-not (Test-Path 'backend\.env')) {
    Copy-Item 'backend\.env.example' 'backend\.env'
    Write-Host '已创建 backend\.env，请用记事本打开它，填入 LLM_API_KEY 后重新运行。' -ForegroundColor Yellow
    exit 1
}
if (Select-String -Path 'backend\.env' -Pattern 'your-zhipu-api-key' -Quiet) {
    Write-Host '请先在 backend\.env 里填入 LLM_API_KEY。' -ForegroundColor Yellow
    exit 1
}

foreach ($port in 8000, 5173) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
        Write-Host "端口 $port 已被占用，Unstuck 可能已经在运行：http://localhost:5173" -ForegroundColor Yellow
        exit 1
    }
}

Write-Host '检查依赖…'
Push-Location backend
uv sync --quiet
if ($LASTEXITCODE -ne 0) { Pop-Location; Write-Host 'uv sync 失败' -ForegroundColor Red; exit 1 }
Pop-Location
if (-not (Test-Path 'frontend\node_modules')) {
    Push-Location frontend
    npm install --silent
    if ($LASTEXITCODE -ne 0) { Pop-Location; Write-Host 'npm install 失败' -ForegroundColor Red; exit 1 }
    Pop-Location
}

Write-Host '启动后端和前端…'
# -NoNewWindow keeps both servers on this console, so closing the window stops them too.
$backend = Start-Process -FilePath (Join-Path $PSScriptRoot 'backend\.venv\Scripts\uvicorn.exe') `
    -ArgumentList 'unstuck.api.app:create_app', '--factory', '--host', '127.0.0.1', '--port', '8000', '--log-level', 'warning' `
    -WorkingDirectory (Join-Path $PSScriptRoot 'backend') -NoNewWindow -PassThru
$frontend = Start-Process -FilePath 'node' `
    -ArgumentList 'node_modules\vite\bin\vite.js', '--host', '127.0.0.1', '--port', '5173', '--strictPort', '--logLevel', 'warn' `
    -WorkingDirectory (Join-Path $PSScriptRoot 'frontend') -NoNewWindow -PassThru

function Responds($url) {
    try {
        Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2 | Out-Null
        return $true
    } catch {
        return $false
    }
}

try {
    $ready = $false
    for ($i = 0; $i -lt 90; $i++) {
        if ($backend.HasExited -or $frontend.HasExited) { break }
        # 127.0.0.1, not localhost: Windows tries IPv6 (::1) first, where neither server listens.
        if ((Responds 'http://127.0.0.1:8000/api/health') -and (Responds 'http://127.0.0.1:5173')) { $ready = $true; break }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) {
        Write-Host 'Unstuck 没能启动，请查看上面的报错。' -ForegroundColor Red
        exit 1
    }
    if (-not $env:UNSTUCK_NO_OPEN) { Start-Process 'http://localhost:5173' }
    Write-Host 'Unstuck 已启动：http://localhost:5173 （按 Ctrl+C 或关闭这个窗口即可停止）' -ForegroundColor Green
    if ($env:UNSTUCK_SMOKE_TEST) { exit 0 }  # CI: started and answering is enough
    while (-not $backend.HasExited -and -not $frontend.HasExited) { Start-Sleep -Seconds 1 }
} finally {
    foreach ($p in $backend, $frontend) {
        if ($p -and -not $p.HasExited) { taskkill /PID $p.Id /T /F | Out-Null }
    }
    Write-Host 'Unstuck 已停止。'
}
