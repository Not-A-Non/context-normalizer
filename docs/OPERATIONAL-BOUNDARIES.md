# Operational boundaries

## Ownership

Core configuration, component installations, and managed workspaces carry explicit marker files. Cleanup validates marker schema, component identity, ownership, expected location, and workspace state before deleting managed data.

## Workspace safety

- Source workspaces are never used as component installation directories.
- Managed workspaces are created under the configured workspace root.
- Synchronization plans are calculated before source mutation.
- Concurrent source and normalized changes produce a conflict record.
- Cleanup refuses an unvalidated, dirty, active, or unexpected workspace.

## Configuration safety

Vocabulary mutation supports expected SHA-256 identity for guarded automation. Profile activation creates a recoverable configuration state. `purge` requires confirmation and removes only core-owned configuration.

## Client isolation

Each client component installs separately. Client authentication and session data remain owned by that client. Component uninstallers validate their fixed destination and marker before deleting component files.

## Logging

Release packages contain no personal paths, addresses, secrets, authentication artifacts, session histories, build logs, or generated workspaces. Public-tree auditing enforces these boundaries before release.
