#!/usr/bin/env python3
"""Verify local Markdown links in public documentation."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")


def markdown_files() -> list[Path]:
    files = list(ROOT.glob("*.md"))
    for directory in (ROOT / "docs", ROOT / ".github", ROOT / "integrations"):
        files.extend(directory.rglob("*.md"))
    return sorted(path for path in files if path.is_file())


def main() -> int:
    failures: list[str] = []
    files = markdown_files()
    for document in files:
        text = document.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), 1):
            for raw in LINK.findall(line):
                target = raw.strip().strip("<>").split("#", 1)[0]
                if not target or target.startswith(("http://", "https://", "mailto:")):
                    continue
                resolved = (document.parent / unquote(target)).resolve(strict=False)
                try:
                    resolved.relative_to(ROOT)
                except ValueError:
                    failures.append(f"{document.relative_to(ROOT)}:{line_number}: link escapes repository: {raw}")
                    continue
                if not resolved.exists():
                    failures.append(f"{document.relative_to(ROOT)}:{line_number}: missing link target: {raw}")
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(f"documentation links passed: {len(files)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
