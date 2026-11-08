import { formatDate, formatDuration, formatNumber } from "./formatters.js?v=20261104-archive1";

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
  // Older task logs used dotted names (agent.message); normalize them so the
  // same row styling and default-collapse rules apply to legacy data.
  const rawKind = event?.kind || "event";
  const kind = rawKind === "agent.message" ? "agent_message" : rawKind;
  const presentations = {
    "thread.started": ["会话", "session", "Codex 会话已建立", "message-square"],
    "turn.started": ["开始", "progress", "开始处理请求", "play"],
    "round.started": ["轮次", "progress", "开始处理本轮输入", "rotate-cw"],
    "round.completed": ["轮次", "success", "本轮处理完成", "circle-check"],
    "round.failed": ["轮次", "error", "本轮处理失败", "circle-x"],
    "round.canceled": ["轮次", "muted", "本轮已停止", "circle-slash"],
    "round.interrupted": ["轮次", "interrupted", "本轮提前结束", "circle-slash"],
    "task.interrupted": ["中断", "interrupted", "程序退出导致运行中断", "triangle-alert"],
    "task.resumed": ["恢复", "progress", "使用原会话恢复执行", "rotate-ccw"],
    "task.finished": ["结束", "muted", "中断任务已标记结束", "circle-stop"],
    "user.message": ["用户输入", "user", "", "user"],
    "user.followup_queued": ["用户输入", "user", "", "user"],
    "user.followup_updated": ["用户输入", "user", "", "user"],
    "user.followup_canceled": ["用户输入", "muted", "", "user"],
    reasoning: ["推理", "reasoning", "", "brain"],
    agent_message: ["对话", "message", "", "message-square"],
    command_execution: ["工具调用", "command", "", "terminal"],
    "process.started": ["进程", "progress", "Codex 子进程已启动", "cpu"],
    "process.waiting": ["等待", "progress", "长时间没有新输出，正在等待模型响应", "clock"],
    "process.completed": ["进程", "success", "Codex 子进程已结束", "cpu"],
    "turn.completed": ["用量", "success", "本轮 Codex 处理完成", "gauge"],
    warning: ["警告", "warning", "", "triangle-alert"],
    stderr: ["错误输出", "error", "", "circle-x"],
    error: ["错误", "error", "", "circle-x"],
    stream_error: ["日志错误", "error", "", "circle-x"],
    stdout: ["工具结果", "output", "", "file-output"],
  };
  let [label, tone, fallback, iconName] = presentations[kind] || [kind, "output", "", "dot"];
  if (kind === "stderr") {
    const diagnostic = String(event?.summary || "").toLowerCase();
    const warning = ["warning", "warn:", "defaulting to fallback metadata", "degrade performance", "deprecated"].some((marker) => diagnostic.includes(marker));
    const hardError = ["error", "failed", "fatal", "panic", "exception", "退出码"].some((marker) => diagnostic.includes(marker));
    [label, tone, fallback] = warning
      ? ["警告", "warning", "", "triangle-alert"]
      : hardError
        ? ["错误输出", "error", "", "circle-x"]
        : ["诊断输出", "muted", "", "info"];
    iconName = warning ? "triangle-alert" : hardError ? "circle-x" : "info";
  }
  const summary = kind === "command_execution"
    ? codexCommandSummary(event)
    : kind === "turn.completed" && event.usage
      ? codexUsageSummary(event.usage)
      : codexSummary(event?.summary || fallback, 220);
  return { label, tone, summary, icon: iconName };
}

export function codexEventAgent(event) {
  const metadata = event?.metadata && typeof event.metadata === "object" ? event.metadata : {};
  const raw = String(metadata.agent || metadata.worker || "").trim();
  return raw || "mainagent";
}

export function codexAgentLabel(agent) {
  const normalized = String(agent || "mainagent").toLowerCase();
  if (normalized === "mainagent" || normalized === "main") return "主 Agent";
  if (normalized === "planner") return "Planner";
  if (normalized === "system") return "系统";
  if (normalized.startsWith("worker")) return "Worker " + String(agent).replace(/^worker\s*/i, "");
  return String(agent || "主 Agent");
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
    const event = {
      ...source,
      kind: source.kind === "agent.message" ? "agent_message" : source.kind,
      repeatCount: Number(source.repeatCount || 1),
      sourceSequences: [source.sequence || 0],
    };
    if (event.kind === "command_execution") {
      const commandText = String(event.command || event.summary || "").trim();
      let key = event.itemId ? `item:${event.roundNumber || 0}:${codexEventAgent(event)}:${event.itemId}` : "";
      if (!key) {
        const previous = normalized.at(-1);
        if (previous?.kind === "command_execution" && previous.roundNumber === event.roundNumber && codexEventAgent(previous) === codexEventAgent(event) && String(previous.command || previous.summary || "").trim() === commandText) {
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
      && codexEventAgent(previous) === codexEventAgent(event)
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

export function codexEventDefaultExpanded() {
  return false;
}
