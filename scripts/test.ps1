<#
.SYNOPSIS
    Run aiLr's local backend tests and frontend production build.
.DESCRIPTION
    Runs the backend unittest suite and the frontend TypeScript/Vite build without
    starting Ollama, Lightroom Classic, or either application server.
.PARAMETER BackendOnly
    Run only the backend unittest suite.
.PARAMETER FrontendOnly
    Run only the frontend TypeScript/Vite build.
#>
#Requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$BackendOnly,
    [switch]$FrontendOnly
)

if ($BackendOnly -and $FrontendOnly) {
    Write-Error 'Choose only one of -BackendOnly or -FrontendOnly.'
    exit 2
}

$Root = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $Root '.venv\Scripts\python.exe'
$FrontendNodeModules = Join-Path $Root 'frontend\node_modules'
$RunBackend = -not $FrontendOnly
$RunFrontend = -not $BackendOnly
$Failures = 0

if ($RunBackend) {
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        Write-Error 'Missing .venv\Scripts\python.exe. Run .\scripts\init.ps1 first.'
        $Failures++
    } else {
        Write-Host "`n=== Backend unittest ===" -ForegroundColor Cyan
        Push-Location $Root
        try {
            & $VenvPython -m unittest discover -s 'backend\tests' -v
            if ($LASTEXITCODE -ne 0) {
                Write-Host 'Backend tests failed.' -ForegroundColor Red
                $Failures++
            } else {
                Write-Host 'Backend tests passed.' -ForegroundColor Green
            }
        } finally {
            Pop-Location
        }
    }
}

if ($RunFrontend) {
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        Write-Error 'npm not found. Install Node.js 20+ and ensure npm is on PATH.'
        $Failures++
    } elseif (-not (Test-Path -LiteralPath $FrontendNodeModules)) {
        Write-Error 'Missing frontend\node_modules. Run .\scripts\init.ps1 first.'
        $Failures++
    } else {
        Write-Host "`n=== Frontend TypeScript check and production build ===" -ForegroundColor Cyan
        Push-Location $Root
        try {
            & npm --prefix frontend run build
            if ($LASTEXITCODE -ne 0) {
                Write-Host 'Frontend build failed.' -ForegroundColor Red
                $Failures++
            } else {
                Write-Host 'Frontend TypeScript check and production build passed.' -ForegroundColor Green
            }
        } finally {
            Pop-Location
        }
    }
}

if ($Failures -gt 0) {
    Write-Host "`nLocal validation failed ($Failures failing step(s))." -ForegroundColor Red
    exit 1
}

Write-Host "`nAll local validation passed." -ForegroundColor Green
exit 0
