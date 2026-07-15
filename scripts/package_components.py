#!/usr/bin/env python3
"""Build separate deterministic Codex and Pi component archives."""

from __future__ import annotations

import argparse
import gzip
import io
import json
import tarfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = ("codex", "pi")
EXCLUDED = {"__pycache__", "node_modules", "target"}


def component_version(name: str) -> str:
    metadata = "integration.json" if name == "codex" else "package.json"
    document = json.loads((ROOT / "integrations" / name / metadata).read_text(encoding="utf-8"))
    return str(document["version"])


def archive_component(name: str, destination: Path) -> Path:
    source = ROOT / "integrations" / name
    version = component_version(name)
    prefix = f"context-normalizer-{name}-{version}"
    output = destination / f"{prefix}.tar.gz"
    files = sorted(
        path
        for path in source.rglob("*")
        if path.is_file() and not EXCLUDED.intersection(path.relative_to(source).parts)
    )
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for path in files:
            relative = path.relative_to(source)
            data = path.read_bytes()
            info = tarfile.TarInfo(f"{prefix}/{relative.as_posix()}")
            info.size = len(data)
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = 0o755 if path.suffix == ".sh" or path.name == "ctxnorm-codex" else 0o644
            archive.addfile(info, io.BytesIO(data))
    with output.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            compressed.write(buffer.getvalue())
    return output


def verify_archive(path: Path, component: str) -> None:
    with tarfile.open(path, "r:gz") as archive:
        names = archive.getnames()
        for member in archive.getmembers():
            candidate = Path(member.name)
            if candidate.is_absolute() or ".." in candidate.parts:
                raise ValueError(f"unsafe component archive entry: {member.name}")
    required = {
        "LICENSE",
        "README.md",
        "install.ps1",
        "install.sh",
        "uninstall.ps1",
        "uninstall.sh",
        "verify.ps1",
        "verify.sh",
    }
    if component == "codex":
        required.update({"integration.json", "THIRD_PARTY-NOTICES.md", "ctxnorm-codex", "ctxnorm-codex.ps1", "patches/codex-tui-context-normalizer.patch"})
    else:
        required.update({"package.json", "THIRD_PARTY-NOTICES.md", "extensions/context-normalizer.mjs", "test/context-normalizer.test.mjs"})
    missing = {suffix for suffix in required if not any(name.endswith("/" + suffix) for name in names)}
    if missing:
        raise ValueError(f"{component} archive missing entries: {sorted(missing)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="dist")
    args = parser.parse_args()
    destination = Path(args.output).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    for name in COMPONENTS:
        path = archive_component(name, destination)
        verify_archive(path, name)
        print(path.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
