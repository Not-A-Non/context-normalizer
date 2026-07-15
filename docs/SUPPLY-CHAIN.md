# Supply-chain policy

## Core package

The runtime uses only the Python standard library. Build tools are exactly pinned with hashes in `requirements/build.lock`. CI verifies dependency age and policy before installing build requirements.

Release workflows pin GitHub Actions by full commit SHA. The release job fixes archive timestamps, builds the wheel and source archive, verifies their contents, generates SHA-256 checksums, and publishes build provenance.

## Codex component

`integrations/codex/integration.json` pins the upstream repository, tag, and commit. The installer verifies the commit, applies the reviewed patch, runs focused tests, and builds locally. The installed component contains the executable and component metadata only.

The pinned upstream release has different manifest and lockfile versions for local workspace packages. Cargo refreshes only those local workspace version fields in the temporary checkout. Third-party selections remain lockfile-controlled. The checkout is deleted after installation.

## Pi component

The Pi package has no runtime package dependency. Its release archive is produced from the reviewed integration directory and verified by Node tests before publication.

## Public-tree audit

Release gates reject personal filesystem paths, private network addresses, secret-shaped values, private-key blocks, authentication artifacts, session files, logs, generated workspaces, build directories, and oversized files.
