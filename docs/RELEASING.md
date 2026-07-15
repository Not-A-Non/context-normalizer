# Releasing

## Versioning

Use semantic versioning. Keep the core version in `src/context_normalizer/__init__.py`, the Pi version in `integrations/pi/package.json`, and component metadata aligned for a coordinated release.

## Required verification

From a clean checkout:

```text
python scripts/check_public_language.py
python scripts/check_docs.py
python scripts/audit_public_tree.py
python scripts/check_supply_chain_policy.py --online
python -m unittest discover -s tests -v
node integrations/pi/test/context-normalizer.test.mjs
python -m build --no-isolation
python scripts/verify_release.py dist
```

Run shell syntax validation, PowerShell parser validation, Windows lifecycle acceptance, Linux Agent Sandbox acceptance, and the Codex pinned-source verifier.

## Artifacts

A release contains separate artifacts:

- core Python wheel;
- core source archive;
- Pi component package;
- Codex component integration package;
- `SHA256SUMS.txt`.

No installer may install another component implicitly.

## Publication

1. Review the complete staged tree and audit output.
2. Confirm the version and changelog.
3. Create an annotated signed tag when signing is available.
4. Allow the tag workflow to build artifacts from source.
5. Verify checksums and provenance on the GitHub release.

Do not publish from a working tree containing uncommitted files or generated credentials, sessions, logs, build outputs, or workspaces.
