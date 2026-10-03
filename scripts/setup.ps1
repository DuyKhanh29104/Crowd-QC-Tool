param([switch]$IncludeModel, [switch]$GPU)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    if (-not (Test-Path '.venv-app\Scripts\python.exe')) {
        python -m venv .venv-app
        if ($LASTEXITCODE -ne 0) { throw 'Cannot create app environment' }
    }
    & .\.venv-app\Scripts\python.exe -m pip install -r requirements-app.lock
    if ($LASTEXITCODE -ne 0) { throw 'App dependency installation failed' }
    Push-Location frontend
    try {
        npm ci
        if ($LASTEXITCODE -ne 0) { throw 'Frontend installation failed' }
        npm run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
    } finally { Pop-Location }
    if ($IncludeModel) {
        if (-not (Test-Path '.venv-model\Scripts\python.exe')) {
            python -m venv .venv-model
            if ($LASTEXITCODE -ne 0) { throw 'Cannot create model environment' }
        }
        & .\.venv-model\Scripts\python.exe -m pip install -r requirements-model.lock
        if ($LASTEXITCODE -ne 0) { throw 'Model dependency installation failed' }
    }
    if ($GPU) {
        if (-not (Test-Path '.venv-gpu\Scripts\python.exe')) {
            python -m venv .venv-gpu
            if ($LASTEXITCODE -ne 0) { throw 'Cannot create GPU environment' }
        }
        & .\.venv-gpu\Scripts\python.exe -m pip install -r requirements-gpu.lock
        if ($LASTEXITCODE -ne 0) { throw 'GPU dependency installation failed' }
        & .\.venv-gpu\Scripts\python.exe scripts/check_environment.py --output docs/evidence/environment_gpu.json --require-cuda
        if ($LASTEXITCODE -ne 0) { throw 'CUDA validation failed; see environment_gpu.json' }
    }
    Write-Host 'Ready. Start with: powershell -ExecutionPolicy Bypass -File scripts/run.ps1'
} finally { Pop-Location }
