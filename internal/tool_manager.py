"""Workspace-local CTF tool catalog and installer."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class InstallCancelled(RuntimeError):
    """Raised internally when the user cancels a tool installation."""


class ToolBusyError(RuntimeError):
    """Raised when a tool operation cannot run alongside an installation."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    label: str
    package: str
    description: str
    kind: str = "python"
    command: str = ""
    homepage: str = ""
    install_url: str = ""
    category: str = ""
    subgroup: str = ""
    packages: tuple[str, ...] = ()
    platforms: tuple[str, ...] = ("linux", "macos", "windows")
    support_note: str = ""

    def to_dict(self, installed: bool, error: str = "") -> dict[str, Any]:
        category, category_label = _tool_category(self)
        supported = _tool_supported(self)
        return {
            "name": self.name,
            "label": self.label,
            "package": self.package,
            "description": self.description,
            "kind": self.kind,
            "homepage": self.homepage,
            "installUrl": self.install_url,
            "category": category,
            "categoryLabel": category_label,
            "subgroup": self.subgroup,
            "packages": list(self.packages),
            "platforms": [_PLATFORM_LABELS[item] for item in self.platforms],
            "supported": supported,
            "supportNote": self.support_note,
            "execution": "source-only" if self.kind == "github-source" else "runnable",
            "installed": installed,
            "error": error,
        }


TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "basic-libraries",
        "基础库",
        "",
        "CTF 通用 Python 基础库：requests、gmpy2、PyCryptodome；json、base64、hashlib、re、struct、urllib 等标准库无需额外安装。",
        kind="python-bundle",
        homepage="https://pypi.org/",
        install_url="https://pypi.org/simple/",
        category="python-library",
        packages=("requests", "gmpy2", "pycryptodome"),
    ),
    ToolSpec("sqlmap", "sqlmap", "sqlmap", "自动化 SQL 注入检测与利用", command="sqlmap", homepage="https://sqlmap.org/", install_url="https://pypi.org/project/sqlmap/"),
    ToolSpec("dirsearch", "dirsearch", "dirsearch", "Web 路径和文件枚举", command="dirsearch", homepage="https://github.com/maurosardara/dirsearch", install_url="https://pypi.org/project/dirsearch/"),
    ToolSpec("ffuf", "ffuf", "github.com/ffuf/ffuf/v2", "高速 Web 模糊测试", "go", "ffuf", "https://github.com/ffuf/ffuf", "https://github.com/ffuf/ffuf"),
    ToolSpec("z3", "Z3 Solver", "z3-solver", "约束求解与符号推理", homepage="https://github.com/Z3Prover/z3", install_url="https://pypi.org/project/z3-solver/", subgroup="专项库"),
    ToolSpec("pwntools", "pwntools", "pwntools", "Pwn 脚本与远程交互框架", command="pwn", homepage="https://github.com/Gallopsled/pwntools", install_url="https://pypi.org/project/pwntools/", platforms=("linux", "macos"), support_note="Windows 请在 WSL 中使用"),
    ToolSpec("binwalk", "binwalk", "binwalk", "固件和嵌套文件分析", command="binwalk", homepage="https://github.com/ReFirmLabs/binwalk", install_url="https://pypi.org/project/binwalk/", platforms=("linux", "macos"), support_note="原生 Windows 不受上游支持"),
    ToolSpec("sympy", "SymPy", "sympy", "符号数学与数论计算", homepage="https://www.sympy.org/", install_url="https://pypi.org/project/sympy/", subgroup="专项库"),
    ToolSpec("scapy", "Scapy", "scapy", "网络数据包构造与分析", command="scapy", homepage="https://github.com/secdev/scapy", install_url="https://pypi.org/project/scapy/"),
    ToolSpec("json", "json（标准库）", "json", "Python 内置 JSON 编解码库，无需额外安装", kind="builtin", homepage="https://docs.python.org/3/library/json.html", install_url="", category="python-library", subgroup="基础库"),
    ToolSpec("base64", "base64（标准库）", "base64", "常用 Base16、Base32、Base64 编解码库，无需额外安装", kind="builtin", homepage="https://docs.python.org/3/library/base64.html", category="python-library", subgroup="基础库"),
    ToolSpec("hashlib", "hashlib（标准库）", "hashlib", "MD5、SHA 等摘要算法库，无需额外安装", kind="builtin", homepage="https://docs.python.org/3/library/hashlib.html", category="python-library", subgroup="基础库"),
    ToolSpec("re", "re（标准库）", "re", "正则表达式匹配库，无需额外安装", kind="builtin", homepage="https://docs.python.org/3/library/re.html", category="python-library", subgroup="基础库"),
    ToolSpec("struct", "struct（标准库）", "struct", "二进制数据打包与解析库，无需额外安装", kind="builtin", homepage="https://docs.python.org/3/library/struct.html", category="python-library", subgroup="基础库"),
    ToolSpec("urllib", "urllib（标准库）", "urllib", "URL 解析与基础 HTTP 客户端库，无需额外安装", kind="builtin", homepage="https://docs.python.org/3/library/urllib.html", category="python-library", subgroup="基础库"),
    ToolSpec("pyelftools", "pyelftools", "pyelftools", "ELF 文件解析与逆向辅助", homepage="https://github.com/eliben/pyelftools", install_url="https://pypi.org/project/pyelftools/"),
    ToolSpec("capstone", "Capstone", "capstone", "多架构反汇编引擎", homepage="https://github.com/capstone-engine/capstone", install_url="https://pypi.org/project/capstone/"),
    ToolSpec("unicorn", "Unicorn", "unicorn", "多架构 CPU 仿真引擎", homepage="https://github.com/unicorn-engine/unicorn", install_url="https://pypi.org/project/unicorn/"),
    ToolSpec("angr", "angr", "angr", "二进制分析与符号执行框架", homepage="https://github.com/angr/angr", install_url="https://pypi.org/project/angr/"),
    ToolSpec("qiling", "Qiling", "qiling", "跨平台二进制仿真框架", homepage="https://github.com/qilingframework/qiling", install_url="https://pypi.org/project/qiling/"),
    ToolSpec("frida-tools", "Frida Tools", "frida-tools", "动态插桩与移动端分析工具", command="frida", homepage="https://github.com/frida/frida-tools", install_url="https://pypi.org/project/frida-tools/"),
    ToolSpec("volatility3", "Volatility 3", "volatility3", "内存取证分析框架", command="vol", homepage="https://github.com/volatilityfoundation/volatility3", install_url="https://pypi.org/project/volatility3/"),
    ToolSpec("yara-python", "YARA Python", "yara-python", "恶意样本规则匹配", homepage="https://github.com/VirusTotal/yara-python", install_url="https://pypi.org/project/yara-python/"),
    ToolSpec("pefile", "pefile", "pefile", "Windows PE 文件解析", homepage="https://github.com/erocarrera/pefile", install_url="https://pypi.org/project/pefile/"),
    ToolSpec("ropper", "Ropper", "ropper", "ROP gadget 搜索与二进制分析", command="ropper", homepage="https://github.com/sashs/Ropper", install_url="https://pypi.org/project/ropper/", support_note="上游 filebytes 目前不兼容 Python 3.14；请使用 Python 3.12/3.13 的工作区虚拟环境"),
    ToolSpec("ropgadget", "ROPgadget", "ROPGadget", "ROP/JOP gadget 搜索", command="ROPgadget", homepage="https://github.com/JonathanSalwan/ROPgadget", install_url="https://pypi.org/project/ROPGadget/"),
    ToolSpec("impacket", "Impacket", "impacket", "Windows 协议与网络渗透工具集", command="smbclient.py", homepage="https://github.com/fortra/impacket", install_url="https://pypi.org/project/impacket/", subgroup="专项库"),
    ToolSpec("gobuster", "Gobuster", "github.com/OJ/gobuster/v3", "目录、DNS 和虚拟主机枚举", "go", "gobuster", "https://github.com/OJ/gobuster", "https://github.com/OJ/gobuster"),
    ToolSpec("jadx", "jadx", "skylot/jadx", "Android DEX/APK 反编译", "jadx", "jadx", "https://github.com/skylot/jadx", "https://github.com/skylot/jadx/releases"),
    ToolSpec("exiftool", "ExifTool", "exiftool", "图片和文件元数据读取", "exiftool", "exiftool", "https://exiftool.org/", "https://github.com/exiftool/exiftool"),
    ToolSpec("checksec", "checksec", "checksec", "检查 ELF、Mach-O 和 PE 文件的安全保护属性", kind="github-script", command="checksec", homepage="https://github.com/slimm609/checksec.sh", install_url="https://raw.githubusercontent.com/slimm609/checksec.sh/master/checksec", platforms=("linux", "macos"), support_note="Shell 工具，不支持原生 Windows"),
    ToolSpec("apktool", "Apktool", "apktool", "Android APK 反编译、资源解析和重打包", kind="github-release", command="apktool", homepage="https://github.com/iBotPeaches/Apktool", install_url="https://github.com/iBotPeaches/Apktool/releases"),
    ToolSpec("7z", "7-Zip", "7z", "常用压缩包解压和归档工具（当前安装为源码副本）", kind="github-source", command="7z", homepage="https://github.com/ip7z/7zip", install_url="https://github.com/ip7z/7zip/releases", support_note="当前仅获取源码，不自动编译；Windows 可在 tools/src/7z 中使用 Visual Studio 构建"),
    ToolSpec("foremost", "foremost", "foremost", "基于文件头和尾部特征的数据雕刻恢复工具（当前安装为源码副本）", kind="github-source", command="foremost", homepage="https://github.com/korczis/foremost", install_url="https://github.com/korczis/foremost", platforms=("linux", "macos"), support_note="当前仅获取源码，不自动编译；原生 Windows 不受上游支持"),
    ToolSpec("john", "John the Ripper", "john", "密码哈希审计和口令恢复工具（当前安装为源码副本）", kind="github-source", command="john", homepage="https://github.com/openwall/john", install_url="https://github.com/openwall/john", support_note="当前仅获取源码，不自动编译；Windows 可在 tools/src/john 中按上游说明构建"),
    ToolSpec("hashcat", "Hashcat", "hashcat", "GPU/CPU 密码哈希恢复工具（当前安装为源码副本）", kind="github-source", command="hashcat", homepage="https://github.com/hashcat/hashcat", install_url="https://github.com/hashcat/hashcat", support_note="当前仅获取源码，不自动编译；Windows 可在 tools/src/hashcat 中按上游说明构建"),
    ToolSpec("gdb", "GDB", "gdb", "GNU 调试器源码副本，安装后需自行构建", kind="github-source", command="gdb", homepage="https://github.com/bminor/binutils-gdb", install_url="https://github.com/bminor/binutils-gdb", platforms=("linux", "windows"), support_note="当前仅获取源码，不自动编译；macOS 需要额外代码签名，本工作台暂不自动安装"),
    ToolSpec("pwndbg", "pwndbg", "pwndbg", "GDB 的二进制漏洞利用调试增强插件源码副本", kind="github-source", command="pwndbg", homepage="https://github.com/pwndbg/pwndbg", install_url="https://github.com/pwndbg/pwndbg", platforms=("linux",), support_note="当前仅获取源码，不自动安装；Windows 请在 WSL 中使用，并先准备 GDB"),
)


_CATEGORY_LABELS: dict[str, str] = {
    "python-library": "Python 库",
    "go-project": "Go 开源项目",
    "binary-package": "独立工具 / 发行包",
}
_PLATFORM_LABELS: dict[str, str] = {
    "linux": "Linux",
    "macos": "macOS",
    "windows": "Windows",
}

_PYTHON_SOURCES: tuple[str, ...] = (
    "https://pypi.org/simple/",
    "https://pypi.tuna.tsinghua.edu.cn/simple/",
    "https://mirrors.aliyun.com/pypi/simple/",
    "https://mirrors.cloud.tencent.com/pypi/simple/",
    "https://pypi.mirrors.ustc.edu.cn/simple/",
)
_GO_SOURCES: tuple[str, ...] = (
    "https://proxy.golang.org",
    "https://goproxy.cn",
    "https://mirrors.aliyun.com/goproxy/",
)
_TRANSFER_SPEED_WINDOW_SECONDS = 2.0

_GITHUB_MIRRORS: tuple[str, ...] = (
    "https://ghfast.top/",
    "https://gh-proxy.com/",
    "https://ghproxy.net/",
)


def _tool_category(spec: ToolSpec) -> tuple[str, str]:
    if spec.category:
        return spec.category, _CATEGORY_LABELS.get(spec.category, "其他工具")
    if spec.kind == "python":
        return "python-library", _CATEGORY_LABELS["python-library"]
    if spec.kind == "go":
        return "go-project", _CATEGORY_LABELS["go-project"]
    return "binary-package", _CATEGORY_LABELS["binary-package"]


class ToolManager:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.root = self.workspace / "tools"
        self.manifest_path = self.root / "installed-tools.txt"
        self._migrate_legacy_root()
        self._repair_legacy_paths()
        self.venv = self.root / "venv"
        self._lock = threading.RLock()
        self._status = "idle"
        self._current = ""
        self._completed = 0
        self._total = 0
        self._error = ""
        self._errors: dict[str, str] = {}
        self._cancel_event = threading.Event()
        self._started_at = 0.0
        self._current_started_at = 0.0
        self._current_bytes = 0
        self._current_total = 0
        self._current_speed = 0.0
        self._transfer_samples: deque[tuple[float, int]] = deque()
        self._phase = ""
        self._message = ""
        self._python_index_url = ""
        self._go_proxy_url = ""
        self._python_source_tested = False
        self._go_source_tested = False
        self._github_source_url = ""
        self._github_source_tested = False
        self._sync_venv_launchers()
        # Rebuild a stale/missing manifest when a workspace already contains
        # installed tools (for example after upgrading this application).
        if any(self._installed(spec) for spec in TOOL_SPECS):
            self._write_manifest()

    def catalog(self) -> dict[str, Any]:
        with self._lock:
            tool_rows = []
            for spec in TOOL_SPECS:
                error = self._errors.get(spec.name, "")
                # A failed operation is shown as failed until the user
                # retries it, even if a command was left behind partially.
                tool_rows.append(spec.to_dict(self._installed(spec) and not error, error))
            return {
                "tools": tool_rows,
                "root": str(self.root.relative_to(self.workspace)),
                "manifest": str(self.manifest_path.relative_to(self.workspace)),
                "sources": {
                    "python": self._python_index_url,
                    "go": self._go_proxy_url,
                    "github": self._github_source_url,
                },
                "status": self._status,
                "current": self._current,
                "completed": self._completed,
                "total": self._total,
                "error": self._error,
                "progress": self._progress_snapshot(),
                "installedCount": sum(bool(row["installed"]) for row in tool_rows),
                "toolCount": len(TOOL_SPECS),
                "currentPlatform": _PLATFORM_LABELS[_current_platform()],
            }

    def _progress_snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        elapsed = (
            max(0.0, now - self._current_started_at)
            if self._current_started_at > 0
            else 0.0
        )
        speed = self._current_speed
        if not self._transfer_samples or now - self._transfer_samples[-1][0] > _TRANSFER_SPEED_WINDOW_SECONDS:
            speed = 0.0
        remaining = self._current_total - self._current_bytes
        eta = remaining / speed if speed > 0 and self._current_total > 0 else 0.0
        return {
            "current": self._current,
            "completed": self._completed,
            "total": self._total,
            "currentBytes": self._current_bytes,
            "currentTotal": self._current_total,
            "speed": speed,
            "eta": eta,
            "phase": self._phase,
            "message": self._message,
            "elapsed": elapsed,
            "indeterminate": self._current_total <= 0,
            "cancelable": self._status in {"running", "cancelling"},
        }

    def _migrate_legacy_root(self) -> None:
        """Move the former hidden directory to the visible workspace path."""
        legacy = self.workspace / ".tools"
        if self.root.exists() or not legacy.exists():
            return
        try:
            legacy.rename(self.root)
        except OSError:
            # A cross-device or partially populated move should not prevent
            # startup; copy the tree while preserving the legacy directory.
            try:
                shutil.copytree(legacy, self.root, dirs_exist_ok=True)
            except OSError:
                return

    def _sync_venv_launchers(self) -> None:
        """Expose installed Python console scripts under ``tools/bin``."""
        for spec in TOOL_SPECS:
            if spec.kind == "python" and spec.command and self._venv_command(spec.command).is_file():
                self._write_venv_launcher(spec.command)

    def _venv_bin(self) -> Path:
        return self.venv / ("Scripts" if os.name == "nt" else "bin")

    def _venv_command(self, name: str) -> Path:
        suffix = ".exe" if os.name == "nt" and not name.endswith(".py") else ""
        return self._venv_bin() / f"{name}{suffix}"

    def _launcher_path(self, name: str) -> Path:
        suffix = ".cmd" if os.name == "nt" else ""
        return self.root / "bin" / f"{name}{suffix}"

    def _repair_legacy_paths(self) -> None:
        """Repair venv script shebangs after moving ``.tools`` to ``tools``."""
        old_root = (self.workspace / ".tools").as_posix()
        new_root = self.root.as_posix()
        if not self.root.is_dir() or old_root == new_root:
            return
        for path in self.root.rglob("*"):
            try:
                is_candidate = path.is_file() and path.stat().st_size <= 2 * 1024 * 1024
            except OSError:
                is_candidate = False
            if not is_candidate:
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if old_root not in content:
                continue
            try:
                path.write_text(content.replace(old_root, new_root), encoding="utf-8")
            except OSError:
                continue

    def install(self, names: list[str] | None = None, all_tools: bool = False) -> dict[str, Any]:
        with self._lock:
            if self._status in {"running", "cancelling", "uninstalling"}:
                return self.catalog()
            candidates = (
                [spec for spec in TOOL_SPECS if _tool_supported(spec)]
                if all_tools
                else [spec for spec in TOOL_SPECS if spec.name in (names or [])]
            )
            if not candidates:
                raise ValueError("至少选择一个工具")
            unsupported = [spec.label for spec in candidates if not _tool_supported(spec)]
            if unsupported:
                raise ValueError(f"当前系统不支持安装：{', '.join(unsupported)}")
            selected = [spec for spec in candidates if not self._installed(spec)]
            if not selected:
                return self.catalog()
            self.root.mkdir(parents=True, exist_ok=True)
            self._status, self._current, self._completed, self._total, self._error = "running", "", 0, len(selected), ""
            self._errors = {}
            self._cancel_event.clear()
            self._started_at = time.monotonic()
            self._current_started_at = self._started_at
            self._current_bytes = 0
            self._current_total = 0
            self._reset_transfer_speed_locked()
            self._phase = "排队"
            self._message = "等待安装任务开始"
        threading.Thread(target=self._install_worker, args=(selected,), daemon=True).start()
        return self.catalog()

    def cancel(self) -> dict[str, Any]:
        with self._lock:
            if self._status == "running":
                self._status = "cancelling"
                self._cancel_event.set()
            return self.catalog()

    def uninstall(self, name: str) -> dict[str, Any]:
        """Remove one catalogued tool from the workspace-local ``tools`` root.

        Uninstall never invokes a system package manager and never removes the
        workspace virtual environment.  Python packages are removed through
        the workspace venv's pip; native/Go distributions are removed by
        deleting only their known launcher and installation directory.
        """
        normalized = name.strip()
        spec = next((item for item in TOOL_SPECS if item.name == normalized), None)
        if spec is None:
            raise ValueError("未知工具")

        with self._lock:
            if self._status in {"running", "cancelling", "uninstalling"}:
                raise ToolBusyError("安装任务正在运行，请稍后再试")
            self._status = "uninstalling"
            self._current = spec.name
            self._current_bytes = 0
            self._current_total = 0
            self._reset_transfer_speed_locked()
            self._phase = "卸载"
            self._message = f"正在卸载 {spec.label}"
            self._error = ""
            self._errors.pop(spec.name, None)
            self._cancel_event.clear()

        try:
            if spec.kind in {"python", "python-bundle"}:
                self._uninstall_python(spec)
            else:
                self._uninstall_external(spec)
        except Exception as exc:
            with self._lock:
                self._status = "failed"
                self._error = str(exc) or type(exc).__name__
                self._current = ""
                self._phase = ""
                self._message = ""
            raise
        with self._lock:
            self._status = "idle"
            self._current = ""
            self._phase = ""
            self._message = ""
        self._write_manifest()
        return self.catalog()

    def _uninstall_python(self, spec: ToolSpec) -> None:
        pip = self._venv_command("pip")
        if pip.is_file():
            root = self.root.resolve()
            resolved_pip = pip.resolve(strict=False)
            if resolved_pip == root or root not in resolved_pip.parents:
                raise OSError("拒绝执行 tools 目录之外的 pip")
            packages = spec.packages or ((spec.package,) if spec.package else ())
            if packages:
                self._run_command([str(pip), "uninstall", "-y", *packages], timeout=300)
        self._remove_launcher(spec)

    def _remove_launcher(self, spec: ToolSpec) -> None:
        if spec.command:
            for suffix in ("", ".cmd", ".exe"):
                self._remove_workspace_path(self.root / "bin" / f"{spec.command}{suffix}")

    def _uninstall_external(self, spec: ToolSpec) -> None:
        """Remove every artifact this tool installer can create.

        The launcher is deliberately kept separate from the virtualenv.  In
        particular, pwntools may provide ``tools/venv/bin/checksec``; that
        command is not owned by the standalone checksec card and is never
        removed here.
        """
        self._remove_launcher(spec)
        owned_paths: tuple[Path, ...] = ()
        if spec.kind == "jadx":
            owned_paths = (
                self.root / "jadx",
                self.root / "jadx.zip",
                self.root / ".jadx.zip.part",
            )
        elif spec.kind == "exiftool":
            owned_paths = (
                self.root / "exiftool",
                self.root / "exiftool.tar.gz",
                self.root / "exiftool.zip",
                self.root / ".exiftool.tar.gz.part",
                self.root / ".exiftool.zip.part",
            )
        elif spec.kind in {"github-release", "github-source"}:
            owned_paths = (
                self.root / "src" / spec.name,
                self.root / spec.name,
                self.root / f"{spec.name}.jar",
                self.root / f"{spec.name}.zip",
                self.root / f".{spec.name}.tar.gz.part",
                self.root / f".{spec.name}.zip.part",
                self.root / f".{spec.name}.jar.part",
            )
        elif spec.kind == "github-script":
            # Older versions briefly wrote the script beside the launcher;
            # clean that legacy path too, but never descend outside tools/.
            owned_paths = (
                self.root / spec.command,
                self.root / f"{spec.command}.sh",
                self.root / f".{spec.command}.part",
            )
        for path in owned_paths:
            self._remove_workspace_path(path)

    def _remove_workspace_path(self, path: Path) -> None:
        """Delete a path only when it resolves beneath this manager's root."""
        root = self.root.resolve()
        lexical = path.absolute()
        try:
            lexical.relative_to(root)
        except ValueError:
            raise OSError("拒绝删除 tools 目录之外的路径")
        # A symlink itself is an owned directory entry even when its target is
        # outside tools/.  Unlinking the link is safe and avoids treating a
        # manually-created launcher link as an attempt to delete external
        # data.
        if path.is_symlink():
            path.unlink(missing_ok=True)
            return
        candidate = path.resolve(strict=False)
        if candidate == root or root not in candidate.parents:
            raise OSError("拒绝删除 tools 目录之外的路径")
        if path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            shutil.rmtree(path)

    def _install_worker(self, selected: list[ToolSpec]) -> None:
        try:
            for spec in selected:
                with self._lock:
                    if self._cancel_event.is_set():
                        break
                    self._current = spec.name
                    self._current_started_at = time.monotonic()
                    self._current_bytes = 0
                    self._current_total = 0
                    self._reset_transfer_speed_locked()
                    self._phase = "准备"
                    self._message = f"准备安装 {spec.label}"
                try:
                    self._install_one(spec)
                except InstallCancelled:
                    break
                except Exception as exc:
                    with self._lock:
                        self._errors[spec.name] = _format_process_error(exc)
                finally:
                    with self._lock:
                        self._completed += 1
            with self._lock:
                self._status = (
                    "cancelled"
                    if self._cancel_event.is_set()
                    else "completed_with_errors"
                    if self._errors
                    else "completed"
                )
                self._error = "；".join(
                    f"{name}: {error}" for name, error in self._errors.items()
                )
                if self._cancel_event.is_set() and not self._error:
                    self._error = "用户已取消安装"
                self._current = ""
                self._current_bytes = 0
                self._current_total = 0
                self._reset_transfer_speed_locked()
                self._phase = ""
                self._message = ""
            # Keep the manifest useful even when a batch partially fails or
            # is cancelled: it reflects what is actually available now.
            self._write_manifest()
        except Exception as exc:
            with self._lock:
                self._status, self._error, self._current = "failed", _format_process_error(exc), ""
            self._write_manifest()

    def _install_one(self, spec: ToolSpec) -> None:
        if spec.kind == "builtin":
            self._set_progress("跳过", f"{spec.label} 已随 Python 提供")
            return
        if spec.kind in {"python", "python-bundle"}:
            packages = spec.packages or ((spec.package,) if spec.package else ())
            if not packages:
                raise OSError(f"{spec.label} 没有可安装的 Python 包")
            self._set_progress("下载", f"通过 pip 下载 {', '.join(packages)}")
            self._ensure_venv()
            self._run_command(
                [
                    str(self._venv_command("pip")),
                    "install",
                    "--disable-pip-version-check",
                    "--index-url",
                    self._select_python_source(),
                    *packages,
                ],
                timeout=900,
            )
            self._set_progress("安装", f"安装 Python 库 {', '.join(packages)}")
            if spec.command and self._venv_command(spec.command).is_file():
                self._write_venv_launcher(spec.command)
        elif spec.kind == "go":
            self._set_progress("下载", f"通过 Go 模块下载 {spec.label}")
            self._install_go(spec)
        elif spec.kind == "jadx":
            self._set_progress("下载", f"下载 {spec.label} 发行包")
            self._install_jadx()
        elif spec.kind == "exiftool":
            self._set_progress("下载", f"下载 {spec.label} 源码包")
            self._install_exiftool()
        elif spec.kind == "github-script":
            self._set_progress("下载", f"下载 {spec.label} 脚本")
            self._install_github_script(spec)
        elif spec.kind == "github-release":
            self._set_progress("下载", f"下载 {spec.label} 发布包")
            self._install_github_release(spec)
        elif spec.kind == "github-source":
            self._set_progress("获取", f"准备 {spec.label} 源码目录")
            self._install_github_source(spec)

    def _set_progress(self, phase: str, message: str) -> None:
        with self._lock:
            if phase != self._phase:
                self._reset_transfer_speed_locked(self._current_bytes)
            self._phase = phase
            self._message = message

    def _reset_transfer_speed_locked(self, current_bytes: int = 0) -> None:
        now = time.monotonic()
        self._current_speed = 0.0
        self._transfer_samples.clear()
        self._transfer_samples.append((now, max(0, current_bytes)))

    def _record_transfer_bytes_locked(self, current_bytes: int) -> None:
        now = time.monotonic()
        normalized = max(0, current_bytes)
        if normalized < self._current_bytes or not self._transfer_samples:
            self._current_bytes = normalized
            self._reset_transfer_speed_locked(normalized)
            return
        self._current_bytes = normalized
        self._transfer_samples.append((now, normalized))
        cutoff = now - _TRANSFER_SPEED_WINDOW_SECONDS
        while len(self._transfer_samples) > 2 and self._transfer_samples[1][0] <= cutoff:
            self._transfer_samples.popleft()
        started_at, started_bytes = self._transfer_samples[0]
        elapsed = now - started_at
        self._current_speed = (
            max(0, normalized - started_bytes) / elapsed if elapsed > 0 else 0.0
        )

    def _ensure_venv(self) -> None:
        if self._venv_command("python").is_file():
            return
        self.root.mkdir(parents=True, exist_ok=True)
        self._run_process(
            [sys.executable, "-m", "venv", str(self.venv)],
            self._local_environment(),
            timeout=120,
            progress_root=self.root / "tmp",
        )

    def _local_environment(self) -> dict[str, str]:
        env = dict(os.environ)
        env["PIP_CACHE_DIR"] = str(self.root / "cache" / "pip")
        env["XDG_CACHE_HOME"] = str(self.root / "cache")
        env["TMPDIR"] = str(self.root / "tmp")
        if os.name == "nt":
            env["TMP"] = str(self.root / "tmp")
            env["TEMP"] = str(self.root / "tmp")
        Path(env["PIP_CACHE_DIR"]).mkdir(parents=True, exist_ok=True)
        Path(env["TMPDIR"]).mkdir(parents=True, exist_ok=True)
        return env

    def _run_command(self, command: list[str], timeout: float) -> None:
        environment = self._local_environment()
        self._run_process(
            command,
            environment,
            timeout,
            progress_root=Path(environment["PIP_CACHE_DIR"]),
        )

    def _run_process(
        self,
        command: list[str],
        env: dict[str, str],
        timeout: float,
        progress_root: Path | None = None,
    ) -> None:
        process = subprocess.Popen(
            command,
            cwd=self.workspace,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            bufsize=1,
            text=True,
        )
        output_lines: list[str] = []
        reader = threading.Thread(
            target=self._read_progress, args=(process.stdout, output_lines), daemon=True
        )
        reader.start()
        deadline = time.monotonic() + timeout
        watched_root = progress_root or _progress_root_from_environment(env)
        baseline_bytes = _directory_size(watched_root) if watched_root else 0
        try:
            while process.poll() is None:
                if watched_root:
                    current_size = _directory_size(watched_root)
                    with self._lock:
                        self._record_transfer_bytes_locked(current_size - baseline_bytes)
                if self._cancel_event.is_set():
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)
                    raise InstallCancelled
                if time.monotonic() >= deadline:
                    process.kill()
                    process.wait(timeout=2)
                    raise subprocess.TimeoutExpired(command, timeout)
                time.sleep(0.1)
            if process.returncode:
                detail = "".join(output_lines[-20:]).strip()
                raise subprocess.CalledProcessError(
                    process.returncode,
                    command,
                    output=detail,
                )
        finally:
            if process.poll() is None:
                process.kill()
            reader.join(timeout=2)

    def _read_progress(self, stream: Any, output_lines: list[str] | None = None) -> None:
        if stream is None:
            return
        for line in iter(stream.readline, ""):
            if output_lines is not None:
                output_lines.append(line)
                if len(output_lines) > 40:
                    del output_lines[:-40]
            match = re.search(r"Progress\s+(\d+)\s+of\s+(\d+)", line)
            if match is None:
                continue
            received, total = (int(value) for value in match.groups())
            with self._lock:
                self._record_transfer_bytes_locked(received)
                self._current_total = max(0, total)

    def _install_go(self, spec: ToolSpec) -> None:
        if not shutil.which("go"):
            raise OSError("未找到 Go，无法安装 ffuf")
        go_root = self.root / "go"
        bin_root = self.root / "bin"
        go_root.mkdir(parents=True, exist_ok=True)
        bin_root.mkdir(parents=True, exist_ok=True)
        env = self._local_environment()
        env.update(
            {
                "GOPATH": str(go_root),
                "GOMODCACHE": str(go_root / "pkg" / "mod"),
                "GOCACHE": str(go_root / "cache"),
                "GOBIN": str(bin_root),
                "GOPROXY": self._select_go_source(),
                "GOTOOLCHAIN": "local",
            }
        )
        self._run_command_with_env(
            ["go", "install", f"{spec.package}@latest"],
            env,
            timeout=900,
        )

    def _run_command_with_env(self, command: list[str], env: dict[str, str], timeout: float) -> None:
        progress_root = Path(env["GOMODCACHE"]) if env.get("GOMODCACHE") else None
        self._run_process(command, env, timeout, progress_root=progress_root)

    def _install_jadx(self) -> None:
        payload = self._github_json("skylot/jadx/releases/latest")
        assets = payload.get("assets", [])
        url = next((str(item.get("browser_download_url", "")) for item in assets if str(item.get("name", "")).endswith(".zip")), "")
        if not url:
            raise OSError("jadx 最新版本没有可下载的 zip 包")
        archive = self.root / "jadx.zip"
        try:
            self._download(url, archive)
            install_root = self.root / "jadx"
            install_root.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(archive) as handle:
                _safe_extract_zip(handle, install_root)
            launchers = list(install_root.rglob("jadx"))
            if not launchers:
                raise OSError("jadx 压缩包中未找到启动文件")
            launchers[0].chmod(launchers[0].stat().st_mode | 0o755)
            self._write_launcher("jadx", launchers[0].parent)
        finally:
            archive.unlink(missing_ok=True)

    def _install_exiftool(self) -> None:
        with urllib.request.urlopen("https://exiftool.org/ver.txt", timeout=30) as response:
            version = response.read().decode("utf-8").strip()
        if os.name == "nt":
            archive = self.root / "exiftool.zip"
            archive_url = (
                "https://sourceforge.net/projects/exiftool/files/"
                f"exiftool-{version}_64.zip/download"
            )
        else:
            archive = self.root / "exiftool.tar.gz"
            # The official site publishes the source archive through
            # SourceForge; the exiftool.org host serves ``ver.txt`` but
            # returns 404 for the archive path. GitHub's official mirror is
            # stable and avoids that redirect/protection issue.
            archive_url = (
                "https://github.com/exiftool/exiftool/archive/"
                f"refs/tags/{version}.tar.gz"
            )
        try:
            self._download(archive_url, archive)
            install_root = self.root / "exiftool"
            install_root.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                with zipfile.ZipFile(archive) as handle:
                    _safe_extract_zip(handle, install_root)
                executable = next(
                    (path for path in install_root.rglob("*.exe") if path.is_file()),
                    None,
                )
            else:
                with tarfile.open(archive, "r:gz") as handle:
                    handle.extractall(install_root, filter="data")
                executable = next(
                    (path for path in install_root.rglob("exiftool") if path.is_file()),
                    None,
                )
            if executable is None:
                raise OSError("ExifTool 压缩包中未找到启动文件")
            if os.name != "nt":
                executable.chmod(executable.stat().st_mode | 0o755)
            self._write_launcher("exiftool", executable.parent)
        finally:
            archive.unlink(missing_ok=True)

    def _install_github_script(self, spec: ToolSpec) -> None:
        if not spec.install_url:
            raise OSError(f"{spec.label} 没有可下载地址")
        target = self.root / "bin" / (spec.command or spec.name)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.part")
        try:
            self._download(spec.install_url, temporary)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        target.chmod(0o755)

    def _install_github_release(self, spec: ToolSpec) -> None:
        repository = _github_repository(spec.homepage)
        payload = self._github_json(f"{repository}/releases/latest")
        assets = payload.get("assets", [])
        if not isinstance(assets, list):
            raise OSError(f"{spec.label} 发布信息无效")
        candidates = [
            item for item in assets
            if isinstance(item, dict)
            and str(item.get("browser_download_url", ""))
            and (str(item.get("name", "")).lower().endswith((".jar", ".zip")))
        ]
        if not candidates:
            raise OSError(f"{spec.label} 最新版本没有可下载的发行包")
        asset = next((item for item in candidates if "apktool" in str(item.get("name", "")).lower()), candidates[0])
        url = str(asset["browser_download_url"])
        suffix = ".jar" if url.lower().endswith(".jar") else ".zip"
        archive = self.root / f".{spec.name}{suffix}.part"
        try:
            self._download(url, archive)
            if suffix == ".jar":
                target = self.root / f"{spec.name}.jar"
                shutil.copyfile(archive, target)
                self._write_java_launcher(spec.name, target)
            else:
                install_root = self.root / spec.name
                install_root.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(archive) as handle:
                    _safe_extract_zip(handle, install_root)
                self._write_launcher(spec.name, next((p.parent for p in install_root.rglob(spec.name) if p.is_file()), install_root))
        finally:
            archive.unlink(missing_ok=True)

    def _install_github_source(self, spec: ToolSpec) -> None:
        repository = _github_repository(spec.homepage)
        source_root = self.root / "src" / spec.name
        if (source_root / ".gcsis-installed").is_file():
            return
        if source_root.exists():
            shutil.rmtree(source_root)
        source_root.parent.mkdir(parents=True, exist_ok=True)
        metadata = self._github_json(repository)
        branch = str(metadata.get("default_branch", "main") or "main")
        archive = self.root / f".{spec.name}.tar.gz.part"
        staging = source_root.parent / f".{spec.name}.staging"
        try:
            self._download(
                f"https://github.com/{repository}/archive/refs/heads/{branch}.tar.gz",
                archive,
            )
            staging.mkdir(parents=True, exist_ok=True)
            with tarfile.open(archive, "r:gz") as handle:
                handle.extractall(staging, filter="data")
            extracted = next((path for path in staging.iterdir() if path.is_dir()), None)
            if extracted is None:
                raise OSError(f"{spec.label} 源码归档为空")
            shutil.move(str(extracted), str(source_root))
            (source_root / ".gcsis-installed").write_text(
                "source archive complete\n", encoding="utf-8"
            )
        finally:
            archive.unlink(missing_ok=True)
            if staging.exists():
                shutil.rmtree(staging)
        # Source projects are intentionally kept as source-only entries. A
        # platform-specific build is not attempted automatically, but the
        # checkout is ready for a user to build inside tools/src. The marker
        # is written only after clone completion so a partially created Git
        # directory is never reported as installed.
        (source_root / ".gcsis-installed").write_text("source checkout complete\n", encoding="utf-8")

    def _write_java_launcher(self, name: str, jar: Path) -> None:
        bin_root = self.root / "bin"
        bin_root.mkdir(parents=True, exist_ok=True)
        launcher = self._launcher_path(name)
        if os.name == "nt":
            content = f'@echo off\r\njava -jar "{jar}" %*\r\n'
        else:
            content = "#!/bin/sh\n" f"exec java -jar {shlex.quote(jar.as_posix())} \"$@\"\n"
        launcher.write_text(content, encoding="utf-8")
        if os.name != "nt":
            launcher.chmod(0o755)

    @staticmethod
    def _github_json(path: str) -> dict[str, Any]:
        request = urllib.request.Request(
            f"https://api.github.com/repos/{path}",
            headers={"User-Agent": "gcsis-tools/1.0", "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            value = json.load(response)
        if not isinstance(value, dict):
            raise OSError("GitHub 返回了无效响应")
        return value

    def _download(self, url: str, target: Path) -> None:
        candidates = _github_candidates(url)
        if len(candidates) > 1:
            candidates = self._ordered_github_candidates(candidates)
        last_error: Exception | None = None
        for candidate in candidates:
            try:
                request = urllib.request.Request(candidate, headers={"User-Agent": "gcsis-tools/1.0"})
                with urllib.request.urlopen(request, timeout=60) as response, target.open("wb") as handle:
                    total = int(response.headers.get("Content-Length", "0") or 0)
                    with self._lock:
                        self._current_total = max(0, total)
                        self._current_bytes = 0
                        self._reset_transfer_speed_locked()
                    while True:
                        if self._cancel_event.is_set():
                            raise InstallCancelled
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        handle.write(chunk)
                        with self._lock:
                            self._record_transfer_bytes_locked(
                                self._current_bytes + len(chunk)
                            )
                return
            except InstallCancelled:
                target.unlink(missing_ok=True)
                raise
            except (OSError, TimeoutError, urllib.error.URLError) as exc:
                last_error = exc
                target.unlink(missing_ok=True)
        if last_error is not None:
            raise last_error

    def _ordered_github_candidates(self, candidates: list[str]) -> list[str]:
        timings: list[tuple[float, str]] = []
        for candidate in candidates:
            started = time.monotonic()
            try:
                request = urllib.request.Request(
                    candidate,
                    headers={"User-Agent": "gcsis-tools/1.0", "Range": "bytes=0-128"},
                )
                with urllib.request.urlopen(request, timeout=4) as response:
                    response.read(128)
                timings.append((time.monotonic() - started, candidate))
            except (OSError, TimeoutError, urllib.error.URLError):
                continue
        if not timings:
            with self._lock:
                self._github_source_url = "官方 GitHub"
                self._github_source_tested = True
            return candidates
        timings.sort(key=lambda item: item[0])
        selected = timings[0][1]
        with self._lock:
            self._github_source_url = selected if not selected.startswith("https://github.com/") else "官方 GitHub"
            self._github_source_tested = True
        ordered = [candidate for _, candidate in timings]
        ordered.extend(candidate for candidate in candidates if candidate not in ordered)
        return ordered

    def _write_launcher(self, name: str, directory: Path) -> None:
        bin_root = self.root / "bin"
        bin_root.mkdir(parents=True, exist_ok=True)
        launcher = self._launcher_path(name)
        target = _find_executable(directory, name)
        if os.name == "nt":
            script = f'@echo off\r\ncd /d "{directory}"\r\n"{target}" %*\r\n'
        else:
            script = (
                "#!/bin/sh\n"
                f"cd {shlex.quote(directory.as_posix())}\n"
                f"exec {shlex.quote(target.as_posix())} \"$@\"\n"
            )
        launcher.write_text(script, encoding="utf-8")
        if os.name != "nt":
            launcher.chmod(0o755)

    def _write_venv_launcher(self, name: str) -> None:
        bin_root = self.root / "bin"
        bin_root.mkdir(parents=True, exist_ok=True)
        launcher = self._launcher_path(name)
        target = self._venv_command(name)
        cache_root = self.root / "cache"
        tmp_root = self.root / "tmp"
        if os.name == "nt":
            config_root = cache_root / "config"
            content = (
                "@echo off\r\n"
                f'set "XDG_CACHE_HOME={cache_root}"\r\n'
                f'set "XDG_CONFIG_HOME={config_root}"\r\n'
                f'set "TMP={tmp_root}"\r\n'
                f'"{target}" %*\r\n'
            )
        else:
            content = (
                "#!/bin/sh\n"
                f"export XDG_CACHE_HOME={shlex.quote(cache_root.as_posix())}\n"
                f"export XDG_CONFIG_HOME={shlex.quote((cache_root / 'config').as_posix())}\n"
                f"export TMPDIR={shlex.quote(tmp_root.as_posix())}\n"
                f"exec {shlex.quote(target.as_posix())} \"$@\"\n"
            )
        launcher.write_text(content, encoding="utf-8")
        if os.name != "nt":
            launcher.chmod(0o755)

    def _installed(self, spec: ToolSpec) -> bool:
        if spec.kind == "builtin":
            return True
        if spec.kind == "github-source":
            return (self.root / "src" / spec.name / ".gcsis-installed").is_file()
        if spec.kind not in {"python", "python-bundle"} and spec.command and self._launcher_path(spec.command).is_file():
            return True
        if spec.kind == "python" and spec.command and self._venv_command(spec.command).is_file():
            return True
        site_packages = self._venv_site_packages()
        if site_packages is None:
            return False
        packages = spec.packages or ((spec.package,) if spec.package else ())
        if not packages:
            return False
        installed_names = {
            path.name.lower().split("-")[0].replace("-", "_")
            for path in site_packages.glob("*.dist-info")
        }
        return all(package.lower().replace("-", "_") in installed_names for package in packages)

    def _venv_site_packages(self) -> Path | None:
        """Return the platform-specific site-packages directory.

        POSIX virtualenvs use ``lib/pythonX.Y/site-packages`` while Windows
        uses ``Lib/site-packages``.  Keeping this lookup centralized prevents
        a successful Windows install from being shown as missing.
        """
        candidates = (
            self.venv / "Lib" / "site-packages",
            *self.venv.glob("lib/python*/site-packages"),
        )
        return next((path for path in candidates if path.is_dir()), None)

    def _write_manifest(self) -> None:
        """Write a human-readable inventory of tools available to Codex.

        The manifest is deliberately workspace-local and ignored by Git.  It
        gives the agent stable paths and safe examples without requiring it to
        inspect package-manager metadata or system directories.
        """
        installed = [
            spec
            for spec in TOOL_SPECS
            if self._installed(spec) and not self._errors.get(spec.name)
        ]
        self.root.mkdir(parents=True, exist_ok=True)
        lines = [
            "# CTF 工作台工具清单",
            f"# 更新时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"工具根目录：{self.root}",
            f"Python 虚拟环境：{self.venv}",
            f"命令入口目录：{self.root / 'bin'}",
            f"内置字典目录：{self.root / 'dictionaries'}",
            "",
        ]
        if self._python_index_url:
            lines.append(f"当前 Python 包源：{self._python_index_url}")
        if self._go_proxy_url:
            lines.append(f"当前 Go 模块代理：{self._go_proxy_url}")
        if self._python_index_url or self._go_proxy_url:
            lines.append("")
        if not installed:
            lines.append("当前没有已安装工具。请在首页的工具管理中选择并安装。")
        else:
            lines.append(f"已安装工具：{len(installed)} / {len(TOOL_SPECS)}")
            lines.append("")
            for spec in installed:
                _category, category_label = _tool_category(spec)
                lines.extend(
                    [
                        f"## {spec.label}",
                        f"类型：{category_label}（{spec.kind}）",
                        f"安装包 / 模块：{', '.join(spec.packages) if spec.packages else (spec.package or '源码目录')}",
                        f"项目主页：{spec.homepage or '未提供'}",
                    ]
                )
                command_path = self._launcher_path(spec.command) if spec.command else None
                if command_path is not None and command_path.is_file():
                    lines.append(f"命令路径：{command_path}")
                    lines.append(f"基本用法：{command_path} --help")
                elif spec.kind in {"python", "python-bundle"}:
                    lines.append(f"Python 环境：{self.venv}")
                    package_name = (spec.packages or (spec.package,))[0].replace("-", "_")
                    lines.append(f"基本用法：{self._venv_command('python')} -c 'import {package_name}'")
                elif spec.kind == "builtin":
                    lines.append(f"基本用法：Python 标准库，无需安装，例如 python -c 'import {spec.package}'")
                else:
                    lines.append(f"安装目录：{self.root}")
                    lines.append("基本用法：请先查看项目主页中的使用说明。")
                lines.append("")
        payload = "\n".join(lines).rstrip() + "\n"
        temporary = self.manifest_path.with_name(f".{self.manifest_path.name}.part")
        try:
            temporary.write_text(payload, encoding="utf-8")
            os.replace(temporary, self.manifest_path)
        except OSError:
            temporary.unlink(missing_ok=True)

    def _select_python_source(self) -> str:
        with self._lock:
            if self._python_source_tested:
                return self._python_index_url or _PYTHON_SOURCES[0]
            self._python_source_tested = True
            self._phase = "测速"
            self._message = "正在测试 Python 包源"
        selected = _fastest_source(_PYTHON_SOURCES, "/requests/")
        with self._lock:
            self._python_index_url = selected or _PYTHON_SOURCES[0]
        return self._python_index_url

    def _select_go_source(self) -> str:
        with self._lock:
            if self._go_source_tested:
                return self._go_proxy_url or _GO_SOURCES[0]
            self._go_source_tested = True
            self._phase = "测速"
            self._message = "正在测试 Go 模块代理"
        selected = _fastest_source(
            _GO_SOURCES, "/github.com/ffuf/ffuf/v2/@v/list"
        )
        with self._lock:
            self._go_proxy_url = selected or _GO_SOURCES[0]
        return self._go_proxy_url


def _safe_extract_zip(archive: zipfile.ZipFile, destination: Path) -> None:
    root = destination.resolve()
    for member in archive.infolist():
        target = (root / member.filename).resolve()
        if target != root and root not in target.parents:
            raise OSError("压缩包包含越界路径")
    archive.extractall(root)


def _current_platform() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _tool_supported(spec: ToolSpec) -> bool:
    """Return whether this host can install the selected tool reliably."""
    if _current_platform() not in spec.platforms:
        return False
    # Ropper's current filebytes release references ast.Str, which was
    # removed in Python 3.14.  Reject it up front instead of showing a vague
    # pip build failure after several minutes of dependency work.
    if spec.name == "ropper" and sys.version_info >= (3, 14):
        return False
    return True


def _find_executable(directory: Path, name: str) -> Path:
    candidates = (
        directory / name,
        directory / f"{name}.exe",
        directory / f"{name}.bat",
        directory / f"{name}.cmd",
    )
    return next((path for path in candidates if path.is_file()), candidates[0])


def _github_repository(homepage: str) -> str:
    match = re.match(r"https://github\.com/([^/]+/[^/#]+)", homepage.strip())
    if match is None:
        raise OSError(f"无效的 GitHub 项目地址：{homepage}")
    return match.group(1).removesuffix(".git")


def _github_candidates(url: str) -> list[str]:
    """Return official URL plus known public accelerators for downloads.

    API requests remain on api.github.com; only archive/raw download URLs are
    accelerated. Every mirror is probed before use and the official URL is
    always retained as a fallback.
    """
    if not url.startswith("https://github.com/") and not url.startswith("https://raw.githubusercontent.com/"):
        return [url]
    candidates = [url]
    for mirror in _GITHUB_MIRRORS:
        candidates.append(mirror + url)
    return candidates


def _progress_root_from_environment(environment: dict[str, str]) -> Path | None:
    for key in ("PIP_CACHE_DIR", "GOMODCACHE", "GOCACHE", "TMPDIR"):
        value = environment.get(key, "").strip()
        if value:
            return Path(value)
    return None


def _directory_size(root: Path | None) -> int:
    if root is None or not root.exists():
        return 0
    total = 0
    try:
        for path in root.rglob("*"):
            if path.is_file():
                total += path.stat().st_size
    except OSError:
        return total
    return total


def _format_process_error(error: Exception) -> str:
    """Keep actionable command output in the UI without dumping huge logs."""
    if isinstance(error, subprocess.CalledProcessError):
        detail = str(error.output or error.stderr or "").strip()
        if detail:
            detail = " ".join(detail.split())[-800:]
            return f"命令退出码 {error.returncode}：{detail}"
        return f"命令退出码 {error.returncode}：{shlex.join(error.cmd)}"
    return str(error) or type(error).__name__


def _fastest_source(sources: tuple[str, ...], suffix: str) -> str:
    """Probe mirrors in parallel and return the one with lowest latency."""
    timings: dict[str, float] = {}

    def probe(source: str) -> tuple[str, float] | None:
        started = time.monotonic()
        try:
            request = urllib.request.Request(
                source.rstrip("/") + suffix,
                headers={"User-Agent": "gcsis-tools/1.0", "Range": "bytes=0-128"},
            )
            with urllib.request.urlopen(request, timeout=4) as response:  # noqa: S310 - fixed mirror list.
                response.read(128)
            return source, time.monotonic() - started
        except (OSError, TimeoutError, urllib.error.URLError):
            return None

    with ThreadPoolExecutor(max_workers=len(sources)) as executor:
        futures = [executor.submit(probe, source) for source in sources]
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                timings[result[0]] = result[1]
    if not timings:
        return ""
    return min(timings, key=lambda source: timings[source])
