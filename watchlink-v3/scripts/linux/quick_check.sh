#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WLCTL="$SCRIPT_DIR/wlctl.sh"

uart_port=""
mgr_port=""
smoke_monkey=0
interval_ms="1000"
print_opt="off"
mem_opt="0"

usage() {
  cat <<'USAGE'
Usage:
  quick_check.sh [--uart-port PORT] [--mgr-port PORT]
                 [--smoke-monkey] [--interval-ms N] [--print on|off] [--mem 0|6]

Behavior:
  - Detect UART/MGR ports when possible
  - Query WL+FWVER=? on MGR
  - Query monkey status on UART
  - Optionally run a monkey on/off smoke test and restore the original state
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --uart-port) uart_port="$2"; shift 2 ;;
    --mgr-port) mgr_port="$2"; shift 2 ;;
    --smoke-monkey) smoke_monkey=1; shift ;;
    --interval-ms) interval_ms="$2"; shift 2 ;;
    --print) print_opt="$2"; shift 2 ;;
    --mem) mem_opt="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "[ERR] Unknown arg: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ ! -x "$WLCTL" ]]; then
  chmod +x "$WLCTL" 2>/dev/null || true
fi

get_v3dl_output() {
  v3dl com 2>/dev/null && return 0
  if command -v wsl.exe >/dev/null 2>&1; then
    wsl.exe bash -lc 'v3dl com' 2>/dev/null && return 0
  fi
  return 1
}

get_role_ports() {
  local role="$1"
  local key="MGR"
  [[ "$role" == "UART" ]] && key="UART"
  local out
  out="$(get_v3dl_output || true)"
  [[ -z "$out" ]] && return 0
  echo "$out" | sed -nE "s/.*${key}[^\(]*\((COM[0-9]+|\/dev\/tty[^)]*)\).*/\1/p" | awk '!seen[$0]++'
}

resolve_port() {
  local role="$1"
  local explicit_port="$2"
  if [[ -n "$explicit_port" ]]; then
    echo "$explicit_port"
    return 0
  fi

  mapfile -t ports < <(get_role_ports "$role")
  if [[ ${#ports[@]} -eq 1 ]]; then
    echo "${ports[0]}"
    return 0
  fi
  if [[ ${#ports[@]} -eq 0 ]]; then
    echo "[ERR] No $role port detected. Pass the port explicitly or make v3dl available." >&2
    return 1
  fi

  echo "[ERR] Multiple $role ports detected (${ports[*]}). Pass the port explicitly." >&2
  return 1
}

run_wlctl() {
  "$WLCTL" "$@"
}

step() {
  echo "[STEP] $1"
}

uart_port="$(resolve_port UART "$uart_port")"
mgr_port="$(resolve_port MGR "$mgr_port")"

echo "[INFO] UART port: $uart_port"
echo "[INFO] MGR port: $mgr_port"

step 'Querying firmware version via MGR'
run_wlctl serial --role MGR --port "$mgr_port" --cmd 'WL+FWVER=?'

step 'Querying monkey status via UART'
status_output="$(run_wlctl monkey status --port "$uart_port" | tee /dev/stderr)"
initial_state="unknown"
if grep -q 'monkey status on' <<<"$status_output"; then
  initial_state="on"
elif grep -q 'monkey status off' <<<"$status_output"; then
  initial_state="off"
fi

if [[ $smoke_monkey -eq 0 ]]; then
  echo "[PASS] Quick check completed without monkey state changes."
  exit 0
fi

step 'Running monkey control smoke test'
run_wlctl monkey off --port "$uart_port"
run_wlctl monkey on --port "$uart_port" --interval-ms "$interval_ms" --print "$print_opt" --mem "$mem_opt"
after_on="$(run_wlctl monkey status --port "$uart_port" | tee /dev/stderr)"
if ! grep -q 'monkey status on' <<<"$after_on"; then
  echo '[ERR] Monkey smoke test failed: status did not become on.' >&2
  exit 2
fi

if [[ "$initial_state" == 'off' ]]; then
  step 'Restoring original monkey state to off'
  run_wlctl monkey off --port "$uart_port"
elif [[ "$initial_state" == 'on' ]]; then
  step 'Restoring original monkey state to on'
  run_wlctl monkey on --port "$uart_port" --interval-ms "$interval_ms" --print "$print_opt" --mem "$mem_opt"
fi

echo "[PASS] Quick check completed, monkey control works on $uart_port."

