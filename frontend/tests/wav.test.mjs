import test from "node:test";
import assert from "node:assert/strict";
import { encodeWav } from "../js/wav.js";

test("encodes a valid 16-bit mono WAV header and samples", async () => {
  const blob = encodeWav([Int16Array.of(1, -1), Int16Array.of(32767)], 16000);
  const view = new DataView(await blob.arrayBuffer());
  const tag = (o) => String.fromCharCode(...[0, 1, 2, 3].map((i) => view.getUint8(o + i)));
  assert.equal(view.byteLength, 44 + 6);
  assert.equal(tag(0), "RIFF");
  assert.equal(tag(8), "WAVE");
  assert.equal(view.getUint32(24, true), 16000);
  assert.equal(view.getUint32(40, true), 6);
  assert.equal(view.getInt16(44, true), 1);
  assert.equal(view.getInt16(46, true), -1);
  assert.equal(view.getInt16(48, true), 32767);
});