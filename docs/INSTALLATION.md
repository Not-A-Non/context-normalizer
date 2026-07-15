# Installation

## Component model

Install the core first. Install exactly one or both client integrations separately. Each component has its own installer, verifier, and uninstaller.

## Core on Windows

Run PowerShell from the release root:

```powershell
.\scripts\lifecycle\install.ps1
ctxnorm doctor
```

The default configuration directory is `.context-normalizer` in the current user home directory. Use the installer help for supported destination options.

## Core on Linux or macOS

```sh
./scripts/lifecycle/install.sh
ctxnorm doctor
```

## Python package

Install a release wheel into an isolated environment or managed Python installation:

```text
python -m pip install context-normalizer-1.0.0-py3-none-any.whl
ctxnorm init
ctxnorm doctor
```

The runtime has no third-party Python dependency.

## Codex integration

Follow [the Codex component instructions](../integrations/codex/README.md). Its installer builds the pinned upstream revision and installs a dedicated executable. A standard Codex installation is unchanged.

## Pi integration

Follow [the Pi component instructions](../integrations/pi/README.md). Its installer registers only the Context Normalizer Pi package.

## Upgrade

1. Verify and close active normalized workspaces.
2. Run the component uninstaller.
3. Install the new component release.
4. Run the component verifier.
5. Run `ctxnorm doctor`.

User vocabulary remains in the core configuration unless the core purge command is explicitly used.
