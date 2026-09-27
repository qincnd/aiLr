<#
.SYNOPSIS
    aiLr 项目初始化脚本。
.DESCRIPTION
    以幂等方式准备本地开发环境：
      1. 检查 Python 3.11+ 与 Node.js 20+ / npm；
      2. 创建或复用 .venv 虚拟环境；
      3. 升级 pip 并安装 requirements.txt，然后做一次后端导入自检；
      4. 从 .env.example 生成 .env（已存在则保留，-Force 会先备份再覆盖）；
      5. 安装 frontend 的 npm 依赖；
      6. 检查 Ollama 与默认视觉模型，必要时执行 ollama pull。
.PARAMETER Force
    重建 .venv、备份并重新生成 .env、强制重装前后端依赖。
.PARAMETER SkipFrontend
    跳过前端 npm 依赖安装。
.PARAMETER SkipModelPull
    跳过 Ollama 模型检查与下载。
.PARAMETER RunTests
    初始化结束后运行 backend 单元测试。
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\init.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\init.ps1 -Force -RunTests
#>
#Requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$Force,
    [switch]$SkipFrontend,
    [switch]$SkipModelPull,
    [switch]$RunTests
)

. (Join-Path $PSScriptRoot 'common.ps1')

$Root = Split-Path -Parent $PSScriptRoot
$VenvDir = Join-Path $Root '.venv'
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
$EnvFile = Join-Path $Root '.env'
$EnvExample = Join-Path $Root '.env.example'
$Requirements = Join-Path $Root 'requirements.txt'
$FrontendDir = Join-Path $Root 'frontend'
$FrontendNodeModules = Join-Path $FrontendDir 'node_modules'
$ModelConfigFile = Join-Path $Root 'backend\data\model_config.json'
$Script:WarningCount = 0

function Add-Warning {
    param([Parameter(Mandatory = $true)][string]$Message)
    $Script:WarningCount++
    Write-Warn $Message
}

Write-Host 'aiLr 初始化' -ForegroundColor White
Write-Info "项目根目录: ${Root}"
if (-not (Test-Path -LiteralPath $Requirements)) {
    Fail "找不到 ${Requirements}，请在 aiLr 项目根目录下运行本脚本。"
}

Write-Step '检查 Python 解释器'
$PythonCandidates = @()
if (Get-Command py -ErrorAction SilentlyContinue) { $PythonCandidates += , @('py', '-3') }
if (Get-Command python -ErrorAction SilentlyContinue) { $PythonCandidates += , @('python') }
if ($PythonCandidates.Count -eq 0) {
    Fail '未找到 Python。请安装 Python 3.11+ 并确保 py 或 python 命令在 PATH 中。'
}

$PythonExe = $null
$PythonBaseArgs = @()
$PythonVersion = $null
foreach ($candidate in $PythonCandidates) {
    $candidateExe = $candidate[0]
    $candidateArgs = @($candidate | Select-Object -Skip 1)
    $versionText = (& $candidateExe @candidateArgs --version 2>&1 | Out-String).Trim()
    Write-Info "$($candidate -join ' ') -> ${versionText}"
    if ($versionText -match 'Python\s+(\d+)\.(\d+)') {
        $parsedVersion = [version]"$($Matches[1]).$($Matches[2])"
        if ($parsedVersion -ge [version]'3.11') {
            $PythonExe = $candidateExe
            $PythonBaseArgs = $candidateArgs
            $PythonVersion = $parsedVersion
            break
        }
    }
}
if (-not $PythonExe) {
    Fail '未找到 Python 3.11 或更高版本。可用 py -3.11 / py -3.12 指定版本，安装后重新运行本脚本。'
}
Write-Ok "使用 Python ${PythonVersion}（${PythonExe}）"

Write-Step '准备虚拟环境 .venv'
if ($Force -and (Test-Path -LiteralPath $VenvDir)) {
    Write-Warn '按 -Force 删除现有 .venv 并重建。'
    Remove-Item -LiteralPath $VenvDir -Recurse -Force
}
if (Test-Path -LiteralPath $VenvPython) {
    Write-Ok '.venv 已存在，直接复用（-Force 可重建）。'
} else {
    Push-Location $Root
    try {
        Write-Info "创建虚拟环境: $PythonExe $($PythonBaseArgs -join ' ') -m venv .venv"
        & $PythonExe @PythonBaseArgs -m venv .venv
        if ($LASTEXITCODE -ne 0) { Fail '创建虚拟环境失败。' }
    } finally {
        Pop-Location
    }
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        Fail '虚拟环境创建后仍找不到 python.exe，请检查磁盘权限或杀毒软件拦截。'
    }
    Write-Ok '.venv 已创建。'
}
$VenvVersionText = (& $VenvPython --version 2>&1 | Out-String).Trim()
Write-Info "虚拟环境解释器: ${VenvVersionText}"

Write-Step '安装后端依赖'
& $VenvPython -m pip install --upgrade pip --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { Add-Warning '升级 pip 失败，继续使用现有版本。' }
Write-Info '安装 requirements.txt（已满足的包会自动跳过）'
& $VenvPython -m pip install -r $Requirements --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { Fail '安装 requirements.txt 失败，请检查网络或代理后重试。' }

Push-Location $Root
try {
    $importOutput = (& $VenvPython -c "import sys; sys.path.insert(0, 'backend'); import app.main" 2>&1 | ForEach-Object { "$_" } | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        Add-Warning "后端模块导入自检失败：${importOutput}"
    } else {
        Write-Ok '后端依赖安装完成，app.main 导入自检通过。'
    }
} finally {
    Pop-Location
}

Write-Step '准备环境变量文件 .env'
if (-not (Test-Path -LiteralPath $EnvExample)) {
    Add-Warning '找不到 .env.example，跳过 .env 生成。'
} elseif (Test-Path -LiteralPath $EnvFile) {
    if ($Force) {
        Copy-Item -LiteralPath $EnvFile -Destination "$EnvFile.bak" -Force
        Copy-Item -LiteralPath $EnvExample -Destination $EnvFile -Force
        Write-Warn '按 -Force 覆盖 .env，原文件已备份为 .env.bak。'
    } else {
        Write-Ok '.env 已存在，保持原样（-Force 可备份并重置为示例值）。'
    }
} else {
    Copy-Item -LiteralPath $EnvExample -Destination $EnvFile
    Write-Ok '.env 已从 .env.example 生成，可按需修改。'
}

Write-Step '安装前端依赖'
if ($SkipFrontend) {
    Write-Info '已按 -SkipFrontend 跳过。'
} elseif (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Add-Warning '未找到 npm。请安装 Node.js 20+ 后重新运行本脚本。'
} else {
    $nodeVersion = (& node --version 2>&1 | Out-String).Trim()
    $npmVersion = (& npm --version 2>&1 | Out-String).Trim()
    Write-Info "Node ${nodeVersion} / npm ${npmVersion}"
    if ($nodeVersion -match '^v(\d+)\.') {
        if ([int]$Matches[1] -lt 20) { Add-Warning "Node.js 版本低于 20（${nodeVersion}），Vite 6 可能无法启动。" }
    }
    if ((Test-Path -LiteralPath $FrontendNodeModules) -and -not $Force) {
        Write-Ok 'frontend\node_modules 已存在，跳过安装（-Force 可强制重装）。'
    } else {
        Push-Location $FrontendDir
        try {
            & npm install --no-fund --no-audit
            if ($LASTEXITCODE -ne 0) { Add-Warning 'npm install 失败，请检查网络或代理后重试。' } else { Write-Ok '前端依赖安装完成。' }
        } finally {
            Pop-Location
        }
    }
}

Write-Step '检查本地模型（Ollama）'
$provider = (Get-EnvFileValue -Path $EnvFile -Key 'AILR_MODEL_PROVIDER' -Default 'ollama').ToLowerInvariant()
$modelName = Get-EnvFileValue -Path $EnvFile -Key 'AILR_MODEL_NAME' -Default 'qwen2.5vl:7b'
$ollamaBaseUrl = (Get-EnvFileValue -Path $EnvFile -Key 'AILR_OLLAMA_BASE_URL' -Default 'http://127.0.0.1:11434').TrimEnd('/')

if ($provider -eq 'openai') {
    Write-Info 'AILR_MODEL_PROVIDER=openai，跳过本地模型下载。'
    if (-not (Get-EnvFileValue -Path $EnvFile -Key 'AILR_OPENAI_API_KEY')) {
        Add-Warning 'AILR_OPENAI_API_KEY 为空：云端模式需要在 .env 或网页「模型设置」中填写密钥。'
    }
} elseif ($SkipModelPull) {
    Write-Info '已按 -SkipModelPull 跳过模型检查。'
} elseif (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Add-Warning '未找到 ollama 命令。使用本地模型请先安装 Ollama: https://ollama.com/download'
} else {
    $tags = Invoke-ApiGet -Url "$ollamaBaseUrl/api/tags" -TimeoutSeconds 5
    if (-not $tags) {
        Add-Warning "无法连接 Ollama（${ollamaBaseUrl}）。请先运行 ollama serve，之后可在网页「模型设置」里重新检测。"
    } else {
        $installedModels = @()
        if ($tags.models) { $installedModels = @($tags.models | ForEach-Object { [string]$_.name }) }
        if ($installedModels -contains $modelName) {
            Write-Ok "Ollama 中已存在模型 ${modelName}。"
        } else {
            Write-Warn "Ollama 中缺少 ${modelName}，开始下载（体积较大，可能耗时较久）。"
            & ollama pull $modelName
            if ($LASTEXITCODE -ne 0) { Add-Warning "ollama pull ${modelName} 失败，可在网络恢复后手动重试。" } else { Write-Ok "${modelName} 下载完成。" }
        }
    }
}

if ($RunTests) {
    Write-Step '运行后端单元测试'
    Push-Location $Root
    try {
        & $VenvPython -m unittest discover -s backend/tests -v
        if ($LASTEXITCODE -ne 0) { Add-Warning '单元测试未全部通过，请查看上面的输出。' } else { Write-Ok '单元测试全部通过。' }
    } finally {
        Pop-Location
    }
}

Write-Step '初始化完成'
Write-Host "    虚拟环境 : ${VenvPython}"
Write-Host "    环境变量 : ${EnvFile}"
Write-Host "    前端依赖 : ${FrontendNodeModules}"
Write-Host "    默认模型 : ${provider} / ${modelName}"
if (Test-Path -LiteralPath $ModelConfigFile) {
    Write-Host "    模型配置 : ${ModelConfigFile}"
} else {
    Write-Info '模型配置尚未生成；首次在网页「模型设置」保存时才会创建 backend\data\model_config.json。'
}

if ($Script:WarningCount -gt 0) {
    Write-Warn "本次初始化有 ${Script:WarningCount} 条警告，请检查上面的输出。"
} else {
    Write-Ok '未发现需要处理的问题。'
}

Write-Host ''
Write-Host '下一步：' -ForegroundColor White
Write-Host '    powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1' -ForegroundColor White
Write-Host '    或直接双击 start.bat' -ForegroundColor White

