#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  run_serial_cmd_auto.sh --role UART|MGR [--port COMx|/dev/ttySx] --cmd "COMMAND" [--read-ms N]
  run_serial_cmd_auto.sh --role UART|MGR [--port COMx|/dev/ttySx] --listen [--seconds N]

Examples:
  run_serial_cmd_auto.sh --role UART --cmd "monkey -g"
  run_serial_cmd_auto.sh --role UART --port COM31 --cmd "monkey -s on -p on -m 0"
  run_serial_cmd_auto.sh --role MGR  --cmd "WL+FWVER=?"
USAGE
}

ROLE=""
PORT=""
CMD=""
READ_MS="1200"
LISTEN=0
SECONDS="10"
BAUD="${BAUD:-115200}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --role) ROLE="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --cmd) CMD="$2"; shift 2 ;;
    --read-ms) READ_MS="$2"; shift 2 ;;
    --listen) LISTEN=1; shift ;;
    --seconds) SECONDS="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "[ERR] Unknown arg: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ -z "$ROLE" ]]; then
  echo "[ERR] --role is required" >&2
  usage
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PS1_PATH="$SCRIPT_DIR/../windows/serial_cmd_auto.ps1"

is_wsl() { grep -qiE "microsoft|wsl" /proc/version 2>/dev/null; }
is_com_port() { [[ "$1" =~ ^COM[0-9]+$ ]]; }
resolve_linux_tty() {
  local p="$1"
  if [[ "$p" =~ ^COM([0-9]+)$ ]]; then echo "/dev/ttyS${BASH_REMATCH[1]}"; else echo "$p"; fi
}

detect_from_v3dl() {
  local role="$1"
  local key="MGR"
  [[ "$role" == "UART" ]] && key="UART"
  local out
  out="$(v3dl com 2>&1 || true)"
  if echo "$out" | grep -qiE "sudo session expired|authentication required|terminal is required to read the password|sudo:"; then
    echo "[ERR] v3dl com failed due to sudo/auth. Run scripts/linux/install_watchlink_v3_sudo.sh once for persistent setup, or run 'sudo -v' in your own terminal and retry." >&2
    return 31
  fi
  echo "$out" | sed -nE "s/.*${key}[^\(]*\((COM[0-9]+)\).*/\1/p" | awk '!seen[$0]++'
}

auto_pick_port_if_needed() {
  if [[ -n "$PORT" ]]; then return 0; fi
  mapfile -t ports < <(detect_from_v3dl "$ROLE" || true)
  if [[ ${#ports[@]} -eq 0 ]]; then
    echo "[ERR] No $ROLE port detected. Run 'v3dl com' and reconnect device." >&2
    exit 20
  elif [[ ${#ports[@]} -eq 1 ]]; then
    PORT="${ports[0]}"
    echo "[INFO] Auto-selected $ROLE port: $PORT"
  else
    if [[ -t 0 ]]; then
      echo "[INFO] Multiple $ROLE ports detected:"
      local i=1
      for p in "${ports[@]}"; do echo "  $i) $p"; i=$((i+1)); done
      read -r -p "Select index: " idx
      if [[ "$idx" =~ ^[0-9]+$ ]] && (( idx>=1 && idx<=${#ports[@]} )); then
        PORT="${ports[$((idx-1))]}"
      else
        echo "[ERR] Invalid selection" >&2
        exit 21
      fi
    else
      echo "[ERR] Multiple $ROLE ports found; pass --port explicitly." >&2
      exit 22
    fi
  fi
}

run_powershell_bridge() {
  local ps1_win
  ps1_win="$(wslpath -w "$PS1_PATH")"
  if [[ "$LISTEN" -eq 1 ]]; then
    /mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$ps1_win" -Role "$ROLE" -Port "$PORT" -Listen -ListenSeconds "$SECONDS"
  else
    /mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$ps1_win" -Role "$ROLE" -Port "$PORT" -Command "$CMD" -ReadWindowMs "$READ_MS"
  fi
}

run_linux_tty_fallback() {
  local dev
  dev="$(resolve_linux_tty "$PORT")"
  if [[ ! -e "$dev" ]]; then
    echo "[ERR] Linux tty not found: $dev" >&2
    exit 3
  fi
  stty -F "$dev" "$BAUD" cs8 -cstopb -parenb -icanon -echo min 0 time 5

  if [[ "$LISTEN" -eq 1 ]]; then
    echo "[INFO] Listening on $dev for ${SECONDS}s..."
    timeout "${SECONDS}s" cat "$dev" || true
  else
    exec 3<>"$dev"
    printf "%s\r\n" "$CMD" >&3
    local deadline=$(( $(date +%s%3N) + READ_MS ))
    local got=0
    while [[ $(date +%s%3N) -lt $deadline ]]; do
      if IFS= read -r -t 0.2 line <&3; then
        [[ -n "$line" ]] && echo "$line" && got=1
      fi
    done
    exec 3>&-
    exec 3<&-
    [[ $got -eq 0 ]] && echo "[WARN] No response within ${READ_MS}ms"
  fi
}

if [[ "$LISTEN" -eq 0 && -z "$CMD" ]]; then
  echo "[ERR] --cmd is required unless --listen is used" >&2
  usage
  exit 1
fi

auto_pick_port_if_needed

if is_wsl && [[ -x /mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe ]]; then
  echo "[INFO] Backend: Windows PowerShell bridge"
  if ! run_powershell_bridge; then
    echo "[WARN] Windows bridge failed, fallback Linux tty..." >&2
    echo "[HINT] If error contains 'UtilBindVsockAnyPort', use Windows terminal to run wlctl.ps1 directly." >&2
    run_linux_tty_fallback
  fi
else
  echo "[INFO] Backend: Linux tty"
  run_linux_tty_fallback
fi
