import {
  codexEventDefaultExpanded,
  codexEventPresentation,
  codexEventStateKey,
  codexModeLabel,
  codexStatusClass,
  codexStatusLabel,
  codexSummary,
  codexTaskDuration,
  codexTaskFullTime,
  codexTaskLogTime,
  codexTaskTime,
  codexUsageSummary,
  normalizeCodexEvents,
} from "./codex-presenters.js?v=20261104-archive1";
import { formatBytes, formatDuration, formatNumber } from "./formatters.js?v=20261104-archive1";
import { createElement, icon, refreshIcons } from "./rich-text.js?v=20261104-archive1";

export function createCodexSessionView({
  state,
  els,
  onSelectTask = () => {},
  onSelectionChange = () => {},
  onRefreshDetail = () => {},
  onCopyLog = () => {},
  onDownloadArtifact = () => {},
  onOpenRawLog = () => {},
}) {
  function exerciseName(task) {
    const exercise = state.groups
      .flatMap((group) => group.corpus || [])
      .find((item) => item.id === task.exerciseId);
    return exercise ? exercise.name || `题目 ${task.exerciseId}` : `题目 ${task.exerciseId}`;
  }

  function title(task) {
    if (task.title) return task.title;
    if (task.mode === "side") return codexSummary(task.sourceMessage || "Side 对话", 58);
    return `${codexModeLabel(task.mode)} · ${codexTaskTime(task.createdAt)}`;
  }

  function eventExpanded(key, event) {
    if (state.codexExpandedEvents.has(key)) return true;
    if (state.codexCollapsedEvents.has(key)) return false;
    return codexEventDefaultExpanded(event);
  }

  function setEventExpanded(key, expanded) {
    if (expanded) {
      state.codexExpandedEvents.add(key);
      state.codexCollapsedEvents.delete(key);
    } else {
      state.codexCollapsedEvents.add(key);
      state.codexExpandedEvents.delete(key);
    }
  }

  function commandStatusText(event) {
    const running = ["in_progress", "running", "started"].includes(event.status) && !event.finishedAt;
    if (running) return `正在执行 · ${codexTaskDuration(event.startedAt || event.at)}`;
    const duration = event.durationSeconds !== undefined && event.durationSeconds !== null
      ? formatDuration(Math.max(0, Number(event.durationSeconds)))
      : event.startedAt ? codexTaskDuration(event.startedAt, event.finishedAt || event.at) : "耗时未知";
    const successful = event.status === "completed" && (event.exitCode === undefined || Number(event.exitCode) === 0);
    const result = successful ? "成功" : event.status === "failed" || Number(event.exitCode) !== 0 ? "失败" : codexStatusLabel(event.status);
    const exit = event.exitCode === undefined || event.exitCode === null ? "" : ` · 退出码 ${event.exitCode}`;
    return `${result}${exit} · ${duration}`;
  }

  function renderEventCard(taskId, event, index) {
    const presentation = codexEventPresentation(event);
    const key = codexEventStateKey(taskId, event, index);
    const expanded = eventExpanded(key, event);
    const card = createElement("article", `codex-event ${presentation.tone}${expanded ? " is-expanded" : " is-collapsed"}`);
    const toggle = createElement("button", "codex-event-toggle");
    toggle.type = "button";
    toggle.setAttribute("aria-expanded", expanded ? "true" : "false");
    const header = createElement("span", "codex-event-header");
    const time = createElement("time", "codex-event-time", codexTaskLogTime(event.at));
    time.dateTime = event.at || "";
    time.title = codexTaskFullTime(event.at);
    const badges = createElement("span", "codex-event-badges");
    if (Number(event.repeatCount || 1) > 1) {
      badges.append(createElement("span", "codex-repeat-badge", `×${event.repeatCount}`));
    }
    if (event.kind === "reasoning") {
      badges.append(createElement("span", "codex-character-count", `${String(event.summary || "").length} 字`));
    }
    if (event.kind === "command_execution") {
      badges.append(createElement("span", `codex-command-state ${event.status || "running"}`, commandStatusText(event)));
    }
    header.append(
      createElement("span", "codex-event-label", presentation.label),
      badges,
      time,
      icon(expanded ? "chevron-up" : "chevron-down"),
    );
    toggle.append(header, createElement("span", "codex-event-summary", presentation.summary));
    toggle.addEventListener("click", () => {
      setEventExpanded(key, !expanded);
      const selected = state.codexTasks.find((task) => task.id === taskId);
      onRefreshDetail(selected || null);
    });
    card.append(toggle);
    const full = createElement("div", "codex-event-full");
    full.hidden = !expanded;
    if (event.kind === "command_execution") {
      full.append(
        createElement("p", "codex-event-section-label", "完整命令"),
        createElement("pre", "codex-command-full", String(event.command || event.summary || "")),
      );
      if (event.output) {
        full.append(
          createElement("p", "codex-event-section-label", "命令输出"),
          createElement("pre", "codex-command-output", event.output),
        );
      }
    } else {
      full.append(createElement("pre", "codex-event-content", String(event.summary || presentation.summary || "")));
      if (event.output && event.output !== event.summary) {
        full.append(createElement("pre", "codex-command-output", event.output));
      }
    }
    card.append(full);
    return card;
  }

  function roundData(events, task) {
    const groups = new Map();
    events.forEach((event) => {
      const number = Number(event.roundNumber || 0);
      if (!groups.has(number)) groups.set(number, []);
      groups.get(number).push(event);
    });
    return [...groups.entries()].sort((a, b) => a[0] - b[0]).map(([number, rows]) => {
      const user = rows.find((event) => event.kind === "user.message")
        || rows.find((event) => event.kind === "user.followup_queued");
      const startEvent = rows.find((event) => event.kind === "round.started") || rows[0];
      const endEvent = [...rows].reverse().find((event) =>
        ["round.completed", "round.failed", "round.canceled", "round.interrupted"].includes(event.kind));
      const usage = rows.reduce((total, event) => {
        if (!event.usage) return total;
        return {
          inputTokens: total.inputTokens + Number(event.usage.inputTokens || 0),
          cachedInputTokens: total.cachedInputTokens + Number(event.usage.cachedInputTokens || 0),
          outputTokens: total.outputTokens + Number(event.usage.outputTokens || 0),
        };
      }, { inputTokens: 0, cachedInputTokens: 0, outputTokens: 0 });
      const status = endEvent?.status
        || (number === Number(task.currentRound || 0) && task.status === "running" ? "running" : task.status);
      return {
        number,
        rows,
        input: user?.summary || (number ? "本轮输入未记录" : "会话与任务事件"),
        startedAt: startEvent?.at,
        finishedAt: endEvent?.at,
        usage,
        status,
      };
    });
  }

  function renderEvents(task) {
    const rows = normalizeCodexEvents(task?.events || []);
    if (!rows.length) {
      els.codexTaskEvents.replaceChildren(createElement("div", "codex-event-empty", "暂无运行日志"));
      return;
    }
    const fragment = document.createDocumentFragment();
    roundData(rows, task).forEach((round) => {
      const roundKey = `${task.id}:${round.number}`;
      const collapsed = state.codexCollapsedRounds.has(roundKey);
      const section = createElement("section", `codex-round${collapsed ? " is-collapsed" : ""}`);
      section.dataset.round = String(round.number);
      const heading = createElement("button", "codex-round-heading");
      heading.type = "button";
      heading.setAttribute("aria-expanded", collapsed ? "false" : "true");
      const main = createElement("span", "codex-round-main");
      main.append(
        icon(collapsed ? "chevron-right" : "chevron-down"),
        createElement("strong", "", round.number ? `第 ${round.number} 轮` : "会话事件"),
        createElement("span", `codex-round-status ${codexStatusClass(round.status)}`, codexStatusLabel(round.status)),
      );
      const metrics = createElement("span", "codex-round-metrics");
      metrics.append(
        createElement("span", "", codexSummary(round.input, 72)),
        createElement("span", "", `${codexTaskLogTime(round.startedAt)} → ${round.finishedAt ? codexTaskLogTime(round.finishedAt) : "进行中"}`),
        createElement("span", "", round.startedAt ? codexTaskDuration(round.startedAt, round.finishedAt) : "耗时未知"),
        createElement("span", "", codexUsageSummary(round.usage)),
      );
      heading.append(main, metrics);
      heading.addEventListener("click", () => {
        if (collapsed) state.codexCollapsedRounds.delete(roundKey);
        else state.codexCollapsedRounds.add(roundKey);
        onRefreshDetail(task);
      });
      const body = createElement("div", "codex-round-events");
      body.hidden = collapsed;
      round.rows.forEach((event, index) => body.append(renderEventCard(task.id, event, index)));
      section.append(heading, body);
      fragment.append(section);
    });
    els.codexTaskEvents.replaceChildren(fragment);
  }

  function appendDetailSection(container, heading, rows) {
    const section = createElement("section", "codex-task-details-section");
    section.append(createElement("h3", "", heading));
    const list = createElement("dl", "codex-task-details-list");
    rows.forEach(([label, value, monospace = false]) => {
      const row = createElement("div", "codex-task-details-row");
      row.append(
        createElement("dt", "", label),
        createElement("dd", monospace ? "is-monospace" : "", String(value || "--")),
      );
      list.append(row);
    });
    section.append(list);
    container.append(section);
  }

  function renderDetails(task) {
    if (!task) {
      els.codexTaskDetailsContent.replaceChildren(createElement("div", "empty-state compact", "尚未选择任务"));
      return;
    }
    const content = document.createDocumentFragment();
    appendDetailSection(content, "任务与会话", [
      ["题目", `#${task.exerciseId} · ${exerciseName(task)}`],
      ["对话标题", title(task)],
      ["运行模式", codexModeLabel(task.mode)],
      ["当前状态", codexStatusLabel(task.status)],
      ["任务 ID", task.id, true],
      ["Session ID", task.sessionId || "尚未建立", true],
      ["父任务 ID", task.parentTaskId || "无", true],
      ["Side 来源", task.sourceMessage || "非 Side 对话"],
    ]);
    appendDetailSection(content, "完整时间线", [
      ["创建时间", codexTaskFullTime(task.createdAt)],
      ["开始时间", codexTaskFullTime(task.startedAt)],
      ["结束时间", codexTaskFullTime(task.finishedAt)],
      ["首条日志", codexTaskFullTime(task.firstEventAt)],
      ["最近日志", codexTaskFullTime(task.lastEventAt)],
      ["排队耗时", task.startedAt ? codexTaskDuration(task.createdAt, task.startedAt) : codexTaskDuration(task.createdAt)],
      ["运行耗时", codexTaskDuration(task.startedAt, task.finishedAt)],
      ["总耗时", codexTaskDuration(task.createdAt, task.finishedAt)],
      ["程序退出时间", codexTaskFullTime(task.interruptedAt)],
      ["中断次数", formatNumber(task.interruptionCount || 0)],
      ["最近恢复时间", codexTaskFullTime(task.lastResumedAt)],
    ]);
    const usage = task.usage;
    appendDetailSection(content, "Token 与日志", usage ? [
      ["完成轮次", formatNumber(task.completedTurns || 0)],
      ["已开始轮次", formatNumber(task.roundsStarted || 0)],
      ["输入 Token", formatNumber(usage.inputTokens || 0)],
      ["缓存输入", formatNumber(usage.cachedInputTokens || 0)],
      ["非缓存输入", formatNumber(usage.uncachedInputTokens || 0)],
      ["缓存写入", formatNumber(usage.cacheWriteInputTokens || 0)],
      ["输出 Token", formatNumber(usage.outputTokens || 0)],
      ["推理输出", formatNumber(usage.reasoningOutputTokens || 0)],
      ["合计 Token", formatNumber(usage.totalTokens || 0)],
      ["完整日志数量", formatNumber(task.logCount || 0)],
    ] : [
      ["完整日志数量", formatNumber(task.logCount || 0)],
      ["Token 状态", "尚未收到统计"],
    ]);
    appendDetailSection(content, "文件", [
      ["题目目录", task.challengePath || "未记录", true],
      ["Writeup", task.writeupPath || "未生成", true],
      ["压缩归档", task.archivePath || (task.archived ? "旧版归档" : "未归档"), true],
      ["归档大小", task.archiveSize
        ? `${formatBytes(task.archiveSize)}${task.archiveOriginalSize
          ? ` / ${formatBytes(task.archiveOriginalSize)}（节省 ${Math.max(0, Math.round((1 - task.archiveSize / task.archiveOriginalSize) * 100))}%）`
          : ""}`
        : "--"],
      ["Codex PID", task.processId ? `${task.processId}${task.processAlive ? "（仍存在）" : "（已退出）"}` : "无"],
    ]);
    const actions = createElement("div", "codex-details-actions");
    [
      ["复制完整日志", onCopyLog],
      ["下载纯文本", () => onDownloadArtifact("logs.txt")],
      ["导出 JSON", () => onDownloadArtifact("logs.json")],
      ["打开原始日志", onOpenRawLog],
    ].forEach(([label, action]) => {
      const button = createElement("button", "button button-ghost", label);
      button.type = "button";
      button.addEventListener("click", action);
      actions.append(button);
    });
    content.append(actions);
    els.codexTaskDetailsContent.replaceChildren(content);
  }

  function createTreeItem(task, depth = 0) {
    const row = createElement(
      "div",
      `codex-tree-row depth-${depth}${task.id === state.codexSelectedTaskId ? " is-selected" : ""}${task.status === "running" ? " is-current-branch" : ""}`,
    );
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.className = "codex-task-select";
    checkbox.checked = state.selectedCodexTaskIds.has(task.id);
    checkbox.setAttribute("aria-label", `选择会话 ${title(task)}`);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) state.selectedCodexTaskIds.add(task.id);
      else state.selectedCodexTaskIds.delete(task.id);
      onSelectionChange();
    });
    const button = createElement("button", "codex-tree-item");
    button.type = "button";
    button.setAttribute("aria-current", task.id === state.codexSelectedTaskId ? "true" : "false");
    const iconWrap = createElement("span", `codex-tree-icon ${codexStatusClass(task.status)}`);
    iconWrap.append(icon(task.mode === "side" ? "git-branch" : "messages-square"));
    const copy = createElement("span", "codex-tree-copy");
    copy.append(createElement("span", "codex-task-item-title", title(task)));
    const archived = task.archived ? " · 已归档" : "";
    copy.append(createElement(
      "span",
      "codex-task-item-meta",
      `${codexModeLabel(task.mode)} · ${codexStatusLabel(task.status)}${archived}${task.mode === "side" && task.sourceMessage ? ` · 来源：${codexSummary(task.sourceMessage, 36)}` : ""}`,
    ));
    const status = createElement(
      "span",
      `codex-tree-status ${codexStatusClass(task.status)}`,
      task.status === "queued"
        ? `#${task.queuePosition || "—"}`
        : task.status === "running" ? "运行" : codexStatusLabel(task.status),
    );
    button.append(iconWrap, copy, status);
    button.addEventListener("click", () => {
      state.codexSelectedTaskId = task.id;
      state.codexEventsFollowTail = true;
      renderList();
      onSelectTask(task);
    });
    row.append(checkbox, button);
    return row;
  }

  function renderList() {
    if (!state.codexTasks.length) {
      els.codexTaskList.replaceChildren(createElement("div", "empty-state compact", "暂无 Codex 任务"));
      return;
    }
    const fragment = document.createDocumentFragment();
    const byExercise = new Map();
    state.codexTasks.forEach((task) => {
      const key = Number(task.exerciseId || 0);
      if (!byExercise.has(key)) byExercise.set(key, []);
      byExercise.get(key).push(task);
    });
    [...byExercise.entries()].forEach(([exerciseId, tasks]) => {
      const groupKey = `exercise:${exerciseId}`;
      const collapsed = state.codexCollapsedTaskGroups.has(groupKey);
      const group = createElement("section", `codex-tree-group${collapsed ? " is-collapsed" : ""}`);
      const heading = createElement("button", "codex-tree-group-heading");
      heading.type = "button";
      const active = tasks.filter((task) => ["running", "queued"].includes(task.status)).length;
      heading.append(
        icon(collapsed ? "chevron-right" : "chevron-down"),
        createElement("strong", "", `#${exerciseId} ${exerciseName(tasks[0])}`),
        createElement("span", "", `${tasks.length} 个会话${active ? ` · ${active} 活跃` : ""}`),
      );
      heading.addEventListener("click", () => {
        if (collapsed) state.codexCollapsedTaskGroups.delete(groupKey);
        else state.codexCollapsedTaskGroups.add(groupKey);
        renderList();
      });
      group.append(heading);
      const body = createElement("div", "codex-tree-group-body");
      body.hidden = collapsed;
      const children = new Map();
      tasks.forEach((task) => {
        if (!task.parentTaskId) return;
        if (!children.has(task.parentTaskId)) children.set(task.parentTaskId, []);
        children.get(task.parentTaskId).push(task);
      });
      const roots = tasks.filter((task) =>
        !task.parentTaskId || !tasks.some((candidate) => candidate.id === task.parentTaskId));
      roots.forEach((root) => {
        const sessionKey = `session:${root.id}`;
        const sessionCollapsed = state.codexCollapsedSessions.has(sessionKey);
        const session = createElement("div", `codex-tree-session${sessionCollapsed ? " is-collapsed" : ""}`);
        const rootRow = createTreeItem(root, 0);
        const sides = children.get(root.id) || [];
        if (sides.length) {
          const toggle = createElement("button", "codex-tree-session-toggle");
          toggle.type = "button";
          toggle.title = sessionCollapsed ? "展开 Side 对话" : "折叠 Side 对话";
          toggle.append(icon(sessionCollapsed ? "chevron-right" : "chevron-down"));
          toggle.addEventListener("click", () => {
            if (sessionCollapsed) state.codexCollapsedSessions.delete(sessionKey);
            else state.codexCollapsedSessions.add(sessionKey);
            renderList();
          });
          rootRow.prepend(toggle);
        }
        session.append(rootRow);
        const sideBody = createElement("div", "codex-tree-children");
        sideBody.hidden = sessionCollapsed;
        sides.forEach((side) => sideBody.append(createTreeItem(side, 1)));
        session.append(sideBody);
        body.append(session);
      });
      group.append(body);
      fragment.append(group);
    });
    els.codexTaskList.replaceChildren(fragment);
    refreshIcons();
  }

  function setAllEventsExpanded(task, expanded) {
    normalizeCodexEvents(task.events || []).forEach((event, index) => {
      setEventExpanded(codexEventStateKey(task.id, event, index), expanded);
    });
  }

  function markdown(task, logs) {
    const usage = task.usage || {};
    const header = [
      `# ${title(task)}`,
      "",
      `- 任务 ID：\`${task.id}\``,
      `- 题目：\`#${task.exerciseId}\` ${exerciseName(task)}`,
      `- 模式：${codexModeLabel(task.mode)}`,
      `- 状态：${codexStatusLabel(task.status)}`,
      `- 创建时间：${codexTaskFullTime(task.createdAt)}`,
      `- 结束时间：${codexTaskFullTime(task.finishedAt)}`,
      `- Session ID：${task.sessionId ? `\`${task.sessionId}\`` : "未建立"}`,
      `- Token：${formatNumber(usage.totalTokens || 0)}`,
      `- 归档状态：${task.archived ? "已压缩归档" : "活动会话"}`,
      "",
      "## 运行日志",
      "",
    ];
    const events = (Array.isArray(logs) ? logs : []).map((event) => {
      const details = [
        `### ${codexTaskFullTime(event.at)} · ${event.kind || "event"}`,
        "",
        String(event.summary || "").trim(),
      ].filter(Boolean);
      if (event.command && event.command !== event.summary) {
        details.push("", "```sh", String(event.command), "```");
      }
      if (event.output) {
        details.push("", "```text", String(event.output), "```");
      }
      return details.join("\n");
    });
    if (!events.length) events.push("_没有可用日志。_");
    return [...header, ...events].join("\n").trimEnd() + "\n";
  }

  return {
    exerciseName,
    title,
    markdown,
    renderList,
    renderEvents,
    renderDetails,
    setAllEventsExpanded,
  };
}
