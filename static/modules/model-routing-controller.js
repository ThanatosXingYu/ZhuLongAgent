export function createModelRoutingController({
  els,
  request,
  createElement,
  icon,
  refreshIcons,
  withButton,
  formatNumber,
  showToast,
  onConfigLoaded,
}) {
  let providers = [];
  let activeProviderId = "";
  let poolEnabled = true;
  let strategy = "round-robin";
  let editingProviderId = "";
  let providerSequence = 0;
  let providerStatus = new Map();
  let statusPollTimer = null;

  function setStatus(message, kind = "") {
    els.modelRoutingStatus.textContent = message;
    els.modelRoutingStatus.className = `model-routing-status${kind ? ` ${kind}` : ""}`;
  }

  function setSheetStatus(message, kind = "") {
    els.modelPoolSheetStatus.textContent = message;
    els.modelPoolSheetStatus.className = `settings-status${kind ? ` ${kind}` : ""}`;
  }

  function setTab(tab) {
    const selected = tab === "routing" ? "routing" : "providers";
    els.modelRoutingPage.querySelectorAll("[data-model-tab]").forEach((button) => {
      const active = button.dataset.modelTab === selected;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    els.modelRoutingPage.querySelectorAll("[data-model-panel]").forEach((panel) => {
      panel.hidden = panel.dataset.modelPanel !== selected;
    });
  }

  function setStrategy(value) {
    strategy = value === "failover" ? "failover" : "round-robin";
    els.modelRoutingPage.querySelectorAll("[data-routing]").forEach((button) => {
      const active = button.dataset.routing === strategy;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    updateSummary();
  }

  function providerById(id) {
    return providers.find((provider) => provider.id === id) || null;
  }

  function providerProtocolLabel(protocol) {
    return {
      "openai-chat": "OpenAI Chat",
      "openai-responses": "Responses",
      anthropic: "Anthropic",
    }[protocol] || protocol || "未知协议";
  }

  function providerHealth(provider) {
    const runtime = providerStatus.get(provider.id) || {};
    if (!provider.enabled) return { label: "已停用", tone: "muted", hint: "该配置不会发出请求" };
    if (!provider.apiKeyConfigured) {
      return { label: "未配置 Key", tone: "warning", hint: "需要填写 API Key 后才能调用" };
    }
    if (runtime.available === false) {
      const seconds = Number(runtime.cooldownSeconds || 0);
      return {
        label: seconds > 0 ? `冷却 ${seconds}s` : "不可用",
        tone: "error",
        hint: runtime.lastError || "提供商暂不可用",
      };
    }
    return { label: "可用", tone: "success", hint: "当前配置可以接收请求" };
  }

  function updateSummary() {
    const enabled = providers.filter((provider) => provider.enabled).length;
    const participantIds = routingOrder();
    const active = providerById(activeProviderId);
    els.modelActiveProviderCount.textContent = formatNumber(enabled);
    els.modelTotalProviderCount.textContent = `共 ${formatNumber(providers.length)} 个`;
    els.modelActiveProviderName.textContent = active?.name || "未选择";
    els.modelPoolEnabled.checked = poolEnabled;
    els.modelPoolEnabledSheet.checked = poolEnabled;
    els.modelPoolState.textContent = poolEnabled ? "已开启" : "已关闭";
    els.modelPoolHeaderBadge.hidden = !poolEnabled;
    els.modelPoolParticipantCount.textContent = poolEnabled
      ? `${formatNumber(participantIds.length)} 个参与项`
      : "仅使用激活配置";
    els.modelPoolParticipantCountSheet.textContent = poolEnabled
      ? `${formatNumber(participantIds.length)} 个参与项`
      : "轮询已关闭，仅使用激活配置";
    els.modelStrategySummary.textContent = strategy === "failover" ? "故障转移" : "每次轮询";
    els.modelRecoverySummary.textContent = `${formatNumber(Number(els.modelRecoveryInterval?.value || 60))} 秒`;
    renderPoolChain();
  }

  function routingOrder() {
    const active = providerById(activeProviderId);
    const others = providers.filter((provider) =>
      provider.id !== activeProviderId
      && provider.enabled
      && !provider.poolExclude);
    return [active, ...others].filter(Boolean);
  }

  function renderPoolChain() {
    if (!els.modelPoolChain) return;
    const order = poolEnabled
      ? routingOrder()
      : [providerById(activeProviderId)].filter(Boolean);
    const fragment = document.createDocumentFragment();
    if (!order.length) {
      fragment.append(createElement("div", "model-pool-empty", "还没有可用模型配置"));
    }
    order.forEach((provider, index) => {
      const runtime = providerStatus.get(provider.id) || {};
      const health = providerHealth(provider);
      const row = createElement("div", "model-pool-chain-row");
      row.dataset.providerId = provider.id;
      const orderBadge = createElement("span", "model-pool-order", String(index + 1));
      const copy = createElement("span", "model-pool-chain-copy");
      const title = createElement("span", "model-pool-chain-title");
      title.append(
        createElement("strong", "", provider.name || provider.id),
        provider.id === activeProviderId
          ? createElement("span", "model-pool-active-badge", "激活")
          : createElement("span", "", ""),
      );
      copy.append(
        title,
        createElement("code", "", provider.model || "未填写模型"),
        createElement("small", "", provider.baseUrl || "未填写 Base URL"),
      );
      const state = createElement("span", `model-health ${health.tone}`, health.label);
      state.title = [health.hint, runtime.lastError].filter(Boolean).join(" · ");
      row.append(orderBadge, copy, state);
      fragment.append(row);
    });
    els.modelPoolChain.replaceChildren(fragment);
  }

  function renderProviders() {
    const fragment = document.createDocumentFragment();
    providers.forEach((provider, index) => {
      const active = provider.id === activeProviderId;
      const health = providerHealth(provider);
      const card = createElement(
        "article",
        `model-provider-card${active ? " is-active-provider" : ""}${provider.enabled ? "" : " is-disabled"}`,
      );
      card.dataset.providerId = provider.id;
      card.tabIndex = 0;
      card.setAttribute("role", "button");
      card.setAttribute("aria-label", `编辑模型配置 ${provider.name || provider.id}`);
      card.style.setProperty("--provider-index", String(index + 1));
      const top = createElement("div", "model-provider-card-top");
      const star = createElement("span", "model-provider-star");
      star.append(icon(active ? "star" : "circle"));
      const identity = createElement("span", "model-provider-identity");
      identity.append(
        createElement("strong", "", provider.name || provider.id),
        createElement("code", "", provider.model || "未填写模型"),
      );
      const badge = createElement("span", `model-health ${health.tone}`, health.label);
      badge.title = health.hint;
      top.append(star, identity, badge);
      const details = createElement("div", "model-provider-card-details");
      details.append(
        createElement("span", "model-provider-protocol", providerProtocolLabel(provider.protocol)),
        createElement("span", "", provider.id === activeProviderId ? "请求首选" : provider.poolExclude ? "不参与轮询" : "轮询成员"),
        createElement("span", "", provider.apiKeyConfigured ? "Key 已配置" : "Key 未配置"),
      );
      const url = createElement("code", "model-provider-card-url", provider.baseUrl || "未填写 Base URL");
      const actions = createElement("div", "model-provider-card-actions");
      const activate = createElement(
        "button",
        `button button-compact ${active ? "button-ghost" : "button-primary"}`,
        active ? "已激活" : "设为激活",
      );
      activate.type = "button";
      activate.disabled = active || !provider.enabled;
      activate.addEventListener("click", (event) => {
        event.stopPropagation();
        void activateProvider(provider.id);
      });
      const edit = createElement("button", "button button-ghost button-compact", "编辑");
      edit.type = "button";
      edit.addEventListener("click", (event) => {
        event.stopPropagation();
        openProviderSheet(provider.id);
      });
      actions.append(activate, edit);
      card.append(top, details, url, actions);
      card.addEventListener("click", () => openProviderSheet(provider.id));
      card.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          openProviderSheet(provider.id);
        }
      });
      fragment.append(card);
    });
    if (!providers.length) {
      const empty = createElement("div", "model-provider-empty");
      empty.append(
        icon("server"),
        createElement("strong", "", "还没有模型配置"),
        createElement("span", "", "点击右上角「新建」添加第一个提供商。"),
      );
      fragment.append(empty);
    }
    els.modelProviderList.replaceChildren(fragment);
    refreshIcons();
    updateSummary();
  }

  function generateProviderId() {
    let id = "";
    do {
      id = `provider-${Date.now().toString(36)}-${++providerSequence}`;
    } while (providerById(id));
    return id;
  }

  function resetSheet(provider = null) {
    editingProviderId = provider?.id || "";
    els.modelProviderSheetTitle.textContent = provider ? `编辑：${provider.name || provider.id}` : "新建模型配置";
    els.modelProviderSheetDescription.textContent = provider
      ? "修改后保存；留空 API Key 会保留当前 Key。"
      : "新建后不会自动激活，请在卡片上「设为激活」。";
    els.modelSheetName.value = provider?.name || "";
    els.modelSheetProtocol.value = provider?.protocol || "openai-chat";
    els.modelSheetBaseUrl.value = provider?.baseUrl || "";
    els.modelSheetModel.value = provider?.model || "";
    els.modelSheetApiKey.value = "";
    els.modelSheetApiKey.placeholder = provider?.apiKeyConfigured
      ? `当前 Key：${provider.apiKeyMasked || "已配置"}（留空保持）`
      : "sk-…";
    els.modelSheetEnabled.checked = provider?.enabled !== false;
    els.modelSheetPool.checked = provider ? !provider.poolExclude : true;
    els.modelSheetPool.disabled = provider?.id === activeProviderId;
    els.modelSheetDelete.hidden = !provider;
    els.modelSheetTestResult.className = "model-sheet-test-result";
    els.modelSheetTestResult.textContent = "";
    els.modelSheetModels.hidden = true;
    els.modelSheetModels.replaceChildren();
    refreshIcons();
  }

  function openProviderSheet(id = "") {
    const provider = id ? providerById(id) : null;
    resetSheet(provider);
    if (els.modelProviderSheet.open) return;
    els.modelProviderSheet.showModal();
    window.requestAnimationFrame(() => els.modelSheetName.focus());
  }

  function closeProviderSheet() {
    if (els.modelProviderSheet.open) els.modelProviderSheet.close();
    editingProviderId = "";
  }

  function sheetValues() {
    return {
      name: els.modelSheetName.value.trim(),
      protocol: els.modelSheetProtocol.value,
      baseUrl: els.modelSheetBaseUrl.value.trim(),
      model: els.modelSheetModel.value.trim(),
      apiKey: els.modelSheetApiKey.value.trim(),
      enabled: els.modelSheetEnabled.checked,
      poolExclude: editingProviderId === activeProviderId ? false : !els.modelSheetPool.checked,
    };
  }

  function validateSheet(values) {
    if (!values.name) return "请填写配置名称";
    if (!values.baseUrl) return "请填写 Base URL";
    if (!/^https?:\/\//i.test(values.baseUrl)) return "Base URL 必须以 http:// 或 https:// 开头";
    if (!values.model) return "请填写模型名称";
    return "";
  }

  function mergeProvider(provider) {
    const index = providers.findIndex((item) => item.id === provider.id);
    if (index >= 0) providers.splice(index, 1, provider);
    else providers.push(provider);
  }

  async function saveProvider() {
    const values = sheetValues();
    const validation = validateSheet(values);
    if (validation) {
      els.modelSheetTestResult.className = "model-sheet-test-result is-error";
      els.modelSheetTestResult.textContent = validation;
      return;
    }
    const id = editingProviderId || generateProviderId();
    const old = providerById(id);
    const provider = {
      id,
      name: values.name,
      baseUrl: values.baseUrl.replace(/\/+$/, ""),
      model: values.model,
      protocol: values.protocol,
      enabled: values.enabled,
      poolExclude: values.poolExclude,
      apiKeyConfigured: Boolean(values.apiKey || old?.apiKeyConfigured),
      apiKeyMasked: old?.apiKeyMasked || "",
    };
    if (values.apiKey) provider.apiKey = values.apiKey;
    if (!activeProviderId) activeProviderId = id;
    mergeProvider(provider);
    try {
      await persistProviders("模型配置已保存");
      closeProviderSheet();
    } catch (error) {
      setStatus(`保存模型配置失败：${error.message}`, "error");
    }
  }

  async function deleteProvider() {
    const provider = providerById(editingProviderId);
    if (!provider) return;
    if (provider.id === activeProviderId) {
      setStatus("当前激活配置不能直接删除，请先把其它配置设为激活", "error");
      return;
    }
    if (!window.confirm(`删除模型配置「${provider.name || provider.id}」？`)) return;
    providers = providers.filter((item) => item.id !== provider.id);
    providerStatus.delete(provider.id);
    try {
      await persistProviders("模型配置已删除");
      closeProviderSheet();
    } catch (error) {
      setStatus(`删除模型配置失败：${error.message}`, "error");
    }
  }

  async function fetchModels() {
    const values = sheetValues();
    if (!values.baseUrl) {
      els.modelSheetTestResult.className = "model-sheet-test-result is-error";
      els.modelSheetTestResult.textContent = "请先填写 Base URL";
      return;
    }
    await withButton(els.modelSheetFetchModels, async () => {
      try {
        const result = await request("/api/models", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            baseUrl: values.baseUrl,
            apiKey: values.apiKey || undefined,
            providerId: editingProviderId && providerById(editingProviderId)?.apiKeyConfigured
              ? editingProviderId
              : undefined,
            protocol: values.protocol,
            kind: "model",
          }),
        });
        const models = Array.isArray(result.models) ? result.models : [];
        const fragment = document.createDocumentFragment();
        models.forEach((modelName) => {
          const option = createElement("button", "model-sheet-model-option", String(modelName));
          option.type = "button";
          option.addEventListener("click", () => {
            els.modelSheetModel.value = String(modelName);
            els.modelSheetModels.hidden = true;
          });
          fragment.append(option);
        });
        els.modelSheetModels.replaceChildren(fragment);
        els.modelSheetModels.hidden = models.length === 0;
        if (!models.length) {
          els.modelSheetTestResult.className = "model-sheet-test-result is-error";
          els.modelSheetTestResult.textContent = "接口没有返回可用模型";
        } else if (!els.modelSheetModel.value.trim() && models.length === 1) {
          els.modelSheetModel.value = String(models[0]);
        }
        setStatus(models.length ? `获取到 ${models.length} 个模型` : "没有可用模型", models.length ? "success" : "error");
      } catch (error) {
        els.modelSheetTestResult.className = "model-sheet-test-result is-error";
        els.modelSheetTestResult.textContent = `获取模型失败：${error.message}`;
      }
    });
  }

  async function testProvider() {
    const values = sheetValues();
    const validation = validateSheet(values);
    if (validation) {
      els.modelSheetTestResult.className = "model-sheet-test-result is-error";
      els.modelSheetTestResult.textContent = validation;
      return;
    }
    await withButton(els.modelSheetTest, async () => {
      els.modelSheetTestResult.className = "model-sheet-test-result is-testing";
      els.modelSheetTestResult.textContent = "正在发送测试消息…";
      try {
        const result = await request("/api/model-test", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            baseUrl: values.baseUrl,
            apiKey: values.apiKey || undefined,
            providerId: editingProviderId && providerById(editingProviderId)?.apiKeyConfigured
              ? editingProviderId
              : undefined,
            model: values.model,
            protocol: values.protocol,
          }),
        });
        els.modelSheetTestResult.className = "model-sheet-test-result is-success";
        els.modelSheetTestResult.textContent = `连接成功 · ${formatNumber(result.latencyMs)}ms · ${result.reply || "OK"}`;
        setStatus(`连接成功：${result.model || values.model} · ${formatNumber(result.latencyMs)}ms`, "success");
      } catch (error) {
        els.modelSheetTestResult.className = "model-sheet-test-result is-error";
        els.modelSheetTestResult.textContent = `连接失败：${error.message}`;
        setStatus(`连接失败：${error.message}`, "error");
      }
    });
  }

  async function activateProvider(id) {
    if (!providerById(id)) return;
    activeProviderId = id;
    const provider = providerById(id);
    if (provider) provider.poolExclude = false;
    try {
      await persistProviders(`已激活：${provider?.name || id}`);
      await refreshProviderStatus();
    } catch (error) {
      setStatus(`激活失败：${error.message}`, "error");
    }
  }

  function payloadProviders() {
    return providers.map((provider) => {
      const item = {
        id: provider.id,
        name: provider.name,
        baseUrl: provider.baseUrl,
        model: provider.model,
        protocol: provider.protocol,
        enabled: provider.enabled,
        poolExclude: provider.id === activeProviderId ? false : provider.poolExclude,
      };
      if (provider.apiKey) item.apiKey = provider.apiKey;
      return item;
    });
  }

  function configPayload() {
    const delays = els.modelRetryDelays.value
      .split(",")
      .map((item) => Number(item.trim()))
      .filter((item) => Number.isFinite(item) && item >= 0);
    if (!delays.length) throw new Error("重试间隔不能为空");
    return {
      modelProviders: payloadProviders(),
      modelActiveProviderId: activeProviderId,
      modelPoolEnabled: poolEnabled,
      modelRoutingStrategy: strategy,
      modelRetryDelays: delays,
      modelRecoveryInterval: Number(els.modelRecoveryInterval.value || 60),
      codexAutoCompactTokenLimit: Number(els.codexAutoCompactTokenLimit.value || 500000),
    };
  }

  function replaceFromConfig(config) {
    providers = (Array.isArray(config.modelProviders) ? config.modelProviders : []).map((provider) => ({ ...provider }));
    activeProviderId = String(config.modelActiveProviderId || "");
    poolEnabled = config.modelPoolEnabled !== false;
    els.modelRetryDelays.value = (config.modelRetryDelays || [1, 5, 10, 10, 15]).join(",");
    els.modelRecoveryInterval.value = String(config.modelRecoveryInterval || 60);
    els.codexAutoCompactTokenLimit.value = String(config.codexAutoCompactTokenLimit || 500000);
    if (!activeProviderId) activeProviderId = providers.find((provider) => provider.enabled)?.id || providers[0]?.id || "";
    setStrategy(config.modelRoutingStrategy || "round-robin");
    renderProviders();
  }

  async function persistProviders(message) {
    els.saveModelRouting.disabled = true;
    setStatus("正在保存…");
    try {
      const config = await request("/api/config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(configPayload()),
      });
      onConfigLoaded(config);
      replaceFromConfig(config);
      setStatus(`${message}，已生效`, "success");
      showToast(message, "success");
      await refreshProviderStatus();
      return config;
    } finally {
      els.saveModelRouting.disabled = false;
    }
  }

  async function saveRouting() {
    try {
      await persistProviders("轮询配置已保存");
      if (els.modelPoolSheet.open) els.modelPoolSheet.close();
    } catch (error) {
      const message = `保存失败：${error.message}`;
      setStatus(message, "error");
      setSheetStatus(message, "error");
    }
  }

  async function refreshProviderStatus() {
    try {
      const result = await request("/api/model-routing/status");
      providerStatus = new Map(
        (Array.isArray(result.providers) ? result.providers : [])
          .map((provider) => [String(provider.id || ""), provider]),
      );
      renderProviders();
    } catch {
      providerStatus = new Map();
    }
  }

  function startStatusPolling() {
    stopStatusPolling();
    statusPollTimer = window.setInterval(() => {
      if (!els.modelRoutingPage.hidden) void refreshProviderStatus();
    }, 10000);
  }

  function stopStatusPolling() {
    if (statusPollTimer) window.clearInterval(statusPollTimer);
    statusPollTimer = null;
  }

  function load(config) {
    onConfigLoaded(config);
    const configured = Array.isArray(config.modelProviders) ? config.modelProviders : [];
    if (!configured.length && config.modelBaseUrl && config.modelName) {
      providers = [{
        id: "legacy",
        name: "默认提供商",
        baseUrl: config.modelBaseUrl,
        model: config.modelName,
        protocol: config.modelProtocol || "openai-chat",
        enabled: true,
        poolExclude: false,
        apiKeyConfigured: Boolean(config.modelApiKeyConfigured),
        apiKeyMasked: config.modelApiKeyMasked || "",
      }];
      activeProviderId = "legacy";
      poolEnabled = config.modelPoolEnabled !== false;
      els.modelRetryDelays.value = (config.modelRetryDelays || [1, 5, 10, 10, 15]).join(",");
      els.modelRecoveryInterval.value = String(config.modelRecoveryInterval || 60);
      els.codexAutoCompactTokenLimit.value = String(config.codexAutoCompactTokenLimit || 500000);
      setStrategy(config.modelRoutingStrategy || "round-robin");
      renderProviders();
    } else {
      replaceFromConfig(config);
    }
    setStatus(providers.length ? "点击配置卡片编辑，星标为当前激活项" : "还没有模型配置");
    setTab("providers");
    startStatusPolling();
    void refreshProviderStatus();
  }

  function bind() {
    els.addModelProvider.addEventListener("click", () => openProviderSheet());
    els.closeModelProviderSheet.addEventListener("click", closeProviderSheet);
    els.modelSheetSave.addEventListener("click", () => void saveProvider());
    els.modelSheetDelete.addEventListener("click", () => void deleteProvider());
    els.modelSheetTest.addEventListener("click", () => void testProvider());
    els.modelSheetFetchModels.addEventListener("click", () => void fetchModels());
    els.modelProviderSheet.addEventListener("close", () => {
      editingProviderId = "";
    });
    els.openModelPool.addEventListener("click", () => {
      els.modelPoolEnabledSheet.checked = poolEnabled;
      renderPoolChain();
      setSheetStatus("");
      els.modelPoolSheet.showModal();
    });
    els.closeModelPoolSheet.addEventListener("click", () => els.modelPoolSheet.close());
    els.modelPoolEnabledSheet.addEventListener("change", () => {
      poolEnabled = els.modelPoolEnabledSheet.checked;
      updateSummary();
    });
    els.saveModelPool.addEventListener("click", () => void saveRouting());
    els.saveModelRouting.addEventListener("click", () => void saveRouting());
    els.modelRoutingPage.querySelectorAll("[data-model-tab]").forEach((button) => {
      button.addEventListener("click", () => setTab(button.dataset.modelTab || "providers"));
    });
    els.modelRecoveryInterval.addEventListener("input", updateSummary);
    els.modelRoutingPage.querySelectorAll("[data-routing]").forEach((button) => {
      button.addEventListener("click", () => setStrategy(button.dataset.routing || "round-robin"));
    });
  }

  return {
    bind,
    load,
    save: saveRouting,
    setStatus,
    stopPolling: stopStatusPolling,
  };
}
