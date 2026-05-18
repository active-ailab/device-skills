from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


WLCTL_SH = Path(__file__).resolve().parents[1] / "linux" / "wlctl.sh"


SERIAL_STUB = """#!/usr/bin/env bash
set -euo pipefail
STATE_FILE="${WLCTL_STUB_STATE_FILE:?}"
COUNTER_DIR="${WLCTL_STUB_COUNTER_DIR:?}"
cmd=""
listen=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --cmd) cmd="$2"; shift 2 ;;
    --listen) listen=1; shift ;;
    *) shift ;;
  esac
done

next_response() {
  local key="$1"
  local count_file="$COUNTER_DIR/${key}.count"
  local idx=1
  if [[ -f "$count_file" ]]; then
    idx=$(( $(cat "$count_file") + 1 ))
  fi
  printf '%s' "$idx" > "$count_file"
  if [[ -f "$COUNTER_DIR/${key}_${idx}.txt" ]]; then
    cat "$COUNTER_DIR/${key}_${idx}.txt"
    return 0
  fi
  if [[ -f "$COUNTER_DIR/${key}.txt" ]]; then
    cat "$COUNTER_DIR/${key}.txt"
  fi
}

state="$(cat "$STATE_FILE")"
if [[ "$listen" -eq 1 ]]; then
  next_response listen
  exit 0
fi

case "$cmd" in
  "WL+VBUS=?")
    printf '%s\\n' "$state"
    ;;
  "WL+VBUS=ON")
    printf 'OK\\n'
    printf '1\\n' > "$STATE_FILE"
    ;;
  "WL+VBUS=OFF")
    printf 'OK\\n'
    printf '0\\n' > "$STATE_FILE"
    ;;
  "power -s on")
    next_response keepawake
    ;;
  *)
    next_response probe
    ;;
esac
"""


class WlctlShellTests(unittest.TestCase):
    def _run(self, state: str, *args: str, responses=None):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state_file = tmp_path / "vbus.state"
            state_file.write_text(state, encoding="utf-8")
            stub = tmp_path / "serial_stub.sh"
            stub.write_text(SERIAL_STUB, encoding="utf-8")
            stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
            counter_dir = tmp_path / "responses"
            counter_dir.mkdir()

            for key, values in (responses or {}).items():
                for idx, value in enumerate(values, 1):
                    (counter_dir / f"{key}_{idx}.txt").write_text(value, encoding="utf-8")

            env = os.environ.copy()
            env["WLCTL_SERIAL_SH"] = str(stub)
            env["WLCTL_STUB_STATE_FILE"] = str(state_file)
            env["WLCTL_STUB_COUNTER_DIR"] = str(counter_dir)

            proc = subprocess.run(
                ["bash", str(WLCTL_SH), *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )
            return proc, state_file.read_text(encoding="utf-8").strip()

    def test_vbus_status_reports_off(self) -> None:
        proc, _ = self._run("0", "vbus", "status")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "off")

    def test_vbus_status_json_output(self) -> None:
        proc, _ = self._run("0", "vbus", "status", "--output", "json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["command"], "vbus")
        self.assertEqual(payload["action"], "status")
        self.assertEqual(payload["state"], "off")

    def test_help_flag_exits_zero(self) -> None:
        proc = subprocess.run(
            ["bash", str(WLCTL_SH), "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Usage:", proc.stdout)

    def test_vbus_on_switches_state(self) -> None:
        proc, state = self._run("0", "vbus", "on")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "on")
        self.assertEqual(state, "1")

    def test_vbus_off_switches_state(self) -> None:
        proc, state = self._run("1", "vbus", "off")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "off")
        self.assertEqual(state, "0")

    def test_ready_succeeds_when_listen_hits_launcher_marker(self) -> None:
        proc, _ = self._run(
            "1",
            "ready",
            "--timeout",
            "2",
            responses={
                "probe": ["booting...\n"],
                "listen": ["gotoScreen:LauncherScreen\n"],
            },
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("gotoScreen:LauncherScreen", proc.stdout)

    def test_ready_json_output_contains_transcript(self) -> None:
        proc, _ = self._run(
            "1",
            "ready",
            "--timeout",
            "2",
            "--output",
            "json",
            responses={
                "probe": ["booting...\n"],
                "listen": ["gotoScreen:LauncherScreen\n"],
            },
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["command"], "ready")
        self.assertEqual(payload["status"], "ok")
        self.assertIn("gotoScreen:LauncherScreen", payload["output"])

    def test_awake_succeeds_when_probe_reaches_shell_prompt(self) -> None:
        proc, _ = self._run(
            "1",
            "awake",
            "--timeout",
            "2",
            responses={
                "keepawake": ["screen awake\n"],
                "probe": ["# \n"],
            },
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("UART shell probe #1 succeeded", proc.stdout)

    def test_diagnose_outputs_json_payload(self) -> None:
        proc = subprocess.run(
            [
                "bash",
                str(WLCTL_SH),
                "diagnose",
                "--output",
                "json",
                "--message",
                "cmd.exe timed out",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('"primary_error_code": "E_WSL_BRIDGE"', proc.stdout)

    def test_disk_mount_data_uses_bridge_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bridge = tmp_path / "disk_bridge.sh"
            bridge.write_text(
                "#!/usr/bin/env bash\nset -euo pipefail\nprintf 'MODE=%s PORT=%s WAIT=%s\\n' \"$1\" \"$2\" \"$3\"\n",
                encoding="utf-8",
            )
            bridge.chmod(bridge.stat().st_mode | stat.S_IEXEC)

            env = os.environ.copy()
            env["WLCTL_DISK_BRIDGE_SH"] = str(bridge)
            proc = subprocess.run(
                ["bash", str(WLCTL_SH), "disk", "mount-data", "--port", "COM37", "--wait-seconds", "31"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("MODE=mount-data PORT=COM37 WAIT=31", proc.stdout)

    def test_fs_subcommand_uses_python_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fs_stub = tmp_path / "fs_stub.py"
            fs_stub.write_text(
                "import json, sys\nprint(json.dumps({'argv': sys.argv[1:]}))\n",
                encoding="utf-8",
            )

            env = os.environ.copy()
            env["WLCTL_FS_PY"] = str(fs_stub)
            proc = subprocess.run(
                [
                    "bash",
                    str(WLCTL_SH),
                    "fs",
                    "ls",
                    "--disk",
                    "data",
                    "--path",
                    "payloads",
                    "--output",
                    "json",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["argv"][0], "ls")
            self.assertIn("--disk", payload["argv"])
            self.assertIn("data", payload["argv"])
