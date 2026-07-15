$ErrorActionPreference = 'Stop'
$binary = Join-Path $PSScriptRoot 'bin\codex.exe'
if (-not (Test-Path -LiteralPath $binary -PathType Leaf)) {
    throw "Codex integration binary is missing: $binary"
}
& $binary @args
exit $LASTEXITCODE
