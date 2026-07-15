[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$destinationPath = [IO.Path]::GetFullPath((Join-Path $HOME '.context-normalizer\integrations\pi'))
$markerPath = Join-Path $destinationPath '.ctxnorm-pi-install.json'
if (-not (Test-Path -LiteralPath $markerPath -PathType Leaf)) {
    throw "Pi integration marker is missing at $markerPath"
}
$unsafeEntries = @(Get-ChildItem -LiteralPath $destinationPath -Recurse -Force | Where-Object {
    $_.Attributes -band [IO.FileAttributes]::ReparsePoint
})
if ($unsafeEntries.Count -ne 0) {
    throw 'Pi integration contains a linked or reparse-point entry. Nothing was removed.'
}
$marker = Get-Content -Raw -LiteralPath $markerPath | ConvertFrom-Json
if ($marker.schema_version -ne 1 -or $marker.component -ne 'pi' -or
    [IO.Path]::GetFullPath([string]$marker.destination) -ne $destinationPath) {
    throw 'Pi integration marker does not match the requested destination.'
}
$expectedFiles = @(
    'extensions\context-normalizer.mjs', 'package.json', 'README.md', 'LICENSE', 'THIRD_PARTY-NOTICES.md',
    'uninstall.ps1', 'verify.ps1', '.ctxnorm-pi-install.json'
)
$destinationPrefix = $destinationPath.TrimEnd('\') + '\'
$actualFiles = @(Get-ChildItem -LiteralPath $destinationPath -File -Recurse | ForEach-Object {
    $_.FullName.Substring($destinationPrefix.Length)
})
if (@(Compare-Object $expectedFiles $actualFiles).Count -ne 0) {
    throw 'Pi integration contains an unexpected or missing file. Nothing was removed.'
}
if (Get-Command pi -ErrorAction SilentlyContinue) {
    & pi remove $destinationPath
    if ($LASTEXITCODE -ne 0) { throw "Pi package removal failed: $LASTEXITCODE" }
}
$expectedFiles | ForEach-Object { Remove-Item -LiteralPath (Join-Path $destinationPath $_) -Force }
Remove-Item -LiteralPath (Join-Path $destinationPath 'extensions')
Remove-Item -LiteralPath $destinationPath
Write-Host 'Pi integration removed.'
