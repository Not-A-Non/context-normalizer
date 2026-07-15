#!/usr/bin/env python3
"""Enforce the public vocabulary and context normalization scope."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_FILES = {
    ROOT / "README.md",
    ROOT / "AGENTS.md",
    ROOT / "CHANGELOG.md",
    ROOT / "pyproject.toml",
}
PUBLIC_ROOTS = (
    ROOT / "docs",
    ROOT / ".github",
    ROOT / "integrations",
)
PUBLIC_SUFFIXES = {".md", ".yml", ".yaml", ".json"}
BANNED = {
    "bypass": re.compile(r"\bbypass(?:es|ed|ing)?\b", re.IGNORECASE),
    "censor": re.compile(r"\bcensor(?:s|ed|ing|ship)?\b", re.IGNORECASE),
    "claude": re.compile(r"\bclaude\b", re.IGNORECASE),
    "filter": re.compile(r"\bfilter(?:s|ed|ing)?\b", re.IGNORECASE),
    "proxy": re.compile(r"\bprox(?:y|ies|ied)\b", re.IGNORECASE),
    "replacement": re.compile(r"\breplac(?:e|es|ed|ing|ement|ements)\b", re.IGNORECASE),
    "sanitize": re.compile(r"\bsanitiz(?:e|es|ed|ing|ation)\b", re.IGNORECASE),
}


def public_files() -> list[Path]:
    files = {path for path in PUBLIC_FILES if path.is_file()}
    files.update(ROOT.glob("*.md"))
    for root in PUBLIC_ROOTS:
        if not root.is_dir():
            continue
        files.update(
            path
            for path in root.rglob("*")
            if path.is_file()
            and path.suffix.lower() in PUBLIC_SUFFIXES
            and ".github/workflows" not in path.relative_to(ROOT).as_posix()
        )
    return sorted(files)


def main() -> int:
    failures: list[str] = []
    for path in public_files():
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), 1):
            for label, pattern in BANNED.items():
                if pattern.search(line):
                    failures.append(
                        f"{path.relative_to(ROOT)}:{line_number}: prohibited public term: {label}"
                    )
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(f"public language passed: {len(public_files())} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
