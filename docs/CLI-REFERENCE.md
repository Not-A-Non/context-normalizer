# CLI reference

## General

```text
ctxnorm --version
ctxnorm COMMAND --help
```

Commands that change configuration or workspaces require confirmation where indicated. JSON output is intended for automation.

## Core normalization

`ctxnorm normalize [INPUT]` reads a UTF-8 file or standard input. `--output PATH` writes normalized text. `--audit PATH` writes a normalization receipt. `--rules PATH` and `--context PATH` select explicit files. `--no-context` omits configured context. `--preview` prints the planned result.

`ctxnorm clipboard` previews clipboard normalization. `--yes` confirms the clipboard update. `--audit PATH` writes a receipt. `--no-context` omits configured context.

## Initialization and verification

`ctxnorm init` creates missing configuration. `--force` refreshes core-owned configuration files.

`ctxnorm doctor` validates configuration and vocabulary.

`ctxnorm capabilities` returns supported normalization surfaces as JSON.

`ctxnorm purge --yes` removes validated core-owned configuration. Client components have separate uninstallers.

## Vocabulary

`ctxnorm vocabulary list` supports `--format table|json|tsv`.

`ctxnorm vocabulary find QUERY` searches source and normalized vocabulary.

`ctxnorm vocabulary validate` validates format, uniqueness, and identity.

`ctxnorm vocabulary path` prints the selected vocabulary path.

`ctxnorm vocabulary add SOURCE NORMALIZED` adds an entry. `--update` updates an existing source entry.

`ctxnorm vocabulary remove SOURCE` removes an entry.

Vocabulary commands accept `--rules PATH`, `--bundled`, or `--bidirectional` as applicable. Mutation commands accept `--expect-sha256 HASH` for guarded automation.

## Profiles

`ctxnorm profiles list`, `show NAME`, and `active` support JSON output.

`ctxnorm profiles apply NAME --mode merge|reset --yes` activates a profile. `--expect-sha256 HASH` guards the active configuration identity.

## Configuration

`ctxnorm config list`, `show NAME`, and `path NAME` inspect active configuration without changing it. Use `--format json` for automation.

## Workspaces

`ctxnorm workspace create SOURCE --normalize-paths --yes` creates a managed normalized workspace. `--mode auto|git|filesystem`, `--root PATH`, and `--format json` control creation.

`ctxnorm workspace status WORKSPACE` reports current state.

`ctxnorm workspace verify WORKSPACE` verifies ownership, state, and content identity.

`ctxnorm workspace plan WORKSPACE --direction model|source` calculates a synchronization plan. `--output PATH` saves it.

`ctxnorm workspace conflicts WORKSPACE --direction model|source` reports divergent changes.

`ctxnorm workspace apply WORKSPACE --plan PATH --yes` applies a verified plan. `--sha256 HASH` supplies an explicit plan identity.

`ctxnorm workspace recover WORKSPACE --action resume|rollback --yes` resolves an interrupted transaction.

`ctxnorm workspace close WORKSPACE --yes` closes a workspace. `--no-archive` omits the managed closed copy.

`ctxnorm workspace cleanup WORKSPACE_ID --yes` deletes validated closed workspace records.

`WORKSPACE` may be an absolute managed workspace path or its generated identifier.

## Local API relay

`ctxnorm relay serve` starts a loopback HTTP relay that normalizes outbound message bodies and translates inbound assistant text. `--port PORT` selects the local port (default 8377), `--workspace WORKSPACE` enables per-turn workspace synchronization, and `--upstream URL` overrides the upstream API origin.

`ctxnorm relay run --workspace WORKSPACE -- COMMAND [ARGS...]` starts an ephemeral relay, sets `ANTHROPIC_BASE_URL` for the child process, runs the command inside the normalized workspace, and exits with the command's result code.

Behavior and limits are documented in [Local API relay](API-RELAY.md).

## Integration interface

`ctxnorm bridge` is reserved for installed Codex and Pi components. Its contract is documented in [Client integration](CLIENT-INTEGRATION.md). Automation outside a client component should use the public core and workspace commands.
