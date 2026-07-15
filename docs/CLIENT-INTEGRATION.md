# Client integration contract

## Activation

An integration activates only when the current directory or a parent directory contains a valid `.ctxnorm-workspace.json` marker. Ordinary client sessions remain unchanged.

## Submission

Before model context is created, the integration submits the complete user input to `ctxnorm bridge submit`. The returned text and context become the client submission. A nonzero bridge result blocks that normalized submission.

## Display

The integration sends complete assistant, tool-result, command, and command-output text to `ctxnorm bridge normalize --direction display` before rendering it. Integrations buffer any stream whose partial text could divide a vocabulary match.

## Completion

After a final assistant item completes successfully, the integration calls `ctxnorm bridge complete`. The core evaluates workspace changes, records conflicts, and synchronizes accepted changes.

## Data ownership

The core owns vocabulary and workspace state. The client owns authentication, model selection, network transport, and session storage. Integrations do not read, write, copy, or document client credentials.

## Component support

The Codex component is source-integrated at a pinned revision. The Pi component follows Pi's extension contract. See each component README for supported versions and verification.

## Network-boundary alternative

Clients that honor a configurable API base address can run without an installed integration through the [local API relay](API-RELAY.md). The relay applies the same submission, display, and completion semantics at the network boundary: outbound bodies are normalized, inbound assistant text is translated for display, and each turn synchronizes the workspace. Signed reasoning blocks pass through unchanged in both directions.
