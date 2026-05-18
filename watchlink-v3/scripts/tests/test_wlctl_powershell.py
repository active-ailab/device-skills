from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
WLCTL_PS1 = SCRIPTS_DIR / "windows" / "wlctl.ps1"
DIAGNOSE_PY = SCRIPTS_DIR / "common" / "diagnose_utils.py"

SERIAL_STUB = r"""
param(
    [string]$Role,
    [string]$Port,
    [string]$Command,
    [int]$ReadWindowMs = 1500,
    [switch]$Listen,
    [int]$ListenSeconds = 10
)

$mode = $env:WLCTL_TEST_MODE

if ($mode -eq 'ready') {
    if ($Listen) {
        Write-Output 'gotoScreen:LauncherScreen'
        exit 0
    }
    if ($Command -eq 'monkey -g') {
        Write-Output 'booting...'
        exit 0
    }
    Write-Output 'noop'
    exit 0
}

if ($mode -eq 'awake') {
    if ($Command -eq 'power -s on') {
        Write-Output 'screen awake'
        exit 0
    }
    if ($Command -eq 'monkey -g') {
        Write-Output '# '
        exit 0
    }
    if ($Listen) {
        Write-Output ''
        exit 0
    }
    Write-Output 'noop'
    exit 0
}

Write-Output "mode=$mode role=$Role cmd=$Command listen=$Listen"
"""

DISK_STUB = "param()\nWrite-Output 'disk stub'\n"
FS_STUB = "import json, sys\nprint(json.dumps({'argv': sys.argv[1:]}))\n"


def _command_available(name: str) -> bool:
    return (
        subprocess.run(
            ["bash", "-lc", f"command -v {name} >/dev/null 2>&1"],
            check=False,
        ).returncode
        == 0
    )


def _to_windows_path(path: Path) -> str:
    proc = subprocess.run(
        ["wslpath", "-w", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or f"wslpath failed for {path}")
    return proc.stdout.strip()


def _find_windows_python() -> str | None:
    if not _command_available("powershell.exe"):
        return None
    candidates = ("python.exe", "python", "py")
    for candidate in candidates:
        proc = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                f"$cmd = Get-Command {candidate} -ErrorAction SilentlyContinue; if ($cmd) {{ Write-Output $cmd.Source }}",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        resolved = proc.stdout.strip()
        if resolved:
            if candidate == "py":
                return "py"
            return resolved
    return None


WINDOWS_PYTHON = _find_windows_python()


@unittest.skipUnless(_command_available("powershell.exe") and _command_available("wslpath"), "powershell.exe + wslpath required")
class WlctlPowerShellTests(unittest.TestCase):
    def _run(self, *args: str, env=None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                _to_windows_path(WLCTL_PS1),
                *args,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            check=False,
        )

    def _build_env(self, tmp: str, mode: str) -> dict:
        tmp_path = Path(tmp)
        serial_stub = tmp_path / "serial_stub.ps1"
        serial_stub.write_text(SERIAL_STUB, encoding="utf-8")
        disk_stub = tmp_path / "disk_stub.ps1"
        disk_stub.write_text(DISK_STUB, encoding="utf-8")
        fs_stub = tmp_path / "fs_stub.py"
        fs_stub.write_text(FS_STUB, encoding="utf-8")

        env = os.environ.copy()
        env["WLCTL_SERIAL_PS1"] = str(serial_stub)
        env["WLCTL_DISK_PS1"] = str(disk_stub)
        env["WLCTL_DIAGNOSE_PY"] = str(DIAGNOSE_PY)
        env["WLCTL_FS_PY"] = str(fs_stub)
        env["WLCTL_TEST_MODE"] = mode
        if WINDOWS_PYTHON:
            env["WLCTL_PYTHON_BIN"] = WINDOWS_PYTHON
        markers = {
            "WLCTL_SERIAL_PS1/p",
            "WLCTL_DISK_PS1/p",
            "WLCTL_DIAGNOSE_PY/p",
            "WLCTL_FS_PY/p",
            "WLCTL_TEST_MODE",
        }
        if WINDOWS_PYTHON:
            markers.add("WLCTL_PYTHON_BIN")
        existing = [item for item in env.get("WSLENV", "").split(":") if item]
        env["WSLENV"] = ":".join(existing + [item for item in markers if item not in existing])
        return env

    def test_help_flag_exits_zero(self) -> None:
        proc = self._run("-Help")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Usage:", proc.stdout)
        self.assertIn("ready", proc.stdout)

    def test_ready_action_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proc = self._run(
                "ready",
                "-Port",
                "COM37",
                "-Timeout",
                "2",
                env=self._build_env(tmp, mode="ready"),
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("gotoScreen:LauncherScreen", proc.stdout)

    def test_ready_json_output_is_machine_readable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proc = self._run(
                "ready",
                "-Port",
                "COM37",
                "-Timeout",
                "2",
                "-Output",
                "json",
                env=self._build_env(tmp, mode="ready"),
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["command"], "ready")
        self.assertEqual(payload["status"], "ok")
        self.assertIn("gotoScreen:LauncherScreen", payload["output"])

    def test_awake_action_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proc = self._run(
                "awake",
                "-Port",
                "COM37",
                "-Timeout",
                "2",
                env=self._build_env(tmp, mode="awake"),
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("UART shell probe #1 succeeded", proc.stdout)

    def test_fs_subcommand_uses_python_override(self) -> None:
        if not WINDOWS_PYTHON:
            self.skipTest("Windows python runtime not available")
        with tempfile.TemporaryDirectory() as tmp:
            proc = self._run(
                "fs",
                "ls",
                "-Disk",
                "data",
                "-FsPath",
                "payloads",
                "-Output",
                "json",
                env=self._build_env(tmp, mode="ready"),
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["argv"][0], "ls")
        self.assertIn("--disk", payload["argv"])
        self.assertIn("data", payload["argv"])
