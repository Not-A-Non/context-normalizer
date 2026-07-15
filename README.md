# Context Normalizer

Context Normalizer provides deterministic vocabulary and context normalization for coding clients. It normalizes prompts, model context, displayed text, command text, paths, filenames, folder names, and UTF-8 workspace content from explicit local vocabulary files.

The project is a monorepo with three independently installed components:

- `context-normalizer`: dependency-free Python core and `ctxnorm` CLI.
- `integrations/codex`: version-pinned Codex integration built from reviewed source.
- `integrations/pi`: Pi extension package.

Each integration remains inactive outside a workspace containing `.ctxnorm-workspace.json`.

## Requirements

- Python 3.10 or later for the core package.
- Git for Git workspace mode.
- Component-specific requirements listed in each integration README.

## Install the core

From a release archive on Windows:

```powershell
.\scripts\lifecycle\install.ps1
```

On Linux or macOS:

```sh
./scripts/lifecycle/install.sh
```

For Python package installation:

```text
python -m pip install context-normalizer-1.0.0-py3-none-any.whl
ctxnorm init
ctxnorm doctor
```

The core installer does not install a client integration. Install Codex or Pi from its own directory after the core passes `ctxnorm doctor`.

## Configure vocabulary

The standard vocabulary is stored in `rules.tsv`. A line contains source vocabulary, one tab, and normalized vocabulary.

```text
source vocabulary<TAB>normalized vocabulary
```

Inspect and tune it through the CLI:

```text
ctxnorm vocabulary list --format json
ctxnorm vocabulary find "source vocabulary" --format json
ctxnorm vocabulary add "source vocabulary" "normalized vocabulary"
ctxnorm vocabulary validate --format json
```

Paths and round-trip workspace content require an explicit bidirectional entry in `path-rules.tsv`:

```text
ctxnorm vocabulary add "source-name" "normalized-name" --bidirectional
```

The broad context vocabulary is one-directional. The bidirectional path vocabulary must be unambiguous in both directions.

## Create a normalized workspace

```text
ctxnorm workspace create PATH --normalize-paths --format json --yes
```

Change into the returned workspace directory and start an installed client integration. During the session, vocabulary and context remain normalized across prompts, model context, display text, commands, paths, and UTF-8 files. On a completed turn, accepted workspace changes are normalized into the source workspace.

Inspect workspace state at any time:

```text
ctxnorm workspace status WORKSPACE --format json
ctxnorm workspace verify WORKSPACE --format json
ctxnorm workspace conflicts WORKSPACE --format json
```

Close and clean a managed workspace:

```text
ctxnorm workspace close WORKSPACE --yes
ctxnorm workspace cleanup WORKSPACE --yes
```

## Install a client integration

See [Codex installation](integrations/codex/README.md) or [Pi installation](integrations/pi/README.md). Their installers, verification commands, and uninstallers are separate.

Clients that honor the `ANTHROPIC_BASE_URL` environment variable need no installed integration: the [local API relay](docs/API-RELAY.md) normalizes traffic at the network boundary.

```text
ctxnorm relay run --workspace WORKSPACE -- CLIENT-COMMAND
```

## Documentation

- [Installation](docs/INSTALLATION.md)
- [Configuration](docs/CONFIGURATION.md)
- [CLI reference](docs/CLI-REFERENCE.md)
- [Local API relay](docs/API-RELAY.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Client integration contract](docs/CLIENT-INTEGRATION.md)
- [Capabilities and limits](docs/CAPABILITIES-AND-LIMITS.md)
- [Operations](docs/OPERATIONAL-BOUNDARIES.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Security policy](.github/SECURITY.md)
- [Release process](docs/RELEASING.md)

## License

MIT. See [LICENSE](LICENSE).

See [third-party notices](THIRD_PARTY-NOTICES.md) for client component attribution.
