# Capabilities and limits

## Capabilities

Context Normalizer provides deterministic normalization for:

- prompts and model context;
- complete assistant and tool-result text;
- displayed command text and completed command output;
- filenames, folder names, and path segments;
- UTF-8 workspace content;
- completed workspace changes synchronized into the source workspace;
- network-boundary normalization through the local API relay for clients that honor a configurable API base address.

Normalization uses explicit local vocabulary. Longest matches win in a single pass, so normalized output is never processed again during that pass.

## Limits

- Broad context vocabulary is one-directional.
- Path and workspace synchronization require explicit bidirectional vocabulary.
- Binary file content remains unchanged.
- Files that are not valid UTF-8 remain unchanged.
- Streaming display waits for a complete text item when partial text could expose an incomplete vocabulary match.
- Client integrations activate only in a managed normalized workspace.
- A workspace conflict requires explicit review and recovery.
- Workspace normalization refuses content or path segments that do not round-trip, including source content that already contains a normalized vocabulary value.
- Workspace status and planning reuse cached content hashes for files whose size and nanosecond modification time are unchanged; `workspace verify` ignores the cache and re-hashes every file.
- `normalize` preserves input bytes (including CRLF line endings), and the audit `original_sha256` covers the input exactly as read.
- Uniform casing (UPPERCASE, Capitalized, and the exact vocabulary casing) is preserved and reversible; other mixed casings normalize to the vocabulary casing and are rejected by the workspace round-trip check.
- The Codex component supports only the upstream revision recorded in `integration.json`.
- The relay rewrites only message and token-count bodies; batch submissions and other endpoints pass through unchanged.
- The relay buffers each streamed text block and emits it as one delta when the block completes; signed reasoning blocks are never rewritten.
- A relay turn is refused when both the source and the normalized workspace changed since the last synchronization; the conflict requires explicit review.
