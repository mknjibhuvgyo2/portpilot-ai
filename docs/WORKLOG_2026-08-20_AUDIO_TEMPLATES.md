# Worklog 2026-08-20: Audio templates (ASR / TTS)

## What

Two new app templates give a port a speech endpoint instead of a chat endpoint:
`asr` transcribes uploaded audio, `tts` synthesizes speech. Both speak the
OpenAI audio API, so any stock OpenAI client works against them unchanged, and
any OpenAI-compatible speech server (GPUStack/vox-box, faster-whisper-server,
vLLM) can sit behind them as a provider.

This is the first template family whose request is **not** JSON in / JSON out:
ASR takes multipart form data, and TTS returns raw binary audio.

## Changes

### Model layer

- `models_layer/providers/base.py` — `transcribe()` and `speak()` alongside
  `embed()`/`rerank()`, defaulting to `NotImplementedError` so a text-only
  provider declines audio in the way the router already knows how to skip.
  `speak()` returns `(bytes, content_type)`: the payload is binary, so the
  caller cannot otherwise tell whether it got mp3 or wav.
- `models_layer/providers/openai_compat.py` — both implemented against
  `/v1/audio/transcriptions` and `/v1/audio/speech`, plus `_audio_mime()`.
  Two details that are easy to get wrong:
  - `_headers_multipart()` drops `Content-Type` for the upload. httpx has to
    set that header itself so it can append the multipart boundary; leaving the
    hardcoded `application/json` in place makes the server parse the body as
    JSON and reject the file.
  - `response_format=text|srt|vtt` comes back as plain text, not JSON. Calling
    `.json()` on it turns an otherwise successful request into a hard failure,
    so the content type decides how the response is read.
- `models_layer/router.py` — `transcribe()` / `speak()` reuse the same
  target-ordering, retry and skip-unsupported fallback chain as `embed()`.

### Templates

- `apps/asr.py` — `POST /v1/audio/transcriptions` (multipart). `model` is
  accepted for OpenAI-client compatibility and then ignored, same as
  embedding/rerank: the port is bound to one alias. Uploads are capped at 32MB.
- `apps/tts.py` — `POST /v1/audio/speech`. Returns a raw `Response` carrying the
  upstream content type; handing bytes back as a dict would have FastAPI
  JSON-encode and corrupt them. Input capped at 4000 characters, because
  synthesis cost scales with length and a runaway prompt occupies the backend
  for minutes.
- Both read per-port defaults from `extra.audio` — `language` for ASR,
  `voice`/`response_format`/`speed` for TTS — with request fields overriding.
  Pinning these on the port matters: left to auto-detect, a short noisy turn
  gets transcribed into the wrong language entirely, and an unpinned voice makes
  one persona sound like a different person on every call.
- `apps/registry.py` — both registered under a new `audio` category.

### Frontend

- `views/Ports.vue` — `audio` added to the category order (generic → audio →
  agent → eval) and to `ENDPOINT_BY_TYPE`, so an ASR/TTS port advertises its
  real path instead of `/v1/chat/completions`.
- i18n `zh/en/ja` — `ports.category.audio`.

## Verification

- Backend: `pytest` **147 passed** (130 + 17 new in `tests/test_audio.py`,
  covering router fallback, both endpoints, per-port defaults vs request
  overrides, validation, the multipart wire format, the plain-text response
  path, and the mime guesser).
- Frontend: `npm run build` OK.

## Follow-up: malformed TTS body is 400, not 500

`await request.json()` raises on a body that isn't valid UTF-8 JSON, and the
exception escaped as an unhandled 500 — which reads as "the synthesis server
broke" when the caller simply sent the wrong encoding. Both that and a
non-object body now return 400. Found by sending Chinese through a shell that
re-encoded it to GBK.

Measured against a real backend (faster-whisper-medium and CosyVoice-300M on
one GPU) once the endpoints were wired up: transcription of 5.62s of speech
takes 0.42-0.44s end to end, synthesis takes 0.84s for 10 characters and 1.83s
for 28. Synthesis dominates and scales with length, so a caller that wants low
latency should split a long line into clauses and stream them rather than
waiting for one call to return.

