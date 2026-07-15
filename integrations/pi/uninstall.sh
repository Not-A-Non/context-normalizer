#!/usr/bin/env sh
set -eu

DESTINATION="$HOME/.context-normalizer/integrations/pi"
MARKER="$DESTINATION/.ctxnorm-pi-install.json"
[ -f "$MARKER" ] || { printf '%s\n' "Pi integration marker is missing at $MARKER" >&2; exit 1; }
python3 - "$MARKER" "$DESTINATION" <<'PY'
import json, pathlib, sys
marker = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
expected = pathlib.Path(sys.argv[2]).resolve()
actual = pathlib.Path(marker.get("destination", "")).resolve()
if marker.get("schema_version") != 1 or marker.get("component") != "pi" or actual != expected:
    raise SystemExit("Pi integration marker validation failed.")
if any(path.is_symlink() for path in expected.rglob("*")):
    raise SystemExit("Pi integration contains a linked entry. Nothing was removed.")
expected_files = {
    "extensions/context-normalizer.mjs", "package.json", "README.md", "LICENSE", "THIRD_PARTY-NOTICES.md",
    "uninstall.sh", "verify.sh", ".ctxnorm-pi-install.json",
}
actual_files = {
    path.relative_to(expected).as_posix()
    for path in expected.rglob("*")
    if path.is_file()
}
if actual_files != expected_files:
    raise SystemExit("Pi integration contains an unexpected or missing file. Nothing was removed.")
PY
if command -v pi >/dev/null 2>&1; then
  pi remove "$DESTINATION"
fi
rm -f -- "$DESTINATION/extensions/context-normalizer.mjs" "$DESTINATION/package.json" "$DESTINATION/README.md" "$DESTINATION/LICENSE" "$DESTINATION/THIRD_PARTY-NOTICES.md" "$DESTINATION/uninstall.sh" "$DESTINATION/verify.sh" "$MARKER"
rmdir "$DESTINATION/extensions" "$DESTINATION"
printf '%s\n' 'Pi integration removed.'
