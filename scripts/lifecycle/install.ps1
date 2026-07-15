[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$pythonCommand = Get-Command py -ErrorAction SilentlyContinue
$pythonArgs = @('-3')
if (-not $pythonCommand) {
    $pythonCommand = Get-Command python -ErrorAction Stop
    $pythonArgs = @()
}

function Invoke-InstallerPython {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FailureMessage,
        [string]$Executable = $pythonCommand.Source,
        [string[]]$ExecutableArguments = $pythonArgs,
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$Arguments
    )

    # Windows PowerShell converts native stderr (including harmless pip warnings)
    # into error records. Temporarily use Continue and decide success only from the
    # process exit code so a warning cannot strand a partial installation.
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Executable @ExecutableArguments @Arguments
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($exitCode -ne 0) { throw "$FailureMessage`: $exitCode" }
}

$existingPackage = (& $pythonCommand.Source @pythonArgs -c "import importlib.metadata as m; print(m.version('context-normalizer') if 'context_normalizer' in m.packages_distributions() else 'absent')").Trim()
if ($LASTEXITCODE -ne 0) { throw 'Could not inspect existing package state.' }
if ($existingPackage -ne 'absent') {
    throw "A context-normalizer package is already installed ($existingPackage). This installer is for fresh installs only."
}
$installConfig = if ($env:CONTEXT_NORMALIZER_HOME) {
    [IO.Path]::GetFullPath($env:CONTEXT_NORMALIZER_HOME)
} else {
    Join-Path $HOME '.context-normalizer'
}
if (Test-Path -LiteralPath $installConfig) {
    throw "Configuration already exists at $installConfig. It was not changed."
}

$packageInstalled = $false
try {
    $temporaryBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    $buildRoot = Join-Path $temporaryBase ('ctxnorm-build-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $buildRoot | Out-Null
    $resolvedBuild = (Resolve-Path -LiteralPath $buildRoot).Path
    if (-not $resolvedBuild.StartsWith($temporaryBase, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Unsafe temporary build path: $resolvedBuild"
    }
    try {
        Invoke-InstallerPython -FailureMessage 'Temporary build environment creation failed' -Arguments @(
            '-m', 'venv', (Join-Path $resolvedBuild 'venv')
        )
        $buildPython = Join-Path $resolvedBuild 'venv\Scripts\python.exe'
        Invoke-InstallerPython -FailureMessage 'Locked build-tool installation failed' `
            -Executable $buildPython -ExecutableArguments @() -Arguments @(
                '-m', 'pip', 'install', '--require-hashes', '--only-binary=:all:',
                '-r', (Join-Path $repositoryRoot 'requirements\build.lock')
            )
        $previousNoIndex = $env:PIP_NO_INDEX
        $previousVersionCheck = $env:PIP_DISABLE_PIP_VERSION_CHECK
        try {
            $env:PIP_NO_INDEX = '1'
            $env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
            Invoke-InstallerPython -FailureMessage 'Locked wheel build failed' `
                -Executable $buildPython -ExecutableArguments @() -Arguments @(
                    '-m', 'build', '--wheel', '--no-isolation', '--outdir',
                    (Join-Path $resolvedBuild 'wheel'), $repositoryRoot
                )
        } finally {
            $env:PIP_NO_INDEX = $previousNoIndex
            $env:PIP_DISABLE_PIP_VERSION_CHECK = $previousVersionCheck
        }
        $wheels = @(Get-ChildItem -LiteralPath (Join-Path $resolvedBuild 'wheel') -Filter '*.whl')
        if ($wheels.Count -ne 1) { throw "Expected one built wheel, found $($wheels.Count)." }
        Invoke-InstallerPython -FailureMessage 'Package installation failed' -Arguments @(
            '-m', 'pip', 'install', '--user', '--no-deps', $wheels[0].FullName
        )
        $packageInstalled = $true
    } finally {
        if ((Test-Path -LiteralPath $resolvedBuild) -and
            $resolvedBuild.StartsWith($temporaryBase, [StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $resolvedBuild -Recurse -Force
        }
    }

    $initArguments = @('-m', 'context_normalizer', 'init')
    Invoke-InstallerPython -FailureMessage 'Configuration initialization failed' -Arguments $initArguments

    if ($env:CONTEXT_NORMALIZER_TEST_FAIL_AFTER_INIT -eq '1') {
        throw 'Injected post-init failure for lifecycle acceptance testing.'
    }

    $doctorArguments = @('-m', 'context_normalizer', 'doctor')
    Invoke-InstallerPython -FailureMessage 'Installation verification failed' -Arguments $doctorArguments
} catch {
    $originalError = $_
    $rollbackErrors = @()
    $marker = Join-Path $installConfig 'installation.json'
    if ($packageInstalled) {
        if (Test-Path -LiteralPath $marker -PathType Leaf) {
            try {
                Invoke-InstallerPython -FailureMessage 'Configuration rollback failed' -Arguments @(
                    '-m', 'context_normalizer', 'purge', '--yes'
                )
            } catch {
                $rollbackErrors += $_.Exception.Message
            }
        } elseif (Test-Path -LiteralPath $installConfig) {
            $entries = @(Get-ChildItem -LiteralPath $installConfig -Force)
            if ($entries.Count -eq 0) {
                Remove-Item -LiteralPath $installConfig
            } else {
                $rollbackErrors += "Unmarked partial configuration was retained: $installConfig"
            }
        }
        if ($rollbackErrors.Count -eq 0) {
            try {
                Invoke-InstallerPython -FailureMessage 'Package rollback failed' -Arguments @(
                    '-m', 'pip', 'uninstall', '--yes', 'context-normalizer'
                )
                $packageInstalled = $false
            } catch {
                $rollbackErrors += $_.Exception.Message
            }
        }
    }
    if ($rollbackErrors.Count) {
        throw "$($originalError.Exception.Message) Rollback was incomplete: $($rollbackErrors -join '; ')"
    }
    throw $originalError
}

Write-Host ''
Write-Host 'Installed. Normalize a draft with:'
Write-Host '  py -3 -m context_normalizer normalize prompt.md --output prompt.normalized.md --audit prompt.audit.json --preview'
Write-Host ''
Write-Host 'Edit your rules under:'
$displayConfig = if ($env:CONTEXT_NORMALIZER_HOME) {
    [IO.Path]::GetFullPath($env:CONTEXT_NORMALIZER_HOME)
} else {
    Join-Path $HOME '.context-normalizer'
}
Write-Host "  $displayConfig"
