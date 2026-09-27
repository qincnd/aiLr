# aiLr 脚本共享函数库。由 init.ps1 / start.ps1 通过点源方式加载，不直接运行。
# 兼容 Windows PowerShell 5.1 与 PowerShell 7+。

Set-StrictMode -Version Latest

function Write-Step {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host ''
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Ok {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host "    [完成] $Message" -ForegroundColor Green
}

function Write-Info {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host "    [信息] $Message" -ForegroundColor Gray
}

function Write-Warn {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host "    [警告] $Message" -ForegroundColor Yellow
}

function Fail {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host ''
    Write-Host "[失败] $Message" -ForegroundColor Red
    exit 1
}

# 读取 .env 中的键值；文件不存在或键缺失时返回默认值。
function Get-EnvFileValue {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Key,
        [string]$Default = ''
    )
    if (-not (Test-Path -LiteralPath $Path)) { return $Default }
    $pattern = '^\s*' + [regex]::Escape($Key) + '\s*=\s*(.*)$'
    foreach ($line in (Get-Content -LiteralPath $Path -Encoding UTF8)) {
        if ($line -match $pattern) {
            return $Matches[1].Trim().Trim('"')
        }
    }
    return $Default
}

# 检查本机 TCP 端口是否可建立连接（不需要管理员权限）。
function Test-TcpPort {
    param(
        [Parameter(Mandatory = $true)][int]$Port,
        [string]$ComputerName = '127.0.0.1',
        [int]$TimeoutMilliseconds = 600
    )
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect($ComputerName, $Port, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne($TimeoutMilliseconds, $false)) { return $false }
        $client.EndConnect($async)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Wait-ForTcpPort {
    param(
        [Parameter(Mandatory = $true)][int]$Port,
        [int]$TimeoutSeconds = 60,
        [int]$IntervalMilliseconds = 500
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-TcpPort -Port $Port) { return $true }
        Start-Sleep -Milliseconds $IntervalMilliseconds
    }
    return $false
}

# 发起一次 GET 请求，失败时返回 $null 而不抛出异常。
# FastAPI 的 JSON 响应不带 charset，Windows PowerShell 5.1 会按 Latin-1 解码导致中文乱码，
# 因此优先从原始字节流按 UTF-8 解码后再解析 JSON。
function Invoke-ApiGet {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [int]$TimeoutSeconds = 5
    )
    try {
        $response = Invoke-WebRequest -Uri $Url -Method Get -TimeoutSec $TimeoutSeconds -UseBasicParsing -ErrorAction Stop
    } catch {
        return $null
    }

    $json = $null
    try {
        if ($response.RawContentStream) {
            $response.RawContentStream.Position = 0
            $reader = New-Object System.IO.StreamReader($response.RawContentStream, (New-Object System.Text.UTF8Encoding($false)))
            $json = $reader.ReadToEnd()
            $reader.Dispose()
        }
    } catch {
        $json = $null
    }
    if (-not $json) { $json = [string]$response.Content }

    try {
        return ($json | ConvertFrom-Json)
    } catch {
        return $null
    }
}

function Get-BackendHealth {
    param(
        [int]$ApiPort = 8000,
        [int]$TimeoutSeconds = 3
    )
    return Invoke-ApiGet -Url "http://127.0.0.1:${ApiPort}/api/health" -TimeoutSeconds $TimeoutSeconds
}

function Wait-ForBackend {
    param(
        [int]$ApiPort = 8000,
        [int]$TimeoutSeconds = 60,
        [int]$IntervalMilliseconds = 500
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $health = Get-BackendHealth -ApiPort $ApiPort
        if ($health -and $health.status -eq 'ok') { return $health }
        Start-Sleep -Milliseconds $IntervalMilliseconds
    }
    return $null
}

# 返回当前使用的 PowerShell 宿主可执行文件路径，用于打开新的控制台窗口。
function Get-ShellExecutable {
    try {
        $path = (Get-Process -Id $PID).Path
        if ($path) { return $path }
    } catch {
    }
    if ($PSVersionTable.PSEdition -eq 'Core') { return 'pwsh.exe' }
    return 'powershell.exe'
}
