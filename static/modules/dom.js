export class MissingElementError extends Error {
  constructor(moduleName, elementIds) {
    const ids = [...new Set(elementIds)].filter(Boolean);
    super(`模块“${moduleName}”缺少 DOM 元素：${ids.map((id) => `#${id}`).join("、")}`);
    this.name = "MissingElementError";
    this.moduleName = moduleName;
    this.elementIds = ids;
  }
}

export function requireElements(moduleName, elements) {
  const missing = Object.entries(elements)
    .filter(([, element]) => !element)
    .map(([id]) => id);
  if (missing.length) throw new MissingElementError(moduleName, missing);
  return elements;
}

export function createFaultCenter({ host, retryButton, notify = () => {} } = {}) {
  const faults = new Map();

  function render() {
    if (!host) return;
    const list = host.querySelector("[data-fault-list]");
    const summary = host.querySelector("[data-fault-summary]");
    if (!list || !summary) return;
    list.replaceChildren();
    for (const fault of faults.values()) {
      const item = document.createElement("li");
      const title = document.createElement("strong");
      const detail = document.createElement("span");
      title.textContent = fault.module;
      detail.textContent = fault.message;
      item.append(title, detail);
      list.append(item);
    }
    summary.textContent = `${faults.size} 个模块初始化失败，其余功能仍可继续使用。`;
    host.hidden = faults.size === 0;
  }

  function report(moduleName, error, { toast = true } = {}) {
    const normalized = error instanceof Error ? error : new Error(String(error));
    const module = normalized.moduleName || moduleName || "未知模块";
    const elementHint = Array.isArray(normalized.elementIds) && normalized.elementIds.length
      ? `（${normalized.elementIds.map((id) => `#${id}`).join("、")}）`
      : "";
    const message = `${elementHint}${normalized.message || "未知错误"}`;
    faults.set(module, { module, message, error: normalized });
    console.error(`[初始化失败] ${module}:`, normalized);
    render();
    if (toast) notify(`${module}不可用：${message}`, "error");
  }

  function clear(moduleName) {
    faults.delete(moduleName);
    render();
  }

  function clearAll() {
    faults.clear();
    render();
  }

  if (retryButton) retryButton.addEventListener("click", () => {
    document.dispatchEvent(new CustomEvent("app:retry-init"));
  });

  render();
  return { report, clear, clearAll, entries: () => [...faults.values()] };
}

export async function safeInitModule(faultCenter, moduleName, initializer) {
  try {
    await initializer();
    faultCenter?.clear(moduleName);
    return true;
  } catch (error) {
    faultCenter?.report(moduleName, error);
    return false;
  }
}

export function installGlobalErrorBoundary(faultCenter, notify = () => {}) {
  window.addEventListener("error", (event) => {
    const error = event.error instanceof Error
      ? event.error
      : new Error(event.message || "未捕获的脚本异常");
    console.error("[window.error]", error);
    faultCenter?.report("页面运行时", error, { toast: false });
    notify(`页面发生异常：${error.message}`, "error");
  });
  window.addEventListener("unhandledrejection", (event) => {
    const error = event.reason instanceof Error
      ? event.reason
      : new Error(String(event.reason || "未处理的 Promise 异常"));
    console.error("[unhandledrejection]", error);
    faultCenter?.report("异步任务", error, { toast: false });
    notify(`异步操作异常：${error.message}`, "error");
  });
}
