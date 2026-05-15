#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  run_mgr_cmd_auto.sh [PORT] <COMMAND> [READ_WINDOW_MS]
  run_mgr_cmd_auto.sh --listen [PORT] [LISTEN_SECONDS]
  run_mgr_cmd_auto.sh [--port PORT] [--read-ms N] <COMMAND>
  run_mgr_cmd_auto.sh --listen [--port PORT] [--seconds N]

Examples:
  run_mgr_cmd_auto.sh COM30 "WL+FWVER=?"
  run_mgr_cmd_auto.sh "WL+FWVER=?"                # auto-detect MGR port
  run_mgr_cmd_auto.sh /dev/ttyS30 "WL+DISK=IMG" 3000
  run_mgr_cmd_auto.sh --listen COM30 10
  run_mgr_cmd_auto.sh --listen                     # auto-detect + default 10s
  run_mgr_cmd_auto.sh --port COM30 --read-ms 2500 "WL+FWVER=?"
  run_mgr_cmd_auto.sh --listen --port COM30 --seconds 15

Notes:
- Auto-detects runtime and backend:
  1) In WSL with powershell.exe available: uses Windows PowerShell script.
  2) Otherwise falls back to Linux serial device (/dev/ttyS* or /dev/ttyUSB*).
- Port selection rule:
  1) 0 MGR ports: error
  2) 1 MGR port: auto-select
  3) >1 MGR ports: prompt user to choose (interactive); non-interactive mode requires explicit PORT
USAGE
}

MODE="cmd"
PORT_INPUT=""
CMD=""
READ_WINDOW_MS="1200"
LISTEN_SECONDS="10"
BAUD="${BAUD:-115200}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PS1_PATH="$SCRIPT_DIR/../windows/mgr_serial.ps1"

is_wsl() {
  grep -qiE "microsoft|wsl" /proc/version 2>/dev/null
}

is_com_port() {
  [[ "$1" =~ ^COM[0-9]+$ ]]
}

resolve_linux_tty() {
  local p="$1"
  if [[ "$p" =~ ^COM([0-9]+)$ ]]; then
    echo "/dev/ttyS${BASH_REMATCH[1]}"
    return 0
  fi
  echo "$p"
}

detect_mgr_ports() {
  if ! command -v v3dl >/dev/null 2>&1; then
    return 1
  fi

  local out
  if ! out="$(v3dl com 2>/dev/null || true)"; then
    return 1
  fi

  # Example: WLINK 0422 MGR  (COM30)
  echo "$out" | sed -nE 's/.*MGR[^\(]*\((COM[0-9]+)\).*/\1/p' | awk '!seen[$0]++'
}

select_port_interactive() {
  local -a ports=("$@")
  local n="${#ports[@]}"

  if [[ "$n" -eq 0 ]]; then
    echo "[ERR] No MGR port detected via 'v3dl com'. Please connect device and retry." >&2
    return 20
  fi

  if [[ "$n" -eq 1 ]]; then
    PORT_INPUT="${ports[0]}"
    echo "[INFO] Auto-selected MGR port: $PORT_INPUT"
    return 0
  fi

  echo "[INFO] Multiple MGR ports detected:"
  local i=1
  for p in "${ports[@]}"; do
    echo "  $i) $p"
    i=$((i + 1))
  done

  if [[ -t 0 ]]; then
    local choice
    read -r -p "Select MGR port index (1-$n): " choice
    if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= n )); then
      PORT_INPUT="${ports[$((choice-1))]}"
      echo "[INFO] Selected MGR port: $PORT_INPUT"
      return 0
    fi
    echo "[ERR] Invalid selection: $choice" >&2
    return 21
  fi

  echo "[ERR] Multiple MGR ports found in non-interactive mode. Please pass PORT explicitly." >&2
  return 22
}

auto_select_port_if_needed() {
  if [[ -n "$PORT_INPUT" ]]; then
    return 0
  fi

  mapfile -t ports < <(detect_mgr_ports || true)
  select_port_interactive "${ports[@]}"
}

send_via_powershell_cmd() {
  local port="$1"
  local cmd="$2"
  local read_ms="$3"

  if [[ ! -f "$PS1_PATH" ]]; then
    echo "[ERR] PowerShell script not found: $PS1_PATH" >&2
    return 2
  fi

  local ps1_win
  ps1_win="$(wslpath -w "$PS1_PATH")"

  /mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe \
    -NoProfile -ExecutionPolicy Bypass -File "$ps1_win" \
    -Port "$port" -Command "$cmd" -ReadWindowMs "$read_ms"
}

send_via_powershell_listen() {
  local port="$1"
  local sec="$2"

  if [[ ! -f "$PS1_PATH" ]]; then
    echo "[ERR] PowerShell script not found: $PS1_PATH" >&2
    return 2
  fi

  local ps1_win
  ps1_win="$(wslpath -w "$PS1_PATH")"

  /mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe \
    -NoProfile -ExecutionPolicy Bypass -File "$ps1_win" \
    -Port "$port" -Listen -ListenSeconds "$sec"
}

send_via_linux_tty_cmd() {
  local port="$1"
  local cmd="$2"
  local read_ms="$3"
  local dev
  dev="$(resolve_linux_tty "$port")"

  if [[ ! -e "$dev" ]]; then
    echo "[ERR] Serial device not found: $dev" >&2
    return 3
  fi

  stty -F "$dev" "$BAUD" cs8 -cstopb -parenb -icanon -echo min 0 time 5

  exec 3<>"$dev"
  printf "%s\r\n" "$cmd" >&3

  local deadline=$(( $(date +%s%3N) + read_ms ))
  local got=0
  while [[ $(date +%s%3N) -lt $deadline ]]; do
    if IFS= read -r -t 0.2 line <&3; then
      if [[ -n "$line" ]]; then
        printf '%s\n' "$line"
        got=1
      fi
    fi
  done

  exec 3>&-
  exec 3<&-

  if [[ $got -eq 0 ]]; then
    echo "[WARN] No response within ${read_ms}ms"
  fi
}

send_via_linux_tty_listen() {
  local port="$1"
  local sec="$2"
  local dev
  dev="$(resolve_linux_tty "$port")"

  if [[ ! -e "$dev" ]]; then
    echo "[ERR] Serial device not found: $dev" >&2
    return 3
  fi

  stty -F "$dev" "$BAUD" cs8 -cstopb -parenb -icanon -echo min 0 time 5

  echo "[INFO] Listening on $dev for ${sec}s..."
  timeout "${sec}s" cat "$dev" || true
}

run_powershell_backend() {
  if [[ "$MODE" == "listen" ]]; then
    send_via_powershell_listen "$PORT_INPUT" "$LISTEN_SECONDS"
  else
    send_via_powershell_cmd "$PORT_INPUT" "$CMD" "$READ_WINDOW_MS"
  fi
}

run_linux_backend() {
  if [[ "$MODE" == "listen" ]]; then
    send_via_linux_tty_listen "$PORT_INPUT" "$LISTEN_SECONDS"
  else
    send_via_linux_tty_cmd "$PORT_INPUT" "$CMD" "$READ_WINDOW_MS"
  fi
}

# -------------------- Arg parse --------------------
if [[ $# -lt 1 ]]; then
  usage
  exit 1
fi

# Parse long options first. Remaining args keep backward compatibility with positional form.
while [[ $# -gt 0 ]]; do
  case "$1" in
    --listen)
      MODE="listen"
      shift
      ;;
    --port)
      if [[ $# -lt 2 ]]; then
        echo "[ERR] --port requires a value" >&2
        exit 1
      fi
      PORT_INPUT="$2"
      shift 2
      ;;
    --read-ms)
      if [[ $# -lt 2 ]]; then
        echo "[ERR] --read-ms requires a value" >&2
        exit 1
      fi
      READ_WINDOW_MS="$2"
      shift 2
      ;;
    --seconds)
      if [[ $# -lt 2 ]]; then
        echo "[ERR] --seconds requires a value" >&2
        exit 1
      fi
      LISTEN_SECONDS="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    *)
      break
      ;;
  esac
done

if [[ "$MODE" == "listen" ]]; then
  # Backward-compatible positional parse: [PORT] [SECONDS]
  if [[ $# -ge 1 ]] && [[ -z "$PORT_INPUT" ]] && ( is_com_port "$1" || [[ "$1" == /dev/* ]] ); then
    PORT_INPUT="$1"
    shift
  fi
  if [[ $# -ge 1 ]]; then
    LISTEN_SECONDS="$1"
    shift
  fi
else
  # Backward-compatible positional parse:
  # a) PORT CMD [READ_MS]
  # b) CMD [READ_MS]
  if [[ $# -ge 2 ]] && [[ -z "$PORT_INPUT" ]] && ( is_com_port "$1" || [[ "$1" == /dev/* ]] ); then
    PORT_INPUT="$1"
    CMD="$2"
    if [[ $# -ge 3 ]] && [[ "$READ_WINDOW_MS" == "1200" ]]; then
      READ_WINDOW_MS="$3"
    fi
    shift $(( $# >= 3 ? 3 : 2 ))
  elif [[ $# -ge 1 ]]; then
    CMD="$1"
    if [[ $# -ge 2 ]] && [[ "$READ_WINDOW_MS" == "1200" ]]; then
      READ_WINDOW_MS="$2"
    fi
    shift $(( $# >= 2 ? 2 : 1 ))
  fi
fi

if [[ "$MODE" == "cmd" && -z "$CMD" ]]; then
  usage
  exit 1
fi

auto_select_port_if_needed

# -------------------- Backend dispatch --------------------
if is_wsl && [[ -x /mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe ]]; then
  echo "[INFO] Backend: Windows PowerShell (WSL bridge)"
  if ! run_powershell_backend; then
    echo "[WARN] PowerShell backend failed, fallback to Linux TTY backend..." >&2
    if is_com_port "$PORT_INPUT"; then
      fallback_tty="$(resolve_linux_tty "$PORT_INPUT")"
      if [[ ! -e "$fallback_tty" ]]; then
        echo "[ERR] Fallback failed: $fallback_tty not found. If COM port is >3, run this command in Windows terminal." >&2
        exit 3
      fi
    fi
    run_linux_backend
  fi
else
  echo "[INFO] Backend: Linux TTY"
  run_linux_backend
fi
