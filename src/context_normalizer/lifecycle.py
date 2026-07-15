from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

from .config import config_dir
from .workspace import (
    MARKER,
    STATE_DIR,
    cleanup_workspaces,
    close_workspace,
    verify_workspace,
)


OWNED_FILES = (
    "rules.tsv",
    "rules.tsv.previous",
    "path-rules.tsv",
    "path-rules.tsv.previous",
    "context.txt",
    "subagent-context.txt",
    "cues.txt",
    "cues.txt.previous",
    "installation.json",
    "runtime-python.txt",
    "profile-apply.lock",
)


_WORKSPACE_NAME = re.compile(r"workspace-[0-9a-f]{12}")
_TRANSACTION_NAME = re.compile(r"txn-[0-9a-f]{32}")
_HEX_DIGEST = re.compile(r"[0-9a-f]{64}")


def _is_reparse_or_symlink(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    except FileNotFoundError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def read_installation_marker() -> dict[str, object]:
    root = config_dir()
    if _is_reparse_or_symlink(root):
        raise ValueError(
            f"refusing linked or reparse-point configuration directory: {root}"
        )
    marker = root / "installation.json"
    if not marker.is_file() or _is_reparse_or_symlink(marker):
        raise ValueError(
            f"installation ownership marker is missing or unsafe: {marker}"
        )
    document = json.loads(marker.read_text(encoding="utf-8"))
    if document.get("schema_version") != 1:
        raise ValueError(f"unsupported installation marker schema: {marker}")
    recorded = Path(str(document.get("config_dir", ""))).resolve(strict=False)
    if recorded != root.resolve(strict=False):
        raise ValueError(f"installation marker path mismatch: {recorded} != {root}")
    if not document.get("installation_id") or not document.get("python_executable"):
        raise ValueError(f"incomplete installation marker: {marker}")
    return document


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _read_json_file(path: Path) -> dict[str, Any]:
    if not path.is_file() or _is_reparse_or_symlink(path):
        raise ValueError(f"managed workspace file is missing or unsafe: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"managed workspace JSON must be an object: {path}")
    return value


def _safe_relative(value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("managed workspace contains an invalid relative path")
    normalized = value.replace("\\", "/")
    path = Path(*normalized.split("/"))
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"managed workspace contains an unsafe relative path: {value}")
    return path


def _all_entries(root: Path) -> tuple[set[Path], set[Path]]:
    files: set[Path] = set()
    directories: set[Path] = set()
    pending = [root]
    while pending:
        directory = pending.pop()
        for entry in os.scandir(directory):
            path = Path(entry.path)
            relative = path.relative_to(root)
            info = entry.stat(follow_symlinks=False)
            attrs = getattr(info, "st_file_attributes", 0)
            if entry.is_symlink() or attrs & getattr(
                stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0
            ):
                raise ValueError(
                    f"managed workspace contains a link/reparse point: {path}"
                )
            if stat.S_ISDIR(info.st_mode):
                directories.add(relative)
                pending.append(path)
            elif stat.S_ISREG(info.st_mode):
                files.add(relative)
            else:
                raise ValueError(f"managed workspace contains a special file: {path}")
    return files, directories


def _parents(paths: set[Path]) -> set[Path]:
    result: set[Path] = set()
    for path in paths:
        parent = path.parent
        while parent != Path("."):
            result.add(parent)
            parent = parent.parent
    return result


def _verify_sealed_state(state: dict[str, Any], state_file: Path) -> None:
    claimed = state.get("state_sha256")
    unsigned = dict(state)
    unsigned.pop("state_sha256", None)
    actual = hashlib.sha256(_canonical(unsigned)).hexdigest()
    if claimed != actual:
        raise ValueError(
            f"managed workspace state integrity check failed: {state_file}"
        )


def _verify_archived_tree(
    archive: Path, state: dict[str, Any], manifest: dict[str, Any]
) -> None:
    files, directories = _all_entries(archive)
    marker_path = archive / MARKER
    marker = _read_json_file(marker_path)
    if (
        marker.get("workspace_id") != state["workspace_id"]
        or marker.get("token") != state["token"]
    ):
        raise ValueError("closed workspace marker does not match its state")
    expected_files = {Path(MARKER)}
    for relative, identity in manifest.get("files", {}).items():
        path = _safe_relative(relative)
        expected_files.add(path)
        payload = archive / path
        if not payload.is_file():
            raise ValueError(f"closed workspace payload is missing: {payload}")
        digest = hashlib.sha256(payload.read_bytes()).hexdigest()
        if payload.stat().st_size != identity.get("bytes") or digest != identity.get(
            "sha256"
        ):
            raise ValueError(f"closed workspace payload changed after close: {payload}")
    expected_directories = {
        _safe_relative(item) for item in manifest.get("directories", [])
    }
    if files != expected_files or directories != expected_directories:
        raise ValueError(
            f"closed workspace archive contains unknown entries: {archive}"
        )


def _workspace_state_inventory(state_path: Path, managed: Path) -> dict[str, Any]:
    state_file = state_path / "state.json"
    state = _read_json_file(state_file)
    _verify_sealed_state(state, state_file)
    workspace_id = state_path.name
    if (
        state.get("schema_version") != 1
        or not _WORKSPACE_NAME.fullmatch(workspace_id)
        or state.get("workspace_id") != workspace_id
        or Path(str(state.get("managed_root", ""))).resolve(strict=False)
        != managed.resolve(strict=True)
        or Path(str(state.get("mirror", ""))).resolve(strict=False)
        != (managed / workspace_id).resolve(strict=False)
    ):
        raise ValueError(f"managed workspace ownership mismatch: {state_path}")
    if (state_path / "pending.json").exists() or (state_path / "lock.json").exists():
        raise ValueError(
            f"managed workspace has pending work or a lock: {workspace_id}"
        )
    if not isinstance(state.get("closed"), bool):
        raise ValueError(
            f"managed workspace has an invalid lifecycle state: {workspace_id}"
        )

    manifest_path = state_path / "base" / "manifest.json"
    manifest = _read_json_file(manifest_path)
    unsigned_manifest = dict(manifest)
    claimed_manifest = unsigned_manifest.pop("sha256", None)
    if claimed_manifest != hashlib.sha256(_canonical(unsigned_manifest)).hexdigest():
        raise ValueError(
            f"managed workspace manifest integrity check failed: {manifest_path}"
        )

    expected_files = {Path("state.json"), Path("base/manifest.json")}
    expected_directories_extra = {Path("base"), Path("base/blobs")}
    cache_dir = state_path / "cache"
    if cache_dir.exists():
        if not cache_dir.is_dir() or _is_reparse_or_symlink(cache_dir):
            raise ValueError(f"unsafe managed workspace cache directory: {cache_dir}")
        expected_directories_extra.add(Path("cache"))
        for cache_name in (
            "source-scan.json",
            "mirror-scan.json",
            "source-translation.json",
        ):
            if (cache_dir / cache_name).is_file():
                expected_files.add(Path("cache") / cache_name)
    required_blobs: set[str] = set()
    allowed_blobs: set[str] = set()
    for identity in manifest.get("files", {}).values():
        digest = identity.get("sha256")
        if not isinstance(digest, str) or not _HEX_DIGEST.fullmatch(digest):
            raise ValueError(f"invalid managed workspace blob identity: {state_path}")
        required_blobs.add(digest)
        allowed_blobs.add(digest)

    transactions = state_path / "transactions"
    if transactions.exists():
        if not transactions.is_dir() or _is_reparse_or_symlink(transactions):
            raise ValueError(
                f"unsafe managed workspace transaction directory: {transactions}"
            )
        for transaction in transactions.iterdir():
            if (
                not transaction.is_dir()
                or _is_reparse_or_symlink(transaction)
                or not _TRANSACTION_NAME.fullmatch(transaction.name)
            ):
                raise ValueError(
                    f"unknown managed workspace transaction entry: {transaction}"
                )
            journal_rel = Path("transactions") / transaction.name / "journal.json"
            journal = _read_json_file(state_path / journal_rel)
            if journal.get("status") not in {"complete", "rolled-back"}:
                raise ValueError(
                    f"managed workspace transaction is not complete: {transaction}"
                )
            expected_files.add(journal_rel)
            plan = journal.get("plan")
            if not isinstance(plan, dict) or plan.get("workspace_id") != workspace_id:
                raise ValueError(f"transaction plan ownership mismatch: {transaction}")
            claimed_plan = plan.get("plan_sha256")
            unsigned_plan = dict(plan)
            unsigned_plan.pop("plan_sha256", None)
            if claimed_plan != hashlib.sha256(_canonical(unsigned_plan)).hexdigest():
                raise ValueError(
                    f"transaction plan integrity check failed: {transaction}"
                )
            for operation in plan.get("operations", []):
                if not isinstance(operation, dict):
                    raise ValueError(f"invalid transaction operation: {transaction}")
                if operation.get("kind") in {"add", "modify"}:
                    expected_files.add(
                        Path("transactions")
                        / transaction.name
                        / "staging"
                        / _safe_relative(operation.get("path"))
                    )
                for image_name in ("preimage", "postimage"):
                    image = operation.get(image_name)
                    if isinstance(image, dict) and image.get("type") == "file":
                        digest = image.get("sha256")
                        if not isinstance(digest, str) or not _HEX_DIGEST.fullmatch(
                            digest
                        ):
                            raise ValueError(
                                f"invalid transaction image: {transaction}"
                            )
                        allowed_blobs.add(digest)
            backups = journal.get("backups", {})
            if not isinstance(backups, dict):
                raise ValueError(
                    f"invalid transaction recovery inventory: {transaction}"
                )
            for relative, absolute in backups.items():
                expected = transaction / "recovery" / _safe_relative(relative)
                if Path(str(absolute)).resolve(strict=False) != expected.resolve(
                    strict=False
                ):
                    raise ValueError(
                        f"transaction recovery path mismatch: {transaction}"
                    )
                expected_files.add(expected.relative_to(state_path))

    actual_blobs: set[str] = set()
    for blob_path in (state_path / "base" / "blobs").iterdir():
        digest = blob_path.name
        if not blob_path.is_file() or _is_reparse_or_symlink(blob_path):
            raise ValueError(f"unsafe managed workspace blob: {blob_path}")
        if digest not in allowed_blobs:
            raise ValueError(f"unknown managed workspace blob: {blob_path}")
        actual_blobs.add(digest)
        blob = Path("base/blobs") / digest
        expected_files.add(blob)
        if hashlib.sha256(blob_path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"missing or corrupt managed workspace blob: {blob_path}")
    if not required_blobs.issubset(actual_blobs):
        raise ValueError(
            f"managed workspace is missing current base blobs: {state_path}"
        )

    closed = state.get("closed") is True
    mirror = managed / workspace_id
    if closed:
        if mirror.exists() or mirror.is_symlink():
            raise ValueError(f"closed workspace still has an active mirror: {mirror}")
        archives = [
            item
            for item in (
                state_path / "closed-mirror",
                state_path / "deletion-quarantine",
            )
            if item.exists()
        ]
        if state.get("mode") == "git":
            if archives:
                raise ValueError(
                    f"closed Git workspace has an unexpected archive: {state_path}"
                )
        elif len(archives) != 1:
            raise ValueError(
                "closed filesystem workspace archive is missing or ambiguous: "
                f"{state_path}"
            )
        else:
            archive = archives[0]
            _verify_archived_tree(archive, state, manifest)
            archive_files, archive_directories = _all_entries(archive)
            expected_files.update(
                archive.relative_to(state_path) / item for item in archive_files
            )
            expected_directories_extra.add(archive.relative_to(state_path))
            expected_directories_extra.update(
                archive.relative_to(state_path) / item for item in archive_directories
            )
    else:
        if not mirror.is_dir() or _is_reparse_or_symlink(mirror):
            raise ValueError(
                f"active managed workspace mirror is missing or unsafe: {mirror}"
            )
        verified = verify_workspace(mirror)
        if not verified.get("clean"):
            raise ValueError(f"managed workspace has unmerged changes: {workspace_id}")

    actual_files, actual_directories = _all_entries(state_path)
    expected_directories = _parents(expected_files) | expected_directories_extra
    if actual_files != expected_files or actual_directories != expected_directories:
        raise ValueError(
            f"managed workspace state contains unknown entries: {state_path}"
        )
    return {"workspace_id": workspace_id, "closed": closed, "mirror": mirror}


def _preflight_managed_workspaces(
    root: Path,
) -> tuple[Path | None, list[dict[str, Any]]]:
    managed = root / "workspaces"
    if not managed.exists() and not managed.is_symlink():
        return None, []
    if not managed.is_dir() or _is_reparse_or_symlink(managed):
        raise ValueError(f"managed workspace root is unsafe: {managed}")
    state_root = managed / STATE_DIR
    allowed_top = {STATE_DIR}
    states: list[dict[str, Any]] = []
    if state_root.exists():
        if not state_root.is_dir() or _is_reparse_or_symlink(state_root):
            raise ValueError(f"managed workspace state root is unsafe: {state_root}")
        for state_path in sorted(state_root.iterdir()):
            if not state_path.is_dir() or _is_reparse_or_symlink(state_path):
                raise ValueError(f"unknown managed workspace state entry: {state_path}")
            item = _workspace_state_inventory(state_path, managed)
            states.append(item)
            if not item["closed"]:
                allowed_top.add(state_path.name)
    for entry in managed.iterdir():
        if entry.name not in allowed_top:
            raise ValueError(f"unknown file in managed workspace root: {entry}")
    active_ids = {
        entry.name
        for entry in managed.iterdir()
        if _WORKSPACE_NAME.fullmatch(entry.name)
    }
    if active_ids != {item["workspace_id"] for item in states if not item["closed"]}:
        raise ValueError("managed workspace mirror/state inventory mismatch")
    return managed, states


def _preflight_purge(root: Path) -> tuple[Path | None, list[dict[str, Any]]]:
    for relative in OWNED_FILES:
        path = root / relative
        if path.exists() or path.is_symlink():
            if path.is_dir() and not path.is_symlink():
                raise ValueError(
                    f"expected an owned file but found a directory: {path}"
                )
            if _is_reparse_or_symlink(path):
                raise ValueError(f"refusing unsafe owned file: {path}")
    return _preflight_managed_workspaces(root)


def purge_installation() -> dict[str, object]:
    marker = read_installation_marker()
    root = config_dir()
    managed, workspaces = _preflight_purge(root)
    removed: list[str] = []
    if managed is not None:
        active = [item for item in workspaces if not item["closed"]]
        for item in active:
            close_workspace(item["mirror"], archive=False)
        if workspaces:
            removed_ids = cleanup_workspaces(
                managed, [str(item["workspace_id"]) for item in workspaces]
            )
            removed.extend(str(managed / STATE_DIR / item) for item in removed_ids)
        state_root = managed / STATE_DIR
        if state_root.exists() and not any(state_root.iterdir()):
            state_root.rmdir()
        if not any(managed.iterdir()):
            managed.rmdir()
            removed.append(str(managed))
    for relative in OWNED_FILES:
        path = root / relative
        if path.exists() or path.is_symlink():
            if path.is_dir() and not path.is_symlink():
                raise ValueError(
                    f"expected an owned file but found a directory: {path}"
                )
            path.unlink()
            removed.append(str(path))
    remaining = [str(path) for path in root.iterdir()] if root.exists() else []
    if root.exists() and not remaining:
        root.rmdir()
    return {
        "installation_id": marker["installation_id"],
        "removed": removed,
        "remaining_unowned": remaining,
        "config_directory_removed": not root.exists(),
    }
