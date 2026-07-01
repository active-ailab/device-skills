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
            mounted = fs_utils.ensure_local_mount_root(Path("F:\\payloads"))

        self.assertEqual(mounted, Path("/mnt/f/payloads"))
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
                fs_utils.ensure_local_mount_root(Path("F:\\payloads"))

        self.assertIn("Failed to mount F:", str(ctx.exception))

    def test_cleanup_local_mount_root_prefers_helper_when_available(self) -> None:
        helper = Path("/usr/local/sbin/watchlink-v3-mount-helper")

        with mock.patch.object(fs_utils, "is_wsl_environment", return_value=True), \
            mock.patch.object(fs_utils, "_get_mount_helper", return_value=helper), \
            mock.patch("common.fs_utils.subprocess.run") as run_mock:
            run_mock.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            fs_utils.cleanup_local_mount_root(Path("/mnt/f/payloads"))

        run_mock.assert_called_once_with(
            [
                "sudo",
                "-n",
                str(helper),
                "cleanup-drvfs-mount",
                "--mount-point",
                "/mnt/f",
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

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

    def test_push_bstyle_mounts_system_and_writes_fixed_styles_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            system_root = base / "SYSTEM"
            system_root.mkdir()
            source = base / "Foo.bstyle"
            source.write_text("style", encoding="utf-8")

            class FakeDevice:
                unmounted = False

                def mount_system(self, timeout: int = 30) -> str:
                    return f"{system_root}\n"

                def unmount(self, timeout: int = 15) -> str:
                    self.unmounted = True
                    return "UNMOUNTED\n"

            fake = FakeDevice()

            with mock.patch.object(fs_utils, "verify_system_disk", return_value=True):
                with fs_utils.MountedDiskSession(fake, "system") as session:
                    copied = session.push_files(
                        source,
                        str(Path("resources") / "styles") + fs_utils.os.sep,
                    )

            expected = system_root / "resources" / "styles" / "Foo.bstyle"
            self.assertEqual(copied, [expected.resolve(strict=False)])
            self.assertEqual(expected.read_text(encoding="utf-8"), "style")
            self.assertTrue(fake.unmounted)

    def test_push_bstyle_rejects_disk_argument(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            fs_utils.main(["push-bstyle", "--disk", "data", "--from-path", "Foo.bstyle"])
        self.assertEqual(ctx.exception.code, 2)

    def test_push_bstyle_rejects_non_bstyle_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "Foo.txt"
            source.write_text("style", encoding="utf-8")

            with self.assertRaises(SystemExit) as ctx:
                fs_utils.main(["push-bstyle", "--from-path", str(source)])

        self.assertEqual(ctx.exception.code, 2)

    def test_push_bstyle_directory_ignores_non_bstyle_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source_dir = base / "styles"
            system_root = base / "SYSTEM"
            nested_dir = source_dir / "nested"
            nested_dir.mkdir(parents=True)
            system_root.mkdir()
            (source_dir / "Foo.bstyle").write_text("style", encoding="utf-8")
            (source_dir / "note.txt").write_text("not style", encoding="utf-8")
            (nested_dir / "Bar.bstyle").write_text("nested style", encoding="utf-8")

            class FakeDevice:
                def mount_system(self, timeout: int = 30) -> str:
                    return f"{system_root}\n"

                def unmount(self, timeout: int = 15) -> str:
                    return "UNMOUNTED\n"

            with mock.patch.object(fs_utils, "verify_system_disk", return_value=True):
                with fs_utils.MountedDiskSession(FakeDevice(), "system") as session:
                    copied = session.push_bstyle_files(
                        source_dir,
                        str(Path("resources") / "styles") + fs_utils.os.sep,
                    )

            self.assertEqual((system_root / "resources" / "styles" / "Foo.bstyle").read_text(encoding="utf-8"), "style")
            self.assertEqual((system_root / "resources" / "styles" / "Bar.bstyle").read_text(encoding="utf-8"), "nested style")
            self.assertFalse((system_root / "resources" / "styles" / "note.txt").exists())
            self.assertFalse((system_root / "resources" / "styles" / "nested").exists())
            self.assertEqual(
                copied,
                [
                    (system_root / "resources" / "styles" / "Foo.bstyle").resolve(strict=False),
                    (system_root / "resources" / "styles" / "Bar.bstyle").resolve(strict=False),
                ],
            )
