from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterable, Optional


LAYOUT_RELATIVE_SCRIPTS = Path("agent_skills/skills/device/watchlink-v3/scripts")
PLATFORM_SUBDIRS = {"common", "linux", "windows"}


def _dedupe_paths(paths: Iterable[Path]) -> Iterable[Path]:
    seen = set()
    for path in paths:
        resolved = str(path)
        if resolved in seen:
            continue
        seen.add(resolved)
        yield path


def _iter_anchor_roots(anchor: Optional[Path]) -> Iterable[Path]:
    if anchor is None:
        base = Path.cwd()
    else:
        base = anchor if anchor.is_dir() else anchor.parent
    yield base
    yield from base.parents


def _normalize_scripts_dir(path: Path) -> Path:
    expanded = path.expanduser()
    if expanded.name in PLATFORM_SUBDIRS:
        return expanded.parent
    return expanded


def _looks_like_scripts_dir(path: Path) -> bool:
    normalized = _normalize_scripts_dir(path)
    return normalized.is_dir() and any(
        candidate.exists()
        for candidate in (
            normalized / "wlctl.sh",
            normalized / "wlctl.ps1",
            normalized / "linux" / "wlctl.sh",
            normalized / "windows" / "wlctl.ps1",
        )
    )


def iter_candidate_script_dirs(anchor: Optional[Path] = None) -> Iterable[Path]:
    env_dir = os.getenv("WATCHLINK_V3_SCRIPTS_DIR", "").strip()
    if env_dir:
        yield _normalize_scripts_dir(Path(env_dir))

    scripts_root = Path(__file__).resolve().parents[1]
    yield scripts_root

    for root in _iter_anchor_roots(anchor):
        yield root / LAYOUT_RELATIVE_SCRIPTS
        yield root / "skills" / "device-watchlink-v3" / "scripts"
        yield root / "skills" / "device" / "watchlink-v3" / "scripts"
        yield root / "watchlink-v3" / "scripts"

    yield Path.home() / LAYOUT_RELATIVE_SCRIPTS


def find_watchlink_scripts_dir(anchor: Optional[Path] = None) -> Path:
    checked = []
    for candidate in _dedupe_paths(iter_candidate_script_dirs(anchor=anchor)):
        normalized = _normalize_scripts_dir(candidate)
        checked.append(str(normalized))
        if _looks_like_scripts_dir(normalized):
            return normalized
    raise FileNotFoundError(
        "Unable to locate watchlink-v3 scripts directory. Checked: "
        + ", ".join(checked)
    )


def build_wlctl_path(anchor: Optional[Path] = None, prefer_windows: Optional[bool] = None) -> Path:
    scripts_dir = find_watchlink_scripts_dir(anchor=anchor)
    use_windows = sys.platform == "win32" if prefer_windows is None else prefer_windows
    candidates = (
        [
            scripts_dir / "windows" / "wlctl.ps1",
            scripts_dir / "linux" / "wlctl.sh",
            scripts_dir / "wlctl.ps1",
            scripts_dir / "wlctl.sh",
        ]
        if use_windows
        else [
            scripts_dir / "linux" / "wlctl.sh",
            scripts_dir / "windows" / "wlctl.ps1",
            scripts_dir / "wlctl.sh",
            scripts_dir / "wlctl.ps1",
        ]
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"wlctl entrypoint not found under {scripts_dir}")


def ensure_wlctl_sdk(anchor: Optional[Path] = None) -> Path:
    scripts_dir = find_watchlink_scripts_dir(anchor=anchor)
    scripts_text = str(scripts_dir)
    if scripts_text not in sys.path:
        sys.path.insert(0, scripts_text)
    return scripts_dir
