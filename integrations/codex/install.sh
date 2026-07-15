#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
DESTINATION="$HOME/.context-normalizer/integrations/codex"
command -v ctxnorm >/dev/null 2>&1 || { printf '%s\n' 'Context Normalizer core must be installed first.' >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { printf '%s\n' 'Python 3 must be installed first.' >&2; exit 1; }
command -v git >/dev/null 2>&1 || { printf '%s\n' 'Git must be installed first.' >&2; exit 1; }
command -v cargo >/dev/null 2>&1 || { printf '%s\n' 'Cargo must be installed first.' >&2; exit 1; }
[ ! -e "$DESTINATION" ] || { printf '%s\n' "Codex integration already exists at $DESTINATION" >&2; exit 1; }
ctxnorm doctor >/dev/null

UPSTREAM_REPOSITORY=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["upstream_repository"])' "$SCRIPT_DIR/integration.json")
UPSTREAM_TAG=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["upstream_tag"])' "$SCRIPT_DIR/integration.json")
UPSTREAM_COMMIT=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["upstream_commit"])' "$SCRIPT_DIR/integration.json")
TEMP_BASE=${TMPDIR:-/tmp}
TEMP_ROOT=$(mktemp -d "$TEMP_BASE/ctxnorm-codex.XXXXXX")
SOURCE="$TEMP_ROOT/codex"
DESTINATION_CREATED=0

cleanup() {
  case "$TEMP_ROOT" in
    "$TEMP_BASE"/ctxnorm-codex.*) rm -rf -- "$TEMP_ROOT" ;;
    *) printf '%s\n' 'Refused unexpected temporary cleanup path.' >&2 ;;
  esac
  if [ "$DESTINATION_CREATED" -eq 1 ]; then
    rm -f -- "$DESTINATION/bin/codex" "$DESTINATION/integration.json" "$DESTINATION/README.md" "$DESTINATION/LICENSE" "$DESTINATION/THIRD_PARTY-NOTICES.md" "$DESTINATION/ctxnorm-codex" "$DESTINATION/uninstall.sh" "$DESTINATION/verify.sh" "$DESTINATION/.ctxnorm-codex-install.json"
    rmdir "$DESTINATION/bin" "$DESTINATION" 2>/dev/null || true
  fi
}
trap cleanup HUP INT TERM EXIT

git clone --filter=blob:none --branch "$UPSTREAM_TAG" --depth 1 "$UPSTREAM_REPOSITORY" "$SOURCE"
[ "$(git -C "$SOURCE" rev-parse HEAD)" = "$UPSTREAM_COMMIT" ] || { printf '%s\n' 'Codex source revision validation failed.' >&2; exit 1; }
git -C "$SOURCE" apply "$SCRIPT_DIR/patches/codex-tui-context-normalizer.patch"
(
  cd "$SOURCE/codex-rs"
  cargo test -p codex-tui context_normalizer
  cargo build -p codex-cli --release
)
mkdir -p "$DESTINATION/bin"
DESTINATION_CREATED=1
cp "$SOURCE/codex-rs/target/release/codex" "$DESTINATION/bin/codex"
cp "$SCRIPT_DIR/integration.json" "$SCRIPT_DIR/README.md" "$SCRIPT_DIR/LICENSE" "$SCRIPT_DIR/THIRD_PARTY-NOTICES.md" "$SCRIPT_DIR/ctxnorm-codex" "$SCRIPT_DIR/uninstall.sh" "$SCRIPT_DIR/verify.sh" "$DESTINATION/"
python3 - "$SCRIPT_DIR/integration.json" "$DESTINATION/.ctxnorm-codex-install.json" "$DESTINATION" <<'PY'
import json, pathlib, sys
source = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
marker = {
    "schema_version": 1,
    "component": "codex",
    "version": source["version"],
    "upstream_commit": source["upstream_commit"],
    "destination": str(pathlib.Path(sys.argv[3]).resolve()),
}
pathlib.Path(sys.argv[2]).write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")
PY
chmod 755 "$DESTINATION/bin/codex" "$DESTINATION/ctxnorm-codex" "$DESTINATION/uninstall.sh" "$DESTINATION/verify.sh"
trap - HUP INT TERM EXIT
DESTINATION_CREATED=0
case "$TEMP_ROOT" in
  "$TEMP_BASE"/ctxnorm-codex.*) rm -rf -- "$TEMP_ROOT" ;;
  *) printf '%s\n' 'Refused unexpected temporary cleanup path.' >&2; exit 1 ;;
esac
"$DESTINATION/verify.sh"
printf '%s\n' "Codex integration installed at $DESTINATION"
