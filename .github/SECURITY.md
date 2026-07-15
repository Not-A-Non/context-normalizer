# Security policy

## Supported versions

Security fixes are provided for the latest tagged release. Upgrade to the newest patch before reporting an issue.

## Private reporting

Do not include credentials, private prompts, vocabulary, workspace content, personal paths, or client configuration in a public issue. Use GitHub's private vulnerability reporting feature for this repository. If private reporting is unavailable, request a private reporting channel before sharing details.

Include the affected component and version, operating system, client version, minimal reproduction, impact, and redacted diagnostics. The acknowledgement target is seven days. The status-update target is fourteen days.

## In scope

- Disclosure of private vocabulary, prompts, context, or workspace content caused by this project.
- Command execution caused by malformed project configuration.
- Mutation or deletion outside validated owned paths.
- Component installer or uninstaller path validation failures.
- Package, build, checksum, or release-integrity issues.

General model behavior and upstream client vulnerabilities are outside this project's scope. Report them to the relevant provider.
