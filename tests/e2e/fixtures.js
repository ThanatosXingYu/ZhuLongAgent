import { expect } from "@playwright/test";

export const exercises = [
  { id: 1, name: "Cold Forge", category: "Pwn", order: 1, hasSolved: false, hasAttachment: true, hasEnvironment: true },
  { id: 2, name: "Warmup Crypto", category: "Crypto", order: 2, hasSolved: true, hasAttachment: false, hasEnvironment: false },
  { id: 3, name: "Web Gate", category: "Web", order: 3, hasSolved: false, hasAttachment: false, hasEnvironment: true },
];

export const exerciseGroups = [
  { id: "pwn", name: "Pwn", order: 1, corpus: [exercises[0]] },
  { id: "crypto", name: "Crypto", order: 2, corpus: [exercises[1]] },
  { id: "web", name: "Web", order: 3, corpus: [exercises[2]] },
];

export function success(data) {
  return { ok: true, data };
}

export function task(overrides = {}) {
  return {
    id: "task-main",
    exerciseId: 1,
    exerciseName: "Cold Forge",
    title: "主对话",
    mode: "run",
    status: "completed",
    sessionId: "session-main",
    createdAt: "2026-09-15T01:00:00Z",
    startedAt: "2026-09-15T01:00:01Z",
    finishedAt: "2026-09-15T01:00:02Z",
    logCount: 2,
    events: [
      { sequence: 1, roundNumber: 1, at: "2026-09-15T01:00:01Z", kind: "user.message", summary: "输出 123" },
      { sequence: 2, roundNumber: 1, at: "2026-09-15T01:00:02Z", kind: "agent.message", summary: "123" },
    ],
    output: "123",
    ...overrides,
  };
}

export async function installApiMock(page, overrides = {}) {
  const requests = [];
  const state = {
    codexTasks: [task()],
    attachmentTask: null,
    toolCatalog: {
      status: "idle", root: "tools", tools: [
        { name: "nmap", label: "Nmap", category: "web", categoryLabel: "Web", installed: false, supported: true, kind: "binary" },
      ],
    },
    ...overrides.state,
  };
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/api/events") return route.continue();
    let body = null;
    try { body = request.postDataJSON(); } catch { body = request.postData(); }
    requests.push({ method: request.method(), path: url.pathname, search: url.search, body });
    if (overrides.handler) {
      const result = await overrides.handler({ route, request, url, body, state, requests });
      if (result === true) return;
    }
    let data;
    switch (url.pathname) {
      case "/api/config": data = {
        configured: true, matchUrl: "https://match.example/competition", matchTitle: "Regression CTF",
        modelBaseUrl: "", modelName: "", codexBaseUrl: "http://model", codexModel: "deepseek-v4-flash",
        codexMaxConcurrency: 10, codexSystemPrompt: "", codexCtfSkillsEnabled: true,
        codexAutoResumeInterrupted: false, modelApiKeyConfigured: false, codexApiKeyConfigured: true,
      }; break;
      case "/api/platform/auth": data = { authenticated: true, teamName: "QA Team", userName: "Tester", score: 100, rank: 1, memberCount: 2, organization: "Lab" }; break;
      case "/api/overview": data = { score: 100, rank: 1, updatedAt: "2026-09-15T02:00:00Z", teamName: "QA Team" }; break;
      case "/api/exercises": data = exerciseGroups; break;
      case "/api/exercises/1": data = { ...exercises[0], score: 500, difficulty: "中等", category: "Pwn", description: "Cold Forge detail", attachment: { files: [{ name: "cold.zip", ext: ".zip", size: 1024 }] }, environment: { enabled: true, status: "stopped" } }; break;
      case "/api/exercises/2": data = { ...exercises[1], score: 100, category: "Crypto", description: "Warmup detail", attachment: { files: [] } }; break;
      case "/api/exercises/3": data = { ...exercises[2], score: 300, category: "Web", description: "Web detail", attachment: { files: [] }, environment: { enabled: true, status: "stopped" } }; break;
      case "/api/match-info": data = { note: "Regression note", rule: "Regression rule" }; break;
      case "/api/notices": data = [{ id: 11, title: "比赛公告", userName: "Admin", createdAt: "2026-09-15T00:00:00Z", isFile: false }]; break;
      case "/api/notices/11": data = { id: 11, title: "比赛公告", content: "公告正文", userName: "Admin", createdAt: "2026-09-15T00:00:00Z" }; break;
      case "/api/attachments": data = { totalExercises: 1, totalAttachments: 1, existingAttachments: 0, exercises: [{ id: 1, name: "Cold Forge", category: "Pwn", attachments: [{ name: "cold.zip", size: 1024, exists: false }] }] }; break;
      case "/api/attachments/downloads": data = state.attachmentTask; break;
      case "/api/attachments/sizes": data = { status: "completed", totalFiles: 1, completedFiles: 1 }; break;
      case "/api/tools": data = state.toolCatalog; break;
      case "/api/environment": data = { items: [], summary: "环境可用" }; break;
      case "/api/codex/tasks": data = { available: true, active: state.codexTasks.filter((item) => ["running", "queued"].includes(item.status)).length, limit: 10, tasks: state.codexTasks.map(({ events, ...item }) => item) }; break;
      default: {
        const codexTask = url.pathname.match(/^\/api\/codex\/tasks\/([^/]+)$/);
        if (codexTask && request.method() === "GET") data = state.codexTasks.find((item) => item.id === decodeURIComponent(codexTask[1])) || task();
        else if (url.pathname.endsWith("/logs.txt")) return route.fulfill({ status: 200, contentType: "text/plain", body: "complete log" });
        else if (url.pathname.endsWith("/logs.json")) return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ events: task().events }) });
        else data = {};
      }
    }
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(success(data)) });
  });
  return { requests, state };
}

export async function openWorkbench(page, options = {}) {
  const mock = await installApiMock(page, options);
  await page.goto("/");
  await expect(page.locator(".exercise-item")).toHaveCount(3);
  await expect(page.locator(".notice-item")).toHaveCount(1);
  await expect(page.locator("#realtime-status")).toHaveText("实时");
  return mock;
}

export async function emitSse(page, event) {
  return page.evaluate(async (payload) => {
    const response = await fetch("/__test/sse/emit", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    return response.json();
  }, event);
}
