/** Small, testable API client shared by the feature modules. */
export class RequestError extends Error {
  constructor(message, code = "REQUEST_ERROR", status = 0) {
    super(message);
    this.name = "RequestError";
    this.code = code;
    this.status = status;
  }
}

export function createApiClient({
  onConnection = () => {},
  onAuthExpired = () => {},
  onMatchContextExpired = () => {},
} = {}) {
  async function request(path, options = {}) {
    let response;
    try {
      response = await fetch(path, { credentials: "same-origin", ...options });
    } catch {
      onConnection("error");
      throw new RequestError("无法连接到本地服务", "NETWORK_ERROR");
    }

    let payload;
    try {
      payload = await response.json();
    } catch {
      onConnection("error");
      throw new RequestError("服务返回了无效响应", "INVALID_RESPONSE", response.status);
    }
    onConnection("connected");
    if (!response.ok || !payload.ok) {
      const fault = payload.error || {};
      if (fault.code === "AUTH_EXPIRED") onAuthExpired();
      if (fault.code === "MATCH_CONTEXT_MISSING") onMatchContextExpired();
      throw new RequestError(fault.message || "请求失败", fault.code || "REQUEST_FAILED", response.status);
    }
    return payload.data;
  }
  return { request };
}

export function withRefresh(path, refresh = false) {
  if (!refresh) return path;
  const url = new URL(path, window.location.origin);
  url.searchParams.set("refresh", "true");
  return `${url.pathname}${url.search}`;
}
