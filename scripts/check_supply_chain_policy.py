#!/usr/bin/env python3
"""Verify exact build-tool pins, hashes, release age, and stable-version lag."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+)*$")


def parsed_version(value: str) -> tuple[int, ...]:
    if not VERSION.fullmatch(value):
        raise ValueError(f"policy versions must be stable numeric releases: {value}")
    return tuple(int(part) for part in value.split("."))


def load_lock(path: Path) -> dict[str, dict[str, object]]:
    packages: dict[str, dict[str, object]] = {}
    current: str | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        match = re.match(r"^([A-Za-z0-9._-]+)==([^ ]+)", line)
        if match:
            current = match.group(1).lower().replace("_", "-")
            packages[current] = {"version": match.group(2).rstrip("\\"), "hashes": []}
        elif line.startswith("--hash=sha256:") and current:
            digest = line.removeprefix("--hash=sha256:").rstrip("\\").rstrip()
            packages[current]["hashes"].append(digest)
    return packages


def fetch_json(url: str) -> dict[str, object]:
    with urllib.request.urlopen(url, timeout=20) as response:
        return json.load(response)


def emergency_waiver(now: dt.datetime) -> tuple[str, str] | None:
    path = ROOT / "requirements" / "dependency-policy-emergency.json"
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "package", "version", "advisory_url", "reason", "approver",
        "created_at", "expires_at", "follow_up_issue",
    }
    if required.difference(value) or not all(str(value[key]).strip() for key in required):
        raise ValueError("emergency policy is incomplete")
    if not str(value["advisory_url"]).startswith("https://"):
        raise ValueError("emergency advisory must use an HTTPS URL")
    created = dt.datetime.fromisoformat(value["created_at"].replace("Z", "+00:00"))
    expires = dt.datetime.fromisoformat(value["expires_at"].replace("Z", "+00:00"))
    if expires <= created or expires - created > dt.timedelta(days=7) or now > expires:
        raise ValueError("emergency policy is expired or exceeds seven days")
    return str(value["package"]), str(value["version"])


def verify(*, online: bool) -> dict[str, object]:
    policy = json.loads(
        (ROOT / "requirements" / "dependency-policy.json").read_text(encoding="utf-8")
    )
    reviewed = dt.datetime.fromisoformat(policy["reviewed_at"].replace("Z", "+00:00"))
    minimum_age = dt.timedelta(days=int(policy["minimum_age_days"]))
    locked: dict[str, dict[str, object]] = {}
    for relative in policy["locks"]:
        additions = load_lock(ROOT / relative)
        duplicates = set(locked).intersection(additions)
        if duplicates:
            raise ValueError(f"packages appear in more than one lock: {sorted(duplicates)}")
        locked.update(additions)
    expected = policy["packages"]
    now = dt.datetime.now(dt.timezone.utc)
    if reviewed > now + dt.timedelta(minutes=5):
        raise ValueError("dependency review timestamp is in the future")
    waiver = emergency_waiver(now)
    if waiver and (
        waiver[0] not in expected or expected[waiver[0]]["version"] != waiver[1]
    ):
        raise ValueError("emergency policy does not match a selected package version")
    if set(locked) != set(expected):
        raise ValueError(f"lock/policy package mismatch: {sorted(set(locked) ^ set(expected))}")

    for name, record in expected.items():
        selected = record["version"]
        if locked[name]["version"] != selected or not locked[name]["hashes"]:
            raise ValueError(f"{name}: exact version/hash is missing from the lock")
        released = dt.datetime.fromisoformat(record["released_at"].replace("Z", "+00:00"))
        waived = waiver == (name, selected)
        if reviewed - released < minimum_age and not waived:
            raise ValueError(f"{name} {selected}: younger than the minimum age")
        if parsed_version(record["newest_observed"]) <= parsed_version(selected):
            raise ValueError(f"{name} {selected}: not behind the newest observed stable release")

        if online:
            version_data = fetch_json(f"https://pypi.org/pypi/{name}/{selected}/json")
            files = version_data["urls"]
            if not files or all(item.get("yanked", False) for item in files):
                raise ValueError(f"{name} {selected}: missing or yanked")
            wheel_hashes = {
                item["digests"]["sha256"]
                for item in files
                if item["packagetype"] == "bdist_wheel"
            }
            if not set(locked[name]["hashes"]).issubset(wheel_hashes):
                raise ValueError(f"{name} {selected}: committed wheel hash differs from PyPI")
            uploads = [
                dt.datetime.fromisoformat(item["upload_time_iso_8601"].replace("Z", "+00:00"))
                for item in files
                if not item.get("yanked", False) and item.get("upload_time_iso_8601")
            ]
            if not uploads:
                raise ValueError(f"{name} {selected}: no verifiable upload timestamp")
            actual_release = min(uploads)
            if abs(actual_release - released) > dt.timedelta(minutes=5):
                raise ValueError(
                    f"{name} {selected}: policy release timestamp differs from PyPI"
                )
            if now - actual_release < minimum_age and not waived:
                raise ValueError(f"{name} {selected}: younger than the minimum age on PyPI")
            all_data = fetch_json(f"https://pypi.org/pypi/{name}/json")
            stable = [parsed_version(value) for value, items in all_data["releases"].items() if items and VERSION.fullmatch(value)]
            if not waived and sum(item > parsed_version(selected) for item in stable) < int(policy["minimum_stable_lag"]):
                raise ValueError(f"{name} {selected}: insufficient stable-version lag")

    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    backend = expected["setuptools"]["version"]
    if f'requires = ["setuptools=={backend}"]' not in pyproject:
        raise ValueError("pyproject build-backend pin differs from policy")
    return {"status": "passed", "packages": len(expected), "online": online}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--online", action="store_true", help="verify PyPI release state and hashes")
    args = parser.parse_args()
    print(json.dumps(verify(online=args.online), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
