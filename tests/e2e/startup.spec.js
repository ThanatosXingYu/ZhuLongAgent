import { expect, test } from "@playwright/test";
import { emitSse, installApiMock, openWorkbench, success, task } from "./fixtures.js";

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

test("比赛概览完整展示队伍数据、排名变化和公告区隔", async ({ page }) => {
  let rank = 8;
  await openWorkbench(page, {
    handler: async ({ route, url }) => {
      if (url.pathname !== "/api/overview") return false;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(success({
          stagePoint: 1934.96,
          stageRank: rank,
          solvedCount: 19,
          teamName: "GuanXing",
          organization: "青岛腾权网络科技有限公司",
        })),
      });
      return true;
    },
  });

  await page.locator("#show-competition-view").click();
  await expect(page.locator("#overview-score-value")).toHaveText("1,934.96");
  await expect(page.locator("#overview-rank-value")).toHaveText("8");
  await expect(page.locator("#overview-solved-value")).toHaveText("19");
  await expect(page.locator("#overview-team-name")).toHaveText("GuanXing");
  await expect(page.locator("#team-summary-meta")).toContainText("青岛腾权网络科技有限公司");
  await expect(page.locator("#overview-match-note")).toContainText("Regression note");
  await expect(page.locator("#notice-list-title")).toHaveText("公告列表");
  await expect(page.locator("#notice-reader-title")).toHaveText("公告正文");
  expect(await page.locator(".notice-list-section").evaluate((node) => getComputedStyle(node).borderBottomWidth)).toBe("6px");

  rank = 5;
  await page.locator("#refresh-overview").click();
  await expect(page.locator("#rank-delta")).toHaveText("↑ 3");
  await expect(page.locator("#overview-rank-delta")).toHaveText("↑ 3");
  await expect(page.locator("#overview-rank-value")).toHaveText("5");
  await expect(page.locator("#overview-rank-history-count")).toHaveText("2");
  await expect(page.locator(".overview-rank-event").first()).toContainText("↑ 3");
  for (rank of [4, 3, 2, 1, 0]) {
    await page.locator("#refresh-overview").click();
  }
  await expect(page.locator("#overview-rank-history-count")).toHaveText("5");
  await expect(page.locator(".overview-rank-event")).toHaveCount(5);
});

test("侧栏宽度、折叠边界与文字显隐时序稳定", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openWorkbench(page);

  const sidebar = page.locator("#app-sidebar");
  const collapse = page.locator("#collapse-sidebar");
  expect(await sidebar.evaluate((node) => Math.round(node.getBoundingClientRect().width))).toBe(200);

  await collapse.click();
  await expect(page.locator("body")).toHaveClass(/sidebar-collapsed/);
  await expect.poll(() => sidebar.evaluate((node) => Math.round(node.getBoundingClientRect().width))).toBe(56);
  const collapsedState = await page.evaluate(() => {
    const sidebarNode = document.querySelector("#app-sidebar");
    const toggle = document.querySelector("#collapse-sidebar");
    const brand = document.querySelector(".sidebar-brand-mark");
    const nav = document.querySelector(".sidebar-nav");
    const footer = document.querySelector(".sidebar-footer");
    const toggleRect = toggle.getBoundingClientRect();
    const brandRect = brand.getBoundingClientRect();
    const navRect = nav.getBoundingClientRect();
    return {
      toggleRight: Math.round(toggleRect.right),
      toggleTop: Math.round(toggleRect.top),
      toggleBottom: Math.round(toggleRect.bottom),
      brandBottom: Math.round(brandRect.bottom),
      navTop: Math.round(navRect.top),
      sidebarRight: Math.round(sidebarNode.getBoundingClientRect().right),
      brandWidth: Math.round(brand.getBoundingClientRect().width),
      footerDisplay: getComputedStyle(footer).display,
      labelOpacity: getComputedStyle(document.querySelector(".sidebar-link > span")).opacity,
    };
  });
  expect(collapsedState.toggleRight).toBeLessThanOrEqual(collapsedState.sidebarRight);
  expect(collapsedState.toggleTop - collapsedState.brandBottom).toBeGreaterThanOrEqual(16);
  expect(collapsedState.toggleBottom).toBeLessThanOrEqual(collapsedState.navTop);
  expect(collapsedState.brandWidth).toBe(34);
  expect(collapsedState.footerDisplay).toBe("none");
  expect(collapsedState.labelOpacity).toBe("0");
  const iconAlignment = await page.evaluate(() => {
    const links = [...document.querySelectorAll(".sidebar-link")];
    const iconCenters = links.map((link) => {
      const icon = link.querySelector("svg").getBoundingClientRect();
      return Math.round(icon.x + icon.width / 2);
    });
    const tops = links.map((link) => Math.round(link.getBoundingClientRect().top));
    const gaps = tops.slice(1).map((top, index) => top - tops[index]);
    return { iconCenters: new Set(iconCenters).size, gaps: new Set(gaps).size };
  });
  expect(iconAlignment.iconCenters).toBe(1);
  expect(iconAlignment.gaps).toBe(1);

  await collapse.click();
  await page.waitForTimeout(60);
  expect(Number(await page.locator(".sidebar-link > span").first().evaluate((node) => getComputedStyle(node).opacity))).toBe(0);
  await expect.poll(() => sidebar.evaluate((node) => Math.round(node.getBoundingClientRect().width))).toBe(200);
  await expect.poll(() => page.locator(".sidebar-link > span").first().evaluate((node) => getComputedStyle(node).opacity)).toBe("1");
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

  await expect(page.locator("#codex-active-count")).toHaveText("2");
  await page.locator("#open-codex-tasks").click();
  const taskPage = page.locator("#codex-task-page");
  await expect(taskPage).toBeVisible();
  await expect(page.locator(".codex-task-page-header")).toBeHidden();
  await expect(page.locator("body")).toHaveClass(/codex-task-open/);
  await expect(page.locator("#app-sidebar")).toBeVisible();
  await expect(page.locator("#exercise-view")).toHaveCSS("visibility", "hidden");
  const sessionControls = await page.locator(".codex-session-controls").evaluate((node) => {
    const search = node.querySelector(".codex-session-search").getBoundingClientRect();
    const selects = [...node.querySelectorAll("select")].map((select) => select.getBoundingClientRect());
    return {
      searchWidth: Math.round(search.width),
      selectWidths: selects.map((rect) => Math.round(rect.width)),
      selectRows: new Set(selects.map((rect) => Math.round(rect.y))).size,
    };
  });
  expect(sessionControls.searchWidth).toBeGreaterThan(200);
  expect(sessionControls.selectWidths.every((width) => width > 100)).toBeTruthy();
  expect(sessionControls.selectRows).toBe(1);

  const sideRow = page.locator(".codex-tree-row", { hasText: "仅输出 123" });
  await sideRow.locator(".codex-tree-item").click();
  await expect(sideRow).toHaveClass(/is-selected/);
  await expect(sideRow.locator(".codex-tree-item")).toHaveAttribute("aria-current", "true");
  expect(await sideRow.evaluate((node) => getComputedStyle(node, "::before").width)).toBe("4px");
  await expect(page.locator(".codex-tree-row", { hasText: "主对话" })).not.toHaveClass(/is-selected/);

  await page.locator("#codex-more-menu summary").click();
  await page.locator("#open-codex-folder").click();
  await expect(page.locator(".toast.success")).toContainText("已打开题目目录");
  expect(await page.locator("#toast-region").evaluate((node) => node.parentElement === document.body)).toBeTruthy();

  await page.locator("#show-competition-view").click();
  await expect(taskPage).toBeHidden();
  await expect(page.locator("body")).not.toHaveClass(/codex-task-open/);

  await expect(page.locator("#overview-match-note")).toContainText("Regression note");
  await expect(page.locator("#overview-match-rule")).toContainText("Regression rule");

  await page.locator("#open-settings").click();
  const settings = page.locator("#settings-dialog");
  await expect(settings).toHaveAttribute("open", "");
  await expect(settings).toHaveClass(/is-page-dialog/);
  await expect(settings.locator(".dialog-header")).toBeHidden();
  await page.locator("#show-competition-view").click();
  await expect(settings).not.toHaveAttribute("open", "");
});

test("Codex 全屏会话页右侧整体可滚动到输入区", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 600 });
  const events = Array.from({ length: 40 }, (_, index) => ({
    sequence: index + 1,
    roundNumber: 1,
    at: `2026-09-15T01:00:${String(index % 60).padStart(2, "0")}Z`,
    kind: "agent_message",
    summary: `长日志 ${index + 1} ${"detail ".repeat(20)}`,
  }));
  const longTask = task({
    events,
    logCount: events.length,
    output: "final output\n".repeat(120),
  });
  await openWorkbench(page, { state: { codexTasks: [longTask] } });
  await page.locator("#open-codex-tasks").click();
  await page.locator(".codex-final-output summary").click();

  const detail = page.locator(".codex-task-detail");
  expect(await detail.evaluate((node) => node.scrollHeight > node.clientHeight)).toBeTruthy();
  await detail.evaluate((node) => {
    node.scrollTop = node.scrollHeight;
  });
  const inputBox = await page.locator("#codex-task-message").boundingBox();
  expect(inputBox).not.toBeNull();
  expect(inputBox.y + inputBox.height).toBeLessThanOrEqual(600);
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

test("Codex 顶部恢复与一键 WP 发送内置提示词", async ({ page }) => {
  const main = task();
  const side = task({
    id: "task-side",
    title: "侧分支",
    mode: "side",
    parentTaskId: main.id,
    sourceMessage: "侧分支",
    sessionId: "session-side",
  });
  const { requests } = await openWorkbench(page, { state: { codexTasks: [main, side] } });
  await page.locator("#open-codex-tasks").click();
  await page.locator(".codex-tree-row", { hasText: "侧分支" }).locator(".codex-tree-item").click();

  await page.locator("#codex-task-message").fill("不要覆盖的草稿");
  await page.locator("#codex-task-compose").click();
  await expect.poll(() => requests.find((entry) => entry.path === "/api/codex/tasks/task-side/message")?.body).toEqual({
    message: "刚才程序意外退出了，请你核对上下文，接着之前的进度继续执行任务。",
    side: false,
  });
  await expect(page.locator("#codex-task-message")).toHaveValue("不要覆盖的草稿");

  await page.locator("#codex-wp-menu summary").click();
  await expect(page.locator("#codex-wp-menu")).toHaveAttribute("open", "");
  await page.locator("#codex-wp-desensitized").click();
  await expect.poll(() => requests.find((entry) => entry.path === "/api/codex/tasks/task-main/message")?.body).toMatchObject({
    side: true,
  });
  const wpRequest = requests.find((entry) => entry.path === "/api/codex/tasks/task-main/message")?.body;
  expect(wpRequest.message).toContain("脱敏版WriteUp");
  expect(wpRequest.message).toContain("不得写出该题目的靶机和端口信息");
  await expect(page.locator("#codex-wp-menu")).not.toHaveAttribute("open", "");
  await expect(page.locator(".toast.success", { hasText: "脱敏版 WP Side 分支已创建" })).toBeVisible();
});

test("设置页支持默认比赛、三种模型协议与请求包登录", async ({ page }) => {
  const config = {
    configured: true,
    matchUrl: "",
    matchTitle: "Regression CTF",
    modelBaseUrl: "",
    modelName: "",
    modelProtocol: "openai-chat",
    codexBaseUrl: "http://model",
    codexModel: "deepseek-v4-flash",
    codexProtocol: "openai-responses",
    codexMaxConcurrency: 10,
    codexSystemPrompt: "",
    codexCtfSkillsEnabled: true,
    codexAutoResumeInterrupted: false,
    modelApiKeyConfigured: false,
    codexApiKeyConfigured: true,
  };
  let importedRequest = null;
  const { requests } = await openWorkbench(page, {
    handler: async ({ route, url, body }) => {
      if (url.pathname === "/api/config") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(success(config)),
        });
        return true;
      }
      if (url.pathname === "/api/platform/logout") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(success({ ...config, authenticated: false })),
        });
        return true;
      }
      if (url.pathname === "/api/platform/import-session") {
        importedRequest = body;
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(success({
            ...config,
            matchUrl: "https://match.ichunqiu.com/2026qwbs10",
            authenticated: true,
            authStatus: { authenticated: true, teamName: "QA Team" },
          })),
        });
        return true;
      }
      if (url.pathname === "/api/platform/session-export") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(success({
            session: "ichunqiu-login-v1:portable-session-value",
            browserScript: "localStorage.setItem('source-id','session');location.reload();",
            browserLogin: {
              matchUrl: "https://match.ichunqiu.com/2026qwbs10",
              sourceId: "source-id",
              token: "login:test",
              cookies: [],
            },
          })),
        });
        return true;
      }
      return false;
    },
  });

  await page.locator("#open-settings").click();
  await expect(page.locator("#setting-match-url")).toHaveValue("https://match.ichunqiu.com/2026qwbs10");
  await expect(page.locator("#setting-model-protocol option")).toHaveCount(3);
  await expect(page.locator("#settings-dialog .dialog-header h2")).toBeHidden();
  await page.locator('[data-settings-tab="codex"]').click();
  await expect(page.locator("#setting-codex-protocol option")).toHaveCount(3);
  await page.locator("#setting-codex-protocol").selectOption("openai-chat");
  await expect(page.locator("#setting-codex-protocol")).toHaveValue("openai-chat");
  await page.locator("#fetch-codex-models").click();
  await expect.poll(() => requests.find((request) => request.path === "/api/models")?.body?.protocol).toBe("openai-chat");
  await page.locator('[data-settings-tab="platform"]').click();
  await expect(page.locator('#login-mode-password, #login-mode-sms, #login-mode-session')).toHaveCount(3);

  page.once("dialog", (dialog) => dialog.accept());
  await page.locator("#platform-logout").click();
  await expect(page.locator("#platform-login")).toBeVisible();
  const loginModeRows = await page.locator("#login-mode-password, #login-mode-sms, #login-mode-session").evaluateAll(
    (buttons) => buttons.map((button) => Math.round(button.getBoundingClientRect().top)),
  );
  expect(new Set(loginModeRows).size).toBe(1);
  await page.locator("#login-mode-session").click();
  await expect(page.locator("#session-login-form")).toBeVisible();
  await expect(page.locator("#session-request-packet")).toHaveValue("");
  await page.locator("#session-request-packet").fill(
    "POST /match/team/info HTTP/1.1\nCookie: PHPSESSID=session-value\nReferer: https://match.ichunqiu.com/2026qwbs10\n\n{\"k\":\"match-key\",\"token\":\"login:test\"}",
  );
  await page.locator("#session-login").click();

  await expect.poll(() => importedRequest?.rawRequest).toContain("PHPSESSID=session-value");
  expect(importedRequest?.matchUrl).toBe("https://match.ichunqiu.com/2026qwbs10");
  await expect(page.locator("#session-login-status")).toHaveText("登录态导入成功");
  await expect(page.locator("#platform-auth-status")).toContainText("已登录");
  await expect(page.locator("#session-request-packet")).toHaveValue("ichunqiu-login-v1:portable-session-value");
  await expect(page.locator("#platform-authenticated-actions")).toBeVisible();
  await page.locator("#session-export-menu summary").click();
  await expect(page.locator("#copy-agent-session")).toBeVisible();
  await expect(page.locator("#copy-browser-session")).toBeVisible();
  await page.evaluate(() => {
    document.documentElement.dataset.gcsisBrowserHelper = "test-extension";
    Object.defineProperty(globalThis, "chrome", {
      configurable: true,
      value: {
        runtime: {
          lastError: null,
          sendMessage: (_extensionId, _message, callback) => callback({
            ok: true,
            message: "已注入登录态并打开比赛页面",
          }),
        },
      },
    });
  });
  await page.locator("#copy-browser-session").click();
  await expect(page.locator(".toast.success", { hasText: "已注入登录态并打开比赛页面" })).toBeVisible();
});

test("模型配置全屏页面支持多提供商、轮询和重试策略", async ({ page }) => {
  const { requests } = await openWorkbench(page);
  await page.locator("#open-model-routing").click();
  const routingPage = page.locator("#model-routing-page");
  await expect(routingPage).toBeVisible();
  await expect(page.locator(".model-routing-page-header")).toBeHidden();
  await expect(page.locator("dialog#model-routing-dialog")).toHaveCount(0);
  await expect(page.locator(".model-provider-item")).toHaveCount(1);

  const rows = page.locator(".model-provider-item");
  await rows.nth(0).locator(".provider-name").fill("Provider A");
  await rows.nth(0).locator(".provider-base-url").fill("https://a.example/v1");
  await rows.nth(0).locator(".provider-model").fill("model-a");
  await rows.nth(0).locator(".provider-protocol").selectOption("anthropic");
  await rows.nth(0).locator(".provider-test").click();
  await expect(rows.nth(0).locator(".provider-test-result")).toContainText("连接成功");
  await expect(rows.nth(0).locator(".provider-test-result")).toContainText("42ms");
  await rows.nth(0).locator(".provider-active").check();
  await expect(rows.nth(0)).toHaveClass(/is-active-provider/);
  await expect(page.locator("#model-active-provider-name")).toHaveText("Provider A");
  await page.locator(".model-pool-switch").click();
  await expect(page.locator("#model-pool-state")).toHaveText("已关闭");
  await page.locator(".model-pool-switch").click();

  await page.locator("#add-model-provider").click();
  await expect(rows).toHaveCount(2);
  await rows.nth(1).locator(".provider-name").fill("Provider B");
  await rows.nth(1).locator(".provider-base-url").fill("https://b.example/v1");
  await rows.nth(1).locator(".provider-model").fill("model-b");
  await rows.nth(1).locator(".provider-protocol").selectOption("openai-responses");
  await rows.nth(1).locator(".provider-pool").uncheck();
  await rows.nth(1).locator(".provider-enabled").uncheck();
  await expect(page.locator("#model-active-provider-count")).toHaveText("1");
  await expect(page.locator("#model-total-provider-count")).toHaveText("共 2 个");

  await page.locator('[data-model-tab="routing"]').click();
  await expect(page.locator('[data-model-panel="providers"]')).toBeHidden();
  await page.locator('[data-routing="failover"]').click();
  await expect(page.locator("#model-strategy-summary")).toHaveText("故障转移");
  await page.locator("#model-retry-delays").fill("1,5,10,10,15");
  await page.locator("#model-recovery-interval").fill("60");
  await expect(page.locator("#codex-auto-compact-limit")).toHaveValue("500000");
  await page.locator("#save-model-routing").click();
  const activeProviderId = await rows.nth(0).getAttribute("data-provider-id");

  await expect.poll(() => requests.find((request) => request.method === "PUT" && request.path === "/api/config")?.body).toMatchObject({
    modelActiveProviderId: activeProviderId,
    modelPoolEnabled: true,
    modelRoutingStrategy: "failover",
    modelRetryDelays: [1, 5, 10, 10, 15],
    modelRecoveryInterval: 60,
    codexAutoCompactTokenLimit: 500000,
  });
  const saved = requests.find((request) => request.method === "PUT" && request.path === "/api/config")?.body;
  expect(saved.modelProviders).toHaveLength(2);
  expect(saved.modelProviders[0]).toMatchObject({ name: "Provider A", protocol: "anthropic", enabled: true, poolExclude: false });
  expect(saved.modelProviders[1]).toMatchObject({ name: "Provider B", protocol: "openai-responses", enabled: false, poolExclude: true });
  await expect(page.locator("#model-routing-status")).toContainText("已保存");
  await page.locator("#show-exercise-view").click();
  await expect(routingPage).toBeHidden();
});

test("附件管理页突出缺失状态并支持筛选", async ({ page }) => {
  await openWorkbench(page);
  await page.locator("#open-attachment-manager").click();
  const dialog = page.locator("#attachment-manager-dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog).toHaveClass(/is-page-dialog/);
  await expect(page.locator("#attachment-missing-count")).toHaveText("1");
  await expect(page.locator("#attachment-existing-count")).toHaveText("0");
  await expect(page.locator("#attachment-total-count")).toHaveText("1");

  await page.locator('[data-attachment-filter="missing"]').click();
  await expect(page.locator(".attachment-file-row")).toHaveCount(1);
  await expect(page.locator(".attachment-file-status")).toContainText("缺失");

  await page.locator('[data-attachment-filter="ready"]').click();
  await expect(page.locator("#attachment-manager-list")).toContainText("没有符合当前筛选条件的附件");
  await page.locator("#attachment-search").fill("不存在");
  await expect(page.locator("#attachment-manager-list")).toContainText("没有符合当前筛选条件的附件");
});

test("登录态验证较慢时仍可绑定并进入登录", async ({ page }) => {
  let releaseAuth = () => {};
  let markAuthStarted = () => {};
  const authStarted = new Promise((resolve) => {
    markAuthStarted = resolve;
  });
  const authGate = new Promise((resolve) => {
    releaseAuth = resolve;
  });
  const config = {
    configured: true,
    matchUrl: "https://match.ichunqiu.com/2026qwbs10",
    matchTitle: "Regression CTF",
    modelBaseUrl: "",
    modelName: "",
    modelProtocol: "openai-chat",
    codexBaseUrl: "http://model",
    codexModel: "deepseek-v4-flash",
    codexProtocol: "openai-responses",
    codexMaxConcurrency: 10,
    codexSystemPrompt: "",
    codexCtfSkillsEnabled: true,
    codexAutoResumeInterrupted: false,
    modelApiKeyConfigured: false,
    codexApiKeyConfigured: true,
  };
  await installApiMock(page, {
    handler: async ({ route, url }) => {
      if (url.pathname === "/api/platform/auth") {
        markAuthStarted();
        await authGate;
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(success({ authenticated: true, teamName: "Late Auth" })),
        });
        return true;
      }
      if (url.pathname === "/api/platform/bind") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(success({
            ...config,
            authenticated: false,
            authStatus: { authenticated: false, message: "尚未登录" },
          })),
        });
        return true;
      }
      return false;
    },
  });
  await page.goto("/");

  await page.waitForSelector("#open-settings");
  await authStarted;
  await page.locator("#open-settings").click();
  await expect(page.locator("#settings-dialog")).toHaveAttribute("open", "");
  await expect(page.locator("#platform-login")).toBeVisible();
  await expect(page.locator("#platform-auth-status")).toHaveText("正在验证登录态");

  await page.locator("#bind-match").click();
  await expect(page.locator("#platform-login")).toBeVisible();
  await expect(page.locator("#platform-auth-status")).toHaveText("待登录");
  releaseAuth();
  await page.waitForTimeout(200);
  await expect(page.locator("#platform-login")).toBeVisible();
  await expect(page.locator("#platform-auth-status")).toHaveText("待登录");
});

test("侧栏工作区在桌面和移动端保持稳定布局", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openWorkbench(page);

  const desktop = await page.evaluate(() => {
    const sidebar = document.querySelector("#app-sidebar").getBoundingClientRect();
    const topbar = document.querySelector(".topbar").getBoundingClientRect();
    const workspace = document.querySelector("#exercise-view").getBoundingClientRect();
    return {
      sidebarRight: sidebar.right,
      topbarLeft: topbar.left,
      topbarHeight: topbar.height,
      workspaceLeft: workspace.left,
      workspaceTop: workspace.top,
      overviewScrollHeight: document.querySelector(".topbar-overview").scrollHeight,
      overviewClientHeight: document.querySelector(".topbar-overview").clientHeight,
      overflow: document.documentElement.scrollWidth - window.innerWidth,
    };
  });
  expect(desktop.sidebarRight).toBeGreaterThanOrEqual(199);
  expect(desktop.topbarLeft).toBeGreaterThanOrEqual(desktop.sidebarRight - 1);
  expect(desktop.topbarHeight).toBeLessThan(90);
  expect(desktop.workspaceLeft).toBeGreaterThan(desktop.sidebarRight);
  expect(desktop.workspaceTop).toBeGreaterThan(desktop.topbarHeight);
  expect(desktop.overviewScrollHeight).toBeLessThanOrEqual(desktop.overviewClientHeight + 1);
  expect(desktop.overflow).toBeLessThanOrEqual(1);

  await page.locator("#collapse-sidebar").click();
  await expect(page.locator("body")).toHaveClass(/sidebar-collapsed/);
  await expect.poll(async () => (await page.locator("#app-sidebar").boundingBox()).width).toBeLessThan(90);
  await expect(page.locator("#show-exercise-view > span").first()).toBeHidden();
  expect(await page.evaluate(() => localStorage.getItem("gcsis.sidebar-collapsed"))).toBe("true");
  await page.reload();
  await expect(page.locator("body")).toHaveClass(/sidebar-collapsed/);
  await expect(page.locator("#startup-faults")).toBeHidden();
  await expect.poll(async () => (await page.locator("#app-sidebar").boundingBox()).width).toBeLessThan(90);
  await page.locator("#collapse-sidebar").click();
  await expect(page.locator("body")).not.toHaveClass(/sidebar-collapsed/);

  await page.locator("#show-competition-view").click();
  await expect(page.locator("#competition-view")).toBeVisible();
  await expect(page.locator("#exercise-view")).toBeHidden();
  await expect(page.locator("#overview-team-name")).not.toHaveText("尚未获取队伍信息");
  await page.locator("#overview-open-exercises").click();
  await expect(page.locator("#exercise-view")).toBeVisible();
  await expect(page.locator("#competition-view")).toBeHidden();

  await page.locator("#open-settings").click();
  const settingsBox = await page.locator("#settings-dialog").boundingBox();
  expect(settingsBox.x).toBeGreaterThanOrEqual(desktop.sidebarRight - 1);
  await page.locator("#show-exercise-view").click();

  await page.setViewportSize({ width: 1100, height: 900 });
  const mediumDesktop = await page.evaluate(() => {
    const overview = document.querySelector(".topbar-overview");
    const copy = document.querySelector(".topbar-team-copy");
    return {
      topbarHeight: Math.round(document.querySelector(".topbar").getBoundingClientRect().height),
      copyWidth: Math.round(copy.getBoundingClientRect().width),
      scrollHeight: overview.scrollHeight,
      clientHeight: overview.clientHeight,
    };
  });
  expect(mediumDesktop.topbarHeight).toBeLessThan(90);
  expect(mediumDesktop.copyWidth).toBeGreaterThan(140);
  expect(mediumDesktop.scrollHeight).toBeLessThanOrEqual(mediumDesktop.clientHeight + 1);
  await page.setViewportSize({ width: 1440, height: 900 });

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator("#open-sidebar")).toBeVisible();
  await page.locator("#open-sidebar").click();
  await expect(page.locator("body")).toHaveClass(/sidebar-open/);
  await expect(page.locator("#app-sidebar")).toBeVisible();
  const mobileOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(mobileOverflow).toBeLessThanOrEqual(1);
  await page.locator("#sidebar-scrim").click({ position: { x: 380, y: 800 } });
  await expect(page.locator("body")).not.toHaveClass(/sidebar-open/);
});

test("功能页拥有独立 URL 且可从 Codex 直接切换", async ({ page }) => {
  await installApiMock(page);
  await page.goto("/codex");
  await expect(page.locator("#codex-task-page")).toBeVisible();
  await expect(page).toHaveURL(/\/codex$/);

  await page.locator("#open-settings").click();
  await expect(page.locator("#settings-dialog")).toHaveAttribute("open", "");
  await expect(page.locator("#codex-task-page")).toBeHidden();
  await expect(page).toHaveURL(/\/settings$/);

  await page.locator("#show-exercise-view").click();
  await expect(page).toHaveURL(/\/exercises$/);
  await page.goBack();
  await expect(page).toHaveURL(/\/settings$/);
  await page.goBack();
  await expect(page).toHaveURL(/\/codex$/);
  await expect(page.locator("#codex-task-page")).toBeVisible();
});

test("容器下发后立即进入侧栏并支持续期", async ({ page }) => {
  let started = false;
  let detailLoads = 0;
  let renewCalls = 0;
  let expiresInMinutes = 55;
  const { requests } = await openWorkbench(page, {
    handler: async ({ route, request, url }) => {
      if (url.pathname === "/api/exercises/1/environment/start" && request.method() === "POST") {
        started = true;
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(success({ accepted: true })),
        });
        return true;
      }
      if (url.pathname === "/api/exercises/1/environment/renew" && request.method() === "POST") {
        renewCalls += 1;
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(success({ accepted: true })),
        });
        return true;
      }
      if (url.pathname === "/api/exercises/1" && request.method() === "GET") {
        detailLoads += 1;
        const ready = started && detailLoads >= 2;
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(success({
            id: 1,
            name: "Cold Forge",
            category: "Pwn",
            score: 500,
            description: "Cold Forge detail",
            attachment: { files: [] },
            endpointType: "docker",
            isNeedInit: !started,
            isNeedCheck: started && !ready,
            endpoints: ready ? [{
              exposeIps: ["10.0.0.8"],
              ports: ["8080"],
              users: [],
              portMappings: [],
              proxyIps: [],
              isProxy: false,
              expireTime: Date.now() + expiresInMinutes * 60 * 1000,
            }] : [],
            renewRemaining: ready ? Math.max(0, 2 - renewCalls) : 0,
            renewLimit: 3,
          })),
        });
        return true;
      }
      return false;
    },
  });

  await page.locator(".exercise-item", { hasText: "Cold Forge" }).click();
  await page.locator("#environment-action button", { hasText: "启动容器" }).click();
  await expect(page.locator("#active-environment-count")).toHaveText("1");
  await expect(page.locator(".active-environment-item")).toContainText("正在准备环境");

  await page.locator(".exercise-item", { hasText: "Warmup Crypto" }).click();
  await expect(page.locator(".active-environment-item"), "容器状态不应因切换题目而丢失").toContainText("可续期 2/3 次", { timeout: 5000 });
  await expect(page.locator(".active-environment-renew")).toBeDisabled();
  await expect(page.locator(".active-environment-renew")).toHaveAttribute("title", /后开放续期/);
  expiresInMinutes = 9;
  await page.locator("#refresh-active-environments").click();
  await expect(page.locator(".active-environment-renew")).toBeEnabled();
  await page.locator(".active-environment-renew").click();
  await expect.poll(() => renewCalls).toBe(1);
  expect(requests.some((item) => item.path === "/api/exercises/1/environment/renew")).toBeTruthy();
  await expect(page.locator(".active-environment-item")).toContainText("可续期 1/3 次");
});

test("容器准备轮询遇到临时错误会退避后继续", async ({ page }) => {
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  let started = false;
  let pollAttempts = 0;
  await openWorkbench(page, {
    handler: async ({ route, request, url }) => {
      if (url.pathname === "/api/exercises/1/environment/start" && request.method() === "POST") {
        started = true;
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(success({ accepted: true })),
        });
        return true;
      }
      if (url.pathname === "/api/exercises/1" && request.method() === "GET" && started) {
        pollAttempts += 1;
        if (pollAttempts === 1) {
          await route.fulfill({
            status: 503,
            contentType: "application/json",
            body: JSON.stringify({ error: { code: "TEMPORARY_UNAVAILABLE", message: "临时网络错误" } }),
          });
          return true;
        }
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(success({
            id: 1,
            name: "Cold Forge",
            category: "Pwn",
            description: "Cold Forge detail",
            attachment: { files: [] },
            endpointType: "docker",
            isNeedInit: false,
            isNeedCheck: false,
            endpoints: [{
              exposeIps: ["10.0.0.8"],
              ports: ["8080"],
              users: [],
              portMappings: [],
              proxyIps: [],
              isProxy: false,
              expireTime: Date.now() + 55 * 60 * 1000,
            }],
            renewRemaining: 2,
            renewLimit: 3,
          })),
        });
        return true;
      }
      return false;
    },
  });

  await page.locator(".exercise-item", { hasText: "Cold Forge" }).click();
  await page.locator("#environment-action button", { hasText: "启动容器" }).click();
  await expect(page.locator(".toast.success", { hasText: "环境启动请求已提交" })).toBeVisible();
  await expect(page.locator(".toast.success", { hasText: "容器已就绪" })).toBeVisible({ timeout: 10000 });
  expect(pollAttempts).toBeGreaterThanOrEqual(2);
  expect(pageErrors).toEqual([]);
});

test("Codex 会话树按页加载并显示总数", async ({ page }) => {
  const tasks = Array.from({ length: 75 }, (_, index) => task({
    id: `task-${index + 1}`,
    title: `分页会话 ${index + 1}`,
    exerciseId: 1,
    sessionId: `session-${index + 1}`,
  }));
  await openWorkbench(page, {
    handler: async ({ route, url }) => {
      if (url.pathname !== "/api/codex/tasks") return false;
      const offset = Number(url.searchParams.get("offset") || 0);
      const limit = Number(url.searchParams.get("limit") || 50);
      const pageTasks = tasks.slice(offset, offset + limit);
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(success({
          available: true,
          active: 0,
          limit: 10,
          total: tasks.length,
          offset,
          pageSize: limit,
          hasMore: offset + pageTasks.length < tasks.length,
          tasks: pageTasks.map(({ events, ...item }) => item),
        })),
      });
      return true;
    },
  });

  await expect(page.locator("#codex-active-count")).toHaveText("75");
  await page.locator("#open-codex-tasks").click();
  await expect.poll(() => page.locator(".codex-tree-item").count()).toBeGreaterThanOrEqual(50);
  await expect(page.locator("#codex-load-more-tasks")).toBeVisible();
  await page.locator("#codex-load-more-tasks").click();
  await expect.poll(() => page.locator(".codex-tree-item").count()).toBeGreaterThanOrEqual(75);
  await expect(page.locator("#codex-load-more-tasks")).toBeHidden();
});

test("Codex 会话支持搜索、状态筛选、压缩归档和双格式导出", async ({ page }) => {
  page.on("dialog", (dialog) => dialog.accept());
  await openWorkbench(page, {
    state: {
      codexTasks: [
        task({ id: "task-main", title: "正常工作流", status: "completed" }),
        task({ id: "task-broken", title: "Broken Workflow", status: "failed" }),
      ],
    },
  });
  await page.locator("#open-codex-tasks").click();
  await expect(page.locator("#codex-session-archive")).toHaveValue("active");
  await expect(page.locator(".codex-tree-item")).toHaveCount(2);

  await page.locator("#codex-session-search").fill("Broken");
  await expect(page.locator(".codex-tree-item")).toHaveCount(1);
  await expect(page.locator(".codex-tree-item")).toContainText("Broken Workflow");

  await page.locator("#codex-session-search").fill("");
  await page.locator("#codex-session-status").selectOption("failed");
  await expect(page.locator(".codex-tree-item")).toHaveCount(1);
  await emitSse(page, {
    type: "codex.task",
    resourceId: "task-new-completed",
    data: task({ id: "task-new-completed", title: "不符合筛选", status: "completed" }),
  });
  await expect(page.locator(".codex-tree-item")).toHaveCount(1);
  await page.locator(".codex-task-select").check();
  await page.locator("#codex-archive-selected").click();
  await expect(page.locator(".toast.success")).toContainText("压缩归档");
  await expect(page.locator(".codex-tree-item")).toHaveCount(0);

  await page.locator("#codex-session-status").selectOption("");
  await expect(page.locator(".codex-tree-item")).toHaveCount(1);
  await expect(page.locator(".codex-tree-item")).toContainText("正常工作流");
  await page.locator("#open-codex-archive").click();
  await expect(page).toHaveURL(/\/codex\/archive$/);
  await expect(page.locator(".codex-tree-item")).toHaveCount(1);
  await page.locator(".codex-task-select").check();
  await expect(page.locator("#codex-export-selected")).toBeEnabled();
  await page.locator("#codex-export-format").selectOption("markdown");
  const markdownDownload = page.waitForEvent("download");
  await page.locator("#codex-export-selected").click();
  expect((await markdownDownload).suggestedFilename()).toMatch(/^codex-sessions-.*\.md$/);
  await page.locator("#codex-export-format").selectOption("json");
  const jsonDownload = page.waitForEvent("download");
  await page.locator("#codex-export-selected").click();
  expect((await jsonDownload).suggestedFilename()).toMatch(/^codex-sessions-.*\.json$/);
});

test("Codex 会话支持多选批量删除", async ({ page }) => {
  page.on("dialog", (dialog) => dialog.accept());
  const { requests } = await openWorkbench(page, {
    state: {
      codexTasks: [
        task({ id: "task-one", title: "待删除一" }),
        task({ id: "task-two", title: "待删除二" }),
      ],
    },
  });
  await page.locator("#open-codex-tasks").click();
  const checkboxes = page.locator(".codex-task-select");
  await checkboxes.nth(0).check();
  await checkboxes.nth(1).check();
  await expect(page.locator("#codex-delete-selected")).toBeEnabled();
  await page.locator("#codex-delete-selected").click();
  await expect.poll(() => requests.find((request) => request.path === "/api/codex/tasks/delete")?.body?.taskIds?.length).toBe(2);
  await expect(page.locator(".codex-tree-item")).toHaveCount(0);
});
