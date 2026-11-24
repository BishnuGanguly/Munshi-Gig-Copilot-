# Munshi Gig Copilot

Voice-first earnings and expense assistant for Indian gig workers.
Internally everything is English. The user speaks and is answered in the same language, auto-detected per message.
The WhatsApp channel comes after the backend is solid.

## Status: Step 1 (scaffold)
All providers are **mocks**, so everything runs at zero cost with no API keys:
- `stt/mock.py` returns a fixed Hinglish transcript.
- `translate/mock.py` passes text through unchanged (no real translation yet).
- `core/parser.py` and `core/tools.py` are stubs.

Real implementations: Step 2 (audio + STT), Step 3 (language layer), Step 4 (English core), Step 5 (evaluation), Step 6 (WhatsApp).

## Run
```bash
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
pytest
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
  audio/         audio preparation (passthrough for now)
  stt/           speech-to-text interface + providers
  translate/     translation interface + providers
  core/          db (users, transactions, messages), parser, tools
tests/
```
