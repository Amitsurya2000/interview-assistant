# Interview Assistant Launcher
# Run this script to start all components

param(
    [switch]$ListDevices,
    [int]$DeviceId,
    [string]$WsUrl = "ws://127.0.0.1:8123/"
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Interview Assistant Launcher" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Check Python
try {
    $pyVersion = python --version
    Write-Host "[OK] $pyVersion" -ForegroundColor Green
} catch {
    Write-Host "[ERROR] Python not found. Please install Python 3.9+" -ForegroundColor Red
    exit 1
}

# Check if virtual environment exists
$venvPath = Join-Path $ProjectDir "venv"
if (-not (Test-Path $venvPath)) {
    Write-Host "[...] Creating virtual environment..." -ForegroundColor Yellow
    python -m venv $venvPath
    if (-not $?) {
        Write-Host "[ERROR] Failed to create venv" -ForegroundColor Red
        exit 1
    }
    Write-Host "[OK] Virtual environment created" -ForegroundColor Green
}

# Activate venv and install deps
$pip = Join-Path $venvPath "Scripts\pip.exe"
$python = Join-Path $venvPath "Scripts\python.exe"

Write-Host "[...] Installing dependencies..." -ForegroundColor Yellow
& $pip install -r "$ProjectDir\requirements.txt" -q
if (-not $?) {
    Write-Host "[ERROR] Failed to install dependencies" -ForegroundColor Red
    exit 1
}
Write-Host "[OK] Dependencies installed" -ForegroundColor Green

# Check Ollama
try {
    $ollamaCheck = & ollama list 2>&1
    Write-Host "[OK] Ollama is running" -ForegroundColor Green
} catch {
    Write-Host "[WARNING] Ollama not detected. Make sure to run 'ollama serve' and 'ollama pull llama3.2'" -ForegroundColor Yellow
}

if ($ListDevices) {
    Write-Host "[...] Listing audio devices..." -ForegroundColor Yellow
    & $python "$ProjectDir\audio_client.py" --list-devices
    exit 0
}

# Start server in background
Write-Host "[...] Starting WebSocket server..." -ForegroundColor Yellow
$serverJob = Start-Job -ScriptBlock {
    param($p, $d)
    Set-Location $d
    & $p "$d\server.py"
} -ArgumentList $python, $ProjectDir

Write-Host "[OK] Server started on $WsUrl" -ForegroundColor Green

# Wait for server to be ready
Start-Sleep -Seconds 3

# Open overlay in browser
Write-Host "[...] Opening overlay..." -ForegroundColor Yellow
Start-Process "msedge.exe" -ArgumentList "$ProjectDir\overlay.html" -ErrorAction SilentlyContinue
Start-Process "chrome.exe" -ArgumentList "$ProjectDir\overlay.html" -ErrorAction SilentlyContinue

# Start audio client
if ($DeviceId -ge 0) {
    Write-Host "[...] Starting audio client (device: $DeviceId)..." -ForegroundColor Yellow
    & $python "$ProjectDir\audio_client.py" --device $DeviceId
} else {
    Write-Host ""
    Write-Host "Audio client not started automatically." -ForegroundColor Yellow
    Write-Host "To start with system audio capture, run:" -ForegroundColor Yellow
    Write-Host "  .\venv\Scripts\python audio_client.py --device <device_id>" -ForegroundColor Gray
    Write-Host ""
    Write-Host "To list available devices:" -ForegroundColor Yellow
    Write-Host "  .\venv\Scripts\python audio_client.py --list-devices" -ForegroundColor Gray
    Write-Host ""
    Write-Host "Press Ctrl+C to stop the server." -ForegroundColor Yellow

    try {
        while ($true) { Start-Sleep -Seconds 1 }
    } finally {
        Stop-Job $serverJob
        Remove-Job $serverJob
    }
}

Stop-Job $serverJob -ErrorAction SilentlyContinue
Remove-Job $serverJob -ErrorAction SilentlyContinue
