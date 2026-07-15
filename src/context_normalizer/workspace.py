"""Reversible, provenance-preserving workspace mirrors.

This module deliberately exposes a Python API rather than modifying PATH or
installing shell aliases.  The default mirror is neutral.  A translated
filesystem mirror can additionally rewrite path segments with a recorded rule
set and synchronize those paths back to the original source names.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import tempfile
import time
import unicodedata
import uuid
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping, Sequence

from .config import Rule
from .normalize import case_like, translate_reversible_text


SCHEMA = 1
MARKER = ".ctxnorm-workspace.json"
STATE_DIR = ".ctxnorm-state"
NAME_RE = re.compile(r"workspace-[0-9a-f]{12}")
ALIAS_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}")
DEFAULT_FILE_CAP = 256 * 1024 * 1024
DEFAULT_TREE_CAP = 10 * 1024 * 1024 * 1024


class WorkspaceError(RuntimeError):
    pass


class WorkspaceConflict(WorkspaceError):
    pass


class SimulatedCrash(WorkspaceError):
    """Test fault representing interruption after durable journal progress."""


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_identity(path: Path, cap: int = DEFAULT_FILE_CAP) -> dict[str, Any]:
    size = path.stat().st_size
    if size > cap:
        raise WorkspaceError(f"file exceeds {cap} byte cap: {path}")
    digest = hashlib.sha256()
    read = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            read += len(chunk)
    return {"bytes": read, "sha256": digest.hexdigest()}


def _is_link_or_reparse(path: Path) -> bool:
    info = path.lstat()
    attrs = getattr(info, "st_file_attributes", 0)
    return stat.S_ISLNK(info.st_mode) or bool(
        attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _absolute_directory(path: Path, label: str, *, create: bool = False) -> Path:
    if not path.is_absolute():
        raise WorkspaceError(f"{label} must be absolute: {path}")
    if create:
        path.mkdir(parents=True, exist_ok=True)
    if not path.exists() or not path.is_dir():
        raise WorkspaceError(f"{label} must be an existing directory: {path}")
    if _is_link_or_reparse(path):
        raise WorkspaceError(f"{label} must not be a link/reparse point: {path}")
    return path.resolve(strict=True)


def _separate(left: Path, right: Path) -> bool:
    try:
        left.relative_to(right)
        return False
    except ValueError:
        pass
    try:
        right.relative_to(left)
        return False
    except ValueError:
        return True


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(_canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _seal_state(state: Mapping[str, Any]) -> dict[str, Any]:
    sealed = dict(state)
    sealed.pop("state_sha256", None)
    sealed["state_sha256"] = _sha_bytes(_canonical(sealed))
    return sealed


def _verify_state(state: Mapping[str, Any]) -> None:
    claimed = state.get("state_sha256")
    unsealed = dict(state)
    unsealed.pop("state_sha256", None)
    if claimed != _sha_bytes(_canonical(unsealed)):
        raise WorkspaceError("workspace state integrity check failed")


def _verify_manifest(manifest: Mapping[str, Any]) -> None:
    claimed = manifest.get("sha256")
    unsigned = dict(manifest)
    unsigned.pop("sha256", None)
    if claimed != _sha_bytes(_canonical(unsigned)):
        raise WorkspaceError("base manifest integrity check failed")


def _safe_relative(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise WorkspaceError("relative path must be a nonempty string")
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise WorkspaceError(f"unsafe relative path: {value}")
    return path


def _path_rule_payload(raw_rules: Sequence[Any] | None) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen_sources: set[str] = set()
    seen_normalized: set[str] = set()
    for raw in raw_rules or []:
        if isinstance(raw, Mapping):
            source = str(raw.get("source", ""))
            normalized = str(raw.get("normalized", ""))
        else:
            source = str(getattr(raw, "source", ""))
            normalized = str(getattr(raw, "normalized", ""))
        if not source or not normalized:
            raise WorkspaceError(
                "path normalization entries require source and normalized vocabulary"
            )
        if "\0" in source or "\0" in normalized:
            raise WorkspaceError(
                "path normalization entries must not contain NUL bytes"
            )
        source_key = source.casefold()
        normalized_key = normalized.casefold()
        if source_key in seen_sources:
            raise WorkspaceError(f"duplicate path translation source: {source!r}")
        if normalized_key in seen_normalized:
            raise WorkspaceError(
                f"ambiguous bidirectional normalized vocabulary: {normalized!r}"
            )
        seen_sources.add(source_key)
        seen_normalized.add(normalized_key)
        result.append({"source": source, "normalized": normalized})
    return result


def _path_rules_from_state(state: Mapping[str, Any]) -> list[dict[str, str]]:
    translation = state.get("path_normalization")
    if not isinstance(translation, Mapping) or not translation.get("enabled"):
        return []
    rules = translation.get("rules")
    if not isinstance(rules, list):
        raise WorkspaceError("invalid path normalization state")
    return _path_rule_payload(rules)


def _as_rules(rules: Sequence[Mapping[str, str]]) -> list[Rule]:
    return [Rule(str(rule["source"]), str(rule["normalized"])) for rule in rules]


def _translate_payload(
    payload: bytes,
    rules: Sequence[Mapping[str, str]],
    *,
    reverse: bool = False,
) -> bytes:
    if not rules or b"\0" in payload:
        return payload
    bom = payload.startswith(b"\xef\xbb\xbf")
    encoded = payload[3:] if bom else payload
    try:
        text = encoded.decode("utf-8")
    except UnicodeDecodeError:
        return payload
    translated = translate_reversible_text(text, _as_rules(rules), reverse=reverse)
    if not reverse:
        # Fail closed when content cannot round-trip: a normalized value that
        # already occurs naturally in the source would be rewritten to the
        # private source vocabulary during back-synchronization.
        restored = translate_reversible_text(translated, _as_rules(rules), reverse=True)
        if restored != text:
            raise WorkspaceError(
                "content does not round-trip through bidirectional vocabulary; "
                "a normalized value already occurs in the source content"
            )
    result = translated.encode("utf-8")
    return (b"\xef\xbb\xbf" + result) if bom else result


def _translated_file_identity(
    path: Path,
    rules: Sequence[Mapping[str, str]],
    *,
    reverse: bool = False,
    cap: int = DEFAULT_FILE_CAP,
) -> dict[str, Any]:
    size = path.stat().st_size
    if size > cap:
        raise WorkspaceError(f"file exceeds {cap} byte cap: {path}")
    payload = _translate_payload(path.read_bytes(), rules, reverse=reverse)
    return {"bytes": len(payload), "sha256": _sha_bytes(payload)}


def _validate_translated_part(part: str, original: str) -> None:
    if part in {"", ".", ".."}:
        raise WorkspaceError(
            f"path translation produced an unsafe segment: {original!r}"
        )
    if "\0" in part or any(separator in part for separator in ("/", "\\")):
        raise WorkspaceError(
            f"path translation produced a path separator: {original!r}"
        )
    if os.name == "nt" and any(character in part for character in '<>:"|?*'):
        raise WorkspaceError(
            f"path translation produced an invalid Windows segment: {original!r}"
        )


@lru_cache(maxsize=64)
def _segment_translator(
    pairs: tuple[tuple[str, str], ...],
) -> tuple[re.Pattern[str], dict[str, tuple[str, str]]]:
    by_source = {
        source.casefold(): (source, normalized) for source, normalized in pairs
    }
    alternatives = "|".join(
        re.escape(source)
        for source, _ in sorted(pairs, key=lambda item: len(item[0]), reverse=True)
    )
    pattern = re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)", re.IGNORECASE)
    return pattern, by_source


def _translate_part(
    part: str, rules: Sequence[Mapping[str, str]], *, reverse: bool
) -> str:
    if not rules:
        return part
    pairs = tuple(
        (
            str(rule["normalized"] if reverse else rule["source"]),
            str(rule["source"] if reverse else rule["normalized"]),
        )
        for rule in rules
        if str(rule["normalized"] if reverse else rule["source"])
    )
    if not pairs:
        return part
    pattern, by_source = _segment_translator(pairs)

    def normalize_match(match: re.Match[str]) -> str:
        entry = by_source.get(match.group(0).casefold())
        if entry is None:
            return match.group(0)
        _, normalized = entry
        return case_like(match.group(0), normalized)

    translated = pattern.sub(normalize_match, part)
    if translated != part:
        _validate_translated_part(translated, part)
    if not reverse:
        # Fail closed on segments that cannot round-trip (for example a
        # directory already named like a normalized value).
        restored = _translate_part(translated, rules, reverse=True)
        if restored != part:
            raise WorkspaceError(
                f"path segment does not round-trip through bidirectional vocabulary: {part!r}"
            )
    return translated


def _translate_relative(
    relative: str, rules: Sequence[Mapping[str, str]], *, reverse: bool = False
) -> str:
    pure = _safe_relative(relative)
    translated = [_translate_part(part, rules, reverse=reverse) for part in pure.parts]
    return PurePosixPath(*translated).as_posix()


def _load_translation_cache(
    cache_path: Path | None, rules_sha256: str
) -> dict[str, Any]:
    if cache_path is None or not cache_path.is_file():
        return {}
    try:
        loaded = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if (
        not isinstance(loaded, Mapping)
        or loaded.get("schema_version") != SCHEMA
        or loaded.get("rules_sha256") != rules_sha256
    ):
        return {}
    by_source = loaded.get("by_source")
    return dict(by_source) if isinstance(by_source, Mapping) else {}


def _save_translation_cache(
    cache_path: Path | None, rules_sha256: str, by_source: Mapping[str, Any]
) -> None:
    if cache_path is None:
        return
    try:
        _atomic_json(
            cache_path,
            {
                "schema_version": SCHEMA,
                "rules_sha256": rules_sha256,
                "by_source": dict(by_source),
            },
        )
    except OSError:
        pass  # The cache is an optimization only; a failed write must not fail the scan.


def _translated_manifest(
    manifest: Mapping[str, Any],
    rules: Sequence[Mapping[str, str]],
    *,
    root: Path | None = None,
    file_cap: int = DEFAULT_FILE_CAP,
    identity_cache_path: Path | None = None,
) -> dict[str, Any]:
    if not rules:
        return dict(manifest)
    rules_sha256 = _sha_bytes(_canonical(list(rules)))
    translated_by_source = _load_translation_cache(identity_cache_path, rules_sha256)
    fresh_by_source: dict[str, Any] = {}
    files: dict[str, dict[str, Any]] = {}
    directories: list[str] = []
    folded: dict[str, str] = {}

    def add_path(path: str, original: str) -> None:
        key = unicodedata.normalize("NFC", path).casefold()
        previous = folded.get(key)
        if previous is not None and previous != original:
            raise WorkspaceError(
                f"path normalization collision: {previous!r}, {original!r}"
            )
        folded[key] = original

    for directory in manifest["directories"]:
        translated = _translate_relative(directory, rules)
        add_path(translated, directory)
        directories.append(translated)
    for relative, identity in manifest["files"].items():
        translated = _translate_relative(relative, rules)
        add_path(translated, relative)
        if root is None:
            files[translated] = dict(identity)
        else:
            source_sha = str(identity.get("sha256", ""))
            cached = translated_by_source.get(source_sha)
            if (
                isinstance(cached, Mapping)
                and isinstance(cached.get("bytes"), int)
                and isinstance(cached.get("sha256"), str)
            ):
                translated_identity = {
                    "bytes": cached["bytes"],
                    "sha256": cached["sha256"],
                }
            else:
                source_path = root / Path(*PurePosixPath(relative).parts)
                translated_identity = _translated_file_identity(
                    source_path, rules, cap=file_cap
                )
            if source_sha:
                fresh_by_source[source_sha] = dict(translated_identity)
            files[translated] = {
                **translated_identity,
                "executable": bool(identity.get("executable")),
            }
    if root is not None:
        _save_translation_cache(identity_cache_path, rules_sha256, fresh_by_source)
    translated_manifest = {
        "files": files,
        "directories": sorted(directories),
        "bytes": sum(identity["bytes"] for identity in files.values()),
    }
    translated_manifest["sha256"] = _sha_bytes(_canonical(translated_manifest))
    return translated_manifest


def _load_scan_cache(cache_path: Path | None) -> dict[str, Any]:
    if cache_path is None or not cache_path.is_file():
        return {}
    try:
        loaded = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(loaded, Mapping) or loaded.get("schema_version") != SCHEMA:
        return {}
    files = loaded.get("files")
    return dict(files) if isinstance(files, Mapping) else {}


def _save_scan_cache(cache_path: Path | None, files: Mapping[str, Any]) -> None:
    if cache_path is None:
        return
    try:
        _atomic_json(cache_path, {"schema_version": SCHEMA, "files": dict(files)})
    except OSError:
        pass  # The cache is an optimization only; a failed write must not fail the scan.


def _scan(
    root: Path,
    *,
    git_mode: bool,
    file_cap: int,
    tree_cap: int,
    cache_path: Path | None = None,
) -> dict[str, Any]:
    files: dict[str, dict[str, Any]] = {}
    directories: list[str] = []
    folded: dict[str, str] = {}
    total = 0
    cache = _load_scan_cache(cache_path)
    fresh: dict[str, dict[str, Any]] = {}

    def visit(directory: Path, prefix: PurePosixPath | None = None) -> None:
        nonlocal total
        entries = sorted(os.scandir(directory), key=lambda item: item.name)
        for entry in entries:
            relative = (
                PurePosixPath(entry.name) if prefix is None else prefix / entry.name
            )
            name = relative.as_posix()
            if prefix is None and entry.name == MARKER:
                continue
            if git_mode and prefix is None and entry.name == ".git":
                continue
            key = unicodedata.normalize("NFC", name).casefold()
            previous = folded.get(key)
            if previous is not None and previous != name:
                raise WorkspaceError(
                    f"case/Unicode path collision: {previous!r}, {name!r}"
                )
            folded[key] = name
            path = Path(entry.path)
            info = entry.stat(follow_symlinks=False)
            attrs = getattr(info, "st_file_attributes", 0)
            if entry.is_symlink() or attrs & getattr(
                stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0
            ):
                raise WorkspaceError(f"links/reparse points are unsupported: {path}")
            if stat.S_ISDIR(info.st_mode):
                directories.append(name)
                visit(path, relative)
            elif stat.S_ISREG(info.st_mode):
                cached = cache.get(name)
                if (
                    isinstance(cached, Mapping)
                    and cached.get("size") == info.st_size
                    and cached.get("mtime_ns") == info.st_mtime_ns
                    and isinstance(cached.get("sha256"), str)
                ):
                    if info.st_size > file_cap:
                        raise WorkspaceError(
                            f"file exceeds {file_cap} byte cap: {path}"
                        )
                    identity = {
                        "bytes": info.st_size,
                        "sha256": str(cached["sha256"]),
                    }
                else:
                    identity = _file_identity(path, file_cap)
                total += identity["bytes"]
                if total > tree_cap:
                    raise WorkspaceError(f"workspace exceeds {tree_cap} byte cap")
                files[name] = {
                    **identity,
                    "executable": bool(info.st_mode & stat.S_IXUSR),
                }
                fresh[name] = {
                    "size": info.st_size,
                    "mtime_ns": info.st_mtime_ns,
                    "sha256": identity["sha256"],
                }
            else:
                raise WorkspaceError(f"special files are unsupported: {path}")

    visit(root)
    _save_scan_cache(cache_path, fresh)
    manifest = {"files": files, "directories": sorted(directories), "bytes": total}
    manifest["sha256"] = _sha_bytes(_canonical(manifest))
    return manifest


def _copy_blob(source: Path, destination: Path, expected: Mapping[str, Any]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".blob.", dir=destination.parent)
    try:
        with source.open("rb") as incoming, os.fdopen(fd, "wb") as outgoing:
            shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        identity = _file_identity(Path(temporary), int(expected["bytes"]))
        if identity != {"bytes": expected["bytes"], "sha256": expected["sha256"]}:
            raise WorkspaceError(f"file changed while copying: {source}")
        os.chmod(temporary, 0o755 if expected.get("executable") else 0o644)
        os.replace(temporary, destination)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _copy_translated_blob(
    source: Path,
    destination: Path,
    rules: Sequence[Mapping[str, str]],
    *,
    reverse: bool = False,
) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = _translate_payload(source.read_bytes(), rules, reverse=reverse)
    fd, temporary = tempfile.mkstemp(prefix=".blob.", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as outgoing:
            outgoing.write(payload)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        executable = bool(source.stat().st_mode & stat.S_IXUSR)
        os.chmod(temporary, 0o755 if executable else 0o644)
        identity = {"bytes": len(payload), "sha256": _sha_bytes(payload)}
        os.replace(temporary, destination)
        return {**identity, "executable": executable}
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _materialize_snapshot(
    root: Path,
    state_path: Path,
    manifest: dict[str, Any],
    *,
    path_mapper: Any = None,
    payload_rules: Sequence[Mapping[str, str]] | None = None,
) -> None:
    blobs = state_path / "base" / "blobs"
    blobs.mkdir(parents=True, exist_ok=True)
    for relative, identity in manifest["files"].items():
        blob = blobs / identity["sha256"]
        if not blob.exists():
            mapped = path_mapper(relative) if path_mapper is not None else relative
            source = root / Path(*PurePosixPath(mapped).parts)
            if payload_rules:
                copied = _copy_translated_blob(source, blob, payload_rules)
                if {
                    "bytes": copied["bytes"],
                    "sha256": copied["sha256"],
                } != {"bytes": identity["bytes"], "sha256": identity["sha256"]}:
                    raise WorkspaceError(f"snapshot translation mismatch: {relative}")
            else:
                _copy_blob(source, blob, identity)
    _atomic_json(state_path / "base" / "manifest.json", manifest)


def _git(
    source: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", "-C", str(source), *args],
        shell=False,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and completed.returncode:
        raise WorkspaceError(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed


def _git_preflight(source: Path) -> dict[str, str]:
    top = Path(_git(source, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
    if top != source:
        raise WorkspaceError(f"Git source must be the worktree root: {top}")
    if (source / ".gitmodules").exists():
        raise WorkspaceError("Git submodules require an explicit future policy")
    staged = _git(source, "ls-files", "--stage", "-z").stdout.split("\0")
    if any(record.startswith("160000 ") for record in staged if record):
        raise WorkspaceError("Git submodule entries require an explicit future policy")
    sparse = _git(source, "config", "--bool", "core.sparseCheckout", check=False)
    if sparse.returncode == 0 and sparse.stdout.strip().lower() == "true":
        raise WorkspaceError("sparse checkout is unsupported")
    tracked = _git(source, "ls-files", "-z").stdout.split("\0")
    for relative in tracked:
        if not relative:
            continue
        if PurePosixPath(relative).name == ".lfsconfig":
            raise WorkspaceError(
                "Git LFS configuration requires an explicit future policy"
            )
        if PurePosixPath(relative).name == ".gitattributes":
            attributes = source / Path(*_safe_relative(relative).parts)
            if _is_link_or_reparse(attributes):
                raise WorkspaceError(
                    f"linked Git attributes file is unsupported: {relative}"
                )
            if re.search(r"filter\s*=\s*lfs", attributes.read_text()):
                raise WorkspaceError("Git LFS requires an explicit future policy")
    return {
        "head": _git(source, "rev-parse", "HEAD").stdout.strip(),
        "git_dir": _git(source, "rev-parse", "--git-dir").stdout.strip(),
    }


def _state_from_mirror(mirror: Path) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    mirror = _absolute_directory(mirror, "mirror")
    marker_path = mirror / MARKER
    if not marker_path.is_file() or _is_link_or_reparse(marker_path):
        raise WorkspaceError(f"missing safe ownership marker: {marker_path}")
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    state_path = Path(marker.get("state", ""))
    state_path = _absolute_directory(state_path, "workspace state")
    state_file = state_path / "state.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    _verify_state(state)
    if marker.get("workspace_id") != state.get("workspace_id") or marker.get(
        "token"
    ) != state.get("token"):
        raise WorkspaceError("workspace ownership identity mismatch")
    if Path(state["mirror"]).resolve() != mirror or state.get("closed"):
        raise WorkspaceError("workspace state does not describe this open mirror")
    return state_path, state, marker


def _validate_aliases(
    aliases: Mapping[str, Mapping[str, Any]] | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, raw in (aliases or {}).items():
        if not ALIAS_RE.fullmatch(name) or not isinstance(raw, Mapping):
            raise WorkspaceError(f"invalid command alias: {name!r}")
        executable = Path(str(raw.get("executable", "")))
        if (
            not executable.is_absolute()
            or not executable.is_file()
            or _is_link_or_reparse(executable)
        ):
            raise WorkspaceError(
                f"alias executable must be an absolute nonlinked file: {name}"
            )
        fixed = raw.get("fixed_argv", [])
        if not isinstance(fixed, list) or any(
            not isinstance(item, str) for item in fixed
        ):
            raise WorkspaceError(f"alias fixed_argv must be a string list: {name}")
        identity = _file_identity(executable)
        expected = raw.get("sha256", identity["sha256"])
        if expected != identity["sha256"]:
            raise WorkspaceError(f"alias executable hash mismatch: {name}")
        result[name] = {
            "executable": str(executable.resolve()),
            "sha256": expected,
            "bytes": identity["bytes"],
            "fixed_argv": fixed,
        }
    return result


def _copy_translated_tree(
    source: Path,
    mirror: Path,
    manifest: Mapping[str, Any],
    rules: Sequence[Mapping[str, str]],
) -> None:
    for directory in sorted(
        manifest["directories"], key=lambda item: len(PurePosixPath(item).parts)
    ):
        translated = _translate_relative(directory, rules)
        (mirror / Path(*PurePosixPath(translated).parts)).mkdir(
            parents=True, exist_ok=True
        )
    for relative, identity in manifest["files"].items():
        translated = _translate_relative(relative, rules)
        source_path = source / Path(*PurePosixPath(relative).parts)
        target_path = mirror / Path(*PurePosixPath(translated).parts)
        copied = _copy_translated_blob(source_path, target_path, rules)
        expected = _translated_file_identity(source_path, rules)
        if {"bytes": copied["bytes"], "sha256": copied["sha256"]} != expected:
            raise WorkspaceError(f"file changed while translating: {source_path}")


def create_workspace(
    source: str | Path,
    managed_root: str | Path,
    *,
    name: str | None = None,
    mode: str = "auto",
    command_aliases: Mapping[str, Mapping[str, Any]] | None = None,
    path_rules: Sequence[Any] | None = None,
    file_cap: int = DEFAULT_FILE_CAP,
    tree_cap: int = DEFAULT_TREE_CAP,
) -> dict[str, Any]:
    if (
        isinstance(file_cap, bool)
        or not isinstance(file_cap, int)
        or file_cap <= 0
        or isinstance(tree_cap, bool)
        or not isinstance(tree_cap, int)
        or tree_cap <= 0
        or file_cap > tree_cap
    ):
        raise WorkspaceError("caps must be positive integers with file_cap <= tree_cap")
    source = _absolute_directory(Path(source), "source")
    managed = _absolute_directory(Path(managed_root), "managed root", create=True)
    if not _separate(source, managed):
        raise WorkspaceError("source and managed root must not contain one another")
    name = name or f"workspace-{uuid.uuid4().hex[:12]}"
    if not NAME_RE.fullmatch(name):
        raise WorkspaceError("workspace name must match workspace-<12 lowercase hex>")
    mirror = managed / name
    state_path = managed / STATE_DIR / name
    if mirror.exists() or state_path.exists():
        raise WorkspaceError("workspace mirror or state already exists")
    if mode not in {"auto", "git", "filesystem"}:
        raise WorkspaceError("mode must be auto, git, or filesystem")
    detected_git = (
        _git(source, "rev-parse", "--is-inside-work-tree", check=False).returncode == 0
    )
    actual_mode = (
        "git" if (mode == "git" or mode == "auto" and detected_git) else "filesystem"
    )
    if mode == "git" and not detected_git:
        raise WorkspaceError("requested Git mode for a non-Git source")
    aliases = _validate_aliases(command_aliases)
    translation_rules = _path_rule_payload(path_rules)
    git_identity: dict[str, str] | None = None
    state_path.mkdir(parents=True)
    try:
        if translation_rules and actual_mode == "git":
            raise WorkspaceError(
                "normalized path workspaces support filesystem mode only"
            )
        if actual_mode == "git":
            git_identity = _git_preflight(source)
            completed = _git(
                source, "worktree", "add", "--detach", str(mirror), git_identity["head"]
            )
            if completed.returncode:
                raise WorkspaceError(completed.stderr.strip())
        else:
            mirror.mkdir()
            # Refuse unsupported entries before copying any source payload.
            source_manifest = _scan(
                source,
                git_mode=False,
                file_cap=file_cap,
                tree_cap=tree_cap,
                cache_path=state_path / "cache" / "source-scan.json",
            )
            if translation_rules:
                _translated_manifest(
                    source_manifest,
                    translation_rules,
                    root=source,
                    file_cap=file_cap,
                    identity_cache_path=state_path
                    / "cache"
                    / "source-translation.json",
                )
                _copy_translated_tree(
                    source, mirror, source_manifest, translation_rules
                )
            else:
                for entry in os.scandir(source):
                    destination = mirror / entry.name
                    if entry.is_dir(follow_symlinks=False):
                        shutil.copytree(entry.path, destination, symlinks=False)
                    else:
                        shutil.copy2(entry.path, destination, follow_symlinks=False)
        token = uuid.uuid4().hex
        marker = {
            "schema_version": SCHEMA,
            "workspace_id": name,
            "state": str(state_path.resolve()),
            "token": token,
        }
        _atomic_json(mirror / MARKER, marker)
        base = _scan(
            mirror,
            git_mode=actual_mode == "git",
            file_cap=file_cap,
            tree_cap=tree_cap,
            cache_path=state_path / "cache" / "mirror-scan.json",
        )
        _materialize_snapshot(mirror, state_path, base)
        state = {
            "schema_version": SCHEMA,
            "workspace_id": name,
            "token": token,
            "source": str(source),
            "mirror": str(mirror.resolve()),
            "managed_root": str(managed),
            "mode": actual_mode,
            "git": git_identity,
            "file_cap": file_cap,
            "tree_cap": tree_cap,
            "command_aliases": aliases,
            "closed": False,
            "path_normalization": {
                "enabled": bool(translation_rules),
                "rules": translation_rules,
            },
            "created_ns": time.time_ns(),
            "platform": platform.platform(),
        }
        _atomic_json(state_path / "state.json", _seal_state(state))
        return {
            "workspace_id": name,
            "source": str(source),
            "mirror": str(mirror),
            "mode": actual_mode,
            "base_sha256": base["sha256"],
            "path_normalization": bool(translation_rules),
        }
    except Exception:
        if actual_mode == "git" and mirror.exists():
            _git(source, "worktree", "remove", "--force", str(mirror), check=False)
        elif mirror.exists():
            shutil.rmtree(mirror)
        if state_path.exists():
            shutil.rmtree(state_path)
        raise


def _cache_paths(state: Mapping[str, Any]) -> dict[str, Path]:
    cache_dir = (
        Path(state["managed_root"]) / STATE_DIR / str(state["workspace_id"]) / "cache"
    )
    return {
        "source_scan": cache_dir / "source-scan.json",
        "mirror_scan": cache_dir / "mirror-scan.json",
        "source_translation": cache_dir / "source-translation.json",
    }


def _current(
    state: Mapping[str, Any],
    *,
    use_cache: bool = True,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    base = json.loads(
        (
            Path(state["managed_root"])
            / STATE_DIR
            / state["workspace_id"]
            / "base"
            / "manifest.json"
        ).read_text()
    )
    _verify_manifest(base)
    kwargs = {
        "git_mode": state["mode"] == "git",
        "file_cap": state["file_cap"],
        "tree_cap": state["tree_cap"],
    }
    caches: dict[str, Path] = _cache_paths(state) if use_cache else {}
    rules = _path_rules_from_state(state)
    source = _scan(
        Path(state["source"]), cache_path=caches.get("source_scan"), **kwargs
    )
    if rules:
        source = _translated_manifest(
            source,
            rules,
            root=Path(state["source"]),
            file_cap=int(state["file_cap"]),
            identity_cache_path=caches.get("source_translation"),
        )
    mirror = _scan(
        Path(state["mirror"]), cache_path=caches.get("mirror_scan"), **kwargs
    )
    return base, source, mirror


def status_workspace(mirror: str | Path, *, use_cache: bool = True) -> dict[str, Any]:
    _, state, _ = _state_from_mirror(Path(mirror))
    base, source, current_mirror = _current(state, use_cache=use_cache)
    return {
        "workspace_id": state["workspace_id"],
        "base_sha256": base["sha256"],
        "source_sha256": source["sha256"],
        "mirror_sha256": current_mirror["sha256"],
        "source_changed": source["sha256"] != base["sha256"],
        "mirror_changed": current_mirror["sha256"] != base["sha256"],
        "converged": source["sha256"] == current_mirror["sha256"],
        "clean": source["sha256"] == current_mirror["sha256"] == base["sha256"],
    }


def verify_workspace(mirror: str | Path) -> dict[str, Any]:
    state_path, state, marker = _state_from_mirror(Path(mirror))
    # Verification is the trust anchor: bypass the (size, mtime) scan cache and
    # re-hash every file so a stale or tampered cache cannot mask a change.
    status = status_workspace(mirror, use_cache=False)
    base = json.loads((state_path / "base" / "manifest.json").read_text())
    for identity in base["files"].values():
        blob = state_path / "base" / "blobs" / identity["sha256"]
        if (
            not blob.is_file()
            or _file_identity(blob, state["file_cap"])["sha256"] != identity["sha256"]
        ):
            raise WorkspaceError(f"missing or corrupt base blob: {identity['sha256']}")
    return {
        **status,
        "ownership_token_sha256": _sha_bytes(marker["token"].encode()),
        "base_verified": True,
    }


def _same(left: dict[str, Any] | None, right: dict[str, Any] | None) -> bool:
    return left == right


def _entries(manifest: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    entries = {path: {"type": "directory"} for path in manifest["directories"]}
    entries.update(
        {
            path: {"type": "file", **identity}
            for path, identity in manifest["files"].items()
        }
    )
    return entries


def _entry_at(
    root: Path,
    relative: str,
    *,
    state: Mapping[str, Any],
    virtual_source: bool = False,
) -> dict[str, Any] | None:
    pure = _safe_relative(relative)
    parent = (
        root
        if len(pure.parts) == 1
        else _target_path(root, PurePosixPath(*pure.parts[:-1]).as_posix())
    )
    name = pure.name
    try:
        with os.scandir(parent) as entries:
            entry = next(item for item in entries if item.name == name)
    except (FileNotFoundError, StopIteration):
        return None
    info = entry.stat(follow_symlinks=False)
    attrs = getattr(info, "st_file_attributes", 0)
    if entry.is_symlink() or attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
        raise WorkspaceError(f"target is a link/reparse point: {relative}")
    if stat.S_ISDIR(info.st_mode):
        return {"type": "directory"}
    if stat.S_ISREG(info.st_mode):
        identity = (
            _translated_file_identity(
                Path(entry.path),
                _path_rules_from_state(state),
                cap=int(state["file_cap"]),
            )
            if virtual_source
            else _file_identity(Path(entry.path), int(state["file_cap"]))
        )
        return {
            "type": "file",
            **identity,
            "executable": bool(info.st_mode & stat.S_IXUSR),
        }
    raise WorkspaceError(f"special files are unsupported: {Path(entry.path)}")


def plan_workspace(mirror: str | Path, *, direction: str) -> dict[str, Any]:
    _, state, _ = _state_from_mirror(Path(mirror))
    if direction not in {"to-mirror", "back"}:
        raise WorkspaceError("direction must be to-mirror or back")
    base, source, current_mirror = _current(state)
    desired = source if direction == "to-mirror" else current_mirror
    operations: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    base_entries, source_entries, mirror_entries = map(
        _entries, (base, source, current_mirror)
    )
    desired_entries = source_entries if direction == "to-mirror" else mirror_entries
    target_entries = mirror_entries if direction == "to-mirror" else source_entries
    paths = sorted(set(base_entries) | set(source_entries) | set(mirror_entries))
    for relative in paths:
        before = base_entries.get(relative)
        wanted = desired_entries.get(relative)
        present = target_entries.get(relative)
        if _same(wanted, present):
            continue
        if (
            wanted is not None
            and present is not None
            and wanted["type"] != present["type"]
        ):
            conflicts.append(
                {
                    "path": relative,
                    "reason": "file/directory type change",
                    "base": before,
                    "source": source_entries.get(relative),
                    "mirror": mirror_entries.get(relative),
                }
            )
            continue
        if _same(present, before):
            if wanted is None:
                kind = (
                    "rmdir" if present and present["type"] == "directory" else "delete"
                )
            elif wanted["type"] == "directory":
                kind = "mkdir"
            else:
                kind = "add" if present is None else "modify"
            operations.append(
                {
                    "kind": kind,
                    "path": relative,
                    "preimage": present,
                    "postimage": wanted,
                }
            )
        else:
            conflicts.append(
                {
                    "path": relative,
                    "base": before,
                    "source": source_entries.get(relative),
                    "mirror": mirror_entries.get(relative),
                }
            )
    order = {"mkdir": 0, "add": 1, "modify": 1, "delete": 2, "rmdir": 3}
    operations.sort(
        key=lambda item: (
            order[item["kind"]],
            -len(PurePosixPath(item["path"]).parts)
            if item["kind"] == "rmdir"
            else len(PurePosixPath(item["path"]).parts),
            item["path"],
        )
    )
    payload = {
        "schema_version": SCHEMA,
        "workspace_id": state["workspace_id"],
        "direction": direction,
        "base_sha256": base["sha256"],
        "source_sha256": source["sha256"],
        "mirror_sha256": current_mirror["sha256"],
        "desired_manifest": desired,
        "operations": operations,
        "conflicts": conflicts,
    }
    payload["plan_sha256"] = _sha_bytes(_canonical(payload))
    return payload


def conflicts_workspace(mirror: str | Path, *, direction: str) -> list[dict[str, Any]]:
    return plan_workspace(mirror, direction=direction)["conflicts"]


@contextmanager
def _lock(state_path: Path, *, recover: bool = False) -> Iterator[None]:
    path = state_path / "lock.json"
    if recover and path.exists():
        os.replace(path, state_path / f"recovered-lock-{time.time_ns()}.json")
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise WorkspaceError(
            "workspace is locked; use explicit recovery, never break it blindly"
        ) from exc
    try:
        os.write(
            fd,
            _canonical(
                {
                    "pid": os.getpid(),
                    "host": platform.node(),
                    "started_ns": time.time_ns(),
                    "nonce": uuid.uuid4().hex,
                }
            ),
        )
        os.fsync(fd)
        os.close(fd)
        yield
    finally:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _target_path(root: Path, relative: str) -> Path:
    pure = _safe_relative(relative)
    current = root
    for part in pure.parts[:-1]:
        current = current / part
        if current.exists() and _is_link_or_reparse(current):
            raise WorkspaceError(f"target crosses a link/reparse point: {relative}")
    target = root / Path(*pure.parts)
    if target.exists() and _is_link_or_reparse(target):
        raise WorkspaceError(f"target is a link/reparse point: {relative}")
    return target


def _source_relative(state: Mapping[str, Any], relative: str) -> str:
    return _translate_relative(relative, _path_rules_from_state(state), reverse=True)


def _operation_relative_for_target(
    state: Mapping[str, Any], relative: str, target_root: Path
) -> str:
    if target_root.resolve(strict=False) == Path(state["source"]).resolve(strict=False):
        return _source_relative(state, relative)
    return relative


def _manifest_for_target_scan(
    state: Mapping[str, Any],
    target_root: Path,
    scanned: dict[str, Any],
    *,
    identity_cache_path: Path | None = None,
) -> dict[str, Any]:
    if target_root.resolve(strict=False) == Path(state["source"]).resolve(strict=False):
        return _translated_manifest(
            scanned,
            _path_rules_from_state(state),
            root=target_root,
            file_cap=int(state["file_cap"]),
            identity_cache_path=identity_cache_path,
        )
    return scanned


def _materialize_path_mapper(state: Mapping[str, Any], target_root: Path):
    if target_root.resolve(strict=False) == Path(state["source"]).resolve(strict=False):
        return lambda relative: _source_relative(state, relative)
    return None


def _save_journal(path: Path, journal: dict[str, Any]) -> None:
    _atomic_json(path / "journal.json", journal)


def _run_transaction(
    state_path: Path,
    state: dict[str, Any],
    txn: Path,
    journal: dict[str, Any],
    *,
    fault_after_operations: int | None = None,
) -> None:
    plan = journal["plan"]
    target_root = Path(
        state["mirror"] if plan["direction"] == "to-mirror" else state["source"]
    )
    target_is_source = target_root.resolve(strict=False) == Path(
        state["source"]
    ).resolve(strict=False)
    for index, operation in enumerate(plan["operations"]):
        if index in journal["completed"]:
            continue
        relative = operation["path"]
        target_relative = _operation_relative_for_target(state, relative, target_root)
        target = _target_path(target_root, target_relative)
        current = _entry_at(
            target_root,
            target_relative,
            state=state,
            virtual_source=target_is_source,
        )
        if current != operation["preimage"]:
            raise WorkspaceError(f"stale transaction preimage: {relative}")
        backup = txn / "recovery" / Path(*_safe_relative(relative).parts)
        if target.exists() and target.is_file():
            backup.parent.mkdir(parents=True, exist_ok=True)
            _copy_blob(target, backup, _file_identity(target, int(state["file_cap"])))
            journal["backups"][relative] = str(backup)
            _save_journal(txn, journal)
        if operation["kind"] == "mkdir":
            target.mkdir(parents=True)
        elif operation["kind"] == "rmdir":
            target.rmdir()
        elif operation["kind"] == "delete":
            target.unlink()
        else:
            source = txn / "staging" / Path(*_safe_relative(relative).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            _copy_blob(source, target, _file_identity(source, int(state["file_cap"])))
        journal["completed"].append(index)
        _save_journal(txn, journal)
        if (
            fault_after_operations is not None
            and len(journal["completed"]) >= fault_after_operations
        ):
            raise SimulatedCrash("simulated crash after durable journal update")
    caches = _cache_paths(state)
    scanned_target = _scan(
        target_root,
        git_mode=state["mode"] == "git",
        file_cap=state["file_cap"],
        tree_cap=state["tree_cap"],
        cache_path=caches["source_scan" if target_is_source else "mirror_scan"],
    )
    current_target = _manifest_for_target_scan(
        state,
        target_root,
        scanned_target,
        identity_cache_path=caches["source_translation"] if target_is_source else None,
    )
    if current_target["sha256"] != plan["desired_manifest"]["sha256"]:
        raise WorkspaceError("post-transaction manifest mismatch")
    _materialize_snapshot(
        target_root,
        state_path,
        current_target,
        path_mapper=_materialize_path_mapper(state, target_root),
        payload_rules=_path_rules_from_state(state) if target_is_source else None,
    )
    journal["status"] = "complete"
    _save_journal(txn, journal)
    (state_path / "pending.json").unlink(missing_ok=True)


def apply_plan(
    mirror: str | Path,
    plan: Mapping[str, Any],
    *,
    expected_sha256: str,
    fault_after_operations: int | None = None,
) -> dict[str, Any]:
    state_path, state, _ = _state_from_mirror(Path(mirror))
    supplied = dict(plan)
    claimed = supplied.pop("plan_sha256", None)
    actual = _sha_bytes(_canonical(supplied))
    if claimed != actual or expected_sha256 != actual:
        raise WorkspaceError("plan hash mismatch")
    if plan.get("workspace_id") != state["workspace_id"]:
        raise WorkspaceError("plan belongs to another workspace")
    if plan.get("conflicts"):
        raise WorkspaceConflict("whole plan rejected because conflicts exist")
    if (state_path / "pending.json").exists():
        raise WorkspaceError("workspace has a pending transaction; recover it first")
    current = status_workspace(mirror)
    for name in ("base_sha256", "source_sha256", "mirror_sha256"):
        if current[name] != plan[name]:
            raise WorkspaceError(f"stale plan: {name} changed")
    regenerated = plan_workspace(mirror, direction=str(plan.get("direction")))
    if _canonical(regenerated) != _canonical(dict(plan)):
        raise WorkspaceError("plan contents do not match the current canonical plan")
    with _lock(state_path):
        transaction_id = f"txn-{uuid.uuid4().hex}"
        txn = state_path / "transactions" / transaction_id
        txn.mkdir(parents=True)
        journal = {
            "schema_version": SCHEMA,
            "status": "applying",
            "plan": dict(plan),
            "completed": [],
            "backups": {},
        }
        _save_journal(txn, journal)
        _atomic_json(state_path / "pending.json", {"transaction": transaction_id})
        desired_root = Path(
            state["source"] if plan["direction"] == "to-mirror" else state["mirror"]
        )
        for operation in plan["operations"]:
            if operation["kind"] in {"add", "modify"}:
                relative = operation["path"]
                source_relative = (
                    _source_relative(state, relative)
                    if desired_root.resolve(strict=False)
                    == Path(state["source"]).resolve(strict=False)
                    else relative
                )
                source = _target_path(desired_root, source_relative)
                stage = txn / "staging" / Path(*_safe_relative(relative).parts)
                rules = _path_rules_from_state(state)
                if rules:
                    copied = _copy_translated_blob(
                        source,
                        stage,
                        rules,
                        reverse=plan["direction"] == "back",
                    )
                    if plan["direction"] == "to-mirror":
                        staged_virtual = copied
                    else:
                        staged_virtual = {
                            **_translated_file_identity(
                                stage,
                                rules,
                                cap=int(state["file_cap"]),
                            ),
                            "executable": bool(copied.get("executable")),
                        }
                    expected = operation["postimage"]
                    if {
                        "bytes": staged_virtual["bytes"],
                        "sha256": staged_virtual["sha256"],
                    } != {"bytes": expected["bytes"], "sha256": expected["sha256"]}:
                        raise WorkspaceError(f"translated staging mismatch: {relative}")
                else:
                    _copy_blob(source, stage, operation["postimage"])
        _run_transaction(
            state_path,
            state,
            txn,
            journal,
            fault_after_operations=fault_after_operations,
        )
    return {
        "workspace_id": state["workspace_id"],
        "transaction": transaction_id,
        "status": "complete",
        "operations": len(plan["operations"]),
    }


def recover_workspace(mirror: str | Path, *, action: str) -> dict[str, Any]:
    state_path, state, _ = _state_from_mirror(Path(mirror))
    if action not in {"resume", "rollback"}:
        raise WorkspaceError("recovery action must be resume or rollback")
    pending_path = state_path / "pending.json"
    if not pending_path.exists():
        raise WorkspaceError("no pending transaction")
    transaction_id = json.loads(pending_path.read_text())["transaction"]
    txn = state_path / "transactions" / transaction_id
    journal = json.loads((txn / "journal.json").read_text())
    with _lock(state_path, recover=True):
        if action == "resume":
            _run_transaction(state_path, state, txn, journal)
        else:
            plan = journal["plan"]
            target_root = Path(
                state["mirror"] if plan["direction"] == "to-mirror" else state["source"]
            )
            target_is_source = target_root.resolve(strict=False) == Path(
                state["source"]
            ).resolve(strict=False)
            for index in reversed(journal["completed"]):
                operation = plan["operations"][index]
                target_relative = _operation_relative_for_target(
                    state, operation["path"], target_root
                )
                target = _target_path(target_root, target_relative)
                expected = operation["postimage"]
                current = _entry_at(
                    target_root,
                    target_relative,
                    state=state,
                    virtual_source=target_is_source,
                )
                if current != expected:
                    raise WorkspaceError(
                        f"rollback postimage changed: {operation['path']}"
                    )
                backup_name = journal["backups"].get(operation["path"])
                if backup_name:
                    backup = Path(backup_name)
                    _copy_blob(
                        backup,
                        target,
                        _file_identity(backup, int(state["file_cap"])),
                    )
                elif operation["kind"] == "mkdir":
                    target.rmdir()
                elif operation["kind"] == "rmdir":
                    target.mkdir(parents=True)
                elif target.exists():
                    target.unlink()
            journal["status"] = "rolled-back"
            _save_journal(txn, journal)
            pending_path.unlink()
    return {
        "workspace_id": state["workspace_id"],
        "transaction": transaction_id,
        "status": "complete" if action == "resume" else "rolled-back",
    }


def exec_alias(
    mirror: str | Path,
    alias: str,
    args: Sequence[str] = (),
    *,
    timeout: float | None = None,
    capture_output: bool = True,
) -> subprocess.CompletedProcess[str]:
    _, state, _ = _state_from_mirror(Path(mirror))
    mapping = state["command_aliases"].get(alias)
    if mapping is None:
        raise WorkspaceError(f"unknown command alias: {alias}")
    executable = Path(mapping["executable"])
    if (
        not executable.is_absolute()
        or not executable.is_file()
        or _is_link_or_reparse(executable)
    ):
        raise WorkspaceError(f"alias executable is unavailable: {alias}")
    if _file_identity(executable)["sha256"] != mapping["sha256"]:
        raise WorkspaceError(f"alias executable hash changed: {alias}")
    if any(not isinstance(item, str) for item in args):
        raise WorkspaceError("alias arguments must be strings")
    return subprocess.run(
        [str(executable), *mapping["fixed_argv"], *args],
        cwd=state["mirror"],
        shell=False,
        text=True,
        capture_output=capture_output,
        timeout=timeout,
        check=False,
    )


def close_workspace(mirror: str | Path, *, archive: bool = True) -> dict[str, Any]:
    state_path, state, _ = _state_from_mirror(Path(mirror))
    status = status_workspace(mirror)
    if not status["clean"]:
        raise WorkspaceError("refusing to close a workspace with unmerged changes")
    if (state_path / "pending.json").exists():
        raise WorkspaceError("recover the pending transaction before close")
    with _lock(state_path):
        mirror_path = Path(state["mirror"])
        if state["mode"] == "git":
            marker_path = mirror_path / MARKER
            marker_payload = marker_path.read_bytes()
            marker_path.unlink()
            try:
                _git(Path(state["source"]), "worktree", "remove", str(mirror_path))
            except Exception:
                marker_path.write_bytes(marker_payload)
                raise
        elif archive:
            archive_path = state_path / "closed-mirror"
            if archive_path.exists():
                raise WorkspaceError("closed mirror archive already exists")
            os.replace(mirror_path, archive_path)
        else:
            # Quarantine even when archive=False; cleanup performs the actual deletion.
            quarantine = state_path / "deletion-quarantine"
            os.replace(mirror_path, quarantine)
        state["closed"] = True
        state["closed_ns"] = time.time_ns()
        _atomic_json(state_path / "state.json", _seal_state(state))
    return {
        "workspace_id": state["workspace_id"],
        "closed": True,
        "archived": bool(archive and state["mode"] != "git"),
    }


def cleanup_workspaces(
    managed_root: str | Path, workspace_ids: Sequence[str]
) -> list[str]:
    managed = _absolute_directory(Path(managed_root), "managed root")
    removed: list[str] = []
    for workspace_id in workspace_ids:
        if not NAME_RE.fullmatch(workspace_id):
            raise WorkspaceError(f"invalid cleanup workspace ID: {workspace_id}")
        state_path = managed / STATE_DIR / workspace_id
        state_path = _absolute_directory(state_path, "closed workspace state")
        state = json.loads((state_path / "state.json").read_text())
        _verify_state(state)
        if (
            not state.get("closed")
            or Path(state["source"]).resolve() == state_path.resolve()
        ):
            raise WorkspaceError(f"workspace is not safely closed: {workspace_id}")
        if (
            Path(state["managed_root"]).resolve() != managed
            or state["workspace_id"] != workspace_id
        ):
            raise WorkspaceError("cleanup ownership mismatch")
        open_mirror = managed / workspace_id
        if open_mirror.exists():
            raise WorkspaceError("refusing cleanup while mirror path still exists")
        shutil.rmtree(state_path)
        removed.append(workspace_id)
    return removed
