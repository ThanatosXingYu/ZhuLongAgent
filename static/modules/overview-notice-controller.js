export function createOverviewNoticeController({
  state,
  els,
  request,
  apiPath,
  createElement,
  icon,
  formatNumber,
  formatDate,
  renderRichText,
  safeURL,
  refreshIcons,
  showToast,
  showLoadError,
}) {
  function updateOverview(data) {
    const previousRank = Number(state.overview?.stageRank);
    const nextRank = Number(data.stageRank);
    const hasRankDelta = Number.isFinite(previousRank) && previousRank > 0 && Number.isFinite(nextRank) && nextRank > 0;
    const rankDelta = hasRankDelta ? previousRank - nextRank : null;
    state.overview = data;
    els.score.textContent = formatNumber(data.stagePoint);
    els.rank.textContent = formatNumber(data.stageRank);
    const updatedText = `更新于 ${new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date())}`;
    els.overviewUpdated.textContent = updatedText;
    els.overviewScore.textContent = formatNumber(data.stagePoint);
    els.overviewRank.textContent = formatNumber(data.stageRank);
    els.overviewSolved.textContent = formatNumber(data.solvedCount);
    els.overviewLastUpdated.textContent = updatedText.replace(/^更新于 /, "");
    if (data.teamName) {
      els.overviewTeamName.textContent = data.teamName;
      els.teamSummary.textContent = data.teamName;
      els.teamSummaryBlock.hidden = false;
      els.teamAvatar.textContent = String(data.teamName).trim().slice(0, 1).toUpperCase();
    }
    if (data.organization) {
      els.overviewTeamSummary.textContent = data.organization;
      els.teamSummaryMeta.textContent = data.organization;
    }
    [els.rankDelta, els.overviewRankDelta].forEach((node) => {
      if (!node) return;
      node.hidden = rankDelta === null;
      node.className = "rank-delta";
      if (rankDelta === null) return;
      if (rankDelta > 0) {
        node.textContent = `↑ ${formatNumber(rankDelta)}`;
        node.classList.add("is-up");
        node.title = `较上次刷新上升 ${formatNumber(rankDelta)} 名`;
      } else if (rankDelta < 0) {
        node.textContent = `↓ ${formatNumber(Math.abs(rankDelta))}`;
        node.classList.add("is-down");
        node.title = `较上次刷新下降 ${formatNumber(Math.abs(rankDelta))} 名`;
      } else {
        node.textContent = "持平";
        node.classList.add("is-flat");
        node.title = "排名与上次刷新相同";
      }
    });
    if (nextRank > 0) {
      state.overviewRankHistory.unshift({
        at: Date.now(),
        rank: nextRank,
        score: Number(data.stagePoint || 0),
        delta: rankDelta,
      });
      state.overviewRankHistory = state.overviewRankHistory.slice(0, 8);
      renderRankHistory();
    }
  }

  function renderRankHistory() {
    const history = state.overviewRankHistory;
    els.overviewRankHistoryCount.textContent = formatNumber(history.length);
    if (!history.length) {
      els.overviewRankHistory.replaceChildren(createElement("div", "empty-state compact", "等待第一次排名刷新"));
      return;
    }
    const fragment = document.createDocumentFragment();
    history.forEach((item, index) => {
      const row = createElement("div", `overview-rank-event${index === 0 ? " is-latest" : ""}`);
      const marker = createElement("span", "overview-rank-event-marker");
      marker.append(icon(index === 0 ? "activity" : "history"));
      const copy = createElement("span", "overview-rank-event-copy");
      copy.append(
        createElement("strong", "", `排名 #${formatNumber(item.rank)}`),
        createElement("small", "", new Intl.DateTimeFormat("zh-CN", {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
        }).format(new Date(item.at))),
      );
      const delta = createElement("span", "overview-rank-event-delta");
      if (item.delta === null) {
        delta.textContent = "基准";
        delta.classList.add("is-flat");
      } else if (item.delta > 0) {
        delta.textContent = `↑ ${formatNumber(item.delta)}`;
        delta.classList.add("is-up");
      } else if (item.delta < 0) {
        delta.textContent = `↓ ${formatNumber(Math.abs(item.delta))}`;
        delta.classList.add("is-down");
      } else {
        delta.textContent = "持平";
        delta.classList.add("is-flat");
      }
      row.append(marker, copy, delta);
      fragment.append(row);
    });
    els.overviewRankHistory.replaceChildren(fragment);
    refreshIcons();
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

  function renderFacts(container, text, emptyText) {
    if (!container) return;
    const lines = String(text || "").split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
    if (!lines.length) {
      container.replaceChildren(createElement("p", "overview-fact-empty", emptyText));
      return;
    }
    const fragment = document.createDocumentFragment();
    lines.forEach((line) => {
      const match = line.match(/^([^：:]{1,12})[：:]\s*(.+)$/);
      if (match) {
        const row = createElement("div", "overview-fact-row");
        row.append(createElement("span", "", match[1]), createElement("strong", "", match[2]));
        fragment.append(row);
      } else {
        fragment.append(createElement("strong", "overview-fact-title", line));
      }
    });
    container.replaceChildren(fragment);
  }

  async function loadMatchInfo(options = {}) {
    try {
      state.matchInfo = await request(apiPath("/api/match-info", options.refresh));
      renderFacts(els.overviewMatchNote, state.matchInfo.note, "暂无比赛说明");
      renderFacts(els.overviewMatchRule, state.matchInfo.rule, "暂无比赛规则");
      return state.matchInfo;
    } catch (error) {
      renderFacts(els.overviewMatchNote, `比赛说明加载失败：${error.message}`, "比赛说明加载失败");
      renderFacts(els.overviewMatchRule, "请确认比赛已绑定且登录态有效", "比赛规则加载失败");
      if (!options.silent) showToast(`比赛说明加载失败：${error.message}`, "error");
      return null;
    }
  }

  function noticeTime(notice) {
    return notice.createdTime ? formatDate(notice.createdTime) : notice.createdAt || "时间未知";
  }

  function noticesShareRecordedTime() {
    const values = new Set(
      state.notices
        .map((notice) => Number(notice.createdTime || 0))
        .filter((value) => value > 0),
    );
    return values.size === 1;
  }

  async function loadNotices(options = {}) {
    const refreshDetail = options.refreshDetail !== false;
    try {
      const notices = await request(apiPath("/api/notices", options.refresh));
      state.notices = Array.isArray(notices)
        ? [...notices].sort((a, b) => (b.createdTime || 0) - (a.createdTime || 0))
        : [];
      const ids = new Set(state.notices.map((notice) => notice.id));
      if (state.selectedNoticeId === null || !ids.has(state.selectedNoticeId)) {
        state.selectedNoticeId = state.notices[0]?.id ?? null;
      }
      if (!ids.has(state.selectedNoticeId)) state.noticeDetail = null;
      renderNotices();
      if (refreshDetail && state.selectedNoticeId !== null) {
        await loadNoticeDetail(state.selectedNoticeId, {
          silent: true,
          refresh: Boolean(options.refresh),
        });
      }
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
      renderNoticeDetail();
      return;
    }
    const sharedRecordedTime = noticesShareRecordedTime();
    const fragment = document.createDocumentFragment();
    state.notices.forEach((notice) => {
      const button = createElement("button", `notice-item${state.selectedNoticeId === notice.id ? " is-selected" : ""}`);
      button.type = "button";
      button.setAttribute("aria-current", state.selectedNoticeId === notice.id ? "true" : "false");
      button.append(icon(notice.isFile ? "file-text" : "megaphone"));
      const main = createElement("span", "notice-item-main");
      const metaText = sharedRecordedTime
        ? notice.userName || "系统公告"
        : `${notice.userName || "系统公告"} · ${noticeTime(notice)}`;
      main.append(
        createElement("span", "notice-item-title", notice.title || "未命名公告"),
        createElement("span", "notice-item-meta", metaText),
      );
      button.append(main);
      button.addEventListener("click", () => void selectNotice(notice.id));
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
      els.noticeReaderSource.textContent = "--";
      return;
    }
    const header = createElement("h3", "", detail.title || "未命名公告");
    els.noticeReaderSource.textContent = "平台记录时间";
    const meta = createElement(
      "p",
      "notice-meta",
      detail.createdTime ? `平台记录时间：${formatDate(detail.createdTime)}` : "平台未提供发布时间",
    );
    const content = renderRichText(detail.content || "暂无正文", "notice-content rich-text");
    const fragment = document.createDocumentFragment();
    fragment.append(header, meta, content);
    const files = [...((detail.file && detail.file.files) || [])];
    if (detail.url && !files.some((file) => file.url === detail.url)) {
      files.push({ name: "公告附件", url: detail.url });
    }
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

  return {
    loadOverview,
    loadMatchInfo,
    loadNotices,
  };
}
