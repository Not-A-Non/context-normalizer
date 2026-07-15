# Configuration

## Files

`ctxnorm init` creates editable configuration in the current user's Context Normalizer directory:

- `rules.tsv`: one-directional context vocabulary.
- `path-rules.tsv`: bidirectional path and workspace-content vocabulary.
- `context.txt`: context included during normalization.
- `subagent-context.txt`: context for supported delegated sessions.
- `cues.txt`: active vocabulary cues.

Use `ctxnorm config list --format json` to resolve the active paths on any platform.

## Vocabulary format

Each active line contains source vocabulary, a tab, and normalized vocabulary. Empty lines and lines beginning with `#` are ignored.

```text
source vocabulary<TAB>normalized vocabulary
```

Use CLI mutation when possible because it validates duplicates, conflicts, and expected vocabulary identity:

```text
ctxnorm vocabulary add SOURCE NORMALIZED
ctxnorm vocabulary add SOURCE NORMALIZED --bidirectional
ctxnorm vocabulary remove SOURCE
ctxnorm vocabulary validate --format json
```

Use `--expect-sha256 HASH` for guarded automation.

## Profiles

Profiles provide versioned vocabulary sets:

```text
ctxnorm profiles list --format json
ctxnorm profiles show software-writing-expansions --format json
ctxnorm profiles apply software-writing-expansions --mode reset --yes
```

Use `--mode merge` to retain existing entries. Use `--mode reset` to activate only the selected profile.

## Path vocabulary

Path normalization runs only when workspace creation includes `--normalize-paths`. Every normalized path segment must have one unique source entry and one unique normalized entry. Validation rejects ambiguous pairs before workspace mutation.
