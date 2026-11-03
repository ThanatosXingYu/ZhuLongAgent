const ALLOWED_WORKBENCH_HOSTS = new Set(["127.0.0.1", "localhost"]);
const COMPETITION_HOST_SUFFIX = ".ichunqiu.com";

chrome.runtime.onMessageExternal.addListener((message, sender, sendResponse) => {
  if (message?.type !== "GCSIS_INJECT_ICHUNQIU_SESSION") return false;
  void handleInjection(message, sender)
    .then((result) => sendResponse(result))
    .catch((error) => sendResponse({ ok: false, message: error.message || String(error) }));
  return true;
});

async function handleInjection(message, sender) {
  const apiBase = normalizeWorkbenchOrigin(message.apiBase || sender.url);
  const response = await fetch(`${apiBase}/api/platform/session-export`, {
    method: "GET",
    credentials: "omit",
    cache: "no-store",
    headers: { "X-GCSIS-Action": "platform-auth" },
  });
  const envelope = await response.json();
  if (!response.ok || !envelope.ok) {
    throw new Error(envelope.error?.message || `读取登录态失败（HTTP ${response.status}）`);
  }
  const login = envelope.data?.browserLogin;
  if (!login?.matchUrl || !login?.sourceId || !login?.token) {
    throw new Error("登录态缺少浏览器注入所需的信息");
  }
  const target = new URL(login.matchUrl);
  if (!isCompetitionHost(target.hostname)) {
    throw new Error("比赛地址不是受支持的 i春秋域名");
  }
  await setCookies(login.cookies || []);
  const tab = await findOrCreateCompetitionTab(target.origin);
  await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: injectSession,
    args: [String(login.sourceId), String(login.token)],
  });
  await chrome.tabs.update(tab.id, { url: target.href, active: true });
  return { ok: true, message: "已注入登录态并打开比赛页面" };
}

function normalizeWorkbenchOrigin(raw) {
  const url = new URL(raw);
  if (!["http:", "https:"].includes(url.protocol) || !ALLOWED_WORKBENCH_HOSTS.has(url.hostname)) {
    throw new Error("浏览器助手只接受本机工作台请求");
  }
  return url.origin;
}

function isCompetitionHost(hostname) {
  const normalized = String(hostname || "").toLowerCase();
  return normalized === "ichunqiu.com" || normalized.endsWith(COMPETITION_HOST_SUFFIX);
}

async function setCookies(cookies) {
  await Promise.all(cookies.filter((cookie) => cookie?.name).map((cookie) => chrome.cookies.set({
    url: "https://match.ichunqiu.com/",
    name: String(cookie.name || ""),
    value: String(cookie.value || ""),
    path: "/",
    secure: true,
    sameSite: "lax",
  })));
}

async function findOrCreateCompetitionTab(origin) {
  const tabs = await chrome.tabs.query({ url: ["https://*.ichunqiu.com/*"] });
  if (tabs.length) return tabs[0];
  const tab = await chrome.tabs.create({ url: origin, active: true });
  await waitForTabReady(tab.id);
  return tab;
}

function waitForTabReady(tabId) {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener);
      reject(new Error("比赛页面加载超时"));
    }, 15000);
    function listener(updatedId, info) {
      if (updatedId !== tabId || info.status !== "complete") return;
      clearTimeout(timeout);
      chrome.tabs.onUpdated.removeListener(listener);
      resolve();
    }
    chrome.tabs.onUpdated.addListener(listener);
  });
}

function injectSession(sourceId, token) {
  localStorage.setItem(sourceId, JSON.stringify({
    user_id: "ichunqiu-agent-imported",
    token,
    user_name: "",
  }));
}
