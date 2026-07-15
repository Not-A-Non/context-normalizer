function Test-ContextNormalizerAbsolutePath {
    param([Parameter(Mandatory = $true)][string]$Path)
    if ($Path -match '^[A-Za-z]:[\\/]' -or $Path -match '^\\\\' -or $Path.StartsWith('/')) {
        return $true
    }
    return $false
}

function Get-ContextNormalizerRuntime {
    $configPath = if ($env:CONTEXT_NORMALIZER_HOME) {
        if (-not (Test-ContextNormalizerAbsolutePath $env:CONTEXT_NORMALIZER_HOME)) {
            throw 'CONTEXT_NORMALIZER_HOME must be an absolute path'
        }
        [IO.Path]::GetFullPath($env:CONTEXT_NORMALIZER_HOME)
    } else {
        [IO.Path]::GetFullPath((Join-Path $HOME '.context-normalizer'))
    }
    $markerPath = Join-Path $configPath 'installation.json'
    $runtimePath = Join-Path $configPath 'runtime-python.txt'
    if (-not (Test-Path -LiteralPath $markerPath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $runtimePath -PathType Leaf)) {
        throw "Context Normalizer installation marker is missing under $configPath"
    }
    $marker = Get-Content -LiteralPath $markerPath -Raw | ConvertFrom-Json
    if ($marker.schema_version -ne 1 -or
        [IO.Path]::GetFullPath([string]$marker.config_dir) -ne $configPath) {
        throw "Context Normalizer installation marker does not match $configPath"
    }
    $python = (Get-Content -LiteralPath $runtimePath -Raw).Trim()
    if (-not (Test-ContextNormalizerAbsolutePath $python) -or
        -not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "Recorded Python runtime is missing or invalid: $python"
    }
    [pscustomobject]@{
        ConfigPath = $configPath
        Python = $python
        InstallationId = [string]$marker.installation_id
    }
}
