from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence


IS_WIN32 = sys.platform == "win32"


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


def resolve_gomore_root(path: Path) -> Optional[Path]:
    expanded = path.expanduser()
    candidates = [
        expanded,
        expanded / "storage" / "sport" / "gomore",
        expanded / "sport" / "gomore",
        expanded / "gomore",
    ]
    for candidate in candidates:
        if candidate.is_dir() and (
            (candidate / "data_sample").exists()
            or candidate.name.lower() == "gomore"
            or (candidate / "gomore_his.data").exists()
            or (candidate / "lactateData.data").exists()
        ):
            return candidate
    return None


def select_data_root(roots: Sequence[Path]) -> Optional[Path]:
    roots = _dedupe_paths(Path(root) for root in roots)
    if not roots:
        return None

    gomore_matches = _dedupe_paths(
        match for match in (resolve_gomore_root(root) for root in roots) if match
    )
    if gomore_matches:
        return gomore_matches[0]

    storage_roots = [root for root in roots if (root / "storage").exists()]
    if storage_roots:
        return storage_roots[0]

    sport_roots = [root for root in roots if (root / "sport").exists()]
    if sport_roots:
        return sport_roots[0]

    return roots[0]


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
            root = resolve_gomore_root(Path(explicit_root).expanduser())
            if root:
                return root
        else:
            matches = [
                root for root in (resolve_gomore_root(path) for path in provider())
                if root and str(root) not in excluded
            ]
            unique = _dedupe_paths(matches)
            last_candidates = unique
            if len(unique) == 1:
                return unique[0]
            if len(unique) > 1:
                joined = "\n".join(f"  - {path}" for path in unique)
                raise RuntimeError(
                    f"Multiple mounted gomore roots detected, please pass --disk-root:\n{joined}"
                )
        time.sleep(1.0)

    if explicit_root:
        raise RuntimeError(f"Unable to find gomore root under --disk-root={explicit_root}")
    searched = "\n".join(f"  - {path}" for path in provider()) or "  (no mount roots found)"
    found = "\n".join(f"  - {path}" for path in last_candidates) or "  (no gomore roots found)"
    raise RuntimeError(
        "Unable to auto-detect mounted gomore root.\n"
        f"Searched mount roots:\n{searched}\n"
        f"Detected gomore roots:\n{found}"
    )


def _format_text(path: Optional[Path]) -> str:
    return f"{path}\n" if path else ""


def _format_json(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Watch-Link disk mount helpers")
    parser.add_argument(
        "action",
        choices=("detect-roots", "resolve-gomore-root", "select-data-root", "select-log-root", "wait-for-disk-root"),
    )
    parser.add_argument("--path", default="", help="Path used by resolve-gomore-root")
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

    if args.action == "resolve-gomore-root":
        path = resolve_gomore_root(Path(args.path))
        print(_format_json({"gomore_root": str(path) if path else ""}) if args.output == "json" else _format_text(path), end="")
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
