# Munshi Gig Copilot

A voice-first earnings and expense assistant for Indian gig workers (delivery and ride-hailing riders).

A rider sends a voice note — *"aaj Swiggy se 1400 aaye aur petrol 250 ka dala"* — and Munshi logs it, reads
it back for confirmation, and answers questions about their own money. Riders never pick a language: it is
detected per message and the reply comes back in the same one. **Internally everything is English**, so the
parsing, rules and maths live in one language.

**Status: backend only.** There is no app and no WhatsApp bot yet; the channel plugs in on top of
`POST /process`. 340 tests pass. See [What is and isn't verified](#what-is-and-isnt-verified) before
trusting any provider-facing part.

## Two pipelines

`PIPELINE` in `.env` chooses how a message is understood. Both share the audio and voice ends.

**`llm` — the LLM-first path** (`app/core/brain.py`), being moved to:

```
voice note → Sarvam STT → numbers.py (दो सौ पचास → 250)
           → yes / no?  answered locally, no model call
           → the model picks an action (reading the rider's own language)
           → our code validates it, runs it, computes every number
           → the model renders our English sentence in the rider's language
           → Sarvam TTS → voice note
```

No translation chain, no language detection, no cue-word regexes. Measurement drove this: with the rules
alone, logging worked in every language (100% on regional-language samples, because `numbers.py` does the
real work), but questions scored 42% in Hindi and **0%** in Tamil, Kannada, Bengali, Telugu and Marathi.
A multilingual model reads those directly.

Three things stay in code on purpose:

- **Amounts.** `numbers.py` converts spoken amounts to digits before the model sees them. That is
  arithmetic, not understanding — and the number guard compares the model's figures against the rider's
  text, so the text has to carry digits for it to work at all.
- **Yes and no.** The commonest message in the app, answered in a few lines with no round-trip.
- **Every number the rider is told.** Figures come from SQL in `tools.py`. The model only *rewords* our
  sentence, and a guard rejects the rewording if it contains a number we did not state.

Needs `BRAIN=anthropic` and `ANTHROPIC_API_KEY`; without them it answers "I could not understand" rather
than guessing. **Every message costs a round-trip** (roughly ₹10 per rider per month on Haiku), so there is
no free offline path.

**`rules` — the original path** (default for now, `app/core/engine.py`), kept so the two can be compared on
real voice notes:

```
voice note or text

```
voice note or text
  ├─ audio clean-up      ffmpeg → 16 kHz mono WAV            app/audio/
  ├─ speech-to-text      Sarvam | faster-whisper | mock      app/stt/
  ├─ language detected   from the audio, or the text's script app/translate/languages.py
  ├─ normalise           spoken amounts → digits, platforms   app/translate/numbers.py
  ├─ translate           → English, numbers verified           app/translate/
  ├─ route + execute     rules first, LLM only if they fail    app/core/
  ├─ reply               short English sentence                app/core/replies.py
  ├─ translate back      → the rider's language                app/translate/
  └─ voice reply         → OGG/Opus voice note                 app/tts/
```

## Quick start

```bash
uv sync                      # creates .venv and installs everything, including a bundled ffmpeg
cp .env.example .env         # copy on Windows; then edit it
uv run pytest -q             # 340 passed
uv run python scripts/chat_cli.py --fresh
uv run python scripts/chat_cli.py --llm --fresh   # the LLM-first pipeline
```

Out of the box every provider is a **mock**, so it runs with no API keys and at no cost. Set
`STT_PROVIDER=sarvam`, `TRANSLATE_PROVIDER=sarvam`, `TTS_PROVIDER=sarvam` and `SARVAM_API_KEY` in `.env`
for the real thing.

A sample conversation:

```
you>    set my daily target to 1500
munshi> Set your daily income target to ₹1,500? Say yes to confirm.
you>    yes
munshi> Done. Your daily income target is ₹1,500.
you>    Got 600 from Swiggy and spent 250 on petrol
munshi> Please confirm: income ₹600 from Swiggy; expense ₹250 on fuel. Is that correct?
you>    yes
munshi> Saved. Today's profit is ₹350. Target: ₹600 of ₹1,500, ₹900 to go.
you>    should be 300
munshi> Change the last entry from expense ₹250 on fuel to expense ₹300 on fuel? Say yes to confirm.
you>    yes
munshi> Updated. The last entry is now expense ₹300 on fuel.
you>    which platform is best
munshi> Your best platform this week is Swiggy with ₹600.
```

## What it can do

**Nothing is written to the database until the rider confirms it.** Every number the rider sees is computed
in SQL or Python (`app/core/tools.py`), never produced by a model.

| | |
|---|---|
| **Log** | Income, expenses and savings; several per message; "yesterday"; `1.5k`; bonuses as `incentive`. Money is told apart from quantities, so "5 litres for 500" records ₹500. |
| **Never guesses** | If income versus expense is unclear it asks ("Was ₹500 income or an expense?"). Amounts outside ₹1–₹50,000 are refused. |
| **Correct** | "it was 300 not 250", "250 nahi 300", "should be 300", "actually it was diesel" — on the unsaved entry, or on the last saved one (confirmed first). |
| **Delete** | "delete last entry", "undo" — confirmed first. |
| **Ask** | Earnings, spending (by category), profit, best platform, savings, summary — for today, yesterday, this/last week, this/last month, or overall. |
| **Daily target** | Per rider, confirmed before it changes, with history kept so a past day is judged by the target in force then. Progress after each save, plus a streak. |
| **Voice replies** | A voice note in gets a voice note out; typed text gets text. |

One intent per message. Unconfirmed actions expire after 30 minutes.

## How a message is routed

1. **An action waiting for confirmation** → yes / no / a correction / an answer to a question.
2. **Rules** (`app/core/parser.py`) — free, instant, and they handle the common phrasings.
3. **The LLM understanding layer** (`app/core/llm_router.py`) — only when the rules cannot place the
   message, or found an amount but could not tell income from expense. **Off by default.**

### The action registry

Every capability is declared once in `app/core/actions.py` — name, description, JSON input schema,
read-only or write, and handlers. `engine.py` is only a router; the handlers hold the logic.

Seven actions: `log_entries`, `correct_entry`, `delete_entry`, `query_summary`, `set_target`,
`get_target_progress`, `help`.

The same declarations serve three consumers, so a new feature is one `register(...)` call:

- the rule router, via `REGISTRY.execute(name, args, ctx)`
- the LLM layer, via `REGISTRY.tool_definitions()` as its tool list
- an MCP server later, as a thin adapter

Invariants enforced in code: arguments are schema-validated; **`user_id` is never an argument** (it comes
from the `Context`), so no caller can act for another rider; a write action only *proposes* (it stores a
pending action and returns a read-back) and applies on `commit` after a yes.

### The LLM understanding layer

The model **chooses actions; our code executes them.** It never sees or writes the database.

- **One call, not an agent loop:** `messages.create` with the registry's tool definitions,
  `tool_choice: auto`, effort `low`, on `claude-haiku-5-5`. The static system prompt carries
  `cache_control` so the prefix caches; the date, target and last few turns go in the user turn.
- **Guards, none of them trusted to the model:** schema validation; **every number in the chosen
  arguments must appear in the rider's own message**, or the call is dropped (no invented amounts); no
  `user_id` argument; at most `LLM_ROUTER_MAX_ACTIONS` calls are read and the first usable one runs;
  writes still need confirmation; the message is sent as data and the prompt says to ignore instructions
  inside it. Refusals, network errors, a missing package and malformed output all fall back to the rules.
- **Audit:** a routed message records `llm_router_used` in `warnings`, and the action in
  `messages.actions_json`.
- **Turn it on:** `uv sync --extra llm`, then `LLM_ROUTER=anthropic` and `ANTHROPIC_API_KEY=...`.
  **This is paid** — roughly ₹0.03 per routed message. With it off, nothing changes.

## Languages

- **Voice:** the language Sarvam reports for the audio.
- **Text:** detected from the script (Devanagari, Bengali, Tamil, Telugu, Kannada, Gujarati, Gurmukhi,
  Odia, Malayalam). Latin text is English or **Hinglish** (`hi-Latn`), which is replied to in Roman script
  but *spoken* with a Hindi voice, because Roman script reads badly aloud.
- **A message with no script clue** (for example just `1400`) reuses the rider's last language.

Before translation (`app/translate/normalize.py`, `numbers.py`):

- digits of any script → `0-9`
- spoken Hindi amounts → digits: `दो सौ पचास` and `dhai sau` both become 250; also dedh, sawa, saadhe,
  paune, hazaar, lakh. A lone small word like `दो` ("give") is only converted when a currency word follows.
- platform spellings → canonical: `स्विगी`, `swigi` → `Swiggy`

After translation, **every number in the input must still be present in the output**. If a provider drops
one, the next provider is tried; if all do, the result carries a warning. Reply digits are always `0-9`.
If translating the reply fails, the rider gets the English reply plus a warning rather than nothing.

## HTTP API

Start it with `uv run uvicorn app.main:app --reload`.

### `POST /process`

Multipart form. `user_id` is required, plus either `audio` (a file) or `text`.
`voice` is `auto` (the default: a voice note gets a voice reply), `always`, or `never`.

```bash
curl -F user_id=u1 -F text="Swiggy se 1400 aaye" localhost:8000/process
curl -F user_id=u1 -F audio=@note.ogg localhost:8000/process
curl -F user_id=u1 -F text="hello" -F voice=always localhost:8000/process
```

Response fields: `transcript`, `detected_language`, `normalized_text`, `english_text`, `entries`,
`reply_english`, `reply_user_language`, `intent`, `warnings`, `reply_audio_base64`, `reply_audio_mime`.

`ProcessResult.reply_audio` holds the raw voice-note bytes for the coming WhatsApp step.

Errors: `400` for unusable audio or input (empty, silent, too long, unclear); `502` when a speech or
language provider is unavailable.

### `POST /speak`

`text` + `language` → an OGG/Opus voice note. For trying out voices.

### `GET /health`

## Command-line tools

```bash
uv run python scripts/chat_cli.py --debug --fresh   # full conversation; --voice saves data/last_reply.wav
uv run python scripts/process_cli.py note1.ogg      # one message through the pipeline, as JSON
uv run python scripts/transcribe_cli.py note1.ogg   # audio → transcript, language, confidence
uv run python scripts/speak_cli.py "Aaj ka profit 1150 rupaye hai" --lang hi-IN
uv run python scripts/eval_translation.py           # 75 sample sentences, scored per language
```

`chat_cli.py` is multi-turn, so confirmations work; a path to an audio file sends it as a voice note.
`speak_cli.py` writes `reply.ogg` and a `reply.wav` that plays in any Windows player.

## Data

SQLite, with versioned migrations (`db.MIGRATIONS` + `PRAGMA user_version`, currently version 3). Old
databases upgrade in place; extend the schema by appending a numbered function, never by editing an old one.

| Table | Holds |
|---|---|
| `users` | id, last detected language |
| `transactions` | type (`income` / `expense` / `saving`), amount, category, platform, date |
| `targets` | daily income targets with `effective_from`, so history is preserved |
| `pending` | the one unconfirmed action per rider, with its timestamp |
| `messages` | transcript, language, English text, both replies, latency, `intent`, `actions_json` |

`messages.intent` and `actions_json` are the audit trail; `db.recent_turns()` reads them back as
conversation context.

## Configuration

All of it in `.env` (see `.env.example`). The defaults are mocks, so nothing costs anything until changed.

| Group | Keys |
|---|---|
| **Speech-to-text** | `STT_PROVIDER` (`mock`/`sarvam`/`faster_whisper`), `STT_FALLBACK`, `STT_MIN_CONFIDENCE`, `SARVAM_API_KEY`, `SARVAM_MODEL`, `SARVAM_LANGUAGE_CODE`, `SARVAM_MODE`, `WHISPER_MODEL` |
| **Translation** | `TRANSLATE_PROVIDER` (`mock`/`sarvam`/`llm`), `TRANSLATE_FALLBACK`, `SARVAM_TRANSLATE_MODEL`, `SARVAM_NUMERALS_FORMAT`, `SARVAM_ROMAN_SCRIPT`, `LLM_TRANSLATE_MODEL` |
| **Voice replies** | `TTS_PROVIDER` (`mock`/`sarvam`), `SARVAM_TTS_MODEL`, `SARVAM_TTS_SPEAKER`, `VOICE_MAX_CHARS` |
| **Pipeline** | `PIPELINE` (`rules`/`llm`), `BRAIN` (`none`/`anthropic`), `BRAIN_MODEL`, `BRAIN_RENDER_MODEL` |
| **LLM layer** | `LLM_ROUTER` (`none`/`anthropic`), `LLM_ROUTER_MODEL`, `LLM_ROUTER_MAX_ACTIONS`, `PARSER_LLM`, `LLM_PARSE_MODEL`, `ANTHROPIC_API_KEY` |
| **Audio** | `MAX_AUDIO_SECONDS`, `SILENCE_RMS_THRESHOLD`, `FFMPEG_BIN` |
| **App** | `DB_PATH`, `LOG_LEVEL` |

ffmpeg ships with the install (`imageio-ffmpeg`), so no system install is needed; `FFMPEG_BIN` overrides it.
Optional extras: `uv sync --extra llm` (Claude) and `uv sync --extra whisper` (offline speech-to-text).

## What is and isn't verified

**Verified against a real service:** speech-to-text only. A real Hindi voice note returned
`आज मैं दो सौ पचास रुपए का बचत किया।`, which normalises to `आज मैं 250 रुपए का बचत किया।`

**Covered by the 340 tests:** audio handling, number words and normalisation, language detection, the
parser, the engine and every confirmation flow, the registry and its guards, migrations, targets, and the
LLM layer against a fake client (including hostile and malformed output).

**Not verified — no live call has ever been made:**

- **Sarvam translation.** The parameters `numerals_format` and `output_script` are informed guesses; on
  HTTP 400/422 the code retries once without them.
- **Sarvam text-to-speech.** Whether the language field is `target_language_code` or `language_code` is
  unconfirmed, so the code retries with the other name; speaker names for a newer Bulbul than v2 are
  unchecked; and nobody has heard how it reads `₹1,400` aloud.
- **The Claude API.** The request shape is checked against the installed SDK (2.13.0), nothing more.
- **The regional-language sample sentences** (Tamil, Kannada, Bengali, Telugu, Marathi in
  `tests/data/sentences.json`) were written without a native speaker and need review before the scores
  from `eval_translation.py` mean anything.

## Not built yet

- **The WhatsApp bot.** No webhook; nothing is connected to WhatsApp.
- **An end-of-day summary**, and the scheduler it needs.
- **Spoken-friendly amounts** — the voice reads `₹1,400` rather than words.
- **A real evaluation set** of 50+ rider recordings scored end to end. This is the most important gap:
  the current tests prove the code matches our own assumptions, not that it works for riders.
- Savings goals, reminders, hours tracking (the bot says so when asked), the web dashboard, Postgres.

## Layout

```
app/
  main.py              app factory
  config.py            .env settings
  pipeline.py          stage-by-stage orchestration, per-stage latency logs
  errors.py            AudioError, UnclearAudioError, ServiceUnavailableError
                       (+ Stt/Translate/TtsUnavailableError subclasses)
  api/routes.py        /process, /speak, /health
  audio/               ffmpeg clean-up, validation, voice-note encoding
  stt/                 interface + sarvam, whisper_local, mock
  translate/           languages, numbers, normalize, service + sarvam, llm, mock
  tts/                 service + sarvam, mock
  core/
    registry.py        ActionSpec, Registry, Context, schema validation
    actions.py         the seven action handlers + build_registry()
    engine.py          the router
    parser.py          rule-based parsing (logs, corrections, queries, yes/no, targets)
    tools.py           all database reads and writes; every number shown comes from here
    db.py              schema + versioned migrations
    llm_router.py      the LLM understanding layer
    llm_parser.py      older extraction-only fallback
    dates.py           IST and period resolution
    vocab.py           platform, category and cue words
    replies.py         reply wording and ₹ formatting
scripts/               chat_cli, process_cli, transcribe_cli, speak_cli, eval_translation
tests/                 340 tests; tests/data/sentences.json holds the 75 sample sentences
```

## History

Built in order: a FastAPI scaffold with mocks; audio input and speech-to-text; the language layer; the
English core; voice replies; the action registry, migrations and the daily target; the LLM understanding
layer. `git log` has the detail.
