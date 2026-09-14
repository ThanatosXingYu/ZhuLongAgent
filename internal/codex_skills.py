"""Workspace-local installation and activation of bundled Codex skills."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

CTF_SKILL_NAMES: tuple[str, ...] = (
    "ctf-ai-ml",
    "ctf-crypto",
    "ctf-forensics",
    "ctf-malware",
    "ctf-misc",
    "ctf-osint",
    "ctf-pwn",
    "ctf-reverse",
    "ctf-web",
    "ctf-writeup",
    "solve-challenge",
)
SKILL_SOURCE_URL = "https://github.com/ljagiello/ctf-skills"
_MARKER_NAME = ".gcsis-ctf-skills.json"


def prepare_codex_skills(
    workspace: Path, codex_home: Path, enabled: bool = True
) -> tuple[str, ...]:
    """Make bundled skills visible only to this workspace's Codex CLI."""

    target_root = codex_home / "skills"
    target_root.mkdir(parents=True, exist_ok=True)
    source_root = workspace / "tools" / "skills" / "ctf-skills"
    # Keep a one-time compatibility path for workspaces created before the
    # skills bundle was moved under ``tools/``.  New installations always use
    # the visible, workspace-local tools directory.
    legacy_source_root = workspace / "skills" / "ctf-skills"
    if not source_root.is_dir() and legacy_source_root.is_dir():
        source_root = legacy_source_root
    if not enabled:
        _remove_managed_skills(target_root)
        return ()
    missing = [name for name in CTF_SKILL_NAMES if not (source_root / name / "SKILL.md").is_file()]
    if missing:
        raise FileNotFoundError(
            f"bundled CTF skills are incomplete: {', '.join(missing)}"
        )
    for name in CTF_SKILL_NAMES:
        source = source_root / name
        target = target_root / name
        if target.exists() or target.is_symlink():
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink()
        shutil.copytree(source, target)
    scripts_source = source_root / "scripts"
    scripts_target = target_root.parent / "scripts"
    if scripts_source.is_dir():
        if scripts_target.exists():
            shutil.rmtree(scripts_target)
        shutil.copytree(scripts_source, scripts_target)
    marker = target_root / _MARKER_NAME
    marker.write_text(
        json.dumps(
            {"source": SKILL_SOURCE_URL, "skills": list(CTF_SKILL_NAMES)},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return CTF_SKILL_NAMES


def _remove_managed_skills(target_root: Path) -> None:
    marker = target_root / _MARKER_NAME
    if not marker.is_file():
        return
    for name in CTF_SKILL_NAMES:
        target = target_root / name
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        elif target.exists() or target.is_symlink():
            target.unlink()
    scripts_target = target_root.parent / "scripts"
    if scripts_target.is_dir():
        shutil.rmtree(scripts_target)
    marker.unlink(missing_ok=True)
