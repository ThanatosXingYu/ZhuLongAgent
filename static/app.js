"use strict";

(() => {
  const SCORE_REFRESH_MS = 5000;
  const NOTICE_REFRESH_MS = 10000;
  const BUILD_POLL_MS = 2000;
  const BUILD_POLL_TIMEOUT_MS = 60000;
  const ATTACHMENT_TASK_POLL_MS = 500;
  const ATTACHMENT_SIZE_POLL_MS = 1000;
  const ACTIVE_ENVIRONMENT_STORAGE_KEY = "gcsis.active-environment-ids";
  const COLLAPSED_GROUP_STORAGE_KEY = "gcsis.collapsed-exercise-groups";
  const COLLAPSED_ATTACHMENT_CATEGORY_STORAGE_KEY = "gcsis.collapsed-attachment-categories";
  const ATTACHMENT_ACTION_HEADERS = Object.freeze({ "X-GCSIS-Action": "attachment-manager" });
  const PLATFORM_ACTION_HEADERS = Object.freeze({ "X-GCSIS-Action": "platform-auth" });
  const RICH_TEXT_TAGS = new Set(["A", "B", "BLOCKQUOTE", "BR", "CODE", "DIV", "EM", "H1", "H2", "H3", "H4", "HR", "I", "IMG", "LI", "OL", "P", "PRE", "S", "SPAN", "STRONG", "TABLE", "TBODY", "TD", "TH", "THEAD", "TR", "U", "UL"]);
  const DROPPED_RICH_TEXT_TAGS = new Set(["BUTTON", "EMBED", "FORM", "IFRAME", "INPUT", "MATH", "OBJECT", "SCRIPT", "STYLE", "SVG", "TEXTAREA"]);

  const state = {
    groups: [],
    notices: [],
    overview: null,
    matchInfo: null,
    detail: null,
    noticeDetail: null,
    attachmentCatalog: null,
    attachmentBusy: false,
    attachmentTask: null,
    attachmentTaskTimer: null,
    attachmentSizeTimer: null,
    attachmentSizeProbing: false,
    collapsedGroups: new Set(),
    collapsedAttachmentCategories: new Set(),
    trackedEnvironmentIds: new Set(),
    activeEnvironments: new Map(),
    environmentRefreshBusy: false,
    environmentRefreshQueued: false,
    environmentSyncVersion: 0,
    environmentBatchBusy: false,
    environmentStopBusy: new Set(),
    environmentsRestored: false,
    exercisesLoaded: false,
    selectedExerciseId: null,
    selectedNoticeId: null,
    aiExerciseId: null,
    codexAvailable: null,
    codexSystemPrompt: "",
    codexCtfSkillsEnabled: true,
    modelApiKeyConfigured: false,
    modelApiKeyMasked: "",
    codexApiKeyConfigured: false,
    codexApiKeyMasked: "",
    codexStartMode: "run",
    codexTasks: [],
    codexSelectedTaskId: null,
    codexPollTimer: null,
    codexTerminalSeen: new Set(),
    codexEventsFollowTail: true,
    toolManagerTimer: null,
    tools: [],
    toolSelection: new Set(),
    environmentStatus: null,
    scoreTimer: null,
    noticeTimer: null,
    pollToken: 0,
    configured: false,
    authenticated: false,
    authExpiryNoticeAt: 0,
  };

  const $ = (id) => document.getElementById(id);
  const els = {
    connection: $("connection-status"),
    connectionLabel: $("connection-status").querySelector(".connection-label"),
    teamSummary: $("team-summary"),
    score: $("score-value"),
    rank: $("rank-value"),
    overviewUpdated: $("overview-updated-at"),
    exerciseCount: $("exercise-count"),
    exerciseList: $("exercise-list"),
    selectedExerciseName: $("selected-exercise-name"),
    exerciseMeta: $("exercise-meta"),
    exerciseDetail: $("exercise-detail"),
    environmentAction: $("environment-action"),
    selectedExerciseRefresh: $("refresh-selected-exercise"),
    openAIPrompt: $("open-ai-prompt"),
    runCodex: $("run-codex"),
    runCodexPure: $("run-codex-pure"),
    flagForm: $("flag-form"),
    flagInput: $("flag-input"),
    flagLength: $("flag-length"),
    flagSubmit: $("flag-submit"),
    activeEnvironmentCount: $("active-environment-count"),
    activeEnvironmentList: $("active-environment-list"),
    activeEnvironmentRefresh: $("refresh-active-environments"),
    stopAllEnvironments: $("stop-all-environments"),
    noticeCount: $("notice-count"),
    noticeList: $("notice-list"),
    noticeDetail: $("notice-detail"),
    scoreRefresh: $("score-auto-refresh"),
    noticeRefresh: $("notice-auto-refresh"),
    matchDialog: $("match-info-dialog"),
    matchNote: $("match-note"),
    matchRule: $("match-rule"),
    aiPromptDialog: $("ai-prompt-dialog"),
    aiModelStatus: $("ai-model-status"),
    aiPromptText: $("ai-prompt-text"),
    aiCopyPrompt: $("copy-ai-prompt"),
    aiRun: $("run-ai-solver"),
    aiRunOutput: $("ai-run-output"),
    openCodexTasks: $("open-codex-tasks"),
    codexActiveCount: $("codex-active-count"),
    openSettings: $("open-settings"),
    openToolManager: $("open-tool-manager"),
    toolManagerDialog: $("tool-manager-dialog"),
    environmentStatusItems: $("environment-status-items"),
    environmentStatusSummary: $("environment-status-summary"),
    refreshEnvironmentStatus: $("refresh-environment-status"),
    settingsDialog: $("settings-dialog"),
    settingsForm: $("settings-form"),
    matchBindForm: $("match-bind-form"),
    settingsStatus: $("settings-status"),
    settingMatchUrl: $("setting-match-url"),
    settingPlatformToken: $("setting-platform-token"),
    bindMatch: $("bind-match"),
    platformBindStatus: $("platform-bind-status"),
    platformAuthStatus: $("platform-auth-status"),
    platformLogout: $("platform-logout"),
    platformTeamInfo: $("platform-team-info"),
    platformTeamName: $("platform-team-name"),
    platformUserName: $("platform-user-name"),
    platformTeamScore: $("platform-team-score"),
    platformTeamRank: $("platform-team-rank"),
    platformMemberCount: $("platform-member-count"),
    platformOrganization: $("platform-organization"),
    platformLogin: $("platform-login"),
    loginModePassword: $("login-mode-password"),
    loginModeSms: $("login-mode-sms"),
    passwordLoginForm: $("password-login-form"),
    passwordAccount: $("password-account"),
    passwordValue: $("password-value"),
    passwordCaptchaWrap: $("password-captcha-wrap"),
    passwordCaptcha: $("password-captcha"),
    passwordImageCode: $("password-image-code"),
    refreshPasswordCaptcha: $("refresh-password-captcha"),
    passwordLogin: $("password-login"),
    passwordLoginStatus: $("password-login-status"),
    smsLoginForm: $("sms-login-form"),
    smsAreaId: $("sms-area-id"),
    smsPhone: $("sms-phone"),
    rotateCaptchaImage: $("rotate-captcha-image"),
    rotateCaptchaAngle: $("rotate-captcha-angle"),
    rotateCaptchaOutput: $("rotate-captcha-output"),
    refreshRotateCaptcha: $("refresh-rotate-captcha"),
    smsCode: $("sms-code"),
    sendSmsCode: $("send-sms-code"),
    smsLogin: $("sms-login"),
    smsLoginStatus: $("sms-login-status"),
    settingModelBaseUrl: $("setting-model-base-url"),
    settingModelName: $("setting-model-name"),
    settingModelApiKey: $("setting-model-api-key"),
    fetchModels: $("fetch-models"),
    modelOptions: $("model-options"),
    settingCodexBaseUrl: $("setting-codex-base-url"),
    settingCodexModel: $("setting-codex-model"),
    settingCodexApiKey: $("setting-codex-api-key"),
    fetchCodexModels: $("fetch-codex-models"),
    codexModelOptions: $("codex-model-options"),
    settingCodexConcurrency: $("setting-codex-concurrency"),
    settingCodexSystemPrompt: $("setting-codex-system-prompt"),
    settingCodexCtfSkills: $("setting-codex-ctf-skills"),
    toolManagerStatus: $("tool-manager-status"),
    toolManagerProgress: $("tool-manager-progress"),
    toolManagerProgressTitle: $("tool-manager-progress-title"),
    toolManagerProgressCount: $("tool-manager-progress-count"),
    toolManagerProgressBar: $("tool-manager-progress-bar"),
    toolManagerProgressFile: $("tool-manager-progress-file"),
    toolManagerProgressSize: $("tool-manager-progress-size"),
    toolManagerProgressSpeed: $("tool-manager-progress-speed"),
    toolManagerProgressEta: $("tool-manager-progress-eta"),
    cancelToolInstall: $("cancel-tool-install"),
    toolManagerList: $("tool-manager-list"),
    installSelectedTools: $("install-selected-tools"),
    installAllTools: $("install-all-tools"),
    saveSettings: $("save-settings"),
    codexTaskDialog: $("codex-task-dialog"),
    codexTaskList: $("codex-task-list"),
    codexTaskStatus: $("codex-task-status"),
    codexTaskMeta: $("codex-task-meta"),
    codexTaskEvents: $("codex-task-events"),
    codexTaskOutput: $("codex-task-output"),
    codexTaskResume: $("codex-task-resume"),
    codexTaskWriteup: $("codex-task-writeup"),
    continueCodexTask: $("continue-codex-task"),
    cancelCodexTask: $("cancel-codex-task"),
    deleteCodexTask: $("delete-codex-task"),
    openCodexTerminal: $("open-codex-terminal"),
    codexTaskMessage: $("codex-task-message"),
    codexTaskFollowUp: $("codex-task-follow-up"),
    codexTaskSide: $("codex-task-side"),
    codexPromptDialog: $("codex-prompt-dialog"),
    codexStartPrompt: $("codex-start-prompt"),
    codexPromptMode: $("codex-prompt-mode"),
    confirmCodexStart: $("confirm-codex-start"),
    cancelCodexStart: $("cancel-codex-start"),
    attachmentDialog: $("attachment-manager-dialog"),
    attachmentSummary: $("attachment-summary"),
    attachmentCheck: $("check-attachments"),
    attachmentDownloadAll: $("download-all-attachments"),
    attachmentList: $("attachment-manager-list"),
    attachmentProgress: $("attachment-progress"),
    attachmentProgressTitle: $("attachment-progress-title"),
    attachmentProgressCount: $("attachment-progress-count"),
    attachmentProgressPercent: $("attachment-progress-percent"),
    attachmentProgressBar: $("attachment-progress-bar"),
    attachmentProgressFile: $("attachment-progress-file"),
    attachmentProgressFileSize: $("attachment-progress-file-size"),
    attachmentProgressBytes: $("attachment-progress-bytes"),
    attachmentProgressSpeed: $("attachment-progress-speed"),
    attachmentProgressEta: $("attachment-progress-eta"),
    attachmentProgressFailed: $("attachment-progress-failed"),
    attachmentPause: $("pause-attachment-download"),
    attachmentResume: $("resume-attachment-download"),
    attachmentCancel: $("cancel-attachment-download"),
    toastRegion: $("toast-region"),
  };

  class RequestError extends Error {
    constructor(message, code = "REQUEST_ERROR", status = 0) {
      super(message);
      this.name = "RequestError";
      this.code = code;
      this.status = status;
    }
  }

  function createElement(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function renderRichText(raw, className) {
    const container = createElement("div", className);
    const documentNode = new DOMParser().parseFromString(String(raw || ""), "text/html");
    if (![...documentNode.body.childNodes].some((node) => node.nodeType === Node.ELEMENT_NODE)) {
      container.classList.add("is-plain");
    }
    documentNode.body.childNodes.forEach((node) => appendRichTextNode(container, node));
    return container;
  }

  function appendRichTextNode(parent, source) {
    if (source.nodeType === Node.TEXT_NODE) {
      parent.append(document.createTextNode(source.textContent || ""));
      return;
    }
    if (source.nodeType !== Node.ELEMENT_NODE) return;
    const tag = source.tagName.toUpperCase();
    if (DROPPED_RICH_TEXT_TAGS.has(tag)) return;
    if (!RICH_TEXT_TAGS.has(tag)) {
      source.childNodes.forEach((child) => appendRichTextNode(parent, child));
      return;
    }
    if (tag === "A") {
      const href = safeURL(source.getAttribute("href"));
      if (!href) {
        source.childNodes.forEach((child) => appendRichTextNode(parent, child));
        return;
      }
      const link = document.createElement("a");
      link.href = href;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      if (source.getAttribute("title")) link.title = source.getAttribute("title");
      source.childNodes.forEach((child) => appendRichTextNode(link, child));
      parent.append(link);
      return;
    }
    if (tag === "IMG") {
      const src = safeURL(source.getAttribute("src"));
      if (!src) return;
      const image = document.createElement("img");
      image.src = src;
      image.alt = source.getAttribute("alt") || "题目图片";
      image.loading = "lazy";
      parent.append(image);
      return;
    }
    const element = document.createElement(tag.toLowerCase());
    source.childNodes.forEach((child) => appendRichTextNode(element, child));
    parent.append(element);
  }

  function icon(name) {
    const node = document.createElement("i");
    node.dataset.lucide = name;
    node.setAttribute("aria-hidden", "true");
    return node;
  }

  function refreshIcons() {
    if (window.lucide && typeof window.lucide.createIcons === "function") {
      window.lucide.createIcons({ attrs: { "stroke-width": 1.8 } });
    }
  }

  function setConnection(kind) {
    els.connection.classList.remove("is-connecting", "is-connected", "is-error");
    els.connection.classList.add(`is-${kind}`);
    els.connectionLabel.textContent = kind === "connected" ? "已连接" : kind === "error" ? "连接异常" : "正在连接";
  }

  function formatNumber(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
    return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(Number(value));
  }

  function formatBytes(value) {
    const bytes = Number(value);
    if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
    const units = ["B", "KiB", "MiB", "GiB"];
    const unit = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
    const amount = bytes / (1024 ** unit);
    return `${amount.toFixed(unit === 0 || amount >= 10 ? 0 : 1)} ${units[unit]}`;
  }

  function formatDuration(value) {
    const seconds = Math.max(0, Math.round(Number(value) || 0));
    if (seconds < 60) return `${seconds} 秒`;
    const minutes = Math.floor(seconds / 60);
    const remainder = seconds % 60;
    if (minutes < 60) return `${minutes} 分 ${remainder} 秒`;
    const hours = Math.floor(minutes / 60);
    return `${hours} 小时 ${minutes % 60} 分`;
  }

  function formatDate(timestamp) {
    if (!timestamp) return "时间未知";
    const date = new Date(Number(timestamp));
    if (Number.isNaN(date.getTime())) return "时间未知";
    return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(date);
  }

  function safeURL(raw) {
    if (!raw) return null;
    try {
      const value = new URL(raw, window.location.origin);
      return value.protocol === "http:" || value.protocol === "https:" ? value.href : null;
    } catch {
      return null;
    }
  }

  function apiPath(path, refresh = false) {
    if (!refresh) return path;
    const url = new URL(path, window.location.origin);
    url.searchParams.set("refresh", "true");
    return `${url.pathname}${url.search}`;
  }

  function delay(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
  }

  async function request(path, options = {}) {
    let response;
    try {
      response = await fetch(path, { credentials: "same-origin", ...options });
    } catch {
      setConnection("error");
      throw new RequestError("无法连接到本地服务", "NETWORK_ERROR");
    }

    let payload;
    try {
      payload = await response.json();
    } catch {
      setConnection("error");
      throw new RequestError("服务返回了无效响应", "INVALID_RESPONSE", response.status);
    }
    setConnection("connected");
    if (!response.ok || !payload.ok) {
      const fault = payload.error || {};
      if (fault.code === "AUTH_EXPIRED") handleAuthExpired();
      if (fault.code === "MATCH_CONTEXT_MISSING") handleMatchContextExpired();
      throw new RequestError(fault.message || "请求失败", fault.code || "REQUEST_FAILED", response.status);
    }
    return payload.data;
  }

  function handleAuthExpired() {
    stopWorkspacePollingOnAuthFailure();
    state.authenticated = false;
    els.platformAuthStatus.textContent = "登录已过期，请重新登录";
    els.platformAuthStatus.className = "platform-auth-status is-expired";
    els.platformLogout.hidden = true;
    els.platformLogin.hidden = false;
    showAuthenticationRequiredWorkspace();
    const now = Date.now();
    if (now - state.authExpiryNoticeAt > 15000) {
      state.authExpiryNoticeAt = now;
      showToast("i春秋登录态已过期，请打开设置重新登录", "warning");
    }
  }

  function handleMatchContextExpired() {
    stopWorkspacePollingOnAuthFailure();
    state.authenticated = false;
    els.platformAuthStatus.textContent = "比赛上下文已失效，请重新绑定";
    els.platformAuthStatus.className = "platform-auth-status is-expired";
    els.platformLogout.hidden = true;
    els.platformLogin.hidden = false;
    showAuthenticationRequiredWorkspace();
    const now = Date.now();
    if (now - state.authExpiryNoticeAt > 15000) {
      state.authExpiryNoticeAt = now;
      showToast("比赛登录上下文已失效，请在设置中重新绑定并登录", "warning");
    }
  }

  function stopWorkspacePollingOnAuthFailure() {
    if (state.scoreTimer) window.clearInterval(state.scoreTimer);
    if (state.noticeTimer) window.clearInterval(state.noticeTimer);
    state.scoreTimer = null;
    state.noticeTimer = null;
  }

  function setSettingsStatus(message, kind = "") {
    els.settingsStatus.textContent = message;
    els.settingsStatus.className = `settings-status${kind ? ` ${kind}` : ""}`;
  }

  function setInlineStatus(element, message, kind = "") {
    element.textContent = message;
    element.className = `settings-status${kind ? ` ${kind}` : ""}`;
  }

  function setPlatformAuthentication(status) {
    const authenticated = Boolean(status && status.authenticated);
    const contextUnavailable = authenticated && status.message === "已登录，队伍信息暂不可用";
    const expired = !authenticated && Boolean(status && /失效|过期|重新登录/.test(status.message || ""));
    state.authenticated = authenticated;
    if (authenticated) state.authExpiryNoticeAt = 0;
    const teamName = status && status.teamName ? ` · ${status.teamName}` : "";
    els.platformAuthStatus.textContent = authenticated ? `已登录${teamName}` : expired ? "登录已过期，请重新登录" : "待登录";
    els.platformAuthStatus.className = `platform-auth-status${authenticated ? " is-authenticated" : expired ? " is-expired" : ""}`;
    els.platformLogout.hidden = !authenticated;
    els.platformLogin.hidden = authenticated && !contextUnavailable;
    els.platformTeamInfo.hidden = !authenticated || contextUnavailable;
    const values = {
      teamName: status && status.teamName,
      userName: status && status.userName,
      score: status && status.score,
      rank: status && status.rank,
      memberCount: status && status.memberCount,
      organization: status && status.organization,
    };
    els.teamSummary.hidden = !authenticated || !values.teamName;
    els.teamSummary.textContent = values.teamName || "";
    els.teamSummary.title = values.teamName || "";
    els.platformTeamName.textContent = values.teamName || "--";
    els.platformUserName.textContent = values.userName || "--";
    els.platformTeamScore.textContent = formatNumber(values.score);
    els.platformTeamRank.textContent = values.rank === null || values.rank === undefined ? "--" : `#${formatNumber(values.rank)}`;
    els.platformMemberCount.textContent = formatNumber(values.memberCount);
    els.platformOrganization.textContent = values.organization || "--";
    els.platformTeamInfo.querySelectorAll("[data-team-field]").forEach((item) => {
      const value = values[item.dataset.teamField];
      item.hidden = value === null || value === undefined || value === "";
    });
    if (authenticated) {
      els.passwordValue.value = "";
      els.passwordImageCode.value = "";
      els.smsCode.value = "";
      els.passwordCaptchaWrap.hidden = true;
    }
  }

  function fillRuntimeConfig(config) {
    state.configured = Boolean(config.configured);
    const matchUrl = config.matchUrl || "";
    const hiddenAccessUrl = matchUrl.endsWith("/index") && config.matchKeyConfigured;
    els.settingMatchUrl.value = matchUrl;
    els.settingMatchUrl.placeholder = hiddenAccessUrl ? "已通过带 k 的比赛地址绑定" : "https://match.ichunqiu.com/wanqubei";
    els.settingModelBaseUrl.value = config.modelBaseUrl || "";
    els.settingModelName.value = config.modelName || "";
    els.modelOptions.replaceChildren();
    els.modelOptions.hidden = true;
    els.settingCodexBaseUrl.value = config.codexBaseUrl || "";
    els.settingCodexModel.value = config.codexModel || "";
    els.codexModelOptions.replaceChildren();
    els.codexModelOptions.hidden = true;
    els.settingCodexConcurrency.value = String(config.codexMaxConcurrency || 5);
    state.codexSystemPrompt = config.codexSystemPrompt || "";
    els.settingCodexSystemPrompt.value = state.codexSystemPrompt;
    state.codexCtfSkillsEnabled = config.codexCtfSkillsEnabled !== false;
    els.settingCodexCtfSkills.checked = state.codexCtfSkillsEnabled;
    state.modelApiKeyConfigured = Boolean(config.modelApiKeyConfigured);
    state.modelApiKeyMasked = config.modelApiKeyMasked || "";
    state.codexApiKeyConfigured = Boolean(config.codexApiKeyConfigured);
    state.codexApiKeyMasked = config.codexApiKeyMasked || "";
    els.settingModelApiKey.placeholder = state.modelApiKeyMasked
      ? `当前 Key：${state.modelApiKeyMasked}（留空保持）`
      : "未配置";
    els.settingCodexApiKey.placeholder = state.codexApiKeyMasked
      ? `当前 Key：${state.codexApiKeyMasked}（留空保持）`
      : "未配置";
    els.settingPlatformToken.value = "";
    els.smsAreaId.value = config.smsAreaId || "";
    els.settingModelApiKey.value = "";
    els.settingCodexApiKey.value = "";
    const hints = [];
    if (config.modelApiKeyConfigured) hints.push("模型 Key 已配置");
    if (config.codexApiKeyConfigured) hints.push("Codex Key 已配置");
    setSettingsStatus(hints.length ? hints.join(" · ") : "模型配置未设置");
    if (config.configured) {
      state.authenticated = false;
      els.platformAuthStatus.textContent = "正在验证登录态";
      els.platformAuthStatus.className = "platform-auth-status";
      els.platformLogin.hidden = true;
      els.platformTeamInfo.hidden = true;
      els.teamSummary.hidden = true;
      els.platformLogout.hidden = true;
      setInlineStatus(els.platformBindStatus, config.matchTitle ? `已绑定：${config.matchTitle}` : `已绑定：${matchUrl}`);
    } else {
      state.authenticated = false;
      els.platformAuthStatus.textContent = "尚未绑定";
      els.platformAuthStatus.className = "platform-auth-status";
      els.platformLogin.hidden = true;
      els.platformTeamInfo.hidden = true;
      els.teamSummary.hidden = true;
      els.platformLogout.hidden = true;
      setInlineStatus(els.platformBindStatus, "");
    }
  }

  async function loadRuntimeConfig() {
    try {
      const config = await request("/api/config");
      fillRuntimeConfig(config);
      if (config.configured) await loadPlatformAuth();
      return config;
    } catch (error) {
      setSettingsStatus(`读取配置失败：${error.message}`, "error");
      return null;
    }
  }

  function renderToolManager(catalog) {
    // Built-in Python modules are documented inside the standalone “基础库”
    // card and do not occupy separate install slots in the UI.
    const tools = (Array.isArray(catalog?.tools) ? catalog.tools : []).filter((tool) => tool.kind !== "builtin");
    state.tools = tools;
    const progress = catalog?.progress || {};
    const installing = catalog?.status === "running" || catalog?.status === "cancelling";
    els.toolManagerStatus.textContent = installing
      ? `正在安装 ${progress.current || catalog.current || "工具"}：${Number(progress.completed ?? catalog.completed ?? 0)} / ${Number(progress.total ?? catalog.total ?? 0)}`
      : catalog?.status === "cancelled"
        ? `安装已取消${catalog.error ? `：${catalog.error}` : ""}`
      : catalog?.error
        ? `安装失败：${catalog.error}`
        : `安装目录：${catalog?.root || "tools"}`;
    els.toolManagerProgress.hidden = !installing;
    if (installing) {
      const completed = Number(progress.completed || catalog.completed || 0);
      const total = Number(progress.total || catalog.total || 0);
      const currentBytes = Number(progress.currentBytes || 0);
      const currentTotal = Number(progress.currentTotal || 0);
      const percent = currentTotal > 0 ? Math.min(100, currentBytes / currentTotal * 100) : 0;
      els.toolManagerProgressTitle.textContent = progress.current ? `正在安装 ${progress.current}` : "准备安装";
      els.toolManagerProgressCount.textContent = `${completed} / ${total}`;
      if (currentTotal > 0) els.toolManagerProgressBar.value = percent;
      else els.toolManagerProgressBar.removeAttribute("value");
      els.toolManagerProgressFile.textContent = progress.message || progress.phase || (currentTotal > 0 ? "当前下载" : "正在准备安装");
      els.toolManagerProgressSize.textContent = currentTotal > 0
        ? `${formatBytes(currentBytes)} / ${formatBytes(currentTotal)}`
        : currentBytes > 0 ? `${formatBytes(currentBytes)} / 总大小未知` : "总大小未知";
      els.toolManagerProgressSpeed.textContent = Number(progress.speed) > 0 ? `${formatBytes(progress.speed)}/秒` : "测量中";
      els.toolManagerProgressEta.textContent = Number(progress.eta) > 0 ? formatDuration(progress.eta) : "未知";
      els.cancelToolInstall.disabled = catalog?.status === "cancelling";
    }
    if (!tools.length) {
      els.toolManagerList.replaceChildren(createElement("div", "empty-state compact", "暂无可用工具"));
      els.installSelectedTools.disabled = true;
      els.installAllTools.disabled = true;
      return;
    }
    const categoryOrder = [
      ["python-library", "Python 库"],
      ["go-project", "Go 开源项目"],
      ["binary-package", "独立工具 / 发行包"],
    ];
    const grouped = new Map(categoryOrder.map(([key, label]) => [key, { key, label, tools: [] }]));
    tools.forEach((tool) => {
      const category = tool.category || "other";
      if (!grouped.has(category)) grouped.set(category, { key: category, label: tool.categoryLabel || "其他工具", tools: [] });
      grouped.get(category).tools.push(tool);
    });
    const fragment = document.createDocumentFragment();
    grouped.forEach((group) => {
      if (!group.tools.length) return;
      const section = createElement("section", "tool-manager-group");
      const installedCount = group.tools.filter((tool) => tool.installed).length;
      const heading = createElement("div", "tool-manager-group-heading");
      heading.append(
        createElement("h3", "", group.label),
        createElement("span", "tool-manager-group-count", `${installedCount} / ${group.tools.length} 已安装`),
      );
      section.append(heading);
      const list = createElement("div", "tool-manager-list");
      group.tools.forEach((tool) => {
          const toolName = tool.name || "";
          // Keep the selection empty until the user chooses tools. Subsequent
          // polls preserve explicit choices while an install is running.
          if (tool.installed) state.toolSelection.delete(toolName);
          const itemState = tool.error ? "is-error" : tool.installed ? "is-installed" : tool.supported === false ? "is-unsupported" : "is-available";
          const selectable = !tool.installed && tool.kind !== "builtin" && tool.supported !== false;
          const item = createElement("article", `tool-manager-item ${itemState}${selectable ? " is-selectable" : ""}${state.toolSelection.has(toolName) ? " is-selected" : ""}`);
          if (selectable) {
            const checkbox = document.createElement("input");
            checkbox.type = "checkbox";
            checkbox.value = toolName;
            checkbox.checked = state.toolSelection.has(toolName);
            checkbox.disabled = installing;
            checkbox.setAttribute("aria-label", `选择安装 ${tool.label || tool.name || "工具"}`);
            checkbox.addEventListener("change", () => {
              if (checkbox.checked) state.toolSelection.add(toolName);
              else state.toolSelection.delete(toolName);
              item.classList.toggle("is-selected", checkbox.checked);
              item.setAttribute("aria-checked", String(checkbox.checked));
            });
            item.append(checkbox);
          } else {
            const marker = createElement("span", "tool-manager-installed-marker");
            marker.append(icon("check-circle-2"));
            marker.setAttribute("aria-label", "已安装");
            item.append(marker);
          }
          const copy = createElement("div", "tool-manager-copy");
          const title = createElement("div", "tool-manager-title-row");
          title.append(createElement("strong", "", tool.label || tool.name || "工具"));
          const installState = tool.installed
            ? tool.execution === "source-only" ? "源码已获取" : "已安装"
            : tool.error ? `失败：${tool.error}`
              : tool.supported === false ? "当前系统不可安装" : "待安装";
          title.append(createElement("span", `tool-manager-state ${itemState}`, installState));
          copy.append(title, createElement("small", "tool-manager-description", tool.description || ""));
          const platformText = Array.isArray(tool.platforms) ? tool.platforms.join(" / ") : "平台信息未知";
          const platform = createElement("small", "tool-manager-platforms", `支持：${platformText}`);
          if (tool.supportNote) platform.title = tool.supportNote;
          copy.append(platform);
          if (tool.execution === "source-only") {
            copy.append(createElement("small", "tool-manager-platforms", "当前安装方式：仅下载源码，不自动编译"));
          }
          const links = createElement("small", "tool-manager-links");
          if (tool.homepage) {
            const link = document.createElement("a");
            link.href = tool.homepage;
            link.target = "_blank";
            link.rel = "noreferrer noopener";
            link.textContent = "项目主页";
            links.append(link);
          }
          if (tool.installUrl && tool.installUrl !== tool.homepage) {
            if (links.childNodes.length) links.append(document.createTextNode(" · "));
            const link = document.createElement("a");
            link.href = tool.installUrl;
            link.target = "_blank";
            link.rel = "noreferrer noopener";
            link.textContent = "安装来源";
            links.append(link);
          }
          if (links.childNodes.length) copy.append(links);
          item.append(copy);
          if (tool.installed && tool.kind !== "builtin") {
            const action = createElement("div", "tool-manager-item-actions");
            const uninstall = createElement("button", "button button-danger button-compact tool-manager-uninstall", "卸载");
            uninstall.type = "button";
            uninstall.dataset.toolId = toolName;
            uninstall.title = `卸载 ${tool.label || tool.name || "工具"}`;
            uninstall.disabled = installing;
            uninstall.addEventListener("click", () => void uninstallTool(tool, uninstall));
            action.append(uninstall);
            item.append(action);
          }
          if (selectable) {
            item.tabIndex = 0;
            item.setAttribute("role", "checkbox");
            item.setAttribute("aria-checked", String(state.toolSelection.has(toolName)));
            const toggleSelection = (event) => {
              if (event.target.closest("a, button, input")) return;
              const checkbox = item.querySelector("input[type=checkbox]");
              if (!checkbox || checkbox.disabled) return;
              checkbox.checked = !checkbox.checked;
              checkbox.dispatchEvent(new Event("change", { bubbles: true }));
            };
            item.addEventListener("click", toggleSelection);
            item.addEventListener("keydown", (event) => {
              if (event.key !== "Enter" && event.key !== " ") return;
              event.preventDefault();
              toggleSelection(event);
            });
          }
        list.append(item);
      });
      section.append(list);
      fragment.append(section);
    });
    els.toolManagerList.replaceChildren(fragment);
    refreshIcons();
    els.installSelectedTools.disabled = installing;
    els.installAllTools.disabled = installing || !tools.some((tool) => !tool.installed && tool.supported !== false);
  }

  async function loadTools(options = {}) {
    try {
      const catalog = await request("/api/tools");
      renderToolManager(catalog);
      if (catalog.status === "running" || catalog.status === "cancelling") {
        if (state.toolManagerTimer) window.clearTimeout(state.toolManagerTimer);
        state.toolManagerTimer = window.setTimeout(() => loadTools(options), 1000);
      }
      return catalog;
    } catch (error) {
      els.toolManagerStatus.textContent = `工具状态读取失败：${error.message}`;
      if (options.notify) showToast(`工具状态读取失败：${error.message}`, "error");
      return null;
    }
  }

  async function installTools(allTools) {
    const selected = [...state.toolSelection];
    if (!allTools && !selected.length) {
      showToast("请先选择要安装的工具", "warning");
      return;
    }
    const button = allTools ? els.installAllTools : els.installSelectedTools;
    await withButton(button, async () => {
      try {
        await request("/api/tools", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-GCSIS-Action": "tools-manager" },
          body: JSON.stringify({ toolIds: selected, all: allTools }),
        });
        showToast("工具安装任务已启动", "success");
        await loadTools();
      } catch (error) {
        showToast(`工具安装启动失败：${error.message}`, "error");
      }
    });
  }

  async function uninstallTool(tool, trigger = null) {
    const toolName = String(tool?.name || "");
    if (!toolName) return;
    const label = tool.label || toolName;
    if (!window.confirm(`确认卸载 ${label}？仅删除项目 tools/ 目录中的文件。`)) return;
    const button = trigger && typeof trigger === "object" ? trigger : null;
    await withButton(button, async () => {
      try {
        await request(`/api/tools/${encodeURIComponent(toolName)}`, {
          method: "DELETE",
          headers: { "X-GCSIS-Action": "tools-manager" },
        });
        state.toolSelection.delete(toolName);
        showToast(`${label} 已卸载`, "success");
        await loadTools();
      } catch (error) {
        showToast(`卸载 ${label} 失败：${error.message}`, "error");
      }
    });
  }

  async function cancelToolInstall() {
    if (!window.confirm("确认取消当前工具安装？已完成的工具会保留。")) return;
    await withButton(els.cancelToolInstall, async () => {
      try {
        await request("/api/tools/cancel", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-GCSIS-Action": "tools-manager" },
        });
        showToast("工具安装取消请求已提交", "warning");
        await loadTools();
      } catch (error) {
        showToast(`取消安装失败：${error.message}`, "error");
      }
    });
  }

  function renderEnvironmentStatus(data) {
    const items = Array.isArray(data?.items) ? data.items : [];
    state.environmentStatus = data;
    const availableCount = items.filter((item) => Boolean(item.available)).length;
    els.environmentStatusSummary.textContent = items.length ? `${availableCount} / ${items.length} 可用` : "暂无环境信息";
    if (!items.length) {
      els.environmentStatusItems.replaceChildren(createElement("span", "environment-status-card is-missing", "暂无环境信息"));
      return;
    }
    const fragment = document.createDocumentFragment();
    items.forEach((item) => {
      const available = Boolean(item.available);
      const label = item.label || item.key || "环境";
      const node = createElement("article", `environment-status-card ${available ? "is-ready" : "is-missing"}`);
      const heading = createElement("div", "environment-status-card-heading");
      heading.append(createElement("strong", "", label), createElement("span", "environment-status-dot", available ? "可用" : "未检测到"));
      const version = createElement("div", "environment-status-version", item.version || (available ? "可用" : "未检测到"));
      const location = createElement("div", "environment-status-path", item.path || "未找到可执行文件");
      node.append(heading, version, location);
      node.title = item.path || label;
      fragment.append(node);
    });
    els.environmentStatusItems.replaceChildren(fragment);
  }

  async function loadEnvironmentStatus(options = {}) {
    try {
      const data = await request(apiPath("/api/environment", options.refresh));
      renderEnvironmentStatus(data);
      return data;
    } catch (error) {
      els.environmentStatusItems.replaceChildren(createElement("span", "environment-status-card is-missing", "检测失败"));
      els.environmentStatusSummary.textContent = "检测失败";
      if (options.notify) showToast(`本地环境检测失败：${error.message}`, "error");
      return null;
    }
  }

  async function loadPlatformAuth() {
    try {
      const status = await request("/api/platform/auth");
      setPlatformAuthentication(status);
      return status;
    } catch (error) {
      els.platformAuthStatus.textContent = "登录态不可用";
      els.platformAuthStatus.className = "platform-auth-status";
      els.platformLogin.hidden = false;
      els.platformLogout.hidden = true;
      setInlineStatus(els.platformBindStatus, error.message, "error");
      return null;
    }
  }

  async function logoutPlatform() {
    if (!window.confirm("确认退出当前 i春秋账号？已绑定的比赛将保留。")) return;
    await withButton(els.platformLogout, async () => {
      try {
        const config = await request("/api/platform/logout", {
          method: "POST",
          headers: PLATFORM_ACTION_HEADERS,
        });
        fillRuntimeConfig(config);
        state.activeEnvironments.clear();
        renderActiveEnvironments();
        if (config.configured) {
          setPlatformAuthentication({ authenticated: false });
          showAuthenticationRequiredWorkspace();
        } else {
          showUnconfiguredWorkspace();
        }
        showToast(config.configured ? "已退出账号，可以登录其他账号" : "已退出账号，请重新绑定比赛", "success");
      } catch (error) {
        showToast(`退出失败：${error.message}`, "error");
      }
    });
  }

  function setLoginMode(mode) {
    const passwordMode = mode === "password";
    els.loginModePassword.classList.toggle("is-active", passwordMode);
    els.loginModePassword.setAttribute("aria-pressed", String(passwordMode));
    els.loginModeSms.classList.toggle("is-active", !passwordMode);
    els.loginModeSms.setAttribute("aria-pressed", String(!passwordMode));
    els.passwordLoginForm.hidden = !passwordMode;
    els.smsLoginForm.hidden = passwordMode;
    if (!passwordMode) refreshCaptcha("rotate");
  }

  function refreshCaptcha(kind) {
    const image = kind === "image" ? els.passwordCaptcha : els.rotateCaptchaImage;
    image.src = `/api/platform/captcha?kind=${kind}&nonce=${Date.now()}`;
    if (kind === "rotate") {
      els.rotateCaptchaAngle.value = "0";
      updateCaptchaRotation();
    }
  }

  function updateCaptchaRotation() {
    const angle = Number(els.rotateCaptchaAngle.value || 0);
    els.rotateCaptchaImage.style.transform = `rotate(${angle}deg)`;
    els.rotateCaptchaOutput.value = `${angle}°`;
    els.rotateCaptchaOutput.textContent = `${angle}°`;
  }

  async function refreshWorkspaceAfterLogin(options = {}) {
    const results = await Promise.all([
      loadOverview({ refresh: true, silent: true }),
      loadExercises({ refresh: true, silent: true }),
      loadMatchInfo({ refresh: true, silent: true }),
      loadNotices({ refresh: true, refreshDetail: true, silent: true }),
    ]);
    setWorkspaceActionsEnabled(true);
    const failures = results.filter((result) => result === null).length;
    if (options.notify && failures) showToast(`${failures} 项比赛信息加载失败，请检查登录态后重试`, "warning");
    if (failures === 0) {
      toggleScoreRefresh();
      toggleNoticeRefresh();
      void startAttachmentSizeProbe();
    }
    return failures === 0;
  }

  function showWorkspacePlaceholder(messages) {
    stopWorkspacePollingOnAuthFailure();
    state.groups = [];
    state.notices = [];
    state.overview = null;
    state.matchInfo = null;
    state.detail = null;
    state.noticeDetail = null;
    state.selectedExerciseId = null;
    state.selectedNoticeId = null;
    els.score.textContent = "--";
    els.rank.textContent = "--";
    els.overviewUpdated.textContent = messages.overview;
    els.exerciseCount.textContent = "0";
    els.exerciseList.replaceChildren(createElement("div", "empty-state compact", messages.exercise));
    els.noticeCount.textContent = "0";
    els.noticeList.replaceChildren(createElement("div", "empty-state compact", messages.notice));
    els.noticeDetail.replaceChildren(createElement("div", "empty-state compact", messages.noticeDetail));
    els.matchNote.textContent = messages.matchNote;
    els.matchRule.textContent = messages.matchRule;
    setWorkspaceActionsEnabled(false);
    renderDetail();
    refreshIcons();
  }

  function showUnconfiguredWorkspace() {
    showWorkspacePlaceholder({
      overview: "尚未绑定比赛",
      exercise: "请先在设置中绑定比赛",
      notice: "请先绑定比赛",
      noticeDetail: "绑定比赛后可查看公告",
      matchNote: "请先绑定比赛",
      matchRule: "绑定比赛后可查看比赛说明与规则",
    });
  }

  function showAuthenticationRequiredWorkspace() {
    showWorkspacePlaceholder({
      overview: "请先登录 i春秋",
      exercise: "登录 i春秋后加载题目",
      notice: "登录 i春秋后加载公告",
      noticeDetail: "登录后可查看公告详情",
      matchNote: "登录 i春秋后加载比赛说明",
      matchRule: "登录后可查看比赛规则",
    });
  }

  async function bindMatch() {
    const matchUrl = els.settingMatchUrl.value.trim();
    if (!matchUrl) {
      setInlineStatus(els.platformBindStatus, "请输入比赛地址", "error");
      return;
    }
    const payload = { matchUrl };
    if (els.settingPlatformToken.value) payload.platformToken = els.settingPlatformToken.value;
    await withButton(els.bindMatch, async () => {
      setInlineStatus(els.platformBindStatus, "正在绑定…");
      try {
        const config = await request("/api/platform/bind", {
          method: "POST",
          headers: { "Content-Type": "application/json", ...PLATFORM_ACTION_HEADERS },
          body: JSON.stringify(payload),
        });
        fillRuntimeConfig(config);
        setInlineStatus(els.platformBindStatus, config.matchTitle ? `已绑定：${config.matchTitle}` : "绑定成功");
        const status = config.authenticated ? await loadPlatformAuth() : null;
        const authenticated = Boolean(status && status.authenticated);
        showToast(authenticated ? "比赛已绑定，登录态有效" : "比赛已绑定，请登录", "success");
        if (authenticated) await refreshWorkspaceAfterLogin({ notify: true });
        else showAuthenticationRequiredWorkspace();
      } catch (error) {
        setInlineStatus(els.platformBindStatus, `绑定失败：${error.message}`, "error");
        showToast(`绑定失败：${error.message}`, "error");
      }
    });
  }

  async function passwordLogin(event) {
    event.preventDefault();
    const account = els.passwordAccount.value.trim();
    const password = els.passwordValue.value;
    if (!account || !password) {
      setInlineStatus(els.passwordLoginStatus, "请输入账号和密码", "error");
      return;
    }
    await withButton(els.passwordLogin, async () => {
      setInlineStatus(els.passwordLoginStatus, "正在登录…");
      try {
        const status = await request("/api/platform/login/password", {
          method: "POST",
          headers: { "Content-Type": "application/json", ...PLATFORM_ACTION_HEADERS },
          body: JSON.stringify({ account, password, imageCode: els.passwordImageCode.value.trim() }),
        });
        if (!status.authenticated) throw new RequestError(status.message || "登录态验证失败", "AUTHENTICATION_FAILED");
        setPlatformAuthentication(status);
        setInlineStatus(els.passwordLoginStatus, "登录成功");
        showToast("i春秋登录成功", "success");
        await refreshWorkspaceAfterLogin({ notify: true });
      } catch (error) {
        if (error.code === "1002") {
          els.passwordCaptchaWrap.hidden = false;
          els.passwordImageCode.value = "";
          refreshCaptcha("image");
        }
        setInlineStatus(els.passwordLoginStatus, `登录失败：${error.message}`, "error");
      } finally {
        els.passwordValue.value = "";
      }
    });
  }

  async function sendSmsCode() {
    const areaId = els.smsAreaId.value.trim() || "86";
    const phone = els.smsPhone.value.trim();
    if (!/^[A-Za-z0-9_-]{1,64}$/.test(areaId) || !/^\d{5,20}$/.test(phone)) {
      setInlineStatus(els.smsLoginStatus, "比赛区域标识或手机号无效，请先重新绑定比赛", "error");
      return;
    }
    await withButton(els.sendSmsCode, async () => {
      setInlineStatus(els.smsLoginStatus, "正在发送…");
      try {
        await request("/api/platform/login/sms/send", {
          method: "POST",
          headers: { "Content-Type": "application/json", ...PLATFORM_ACTION_HEADERS },
          body: JSON.stringify({ areaId, phone, rotation: Number(els.rotateCaptchaAngle.value) }),
        });
        setInlineStatus(els.smsLoginStatus, "短信验证码已发送");
      } catch (error) {
        setInlineStatus(els.smsLoginStatus, `发送失败：${error.message}`, "error");
        refreshCaptcha("rotate");
      }
    });
  }

  async function smsLogin(event) {
    event.preventDefault();
    const phone = els.smsPhone.value.trim();
    const smsCode = els.smsCode.value.trim();
    if (!/^\d{5,20}$/.test(phone) || !/^\d{4,8}$/.test(smsCode)) {
      setInlineStatus(els.smsLoginStatus, "请输入手机号和短信验证码", "error");
      return;
    }
    await withButton(els.smsLogin, async () => {
      setInlineStatus(els.smsLoginStatus, "正在登录…");
      try {
        const status = await request("/api/platform/login/sms", {
          method: "POST",
          headers: { "Content-Type": "application/json", ...PLATFORM_ACTION_HEADERS },
          body: JSON.stringify({ phone, smsCode }),
        });
        els.smsCode.value = "";
        if (!status.authenticated) throw new RequestError(status.message || "登录态验证失败", "AUTHENTICATION_FAILED");
        setPlatformAuthentication(status);
        setInlineStatus(els.smsLoginStatus, "登录成功");
        showToast("i春秋登录成功", "success");
        await refreshWorkspaceAfterLogin({ notify: true });
      } catch (error) {
        els.smsCode.value = "";
        setInlineStatus(els.smsLoginStatus, `登录失败：${error.message}`, "error");
      }
    });
  }

  function openSettingsDialog() {
    if (typeof els.settingsDialog.showModal === "function") els.settingsDialog.showModal();
    else els.settingsDialog.setAttribute("open", "open");
    void loadRuntimeConfig();
  }

  function renderModelOptions(target, models, modelInput) {
    target.replaceChildren();
    target.hidden = models.length === 0;
    models.forEach((model) => {
      const option = document.createElement("button");
      option.type = "button";
      option.className = "model-option";
      option.setAttribute("role", "option");
      option.textContent = model;
      option.addEventListener("click", () => {
        modelInput.value = model;
        target.hidden = true;
        modelInput.focus();
      });
      target.append(option);
    });
  }

  function bindModelPicker(modelInput, modelMenu) {
    modelInput.addEventListener("input", () => {
      modelMenu.hidden = true;
    });
  }

  async function fetchModelList(kind) {
    const codex = kind === "codex";
    const baseUrl = (codex ? els.settingCodexBaseUrl : els.settingModelBaseUrl).value.trim();
    const keyInput = codex ? els.settingCodexApiKey : els.settingModelApiKey;
    const button = codex ? els.fetchCodexModels : els.fetchModels;
    if (!baseUrl) {
      setSettingsStatus("请先填写模型地址", "error");
      return;
    }
    await withButton(button, async () => {
      setSettingsStatus("正在获取模型列表…");
      try {
        const body = { baseUrl, kind };
        if (keyInput.value.trim()) body.apiKey = keyInput.value.trim();
        const result = await request("/api/models", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        const models = Array.isArray(result.models) ? result.models : [];
        const modelInput = codex ? els.settingCodexModel : els.settingModelName;
        const modelSelect = codex ? els.codexModelOptions : els.modelOptions;
        if (!modelInput.value.trim() && models.length === 1) modelInput.value = models[0];
        renderModelOptions(modelSelect, models, modelInput);
        setSettingsStatus(`已获取 ${models.length} 个模型，请在弹出列表中选择，也可手动输入`, "success");
      } catch (error) {
        setSettingsStatus(`获取模型失败：${error.message}`, "error");
      }
    });
  }

  function openToolManagerDialog() {
    if (typeof els.toolManagerDialog.showModal === "function") els.toolManagerDialog.showModal();
    else els.toolManagerDialog.setAttribute("open", "open");
    void Promise.all([loadTools(), loadEnvironmentStatus()]);
  }

  async function saveRuntimeConfig(event) {
    event.preventDefault();
    const payload = {
      modelBaseUrl: els.settingModelBaseUrl.value.trim(),
      modelName: els.settingModelName.value.trim(),
      codexBaseUrl: els.settingCodexBaseUrl.value.trim(),
      codexModel: els.settingCodexModel.value.trim(),
      codexMaxConcurrency: Number(els.settingCodexConcurrency.value || 5),
      codexSystemPrompt: els.settingCodexSystemPrompt.value.trim(),
      codexCtfSkillsEnabled: els.settingCodexCtfSkills.checked,
    };
    const hasCodexKey = Boolean(els.settingCodexApiKey.value.trim() || state.codexApiKeyConfigured);
    if (!payload.codexBaseUrl || !payload.codexModel || !hasCodexKey) {
      setSettingsStatus("Codex CLI 为必填项，请完整填写地址、模型和 API Key", "error");
      showToast("请先完成 Codex CLI 配置", "warning");
      return;
    }
    if (els.settingModelApiKey.value) payload.modelApiKey = els.settingModelApiKey.value;
    if (els.settingCodexApiKey.value) payload.codexApiKey = els.settingCodexApiKey.value;
    await withButton(els.saveSettings, async () => {
      setSettingsStatus("正在保存…");
      try {
        const config = await request("/api/config", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        fillRuntimeConfig(config);
        showToast("配置已保存，服务已立即应用", "success");
        if (config.configured) {
          const status = await loadPlatformAuth();
          if (status && status.authenticated) await refreshWorkspaceAfterLogin();
          else showAuthenticationRequiredWorkspace();
        } else {
          showUnconfiguredWorkspace();
        }
        await loadCodexTasks();
      } catch (error) {
        setSettingsStatus(`保存失败：${error.message}`, "error");
        showToast(`配置保存失败：${error.message}`, "error");
      }
    });
  }

  function showToast(message, kind = "info") {
    const toast = createElement("div", `toast ${kind}`);
    const iconName = kind === "success" ? "check-circle-2" : kind === "warning" ? "triangle-alert" : kind === "error" ? "circle-x" : "info";
    toast.append(icon(iconName), createElement("span", "toast-message", message));
    els.toastRegion.append(toast);
    refreshIcons();
    window.setTimeout(() => toast.remove(), 4600);
  }

  function showLoadError(container, message) {
    container.replaceChildren(createElement("div", "empty-state compact", message));
  }

  function setLoading(button, loading) {
    button.disabled = loading;
    button.classList.toggle("is-loading", loading);
    button.setAttribute("aria-busy", loading ? "true" : "false");
  }

  function setWorkspaceActionsEnabled(enabled) {
    [
      "refresh-overview",
      "refresh-exercises",
      "refresh-notices",
      "refresh-match-info",
      "refresh-active-environments",
      "open-attachment-manager",
    ].forEach((id) => {
      const button = $(id);
      if (button) button.disabled = !enabled;
    });
    els.scoreRefresh.disabled = !enabled;
    els.noticeRefresh.disabled = !enabled;
  }

  async function withButton(button, task) {
    setLoading(button, true);
    try {
      await task();
    } finally {
      setLoading(button, false);
      refreshIcons();
    }
  }

  function updateOverview(data) {
    state.overview = data;
    els.score.textContent = formatNumber(data.stagePoint);
    els.rank.textContent = formatNumber(data.stageRank);
    els.overviewUpdated.textContent = `更新于 ${new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date())}`;
  }

  async function loadOverview(options = {}) {
    try {
      const data = await request(apiPath("/api/overview", options.refresh));
      updateOverview(data);
      return data;
    } catch (error) {
      if (!options.silent) showToast(`得分刷新失败：${error.message}`, "error");
      return null;
    }
  }

  function totalExercises(groups) {
    return groups.reduce((sum, group) => sum + (group.corpus || []).length, 0);
  }

  async function loadExercises(options = {}) {
    try {
      const groups = await request(apiPath("/api/exercises", options.refresh));
      state.groups = Array.isArray(groups) ? groups : [];
      state.exercisesLoaded = true;
      const allIds = new Set(state.groups.flatMap((group) => (group.corpus || []).map((item) => item.id)));
      if (state.selectedExerciseId !== null && !allIds.has(state.selectedExerciseId)) {
        state.selectedExerciseId = null;
        state.detail = null;
        renderDetail();
      }
      renderExercises();
      reconcileTrackedExerciseIds(allIds);
      if (!state.environmentsRestored) {
        state.environmentsRestored = true;
        await restoreTrackedEnvironments();
      }
      return state.groups;
    } catch (error) {
      if (!state.groups.length) showLoadError(els.exerciseList, "题目列表暂时不可用");
      if (!options.silent) showToast(`题目刷新失败：${error.message}`, "error");
      return null;
    }
  }

  function renderExercises() {
    const fragment = document.createDocumentFragment();
    els.exerciseCount.textContent = formatNumber(totalExercises(state.groups));
    if (!state.groups.length) {
      els.exerciseList.replaceChildren(createElement("div", "empty-state compact", "暂无题目"));
      return;
    }
    [...state.groups].sort((a, b) => (a.order || 0) - (b.order || 0)).forEach((group) => {
      const groupKey = String(group.id ?? group.name ?? "uncategorized");
      const collapsed = state.collapsedGroups.has(groupKey);
      const section = createElement("section", `exercise-group${collapsed ? " is-collapsed" : ""}`);
      const heading = createElement("button", "group-heading");
      const body = createElement("div", "exercise-group-body");
      const bodyId = `exercise-group-${groupKey.replace(/[^A-Za-z0-9_-]/g, "-")}`;
      heading.type = "button";
      heading.setAttribute("aria-expanded", collapsed ? "false" : "true");
      heading.setAttribute("aria-controls", bodyId);
      const headingMain = createElement("span", "group-heading-main");
      headingMain.append(icon("chevron-down"), createElement("span", "", group.name || "未分类"));
      const headingMeta = createElement("span", "group-heading-meta", String((group.corpus || []).length));
      heading.append(headingMain, headingMeta);
      heading.addEventListener("click", () => {
        if (state.collapsedGroups.has(groupKey)) state.collapsedGroups.delete(groupKey);
        else state.collapsedGroups.add(groupKey);
        persistCollapsedGroups();
        renderExercises();
      });
      body.id = bodyId;
      body.hidden = collapsed;
      section.append(heading, body);
      [...(group.corpus || [])].sort((a, b) => (a.order || 0) - (b.order || 0)).forEach((exercise) => {
        const button = createElement("button", `exercise-item${state.selectedExerciseId === exercise.id ? " is-selected" : ""}`);
        button.type = "button";
        button.disabled = exercise.isOpen === false;
        button.setAttribute("aria-current", state.selectedExerciseId === exercise.id ? "true" : "false");
        const main = createElement("span", "exercise-item-main");
        const titleRow = createElement("span", "exercise-title-row");
        titleRow.append(createElement("span", "exercise-name", exercise.name || `题目 ${exercise.id}`));
        const tags = exerciseTags(exercise);
        if (tags.length) {
          const tagList = createElement("span", "exercise-tags");
          tags.forEach((tag) => tagList.append(tag));
          titleRow.append(tagList);
        }
        main.append(titleRow);
        const sub = createElement("span", "exercise-sub");
        sub.append(createElement("span", exercise.hasSolved ? "solved-mark" : "closed-mark", exercise.hasSolved ? "已解决" : exercise.isOpen === false ? "未开放" : "未解决"));
        main.append(sub);
        button.append(main);
        if (exercise.hasSolved) button.append(icon("check"));
        button.addEventListener("click", () => selectExercise(exercise.id));
        body.append(button);
      });
      fragment.append(section);
    });
    els.exerciseList.replaceChildren(fragment);
    refreshIcons();
  }

  function exerciseTags(exercise) {
    const tags = [];
    if (exercise.hasEnvironment) tags.push(createElement("span", "exercise-tag exercise-tag-environment", "容器题"));
    if (exercise.hasAttachment) tags.push(createElement("span", "exercise-tag exercise-tag-attachment", "附件题"));
    if (!tags.length) tags.push(createElement("span", "exercise-tag exercise-tag-plain", "普通题"));
    return tags;
  }

  function loadCollapsedGroups() {
    try {
      const value = JSON.parse(window.localStorage.getItem(COLLAPSED_GROUP_STORAGE_KEY) || "[]");
      state.collapsedGroups = new Set((Array.isArray(value) ? value : []).map(String));
    } catch (_) {
      state.collapsedGroups = new Set();
    }
  }

  function persistCollapsedGroups() {
    try {
      window.localStorage.setItem(COLLAPSED_GROUP_STORAGE_KEY, JSON.stringify([...state.collapsedGroups]));
    } catch (_) {
      // Collapsing still works for the current page when browser storage is unavailable.
    }
  }

  function loadCollapsedAttachmentCategories() {
    try {
      const value = JSON.parse(window.localStorage.getItem(COLLAPSED_ATTACHMENT_CATEGORY_STORAGE_KEY) || "[]");
      state.collapsedAttachmentCategories = new Set((Array.isArray(value) ? value : []).map(String));
    } catch (_) {
      state.collapsedAttachmentCategories = new Set();
    }
  }

  function persistCollapsedAttachmentCategories() {
    try {
      window.localStorage.setItem(COLLAPSED_ATTACHMENT_CATEGORY_STORAGE_KEY, JSON.stringify([...state.collapsedAttachmentCategories]));
    } catch (_) {
      // Current-page folding remains available when browser storage is unavailable.
    }
  }

  async function selectExercise(id) {
    state.pollToken += 1;
    state.selectedExerciseId = id;
    state.detail = null;
    els.flagInput.value = "";
    updateFlagLength();
    renderExercises();
    renderDetailLoading();
    await loadExerciseDetail(id);
  }

  function renderDetailLoading() {
    const summary = state.groups.flatMap((group) => group.corpus || []).find((item) => item.id === state.selectedExerciseId);
    els.selectedExerciseName.textContent = summary ? summary.name : "题目详情";
    els.exerciseMeta.replaceChildren(createElement("span", "meta-chip", "加载中"));
    els.environmentAction.replaceChildren();
    els.selectedExerciseRefresh.disabled = true;
    els.openAIPrompt.disabled = true;
    els.runCodex.disabled = true;
    els.exerciseDetail.replaceChildren(createElement("div", "empty-state", "正在加载题目详情"));
    els.flagInput.disabled = true;
    els.flagSubmit.disabled = true;
    refreshIcons();
  }

  function isContainerExercise(detail) {
    return Boolean(
      detail &&
      detail.endpointType &&
      (detail.isNeedInit || detail.isNeedCheck || (detail.endpoints || []).length)
    );
  }

  function exerciseCategory(detail) {
    if (detail && detail.category) return detail.category;
    const group = state.groups.find((item) => (item.corpus || []).some((exercise) => exercise.id === detail?.id));
    return group?.name || "未分类";
  }

  function loadTrackedEnvironmentIds() {
    try {
      const value = JSON.parse(window.localStorage.getItem(ACTIVE_ENVIRONMENT_STORAGE_KEY) || "[]");
      state.trackedEnvironmentIds = new Set((Array.isArray(value) ? value : [])
        .map(Number)
        .filter((id) => Number.isSafeInteger(id) && id > 0));
    } catch (_) {
      state.trackedEnvironmentIds = new Set();
    }
    state.environmentSyncVersion += 1;
  }

  function persistTrackedEnvironmentIds() {
    try {
      window.localStorage.setItem(ACTIVE_ENVIRONMENT_STORAGE_KEY, JSON.stringify([...state.trackedEnvironmentIds].sort((a, b) => a - b)));
    } catch (_) {
      // Storage may be unavailable; in-memory tracking still works for this page.
    }
  }

  function exerciseSummary(id) {
    return state.groups.flatMap((group) => group.corpus || []).find((exercise) => exercise.id === id) || null;
  }

  function activeEnvironmentName(detail) {
    return detail.name || exerciseSummary(detail.id)?.name || `题目 ${detail.id}`;
  }

  function activeEnvironmentExpiry(detail) {
    const expiries = (detail.endpoints || [])
      .map((endpoint) => Number(endpoint.expireTime))
      .filter((value) => Number.isFinite(value) && value > 0);
    return expiries.length ? `到期 ${formatDate(Math.min(...expiries))}` : "运行中";
  }

  function makeActiveEnvironmentItem(detail) {
    const id = detail.id;
    const name = activeEnvironmentName(detail);
    const busy = state.environmentRefreshBusy || state.environmentBatchBusy || state.environmentStopBusy.size > 0;
    const row = createElement("div", "active-environment-item");
    const main = createElement("button", "active-environment-main");
    main.type = "button";
    main.title = `切换到 ${name}`;
    main.append(createElement("span", "active-environment-name", name), createElement("span", "active-environment-meta", activeEnvironmentExpiry(detail)));
    main.addEventListener("click", () => selectExercise(id));

    const stop = createElement("button", "icon-button active-environment-stop");
    stop.type = "button";
    stop.title = `关闭 ${name}`;
    stop.setAttribute("aria-label", `关闭 ${name}`);
    stop.disabled = busy;
    if (state.environmentStopBusy.has(id)) stop.setAttribute("aria-busy", "true");
    stop.append(icon("square"));
    stop.addEventListener("click", () => stopTrackedEnvironment(id));
    row.append(main, stop);
    return row;
  }

  function renderActiveEnvironments() {
    const entries = [...state.activeEnvironments.values()].sort((a, b) => {
      const byName = activeEnvironmentName(a).localeCompare(activeEnvironmentName(b), "zh-CN");
      return byName || a.id - b.id;
    });
    const interactionBusy = state.environmentRefreshBusy || state.environmentBatchBusy || state.environmentStopBusy.size > 0;
    els.activeEnvironmentCount.textContent = formatNumber(entries.length);
    els.activeEnvironmentRefresh.disabled = interactionBusy;
    els.stopAllEnvironments.disabled = !entries.length || interactionBusy;
    if (!entries.length) {
      const message = state.environmentRefreshBusy && state.trackedEnvironmentIds.size ? "正在校验容器状态" : "暂无开启的容器";
      els.activeEnvironmentList.replaceChildren(createElement("div", "empty-state compact", message));
      return;
    }
    const fragment = document.createDocumentFragment();
    entries.forEach((detail) => fragment.append(makeActiveEnvironmentItem(detail)));
    els.activeEnvironmentList.replaceChildren(fragment);
    refreshIcons();
  }

  function removeTrackedEnvironment(id) {
    const tracked = state.trackedEnvironmentIds.delete(id);
    const active = state.activeEnvironments.delete(id);
    if (tracked) {
      state.environmentSyncVersion += 1;
      persistTrackedEnvironmentIds();
    }
    if (tracked || active) renderActiveEnvironments();
  }

  function observeEnvironment(detail) {
    const id = Number(detail && detail.id);
    if (!Number.isSafeInteger(id) || id <= 0) return;
    const active = Array.isArray(detail.endpoints) && detail.endpoints.length > 0;
    const wasTracked = state.trackedEnvironmentIds.has(id);
    if (active) {
      state.trackedEnvironmentIds.add(id);
      state.activeEnvironments.set(id, { ...detail, id });
    } else {
      state.trackedEnvironmentIds.delete(id);
      state.activeEnvironments.delete(id);
    }
    if (active !== wasTracked) {
      state.environmentSyncVersion += 1;
      persistTrackedEnvironmentIds();
    }
    renderActiveEnvironments();
  }

  function reconcileTrackedExerciseIds(allIds) {
    let changed = false;
    state.trackedEnvironmentIds.forEach((id) => {
      if (allIds.has(id)) return;
      state.trackedEnvironmentIds.delete(id);
      state.activeEnvironments.delete(id);
      changed = true;
    });
    state.activeEnvironments.forEach((_, id) => {
      if (!state.trackedEnvironmentIds.has(id)) state.activeEnvironments.delete(id);
    });
    if (changed) {
      state.environmentSyncVersion += 1;
      persistTrackedEnvironmentIds();
    }
    renderActiveEnvironments();
  }

  async function restoreTrackedEnvironments() {
    if (!state.exercisesLoaded) return;
    const allIds = new Set(state.groups.flatMap((group) => (group.corpus || []).map((exercise) => exercise.id)));
    reconcileTrackedExerciseIds(allIds);
    await refreshTrackedEnvironments({ refresh: true, silent: true });
  }

  async function refreshTrackedEnvironments(options = {}) {
    if (state.environmentRefreshBusy) {
      state.environmentRefreshQueued = true;
      return;
    }
    state.environmentRefreshBusy = true;
    setLoading(els.activeEnvironmentRefresh, true);
    renderActiveEnvironments();
    let failures = 0;
    let syncVersion = state.environmentSyncVersion;
    let rerun = false;
    try {
      for (const id of [...state.trackedEnvironmentIds]) {
        try {
          const detail = await fetchExerciseDetail(id, { refresh: options.refresh, observe: false });
          if (syncVersion !== state.environmentSyncVersion) {
            state.environmentRefreshQueued = true;
            continue;
          }
          observeEnvironment(detail);
          syncVersion = state.environmentSyncVersion;
        } catch (error) {
          if (syncVersion !== state.environmentSyncVersion) {
            state.environmentRefreshQueued = true;
          } else if (error.status === 404) {
            removeTrackedEnvironment(id);
            syncVersion = state.environmentSyncVersion;
          } else failures += 1;
        }
      }
    } finally {
      rerun = state.environmentRefreshQueued;
      state.environmentRefreshQueued = false;
      state.environmentRefreshBusy = false;
      setLoading(els.activeEnvironmentRefresh, false);
      renderActiveEnvironments();
    }
    if (!rerun && failures && !options.silent) showToast(`${failures} 个容器状态刷新失败，已保留原状态`, "warning");
    else if (!rerun && options.notify) showToast("容器状态已更新", "success");
    if (rerun) await refreshTrackedEnvironments({ refresh: true, silent: true });
  }

  async function fetchExerciseDetail(id, options = {}) {
    const detail = await request(apiPath(`/api/exercises/${encodeURIComponent(id)}`, options.refresh));
    if (options.observe !== false) observeEnvironment(detail);
    return detail;
  }

  async function loadExerciseDetail(id, options = {}) {
    try {
      const detail = await fetchExerciseDetail(id, options);
      if (state.selectedExerciseId !== id) return detail;
      state.detail = detail;
      renderDetail();
      return detail;
    } catch (error) {
      if (state.selectedExerciseId === id && !options.silent) {
        if (!state.detail) els.exerciseDetail.replaceChildren(createElement("div", "empty-state", "题目详情暂时不可用"));
        showToast(`题目详情加载失败：${error.message}`, "error");
      }
      return null;
    }
  }

  function makeSection(title, iconName) {
    const section = createElement("section", "detail-section");
    const heading = createElement("div", "section-heading");
    heading.append(icon(iconName), createElement("h3", "", title));
    section.append(heading);
    return section;
  }

  function addEndpointPair(grid, label, value) {
    const pair = createElement("div", "endpoint-pair");
    pair.append(createElement("dt", "", label), createElement("dd", "", value || "—"));
    grid.append(pair);
  }

  function makeCopyButton(value) {
    const button = createElement("button", "copy-button");
    button.type = "button";
    button.title = "复制到剪贴板";
    button.setAttribute("aria-label", "复制到剪贴板");
    button.append(icon("copy"));
    button.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(value);
        showToast("已复制", "success");
      } catch {
        showToast("复制失败，请手动选择文本", "warning");
      }
    });
    return button;
  }

  async function saveAttachmentLocally(index, button) {
    const id = state.selectedExerciseId;
    if (id === null) return;
    if (!els.attachmentDialog.open) openAttachmentManager();
    const file = (state.detail?.attachment?.files || [])[index];
    const label = file?.name || `题目 #${id} 附件 ${index + 1}`;
    await startAttachmentDownload({ scope: "single", exerciseId: id, attachmentIndex: index }, label, button);
  }

  function attachmentFiles(exercise) {
    return Array.isArray(exercise && exercise.attachments) ? exercise.attachments : [];
  }

  function setAttachmentManagerBusy(busy) {
    state.attachmentBusy = busy;
    els.attachmentCheck.disabled = busy;
    const catalog = state.attachmentCatalog;
    const missing = catalog ? Number(catalog.totalAttachments || 0) - Number(catalog.existingAttachments || 0) : 0;
    els.attachmentDownloadAll.disabled = busy || missing <= 0;
    els.attachmentList.querySelectorAll("button").forEach((button) => {
      button.disabled = busy;
    });
  }

  function attachmentTaskFinished(task) {
    return Boolean(task && ["completed", "completed_with_errors", "failed", "cancelled"].includes(task.status));
  }

  function renderAttachmentProgress() {
    const task = state.attachmentTask;
    els.attachmentProgress.hidden = !task;
    if (!task) return;
    const completed = Number(task.completedFiles || 0);
    const total = Number(task.totalFiles || 0);
    const transferred = Number(task.transferredBytes || 0);
    const totalBytes = Number(task.totalBytes || 0);
    const bytePercent = totalBytes > 0 ? (transferred / totalBytes) * 100 : 0;
    const filePercent = total > 0 ? (completed / total) * 100 : 0;
    const terminalComplete = task.status === "completed" && total === completed;
    const percent = Math.max(0, Math.min(100, terminalComplete ? 100 : totalBytes > 0 ? bytePercent : filePercent));
    const statusLabels = {
      running: task.label || "正在下载附件",
      paused: "附件下载已暂停",
      cancelling: "正在取消附件下载",
      cancelled: "附件下载已取消",
      completed: "附件下载完成",
      completed_with_errors: "附件下载完成，部分文件失败",
      failed: "附件下载失败",
    };
    els.attachmentProgressTitle.textContent = statusLabels[task.status] || task.label || "附件下载";
    els.attachmentProgressCount.textContent = `${completed} / ${total} 个附件`;
    const waitingForTotals = task.status === "running" && total === 0 && totalBytes === 0;
    els.attachmentProgressPercent.textContent = waitingForTotals ? "准备中" : `${Math.round(percent)}%`;
    if (waitingForTotals) els.attachmentProgressBar.removeAttribute("value");
    else els.attachmentProgressBar.value = percent;
    els.attachmentProgressBar.textContent = waitingForTotals ? "准备中" : `${Math.round(percent)}%`;
    els.attachmentProgressFile.textContent = task.currentName || (attachmentTaskFinished(task) ? (task.error || "任务已结束") : "正在整理附件列表");
    const currentBytes = Number(task.currentBytes || 0);
    const currentTotal = Number(task.currentTotalBytes || 0);
    els.attachmentProgressFileSize.textContent = currentTotal > 0 ? `${formatBytes(currentBytes)} / ${formatBytes(currentTotal)}` : currentBytes > 0 ? formatBytes(currentBytes) : "大小待获取";
    els.attachmentProgressBytes.textContent = totalBytes > 0 ? `${formatBytes(transferred)} / ${formatBytes(totalBytes)}` : formatBytes(transferred);
    els.attachmentProgressSpeed.textContent = Number(task.speedBytesPerSecond) > 0 ? `${formatBytes(task.speedBytesPerSecond)}/s` : "--";
    els.attachmentProgressEta.textContent = Number.isFinite(Number(task.etaSeconds)) && task.etaSeconds !== null ? formatDuration(task.etaSeconds) : "--";
    els.attachmentProgressFailed.textContent = formatNumber(task.failedFiles);
    els.attachmentPause.hidden = task.status !== "running";
    els.attachmentResume.hidden = task.status !== "paused";
    els.attachmentCancel.hidden = attachmentTaskFinished(task) || task.status === "cancelling";
  }

  function stopAttachmentTaskPolling() {
    if (state.attachmentTaskTimer) window.clearTimeout(state.attachmentTaskTimer);
    state.attachmentTaskTimer = null;
  }

  async function pollAttachmentTask(taskId) {
    stopAttachmentTaskPolling();
    try {
      const task = await request(`/api/attachments/downloads/${encodeURIComponent(taskId)}`);
      if (state.attachmentTask && state.attachmentTask.id !== taskId) return;
      state.attachmentTask = task;
      renderAttachmentProgress();
      setAttachmentManagerBusy(!attachmentTaskFinished(task));
      if (!attachmentTaskFinished(task)) {
        state.attachmentTaskTimer = window.setTimeout(() => pollAttachmentTask(taskId), ATTACHMENT_TASK_POLL_MS);
        return;
      }
      const failures = Number(task.failedFiles || 0);
      if (task.status === "cancelled") showToast(`${task.label || "附件下载"}已取消`, "warning");
      else showToast(failures || task.status === "failed" ? `${task.label || "附件下载"}完成，${failures} 个文件失败` : `${task.label || "附件下载"}完成`, failures || task.status === "failed" ? "warning" : "success");
      await loadAttachmentCatalog({ silent: true });
      if (state.selectedExerciseId !== null) await loadExerciseDetail(state.selectedExerciseId, { silent: true });
    } catch (error) {
      setAttachmentManagerBusy(false);
      showToast(`附件进度获取失败：${error.message}`, "error");
    }
  }

  async function startAttachmentDownload(payload, label, button) {
    setAttachmentManagerBusy(true);
    button.classList.add("is-loading");
    button.setAttribute("aria-busy", "true");
    state.attachmentTask = {
      status: "running",
      label,
      totalFiles: 0,
      completedFiles: 0,
      transferredBytes: 0,
      totalBytes: 0,
      failedFiles: 0,
    };
    renderAttachmentProgress();
    try {
      const task = await request("/api/attachments/downloads", {
        method: "POST",
        headers: { ...ATTACHMENT_ACTION_HEADERS, "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      state.attachmentTask = task;
      renderAttachmentProgress();
      void pollAttachmentTask(task.id);
    } catch (error) {
      state.attachmentTask = { ...state.attachmentTask, status: "failed", error: error.message };
      renderAttachmentProgress();
      setAttachmentManagerBusy(false);
      showToast(`${label}启动失败：${error.message}`, "error");
    } finally {
      button.classList.remove("is-loading");
      button.setAttribute("aria-busy", "false");
      refreshIcons();
    }
  }

  async function controlAttachmentDownload(action) {
    const task = state.attachmentTask;
    if (!task?.id || attachmentTaskFinished(task)) return;
    const actionLabels = { pause: "暂停", resume: "继续", cancel: "取消" };
    const button = action === "pause" ? els.attachmentPause : action === "resume" ? els.attachmentResume : els.attachmentCancel;
    button.disabled = true;
    try {
      state.attachmentTask = await request(`/api/attachments/downloads/${encodeURIComponent(task.id)}/${action}`, {
        method: "POST",
        headers: ATTACHMENT_ACTION_HEADERS,
      });
      renderAttachmentProgress();
      setAttachmentManagerBusy(!attachmentTaskFinished(state.attachmentTask));
      if (!attachmentTaskFinished(state.attachmentTask)) void pollAttachmentTask(task.id);
    } catch (error) {
      showToast(`附件下载${actionLabels[action]}失败：${error.message}`, "error");
    } finally {
      button.disabled = false;
    }
  }

  async function cancelAttachmentDownload() {
    if (!window.confirm("确认取消当前附件下载？已完整下载的文件会保留，当前未完成文件会删除。")) return;
    await controlAttachmentDownload("cancel");
  }

  async function resumeAttachmentDownload() {
    try {
      const task = await request("/api/attachments/downloads");
      if (!task) return;
      state.attachmentTask = task;
      renderAttachmentProgress();
      setAttachmentManagerBusy(true);
      void pollAttachmentTask(task.id);
    } catch (_) {
      // Download controls remain usable when no resumable task is available.
    }
  }

  function stopAttachmentSizePolling() {
    if (state.attachmentSizeTimer) window.clearTimeout(state.attachmentSizeTimer);
    state.attachmentSizeTimer = null;
  }

  async function pollAttachmentSizes() {
    stopAttachmentSizePolling();
    try {
      const status = await request("/api/attachments/sizes");
      if (status.status === "running") {
        state.attachmentSizeProbing = true;
        els.attachmentSummary.textContent = `正在获取附件大小 ${Number(status.completedFiles || 0)} / ${Number(status.totalFiles || 0)}`;
        state.attachmentSizeTimer = window.setTimeout(pollAttachmentSizes, ATTACHMENT_SIZE_POLL_MS);
        return;
      }
      state.attachmentSizeProbing = false;
      if (state.attachmentCatalog) await loadAttachmentCatalog({ silent: true });
      if (state.selectedExerciseId !== null) await loadExerciseDetail(state.selectedExerciseId, { silent: true });
    } catch (_) {
      state.attachmentSizeProbing = false;
      // Attachment sizes are optional metadata and never block the workspace.
    }
  }

  async function startAttachmentSizeProbe(force = false) {
    stopAttachmentSizePolling();
    state.attachmentSizeProbing = true;
    try {
      const status = await request(`/api/attachments/sizes${force ? "?refresh=true" : ""}`, { method: "POST", headers: ATTACHMENT_ACTION_HEADERS });
      if (status.status === "running") state.attachmentSizeTimer = window.setTimeout(pollAttachmentSizes, ATTACHMENT_SIZE_POLL_MS);
      else await pollAttachmentSizes();
    } catch (_) {
      state.attachmentSizeProbing = false;
      // A failed size probe is retried after the next login or page load.
    }
  }

  async function loadAttachmentCatalog(options = {}) {
    try {
      const catalog = await request("/api/attachments");
      state.attachmentCatalog = catalog || { exercises: [], totalExercises: 0, totalAttachments: 0, existingAttachments: 0 };
      renderAttachmentCatalog();
      return state.attachmentCatalog;
    } catch (error) {
      els.attachmentSummary.textContent = "本地检查失败";
      if (!state.attachmentCatalog) showLoadError(els.attachmentList, "附件目录暂时不可用");
      if (!options.silent) showToast(`附件检查失败：${error.message}`, "error");
      throw error;
    }
  }

  function renderAttachmentCatalog() {
    const catalog = state.attachmentCatalog || {};
    const exercises = Array.isArray(catalog.exercises) ? catalog.exercises : [];
    const total = Number(catalog.totalAttachments || 0);
    const existing = Number(catalog.existingAttachments || 0);
    const knownSizes = exercises.reduce((count, exercise) => count + attachmentFiles(exercise).filter((attachment) => Number(attachment.size) > 0).length, 0);
    els.attachmentSummary.textContent = `本地 ${existing} / ${total} · 大小 ${knownSizes} / ${total} · ${Number(catalog.totalExercises || exercises.length)} 道题`;
    if (!exercises.length) {
      els.attachmentList.replaceChildren(createElement("div", "empty-state", "暂无题目附件"));
      setAttachmentManagerBusy(state.attachmentBusy);
      return;
    }

    const categories = new Map();
    exercises.forEach((exercise) => {
      const category = String(exercise.category || "").trim() || "未分类";
      if (!categories.has(category)) categories.set(category, []);
      categories.get(category).push(exercise);
    });

    const fragment = document.createDocumentFragment();
    categories.forEach((items, category) => {
      const section = createElement("section", "attachment-category");
      const categoryKey = String(category);
      const collapsed = state.collapsedAttachmentCategories.has(categoryKey);
      section.classList.toggle("is-collapsed", collapsed);
      const heading = createElement("header", "attachment-category-heading");
      const toggle = createElement("button", "attachment-category-toggle");
      toggle.type = "button";
      toggle.setAttribute("aria-expanded", String(!collapsed));
      toggle.append(icon(collapsed ? "chevron-right" : "chevron-down"), createElement("h3", "", category));
      const totalFiles = items.reduce((sum, exercise) => sum + attachmentFiles(exercise).length, 0);
      const totalBytes = items.reduce((sum, exercise) => sum + attachmentFiles(exercise).reduce((inner, file) => inner + (Number(file.size) > 0 ? Number(file.size) : 0), 0), 0);
      const categoryActions = createElement("div", "attachment-category-actions");
      categoryActions.append(createElement("span", "count-badge", `${items.length} 题 · ${totalFiles} 个附件${totalBytes ? ` · ${formatBytes(totalBytes)}` : ""}`));
      if (totalFiles) {
        const download = createElement("button", "button button-ghost button-compact");
        download.type = "button";
        download.title = `下载 ${category} 类别全部附件`;
        download.append(icon("folder-down"), createElement("span", "", "下载本类"));
        download.addEventListener("click", (event) => {
          event.stopPropagation();
          void downloadCategoryAttachments(category, download);
        });
        categoryActions.append(download);
      }
      toggle.addEventListener("click", () => {
        if (state.collapsedAttachmentCategories.has(categoryKey)) state.collapsedAttachmentCategories.delete(categoryKey);
        else state.collapsedAttachmentCategories.add(categoryKey);
        persistCollapsedAttachmentCategories();
        renderAttachmentCatalog();
      });
      heading.append(toggle, categoryActions);
      section.append(heading);
      items.forEach((exercise) => section.append(renderAttachmentExercise(exercise)));
      fragment.append(section);
    });
    els.attachmentList.replaceChildren(fragment);
    setAttachmentManagerBusy(state.attachmentBusy);
    refreshIcons();
  }

  function renderAttachmentExercise(exercise) {
    const files = attachmentFiles(exercise);
    const section = createElement("section", "attachment-exercise");
    const header = createElement("div", "attachment-exercise-row");
    const main = createElement("div", "attachment-exercise-main");
    const title = createElement("div", "attachment-exercise-title");
    title.append(createElement("span", "attachment-exercise-id", `#${exercise.id}`), createElement("strong", "", exercise.name || `题目 ${exercise.id}`));
    const meta = createElement("span", exercise.error ? "attachment-exercise-meta is-error" : "attachment-exercise-meta");
    const knownBytes = files.reduce((sum, attachment) => sum + (Number(attachment.size) > 0 ? Number(attachment.size) : 0), 0);
    meta.textContent = exercise.error || (files.length ? `${files.length} 个附件${knownBytes > 0 ? ` · ${formatBytes(knownBytes)}` : " · 正在获取大小"}` : "无附件");
    main.append(title, meta);
    header.append(main);

    if (!exercise.error) {
      const actions = createElement("div", "attachment-exercise-actions");
      if (files.length) {
        const downloadButton = createElement("button", "button button-ghost attachment-download-exercise");
        downloadButton.type = "button";
        downloadButton.title = "下载该题全部附件";
        downloadButton.setAttribute("aria-label", `下载 ${exercise.name || exercise.id} 的全部附件`);
        downloadButton.append(icon("download"), createElement("span", "", "下载全部"));
        downloadButton.addEventListener("click", () => downloadExerciseAttachments(exercise, downloadButton));
        actions.append(downloadButton);
      }

      const clearButton = createElement("button", "icon-button attachment-clear-exercise");
      clearButton.type = "button";
      clearButton.title = "清空本地附件目录";
      clearButton.setAttribute("aria-label", `清空 ${exercise.name || exercise.id} 的本地附件目录`);
      clearButton.append(icon("trash-2"));
      clearButton.addEventListener("click", () => clearExerciseAttachments(exercise, clearButton));
      actions.append(clearButton);

      if (files.length) {
        const redownloadButton = createElement("button", "button button-ghost attachment-redownload-exercise");
        redownloadButton.type = "button";
        redownloadButton.title = "清空后重新下载全部附件";
        redownloadButton.setAttribute("aria-label", `重新下载 ${exercise.name || exercise.id} 的全部附件`);
        redownloadButton.append(icon("rotate-cw"), createElement("span", "", "重新下载"));
        redownloadButton.addEventListener("click", () => redownloadExerciseAttachments(exercise, redownloadButton));
        actions.append(redownloadButton);
      }
      header.append(actions);
    }
    section.append(header);

    if (files.length) {
      const list = createElement("div", "attachment-file-list");
      files.forEach((attachment) => {
        const row = createElement("div", "attachment-file-row");
        const localError = Boolean(attachment.error);
        row.append(icon(localError ? "triangle-alert" : attachment.exists ? "circle-check" : "circle-dashed"));
        const copy = createElement("div", "attachment-file-copy");
        copy.append(createElement("strong", "attachment-file-name", attachment.name || `附件 ${Number(attachment.index || 0) + 1}`));
        copy.append(createElement("code", "attachment-file-path", attachment.path || "本地路径未知"));
        const size = createElement("span", "attachment-file-size", Number(attachment.size) > 0 ? formatBytes(attachment.size) : "获取中");
        const status = createElement("span", `attachment-file-status ${localError ? "is-error" : attachment.exists ? "is-present" : "is-missing"}`);
        status.textContent = localError ? "检查失败" : attachment.exists ? "已存在" : "缺失";
        if (localError) status.title = attachment.error;
        row.append(copy, size, status);
        list.append(row);
      });
      section.append(list);
    }
    return section;
  }

  async function runAttachmentManagerOperation(button, label, operation) {
    setAttachmentManagerBusy(true);
    button.classList.add("is-loading");
    button.setAttribute("aria-busy", "true");
    try {
      await operation();
    } catch (error) {
      showToast(`${label}失败：${error.message}`, "error");
    } finally {
      try {
        await loadAttachmentCatalog({ silent: true });
      } catch {
        showToast("附件状态重新检查失败", "warning");
      }
      button.classList.remove("is-loading");
      button.setAttribute("aria-busy", "false");
      setAttachmentManagerBusy(false);
      refreshIcons();
    }
  }

  function openAttachmentManager() {
    if (typeof els.attachmentDialog.showModal === "function") els.attachmentDialog.showModal();
    else els.attachmentDialog.setAttribute("open", "open");
    els.attachmentSummary.textContent = "正在检查本地文件";
    els.attachmentList.replaceChildren(createElement("div", "empty-state", "正在检查本地附件"));
    setAttachmentManagerBusy(true);
    loadAttachmentCatalog().catch(() => {}).finally(() => setAttachmentManagerBusy(Boolean(state.attachmentTask && !attachmentTaskFinished(state.attachmentTask))));
    if (!state.attachmentTask) void resumeAttachmentDownload();
  }

  async function checkAttachments() {
    await runAttachmentManagerOperation(els.attachmentCheck, "附件检查", async () => {
      await loadAttachmentCatalog({ silent: true });
      showToast("本地附件状态已更新", "success");
    });
  }

  async function downloadAllAttachments() {
    if (!window.confirm("确认下载全部缺失附件？现有文件不会重复下载。")) return;
    await startAttachmentDownload({ scope: "all" }, "全部缺失附件", els.attachmentDownloadAll);
  }

  async function downloadExerciseAttachments(exercise, button) {
    await startAttachmentDownload({ scope: "exercise", exerciseId: exercise.id }, `#${exercise.id} ${exercise.name || "题目附件"}`, button);
  }

  async function downloadCategoryAttachments(category, button) {
    await startAttachmentDownload({ scope: "category", category }, `${category} 类别附件`, button);
  }

  async function clearExerciseAttachments(exercise, button) {
    if (!window.confirm(`确认清空题目 #${exercise.id} “${exercise.name || "未命名题目"}” 的本地附件目录？`)) return;
    await runAttachmentManagerOperation(button, "附件清空", async () => {
      const result = await request(`/api/exercises/${encodeURIComponent(exercise.id)}/attachments`, { method: "DELETE", headers: ATTACHMENT_ACTION_HEADERS });
      showToast(`已清空 ${Number(result.removedFiles || 0)} 个文件，共 ${formatBytes(result.removedBytes)}`, "success");
    });
  }

  async function redownloadExerciseAttachments(exercise, button) {
    if (!window.confirm(`确认清空并重新下载题目 #${exercise.id} “${exercise.name || "未命名题目"}” 的全部附件？`)) return;
    await startAttachmentDownload({ scope: "redownload", exerciseId: exercise.id }, `#${exercise.id} 重新下载`, button);
  }

  function renderDetail() {
    const detail = state.detail;
    if (!detail) {
      els.selectedExerciseName.textContent = "暂无已选题目";
      els.exerciseMeta.replaceChildren();
      els.environmentAction.replaceChildren();
      els.selectedExerciseRefresh.disabled = true;
      els.openAIPrompt.disabled = true;
      els.runCodex.disabled = true;
      els.exerciseDetail.replaceChildren(createElement("div", "empty-state", "暂无题目详情"));
      els.flagInput.disabled = true;
      els.flagSubmit.disabled = true;
      refreshIcons();
      return;
    }

    els.selectedExerciseName.textContent = detail.name || `题目 ${detail.id}`;
    els.selectedExerciseRefresh.disabled = false;
    els.openAIPrompt.disabled = false;
    els.runCodex.disabled = state.codexAvailable !== true;
    const meta = [
      createElement("span", "meta-chip info", exerciseCategory(detail)),
      createElement("span", "meta-chip", `分值 ${detail.score || "—"}`),
      createElement("span", detail.hasSolved ? "meta-chip success" : "meta-chip", detail.hasSolved ? "已解决" : "未解决"),
    ];
    if (detail.difficulty) meta.splice(2, 0, createElement("span", "meta-chip", `难度 ${detail.difficulty}`));
    els.exerciseMeta.replaceChildren(...meta);
    renderEnvironmentAction();

    const fragment = document.createDocumentFragment();
    if (detail.description) {
      const section = makeSection("题目说明", "file-text");
      section.append(renderRichText(detail.description, "description rich-text"));
      fragment.append(section);
    }
    const files = (detail.attachment && detail.attachment.files) || [];
    if (files.length) {
      const section = makeSection("附件", "paperclip");
      const list = createElement("div", "file-list");
      files.forEach((file, index) => {
        const row = createElement("div", "file-row");
        const main = createElement("div", "file-main");
        const copy = createElement("div", "file-copy");
        const title = createElement("div", "file-title");
        title.append(createElement("span", "file-name", file.name || "未命名附件"));
        if (file.ext) title.append(createElement("span", "file-ext", file.ext));
        title.append(createElement("span", "file-size", Number(file.size) > 0 ? formatBytes(file.size) : "大小获取中"));
        copy.append(title);
        main.append(icon("file-archive"), copy);
        row.append(main);
        const url = safeURL(file.url);
        if (url) {
          const urlLink = createElement("a", "file-url", url);
          urlLink.href = url;
          urlLink.target = "_blank";
          urlLink.rel = "noopener noreferrer";
          urlLink.title = url;
          copy.append(urlLink);

          const actions = createElement("div", "file-actions");
          const copyButton = makeCopyButton(url);
          copyButton.className = "copy-button copy-file-url";
          copyButton.title = "复制附件地址";
          copyButton.setAttribute("aria-label", "复制附件地址");
          const download = createElement("a", "file-link file-download");
          download.href = url;
          download.target = "_blank";
          download.rel = "noopener noreferrer";
          download.download = file.name || "attachment";
          download.append(icon("download"), createElement("span", "", "下载"));
          const localSave = createElement("button", "file-save-local");
          localSave.type = "button";
          localSave.title = "保存附件到本地 download 目录";
          localSave.append(icon("folder-down"), createElement("span", "", "保存本地"));
          localSave.addEventListener("click", () => saveAttachmentLocally(index, localSave));
          actions.append(copyButton, download, localSave);
          row.append(actions);
        }
        list.append(row);
      });
      section.append(list);
      fragment.append(section);
    }
    if (isContainerExercise(detail)) {
      const section = makeSection("连接信息", "server");
      const endpoints = createElement("div", "endpoint-list");
      const endpointData = detail.endpoints || [];
      if (!endpointData.length) {
        endpoints.append(createElement("p", "description", detail.isNeedCheck ? "环境正在准备" : "环境尚未启动"));
      }
      endpointData.forEach((endpoint, index) => {
        const row = createElement("div", "endpoint-row");
        const top = createElement("div", "endpoint-top");
        top.append(createElement("span", "endpoint-label", `环境 ${index + 1}${endpoint.isProxy ? " · 代理" : ""}`), createElement("span", "endpoint-expire", endpoint.expireTime ? `到期 ${formatDate(endpoint.expireTime)}` : ""));
        row.append(top);
        const grid = createElement("dl", "endpoint-grid");
        addEndpointPair(grid, "靶机 IP", (endpoint.exposeIps || []).join(", "));
        addEndpointPair(grid, "端口", (endpoint.ports || []).join(", "));
        addEndpointPair(grid, "代理 IP", (endpoint.proxyIps || []).join(", "));
        const mappings = (endpoint.portMappings || []).map((mapping) => `${mapping.type || "tcp"}:${mapping.port || "—"} → ${mapping.proxy || "—"}`).join(", ");
        addEndpointPair(grid, "端口映射", mappings);
        row.append(grid);
        if ((endpoint.users || []).length) {
          const credentials = createElement("div", "credential-list");
          endpoint.users.forEach((user) => {
            const credential = `${user.username || ""} / ${user.password || ""}`;
            const credentialRow = createElement("div", "credential-row");
            credentialRow.append(createElement("span", "credential-value", credential), makeCopyButton(credential));
            credentials.append(credentialRow);
          });
          row.append(credentials);
        }
        endpoints.append(row);
      });
      section.append(endpoints);
      fragment.append(section);
    }
    if (!fragment.childNodes.length) {
      fragment.append(createElement("div", "empty-state", "暂无补充信息"));
    }
    els.exerciseDetail.replaceChildren(fragment);
    els.flagInput.disabled = false;
    els.flagSubmit.disabled = false;
    refreshIcons();
  }

  function renderEnvironmentAction() {
    els.environmentAction.replaceChildren();
    const detail = state.detail;
    if (!isContainerExercise(detail)) return;
    if (detail.isNeedCheck) {
      const button = createElement("button", "button button-ghost");
      button.type = "button";
      button.disabled = true;
      button.append(icon("loader-circle"), createElement("span", "", "准备中"));
      els.environmentAction.append(button);
      refreshIcons();
      return;
    }
    const active = (detail.endpoints || []).length > 0;
    const button = createElement("button", `button ${active ? "button-danger" : "button-primary"}`);
    button.type = "button";
    const stopping = active && (state.environmentRefreshBusy || state.environmentBatchBusy || state.environmentStopBusy.has(detail.id));
    button.disabled = stopping;
    if (stopping) button.setAttribute("aria-busy", "true");
    button.append(icon(active ? "square" : "play"), createElement("span", "", active ? "关闭容器" : "启动容器"));
    button.addEventListener("click", active ? stopEnvironment : startEnvironment);
    els.environmentAction.append(button);
    refreshIcons();
  }

  async function startEnvironment() {
    const id = state.selectedExerciseId;
    if (id === null) return;
    const button = els.environmentAction.querySelector("button");
    await withButton(button, async () => {
      try {
        await request(`/api/exercises/${encodeURIComponent(id)}/environment/start`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
        state.pollToken += 1;
        const token = state.pollToken;
        if (state.detail) state.detail = { ...state.detail, isNeedInit: false, isNeedCheck: true, endpoints: [] };
        renderDetail();
        showToast("环境启动请求已提交", "success");
        pollEnvironment(id, token);
      } catch (error) {
        showToast(`启动失败：${error.message}`, "error");
      }
    });
  }

  async function requestEnvironmentStop(id) {
    if (state.selectedExerciseId === id) state.pollToken += 1;
    await request(`/api/exercises/${encodeURIComponent(id)}/environment/stop`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    removeTrackedEnvironment(id);
    if (state.selectedExerciseId === id) await loadExerciseDetail(id, { refresh: true, observe: false });
  }

  async function stopTrackedEnvironment(id) {
    const detail = state.activeEnvironments.get(id);
    if (!detail || state.environmentRefreshBusy || state.environmentBatchBusy || state.environmentStopBusy.size) return;
    const name = activeEnvironmentName(detail);
    if (!window.confirm(`确认关闭“${name}”的容器？`)) return;
    state.environmentStopBusy.add(id);
    renderActiveEnvironments();
    renderEnvironmentAction();
    try {
      await requestEnvironmentStop(id);
      showToast(`已关闭 ${name}`, "success");
    } catch (error) {
      showToast(`关闭 ${name} 失败：${error.message}`, "error");
    } finally {
      state.environmentStopBusy.delete(id);
      renderActiveEnvironments();
      renderEnvironmentAction();
    }
  }

  async function stopAllEnvironments() {
    const ids = [...state.activeEnvironments.keys()];
    if (!ids.length || state.environmentRefreshBusy || state.environmentBatchBusy || state.environmentStopBusy.size) return;
    if (!window.confirm(`确认关闭全部 ${ids.length} 个容器？`)) return;
    state.environmentBatchBusy = true;
    setLoading(els.stopAllEnvironments, true);
    renderActiveEnvironments();
    renderEnvironmentAction();
    let closed = 0;
    let failed = 0;
    try {
      for (const id of ids) {
        state.environmentStopBusy.add(id);
        try {
          await requestEnvironmentStop(id);
          closed += 1;
        } catch (_) {
          failed += 1;
        } finally {
          state.environmentStopBusy.delete(id);
        }
      }
    } finally {
      state.environmentBatchBusy = false;
      setLoading(els.stopAllEnvironments, false);
      renderActiveEnvironments();
      renderEnvironmentAction();
    }
    const message = failed ? `已关闭 ${closed} 个容器，${failed} 个关闭失败` : `已关闭全部 ${closed} 个容器`;
    showToast(message, failed ? "warning" : "success");
  }

  async function stopEnvironment() {
    const id = state.selectedExerciseId;
    if (id === null || state.environmentRefreshBusy || state.environmentBatchBusy || state.environmentStopBusy.size || !window.confirm("确认关闭当前容器？")) return;
    const button = els.environmentAction.querySelector("button");
    state.environmentStopBusy.add(id);
    renderActiveEnvironments();
    await withButton(button, async () => {
      try {
        await requestEnvironmentStop(id);
        showToast("环境关闭请求已提交", "success");
      } catch (error) {
        showToast(`关闭失败：${error.message}`, "error");
      } finally {
        state.environmentStopBusy.delete(id);
        renderActiveEnvironments();
        renderEnvironmentAction();
      }
    });
  }

  async function pollEnvironment(id, token) {
    const deadline = Date.now() + BUILD_POLL_TIMEOUT_MS;
    while (Date.now() < deadline) {
      await delay(BUILD_POLL_MS);
      if (token !== state.pollToken || state.selectedExerciseId !== id) return;
      const detail = await loadExerciseDetail(id, { silent: true, refresh: true });
      if (!detail) {
        showToast("环境状态查询失败", "error");
        return;
      }
      if (!detail.isNeedCheck && ((detail.endpoints || []).length > 0 || detail.isNeedInit)) {
        showToast("环境状态已更新", "success");
        return;
      }
    }
    if (token === state.pollToken && state.selectedExerciseId === id) showToast("环境准备超时，可稍后重试", "warning");
  }

  function updateFlagLength() {
    const length = Array.from(els.flagInput.value).length;
    els.flagLength.textContent = `${length}/256`;
    els.flagLength.classList.toggle("is-long", length > 256);
  }

  async function submitFlag(event) {
    event.preventDefault();
    const id = state.selectedExerciseId;
    const flag = els.flagInput.value.trim();
    if (id === null || !flag) {
      showToast("请输入 Flag", "warning");
      return;
    }
    if (Array.from(flag).length > 256) {
      showToast("Flag 不能超过 256 个字符", "warning");
      return;
    }
    setLoading(els.flagSubmit, true);
    try {
      const result = await request(`/api/exercises/${encodeURIComponent(id)}/answer`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ flag }) });
      if (!result.isCorrect) {
        showToast("Flag 不正确", "warning");
        return;
      }
      showToast("Flag 正确，成绩已更新", "success");
      els.flagInput.value = "";
      updateFlagLength();
      await Promise.all([
        loadOverview({ silent: true, refresh: true }),
        loadExercises({ silent: true, refresh: true }),
        loadExerciseDetail(id, { silent: true, refresh: true }),
      ]);
    } catch (error) {
      showToast(`提交失败：${error.message}`, "error");
    } finally {
      setLoading(els.flagSubmit, false);
      refreshIcons();
    }
  }

  async function openAIPromptDialog() {
    const id = state.selectedExerciseId;
    if (id === null) return;
    state.aiExerciseId = id;
    els.aiModelStatus.textContent = "正在生成标准提示词";
    els.aiPromptText.value = "";
    els.aiRunOutput.textContent = "尚未运行";
    els.aiCopyPrompt.disabled = true;
    els.aiRun.disabled = true;
    if (typeof els.aiPromptDialog.showModal === "function") els.aiPromptDialog.showModal();
    else els.aiPromptDialog.setAttribute("open", "open");
    try {
      const prompt = await request(`/api/exercises/${encodeURIComponent(id)}/ai/prompt`);
      if (state.aiExerciseId !== id) return;
      els.aiPromptText.value = prompt.prompt || "";
      els.aiCopyPrompt.disabled = !els.aiPromptText.value;
      els.aiRun.disabled = !prompt.modelEnabled;
      els.aiModelStatus.textContent = prompt.modelEnabled ? `已配置模型：${prompt.model || "未命名模型"}` : "模型未配置，可复制提示词到其他代理";
    } catch (error) {
      els.aiModelStatus.textContent = "提示词生成失败";
      els.aiRunOutput.textContent = error.message;
      showToast(`AI 提示词生成失败：${error.message}`, "error");
    }
    refreshIcons();
  }

  async function copyAIPrompt() {
    if (!els.aiPromptText.value) return;
    try {
      await navigator.clipboard.writeText(els.aiPromptText.value);
      showToast("AI 提示词已复制", "success");
    } catch {
      showToast("复制失败，请手动选择提示词", "warning");
    }
  }

  async function runAISolver() {
    const id = state.aiExerciseId;
    if (id === null) return;
    await withButton(els.aiRun, async () => {
      els.aiRunOutput.textContent = "模型正在分析，可能需要数分钟";
      try {
        const result = await request(`/api/exercises/${encodeURIComponent(id)}/ai/run`, { method: "POST" });
        const summary = [
          `状态：${result.solved ? "已解出" : "未确认解出"}`,
          result.flag ? `提交值：${result.flag}` : "",
          result.cleanupAttempted ? `环境回收：${result.cleanupSucceeded ? "成功" : "失败"}` : "",
          result.writeupPath ? `WP：${result.writeupPath}` : "",
          result.warning ? `警告：${result.warning}` : "",
          "",
          result.output || "模型没有返回文本",
        ].filter((line, index) => line || index >= 5).join("\n");
        els.aiRunOutput.textContent = summary;
        showToast(result.solved ? "AI 已提交正确答案" : "AI 运行结束，尚未确认正确答案", result.solved ? "success" : "warning");
      } catch (error) {
        els.aiRunOutput.textContent = `运行失败：${error.message}`;
        showToast(`AI 运行失败：${error.message}`, "error");
      }
    });
  }

  async function loadMatchInfo(options = {}) {
    try {
      state.matchInfo = await request(apiPath("/api/match-info", options.refresh));
      els.matchNote.textContent = state.matchInfo.note || "暂无内容";
      els.matchRule.textContent = state.matchInfo.rule || "暂无内容";
      return state.matchInfo;
    } catch (error) {
      els.matchNote.textContent = `加载失败：${error.message}`;
      els.matchRule.textContent = "请确认比赛已绑定且登录态有效";
      if (!options.silent) showToast(`比赛说明加载失败：${error.message}`, "error");
      return null;
    }
  }

  function updateNoticeTime(notice) {
    return notice.createdTime ? formatDate(notice.createdTime) : notice.createdAt || "时间未知";
  }

  async function loadNotices(options = {}) {
    const refreshDetail = options.refreshDetail !== false;
    try {
      const notices = await request(apiPath("/api/notices", options.refresh));
      state.notices = Array.isArray(notices) ? [...notices].sort((a, b) => (b.createdTime || 0) - (a.createdTime || 0)) : [];
      const ids = new Set(state.notices.map((notice) => notice.id));
      if (state.selectedNoticeId === null || !ids.has(state.selectedNoticeId)) state.selectedNoticeId = state.notices[0]?.id ?? null;
      if (!ids.has(state.selectedNoticeId)) state.noticeDetail = null;
      renderNotices();
      if (refreshDetail && state.selectedNoticeId !== null) await loadNoticeDetail(state.selectedNoticeId, { silent: true, refresh: Boolean(options.refresh) });
      return state.notices;
    } catch (error) {
      if (!state.notices.length) showLoadError(els.noticeList, "公告列表暂时不可用");
      if (!options.silent) showToast(`公告刷新失败：${error.message}`, "error");
      return null;
    }
  }

  function renderNotices() {
    els.noticeCount.textContent = formatNumber(state.notices.length);
    if (!state.notices.length) {
      els.noticeList.replaceChildren(createElement("div", "empty-state compact", "暂无公告"));
      return;
    }
    const fragment = document.createDocumentFragment();
    state.notices.forEach((notice) => {
      const button = createElement("button", `notice-item${state.selectedNoticeId === notice.id ? " is-selected" : ""}`);
      button.type = "button";
      button.setAttribute("aria-current", state.selectedNoticeId === notice.id ? "true" : "false");
      button.append(icon(notice.isFile ? "file-text" : "megaphone"));
      const main = createElement("span", "notice-item-main");
      main.append(createElement("span", "notice-item-title", notice.title || "未命名公告"), createElement("span", "notice-item-meta", `${notice.userName || "系统公告"} · ${updateNoticeTime(notice)}`));
      button.append(main);
      button.addEventListener("click", () => selectNotice(notice.id));
      fragment.append(button);
    });
    els.noticeList.replaceChildren(fragment);
    refreshIcons();
  }

  async function selectNotice(id) {
    state.selectedNoticeId = id;
    renderNotices();
    await loadNoticeDetail(id);
  }

  async function loadNoticeDetail(id, options = {}) {
    try {
      const detail = await request(apiPath(`/api/notices/${encodeURIComponent(id)}`, options.refresh));
      if (state.selectedNoticeId !== id) return detail;
      state.noticeDetail = detail;
      renderNoticeDetail();
      return detail;
    } catch (error) {
      if (!options.silent) showToast(`公告详情加载失败：${error.message}`, "error");
      return null;
    }
  }

  function renderNoticeDetail() {
    const detail = state.noticeDetail;
    if (!detail) {
      els.noticeDetail.replaceChildren(createElement("div", "empty-state compact", "暂无公告详情"));
      return;
    }
    const header = createElement("h3", "", detail.title || "未命名公告");
    const meta = createElement("p", "notice-meta", formatDate(detail.createdTime));
    const content = renderRichText(detail.content || "暂无正文", "notice-content rich-text");
    const fragment = document.createDocumentFragment();
    fragment.append(header, meta, content);
    const files = [...((detail.file && detail.file.files) || [])];
    if (detail.url && !files.some((file) => file.url === detail.url)) files.push({ name: "公告附件", url: detail.url });
    if (files.length) {
      const fileSection = createElement("div", "notice-files");
      files.forEach((file) => {
        const url = safeURL(file.url);
        if (!url) return;
        const link = createElement("a", "notice-file file-link");
        link.href = url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.download = file.name || "notice-file";
        link.append(icon("paperclip"), createElement("span", "", file.name || "下载附件"));
        fileSection.append(link);
      });
      if (fileSection.childNodes.length) fragment.append(fileSection);
    }
    els.noticeDetail.replaceChildren(fragment);
    refreshIcons();
  }

  function codexStatusLabel(status) {
    return {
      queued: "排队中",
      running: "运行中",
      completed: "已完成",
      failed: "失败",
      canceled: "已停止",
    }[status] || "未知状态";
  }

  function codexStatusClass(status) {
    if (status === "completed") return "success";
    if (status === "failed") return "error";
    if (status === "canceled") return "muted";
    return "running";
  }

  function isCodexTerminal(status) {
    return status === "completed" || status === "failed" || status === "canceled";
  }

  function codexTaskTime(value) {
    if (!value) return "时间未知";
    const timestamp = new Date(value).getTime();
    return Number.isNaN(timestamp) ? "时间未知" : formatDate(timestamp);
  }

  function codexTaskExerciseName(task) {
    const exercise = state.groups.flatMap((group) => group.corpus || []).find((item) => item.id === task.exerciseId);
    return exercise ? exercise.name || `题目 ${task.exerciseId}` : `题目 ${task.exerciseId}`;
  }

  function codexEventsNearBottom() {
    const node = els.codexTaskEvents;
    return node.scrollHeight - node.scrollTop - node.clientHeight <= 28;
  }

  function scrollCodexEventsToBottom() {
    if (state.codexEventsFollowTail) els.codexTaskEvents.scrollTop = els.codexTaskEvents.scrollHeight;
  }

  function codexModeLabel(mode) {
    if (mode === "pure") return "纯解题";
    if (mode === "side") return "Side 对话";
    return "完整模式";
  }

  function renderCodexTaskList() {
    if (!state.codexTasks.length) {
      els.codexTaskList.replaceChildren(createElement("div", "empty-state compact", "暂无 Codex 任务"));
      return;
    }
    const fragment = document.createDocumentFragment();
    state.codexTasks.forEach((task) => {
      const button = createElement("button", `codex-task-item${task.id === state.codexSelectedTaskId ? " is-selected" : ""}`);
      button.type = "button";
      button.setAttribute("aria-current", task.id === state.codexSelectedTaskId ? "true" : "false");
      const title = createElement("span", "codex-task-item-title", codexTaskExerciseName(task));
      const meta = createElement("span", "codex-task-item-meta", `#${task.exerciseId} · ${codexModeLabel(task.mode)} · ${codexStatusLabel(task.status)}`);
      const status = createElement("span", `codex-task-item-status ${codexStatusClass(task.status)}`, task.status === "queued" ? `第 ${task.queuePosition || "—"} 位` : codexStatusLabel(task.status));
      if (task.mode === "side") title.append(createElement("span", "codex-task-side-badge", "Side"));
      button.append(title, meta, status);
      button.addEventListener("click", () => {
        state.codexSelectedTaskId = task.id;
        renderCodexTaskList();
        void loadCodexTask(task.id);
      });
      fragment.append(button);
    });
    els.codexTaskList.replaceChildren(fragment);
    refreshIcons();
  }

  function renderCodexTaskDetail(task) {
    if (!task) {
      els.codexTaskStatus.textContent = "选择一个任务";
      els.codexTaskMeta.textContent = "--";
      els.codexTaskEvents.textContent = "尚未选择任务";
      state.codexEventsFollowTail = true;
      els.codexTaskOutput.textContent = "尚未完成";
      els.codexTaskResume.hidden = true;
      els.codexTaskResume.replaceChildren();
      els.codexTaskWriteup.textContent = "";
      els.cancelCodexTask.disabled = true;
      els.continueCodexTask.disabled = true;
      els.deleteCodexTask.disabled = true;
      els.openCodexTerminal.disabled = true;
      els.codexTaskMessage.value = "";
      els.codexTaskFollowUp.disabled = true;
      els.codexTaskSide.disabled = true;
      return;
    }
    els.codexTaskStatus.className = `codex-task-status ${codexStatusClass(task.status)}`;
    els.codexTaskStatus.textContent = `${codexModeLabel(task.mode)} · ${codexStatusLabel(task.status)} · ${codexTaskExerciseName(task)}`;
    const started = task.startedAt ? codexTaskTime(task.startedAt) : "尚未启动";
    const finished = task.finishedAt ? ` · 结束 ${codexTaskTime(task.finishedAt)}` : "";
    els.codexTaskMeta.textContent = `任务 ${task.id} · 创建 ${codexTaskTime(task.createdAt)} · 开始 ${started}${finished}`;
    const shouldFollow = state.codexEventsFollowTail || codexEventsNearBottom();
    const eventLines = (task.events || []).map((event) => `[${event.kind || "event"}] ${event.summary || ""}`);
    els.codexTaskEvents.textContent = eventLines.length ? eventLines.join("\n") : "暂无运行日志";
    state.codexEventsFollowTail = shouldFollow;
    requestAnimationFrame(scrollCodexEventsToBottom);
    els.codexTaskOutput.textContent = task.output || (task.error ? `任务失败：${task.error}` : "尚未完成");
    els.codexTaskResume.hidden = !task.sessionId;
    els.codexTaskResume.replaceChildren();
    if (task.sessionId) {
      const resumeCommand = `codex resume ${task.sessionId}`;
      els.codexTaskResume.append(
        createElement("code", "", resumeCommand),
        createElement("small", "codex-task-resume-note", "会话保存在本项目 runtime/codex；建议点击“打开对话”"),
        makeCopyButton(resumeCommand),
      );
    }
    els.codexTaskWriteup.textContent = task.writeupPath ? `WP：${task.writeupPath}` : "";
    const terminal = isCodexTerminal(task.status);
    els.cancelCodexTask.disabled = terminal;
    els.continueCodexTask.disabled = !terminal || !task.sessionId;
    els.deleteCodexTask.disabled = !terminal;
    els.openCodexTerminal.disabled = !terminal || !task.sessionId;
    const canMessage = Boolean(task.sessionId) && task.status !== "queued" && task.mode !== "side";
    els.codexTaskFollowUp.disabled = !canMessage;
    els.codexTaskSide.disabled = !canMessage;
  }

  function renderCodexTaskState() {
    const selected = state.codexTasks.find((task) => task.id === state.codexSelectedTaskId) || null;
    renderCodexTaskList();
    renderCodexTaskDetail(selected);
    const canRun = state.codexAvailable === true && state.selectedExerciseId !== null;
    els.runCodex.disabled = !canRun;
    els.runCodexPure.disabled = !canRun;
  }

  async function openCodexTerminal() {
    const id = state.codexSelectedTaskId;
    if (!id) return;
    await withButton(els.openCodexTerminal, async () => {
      try {
        const result = await request(`/api/codex/tasks/${encodeURIComponent(id)}/terminal`, {
          method: "POST",
        });
        showToast(`已打开系统终端，可继续 Codex 对话（${result.scriptPath || "本地恢复脚本"}）`, "success");
      } catch (error) {
        showToast(`打开 Codex 对话失败：${error.message}`, "error");
      }
    });
  }

  function stopCodexPollingIfIdle() {
    const active = state.codexTasks.some((task) => !isCodexTerminal(task.status));
    if (!active && !els.codexTaskDialog.open && state.codexPollTimer) {
      window.clearInterval(state.codexPollTimer);
      state.codexPollTimer = null;
    }
  }

  function ensureCodexPolling() {
    if (!state.codexPollTimer) state.codexPollTimer = window.setInterval(() => loadCodexTasks(), 1000);
  }

  async function loadCodexTask(id) {
    try {
      const task = await request(`/api/codex/tasks/${encodeURIComponent(id)}`);
      const index = state.codexTasks.findIndex((item) => item.id === id);
      if (index >= 0) state.codexTasks[index] = task;
      else state.codexTasks.unshift(task);
      state.codexSelectedTaskId = id;
      renderCodexTaskState();
      return task;
    } catch (error) {
      if (error.code !== "CODEX_TASK_NOT_FOUND") showToast(`Codex 任务读取失败：${error.message}`, "error");
      return null;
    }
  }

  async function loadCodexTasks(options = {}) {
    try {
      const previous = new Map(state.codexTasks.map((task) => [task.id, task.status]));
      const result = await request("/api/codex/tasks");
      state.codexAvailable = result.available === undefined ? true : Boolean(result.available);
      state.codexTasks = Array.isArray(result.tasks) ? result.tasks : [];
      els.codexActiveCount.textContent = result.available === false
        ? "未配置"
        : `${Number(result.active || 0)}/${Number(result.limit || 0)}`;
      if (state.codexSelectedTaskId === null && state.codexTasks.length) state.codexSelectedTaskId = state.codexTasks[0].id;
      renderCodexTaskState();
      for (const task of state.codexTasks) {
        if (isCodexTerminal(task.status) && previous.get(task.id) && previous.get(task.id) !== task.status && !state.codexTerminalSeen.has(task.id)) {
          state.codexTerminalSeen.add(task.id);
          if (task.exerciseId === state.selectedExerciseId) await loadExerciseDetail(task.exerciseId, { refresh: true, silent: true });
          await loadOverview({ refresh: true, silent: true });
        }
      }
      const active = state.codexTasks.some((task) => !isCodexTerminal(task.status));
      if (active || els.codexTaskDialog.open) ensureCodexPolling();
      else stopCodexPollingIfIdle();
      return result;
    } catch (error) {
      if (error.code === "CODEX_UNAVAILABLE") {
        state.codexAvailable = false;
        els.codexActiveCount.textContent = "不可用";
        renderCodexTaskState();
      } else if (options.notify) {
        showToast(`Codex 任务刷新失败：${error.message}`, "error");
      }
      stopCodexPollingIfIdle();
      return null;
    }
  }

  function openCodexTaskDialog() {
    if (typeof els.codexTaskDialog.showModal === "function") els.codexTaskDialog.showModal();
    else els.codexTaskDialog.setAttribute("open", "open");
    ensureCodexPolling();
    void loadCodexTasks();
  }

  function openCodexPromptDialog(mode) {
    state.codexStartMode = mode;
    const isPure = mode === "pure";
    els.codexStartPrompt.value = state.codexSystemPrompt;
    els.codexPromptMode.textContent = isPure
      ? "题目的标准 AI 提示词（含题目详情、附件路径和接口说明）会自动作为任务正文发送；这里修改本次附加系统指令，纯解题安全约束会始终附加。"
      : "题目的标准 AI 提示词（含题目详情、附件路径和接口说明）会自动作为任务正文发送；这里修改本次附加系统指令，不会改变全局配置。";
    if (typeof els.codexPromptDialog.showModal === "function") els.codexPromptDialog.showModal();
    else els.codexPromptDialog.setAttribute("open", "open");
    els.codexStartPrompt.focus();
  }

  async function startCodexTask(mode) {
    const id = state.selectedExerciseId;
    if (id === null || state.codexAvailable !== true) return;
    openCodexPromptDialog(mode);
  }

  async function confirmCodexStart() {
    const id = state.selectedExerciseId;
    const mode = state.codexStartMode;
    if (id === null || state.codexAvailable !== true) return;
    const isPure = mode === "pure";
    const button = isPure ? els.runCodexPure : els.runCodex;
    const systemPrompt = els.codexStartPrompt.value.trim();
    els.codexPromptDialog.close();
    await withButton(button, async () => {
      try {
        const task = await request(`/api/exercises/${encodeURIComponent(id)}/codex/${mode}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ systemPrompt }),
        });
        state.codexSelectedTaskId = task.taskId || task.id;
        openCodexTaskDialog();
        showToast(isPure ? "Codex 纯解题任务已提交" : "Codex 任务已提交", "success");
        await loadCodexTasks();
      } catch (error) {
        showToast(`Codex 启动失败：${error.message}`, "error");
      }
    });
  }

  async function sendCodexMessage(side) {
    const id = state.codexSelectedTaskId;
    const message = els.codexTaskMessage.value.trim();
    if (!id || !message) return;
    const button = side ? els.codexTaskSide : els.codexTaskFollowUp;
    await withButton(button, async () => {
      try {
        const task = await request(`/api/codex/tasks/${encodeURIComponent(id)}/message`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message, side }),
        });
        els.codexTaskMessage.value = "";
        if (side && task.id) state.codexSelectedTaskId = task.id;
        await loadCodexTasks({ notify: true });
        showToast(side ? "Side 提问已提交" : "追加消息已提交", "success");
      } catch (error) {
        showToast(`${side ? "Side 提问" : "追加消息"}失败：${error.message}`, "error");
      }
    });
  }

  async function runCodex() {
    await startCodexTask("run");
  }

  async function runCodexPure() {
    await startCodexTask("pure");
  }

  async function cancelCodexTask() {
    const id = state.codexSelectedTaskId;
    if (!id || !window.confirm("确认停止当前 Codex 任务？")) return;
    await withButton(els.cancelCodexTask, async () => {
      try {
        await request(`/api/codex/tasks/${encodeURIComponent(id)}/cancel`, { method: "POST" });
        showToast("Codex 任务停止请求已提交", "success");
        await loadCodexTasks({ notify: true });
      } catch (error) {
        showToast(`Codex 停止失败：${error.message}`, "error");
      }
    });
  }

  async function continueCodexTask() {
    const id = state.codexSelectedTaskId;
    if (!id) return;
    await withButton(els.continueCodexTask, async () => {
      try {
        await request(`/api/codex/tasks/${encodeURIComponent(id)}/continue`, { method: "POST" });
        showToast("已发送继续指令", "success");
        await loadCodexTasks({ notify: true });
      } catch (error) {
        showToast(`继续任务失败：${error.message}`, "error");
      }
    });
  }

  async function deleteCodexTask() {
    const id = state.codexSelectedTaskId;
    if (!id || !window.confirm("确认删除当前 Codex 任务及其本地日志？")) return;
    await withButton(els.deleteCodexTask, async () => {
      try {
        await request(`/api/codex/tasks/${encodeURIComponent(id)}`, { method: "DELETE" });
        state.codexTasks = state.codexTasks.filter((task) => task.id !== id);
        state.codexSelectedTaskId = state.codexTasks[0]?.id || null;
        renderCodexTaskState();
        showToast("Codex 任务已删除", "success");
      } catch (error) {
        showToast(`删除任务失败：${error.message}`, "error");
      }
    });
  }

  function toggleScoreRefresh() {
    if (state.scoreTimer) window.clearInterval(state.scoreTimer);
    state.scoreTimer = els.scoreRefresh.checked ? window.setInterval(() => loadOverview({ silent: true, refresh: true }), SCORE_REFRESH_MS) : null;
  }

  function toggleNoticeRefresh() {
    if (state.noticeTimer) window.clearInterval(state.noticeTimer);
    state.noticeTimer = els.noticeRefresh.checked ? window.setInterval(() => loadNotices({ silent: true, refresh: true, refreshDetail: true }), NOTICE_REFRESH_MS) : null;
  }

  function wireEvents() {
    $("refresh-overview").addEventListener("click", () => withButton($("refresh-overview"), () => loadOverview({ refresh: true })));
    $("refresh-exercises").addEventListener("click", () => withButton($("refresh-exercises"), () => loadExercises({ refresh: true })));
    els.activeEnvironmentRefresh.addEventListener("click", () => refreshTrackedEnvironments({ refresh: true, notify: true }));
    els.stopAllEnvironments.addEventListener("click", stopAllEnvironments);
    $("refresh-notices").addEventListener("click", () => withButton($("refresh-notices"), () => loadNotices({ refresh: true, refreshDetail: true })));
    els.selectedExerciseRefresh.addEventListener("click", () => {
      const id = state.selectedExerciseId;
      if (id !== null) withButton(els.selectedExerciseRefresh, () => loadExerciseDetail(id, { refresh: true }));
    });
    els.openAIPrompt.addEventListener("click", openAIPromptDialog);
    els.runCodex.addEventListener("click", runCodex);
    els.runCodexPure.addEventListener("click", runCodexPure);
    els.openCodexTasks.addEventListener("click", openCodexTaskDialog);
    els.openSettings.addEventListener("click", openSettingsDialog);
    els.openToolManager.addEventListener("click", openToolManagerDialog);
    $("close-tool-manager").addEventListener("click", () => els.toolManagerDialog.close());
    els.toolManagerDialog.addEventListener("click", (event) => {
      if (event.target === els.toolManagerDialog) els.toolManagerDialog.close();
    });
    els.matchBindForm.addEventListener("submit", (event) => {
      event.preventDefault();
      void bindMatch();
    });
    els.loginModePassword.addEventListener("click", () => setLoginMode("password"));
    els.loginModeSms.addEventListener("click", () => setLoginMode("sms"));
    els.passwordLoginForm.addEventListener("submit", passwordLogin);
    els.smsLoginForm.addEventListener("submit", smsLogin);
    els.platformLogout.addEventListener("click", logoutPlatform);
    els.sendSmsCode.addEventListener("click", sendSmsCode);
    els.refreshPasswordCaptcha.addEventListener("click", () => refreshCaptcha("image"));
    els.refreshRotateCaptcha.addEventListener("click", () => refreshCaptcha("rotate"));
    els.rotateCaptchaAngle.addEventListener("input", updateCaptchaRotation);
    els.settingsForm.addEventListener("submit", saveRuntimeConfig);
    els.fetchModels.addEventListener("click", () => void fetchModelList("model"));
    els.fetchCodexModels.addEventListener("click", () => void fetchModelList("codex"));
    bindModelPicker(els.settingModelName, els.modelOptions);
    bindModelPicker(els.settingCodexModel, els.codexModelOptions);
    document.addEventListener("click", (event) => {
      if (event.target.closest(".model-picker")) return;
      els.modelOptions.hidden = true;
      els.codexModelOptions.hidden = true;
    });
    els.installSelectedTools.addEventListener("click", () => installTools(false));
    els.installAllTools.addEventListener("click", () => installTools(true));
    els.cancelToolInstall.addEventListener("click", cancelToolInstall);
    els.refreshEnvironmentStatus.addEventListener("click", () => withButton(els.refreshEnvironmentStatus, () => loadEnvironmentStatus({ refresh: true, notify: true })));
    $("close-settings").addEventListener("click", () => {
      els.settingPlatformToken.value = "";
      els.passwordValue.value = "";
      els.passwordImageCode.value = "";
      els.smsCode.value = "";
      els.settingsDialog.close();
    });
    els.settingsDialog.addEventListener("click", (event) => {
      if (event.target === els.settingsDialog) els.settingsDialog.close();
    });
    els.settingsDialog.addEventListener("close", () => {
      els.settingPlatformToken.value = "";
      els.passwordValue.value = "";
      els.passwordImageCode.value = "";
      els.smsCode.value = "";
      els.passwordCaptcha.removeAttribute("src");
      els.rotateCaptchaImage.removeAttribute("src");
    });
    els.cancelCodexTask.addEventListener("click", cancelCodexTask);
    els.continueCodexTask.addEventListener("click", continueCodexTask);
    els.deleteCodexTask.addEventListener("click", deleteCodexTask);
    const updateCodexEventsFollowState = () => {
      state.codexEventsFollowTail = codexEventsNearBottom();
    };
    els.codexTaskEvents.addEventListener("scroll", updateCodexEventsFollowState);
    els.codexTaskEvents.addEventListener("wheel", () => window.requestAnimationFrame(updateCodexEventsFollowState), { passive: true });
    els.codexTaskEvents.addEventListener("touchmove", () => window.requestAnimationFrame(updateCodexEventsFollowState), { passive: true });
    els.codexTaskFollowUp.addEventListener("click", () => sendCodexMessage(false));
    els.codexTaskSide.addEventListener("click", () => sendCodexMessage(true));
    els.openCodexTerminal.addEventListener("click", () => void openCodexTerminal());
    els.confirmCodexStart.addEventListener("click", confirmCodexStart);
    els.cancelCodexStart.addEventListener("click", () => els.codexPromptDialog.close());
    $("close-codex-prompt").addEventListener("click", () => els.codexPromptDialog.close());
    els.codexPromptDialog.addEventListener("click", (event) => {
      if (event.target === els.codexPromptDialog) els.codexPromptDialog.close();
    });
    $("close-codex-tasks").addEventListener("click", () => {
      els.codexTaskDialog.close();
      stopCodexPollingIfIdle();
    });
    els.codexTaskDialog.addEventListener("click", (event) => {
      if (event.target === els.codexTaskDialog) {
        els.codexTaskDialog.close();
        stopCodexPollingIfIdle();
      }
    });
    els.aiCopyPrompt.addEventListener("click", copyAIPrompt);
    els.aiRun.addEventListener("click", runAISolver);
    $("close-ai-prompt").addEventListener("click", () => els.aiPromptDialog.close());
    els.aiPromptDialog.addEventListener("click", (event) => {
      if (event.target === els.aiPromptDialog) els.aiPromptDialog.close();
    });
    $("open-attachment-manager").addEventListener("click", openAttachmentManager);
    els.attachmentCheck.addEventListener("click", checkAttachments);
    els.attachmentDownloadAll.addEventListener("click", downloadAllAttachments);
    els.attachmentPause.addEventListener("click", () => controlAttachmentDownload("pause"));
    els.attachmentResume.addEventListener("click", () => controlAttachmentDownload("resume"));
    els.attachmentCancel.addEventListener("click", cancelAttachmentDownload);
    $("close-attachment-manager").addEventListener("click", () => els.attachmentDialog.close());
    els.attachmentDialog.addEventListener("click", (event) => {
      if (event.target === els.attachmentDialog) els.attachmentDialog.close();
    });
    els.scoreRefresh.addEventListener("change", toggleScoreRefresh);
    els.noticeRefresh.addEventListener("change", toggleNoticeRefresh);
    els.flagInput.addEventListener("input", updateFlagLength);
    els.flagForm.addEventListener("submit", submitFlag);
    $("open-match-info").addEventListener("click", () => {
      if (typeof els.matchDialog.showModal === "function") els.matchDialog.showModal();
      else els.matchDialog.setAttribute("open", "open");
    });
    $("refresh-match-info").addEventListener("click", () => withButton($("refresh-match-info"), () => loadMatchInfo({ refresh: true })));
    $("close-match-info").addEventListener("click", () => els.matchDialog.close());
    els.matchDialog.addEventListener("click", (event) => {
      if (event.target === els.matchDialog) els.matchDialog.close();
    });
  }

  async function init() {
    setConnection("connecting");
    loadTrackedEnvironmentIds();
    loadCollapsedGroups();
    loadCollapsedAttachmentCategories();
    renderActiveEnvironments();
    wireEvents();
    setWorkspaceActionsEnabled(false);
    updateFlagLength();
    updateCaptchaRotation();
    refreshIcons();
    const config = await loadRuntimeConfig();
    if (config && config.configured && state.authenticated) await refreshWorkspaceAfterLogin();
    else if (config && config.configured) showAuthenticationRequiredWorkspace();
    else if (config) showUnconfiguredWorkspace();
    await resumeAttachmentDownload();
    await loadCodexTasks();
  }

  window.addEventListener("storage", (event) => {
    if (event.key !== ACTIVE_ENVIRONMENT_STORAGE_KEY) return;
    loadTrackedEnvironmentIds();
    void restoreTrackedEnvironments();
  });

  window.addEventListener("beforeunload", () => {
    if (state.scoreTimer) window.clearInterval(state.scoreTimer);
    if (state.noticeTimer) window.clearInterval(state.noticeTimer);
    if (state.codexPollTimer) window.clearInterval(state.codexPollTimer);
    if (state.toolManagerTimer) window.clearTimeout(state.toolManagerTimer);
    stopAttachmentTaskPolling();
    stopAttachmentSizePolling();
    state.pollToken += 1;
  });

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, { once: true });
  else init();
})();
