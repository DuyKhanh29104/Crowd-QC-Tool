param(
    [Parameter(Mandatory=$true)][string]$Source,
    [string]$Output,
    [ValidateSet('cuda:0','cpu')][string]$Device = 'cuda:0',
    [ValidateSet('DEMO','PILOT')][string]$EvidenceType = 'DEMO',
    [int]$MaxFrames = 40,
    [double]$EverySeconds = 1
)
$ErrorActionPreference = 'Stop'
# Resolve caller paths before changing directory.
$sourcePath = (Resolve-Path -LiteralPath $Source).Path
$outputPath = if ($Output) { [IO.Path]::GetFullPath($Output) } else { $null }
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    $pythonPath = if ($Device -eq 'cuda:0') { '.venv-gpu\Scripts\python.exe' } else { '.venv-model\Scripts\python.exe' }
    if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Run scripts/setup.ps1 -GPU (or -IncludeModel for CPU) first.' }
    if (-not $outputPath) { $outputPath = Join-Path $repoRoot ('runs\proposal_' + [Guid]::NewGuid().ToString('N')) }
    & $pythonPath scripts/spike_autolabel.py --source $sourcePath --weights weights/yolo11n.pt --output $outputPath --device $Device --evidence-type $EvidenceType --max-frames $MaxFrames --every-seconds $EverySeconds
    if ($LASTEXITCODE -ne 0) { throw 'Auto-label failed. Inspect the run manifest; no CPU fallback was performed.' }
    Write-Host ('Proposals (not GT): ' + $outputPath)
} finally { Pop-Location }
