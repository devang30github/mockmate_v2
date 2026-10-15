// Runs on the audio thread. Resamples to 16 kHz mono, emits 100 ms Int16 chunks plus their RMS.
class PcmCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.phase = 0;
    this.acc = 0;
    this.n = 0;
    this.sumSq = 0;
    this.count = 0;
    this.out = new Int16Array(1600);
    this.o = 0;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;
    for (let i = 0; i < channel.length; i++) {
      this.acc += channel[i];
      this.n += 1;
      this.phase += 1;
      if (this.phase >= this.ratio) {
        this.phase -= this.ratio;
        const v = this.acc / this.n; // box filter: average the samples in each output step
        this.acc = 0;
        this.n = 0;
        const c = v > 1 ? 1 : v < -1 ? -1 : v;
        this.out[this.o++] = c < 0 ? c * 0x8000 : c * 0x7fff;
        this.sumSq += c * c;
        this.count += 1;
        if (this.o === this.out.length) {
          const buffer = this.out.buffer;
          this.port.postMessage({ pcm: buffer, rms: Math.sqrt(this.sumSq / this.count) }, [buffer]);
          this.out = new Int16Array(1600);
          this.o = 0;
          this.sumSq = 0;
          this.count = 0;
        }
      }
    }
    return true;
  }
}

registerProcessor("pcm-capture", PcmCapture);