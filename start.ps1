# Start Unstuck on Windows and open it in the browser (http://localhost:8000).
# Double-click Unstuck.bat. Everything it needs is installed automatically on the first run.
# Press Ctrl+C or close the window to stop.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Set-Location -LiteralPath $PSScriptRoot  # -LiteralPath: [ ] in a folder name are not wildcards
$env:PYTHONUTF8 = '1'  # Chinese text in logs and files, whatever the Windows code page
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host '第一次运行：正在安装 uv（用来安装 Python 和其他组件）…'
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-Host 'uv 安装失败，请检查网络后重新运行。' -ForegroundColor Red
        exit 1
    }
}

if (-not (Test-Path 'backend\.env')) {
    Copy-Item 'backend\.env.example' 'backend\.env'
    Write-Host '已创建 backend\.env，请用记事本打开它，填入 LLM_API_KEY 后重新运行。' -ForegroundColor Yellow
    exit 1
}
if (Select-String -Path 'backend\.env' -Pattern 'your-zhipu-api-key' -Quiet) {
    Write-Host '请先在 backend\.env 里填入 LLM_API_KEY。' -ForegroundColor Yellow
    exit 1
}

# The web page: shipped pre-built in packages; a source checkout (with Node) builds it when missing or stale.
$index = 'frontend\dist\index.html'
$stale = -not (Test-Path $index)
if (-not $stale) {
    $builtAt = (Get-Item $index).LastWriteTime
    $stale = [bool](Get-ChildItem -Path 'frontend\src', 'frontend\index.html' -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -gt $builtAt } | Select-Object -First 1)
}
if ($stale) {
    if (Get-Command npm -ErrorAction SilentlyContinue) {
        Write-Host '正在生成网页…'
        Push-Location frontend
        npm install --silent
        npm run build --silent
        $built = $LASTEXITCODE
        Pop-Location
        if ($built -ne 0) { Write-Host '网页生成失败' -ForegroundColor Red; exit 1 }
    } elseif (-not (Test-Path $index)) {
        Write-Host '缺少网页文件（frontend\dist）。请使用完整安装包，或者安装 Node.js 后重新运行。' -ForegroundColor Yellow
        exit 1
    }
}

if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) {
    Write-Host '端口 8000 已被占用，Unstuck 可能已经在运行：http://localhost:8000' -ForegroundColor Yellow
    exit 1
}

Write-Host '检查组件（第一次运行要下载 Python 和 AI 组件，约 2–3 GB，可能需要 10–20 分钟，请耐心等待）…'
Push-Location backend
uv sync --quiet
$synced = $LASTEXITCODE
Pop-Location
if ($synced -ne 0) { Write-Host '组件安装失败，请检查网络后重新运行。' -ForegroundColor Red; exit 1 }

Write-Host '启动 Unstuck…'
# -NoNewWindow keeps the server on this console, so closing the window stops it too.
$server = Start-Process -FilePath (Join-Path $PSScriptRoot 'backend\.venv\Scripts\uvicorn.exe') `
    -ArgumentList 'unstuck.api.app:create_app', '--factory', '--host', '127.0.0.1', '--port', '8000', '--log-level', 'warning' `
    -WorkingDirectory (Join-Path $PSScriptRoot 'backend') -NoNewWindow -PassThru

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
    for ($i = 0; $i -lt 120; $i++) {
        if ($server.HasExited) { break }
        # 127.0.0.1, not localhost: Windows tries IPv6 (::1) first, where the server does not listen.
        if ((Responds 'http://127.0.0.1:8000/api/health') -and (Responds 'http://127.0.0.1:8000/')) { $ready = $true; break }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) {
        Write-Host 'Unstuck 没能启动，请查看上面的报错。' -ForegroundColor Red
        exit 1
    }
    if (-not $env:UNSTUCK_NO_OPEN) { Start-Process 'http://localhost:8000' }
    Write-Host 'Unstuck 已启动：http://localhost:8000 （按 Ctrl+C 或关闭这个窗口即可停止）' -ForegroundColor Green
    if ($env:UNSTUCK_SMOKE_TEST) { exit 0 }  # CI: started and answering is enough
    while (-not $server.HasExited) { Start-Sleep -Seconds 1 }
} finally {
    if ($server -and -not $server.HasExited) { taskkill /PID $server.Id /T /F | Out-Null }
    Write-Host 'Unstuck 已停止。'
}
