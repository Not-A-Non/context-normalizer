#!/usr/bin/env python3
"""Reject personal, secret, runtime, and generated data from the public tree."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_FILE_BYTES = 10 * 1024 * 1024
TEXT_LIMIT = 2 * 1024 * 1024
FORBIDDEN_PARTS = {
    ".env",
    ".ctxnorm-workspace",
    "__pycache__",
    "build",
    "dist",
    "logs",
    "node_modules",
    "target",
}
FORBIDDEN_NAMES = {
    ".ctxnorm-workspace.json",
    "auth.json",
    "credentials.json",
}
PATTERNS = {
    "Windows user path": re.compile(r"[A-Za-z]:[\\/]Users[\\/][^\\/\s]+", re.IGNORECASE),
    "macOS user path": re.compile(r"/Users/[^/\s]+"),
    "personal identity": re.compile(r"\bchris\b", re.IGNORECASE),
    "private IPv4 address": re.compile(
        r"\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b"
    ),
    "GitHub credential": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    "API credential": re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}\b"),
    "AWS credential": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "Google OAuth credential": re.compile(r"\bya29\.[A-Za-z0-9_-]{20,}\b"),
    "OAuth JSON value": re.compile(r'"(?:access_token|client_secret|refresh_token)"\s*:\s*"[^"\r\n]{8,}"', re.IGNORECASE),
    "Slack credential": re.compile(r"\bxox[aboprs]-[A-Za-z0-9-]{20,}\b"),
    "bearer credential": re.compile(r"\bBearer\s+[A-Za-z0-9._~-]{20,}\b", re.IGNORECASE),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}


def candidate_files() -> list[Path]:
    command = ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, check=True)
    return sorted(
        ROOT / item.decode("utf-8")
        for item in result.stdout.split(b"\0")
        if item and (ROOT / item.decode("utf-8")).is_file()
    )


def main() -> int:
    failures: list[str] = []
    files = candidate_files()
    for path in files:
        relative = path.relative_to(ROOT)
        lower_parts = {part.lower() for part in relative.parts}
        if lower_parts.intersection(FORBIDDEN_PARTS):
            failures.append(f"{relative}: generated or runtime directory")
            continue
        if path.name.lower() in FORBIDDEN_NAMES or path.name.lower().startswith("session"):
            failures.append(f"{relative}: authentication, session, or workspace artifact")
            continue
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            failures.append(f"{relative}: file exceeds {MAX_FILE_BYTES} bytes")
            continue
        if size > TEXT_LIMIT:
            continue
        data = path.read_bytes()
        if b"\0" in data:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if relative == Path("scripts/audit_public_tree.py"):
            continue
        for label, pattern in PATTERNS.items():
            if pattern.search(text):
                failures.append(f"{relative}: {label}")
    if failures:
        print("\n".join(sorted(failures)), file=sys.stderr)
        return 1
    print(f"public tree passed: {len(files)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
