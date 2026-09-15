import { expect, test } from "@playwright/test";
import { emitSse, openWorkbench, task } from "./fixtures.js";

test("首次打开自动加载题目、公告并支持搜索与组合筛选", async ({ page }) => {
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(error));
  const { requests } = await openWorkbench(page);

  for (const path of ["/api/config", "/api/platform/auth", "/api/exercises", "/api/notices"]) {
    expect(requests.some((request) => request.method === "GET" && request.path === path), `${path} should be requested`).toBeTruthy();
  }
  await expect(page.locator("#exercise-count")).toHaveText("3");
  await expect(page.locator("#notice-detail")).toContainText("公告正文");

  await page.locator("#exercise-search").fill("Cold");
  await expect(page.locator(".exercise-item")).toHaveCount(1);
  await expect(page.locator(".exercise-item")).toContainText("Cold Forge");
  await page.locator("#exercise-search").fill("3");
  await expect(page.locator(".exercise-item")).toHaveCount(1);
  await expect(page.locator(".exercise-item")).toContainText("Web Gate");
  await page.locator("#exercise-search").fill("crypto");
  await expect(page.locator(".exercise-item")).toHaveCount(1);
  await expect(page.locator(".exercise-item")).toContainText("Warmup Crypto");
  await page.locator("#exercise-search").fill("");

  await page.locator("#filter-unsolved").click();
  await page.locator("#filter-environment").click();
  await expect(page.locator(".exercise-item")).toHaveCount(2);
  await page.locator("#filter-attachment").click();
  await expect(page.locator(".exercise-item")).toHaveCount(1);
  await expect(page.locator("#filter-unsolved")).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("#filter-attachment")).toHaveClass(/is-selected/);
  await page.locator("#exercise-search").fill("missing");
  await expect(page.locator(".exercise-filter-empty")).toContainText("没有符合");
  await page.locator(".exercise-filter-empty button").click();
  await expect(page.locator(".exercise-item")).toHaveCount(3);

  expect(pageErrors).toEqual([]);
});

test("Codex 会话树选择标记、弹窗关闭与 Toast 顶层均可交互", async ({ page }) => {
  const main = task();
  const side = task({
    id: "task-side",
    title: "仅输出 123",
    mode: "side",
    parentTaskId: main.id,
    sourceMessage: "仅输出 123",
    sessionId: "session-side",
  });
  await openWorkbench(page, { state: { codexTasks: [main, side] } });

  await page.locator("#open-codex-tasks").click();
  const dialog = page.locator("#codex-task-dialog");
  await expect(dialog).toHaveAttribute("open", "");

  const sideRow = page.locator(".codex-tree-row", { hasText: "仅输出 123" });
  await sideRow.locator(".codex-tree-item").click();
  await expect(sideRow).toHaveClass(/is-selected/);
  await expect(sideRow.locator(".codex-tree-item")).toHaveAttribute("aria-current", "true");
  expect(await sideRow.evaluate((node) => getComputedStyle(node, "::before").width)).toBe("4px");
  await expect(page.locator(".codex-tree-row", { hasText: "主对话" })).not.toHaveClass(/is-selected/);

  await page.locator("#codex-more-menu summary").click();
  await page.locator("#open-codex-folder").click();
  await expect(page.locator(".toast.success")).toContainText("已打开题目目录");
  await expect(page.locator("#toast-region")).toHaveJSProperty("parentElement", await dialog.elementHandle());

  await page.locator("#close-codex-tasks").click();
  await expect(dialog).not.toHaveAttribute("open", "");

  await page.locator("#open-match-info").click();
  await expect(page.locator("#match-info-dialog")).toHaveAttribute("open", "");
  await page.keyboard.press("Escape");
  await expect(page.locator("#match-info-dialog")).not.toHaveAttribute("open", "");

  await page.locator("#open-settings").click();
  const settings = page.locator("#settings-dialog");
  await expect(settings).toHaveAttribute("open", "");
  await page.mouse.click(5, 5);
  await expect(settings).not.toHaveAttribute("open", "");
});

test("Codex 警告分级、Side 请求正文与 SSE 增量更新保持正确", async ({ page }) => {
  const warningText = "Model metadata for `deepseek-v4-flash` not found. Defaulting to fallback metadata; this can degrade performance and cause issues.";
  const main = task({
    events: [
      { sequence: 1, roundNumber: 1, at: "2026-09-15T01:00:01Z", kind: "stderr", summary: warningText },
    ],
    logCount: 1,
  });
  const { requests } = await openWorkbench(page, { state: { codexTasks: [main] } });
  await page.locator("#open-codex-tasks").click();

  const warning = page.locator(".codex-event.warning", { hasText: warningText });
  await expect(warning).toHaveCount(1);
  await expect(warning.locator(".codex-event-label")).toHaveText("警告");
  await expect(page.locator(".codex-event.error", { hasText: warningText })).toHaveCount(0);

  await page.locator("#codex-task-message").fill("仅输出 123");
  await page.locator("#codex-task-side").click();
  await expect.poll(() => requests.find((entry) => entry.path === "/api/codex/tasks/task-main/message")?.body).toEqual({ message: "仅输出 123", side: true });
  await expect(page.locator(".toast.success")).toContainText("Side 分支已创建");
  await expect(page.locator("#codex-task-side")).toBeEnabled();

  await emitSse(page, { type: "codex.task", resourceId: "task-main", data: { ...main, status: "running", finishedAt: null } });
  await expect(page.locator("#codex-task-status")).toContainText("运行中");
  await emitSse(page, {
    type: "codex.event",
    resourceId: "task-main",
    data: { taskId: "task-main", event: { sequence: 2, roundNumber: 1, at: "2026-09-15T01:00:02Z", kind: "agent_message", summary: "SSE 已送达" } },
  });
  await expect(page.locator("#codex-task-events")).toContainText("SSE 已送达");
  await expect(page.locator("#realtime-status")).toHaveText("实时");
});
