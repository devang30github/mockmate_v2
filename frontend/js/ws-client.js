const MAX_ATTEMPTS = 10;
const FATAL_CODES = new Set([4401, 4404]); // unauthorized, not found: retrying can't help

export class WsClient {
  constructor({ url, onOpen, onJson, onBinary, onStatus, onFatal }) {
    Object.assign(this, { url, onOpen, onJson, onBinary, onStatus, onFatal });
    this.lastEventId = 0;
    this.attempts = 0;
    this.ws = null;
    this.closedByUs = false;
    this.pingTimer = null;
    this.retryTimer = null;
  }

  get isOpen() {
    return this.ws !== null && this.ws.readyState === 1;
  }

  connect() {
    this.closedByUs = false;
    this.onStatus(this.attempts === 0 ? "connecting" : "reconnecting");
    const ws = new WebSocket(this.url);
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    ws.onopen = () => {
      this.attempts = 0;
      this.onStatus("connected");
      this._startPing();
      this.onOpen();
    };
    ws.onmessage = (e) => {
      if (typeof e.data === "string") {
        let m;
        try { m = JSON.parse(e.data); } catch { return; }
        if (typeof m.event_id === "number" && m.event_id > this.lastEventId) {
          this.lastEventId = m.event_id;
        }
        this.onJson(m);
      } else {
        this.onBinary(e.data);
      }
    };
    ws.onclose = (e) => {
      this._stopPing();
      if (this.ws !== ws) return;
      this.ws = null;
      if (this.closedByUs) {
        this.onStatus("closed");
        return;
      }
      if (FATAL_CODES.has(e.code)) {
        this.onStatus("closed");
        this.onFatal(e.code);
        return;
      }
      this._scheduleRetry();
    };
    ws.onerror = () => { /* onclose follows and handles it */ };
  }

  _scheduleRetry() {
    if (this.attempts >= MAX_ATTEMPTS) {
      this.onStatus("failed");
      return;
    }
    this.attempts += 1;
    this.onStatus("reconnecting");
    const delay = Math.min(8000, 500 * 2 ** (this.attempts - 1));
    this.retryTimer = setTimeout(() => this.connect(), delay);
  }

  _startPing() {
    this._stopPing();
    this.pingTimer = setInterval(() => this.sendJson("ping"), 20000);
  }

  _stopPing() {
    if (this.pingTimer) clearInterval(this.pingTimer);
    this.pingTimer = null;
  }

  sendJson(type, fields = {}) {
    if (!this.isOpen) return false;
    this.ws.send(JSON.stringify({ v: 1, type, ...fields }));
    return true;
  }

  sendBinary(buffer) {
    if (!this.isOpen) return false;
    this.ws.send(buffer);
    return true;
  }

  close() {
    this.closedByUs = true;
    this._stopPing();
    if (this.retryTimer) clearTimeout(this.retryTimer);
    if (this.ws) this.ws.close();
  }
}