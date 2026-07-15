# Pi integration

This independently installed Pi extension provides vocabulary and context normalization inside a Context Normalizer workspace.

It remains inactive in ordinary directories. In a marked normalized workspace it normalizes submissions, model context, complete messages, tool results, paths, filenames, folder names, and UTF-8 workspace content. Completed workspace changes are normalized into the source workspace.

## Requirements

- Context Normalizer core 1.0 or later
- Pi 0.80 or later
- Node.js 20 or later

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

The installer copies this component to the fixed current-user integration directory and registers it with Pi. It does not install or configure the core or Codex component.

## Configure normalization

Tune core vocabulary before creating a workspace:

```text
ctxnorm vocabulary add SOURCE NORMALIZED
ctxnorm vocabulary add SOURCE NORMALIZED --bidirectional
ctxnorm vocabulary validate --format json
```

Bidirectional vocabulary governs paths, filenames, folder names, displayed text, and round-trip UTF-8 workspace content.

## Run

```text
ctxnorm workspace create PATH --normalize-paths --format json --yes
```

Change into the returned `mirror` directory and start Pi normally. The extension activates from the workspace marker.

## Verify or uninstall

Run `verify.ps1` or `verify.sh` from the installed component. Run `uninstall.ps1` or `uninstall.sh` to unregister and remove only the Pi component. Core configuration, vocabulary, and normalized workspaces remain separate.

## License

MIT. See [LICENSE](LICENSE) and [third-party notices](THIRD_PARTY-NOTICES.md).
