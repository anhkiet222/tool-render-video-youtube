param(
    [Parameter(Mandatory=$true)]
    [string]$Topic,
    [switch]$SkipVisual
)

$env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("PATH", "User")
Set-Location $PSScriptRoot

# --- 1. Ollama ---
Write-Host "[1/3] Checking Ollama..." -ForegroundColor Cyan
try {
    Invoke-WebRequest -Uri "http://localhost:11434" -TimeoutSec 3 | Out-Null
    Write-Host "      Ollama already running" -ForegroundColor Green
} catch {
    Write-Host "      Starting Ollama..." -ForegroundColor Yellow
    Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden
    $i = 0
    do {
        Start-Sleep -Seconds 2
        $i++
        try { Invoke-WebRequest -Uri "http://localhost:11434" -TimeoutSec 2 | Out-Null; break } catch {}
    } while ($i -lt 15)
    Write-Host "      Ollama ready" -ForegroundColor Green
}

# --- 2. ComfyUI ---
Write-Host "[2/3] Checking ComfyUI..." -ForegroundColor Cyan
try {
    Invoke-WebRequest -Uri "http://localhost:8188" -TimeoutSec 3 | Out-Null
    Write-Host "      ComfyUI already running" -ForegroundColor Green
} catch {
    Write-Host "      Starting ComfyUI (CPU mode)..." -ForegroundColor Yellow
    Start-Process -FilePath "python" -ArgumentList "main.py --cpu --listen 127.0.0.1 --port 8188" -WorkingDirectory "D:\ComfyUI" -WindowStyle Normal
    $i = 0
    do {
        Start-Sleep -Seconds 5
        $i++
        $waited = $i * 5
        Write-Host "      Waiting for ComfyUI... ${waited}s" -ForegroundColor DarkGray
        try { Invoke-WebRequest -Uri "http://localhost:8188" -TimeoutSec 3 | Out-Null; break } catch {}
    } while ($i -lt 24)
    Write-Host "      ComfyUI ready" -ForegroundColor Green
}

# --- 3. Pipeline ---
Write-Host "[3/3] Running pipeline..." -ForegroundColor Cyan
if ($SkipVisual) {
    python main.py $Topic --skip-visual
} else {
    python main.py $Topic
}
