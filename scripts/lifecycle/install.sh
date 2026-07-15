#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
CONFIG_ROOT=${CONTEXT_NORMALIZER_HOME:-"$HOME/.context-normalizer"}
case "$CONFIG_ROOT" in
  /*) ;;
  *) printf '%s\n' 'CONTEXT_NORMALIZER_HOME must be absolute.' >&2; exit 1 ;;
esac

EXISTING=$(python3 - <<'PY'
import importlib.metadata as m
try:
    print(m.version("context-normalizer"))
except m.PackageNotFoundError:
    print("absent")
PY
)
if [ "$EXISTING" != absent ]; then
  printf '%s\n' "A context-normalizer package is already installed ($EXISTING). This installer is for fresh installs only." >&2
  exit 1
fi
if [ -e "$CONFIG_ROOT" ] || [ -L "$CONFIG_ROOT" ]; then
  printf '%s\n' "Configuration already exists at $CONFIG_ROOT. It was not changed." >&2
  exit 1
fi

BUILD_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/ctxnorm-build.XXXXXXXX")
INSTALLED=0
finish() {
  status=$?
  rm -rf -- "$BUILD_ROOT"
  if [ "$status" -ne 0 ] && [ "$INSTALLED" -eq 1 ]; then
    rollback_ok=1
    if [ -f "$CONFIG_ROOT/installation.json" ]; then
      python3 -m context_normalizer purge --yes || rollback_ok=0
    elif [ -d "$CONFIG_ROOT" ]; then
      if ! rmdir -- "$CONFIG_ROOT"; then
        rollback_ok=0
        printf '%s\n' "Unmarked partial configuration retained at $CONFIG_ROOT" >&2
      fi
    fi
    if [ "$rollback_ok" -eq 1 ]; then
      python3 -m pip uninstall -y context-normalizer || rollback_ok=0
    fi
    if [ "$rollback_ok" -ne 1 ]; then
      printf '%s\n' 'Installation rollback was incomplete; recovery-capable package/state was retained.' >&2
    fi
  fi
  trap - EXIT HUP INT TERM
  exit "$status"
}
trap finish EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

python3 -m venv "$BUILD_ROOT/venv"
"$BUILD_ROOT/venv/bin/python" -m pip install \
  --require-hashes --only-binary=:all: -r "$ROOT/requirements/build.lock"
PIP_NO_INDEX=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
  "$BUILD_ROOT/venv/bin/python" -m build --wheel --no-isolation \
  --outdir "$BUILD_ROOT/wheel" "$ROOT"
set -- "$BUILD_ROOT"/wheel/*.whl
[ "$#" -eq 1 ] && [ -f "$1" ] || { printf '%s\n' 'Expected exactly one built wheel.' >&2; exit 1; }
python3 -m pip install --user --no-deps "$1"
INSTALLED=1
python3 -m context_normalizer init
python3 -m context_normalizer doctor
if [ "${CONTEXT_NORMALIZER_TEST_FAIL_AFTER_INIT:-}" = 1 ]; then
  printf '%s\n' 'Injected post-init failure for lifecycle acceptance testing.' >&2
  exit 1
fi
python3 -m context_normalizer doctor

printf '%s\n' \
  '' \
  'Installed. Normalize a draft with:' \
  '  python3 -m context_normalizer normalize prompt.md --output prompt.normalized.md --audit prompt.audit.json --preview' \
  '' \
  'Edit rules.tsv and cues.txt under ~/.context-normalizer.'
