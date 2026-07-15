#!/usr/bin/env python3
"""Verify core archives and an isolated installed-wheel workflow."""

from __future__ import annotations

import email.parser
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path


REQUIRED_WHEEL = {
    "context_normalizer/cli.py",
    "context_normalizer/config.py",
    "context_normalizer/feature_cli.py",
    "context_normalizer/lifecycle.py",
    "context_normalizer/normalize.py",
    "context_normalizer/profiles.py",
    "context_normalizer/relay.py",
    "context_normalizer/terminology.py",
    "context_normalizer/workspace.py",
    "context_normalizer/defaults/rules.tsv",
    "context_normalizer/defaults/path-rules.tsv",
    "context_normalizer/defaults/profiles/catalog.json",
    "context_normalizer/py.typed",
}
REQUIRED_SDIST = {
    "pyproject.toml",
    "README.md",
    "CHANGELOG.md",
    "THIRD_PARTY-NOTICES.md",
    "scripts/lifecycle/install.ps1",
    "scripts/lifecycle/install.sh",
    "scripts/lifecycle/uninstall.ps1",
    "scripts/lifecycle/uninstall.sh",
    "scripts/verify_release.py",
    "scripts/check_public_language.py",
    "scripts/check_docs.py",
    "scripts/audit_public_tree.py",
    *{f"src/{name}" for name in REQUIRED_WHEEL},
}
FORBIDDEN_ARCHIVE_PARTS = {
    ".git",
    "__pycache__",
    "build",
    "dist",
    "integrations",
    "node_modules",
    "target",
}


def run(
    arguments: list[str], environment: dict[str, str], input_text: str | None = None
) -> str:
    result = subprocess.run(
        arguments,
        input=input_text,
        text=True,
        capture_output=True,
        env=environment,
        check=False,
        timeout=120,
    )
    if result.returncode:
        raise RuntimeError(
            f"command failed ({result.returncode}): {arguments}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result.stdout


def validate_names(names: list[str], artifact: Path) -> None:
    for name in names:
        path = Path(name.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"unsafe archive entry in {artifact.name}: {name}")
        if FORBIDDEN_ARCHIVE_PARTS.intersection(path.parts) or path.suffix == ".pyc":
            raise ValueError(f"forbidden archive entry in {artifact.name}: {name}")


def require_suffixes(names: list[str], required: set[str], label: str) -> None:
    normalized = {name.replace("\\", "/") for name in names}
    missing = {
        suffix
        for suffix in required
        if not any(name == suffix or name.endswith("/" + suffix) for name in normalized)
    }
    if missing:
        raise ValueError(f"{label} is missing entries: {sorted(missing)}")


def main() -> int:
    dist = Path(sys.argv[1] if len(sys.argv) > 1 else "dist").resolve()
    wheels = sorted(dist.glob("context_normalizer-*.whl"))
    sdists = sorted(dist.glob("context_normalizer-*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ValueError("expected exactly one core wheel and one core source archive")
    wheel, sdist = wheels[0], sdists[0]

    with zipfile.ZipFile(wheel) as archive:
        wheel_names = archive.namelist()
        metadata_names = [
            name for name in wheel_names if name.endswith(".dist-info/METADATA")
        ]
        if len(metadata_names) != 1:
            raise ValueError("wheel must contain one METADATA file")
        metadata = email.parser.BytesParser().parsebytes(
            archive.read(metadata_names[0])
        )
        if metadata.get_all("Requires-Dist", []):
            raise ValueError("core wheel must have no runtime dependency")
        if metadata.get("Version") != "1.0.0":
            raise ValueError(f"unexpected core version: {metadata.get('Version')}")
    validate_names(wheel_names, wheel)
    require_suffixes(wheel_names, REQUIRED_WHEEL, "wheel")

    with tarfile.open(sdist, "r:gz") as archive:
        sdist_names = archive.getnames()
    validate_names(sdist_names, sdist)
    require_suffixes(sdist_names, REQUIRED_SDIST, "source archive")

    with tempfile.TemporaryDirectory(prefix="ctxnorm-release-") as temporary:
        root = Path(temporary)
        target = root / "site"
        configuration = root / "configuration"
        source = root / "source"
        source.joinpath("source-name").mkdir(parents=True)
        source.joinpath("source-name", "payload.txt").write_text(
            "alpha term source-name\n", encoding="utf-8"
        )
        environment = dict(os.environ)
        environment.update(
            {"PYTHONPATH": str(target), "CONTEXT_NORMALIZER_HOME": str(configuration)}
        )
        cli = [sys.executable, "-m", "context_normalizer"]

        run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--no-index",
                "--no-deps",
                "--target",
                str(target),
                str(wheel),
            ],
            environment,
        )
        run([*cli, "init"], environment)
        run([*cli, "doctor"], environment)
        run([*cli, "vocabulary", "add", "alpha term", "canonical term"], environment)
        run(
            [
                *cli,
                "vocabulary",
                "add",
                "alpha term",
                "canonical term",
                "--bidirectional",
            ],
            environment,
        )
        run(
            [
                *cli,
                "vocabulary",
                "add",
                "source-name",
                "normalized-name",
                "--bidirectional",
            ],
            environment,
        )
        run([*cli, "vocabulary", "validate", "--format", "json"], environment)
        created = json.loads(
            run(
                [
                    *cli,
                    "workspace",
                    "create",
                    str(source),
                    "--mode",
                    "filesystem",
                    "--normalize-paths",
                    "--format",
                    "json",
                    "--yes",
                ],
                environment,
            )
        )
        mirror = Path(created["mirror"])
        normalized_payload = mirror / "normalized-name" / "payload.txt"
        if (
            normalized_payload.read_text(encoding="utf-8")
            != "canonical term normalized-name\n"
        ):
            raise ValueError("workspace content or path normalization failed")
        submitted = run(
            [*cli, "bridge", "submit", "--workspace", str(mirror)],
            environment,
            "alpha term source-name",
        )
        if submitted != "canonical term normalized-name":
            raise ValueError("submission normalization failed")
        displayed = run(
            [*cli, "bridge", "normalize", "--direction", "display"],
            environment,
            submitted,
        )
        if displayed != "alpha term source-name":
            raise ValueError("display normalization failed")
        mirror.joinpath("normalized-name", "new normalized-name.txt").write_text(
            "canonical term normalized-name\n", encoding="utf-8"
        )
        completion = json.loads(
            run([*cli, "bridge", "complete", "--workspace", str(mirror)], environment)
        )
        if completion["status"] != "applied":
            raise ValueError("completed workspace normalization did not apply")
        source_output = source / "source-name" / "new source-name.txt"
        if source_output.read_text(encoding="utf-8") != "alpha term source-name\n":
            raise ValueError("source workspace normalization failed")
        run([*cli, "workspace", "verify", str(mirror), "--format", "json"], environment)
        run(
            [*cli, "workspace", "close", str(mirror), "--no-archive", "--yes"],
            environment,
        )
        run(
            [*cli, "workspace", "cleanup", created["workspace_id"], "--yes"],
            environment,
        )
        manifest = json.loads(
            run([*cli, "vocabulary", "list", "--format", "json"], environment)
        )
        if manifest["count"] < 1 or len(manifest["sha256"]) != 64:
            raise ValueError("vocabulary manifest is incomplete")
        run([*cli, "purge", "--yes"], environment)
        if configuration.exists():
            raise ValueError("purge left core-owned configuration")

    print(
        json.dumps(
            {"status": "passed", "wheel": wheel.name, "sdist": sdist.name},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
