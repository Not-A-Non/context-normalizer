# Compatibility

## Core

| Component | Supported versions |
| --- | --- |
| Python | 3.10 through 3.13 |
| Windows | Current supported Windows with PowerShell 5.1 or later |
| Linux | Current distributions with POSIX shell tools |
| macOS | Current supported macOS with POSIX shell tools |
| Git workspace mode | Git 2.30 or later |

## Codex

The Codex integration supports only the upstream tag and commit recorded in `integrations/codex/integration.json`. Installation verifies both values before applying the integration patch. A new Codex revision requires a reviewed patch, focused tests, and a compatibility update.

## Pi

The Pi integration supports Pi 0.80 or later and Node.js 20 or later. The extension uses documented session events and contains no runtime package dependency.

## Files

Text content normalization supports valid UTF-8. Binary and non-UTF-8 content remains byte-identical. Path normalization rejects ambiguous vocabulary and platform path separators in individual vocabulary fields.
