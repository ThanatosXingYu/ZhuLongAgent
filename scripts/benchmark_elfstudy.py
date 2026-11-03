"""Run isolated, concurrent ELFStudy benchmarks with CTF skills on or off."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from internal.codex import CodexManager, ManagerConfig, ProcessConfig, ProcessRunner
from internal.codex import prepare_model_catalog
from internal.codex_skills import prepare_codex_skills
from internal.config import Config, RuntimeConfigStore
from internal.solver import PromptResult

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "work" / "download" / "Reverse" / "13-ELFStudy"
BENCHMARK_ROOT = ROOT / "work" / "benchmarks" / "ELFStudy"
TERMINAL = {"completed", "failed", "canceled"}


class OfflinePrompt:
    def __init__(self, run_dir: Path, skills_enabled: bool) -> None:
        self.run_dir = run_dir
        self.skills_enabled = skills_enabled

    def prompt(self, exercise_id: int) -> PromptResult:
        skill_rule = (
            "可以使用当前 Codex 环境中已经安装的 CTF 技能。"
            if self.skills_enabled
            else "禁止读取或调用任何 CTF 技能、技能目录或技能包。"
        )
        prompt = f"""你正在做一次可重复性基准测试，题目是 Reverse 类 CTF 题 ELFStudy。

唯一工作目录：{self.run_dir}
附件：{self.run_dir / "elfstudy_401568f195e6e6e12923a6b9687d14b5.zip"}
已解包程序：{self.run_dir / "elfstudy"}

限制：
1. 只能读取和写入唯一工作目录中的内容。
2. 禁止读取原题目目录、其他 benchmark 目录、历史 WP、public_wp、公开题解或互联网搜索结果。
3. {skill_rule}
4. 必须实际分析并验证结果，不能只给计划；脚本、输出和笔记只能保存在唯一工作目录。
5. 最终必须输出完整 flag{{...}}，不要在确认前猜测。
"""
        return PromptResult(
            prompt,
            True,
            "",
            exercise_id,
            "Reverse",
            "ELFStudy",
        )


def prepare_run_dir(run_name: str) -> Path:
    run_dir = BENCHMARK_ROOT / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    for filename in (
        "elfstudy",
        "elfstudy_401568f195e6e6e12923a6b9687d14b5.zip",
    ):
        target = run_dir / filename
        if not target.exists():
            shutil.copy2(SOURCE / filename, target)
    tools_link = run_dir / "tools"
    if not tools_link.exists():
        os.symlink(ROOT / "tools", tools_link, target_is_directory=True)
    return run_dir


def run_one(
    runtime: Config,
    binary: str,
    run_name: str,
    skills_enabled: bool,
    start_barrier: threading.Barrier,
) -> dict[str, Any]:
    run_dir = prepare_run_dir(run_name)
    codex_home = run_dir / ".codex-home"
    prepare_codex_skills(run_dir, codex_home, skills_enabled)
    catalog = prepare_model_catalog(
        binary,
        codex_home / "model-catalog.json",
        runtime.codex_model,
        ("deepseek-v4-flash",),
        runtime.codex_auto_compact_token_limit,
    )
    output_path = run_dir / "codex-runs" / "final-placeholder.md"
    process = ProcessConfig(
        binary,
        str(run_dir),
        runtime.codex_base_url,
        runtime.codex_api_key,
        runtime.codex_model,
        str(output_path),
        home=str(codex_home),
        protocol=runtime.codex_protocol,
        model_catalog_path=catalog,
    )
    manager = CodexManager(
        ManagerConfig(
            process=process,
            prompt=OfflinePrompt(run_dir, skills_enabled),
            runner=ProcessRunner(binary),
            max_concurrency=1,
            runs_root=run_dir / "codex-runs",
            writeup_root=run_dir / "writeups",
            workspace_root=run_dir,
            tasks_path=run_dir / "tasks.json",
            download_root=run_dir,
        )
    )
    start_barrier.wait(timeout=60)
    started = time.monotonic()
    snapshot = manager.start(13)
    while True:
        snapshot = manager.get(snapshot.id)
        if snapshot.status in TERMINAL:
            break
        if time.monotonic() - started > 3900:
            manager.cancel(snapshot.id)
            snapshot = manager.get(snapshot.id)
            break
        time.sleep(2)
    duration = time.monotonic() - started
    flag_match = re.search(r"flag\{[^{}\r\n]{1,512}\}", snapshot.output, re.IGNORECASE)
    result = {
        "run": run_name,
        "skillsEnabled": skills_enabled,
        "taskId": snapshot.id,
        "status": snapshot.status,
        "durationSeconds": round(duration, 3),
        "createdAt": snapshot.created_at.isoformat(),
        "finishedAt": snapshot.finished_at.isoformat() if snapshot.finished_at else "",
        "rounds": snapshot.completed_turns,
        "usage": snapshot.usage.to_dict(),
        "flag": flag_match.group(0) if flag_match else "",
        "writeupPath": snapshot.writeup_path,
        "error": snapshot.error,
    }
    manager.close()
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skills", choices=("on", "off"), required=True)
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    binary = shutil.which("codex") or ""
    if not binary:
        raise SystemExit("codex CLI not found")
    runtime = RuntimeConfigStore(Config(), ROOT / "work" / "runtime" / "config.json").get()
    skills_enabled = args.skills == "on"
    barrier = threading.Barrier(args.runs)
    names = [
        f"skills-{args.skills}-{index}"
        for index in range(1, args.runs + 1)
    ]
    with ThreadPoolExecutor(max_workers=args.runs) as executor:
        futures = [
            executor.submit(
                run_one,
                runtime,
                binary,
                name,
                skills_enabled,
                barrier,
            )
            for name in names
        ]
        results = [future.result() for future in futures]
    payload = {
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "skillsEnabled": skills_enabled,
        "results": results,
    }
    output = BENCHMARK_ROOT / f"skills-{args.skills}-results.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
