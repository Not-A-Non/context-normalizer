# Codex integration

This independently installed component builds a version-pinned Codex CLI with vocabulary and context normalization inside a Context Normalizer workspace.

It remains inactive in ordinary directories. In a marked normalized workspace it normalizes submissions before model context is created, normalizes complete assistant and command text for display, and normalizes completed workspace changes into the source workspace.

## Requirements

- Context Normalizer core 1.0 or later
- Git
- The Rust toolchain required by the pinned Codex source
- Platform C/C++ build tools required by Codex

The upstream repository, tag, and commit are fixed in `integration.json`.

## Install on Windows

Open PowerShell in this component directory:

```powershell
.\install.ps1
.\verify.ps1
```

## Install on Linux or macOS

```sh
./install.sh
./verify.sh
```

The installer verifies the exact upstream commit, applies the reviewed source patch, runs focused tests, builds a release executable, and installs the dedicated component under the current user home directory. A standard Codex installation remains independent.

The pinned upstream release records `0.144.1` in workspace manifests and `0.0.0` in local workspace entries of its committed Cargo lockfile. Cargo refreshes those local workspace version fields in the temporary checkout. Third-party dependency selections remain lockfile-controlled, and the temporary checkout is deleted after the build.

## Codex sign-in

Use the installed component launcher with the standard Codex login command.

Windows:

```powershell
& "$HOME\.context-normalizer\integrations\codex\ctxnorm-codex.ps1" login
```

Linux or macOS:

```sh
"$HOME/.context-normalizer/integrations/codex/ctxnorm-codex" login
```

## Configure normalization

Tune core vocabulary before creating a workspace:

```text
ctxnorm vocabulary add SOURCE NORMALIZED
ctxnorm vocabulary add SOURCE NORMALIZED --bidirectional
ctxnorm vocabulary validate --format json
```

Bidirectional vocabulary governs paths, filenames, folder names, displayed text, and round-trip UTF-8 workspace content.

## Run

Create a normalized workspace and change into the returned `mirror` directory:

```text
ctxnorm workspace create PATH --normalize-paths --format json --yes
```

Start the installed launcher.

Windows:

```powershell
& "$HOME\.context-normalizer\integrations\codex\ctxnorm-codex.ps1"
```

Linux or macOS:

```sh
"$HOME/.context-normalizer/integrations/codex/ctxnorm-codex"
```

## Verify or uninstall

Run `verify.ps1` or `verify.sh` from the installed component. Run `uninstall.ps1` or `uninstall.sh` to remove only this component. Core configuration, vocabulary, normalized workspaces, and independent Codex installations remain separate.

## Upstream documentation

- [Codex CLI](https://developers.openai.com/codex/cli)
- [Codex commands](https://learn.chatgpt.com/docs/developer-commands)

## License

Context Normalizer integration files are MIT licensed. See [LICENSE](LICENSE) and [third-party notices](THIRD_PARTY-NOTICES.md).
