from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from common.disk_utils import verify_data_disk, verify_system_disk


_WILDCARD_CHARS = "*?["
_BSTYLE_TARGET_DIR = Path("resources") / "styles"
_BSTYLE_SUFFIX = ".bstyle"
_DEFAULT_MOUNT_HELPER = Path("/usr/local/sbin/watchlink-v3-mount-helper")
_DEFAULT_WSL_MOUNT_BASE = Path(
    os.environ.get("WLCTL_WSL_MOUNT_BASE", "/mnt")
)


def is_wsl_environment() -> bool:
    if sys.platform != "linux":
        return False
    try:
        version_text = Path("/proc/version").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "microsoft" in version_text.lower() or "wsl" in version_text.lower()


def _looks_like_windows_drive(path_text: str) -> bool:
    return len(path_text) >= 2 and path_text[1] == ":"


def _get_mount_helper() -> Optional[Path]:
    candidate = os.environ.get("WLCTL_MOUNT_HELPER", "").strip()
    helper = Path(candidate) if candidate else _DEFAULT_MOUNT_HELPER
    try:
        if helper.is_file() and os.access(str(helper), os.X_OK):
            return helper
    except OSError:
        return None
    return None


def _run_mount_helper(helper: Path, drive_letter: str, mount_point: Path) -> None:
    owner = f"{os.getuid()}:{os.getgid()}"
    proc = subprocess.run(
        [
            "sudo",
            "-n",
            str(helper),
            "prepare-drvfs-mount",
            "--drive",
            drive_letter.upper(),
            "--mount-point",
            str(mount_point),
            "--owner",
            owner,
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip() or f"exit={proc.returncode}"
        raise RuntimeError(f"Mount helper failed for {drive_letter.upper()}: {detail}")


def _run_mount_helper_cleanup(helper: Path, mount_point: Path) -> None:
    proc = subprocess.run(
        [
            "sudo",
            "-n",
            str(helper),
            "cleanup-drvfs-mount",
            "--mount-point",
            str(mount_point),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip() or f"exit={proc.returncode}"
        raise RuntimeError(f"Mount helper cleanup failed for {mount_point}: {detail}")


def _wsl_mount_point_for_root(root: Path) -> Optional[Path]:
    path_text = str(root)
    if _looks_like_windows_drive(path_text):
        return _DEFAULT_WSL_MOUNT_BASE / path_text[0].lower()

    try:
        parts = root.resolve(strict=False).parts
    except OSError:
        parts = root.parts
    if len(parts) >= 3 and parts[1] == "mnt" and len(parts[2]) == 1 and parts[2].isalpha():
        return Path("/", parts[1], parts[2])
    return None


def cleanup_local_mount_root(root: Path) -> None:
    if not is_wsl_environment():
        return

    mount_point = _wsl_mount_point_for_root(root)
    if mount_point is None:
        return

    helper = _get_mount_helper()
    if helper is not None:
        _run_mount_helper_cleanup(helper, mount_point)
        return

    subprocess.run(
        ["sudo", "-n", "umount", "-l", str(mount_point)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    subprocess.run(
        ["sudo", "-n", "rm", "-rf", str(mount_point)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def ensure_local_mount_root(root: Path) -> Path:
    path_text = str(root)
    if not is_wsl_environment() or not _looks_like_windows_drive(path_text):
        return root

    drive_letter = path_text[0].lower()
    mount_point = _DEFAULT_WSL_MOUNT_BASE / drive_letter

    # Check if the mount point exists and is a real filesystem.
    # A corrupted/stale /mnt/{letter} from a previous failed mount can cause
    # Path.exists() to raise OSError — handle it gracefully.
    _mp_ok = False
    try:
        _mp_ok = mount_point.exists() and os.path.ismount(str(mount_point))
    except OSError:
        _mp_ok = False

    if _mp_ok:
        pass  # Already mounted, just convert the path below.
    else:
        helper = _get_mount_helper()
        if helper is not None:
            _run_mount_helper(helper, drive_letter, mount_point)
        else:
            # Clean up stale directory before attempting mount.
            subprocess.run(
                ["sudo", "-n", "rm", "-rf", str(mount_point)],
                capture_output=True, text=True, timeout=10, check=False,
            )
            subprocess.run(
                ["sudo", "-n", "mkdir", "-p", str(mount_point)],
                capture_output=True, text=True, timeout=10, check=False,
            )
            subprocess.run(
                ["sudo", "-n", "mount", "-t", "drvfs", f"{drive_letter.upper()}:", str(mount_point)],
                capture_output=True, text=True, timeout=15, check=False,
            )
            import getpass
            _user = getpass.getuser()
            subprocess.run(
                ["sudo", "-n", "chown", f"{_user}:{_user}", str(mount_point)],
                capture_output=True, text=True, timeout=10, check=False,
            )
        # Fail fast: verify the mount actually succeeded.
        try:
            mounted_ok = mount_point.exists() and os.path.ismount(str(mount_point))
        except OSError:
            mounted_ok = False
        if not mounted_ok:
            raise RuntimeError(
                f"Failed to mount {drive_letter.upper()}: to {mount_point}. "
                "Ensure the drive exists and is not in use."
            )

    rest = path_text[2:].replace("\\", "/")
    if rest.startswith("/"):
        return Path(str(mount_point) + rest)
    if rest:
        return Path(f"{mount_point}/{rest}")
    return mount_point


def _ensure_data_mount_root(root: Path) -> Path:
    mounted_root = ensure_local_mount_root(root)
    if not verify_data_disk(mounted_root):
        raise RuntimeError(
            f"Mounted DATA root verification failed: {root} -> {mounted_root}. "
            "Refusing to read/write because this may be the SYSTEM disk. "
            "Check the actual DATA drive letter in Windows Explorer or run disk mount-data again."
        )
    return mounted_root


def _ensure_system_mount_root(root: Path) -> Path:
    mounted_root = ensure_local_mount_root(root)
    if not verify_system_disk(mounted_root):
        raise RuntimeError(
            f"Mounted SYSTEM root verification failed: {root} -> {mounted_root}. "
            "Refusing to write bstyle resources because this may not be the device SYSTEM disk."
        )
    return mounted_root


def _collect_bstyle_sources(source: Path) -> tuple[Path, List[Path]]:
    source_path = source.expanduser().resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"Local source not found: {source_path}")

    if source_path.is_file():
        if source_path.suffix.lower() != _BSTYLE_SUFFIX:
            raise ValueError(f"push-bstyle only accepts .bstyle files: {source_path}")
        return source_path, [source_path]

    bstyle_files = sorted((item for item in source_path.rglob("*") if item.is_file() and item.suffix.lower() == _BSTYLE_SUFFIX), key=str)
    if not bstyle_files:
        raise ValueError(f"push-bstyle found no .bstyle files under: {source_path}")
    return source_path, bstyle_files


def _has_magic(path_text: str) -> bool:
    return any(ch in path_text for ch in _WILDCARD_CHARS)


def _normalize_device_path(path_text: str) -> str:
    normalized = (path_text or "").strip().replace("\\", "/")
    if normalized in {"", ".", "/"}:
        return ""
    return normalized.lstrip("/")


def _ensure_within_root(root: Path, candidate: Path) -> Path:
    root_resolved = root.resolve(strict=False)
    candidate_resolved = candidate.resolve(strict=False)
    if candidate_resolved == root_resolved:
        return candidate_resolved
    if root_resolved not in candidate_resolved.parents:
        raise ValueError(f"Path escapes mounted root: {candidate}")
    return candidate_resolved


def resolve_device_path(root: Path, device_path: str) -> Path:
    normalized = _normalize_device_path(device_path)
    if not normalized:
        return root.resolve(strict=False)
    return _ensure_within_root(root, root / normalized)


def _expand_matches(root: Path, path_text: str, recursive: bool = False) -> List[Path]:
    normalized = _normalize_device_path(path_text)
    if not normalized:
        iterator = root.rglob("*") if recursive else root.iterdir()
        return sorted((path.resolve(strict=False) for path in iterator if path.exists()), key=str)

    if _has_magic(normalized):
        return sorted(
            (_ensure_within_root(root, path) for path in root.glob(normalized) if path.exists()),
            key=str,
        )

    target = resolve_device_path(root, normalized)
    if not target.exists():
        return []
    if target.is_dir():
        iterator = target.rglob("*") if recursive else target.iterdir()
        return sorted((path.resolve(strict=False) for path in iterator if path.exists()), key=str)
    return [target]


def list_mounted_paths(root: Path, path: str = "", recursive: bool = False) -> List[Path]:
    mounted_root = ensure_local_mount_root(root)
    return _expand_matches(mounted_root, path, recursive=recursive)


def read_mounted_file(root: Path, path: str, encoding: str = "utf-8") -> str:
    mounted_root = ensure_local_mount_root(root)
    target = resolve_device_path(mounted_root, path)
    if not target.exists() or not target.is_file():
        raise FileNotFoundError(f"Device file not found: {path}")
    return target.read_text(encoding=encoding, errors="replace")


def _iter_files(paths: Iterable[Path]) -> Iterable[Path]:
    for path in paths:
        if path.is_dir():
            for child in sorted((item for item in path.rglob("*") if item.is_file()), key=str):
                yield child
            continue
        if path.is_file():
            yield path


def pull_mounted_paths(root: Path, from_path: str, to_path: Path) -> List[Path]:
    mounted_root = ensure_local_mount_root(root)
    destination_root = Path(to_path).expanduser().resolve()
    destination_root.mkdir(parents=True, exist_ok=True)

    matches = _expand_matches(mounted_root, from_path, recursive=True)
    copied: List[Path] = []
    for source in _iter_files(matches):
        relative = source.relative_to(mounted_root)
        destination = destination_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        # copyfile avoids copystat EPERM on drvfs (same as _copy_file_to_destination).
        shutil.copyfile(str(source), str(destination))
        copied.append(destination)
    return copied


def _copy_file_to_destination(source: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Use copyfile instead of copy2 — drvfs (WSL Windows mount) does not
    # support setting utime/chmod, and copy2's copystat raises EPERM.
    shutil.copyfile(str(source), str(destination))
    return destination


def push_to_mounted_root(root: Path, from_path: Path, to_path: str = "") -> List[Path]:
    mounted_root = ensure_local_mount_root(root)
    source = Path(from_path).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f"Local source not found: {source}")

    copied: List[Path] = []
    if source.is_file():
        if to_path:
            destination = resolve_device_path(mounted_root, to_path)
            if to_path.endswith("/") or destination.is_dir():
                destination = destination / source.name
        else:
            destination = mounted_root / source.name
        copied.append(_copy_file_to_destination(source, destination))
        return copied

    destination_root = resolve_device_path(mounted_root, to_path or source.name)
    destination_root.mkdir(parents=True, exist_ok=True)
    for child in sorted((item for item in source.rglob("*") if item.is_file()), key=str):
        relative = child.relative_to(source)
        copied.append(_copy_file_to_destination(child, destination_root / relative))
    return copied


def push_bstyle_to_mounted_root(root: Path, from_path: Path, to_path: str) -> List[Path]:
    mounted_root = ensure_local_mount_root(root)
    source, bstyle_files = _collect_bstyle_sources(from_path)
    destination_root = resolve_device_path(mounted_root, to_path)

    copied: List[Path] = []
    if source.is_file():
        if to_path.endswith("/") or destination_root.is_dir():
            destination = destination_root / source.name
        else:
            destination = destination_root
        copied.append(_copy_file_to_destination(source, destination))
        return copied

    destination_root.mkdir(parents=True, exist_ok=True)
    for child in bstyle_files:
        copied.append(_copy_file_to_destination(child, destination_root / child.name))
    return copied


def remove_from_mounted_root(
    root: Path,
    path: str,
    *,
    recursive: bool = False,
    missing_ok: bool = True,
) -> List[Path]:
    mounted_root = ensure_local_mount_root(root)
    normalized = _normalize_device_path(path)
    removed: List[Path] = []

    if _has_magic(normalized):
        targets = _expand_matches(mounted_root, normalized, recursive=True)
    else:
        target = resolve_device_path(mounted_root, normalized)
        if not target.exists():
            if missing_ok:
                return []
            raise FileNotFoundError(f"Device path not found: {path}")
        targets = [target]

    for target in sorted(targets, key=lambda item: (len(item.parts), str(item)), reverse=True):
        if not target.exists():
            continue
        if target.is_dir():
            if not recursive:
                target.rmdir()
                removed.append(target)
                continue
            descendants = sorted(
                (item for item in target.rglob("*") if item.exists()),
                key=lambda item: (len(item.parts), str(item)),
                reverse=True,
            )
            for child in descendants:
                if child.is_dir():
                    child.rmdir()
                else:
                    child.unlink()
                removed.append(child)
            target.rmdir()
            removed.append(target)
            continue
        target.unlink()
        removed.append(target)
    return removed


class MountedDiskSession:
    def __init__(
        self,
        device: "WatchlinkDeviceProtocol",
        disk: str,
        timeout: int = 30,
        auto_unmount: bool = True,
        *,
        skip_mount: bool = False,
        explicit_root: str | Path | None = None,
    ):
        disk_normalized = disk.strip().lower()
        if disk_normalized not in {"data", "log", "system"}:
            raise ValueError("disk must be 'data', 'log' or 'system'")
        self._device = device
        self._disk = disk_normalized
        self._timeout = timeout
        self._auto_unmount = auto_unmount and not skip_mount
        self._skip_mount = skip_mount
        self._explicit_root = Path(explicit_root) if explicit_root else None
        self._root: Optional[Path] = None
        self.mount_output = ""
        self.unmount_output = ""

    @property
    def root(self) -> Path:
        if self._root is None:
            raise RuntimeError("MountedDiskSession is not active")
        return self._root

    def __enter__(self) -> "MountedDiskSession":
        if self._skip_mount:
            if self._explicit_root is None:
                raise ValueError("--skip-mount requires --root")
            if self._disk == "data":
                self._root = _ensure_data_mount_root(self._explicit_root)
            elif self._disk == "system":
                self._root = _ensure_system_mount_root(self._explicit_root)
            else:
                self._root = ensure_local_mount_root(self._explicit_root)
            return self

        if self._disk == "data":
            mount_output = self._device.mount_data(timeout=self._timeout)
        elif self._disk == "system":
            mount_output = self._device.mount_system(timeout=self._timeout)
        else:
            mount_output = self._device.mount_log(timeout=self._timeout)
        self.mount_output = mount_output
        try:
            from .wlctl_sdk import extract_disk_root
        except ImportError:
            from wlctl_sdk import extract_disk_root  # type: ignore

        root = extract_disk_root(mount_output)
        if self._disk == "data":
            self._root = _ensure_data_mount_root(root)
        elif self._disk == "system":
            self._root = _ensure_system_mount_root(root)
        else:
            self._root = ensure_local_mount_root(root)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self._auto_unmount:
            try:
                self.unmount_output = self._device.unmount(timeout=15)
            except Exception:
                pass
            finally:
                if self._root is not None:
                    try:
                        cleanup_local_mount_root(self._root)
                    except Exception:
                        pass
        return False

    def list_paths(self, path: str = "", recursive: bool = False) -> List[Path]:
        return list_mounted_paths(self.root, path=path, recursive=recursive)

    def read_file(self, path: str, encoding: str = "utf-8") -> str:
        return read_mounted_file(self.root, path=path, encoding=encoding)

    def pull_files(self, from_path: str, to_path: Path) -> List[Path]:
        return pull_mounted_paths(self.root, from_path=from_path, to_path=to_path)

    def push_files(self, from_path: Path, to_path: str = "") -> List[Path]:
        return push_to_mounted_root(self.root, from_path=from_path, to_path=to_path)

    def push_bstyle_files(self, from_path: Path, to_path: str) -> List[Path]:
        return push_bstyle_to_mounted_root(self.root, from_path=from_path, to_path=to_path)

    def remove_paths(self, path: str, *, recursive: bool = False, missing_ok: bool = True) -> List[Path]:
        return remove_from_mounted_root(self.root, path, recursive=recursive, missing_ok=missing_ok)


def _format_text_lines(paths: Sequence[Path]) -> str:
    return "".join(f"{path}\n" for path in paths)


def _format_json(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Watch-Link mounted filesystem helpers")
    parser.add_argument("action", choices=("ls", "read", "pull", "push", "rm", "push-bstyle"))
    parser.add_argument("--disk", choices=("data", "log"), required=False)
    parser.add_argument("--mgr-port", default="", help="Explicit MGR port for mount/unmount")
    parser.add_argument("--skip-mount", action="store_true", help="Use an already-mounted disk (requires --root)")
    parser.add_argument("--root", default="", help="Explicit mounted root path (e.g. /mnt/f, use with --skip-mount)")
    parser.add_argument("--path", default="", help="Device path relative to the mounted root")
    parser.add_argument("--from-path", default="", help="Device path or local source path")
    parser.add_argument("--to-path", default="", help="Local destination dir or device destination path")
    parser.add_argument("--wait-seconds", type=int, default=30, help="Disk mount timeout in seconds")
    parser.add_argument("--encoding", default="utf-8", help="Text encoding for read")
    parser.add_argument("--recursive", action="store_true", help="List or remove recursively")
    parser.add_argument("--missing-ok", action="store_true", help="Do not fail when rm target is missing")
    parser.add_argument("--output", choices=("text", "json"), default="text")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.action != "push-bstyle" and not args.disk:
        parser.error("--disk is required")
    if args.action == "push-bstyle":
        if args.disk:
            parser.error("push-bstyle uses SYSTEM/resources/styles automatically; do not pass --disk")
        if args.skip_mount or args.root:
            parser.error("push-bstyle manages SYSTEM mount automatically; do not pass --skip-mount or --root")
        if args.path or args.to_path or args.recursive or args.missing_ok:
            parser.error("push-bstyle only accepts --from-path plus mount/output options")

    try:
        from .wlctl_sdk import WatchlinkDevice
    except ImportError:
        from wlctl_sdk import WatchlinkDevice  # type: ignore

    if args.skip_mount:
        if not args.root:
            parser.error("--skip-mount requires --root")
        dev = None  # type: ignore[assignment]
    else:
        try:
            from .wlctl_sdk import WatchlinkDevice
        except ImportError:
            from wlctl_sdk import WatchlinkDevice  # type: ignore
        dev = WatchlinkDevice(mgr_port=args.mgr_port)

    if args.action == "push-bstyle":
        if not args.from_path:
            parser.error("push-bstyle requires --from-path")
        try:
            _collect_bstyle_sources(Path(args.from_path))
        except Exception as exc:
            parser.error(str(exc))
        with MountedDiskSession(dev, "system", timeout=args.wait_seconds) as session:
            target_dir = str(_BSTYLE_TARGET_DIR) + os.sep
            try:
                copied = session.push_bstyle_files(Path(args.from_path), target_dir)
            except Exception as exc:
                parser.error(str(exc))
            if args.output == "json":
                print(
                    _format_json(
                        {
                            "action": "push-bstyle",
                            "disk": "system",
                            "target": str(_BSTYLE_TARGET_DIR),
                            "copied": [str(path) for path in copied],
                            "count": len(copied),
                        }
                    ),
                    end="",
                )
            else:
                print(_format_text_lines(copied), end="")
            return 0

    with MountedDiskSession(
        dev,
        args.disk,
        timeout=args.wait_seconds,
        skip_mount=args.skip_mount,
        explicit_root=args.root or None,
    ) as session:
        if args.action == "ls":
            paths = session.list_paths(path=args.path, recursive=args.recursive)
            if args.output == "json":
                print(_format_json({"paths": [str(path) for path in paths]}), end="")
            else:
                print(_format_text_lines(paths), end="")
            return 0

        if args.action == "read":
            content = session.read_file(path=args.path, encoding=args.encoding)
            if args.output == "json":
                print(
                    _format_json(
                        {
                            "path": args.path,
                            "disk": args.disk,
                            "content": content,
                            "encoding": args.encoding,
                        }
                    ),
                    end="",
                )
            else:
                print(content, end="" if content.endswith("\n") else "\n")
            return 0

        if args.action == "pull":
            if not args.from_path:
                parser.error("pull requires --from-path")
            if not args.to_path:
                parser.error("pull requires --to-path")
            copied = session.pull_files(args.from_path, Path(args.to_path))
            if args.output == "json":
                print(_format_json({"copied": [str(path) for path in copied], "count": len(copied)}), end="")
            else:
                print(_format_text_lines(copied), end="")
            return 0

        if args.action == "push":
            if not args.from_path:
                parser.error("push requires --from-path")
            copied = session.push_files(Path(args.from_path), args.to_path)
            if args.output == "json":
                print(_format_json({"copied": [str(path) for path in copied], "count": len(copied)}), end="")
            else:
                print(_format_text_lines(copied), end="")
            return 0

        if args.action == "rm":
            target_path = args.path or args.from_path
            if not target_path:
                parser.error("rm requires --path")
            removed = session.remove_paths(target_path, recursive=args.recursive, missing_ok=args.missing_ok)
            if args.output == "json":
                print(_format_json({"removed": [str(path) for path in removed], "count": len(removed)}), end="")
            else:
                print(_format_text_lines(removed), end="")
            return 0

    parser.error(f"Unknown action: {args.action}")
    return 2


__all__ = [
    "MountedDiskSession",
    "ensure_local_mount_root",
    "is_wsl_environment",
    "list_mounted_paths",
    "main",
    "pull_mounted_paths",
    "push_to_mounted_root",
    "read_mounted_file",
    "remove_from_mounted_root",
    "resolve_device_path",
]


if __name__ == "__main__":
    raise SystemExit(main())
