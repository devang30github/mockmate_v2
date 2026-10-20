# MockMate voice WebSocket protocol, version 1

Endpoint: `/ws/interviews/{interview_id}` (added in the next batch). Authentication uses the
session cookie. Control messages are JSON text frames. Audio is binary frames.

## Client to server
| type | fields | meaning |
|---|---|---|
| hello | last_event_id (int, default 0) | first message; the server replays events after this id |
| utterance.start | utterance_id (uuid) | the candidate began speaking |
| (binary frames) | 16 kHz mono 16-bit little-endian PCM | audio between start and end |
| utterance.end | utterance_id, reason: vad / manual / max_length | the answer is complete |
| tts.done | question_id (uuid) | the question audio finished playing; the server opens the listening state |
| start | | begin the interview (READY to ASKING); sent from the Start button so the browser allows audio |
| pause / unpause | | only while a question plays or while listening |
| finish | | end the interview and start the report |
| ping | | keepalive; the server replies with pong |
| utterance.pause | utterance_id (uuid) | a pause inside an answer; the server may transcribe the phrase so far |

Every client message carries `"v": 1`. Unknown fields, versions or types are rejected with an
`error` message. Control messages over 4096 bytes are rejected.

## Server to client
| type | fields | event_id |
|---|---|---|
| server.hello | session info | no |
| state | state, state_version | yes |
| question.text | question_id, seq, text | yes |
| tts.start / (binary) / tts.end | question_id, mime | no |
| transcript.final | answer_id, utterance_id, seq, text | yes |
| answer.ack | utterance_id, duplicate | no |
| error | code, message, retryable | no |
| pong | | no |
| transcript.partial | utterance_id, committed, live | live captions while speaking; `committed` is stable, `live` may change. No event_id, not replayed |

`event_id` values increase per interview. A reconnecting client sends
`hello {last_event_id}` and gets every later persisted event again, in order. Audio is not
replayed from storage; the server regenerates the current question's audio if needed.

## Ordering and idempotency
- The server handles one client message at a time per connection, in arrival order.
- `utterance_id` is the idempotency key. A repeat is acknowledged with `duplicate: true` and
  is not processed again.
- Audio frames outside the listening state are dropped and answered with an `error`.

## Resync and recovery
- `hello` may be sent at any time. The server replays persisted events after `last_event_id`,
  then, if a question is being asked, plays that question's audio again.
- If the connection dropped mid-transcription, the server returns to listening and sends
  `error {code: answer_lost}`: the candidate should answer again.
- If it dropped between an answer and the next question, the server finishes generating the
  next question on reconnect.

## Error codes
unauthorized (close 4401), not_found (close 4404), bad_message, too_large, invalid_state,
stale_state, stale_question, not_listening, no_utterance, no_speech, stt_failed, tts_failed,
answer_lost, internal.