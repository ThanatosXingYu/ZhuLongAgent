"""Workspace-local generated data layout and legacy migration."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

LOGGER = logging.getLogger("gcsis-tools.workspace")


@dataclass(frozen=True)
class WorkspaceLayout:
    """All mutable workbench data lives below one visible directory."""

    root: Path
    data_root: Path
    runtime: Path
    download: Path
    writeups: Path
    codex_runs: Path
    cache: Path


def prepare_workspace_layout(workspace: str | Path) -> WorkspaceLayout:
    root = Path(workspace).resolve()
    data_root = root / "workbench-data"
    layout = WorkspaceLayout(
        root=root,
        data_root=data_root,
        runtime=data_root / "runtime",
        download=data_root / "download",
        writeups=data_root / "writeups",
        codex_runs=data_root / "codex-runs",
        cache=data_root / "cache",
    )
    data_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    _migrate_directory(root / "runtime", layout.runtime)
    _migrate_directory(root / "download", layout.download)
    _migrate_directory(root / "writeups", layout.writeups)
    _migrate_directory(root / "codex-runs", layout.codex_runs)
    _migrate_directory(root / "tools" / "cache", layout.cache / "tools")
    _migrate_directory(root / "tools" / "tmp", layout.cache / "tools-tmp")
    _migrate_directory(root / "tools" / "go", layout.cache / "go")
    for path in (
        layout.runtime,
        layout.download,
        layout.writeups,
        layout.codex_runs,
        layout.cache,
    ):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return layout


def _migrate_directory(legacy: Path, target: Path) -> None:
    if not legacy.exists() or legacy == target:
        return
    if not target.exists():
        try:
            legacy.rename(target)
            return
        except OSError:
            pass
    try:
        shutil.copytree(legacy, target, dirs_exist_ok=True)
    except OSError:
        LOGGER.warning("无法迁移旧数据目录 %s 到 %s", legacy, target, exc_info=True)
