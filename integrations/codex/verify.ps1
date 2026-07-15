[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$destinationPath = [IO.Path]::GetFullPath((Join-Path $HOME '.context-normalizer\integrations\codex'))
$marker = Get-Content -Raw -LiteralPath (Join-Path $destinationPath '.ctxnorm-codex-install.json') | ConvertFrom-Json
if ($marker.schema_version -ne 1 -or $marker.component -ne 'codex' -or
    [IO.Path]::GetFullPath([string]$marker.destination) -ne $destinationPath) {
    throw 'Codex integration marker does not match the requested destination.'
}
& ctxnorm doctor | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Context Normalizer core verification failed.' }
$binary = Join-Path $destinationPath 'bin\codex.exe'
& $binary --version | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Codex integration executable verification failed.' }
Write-Host 'Codex integration verification passed.'
