# Munshi Gig Copilot

Voice-first earnings and expense assistant for Indian gig workers.
Internally everything is English. The user speaks and is answered in the same language, auto-detected per message.
The WhatsApp channel comes after the backend is solid.

## Status: LLM understanding layer done (off by default)
`app/core/llm_router.py`. The model **chooses actions; our code executes them** - it never sees or writes the database.
- **When it runs:** only after the rules fail, i.e. the message could not be placed at all, or an amount was found but not whether it was income or an expense. A message the rules fully understand never reaches the model, so the common path stays free and instant.
- **One call, not a loop:** `messages.create` with the registry's `tool_definitions()` as the tool list, `tool_choice: auto`, effort `low` on `claude-haiku-5-5`. The static system prompt is sent with `cache_control` so the prefix caches; today's date, the target, and the last few turns go in the user turn.
- **Guards (all in code, none trusted to the model):** arguments are schema-validated by the registry; **every number in the chosen arguments must appear in the rider's message**, or the call is dropped (no invented amounts); `user_id` is not an argument, so it cannot act for another user; at most `LLM_ROUTER_MAX_ACTIONS` (3) calls are read and the first usable one runs; write actions still only propose and the rider confirms; the message is sent as data and the prompt says to ignore instructions inside it. Refusals, network errors, a missing package and malformed output all fall back to the rules.
- **Audit:** each routed message records `llm_router_used` in `warnings` and the action in `messages.actions_json`.
- **Turn it on:** `uv sync --extra llm`, then `LLM_ROUTER=anthropic` and `ANTHROPIC_API_KEY=...` in `.env`. **This is paid** (about ₹0.03 per routed message on Haiku 5.5); with it off, nothing changes.
- **Not verified:** no live API call has been made from here (no key, no network). The request shape is checked against the installed SDK (2.13.0) and the whole layer is tested against a fake client, including hostile output.

## Earlier: Phase 0 (foundation) and daily income target
**Action registry** (`app/core/registry.py`, `actions.py`): every capability is declared once (name, description, JSON input schema, read-only or write, handlers). `engine.py` is now only a router that calls `REGISTRY.execute(...)`. The same declarations are the LLM tool list (`REGISTRY.tool_definitions()`) and could back an MCP server later; a new feature is one `register(...)` call. Rules: arguments are schema-validated; `user_id` is never an argument (it comes from the Context); write actions only *propose* (pending + read-back) and apply on `commit` after the user says yes; every message records its intent and actions in `messages.intent / actions_json`.
**Versioned migrations** (`db.MIGRATIONS`, `PRAGMA user_version`): add a numbered function to extend the schema; old databases upgrade in place. `db.recent_turns()` gives the last exchanges as context for the coming LLM layer.
**Daily income target** (per user, confirmed before it changes, history kept so past days are judged by the target then):
- "set my daily target to 1500", "my target is 1500", "I want to earn 1800 a day" -> "Set your daily income target to ₹1,500? Say yes to confirm." Range ₹100 to ₹50,000. "make it 2000" corrects it before confirming.
- After each save that includes income: "Target: ₹600 of ₹1,500, ₹900 to go." / "You reached your daily target of ₹1,500!" / "already reached today".
- "how far am I from my target", "did I hit my target yesterday" -> progress, plus a streak of consecutive days.
- Not included yet: an "off day" (no streak penalty), clearing a target, weekly targets.

## Earlier: Step 6 (voice replies)
A voice note in gets a voice note out; typed text gets text (override with `voice=always|never`).
- **How:** reply text (in the user's language) -> Sarvam Bulbul text-to-speech -> ffmpeg -> **OGG/Opus mono**, the format WhatsApp plays as a voice note. Returned by `POST /process` as `reply_audio_base64` + `reply_audio_mime`; `ProcessResult.reply_audio` holds the raw bytes for the WhatsApp step.
- **Never blocks the text reply:** if the voice service fails, the reply is still returned with a `voice_reply_failed` warning. Replies over `VOICE_MAX_CHARS` (default 400) stay text-only.
- **Language rules:** Hinglish is spoken with a Hindi voice (re-rendered in Hindi script); languages without a voice, and replies that could not be translated, are spoken in English.
- **Providers:** `TTS_PROVIDER=mock` (a beep; zero cost) or `sarvam`. `POST /speak` (text + language -> voice note) is there for trying voices.
- **Try it:** `uv run python scripts/speak_cli.py "Aaj ka profit 1150 rupaye hai" --lang hi-IN` writes `reply.ogg` and a `reply.wav` that plays in any Windows player. `uv run python scripts/chat_cli.py --voice` saves each answer to `data/last_reply.wav`.
- **Unverified (no live key here):** the Sarvam TTS language field name (the code retries with the alternate name on HTTP 400/422), speaker names for your model version, and how well digits like 1,150 are read aloud.
- **Still not built:** the WhatsApp webhook that sends these voice notes (the remaining part of the original Step 6), goals, reminders, evaluation set.

## Earlier: Step 4 (English core)
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
  tts/           text-to-speech providers (sarvam, mock) + voice-note service
  core/          db (users, transactions, messages), parser, tools
tests/
```
