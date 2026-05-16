from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import common.fs_utils as fs_utils


class FsUtilsTests(unittest.TestCase):
    def test_ensure_local_mount_root_prefers_helper_when_available(self) -> None:
        helper = Path("/usr/local/sbin/watchlink-v3-mount-helper")

        with mock.patch.object(fs_utils, "is_wsl_environment", return_value=True), \
            mock.patch.object(fs_utils, "_get_mount_helper", return_value=helper), \
            mock.patch("common.fs_utils.os.path.ismount", side_effect=[False, True]), \
            mock.patch("pathlib.Path.exists", return_value=True), \
            mock.patch("common.fs_utils.subprocess.run") as run_mock:
            run_mock.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            mounted = fs_utils.ensure_local_mount_root(Path("F:\\sport\\gomore"))

        self.assertEqual(mounted, Path("/mnt/f/sport/gomore"))
        run_mock.assert_called_once_with(
            [
                "sudo",
                "-n",
                str(helper),
                "prepare-drvfs-mount",
                "--drive",
                "F",
                "--mount-point",
                "/mnt/f",
                "--owner",
                f"{fs_utils.os.getuid()}:{fs_utils.os.getgid()}",
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

    def test_ensure_local_mount_root_raises_when_mount_still_missing(self) -> None:
        helper = Path("/usr/local/sbin/watchlink-v3-mount-helper")

        with mock.patch.object(fs_utils, "is_wsl_environment", return_value=True), \
            mock.patch.object(fs_utils, "_get_mount_helper", return_value=helper), \
            mock.patch("common.fs_utils.os.path.ismount", side_effect=[False, False]), \
            mock.patch("pathlib.Path.exists", return_value=True), \
            mock.patch("common.fs_utils.subprocess.run") as run_mock:
            run_mock.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            with self.assertRaises(RuntimeError) as ctx:
                fs_utils.ensure_local_mount_root(Path("F:\\sport\\gomore"))

        self.assertIn("Failed to mount F:", str(ctx.exception))

    def test_pull_mounted_paths_uses_copyfile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "IMAGE"
            out = Path(tmp) / "out"
            source = root / "logs" / "run.log"
            source.parent.mkdir(parents=True)
            source.write_text("log-body\n", encoding="utf-8")

            with mock.patch("common.fs_utils.shutil.copyfile", wraps=shutil.copyfile) as copyfile_mock:
                copied = fs_utils.pull_mounted_paths(root, "**/*.log", out)
                copied_text = copied[0].read_text(encoding="utf-8")

            self.assertEqual(copied, [out / "logs" / "run.log"])
            self.assertEqual(copied_text, "log-body\n")
            copyfile_mock.assert_called_once()
