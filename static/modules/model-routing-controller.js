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
  let strategy = "round-robin";
  let activeProviderId = "";
  let poolEnabled = true;
  let providerSequence = 0;

  function setStatus(message, kind = "") {
    els.modelRoutingStatus.textContent = message;
    els.modelRoutingStatus.className = `settings-status${kind ? ` ${kind}` : ""}`;
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

  function updateSummary() {
    if (!els.modelProviderList) return;
    const providers = [...els.modelProviderList.querySelectorAll(".model-provider-item")];
    const enabledCount = providers.filter((item) => item.querySelector(".provider-enabled")?.checked).length;
    const activeItem = providers.find((item) => item.dataset.providerId === activeProviderId);
    const participants = providers.filter((item) => {
      if (!item.querySelector(".provider-enabled")?.checked) return false;
      return item.dataset.providerId === activeProviderId || item.querySelector(".provider-pool")?.checked;
    }).length;
    els.modelActiveProviderCount.textContent = formatNumber(enabledCount);
    els.modelTotalProviderCount.textContent = `共 ${formatNumber(providers.length)} 个`;
    els.modelActiveProviderName.textContent = activeItem?.querySelector(".provider-name")?.value.trim() || "未选择";
    els.modelPoolEnabled.checked = poolEnabled;
    els.modelPoolState.textContent = poolEnabled ? "已开启" : "已关闭";
    els.modelPoolParticipantCount.textContent = poolEnabled
      ? `${formatNumber(participants)} 个参与项`
      : "仅使用默认提供商";
    els.modelStrategySummary.textContent = strategy === "failover" ? "故障转移" : "每次轮询";
    els.modelRecoverySummary.textContent = `${formatNumber(Number(els.modelRecoveryInterval?.value || 60))} 秒`;
    providers.forEach((item, index) => {
      item.style.setProperty("--provider-index", String(index + 1));
      const order = item.querySelector(".provider-order");
      if (order) order.textContent = `#${index + 1}`;
      const enabled = item.querySelector(".provider-enabled")?.checked;
      const isActive = item.dataset.providerId === activeProviderId;
      item.classList.toggle("is-disabled", !enabled);
      item.classList.toggle("is-active-provider", isActive);
      const activeRadio = item.querySelector(".provider-active");
      if (activeRadio) activeRadio.checked = isActive;
      const participant = item.querySelector(".provider-pool");
      if (participant) {
        if (isActive) participant.checked = true;
        participant.disabled = !poolEnabled || !enabled || isActive;
        participant.title = isActive ? "默认提供商始终用于首选请求" : "决定是否进入轮询候选链";
      }
    });
  }

  function setActiveProvider(id) {
    if (!id) return;
    activeProviderId = String(id);
    updateSummary();
  }

  function setPoolEnabled(enabled) {
    poolEnabled = Boolean(enabled);
    updateSummary();
  }

  function addProviderRow(provider = {}) {
    const id = String(provider.id || `provider-${Date.now()}-${++providerSequence}`);
    const item = createElement("article", "model-provider-item");
    item.dataset.providerId = id;
    item.dataset.legacy = provider.legacy ? "true" : "false";
    const heading = createElement("div", "model-provider-heading");
    const providerOrder = createElement("span", "provider-order", "#");
    const providerStatus = createElement("span", "provider-status-dot");

    const enabledLabel = createElement("label", "settings-checkbox");
    const enabled = document.createElement("input");
    enabled.type = "checkbox";
    enabled.checked = provider.enabled !== false;
    enabled.className = "provider-enabled";
    enabled.addEventListener("change", updateSummary);
    enabledLabel.append(enabled, createElement("span", "", "启用"));

    const activeLabel = createElement("label", "provider-choice provider-default-choice");
    const activeRadio = document.createElement("input");
    activeRadio.type = "radio";
    activeRadio.name = "model-default-provider";
    activeRadio.className = "provider-active";
    activeRadio.value = id;
    activeRadio.checked = activeProviderId === id;
    activeRadio.addEventListener("change", () => setActiveProvider(id));
    activeLabel.append(activeRadio, createElement("span", "", "设为默认"));

    const poolLabel = createElement("label", "provider-choice provider-pool-choice");
    const participant = document.createElement("input");
    participant.type = "checkbox";
    participant.className = "provider-pool";
    participant.checked = provider.poolExclude !== true;
    participant.addEventListener("change", updateSummary);
    poolLabel.append(participant, createElement("span", "", "参与轮询"));

    const routingOptions = createElement("div", "provider-routing-options");
    routingOptions.append(enabledLabel, activeLabel, poolLabel);
    const name = document.createElement("input");
    name.type = "text";
    name.className = "provider-name";
    name.maxLength = 128;
    name.placeholder = "提供商名称";
    name.value = String(provider.name || "");
    name.addEventListener("input", updateSummary);
    const remove = createElement("button", "model-provider-remove", "删除");
    remove.type = "button";
    remove.addEventListener("click", () => {
      item.remove();
      if (activeProviderId === id) {
        activeProviderId = els.modelProviderList.querySelector(".model-provider-item")?.dataset.providerId || "";
      }
      updateSummary();
    });
    const identity = createElement("div", "provider-identity");
    identity.append(providerOrder, providerStatus, name);
    heading.append(identity, routingOptions, remove);
    if (!activeProviderId) activeProviderId = id;

    const grid = createElement("div", "model-provider-grid");
    const baseUrl = document.createElement("input");
    baseUrl.type = "url";
    baseUrl.className = "provider-base-url";
    baseUrl.placeholder = "https://api.example.com/v1";
    baseUrl.value = String(provider.baseUrl || "");
    const protocol = document.createElement("select");
    protocol.className = "provider-protocol";
    [
      ["openai-responses", "OpenAI Responses"],
      ["openai-chat", "OpenAI Chat"],
      ["anthropic", "Anthropic Messages"],
    ].forEach(([value, label]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      protocol.append(option);
    });
    protocol.value = String(provider.protocol || "openai-chat");
    const protocolWrap = createElement("span", "select-wrap");
    protocolWrap.append(protocol, icon("chevron-down"));
    const baseField = createElement("label", "provider-field");
    baseField.append(createElement("span", "", "接口地址"), baseUrl);
    const protocolField = createElement("label", "provider-field");
    protocolField.append(createElement("span", "", "协议"), protocolWrap);
    grid.append(baseField, protocolField);

    const modelGrid = createElement("div", "model-provider-grid");
    const picker = createElement("span", "model-picker");
    const model = document.createElement("input");
    model.type = "text";
    model.className = "provider-model";
    model.placeholder = "模型名称";
    model.value = String(provider.model || "");
    const menu = createElement("div", "model-options-menu model-provider-menu");
    menu.hidden = true;
    model.addEventListener("input", () => {
      menu.hidden = true;
    });
    const fetchButton = createElement("button", "button button-ghost button-compact", "获取模型");
    fetchButton.type = "button";
    picker.append(model, fetchButton, menu);
    const apiKey = document.createElement("input");
    apiKey.type = "password";
    apiKey.className = "provider-api-key";
    apiKey.autocomplete = "new-password";
    apiKey.placeholder = provider.apiKeyConfigured
      ? `当前 Key：${provider.apiKeyMasked || "已配置"}（留空保持）`
      : "API Key";
    const modelField = createElement("label", "provider-field");
    modelField.append(createElement("span", "", "模型"), picker);
    const keyField = createElement("label", "provider-field");
    keyField.append(createElement("span", "", "API Key"), apiKey);
    modelGrid.append(modelField, keyField);
    const testRow = createElement("div", "provider-test-row");
    const testButton = createElement("button", "button button-ghost button-compact provider-test", "测试连接");
    testButton.type = "button";
    const testResult = createElement("span", "provider-test-result");
    testRow.append(testButton, testResult);
    fetchButton.addEventListener("click", async () => {
      const value = baseUrl.value.trim();
      if (!value) {
        setStatus("请先填写模型地址", "error");
        return;
      }
      await withButton(fetchButton, async () => {
        try {
          const result = await request("/api/models", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              baseUrl: value,
              apiKey: apiKey.value.trim() || undefined,
              providerId: provider.apiKeyConfigured && !provider.legacy ? id : undefined,
              protocol: protocol.value,
              kind: "model",
            }),
          });
          const models = Array.isArray(result.models) ? result.models : [];
          if (!model.value.trim() && models.length === 1) model.value = models[0];
          menu.replaceChildren();
          models.forEach((modelName) => {
            const option = createElement("button", "model-option", String(modelName));
            option.type = "button";
            option.addEventListener("click", () => {
              model.value = String(modelName);
              menu.hidden = true;
            });
            menu.append(option);
          });
          menu.hidden = models.length === 0;
          setStatus(models.length ? `获取到 ${models.length} 个模型` : "没有可用模型", models.length ? "success" : "error");
        } catch (error) {
          setStatus(`获取模型失败：${error.message}`, "error");
        }
      });
    });
    testButton.addEventListener("click", async () => {
      if (!baseUrl.value.trim() || !model.value.trim()) {
        setStatus("请先填写接口地址和模型", "error");
        return;
      }
      await withButton(testButton, async () => {
        testResult.className = "provider-test-result is-testing";
        testResult.textContent = "正在发送测试消息…";
        try {
          const result = await request("/api/model-test", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              baseUrl: baseUrl.value.trim(),
              apiKey: apiKey.value.trim() || undefined,
              providerId: provider.apiKeyConfigured && !provider.legacy ? id : undefined,
              model: model.value.trim(),
              protocol: protocol.value,
            }),
          });
          testResult.className = "provider-test-result is-success";
          testResult.textContent = `连接成功 · ${formatNumber(result.latencyMs)}ms · ${result.reply || "OK"}`;
          item.classList.add("is-connection-ok");
          setStatus(`连接成功：${result.model || model.value.trim()} · ${formatNumber(result.latencyMs)}ms`, "success");
        } catch (error) {
          testResult.className = "provider-test-result is-error";
          testResult.textContent = `连接失败：${error.message}`;
          item.classList.remove("is-connection-ok");
          setStatus(`连接失败：${error.message}`, "error");
        }
      });
    });
    item.append(heading, grid, modelGrid, testRow);
    els.modelProviderList.append(item);
    updateSummary();
  }

  function collectProviders() {
    return [...els.modelProviderList.querySelectorAll(".model-provider-item")].map((item) => {
      const key = item.querySelector(".provider-api-key").value.trim();
      const provider = {
        id: item.dataset.providerId,
        name: item.querySelector(".provider-name").value.trim(),
        baseUrl: item.querySelector(".provider-base-url").value.trim(),
        model: item.querySelector(".provider-model").value.trim(),
        protocol: item.querySelector(".provider-protocol").value,
        enabled: item.querySelector(".provider-enabled").checked,
        poolExclude: item.dataset.providerId === activeProviderId
          ? false
          : !item.querySelector(".provider-pool").checked,
      };
      if (key) provider.apiKey = key;
      return provider;
    });
  }

  function load(config) {
    onConfigLoaded(config);
    const providers = Array.isArray(config.modelProviders) ? config.modelProviders : [];
    poolEnabled = config.modelPoolEnabled !== false;
    activeProviderId = String(config.modelActiveProviderId || "");
    els.modelProviderList.replaceChildren();
    if (providers.length) {
      providers.forEach(addProviderRow);
    } else if (config.modelBaseUrl && config.modelName) {
      addProviderRow({
        name: "默认提供商",
        baseUrl: config.modelBaseUrl,
        model: config.modelName,
        protocol: config.modelProtocol || "openai-chat",
        enabled: true,
        legacy: true,
        apiKeyConfigured: config.modelApiKeyConfigured,
        apiKeyMasked: config.modelApiKeyMasked,
      });
    } else {
      addProviderRow();
    }
    if (!activeProviderId) {
      activeProviderId = [...els.modelProviderList.querySelectorAll(".model-provider-item")]
        .find((item) => item.querySelector(".provider-enabled")?.checked)
        ?.dataset.providerId || els.modelProviderList.querySelector(".model-provider-item")?.dataset.providerId || "";
    }
    setStrategy(config.modelRoutingStrategy || "round-robin");
    els.modelRetryDelays.value = (config.modelRetryDelays || [1, 5, 10, 10, 15]).join(",");
    els.modelRecoveryInterval.value = String(config.modelRecoveryInterval || 60);
    els.codexAutoCompactTokenLimit.value = String(config.codexAutoCompactTokenLimit || 500000);
    updateSummary();
    setStatus("选择默认提供商，并按需开启轮询");
    setTab("providers");
    refreshIcons();
  }

  async function save() {
    const providers = collectProviders();
    if (!providers.length) {
      setStatus("至少添加一个模型提供商", "error");
      return null;
    }
    if (!activeProviderId || !providers.some((provider) => provider.id === activeProviderId)) {
      setStatus("请选择一个默认提供商", "error");
      return null;
    }
    const delays = els.modelRetryDelays.value
      .split(",")
      .map((item) => Number(item.trim()))
      .filter((item) => Number.isFinite(item) && item >= 0);
    if (!delays.length) {
      setStatus("重试间隔格式无效", "error");
      return null;
    }
    let saved = null;
    await withButton(els.saveModelRouting, async () => {
      setStatus("正在保存…");
      try {
        const config = await request("/api/config", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            modelProviders: providers,
            modelActiveProviderId: activeProviderId,
            modelPoolEnabled: poolEnabled,
            modelRoutingStrategy: strategy,
            modelRetryDelays: delays,
            modelRecoveryInterval: Number(els.modelRecoveryInterval.value || 60),
            codexAutoCompactTokenLimit: Number(els.codexAutoCompactTokenLimit.value || 500000),
          }),
        });
        onConfigLoaded(config);
        activeProviderId = String(config.modelActiveProviderId || activeProviderId);
        poolEnabled = config.modelPoolEnabled !== false;
        const savedProviders = Array.isArray(config.modelProviders) ? config.modelProviders : [];
        if (savedProviders.length) {
          els.modelProviderList.replaceChildren();
          savedProviders.forEach(addProviderRow);
          refreshIcons();
        }
        setStatus("模型路由已保存并生效", "success");
        updateSummary();
        showToast("模型提供商与轮询策略已保存", "success");
        saved = config;
      } catch (error) {
        setStatus(`保存失败：${error.message}`, "error");
      }
    });
    return saved;
  }

  function bind() {
    els.addModelProvider.addEventListener("click", () => {
      addProviderRow();
      refreshIcons();
    });
    els.modelPoolEnabled.addEventListener("change", () => setPoolEnabled(els.modelPoolEnabled.checked));
    els.modelRoutingPage.querySelectorAll("[data-model-tab]").forEach((button) => {
      button.addEventListener("click", () => setTab(button.dataset.modelTab || "providers"));
    });
    els.modelRecoveryInterval.addEventListener("input", updateSummary);
    els.modelRoutingPage.querySelectorAll("[data-routing]").forEach((button) => {
      button.addEventListener("click", () => setStrategy(button.dataset.routing || "round-robin"));
    });
    els.saveModelRouting.addEventListener("click", () => void save());
  }

  return {
    bind,
    load,
    save,
    setStatus,
  };
}
