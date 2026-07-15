[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repository = Split-Path -Parent $PSScriptRoot
$tempRoot = Join-Path ([IO.Path]::GetTempPath()) ("ctxnorm-acceptance-" + [guid]::NewGuid().ToString("n"))
$buildVenv = Join-Path $tempRoot 'build-venv'
$buildPython = Join-Path $buildVenv 'Scripts\python.exe'
$venv = Join-Path $tempRoot 'venv'
$python = Join-Path $venv 'Scripts\python.exe'
$wheelRoot = Join-Path $tempRoot 'wheel'
$env:CONTEXT_NORMALIZER_HOME = Join-Path $tempRoot 'normalizer'
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'

function Invoke-Native {
    param(
        [scriptblock]$Script,
        [string]$Label
    )
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & $Script 2>&1
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($null -eq $exitCode) {
        $exitCode = 0
    }
    if ($exitCode -ne 0) {
        throw "$Label failed with exit code $exitCode`: $($output -join [Environment]::NewLine)"
    }
    return $output
}

function Assert-True {
    param(
        [bool]$Condition,
        [string]$Message
    )
    if (-not $Condition) {
        throw $Message
    }
}

try {
    New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null

    New-Item -ItemType Directory -Path $wheelRoot -Force | Out-Null
    $null = Invoke-Native {
        py -3 -m venv $buildVenv
    } 'build venv'
    $null = Invoke-Native {
        & $buildPython -m pip install --require-hashes --only-binary=:all: -r (Join-Path $repository 'requirements\build.lock')
    } 'build tool install'
    $previousNoIndex = $env:PIP_NO_INDEX
    try {
        $env:PIP_NO_INDEX = '1'
        $null = Invoke-Native {
            & $buildPython -m build --wheel --no-isolation --outdir $wheelRoot $repository
        } 'wheel build'
    } finally {
        $env:PIP_NO_INDEX = $previousNoIndex
    }
    $wheels = @(Get-ChildItem -LiteralPath $wheelRoot -Filter '*.whl')
    Assert-True ($wheels.Count -eq 1) "Expected one wheel, found $($wheels.Count)."

    $null = Invoke-Native {
        py -3 -m venv $venv
    } 'venv'

    $null = Invoke-Native {
        & $python -m pip install --no-index --no-deps $wheels[0].FullName
    } 'install'

    $null = Invoke-Native {
        & $python -m context_normalizer init
    } 'init'

    $null = Invoke-Native {
        & $python -m context_normalizer doctor
    } 'doctor'

    $sample = 'Review the perf regression and repro steps. Preserve `perf regression`.'
    $normalized = Invoke-Native {
        $sample | & $python -m context_normalizer normalize --no-context
    } 'normalize'
    Assert-True ($normalized -match 'performance regression') 'Performance terminology was not normalized.'
    Assert-True ($normalized -match 'reproduction steps') 'Reproduction terminology was not normalized.'
    Assert-True ($normalized -match '`perf regression`') 'Protected inline code changed.'

    $workspace = Join-Path $tempRoot 'workspace'
    New-Item -ItemType Directory -Path (Join-Path $workspace 'kernel') -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $workspace 'kernel\payload.txt') `
        -Value 'kernel repro steps' -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $env:CONTEXT_NORMALIZER_HOME 'path-rules.tsv') `
        -Value @("kernel`truntime-boundary", "repro steps`treproduction steps") -Encoding Ascii

    $created = Invoke-Native {
        & $python -m context_normalizer workspace create $workspace --mode filesystem `
            --normalize-paths --format json --yes
    } 'workspace create' | ConvertFrom-Json
    Assert-True (Test-Path -LiteralPath $created.mirror) 'Workspace mirror was not created.'

    $bridged = Invoke-Native {
        'kernel and repro steps' |
            & $python -m context_normalizer bridge submit --workspace $created.mirror
    } 'bridge submit'
    Assert-True ($bridged -match 'runtime-boundary and reproduction steps') `
        'Bridge prompt was not normalized.'
    $normalizedPayload = Join-Path $created.mirror 'runtime-boundary\payload.txt'
    Assert-True ((Get-Content -Raw -LiteralPath $normalizedPayload).Trim() -eq `
        'runtime-boundary reproduction steps') 'Workspace payload was not normalized.'
    $normalizedFile = Join-Path $created.mirror 'runtime-boundary\new runtime-boundary.txt'
    Set-Content -LiteralPath $normalizedFile -Value 'runtime-boundary reproduction steps' -Encoding UTF8
    $null = Invoke-Native {
        & $python -m context_normalizer bridge complete --workspace $created.mirror
    } 'bridge complete'
    Assert-True (Test-Path -LiteralPath (Join-Path $workspace 'kernel\new kernel.txt')) `
        'Bridge completion did not normalize path names.'
    Assert-True ((Get-Content -Raw -LiteralPath (Join-Path $workspace 'kernel\new kernel.txt')).Trim() -eq `
        'kernel repro steps') 'Bridge completion did not normalize payload content.'

    $vocabulary = Invoke-Native {
        & $python -m context_normalizer vocabulary list --format json
    } 'vocabulary list' | ConvertFrom-Json
    Assert-True ($vocabulary.count -gt 0) 'Vocabulary manifest is empty.'

    [pscustomobject]@{
        status = 'pass'
        vocabulary = $vocabulary.count
        mirror = $created.mirror
    } | ConvertTo-Json -Depth 5
} finally {
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}
