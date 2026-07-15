# Architecture

## Core

The Python core owns vocabulary parsing, deterministic text normalization, normalized workspace lifecycle, conflict detection, synchronization, and the `ctxnorm` CLI. Its runtime uses the Python standard library only.

## Workspace boundary

A managed normalized workspace contains `.ctxnorm-workspace.json`. Client integrations activate only after finding that marker. The marker identifies the source workspace, vocabulary identity, path vocabulary, ownership, and synchronization state.

Workspace creation copies supported files into a managed workspace. Path normalization is optional and requires explicit bidirectional vocabulary. Binary content is copied unchanged. Supported UTF-8 content is normalized.

## Integration boundary

Client integrations call a narrow core bridge:

- submission normalization before model context is created;
- complete-message normalization before display;
- completed-turn workspace synchronization.

The bridge is hidden from the general CLI help because it is an integration interface. It never owns client authentication or client configuration.

## Components

The Codex integration is a reviewed patch against one exact upstream tag and commit. The Pi integration uses Pi's extension lifecycle. Neither component is installed by the core installer.

## Failure behavior

An integration blocks a normalized submission when the core bridge fails. Outside a marked workspace, the integration remains inactive. Workspace synchronization records conflicts and requires explicit recovery when source and normalized states diverge.
