[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$source = $PSScriptRoot
$destinationPath = [IO.Path]::GetFullPath((Join-Path $HOME '.context-normalizer\integrations\pi'))
if (Test-Path -LiteralPath $destinationPath) {
    throw "Pi integration already exists at $destinationPath"
}
if (-not (Get-Command ctxnorm -ErrorAction SilentlyContinue)) {
    throw 'Context Normalizer core must be installed first.'
}
if (-not (Get-Command pi -ErrorAction SilentlyContinue)) {
    throw 'Pi must be installed first.'
}

& ctxnorm doctor | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Context Normalizer core verification failed.' }

$created = $false
try {
    New-Item -ItemType Directory -Path $destinationPath | Out-Null
    $created = $true
    Copy-Item -LiteralPath (Join-Path $source 'package.json') -Destination $destinationPath
    Copy-Item -LiteralPath (Join-Path $source 'README.md') -Destination $destinationPath
    Copy-Item -LiteralPath (Join-Path $source 'LICENSE') -Destination $destinationPath
    Copy-Item -LiteralPath (Join-Path $source 'THIRD_PARTY-NOTICES.md') -Destination $destinationPath
    Copy-Item -LiteralPath (Join-Path $source 'uninstall.ps1') -Destination $destinationPath
    Copy-Item -LiteralPath (Join-Path $source 'verify.ps1') -Destination $destinationPath
    Copy-Item -LiteralPath (Join-Path $source 'extensions') -Destination $destinationPath -Recurse
    @{
        schema_version = 1
        component = 'pi'
        version = '1.0.0'
        destination = $destinationPath
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $destinationPath '.ctxnorm-pi-install.json') -Encoding UTF8
    & pi install $destinationPath
    if ($LASTEXITCODE -ne 0) { throw "Pi package registration failed: $LASTEXITCODE" }
} catch {
    if ($created -and (Test-Path -LiteralPath $destinationPath)) {
        @(
            'extensions\context-normalizer.mjs', 'package.json', 'README.md', 'LICENSE', 'THIRD_PARTY-NOTICES.md',
            'uninstall.ps1', 'verify.ps1', '.ctxnorm-pi-install.json'
        ) | ForEach-Object {
            Remove-Item -LiteralPath (Join-Path $destinationPath $_) -Force -ErrorAction SilentlyContinue
        }
        Remove-Item -LiteralPath (Join-Path $destinationPath 'extensions') -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $destinationPath -ErrorAction SilentlyContinue
    }
    throw
}
& (Join-Path $destinationPath 'verify.ps1')
Write-Host "Pi integration installed at $destinationPath"
