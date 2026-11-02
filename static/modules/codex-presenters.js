import { formatDate, formatDuration, formatNumber } from "./formatters.js?v=20261103-layout12";

export function codexStatusLabel(status) {
  return {
    queued: "排队中",
    running: "运行中",
    completed: "已完成",
    failed: "失败",
    canceled: "已停止",
    interrupted: "运行被中断",
  }[status] || "未知状态";
}

export function codexStatusClass(status) {
  if (status === "completed") return "success";
  if (status === "failed") return "error";
  if (status === "canceled") return "muted";
  if (status === "interrupted") return "interrupted";
  return "running";
}

export function isCodexTerminal(status) {
  return ["completed", "failed", "canceled", "interrupted"].includes(status);
}

export function codexTaskTime(value) {
  if (!value) return "时间未知";
  const timestamp = new Date(value).getTime();
  return Number.isNaN(timestamp) ? "时间未知" : formatDate(timestamp);
}

export function codexTaskFullTime(value) {
  if (!value) return "尚未产生";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).format(date);
}

export function codexTaskLogTime(value) {
  if (!value) return "--:--:--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).format(date);
}

export function codexTaskDuration(start, end) {
  if (!start) return "尚未产生";
  const startedAt = new Date(start).getTime();
  const finishedAt = end ? new Date(end).getTime() : Date.now();
  if (Number.isNaN(startedAt) || Number.isNaN(finishedAt)) return "时间未知";
  return formatDuration(Math.max(0, finishedAt - startedAt) / 1000);
}

export function codexUsageSummary(usage) {
  if (!usage) return "输入 0 · 缓存 0 · 输出 0";
  return [
    `输入 ${formatNumber(usage.inputTokens || 0)}`,
    `缓存 ${formatNumber(usage.cachedInputTokens || 0)}`,
    `输出 ${formatNumber(usage.outputTokens || 0)}`,
  ].join(" · ");
}

export function codexModeLabel(mode) {
  if (mode === "pure") return "纯解题";
  if (mode === "side") return "Side 对话";
  return "主对话";
}

export function codexSummary(value, maximum = 150) {
  const normalized = String(value || "").replace(/\s+/g, " ").trim();
  if (!normalized) return "无详细内容";
  return normalized.length > maximum ? `${normalized.slice(0, maximum)}…` : normalized;
}

export function codexCommandSummary(event) {
  const command = String(event.command || event.summary || "").trim();
  const firstLine = command.split(/\r?\n/, 1)[0] || "命令";
  return codexSummary(firstLine, 180);
}

export function codexEventPresentation(event) {
  const kind = event?.kind || "event";
  const presentations = {
    "thread.started": ["会话", "session", "Codex 会话已建立"],
    "turn.started": ["Codex 开始", "progress", "开始处理请求"],
    "round.started": ["轮次开始", "progress", "开始处理本轮输入"],
    "round.completed": ["轮次完成", "success", "本轮处理完成"],
    "round.failed": ["轮次失败", "error", "本轮处理失败"],
    "round.canceled": ["轮次停止", "muted", "本轮已停止"],
    "round.interrupted": ["轮次切换", "interrupted", "本轮提前结束"],
    "task.interrupted": ["任务中断", "interrupted", "程序退出导致运行中断"],
    "task.resumed": ["恢复执行", "progress", "使用原会话恢复执行"],
    "task.finished": ["标记结束", "muted", "中断任务已标记结束"],
    "user.message": ["用户输入", "user", ""],
    "user.followup_queued": ["用户追加消息", "user", ""],
    "user.followup_updated": ["待处理消息已编辑", "user", ""],
    "user.followup_canceled": ["待处理消息已取消", "muted", ""],
    reasoning: ["思考", "reasoning", ""],
    agent_message: ["Codex 回复", "message", ""],
    command_execution: ["执行命令", "command", ""],
    "process.started": ["进程", "progress", "Codex 子进程已启动"],
    "process.waiting": ["等待上游", "progress", "长时间没有新输出，正在等待模型响应"],
    "process.completed": ["进程", "success", "Codex 子进程已结束"],
    "turn.completed": ["Token 统计", "success", "本轮 Codex 处理完成"],
    warning: ["警告", "warning", ""],
    stderr: ["错误输出", "error", ""],
    error: ["错误", "error", ""],
    stream_error: ["日志读取错误", "error", ""],
    stdout: ["输出", "output", ""],
  };
  let [label, tone, fallback] = presentations[kind] || [kind, "output", ""];
  if (kind === "stderr") {
    const diagnostic = String(event?.summary || "").toLowerCase();
    const warning = ["warning", "warn:", "defaulting to fallback metadata", "degrade performance", "deprecated"].some((marker) => diagnostic.includes(marker));
    const hardError = ["error", "failed", "fatal", "panic", "exception", "退出码"].some((marker) => diagnostic.includes(marker));
    [label, tone, fallback] = warning
      ? ["警告", "warning", ""]
      : hardError
        ? ["错误输出", "error", ""]
        : ["诊断输出", "muted", ""];
  }
  const summary = kind === "command_execution"
    ? codexCommandSummary(event)
    : kind === "turn.completed" && event.usage
      ? codexUsageSummary(event.usage)
      : codexSummary(event?.summary || fallback, 220);
  return { label, tone, summary };
}

export function mergeCodexEventLists(current, incoming) {
  const byKey = new Map();
  [...(current || []), ...(incoming || [])].forEach((event, index) => {
    const sequence = Number(event?.sequence || 0);
    const key = sequence > 0
      ? `sequence:${sequence}`
      : `legacy:${event?.at || ""}:${event?.kind || ""}:${event?.itemId || ""}:${index}`;
    byKey.set(key, event);
  });
  return [...byKey.values()].sort((a, b) => {
    const left = Number(a.sequence || 0);
    const right = Number(b.sequence || 0);
    if (left && right) return left - right;
    return new Date(a.at || 0).getTime() - new Date(b.at || 0).getTime();
  });
}

export function normalizeCodexEvents(events) {
  const normalized = [];
  const commands = new Map();
  (Array.isArray(events) ? events : []).forEach((source) => {
    const event = { ...source, repeatCount: Number(source.repeatCount || 1), sourceSequences: [source.sequence || 0] };
    if (event.kind === "command_execution") {
      const commandText = String(event.command || event.summary || "").trim();
      let key = event.itemId ? `item:${event.itemId}` : "";
      if (!key) {
        const previous = normalized.at(-1);
        if (previous?.kind === "command_execution" && previous.roundNumber === event.roundNumber && String(previous.command || previous.summary || "").trim() === commandText) {
          key = previous._commandKey;
        } else {
          key = `legacy:${event.roundNumber || 0}:${event.sequence || event.at || normalized.length}:${commandText}`;
        }
      }
      const existing = commands.get(key);
      if (existing) {
        const sameEvent = existing.status === event.status
          && String(existing.command || existing.summary || "") === String(event.command || event.summary || "")
          && String(existing.output || "") === String(event.output || "")
          && existing.exitCode === event.exitCode;
        const lifecycleUpdate = ["in_progress", "running", "started"].includes(existing.status)
          && ["completed", "failed", "canceled"].includes(event.status);
        existing.status = event.status || existing.status;
        existing.output = event.output || existing.output;
        existing.exitCode = event.exitCode ?? existing.exitCode;
        existing.startedAt = existing.startedAt || event.startedAt || existing.at;
        existing.finishedAt = event.finishedAt || existing.finishedAt;
        existing.durationSeconds = event.durationSeconds ?? existing.durationSeconds;
        existing.sessionId = event.sessionId || existing.sessionId;
        existing.sourceSequences.push(event.sequence || 0);
        if (sameEvent && !lifecycleUpdate) {
          existing.repeatCount = Number(existing.repeatCount || 1) + Number(event.repeatCount || 1);
        } else {
          existing.repeatCount = Math.max(existing.repeatCount || 1, event.repeatCount || 1);
        }
        return;
      }
      event._commandKey = key;
      event.startedAt = event.startedAt || ((event.status === "in_progress" || event.status === "running") ? event.at : null);
      commands.set(key, event);
      normalized.push(event);
      return;
    }
    const previous = normalized.at(-1);
    const same = previous
      && previous.kind !== "command_execution"
      && previous.roundNumber === event.roundNumber
      && previous.kind === event.kind
      && previous.status === event.status
      && String(previous.summary || "") === String(event.summary || "")
      && String(previous.output || "") === String(event.output || "");
    if (same) {
      previous.repeatCount = Number(previous.repeatCount || 1) + Number(event.repeatCount || 1);
      previous.sourceSequences.push(event.sequence || 0);
      previous.finishedAt = event.finishedAt || previous.finishedAt;
      return;
    }
    normalized.push(event);
  });
  return normalized;
}

export function codexEventStateKey(taskId, event, index) {
  const sequence = event.sourceSequences?.[0] || event.sequence || event.itemId || index;
  return `${taskId}:${sequence}`;
}

export function codexEventDefaultExpanded(event) {
  if (["agent_message", "error", "stream_error", "round.failed"].includes(event.kind)) return true;
  if (event.kind === "warning") return true;
  if (event.kind === "stdout" && String(event.summary || "").length < 360) return true;
  return false;
}
