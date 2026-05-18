#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SERIAL_SH="${WLCTL_SERIAL_SH:-$SCRIPT_DIR/run_serial_cmd_auto.sh}"
DIAGNOSE_PY="${WLCTL_DIAGNOSE_PY:-$ROOT_DIR/common/diagnose_utils.py}"
FS_PY="${WLCTL_FS_PY:-$ROOT_DIR/common/fs_utils.py}"
DISK_PS1="${WLCTL_DISK_PS1:-$ROOT_DIR/windows/disk_mode_auto.ps1}"
DISK_BRIDGE_SH="${WLCTL_DISK_BRIDGE_SH:-}"

usage() {
  cat <<'USAGE'
wlctl - stable entrypoint for device-watchlink-v3

Usage:
  wlctl.sh serial --role UART|MGR [--port PORT] --cmd "COMMAND" [--read-ms N]
  wlctl.sh listen --role UART|MGR [--port PORT] [--seconds N]
  wlctl.sh monkey on [--port PORT] [--interval-ms N] [--print on|off] [--mem 0|6]
  wlctl.sh monkey off [--port PORT]
  wlctl.sh monkey status [--port PORT]
  wlctl.sh disk mount-data|mount-log|unmount [--port PORT] [--wait-seconds N]
  wlctl.sh fs ls|read|pull|push|rm [fs args...]
  wlctl.sh vbus on|off|status [--port PORT] [--output json|text]
  wlctl.sh ready [--port PORT] [--timeout N] [--probe-cmd CMD] [--output json|text]
  wlctl.sh awake [--port PORT] [--timeout N] [--output json|text]
  wlctl.sh diagnose [--message TEXT] [--message-file PATH] [--output json|text]

Examples:
  wlctl.sh serial --role MGR --cmd "WL+FWVER=?"
  wlctl.sh serial --role UART --cmd "monkey -g"
  wlctl.sh monkey on --interval-ms 1000 --print off --mem 0
  wlctl.sh monkey status
  wlctl.sh disk mount-data --wait-seconds 30
  wlctl.sh fs pull --disk log --from-path "**/*.log" --to-path ./img_logs
  wlctl.sh vbus status
  wlctl.sh ready --timeout 60
  wlctl.sh awake --timeout 15
  wlctl.sh diagnose --message "cmd.exe timed out" --output json
USAGE
}

[[ $# -lt 1 ]] && { usage; exit 1; }

case "${1:-}" in
  -h|--help|help)
    usage
    exit 0
    ;;
esac

cmd="$1"; shift || true

trim_line() {
  sed 's/^[[:space:]]*//;s/[[:space:]]*$//'
}

normalize_vbus_state() {
  local raw="$1"
  local line=""
  local lower=""
  while IFS= read -r line; do
    lower="$(printf '%s' "$line" | tr -d '\r' | trim_line | tr '[:upper:]' '[:lower:]')"
    case "$lower" in
      1|1.0|on|vbus=on|wl+vbus=on)
        echo "on"
        return 0
        ;;
      0|0.0|off|vbus=off|wl+vbus=off)
        echo "off"
        return 0
        ;;
    esac
  done <<< "$raw"
  return 1
}

append_output() {
  local chunk="$1"
  [[ -z "$chunk" ]] && return 0
  printf '%s' "$chunk"
  [[ "$chunk" != *$'\n' ]] && printf '\n'
}

contains_marker() {
  local text="$1"
  shift
  local marker=""
  for marker in "$@"; do
    [[ -n "$marker" && "$text" == *"$marker"* ]] && return 0
  done
  return 1
}

uart_probe_succeeded() {
  local output="$1"
  local probe_cmd="${2:-}"
  local probe_token="${3:-}"
  local probe_cmd_norm=""
  local output_lc=""

  [[ -z "$output" ]] && return 1
  [[ -n "$probe_token" && "$output" == *"$probe_token"* ]] && return 0
  if grep -Eq '(^|[\r\n])(\$ |# )' <<< "$output"; then
    return 0
  fi

  probe_cmd_norm="$(printf '%s' "$probe_cmd" | tr '[:upper:]' '[:lower:]' | trim_line)"
  output_lc="$(printf '%s' "$output" | tr '[:upper:]' '[:lower:]')"
  if [[ "$probe_cmd_norm" == "monkey -g" ]] \
    && grep -Eq 'monkey[[:space:]]+status|monkey[[:space:]]*=|status[[:space:]]*=|monkey[[:space:]]+is[[:space:]]+(on|off)' <<< "$output_lc"; then
    return 0
  fi
  return 1
}

run_mgr_serial() {
  "$SERIAL_SH" --role MGR "$@"
}

run_uart_serial() {
  "$SERIAL_SH" --role UART "$@"
}

run_uart_listen() {
  "$SERIAL_SH" --role UART "$@" --listen
}

detect_python_bin() {
  if [[ -n "${WLCTL_PYTHON_BIN:-}" ]]; then
    echo "$WLCTL_PYTHON_BIN"
    return 0
  fi
  if command -v python3 >/dev/null 2>&1; then
    echo "python3"
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    echo "python"
    return 0
  fi
  echo "[ERR] python3/python not found; wlctl diagnose requires Python." >&2
  return 1
}

emit_cli_json() {
  local python_bin="${WLCTL_JSON_PYTHON_BIN:-}"
  if [[ -z "$python_bin" ]]; then
    python_bin="$(detect_python_bin)" || return 6
  fi
  "$python_bin" - <<'PY'
import json
import os

payload = {
    "command": os.environ["WLCTL_JSON_COMMAND"],
    "status": os.environ.get("WLCTL_JSON_STATUS", "ok"),
}
optional = {
    "action": os.environ.get("WLCTL_JSON_ACTION", ""),
    "port": os.environ.get("WLCTL_JSON_PORT", ""),
    "state": os.environ.get("WLCTL_JSON_STATE", ""),
    "probe_command": os.environ.get("WLCTL_JSON_PROBE_COMMAND", ""),
    "output": os.environ.get("WLCTL_JSON_OUTPUT", ""),
}
for key, value in optional.items():
    if value:
        payload[key] = value
if os.environ.get("WLCTL_JSON_TIMEOUT"):
    payload["timeout"] = int(os.environ["WLCTL_JSON_TIMEOUT"])
print(json.dumps(payload, ensure_ascii=False))
PY
}

run_disk_mode() {
  local mode="$1"
  shift
  local port=""
  local wait_seconds="25"
  local cmd=()

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --port) port="$2"; shift 2 ;;
      --wait-seconds) wait_seconds="$2"; shift 2 ;;
      *) echo "[ERR] Unknown arg: $1" >&2; usage; return 1 ;;
    esac
  done

  if [[ -n "$DISK_BRIDGE_SH" ]]; then
    exec "$DISK_BRIDGE_SH" "$mode" "$port" "$wait_seconds"
  fi

  if [[ ! -f "$DISK_PS1" ]]; then
    echo "[ERR] disk_mode_auto.ps1 not found: $DISK_PS1" >&2
    return 6
  fi

  if [[ -x /mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe ]] && command -v wslpath >/dev/null 2>&1; then
    local ps1_win
    ps1_win="$(wslpath -w "$DISK_PS1")"
    cmd=(/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$ps1_win" -Mode "$mode" -WaitSeconds "$wait_seconds")
    [[ -n "$port" ]] && cmd+=(-MgrPort "$port")
    if [[ "$mode" == "unmount" ]]; then
      run_disk_unmount_with_cleanup "${cmd[@]}"
      return $?
    fi
    exec "${cmd[@]}"
  fi

  if command -v powershell >/dev/null 2>&1; then
    cmd=(powershell -NoProfile -ExecutionPolicy Bypass -File "$DISK_PS1" -Mode "$mode" -WaitSeconds "$wait_seconds")
    [[ -n "$port" ]] && cmd+=(-MgrPort "$port")
    if [[ "$mode" == "unmount" ]]; then
      run_disk_unmount_with_cleanup "${cmd[@]}"
      return $?
    fi
    exec "${cmd[@]}"
  fi

  if command -v pwsh >/dev/null 2>&1; then
    cmd=(pwsh -NoProfile -ExecutionPolicy Bypass -File "$DISK_PS1" -Mode "$mode" -WaitSeconds "$wait_seconds")
    [[ -n "$port" ]] && cmd+=(-MgrPort "$port")
    if [[ "$mode" == "unmount" ]]; then
      run_disk_unmount_with_cleanup "${cmd[@]}"
      return $?
    fi
    exec "${cmd[@]}"
  fi

  echo "[ERR] No PowerShell runtime available for wlctl disk." >&2
  return 6
}

cleanup_wsl_mount_for_root() {
  local root="$1"
  local mount_point=""
  local helper="${WLCTL_MOUNT_HELPER:-/usr/local/sbin/watchlink-v3-mount-helper}"

  [[ "$root" =~ ^([A-Za-z]):[\\/]*$ ]] || return 0
  mount_point="/mnt/${BASH_REMATCH[1],,}"

  if [[ -x "$helper" ]]; then
    sudo -n "$helper" cleanup-drvfs-mount --mount-point "$mount_point" >/dev/null 2>&1 || true
    return 0
  fi

  sudo -n umount -l "$mount_point" >/dev/null 2>&1 || true
  sudo -n rm -rf "$mount_point" >/dev/null 2>&1 || true
}

run_disk_unmount_with_cleanup() {
  local output=""
  local status=0
  local line=""
  local cleanup_roots=()

  output="$(WLCTL_UNMOUNT_EMIT_CLEANUP=1 "$@" 2>&1)" || status=$?

  while IFS= read -r line; do
    if [[ "$line" == "[WLCTL_CLEANUP_ROOT] "* ]]; then
      cleanup_roots+=("${line#\[WLCTL_CLEANUP_ROOT\] }")
      continue
    fi
    printf '%s\n' "$line"
  done <<< "$output"

  for line in "${cleanup_roots[@]}"; do
    cleanup_wsl_mount_for_root "$line"
  done

  return $status
}

query_vbus_state() {
  local port_args=("$@")
  local raw=""
  raw="$(run_mgr_serial "${port_args[@]}" --cmd "WL+VBUS=?" --read-ms 1500)"
  normalize_vbus_state "$raw"
}

set_vbus_state() {
  local target="$1"
  shift
  local port_args=("$@")
  local desired_cmd="WL+VBUS=ON"
  local expected_state="on"
  local current=""

  if [[ "$target" == "off" ]]; then
    desired_cmd="WL+VBUS=OFF"
    expected_state="off"
  fi

  current="$(query_vbus_state "${port_args[@]}" || true)"
  if [[ "$current" == "$expected_state" ]]; then
    echo "$expected_state"
    return 0
  fi

  for attempt in 1 2 3; do
    run_mgr_serial "${port_args[@]}" --cmd "$desired_cmd" --read-ms 1500 >/dev/null
    current="$(query_vbus_state "${port_args[@]}" || true)"
    if [[ "$current" == "$expected_state" ]]; then
      echo "$expected_state"
      return 0
    fi
    [[ "$attempt" -lt 3 ]] && sleep 1
  done

  echo "[ERR] Failed to switch VBUS to $expected_state." >&2
  return 5
}

wait_for_device_ready() {
  local port="$1"
  local timeout="$2"
  local probe_cmd="$3"
  local probe_token="$4"
  local listen_chunk_sec="$5"
  shift 5
  local markers=("$@")
  local port_args=()
  local all_output=""
  local deadline=0
  local remaining=0
  local seconds=0
  local probe_index=0
  local probe_read_ms=0
  local probe=""
  local chunk=""

  [[ -n "$port" ]] && port_args+=(--port "$port")

  deadline=$(( $(date +%s) + timeout ))
  while (( $(date +%s) < deadline )); do
    remaining=$(( deadline - $(date +%s) ))
    (( remaining < 1 )) && remaining=1
    seconds=$listen_chunk_sec
    (( seconds > remaining )) && seconds=$remaining
    (( seconds < 1 )) && seconds=1

    probe_index=$((probe_index + 1))
    probe_read_ms=$(( seconds * 900 ))
    (( probe_read_ms < 1800 )) && probe_read_ms=1800
    (( probe_read_ms > 6000 )) && probe_read_ms=6000

    if probe="$(run_uart_serial "${port_args[@]}" --cmd "$probe_cmd" --read-ms "$probe_read_ms" 2>&1)"; then
      all_output+="$probe"$'\n'
      append_output "$probe"
      if uart_probe_succeeded "$probe" "$probe_cmd" "$probe_token"; then
        echo "[INFO] UART shell probe #$probe_index succeeded"
        return 0
      fi
    else
      all_output+="$probe"$'\n'
      echo "[WARN] UART shell probe #$probe_index failed: $probe"
    fi

    if chunk="$(run_uart_listen "${port_args[@]}" --seconds "$seconds" 2>&1)"; then
      all_output+="$chunk"$'\n'
      append_output "$chunk"
      if contains_marker "$chunk" "${markers[@]}"; then
        return 0
      fi
    else
      all_output+="$chunk"$'\n'
      echo "[WARN] UART listen failed while waiting for ready screen logs: $chunk"
    fi
  done

  if [[ -n "$all_output" ]]; then
    echo "Recent UART output:" >&2
    printf '%s\n' "${all_output: -4000}" >&2
  fi
  echo "[ERR] Device did not reach launcher-ready or shell-ready state within timeout. markers=${markers[*]}, probe=$probe_cmd." >&2
  return 7
}

prepare_uart_shell_for_long_log() {
  local port="$1"
  local timeout="$2"
  local keep_awake_cmd="$3"
  local probe_cmd="$4"
  local probe_token="$5"
  local port_args=()
  local all_output=""
  local keep_awake_read_ms=1200
  local probe_read_ms=2500
  local deadline=0
  local remaining=0
  local seconds=0
  local probe_index=0
  local chunk=""
  local probe=""
  local keep_awake_output=""

  [[ -n "$port" ]] && port_args+=(--port "$port")

  if [[ -n "$keep_awake_cmd" ]]; then
    echo "===== UART $keep_awake_cmd ====="
    if keep_awake_output="$(run_uart_serial "${port_args[@]}" --cmd "$keep_awake_cmd" --read-ms "$keep_awake_read_ms" 2>&1)"; then
      all_output+="$keep_awake_output"$'\n'
      append_output "$keep_awake_output"
      if uart_probe_succeeded "$keep_awake_output" "$probe_cmd" "$probe_token"; then
        echo "[INFO] UART keep-awake command returned shell prompt"
        return 0
      fi
    else
      all_output+="$keep_awake_output"$'\n'
      echo "[WARN] $keep_awake_cmd failed: $keep_awake_output"
    fi
  fi

  deadline=$(( $(date +%s) + timeout ))
  while (( $(date +%s) < deadline )); do
    probe_index=$((probe_index + 1))
    echo "===== UART shell probe #$probe_index ====="
    if probe="$(run_uart_serial "${port_args[@]}" --cmd "$probe_cmd" --read-ms "$probe_read_ms" 2>&1)"; then
      all_output+="$probe"$'\n'
      append_output "$probe"
      if uart_probe_succeeded "$probe" "$probe_cmd" "$probe_token"; then
        echo "[INFO] UART shell probe #$probe_index succeeded"
        return 0
      fi
    else
      all_output+="$probe"$'\n'
      echo "[WARN] UART shell probe #$probe_index failed: $probe"
    fi

    remaining=$(( deadline - $(date +%s) ))
    (( remaining <= 0 )) && break
    seconds=2
    (( seconds > remaining )) && seconds=$remaining
    (( seconds < 1 )) && seconds=1

    if chunk="$(run_uart_listen "${port_args[@]}" --seconds "$seconds" 2>&1)"; then
      all_output+="$chunk"$'\n'
      append_output "$chunk"
    else
      all_output+="$chunk"$'\n'
      echo "[WARN] UART stabilize listen #$probe_index failed: $chunk"
    fi
  done

  if [[ -n "$all_output" ]]; then
    echo "Recent UART output:" >&2
    printf '%s\n' "${all_output: -4000}" >&2
  fi
  echo "[ERR] UART shell did not stabilize after ready. keep_awake_cmd=$keep_awake_cmd, probe=$probe_cmd." >&2
  return 8
}

case "$cmd" in
  serial)
    role_hint=""
    cmd_hint=""
    args=("$@")
    for ((i=0; i<${#args[@]}; i++)); do
      [[ "${args[$i]}" == "--role" && $((i+1)) -lt ${#args[@]} ]] && role_hint="${args[$((i+1))]}"
      [[ "${args[$i]}" == "--cmd" && $((i+1)) -lt ${#args[@]} ]] && cmd_hint="${args[$((i+1))]}"
    done
    [[ -z "$role_hint" ]] && role_hint="MGR"

    if [[ "$cmd_hint" =~ ^[[:space:]]*monkey[[:space:]] && "$role_hint" == "MGR" ]]; then
      echo "[WARN] Detected monkey command on MGR, auto-switch role to UART."
      filtered=()
      skip=0
      for ((i=0; i<${#args[@]}; i++)); do
        if [[ $skip -eq 1 ]]; then skip=0; continue; fi
        if [[ "${args[$i]}" == "--role" ]]; then skip=1; continue; fi
        filtered+=("${args[$i]}")
      done
      exec "$SERIAL_SH" --role UART "${filtered[@]}"
    fi

    if [[ "$cmd_hint" =~ ^[[:space:]]*WL\+ && "$role_hint" == "UART" ]]; then
      echo "[WARN] Detected WL+ command on UART, auto-switch role to MGR."
      filtered=()
      skip=0
      for ((i=0; i<${#args[@]}; i++)); do
        if [[ $skip -eq 1 ]]; then skip=0; continue; fi
        if [[ "${args[$i]}" == "--role" ]]; then skip=1; continue; fi
        filtered+=("${args[$i]}")
      done
      exec "$SERIAL_SH" --role MGR "${filtered[@]}"
    fi

    exec "$SERIAL_SH" "${args[@]}"
    ;;
  listen)
    # sugar: wlctl listen --role UART ... => run_serial_cmd_auto.sh --role UART --listen ...
    exec "$SERIAL_SH" "$@" --listen
    ;;
  monkey)
    [[ $# -lt 1 ]] && { usage; exit 1; }
    action="$1"; shift

    port=""
    interval="1000"
    print_opt="off"
    mem_opt="0"

    while [[ $# -gt 0 ]]; do
      case "$1" in
        --port) port="$2"; shift 2 ;;
        --interval-ms) interval="$2"; shift 2 ;;
        --print) print_opt="$2"; shift 2 ;;
        --mem) mem_opt="$2"; shift 2 ;;
        *) echo "[ERR] Unknown arg: $1" >&2; usage; exit 1 ;;
      esac
    done

    port_args=()
    [[ -n "$port" ]] && port_args+=(--port "$port")

    case "$action" in
      on)
        "$SERIAL_SH" --role UART "${port_args[@]}" --cmd "monkey -i $interval" --read-ms 1500
        "$SERIAL_SH" --role UART "${port_args[@]}" --cmd "monkey -p $print_opt" --read-ms 1500
        "$SERIAL_SH" --role UART "${port_args[@]}" --cmd "monkey -m $mem_opt" --read-ms 1500
        exec "$SERIAL_SH" --role UART "${port_args[@]}" --cmd "monkey -s on" --read-ms 1500
        ;;
      off)
        exec "$SERIAL_SH" --role UART "${port_args[@]}" --cmd "monkey -s off" --read-ms 1500
        ;;
      status)
        exec "$SERIAL_SH" --role UART "${port_args[@]}" --cmd "monkey -g" --read-ms 1500
        ;;
      *)
        echo "[ERR] Unknown monkey action: $action" >&2
        usage
        exit 1
        ;;
    esac
    ;;
  disk)
    [[ $# -lt 1 ]] && { usage; exit 1; }
    action="$1"; shift
    case "$action" in
      mount-data|mount-log|unmount)
        run_disk_mode "$action" "$@"
        ;;
      *)
        echo "[ERR] Unknown disk action: $action" >&2
        usage
        exit 1
        ;;
    esac
    ;;
  fs)
    python_bin="$(detect_python_bin)" || exit 6
    PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}" exec "$python_bin" "$FS_PY" "$@"
    ;;
  vbus)
    [[ $# -lt 1 ]] && { usage; exit 1; }
    action="$1"; shift

    port=""
    output_format="text"
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --port) port="$2"; shift 2 ;;
        --output) output_format="$2"; shift 2 ;;
        *) echo "[ERR] Unknown arg: $1" >&2; usage; exit 1 ;;
      esac
    done

    port_args=()
    [[ -n "$port" ]] && port_args+=(--port "$port")

    case "$action" in
      status)
        state="$(query_vbus_state "${port_args[@]}")" || {
          echo "[ERR] Unable to determine VBUS status." >&2
          exit 4
        }
        if [[ "$output_format" == "json" ]]; then
          WLCTL_JSON_COMMAND="vbus" \
          WLCTL_JSON_ACTION="status" \
          WLCTL_JSON_PORT="$port" \
          WLCTL_JSON_STATE="$state" \
          emit_cli_json
        else
          echo "$state"
        fi
        ;;
      on|off)
        state="$(set_vbus_state "$action" "${port_args[@]}")"
        if [[ "$output_format" == "json" ]]; then
          WLCTL_JSON_COMMAND="vbus" \
          WLCTL_JSON_ACTION="$action" \
          WLCTL_JSON_PORT="$port" \
          WLCTL_JSON_STATE="$state" \
          emit_cli_json
        else
          echo "$state"
        fi
        ;;
      *)
        echo "[ERR] Unknown vbus action: $action" >&2
        usage
        exit 1
        ;;
    esac
    ;;
  ready)
    port=""
    timeout="60"
    probe_cmd="monkey -g"
    probe_token="monkey status"
    output_format="text"
    ready_markers=(
      "gotoScreen:LauncherScreen"
      "gotoScreen:ChargeScreen"
      "goto screen LauncherScreen"
      "_setCurrentScreenInterval LauncherScreen"
    )

    while [[ $# -gt 0 ]]; do
      case "$1" in
        --port) port="$2"; shift 2 ;;
        --timeout) timeout="$2"; shift 2 ;;
        --probe-cmd) probe_cmd="$2"; shift 2 ;;
        --output) output_format="$2"; shift 2 ;;
        *) echo "[ERR] Unknown arg: $1" >&2; usage; exit 1 ;;
      esac
    done

    if [[ "$output_format" == "json" ]]; then
      if command_output="$(wait_for_device_ready "$port" "$timeout" "$probe_cmd" "$probe_token" 5 "${ready_markers[@]}" 2>&1)"; then
        WLCTL_JSON_COMMAND="ready" \
        WLCTL_JSON_STATUS="ok" \
        WLCTL_JSON_PORT="$port" \
        WLCTL_JSON_TIMEOUT="$timeout" \
        WLCTL_JSON_PROBE_COMMAND="$probe_cmd" \
        WLCTL_JSON_OUTPUT="$command_output" \
        emit_cli_json
      else
        rc=$?
        WLCTL_JSON_COMMAND="ready" \
        WLCTL_JSON_STATUS="error" \
        WLCTL_JSON_PORT="$port" \
        WLCTL_JSON_TIMEOUT="$timeout" \
        WLCTL_JSON_PROBE_COMMAND="$probe_cmd" \
        WLCTL_JSON_OUTPUT="$command_output" \
        emit_cli_json
        exit "$rc"
      fi
    else
      wait_for_device_ready "$port" "$timeout" "$probe_cmd" "$probe_token" 5 "${ready_markers[@]}"
    fi
    ;;
  awake)
    port=""
    timeout="15"
    keep_awake_cmd="power -s on"
    probe_cmd="monkey -g"
    probe_token="monkey status"
    output_format="text"

    while [[ $# -gt 0 ]]; do
      case "$1" in
        --port) port="$2"; shift 2 ;;
        --timeout) timeout="$2"; shift 2 ;;
        --output) output_format="$2"; shift 2 ;;
        *) echo "[ERR] Unknown arg: $1" >&2; usage; exit 1 ;;
      esac
    done

    if [[ "$output_format" == "json" ]]; then
      if command_output="$(prepare_uart_shell_for_long_log "$port" "$timeout" "$keep_awake_cmd" "$probe_cmd" "$probe_token" 2>&1)"; then
        WLCTL_JSON_COMMAND="awake" \
        WLCTL_JSON_STATUS="ok" \
        WLCTL_JSON_PORT="$port" \
        WLCTL_JSON_TIMEOUT="$timeout" \
        WLCTL_JSON_PROBE_COMMAND="$probe_cmd" \
        WLCTL_JSON_OUTPUT="$command_output" \
        emit_cli_json
      else
        rc=$?
        WLCTL_JSON_COMMAND="awake" \
        WLCTL_JSON_STATUS="error" \
        WLCTL_JSON_PORT="$port" \
        WLCTL_JSON_TIMEOUT="$timeout" \
        WLCTL_JSON_PROBE_COMMAND="$probe_cmd" \
        WLCTL_JSON_OUTPUT="$command_output" \
        emit_cli_json
        exit "$rc"
      fi
    else
      prepare_uart_shell_for_long_log "$port" "$timeout" "$keep_awake_cmd" "$probe_cmd" "$probe_token"
    fi
    ;;
  diagnose)
    python_bin="$(detect_python_bin)" || exit 6
    PYTHONPATH="$ROOT_DIR/common${PYTHONPATH:+:$PYTHONPATH}" exec "$python_bin" "$DIAGNOSE_PY" "$@"
    ;;
  *)
    echo "[ERR] Unknown command: $cmd" >&2
    usage
    exit 1
    ;;
esac
