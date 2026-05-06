# Start ComfyUI server (CPU mode)
# Run this script BEFORE running the pipeline without --skip-visual

$ComfyUIPath = "D:\ComfyUI"

if (-not (Test-Path $ComfyUIPath)) {
    Write-Error "ComfyUI not found at $ComfyUIPath"
    exit 1
}

Write-Host "Starting ComfyUI at http://localhost:8188 (CPU mode)..."
Write-Host "Press Ctrl+C to stop."
Write-Host ""

Set-Location $ComfyUIPath
python main.py --cpu --listen 0.0.0.0 --port 8188
