#!/usr/bin/env sh
set -eu

DESTINATION="$HOME/.context-normalizer/integrations/codex"
MARKER="$DESTINATION/.ctxnorm-codex-install.json"
[ -f "$MARKER" ] || { printf '%s\n' "Codex integration marker is missing at $MARKER" >&2; exit 1; }
python3 - "$MARKER" "$DESTINATION" <<'PY'
import json, pathlib, sys
marker = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
expected = pathlib.Path(sys.argv[2]).resolve()
actual = pathlib.Path(marker.get("destination", "")).resolve()
if marker.get("schema_version") != 1 or marker.get("component") != "codex" or actual != expected:
    raise SystemExit("Codex integration marker validation failed.")
if any(path.is_symlink() for path in expected.rglob("*")):
    raise SystemExit("Codex integration contains a linked entry. Nothing was removed.")
expected_files = {
    "bin/codex", "integration.json", "README.md", "LICENSE", "THIRD_PARTY-NOTICES.md", "ctxnorm-codex",
    "uninstall.sh", "verify.sh", ".ctxnorm-codex-install.json",
}
actual_files = {
    path.relative_to(expected).as_posix()
    for path in expected.rglob("*")
    if path.is_file()
}
if actual_files != expected_files:
    raise SystemExit("Codex integration contains an unexpected or missing file. Nothing was removed.")
PY
rm -f -- "$DESTINATION/bin/codex" "$DESTINATION/integration.json" "$DESTINATION/README.md" "$DESTINATION/LICENSE" "$DESTINATION/THIRD_PARTY-NOTICES.md" "$DESTINATION/ctxnorm-codex" "$DESTINATION/uninstall.sh" "$DESTINATION/verify.sh" "$MARKER"
rmdir "$DESTINATION/bin" "$DESTINATION"
printf '%s\n' 'Codex integration removed.'
