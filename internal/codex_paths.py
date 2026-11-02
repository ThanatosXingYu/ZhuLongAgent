"""Path derivation helpers shared by Codex tasks."""

from __future__ import annotations

from pathlib import Path

from .solver import PromptResult, safe_writeup_segment


def writeup_path_for(
    workspace_root: Path,
    root: Path,
    prompt: PromptResult,
    exercise_id: int,
    task_id: str,
    mode: str,
) -> str:
    category = safe_path_part(prompt.category, "Uncategorized")
    name = safe_path_part(prompt.exercise_name, f"exercise-{exercise_id}")
    marker = "codex-pure" if mode == "pure" else "codex"
    absolute = root / category / f"{exercise_id}-{name}-{marker}-{task_id}.md"
    try:
        return absolute.relative_to(workspace_root).as_posix()
    except ValueError:
        return absolute.as_posix()


def challenge_path_for(
    workspace_root: Path,
    download_root: Path,
    prompt: PromptResult,
    exercise_id: int,
) -> str:
    if prompt.attachments:
        attachment_path = Path(prompt.attachments[0].path)
        directory = attachment_path.parent
        absolute = directory if directory.is_absolute() else workspace_root / directory
    else:
        category = safe_writeup_segment(prompt.category, "Uncategorized")
        name = safe_writeup_segment(prompt.exercise_name, "exercise")
        absolute = download_root / category / f"{exercise_id}-{name}"
    try:
        return (
            absolute.resolve(strict=False)
            .relative_to(workspace_root.resolve())
            .as_posix()
        )
    except ValueError:
        return absolute.resolve(strict=False).as_posix()


def challenge_path_from_writeup(writeup_path: str, task_id: str, mode: str) -> str:
    """Recover pre-migration task folders from their trusted writeup names."""

    path = Path(writeup_path)
    if len(path.parts) < 3 or path.parts[0] not in {"writeups", "work"}:
        return ""
    marker = "codex-pure" if mode == "pure" else "codex"
    suffix = f"-{marker}-{task_id}.md"
    if not path.name.endswith(suffix):
        return ""
    exercise_directory = path.name[: -len(suffix)]
    if not exercise_directory:
        return ""
    if path.parts[0] == "work" and len(path.parts) >= 4:
        return (
            Path("work") / "download" / path.parts[2] / exercise_directory
        ).as_posix()
    return (Path("download") / path.parts[1] / exercise_directory).as_posix()


def safe_path_part(value: str, fallback: str) -> str:
    value = value.strip()
    chars = [
        char if (char.isascii() and (char.isalnum() or char in "-_.")) else "_"
        for char in value
    ]
    result = "".join(chars).strip("._")
    return result or fallback
