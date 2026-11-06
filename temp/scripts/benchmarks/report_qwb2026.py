"""Summarize a completed QWB static-challenge benchmark without stale runs."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MANIFEST = ROOT / "temp" / "scripts" / "benchmarks" / "qwb2026_static_manifest.json"
DEFAULT_BENCHMARK_ROOT = ROOT / "work" / "benchmarks" / "qwb2026-static-final"
MODES = ("skills-on", "skills-off")


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def rounded(value: float) -> float:
    return round(value, 3)


def duration_text(value: float) -> str:
    seconds = round(float(value))
    return f"{seconds // 60}分{seconds % 60:02d}秒"


def has_observed_usage(result: dict[str, Any]) -> bool:
    usage = result.get("usage")
    if not isinstance(usage, dict):
        return False
    return int(usage.get("totalTokens", 0) or 0) > 0


def summarize_group(results: Iterable[dict[str, Any]]) -> dict[str, Any]:
    items = list(results)
    durations = [float(item.get("durationSeconds", 0) or 0) for item in items]
    observed_items = [item for item in items if has_observed_usage(item)]
    tokens = [
        int(item["usage"]["totalTokens"])
        for item in observed_items
    ]
    statuses = Counter(str(item.get("status", "")) for item in items)
    return {
        "runs": len(items),
        "successes": sum(bool(item.get("success")) for item in items),
        "completed": statuses["completed"],
        "failed": statuses["failed"],
        "canceled": statuses["canceled"],
        "timeouts": sum(bool(item.get("timedOut")) for item in items),
        "rateLimitEvents": sum(int(item.get("rateLimitEvents", 0) or 0) for item in items),
        "turnFailures": sum(int(item.get("turnFailures", 0) or 0) for item in items),
        "errorEvents": sum(int(item.get("errorEvents", 0) or 0) for item in items),
        "commandExecutions": sum(
            int(item.get("commandExecutions", 0) or 0) for item in items
        ),
        "durationMeanSeconds": rounded(statistics.mean(durations)) if durations else 0,
        "durationMedianSeconds": rounded(statistics.median(durations)) if durations else 0,
        "durationP95Seconds": rounded(percentile(durations, 0.95)),
        "durationTotalSeconds": rounded(sum(durations)),
        "observedUsageRuns": len(observed_items),
        "missingUsageRuns": len(items) - len(observed_items),
        "tokensObservedTotal": sum(tokens),
        "tokensObservedMean": rounded(statistics.mean(tokens)) if tokens else None,
        "tokensObservedMedian": rounded(statistics.median(tokens)) if tokens else None,
    }


def percent(part: int, total: int) -> str:
    if not total:
        return "0.0%"
    return f"{part * 100 / total:.1f}%"


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "challengeId",
        "category",
        "name",
        "mode",
        "replicate",
        "status",
        "timedOut",
        "success",
        "durationSeconds",
        "usage.inputTokens",
        "usage.cachedInputTokens",
        "usage.outputTokens",
        "usage.totalTokens",
        "usageObserved",
        "rateLimitEvents",
        "turnFailures",
        "errorEvents",
        "commandExecutions",
        "taskId",
        "sessionId",
        "workspace",
        "eventsPath",
        "writeupPath",
        "outputPath",
        "error",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for result in rows:
            row = {
                key: result.get(key, "")
                for key in fields
                if "." not in key
            }
            row["usageObserved"] = has_observed_usage(result)
            for key in fields:
                if not key.startswith("usage."):
                    continue
                usage_key = key.split(".", 1)[1]
                row[key] = result.get("usage", {}).get(usage_key, "")
            writer.writerow(row)


def session_rows(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for result in results:
        rows.append(
            {
                "challengeId": result.get("challengeId", ""),
                "mode": result.get("mode", ""),
                "replicate": result.get("replicate", ""),
                "status": result.get("status", ""),
                "taskId": result.get("taskId", ""),
                "sessionId": result.get("sessionId", ""),
                "workspace": result.get("workspace", ""),
                "eventsPath": result.get("eventsPath", ""),
                "writeupPath": result.get("writeupPath", ""),
                "outputPath": result.get("outputPath", ""),
            }
        )
    return rows


def write_session_csv(path: Path, results: list[dict[str, Any]]) -> None:
    fields = [
        "challengeId",
        "mode",
        "replicate",
        "status",
        "taskId",
        "sessionId",
        "workspace",
        "eventsPath",
        "writeupPath",
        "outputPath",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(session_rows(results))


def mode_table(rows: dict[str, dict[str, Any]]) -> list[str]:
    lines = [
        "| 模式 | 轮次 | 成功 | completed | failed | canceled | 超时 | 429 事件 | 可观测 token 轮次 | 总 token | 平均 token/可观测轮次 | 平均耗时 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for mode in MODES:
        row = rows[mode]
        mean_tokens = (
            f"{row['tokensObservedMean']:.0f}"
            if row["tokensObservedMean"] is not None
            else "N/A"
        )
        lines.append(
            f"| {mode} | {row['runs']} | {row['successes']} | {row['completed']} | "
            f"{row['failed']} | {row['canceled']} | {row['timeouts']} | "
            f"{row['rateLimitEvents']} | {row['observedUsageRuns']}/{row['runs']} | "
            f"{row['tokensObservedTotal']} | {mean_tokens} | "
            f"{duration_text(row['durationMeanSeconds'])} |"
        )
    return lines


def challenge_lines(results: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| 题目 | 类别 | skills-on | skills-off |",
        "| --- | --- | --- | --- |",
    ]
    challenge_ids = sorted({str(item["challengeId"]) for item in results})
    for challenge_id in challenge_ids:
        items = [item for item in results if item["challengeId"] == challenge_id]
        first = items[0]
        cells = []
        for mode in MODES:
            row = summarize_group(item for item in items if item["mode"] == mode)
            cells.append(
                f"{row['successes']}/{row['runs']} 成功; "
                f"{row['completed']}/{row['runs']} completed; "
                f"{duration_text(row['durationMeanSeconds'])}; "
                f"{row['tokensObservedTotal']} observed tokens"
            )
        lines.append(
            f"| {first['name']} (`{challenge_id}`) | {first['category']} | "
            f"{cells[0]} | {cells[1]} |"
        )
    return lines


def build_report(
    final: dict[str, Any],
    valid_results: list[dict[str, Any]],
    stale_count: int,
) -> str:
    by_mode = {
        mode: summarize_group(
            item for item in valid_results if item.get("mode") == mode
        )
        for mode in MODES
    }
    all_summary = summarize_group(valid_results)
    lines = [
        "# 2026 强网杯静态题 Codex A/B 测试",
        "",
        f"- 模型：`{final.get('model', '')}`",
        f"- 源仓库：`{final.get('sourceRepository', '')}`",
        f"- 源提交：`{final.get('sourceCommit', '')}`",
        f"- 轮次：每道题、每种模式 {final.get('runsPerCell', 0)} 次",
        f"- 并发：{final.get('parallelism', 0)}",
        f"- 单轮超时：{final.get('timeoutSeconds', 0)} 秒",
        f"- 单轮终止：{'是' if final.get('singleRound') else '否'}",
        f"- 有效结果：{len(valid_results)}",
        f"- 已剔除历史误跑/非静态结果：{stale_count}",
        "",
        "`success` 表示模型输出与题目预期 Flag/分问答案匹配；"
        "`completed` 只表示 Codex 进程正常完成，两者不混用。"
        "失败或中断的流可能已经消耗 token，但没有返回最终 usage，"
        "因此 token 统计只对 `usage.totalTokens > 0` 的可观测轮次求总计和均值。",
        "",
        "## 总览",
        "",
        *mode_table(by_mode),
        "",
        f"全量耗时合计：{duration_text(all_summary['durationTotalSeconds'])}。"
        f"成功 {all_summary['successes']}/{all_summary['runs']}，"
        f"completed {all_summary['completed']}/{all_summary['runs']}，"
        f"超时 {all_summary['timeouts']}，"
        f"可观测 token 轮次 {all_summary['observedUsageRuns']}/{all_summary['runs']}。",
        "",
        "## 分题结果",
        "",
        *challenge_lines(valid_results),
        "",
        "## 稳定性观察",
        "",
        f"- skills-on 平均耗时比 skills-off 少 "
        f"{duration_text(abs(by_mode['skills-on']['durationMeanSeconds'] - by_mode['skills-off']['durationMeanSeconds']))}，"
        f"completed 率 {percent(by_mode['skills-on']['completed'], by_mode['skills-on']['runs'])} "
        f"对 {percent(by_mode['skills-off']['completed'], by_mode['skills-off']['runs'])}。",
        f"- 可观测 token 总量：skills-on "
        f"{by_mode['skills-on']['tokensObservedTotal']}，skills-off "
        f"{by_mode['skills-off']['tokensObservedTotal']}。"
        f"该差额只代表有最终 usage 的轮次，不能外推为全部 {len(valid_results)} 轮的真实总消耗。",
        f"- skills-on：completed {by_mode['skills-on']['completed']}"
        f"/{by_mode['skills-on']['runs']}，"
        f"failed {by_mode['skills-on']['failed']}，"
        f"canceled {by_mode['skills-on']['canceled']}，"
        f"429 事件 {by_mode['skills-on']['rateLimitEvents']}，"
        f"turn.failed {by_mode['skills-on']['turnFailures']}。",
        f"- skills-off：completed {by_mode['skills-off']['completed']}"
        f"/{by_mode['skills-off']['runs']}，"
        f"failed {by_mode['skills-off']['failed']}，"
        f"canceled {by_mode['skills-off']['canceled']}，"
        f"429 事件 {by_mode['skills-off']['rateLimitEvents']}，"
        f"turn.failed {by_mode['skills-off']['turnFailures']}。",
        "- 本轮同时受到上游模型流中断、GitHub 插件同步 429/超时和模型代理 429 的影响；"
        "这些事件计入稳定性数据，但不强行归因给 CTF 技能包。",
        "",
        "## 产物与会话",
        "",
        f"- 完整结果：`{DEFAULT_BENCHMARK_ROOT / 'results-valid.json'}`",
        f"- CSV：`{DEFAULT_BENCHMARK_ROOT / 'results-valid.csv'}`",
        f"- 会话索引：`{DEFAULT_BENCHMARK_ROOT / 'session-index.csv'}`",
        f"- 原始运行目录：`{DEFAULT_BENCHMARK_ROOT}`",
        "",
        "每个 `run-*` 目录保留独立 `.codex-home`、`codex-runs/<taskId>/events.jsonl`、"
        "输出、WP 和 `result.json`。`session-index.csv` 给出可直接用于查看/恢复的"
        "任务 ID、Session ID、工作目录和日志路径。",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--benchmark-root", type=Path, default=DEFAULT_BENCHMARK_ROOT)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    valid_ids = {str(item["id"]) for item in manifest["challenges"]}
    final_path = args.benchmark_root / "results.json"
    final = json.loads(final_path.read_text(encoding="utf-8"))
    source_results = final.get("results", [])
    valid_results = [
        item for item in source_results if str(item.get("challengeId")) in valid_ids
    ]
    valid_results.sort(
        key=lambda item: (
            str(item.get("challengeId")),
            str(item.get("mode")),
            int(item.get("replicate", 0)),
        )
    )
    stale_paths = []
    for path in args.benchmark_root.glob("*/*/run-*/result.json"):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(item.get("challengeId")) not in valid_ids:
            stale_paths.append(path)
    stale_count = max(len(source_results) - len(valid_results), len(stale_paths))

    payload = {
        "source": {
            "repository": final.get("sourceRepository", ""),
            "commit": final.get("sourceCommit", ""),
            "model": final.get("model", ""),
            "baseUrl": final.get("baseUrl", ""),
            "runsPerCell": final.get("runsPerCell", 0),
            "parallelism": final.get("parallelism", 0),
            "timeoutSeconds": final.get("timeoutSeconds", 0),
            "singleRound": final.get("singleRound", False),
        },
        "excludedChallenges": manifest.get("excludedChallenges", []),
        "staleResultsExcluded": stale_count,
        "byMode": {
            mode: summarize_group(
                item for item in valid_results if item.get("mode") == mode
            )
            for mode in MODES
        },
        "overall": summarize_group(valid_results),
        "results": valid_results,
    }
    (args.benchmark_root / "results-valid.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_csv(args.benchmark_root / "results-valid.csv", valid_results)
    write_session_csv(args.benchmark_root / "session-index.csv", valid_results)
    (args.benchmark_root / "report.md").write_text(
        build_report(final, valid_results, stale_count),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "validResults": len(valid_results),
                "staleResultsExcluded": stale_count,
                "byMode": payload["byMode"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
