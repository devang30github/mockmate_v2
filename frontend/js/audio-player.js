// Collects the binary audio frames the server sends for one question, then plays them.
export class AudioPlayer {
  constructor(ctx) {
    this.ctx = ctx;
    this.chunks = [];
    this.receiving = false;
    this.source = null;
    this.token = 0;
  }

  begin() {
    this.stop();
    this.receiving = true;
  }

  push(buffer) {
    if (this.receiving) this.chunks.push(new Uint8Array(buffer));
  }

  // Resolves "ended" after playback finishes, "stopped" if cut off, "decode_error" if unplayable.
  async finish() {
    const token = this.token;
    this.receiving = false;
    const size = this.chunks.reduce((n, c) => n + c.length, 0);
    if (size === 0) return "decode_error";
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const c of this.chunks) {
      bytes.set(c, offset);
      offset += c.length;
    }
    this.chunks = [];
    let audio;
    try {
      audio = await this.ctx.decodeAudioData(bytes.buffer);
    } catch {
      return "decode_error";
    }
    if (token !== this.token) return "stopped";
    return new Promise((resolve) => {
      const src = this.ctx.createBufferSource();
      src.buffer = audio;
      src.connect(this.ctx.destination);
      src.onended = () => {
        if (this.source === src) this.source = null;
        resolve(token === this.token ? "ended" : "stopped");
      };
      this.source = src;
      src.start();
    });
  }

  stop() {
    this.token += 1;
    this.receiving = false;
    this.chunks = [];
    const src = this.source;
    this.source = null;
    if (src) {
      try { src.stop(); } catch { /* already stopped */ }
    }
  }
}