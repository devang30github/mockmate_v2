export class AudioCapture {
  constructor(ctx, onFrame) {
    this.ctx = ctx;
    this.onFrame = onFrame;
    this.stream = null;
    this.source = null;
    this.node = null;
    this.mute = null;
  }

  async start() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      throw Object.assign(new Error("insecure context"), { name: "InsecureContext" });
    }
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
    try {
      await this.ctx.audioWorklet.addModule("/js/pcm-worklet.js");
      this.source = this.ctx.createMediaStreamSource(this.stream);
      this.node = new AudioWorkletNode(this.ctx, "pcm-capture", {
        numberOfInputs: 1,
        numberOfOutputs: 1,
        outputChannelCount: [1],
      });
      this.node.port.onmessage = (e) =>
        this.onFrame({ pcm: new Int16Array(e.data.pcm), rms: e.data.rms });
      // Some browsers only run a node that reaches the destination, so route it through a muted gain.
      this.mute = this.ctx.createGain();
      this.mute.gain.value = 0;
      this.source.connect(this.node);
      this.node.connect(this.mute);
      this.mute.connect(this.ctx.destination);
    } catch (err) {
      this.stop();
      throw err;
    }
  }

  stop() {
    try { this.source && this.source.disconnect(); } catch { /* already disconnected */ }
    try { this.node && this.node.disconnect(); } catch { /* already disconnected */ }
    try { this.mute && this.mute.disconnect(); } catch { /* already disconnected */ }
    if (this.stream) this.stream.getTracks().forEach((t) => t.stop());
    this.stream = this.source = this.node = this.mute = null;
  }
}