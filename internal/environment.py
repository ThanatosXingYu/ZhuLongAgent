"""Read-only checks for common local CTF development runtimes."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_CHECKS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("python", "Python", ("--version",)),
    ("java", "Java", ("-version",)),
    ("go", "Go", ("version",)),
    ("rustc", "Rust", ("--version",)),
    ("gcc", "GCC", ("--version",)),
    ("gdb", "GDB", ("--version",)),
    ("nasm", "NASM", ("-v",)),
    ("node", "Node.js", ("--version",)),
    ("php", "PHP", ("--version",)),
    ("perl", "Perl", ("-v",)),
    ("ruby", "Ruby", ("--version",)),
    ("git", "Git", ("--version",)),
)


def collect_environment_status(workspace: str | Path | None = None) -> dict[str, Any]:
    root = Path(workspace or Path.cwd()).resolve()
    items = [_check_runtime(root, key, label, args) for key, label, args in _CHECKS]
    return {
        "items": items,
        "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def _check_runtime(
    workspace: Path, key: str, label: str, args: tuple[str, ...]
) -> dict[str, Any]:
    executable = _resolve_executable(workspace, key)
    if executable is None:
        return {"key": key, "label": label, "available": False, "version": "未检测到"}
    try:
        result = subprocess.run(
            [executable, *args],
            cwd=workspace,
            env=_local_environment(workspace),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "key": key,
            "label": label,
            "available": False,
            "version": "检测失败",
            "error": type(exc).__name__,
        }
    output = (result.stdout or result.stderr).strip().splitlines()
    version = output[0].strip() if output else "可用"
    return {
        "key": key,
        "label": label,
        "available": result.returncode == 0,
        "version": version,
        "path": executable,
    }


def _resolve_executable(workspace: Path, key: str) -> str | None:
    if key == "python":
        local_python = workspace / ".venv" / "bin" / "python"
        if local_python.is_file():
            return str(local_python)
        return sys.executable
    return shutil.which(key)


def _local_environment(workspace: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["XDG_CACHE_HOME"] = str(workspace / "tools" / "cache")
    return env
