from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import warnings
from pathlib import Path
from typing import Dict, Optional, Protocol, Sequence, Tuple

from common.diagnose_utils import append_runtime_hint, diagnose_runtime
from common.disk_utils import _find_available_data_drives, select_data_root, wait_for_disk_root
from common.watchlink_discovery import build_wlctl_path


IS_WIN32 = sys.platform == "win32"

# Default timeout for run_cmd() when the caller does not know how long is reasonable.
# Prefer passing an explicit timeout based on the expected operation duration.
_DEFAULT_CMD_TIMEOUT_SEC = 30.0

_WSL_BRIDGE_CACHE_TTL_SEC = 5.0
_WSL_WINDOWS_INTEROP_MAX_ATTEMPTS = 3
_WSL_WINDOWS_INTEROP_RETRY_DELAY_SEC = 1.0
_wsl_bridge_last_check_ts = 0.0
_wsl_bridge_last_error = ""


def is_wsl_environment() -> bool:
    if sys.platform != "linux":
        return False
    try:
        version_text = Path("/proc/version").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "microsoft" in version_text.lower() or "wsl" in version_text.lower()


def _is_windows_interop_command(cmd: Sequence[str]) -> bool:
    if not cmd:
        return False
    first = str(cmd[0]).strip().lower().replace("\\", "/")
    return (
        first.endswith("/cmd.exe")
        or first.endswith("/powershell.exe")
        or first == "cmd.exe"
        or first == "powershell.exe"
    )


def _get_wsl_windows_interop_cwd(preferred_cwd: Optional[Path]) -> Optional[Path]:
    if not is_wsl_environment():
        return preferred_cwd

    if preferred_cwd:
        expanded = preferred_cwd.expanduser()
        try:
            resolved = expanded.resolve()
        except Exception:
            resolved = expanded
        resolved_text = str(resolved)
        if resolved_text.startswith("/mnt/") and resolved.exists():
            return resolved

    for candidate in (Path("/mnt/c/Windows/System32"), Path("/mnt/c/Windows"), Path("/mnt/c")):
        if candidate.exists():
            return candidate
    return preferred_cwd


def run_cmd(
    cmd: Sequence[str],
    timeout: Optional[float] = None,
    cwd: Optional[Path] = None,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Run a subprocess command with WSL interop retries.

    timeout: seconds before killing the process.  Prefer passing
    _DEFAULT_CMD_TIMEOUT_SEC (30 s) or an operation-specific value.
    None means wait forever — only use for interactive / listen commands.
    """
    argv = list(cmd)
    is_windows_interop = is_wsl_environment() and _is_windows_interop_command(argv)
    effective_cwd = _get_wsl_windows_interop_cwd(cwd) if is_windows_interop else cwd

    max_attempts = _WSL_WINDOWS_INTEROP_MAX_ATTEMPTS if is_windows_interop else 1
    proc: Optional[subprocess.CompletedProcess] = None
    for attempt in range(1, max_attempts + 1):
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                cwd=str(effective_cwd) if effective_cwd else None,
            )
            break
        except subprocess.TimeoutExpired as exc:
            if is_windows_interop and attempt < max_attempts:
                time.sleep(_WSL_WINDOWS_INTEROP_RETRY_DELAY_SEC * attempt)
                continue
            if is_windows_interop:
                raise RuntimeError(
                    "WSL Windows interop is unavailable: "
                    f"{argv[0]} timed out after {timeout or 0:.0f}s "
                    f"(attempts={attempt}/{max_attempts}). "
                    "Run `wsl --shutdown` in a Windows terminal, reopen WSL, then verify "
                    "`cmd.exe /c echo ok` and `powershell.exe -NoProfile -Command \"Write-Output ok\"`."
                ) from exc
            raise

    if proc is None:
        raise RuntimeError(f"Command did not produce a result: {' '.join(argv)}")
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(argv)}\n"
            f"exit={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return proc


def ensure_wsl_windows_bridge(timeout_sec: float = 10.0, force: bool = False) -> None:
    global _wsl_bridge_last_check_ts, _wsl_bridge_last_error

    if not is_wsl_environment():
        return

    now = time.time()
    if not force and (now - _wsl_bridge_last_check_ts) < _WSL_BRIDGE_CACHE_TTL_SEC:
        if _wsl_bridge_last_error:
            raise RuntimeError(_wsl_bridge_last_error)
        return

    error_message = ""
    try:
        proc = run_cmd(
            ["powershell.exe", "-NoProfile", "-Command", "Write-Output ok"],
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired:
        error_message = (
            "WSL Windows interop is unavailable: "
            f"powershell.exe timed out after {timeout_sec:.0f}s. "
            "Run `wsl --shutdown` in a Windows terminal, reopen WSL, then verify "
            "`powershell.exe -NoProfile -Command \"Write-Output ok\"` returns immediately."
        )
    except Exception as exc:
        error_message = (
            "WSL Windows interop is unavailable: "
            f"failed to launch powershell.exe: {exc}. "
            "Run `wsl --shutdown` in a Windows terminal and reopen WSL before retrying."
        )
    else:
        output = ((proc.stdout or "") + (proc.stderr or "")).strip().lower()
        if proc.returncode != 0 or "ok" not in output:
            error_message = (
                "WSL Windows interop is unavailable: "
                f"powershell.exe returned exit={proc.returncode}, output={output or '<empty>'}. "
                "Run `wsl --shutdown` in a Windows terminal, reopen WSL, then retry."
            )

    _wsl_bridge_last_check_ts = now
    _wsl_bridge_last_error = error_message
    if error_message:
        raise RuntimeError(error_message)


def run_powershell(command: str, check: bool = True, timeout: Optional[float] = 12.0) -> str:
    ensure_wsl_windows_bridge()
    proc = run_cmd(
        ["powershell.exe", "-NoProfile", "-Command", command],
        timeout=timeout,
        check=check,
    )
    return (proc.stdout or "") + (proc.stderr or "")


def to_windows_path(path: Path) -> str:
    if IS_WIN32:
        return str(path)
    proc = run_cmd(["wslpath", "-w", str(path)], check=True)
    return proc.stdout.strip()


class CmdRunner(Protocol):
    def run(
        self,
        cmd: Sequence[str],
        timeout: Optional[float] = None,
        cwd: Optional[Path] = None,
    ) -> Tuple[str, str, int]:
        ...


class Clock(Protocol):
    def sleep(self, sec: float) -> None:
        ...

    def time(self) -> float:
        ...


class FileSystemWatcher(Protocol):
    def wait_for_path(self, path: Path, timeout: float = 30.0) -> bool:
        ...


class WatchlinkDeviceProtocol(Protocol):
    def ensure_vbus_on(self, timeout: float = 12.0) -> str:
        ...

    def ensure_vbus_off(self, timeout: float = 12.0) -> str:
        ...

    def send_mgr(self, cmd: str, timeout: float = 12.0) -> str:
        ...

    def send_uart(self, cmd: str, read_ms: int = 1500, timeout: Optional[float] = None) -> str:
        ...

    def listen_uart(self, seconds: int = 5, timeout: Optional[float] = None) -> str:
        ...

    def wait_ready(self, timeout: int = 60, probe_cmd: str = "monkey -g") -> str:
        ...

    def prepare_for_long_log(self, timeout: int = 15) -> str:
        ...

    def mount_data(self, timeout: int = 30) -> str:
        ...

    def mount_log(self, timeout: int = 30) -> str:
        ...

    def mount_system(self, timeout: int = 30) -> str:
        ...

    def unmount(self, timeout: int = 15) -> str:
        ...

    def reset(self, settle_sec: float = 8.0) -> str:
        ...

    def open_disk(self, disk: str, timeout: int = 30, auto_unmount: bool = True):
        ...


class RealCmdRunner:
    def run(
        self,
        cmd: Sequence[str],
        timeout: Optional[float] = None,
        cwd: Optional[Path] = None,
    ) -> Tuple[str, str, int]:
        proc = run_cmd(list(cmd), timeout=timeout, cwd=cwd, check=False)
        return proc.stdout, proc.stderr, proc.returncode


class RealClock:
    def sleep(self, sec: float) -> None:
        time.sleep(sec)

    def time(self) -> float:
        return time.time()


class RealFileSystemWatcher:
    def wait_for_path(self, path: Path, timeout: float = 30.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if path.exists():
                return True
            time.sleep(0.2)
        return path.exists()


def _normalize_vbus_state(output: str) -> Optional[bool]:
    for raw_line in (output or "").splitlines():
        line = raw_line.strip().lower()
        if line in {"1", "1.0", "on", "vbus=on", "wl+vbus=on"}:
            return True
        if line in {"0", "0.0", "off", "vbus=off", "wl+vbus=off"}:
            return False
    return None


def _powershell_serial_write(
    port: str,
    cmd: str,
    runner: CmdRunner,
    timeout: float = 8.0,
) -> str:
    ensure_wsl_windows_bridge()
    ps_script = (
        f'$port = New-Object System.IO.Ports.SerialPort "{port}", 115200, None, 8, One; '
        "$port.Open(); "
        f'$port.WriteLine("{cmd}"); '
        "Start-Sleep -Milliseconds 1500; "
        "$result = $port.ReadExisting(); "
        "$port.Close(); "
        "Write-Output $result"
    )
    stdout, stderr, rc = runner.run(
        ["powershell.exe", "-NoProfile", "-Command", ps_script],
        timeout=timeout,
        cwd=None,
    )
    output = (stdout or "") + (stderr or "")
    if rc != 0:
        raise RuntimeError(output.strip() or "PowerShell serial write failed")
    return output


def extract_disk_root(output: str) -> Path:
    for raw_line in reversed((output or "").splitlines()):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("["):
            continue
        if line.upper() == "UNMOUNTED":
            continue
        return Path(line)
    raise RuntimeError(f"Unable to parse disk root from output: {output.strip()}")


def _wrap_wlctl_cmd(wlctl_path: Path, args: Sequence[str]) -> Sequence[str]:
    if wlctl_path.suffix.lower() == ".ps1":
        return [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(wlctl_path),
            *args,
        ]
    if wlctl_path.suffix.lower() == ".sh" and sys.platform == "win32":
        return ["bash", str(wlctl_path), *args]
    return [str(wlctl_path), *args]


def _resolve_powershell_runtime() -> str:
    configured = os.environ.get("WLCTL_POWERSHELL_BIN", "").strip()
    if configured:
        return configured
    for candidate in ("powershell.exe", "powershell", "pwsh"):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    wsl_powershell = Path("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
    if wsl_powershell.exists():
        return str(wsl_powershell)
    return "powershell"


def _to_windows_path_for_wsl(path: Path) -> str:
    if not is_wsl_environment() or not shutil.which("wslpath"):
        return str(path)
    proc = run_cmd(["wslpath", "-w", str(path)], timeout=10.0, check=False)
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip()
    return str(path)


def _build_disk_mode_cmd(wlctl_path: Path, mode: str, mgr_port: str, wait_seconds: int) -> Sequence[str]:
    bridge = os.environ.get("WLCTL_DISK_BRIDGE_SH", "").strip()
    if bridge:
        return [bridge, mode, mgr_port, str(wait_seconds)]

    configured_disk_ps1 = os.environ.get("WLCTL_DISK_PS1", "").strip()
    disk_ps1 = Path(configured_disk_ps1) if configured_disk_ps1 else wlctl_path.parent.parent / "windows" / "disk_mode_auto.ps1"
    script_path = _to_windows_path_for_wsl(disk_ps1) if is_wsl_environment() else str(disk_ps1)
    cmd = [
        _resolve_powershell_runtime(),
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        script_path,
        "-Mode",
        mode,
        "-WaitSeconds",
        str(wait_seconds),
    ]
    if mgr_port:
        cmd.extend(["-MgrPort", mgr_port])
    return cmd


class WatchlinkDevice:
    def __init__(
        self,
        mgr_port: str = "",
        uart_port: str = "",
        cmd_runner: Optional[CmdRunner] = None,
        clock: Optional[Clock] = None,
        fs_watcher: Optional[FileSystemWatcher] = None,
        wlctl_path: Optional[Path] = None,
    ):
        self.mgr_port = mgr_port
        self.uart_port = uart_port
        self._cmd = cmd_runner or RealCmdRunner()
        self._clock = clock or RealClock()
        self._fs = fs_watcher or RealFileSystemWatcher()
        self._wlctl_path = Path(wlctl_path) if wlctl_path else build_wlctl_path(anchor=Path(__file__).resolve())

    @property
    def wlctl_path(self) -> Path:
        return self._wlctl_path

    def run_wlctl(
        self,
        args: Sequence[str],
        timeout: Optional[float] = None,
        cwd: Optional[Path] = None,
    ) -> str:
        cmd = list(_wrap_wlctl_cmd(self._wlctl_path, args))
        stdout, stderr, rc = self._cmd.run(cmd, timeout=timeout, cwd=cwd)
        if rc != 0:
            message = stderr.strip() or stdout.strip() or f"wlctl exited with code {rc}"
            raise RuntimeError(append_runtime_hint(message))
        return stdout

    def send_mgr(self, cmd: str, timeout: float = 12.0) -> str:
        if self.mgr_port and is_wsl_environment() and cmd.startswith("WL+"):
            try:
                fallback = _powershell_serial_write(self.mgr_port, cmd, self._cmd, timeout=max(8.0, timeout))
                if fallback.strip():
                    return fallback
            except Exception:
                pass

        args = ["serial", "--role", "MGR"]
        if self.mgr_port:
            args.extend(["--port", self.mgr_port])
        args.extend(["--cmd", cmd, "--read-ms", "1500"])
        try:
            return self.run_wlctl(args, timeout=timeout)
        except Exception:
            if self.mgr_port and is_wsl_environment() and cmd.startswith("WL+"):
                fallback = _powershell_serial_write(self.mgr_port, cmd, self._cmd, timeout=max(8.0, timeout))
                if fallback.strip():
                    return fallback
            raise

    def send_mgr_cmd(self, cmd: str, timeout: float = 12.0) -> str:
        return self.send_mgr(cmd, timeout=timeout)

    def send_uart(
        self,
        cmd: str,
        read_ms: int = 1500,
        timeout: Optional[float] = None,
    ) -> str:
        args = ["serial", "--role", "UART"]
        if self.uart_port:
            args.extend(["--port", self.uart_port])
        args.extend(["--cmd", cmd, "--read-ms", str(read_ms)])
        return self.run_wlctl(args, timeout=timeout)

    def send_uart_cmd(
        self,
        cmd: str,
        read_ms: int = 1500,
        timeout: Optional[float] = None,
    ) -> str:
        return self.send_uart(cmd, read_ms=read_ms, timeout=timeout)

    def listen_uart(self, seconds: int = 5, timeout: Optional[float] = None) -> str:
        args = ["listen", "--role", "UART", "--seconds", str(seconds)]
        if self.uart_port:
            args.extend(["--port", self.uart_port])
        return self.run_wlctl(args, timeout=timeout)

    def wait_ready(self, timeout: int = 60, probe_cmd: str = "monkey -g") -> str:
        args = ["ready"]
        if self.uart_port:
            args.extend(["--port", self.uart_port])
        args.extend(["--timeout", str(timeout), "--probe-cmd", probe_cmd])
        return self.run_wlctl(args, timeout=max(float(timeout) + 15.0, 20.0))

    def prepare_for_long_log(self, timeout: int = 15) -> str:
        args = ["awake"]
        if self.uart_port:
            args.extend(["--port", self.uart_port])
        args.extend(["--timeout", str(timeout)])
        return self.run_wlctl(args, timeout=max(float(timeout) + 12.0, 20.0))

    def mount_data(self, timeout: int = 30) -> str:
        args = ["disk", "mount-data", "--wait-seconds", str(timeout)]
        if self.mgr_port:
            args.extend(["--port", self.mgr_port])
        # Single combined cmd + 2s sleep + Wait-Root(timeout) + PS overhead.
        return self.run_wlctl(args, timeout=max(float(timeout) + 30.0, 30.0))

    def mount_data_root(self, timeout: int = 30) -> Path:
        """挂载 DATA 盘并验证是否是正确的设备磁盘。

        此方法会:
        1. 执行挂载命令
        2. 从输出中提取磁盘根路径
        3. 验证该路径是否是 DATA 盘 (通过卷标区分 DATA/SYSTEM/WLINK/IMG)
        4. 如果验证失败,自动查找其他可能的 DATA 盘

        Args:
            timeout: 挂载超时时间 (秒)

        Returns:
            验证通过的 DATA 盘根路径

        Raises:
            RuntimeError: 如果挂载失败
            ValueError: 如果无法找到正确的 DATA 盘
        """
        mount_output = self.mount_data(timeout=timeout)
        disk_root = extract_disk_root(mount_output)

        selected = select_data_root([disk_root])
        if selected:
            return selected

        # 验证失败,尝试查找其他 DATA 盘
        warnings.warn(
            f"SDK 返回的盘符 {disk_root} 验证失败,正在查找正确的 DATA 盘...",
            UserWarning
        )

        available_drives = _find_available_data_drives()
        selected = select_data_root([disk_root, *available_drives])
        if selected:
            warnings.warn(
                f"找到正确的 DATA 盘: {selected} (而非 SDK 返回的 {disk_root})",
                UserWarning
            )
            return selected

        # 所有盘都验证失败
        raise ValueError(
            f"无法找到正确的 DATA 盘\n"
            f"SDK 返回: {disk_root}\n"
            f"已尝试的盘符: {available_drives}\n"
            f"DATA 盘卷标应该包含 DATA, 且不能是 SYSTEM/WLINK/IMG 等分区\n"
            f"请检查:\n"
            f"  1. 设备是否正确连接\n"
            f"  2. 通过 Windows 文件管理器确认卷标以 DATA 开头的实际盘符\n"
            f"  3. 手动挂载: sudo mount -t drvfs F: /mnt/f (WSL 环境)"
        )

    def mount_log(self, timeout: int = 30) -> str:
        args = ["disk", "mount-log", "--wait-seconds", str(timeout)]
        if self.mgr_port:
            args.extend(["--port", self.mgr_port])
        return self.run_wlctl(args, timeout=max(float(timeout) + 30.0, 30.0))

    def mount_log_root(self, timeout: int = 30) -> Path:
        return extract_disk_root(self.mount_log(timeout=timeout))

    def mount_system(self, timeout: int = 30) -> str:
        cmd = list(_build_disk_mode_cmd(self._wlctl_path, "mount-system", self.mgr_port, timeout))
        stdout, stderr, rc = self._cmd.run(cmd, timeout=max(float(timeout) + 30.0, 30.0), cwd=None)
        if rc != 0:
            message = stderr.strip() or stdout.strip() or f"disk helper exited with code {rc}"
            raise RuntimeError(append_runtime_hint(message))
        return stdout

    def unmount(self, timeout: int = 15) -> str:
        args = ["disk", "unmount", "--wait-seconds", str(timeout)]
        if self.mgr_port:
            args.extend(["--port", self.mgr_port])
        return self.run_wlctl(args, timeout=max(float(timeout) + 10.0, 20.0))

    def reset(self, settle_sec: float = 8.0) -> str:
        output = self.send_mgr("WL+DISK=NULL WL+RESET", timeout=max(20.0, settle_sec + 12.0))
        if settle_sec > 0:
            self._clock.sleep(settle_sec)
        return output

    def detect_data_root(
        self,
        timeout: int = 30,
        explicit_root: str = "",
        exclude_roots: Optional[Sequence[str]] = None,
    ) -> Path:
        return wait_for_disk_root(
            explicit_root=explicit_root,
            timeout_sec=timeout,
            exclude_roots=exclude_roots,
        )

    def diagnose(self, error_text: str = "") -> Dict[str, object]:
        return diagnose_runtime(error_text)

    def open_disk(self, disk: str, timeout: int = 30, auto_unmount: bool = True):
        from .fs_utils import MountedDiskSession

        return MountedDiskSession(self, disk, timeout=timeout, auto_unmount=auto_unmount)

    def list_paths(self, disk: str, path: str = "", recursive: bool = False, timeout: int = 30):
        with self.open_disk(disk, timeout=timeout) as session:
            return session.list_paths(path=path, recursive=recursive)

    def read_file(self, disk: str, path: str, encoding: str = "utf-8", timeout: int = 30) -> str:
        with self.open_disk(disk, timeout=timeout) as session:
            return session.read_file(path=path, encoding=encoding)

    def pull_files(self, disk: str, from_path: str, to_path: Path, timeout: int = 30):
        with self.open_disk(disk, timeout=timeout) as session:
            return session.pull_files(from_path=from_path, to_path=to_path)

    def push_files(self, disk: str, from_path: Path, to_path: str = "", timeout: int = 30):
        with self.open_disk(disk, timeout=timeout) as session:
            return session.push_files(from_path=from_path, to_path=to_path)

    def remove_paths(
        self,
        disk: str,
        path: str,
        timeout: int = 30,
        *,
        recursive: bool = False,
        missing_ok: bool = True,
    ):
        with self.open_disk(disk, timeout=timeout) as session:
            return session.remove_paths(path=path, recursive=recursive, missing_ok=missing_ok)

    def monkey_on(self, interval_ms: int = 200, print_opt: str = "off", mem_opt: str = "0") -> str:
        args = ["monkey", "on", "--interval-ms", str(interval_ms), "--print", print_opt, "--mem", str(mem_opt)]
        if self.uart_port:
            args.extend(["--port", self.uart_port])
        return self.run_wlctl(args, timeout=20.0)

    def monkey_off(self) -> str:
        args = ["monkey", "off"]
        if self.uart_port:
            args.extend(["--port", self.uart_port])
        return self.run_wlctl(args, timeout=15.0)

    def monkey_status(self) -> str:
        args = ["monkey", "status"]
        if self.uart_port:
            args.extend(["--port", self.uart_port])
        return self.run_wlctl(args, timeout=15.0)

    def vbus_status(self) -> bool:
        args = ["vbus", "status"]
        if self.mgr_port:
            args.extend(["--port", self.mgr_port])
        output = self.run_wlctl(args, timeout=12.0)
        state = _normalize_vbus_state(output)
        if state is None:
            raise RuntimeError(f"Unable to parse VBUS state from output: {output.strip()}")
        return state

    def ensure_vbus_on(self, timeout: float = 12.0) -> str:
        args = ["vbus", "on"]
        if self.mgr_port:
            args.extend(["--port", self.mgr_port])
        output = self.run_wlctl(args, timeout=timeout)
        if not self.vbus_status():
            raise RuntimeError("VBUS should be ON after wlctl vbus on")
        return output

    def ensure_vbus_off(self, timeout: float = 12.0) -> str:
        args = ["vbus", "off"]
        if self.mgr_port:
            args.extend(["--port", self.mgr_port])
        output = self.run_wlctl(args, timeout=timeout)
        if self.vbus_status():
            raise RuntimeError("VBUS should be OFF after wlctl vbus off")
        return output


__all__ = [
    "CmdRunner",
    "Clock",
    "FileSystemWatcher",
    "IS_WIN32",
    "RealCmdRunner",
    "RealClock",
    "RealFileSystemWatcher",
    "WatchlinkDeviceProtocol",
    "WatchlinkDevice",
    "ensure_wsl_windows_bridge",
    "extract_disk_root",
    "is_wsl_environment",
    "run_cmd",
    "run_powershell",
    "to_windows_path",
]
