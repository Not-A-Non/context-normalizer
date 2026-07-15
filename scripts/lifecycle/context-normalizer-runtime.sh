context_normalizer_runtime() {
  config_dir=${CONTEXT_NORMALIZER_HOME:-"$HOME/.context-normalizer"}
  case "$config_dir" in
    /*) ;;
    *) printf '%s\n' 'CONTEXT_NORMALIZER_HOME must be an absolute path' >&2; return 2 ;;
  esac
  marker="$config_dir/installation.json"
  runtime="$config_dir/runtime-python.txt"
  if [ ! -f "$marker" ] || [ ! -f "$runtime" ]; then
    printf '%s\n' "Context Normalizer installation marker is missing under $config_dir" >&2
    return 2
  fi
  python_runtime=$(sed -n '1p' "$runtime")
  case "$python_runtime" in
    /*) ;;
    *) printf '%s\n' "Recorded Python runtime is not absolute: $python_runtime" >&2; return 2 ;;
  esac
  if [ ! -x "$python_runtime" ]; then
    printf '%s\n' "Recorded Python runtime is not executable: $python_runtime" >&2
    return 2
  fi
  CONTEXT_NORMALIZER_RUNTIME=$python_runtime
  export CONTEXT_NORMALIZER_RUNTIME
}
