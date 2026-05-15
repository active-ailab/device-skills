---
name: watchlink-v3
description: Watch-Link V3 设备管理：串口(UART/MGR)发命令、monkey 测试自动化、固件烧录(v3dl app/ota)、磁盘挂载(mount data/log)、读取设备文件、读取指定路径、写文件到设备、pull/push device files、删除设备日志/文件、VBUS 电源控制、设备就绪检测与复位、端口自动发现、健康检查与运行时诊断。Windows/WSL/Linux。触发词: 串口连不上, 发串口命令, monkey 启动停止, 刷机, OTA 升级, 挂盘, 取日志, 读取设备文件, 读取指定路径, 写文件到设备, push CSV 到 data_sample, pull device logs, 删除设备日志, VBUS 没电, 设备不响应, 复位, COM 口找不到, 健康检查, quick check, WL+DISK, WL+RESET.
---

# Device WatchLink V3

Use this skill as the base capability layer for Watch-Link V3 devices. Prefer the provided entrypoint scripts instead of re-implementing port detection, UART/MGR routing, or monkey command handling.

## First-Pass Checklist

1. In WSL, run bridge preflight first: `powershell.exe -NoProfile -Command "Write-Output ok"` and `cmd.exe /c echo ok`.
2. Route by content: `WL+*` to `MGR`, shell and `monkey*` to `UART`.
3. If a higher-level runtime still fails after bridge and channel checks, inspect its runtime hints before re-deriving port, reset, or shell-ready logic by hand.

## Entrypoints

Use these scripts as the primary interface:

- Linux/WSL: `skills/device/watchlink-v3/scripts/linux/wlctl.sh`
- Windows: `skills/device/watchlink-v3/scripts/windows/wlctl.ps1`

Repository layout:

- `scripts/common/`: shared Python SDK, discovery, diagnose, disk helpers
- `scripts/linux/`: Linux/WSL shell entrypoints
- `scripts/windows/`: Windows PowerShell entrypoints
- `scripts/` root keeps only Python import shims for `watchlink_discovery.py`, `wlctl_sdk.py`, `disk_utils.py`, `diagnose_utils.py`

## Team Setup

For WSL teammates who need stable `wlctl fs ...` mounts and passwordless `v3dl com` discovery, use the repo-local installer instead of hand-editing sudoers:

```bash
./skills/device/watchlink-v3/scripts/linux/install_watchlink_v3_sudo.sh
```

This installs:

- `/usr/local/sbin/watchlink-v3-mount-helper`: a constrained root helper that only prepares `/mnt/<drive>` drvfs mounts for watchlink-v3
- `/etc/sudoers.d/99-watchlink-v3-v3dl-com`: a generated sudoers rule for the current user

Variants:

```bash
# install only the watchlink-v3 mount helper rule, skip v3dl compatibility
./skills/device/watchlink-v3/scripts/linux/install_watchlink_v3_sudo.sh --watchlink-only

# install for another teammate account
./skills/device/watchlink-v3/scripts/linux/install_watchlink_v3_sudo.sh --user alice
```

The generated rule is based on:

- `scripts/linux/watchlink-v3-mount-helper`
- `scripts/linux/watchlink-v3-mount-helper.sudoers.template`

Why this exists:

- `watchlink-v3` only needs a tiny audited root surface for `/mnt/<drive>` drvfs preparation
- `v3dl com` still enters `ensure_sudo_ready()` even though the `com` path itself does not need root, so the installer can optionally add the smallest compatibility whitelist without modifying `v3dl`

Python callers should use the co-located SDK through discovery first:

```python
from watchlink_discovery import ensure_wlctl_sdk

ensure_wlctl_sdk()

from wlctl_sdk import WatchlinkDevice

dev = WatchlinkDevice(mgr_port="COM37", uart_port="COM38")
dev.ensure_vbus_on()
dev.wait_ready(timeout=60)
dev.send_uart_cmd("monkey -g")
```

Expose these operations to higher-level skills:

1. Serial command: `wlctl serial ...`
2. Serial listen: `wlctl listen ...`
3. Monkey control: `wlctl monkey on|off|status`
4. Disk mode control: `wlctl disk mount-data|mount-log|unmount`
5. Ready check: `wlctl ready [--port PORT] [--timeout N]`
6. Shell stabilize: `wlctl awake [--port PORT] [--timeout N]`
7. Runtime diagnose: `wlctl diagnose [--message TEXT] [--output json|text]`
8. Machine-readable status: `wlctl vbus|ready|awake ... --output json` / `-Output json`
9. Port selection: omit the port by default and let the script auto-detect; pass `--port` only when needed
10. Device filesystem primitives: `wlctl fs ls|read|pull|push|rm`

## Guardrails

Follow these rules strictly:

- Route commands by content before executing them:
  - `monkey*` and watch shell commands go to `UART`
  - `WL+*` board-management commands go to `MGR`
- Choose firmware download mode before executing it:
  - requests like `fw_only`, `app only`, or firmware-only go to `v3dl app`
  - requests like `ota_sign`, full OTA, or full package go to `v3dl ota`
- If the user does not provide `<PROJECT_ROOT>`, ask for it instead of guessing.
- If the port is omitted on Windows, prefer Windows port entries from `Get-PnpDevice -Class Ports` or, when that is permission-limited, `pnputil /enum-devices /class Ports`, using only `OK`/`Started` `WLINK <id> MGR/UART (COMx)` pairs. If exactly one online WLINK device pair exists, use that pair automatically. Otherwise fall back to existing auto-detection and require an explicit port when multiple matches remain.
- Require an extra confirmation before risky operations such as `fm_ota`, formatting, or `WRITE_OTP`.

## Keyword Mapping

Map user intent like this:

- Start monkey: `wlctl monkey on`
- Stop monkey: `wlctl monkey off`
- Check monkey status: `wlctl monkey status`
- Wait for device ready: `wlctl ready`
- Re-establish shell after reboot: `wlctl awake`
- Diagnose bridge/serial/ready failures: `wlctl diagnose`
- Send an MGR command: `wlctl serial --role MGR --cmd "XXX"`
- Send a UART command: `wlctl serial --role UART --cmd "XXX"`
- List device paths on DATA/LOG disk: `wlctl fs ls --disk data|log --path ...`
- Read a device file: `wlctl fs read --disk data|log --path ...`
- Pull device logs/files to local dir: `wlctl fs pull --disk log --from-path ... --to-path ...`
- Push local files to device path: `wlctl fs push --disk data --from-path ... --to-path ...`
- Remove device files or directories: `wlctl fs rm --disk data|log --path ...`
- Flash firmware-only package: `v3dl app -f ...fw_only_sign.zip`
- Flash OTA package: `v3dl ota -f ...ota_sign.zip`

## Execution Checks

Before running a command, verify:

1. The channel is correct: `UART` or `MGR`
2. The firmware mode is correct: `app` or `ota`
3. `<PROJECT_ROOT>` exists when flashing is requested
4. The port strategy is resolved: auto-detect, user-chosen, or explicit `--port`
5. Extra confirmation has been collected for risky operations

## Edge Cases

Handle these cases carefully:

- If a serial command is sent to the wrong channel, warn and auto-correct when safe.
- If a filename indicates `fw_only` but the requested command is OTA, trust the filename and explain the correction.
- If no device is found, distinguish that from permission failures.
- If multiple ports are present, prompt for a selection in interactive PowerShell flows when possible; otherwise require explicit `--port`.
- If paths are not visible across environments, validate them before running commands.
- If the device returns little or noisy output, rely on key lines such as `monkey status on` or `monkey status off`.
- If monkey start appears ineffective, try status checks and verify whether the firmware expects split monkey commands.
- In WSL, treat `powershell.exe` / `cmd.exe` timeout or `UNC paths are not supported` as a bridge-layer failure first, not as a port-pair or device-state failure.
- If a higher-level runtime emits `[HINT][E_*]` diagnostics, prefer those codes over re-deriving algorithm-specific reset, shell-ready, or log-disk recovery logic inside this skill.

## Quick Validation

Use these helper scripts for a one-command sanity check:

- Windows: `scripts/windows/quick_check.ps1`
- Linux/WSL: `scripts/linux/quick_check.sh`

Default behavior is non-invasive:

1. detect or validate UART and MGR ports
2. query `WL+FWVER=?` on MGR
3. query `monkey status` on UART

Enable monkey control smoke testing only when you want to verify on/off behavior. The script restores the original monkey state after the test.

Windows note: quick_check.ps1 first tries to auto-pair the single online WLINK <id> MGR/UART device from Get-PnpDevice -Class Ports or pnputil /enum-devices /class Ports, so changing USB ports usually does not require updating the command. If automatic discovery still leaves multiple port candidates, an interactive PowerShell session will prompt you to choose the port.

WSL note: quick_check.sh follows the wlctl.sh + Windows resolver path and no longer depends on `v3dl com`. If WSL cannot uniquely see the device after a port change, pass --uart-port and --mgr-port explicitly.

WSL note: if bridge preflight hangs or returns the UNC warning, stop treating it as a device-discovery problem. Prefer a stable `/mnt/c/...` cwd for Windows interop, and let higher-level runtimes own any extra reset or shell-ready logic.

Examples:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\skills\device\watchlink-v3\scripts\windows\quick_check.ps1 -UartPort COM15 -MgrPort COM14
powershell -NoProfile -ExecutionPolicy Bypass -File .\skills\device\watchlink-v3\scripts\windows\quick_check.ps1 -UartPort COM15 -MgrPort COM14 -SmokeMonkey
```

```bash
./skills/device/watchlink-v3/scripts/linux/quick_check.sh --uart-port COM15 --mgr-port COM14
./skills/device/watchlink-v3/scripts/linux/quick_check.sh --uart-port COM15 --mgr-port COM14 --smoke-monkey
```

## Quick Use

### Linux/WSL

```bash
# MGR
./skills/device/watchlink-v3/scripts/linux/wlctl.sh serial --role MGR --cmd "WL+FWVER=?"

# UART
./skills/device/watchlink-v3/scripts/linux/wlctl.sh serial --role UART --cmd "monkey -g"

# monkey
./skills/device/watchlink-v3/scripts/linux/wlctl.sh monkey on --interval-ms 1000 --print off --mem 0
./skills/device/watchlink-v3/scripts/linux/wlctl.sh monkey status
./skills/device/watchlink-v3/scripts/linux/wlctl.sh monkey off
./skills/device/watchlink-v3/scripts/linux/wlctl.sh vbus status --output json
./skills/device/watchlink-v3/scripts/linux/wlctl.sh ready --timeout 60 --output json
./skills/device/watchlink-v3/scripts/linux/wlctl.sh fs ls --disk data --path data_sample --output json
./skills/device/watchlink-v3/scripts/linux/wlctl.sh fs pull --disk log --from-path "**/*.log" --to-path ./img_logs
```

### Windows

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\skills\device\watchlink-v3\scripts\windows\wlctl.ps1 serial -Role MGR -Command "WL+FWVER=?"
powershell -NoProfile -ExecutionPolicy Bypass -File .\skills\device\watchlink-v3\scripts\windows\wlctl.ps1 serial -Role UART -Command "monkey -g"
powershell -NoProfile -ExecutionPolicy Bypass -File .\skills\device\watchlink-v3\scripts\windows\wlctl.ps1 monkey on -IntervalMs 1000 -Print off -Mem 0
powershell -NoProfile -ExecutionPolicy Bypass -File .\skills\device\watchlink-v3\scripts\windows\wlctl.ps1 monkey status
powershell -NoProfile -ExecutionPolicy Bypass -File .\\skills\\device\\watchlink-v3\\scripts\\windows\\wlctl.ps1 monkey off
powershell -NoProfile -ExecutionPolicy Bypass -File .\\skills\\device\\watchlink-v3\\scripts\\windows\\wlctl.ps1 disk mount-data
powershell -NoProfile -ExecutionPolicy Bypass -File .\\skills\\device\\watchlink-v3\\scripts\\windows\\wlctl.ps1 disk mount-log
powershell -NoProfile -ExecutionPolicy Bypass -File .\\skills\\device\\watchlink-v3\\scripts\\windows\\wlctl.ps1 disk unmount
powershell -NoProfile -ExecutionPolicy Bypass -File .\\skills\\device\\watchlink-v3\\scripts\\windows\\wlctl.ps1 ready -Timeout 60
powershell -NoProfile -ExecutionPolicy Bypass -File .\\skills\\device\\watchlink-v3\\scripts\\windows\\wlctl.ps1 awake -Timeout 15
powershell -NoProfile -ExecutionPolicy Bypass -File .\\skills\\device\\watchlink-v3\\scripts\\windows\\wlctl.ps1 diagnose -Message "cmd.exe timed out" -Output json
powershell -NoProfile -ExecutionPolicy Bypass -File .\\skills\\device\\watchlink-v3\\scripts\\windows\\wlctl.ps1 vbus status -Output json
powershell -NoProfile -ExecutionPolicy Bypass -File .\\skills\\device\\watchlink-v3\\scripts\\windows\\wlctl.ps1 fs ls -Disk data -FsPath data_sample -Output json
powershell -NoProfile -ExecutionPolicy Bypass -File .\\skills\\device\\watchlink-v3\\scripts\\windows\\wlctl.ps1 fs pull -Disk log -FromPath "**/*.log" -ToPath .\\img_logs
```

## Port Rules

- Use `UART` for watch shell commands such as `monkey`.
- Use `MGR` for board-management commands such as `WL+FWVER=?`.
- Prefer auto-detection. On Windows, first look for a single online `WLINK <id>` pair from `Get-PnpDevice -Class Ports`; otherwise continue with generic discovery:
  1. zero matching ports: stop and report no device found
  2. one matching port: auto-select it
  3. multiple matching ports: prompt in interactive mode, require `--port` otherwise
- Prefer the started WLINK pair resolver (`Get-PnpDevice` / `pnputil` → `WLINK <id> MGR/UART (COMx)`). Do not route WSL troubleshooting back to `v3dl com` as the primary discovery path.

## MGR Command Examples

- `WL+FWVER=?`
- `WL+HWVER=?`
- `WL+FWCODE=?`
- `WL+RESET`
- `WL+DISK=WL|IMG|FS|NULL`
- `WL+FSTYPE=0|1|2`
- `WL+VBUS=ON|OFF|TEMP|?`

## Monkey Notes

Use monkey commands through `UART`.

Common commands:

```bash
monkey -g
monkey -i 1000
monkey -p off
monkey -m 0
monkey -s on
monkey -s off
monkey -f SportRecordListScreen
```

Important behavior:

- Some firmware builds do not accept a single combined command like `monkey -s on -i 1000 -p off -m 0`.
- Prefer split commands when configuring monkey parameters:
  1. `monkey -i <interval>`
  2. `monkey -p <on|off>`
  3. `monkey -m <0|6>`
  4. `monkey -s on`
- Use `monkey -g` to verify the state.
- `monkey -s off` may clear queued events.
- Long delays may require `power -s on` to keep the screen awake.

## Firmware Download and Flashing

Assume this common project layout when provided by the user:

- project root: `<PROJECT_ROOT>`
- firmware directory: `<PROJECT_ROOT>/build/out/watch@mhs003/binary`
- common packages:
  - `watch@mhs003_fw_only_sign.zip`
  - `watch@mhs003_ota_sign.zip`

Choose commands like this:

```bash
# firmware only
v3dl app -f <PROJECT_ROOT>/build/out/watch@mhs003/binary/watch@mhs003_fw_only_sign.zip [PID=xxxx]

# ota package
v3dl ota -f <PROJECT_ROOT>/build/out/watch@mhs003/binary/watch@mhs003_ota_sign.zip [PID=xxxx]
```

## Windows and WSL Notes

- Prefer the script entrypoints instead of manual serial handling.
- In WSL, you may bridge to Windows serial tooling first and fall back to Linux tty devices when needed.
- When calling Windows tools from WSL, prefer a stable Windows-mounted cwd such as `/mnt/c/Windows/System32`; avoid launching them from a Linux repo cwd that can be surfaced to Windows as `\\wsl$\...`.
- Windows auto-detection is stronger than WSL auto-detection: on Windows, prefer the single online WLINK <id> MGR/UART pair from Get-PnpDevice -Class Ports or pnputil; if multiple candidates remain in an interactive PowerShell session, prompt the user to choose. In WSL, use the bridge path or pass explicit ports when discovery is ambiguous.
- If WSL direct serial access is needed, validate `/dev/ttyACM*` or `/dev/ttyUSB*` explicitly.
- If you hit missing-port issues like `UtilBindVsockAnyPort` or `/dev/ttySxx` problems, switch to the Windows-side workflow.

## Optional WSL Direct Attach

Run from elevated PowerShell when binding USB devices into WSL:

```powershell
usbipd list
usbipd bind --busid <BUSID>
usbipd attach --wsl --busid <BUSID>
```

Verify in WSL:

```bash
ls -l /dev/ttyACM* /dev/ttyUSB* 2>/dev/null
```

Detach when finished:

```powershell
usbipd detach --busid <BUSID>
```

## Path Rules

- Do not hardcode project names, drive letters, or UNC paths into reusable commands.
- Prefer relative paths where possible so the skill can be moved and reused.
