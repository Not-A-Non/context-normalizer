# Agent guidelines

## Product scope

Context Normalizer normalizes vocabulary and context. Public documentation, release metadata, issue templates, and user-facing messages must describe only that scope.

The core, Codex integration, and Pi integration are independent installable components. Do not create an installer that installs more than one component.

## Safe inspection

Agents may run these read-only commands without changing configuration:

```text
ctxnorm vocabulary list --format json
ctxnorm vocabulary find QUERY --format json
ctxnorm vocabulary validate --format json
ctxnorm vocabulary path
ctxnorm profiles list --format json
ctxnorm profiles show NAME --format json
ctxnorm profiles active --format json
ctxnorm config list --format json
ctxnorm capabilities
ctxnorm doctor
```

Treat `source`, `sha256`, `tool_version`, and `schema_version` as vocabulary identity. Query the active configuration instead of relying on memory.

## State changes

Obtain user authorization before changing vocabulary, applying profiles, creating or synchronizing workspaces, initializing with `--force`, purging configuration, or running an installer.

## Implementation invariants

- Normalize configured text and path segments only.
- Preserve protected spans defined by the core normalizer.
- Use one longest-match pass. Normalizations must never cascade.
- Keep broad context vocabulary one-directional.
- Require explicit, collision-free bidirectional vocabulary for paths.
- Keep integrations inactive outside a marker-owned normalized workspace.
- Keep the standard runtime dependency-free.
- Limit cleanup to fixed component paths or validated owned workspaces.
- Add regression tests for every normalization surface and protected construct.

## Required gates

Before completion:

1. Run `python -m unittest discover -s tests -v`.
2. Run Pi integration tests.
3. Run public-language and public-tree audits.
4. Build the sdist and wheel.
5. Verify archive contents and install the wheel in isolation.
6. Run Linux verification in Agent Sandbox when available.
7. Verify the Codex source patch against the pinned upstream revision.
8. Update `CHANGELOG.md` for user-visible changes.

Publication, tags, and releases require explicit user approval.
