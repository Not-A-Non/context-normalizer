#!/usr/bin/env sh
set -eu
. "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/context-normalizer-runtime.sh"
context_normalizer_runtime
"$CONTEXT_NORMALIZER_RUNTIME" -m context_normalizer purge --yes
"$CONTEXT_NORMALIZER_RUNTIME" -m pip uninstall -y context-normalizer
printf '%s\n' 'Context Normalizer was removed.'
