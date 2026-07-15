# Changelog

This project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and semantic versioning.

## 1.0.0 - 2026-07-15

Initial public release.

### Added

- Dependency-free Python core for deterministic vocabulary and context normalization.
- CLI configuration, validation, vocabulary tuning, profile activation, and machine-readable manifests.
- Explicit bidirectional vocabulary for path, filename, folder-name, and workspace-content normalization.
- Managed normalized workspaces with planning, conflict detection, synchronization, recovery, verification, and ownership-bound cleanup.
- Local API relay (`ctxnorm relay serve` and `ctxnorm relay run`) that normalizes outbound message bodies, translates inbound assistant text for display, and synchronizes the workspace each turn for clients that honor a configurable API base address.
- Source-integrated Codex component pinned to an exact upstream tag and commit.
- Pi extension component using Pi session lifecycle events.
- Independent installers, verifiers, and uninstallers for the core, Codex component, and Pi component.
- Cross-platform unit, integration, lifecycle, archive, supply-chain, public-language, and public-tree verification.
- Separate release artifacts and SHA-256 checksums for each component.
- Workspace status, planning, and bridge operations reuse cached content hashes for files whose size and modification time are unchanged; `workspace verify` always re-hashes every file.
