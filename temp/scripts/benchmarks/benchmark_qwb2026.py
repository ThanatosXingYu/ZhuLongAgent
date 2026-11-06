"""Run isolated, repeatable 2026 QWB static-challenge benchmarks."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import internal.codex as codex_core  # noqa: E402
from internal.codex import (  # noqa: E402
    CodexManager,
    ManagerConfig,
    ProcessConfig,
    ProcessRunner,
    prepare_model_catalog,
)
from internal.codex_skills import prepare_codex_skills  # noqa: E402
from internal.config import Config, RuntimeConfigStore  # noqa: E402
from internal.solver import PromptResult  # noqa: E402

DEFAULT_MANIFEST = ROOT / "temp" / "scripts" / "benchmarks" / "qwb2026_static_manifest.json"
DEFAULT_BENCHMARK_ROOT = ROOT / "work" / "benchmarks" / "qwb2026-static"
REFERENCE_ROOT = ROOT / "work" / "reference" / "qwb2026s10_writeups"
TERMINAL = {"completed", "failed", "canceled"}
FLAG_RE = re.compile(r"(?i)\b(?:flag|ctf|qwb|isc|ecf|nssctf)\{[^{}\r\n]{1,512}\}")


def load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit("manifest must be a JSON object")
    challenges = payload.get("challenges")
    if not isinstance(challenges, list) or not challenges:
        raise SystemExit("manifest has no challenges")
    return payload


def never_auto_continue(output: str, mode: str = "side") -> bool:
    del output, mode
    return False


def source_path(spec: str) -> Path:
    if spec.startswith("reference:"):
        return REFERENCE_ROOT / spec.removeprefix("reference:")
    if spec.startswith("cache:"):
        return ROOT / spec.removeprefix("cache:")
    raise ValueError(f"unsupported source spec: {spec}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def copy_public_tools(workspace: Path) -> None:
    target = workspace / "work" / "tools"
    target.mkdir(parents=True, exist_ok=True)
    for name in ("bin", "venv", "installed-tools.txt"):
        source = ROOT / "work" / "tools" / name
        link = target / name
        if not source.exists():
            continue
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(source, target_is_directory=source.is_dir())


def install_skills(codex_home: Path, workspace: Path, enabled: bool) -> tuple[str, ...]:
    if not enabled:
        return prepare_codex_skills(workspace, codex_home, False)
    staging_root = workspace / "tools" / "skills"
    staging_skill = staging_root / "ctf-skills"
    staging_root.mkdir(parents=True, exist_ok=True)
    if staging_skill.exists() or staging_skill.is_symlink():
        if staging_skill.is_dir() and not staging_skill.is_symlink():
            shutil.rmtree(staging_skill)
        else:
            staging_skill.unlink()
    staging_skill.symlink_to(
        ROOT / "resources" / "skills" / "ctf-skills", target_is_directory=True
    )
    try:
        return prepare_codex_skills(workspace, codex_home, True)
    finally:
        if staging_skill.is_symlink():
            staging_skill.unlink()
        try:
            staging_root.rmdir()
        except OSError:
            pass


def prepare_workspace(
    challenge: dict[str, Any],
    run_dir: Path,
    manifest_path: Path,
) -> tuple[Path, Path]:
    attachment_dir = run_dir / "attachments"
    attachment_dir.mkdir(parents=True, exist_ok=True)
    files = challenge.get("files", [])
    if not isinstance(files, list):
        raise ValueError(f"{challenge['id']}: files must be a list")
    for item in files:
        if not isinstance(item, dict):
            raise ValueError(f"{challenge['id']}: invalid file entry")
        source = source_path(str(item["source"]))
        if not source.is_file():
            raise FileNotFoundError(f"missing benchmark source: {source}")
        target = attachment_dir / str(item["target"])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    description_path = REFERENCE_ROOT / str(challenge["descriptionFile"])
    description = description_path.read_text(encoding="utf-8").strip()
    task_path = run_dir / "TASK.md"
    task_path.write_text(
        f"# {challenge['name']}\n\n"
        f"- 类别：{challenge['category']}\n"
        f"- 题单 ID：{challenge['id']}\n"
        f"- 附件目录：`{attachment_dir}`\n\n"
        f"{description}\n",
        encoding="utf-8",
    )
    copy_public_tools(run_dir)

    manifest_copy = run_dir / "benchmark-manifest.json"
    shutil.copy2(manifest_path, manifest_copy)
    return task_path, attachment_dir


class StaticPrompt:
    def __init__(
        self,
        challenge: dict[str, Any],
        run_dir: Path,
        task_path: Path,
        attachment_dir: Path,
        skills_enabled: bool,
    ) -> None:
        self.challenge = challenge
        self.run_dir = run_dir
        self.task_path = task_path
        self.attachment_dir = attachment_dir
        self.skills_enabled = skills_enabled

    def prompt(self, exercise_id: int) -> PromptResult:
        skill_rule = (
            "只可使用当前隔离 Codex HOME 中自动发现的 CTF 技能；不得读取工作目录之外的其他技能副本。"
            if self.skills_enabled
            else "禁止读取、搜索或调用任何 CTF 技能、SKILL.md、技能目录或技能包，只能依靠内置通用能力。"
        )
        marker_rule = ""
        if self.challenge.get("benchmarkMarker"):
            marker_rule = (
                "\n本题是分问提交题。完成全部分问后，除逐项答案外，再输出下面的基准标记，"
                "以便工作台识别本轮已经结束；不要把它当作向比赛平台提交的 Flag：\n"
                f"`{self.challenge['benchmarkMarker']}`\n"
            )
        network_rule = (
            "禁止互联网搜索题解、Flag 或疑似答案；仅允许访问题目说明中明确给出的"
            "靶机、实例或问卷地址，不得访问题解站点或搜索公开答案。"
            if self.challenge.get("networkAccess") == "target-only"
            else "禁止联网搜索，禁止从互联网下载公开题解、Flag 或疑似答案；确需依赖时可使用工作目录中的 `work/tools/`。"
        )
        prompt = f"""你正在参加一次隔离、可重复的 2026 强网杯静态题基准测试。

题目：{self.challenge["name"]}（{self.challenge["category"]}）
唯一工作目录：{self.run_dir}
题目说明：{self.task_path}
附件目录：{self.attachment_dir}

约束：
1. 只读取和写入唯一工作目录；把解包文件、脚本、笔记和输出都放在这里。
2. 禁止读取任何历史 WP、public_wp、参考仓库、其他 benchmark 目录或当前目录以外的项目文件。
3. {network_rule}
4. {skill_rule}
5. 必须实际分析、运行和交叉验证，不能只输出计划、承诺或进度说明。
6. 自己完成解题；不得猜测、枚举或拼接 Flag。题目若使用 `flag{{...}}` 格式，最终必须明确输出完整 Flag。
7. 最终输出先给完整 Flag 或逐问答案，再给可复现的 Markdown WP。{marker_rule}
"""
        return PromptResult(
            prompt,
            True,
            "",
            exercise_id,
            str(self.challenge["category"]),
            str(self.challenge["name"]),
        )


def answer_hits(output: str, answers: list[str]) -> list[bool]:
    normalized = " ".join(output.split())
    return [answer in normalized for answer in answers]


def read_attempt_diagnostics(run_dir: Path, task_id: str) -> dict[str, Any]:
    events_path = run_dir / "codex-runs" / task_id / "events.jsonl"
    if not events_path.is_file():
        return {
            "rateLimitEvents": 0,
            "turnFailures": 0,
            "errorEvents": 0,
            "commandExecutions": 0,
            "eventsPath": str(events_path),
        }
    lines = events_path.read_text(encoding="utf-8", errors="replace").splitlines()
    rate_limit_events = 0
    turn_failures = 0
    error_events = 0
    command_executions = 0
    for line in lines:
        lower = line.casefold()
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            event = {}
        kind = str(event.get("kind", "")) if isinstance(event, dict) else ""
        if "429" in lower or "rate limit" in lower or "stream disconnected" in lower:
            rate_limit_events += 1
        if kind == "turn.failed":
            turn_failures += 1
        if kind == "error":
            error_events += 1
        if kind == "command_execution" and event.get("status") == "completed":
            command_executions += 1
    return {
        "rateLimitEvents": rate_limit_events,
        "turnFailures": turn_failures,
        "errorEvents": error_events,
        "commandExecutions": command_executions,
        "eventsPath": str(events_path),
    }


def challenge_success(result: dict[str, Any]) -> bool:
    expected_flag = str(result.get("expectedFlag", "") or "")
    expected_answers = result.get("expectedAnswers", [])
    if expected_flag:
        return bool(result.get("expectedFlagMatched"))
    if expected_answers:
        return bool(result.get("allAnswersMatched"))
    return bool(result.get("discoveredFlags"))


def run_one(
    runtime: Config,
    binary: str,
    manifest_path: Path,
    benchmark_root: Path,
    challenge: dict[str, Any],
    skills_enabled: bool,
    replicate: int,
    timeout_seconds: int,
    barrier: threading.Barrier,
) -> dict[str, Any]:
    mode = "skills-on" if skills_enabled else "skills-off"
    run_dir = benchmark_root / str(challenge["id"]) / mode / f"run-{replicate}"
    run_dir.mkdir(parents=True, exist_ok=True)
    task_path, attachment_dir = prepare_workspace(challenge, run_dir, manifest_path)
    codex_home = run_dir / ".codex-home"
    active_skills = install_skills(codex_home, run_dir, skills_enabled)
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
            prompt=StaticPrompt(
                challenge,
                run_dir,
                task_path,
                attachment_dir,
                skills_enabled,
            ),
            runner=ProcessRunner(binary),
            max_concurrency=1,
            runs_root=run_dir / "codex-runs",
            writeup_root=run_dir / "writeups",
            workspace_root=run_dir,
            tasks_path=run_dir / "tasks.json",
            download_root=run_dir / "attachments",
            system_prompt=(
                "执行隔离 CTF 基准题。不得访问工作目录之外的数据或网络，"
                "必须实际推进到可验证答案。"
            ),
        )
    )
    try:
        barrier.wait(timeout=120)
        started_monotonic = time.monotonic()
        snapshot = manager.start(replicate)
        timed_out = False
        while True:
            snapshot = manager.get(snapshot.id)
            if snapshot.status in TERMINAL:
                break
            if time.monotonic() - started_monotonic > timeout_seconds:
                timed_out = True
                manager.cancel(snapshot.id)
                stop_deadline = time.monotonic() + 60
                while time.monotonic() < stop_deadline:
                    snapshot = manager.get(snapshot.id)
                    if snapshot.status in TERMINAL:
                        break
                    time.sleep(1)
                break
            time.sleep(2)
        manager.close()
        snapshot = manager.get(snapshot.id)
        duration = time.monotonic() - started_monotonic
        output = snapshot.output or ""
        discovered_flags = FLAG_RE.findall(output)
        expected_flag = str(challenge.get("expectedFlag", "") or "")
        answers = [
            str(value) for value in challenge.get("expectedAnswers", []) if str(value)
        ]
        hits = answer_hits(output, answers)
        marker = str(challenge.get("benchmarkMarker", "") or "")
        diagnostics = read_attempt_diagnostics(run_dir, snapshot.id)
        result = {
            "challengeId": challenge["id"],
            "category": challenge["category"],
            "name": challenge["name"],
            "mode": mode,
            "skillsEnabled": skills_enabled,
            "replicate": replicate,
            "status": snapshot.status,
            "timedOut": timed_out,
            "taskId": snapshot.id,
            "sessionId": snapshot.session_id,
            "durationSeconds": round(duration, 3),
            "createdAt": snapshot.created_at.isoformat(),
            "startedAt": snapshot.started_at.isoformat() if snapshot.started_at else "",
            "finishedAt": (
                snapshot.finished_at.isoformat() if snapshot.finished_at else ""
            ),
            "rounds": snapshot.completed_turns,
            "usage": snapshot.usage.to_dict(),
            "discoveredFlags": discovered_flags,
            "expectedFlag": expected_flag,
            "expectedFlagMatched": bool(
                expected_flag and expected_flag in discovered_flags
            ),
            "expectedAnswers": answers,
            "expectedAnswerHits": hits,
            "allAnswersMatched": bool(hits) and all(hits),
            "benchmarkMarkerMatched": bool(marker and marker in output),
            "success": challenge_success(
                {
                    "expectedFlag": expected_flag,
                    "expectedFlagMatched": bool(
                        expected_flag and expected_flag in discovered_flags
                    ),
                    "expectedAnswers": answers,
                    "allAnswersMatched": bool(hits) and all(hits),
                    "discoveredFlags": discovered_flags,
                }
            ),
            **diagnostics,
            "error": snapshot.error,
            "workspace": str(run_dir),
            "writeupPath": snapshot.writeup_path,
            "outputPath": str(output_path),
            "activeSkills": list(active_skills),
            "attachmentHashes": {
                item["target"]: sha256_file(attachment_dir / item["target"])
                for item in challenge.get("files", [])
                if (attachment_dir / item["target"]).is_file()
            },
            "outputPreview": output[-12000:],
        }
        (run_dir / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return result
    finally:
        manager.close()


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for result in results:
        groups.setdefault((result["challengeId"], result["mode"]), []).append(result)
    rows: list[dict[str, Any]] = []
    for (challenge_id, mode), items in sorted(groups.items()):
        durations = [float(item["durationSeconds"]) for item in items]
        tokens = [int(item["usage"]["totalTokens"]) for item in items]
        success = sum(challenge_success(item) for item in items)
        rows.append(
            {
                "challengeId": challenge_id,
                "mode": mode,
                "runs": len(items),
                "successes": success,
                "failures": len(items) - success,
                "timeouts": sum(bool(item["timedOut"]) for item in items),
                "rateLimitEvents": sum(
                    int(item.get("rateLimitEvents", 0)) for item in items
                ),
                "durationMeanSeconds": round(statistics.mean(durations), 3),
                "durationMedianSeconds": round(statistics.median(durations), 3),
                "durationP95Seconds": round(percentile(durations, 0.95), 3),
                "durationTotalSeconds": round(sum(durations), 3),
                "tokensMean": round(statistics.mean(tokens), 3),
                "tokensMedian": round(statistics.median(tokens), 3),
                "tokensTotal": sum(tokens),
            }
        )
    mode_rows: list[dict[str, Any]] = []
    for mode in sorted({result["mode"] for result in results}):
        items = [result for result in results if result["mode"] == mode]
        durations = [float(item["durationSeconds"]) for item in items]
        tokens = [int(item["usage"]["totalTokens"]) for item in items]
        mode_rows.append(
            {
                "mode": mode,
                "runs": len(items),
                "successes": sum(challenge_success(item) for item in items),
                "timeouts": sum(bool(item["timedOut"]) for item in items),
                "rateLimitEvents": sum(
                    int(item.get("rateLimitEvents", 0)) for item in items
                ),
                "durationMeanSeconds": round(statistics.mean(durations), 3),
                "durationMedianSeconds": round(statistics.median(durations), 3),
                "durationP95Seconds": round(percentile(durations, 0.95), 3),
                "durationTotalSeconds": round(sum(durations), 3),
                "tokensMean": round(statistics.mean(tokens), 3),
                "tokensMedian": round(statistics.median(tokens), 3),
                "tokensTotal": sum(tokens),
            }
        )
    return {"byChallenge": rows, "byMode": mode_rows}


def write_csv(path: Path, results: list[dict[str, Any]]) -> None:
    fields = [
        "challengeId",
        "category",
        "name",
        "mode",
        "replicate",
        "status",
        "timedOut",
        "durationSeconds",
        "rounds",
        "usage.inputTokens",
        "usage.cachedInputTokens",
        "usage.outputTokens",
        "usage.totalTokens",
        "expectedFlagMatched",
        "allAnswersMatched",
        "benchmarkMarkerMatched",
        "success",
        "rateLimitEvents",
        "turnFailures",
        "commandExecutions",
        "sessionId",
        "taskId",
        "error",
        "workspace",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for result in results:
            row = {key: result.get(key, "") for key in fields if "." not in key}
            for key in fields:
                if not key.startswith("usage."):
                    continue
                usage_key = key.split(".", 1)[1]
                row[key] = result.get("usage", {}).get(usage_key, "")
            writer.writerow(row)


def load_existing_results(
    benchmark_root: Path, challenge_ids: set[str] | None = None
) -> list[dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for result_path in sorted(benchmark_root.glob("*/*/run-*/result.json")):
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(result, dict):
            continue
        if challenge_ids is not None and result.get("challengeId") not in challenge_ids:
            continue
        key = str(result.get("workspace", result_path.parent))
        results[key] = result
    return sorted(
        results.values(),
        key=lambda item: (
            str(item.get("challengeId", "")),
            str(item.get("mode", "")),
            int(item.get("replicate", 0)),
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--benchmark-root", type=Path, default=DEFAULT_BENCHMARK_ROOT)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--parallel", type=int, default=6)
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument(
        "--modes",
        default="skills-on,skills-off",
        help="comma-separated: skills-on,skills-off",
    )
    parser.add_argument(
        "--challenges",
        default="",
        help="comma-separated challenge IDs; empty means all",
    )
    parser.add_argument(
        "--include-external",
        action="store_true",
        help="include challenges whose original attachment is not vendored",
    )
    parser.add_argument(
        "--single-round",
        action="store_true",
        help="finish after one Codex turn instead of auto-resuming without a flag",
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    args.manifest = args.manifest.expanduser().resolve()
    args.benchmark_root = args.benchmark_root.expanduser().resolve()
    if args.runs < 1:
        raise SystemExit("--runs must be positive")
    if args.parallel < 1:
        raise SystemExit("--parallel must be positive")
    if args.single_round:
        codex_core._needs_auto_continue = never_auto_continue
    binary = shutil.which("codex") or ""
    if not binary:
        raise SystemExit("codex CLI not found")
    runtime = RuntimeConfigStore(
        Config(), ROOT / "work" / "runtime" / "config.json"
    ).get()
    manifest = load_manifest(args.manifest)
    wanted_ids = {
        value.strip() for value in args.challenges.split(",") if value.strip()
    }
    selected_modes = {value.strip() for value in args.modes.split(",") if value.strip()}
    if not selected_modes or not selected_modes <= {"skills-on", "skills-off"}:
        raise SystemExit("--modes must contain skills-on and/or skills-off")
    challenges = [
        item
        for item in manifest["challenges"]
        if (not wanted_ids or item["id"] in wanted_ids)
        and (
            args.include_external or item.get("availability", "vendored") != "external"
        )
    ]
    if not challenges:
        raise SystemExit("no challenges selected")
    args.benchmark_root.mkdir(parents=True, exist_ok=True)
    jobs: list[tuple[dict[str, Any], bool, int, Path]] = []
    for replicate in range(1, args.runs + 1):
        for mode in ("skills-on", "skills-off"):
            if mode not in selected_modes:
                continue
            for challenge in challenges:
                run_dir = (
                    args.benchmark_root / challenge["id"] / mode / f"run-{replicate}"
                )
                result_path = run_dir / "result.json"
                if args.force and result_path.is_file():
                    archive_stamp = datetime.now(timezone.utc).strftime(
                        "%Y%m%dT%H%M%SZ"
                    )
                    result_path.replace(
                        run_dir / f"result.previous-{archive_stamp}.json"
                    )
                if result_path.is_file() and not args.force:
                    continue
                jobs.append(
                    (
                        challenge,
                        mode == "skills-on",
                        replicate,
                        run_dir,
                    )
                )
    if not jobs:
        raise SystemExit("all selected runs already have result.json; use --force")

    started_at = datetime.now(timezone.utc)
    batch_size = min(args.parallel, len(jobs))
    for offset in range(0, len(jobs), batch_size):
        batch = jobs[offset : offset + batch_size]
        barrier = threading.Barrier(len(batch))
        with ThreadPoolExecutor(max_workers=len(batch)) as executor:
            futures = [
                executor.submit(
                    run_one,
                    runtime,
                    binary,
                    args.manifest,
                    args.benchmark_root,
                    challenge,
                    skills_enabled,
                    replicate,
                    args.timeout,
                    barrier,
                )
                for challenge, skills_enabled, replicate, _run_dir in batch
            ]
            for future in as_completed(futures):
                result = future.result()
                print(
                    json.dumps(
                        {
                            key: result[key]
                            for key in (
                                "challengeId",
                                "mode",
                                "replicate",
                                "status",
                                "timedOut",
                                "durationSeconds",
                                "rounds",
                            )
                        }
                        | {"usage": result["usage"]},
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
        results = load_existing_results(
            args.benchmark_root, {str(item["id"]) for item in challenges}
        )
        checkpoint = {
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "sourceRepository": manifest.get("sourceRepository", ""),
            "sourceCommit": manifest.get("sourceCommit", ""),
            "startedAt": started_at.isoformat(),
            "results": results,
            "summary": summarize(results),
        }
        (args.benchmark_root / "partial-results.json").write_text(
            json.dumps(checkpoint, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    final = {
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "sourceRepository": manifest.get("sourceRepository", ""),
        "sourceCommit": manifest.get("sourceCommit", ""),
        "startedAt": started_at.isoformat(),
        "model": runtime.codex_model,
        "baseUrl": runtime.codex_base_url,
        "runsPerCell": args.runs,
        "parallelism": args.parallel,
        "timeoutSeconds": args.timeout,
        "singleRound": args.single_round,
        "results": results,
        "summary": summarize(results),
    }
    (args.benchmark_root / "results.json").write_text(
        json.dumps(final, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_csv(args.benchmark_root / "results.csv", results)
    print(json.dumps(final["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
