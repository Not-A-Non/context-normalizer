$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'ContextNormalizerRuntime.ps1')
$runtime = Get-ContextNormalizerRuntime

& $runtime.Python -m context_normalizer purge --yes
if ($LASTEXITCODE -ne 0) {
    throw "Owned configuration cleanup failed; package was retained: $LASTEXITCODE"
}
& $runtime.Python -m pip uninstall --yes context-normalizer
if ($LASTEXITCODE -ne 0) { throw "Package removal failed: $LASTEXITCODE" }

Write-Host 'Context Normalizer was removed.'
