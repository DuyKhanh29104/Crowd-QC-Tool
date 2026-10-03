param([switch]$Build)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    if (-not (Test-Path '.venv-app\Scripts\python.exe')) { throw 'Run scripts/setup.ps1 first.' }
    if ($Build -or -not (Test-Path 'frontend\dist\index.html')) {
        Push-Location frontend
        try {
            npm run build
            if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
        } finally { Pop-Location }
    }
    Write-Host 'Crowd QC: http://127.0.0.1:8765 (Ctrl+C to stop)'
    & .\.venv-app\Scripts\python.exe -m uvicorn backend.app.main:create_app --factory --host 127.0.0.1 --port 8765
    if ($LASTEXITCODE -ne 0) { throw 'API stopped with an error.' }
} finally { Pop-Location }
