[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$destinationPath = [IO.Path]::GetFullPath((Join-Path $HOME '.context-normalizer\integrations\pi'))
$markerPath = Join-Path $destinationPath '.ctxnorm-pi-install.json'
$marker = Get-Content -Raw -LiteralPath $markerPath | ConvertFrom-Json
if ($marker.schema_version -ne 1 -or $marker.component -ne 'pi' -or
    [IO.Path]::GetFullPath([string]$marker.destination) -ne $destinationPath) {
    throw 'Pi integration marker validation failed.'
}
& ctxnorm doctor | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Context Normalizer core verification failed.' }
if (-not (Test-Path -LiteralPath (Join-Path $destinationPath 'extensions\context-normalizer.mjs') -PathType Leaf)) {
    throw 'Pi integration extension is missing.'
}
& pi list | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Pi integration registration verification failed.' }
Write-Host 'Pi integration verification passed.'
