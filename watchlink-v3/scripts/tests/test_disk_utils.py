from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from disk_utils import resolve_gomore_root, select_data_root, select_log_root, wait_for_disk_root


class DiskUtilsTests(unittest.TestCase):
    def test_resolve_gomore_root_prefers_nested_storage_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gomore = root / "storage" / "sport" / "gomore"
            gomore.mkdir(parents=True)
            self.assertEqual(resolve_gomore_root(root), gomore)

    def test_select_data_root_prefers_gomore_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root1 = Path(tmp) / "E"
            root2 = Path(tmp) / "F"
            root1.mkdir()
            root2.mkdir()
            gomore = root2 / "sport" / "gomore"
            gomore.mkdir(parents=True)
            self.assertEqual(select_data_root([root1, root2]), gomore)

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
            gomore = root / "gomore"
            gomore.mkdir(parents=True)

            def provider():
                return [root]

            self.assertEqual(wait_for_disk_root(timeout_sec=1, roots_provider=provider), gomore)

    def test_wait_for_disk_root_raises_on_multiple_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root1 = Path(tmp) / "G1"
            root2 = Path(tmp) / "G2"
            (root1 / "gomore").mkdir(parents=True)
            (root2 / "gomore").mkdir(parents=True)

            def provider():
                return [root1, root2]

            with self.assertRaises(RuntimeError) as ctx:
                wait_for_disk_root(timeout_sec=1, roots_provider=provider)
            self.assertIn("Multiple mounted gomore roots detected", str(ctx.exception))
