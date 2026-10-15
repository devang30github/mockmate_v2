// Energy-based voice activity detection. Pure logic: one push() per 100 ms chunk.
const DEFAULTS = {
  frameMs: 100,
  armDelayMs: 300, // ignore the first moments after the interviewer stops (speaker tail)
  onsetFrames: 2, // consecutive loud chunks that count as the start of speech
  endSilenceMs: 2000, // quiet after speech that ends the answer
  maxUtteranceMs: 120000,
  minThreshold: 0.015,
  noiseMultiplier: 3,
  initialFloor: 0.005,
};

export class EnergyVad {
  constructor(options = {}) {
    this.o = { ...DEFAULTS, ...options };
    this.floor = this.o.initialFloor;
    this.armed = false;
    this._reset();
  }

  _reset() {
    this.sinceArm = 0;
    this.started = false;
    this.run = 0;
    this.voiced = 0;
    this.silenceMs = 0;
    this.utterMs = 0;
  }

  calibrate(ambientRms) {
    if (Number.isFinite(ambientRms) && ambientRms >= 0) this.floor = ambientRms;
  }

  get threshold() {
    return Math.max(this.o.minThreshold, this.floor * this.o.noiseMultiplier);
  }

  arm() {
    this.armed = true;
    this._reset();
  }

  disarm() {
    this.armed = false;
  }

  // Manual "Start answering". Returns true if an answer should begin now.
  forceStart() {
    if (!this.armed || this.started) return false;
    this.started = true;
    this.utterMs = 0;
    this.silenceMs = 0;
    this.voiced = 0;
    return true;
  }

  // Returns null, "start", "end-vad" or "end-max".
  push(rms) {
    if (!this.armed) return null;
    const { frameMs, onsetFrames, endSilenceMs, maxUtteranceMs } = this.o;
    this.sinceArm += frameMs;
    if (this.sinceArm <= this.o.armDelayMs) return null;

    const loud = rms > this.threshold;
    if (!this.started) {
      if (loud) {
        this.run += 1;
      } else {
        this.run = 0;
        this.floor = this.floor * 0.98 + rms * 0.02; // follow quiet drift in the room
      }
      if (this.run >= onsetFrames) {
        this.started = true;
        this.voiced = this.run;
        this.utterMs = this.run * frameMs;
        this.silenceMs = 0;
        return "start";
      }
      return null;
    }

    this.utterMs += frameMs;
    if (loud) {
      this.voiced += 1;
      this.silenceMs = 0;
    } else {
      this.silenceMs += frameMs;
    }
    if (this.utterMs >= maxUtteranceMs) {
      this.armed = false;
      return "end-max";
    }
    // After a manual start, silence only ends the answer once speech has been heard.
    if (this.voiced >= onsetFrames && this.silenceMs >= endSilenceMs) {
      this.armed = false;
      return "end-vad";
    }
    return null;
  }
}