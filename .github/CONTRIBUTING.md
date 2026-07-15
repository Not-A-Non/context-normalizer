# Contributing

## Setup

Create a virtual environment, install the exact build requirements, and install this project without dependency resolution:

```text
python -m venv .venv
python -m pip install --require-hashes --only-binary=:all: -r requirements/build.lock
python -m pip install --no-deps --no-build-isolation -e .
```

## Required checks

```text
python scripts/check_public_language.py
python scripts/check_docs.py
python scripts/audit_public_tree.py
python -m unittest discover -s tests -v
node integrations/pi/test/context-normalizer.test.mjs
python -m ruff check src tests scripts
python -m build --no-isolation
python scripts/verify_release.py dist
```

## Change requirements

- Keep public scope limited to vocabulary and context normalization.
- Add regression coverage for each changed normalization surface.
- Preserve dependency-free core runtime operation unless discussed first.
- Keep path normalization explicitly bidirectional and collision-free.
- Keep client components independently installable.
- Pin and verify upstream source used by the Codex component.
- Update compatibility documentation and `CHANGELOG.md`.

## Pull requests

Describe scope, compatibility impact, tests, and documentation changes. Confirm that credentials, sessions, private paths, personal vocabulary, prompts, workspace data, and build output are absent. Submit security reports through the private process.
