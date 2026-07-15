#!/usr/bin/env sh
set -eu

DESTINATION="$HOME/.context-normalizer/integrations/pi"
MARKER="$DESTINATION/.ctxnorm-pi-install.json"
[ -f "$MARKER" ] && [ -f "$DESTINATION/extensions/context-normalizer.mjs" ] || { printf '%s\n' 'Pi integration files are incomplete.' >&2; exit 1; }
python3 - "$MARKER" "$DESTINATION" <<'PY'
import json, pathlib, sys
marker = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
expected = pathlib.Path(sys.argv[2]).resolve()
actual = pathlib.Path(marker.get("destination", "")).resolve()
if marker.get("schema_version") != 1 or marker.get("component") != "pi" or actual != expected:
    raise SystemExit("Pi integration marker validation failed.")
PY
ctxnorm doctor >/dev/null
pi list >/dev/null
printf '%s\n' 'Pi integration verification passed.'
