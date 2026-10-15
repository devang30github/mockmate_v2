import { getMe } from "./auth.js";
import { getInterview } from "./data.js";
import { AudioCapture } from "./audio-capture.js";
import { AudioPlayer } from "./audio-player.js";
import { EnergyVad } from "./vad.js";
import { WsClient } from "./ws-client.js";
import { encodeWav } from "./wav.js";

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const interviewId = params.get("id");
const DEV = params.get("dev") === "1";
const PRE_ROLL_FRAMES = 8; // 800 ms: covers the onset frames plus a little lead-in
const FINISHED = new Set(["FINISHING", "REPORT_PENDING", "COMPLETED"]);
const JOINABLE = new Set(["ASKING", "LISTENING", "TRANSCRIBING", "PROCESSING", "PAUSED", "ERROR"]);
const RETRY_CODES = new Set(["tts_failed", "stale_state", "internal"]);

const FRIENDLY = {
  no_speech: "I didn't hear anything. Please try your answer again.",
  stt_failed: "Transcription failed. Please try your answer again.",
  answer_lost: "Your last answer didn't reach us. Please answer again.",
  tts_failed: "Couldn't play the question audio.",
};

const STATE_INFO = {
  READY: ["Ready", "Getting started…", "…"],
  ASKING: ["Interviewer speaking", "The interviewer is asking a question. Listen, then answer out loud.", "🔊"],
  LISTENING: ["Your turn", "Speak your answer. Pause when you're done, or press Done speaking.", "🎤"],
  TRANSCRIBING: ["Transcribing", "Transcribing your answer…", "⏳"],
  PROCESSING: ["Thinking", "Preparing the next question…", "⏳"],
  PAUSED: ["Paused", "Interview paused. Press Resume to hear the question again.", "⏸"],
  ERROR: ["Problem", "Something went wrong. Try reloading this page.", "!"],
};

let ws = null;
let capture = null;
let player = null;
let vad = null;
let ctx = null;
let state = null;
let stateVersion = -1;
let currentSeq = 0;
let utter = null; // { id, chunks } while an answer is being streamed
let submitting = false; // answer sent, waiting for the server to move on
let lastUtteranceChunks = null;
let calib = null; // array of RMS values while calibrating
let lostSpeech = false;
let doneShown = false;
let ttsQuestionId = null;
const ring = [];
const seen = new Set();

// ---- small UI helpers ---------------------------------------------------------
function showBanner(message, retry = false) {
  $("banner-text").textContent = message;
  $("retry").hidden = !retry;
  $("banner").hidden = false;
}

function hideBanner() {
  $("banner").hidden = true;
}

function setMicStatus(text) {
  $("mic-status").textContent = text;
}

function addLine(who, text) {
  const row = document.createElement("div");
  row.className = "line";
  const label = document.createElement("strong");
  label.textContent = `${who}: `;
  const body = document.createElement("span");
  body.textContent = text;
  row.append(label, body);
  const box = $("transcript");
  box.append(row);
  box.scrollTop = box.scrollHeight;
}

function setCaption(seq, text) {
  if (seq >= currentSeq) {
    currentSeq = seq;
    $("caption").textContent = text;
  }
}

function render() {
  const info = STATE_INFO[state];
  if (info) {
    $("state-badge").textContent = info[0];
    $("state-text").textContent = info[1];
    $("orb").dataset.state = state;
    $("orb").textContent = info[2];
  }
  $("recording").hidden = !utter;
  const answer = $("answer-btn");
  answer.disabled = state !== "LISTENING" || submitting;
  answer.textContent = utter ? "Done speaking" : "Start answering";
  const pause = $("pause-btn");
  pause.textContent = state === "PAUSED" ? "Resume" : "Pause";
  pause.disabled = !(state === "ASKING" || state === "LISTENING" || state === "PAUSED");
  $("end-btn").disabled = !state || FINISHED.has(state);
}

function showDone() {
  if (doneShown) return;
  doneShown = true;
  $("live-card").hidden = true;
  $("controls").hidden = true;
  $("done").hidden = false;
  $("gate").hidden = true;
  if (capture) capture.stop();
  if (ws) ws.close();
  if (ctx) ctx.close().catch(() => {});
}

function micErrorMessage(err) {
  switch (err && err.name) {
    case "NotAllowedError":
    case "SecurityError":
      return "Microphone access was blocked. Allow it in your browser's site settings, then try again.";
    case "NotFoundError":
    case "OverconstrainedError":
      return "No microphone was found. Plug one in and try again.";
    case "NotReadableError":
      return "Your microphone is being used by another app.";
    case "InsecureContext":
      return "This page needs a secure (https) connection to use the microphone.";
    default:
      return `Couldn't start the microphone (${(err && err.name) || "unknown error"}).`;
  }
}

// ---- state handling -------------------------------------------------------------
function applyState(next, version) {
  if (version <= stateVersion) return; // replayed or duplicate event
  stateVersion = version;
  state = next;
  submitting = false;
  if (next === "LISTENING") {
    ring.length = 0;
    vad.arm();
  } else {
    vad.disarm();
    utter = null;
  }
  if (next === "PAUSED" || next === "ERROR" || FINISHED.has(next)) player.stop();
  if (next === "ASKING" || next === "TRANSCRIBING") hideBanner();
  render();
  if (FINISHED.has(next)) showDone();
}

async function playQuestion() {
  const questionId = ttsQuestionId;
  const result = await player.finish();
  if (result === "ended") ws.sendJson("tts.done", { question_id: questionId });
  else if (result === "decode_error") showBanner("Couldn't play the question audio.", true);
}

function handleServerError(m) {
  const text = FRIENDLY[m.code] || m.message || "Something went wrong.";
  showBanner(text, Boolean(m.retryable) && RETRY_CODES.has(m.code));
}

async function onJson(m) {
  if (typeof m.event_id === "number") {
    if (seen.has(m.event_id)) return;
    seen.add(m.event_id);
  }
  switch (m.type) {
    case "server.hello":
      applyState(m.state, m.state_version);
      if (m.current_question) setCaption(m.current_question.seq, m.current_question.text);
      if (m.state === "READY") ws.sendJson("start");
      break;
    case "state":
      applyState(m.state, m.state_version);
      break;
    case "question.text":
      addLine("Interviewer", m.text);
      setCaption(m.seq, m.text);
      break;
    case "transcript.final":
      addLine("You", m.text);
      break;
    case "tts.start":
      ttsQuestionId = m.question_id;
      player.begin();
      break;
    case "tts.end":
      await playQuestion();
      break;
    case "error":
      handleServerError(m);
      break;
    default:
      break;
  }
}

// ---- microphone frames and the answer lifecycle -----------------------------------
function sendFrame(frame) {
  utter.chunks.push(frame.pcm);
  ws.sendBinary(frame.pcm.buffer);
}

function startUtterance() {
  utter = { id: crypto.randomUUID(), chunks: [] };
  ws.sendJson("utterance.start", { utterance_id: utter.id });
  for (const f of ring) sendFrame(f);
  hideBanner();
  render();
}

function endUtterance(reason) {
  if (!utter) return;
  ws.sendJson("utterance.end", { utterance_id: utter.id, reason });
  lastUtteranceChunks = utter.chunks;
  utter = null;
  submitting = true;
  vad.disarm();
  if (DEV) $("dev-download").disabled = false;
  render();
}

function onFrame(frame) {
  if (calib) {
    calib.push(frame.rms);
    return;
  }
  $("meter-fill").style.width = `${Math.min(100, Math.sqrt(frame.rms) * 200)}%`;
  if (DEV && vad) {
    $("dev-level").textContent = `level ${frame.rms.toFixed(3)} · threshold ${vad.threshold.toFixed(3)}`;
  }
  if (state !== "LISTENING" || !vad || !vad.armed) return;

  ring.push(frame);
  if (ring.length > PRE_ROLL_FRAMES) ring.shift();
  if (utter) sendFrame(frame);

  const event = vad.push(frame.rms);
  if (event === "start") startUtterance();
  else if (event === "end-vad") endUtterance("vad");
  else if (event === "end-max") endUtterance("max_length");
}

function calibrate(ms = 1200) {
  return new Promise((resolve) => {
    calib = [];
    setTimeout(() => {
      const levels = calib;
      calib = null;
      resolve(levels);
    }, ms);
  });
}

function median(values) {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)];
}

// ---- controls ----------------------------------------------------------------------
function onAnswerButton() {
  if (state !== "LISTENING" || submitting) return;
  if (!utter) {
    if (vad.forceStart()) startUtterance();
  } else {
    endUtterance("manual");
  }
}

function onPause() {
  if (state === "PAUSED") {
    ws.sendJson("unpause");
    return;
  }
  if (state === "ASKING" || state === "LISTENING") {
    player.stop();
    utter = null;
    vad.disarm();
    render();
    ws.sendJson("pause");
  }
}

function onEnd() {
  if (!window.confirm("End the interview now?")) return;
  player.stop();
  utter = null;
  vad.disarm();
  ws.sendJson("finish");
}

function onStatus(status) {
  const labels = {
    connecting: "Connecting…",
    connected: "Connected",
    reconnecting: "Reconnecting…",
    failed: "Disconnected",
    closed: "Disconnected",
  };
  $("conn").textContent = labels[status] || status;
  if (status === "reconnecting" || status === "failed") {
    if (utter) lostSpeech = true;
    utter = null;
    submitting = false;
    if (vad) vad.disarm();
    if (player) player.stop();
    render();
    if (status === "failed") {
      showBanner("Connection lost. Reload the page to continue where you left off.");
    }
  }
}

function onOpen() {
  stateVersion = -1; // accept the fresh server.hello even if the state is unchanged
  ws.sendJson("hello", { last_event_id: ws.lastEventId });
  if (lostSpeech) {
    lostSpeech = false;
    showBanner("We lost the connection while you were speaking. Please answer again.");
  }
}

// ---- start-up -------------------------------------------------------------------------
async function begin() {
  hideBanner();
  $("begin").disabled = true;
  try {
    ctx = new AudioContext({ latencyHint: "interactive" });
    await ctx.resume(); // allowed because this runs inside the button click
    capture = new AudioCapture(ctx, onFrame);
    await capture.start();
  } catch (err) {
    showBanner(micErrorMessage(err));
    if (ctx) ctx.close().catch(() => {});
    ctx = null;
    capture = null;
    $("begin").disabled = !$("consent").checked;
    return;
  }

  $("gate").hidden = true;
  $("room").hidden = false;
  if (DEV) $("dev").hidden = false;
  render();
  setMicStatus("Checking your microphone. Stay quiet for a second…");
  const levels = await calibrate();
  const ambient = median(levels);
  vad = new EnergyVad();
  vad.calibrate(ambient);
  player = new AudioPlayer(ctx);
  if (Math.max(0, ...levels) < 0.0005) {
    setMicStatus("Microphone is on, but we can't hear any sound. Check that it isn't muted.");
  } else if (ambient > 0.05) {
    setMicStatus("Microphone is on. It's noisy here; a quieter room or headphones will help.");
  } else {
    setMicStatus("Microphone is on.");
  }

  const scheme = location.protocol === "https:" ? "wss" : "ws";
  ws = new WsClient({
    url: `${scheme}://${location.host}/ws/interviews/${interviewId}`,
    onOpen,
    onJson,
    onBinary: (buffer) => player.push(buffer),
    onStatus,
    onFatal: (code) => {
      if (code === 4401) location.replace("/auth.html");
      else showBanner("This interview could not be found.");
    },
  });
  ws.connect();
}

async function main() {
  if (!interviewId) {
    location.replace("/dashboard.html");
    return;
  }
  if (!(await getMe())) {
    location.replace("/auth.html");
    return;
  }
  let interview;
  try {
    interview = await getInterview(interviewId);
  } catch (err) {
    showBanner(err.message);
    return;
  }
  $("title").textContent = interview.settings.role_title;
  $("meta").textContent =
    `${interview.settings.duration_minutes} min · ${interview.settings.difficulty} · ${interview.settings.interview_type}`;

  if (interview.state === "DRAFT") {
    $("draft-note").hidden = false;
    return;
  }
  if (FINISHED.has(interview.state)) {
    showDone();
    return;
  }
  if (interview.state !== "READY" && !JOINABLE.has(interview.state)) {
    showBanner(`This interview can't be joined right now (${interview.state}).`);
    return;
  }

  $("begin").textContent = interview.state === "READY" ? "Start interview" : "Continue interview";
  $("gate").hidden = false;
  $("consent").addEventListener("change", () => {
    $("begin").disabled = !$("consent").checked;
  });
  $("begin").addEventListener("click", begin);
  $("answer-btn").addEventListener("click", onAnswerButton);
  $("pause-btn").addEventListener("click", onPause);
  $("end-btn").addEventListener("click", onEnd);
  $("retry").addEventListener("click", () => {
    hideBanner();
    if (ws) ws.sendJson("hello", { last_event_id: ws.lastEventId });
  });
  $("dev-download").addEventListener("click", () => {
    if (!lastUtteranceChunks || !lastUtteranceChunks.length) return;
    const url = URL.createObjectURL(encodeWav(lastUtteranceChunks));
    const link = document.createElement("a");
    link.href = url;
    link.download = `answer-${Date.now()}.wav`;
    link.click();
    URL.revokeObjectURL(url);
  });
  window.addEventListener("pagehide", () => {
    if (ws) ws.close();
    if (capture) capture.stop();
  });
}

main();