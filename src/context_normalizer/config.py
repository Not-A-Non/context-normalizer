from __future__ import annotations

import os
import shutil
import sys
import json
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path


DEFAULTS_DIR = Path(__file__).resolve().parent / "defaults"

# str.splitlines() also splits on these, which would silently truncate a rule
# and fabricate a phantom rule from the remainder. Only \n and \r\n are lines.
_UNSUPPORTED_LINE_BREAKS = "\x0b\x0c\x1c\x1d\x1e\x85  "


def _rule_lines(text: str) -> list[str]:
    return [line.rstrip("\r") for line in text.split("\n")]


@dataclass(frozen=True)
class Rule:
    source: str
    normalized: str


def config_dir() -> Path:
    override = os.environ.get("CONTEXT_NORMALIZER_HOME")
    path = (
        Path(override).expanduser() if override else Path.home() / ".context-normalizer"
    )
    if not path.is_absolute():
        raise ValueError("CONTEXT_NORMALIZER_HOME must be an absolute path")
    return path.resolve(strict=False)


def config_path(name: str) -> Path:
    return config_dir() / name


def _atomic_json(path: Path, value: dict[str, object]) -> None:
    handle, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def initialize_config(
    *,
    force: bool = False,
    register_installation: bool = False,
) -> list[Path]:
    destination = config_dir()
    destination.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    if register_installation:
        marker = destination / "installation.json"
        previous: dict[str, object] = {}
        if marker.exists():
            previous = json.loads(marker.read_text(encoding="utf-8"))
        _atomic_json(
            marker,
            {
                "schema_version": 1,
                "installation_id": previous.get("installation_id", str(uuid.uuid4())),
                "config_dir": str(destination.resolve(strict=False)),
                "python_executable": str(Path(sys.executable).resolve()),
            },
        )
        if marker not in written:
            written.append(marker)
        runtime = destination / "runtime-python.txt"
        runtime.write_text(str(Path(sys.executable).resolve()) + "\n", encoding="utf-8")
        if runtime not in written:
            written.append(runtime)
    for name in (
        "rules.tsv",
        "context.txt",
        "subagent-context.txt",
        "cues.txt",
        "path-rules.tsv",
    ):
        target = destination / name
        if target.exists() and not force:
            continue
        shutil.copyfile(DEFAULTS_DIR / name, target)
        written.append(target)
    return written


def _ensure_config(*, initialize: bool) -> None:
    if initialize:
        initialize_config(force=False)


def load_rules(path: Path | None = None, *, initialize: bool = True) -> list[Rule]:
    _ensure_config(initialize=initialize and path is None)
    source_path = path or config_path("rules.tsv")
    return parse_rules_text(source_path.read_text(encoding="utf-8"), source_path)


def load_path_rules(path: Path | None = None, *, initialize: bool = True) -> list[Rule]:
    _ensure_config(initialize=initialize and path is None)
    if path is not None:
        return parse_rules_text(path.read_text(encoding="utf-8"), path)
    source_path = config_path("path-rules.tsv")
    if source_path.exists():
        text = source_path.read_text(encoding="utf-8")
        if text.strip():
            return parse_rules_text(text, source_path)
    return []


def parse_rules_text(text: str, source_path: Path | str = "<memory>") -> list[Rule]:
    rules: list[Rule] = []
    seen: set[str] = set()
    for line_number, raw_line in enumerate(_rule_lines(text), start=1):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if any(character in _UNSUPPORTED_LINE_BREAKS for character in raw_line):
            raise ValueError(
                f"{source_path}:{line_number}: unsupported line separator in rule"
            )
        fields = raw_line.split("\t")
        if len(fields) != 2 or not all(field.strip() for field in fields):
            raise ValueError(
                f"{source_path}:{line_number}: expected SOURCE<TAB>NORMALIZED"
            )
        source, normalized = (field.strip() for field in fields)
        key = source.casefold()
        if key in seen:
            raise ValueError(
                f"{source_path}:{line_number}: duplicate source phrase {source!r}"
            )
        seen.add(key)
        rules.append(Rule(source=source, normalized=normalized))
    return rules


def load_context(path: Path | None = None, *, initialize: bool = True) -> str:
    _ensure_config(initialize=initialize and path is None)
    value = (path or config_path("context.txt")).read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError("context text must not be empty")
    return value


def load_subagent_context(path: Path | None = None, *, initialize: bool = True) -> str:
    _ensure_config(initialize=initialize and path is None)
    value = (
        (path or config_path("subagent-context.txt"))
        .read_text(encoding="utf-8")
        .strip()
    )
    if not value:
        raise ValueError("subagent context text must not be empty")
    return value


def load_cues(path: Path | None = None, *, initialize: bool = True) -> list[str]:
    _ensure_config(initialize=initialize and path is None)
    values: list[str] = []
    for raw_line in _rule_lines(
        (path or config_path("cues.txt")).read_text(encoding="utf-8")
    ):
        value = raw_line.strip()
        if value and not value.startswith("#"):
            values.append(value)
    return values
