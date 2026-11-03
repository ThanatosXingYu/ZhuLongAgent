import { isCodexTerminal, mergeCodexEventLists } from "./codex-presenters.js?v=20261104-archive1";
import { formatNumber } from "./formatters.js?v=20261104-archive1";

export function createCodexSessionController({
  state,
  els,
  request,
  withButton,
  showToast,
  fallbackPollMs,
  shouldUseFallbackPolling,
  markdownForTask,
  onRenderState,
  onRenderExercises,
  onRefreshExerciseDetail,
  onRefreshOverview,
  onCloseActionMenus,
}) {
  let loadEpoch = 0;

  function stopPolling() {
    if (state.codexPollTimer) window.clearInterval(state.codexPollTimer);
    state.codexPollTimer = null;
  }

  function stopPollingIfIdle() {
    const active = state.codexTasks.some((task) => ["running", "queued"].includes(task.status));
    if (!active && els.codexTaskPage.hidden) stopPolling();
  }

  function ensurePolling() {
    if (!shouldUseFallbackPolling() || state.realtimeClient) return;
    if (!state.codexPollTimer) {
      state.codexPollTimer = window.setInterval(() => loadTasks({ silent: true }), fallbackPollMs);
    }
  }

  async function loadTask(id, options = {}) {
    try {
      const task = await request(`/api/codex/tasks/${encodeURIComponent(id)}`);
      const mergedEvents = mergeCodexEventLists(state.codexLoadedEvents.get(id) || [], task.events || []);
      state.codexLoadedEvents.set(id, mergedEvents);
      task.events = mergedEvents;
      const firstSequence = Number(mergedEvents[0]?.sequence || 0);
      state.codexHasMoreEvents.set(
        id,
        Boolean(task.logCount > mergedEvents.length && (firstSequence > 1 || !firstSequence)),
      );
      const index = state.codexTasks.findIndex((item) => item.id === id);
      if (index >= 0) state.codexTasks[index] = task;
      else state.codexTasks.unshift(task);
      if (options.select !== false) state.codexSelectedTaskId = id;
      if (options.render !== false) onRenderState();
      return task;
    } catch (error) {
      if (!options.silent && error.code !== "CODEX_TASK_NOT_FOUND") {
        showToast(`Codex 任务读取失败：${error.message}`, "error");
      }
      return null;
    }
  }

  async function loadTasks(options = {}) {
    const append = Boolean(options.append);
    if (append && (!state.codexTaskHasMore || state.codexTaskLoadingMore)) return null;
    const requestEpoch = ++loadEpoch;
    if (append) state.codexTaskLoadingMore = true;
    try {
      const previous = new Map(state.codexTasks.map((task) => [task.id, task.status]));
      const previousDetails = new Map(state.codexTasks.map((task) => [task.id, task]));
      const offset = append ? state.codexTasks.length : 0;
      const pageSize = append ? 50 : Math.max(50, Math.min(state.codexTasks.length || 50, 200));
      const parameters = new URLSearchParams({
        offset: String(offset),
        limit: String(pageSize),
        query: state.codexTaskQuery,
        status: state.codexTaskStatus,
        archived: state.codexTaskArchive,
      });
      const result = await request(`/api/codex/tasks?${parameters}`);
      if (requestEpoch !== loadEpoch) return null;
      state.codexAvailable = result.available === undefined ? true : Boolean(result.available);
      const incoming = (Array.isArray(result.tasks) ? result.tasks : []).map((task) => ({
        ...task,
        events: state.codexLoadedEvents.get(task.id) || previousDetails.get(task.id)?.events || [],
      }));
      state.codexTasks = append
        ? [...state.codexTasks, ...incoming.filter((task) => !previousDetails.has(task.id))]
        : incoming;
      state.codexTaskTotal = Math.max(Number(result.total || 0), state.codexTasks.length);
      state.codexTaskHasMore = state.codexTasks.length < state.codexTaskTotal;
      state.selectedCodexTaskIds = new Set(
        [...state.selectedCodexTaskIds].filter((taskId) =>
          state.codexTasks.some((task) => task.id === taskId)),
      );
      updateCounter();
      updateLoadMore();
      updateSelectionToolbar();
      if (state.codexSelectedTaskId === null && state.codexTasks.length) {
        state.codexSelectedTaskId = state.codexTasks[0].id;
      }
      if (state.codexSelectedTaskId && state.codexTasks.some((task) => task.id === state.codexSelectedTaskId)) {
        await loadTask(state.codexSelectedTaskId, { select: false, render: false, silent: true });
      } else if (state.codexTasks.length) {
        state.codexSelectedTaskId = state.codexTasks[0].id;
        await loadTask(state.codexSelectedTaskId, { select: false, render: false, silent: true });
      } else {
        state.codexSelectedTaskId = null;
      }
      onRenderState();
      onRenderExercises();
      for (const task of state.codexTasks) {
        if (
          isCodexTerminal(task.status)
          && previous.get(task.id)
          && previous.get(task.id) !== task.status
          && !state.codexTerminalSeen.has(task.id)
        ) {
          state.codexTerminalSeen.add(task.id);
          if (task.exerciseId === state.selectedExerciseId) {
            await onRefreshExerciseDetail(task.exerciseId);
          }
          await onRefreshOverview();
        }
      }
      const active = state.codexTasks.some((task) => ["running", "queued"].includes(task.status));
      if (active || !els.codexTaskPage.hidden) ensurePolling();
      else stopPollingIfIdle();
      return result;
    } catch (error) {
      if (error.code === "CODEX_UNAVAILABLE") {
        state.codexAvailable = false;
        els.codexActiveCount.textContent = "不可用";
        onRenderState();
      } else if (options.notify) {
        showToast(`Codex 任务刷新失败：${error.message}`, "error");
      }
      stopPollingIfIdle();
      return null;
    } finally {
      if (append) state.codexTaskLoadingMore = false;
    }
  }

  function updateLoadMore() {
    if (!els.codexLoadMoreTasks) return;
    const shown = state.codexTasks.length;
    const total = Math.max(state.codexTaskTotal, shown);
    els.codexLoadMoreTasks.hidden = !state.codexTaskHasMore;
    els.codexLoadMoreTasks.disabled = state.codexTaskLoadingMore;
    els.codexLoadMoreTasks.textContent = state.codexTaskLoadingMore
      ? "正在加载…"
      : `加载更多会话（${formatNumber(shown)} / ${formatNumber(total)}）`;
  }

  async function loadMoreTasks() {
    await loadTasks({ append: true, silent: true, notify: true });
  }

  function updateSelectionToolbar() {
    const count = state.selectedCodexTaskIds.size;
    if (els.codexSelectedCount) {
      els.codexSelectedCount.textContent = count ? `已选择 ${formatNumber(count)} 个` : "未选择会话";
    }
    if (els.codexExportSelected) els.codexExportSelected.disabled = count === 0;
    if (els.codexDeleteSelected) els.codexDeleteSelected.disabled = count === 0;
    if (els.codexArchiveSelected) {
      els.codexArchiveSelected.disabled = count === 0;
      els.codexArchiveSelected.textContent = state.codexTaskArchive === "archived" ? "取消归档" : "归档";
    }
  }

  function applyFilters() {
    state.codexTaskQuery = els.codexSessionSearch?.value.trim() || "";
    state.codexTaskStatus = els.codexSessionStatus?.value || "";
    state.codexTaskArchive = els.codexSessionArchive?.value || "active";
    state.selectedCodexTaskIds.clear();
    state.codexSelectedTaskId = null;
    state.codexTasks = [];
    state.codexTaskTotal = 0;
    state.codexTaskHasMore = false;
    updateSelectionToolbar();
    updateCounter();
    updateLoadMore();
    onRenderState();
    void loadTasks({ notify: true });
  }

  async function archiveSelected() {
    const taskIds = [...state.selectedCodexTaskIds];
    if (!taskIds.length) return;
    const archive = state.codexTaskArchive !== "archived";
    if (!window.confirm(`${archive ? "归档" : "取消归档"}所选 ${taskIds.length} 个会话？`)) return;
    await withButton(els.codexArchiveSelected, async () => {
      try {
        await request("/api/codex/tasks/archive", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ taskIds, archived: archive }),
        });
        state.selectedCodexTaskIds.clear();
        state.codexTasks = [];
        state.codexSelectedTaskId = null;
        showToast(archive ? "所选会话已压缩归档" : "所选会话已取消归档", "success");
        await loadTasks({ notify: true });
      } catch (error) {
        showToast(`${archive ? "归档" : "取消归档"}失败：${error.message}`, "error");
      }
    });
  }

  async function deleteSelected() {
    const taskIds = [...state.selectedCodexTaskIds];
    if (!taskIds.length) return;
    const confirmed = window.confirm(
      `危险操作：确认永久删除所选 ${taskIds.length} 个 Codex 会话、压缩归档、日志及本地任务文件？此操作不可恢复。`,
    );
    if (!confirmed) return;
    await withButton(els.codexDeleteSelected, async () => {
      try {
        const result = await request("/api/codex/tasks/delete", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ taskIds }),
        });
        for (const taskId of result.deleted || taskIds) {
          state.selectedCodexTaskIds.delete(taskId);
          state.codexLoadedEvents.delete(taskId);
          state.codexHasMoreEvents.delete(taskId);
        }
        state.codexTasks = [];
        state.codexSelectedTaskId = null;
        updateSelectionToolbar();
        showToast(
          result.partial
            ? `已删除 ${result.count || 0} 个会话，其余任务删除失败`
            : `已删除 ${result.count || taskIds.length} 个会话`,
          result.partial ? "warning" : "success",
        );
        await loadTasks({ notify: true });
      } catch (error) {
        showToast(`删除所选会话失败：${error.message}`, "error");
      }
    });
  }

  async function exportSelected() {
    const tasks = state.codexTasks.filter((task) => state.selectedCodexTaskIds.has(task.id));
    if (!tasks.length) return;
    const format = els.codexExportFormat?.value === "markdown" ? "markdown" : "json";
    await withButton(els.codexExportSelected, async () => {
      try {
        const exported = await Promise.all(tasks.map(async (task) => {
          const response = await fetch(
            `/api/codex/tasks/${encodeURIComponent(task.id)}/logs.json`,
            { cache: "no-store" },
          );
          if (!response.ok) throw new Error(`日志导出失败（HTTP ${response.status}）`);
          return { task, logs: await response.json() };
        }));
        const content = format === "markdown"
          ? exported.map(({ task, logs }) => markdownForTask(task, logs)).join("\n\n---\n\n")
          : JSON.stringify(exported, null, 2);
        const blob = new Blob(
          [content],
          { type: format === "markdown" ? "text/markdown;charset=utf-8" : "application/json" },
        );
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = `codex-sessions-${Date.now()}.${format === "markdown" ? "md" : "json"}`;
        document.body.append(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);
        showToast(`已导出 ${tasks.length} 个会话（${format === "markdown" ? "Markdown" : "JSON"}）`, "success");
      } catch (error) {
        showToast(`导出失败：${error.message}`, "error");
      }
    });
  }

  async function deleteTask() {
    const id = state.codexSelectedTaskId;
    if (!id || !window.confirm("危险操作：确认删除当前 Codex 任务、完整日志及本地任务文件？此操作不可恢复。")) return;
    onCloseActionMenus();
    await withButton(els.deleteCodexTask, async () => {
      try {
        await request(`/api/codex/tasks/${encodeURIComponent(id)}`, { method: "DELETE" });
        state.codexTasks = state.codexTasks.filter((task) => task.id !== id);
        state.codexTaskTotal = Math.max(0, state.codexTaskTotal - 1);
        state.codexTaskHasMore = state.codexTasks.length < state.codexTaskTotal;
        state.codexLoadedEvents.delete(id);
        state.codexHasMoreEvents.delete(id);
        state.codexSelectedTaskId = state.codexTasks[0]?.id || null;
        onRenderState();
        updateLoadMore();
        onRenderExercises();
        showToast("Codex 任务已删除", "success");
      } catch (error) {
        showToast(`删除任务失败：${error.message}`, "error");
      }
    });
  }

  function updateCounter() {
    const active = state.codexTasks.filter((task) => ["running", "queued"].includes(task.status)).length;
    const total = Math.max(Number(state.codexTaskTotal || 0), state.codexTasks.length);
    els.codexActiveCount.textContent = state.codexAvailable === false ? "不可用" : formatNumber(total);
    els.codexActiveCount.title = state.codexAvailable === false
      ? "Codex 未配置"
      : `共 ${total} 个会话${active ? `，${active} 个运行或排队中` : ""}`;
  }

  function taskMatchesCurrentView(task) {
    const query = state.codexTaskQuery.trim().toLocaleLowerCase();
    const statuses = new Set(state.codexTaskStatus.split(",").filter(Boolean));
    const searchable = [task.id, task.exerciseId, task.title, task.sourceMessage]
      .join(" ")
      .toLocaleLowerCase();
    return (!query || searchable.includes(query))
      && (!statuses.size || statuses.has(task.status))
      && (state.codexTaskArchive === "all"
        || (state.codexTaskArchive === "archived" ? Boolean(task.archived) : !task.archived));
  }

  function upsertRealtimeTask(incoming) {
    if (!incoming?.id) return null;
    const index = state.codexTasks.findIndex((task) => task.id === incoming.id);
    const previous = index >= 0 ? state.codexTasks[index] : null;
    const task = {
      ...(previous || {}),
      ...incoming,
      events: previous?.events || incoming.events || [],
    };
    if (!taskMatchesCurrentView(task)) {
      if (index < 0) return null;
      state.codexTasks.splice(index, 1);
      state.codexTaskTotal = Math.max(0, state.codexTaskTotal - 1);
      state.selectedCodexTaskIds.delete(task.id);
      if (state.codexSelectedTaskId === task.id) {
        state.codexSelectedTaskId = state.codexTasks[0]?.id || null;
      }
      state.codexTaskHasMore = state.codexTasks.length < state.codexTaskTotal;
      return { task, previous, removed: true };
    }
    if (index >= 0) state.codexTasks.splice(index, 1, task);
    else {
      state.codexTasks.unshift(task);
      state.codexTaskTotal += 1;
    }
    state.codexTaskHasMore = state.codexTasks.length < state.codexTaskTotal;
    updateLoadMore();
    if (!state.codexSelectedTaskId) state.codexSelectedTaskId = task.id;
    return { task, previous };
  }

  async function handleRealtimeTask(incoming) {
    state.codexAvailable = true;
    const result = upsertRealtimeTask(incoming);
    if (!result) return;
    const { task, previous, removed } = result;
    if (removed && state.codexSelectedTaskId) {
      await loadTask(state.codexSelectedTaskId, { render: false, silent: true });
    }
    updateCounter();
    onRenderState();
    onRenderExercises();
    if (
      isCodexTerminal(task.status)
      && previous
      && previous.status !== task.status
      && !state.codexTerminalSeen.has(task.id)
    ) {
      state.codexTerminalSeen.add(task.id);
      if (task.exerciseId === state.selectedExerciseId) {
        await onRefreshExerciseDetail(task.exerciseId);
      }
      await onRefreshOverview();
    }
  }

  function handleRealtimeDeleted(taskId) {
    state.codexTasks = state.codexTasks.filter((task) => task.id !== taskId);
    state.codexTaskTotal = Math.max(0, state.codexTaskTotal - 1);
    state.codexTaskHasMore = state.codexTasks.length < state.codexTaskTotal;
    state.codexLoadedEvents.delete(taskId);
    state.selectedCodexTaskIds.delete(taskId);
    if (state.codexSelectedTaskId === taskId) {
      state.codexSelectedTaskId = state.codexTasks[0]?.id || null;
    }
    updateCounter();
    updateLoadMore();
    onRenderState();
    onRenderExercises();
  }

  return {
    applyFilters,
    archiveSelected,
    deleteSelected,
    deleteTask,
    ensurePolling,
    exportSelected,
    handleRealtimeDeleted,
    handleRealtimeTask,
    loadMoreTasks,
    loadTask,
    loadTasks,
    stopPolling,
    stopPollingIfIdle,
    updateCounter,
    updateLoadMore,
    updateSelectionToolbar,
    upsertRealtimeTask,
  };
}
