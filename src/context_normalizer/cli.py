from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import stat
import sys
from pathlib import Path

from . import __version__
from .clipboard import ClipboardUnavailable, read_clipboard, write_clipboard
from .config import (
    config_dir,
    initialize_config,
    load_context,
    load_rules,
    config_path,
)
from .feature_cli import add_feature_commands
from .lifecycle import purge_installation
from .relay import add_relay_commands
from .normalize import normalize_text
from .profiles import (
    active_profile_manifest,
    apply_profile,
    profile_catalog,
    profile_manifest,
)
from .terminology import (
    add_rule,
    find_rules,
    remove_rule,
    render_rules,
    rules_manifest,
    rules_path,
)
from .workspace import WorkspaceError


CONFIG_FILES = {
    "rules": "rules.tsv",
    "bidirectional-vocabulary": "path-rules.tsv",
    "cues": "cues.txt",
    "context": "context.txt",
    "subagent-context": "subagent-context.txt",
}


def _read(path: str | None) -> str:
    # Decode raw bytes so CRLF line endings survive and the audit hash covers
    # the input as written, not a newline-translated copy.
    if path:
        return Path(path).read_bytes().decode("utf-8")
    stream = getattr(sys.stdin, "buffer", None)
    return stream.read().decode("utf-8") if stream else sys.stdin.read()


def _write(path: str | None, value: str) -> None:
    if path:
        Path(path).write_bytes(value.encode("utf-8"))
        return
    stream = getattr(sys.stdout, "buffer", None)
    if stream:
        stream.write(value.encode("utf-8"))
        stream.flush()
    else:
        sys.stdout.write(value)


def _context_or_none(path: str | None = None) -> str | None:
    if path:
        return load_context(Path(path))
    try:
        return load_context()
    except ValueError as exc:
        if str(exc) == "context text must not be empty":
            return None
        raise


def _normalize(args: argparse.Namespace) -> int:
    original = _read(args.input)
    context = None if args.no_context else _context_or_none(args.context)
    normalized, audit = normalize_text(
        original,
        load_rules(Path(args.rules) if args.rules else None),
        context=context,
    )
    if args.preview:
        sys.stderr.writelines(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                normalized.splitlines(keepends=True),
                fromfile="original",
                tofile="normalized",
            )
        )
    _write(args.output, normalized)
    if args.audit:
        Path(args.audit).write_text(
            json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return 0


def _clipboard(args: argparse.Namespace) -> int:
    original = read_clipboard()
    context = None if args.no_context else _context_or_none()
    normalized, audit = normalize_text(original, load_rules(), context=context)
    diff = "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            normalized.splitlines(keepends=True),
            fromfile="clipboard-original",
            tofile="clipboard-normalized",
        )
    )
    sys.stderr.write(diff or "No clipboard text changes.\n")
    if not args.yes:
        if not sys.stdin.isatty():
            raise ValueError(
                "clipboard normalization needs an interactive terminal or --yes"
            )
        response = input("Normalize clipboard text? [y/N] ")
        if response.strip().casefold() not in {"y", "yes"}:
            print("Clipboard unchanged.", file=sys.stderr)
            return 1
    write_clipboard(normalized)
    if args.audit:
        Path(args.audit).write_text(
            json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    print("Clipboard text normalized.", file=sys.stderr)
    return 0


def _init(args: argparse.Namespace) -> int:
    written = initialize_config(
        force=args.force,
        register_installation=True,
    )
    result = {
        "config_dir": str(config_dir()),
        "written": [str(path) for path in written],
    }
    print(json.dumps(result, indent=2))
    return 0


def _doctor(args: argparse.Namespace) -> int:
    errors: list[str] = []
    try:
        rules = load_rules()
    except Exception as exc:
        errors.append(str(exc))
        rules = []
    print(
        json.dumps(
            {
                "version": __version__,
                "python": sys.version.split()[0],
                "config_dir": str(config_dir()),
                "rule_count": len(rules),
                "status": "ok" if not errors else "error",
                "errors": errors,
            },
            indent=2,
        )
    )
    return 0 if not errors else 1


def _rules_source(args: argparse.Namespace) -> Path:
    if getattr(args, "bidirectional", False):
        if args.bundled:
            raise ValueError("bidirectional vocabulary has no bundled profile")
        return (
            Path(args.rules).resolve() if args.rules else config_path("path-rules.tsv")
        )
    return rules_path(
        bundled=args.bundled,
        explicit=Path(args.rules) if args.rules else None,
    )


def _rules_list(args: argparse.Namespace) -> int:
    path = _rules_source(args)
    manifest = rules_manifest(path)
    sys.stdout.write(render_rules(manifest, output_format=args.format))
    return 0


def _rules_find(args: argparse.Namespace) -> int:
    path = _rules_source(args)
    manifest = rules_manifest(path)
    matches = find_rules(load_rules(path), args.query)
    sys.stdout.write(render_rules(manifest, output_format=args.format, rules=matches))
    return 0 if matches else 1


def _rules_validate(args: argparse.Namespace) -> int:
    path = _rules_source(args)
    manifest = rules_manifest(path)
    result = {
        key: manifest[key]
        for key in ("schema_version", "tool_version", "source", "sha256", "count")
    }
    result["status"] = "valid"
    if args.format == "json":
        print(json.dumps(result, indent=2))
    else:
        print(f"valid: {result['count']} rules ({result['sha256']})")
    return 0


def _rules_path(args: argparse.Namespace) -> int:
    print(_rules_source(args))
    return 0


def _rules_add(args: argparse.Namespace) -> int:
    if args.bundled:
        raise ValueError("bundled defaults are read-only")
    result = add_rule(
        _rules_source(args),
        args.source,
        args.normalized,
        update=args.update,
        expected_sha256=args.expect_sha256,
    )
    print(json.dumps(result, indent=2))
    return 0


def _rules_remove(args: argparse.Namespace) -> int:
    if args.bundled:
        raise ValueError("bundled defaults are read-only")
    result = remove_rule(
        _rules_source(args), args.source, expected_sha256=args.expect_sha256
    )
    print(json.dumps(result, indent=2))
    return 0


def _purge(args: argparse.Namespace) -> int:
    if not args.yes:
        raise ValueError("purge requires --yes")
    print(json.dumps(purge_installation(), indent=2))
    return 0


def _profiles_list(args: argparse.Namespace) -> int:
    catalog = profile_catalog()
    if args.format == "json":
        print(json.dumps(catalog, indent=2, ensure_ascii=False))
    else:
        print("NAME                             DEFAULT  DESCRIPTION")
        for entry in catalog["profiles"]:
            marker = "yes" if entry["name"] == catalog["default"] else ""
            print(f"{entry['name']:<32} {marker:<7}  {entry['description']}")
    return 0


def _profiles_show(args: argparse.Namespace) -> int:
    manifest = profile_manifest(args.name)
    if args.format == "json":
        print(json.dumps(manifest, indent=2, ensure_ascii=False))
    elif args.format == "tsv":
        for rule in manifest["rules"]:
            print(f"{rule['source']}\t{rule['normalized']}")
    else:
        print(f"{manifest['name']}: {manifest['description']}")
        print(f"SHA-256: {manifest['sha256']}")
        print(f"Rules: {manifest['rule_count']}; cues: {manifest['cue_count']}")
        for rule in manifest["rules"]:
            print(f"- {rule['source']}: {rule['normalized']}")
        print("Cues: " + ", ".join(manifest["cues"]))
    return 0


def _profiles_active(args: argparse.Namespace) -> int:
    manifest = active_profile_manifest()
    if args.format == "json":
        print(json.dumps(manifest, indent=2, ensure_ascii=False))
    else:
        print(
            f"active: {manifest['rule_count']} rules, {manifest['cue_count']} cues "
            f"({manifest['sha256']})"
        )
    return 0


def _profiles_apply(args: argparse.Namespace) -> int:
    if not args.yes:
        raise ValueError("profile apply requires --yes")
    result = apply_profile(
        args.name,
        mode=args.mode,
        expected_sha256=args.expect_sha256,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def _active_config_path(name: str) -> Path:
    path = config_path(CONFIG_FILES[name])
    if not path.is_file():
        raise ValueError(f"active configuration file is missing: {path}")
    details = os.lstat(path)
    attributes = getattr(details, "st_file_attributes", 0)
    if stat.S_ISLNK(details.st_mode) or attributes & getattr(
        stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0
    ):
        raise ValueError(f"refusing linked configuration file: {path}")
    return path.resolve(strict=True)


def _config_list(args: argparse.Namespace) -> int:
    entries = []
    for name, filename in CONFIG_FILES.items():
        path = config_path(filename)
        entries.append({"name": name, "path": str(path), "exists": path.is_file()})
    result = {"schema_version": 1, "tool_version": __version__, "files": entries}
    if args.format == "json":
        print(json.dumps(result, indent=2))
    else:
        for entry in entries:
            print(
                f"{entry['name']}: {entry['path']} ({'present' if entry['exists'] else 'missing'})"
            )
    return 0


def _config_path_command(args: argparse.Namespace) -> int:
    print(_active_config_path(args.name))
    return 0


def _config_show(args: argparse.Namespace) -> int:
    path = _active_config_path(args.name)
    payload = path.read_bytes()
    text = payload.decode("utf-8")
    result: dict[str, object] = {
        "schema_version": 1,
        "tool_version": __version__,
        "name": args.name,
        "path": str(path),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    if args.name in {"rules", "bidirectional-vocabulary"}:
        result["rules"] = [
            {"source": rule.source, "normalized": rule.normalized}
            for rule in load_rules(path)
        ]
    elif args.name == "cues":
        result["cues"] = [
            line.strip()
            for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
    else:
        result["text"] = text.strip()
    if args.format == "json":
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        sys.stdout.write(text if text.endswith("\n") else text + "\n")
    return 0


def _add_rule_source_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--rules", help="use a specific rules.tsv file")
    parser.add_argument("--bundled", action="store_true", help="use packaged defaults")
    parser.add_argument(
        "--bidirectional",
        action="store_true",
        help="use the bidirectional vocabulary",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ctxnorm")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    normalize = commands.add_parser("normalize", help="normalize a file or stdin")
    normalize.add_argument("input", nargs="?", help="UTF-8 input; stdin when omitted")
    normalize.add_argument("--output", "-o")
    normalize.add_argument("--audit")
    normalize.add_argument("--rules")
    normalize.add_argument("--context")
    normalize.add_argument("--no-context", action="store_true")
    normalize.add_argument("--preview", action="store_true")
    normalize.set_defaults(function=_normalize)

    clipboard = commands.add_parser(
        "clipboard", help="preview and normalize clipboard text"
    )
    clipboard.add_argument("--yes", "-y", action="store_true", help="skip confirmation")
    clipboard.add_argument("--audit", help="optional audit JSON path")
    clipboard.add_argument("--no-context", action="store_true")
    clipboard.set_defaults(function=_clipboard)

    init = commands.add_parser("init", help="create editable user configuration")
    init.add_argument("--force", action="store_true")
    init.set_defaults(function=_init)

    doctor = commands.add_parser("doctor", help="validate configuration")
    doctor.set_defaults(function=_doctor)

    rules = commands.add_parser(
        "vocabulary", help="query or tune vocabulary normalization"
    )
    rules_commands = rules.add_subparsers(dest="rules_command", required=True)

    rules_list = rules_commands.add_parser(
        "list", help="list the active terminology map"
    )
    _add_rule_source_options(rules_list)
    rules_list.add_argument(
        "--format", choices=("table", "json", "tsv"), default="table"
    )
    rules_list.set_defaults(function=_rules_list)

    rules_find = rules_commands.add_parser(
        "find", help="search source and normalized text"
    )
    rules_find.add_argument("query")
    _add_rule_source_options(rules_find)
    rules_find.add_argument(
        "--format", choices=("table", "json", "tsv"), default="table"
    )
    rules_find.set_defaults(function=_rules_find)

    rules_validate = rules_commands.add_parser(
        "validate", help="validate and identify a rule file"
    )
    _add_rule_source_options(rules_validate)
    rules_validate.add_argument("--format", choices=("text", "json"), default="text")
    rules_validate.set_defaults(function=_rules_validate)

    rules_path_command = rules_commands.add_parser(
        "path", help="print the active rule-file path"
    )
    _add_rule_source_options(rules_path_command)
    rules_path_command.set_defaults(function=_rules_path)

    rules_add = rules_commands.add_parser(
        "add", help="add an active terminology mapping"
    )
    rules_add.add_argument("source")
    rules_add.add_argument("normalized")
    _add_rule_source_options(rules_add)
    rules_add.add_argument("--update", action="store_true")
    rules_add.add_argument("--expect-sha256")
    rules_add.set_defaults(function=_rules_add)

    rules_remove = rules_commands.add_parser(
        "remove", help="remove an active terminology mapping"
    )
    rules_remove.add_argument("source")
    _add_rule_source_options(rules_remove)
    rules_remove.add_argument("--expect-sha256")
    rules_remove.set_defaults(function=_rules_remove)

    profiles = commands.add_parser(
        "profiles", help="query or apply bundled terminology activation profiles"
    )
    profile_commands = profiles.add_subparsers(dest="profiles_command", required=True)
    profiles_list = profile_commands.add_parser("list", help="list bundled profiles")
    profiles_list.add_argument("--format", choices=("table", "json"), default="table")
    profiles_list.set_defaults(function=_profiles_list)
    profiles_show = profile_commands.add_parser("show", help="show one bundled profile")
    profiles_show.add_argument("name")
    profiles_show.add_argument(
        "--format", choices=("table", "json", "tsv"), default="table"
    )
    profiles_show.set_defaults(function=_profiles_show)
    profiles_active = profile_commands.add_parser(
        "active", help="identify active rules and cues without initializing them"
    )
    profiles_active.add_argument("--format", choices=("text", "json"), default="text")
    profiles_active.set_defaults(function=_profiles_active)
    profiles_apply = profile_commands.add_parser(
        "apply", help="apply a vocabulary profile"
    )
    profiles_apply.add_argument("name")
    profiles_apply.add_argument("--mode", choices=("merge", "reset"), required=True)
    profiles_apply.add_argument("--expect-sha256")
    profiles_apply.add_argument("--yes", action="store_true")
    profiles_apply.set_defaults(function=_profiles_apply)

    config = commands.add_parser(
        "config", help="inspect active configuration without initializing it"
    )
    config_commands = config.add_subparsers(dest="config_command", required=True)
    config_list = config_commands.add_parser(
        "list", help="list active configuration files"
    )
    config_list.add_argument("--format", choices=("text", "json"), default="text")
    config_list.set_defaults(function=_config_list)
    config_show = config_commands.add_parser(
        "show", help="show one active configuration file"
    )
    config_show.add_argument("name", choices=tuple(CONFIG_FILES))
    config_show.add_argument("--format", choices=("text", "json"), default="text")
    config_show.set_defaults(function=_config_show)
    config_path_parser = config_commands.add_parser(
        "path", help="print one active configuration path"
    )
    config_path_parser.add_argument("name", choices=tuple(CONFIG_FILES))
    config_path_parser.set_defaults(function=_config_path_command)

    purge = commands.add_parser("purge", help="remove owned configuration")
    purge.add_argument("--yes", action="store_true")
    purge.set_defaults(function=_purge)
    add_feature_commands(commands)
    add_relay_commands(commands)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.function(args))
    except (
        ClipboardUnavailable,
        OSError,
        ValueError,
        WorkspaceError,
        json.JSONDecodeError,
    ) as exc:
        print(f"ctxnorm: {exc}", file=sys.stderr)
        return 2
