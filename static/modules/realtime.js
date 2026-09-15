const EVENT_TYPES = Object.freeze([
  "stream.ready",
  "stream.reset",
  "stream.heartbeat",
  "stream.unavailable",
  "codex.task",
  "codex.event",
  "codex.deleted",
  "attachment.task",
  "attachment.probe",
  "tool.install",
]);

export const REALTIME_STATUS = Object.freeze({
  CONNECTING: "connecting",
  LIVE: "live",
  RECONNECTING: "reconnecting",
  POLLING: "polling",
  OFFLINE: "offline",
  STOPPED: "stopped",
});

export class RealtimeClient {
  constructor({
    url = "/api/events",
    onEvent = () => {},
    onReset = () => {},
    onStatus = () => {},
    onFallbackTick = () => {},
    fallbackIntervalMs = 10000,
    failureThreshold = 3,
    hiddenCloseDelayMs = 30000,
  } = {}) {
    this.url = url;
    this.onEvent = onEvent;
    this.onReset = onReset;
    this.onStatus = onStatus;
    this.onFallbackTick = onFallbackTick;
    this.fallbackIntervalMs = Math.max(5000, fallbackIntervalMs);
    this.failureThreshold = Math.max(1, failureThreshold);
    this.hiddenCloseDelayMs = Math.max(1000, hiddenCloseDelayMs);
    this.source = null;
    this.status = REALTIME_STATUS.STOPPED;
    this.lastEventId = 0;
    this.failures = 0;
    this.reconnectTimer = null;
    this.fallbackTimer = null;
    this.hiddenTimer = null;
    this.stopped = true;
    this._onOnline = () => this._handleOnline();
    this._onOffline = () => this._handleOffline();
    this._onVisibility = () => this._handleVisibility();
  }

  start() {
    if (!this.stopped) return;
    this.stopped = false;
    window.addEventListener("online", this._onOnline);
    window.addEventListener("offline", this._onOffline);
    document.addEventListener("visibilitychange", this._onVisibility);
    if (navigator.onLine === false) {
      this._setStatus(REALTIME_STATUS.OFFLINE);
      this._startFallback();
      return;
    }
    this._connect();
  }

  stop() {
    this.stopped = true;
    this._closeSource();
    this._clearTimer("reconnectTimer");
    this._clearTimer("fallbackTimer", true);
    this._clearTimer("hiddenTimer");
    window.removeEventListener("online", this._onOnline);
    window.removeEventListener("offline", this._onOffline);
    document.removeEventListener("visibilitychange", this._onVisibility);
    this._setStatus(REALTIME_STATUS.STOPPED);
  }

  retryNow() {
    if (this.stopped) return;
    this.failures = 0;
    this._clearTimer("reconnectTimer");
    this._connect();
  }

  _connect() {
    if (this.stopped || navigator.onLine === false) return;
    this._closeSource();
    this._setStatus(this.failures ? REALTIME_STATUS.RECONNECTING : REALTIME_STATUS.CONNECTING);
    const target = new URL(this.url, window.location.href);
    if (this.lastEventId > 0) target.searchParams.set("lastEventId", String(this.lastEventId));
    const source = new EventSource(`${target.pathname}${target.search}`);
    this.source = source;
    for (const eventType of EVENT_TYPES) {
      source.addEventListener(eventType, (event) => this._handleEvent(eventType, event));
    }
    source.onerror = () => this._handleError(source);
  }

  _handleEvent(eventType, message) {
    let event;
    try {
      event = JSON.parse(message.data);
    } catch (error) {
      console.error("[SSE] 无法解析事件", eventType, error);
      return;
    }
    const eventId = Number(event?.id || message.lastEventId || 0);
    if (eventId > 0 && eventId <= this.lastEventId) return;
    if (eventId > 0) this.lastEventId = eventId;
    this.failures = 0;
    if (eventType === "stream.ready" || eventType === "stream.heartbeat") {
      this._setStatus(REALTIME_STATUS.LIVE);
      this._stopFallback();
      return;
    }
    if (eventType === "stream.reset") {
      this._setStatus(REALTIME_STATUS.LIVE);
      this._stopFallback();
      Promise.resolve(this.onReset(event)).catch((error) => console.error("[SSE reset]", error));
      return;
    }
    if (eventType === "stream.unavailable") {
      this.failures = this.failureThreshold;
      this._startFallback();
      return;
    }
    Promise.resolve(this.onEvent(event)).catch((error) => console.error(`[SSE ${eventType}]`, error));
  }

  _handleError(source) {
    if (this.stopped || source !== this.source) return;
    this._closeSource();
    this.failures += 1;
    if (navigator.onLine === false) {
      this._setStatus(REALTIME_STATUS.OFFLINE);
      this._startFallback();
      return;
    }
    if (this.failures >= this.failureThreshold) this._startFallback();
    else this._setStatus(REALTIME_STATUS.RECONNECTING);
    const delay = Math.min(30000, 1000 * (2 ** Math.min(this.failures, 5)));
    this._clearTimer("reconnectTimer");
    this.reconnectTimer = window.setTimeout(() => this._connect(), delay);
  }

  _startFallback() {
    this._setStatus(navigator.onLine === false ? REALTIME_STATUS.OFFLINE : REALTIME_STATUS.POLLING);
    if (this.fallbackTimer) return;
    const tick = () => Promise.resolve(this.onFallbackTick()).catch((error) => console.error("[轮询降级]", error));
    tick();
    this.fallbackTimer = window.setInterval(tick, this.fallbackIntervalMs);
  }

  _stopFallback() {
    this._clearTimer("fallbackTimer", true);
  }

  _handleOnline() {
    if (this.stopped) return;
    this._setStatus(REALTIME_STATUS.RECONNECTING);
    this.retryNow();
  }

  _handleOffline() {
    if (this.stopped) return;
    this._closeSource();
    this._clearTimer("reconnectTimer");
    this._setStatus(REALTIME_STATUS.OFFLINE);
    this._startFallback();
  }

  _handleVisibility() {
    this._clearTimer("hiddenTimer");
    if (!document.hidden) {
      this.retryNow();
      return;
    }
    this.hiddenTimer = window.setTimeout(() => {
      if (document.hidden && !this.stopped) {
        this._closeSource();
        this._setStatus(REALTIME_STATUS.RECONNECTING);
      }
    }, this.hiddenCloseDelayMs);
  }

  _setStatus(status) {
    if (this.status === status) return;
    this.status = status;
    this.onStatus(status);
  }

  _closeSource() {
    if (!this.source) return;
    this.source.onerror = null;
    this.source.close();
    this.source = null;
  }

  _clearTimer(name, interval = false) {
    if (!this[name]) return;
    if (interval) window.clearInterval(this[name]);
    else window.clearTimeout(this[name]);
    this[name] = null;
  }
}
