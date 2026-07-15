from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Iterable

from . import __version__
from .config import (
    DEFAULTS_DIR,
    Rule,
    config_path,
    initialize_config,
    load_rules,
    parse_rules_text,
)


TERMINOLOGY_SCHEMA_VERSION = 1


def rules_path(*, bundled: bool = False, explicit: Path | None = None) -> Path:
    if bundled and explicit is not None:
        raise ValueError("--bundled and --rules cannot be used together")
    if explicit is not None:
        return explicit.expanduser().resolve(strict=False)
    if bundled:
        return (DEFAULTS_DIR / "rules.tsv").resolve()
    initialize_config(force=False)
    return config_path("rules.tsv").resolve(strict=False)


def rules_manifest(path: Path) -> dict[str, object]:
    payload = path.read_bytes()
    rules = load_rules(path)
    return {
        "schema_version": TERMINOLOGY_SCHEMA_VERSION,
        "tool_version": __version__,
        "source": str(path),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "count": len(rules),
        "rules": [
            {"source": rule.source, "normalized": rule.normalized}
            for rule in rules
        ],
    }


def find_rules(rules: Iterable[Rule], query: str) -> list[Rule]:
    if not query.strip():
        raise ValueError("rule search query must not be empty")
    needle = query.casefold()
    return [
        rule
        for rule in rules
        if needle in rule.source.casefold() or needle in rule.normalized.casefold()
    ]


def render_rules(
    manifest: dict[str, object], *, output_format: str, rules: list[Rule] | None = None
) -> str:
    selected = rules
    if selected is None:
        selected = [
            Rule(str(item["source"]), str(item["normalized"]))
            for item in manifest["rules"]  # type: ignore[index]
        ]
    if output_format == "json":
        output = dict(manifest)
        if rules is not None:
            output["total_count"] = manifest["count"]
            output["matched_count"] = len(selected)
        output["count"] = len(selected)
        output["rules"] = [
            {"source": rule.source, "normalized": rule.normalized}
            for rule in selected
        ]
        return json.dumps(output, indent=2, ensure_ascii=False) + "\n"
    if output_format == "tsv":
        return "".join(f"{rule.source}\t{rule.normalized}\n" for rule in selected)
    if output_format == "table":
        width = max([len("SOURCE"), *(len(rule.source) for rule in selected)])
        lines = [f"{'SOURCE':<{width}}  NORMALIZED", f"{'-' * width}  {'-' * 10}"]
        lines.extend(f"{rule.source:<{width}}  {rule.normalized}" for rule in selected)
        return "\n".join(lines) + "\n"
    raise ValueError(f"unsupported output format: {output_format}")


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _backup(path: Path) -> Path:
    backup = path.with_suffix(path.suffix + ".previous")
    shutil.copy2(path, backup)
    return backup


def _verify_mutable(path: Path, initial: bytes, expected_sha256: str | None) -> None:
    if path.is_symlink():
        raise ValueError(f"refusing to modify a linked rules file: {path}")
    actual = hashlib.sha256(initial).hexdigest()
    if expected_sha256 is not None and actual != expected_sha256.casefold():
        raise ValueError(f"rules SHA-256 changed: expected {expected_sha256}, found {actual}")


def add_rule(
    path: Path,
    source: str,
    normalized: str,
    *,
    update: bool,
    expected_sha256: str | None = None,
) -> dict[str, object]:
    source = source.strip()
    normalized = normalized.strip()
    if not source or not normalized:
        raise ValueError("source and normalized vocabulary must not be empty")
    if "\t" in source or "\n" in source or "\t" in normalized or "\n" in normalized:
        raise ValueError("rules must not contain tabs or newlines")

    initial = path.read_bytes()
    _verify_mutable(path, initial, expected_sha256)
    lines = initial.decode("utf-8").splitlines()
    matched_index: int | None = None
    for index, line in enumerate(lines):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) == 2 and fields[0].strip().casefold() == source.casefold():
            matched_index = index
            break
    if matched_index is not None and not update:
        raise ValueError(f"rule already exists for {source!r}; use --update to normalize it")

    line = f"{source}\t{normalized}"
    action = "updated" if matched_index is not None else "added"
    if matched_index is not None:
        lines[matched_index] = line
    else:
        lines.append(line)
    candidate = "\n".join(lines) + "\n"
    parse_rules_text(candidate, path)
    if path.read_bytes() != initial:
        raise ValueError(f"rules changed concurrently: {path}")
    backup = _backup(path)
    _atomic_write(path, candidate)
    return {"action": action, "source": source, "normalized": normalized, "backup": str(backup)}


def remove_rule(
    path: Path, source: str, *, expected_sha256: str | None = None
) -> dict[str, object]:
    source = source.strip()
    initial = path.read_bytes()
    _verify_mutable(path, initial, expected_sha256)
    lines = initial.decode("utf-8").splitlines()
    retained: list[str] = []
    removed: Rule | None = None
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            retained.append(line)
            continue
        fields = line.split("\t")
        if (
            removed is None
            and len(fields) == 2
            and fields[0].strip().casefold() == source.casefold()
        ):
            removed = Rule(fields[0].strip(), fields[1].strip())
            continue
        retained.append(line)
    if removed is None:
        raise ValueError(f"no rule exists for {source!r}")
    candidate = "\n".join(retained) + "\n"
    parse_rules_text(candidate, path)
    if path.read_bytes() != initial:
        raise ValueError(f"rules changed concurrently: {path}")
    backup = _backup(path)
    _atomic_write(path, candidate)
    return {
        "action": "removed",
        "source": removed.source,
        "normalized": removed.normalized,
        "backup": str(backup),
    }
