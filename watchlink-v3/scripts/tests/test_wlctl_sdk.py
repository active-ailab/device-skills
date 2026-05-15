from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import common.wlctl_sdk as wlctl_sdk
from common.wlctl_sdk import WatchlinkDevice


class MockCmdRunner:
    def __init__(self) -> None:
        self.calls = []
        self.responses = {}

    def run(self, cmd, timeout=None, cwd=None):
        key = tuple(cmd)
        self.calls.append((key, timeout, cwd))
        return self.responses.get(key, ("", "", 0))


class WatchlinkSdkTests(unittest.TestCase):
    def test_vbus_status_parses_on(self) -> None:
        runner = MockCmdRunner()
        runner.responses[("/fake/wlctl.sh", "vbus", "status", "--port", "COM37")] = ("on\n", "", 0)
        dev = WatchlinkDevice(mgr_port="COM37", cmd_runner=runner, wlctl_path=Path("/fake/wlctl.sh"))
        self.assertTrue(dev.vbus_status())

    def test_ensure_vbus_on_runs_command_then_verifies(self) -> None:
        runner = MockCmdRunner()
        runner.responses[("/fake/wlctl.sh", "vbus", "on", "--port", "COM37")] = ("on\n", "", 0)
        runner.responses[("/fake/wlctl.sh", "vbus", "status", "--port", "COM37")] = ("on\n", "", 0)
        dev = WatchlinkDevice(mgr_port="COM37", cmd_runner=runner, wlctl_path=Path("/fake/wlctl.sh"))
        output = dev.ensure_vbus_on()
        self.assertIn("on", output)
        self.assertEqual(runner.calls[0][0], ("/fake/wlctl.sh", "vbus", "on", "--port", "COM37"))

    def test_ensure_vbus_off_runs_command_then_verifies(self) -> None:
        runner = MockCmdRunner()
        runner.responses[("/fake/wlctl.sh", "vbus", "off", "--port", "COM37")] = ("off\n", "", 0)
        runner.responses[("/fake/wlctl.sh", "vbus", "status", "--port", "COM37")] = ("off\n", "", 0)
        dev = WatchlinkDevice(mgr_port="COM37", cmd_runner=runner, wlctl_path=Path("/fake/wlctl.sh"))
        output = dev.ensure_vbus_off()
        self.assertIn("off", output)

    def test_wait_ready_uses_ready_subcommand(self) -> None:
        runner = MockCmdRunner()
        runner.responses[("/fake/wlctl.sh", "ready", "--port", "COM38", "--timeout", "30", "--probe-cmd", "monkey -g")] = ("ready\n", "", 0)
        dev = WatchlinkDevice(uart_port="COM38", cmd_runner=runner, wlctl_path=Path("/fake/wlctl.sh"))
        output = dev.wait_ready(timeout=30)
        self.assertIn("ready", output)

    def test_mount_data_uses_disk_subcommand(self) -> None:
        runner = MockCmdRunner()
        runner.responses[("/fake/wlctl.sh", "disk", "mount-data", "--wait-seconds", "25", "--port", "COM37")] = ("X:/sport/gomore\n", "", 0)
        dev = WatchlinkDevice(mgr_port="COM37", cmd_runner=runner, wlctl_path=Path("/fake/wlctl.sh"))
        output = dev.mount_data(timeout=25)
        self.assertIn("gomore", output)

    def test_mount_data_root_parses_last_root_line(self) -> None:
        runner = MockCmdRunner()
        runner.responses[("/fake/wlctl.sh", "disk", "mount-data", "--wait-seconds", "25", "--port", "COM37")] = (
            "[INFO] DATA disk mode: FS\nX:/sport/gomore\n",
            "",
            0,
        )
        dev = WatchlinkDevice(mgr_port="COM37", cmd_runner=runner, wlctl_path=Path("/fake/wlctl.sh"))
        self.assertEqual(dev.mount_data_root(timeout=25), Path("X:/sport/gomore"))

    def test_mount_log_uses_disk_subcommand(self) -> None:
        runner = MockCmdRunner()
        runner.responses[("/fake/wlctl.sh", "disk", "mount-log", "--wait-seconds", "22", "--port", "COM37")] = ("X:/IMAGE\n", "", 0)
        dev = WatchlinkDevice(mgr_port="COM37", cmd_runner=runner, wlctl_path=Path("/fake/wlctl.sh"))
        output = dev.mount_log(timeout=22)
        self.assertIn("IMAGE", output)

    def test_mount_log_root_parses_last_root_line(self) -> None:
        runner = MockCmdRunner()
        runner.responses[("/fake/wlctl.sh", "disk", "mount-log", "--wait-seconds", "22", "--port", "COM37")] = (
            "[INFO] LOG disk mode: MG->IMG\nX:/IMAGE\n",
            "",
            0,
        )
        dev = WatchlinkDevice(mgr_port="COM37", cmd_runner=runner, wlctl_path=Path("/fake/wlctl.sh"))
        self.assertEqual(dev.mount_log_root(timeout=22), Path("X:/IMAGE"))

    def test_unmount_uses_disk_subcommand(self) -> None:
        runner = MockCmdRunner()
        runner.responses[("/fake/wlctl.sh", "disk", "unmount", "--wait-seconds", "15", "--port", "COM37")] = ("UNMOUNTED\n", "", 0)
        dev = WatchlinkDevice(mgr_port="COM37", cmd_runner=runner, wlctl_path=Path("/fake/wlctl.sh"))
        output = dev.unmount(timeout=15)
        self.assertIn("UNMOUNTED", output)

    def test_prepare_for_long_log_uses_awake_subcommand(self) -> None:
        runner = MockCmdRunner()
        runner.responses[("/fake/wlctl.sh", "awake", "--port", "COM38", "--timeout", "12")] = ("awake\n", "", 0)
        dev = WatchlinkDevice(uart_port="COM38", cmd_runner=runner, wlctl_path=Path("/fake/wlctl.sh"))
        output = dev.prepare_for_long_log(timeout=12)
        self.assertIn("awake", output)

    def test_send_uart_cmd_alias_delegates(self) -> None:
        runner = MockCmdRunner()
        runner.responses[("/fake/wlctl.sh", "serial", "--role", "UART", "--port", "COM38", "--cmd", "monkey -g", "--read-ms", "1500")] = ("status=on\n", "", 0)
        dev = WatchlinkDevice(uart_port="COM38", cmd_runner=runner, wlctl_path=Path("/fake/wlctl.sh"))
        output = dev.send_uart_cmd("monkey -g")
        self.assertIn("status=on", output)

    def test_send_mgr_cmd_alias_delegates(self) -> None:
        runner = MockCmdRunner()
        runner.responses[("/fake/wlctl.sh", "serial", "--role", "MGR", "--port", "COM37", "--cmd", "WL+FWVER=?", "--read-ms", "1500")] = ("FWVER=1.2.3\n", "", 0)
        dev = WatchlinkDevice(mgr_port="COM37", cmd_runner=runner, wlctl_path=Path("/fake/wlctl.sh"))
        output = dev.send_mgr_cmd("WL+FWVER=?")
        self.assertIn("FWVER", output)

    def test_send_mgr_uses_powershell_fallback_in_wsl(self) -> None:
        runner = MockCmdRunner()
        runner.responses[(
            "powershell.exe",
            "-NoProfile",
            "-Command",
            '$port = New-Object System.IO.Ports.SerialPort "COM37", 115200, None, 8, One; $port.Open(); $port.WriteLine("WL+FWVER=?"); Start-Sleep -Milliseconds 1500; $result = $port.ReadExisting(); $port.Close(); Write-Output $result',
        )] = ("FWVER=9.9.9\n", "", 0)
        dev = WatchlinkDevice(mgr_port="COM37", cmd_runner=runner, wlctl_path=Path("/fake/wlctl.sh"))

        old_is_wsl = wlctl_sdk.is_wsl_environment
        old_bridge = wlctl_sdk.ensure_wsl_windows_bridge
        try:
            wlctl_sdk.is_wsl_environment = lambda: True
            wlctl_sdk.ensure_wsl_windows_bridge = lambda timeout_sec=10.0, force=False: None
            output = dev.send_mgr("WL+FWVER=?")
        finally:
            wlctl_sdk.is_wsl_environment = old_is_wsl
            wlctl_sdk.ensure_wsl_windows_bridge = old_bridge

        self.assertIn("FWVER=9.9.9", output)
        self.assertEqual(runner.calls[0][0][0], "powershell.exe")

    def test_reset_sends_mgr_reset_command(self) -> None:
        runner = MockCmdRunner()
        runner.responses[("/fake/wlctl.sh", "serial", "--role", "MGR", "--port", "COM37", "--cmd", "WL+DISK=NULL WL+RESET", "--read-ms", "1500")] = ("OK\n", "", 0)
        dev = WatchlinkDevice(mgr_port="COM37", cmd_runner=runner, wlctl_path=Path("/fake/wlctl.sh"))
        output = dev.reset(settle_sec=0)
        self.assertIn("OK", output)

    def test_monkey_on_uses_monkey_subcommand(self) -> None:
        runner = MockCmdRunner()
        runner.responses[("/fake/wlctl.sh", "monkey", "on", "--interval-ms", "333", "--print", "off", "--mem", "0", "--port", "COM38")] = ("started\n", "", 0)
        dev = WatchlinkDevice(uart_port="COM38", cmd_runner=runner, wlctl_path=Path("/fake/wlctl.sh"))
        output = dev.monkey_on(interval_ms=333)
        self.assertIn("started", output)

    def test_monkey_status_uses_monkey_subcommand(self) -> None:
        runner = MockCmdRunner()
        runner.responses[("/fake/wlctl.sh", "monkey", "status", "--port", "COM38")] = ("monkey status on\n", "", 0)
        dev = WatchlinkDevice(uart_port="COM38", cmd_runner=runner, wlctl_path=Path("/fake/wlctl.sh"))
        output = dev.monkey_status()
        self.assertIn("status", output)

    def test_monkey_off_uses_monkey_subcommand(self) -> None:
        runner = MockCmdRunner()
        runner.responses[("/fake/wlctl.sh", "monkey", "off", "--port", "COM38")] = ("stopped\n", "", 0)
        dev = WatchlinkDevice(uart_port="COM38", cmd_runner=runner, wlctl_path=Path("/fake/wlctl.sh"))
        output = dev.monkey_off()
        self.assertIn("stopped", output)

    def test_detect_gomore_root_uses_explicit_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "mount"
            gomore = root / "sport" / "gomore"
            gomore.mkdir(parents=True)
            dev = WatchlinkDevice(cmd_runner=MockCmdRunner(), wlctl_path=Path("/fake/wlctl.sh"))
            self.assertEqual(dev.detect_gomore_root(explicit_root=str(root), timeout=1), gomore)

    def test_diagnose_returns_runtime_payload(self) -> None:
        dev = WatchlinkDevice(cmd_runner=MockCmdRunner(), wlctl_path=Path("/fake/wlctl.sh"))
        payload = dev.diagnose("cmd.exe timed out")
        self.assertEqual(payload["primary_error_code"], "E_WSL_BRIDGE")

    def test_run_wlctl_appends_runtime_hint_on_failure(self) -> None:
        runner = MockCmdRunner()
        runner.responses[("/fake/wlctl.sh", "ready", "--port", "COM38", "--timeout", "30", "--probe-cmd", "monkey -g")] = (
            "",
            "Device did not reach launcher-ready or shell-ready state within timeout",
            7,
        )
        dev = WatchlinkDevice(uart_port="COM38", cmd_runner=runner, wlctl_path=Path("/fake/wlctl.sh"))
        with self.assertRaises(RuntimeError) as ctx:
            dev.wait_ready(timeout=30)
        self.assertIn("E_DEVICE_READY_TIMEOUT", str(ctx.exception))

    def test_read_file_uses_mounted_data_root(self) -> None:
        runner = MockCmdRunner()
        with tempfile.TemporaryDirectory() as tmp:
            mount_root = Path(tmp) / "sport" / "gomore"
            sample_file = mount_root / "data_sample" / "sample.csv"
            sample_file.parent.mkdir(parents=True)
            sample_file.write_text("hello\n", encoding="utf-8")
            runner.responses[("/fake/wlctl.sh", "disk", "mount-data", "--wait-seconds", "30", "--port", "COM37")] = (
                f"{mount_root}\n",
                "",
                0,
            )
            runner.responses[("/fake/wlctl.sh", "disk", "unmount", "--wait-seconds", "15", "--port", "COM37")] = (
                "UNMOUNTED\n",
                "",
                0,
            )
            dev = WatchlinkDevice(mgr_port="COM37", cmd_runner=runner, wlctl_path=Path("/fake/wlctl.sh"))
            self.assertEqual(dev.read_file("data", "data_sample/sample.csv"), "hello\n")

    def test_pull_files_copies_matching_logs(self) -> None:
        runner = MockCmdRunner()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            mount_root = tmp_path / "IMAGE"
            source_file = mount_root / "logs" / "run.log"
            source_file.parent.mkdir(parents=True)
            source_file.write_text("log-body\n", encoding="utf-8")
            output_dir = tmp_path / "out"
            runner.responses[("/fake/wlctl.sh", "disk", "mount-log", "--wait-seconds", "30", "--port", "COM37")] = (
                f"{mount_root}\n",
                "",
                0,
            )
            runner.responses[("/fake/wlctl.sh", "disk", "unmount", "--wait-seconds", "15", "--port", "COM37")] = (
                "UNMOUNTED\n",
                "",
                0,
            )
            dev = WatchlinkDevice(mgr_port="COM37", cmd_runner=runner, wlctl_path=Path("/fake/wlctl.sh"))
            copied = dev.pull_files("log", "**/*.log", output_dir)
            self.assertEqual(len(copied), 1)
            self.assertEqual(copied[0].read_text(encoding="utf-8"), "log-body\n")
            self.assertEqual(copied[0], output_dir / "logs" / "run.log")

    def test_push_files_copies_local_tree_to_device_root(self) -> None:
        runner = MockCmdRunner()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            mount_root = tmp_path / "sport" / "gomore"
            mount_root.mkdir(parents=True)
            source_root = tmp_path / "local_data_sample"
            nested_source = source_root / "nested"
            nested_source.mkdir(parents=True)
            sample_file = nested_source / "gomore_data_20260515_120000.csv"
            sample_file.write_text("csv-body\n", encoding="utf-8")
            runner.responses[("/fake/wlctl.sh", "disk", "mount-data", "--wait-seconds", "30", "--port", "COM37")] = (
                f"{mount_root}\n",
                "",
                0,
            )
            runner.responses[("/fake/wlctl.sh", "disk", "unmount", "--wait-seconds", "15", "--port", "COM37")] = (
                "UNMOUNTED\n",
                "",
                0,
            )
            dev = WatchlinkDevice(mgr_port="COM37", cmd_runner=runner, wlctl_path=Path("/fake/wlctl.sh"))
            copied = dev.push_files("data", source_root, "data_sample")
            target = mount_root / "data_sample" / "nested" / "gomore_data_20260515_120000.csv"
            self.assertEqual(copied, [target])
            self.assertEqual(target.read_text(encoding="utf-8"), "csv-body\n")

    def test_remove_paths_deletes_recursive_directory(self) -> None:
        runner = MockCmdRunner()
        with tempfile.TemporaryDirectory() as tmp:
            mount_root = Path(tmp) / "sport" / "gomore"
            target = mount_root / "data_sample" / "nested" / "gomore_data_20260515_120000.csv"
            target.parent.mkdir(parents=True)
            target.write_text("csv-body\n", encoding="utf-8")
            runner.responses[("/fake/wlctl.sh", "disk", "mount-data", "--wait-seconds", "30", "--port", "COM37")] = (
                f"{mount_root}\n",
                "",
                0,
            )
            runner.responses[("/fake/wlctl.sh", "disk", "unmount", "--wait-seconds", "15", "--port", "COM37")] = (
                "UNMOUNTED\n",
                "",
                0,
            )
            dev = WatchlinkDevice(mgr_port="COM37", cmd_runner=runner, wlctl_path=Path("/fake/wlctl.sh"))
            removed = dev.remove_paths("data", "data_sample", recursive=True)
            self.assertFalse(target.exists())
            self.assertIn(target, removed)
