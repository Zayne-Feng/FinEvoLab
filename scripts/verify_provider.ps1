param(
    [string]$CondaPath = 'D:\ProgramSoftware\Miniconda3\Scripts\conda.exe',
    [string]$EnvironmentName = 'finevolab',
    [string]$ConfigPath,
    [switch]$IncludeUpstream
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Parent $PSScriptRoot
if (-not $ConfigPath) {
    $ConfigPath = Join-Path $repositoryRoot 'finrobot_equity/core/config/config.ini'
}
$stages = @('minimal', 'tool', 'structured')
if ($IncludeUpstream) {
    $stages += 'upstream'
}
Push-Location -LiteralPath $repositoryRoot
try {
    foreach ($stage in $stages) {
        & $CondaPath run --no-capture-output -n $EnvironmentName python scripts/verify_model.py --config $ConfigPath --stage $stage
        if ($LASTEXITCODE -ne 0) {
            throw ('Provider verification failed at stage: ' + $stage)
        }
    }
}
finally {
    Pop-Location
}
