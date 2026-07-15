[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$destinationPath = [IO.Path]::GetFullPath((Join-Path $HOME '.context-normalizer\integrations\codex'))
$markerPath = Join-Path $destinationPath '.ctxnorm-codex-install.json'
if (-not (Test-Path -LiteralPath $markerPath -PathType Leaf)) {
    throw "Codex integration marker is missing at $markerPath"
}
$unsafeEntries = @(Get-ChildItem -LiteralPath $destinationPath -Recurse -Force | Where-Object {
    $_.Attributes -band [IO.FileAttributes]::ReparsePoint
})
if ($unsafeEntries.Count -ne 0) {
    throw 'Codex integration contains a linked or reparse-point entry. Nothing was removed.'
}
$marker = Get-Content -Raw -LiteralPath $markerPath | ConvertFrom-Json
if ($marker.schema_version -ne 1 -or $marker.component -ne 'codex' -or
    [IO.Path]::GetFullPath([string]$marker.destination) -ne $destinationPath) {
    throw 'Codex integration marker does not match the requested destination.'
}
$expectedFiles = @(
    'bin\codex.exe', 'integration.json', 'README.md', 'LICENSE', 'THIRD_PARTY-NOTICES.md', 'ctxnorm-codex.ps1',
    'uninstall.ps1', 'verify.ps1', '.ctxnorm-codex-install.json'
)
$destinationPrefix = $destinationPath.TrimEnd('\') + '\'
$actualFiles = @(Get-ChildItem -LiteralPath $destinationPath -File -Recurse | ForEach-Object {
    $_.FullName.Substring($destinationPrefix.Length)
})
if (@(Compare-Object $expectedFiles $actualFiles).Count -ne 0) {
    throw 'Codex integration contains an unexpected or missing file. Nothing was removed.'
}
$expectedFiles | ForEach-Object { Remove-Item -LiteralPath (Join-Path $destinationPath $_) -Force }
Remove-Item -LiteralPath (Join-Path $destinationPath 'bin')
Remove-Item -LiteralPath $destinationPath
Write-Host 'Codex integration removed.'
