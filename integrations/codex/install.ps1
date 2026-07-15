[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$destinationPath = [IO.Path]::GetFullPath((Join-Path $HOME '.context-normalizer\integrations\codex'))
if (Test-Path -LiteralPath $destinationPath) {
    throw "Codex integration already exists at $destinationPath"
}
foreach ($command in @('ctxnorm', 'git', 'cargo')) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "$command must be installed first."
    }
}
& ctxnorm doctor | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Context Normalizer core verification failed.' }

$manifest = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'integration.json') | ConvertFrom-Json
$temporaryRoot = Join-Path ([IO.Path]::GetTempPath()) ('ctxnorm-codex-' + [guid]::NewGuid().ToString('N'))
$source = Join-Path $temporaryRoot 'codex'
$created = $false
try {
    New-Item -ItemType Directory -Path $temporaryRoot | Out-Null
    & git clone --filter=blob:none --branch $manifest.upstream_tag --depth 1 $manifest.upstream_repository $source
    if ($LASTEXITCODE -ne 0) { throw "Codex source checkout failed: $LASTEXITCODE" }
    $commit = (& git -C $source rev-parse HEAD).Trim()
    if ($commit -ne $manifest.upstream_commit) {
        throw "Codex source revision mismatch: $commit"
    }
    & git -C $source apply (Join-Path $PSScriptRoot 'patches\codex-tui-context-normalizer.patch')
    if ($LASTEXITCODE -ne 0) { throw "Codex integration patch failed: $LASTEXITCODE" }
    Push-Location (Join-Path $source 'codex-rs')
    try {
        & cargo test -p codex-tui context_normalizer
        if ($LASTEXITCODE -ne 0) { throw "Codex integration tests failed: $LASTEXITCODE" }
        & cargo build -p codex-cli --release
        if ($LASTEXITCODE -ne 0) { throw "Codex integration build failed: $LASTEXITCODE" }
    } finally {
        Pop-Location
    }
    New-Item -ItemType Directory -Path (Join-Path $destinationPath 'bin') | Out-Null
    $created = $true
    Copy-Item -LiteralPath (Join-Path $source 'codex-rs\target\release\codex.exe') `
        -Destination (Join-Path $destinationPath 'bin\codex.exe')
    foreach ($name in @('integration.json', 'README.md', 'LICENSE', 'THIRD_PARTY-NOTICES.md', 'ctxnorm-codex.ps1', 'uninstall.ps1', 'verify.ps1')) {
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination $destinationPath
    }
    @{
        schema_version = 1
        component = 'codex'
        version = [string]$manifest.version
        upstream_commit = [string]$manifest.upstream_commit
        destination = $destinationPath
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $destinationPath '.ctxnorm-codex-install.json') -Encoding UTF8
} catch {
    if ($created -and (Test-Path -LiteralPath $destinationPath)) {
        @(
            'bin\codex.exe', 'integration.json', 'README.md', 'LICENSE', 'THIRD_PARTY-NOTICES.md', 'ctxnorm-codex.ps1',
            'uninstall.ps1', 'verify.ps1', '.ctxnorm-codex-install.json'
        ) | ForEach-Object {
            Remove-Item -LiteralPath (Join-Path $destinationPath $_) -Force -ErrorAction SilentlyContinue
        }
        Remove-Item -LiteralPath (Join-Path $destinationPath 'bin') -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $destinationPath -ErrorAction SilentlyContinue
    }
    throw
} finally {
    if (Test-Path -LiteralPath $temporaryRoot) {
        $resolvedTemporary = [IO.Path]::GetFullPath($temporaryRoot)
        $expectedPrefix = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
        if (-not $resolvedTemporary.StartsWith($expectedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refused unexpected temporary cleanup path: $resolvedTemporary"
        }
        Remove-Item -LiteralPath $resolvedTemporary -Recurse -Force
    }
}
& (Join-Path $destinationPath 'verify.ps1')
Write-Host "Codex integration installed at $destinationPath"
