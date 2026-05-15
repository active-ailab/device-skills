#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELPER_SRC="$SCRIPT_DIR/watchlink-v3-mount-helper"
TEMPLATE_SRC="$SCRIPT_DIR/watchlink-v3-mount-helper.sudoers.template"

TARGET_USER="${SUDO_USER:-${USER:-}}"
HELPER_DST="/usr/local/sbin/watchlink-v3-mount-helper"
SUDOERS_NAME="99-watchlink-v3-v3dl-com"
INCLUDE_V3DL_COM=1

usage() {
  cat <<'USAGE'
Usage:
  install_watchlink_v3_sudo.sh [--user USER] [--helper-dst PATH] [--sudoers-name NAME] [--watchlink-only]

Options:
  --user USER           Install rules for the specified user. Default: current shell user.
  --helper-dst PATH     Helper install destination. Default: /usr/local/sbin/watchlink-v3-mount-helper
  --sudoers-name NAME   Filename under /etc/sudoers.d/. Default: 99-watchlink-v3-v3dl-com
  --watchlink-only      Only install watchlink-v3 helper rule; skip v3dl com compatibility rule.
  -h, --help            Show this help.
USAGE
}

require_file() {
  local path="$1"
  [[ -f "$path" ]] || {
    echo "[ERR] required file not found: $path" >&2
    exit 1
  }
}

validate_user() {
  local user="$1"
  [[ "$user" =~ ^[A-Za-z_][A-Za-z0-9_-]*$ ]] || {
    echo "[ERR] invalid user: $user" >&2
    exit 1
  }
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user) TARGET_USER="$2"; shift 2 ;;
    --helper-dst) HELPER_DST="$2"; shift 2 ;;
    --sudoers-name) SUDOERS_NAME="$2"; shift 2 ;;
    --watchlink-only) INCLUDE_V3DL_COM=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "[ERR] Unknown arg: $1" >&2
      usage
      exit 1
      ;;
  esac
done

require_file "$HELPER_SRC"
require_file "$TEMPLATE_SRC"

[[ -n "$TARGET_USER" ]] || {
  echo "[ERR] unable to infer target user; pass --user explicitly." >&2
  exit 1
}
validate_user "$TARGET_USER"

SUDOERS_TMP="$(mktemp /tmp/watchlink-v3-sudoers.XXXXXX)"
trap 'rm -f "$SUDOERS_TMP"' EXIT

if [[ "$INCLUDE_V3DL_COM" -eq 1 ]]; then
  V3DL_RULE="$TARGET_USER ALL=(root) NOPASSWD: V3DL_COM_CMDS"
else
  V3DL_RULE="# v3dl com compatibility rule omitted (--watchlink-only)"
fi

sed \
  -e "s|__WATCHLINK_USER__|$TARGET_USER|g" \
  -e "s|__WATCHLINK_HELPER_DST__|$HELPER_DST|g" \
  -e "s|__V3DL_RULE__|$V3DL_RULE|g" \
  "$TEMPLATE_SRC" > "$SUDOERS_TMP"

echo "[INFO] Validating generated sudoers: $SUDOERS_TMP"
sudo /usr/sbin/visudo -cf "$SUDOERS_TMP"

echo "[INFO] Installing helper to $HELPER_DST"
sudo /usr/bin/install -m 755 "$HELPER_SRC" "$HELPER_DST"

echo "[INFO] Installing sudoers to /etc/sudoers.d/$SUDOERS_NAME"
sudo /usr/bin/install -m 440 "$SUDOERS_TMP" "/etc/sudoers.d/$SUDOERS_NAME"

echo "[INFO] Validating /etc/sudoers"
sudo /usr/sbin/visudo -cf /etc/sudoers

echo "[INFO] Verifying rules"
sudo -k
sudo -n "$HELPER_DST" --help >/dev/null
if [[ "$INCLUDE_V3DL_COM" -eq 1 ]]; then
  sudo -n true >/dev/null
fi

echo "[OK] Installed watchlink-v3 sudo setup for user: $TARGET_USER"
echo "[OK] helper: $HELPER_DST"
echo "[OK] sudoers: /etc/sudoers.d/$SUDOERS_NAME"
