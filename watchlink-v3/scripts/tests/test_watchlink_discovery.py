from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from watchlink_discovery import ensure_wlctl_sdk, find_watchlink_scripts_dir


class WatchlinkDiscoveryTests(unittest.TestCase):
    def test_find_scripts_dir_from_env_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            scripts_dir = Path(tmp) / "watchlink-v3" / "scripts"
            scripts_dir.mkdir(parents=True)
            (scripts_dir / "wlctl.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")

            old_env = os.environ.get("WATCHLINK_V3_SCRIPTS_DIR")
            try:
                os.environ["WATCHLINK_V3_SCRIPTS_DIR"] = str(scripts_dir)
                self.assertEqual(find_watchlink_scripts_dir(), scripts_dir)
            finally:
                if old_env is None:
                    os.environ.pop("WATCHLINK_V3_SCRIPTS_DIR", None)
                else:
                    os.environ["WATCHLINK_V3_SCRIPTS_DIR"] = old_env

    def test_ensure_sdk_adds_scripts_dir_to_sys_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            scripts_dir = Path(tmp) / "watchlink-v3" / "scripts"
            scripts_dir.mkdir(parents=True)
            (scripts_dir / "wlctl.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")

            old_env = os.environ.get("WATCHLINK_V3_SCRIPTS_DIR")
            old_path = list(sys.path)
            try:
                os.environ["WATCHLINK_V3_SCRIPTS_DIR"] = str(scripts_dir)
                ensure_wlctl_sdk()
                self.assertEqual(sys.path[0], str(scripts_dir))
            finally:
                sys.path[:] = old_path
                if old_env is None:
                    os.environ.pop("WATCHLINK_V3_SCRIPTS_DIR", None)
                else:
                    os.environ["WATCHLINK_V3_SCRIPTS_DIR"] = old_env

    def test_find_scripts_dir_accepts_nested_platform_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            scripts_dir = Path(tmp) / "watchlink-v3" / "scripts"
            (scripts_dir / "linux").mkdir(parents=True)
            (scripts_dir / "windows").mkdir(parents=True)
            (scripts_dir / "linux" / "wlctl.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")

            old_env = os.environ.get("WATCHLINK_V3_SCRIPTS_DIR")
            try:
                os.environ["WATCHLINK_V3_SCRIPTS_DIR"] = str(scripts_dir / "linux")
                self.assertEqual(find_watchlink_scripts_dir(), scripts_dir)
            finally:
                if old_env is None:
                    os.environ.pop("WATCHLINK_V3_SCRIPTS_DIR", None)
                else:
                    os.environ["WATCHLINK_V3_SCRIPTS_DIR"] = old_env
