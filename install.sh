#!/usr/bin/env bash
set -euo pipefail

DS4_REPO_URL="https://github.com/antirez/ds4.git"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
DEFAULT_DS4_ROOT="$(cd "$APP_DIR/.." && pwd -P)/ds4"

DS4_ROOT="${DS4_ROOT:-$DEFAULT_DS4_ROOT}"
HOST="127.0.0.1"
PORT="8787"
CLONE_MODE="ask"
BUILD_DS4="ask"
START_AFTER="0"
DRY_RUN="0"

usage() {
  cat <<USAGE
DS Gateway installer

Usage:
  ./install.sh [options]

Options:
  --ds4-root PATH   Path to an existing or desired antirez/ds4 checkout.
                    Default: ../ds4 next to this DS Gateway checkout.
  --clone-ds4       Clone antirez/ds4 if it is missing.
                    Still asks for confirmation before cloning.
  --no-clone        Do not clone; fail if ds4 is missing.
  --build-ds4       Run "make ds4-server" in the ds4 checkout.
                    Still asks for confirmation before building.
  --no-build        Do not offer to build ds4-server.
  --dry-run         Print planned actions without changing files.
  --host HOST       DS Gateway host. Default: 127.0.0.1.
  --port PORT       DS Gateway port. Default: 8787.
  --start           Start DS Gateway after setup.
                    Still asks for confirmation before starting.
  -h, --help        Show this help.

This installer does not modify DS4 source code. It asks before cloning DS4,
building DS4, or starting DS Gateway.
USAGE
}

log() {
  printf '\033[1;32m%s\033[0m %s\n' "✓" "$*"
}

info() {
  printf '\033[1;34m%s\033[0m %s\n' "•" "$*"
}

warn() {
  printf '\033[1;33m%s\033[0m %s\n' "!" "$*" >&2
}

die() {
  printf '\033[1;31m%s\033[0m %s\n' "✗" "$*" >&2
  exit 1
}

abs_path() {
  python3 - "$1" <<'PY'
import os
import sys
print(os.path.abspath(os.path.expanduser(sys.argv[1])))
PY
}

ask_yes_no() {
  local prompt="$1"
  local default="${2:-y}"
  local suffix="[Y/n]"
  if [ "$default" = "n" ]; then
    suffix="[y/N]"
  fi
  if [ ! -t 0 ]; then
    [ "$default" = "y" ]
    return
  fi
  local answer
  read -r -p "$prompt $suffix " answer
  answer="${answer:-$default}"
  case "$answer" in
    y|Y|yes|YES) return 0 ;;
    *) return 1 ;;
  esac
}

confirm_action() {
  local prompt="$1"
  local default="${2:-n}"
  if ask_yes_no "$prompt" "$default"; then
    return 0
  fi
  return 1
}

run_cmd() {
  if [ "$DRY_RUN" = "1" ]; then
    printf '[dry-run] %q' "$1"
    shift
    local arg
    for arg in "$@"; do
      printf ' %q' "$arg"
    done
    printf '\n'
    return 0
  fi
  "$@"
}

need_command() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

looks_like_ds4() {
  local root="$1"
  [ -d "$root" ] &&
    [ -f "$root/Makefile" ] &&
    [ -x "$root/download_model.sh" ] &&
    [ -f "$root/ds4_server.c" ]
}

ds4_remote_label() {
  local root="$1"
  if [ -d "$root/.git" ] && command -v git >/dev/null 2>&1; then
    if git -C "$root" remote -v 2>/dev/null | grep -Eq 'github.com[:/]antirez/ds4(\.git)?'; then
      printf 'antirez/ds4'
      return
    fi
    local remote
    remote="$(git -C "$root" remote -v 2>/dev/null | head -n 1 || true)"
    if [ -n "$remote" ]; then
      printf '%s' "$remote"
      return
    fi
  fi
  printf 'local files'
}

find_ds4() {
  local candidates=(
    "$DS4_ROOT"
    "$DEFAULT_DS4_ROOT"
    "$APP_DIR/ds4"
    "$HOME/ds4"
  )
  local candidate
  for candidate in "${candidates[@]}"; do
    candidate="$(abs_path "$candidate")"
    if looks_like_ds4 "$candidate"; then
      printf '%s' "$candidate"
      return
    fi
  done
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --ds4-root)
      [ "${2:-}" ] || die "--ds4-root requires a path"
      DS4_ROOT="$2"
      shift 2
      ;;
    --clone-ds4)
      CLONE_MODE="yes"
      shift
      ;;
    --no-clone)
      CLONE_MODE="no"
      shift
      ;;
    --build-ds4)
      BUILD_DS4="yes"
      shift
      ;;
    --no-build)
      BUILD_DS4="no"
      shift
      ;;
    --dry-run)
      DRY_RUN="1"
      shift
      ;;
    --host)
      [ "${2:-}" ] || die "--host requires a value"
      HOST="$2"
      shift 2
      ;;
    --port)
      [ "${2:-}" ] || die "--port requires a value"
      PORT="$2"
      shift 2
      ;;
    --start)
      START_AFTER="1"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Unknown option: $1"
      ;;
  esac
done

need_command python3
need_command git

DS4_ROOT="$(abs_path "$DS4_ROOT")"

info "DS Gateway: $APP_DIR"
info "Desired DS4 checkout: $DS4_ROOT"

FOUND_DS4="$(find_ds4 || true)"
if [ -n "$FOUND_DS4" ]; then
  DS4_ROOT="$FOUND_DS4"
  log "Found DS4 checkout at $DS4_ROOT ($(ds4_remote_label "$DS4_ROOT"))"
else
  warn "Could not find an antirez/ds4 checkout."
  if [ "$CLONE_MODE" = "no" ]; then
    die "Clone DS4 first, or rerun with --clone-ds4."
  fi
  clone_default="n"
  if [ "$CLONE_MODE" = "yes" ]; then
    clone_default="y"
  fi
  if confirm_action "Clone antirez/ds4 into $DS4_ROOT?" "$clone_default"; then
    run_cmd mkdir -p "$(dirname "$DS4_ROOT")"
    run_cmd git clone "$DS4_REPO_URL" "$DS4_ROOT"
    log "Cloned antirez/ds4 into $DS4_ROOT"
  else
    die "DS4 is required. Clone it with: git clone $DS4_REPO_URL $DS4_ROOT"
  fi
fi

looks_like_ds4 "$DS4_ROOT" || die "$DS4_ROOT does not look like antirez/ds4."

if [ "$(ds4_remote_label "$DS4_ROOT")" != "antirez/ds4" ]; then
  warn "DS4 files are present, but the Git remote is not antirez/ds4. Continuing with local checkout."
fi

run_cmd mkdir -p "$APP_DIR/data/server-kv"
if [ "$DRY_RUN" = "1" ]; then
  info "Would chmod 700 $APP_DIR/data"
  info "Would prepare runtime data directory at $APP_DIR/data"
else
  chmod 700 "$APP_DIR/data" 2>/dev/null || true
  log "Prepared runtime data directory at $APP_DIR/data"
fi

if [ -x "$DS4_ROOT/ds4-server" ]; then
  log "DS4 server binary already exists: $DS4_ROOT/ds4-server"
else
  warn "DS4 server binary is not built yet."
  build_default="n"
  if [ "$BUILD_DS4" = "yes" ]; then
    build_default="y"
  fi
  if [ "$BUILD_DS4" != "no" ] && confirm_action "Build ds4-server now with DS4 Makefile?" "$build_default"; then
    need_command make
    info "Building ds4-server with DS4 Makefile..."
    run_cmd make -C "$DS4_ROOT" ds4-server
    log "Built ds4-server"
  else
    info "Skipping build. DS Gateway can build ds4-server later when you click Start."
  fi
fi

LAUNCHER="$APP_DIR/data/run-ds-gateway.sh"
if [ "$DRY_RUN" = "1" ]; then
  info "Would write local launcher: $LAUNCHER"
else
  cat >"$LAUNCHER" <<EOF
#!/usr/bin/env bash
set -euo pipefail
cd "$APP_DIR"
exec python3 "$APP_DIR/ds4_ui.py" --ds4-root "$DS4_ROOT" --host "$HOST" --port "$PORT" "\$@"
EOF
  chmod +x "$LAUNCHER"
  log "Wrote local launcher: $LAUNCHER"
fi

cat <<SUMMARY

DS Gateway setup complete.

Run:
  $LAUNCHER

Then open:
  http://$HOST:$PORT

DS4 checkout:
  $DS4_ROOT

First model setup:
  1. Open DS Gateway.
  2. Use the recommended model/download controls.
  3. Click Start in the top bar to load DS4.
  4. Use Settings > Server Exposure > Start to expose the OpenAI endpoint.

SUMMARY

if [ "$START_AFTER" = "1" ]; then
  if confirm_action "Start DS Gateway now on http://$HOST:$PORT?" "y"; then
    exec "$LAUNCHER"
  fi
fi
