# watchlink-v3

Watch-Link V3 设备基础运行时，面向 Linux / WSL / Windows。

这个 skill 负责设备侧通用能力，不承载具体业务语义。典型能力包括：

- UART / MGR 串口命令路由
- monkey 启停与状态查询
- data / log 挂盘与卸盘
- VBUS 控制
- ready / awake 检测
- 设备挂载文件系统读写：`fs ls/read/pull/push/rm`
- 运行时诊断
- Python SDK 接入

上层工作流例如 `lthr-offline-simulator` 应复用这里的设备能力，而不是自己重复实现端口探测、挂盘、串口路由或 bridge 排障逻辑。

## 目录结构

```text
watchlink-v3/
├── SKILL.md
├── README.md
└── scripts/
    ├── common/      # 共享 Python SDK / discovery / fs / diagnose / disk helper
    ├── linux/       # Linux / WSL 入口
    ├── windows/     # Windows PowerShell 入口
    ├── tests/       # 单元测试
    ├── wlctl_sdk.py
    ├── watchlink_discovery.py
    ├── disk_utils.py
    └── diagnose_utils.py
```

## 主要入口

- Linux / WSL：`scripts/linux/wlctl.sh`
- Windows：`scripts/windows/wlctl.ps1`
- Python SDK：`scripts/common/wlctl_sdk.py`

## 常见命令示例

### 1. 发串口命令

```bash
./scripts/linux/wlctl.sh serial --role MGR --cmd "WL+FWVER=?"
./scripts/linux/wlctl.sh serial --role UART --cmd "monkey -g"
```

### 2. monkey 控制

```bash
./scripts/linux/wlctl.sh monkey on --interval-ms 1000 --print off --mem 0
./scripts/linux/wlctl.sh monkey status
./scripts/linux/wlctl.sh monkey off
```

### 3. 设备状态检查

```bash
./scripts/linux/wlctl.sh vbus status --output json
./scripts/linux/wlctl.sh ready --timeout 60 --output json
./scripts/linux/wlctl.sh awake --timeout 15 --output json
./scripts/linux/wlctl.sh diagnose --message "cmd.exe timed out" --output json
```

### 4. 挂盘与文件操作

```bash
./scripts/linux/wlctl.sh disk mount-data
./scripts/linux/wlctl.sh fs ls --disk data --path payloads --output json
./scripts/linux/wlctl.sh fs read --disk data --path payloads/sample.txt
./scripts/linux/wlctl.sh fs pull --disk log --from-path "**/*.log" --to-path ./img_logs
./scripts/linux/wlctl.sh fs push --disk data --from-path ./local_payloads --to-path payloads/
./scripts/linux/wlctl.sh fs rm --disk log --path logs --recursive
```

## Python SDK 示例

```python
from watchlink_discovery import ensure_wlctl_sdk

ensure_wlctl_sdk()

from wlctl_sdk import WatchlinkDevice

dev = WatchlinkDevice(mgr_port="COM37", uart_port="COM38")
dev.ensure_vbus_on()
dev.wait_ready(timeout=60)
dev.send_uart_cmd("monkey -g")
content = dev.read_file("data", "payloads/sample.txt")
```

## 团队安装（WSL 推荐）

为了避免每次挂盘都依赖手工 `sudo -v`，仓库内提供了受控 helper 安装脚本：

```bash
./scripts/linux/install_watchlink_v3_sudo.sh
```

它会安装：

- `/usr/local/sbin/watchlink-v3-mount-helper`
- `/etc/sudoers.d/99-watchlink-v3-v3dl-com`

如果只想安装 `watchlink-v3` 自己的挂盘 helper，不想带 `v3dl com` 的兼容规则：

```bash
./scripts/linux/install_watchlink_v3_sudo.sh --watchlink-only
```

### 为什么这样做

不直接对白名单放开 `/usr/bin/rm`、`/usr/bin/mount`、`/usr/bin/chown`，而是通过一个受控 helper 收窄 root 权限面：

- 只允许处理 `/mnt/<盘符>`
- 只允许 `drvfs` 挂载
- 只允许固定格式 owner

这样比直接开放通用系统命令更适合团队复用。

## 依赖

### 必需依赖

- `Python 3.8+`
  - 用途：运行 `scripts/common/` 下的 SDK、诊断、文件系统 helper，以及测试。
- `v3dl`
  - 用途：Linux / WSL 某些端口自动发现路径仍会使用 `v3dl com`。
  - 影响：如果没有 `v3dl`，串口自动发现能力会变弱，需要更频繁地手工传 `--port`。

### 平台依赖

- `powershell.exe` / `cmd.exe`
  - 场景：仅 WSL bridge 需要。
  - 用途：调用 Windows 侧串口和挂盘逻辑。
- Windows PowerShell
  - 场景：仅 Windows 原生入口 `scripts/windows/wlctl.ps1` 需要。

### 可选但强烈建议

- `scripts/linux/install_watchlink_v3_sudo.sh`
  - 用途：为 WSL 挂盘安装受控 helper 和最小 sudo 规则。
  - 影响：不安装也能靠 `sudo -v` 临时使用，但自动化稳定性会差很多。

## 验证

运行完整测试：

```bash
cd scripts/tests
python3 -m unittest discover -s . -v
```

补充检查：

```bash
bash -n scripts/linux/wlctl.sh
bash -n scripts/linux/install_watchlink_v3_sudo.sh
bash -n scripts/linux/watchlink-v3-mount-helper
```

## 说明

- `SKILL.md` 面向 agent 路由和触发词。
- `README.md` 面向 GitHub / 团队成员快速理解与上手。
- 业务层不要把 `data_sample/`、GoMore 特定文件名、LTHR 解析规则继续下沉到这里。
