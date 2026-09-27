<#
.SYNOPSIS
    aiLr 开发服务启动脚本（FastAPI + Vite）。
.DESCRIPTION
    在独立 PowerShell 窗口中启动后端与前端，等待健康检查通过后打开浏览器，并在当前窗口输出状态。
    若端口已被本机 aiLr 服务占用，会直接复用而不是重复启动。
.PARAMETER BackendOnly
    只启动 FastAPI 后端。
.PARAMETER FrontendOnly
    只启动 Vite 前端。
.PARAMETER ApiPort
    后端端口，默认 8000。前端 src/App.tsx 中的 API 常量固定为 8000，改动前请同步修改前端。
.PARAMETER WebPort
    前端端口，默认 5173。
.PARAMETER NoReload
    关闭 uvicorn 的 --reload 自动重载。
.PARAMETER NoBrowser
    启动完成后不自动打开浏览器。
.PARAMETER Foreground
    后端在当前窗口前台运行（日志直接可见，Ctrl+C 结束），前端仍在独立窗口。
.PARAMETER DryRun
    只做检查并打印将要执行的命令，不启动任何进程，便于排查问题。
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1 -Foreground -NoBrowser
#>
#Requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$BackendOnly,
    [switch]$FrontendOnly,
    [int]$ApiPort = 8000,
    [int]$WebPort = 5173,
    [switch]$NoReload,
    [switch]$NoBrowser,
    [switch]$Foreground,
    [switch]$DryRun
)

. (Join-Path $PSScriptRoot 'common.ps1')

$Root = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $Root '.venv\Scripts\python.exe'
$EnvFile = Join-Path $Root '.env'
$EnvExample = Join-Path $Root '.env.example'
$FrontendDir = Join-Path $Root 'frontend'
$FrontendNodeModules = Join-Path $FrontendDir 'node_modules'

if ($BackendOnly -and $FrontendOnly) {
    Fail '不能同时使用 -BackendOnly 与 -FrontendOnly。'
}

$StartBackend = -not $FrontendOnly
$StartFrontend = -not $BackendOnly

Write-Host 'aiLr 启动' -ForegroundColor White
Write-Info "项目根目录: ${Root}"

Write-Step '检查运行环境'
if ($StartBackend) {
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        Fail '未找到 .venv。请先运行: powershell -ExecutionPolicy Bypass -File .\scripts\init.ps1'
    }
    Write-Ok "后端解释器: ${VenvPython}"
}
if ($StartFrontend) {
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        Fail '未找到 npm。请安装 Node.js 20+ 后重试。'
    }
    if (-not (Test-Path -LiteralPath $FrontendNodeModules)) {
        Fail '未找到 frontend\node_modules。请先运行 init.ps1，或执行 npm --prefix frontend install。'
    }
    Write-Ok '前端依赖已就绪。'
}
if (-not (Test-Path -LiteralPath $EnvFile)) {
    if (Test-Path -LiteralPath $EnvExample) {
        Copy-Item -LiteralPath $EnvExample -Destination $EnvFile
        Write-Warn '.env 不存在，已从 .env.example 复制默认配置。'
    } else {
        Write-Warn '.env 与 .env.example 都不存在，后端将使用内置默认值。'
    }
}
if ($ApiPort -ne 8000) {
    Write-Warn "前端 src/App.tsx 中的 API 地址固定为 http://127.0.0.1:8000，使用 -ApiPort ${ApiPort} 时需同步修改前端常量。"
}

Write-Step '检查端口占用'
if ($StartBackend -and (Test-TcpPort -Port $ApiPort)) {
    $existingHealth = Get-BackendHealth -ApiPort $ApiPort
    if ($existingHealth -and $existingHealth.status -eq 'ok') {
        Write-Warn "端口 ${ApiPort} 上已有 aiLr 后端在运行，直接复用，不重复启动。"
        $StartBackend = $false
    } else {
        Fail "端口 ${ApiPort} 已被其它程序占用，请先关闭它，或用 -ApiPort 指定其它端口。"
    }
}
if ($StartBackend) { Write-Ok "端口 ${ApiPort} 可用。" }
if ($StartFrontend -and (Test-TcpPort -Port $WebPort)) {
    Write-Warn "端口 ${WebPort} 已被占用，跳过启动新的 Vite 服务（若就是本项目的 dev server，可忽略）。"
    $StartFrontend = $false
}
if ($StartFrontend) { Write-Ok "端口 ${WebPort} 可用。" }

$ShellExe = Get-ShellExecutable
$BackendCommand = "`$Host.UI.RawUI.WindowTitle = 'aiLr backend (${ApiPort})'; Set-Location -LiteralPath '${Root}'; & '${VenvPython}' -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port ${ApiPort}"
if (-not $NoReload) { $BackendCommand += ' --reload' }
$FrontendCommand = "`$Host.UI.RawUI.WindowTitle = 'aiLr frontend (${WebPort})'; Set-Location -LiteralPath '${FrontendDir}'; npm run dev -- --host 127.0.0.1 --port ${WebPort}"

if ($DryRun) {
    Write-Step 'DryRun：以下是将会执行的命令（未启动任何进程）'
    if ($StartBackend) { Write-Host "    [${ShellExe} -NoExit -Command] ${BackendCommand}" }
    if ($StartFrontend) { Write-Host "    [${ShellExe} -NoExit -Command] ${FrontendCommand}" }
    Write-Info "健康检查: http://127.0.0.1:${ApiPort}/api/health"
    Write-Info "网页地址: http://127.0.0.1:${WebPort}"
    exit 0
}

function Start-AiLrWindow {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory
    )
    Start-Process -FilePath $ShellExe -ArgumentList @('-NoExit', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', $Command) -WorkingDirectory $WorkingDirectory | Out-Null
}

if ($Foreground -and $StartBackend) {
    if ($StartFrontend) {
        Write-Step '启动前端 (Vite)'
        Start-AiLrWindow -Command $FrontendCommand -WorkingDirectory $FrontendDir
        if (Wait-ForTcpPort -Port $WebPort -TimeoutSeconds 90) {
            Write-Ok "前端已就绪: http://127.0.0.1:${WebPort}"
        } else {
            Write-Warn "等待前端端口 ${WebPort} 超时，请查看 aiLr frontend 窗口的日志。"
        }
    }
    if (-not $NoBrowser) { Start-Process "http://127.0.0.1:${WebPort}" | Out-Null }

    Write-Step '在当前窗口前台运行 FastAPI（按 Ctrl+C 结束）'
    Write-Info "健康检查: http://127.0.0.1:${ApiPort}/api/health"
    $uvicornArgs = @('-m', 'uvicorn', 'app.main:app', '--app-dir', 'backend', '--host', '127.0.0.1', '--port', "$ApiPort")
    if (-not $NoReload) { $uvicornArgs += '--reload' }
    Push-Location $Root
    try {
        & $VenvPython @uvicornArgs
        exit $LASTEXITCODE
    } finally {
        Pop-Location
    }
}

if ($StartBackend) {
    Write-Step '启动后端 (FastAPI)'
    Start-AiLrWindow -Command $BackendCommand -WorkingDirectory $Root
    $health = Wait-ForBackend -ApiPort $ApiPort -TimeoutSeconds 90
    if ($health) {
        Write-Ok "后端已就绪: http://127.0.0.1:${ApiPort}"
    } else {
        Write-Warn '等待后端健康检查超时，请查看 aiLr backend 窗口中的错误信息。'
    }
}

if ($StartFrontend) {
    Write-Step '启动前端 (Vite)'
    Start-AiLrWindow -Command $FrontendCommand -WorkingDirectory $FrontendDir
    if (Wait-ForTcpPort -Port $WebPort -TimeoutSeconds 90) {
        Write-Ok "前端已就绪: http://127.0.0.1:${WebPort}"
    } else {
        Write-Warn "等待前端端口 ${WebPort} 超时，请查看 aiLr frontend 窗口的日志。"
    }
}

Write-Step '运行状态'
$health = Get-BackendHealth -ApiPort $ApiPort
if ($health) {
    Write-Host "    后端服务 : 正常 http://127.0.0.1:${ApiPort}"
    Write-Host "    模型模式 : $($health.provider) / $($health.model)"
    if ($health.model_active) {
        Write-Ok "模型已启动：$($health.model_message)"
    } else {
        Write-Warn "模型尚未启动（$($health.model_message)）。请打开网页，在右上角「模型设置」中点击「保存并启动」。"
    }
} else {
    Write-Warn '后端健康检查未通过，网页可能无法获取调色建议。'
}

if (-not $BackendOnly) {
    Write-Host "    网页地址 : http://127.0.0.1:${WebPort}"
}

$provider = (Get-EnvFileValue -Path $EnvFile -Key 'AILR_MODEL_PROVIDER' -Default 'ollama').ToLowerInvariant()
if ($provider -eq 'ollama') {
    $ollamaBaseUrl = (Get-EnvFileValue -Path $EnvFile -Key 'AILR_OLLAMA_BASE_URL' -Default 'http://127.0.0.1:11434').TrimEnd('/')
    $ollamaPort = 11434
    try { $ollamaPort = ([uri]$ollamaBaseUrl).Port } catch { }
    if (Test-TcpPort -Port $ollamaPort) {
        Write-Host "    Ollama   : 在线 ${ollamaBaseUrl}"
    } else {
        Write-Warn "未检测到 Ollama（${ollamaBaseUrl}）。使用本地模型前请先运行: ollama serve"
    }
}

if (-not $NoBrowser -and -not $BackendOnly) {
    Start-Process "http://127.0.0.1:${WebPort}" | Out-Null
}

Write-Step '提示'
Write-Host '    - 两个服务运行在独立窗口，关闭窗口或在窗口内按 Ctrl+C 即可停止。'
Write-Host '    - 继续使用 Lightroom：在 LrC「增效工具管理器」中添加 aiLr.lrplugin 文件夹，并在「图库」菜单点击 aiLr: Start/Stop Web Bridge。'
Write-Host '    - 完整说明见 README.md。'

