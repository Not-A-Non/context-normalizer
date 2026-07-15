from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from . import __version__
from .config import Rule, config_path, parse_rules_text


PROFILE_SCHEMA_VERSION = 1
PROFILES_DIR = Path(__file__).resolve().parent / "defaults" / "profiles"
_CATALOG_PATH = PROFILES_DIR / "catalog.json"
_PROFILE_NAMES = (
    "software-writing-expansions",
    "life-science-writing-expansions",
    "security-writing-expansions",
    "gpu-compiler",
)
_CATALOG_KEYS = {"schema_version", "default", "profiles"}
_ENTRY_KEYS = {"name", "description"}
_REPARSE_POINT = 0x400


@contextmanager
def _profile_apply_lock(
    *, timeout: float = 5.0, stale_after: float = 30.0, poll_interval: float = 0.01
) -> Iterator[None]:
    """Serialize a vocabulary profile update; stale locks require operator inspection."""
    if timeout < 0 or stale_after <= 0 or poll_interval <= 0:
        raise ValueError("invalid profile lock timing")
    path = config_path("profile-apply.lock")
    _require_plain_directory(path.parent, label="configuration directory")
    token = uuid.uuid4().hex
    deadline = time.monotonic() + timeout
    while True:
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "w", encoding="ascii", newline="\n") as stream:
                stream.write(token + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            break
        except FileExistsError:
            try:
                age = max(0.0, time.time() - path.stat().st_mtime)
            except FileNotFoundError:
                continue
            if age >= stale_after:
                raise RuntimeError(
                    f"stale profile apply lock requires operator inspection: {path}"
                )
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out waiting for profile apply lock: {path}")
            time.sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))
    try:
        yield
    finally:
        try:
            if path.read_text(encoding="ascii").strip() == token:
                path.unlink()
        except FileNotFoundError:
            pass


def _is_reparse_or_link(path: Path) -> bool:
    try:
        details = path.lstat()
    except FileNotFoundError:
        return False
    attributes = getattr(details, "st_file_attributes", 0)
    return path.is_symlink() or bool(attributes & _REPARSE_POINT)


def _require_plain_file(path: Path, *, label: str) -> None:
    if not path.exists():
        raise FileNotFoundError(f"{label} does not exist: {path}")
    if _is_reparse_or_link(path):
        raise ValueError(f"refusing to use a linked or reparse-point {label}: {path}")
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError(f"{label} is not a regular file: {path}")


def _require_plain_directory(path: Path, *, label: str) -> None:
    if not path.exists():
        raise FileNotFoundError(f"{label} does not exist: {path}")
    if _is_reparse_or_link(path):
        raise ValueError(f"refusing to use a linked or reparse-point {label}: {path}")
    if not path.is_dir():
        raise ValueError(f"{label} is not a directory: {path}")


def _read_catalog() -> dict[str, Any]:
    _require_plain_directory(PROFILES_DIR, label="profiles directory")
    _require_plain_file(_CATALOG_PATH, label="profile catalog")
    try:
        value = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid profile catalog: {error}") from error
    if not isinstance(value, dict) or set(value) != _CATALOG_KEYS:
        raise ValueError("profile catalog must contain only schema_version, default, and profiles")
    if value["schema_version"] != PROFILE_SCHEMA_VERSION:
        raise ValueError(f"unsupported profile catalog schema: {value['schema_version']!r}")
    if value["default"] != _PROFILE_NAMES[0]:
        raise ValueError("profile catalog has an invalid default")
    entries = value["profiles"]
    if not isinstance(entries, list) or len(entries) != len(_PROFILE_NAMES):
        raise ValueError("profile catalog has an invalid profiles list")
    names: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != _ENTRY_KEYS:
            raise ValueError("profile catalog entries must contain only name and description")
        if not isinstance(entry["name"], str) or not isinstance(entry["description"], str):
            raise ValueError("profile catalog entry fields must be strings")
        if not entry["description"].strip():
            raise ValueError("profile descriptions must not be empty")
        names.append(entry["name"])
    if tuple(names) != _PROFILE_NAMES:
        raise ValueError("profile catalog contains an unapproved profile or ordering")
    return value


def profile_catalog() -> dict[str, Any]:
    """Return the validated bundled catalog without creating user configuration."""
    return _read_catalog()


def _profile_paths(name: str) -> tuple[Path, Path]:
    if name not in _PROFILE_NAMES:
        raise ValueError(f"unknown profile: {name!r}")
    # The fixed allowlist above is intentionally the only input used in these paths.
    root = PROFILES_DIR / name
    _require_plain_directory(PROFILES_DIR, label="profiles directory")
    _require_plain_directory(root, label="profile directory")
    rules_path = root / "rules.tsv"
    cues_path = root / "cues.txt"
    _require_plain_file(rules_path, label="profile rules file")
    _require_plain_file(cues_path, label="profile cues file")
    return rules_path, cues_path


def _parse_cues(
    payload: bytes, source: Path | str, *, require_nonempty: bool = False
) -> list[str]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"{source}: cues must be UTF-8") from error
    cues: list[str] = []
    seen: set[str] = set()
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        cue = raw_line.strip()
        if not cue or cue.startswith("#"):
            continue
        if "\t" in cue:
            raise ValueError(f"{source}:{line_number}: cue must not contain a tab")
        folded = cue.casefold()
        if folded in seen:
            raise ValueError(f"{source}:{line_number}: duplicate cue {cue!r}")
        seen.add(folded)
        cues.append(cue)
    if require_nonempty and not cues:
        raise ValueError(f"{source}: at least one cue is required")
    return cues


def _parse_rules(
    payload: bytes, source: Path | str, *, require_nonempty: bool = False
) -> list[Rule]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"{source}: rules must be UTF-8") from error
    rules = parse_rules_text(text, source)
    if require_nonempty and not rules:
        raise ValueError(f"{source}: at least one rule is required")
    return rules


def _composite_sha256(rules: bytes, cues: bytes) -> str:
    digest = hashlib.sha256()
    for label, payload in ((b"rules.tsv", rules), (b"cues.txt", cues)):
        digest.update(label)
        digest.update(b"\0")
        digest.update(payload)
        digest.update(b"\0")
    return digest.hexdigest()


def _manifest(
    *, name: str, description: str, rules_path: Path, cues_path: Path
) -> dict[str, Any]:
    rules_payload = rules_path.read_bytes()
    cues_payload = cues_path.read_bytes()
    rules = _parse_rules(rules_payload, rules_path, require_nonempty=name != "active")
    cues = _parse_cues(cues_payload, cues_path, require_nonempty=name != "active")
    return {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "tool_version": __version__,
        "name": name,
        "description": description,
        "sha256": _composite_sha256(rules_payload, cues_payload),
        "rule_count": len(rules),
        "cue_count": len(cues),
        "rules": [
            {"source": rule.source, "normalized": rule.normalized} for rule in rules
        ],
        "cues": cues,
    }


def profile_manifest(name: str) -> dict[str, Any]:
    """Return a validated bundled profile without creating user configuration."""
    catalog = _read_catalog()
    descriptions = {entry["name"]: entry["description"] for entry in catalog["profiles"]}
    if name not in descriptions:
        raise ValueError(f"unknown profile: {name!r}")
    rules_path, cues_path = _profile_paths(name)
    return _manifest(
        name=name,
        description=descriptions[name],
        rules_path=rules_path,
        cues_path=cues_path,
    )


def _active_paths() -> tuple[Path, Path]:
    rules_path = config_path("rules.tsv")
    cues_path = config_path("cues.txt")
    _require_plain_file(rules_path, label="active rules file")
    _require_plain_file(cues_path, label="active cues file")
    return rules_path, cues_path


def active_profile_manifest() -> dict[str, Any]:
    """Return the current rules-and-cues identity without initializing config."""
    rules_path, cues_path = _active_paths()
    return _manifest(
        name="active",
        description="Active terminology rules and cues.",
        rules_path=rules_path,
        cues_path=cues_path,
    )


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    handle, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _append_lines(original: bytes, lines: list[str]) -> bytes:
    if not lines:
        return original
    separator = b"" if original.endswith((b"\n", b"\r")) else b"\n"
    return original + separator + "".join(f"{line}\n" for line in lines).encode("utf-8")


def _merge_rules(active_payload: bytes, profile_payload: bytes, active_path: Path) -> bytes:
    active = _parse_rules(active_payload, active_path)
    profile = _parse_rules(profile_payload, "profile rules.tsv", require_nonempty=True)
    by_source = {rule.source.casefold(): rule.normalized for rule in active}
    additions: list[str] = []
    for rule in profile:
        folded = rule.source.casefold()
        existing = by_source.get(folded)
        if existing is not None:
            if existing != rule.normalized:
                raise ValueError(
                    f"conflicting normalization for {rule.source!r}: "
                    f"active has {existing!r}, profile has {rule.normalized!r}"
                )
            continue
        by_source[folded] = rule.normalized
        additions.append(f"{rule.source}\t{rule.normalized}")
    return _append_lines(active_payload, additions)


def _merge_cues(active_payload: bytes, profile_payload: bytes, active_path: Path) -> bytes:
    active = _parse_cues(active_payload, active_path)
    profile = _parse_cues(profile_payload, "profile cues.txt", require_nonempty=True)
    seen = {cue.casefold() for cue in active}
    additions: list[str] = []
    for cue in profile:
        folded = cue.casefold()
        if folded not in seen:
            seen.add(folded)
            additions.append(cue)
    return _append_lines(active_payload, additions)


def _apply_profile_locked(
    name: str,
    mode: str = "merge",
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    """Apply a bundled profile while the configuration-scoped lock is held."""
    if mode not in {"merge", "reset"}:
        raise ValueError("profile mode must be 'merge' or 'reset'")
    profile_rules_path, profile_cues_path = _profile_paths(name)
    active_rules_path, active_cues_path = _active_paths()

    profile_rules = profile_rules_path.read_bytes()
    profile_cues = profile_cues_path.read_bytes()
    _parse_rules(profile_rules, profile_rules_path, require_nonempty=True)
    _parse_cues(profile_cues, profile_cues_path, require_nonempty=True)
    initial_rules = active_rules_path.read_bytes()
    initial_cues = active_cues_path.read_bytes()
    _parse_rules(initial_rules, active_rules_path)
    _parse_cues(initial_cues, active_cues_path)
    initial_sha256 = _composite_sha256(initial_rules, initial_cues)
    if expected_sha256 is not None and initial_sha256 != expected_sha256.casefold():
        raise ValueError(
            f"active profile SHA-256 changed: expected {expected_sha256}, "
            f"found {initial_sha256}"
        )

    if mode == "reset":
        candidate_rules, candidate_cues = profile_rules, profile_cues
    else:
        candidate_rules = _merge_rules(initial_rules, profile_rules, active_rules_path)
        candidate_cues = _merge_cues(initial_cues, profile_cues, active_cues_path)
    _parse_rules(candidate_rules, active_rules_path)
    _parse_cues(candidate_cues, active_cues_path)

    if candidate_rules == initial_rules and candidate_cues == initial_cues:
        result = active_profile_manifest()
        result.update({"profile": name, "mode": mode, "changed": False})
        return result

    # Detect changes after validation and candidate construction, before committing.
    if (
        active_rules_path.read_bytes() != initial_rules
        or active_cues_path.read_bytes() != initial_cues
    ):
        raise ValueError("active profile changed concurrently")

    rules_backup = active_rules_path.with_name("rules.tsv.previous")
    cues_backup = active_cues_path.with_name("cues.txt.previous")
    _atomic_write_bytes(rules_backup, initial_rules)
    _atomic_write_bytes(cues_backup, initial_cues)
    try:
        _atomic_write_bytes(active_rules_path, candidate_rules)
        _atomic_write_bytes(active_cues_path, candidate_cues)
    except Exception:
        # Restore both members even if only one changed. Backups intentionally remain.
        rollback_errors: list[Exception] = []
        for path, payload in (
            (active_rules_path, initial_rules),
            (active_cues_path, initial_cues),
        ):
            try:
                _atomic_write_bytes(path, payload)
            except Exception as error:  # pragma: no cover - catastrophic filesystem failure
                rollback_errors.append(error)
        if rollback_errors:
            raise RuntimeError("profile apply failed and rollback was incomplete") from rollback_errors[0]
        raise

    result = active_profile_manifest()
    result.update(
        {
            "profile": name,
            "mode": mode,
            "changed": True,
            "previous_sha256": initial_sha256,
            "backups": {"rules": str(rules_backup), "cues": str(cues_backup)},
        }
    )
    return result


def apply_profile(
    name: str,
    mode: str = "merge",
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    """Apply a bundled profile as a serialized rules-and-cues update."""
    with _profile_apply_lock():
        return _apply_profile_locked(name, mode, expected_sha256)
