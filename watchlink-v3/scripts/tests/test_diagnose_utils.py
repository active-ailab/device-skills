from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from diagnose_utils import (
    build_runtime_diagnostic_payload,
    build_runtime_hint,
    collect_runtime_diagnostics,
    diagnose_runtime,
)


class DiagnoseUtilsTests(unittest.TestCase):
    def test_collect_runtime_diagnostics_matches_bridge_error(self) -> None:
        diagnostics = collect_runtime_diagnostics("cmd.exe timed out while probing WSL bridge")
        self.assertEqual(diagnostics[0]["code"], "E_WSL_BRIDGE")

    def test_build_runtime_payload_contains_vbus_code(self) -> None:
        payload = build_runtime_diagnostic_payload("WL+VBUS=OFF and device is not responding")
        self.assertEqual(payload["primary_error_code"], "E_VBUS_OFF")
        self.assertIn("E_VBUS_OFF", payload["error_codes"])

    def test_build_runtime_hint_contains_ready_timeout_hint(self) -> None:
        hint = build_runtime_hint("Device did not reach launcher-ready or shell-ready state within timeout")
        self.assertIn("E_DEVICE_READY_TIMEOUT", hint)

    def test_diagnose_runtime_includes_environment(self) -> None:
        payload = diagnose_runtime("log file /tmp/a.log not found")
        self.assertIn("environment", payload)
        self.assertEqual(payload["primary_error_code"], "E_LOG_FILE_NOT_FOUND")
