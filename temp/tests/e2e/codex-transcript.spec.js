import { test, expect } from "@playwright/test";
import { openWorkbench, task, emitSse } from "./fixtures.js";

const command = "printf '%s\\n' " + "long-argument-".repeat(80);
const event = (sequence, kind, extra = {}) => ({
  sequence, kind, roundNumber: 1, at: "2026-11-09T01:00:00Z", ...extra,
});

for (const width of [1440, 390]) {
  test(`BRTEX 单行日志与展开内容 ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const events = [
      event(1, "agent_message", { summary: "先检查输入文件，再验证结果。" }),
      event(2, "reasoning", { summary: "需要确认文件格式。" }),
      event(3, "command_execution", { itemId: "item-1", command, status: "in_progress" }),
    ];
    const main = task({ id: `transcript-${width}`, events, status: "running", finishedAt: null });
    await openWorkbench(page, { state: { codexTasks: [main] } });
    if (width < 1040) await page.locator("#open-sidebar").click();
    await page.locator("#open-codex-tasks").click();
    const rows = page.locator(".transcript-row");
    await expect(rows).toHaveCount(3);
    await expect(page.locator(".transcript-detail:visible")).toHaveCount(0);
    await expect(rows.first().locator(".transcript-icon svg")).toHaveAttribute("data-lucide", "message-square");
    const tool = rows.nth(2);
    await expect(tool.locator(".transcript-tool-name")).toHaveText("Bash");
    await expect(tool.locator(".transcript-status")).toHaveText("执行中…");
    const headerBounds = await page.locator(".codex-task-detail-actions").boundingBox();
    const logBounds = await page.locator(".codex-log-group").boundingBox();
    expect(headerBounds.y + headerBounds.height).toBeLessThanOrEqual(logBounds.y);
    const metrics = await tool.evaluate((node) => {
      const button = node.querySelector("button");
      const css = getComputedStyle(button);
      return { height: button.getBoundingClientRect().height, font: css.fontSize, line: css.lineHeight,
        gap: getComputedStyle(node.querySelector(".transcript-row-header")).gap,
        border: getComputedStyle(node).borderWidth,
        overflow: document.documentElement.scrollWidth > window.innerWidth };
    });
    expect(metrics).toEqual({ height: 24, font: "12px", line: "16px", gap: "8px", border: "0px", overflow: false });
    await tool.locator("button").click();
    await expect(tool.locator("pre")).toContainText(command);
    await expect(tool.locator("pre")).toHaveCSS("margin-left", "28px");
    await expect(tool.locator("pre")).toHaveCSS("font-size", "11px");
    await emitSse(page, { type: "codex.event", resourceId: main.id, data: { taskId: main.id,
      event: event(4, "command_execution", { itemId: "item-1", command, status: "completed", exitCode: 0, output: "result\n<script>not executable</script>" }),
    } });
    await expect(rows).toHaveCount(3);
    await expect(tool.locator("button")).toHaveAttribute("aria-expanded", "true");
    await expect(tool.locator("pre")).toContainText("【输出 ✓】");
    await expect(tool.locator("pre")).toContainText("<script>not executable</script>");
    await expect(tool.locator("script")).toHaveCount(0);
    await expect(tool.locator(".transcript-status")).toHaveText("✓");
    await tool.locator("button").click();
    await expect(tool.locator("pre")).toBeHidden();
  });
}

test("不同轮次与 Worker 的工具 ID 不互相覆盖", async ({ page }) => {
  const events = [
    event(1, "command_execution", { itemId: "item-1", command: "pwd", status: "completed", output: "main" }),
    event(2, "command_execution", { itemId: "item-1", command: "pwd", status: "completed", output: "worker", metadata: { worker: "worker#1" } }),
    event(3, "command_execution", { itemId: "item-1", command: "pwd", status: "completed", output: "round two", roundNumber: 2 }),
  ];
  await openWorkbench(page, { state: { codexTasks: [task({ events })] } });
  await page.locator("#open-codex-tasks").click();
  await expect(page.locator(".transcript-row")).toHaveCount(3);
  await expect(page.locator(".transcript-agent")).toHaveText(["主 Agent", "Worker #1", "主 Agent"]);
  await page.locator(".transcript-row").nth(1).locator("button").click();
  await expect(page.locator(".transcript-detail:visible")).toContainText("worker");
});
