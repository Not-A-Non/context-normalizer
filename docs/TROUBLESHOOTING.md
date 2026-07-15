# Troubleshooting

## `ctxnorm` is unavailable

Confirm the core installation directory is on `PATH`, then run:

```text
python -m context_normalizer --version
ctxnorm doctor
```

## Integration remains inactive

Confirm the current directory is the managed workspace returned by `ctxnorm workspace create`. The workspace or one of its parents must contain `.ctxnorm-workspace.json`.

## Path vocabulary is rejected

Run:

```text
ctxnorm vocabulary validate --bidirectional --format json
```

Resolve duplicate source entries, duplicate normalized entries, empty fields, and path separator characters.

## Workspace has conflicts

Inspect before applying any changes:

```text
ctxnorm workspace status WORKSPACE --format json
ctxnorm workspace conflicts WORKSPACE --format json
ctxnorm workspace plan WORKSPACE --format json
```

Resolve the reported source and normalized workspace changes, then use `workspace recover` or `workspace apply` as indicated by the plan.

## Codex build fails

Run the verifier from `integrations/codex`. Confirm Git, Cargo, the pinned Rust toolchain, and the platform C/C++ build tools are installed. The installer requires the exact upstream revision in `integration.json`.

## Pi does not load the extension

Run the Pi component verifier and inspect Pi's installed package list. Reinstall only the Pi component if registration is missing.
