from __future__ import annotations

import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import common.disk_utils as disk_utils
from common.disk_utils import is_data_volume_label, select_data_root, select_log_root, verify_data_disk, wait_for_disk_root


class DiskUtilsTests(unittest.TestCase):
    def test_is_data_volume_label_accepts_data_partition(self) -> None:
        self.assertTrue(is_data_volume_label("DATA A1B2"))
        self.assertTrue(is_data_volume_label("DATA55CD"))

    def test_is_data_volume_label_rejects_non_data_partitions(self) -> None:
        self.assertFalse(is_data_volume_label("SYSTEM C3D4"))
        self.assertFalse(is_data_volume_label("WLINK"))
        self.assertFalse(is_data_volume_label("IMAGE"))

    def test_select_data_root_prefers_data_volume_label(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root1 = Path(tmp) / "E"
            root2 = Path(tmp) / "F"
            root1.mkdir()
            root2.mkdir()
            labels = {root1: "SYSTEM C3D4", root2: "DATA A1B2"}
            with mock.patch.object(disk_utils, "get_volume_label", side_effect=lambda root: labels.get(root, "")):
                self.assertEqual(select_data_root([root1, root2]), root2)

    def test_select_data_root_rejects_system_like_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "E"
            (root / "Windows").mkdir(parents=True)
            self.assertIsNone(select_data_root([root]))

    def test_verify_data_disk_accepts_data_volume_label(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "F"
            root.mkdir()
            with mock.patch.object(disk_utils, "get_volume_label", return_value="DATA A1B2"):
                self.assertTrue(verify_data_disk(root))

    def test_select_log_root_prefers_img_partition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root1 = Path(tmp) / "X"
            root2 = Path(tmp) / "IMG"
            root1.mkdir()
            root2.mkdir()
            (root2 / "firmware.img").write_text("x", encoding="utf-8")
            self.assertEqual(select_log_root([root1, root2]), root2)

    def test_wait_for_disk_root_returns_single_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "G"
            root.mkdir(parents=True)

            def provider():
                return [root]

            with mock.patch.object(disk_utils, "get_volume_label", return_value="DATA A1B2"):
                self.assertEqual(wait_for_disk_root(timeout_sec=1, roots_provider=provider), root)

    def test_wait_for_disk_root_raises_on_multiple_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root1 = Path(tmp) / "G1"
            root2 = Path(tmp) / "G2"
            root1.mkdir(parents=True)
            root2.mkdir(parents=True)

            def provider():
                return [root1, root2]

            with mock.patch.object(disk_utils, "get_volume_label", return_value="DATA A1B2"):
                with self.assertRaises(RuntimeError) as ctx:
                    wait_for_disk_root(timeout_sec=1, roots_provider=provider)
            self.assertIn("Multiple mounted DATA roots detected", str(ctx.exception))
