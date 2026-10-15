import test from "node:test";
import assert from "node:assert/strict";
import { EnergyVad } from "../js/vad.js";

const feed = (vad, rms, n) => {
  const events = [];
  for (let i = 0; i < n; i++) {
    const e = vad.push(rms);
    if (e) events.push(e);
  }
  return events;
};
const armed = (options) => {
  const vad = new EnergyVad(options);
  vad.arm();
  return vad;
};
const QUIET = 0.002;
const LOUD = 0.1;

test("ignores everything during the arm delay", () => {
  const vad = armed();
  assert.deepEqual(feed(vad, LOUD, 4), []); // 3 delayed frames plus 1 counted
});

test("starts after two consecutive loud frames", () => {
  const vad = armed();
  feed(vad, QUIET, 4);
  assert.deepEqual(feed(vad, LOUD, 2), ["start"]);
});

test("a single loud blip does not start", () => {
  const vad = armed();
  feed(vad, QUIET, 4);
  assert.deepEqual([...feed(vad, LOUD, 1), ...feed(vad, QUIET, 1), ...feed(vad, LOUD, 1)], []);
});

test("ends after two seconds of silence", () => {
  const vad = armed();
  feed(vad, QUIET, 4);
  feed(vad, LOUD, 2);
  assert.deepEqual(feed(vad, QUIET, 20), ["end-vad"]);
});

test("a short pause mid-answer does not end it", () => {
  const vad = armed();
  feed(vad, QUIET, 4);
  feed(vad, LOUD, 2);
  assert.deepEqual([...feed(vad, QUIET, 10), ...feed(vad, LOUD, 2), ...feed(vad, QUIET, 19)], []);
  assert.deepEqual(feed(vad, QUIET, 1), ["end-vad"]);
});

test("a maximum length ends the answer", () => {
  const vad = armed({ maxUtteranceMs: 1000 });
  feed(vad, QUIET, 4);
  feed(vad, LOUD, 2);
  assert.deepEqual(feed(vad, LOUD, 8), ["end-max"]);
});

test("a noisy room raises the threshold so steady noise never starts an answer", () => {
  const vad = new EnergyVad();
  vad.calibrate(0.02);
  vad.arm();
  assert.deepEqual(feed(vad, 0.02, 30), []);
  assert.deepEqual(feed(vad, 0.1, 2), ["start"]);
});

test("manual start does not end on silence until speech was heard", () => {
  const vad = armed();
  assert.equal(vad.forceStart(), true);
  assert.equal(vad.forceStart(), false);
  assert.deepEqual(feed(vad, QUIET, 30), []);
  feed(vad, LOUD, 2);
  assert.deepEqual(feed(vad, QUIET, 20), ["end-vad"]);
});

test("a disarmed detector reports nothing", () => {
  const vad = armed();
  vad.disarm();
  assert.deepEqual(feed(vad, LOUD, 10), []);
});