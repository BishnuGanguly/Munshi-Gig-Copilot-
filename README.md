# Munshi Gig Copilot

Voice-first earnings and expense assistant for Indian gig workers.
Internally everything is English. The user speaks and is answered in the same language, auto-detected per message.
The WhatsApp channel comes after the backend is solid.

## Status: Step 4 (English core) done
Rules first, an optional LLM only as a last resort. **Nothing is saved until the user says yes.**
- **Log:** "Got 1400 from Swiggy and spent 250 on petrol" -> read-back "Please confirm: income ₹1,400 from Swiggy; expense ₹250 on fuel. Is that correct?" -> `yes` saves, `no` discards. Handles several items, yesterday, `1.5k`, quantities (5 litres) vs money, savings, bonus/incentive.
- **Unsure about type?** It asks ("Was ₹500 income or an expense?") instead of guessing. Amounts outside ₹1 to ₹50,000 are refused.
- **Corrections:** "it was 300 not 250", "250 nahi 300", "should be 300", "actually it was diesel" - on the unsaved entry, or on the last saved entry (with confirmation). **Delete/undo** also asks first.
- **Questions** (all numbers computed in SQL/code): earnings, spending (by category), profit, best platform, savings, summary, for today / yesterday / this or last week / this or last month / overall.
- **Optional LLM fallback** (`PARSER_LLM=anthropic`, paid): only runs when the rules find nothing; it only extracts JSON which is validated in code, and the user still confirms.
- One intent per message; unconfirmed actions expire after 30 minutes; `saving` is a third transaction type (old databases are migrated automatically).
- Try it: `uv run python scripts/chat_cli.py --debug` (multi-turn, also accepts a voice-note path).

Not yet: goals, reminders, working hours (the bot says so), voice replies, WhatsApp.

## Earlier: Step 3 (language layer)
Pipeline order: audio -> speech-to-text -> **language detected per message** -> normalise -> English -> core -> English reply -> same language as the user. Nothing to select.
- **Normalise** (`app/translate/normalize.py`, `numbers.py`): digits of any script -> `0-9`; spoken Hindi amounts -> digits (`दो सौ पचास` / `dhai sau` -> 250; handles dedh, dhai, sawa, saadhe, paune, hazaar, lakh); platform spellings -> canonical (`स्विगी`, `swigi` -> Swiggy). Lone small words like `दो` ("give") are not converted unless followed by a currency word.
- **Languages**: voice uses the code Sarvam returns; typed text is detected by script (Devanagari, Bengali, Tamil, Telugu, Kannada, Gujarati, Gurmukhi, Odia, Malayalam), Latin text is English or Hinglish (`hi-Latn`, replied to in Roman script). Digit-only messages reuse the user's last language.
- **Translation** (`app/translate`): `sarvam` (primary), `llm` (optional Claude fallback, paid), `mock`. A chain tries the next provider if one fails **or drops a number**; if all drop numbers the result carries a `warnings` entry. Reply digits are always `0-9`. If reply translation fails the user gets the English reply plus a warning.
- **Scripts**: `process_cli.py` (whole pipeline), `eval_translation.py` (75 sample sentences, pass/fail table per language).

Verified here only with mocked HTTP: Sarvam translate parameters `numerals_format` / `output_script` and Claude's behaviour. Regional-language sample sentences need native-speaker review.

## Earlier: Step 2 (audio input)
- **Audio** (`app/audio`): OGG/Opus, MP3, WAV, M4A -> clean 16 kHz mono WAV with ffmpeg (loudness normalisation, silence trimming). Rejects empty, undecodable, silent and over-long (default 60 s) audio with friendly errors. ffmpeg comes bundled via `imageio-ffmpeg` (installed by `uv sync`); override with `FFMPEG_BIN`.
- **Speech-to-text** (`app/stt`): `sarvam` (primary), `faster_whisper` (free offline fallback, optional install), `mock`. Retries with backoff, long audio split into 28 s chunks, low-confidence / empty transcripts raise a friendly "unclear" error, and a fallback chain via `STT_FALLBACK`.
- **CLI**: `python scripts/transcribe_cli.py note.ogg` prints transcript, detected language and confidence.



### Sarvam notes
Tested only against a mocked HTTP server (no key was available yet). Unverified assumptions, all configurable in `.env`:
`SARVAM_LANGUAGE_CODE=unknown` auto-detect, `SARVAM_MODEL`, and the response field names `transcript` / `language_code`. Run the CLI with a real key and a real voice note to confirm.

## Earlier: Step 1 (scaffold)
- `translate/mock.py` passes text through unchanged (used by tests).
- `core/parser.py` and `core/tools.py` are stubs.

Still to come: Step 5 (evaluation), Step 6 (WhatsApp).

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

Response fields: `transcript`, `detected_language`, `normalized_text`, `english_text`, `entries`, `reply_english`, `reply_user_language`, `warnings`.

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
  translate/     languages, numbers, normalize, providers (sarvam, llm, mock), service
  core/          db (users, transactions, messages), parser, tools
tests/
```
