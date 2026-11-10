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
  let profiles = [];
  let pool = { enabled: false, bind_fallback: false, chain: [] };
  let retryPolicy = null;
  let editingProfileId = "";
  let statusPollTimer = null;

  function setStatus(message, kind = "") {
    els.modelRoutingStatus.textContent = message;
    els.modelRoutingStatus.className = `model-routing-status${kind ? ` ${kind}` : ""}`;
  }

  function setSheetStatus(message, kind = "") {
    if (!message) return;
    showToast(message, kind === "error" ? "error" : "success");
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

  function profileById(id) {
    return profiles.find((profile) => profile.id === id) || null;
  }

  function protocolLabel(protocol) {
    return {
      "openai-chat": "openai",
      "openai-responses": "openai-responses",
      anthropic: "anthropic",
    }[protocol] || protocol || "未知格式";
  }

  function memberById(id) {
    return pool.chain.find((member) => String(member.profile_id) === String(id)) || null;
  }

  function healthOf(profile) {
    const member = memberById(profile.id);
    if (!profile.apiKeyConfigured) {
      return { label: "未配置 Key", tone: "warning", hint: "需要填写 API Key 后才能调用" };
    }
    if (member && member.state === "tripped") {
      const seconds = Number(member.cooldown_secs || 0);
      return {
        label: seconds > 0 ? `已熔断 · ${cooldownText(seconds)}` : "已熔断",
        tone: "error",
        hint: member.last_error || "提供商暂不可用",
      };
    }
    if (member && member.state === "degraded") {
      return {
        label: `异常 · 失败 ${formatNumber(member.fails || 0)} 次`,
        tone: "warning",
        hint: member.last_error || "连续失败但未达熔断阈值",
      };
    }
    return { label: "正常", tone: "success", hint: "当前配置可以接收请求" };
  }

  function cooldownText(seconds) {
    if (seconds <= 0) return "";
    if (seconds < 60) return `${seconds}s`;
    return `${Math.ceil(seconds / 60)}min`;
  }

  function activeProfile() {
    return profiles.find((profile) => profile.is_default || profile.isDefault) || profiles[0] || null;
  }

  function isActiveProfile(profile) {
    return Boolean(profile?.is_default || profile?.isDefault);
  }

  function routingOrder() {
    return pool.chain.filter((member) => member.active || !member.excluded);
  }

  function renderProfiles() {
    const fragment = document.createDocumentFragment();
    profiles.forEach((profile) => {
      const card = createElement(
        "article",
        `model-provider-card${isActiveProfile(profile) ? " is-active-provider" : ""}`,
      );
      card.dataset.providerId = profile.id;
      card.tabIndex = 0;
      card.setAttribute("role", "button");
      card.setAttribute("aria-label", `编辑模型配置 ${profile.name || profile.id}`);
      const top = createElement("div", "model-provider-card-top");
      const star = createElement("span", "model-provider-star");
      star.append(icon(isActiveProfile(profile) ? "star" : "circle"));
      const health = healthOf(profile);
      const badge = createElement("span", `model-health ${health.tone}`, health.label);
      badge.title = health.hint;
      const identity = createElement("span", "model-provider-identity");
      const nameRow = createElement("span", "model-provider-name-row");
      nameRow.append(
        createElement("strong", "", profile.name || profile.id),
        createElement("span", "model-protocol-badge", protocolLabel(profile.protocol)),
        badge,
      );
      identity.append(
        nameRow,
        createElement("code", "", profile.model || "未填写模型"),
      );
      top.append(star, identity);

      const details = createElement("div", "model-provider-card-details");
      if (profile.apiKeyMasked) details.append(createElement("span", "", profile.apiKeyMasked));
      details.append(
        createElement("span", "", `${formatNumber(profile.ratePerSecond || 0)}/s · ${formatNumber(profile.ratePerMinute || 0)}/min`),
      );
      if (profile.proxy) details.append(createElement("span", "truncate", `代理 ${profile.proxy}`));
      if (profile.reasoningEffort) {
        details.append(
          createElement(
            "span",
            "",
            `思考 ${profile.reasoningEffort === "off" ? "关" : profile.reasoningEffort}`,
          ),
        );
      }
      if (pool.enabled && !isActiveProfile(profile)) {
        details.append(
          createElement(
            "span",
            "",
            profile.poolExclude ? "不参与轮询" : `优先级 ${profile.priority ?? 0}`,
          ),
        );
      }

      const actions = createElement("div", "model-provider-card-actions");
      const activate = createElement(
        "button",
        "button button-compact model-provider-activate",
        isActiveProfile(profile) ? "已激活" : "设为激活",
      );
      activate.type = "button";
      activate.disabled = isActiveProfile(profile);
      activate.addEventListener("click", (event) => {
        event.stopPropagation();
        void activateProfile(profile);
      });
      const remove = createElement("button", "icon-button model-provider-remove", "");
      remove.type = "button";
      remove.title = "删除配置";
      remove.setAttribute("aria-label", "删除配置");
      remove.append(icon("trash-2"));
      remove.addEventListener("click", (event) => {
        event.stopPropagation();
        void deleteProfileFromCard(profile);
      });
      actions.append(activate, remove);
      card.append(top, details, actions);
      card.addEventListener("click", () => openProfileSheet(profile.id));
      card.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          openProfileSheet(profile.id);
        }
      });
      fragment.append(card);
    });
    if (!profiles.length) {
      const empty = createElement("div", "model-provider-empty");
      empty.append(
        icon("server"),
        createElement("strong", "", "还没有模型配置"),
        createElement("span", "", "点击右上角「新建」创建第一个。"),
      );
      fragment.append(empty);
    }
    els.modelProviderList.replaceChildren(fragment);
    refreshIcons();
    updateSummary();
  }

  function renderPoolChain() {
    if (!els.modelPoolChain) return;
    const order = pool.enabled
      ? pool.chain
      : pool.chain.filter((member) => member.active);
    const fragment = document.createDocumentFragment();
    order.forEach((member, index) => {
      const excluded = member.excluded && !member.active;
      const row = createElement("div", `model-pool-chain-row${excluded ? " is-excluded" : ""}`);
      row.dataset.providerId = String(member.profile_id);
      const orderBadge = createElement(
        "span",
        "model-pool-order",
        excluded ? "—" : String(index + 1),
      );
      const copy = createElement("span", "model-pool-chain-copy");
      const title = createElement("span", "model-pool-chain-title");
      title.append(createElement("strong", "", member.name || String(member.profile_id)));
      if (member.active) title.append(createElement("span", "model-pool-active-badge", "激活"));
      if (excluded) title.append(createElement("span", "model-pool-excluded-badge", "不参与轮询"));
      copy.append(
        title,
        createElement("code", "", member.model || "未填写模型"),
      );
      if (!member.active) {
        copy.append(createElement("small", "", `优先级 ${member.priority ?? 0}`));
      }
      if (member.last_error) copy.append(createElement("small", "", member.last_error));
      const state = createElement("span", `model-health ${member.state === "tripped" ? "error" : member.state === "degraded" ? "warning" : "success"}`, memberStateLabel(member));
      row.append(orderBadge, copy, state);
      if (member.state !== "ok" && member.state !== "degraded") {
        const recover = createElement("button", "icon-button button-compact", "");
        recover.type = "button";
        recover.title = "立即恢复";
        recover.setAttribute("aria-label", "立即恢复");
        recover.append(icon("rotate-ccw"));
        recover.addEventListener("click", () => void resetPool(String(member.profile_id)));
        row.append(recover);
      }
      fragment.append(row);
    });
    if (!order.length) {
      fragment.append(createElement("div", "model-pool-empty", "还没有可用模型配置"));
    }
    if (pool.enabled && order.filter((member) => !member.excluded || member.active).length < 2) {
      fragment.append(
        createElement(
          "p",
          "model-pool-hint",
          `当前只有 ${order.filter((member) => !member.excluded || member.active).length} 个可用配置，至少需要 2 个已填 API Key 且参与轮询的配置。`,
        ),
      );
    }
    els.modelPoolChain.replaceChildren(fragment);
    refreshIcons();
  }

  function memberStateLabel(member) {
    if (member.state === "tripped") {
      const seconds = Number(member.cooldown_secs || 0);
      return seconds > 0 ? `冷却 ${cooldownText(seconds)}` : "已熔断";
    }
    if (member.state === "degraded") return `连续失败 ${formatNumber(member.fails || 0)} 次`;
    return "正常";
  }

  function updateSummary() {
    els.modelActiveProviderCount.textContent = formatNumber(profiles.length);
    els.modelTotalProviderCount.textContent = `共 ${formatNumber(profiles.length)} 个`;
    const active = activeProfile();
    els.modelActiveProviderName.textContent = active?.name || "未选择";
    if (els.modelPoolEnabled) els.modelPoolEnabled.checked = pool.enabled;
    if (els.modelPoolEnabledSheet) els.modelPoolEnabledSheet.checked = pool.enabled;
    if (els.modelPoolBindFallbackSheet) els.modelPoolBindFallbackSheet.checked = pool.bind_fallback;
    if (els.modelPoolBindFallbackRow) els.modelPoolBindFallbackRow.hidden = !pool.enabled;
    if (els.modelPoolChainSection) els.modelPoolChainSection.hidden = !pool.enabled;
    if (els.modelPoolNote) els.modelPoolNote.hidden = !pool.enabled;
    if (els.modelPoolState) els.modelPoolState.textContent = pool.enabled ? "已开启" : "已关闭";
    if (els.modelPoolHeaderBadge) els.modelPoolHeaderBadge.hidden = !pool.enabled;
    const participants = routingOrder().length;
    if (els.modelPoolParticipantCount) {
      els.modelPoolParticipantCount.textContent = pool.enabled
        ? `${formatNumber(participants)} 个参与项`
        : "仅使用激活配置";
    }
    if (els.modelPoolParticipantCountSheet) {
      els.modelPoolParticipantCountSheet.textContent = pool.enabled
        ? `${formatNumber(participants)} 个参与项`
        : "轮询已关闭，仅使用激活配置";
    }
    if (els.modelPoolRecoverAll) {
      els.modelPoolRecoverAll.hidden = !pool.chain.some(
        (member) => member.state === "tripped",
      );
    }
    if (els.modelStrategySummary) els.modelStrategySummary.textContent = "优先级故障转移";
    if (els.modelRecoverySummary) {
      const cooldowns = pool.chain.filter((member) => member.state === "tripped").length;
      els.modelRecoverySummary.textContent = cooldowns ? `${formatNumber(cooldowns)} 个冷却中` : "无冷却";
    }
    renderPoolChain();
  }

  function formValues() {
    return {
      name: els.modelSheetName.value.trim(),
      protocol: els.modelSheetProtocol.value,
      baseUrl: els.modelSheetBaseUrl.value.trim(),
      model: els.modelSheetModel.value.trim(),
      apiKey: els.modelSheetApiKey.value,
      proxy: els.modelSheetProxy?.value.trim() || "",
      sessionHeaderKey: els.modelSheetSessionHeader?.value.trim() || "",
      ratePerSecond: Number(els.modelSheetRateSecond?.value || 0),
      ratePerMinute: Number(els.modelSheetRateMinute?.value || 0),
      contextWindowK: Number(els.modelSheetContextWindow?.value || 0),
      priority: Number(els.modelSheetPriority?.value || 0),
      maxTokens: Number(els.modelSheetMaxTokens?.value || 0),
      maxTokensField:
        els.modelSheetProtocol.value === "openai-chat"
          ? els.modelSheetMaxTokensField?.value || ""
          : "",
      thinkingType: els.modelSheetThinkingType?.value || "",
      reasoningEffort: els.modelSheetReasoningEffort?.value || "",
      streaming: els.modelSheetStreaming?.checked !== false,
      poolExclude: Boolean(els.modelSheetPool.checked),
      retry: {
        connect: retryRuleFrom("connect"),
        empty: retryRuleFrom("empty"),
        stream: retryRuleFrom("stream"),
      },
    };
  }

  function retryRuleFrom(layer) {
    const attemptsEl = els[`modelSheetRetry${capitalize(layer)}Attempts`];
    const intervalEl = els[`modelSheetRetry${capitalize(layer)}Interval`];
    return {
      attempts: Number(attemptsEl?.value || 0),
      interval_ms: Number(intervalEl?.value || 0),
    };
  }

  function capitalize(value) {
    return value.charAt(0).toUpperCase() + value.slice(1);
  }

  function validate(values) {
    if (!values.name) return "请填写配置名称";
    if (!values.model) return "请填写模型名称";
    return "";
  }

  function resetProfileSheet(profile = null) {
    editingProfileId = profile?.id || "";
    els.modelProviderSheetTitle.textContent = profile ? `编辑：${profile.name || profile.id}` : "新建模型配置";
    els.modelProviderSheetDescription.textContent = profile
      ? "修改后点击保存；激活配置保存后对全部 Agent 立即生效。"
      : "新建后不会自动激活，请在卡片上「设为激活」。";
    if (els.modelSheetActiveBadge) els.modelSheetActiveBadge.hidden = !isActiveProfile(profile);
    const saveLabel = els.modelSheetSave.querySelector("span");
    if (saveLabel) saveLabel.textContent = profile ? "保存" : "新建";
    const saveIcon = els.modelSheetSave.querySelector("[data-lucide]");
    if (saveIcon) saveIcon.setAttribute("data-lucide", profile ? "save" : "plus");
    els.modelSheetName.value = profile?.name || "";
    els.modelSheetProtocol.value = profile?.protocol || "openai-chat";
    els.modelSheetBaseUrl.value = profile?.baseUrl || "";
    els.modelSheetModel.value = profile?.model || "";
    els.modelSheetApiKey.value = "";
    els.modelSheetApiKey.placeholder = profile?.apiKeyConfigured
      ? `当前 Key：${profile.apiKeyMasked || "已配置"}（留空保持）`
      : "sk-…";
    if (els.modelSheetProxy) els.modelSheetProxy.value = profile?.proxy || "";
    if (els.modelSheetSessionHeader) els.modelSheetSessionHeader.value = profile?.sessionHeaderKey || "";
    if (els.modelSheetRateSecond) els.modelSheetRateSecond.value = String(profile?.ratePerSecond || 0);
    if (els.modelSheetRateMinute) els.modelSheetRateMinute.value = String(profile?.ratePerMinute || 0);
    if (els.modelSheetContextWindow) els.modelSheetContextWindow.value = String(profile?.contextWindowK || 0);
    if (els.modelSheetPriority) els.modelSheetPriority.value = String(profile?.priority || 0);
    if (els.modelSheetMaxTokens) els.modelSheetMaxTokens.value = String(profile?.maxTokens || 0);
    if (els.modelSheetMaxTokensField) els.modelSheetMaxTokensField.value = profile?.maxTokensField || "";
    if (els.modelSheetThinkingType) els.modelSheetThinkingType.value = profile?.thinkingType || "";
    if (els.modelSheetReasoningEffort) els.modelSheetReasoningEffort.value = profile?.reasoningEffort || "";
    if (els.modelSheetStreaming) els.modelSheetStreaming.checked = profile?.streaming !== false;
    els.modelSheetPool.checked = Boolean(profile?.poolExclude);
    els.modelSheetPool.disabled = false;
    setRetryRule("connect", profile?.retry?.connect);
    setRetryRule("empty", profile?.retry?.empty);
    setRetryRule("stream", profile?.retry?.stream);
    els.modelSheetTestResult.className = "model-sheet-test-result";
    els.modelSheetTestResult.textContent = "";
    els.modelSheetModels.hidden = true;
    els.modelSheetModels.replaceChildren();
    refreshIcons();
  }

  function setRetryRule(layer, rule) {
    const attemptsEl = els[`modelSheetRetry${capitalize(layer)}Attempts`];
    const intervalEl = els[`modelSheetRetry${capitalize(layer)}Interval`];
    if (attemptsEl) attemptsEl.value = String(rule?.attempts || 0);
    if (intervalEl) intervalEl.value = String(rule?.interval_ms || 0);
  }

  function openProfileSheet(id = "") {
    const profile = id ? profileById(id) : null;
    resetProfileSheet(profile);
    if (els.modelProviderSheet.open) return;
    els.modelProviderSheet.showModal();
    window.requestAnimationFrame(() => els.modelSheetName.focus());
  }

  function closeProfileSheet() {
    if (els.modelProviderSheet.open) els.modelProviderSheet.close();
    editingProfileId = "";
  }

  async function saveProfile() {
    const values = formValues();
    const isEditing = Boolean(editingProfileId);
    const validation = validate(values);
    if (validation) {
      els.modelSheetTestResult.className = "model-sheet-test-result is-error";
      els.modelSheetTestResult.textContent = validation;
      return;
    }
    await withButton(els.modelSheetSave, async () => {
      try {
        const payload = { ...values };
        if (editingProfileId) payload.id = editingProfileId;
        if (!payload.apiKey) delete payload.apiKey;
        const result = await request("/api/llm/profiles", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        onConfigLoaded(result.config);
        await refresh({ silent: true });
        closeProfileSheet();
        setStatus(isEditing ? "模型配置已保存" : "模型配置已创建", "success");
        showToast(isEditing ? "模型配置已保存" : "模型配置已创建", "success");
      } catch (error) {
        els.modelSheetTestResult.className = "model-sheet-test-result is-error";
        els.modelSheetTestResult.textContent = `保存失败：${error.message}`;
      }
    });
  }

  async function deleteProfileFromCard(profile) {
    if (isActiveProfile(profile)) {
      setStatus("当前激活配置不能删除，请先把其它配置设为激活", "error");
      return;
    }
    try {
      await request(`/api/llm/profiles/${encodeURIComponent(profile.id)}`, {
        method: "DELETE",
      });
      await refresh({ silent: true });
      setStatus(`已删除：${profile.name || profile.id}`, "success");
      showToast("模型配置已删除", "success");
    } catch (error) {
      setStatus(`删除失败：${error.message}`, "error");
    }
  }

  async function fetchModels() {
    const values = formValues();
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
            providerId: editingProfileId || undefined,
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
      } catch (error) {
        els.modelSheetTestResult.className = "model-sheet-test-result is-error";
        els.modelSheetTestResult.textContent = `获取模型失败：${error.message}`;
      }
    });
  }

  async function testProvider() {
    const values = formValues();
    const validation = validate(values);
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
            providerId: editingProfileId || undefined,
            model: values.model,
            protocol: values.protocol,
            proxy: values.proxy,
            thinkingType: values.thinkingType,
            reasoningEffort: values.reasoningEffort,
            streaming: values.streaming,
            sessionHeaderKey: values.sessionHeaderKey,
          }),
        });
        els.modelSheetTestResult.className = "model-sheet-test-result is-success";
        els.modelSheetTestResult.textContent = `连接成功 · ${formatNumber(result.latencyMs)}ms · ${result.reply || "OK"}`;
      } catch (error) {
        els.modelSheetTestResult.className = "model-sheet-test-result is-error";
        els.modelSheetTestResult.textContent = `连接失败：${error.message}`;
      }
    });
  }

  async function activateProfile(profile) {
    if (!profile || isActiveProfile(profile)) return;
    try {
      const result = await request("/api/llm/profiles/active", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: profile.id }),
      });
      onConfigLoaded(result.config);
      await refresh({ silent: true });
      setStatus(`已激活：${profile.name || profile.id}`, "success");
      showToast(`已激活：${profile.name || profile.id}`, "success");
    } catch (error) {
      setStatus(`激活失败：${error.message}`, "error");
    }
  }

  async function togglePool(enabled, bindFallback) {
    try {
      const result = await request("/api/config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          modelPoolEnabled: enabled,
          modelPoolBindFallback: bindFallback,
        }),
      });
      onConfigLoaded(result);
      await refresh({ silent: true });
      showToast(enabled ? "已开启轮询" : "已关闭轮询", "success");
    } catch (error) {
      setSheetStatus(`保存失败：${error.message}`, "error");
    }
  }

  async function resetPool(id = "") {
    try {
      const result = await request("/api/llm/pool/reset", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id }),
      });
      pool = normalizePool(result);
      renderProfiles();
      showToast(id ? "已恢复该配置" : "已恢复全部配置", "success");
    } catch (error) {
      setSheetStatus(`恢复失败：${error.message}`, "error");
    }
  }

  function normalizePool(value) {
    if (!value || typeof value !== "object") return { enabled: false, bind_fallback: false, chain: [] };
    return {
      enabled: Boolean(value.enabled),
      bind_fallback: Boolean(value.bind_fallback),
      chain: Array.isArray(value.chain) ? value.chain : [],
    };
  }

  function retryRuleValues(layer) {
    const attemptsEl = els[`modelRetry${capitalize(layer)}Attempts`];
    const intervalEl = els[`modelRetry${capitalize(layer)}Interval`];
    return {
      attempts: Number(attemptsEl?.value || 0),
      interval_ms: Number(intervalEl?.value || 0),
    };
  }

  function fillRetryPolicy(policy) {
    for (const layer of ["connect", "empty", "stream", "breaker", "intent"]) {
      const rule = policy?.[layer] || {};
      const attemptsEl = els[`modelRetry${capitalize(layer)}Attempts`];
      const intervalEl = els[`modelRetry${capitalize(layer)}Interval`];
      if (attemptsEl) attemptsEl.value = String(rule.attempts || 0);
      if (intervalEl) intervalEl.value = String(rule.interval_ms || 0);
    }
  }

  async function saveRetryPolicy() {
    await withButton(els.saveModelRouting, async () => {
      try {
        const payload = {
          connect: retryRuleValues("connect"),
          empty: retryRuleValues("empty"),
          stream: retryRuleValues("stream"),
          breaker: retryRuleValues("breaker"),
          intent: retryRuleValues("intent"),
        };
        const saved = await request("/api/llm/retry-policy", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        retryPolicy = saved;
        fillRetryPolicy(saved);
        setStatus("重试与退避已保存，即时生效", "success");
        showToast("重试与退避已保存", "success");
      } catch (error) {
        setStatus(`保存失败：${error.message}`, "error");
      }
    });
  }

  function resetRetryPolicy() {
    const zero = { attempts: 0, interval_ms: 0 };
    for (const layer of ["connect", "empty", "stream", "breaker", "intent"]) {
      setRetryRule(layer, zero);
    }
  }

  async function refresh({ silent = false } = {}) {
    try {
      const [profileResult, poolResult, retryResult] = await Promise.all([
        request("/api/llm/profiles"),
        request("/api/llm/pool"),
        request("/api/llm/retry-policy"),
      ]);
      profiles = Array.isArray(profileResult.profiles) ? profileResult.profiles : [];
      pool = normalizePool(poolResult);
      retryPolicy = retryResult;
      fillRetryPolicy(retryPolicy);
      renderProfiles();
      if (!silent) setStatus(profiles.length ? "点击卡片编辑，星标为当前激活配置" : "还没有模型配置");
    } catch (error) {
      setStatus(`读取模型配置失败：${error.message}`, "error");
    }
  }

  function startStatusPolling() {
    stopStatusPolling();
    statusPollTimer = window.setInterval(() => {
      if (!els.modelRoutingPage.hidden) void refresh({ silent: true });
    }, 10000);
  }

  function stopStatusPolling() {
    if (statusPollTimer) window.clearInterval(statusPollTimer);
    statusPollTimer = null;
  }

  function load(config) {
    onConfigLoaded(config);
    setTab("providers");
    void refresh();
    startStatusPolling();
  }

  function bind() {
    els.addModelProvider.addEventListener("click", () => openProfileSheet());
    els.closeModelProviderSheet.addEventListener("click", closeProfileSheet);
    els.modelSheetSave.addEventListener("click", () => void saveProfile());
    els.modelSheetTest.addEventListener("click", () => void testProvider());
    els.modelSheetFetchModels.addEventListener("click", () => void fetchModels());
    els.modelProviderSheet.addEventListener("close", () => {
      editingProfileId = "";
    });
    els.modelSheetProtocol.addEventListener("change", () => {
      if (els.modelSheetMaxTokensField) {
        els.modelSheetMaxTokensField.disabled = els.modelSheetProtocol.value !== "openai-chat";
      }
    });
    els.openModelPool.addEventListener("click", () => {
      renderPoolChain();
      setSheetStatus("");
      els.modelPoolSheet.showModal();
    });
    els.closeModelPoolSheet.addEventListener("click", () => els.modelPoolSheet.close());
    els.modelPoolEnabledSheet.addEventListener("change", () => {
      void togglePool(els.modelPoolEnabledSheet.checked, els.modelPoolBindFallbackSheet.checked);
    });
    els.modelPoolBindFallbackSheet.addEventListener("change", () => {
      void togglePool(els.modelPoolEnabledSheet.checked, els.modelPoolBindFallbackSheet.checked);
    });
    els.modelPoolRecoverAll?.addEventListener("click", () => void resetPool(""));
    els.saveModelRouting.addEventListener("click", () => void saveRetryPolicy());
    els.resetModelRouting?.addEventListener("click", resetRetryPolicy);
    els.modelRoutingPage.querySelectorAll("[data-model-tab]").forEach((button) => {
      button.addEventListener("click", () => setTab(button.dataset.modelTab || "providers"));
    });
  }

  return {
    bind,
    load,
    save: saveRetryPolicy,
    setStatus,
    stopPolling: stopStatusPolling,
  };
}
