# Munshi Gig Copilot

Voice-first earnings and expense assistant for Indian gig workers.
Internally everything is English. The user speaks and is answered in the same language, auto-detected per message.
The WhatsApp channel comes after the backend is solid.

## Status: Step 2 (audio input) done
- **Audio** (`app/audio`): OGG/Opus, MP3, WAV, M4A -> clean 16 kHz mono WAV with ffmpeg (loudness normalisation, silence trimming). Rejects empty, undecodable, silent and over-long (default 60 s) audio with friendly errors. ffmpeg comes bundled via `imageio-ffmpeg` (installed by `uv sync`); override with `FFMPEG_BIN`.
- **Speech-to-text** (`app/stt`): `sarvam` (primary), `faster_whisper` (free offline fallback, optional install), `mock`. Retries with backoff, long audio split into 28 s chunks, low-confidence / empty transcripts raise a friendly "unclear" error, and a fallback chain via `STT_FALLBACK`.
- **CLI**: `python scripts/transcribe_cli.py note.ogg` prints transcript, detected language and confidence.

Not yet real: translation (Step 3), parsing and replies (Step 4).

### Sarvam notes
Tested only against a mocked HTTP server (no key was available yet). Unverified assumptions, all configurable in `.env`:
`SARVAM_LANGUAGE_CODE=unknown` auto-detect, `SARVAM_MODEL`, and the response field names `transcript` / `language_code`. Run the CLI with a real key and a real voice note to confirm.

## Earlier: Step 1 (scaffold)
- `translate/mock.py` passes text through unchanged (no real translation yet).
- `translate/mock.py` passes text through unchanged (no real translation yet).
- `core/parser.py` and `core/tools.py` are stubs.

Real implementations still to come: Step 3 (language layer), Step 4 (English core), Step 5 (evaluation), Step 6 (WhatsApp).

## Run
```bash
uv sync                      # creates .venv and installs everything, including ffmpeg
copy .env.example .env       # (cp on Mac/Linux), then edit it
uv run pytest -q
uv run python scripts/transcribe_cli.py note.ogg
uv run uvicorn app.main:app --reload
```

## API
- `GET /health`
- `POST /process` (multipart form): `user_id` (required) plus either `audio` (file) or `text`.

Response fields: `transcript`, `detected_language`, `english_text`, `entries`, `reply_english`, `reply_user_language`.

```bash
curl -F user_id=u1 -F text="Swiggy se 1400 aaye" localhost:8000/process
curl -F user_id=u1 -F audio=@note.ogg localhost:8000/process
```

## Layout
```
app/
  main.py        app factory
  config.py      env settings
  pipeline.py    stage-by-stage orchestration with per-stage latency logs
  api/           routes
  audio/         ffmpeg cleanup and validation
  errors.py      AudioError / UnclearAudioError / SttUnavailableError
scripts/         transcribe_cli.py
  stt/           speech-to-text interface + providers
  translate/     translation interface + providers
  core/          db (users, transactions, messages), parser, tools
tests/
```
