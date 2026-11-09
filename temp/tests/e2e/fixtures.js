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
    config: {
      configured: true, matchUrl: "https://match.example/competition", matchTitle: "Regression CTF",
      modelBaseUrl: "", modelName: "", codexBaseUrl: "http://model", codexModel: "deepseek-v4-flash",
      modelActiveProviderId: "", modelPoolEnabled: false, modelPoolBindFallback: false,
      modelProtocol: "openai-chat", codexProtocol: "openai-responses",
      codexMaxConcurrency: 10, codexAutoCompactTokenLimit: 500000, codexSystemPrompt: "", codexCtfSkillsEnabled: true,
      codexAutoResumeInterrupted: false, modelApiKeyConfigured: false, codexApiKeyConfigured: true,
      modelProviders: [], modelRetryPolicy: {
        connect: { attempts: 0, interval_ms: 0 }, empty: { attempts: 0, interval_ms: 0 },
        stream: { attempts: 0, interval_ms: 0 }, breaker: { attempts: 0, interval_ms: 0 },
        intent: { attempts: 0, interval_ms: 0 },
      },
    },
    toolCatalog: {
      status: "idle", root: "work/tools", tools: [
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
      case "/api/config": {
        if (request.method() === "PUT" && body && typeof body === "object") {
          const providers = Array.isArray(body.modelProviders)
            ? body.modelProviders.map((provider) => ({
                ...provider,
                apiKey: "",
                apiKeyConfigured: Boolean(provider.apiKey) || Boolean(
                  state.config.modelProviders?.find((item) => item.id === provider.id)?.apiKeyConfigured,
                ),
                apiKeyMasked: provider.apiKey ? "sk-…test" : (
                  state.config.modelProviders?.find((item) => item.id === provider.id)?.apiKeyMasked || ""
                ),
              }))
            : state.config.modelProviders;
          state.config = { ...state.config, ...body, modelProviders: providers };
        }
        data = state.config;
        break;
      }
      case "/api/llm/profiles": {
        if (request.method() === "POST" && body && typeof body === "object") {
          const existing = state.config.modelProviders.find((item) => item.id === body.id);
          const id = body.id || `provider-${state.config.modelProviders.length + 1}`;
          const profile = {
            ...body, id, apiKey: "", apiKeyConfigured: Boolean(body.apiKey) || Boolean(existing?.apiKeyConfigured),
            apiKeyMasked: body.apiKey ? "sk-…test" : (existing?.apiKeyMasked || ""),
          };
          const next = state.config.modelProviders.filter((item) => item.id !== id);
          next.push(profile);
          state.config = { ...state.config, modelProviders: next, modelActiveProviderId: state.config.modelActiveProviderId || id };
        }
        data = request.method() === "POST"
          ? { id: body?.id || state.config.modelProviders.at(-1)?.id, config: state.config }
          : { profiles: state.config.modelProviders.map((provider) => ({
              ...provider,
              isDefault: provider.id === state.config.modelActiveProviderId,
              is_default: provider.id === state.config.modelActiveProviderId,
            })) };
        break;
      }
      case "/api/llm/profiles/active": {
        state.config = { ...state.config, modelActiveProviderId: body.id };
        data = { ok: true, config: state.config };
        break;
      }
      case "/api/llm/pool": data = {
        enabled: state.config.modelPoolEnabled, bind_fallback: state.config.modelPoolBindFallback,
        chain: state.config.modelProviders.map((provider, index) => ({
          profile_id: provider.id, name: provider.name, model: provider.model, format: provider.protocol,
          priority: provider.priority || 0, active: provider.id === state.config.modelActiveProviderId,
          excluded: Boolean(provider.poolExclude), state: "ok", fails: 0, trips: 0, cooldown_secs: 0,
        })),
      }; break;
      case "/api/llm/profiles/1":
      case "/api/llm/profiles/2":
      case "/api/llm/profiles/provider-1":
      case "/api/llm/profiles/provider-2": {
        const id = url.pathname.split("/").at(-1);
        state.config = {
          ...state.config,
          modelProviders: state.config.modelProviders.filter((provider) => provider.id !== id),
        };
        data = { deleted: id };
        break;
      }
      case "/api/llm/pool/reset": data = {
        enabled: state.config.modelPoolEnabled, bind_fallback: state.config.modelPoolBindFallback,
        chain: state.config.modelProviders.map((provider) => ({
          profile_id: provider.id, name: provider.name, model: provider.model, format: provider.protocol,
          priority: provider.priority || 0, active: provider.id === state.config.modelActiveProviderId,
          excluded: Boolean(provider.poolExclude), state: "ok", fails: 0, trips: 0, cooldown_secs: 0,
        })),
      }; break;
      case "/api/llm/retry-policy": {
        if (request.method() === "POST" && body && typeof body === "object") state.config.modelRetryPolicy = body;
        data = state.config.modelRetryPolicy;
        break;
      }
      case "/api/platform/auth": data = { authenticated: true, teamName: "QA Team", userName: "Tester", score: 100, rank: 1, memberCount: 2, organization: "Lab" }; break;
      case "/api/overview": data = { stagePoint: 100, stageRank: 1, solvedCount: 2, teamName: "QA Team", organization: "QA Lab" }; break;
      case "/api/exercises": data = exerciseGroups; break;
      case "/api/exercises/1": data = { ...exercises[0], score: 500, difficulty: "中等", category: "Pwn", description: "Cold Forge detail", attachment: { files: [{ name: "cold.zip", ext: ".zip", size: 1024 }] }, endpointType: "docker", isNeedInit: true, isNeedCheck: false, endpoints: [], renewRemaining: 0, renewLimit: 3 }; break;
      case "/api/exercises/2": data = { ...exercises[1], score: 100, category: "Crypto", description: "Warmup detail", attachment: { files: [] } }; break;
      case "/api/exercises/3": data = { ...exercises[2], score: 300, category: "Web", description: "Web detail", attachment: { files: [] }, endpointType: "docker", isNeedInit: true, isNeedCheck: false, endpoints: [], renewRemaining: 0, renewLimit: 3 }; break;
      case "/api/match-info": data = { note: "Regression note", rule: "Regression rule" }; break;
      case "/api/notices": data = [{ id: 11, title: "比赛公告", userName: "Admin", createdAt: "2026-09-15T00:00:00Z", isFile: false }]; break;
      case "/api/notices/11": data = { id: 11, title: "比赛公告", content: "公告正文", userName: "Admin", createdAt: "2026-09-15T00:00:00Z" }; break;
      case "/api/attachments": data = { totalExercises: 1, totalAttachments: 1, existingAttachments: 0, exercises: [{ id: 1, name: "Cold Forge", category: "Pwn", attachments: [{ name: "cold.zip", size: 1024, exists: false }] }] }; break;
      case "/api/attachments/downloads": data = state.attachmentTask; break;
      case "/api/attachments/sizes": data = { status: "completed", totalFiles: 1, completedFiles: 1 }; break;
      case "/api/tools": data = state.toolCatalog; break;
      case "/api/model-test": data = { model: body?.model || "model", reply: "OK", latencyMs: 42 }; break;
      case "/api/models": data = { models: ["model-a", "model-b"], kind: body?.kind || "model" }; break;
      case "/api/environment": data = { items: [], summary: "环境可用" }; break;
      case "/api/codex/tasks": {
        const query = (url.searchParams.get("query") || "").toLowerCase();
        const statuses = new Set((url.searchParams.get("status") || "").split(",").filter(Boolean));
        const archive = url.searchParams.get("archived") || "active";
        const offset = Number(url.searchParams.get("offset") || 0);
        const limit = Number(url.searchParams.get("limit") || 50);
        const filtered = state.codexTasks.filter((item) => {
          if (archive === "active" && item.archived) return false;
          if (archive === "archived" && !item.archived) return false;
          if (statuses.size && !statuses.has(item.status)) return false;
          if (!query) return true;
          return `${item.id} ${item.exerciseId} ${item.title || ""} ${item.sourceMessage || ""}`.toLowerCase().includes(query);
        });
        const page = filtered.slice(offset, offset + limit);
        data = {
          available: true,
          active: state.codexTasks.filter((item) => ["running", "queued"].includes(item.status)).length,
          limit: 10,
          total: filtered.length,
          offset,
          pageSize: limit,
          hasMore: offset + page.length < filtered.length,
          tasks: page.map(({ events, ...item }) => item),
        };
        break;
      }
      case "/api/codex/tasks/archive": {
        const ids = new Set(body?.taskIds || []);
        state.codexTasks = state.codexTasks.map((item) =>
          ids.has(item.id)
            ? {
                ...item,
                archived: Boolean(body?.archived),
                archivePath: body?.archived ? `codex-runs/_archives/${item.id}.tar.gz` : "",
                archiveSize: body?.archived ? 4096 : 0,
              }
            : item
        );
        data = { tasks: state.codexTasks.filter((item) => ids.has(item.id)) };
        break;
      }
      case "/api/codex/tasks/delete": {
        const ids = new Set(body?.taskIds || []);
        state.codexTasks = state.codexTasks.filter((item) => !ids.has(item.id));
        data = { deleted: [...ids], count: ids.size, partial: false };
        break;
      }
      default: {
        const codexTask = url.pathname.match(/^\/api\/codex\/tasks\/([^/]+)$/);
        if (codexTask && request.method() === "GET") data = state.codexTasks.find((item) => item.id === decodeURIComponent(codexTask[1])) || task();
        else if (codexTask && request.method() === "DELETE") {
          const taskId = decodeURIComponent(codexTask[1]);
          state.codexTasks = state.codexTasks.filter((item) => item.id !== taskId);
          data = { deleted: true };
        }
        else if (url.pathname.endsWith("/logs.txt")) return route.fulfill({ status: 200, contentType: "text/plain", body: "complete log" });
        else if (url.pathname.endsWith("/logs.json")) {
          const taskId = decodeURIComponent(url.pathname.split("/").at(-2));
          const selected = state.codexTasks.find((item) => item.id === taskId) || task();
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(selected.events || []) });
        }
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
