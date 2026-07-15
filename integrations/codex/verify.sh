#!/usr/bin/env sh
set -eu

DESTINATION="$HOME/.context-normalizer/integrations/codex"
MARKER="$DESTINATION/.ctxnorm-codex-install.json"
[ -f "$MARKER" ] && [ -x "$DESTINATION/bin/codex" ] || { printf '%s\n' 'Codex integration files are incomplete.' >&2; exit 1; }
python3 - "$MARKER" "$DESTINATION" <<'PY'
import json, pathlib, sys
marker = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
expected = pathlib.Path(sys.argv[2]).resolve()
actual = pathlib.Path(marker.get("destination", "")).resolve()
if marker.get("schema_version") != 1 or marker.get("component") != "codex" or actual != expected:
    raise SystemExit("Codex integration marker validation failed.")
PY
ctxnorm doctor >/dev/null
"$DESTINATION/bin/codex" --version >/dev/null
printf '%s\n' 'Codex integration verification passed.'
