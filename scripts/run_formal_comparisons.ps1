param(
    [Parameter(Mandatory = $true)][string]$OptimizationDirectory,
    [string]$CondaPath = 'D:\ProgramSoftware\Miniconda3\Scripts\conda.exe',
    [string]$EnvironmentName = 'finevolab',
    [string]$DatasetDirectory = 'data/evaluation/v2',
    [string]$RunPrefix = ([Guid]::NewGuid().ToString('N').Substring(0, 12) + '-v2')
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $repositoryRoot
try {
    $optimizationRoot = (Resolve-Path -LiteralPath $OptimizationDirectory).Path
    $summary = Get-Content -Raw -Encoding UTF8 (Join-Path $optimizationRoot 'summary.json') | ConvertFrom-Json
    if ($summary.status -ne 'complete' -or $summary.test_feedback_used -ne $false) {
        throw 'A completed optimization without test feedback is required'
    }
    $optimizationManifest = Get-Content -Raw -Encoding UTF8 (Join-Path $optimizationRoot 'manifest.json') | ConvertFrom-Json
    foreach ($source in $optimizationManifest.source_hashes.PSObject.Properties) {
        if ($source.Name -match '^(finevolab|configs|requirements|data/evaluation)/') {
            $digest = (Get-FileHash -Algorithm SHA256 -LiteralPath $source.Name).Hash.ToLower()
            if ($digest -ne $source.Value) {
                throw ('Frozen source differs: ' + $source.Name)
            }
        }
    }
    $selectedPrompt = Join-Path $optimizationRoot 'selected_candidate.json'
    if (-not (Test-Path -LiteralPath $selectedPrompt -PathType Leaf)) {
        throw 'The selected candidate is missing'
    }
    foreach ($variant in @('tools', 'rag', 'harness')) {
        & $CondaPath run --no-capture-output -n $EnvironmentName python scripts/run_experiments.py --dataset-dir $DatasetDirectory --split validation --variant $variant --repeats 3 --run-id ($RunPrefix + '-' + $variant + '-validation')
        if ($LASTEXITCODE -ne 0) { throw ('Validation run stopped: ' + $variant) }
    }
    & $CondaPath run --no-capture-output -n $EnvironmentName python scripts/run_experiments.py --dataset-dir $DatasetDirectory --split test --variant tools --repeats 3 --run-id ($RunPrefix + '-initial-test')
    if ($LASTEXITCODE -ne 0) { throw 'The initial test run stopped' }
    & $CondaPath run --no-capture-output -n $EnvironmentName python scripts/run_experiments.py --dataset-dir $DatasetDirectory --split test --variant tools --repeats 3 --prompt $selectedPrompt --run-id ($RunPrefix + '-selected-test')
    if ($LASTEXITCODE -ne 0) { throw 'The selected test run stopped' }
    $experiments = Join-Path $repositoryRoot 'project_notes/experiments'
    $evaluations = Join-Path $repositoryRoot 'project_notes/evaluations'
    foreach ($variant in @('rag', 'harness')) {
        & $CondaPath run --no-capture-output -n $EnvironmentName python scripts/compare_experiments.py (Join-Path $experiments ($RunPrefix + '-tools-validation')) (Join-Path $experiments ($RunPrefix + '-' + $variant + '-validation')) --output (Join-Path $evaluations ($RunPrefix + '-tools-vs-' + $variant + '.json'))
        if ($LASTEXITCODE -ne 0) { throw ('Paired comparison failed: ' + $variant) }
    }
    & $CondaPath run --no-capture-output -n $EnvironmentName python scripts/compare_experiments.py (Join-Path $experiments ($RunPrefix + '-initial-test')) (Join-Path $experiments ($RunPrefix + '-selected-test')) --output (Join-Path $evaluations ($RunPrefix + '-initial-vs-selected.json'))
    if ($LASTEXITCODE -ne 0) { throw 'The final paired comparison failed' }
}
finally {
    Pop-Location
}
