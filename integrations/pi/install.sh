#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
DESTINATION="$HOME/.context-normalizer/integrations/pi"
command -v ctxnorm >/dev/null 2>&1 || { printf '%s\n' 'Context Normalizer core must be installed first.' >&2; exit 1; }
command -v pi >/dev/null 2>&1 || { printf '%s\n' 'Pi must be installed first.' >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { printf '%s\n' 'Python 3 must be installed first.' >&2; exit 1; }
[ ! -e "$DESTINATION" ] || { printf '%s\n' "Pi integration already exists at $DESTINATION" >&2; exit 1; }
ctxnorm doctor >/dev/null
mkdir -p "$DESTINATION/extensions"
DESTINATION_CREATED=1
cleanup() {
  if [ "$DESTINATION_CREATED" -eq 1 ]; then
    rm -f -- "$DESTINATION/extensions/context-normalizer.mjs" "$DESTINATION/package.json" "$DESTINATION/README.md" "$DESTINATION/LICENSE" "$DESTINATION/THIRD_PARTY-NOTICES.md" "$DESTINATION/uninstall.sh" "$DESTINATION/verify.sh" "$DESTINATION/.ctxnorm-pi-install.json"
    rmdir "$DESTINATION/extensions" "$DESTINATION" 2>/dev/null || true
  fi
}
trap cleanup HUP INT TERM EXIT
cp "$SCRIPT_DIR/package.json" "$SCRIPT_DIR/README.md" "$SCRIPT_DIR/LICENSE" "$SCRIPT_DIR/THIRD_PARTY-NOTICES.md" "$DESTINATION/"
cp "$SCRIPT_DIR/uninstall.sh" "$SCRIPT_DIR/verify.sh" "$DESTINATION/"
cp "$SCRIPT_DIR/extensions/context-normalizer.mjs" "$DESTINATION/extensions/"
python3 - "$DESTINATION/.ctxnorm-pi-install.json" "$DESTINATION" <<'PY'
import json, pathlib, sys
marker = {
    "schema_version": 1,
    "component": "pi",
    "version": "1.0.0",
    "destination": str(pathlib.Path(sys.argv[2]).resolve()),
}
pathlib.Path(sys.argv[1]).write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")
PY
pi install "$DESTINATION"
chmod 755 "$DESTINATION/uninstall.sh" "$DESTINATION/verify.sh"
trap - HUP INT TERM EXIT
DESTINATION_CREATED=0
"$DESTINATION/verify.sh"
printf '%s\n' "Pi integration installed at $DESTINATION"
