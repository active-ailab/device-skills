from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import subprocess
import sys
import time
import warnings
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence


IS_WIN32 = sys.platform == "win32"
_NON_DATA_LABEL_TOKENS = ("SYSTEM", "WLINK", "IMAGE", "IMG", "BOOT")


def _dedupe_paths(paths: Iterable[Path]) -> List[Path]:
    unique: List[Path] = []
    seen = set()
    for path in paths:
        text = str(path)
        if text in seen:
            continue
        seen.add(text)
        unique.append(path)
    return unique


def detect_mount_roots() -> List[Path]:
    if IS_WIN32:
        return _detect_mount_roots_win32()
    return _detect_mount_roots_unix()


def _detect_mount_roots_win32() -> List[Path]:
    roots: List[Path] = []
    for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
        drive = Path(f"{letter}:\\")
        if drive.exists():
            roots.append(drive)
    return roots


def _detect_mount_roots_unix() -> List[Path]:
    roots: List[Path] = []
    user = getpass.getuser()
    for base in (Path("/mnt"), Path("/media"), Path("/run/media") / user, Path("/Volumes")):
        if not base.exists():
            continue
        candidates: List[Path] = []
        try:
            candidates.extend(p for p in base.iterdir() if p.is_dir())
        except Exception:
            continue
        if base == Path("/media"):
            extra: List[Path] = []
            for candidate in list(candidates):
                try:
                    extra.extend(p for p in candidate.iterdir() if p.is_dir())
                except Exception:
                    continue
            candidates.extend(extra)
        for candidate in candidates:
            if candidate not in roots:
                roots.append(candidate)
    return roots


def is_wsl_environment() -> bool:
    if sys.platform != "linux":
        return False
    try:
        version_text = Path("/proc/version").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "microsoft" in version_text.lower() or "wsl" in version_text.lower()


def _drive_letter(path: Path) -> Optional[str]:
    text = str(path).strip()
    if len(text) >= 2 and text[1] == ":" and text[0].isalpha():
        return text[0].upper()
    parts = path.parts
    if len(parts) >= 3 and parts[1] == "mnt" and len(parts[2]) == 1 and parts[2].isalpha():
        return parts[2].upper()
    return None


def _volume_label_win32(path: Path) -> str:
    import ctypes

    letter = _drive_letter(path)
    root = f"{letter}:\\" if letter else str(path.anchor or path)
    volume_name = ctypes.create_unicode_buffer(261)
    fs_name = ctypes.create_unicode_buffer(261)
    serial = ctypes.c_ulong()
    max_component = ctypes.c_ulong()
    flags = ctypes.c_ulong()
    ok = ctypes.windll.kernel32.GetVolumeInformationW(
        ctypes.c_wchar_p(root),
        volume_name,
        ctypes.sizeof(volume_name),
        ctypes.byref(serial),
        ctypes.byref(max_component),
        ctypes.byref(flags),
        fs_name,
        ctypes.sizeof(fs_name),
    )
    return volume_name.value.strip() if ok else ""


def _volume_label_wsl(path: Path) -> str:
    letter = _drive_letter(path)
    if not letter:
        return ""
    commands = [
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            f"(Get-Volume -DriveLetter {letter} -ErrorAction SilentlyContinue).FileSystemLabel",
        ],
        ["cmd.exe", "/c", "vol", f"{letter}:"],
    ]
    for cmd in commands:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired):
            continue
        text = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        if proc.returncode != 0 or not text:
            continue
        if cmd[0].lower().endswith("cmd.exe"):
            for line in text.splitlines():
                match = re.search(r"\bis\s+(.+)$", line.strip(), re.IGNORECASE)
                if match:
                    return match.group(1).strip()
            continue
        return text.splitlines()[-1].strip()
    return ""


def get_volume_label(path: Path) -> str:
    try:
        if IS_WIN32:
            return _volume_label_win32(path)
        if is_wsl_environment():
            return _volume_label_wsl(path)
        expanded = path.expanduser()
        if expanded.parent in {Path("/Volumes"), Path("/media") / getpass.getuser(), Path("/run/media") / getpass.getuser()}:
            return expanded.name
    except Exception:
        return ""
    return ""


def is_data_volume_label(label: str) -> bool:
    normalized = (label or "").strip().upper()
    if not normalized:
        return False
    if any(token in normalized for token in _NON_DATA_LABEL_TOKENS):
        return False
    return normalized.startswith("DATA")


def is_system_volume_label(label: str) -> bool:
    normalized = (label or "").strip().upper()
    if not normalized:
        return False
    return normalized.startswith("SYSTEM")


def extract_disk_root(output: str) -> Path:
    for raw_line in reversed((output or "").splitlines()):
        line = raw_line.strip()
        if not line or line.startswith("[") or line.upper() == "UNMOUNTED":
            continue
        return Path(line)
    raise RuntimeError(f"Unable to parse disk root from output: {output.strip()}")


def verify_data_disk(drive_root: Path) -> bool:
    """验证给定的磁盘根路径是否是正确的 DATA 盘。

    通过卷标区分 DATA 盘和 SYSTEM/WLINK/IMG 等其他分区。

    Args:
        drive_root: 磁盘根路径 (如 Path("F:\\") 或 Path("/mnt/f"))

    Returns:
        True 如果是 DATA 盘, False 如果是 SYSTEM 盘或其他
    """
    try:
        label = get_volume_label(drive_root)
        if is_data_volume_label(label):
            return True

        # 检查 Windows 系统盘特征
        windows_dirs = ["Windows", "Program Files", "Users", "ProgramData", "Recovery", "$RECYCLE.BIN"]
        for win_dir in windows_dirs:
            if (drive_root / win_dir).is_dir():
                warnings.warn(
                    f"检测到 Windows 系统目录 {win_dir}, {drive_root} 可能不是 DATA 盘",
                    UserWarning
                )
                return False

        # 如果没有找到明确的特征,记录警告但仍返回 False
        label_hint = f"卷标为 {label!r}" if label else "未读取到卷标"
        warnings.warn(
            f"无法确认 {drive_root} 是 DATA 盘 ({label_hint})",
            UserWarning
        )
        return False

    except Exception as e:
        warnings.warn(f"验证磁盘 {drive_root} 时出错: {e}", UserWarning)
        return False


def verify_system_disk(drive_root: Path) -> bool:
    try:
        label = get_volume_label(drive_root)
        if not is_system_volume_label(label):
            return False

        windows_dirs = ["Windows", "Program Files", "Users", "ProgramData", "Recovery"]
        for win_dir in windows_dirs:
            if (drive_root / win_dir).is_dir():
                warnings.warn(
                    f"检测到 Windows 系统目录 {win_dir}, {drive_root} 不是设备 SYSTEM 盘",
                    UserWarning,
                )
                return False
        return True
    except Exception as e:
        warnings.warn(f"验证 SYSTEM 磁盘 {drive_root} 时出错: {e}", UserWarning)
        return False


def extract_and_verify_data_disk(mount_output: str) -> Path:
    """从挂载输出中提取磁盘根路径,并验证是否是正确的 DATA 盘。

    Args:
        mount_output: 挂载命令的输出文本 (如 "E:\\\\n")

    Returns:
        验证通过的 DATA 盘路径

    Raises:
        RuntimeError: 如果无法提取盘符或验证失败
        ValueError: 如果提取的盘符指向 SYSTEM 盘而非 DATA 盘
    """
    # 提取磁盘根路径
    disk_root = extract_disk_root(mount_output)

    # 验证是否是 DATA 盘
    if not verify_data_disk(disk_root):
        # 在 WSL 环境下,尝试查找其他可能的 DATA 盘
        if IS_WIN32 or is_wsl_environment():
            available_drives = _find_available_data_drives()
            for drive in available_drives:
                if drive != disk_root and verify_data_disk(drive):
                    warnings.warn(
                        f"SDK 返回的盘符 {disk_root} 可能不是 DATA 盘, "
                        f"自动切换到验证通过的 {drive}",
                        UserWarning
                    )
                    return drive

        raise ValueError(
            f"验证失败: {disk_root} 不是正确的 DATA 盘\n"
            f"DATA 盘卷标应该包含 DATA, 且不能是 SYSTEM/WLINK/IMG 等分区\n"
            f"请检查设备是否正确挂载,或手动指定正确的盘符"
        )

    return disk_root


def _find_available_data_drives() -> List[Path]:
    """查找所有可能的 DATA 盘候选。

    Returns:
        所有已挂载的磁盘根路径列表
    """
    return [root for root in detect_mount_roots() if verify_data_disk(root)]


def select_data_root(roots: Sequence[Path]) -> Optional[Path]:
    roots = _dedupe_paths(Path(root) for root in roots)
    if not roots:
        return None

    verified_roots = _dedupe_paths(root for root in roots if verify_data_disk(root))
    if verified_roots:
        return verified_roots[0]

    return None


def select_system_root(roots: Sequence[Path]) -> Optional[Path]:
    roots = _dedupe_paths(Path(root) for root in roots)
    if not roots:
        return None

    verified_roots = _dedupe_paths(root for root in roots if verify_system_disk(root))
    if verified_roots:
        return verified_roots[0]

    return None


def select_log_root(roots: Sequence[Path]) -> Optional[Path]:
    roots = _dedupe_paths(Path(root) for root in roots)
    if not roots:
        return None
    if len(roots) == 1:
        return roots[0]

    img_like = []
    for root in roots:
        try:
            if list(root.glob("*.img")):
                img_like.append(root)
                continue
        except Exception:
            pass
        name = root.name.upper()
        if name.startswith("IMAGE") or name == "IMG":
            img_like.append(root)
    img_like = _dedupe_paths(img_like)
    if img_like:
        return img_like[0]
    return roots[0]


def wait_for_disk_root(
    explicit_root: str = "",
    timeout_sec: int = 30,
    exclude_roots: Optional[Sequence[str]] = None,
    roots_provider: Optional[Callable[[], Sequence[Path]]] = None,
) -> Path:
    provider = roots_provider or detect_mount_roots
    deadline = time.time() + timeout_sec
    last_candidates: List[Path] = []
    excluded = {str(Path(item)) for item in (exclude_roots or [])}
    while time.time() < deadline:
        if explicit_root:
            root = Path(explicit_root).expanduser()
            if verify_data_disk(root):
                return root
        else:
            matches = [
                root for root in provider()
                if str(root) not in excluded and verify_data_disk(root)
            ]
            unique = _dedupe_paths(matches)
            last_candidates = unique
            if len(unique) == 1:
                return unique[0]
            if len(unique) > 1:
                joined = "\n".join(f"  - {path}" for path in unique)
                raise RuntimeError(
                    f"Multiple mounted DATA roots detected, please pass --disk-root:\n{joined}"
                )
        time.sleep(1.0)

    if explicit_root:
        raise RuntimeError(f"Unable to verify DATA root under --disk-root={explicit_root}")
    searched = "\n".join(f"  - {path}" for path in provider()) or "  (no mount roots found)"
    found = "\n".join(f"  - {path}" for path in last_candidates) or "  (no DATA roots found)"
    raise RuntimeError(
        "Unable to auto-detect mounted DATA root.\n"
        f"Searched mount roots:\n{searched}\n"
        f"Detected DATA roots:\n{found}"
    )


def _format_text(path: Optional[Path]) -> str:
    return f"{path}\n" if path else ""


def _format_json(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Watch-Link disk mount helpers")
    parser.add_argument(
        "action",
        choices=("detect-roots", "select-data-root", "select-log-root", "wait-for-disk-root"),
    )
    parser.add_argument("--path", default="", help=argparse.SUPPRESS)
    parser.add_argument("--root", action="append", default=[], help="Candidate root path (repeatable)")
    parser.add_argument("--explicit-root", default="", help="Explicit root for wait-for-disk-root")
    parser.add_argument("--exclude-root", action="append", default=[], help="Exclude root for wait-for-disk-root")
    parser.add_argument("--timeout", type=int, default=30, help="Wait timeout in seconds")
    parser.add_argument("--output", choices=("text", "json"), default="text")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.action == "detect-roots":
        roots = [str(path) for path in detect_mount_roots()]
        print(_format_json({"roots": roots}) if args.output == "json" else "".join(f"{root}\n" for root in roots), end="")
        return 0

    candidate_roots = [Path(item) for item in args.root]
    if args.action == "select-data-root":
        selected = select_data_root(candidate_roots)
        print(_format_json({"selected_root": str(selected) if selected else ""}) if args.output == "json" else _format_text(selected), end="")
        return 0

    if args.action == "select-log-root":
        selected = select_log_root(candidate_roots)
        print(_format_json({"selected_root": str(selected) if selected else ""}) if args.output == "json" else _format_text(selected), end="")
        return 0

    if args.action == "wait-for-disk-root":
        selected = wait_for_disk_root(
            explicit_root=args.explicit_root,
            timeout_sec=args.timeout,
            exclude_roots=args.exclude_root,
        )
        print(_format_json({"selected_root": str(selected)}) if args.output == "json" else _format_text(selected), end="")
        return 0

    parser.error(f"Unknown action: {args.action}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())


# 添加新函数到导出列表
__all__ = [
    "detect_mount_roots",
    "select_data_root",
    "select_system_root",
    "select_log_root",
    "wait_for_disk_root",
    "verify_data_disk",
    "verify_system_disk",
    "get_volume_label",
    "is_data_volume_label",
    "is_system_volume_label",
    "extract_disk_root",
    "extract_and_verify_data_disk",
    "_find_available_data_drives",
]
