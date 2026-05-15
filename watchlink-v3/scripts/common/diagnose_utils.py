from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional


_UNC_PATH_RE = re.compile(r"unc paths are not supported", re.IGNORECASE)
_PORT_DENIED_RE = re.compile(r"access to the port 'com\d+' is denied", re.IGNORECASE)
_PORT_CLOSED_RE = re.compile(
    r"the port is closed|vendor mgr command failed|device deployment failed after retries",
    re.IGNORECASE,
)
_IMG_DISK_NOT_FOUND_RE = re.compile(r"no img disk found", re.IGNORECASE)
_IMG_DISK_TIMEOUT_RE = re.compile(r"waiting for img log disk to appear", re.IGNORECASE)
_LOG_FILE_NOT_FOUND_RE = re.compile(r"日志文件不存在|log file .* not found", re.IGNORECASE)


def is_wsl_environment() -> bool:
    if sys.platform != "linux":
        return False
    try:
        version_text = Path("/proc/version").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "microsoft" in version_text.lower() or "wsl" in version_text.lower()


def collect_runtime_diagnostics(error_text: str) -> List[Dict[str, str]]:
    text = (error_text or "").strip()
    if not text:
        return []

    lower = text.lower()
    diagnostics: List[Dict[str, str]] = []

    def add(code: str, message: str) -> None:
        for item in diagnostics:
            if item["code"] == code:
                return
        diagnostics.append({"code": code, "message": message})

    if (
        "wsl windows interop is unavailable" in lower
        or "powershell.exe timed out" in lower
        or "cmd.exe timed out" in lower
        or _UNC_PATH_RE.search(text)
    ):
        add(
            "E_WSL_BRIDGE",
            "WSL->Windows bridge 失败。先在 Windows 终端执行 `wsl --shutdown`，"
            "重开 WSL 后验证 `powershell.exe -NoProfile -Command \"Write-Output ok\"` "
            "和 `cmd.exe /c echo ok` 可立即返回，再重试。",
        )

    if _PORT_DENIED_RE.search(text):
        add(
            "E_SERIAL_PORT_DENIED",
            "COM 口看起来正被 Windows 串口工具占用。先关闭 sscom、串口助手、"
            "SecureCRT、MobaXterm 等外部串口 GUI，再重试，不要先改端口配对或 bridge 逻辑。",
        )

    if _PORT_CLOSED_RE.search(text):
        add(
            "E_SERIAL_PORT_CLOSED",
            "MGR 通道像是未恢复或端口状态已经失效。先做 watchlink quick_check，"
            "再单独验证 `WL+FWVER=?`；如果只是想收敛问题范围，优先用 `--deploy-only`。",
        )

    if (
        "unable to detect mounted windows gomore root" in lower
        or "multiple mounted gomore roots detected" in lower
        or "unable to auto-detect mounted gomore root" in lower
    ):
        add(
            "E_DATA_ROOT_DETECT",
            "data-root 挂载/识别失败。先确认 Windows 侧是否真的出现 `<盘符>:\\sport\\gomore`，"
            "WSL 下优先省略 `--disk-root`，不要再回到 `/mnt/f` 这类 legacy 路径。",
        )

    if "vbus is off" in lower or ("wl+vbus" in lower and "off" in lower):
        add(
            "E_VBUS_OFF",
            "VBUS 处于关闭状态，MGR/UART 串口无法通讯。请先发送 `WL+VBUS=ON` 开启 VBUS 电源。"
            "设备重新插拔 USB 后也可能自动恢复。",
        )

    if "unable to communicate with any candidate uart port" in lower:
        add(
            "E_UART_PORT_PROBE",
            "UART 探测没有命中有效端口。先确认当前 WLINK 的 UART/MGR 配对，"
            "然后显式传 `--uart-port` / `--mgr-port`，不要继续轮询旧的 COM 列表。",
        )

    if "start marker not found" in lower:
        add(
            "E_UTEST_START_MISSING",
            "utest 可能没有真正启动。确认设备上 `/storage/sport/gomore/data_sample/` 已有目标 CSV，"
            "并且 `gomore_his.data` / `lactateData.data` 已清理。",
        )

    if "timeout waiting for test end" in lower or "test_timeout" in lower:
        add(
            "E_UTEST_TIMEOUT",
            "utest 在预期窗口内没有完成。先看最近 UART 尾部日志，再决定是延长超时还是回到 data/root 与 ready 阶段排查。",
        )

    if "device did not reach launcher-ready or shell-ready state within timeout" in lower:
        add(
            "E_DEVICE_READY_TIMEOUT",
            "先回看 `UART shell probe` 记录；如果 deploy 已完成但设备只刷系统日志，"
            "优先确认 post-unmount reset 输出、MGR/UART 端口配对，以及最近是否有外部串口工具占用 COM 口。",
        )

    if "uart shell did not stabilize after ready" in lower:
        add(
            "E_UART_SHELL_UNSTABLE",
            "设备虽然已经 ready，但 shell 提示符仍未稳定。优先回看 post-ready 的 `power -s on` / `monkey -g` 输出，"
            "确认屏幕常亮命令已送达，再决定是否延后 utest 注入。",
        )

    if _IMG_DISK_NOT_FOUND_RE.search(text):
        add(
            "E_IMG_DISK_NOT_FOUND",
            "未识别到 IMG 日志盘。确认设备当前确实进入 IMG 模式，或在已挂载场景下显式传 `--mount-root`。",
        )

    if _IMG_DISK_TIMEOUT_RE.search(text):
        add(
            "E_IMG_DISK_TIMEOUT",
            "IMG 日志盘在等待窗口内没有出现。优先检查 MGR 通道、挂盘序列是否真的切到了 `WL+DISK=IMG`，再考虑延长挂载等待时间。",
        )

    if _LOG_FILE_NOT_FOUND_RE.search(text):
        add(
            "E_LOG_FILE_NOT_FOUND",
            "本地日志文件不存在。先确认 `--log-file` 路径是否正确，必要时改用 IMG 挂盘提取流程重新生成日志。",
        )

    return diagnostics


def get_runtime_codes(error_text: str) -> List[str]:
    return [item["code"] for item in collect_runtime_diagnostics(error_text)]


def get_primary_runtime_code(error_text: str) -> str:
    diagnostics = collect_runtime_diagnostics(error_text)
    return diagnostics[0]["code"] if diagnostics else ""


def build_runtime_diagnostic_payload(error_text: str) -> Dict[str, object]:
    diagnostics = collect_runtime_diagnostics(error_text)
    if not diagnostics:
        return {
            "primary_error_code": "",
            "error_codes": [],
            "runtime_hint": "",
            "diagnostics": [],
        }
    return {
        "primary_error_code": diagnostics[0]["code"],
        "error_codes": [item["code"] for item in diagnostics],
        "runtime_hint": "\n".join(
            f"[HINT][{item['code']}] {item['message']}" for item in diagnostics
        ),
        "diagnostics": diagnostics,
    }


def build_runtime_hint(error_text: str) -> str:
    return str(build_runtime_diagnostic_payload(error_text).get("runtime_hint", ""))


def append_runtime_hint(message: str) -> str:
    hint = build_runtime_hint(message)
    if not hint or hint in message:
        return message
    return f"{message}\n{hint}"


def collect_environment_snapshot() -> Dict[str, object]:
    return {
        "platform": sys.platform,
        "python": sys.version.split()[0],
        "cwd": os.getcwd(),
        "user": os.getenv("USER") or os.getenv("USERNAME") or "",
        "is_wsl": is_wsl_environment(),
        "powershell_available": bool(shutil.which("powershell.exe") or shutil.which("powershell") or shutil.which("pwsh")),
        "cmd_available": bool(shutil.which("cmd.exe") or shutil.which("cmd")),
    }


def diagnose_runtime(error_text: str = "") -> Dict[str, object]:
    payload = build_runtime_diagnostic_payload(error_text)
    payload["input_error_text"] = error_text
    payload["environment"] = collect_environment_snapshot()
    return payload


def format_diagnostic_text(payload: Dict[str, object]) -> str:
    lines = ["Watch-Link Runtime Diagnose", ""]
    primary = str(payload.get("primary_error_code") or "")
    codes = payload.get("error_codes") or []
    hint = str(payload.get("runtime_hint") or "")
    env = payload.get("environment") or {}
    source = str(payload.get("input_error_text") or "")

    if primary:
        lines.append(f"Primary Error Code: {primary}")
    if codes:
        lines.append("Error Codes: " + ", ".join(str(item) for item in codes))
    if hint:
        lines.extend(["", hint])
    else:
        lines.extend(["", "[INFO] No runtime hint matched the current input."])

    lines.extend(
        [
            "",
            "Environment:",
            f"- platform: {env.get('platform', '')}",
            f"- python: {env.get('python', '')}",
            f"- is_wsl: {env.get('is_wsl', False)}",
            f"- powershell_available: {env.get('powershell_available', False)}",
            f"- cmd_available: {env.get('cmd_available', False)}",
        ]
    )
    if source:
        lines.extend(["", "Input Error Text:", source])
    return "\n".join(lines).rstrip() + "\n"


def _read_message(args: argparse.Namespace) -> str:
    if args.message:
        return str(args.message)
    if args.message_file:
        return Path(args.message_file).read_text(encoding="utf-8", errors="replace")
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return ""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Diagnose watchlink runtime failures")
    parser.add_argument("--message", default="", help="Error text to diagnose")
    parser.add_argument("--message-file", default="", help="Read error text from file")
    parser.add_argument("--output", choices=("json", "text"), default="text", help="Output format")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    payload = diagnose_runtime(_read_message(args).strip())
    if args.output == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(format_diagnostic_text(payload), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
