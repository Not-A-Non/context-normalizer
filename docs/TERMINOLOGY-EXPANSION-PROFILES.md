# Vocabulary profiles

Profiles are versioned vocabulary and cue sets distributed with the core package.

## Included profiles

- `software-writing-expansions`
- `life-science-writing-expansions`
- `security-writing-expansions`
- `gpu-compiler`

Inspect profiles before activation:

```text
ctxnorm profiles list --format json
ctxnorm profiles show PROFILE --format json
ctxnorm profiles active --format json
```

Merge a profile into active vocabulary:

```text
ctxnorm profiles apply PROFILE --mode merge --yes
```

Reset active vocabulary to a profile:

```text
ctxnorm profiles apply PROFILE --mode reset --yes
```

Profile manifests include counts and SHA-256 identity. Local vocabulary tuning remains available after profile activation.
