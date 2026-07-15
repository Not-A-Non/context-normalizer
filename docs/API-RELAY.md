# Local API relay

The relay is a loopback HTTP service that applies context normalization at the network boundary. It supports any client that honors a configurable API base address, such as the `ANTHROPIC_BASE_URL` environment variable. No client source changes are required.

## What the relay does

- Outbound message bodies are normalized before they leave the machine: system text, user text, assistant history text, tool-result text, and text document sources.
- Inbound assistant text is translated back to the local vocabulary before the client renders it, for complete responses and for event streams.
- With `--workspace`, each conversation turn synchronizes the normalized workspace: completed normalized-side changes flow back to the source, then source edits flow forward.
- Signed reasoning blocks are never rewritten in either direction, because the upstream verifies them byte for byte.
- On any normalization or synchronization failure the request is refused with an API-shaped error. Unprocessed text is never forwarded.

## Serve

```text
ctxnorm relay serve [--port 8377] [--workspace WORKSPACE] [--upstream URL]
```

Prints the local address as JSON and serves until interrupted. The relay binds to the loopback interface only. `--upstream` defaults to the standard API origin.

## Run a client

```text
ctxnorm relay run --workspace WORKSPACE -- CLIENT-COMMAND [ARGS...]
```

Starts an ephemeral relay, sets `ANTHROPIC_BASE_URL` for the child process, runs the client command inside the normalized workspace, and shuts the relay down when the client exits. The relay result code is the client result code.

Example with a coding client that honors `ANTHROPIC_BASE_URL`:

```text
ctxnorm workspace create /path/to/source --normalize-paths --yes
ctxnorm relay run --workspace WORKSPACE-ID -- CLIENT-COMMAND
```

## Scope and limits

- Only `POST /v1/messages` and `POST /v1/messages/count_tokens` bodies are rewritten. Batch submissions and all other endpoints pass through unchanged.
- Event streams buffer each text block and emit it as one delta when the block completes, because partial text could divide a vocabulary match. Non-text events keep their original order and timing.
- The client owns authentication. Credential headers pass through to the upstream and are never read, stored, or logged by the relay.
- Client user interfaces render text after inbound translation; text the client persists locally (session history) contains the local vocabulary and is re-normalized on the next request.
- When both the source and the normalized workspace changed since the last synchronization, the turn is refused and the conflict requires explicit review (`ctxnorm workspace conflicts`, `ctxnorm workspace recover`).
