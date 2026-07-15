"""CLI commands for workspace and host integration normalization."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import config_dir, load_path_rules, load_rules
from .normalize import normalize_text, translate_reversible_text
from .workspace import (
    NAME_RE,
    MARKER,
    apply_plan,
    cleanup_workspaces,
    close_workspace,
    conflicts_workspace,
    create_workspace,
    plan_workspace,
    recover_workspace,
    status_workspace,
    verify_workspace,
)


def _emit(value: object, output_format: str = "json") -> None:
    if output_format == "json":
        print(json.dumps(value, indent=2, sort_keys=True))
    elif isinstance(value, str):
        print(value)
    else:
        print(json.dumps(value, sort_keys=True))


def _require_confirmation(args: argparse.Namespace, operation: str) -> None:
    if args.yes:
        return
    if not sys.stdin.isatty():
        raise ValueError(f"{operation} requires --yes outside an interactive terminal")
    if input(f"{operation}? [y/N] ").strip().casefold() not in {"y", "yes"}:
        raise ValueError(f"{operation} cancelled")


def _capabilities(args: argparse.Namespace) -> int:
    _emit(
        {
            "schema_version": 1,
            "context_normalization": True,
            "vocabulary_normalization": True,
            "workspace_normalization": True,
            "bidirectional_vocabulary": True,
            "utf8_payload_normalization": True,
            "binary_payload_preservation": True,
            "host_bridges": ["codex", "pi"],
        },
        args.format,
    )
    return 0


def _workspace_root() -> Path:
    return config_dir() / "workspaces"


def _mirror(value: str) -> Path:
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate.resolve(strict=True)
    name = value if value.startswith("workspace-") else f"workspace-{value}"
    if not NAME_RE.fullmatch(name):
        raise ValueError(
            "workspace reference must be an absolute path or workspace identifier"
        )
    return (_workspace_root() / name).resolve(strict=True)


def _workspace(args: argparse.Namespace) -> int:
    command = args.workspace_command
    if command == "create":
        _require_confirmation(args, "Create normalized workspace")
        result = create_workspace(
            Path(args.source).expanduser().resolve(),
            Path(args.root).expanduser().resolve() if args.root else _workspace_root(),
            mode=args.mode,
            path_rules=load_path_rules() if args.normalize_paths else None,
        )
    elif command == "status":
        result = status_workspace(_mirror(args.workspace))
    elif command == "verify":
        result = verify_workspace(_mirror(args.workspace))
    elif command == "plan":
        direction = "to-mirror" if args.direction == "model" else "back"
        result = plan_workspace(_mirror(args.workspace), direction=direction)
        if args.output:
            Path(args.output).write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
    elif command == "apply":
        _require_confirmation(args, "Apply workspace normalization plan")
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
        result = apply_plan(
            _mirror(args.workspace),
            plan,
            expected_sha256=args.sha256 or plan.get("plan_sha256", ""),
        )
    elif command == "conflicts":
        direction = "to-mirror" if args.direction == "model" else "back"
        result = conflicts_workspace(_mirror(args.workspace), direction=direction)
    elif command == "recover":
        _require_confirmation(args, f"{args.action.title()} workspace transaction")
        result = recover_workspace(_mirror(args.workspace), action=args.action)
    elif command == "close":
        _require_confirmation(args, "Close normalized workspace")
        result = close_workspace(_mirror(args.workspace), archive=not args.no_archive)
    elif command == "cleanup":
        _require_confirmation(args, "Remove validated workspace records")
        result = cleanup_workspaces(
            Path(args.root).expanduser().resolve() if args.root else _workspace_root(),
            args.workspace_ids,
        )
    else:  # pragma: no cover
        raise ValueError(f"unsupported workspace command: {command}")
    _emit(result, args.format)
    return 0


def _auto_sync(mirror: Path) -> dict[str, object]:
    plan = plan_workspace(mirror, direction="back")
    if plan["conflicts"]:
        return {"status": "conflict", "conflicts": plan["conflicts"]}
    if not plan["operations"]:
        return {"status": "clean", "changed": False}
    applied = apply_plan(mirror, plan, expected_sha256=plan["plan_sha256"])
    return {"status": "applied", "changed": True, "result": applied}


def _prepare_workspace(mirror: Path) -> dict[str, object]:
    # Per-turn preparation uses the cached status probe. The deep re-hash
    # stays available as `workspace verify`, and apply_plan re-validates
    # every preimage against the live tree before touching a file.
    checked = status_workspace(mirror)
    plan = plan_workspace(mirror, direction="to-mirror")
    if plan["conflicts"]:
        raise ValueError("workspace conflicts must be resolved before normalization")
    if plan["operations"]:
        applied = apply_plan(mirror, plan, expected_sha256=plan["plan_sha256"])
        return {"status": "applied", "checked": checked, "result": applied}
    return {"status": "clean", "checked": checked}


def _bridge_mirror(args: argparse.Namespace) -> Path:
    candidate = Path(args.workspace).expanduser() if args.workspace else Path.cwd()
    mirror = candidate.resolve(strict=True)
    if not (mirror / MARKER).is_file():
        raise ValueError(
            "bridge must run inside a normalized workspace or use --workspace"
        )
    return mirror


def _bridge(args: argparse.Namespace) -> int:
    if args.bridge_command == "normalize":
        value = sys.stdin.read()
        normalized = translate_reversible_text(
            value,
            load_path_rules(),
            reverse=args.direction == "display",
        )
        sys.stdout.write(normalized)
        return 0
    mirror = _bridge_mirror(args)
    if args.bridge_command == "submit":
        prompt = sys.stdin.read()
        _prepare_workspace(mirror)
        prompt = translate_reversible_text(prompt, load_path_rules())
        normalized, _ = normalize_text(prompt, load_rules(), context=None)
        sys.stdout.write(normalized)
        return 0
    if args.bridge_command == "complete":
        result = _auto_sync(mirror)
        if result["status"] == "conflict":
            raise ValueError("workspace conflicts paused source normalization")
        _emit(result, "json")
        return 0
    raise ValueError(f"unsupported bridge command: {args.bridge_command}")


def add_feature_commands(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    capabilities = commands.add_parser(
        "capabilities", help="report normalization capabilities"
    )
    capabilities.add_argument("--format", choices=("text", "json"), default="json")
    capabilities.set_defaults(function=_capabilities)

    bridge = commands.add_parser(
        "bridge", help=argparse.SUPPRESS, description="host normalization bridge"
    )
    bridge_commands = bridge.add_subparsers(dest="bridge_command", required=True)
    for name in ("submit", "complete"):
        command = bridge_commands.add_parser(name, help=argparse.SUPPRESS)
        command.add_argument("--workspace")
        command.set_defaults(function=_bridge)
    normalize = bridge_commands.add_parser("normalize", help=argparse.SUPPRESS)
    normalize.add_argument("--direction", choices=("model", "display"), required=True)
    normalize.set_defaults(function=_bridge)

    workspace = commands.add_parser("workspace", help="manage normalized workspaces")
    workspace_commands = workspace.add_subparsers(
        dest="workspace_command", required=True
    )
    create = workspace_commands.add_parser("create")
    create.add_argument("source")
    create.add_argument("--root")
    create.add_argument("--mode", choices=("auto", "git", "filesystem"), default="auto")
    create.add_argument("--normalize-paths", action="store_true")
    create.add_argument("--format", choices=("text", "json"), default="json")
    create.add_argument("--yes", action="store_true")
    create.set_defaults(function=_workspace)
    for name in ("status", "verify"):
        command = workspace_commands.add_parser(name)
        command.add_argument("workspace")
        command.add_argument("--format", choices=("text", "json"), default="json")
        command.set_defaults(function=_workspace)
    for name in ("plan", "conflicts"):
        command = workspace_commands.add_parser(name)
        command.add_argument("workspace")
        command.add_argument("--direction", choices=("model", "source"), required=True)
        command.add_argument("--format", choices=("text", "json"), default="json")
        if name == "plan":
            command.add_argument("--output")
        command.set_defaults(function=_workspace)
    apply = workspace_commands.add_parser("apply")
    apply.add_argument("workspace")
    apply.add_argument("--plan", required=True)
    apply.add_argument("--sha256")
    apply.add_argument("--yes", action="store_true")
    apply.add_argument("--format", choices=("text", "json"), default="json")
    apply.set_defaults(function=_workspace)
    recover = workspace_commands.add_parser("recover")
    recover.add_argument("workspace")
    recover.add_argument("--action", choices=("resume", "rollback"), required=True)
    recover.add_argument("--yes", action="store_true")
    recover.add_argument("--format", choices=("text", "json"), default="json")
    recover.set_defaults(function=_workspace)
    close = workspace_commands.add_parser("close")
    close.add_argument("workspace")
    close.add_argument("--no-archive", action="store_true")
    close.add_argument("--yes", action="store_true")
    close.add_argument("--format", choices=("text", "json"), default="json")
    close.set_defaults(function=_workspace)
    cleanup = workspace_commands.add_parser("cleanup")
    cleanup.add_argument("workspace_ids", nargs="+")
    cleanup.add_argument("--root")
    cleanup.add_argument("--yes", action="store_true")
    cleanup.add_argument("--format", choices=("text", "json"), default="json")
    cleanup.set_defaults(function=_workspace)
