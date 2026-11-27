# Munshi Gig Copilot

A voice-first earnings and expense assistant for Indian gig workers (delivery and ride-hailing riders).

A rider sends a voice note — *"aaj Swiggy se 1400 aaye aur petrol 250 ka dala"* — and Munshi logs it, reads
it back for confirmation, and answers questions about their own money. Riders never pick a language: it is
detected per message and the reply comes back in the same one.

**Status: a working prototype.** A backend plus a web page to talk to it, started with one Docker command.
The WhatsApp channel comes later and plugs in on top of `POST /process`. 517 tests pass. See
[What is and isn't verified](#what-is-and-isnt-verified) before trusting any provider-facing part.

```bash
docker compose up --build        # then open http://localhost:3000
```

No keys and no `.env` are needed to start: the page opens and says which keys are not set. See
[Running it](#running-it).

## The pipeline

```
voice note or text
  ├─ audio clean-up   ffmpeg → 16 kHz mono WAV                      app/audio/
  ├─ speech-to-text   Sarvam (it also reports the language)         app/stt/
  ├─ digits           दो सौ पचास → 250; swigi → Swiggy              app/text/
  ├─ yes / no?        answered locally, no model call               app/core/validate.py
  ├─ understand       the model picks an action from the registry    app/core/brain.py
  ├─ execute          our code validates it and computes the numbers app/core/actions.py, tools.py
  ├─ reword           the model puts our reply in the rider's words  app/core/brain.py
  └─ voice note       Sarvam text-to-speech → OGG/Opus              app/tts/
```

There is no translation chain, no language detection and no cue-word regexes. The model reads the rider's
own words, in any Indian language. Measurement drove that: with regex rules, logging worked in every
language (100% on the regional-language samples, because `app/text/` does the real work) but questions
scored 42% in Hindi and **0%** in Tamil, Kannada, Bengali, Telugu and Marathi.

Three things stay in code on purpose:

- **Amounts.** `app/text/numbers.py` converts spoken amounts to digits before the model sees them. That is
  arithmetic, not understanding — and the number guard compares the model's figures against the rider's
  text, so the text has to carry digits for the guard to work at all.
- **Yes and no.** The commonest message in the app, answered in a few lines with no round-trip.
- **Every number the rider is told.** Figures come from SQL in `tools.py`. The model only *rewords* our
  sentence, and a guard rejects the rewording if it contains a number we did not state.

## Running it

**With Docker (one command).** Builds a Python 3.11 image with ffmpeg and the locked dependencies, and serves
the app on port 3000:

```bash
docker compose up --build
```

Open **http://localhost:3000**. The page has a record button, a text box and an upload control; replies
appear as text, and as a voice note when you spoke. A banner at the top says which services have no key.

**Without Docker.** Python 3.11 or later and [uv](https://docs.astral.sh/uv/):

```bash
uv sync                                          # creates .venv; ffmpeg comes bundled
uv run uvicorn app.main:app --port 3000          # then open http://localhost:3000
uv run pytest -q                                 # 517 passed
```

**What works with no keys.** Everything starts and the page works. Speech and voice fall back to **mocks**
(the speech mock always hears one fixed sentence; the voice mock is a beep), and the banner says so. The
model is not mocked, it is **off** (`BRAIN=none`), so a typed or spoken message gets the reply "The language
model key is not set". Yes and no still work, because they never reach the model.

**With keys.** Copy `.env.example` to `.env` (Docker Compose reads it automatically) and set:

```
BRAIN=sarvam,anthropic       # Sarvam first; Claude only if Sarvam is down (see "Model providers")
STT_PROVIDER=sarvam
TTS_PROVIDER=sarvam
SARVAM_API_KEY=...           # one key serves speech, voice and the model
```

Then run `uv run python scripts/check_llm.py` once. Nothing here has been tried against a live provider, and
that script says which assumption is wrong.

**Keep keys out of the repository.** `.env` is ignored by git and by the Docker build, so a key never ends up
in the image or in a commit. For a public demo, set keys as secrets on the host, never in code or the page.

A conversation. The rider's side is Hinglish, which is the model's job; the replies below are the real
output of `app/core/actions.py`, and in practice each one is then reworded into the rider's own language:

```
you>    roz 1500 kamana hai
munshi> Set your daily income target to ₹1,500? Say yes to confirm.
you>    haan
munshi> Done. Your daily income target is ₹1,500.
you>    Swiggy se 600 aaye aur petrol 250 ka dala
munshi> Please confirm: income ₹600 from Swiggy; expense ₹250 on fuel. Is that correct?
you>    haan
munshi> Saved. Today's profit is ₹350. Target: ₹600 of ₹1,500, ₹900 to go.
you>    250 nahi 300 tha
munshi> Change the last entry from expense ₹250 on fuel to expense ₹300 on fuel? Say yes to confirm.
you>    haan
munshi> Updated. The last entry is now expense ₹300 on fuel.
you>    sabse achha platform kaunsa hai
munshi> Your best platform this week is Swiggy with ₹600.
```

The two `haan` turns cost nothing: confirmations never reach the model.

## What it can do

**Nothing is written to the database until the rider confirms it.** Every number the rider sees is computed
in SQL or Python (`app/core/tools.py`), never produced by a model.

| | |
|---|---|
| **Log** | Income, expenses and savings; several per message; "yesterday"; bonuses as `incentive`. |
| **Never guesses** | If income versus expense is unclear it asks ("Was ₹500 income or an expense?"), and a bare "haan" does not skip the question. Amounts outside ₹1–₹50,000 are refused. |
| **Correct** | "it was 300 not 250", "should be 300", "actually it was diesel" — the last saved entry, confirmed first. A correction to an entry still awaiting confirmation replaces it. |
| **Delete** | "delete last entry", "undo" — confirmed first. |
| **Ask** | Earnings, spending (by category), profit, best platform, savings, summary — for today, yesterday, this/last week, this/last month, or overall. |
| **Daily target** | Per rider, confirmed before it changes, with history kept so a past day is judged by the target in force then. Progress after each save, plus a streak. |
| **Voice replies** | A voice note in gets a voice note out; typed text gets text. |

One intent per message. Unconfirmed actions expire after 30 minutes.

## How a message is handled

1. **A confirmation** — yes or no, matched locally against a word list. No model call.
2. **Everything else** — one model call picks an action; our code validates and runs it.
3. **The reply** — our English sentence, reworded by the model into the rider's language.

Without a configured provider (`BRAIN=none`) step 2 cannot run, and the assistant says it did not
understand rather than guessing.

### The action registry

Every capability is declared once in `app/core/actions.py` — name, description, JSON input schema,
read-only or write, and handlers. `brain.py` only routes; the handlers hold the logic.

Seven actions: `log_entries`, `correct_entry`, `delete_entry`, `query_summary`, `set_target`,
`get_target_progress`, `help`.

The same declarations are the model's tool list (`REGISTRY.tool_definitions()`) and could back an MCP
server later, so a new feature is one `register(...)` call.

Invariants enforced in code: arguments are schema-validated; **`user_id` is never an argument** (it comes
from the `Context`), so no caller can act for another rider; a write action only *proposes* (it stores a
pending action and returns a read-back) and applies on `commit` after a yes.

### Model providers

The model sits behind a two-method interface (`app/llm/base.py`): `choose_actions(system, tools, user)` and
`complete(system, user)`. Nothing outside `app/llm/` knows a vendor's name or wire format, so providers are
swappable, and chainable.

```
BRAIN=sarvam                one provider
BRAIN=sarvam,anthropic      a chain: the next answers only when the one before it is down
```

| Name | Speaks | Notes |
|---|---|---|
| `sarvam` | OpenAI-style | India-hosted, billed in rupees; reuses `SARVAM_API_KEY`. Default model `sarvam-105b` |
| `anthropic` | Anthropic SDK | Claude; prompt caching, low effort. Key, or `ant auth login`. `uv sync --extra llm` |
| `openai`, `groq`, `openrouter`, `ollama` | OpenAI-style | presets; Ollama needs no key |
| `openai_compat` | OpenAI-style | anything else: `BRAIN_BASE_URL`, `BRAIN_API_KEY_ENV`, `BRAIN_AUTH`, a model |

Adding a provider that speaks the OpenAI protocol is one entry in `PRESETS`; one with its own protocol is a
class implementing the interface. Models and URLs are per-provider (`BRAIN_MODEL_<NAME>`,
`BRAIN_BASE_URL_<NAME>`); `BRAIN_MODEL` is shorthand for the first provider's model.

What the adapters absorb, so the rest of the code never sees it:

- **Tool-call shape.** Our neutral `{name, description, input_schema}` becomes `{"type":"function",
  "function":{..., "parameters"}}` for OpenAI-style providers, and the reply's `arguments` — a JSON
  *string* there, a dict for Claude — is parsed. Malformed arguments are "no call", never a crash.
- **Schema dialect.** Our schemas allow `null` on optional fields; strict providers reject that, so
  OpenAI-style providers are sent a simplified schema. Validation still runs against the original.
- **Reasoning leaks.** A `<think>…</think>` block in a reply is stripped; a reply cut off mid-thought is
  treated as empty.
- **Parallel tool calls** cannot be switched off on every model, so the app caps them itself.
- **Failure.** Every provider failure — network, auth, quota, malformed reply — becomes one
  `LlmUnavailableError`. A chain moves on only for that. A model that simply chose no action is *not*
  retried on the next vendor: that would double the cost and hide a quality problem. A bug is not an outage
  and is never swallowed.

A misspelt provider name stops the app at startup, not on the first rider's message.

`tests/test_llm_equivalence.py` replays the same scenarios through the Anthropic and the OpenAI-style
adapter, each with its own response shape, and asserts the outcomes are identical — including that the same
guards refuse the same bad calls.

### What the model is and isn't trusted with

It **chooses actions**; it never sees or writes the database, and it never states a figure.

- **Two calls per message**, not an agent loop: one `choose_actions` with the registry's tool definitions,
  then one `complete` that rewords the reply. The second call happens even for an English-speaking rider,
  where it returns the sentence unchanged — detecting that cheaply would mean guessing the language again,
  and Hinglish looks like English to any such test. A confirmation makes **no** call at all. The date,
  target and last few turns go in the user turn.
- **Guards, none of them trusted to the model:** schema validation; **every number in the chosen arguments
  must appear in the rider's own message**, or the call is dropped; **the reworded reply may not contain a
  number our English sentence did not**, or the English stands; no `user_id` argument; at most
  `BRAIN_MAX_ACTIONS` calls are read and the first usable one runs; writes still need confirmation; the
  message is sent as data and the prompt says to ignore instructions inside it. A failing provider, a
  refusal, malformed output — all end in an honest "I could not understand".
- **Yes and no are matched strictly.** A refusal must be made *only* of refusal words and filler
  (`nahi`, `that's wrong`, `ye sahi nahi hai`). It runs before the model and discards a pending entry, so a
  sentence that merely contains one — "cancel my last entry", "kuch samajh nahi aaya" — goes to the model.
- **Audit:** every message records its intent and the actions it caused in `messages.intent` /
  `actions_json`.
- **Cost:** about 1,600 input and 85 output tokens per message across both calls, of which roughly 1,500
  is the fixed prefix. That is on the order of **₹5 per active rider per month** at 10 messages a day on
  either Claude Haiku or Sarvam's reported rates — so choose a provider by how well it understands
  Hinglish, not by price. The Sarvam rates are unconfirmed (see below). **Every message needs a
  round-trip**: there is no free offline path.

## Languages

The rider never selects one.

- **Voice:** Sarvam reports the language of the audio, and that is what the reply is spoken in.
- **Text:** the model replies in whatever language and script the rider wrote in, and reports the code.
  Hinglish (`hi-Latn`) is answered in Roman script but *spoken* by the Hindi voice, because Roman script
  reads badly aloud. A language with no Sarvam voice is spoken in English.

Before the model sees a message (`app/text/`):

- digits of any script → `0-9`
- spoken Hindi amounts → digits: `दो सौ पचास` and `dhai sau` both become 250; also dedh, sawa, saadhe,
  paune, hazaar, lakh. A lone small word like `दो` ("give") is only converted when a currency word follows.
- platform spellings → canonical: `स्विगी`, `swigi` → `Swiggy`

`1,400` and `1400` count as the same figure in both number guards, so Indian digit grouping never makes a
correct reply look invented.

## The web page and HTTP API

### `GET /` — the app

One self-contained page (`app/web/index.html`): no CDN, no build step, so it works offline as shipped.

- **Record** a voice note (up to 60 seconds; the browser records `webm`/`ogg`/`m4a`, all accepted), **type**,
  or **upload** an audio file.
- Replies show the rider's-language text, our English sentence beneath it when they differ, the proposed
  entries with their status, and the voice note.
- **Haan / Nahi** buttons appear when an entry waits for confirmation; **Income / Expense** when the type is
  unknown.
- The key banner reads `/status`. Rider text is only ever set as text, never parsed as HTML.
- Each browser is one rider; **New rider** starts afresh.

Recording needs `localhost` or HTTPS (a browser rule); elsewhere, use upload. Safari may not play OGG/Opus
voice replies, and the page says so when it can't.

### `GET /status`

Which services are configured, for the banner. Booleans, provider names and a message only — **never a key**.

### `POST /process`

Multipart form. `user_id` is required, plus either `audio` (a file) or `text`.
`voice` is `auto` (the default: a voice note gets a voice reply), `always`, or `never`.

```bash
curl -F user_id=u1 -F text="Swiggy se 1400 aaye" localhost:3000/process
curl -F user_id=u1 -F audio=@note.ogg localhost:3000/process
curl -F user_id=u1 -F text="hello" -F voice=always localhost:3000/process
```

Response fields:

| Field | |
|---|---|
| `transcript` | what speech-to-text heard, or the text as typed |
| `normalized_text` | amounts as digits, platform names canonical — what the model read |
| `detected_language` | from the audio, or the language the reply was written in |
| `entries` | the entries this message created, each with a `status` |
| `reply_english` | our sentence; every figure in it came from SQL |
| `reply_user_language` | the same sentence in the rider's language |
| `intent`, `warnings` | what the message was taken to be, and anything that degraded |
| `reply_audio_base64`, `reply_audio_mime` | the voice note, when one was made |

`ProcessResult.reply_audio` holds the raw voice-note bytes for the coming WhatsApp step.

Errors: `400` for unusable audio or input (empty, silent, too long, unclear); `502` when a speech service
is unavailable. A model that is off or failing is **not** an error: with no key the reply says the key is not
set, a failing model says it did not understand, and `warnings` explains which.

### `POST /speak`

`text` + `language` → an OGG/Opus voice note. For trying out voices.

### `GET /health`

Also the Docker health check. `GET /docs` is the generated API reference.

## Command-line tools

```bash
uv run python scripts/chat_cli.py --debug --fresh   # full conversation; --voice saves data/last_reply.wav
uv run python scripts/process_cli.py note1.ogg      # one message through the pipeline, as JSON
uv run python scripts/check_llm.py                  # smoke-test each model provider in BRAIN, live
uv run python scripts/transcribe_cli.py note1.ogg   # audio → transcript, language, confidence
uv run python scripts/speak_cli.py "Aaj ka profit 1150 rupaye hai" --lang hi-IN
uv run python scripts/evaluate.py                   # score the pipeline on 51 labelled transcripts
```

`chat_cli.py` is multi-turn, so confirmations work; a path to an audio file sends it as a voice note.
`speak_cli.py` writes `reply.ogg` and a `reply.wav` that plays in any Windows player.

## Evaluation

`scripts/evaluate.py` runs the whole pipeline on **51 labelled transcripts** (`tests/eval/cases.json`) in Hindi,
Hinglish, English and Tamil, 38 clean and 13 noisy (misspelt platforms, filler words, dropped verbs,
colloquial speech), and scores each against the entries a correct system would propose before the rider
confirms.

It reports **amount, type, category and platform** accuracy per expected entry, **completeness** and
**intent** per case, split by language, clean versus noisy, and speech-to-text provider, with each stage's
latency. Every failure is printed with its likely cause; for example, an amount that never reached the model
is told apart from one the model got wrong. It exits non-zero below the target, **95% amount accuracy on
clean input**. `--audio <folder>` uses recorded clips named `<case-id>.ogg` where they exist, so the score
also covers speech-to-text.

**Results: none yet.** It needs a live model. `--oracle` swaps in a stand-in that answers from the labels: it
scores 100%, which proves the scorer works and says nothing about any model. Tests also check that every
labelled amount survives text preparation, and that the scorer catches a model that swaps income and expense.

Known weaknesses the set records:

- **English number words** ("fifteen hundred") are not converted to digits, so such an amount cannot reach
  the model and the number guard refuses it. One case (`en-13`) is marked as this known gap and reported
  separately, not counted toward the target.
- The **Tamil** cases were written without a native speaker and need review.
- These are **transcripts**, not recordings. Real voice notes from riders on the road are still needed.

## Data

SQLite, with versioned migrations (`db.MIGRATIONS` + `PRAGMA user_version`, currently version 4). Old
databases upgrade in place; extend the schema by appending a numbered function, never by editing an old one.

| Table | Holds |
|---|---|
| `users` | id, last detected language |
| `transactions` | type (`income` / `expense` / `saving`), amount, category, platform, date |
| `targets` | daily income targets with `effective_from`, so history is preserved |
| `pending` | the one unconfirmed action per rider, with its timestamp |
| `messages` | transcript, `normalized_text` (what the model read), language, both replies, latency, `intent`, `actions_json` |

`messages.intent` and `actions_json` are the audit trail; `db.recent_turns()` reads them back as
conversation context.

## Configuration

All of it in `.env` (see `.env.example`). The defaults are mocks, so nothing costs anything until changed.
`docker-compose.yml` passes the main variables through with safe defaults; add any other variable to its
`environment:` list to use it in Docker.

| Group | Keys |
|---|---|
| **Speech-to-text** | `STT_PROVIDER` (`mock`/`sarvam`/`faster_whisper`), `STT_FALLBACK`, `STT_MIN_CONFIDENCE`, `SARVAM_API_KEY`, `SARVAM_MODEL`, `SARVAM_LANGUAGE_CODE`, `SARVAM_MODE`, `WHISPER_MODEL` |
| **Voice replies** | `TTS_PROVIDER` (`mock`/`sarvam`), `SARVAM_TTS_MODEL`, `SARVAM_TTS_SPEAKER`, `VOICE_MAX_CHARS` |
| **The model** | `BRAIN` (a provider, or a comma-separated chain), `BRAIN_MAX_ACTIONS`, `BRAIN_MODEL`, `BRAIN_MODEL_<NAME>`, `BRAIN_BASE_URL_<NAME>`, `ANTHROPIC_API_KEY`; for `openai_compat`: `BRAIN_BASE_URL`, `BRAIN_API_KEY_ENV`, `BRAIN_AUTH` |
| **Audio** | `MAX_AUDIO_SECONDS`, `SILENCE_RMS_THRESHOLD`, `FFMPEG_BIN` |
| **App** | `DB_PATH`, `LOG_LEVEL` |

ffmpeg ships with the install (`imageio-ffmpeg`), so no system install is needed; `FFMPEG_BIN` overrides it.
The Docker image uses Debian's ffmpeg instead, so it also runs on arm64 (Apple Silicon).
Optional extras: `uv sync --extra llm` (the Claude SDK, only needed for `anthropic`; included in the Docker
image) and `uv sync --extra whisper` (offline speech-to-text). Dependencies are declared in `pyproject.toml`
and locked in `uv.lock`.

## What is and isn't verified

**Verified against a real service:** speech-to-text only. A real Hindi voice note returned
`आज मैं दो सौ पचास रुपए का बचत किया।`, which normalises to `आज मैं 250 रुपए का बचत किया।`

**Covered by the 517 tests:** audio handling, spoken amounts and normalisation across 8 languages, the
action handlers and every confirmation flow, the registry and its guards, migrations, the daily target, the
model-facing code against fake clients (including output that invents numbers or tries to act for another
rider), the evaluation scorer, the web page and status endpoint (including that no key ever appears in
`/status`), and the Docker files' ports and defaults. The suite passes on Python 3.11 and 3.13.

**The app, in a real browser:** Chromium, with a fake microphone, at 320 to 1280 pixels wide. Typing,
recording a voice note, the transcript, the voice reply and the key banner all work, with no horizontal
scroll on a phone and no console errors. The Docker image's steps were replayed outside Docker, with the same
files, the same locked install on Python 3.11 and the same environment and no `.env`; that started and
served every page.

**Not verified:** the Docker image itself has not been built (no Docker daemon was available), and nothing
below has ever had a live call.

**Not verified — no live call has ever been made:**

- **Any model provider.** Nothing here has ever spoken to a model. The Claude request shape is checked against
  the installed SDK (2.13.0); the Sarvam chat API's endpoint (`/v1/chat/completions`), auth header
  (`api-subscription-key`), model (`sarvam-105b`; `sarvam-m` is deprecated) and stringified tool-call
  arguments come from its docs as surfaced by search, which could not be opened directly. Its price is
  reported inconsistently (₹10/₹25 or ₹15/₹60 per million tokens) and `parallel_tool_calls: false` is
  reported as *not* enforced on `sarvam-105b`. **Run `scripts/check_llm.py` first.**
- **How well any model reads Hinglish and picks among seven tools.** The adapters are tested; the models are
  not. `scripts/evaluate.py` is built for exactly this and has never been run against a model.
- **Sarvam text-to-speech.** Whether the language field is `target_language_code` or `language_code` is
  unconfirmed, so the code retries with the other name; speaker names for a newer Bulbul than v2 are
  unchecked; and nobody has heard how it reads `₹1,400` aloud.
- **The regional-language sample sentences** (Tamil, Kannada, Bengali, Telugu, Marathi in
  `tests/data/sentences.json`) were written without a native speaker and need review.

## Not built yet

- **The WhatsApp bot.** No webhook; nothing is connected to WhatsApp.
- **An end-of-day summary**, and the scheduler it needs.
- **Spoken-friendly amounts** — the voice reads `₹1,400` rather than words.
- **Real results.** The evaluation harness and 51 labelled transcripts exist; real rider recordings and a
  live-model run do not. This is the most important gap: the tests prove the code matches our own
  assumptions, not that it works for riders.
- Savings goals, reminders, hours tracking (the bot says so when asked), a reporting dashboard, Postgres.

## Layout

```
app/
  main.py              app factory
  config.py            .env settings
  pipeline.py          stage-by-stage orchestration, per-stage latency logs
  errors.py            AudioError, UnclearAudioError, ServiceUnavailableError
                       (+ Stt/TtsUnavailableError subclasses)
  api/routes.py        /, /status, /process, /speak, /health
  web/index.html       the app's page: record, type or upload; read and hear the reply
  status.py            what is configured, for the key banner (never a secret)
  evaluation.py        scores the pipeline against labelled transcripts
  audio/               ffmpeg clean-up, validation, voice-note encoding
  stt/                 interface + sarvam, whisper_local, mock
  llm/                 the provider interface; anthropic, openai_compat (+ presets), fallback chain
  text/                numbers (spoken amounts → digits), normalize, chunk
  tts/                 service + sarvam, mock
  core/
    brain.py           the two model calls (provider-agnostic), their guards, and the routing
    registry.py        ActionSpec, Registry, Context, schema validation
    actions.py         the seven action handlers + build_registry()
    tools.py           all database reads and writes; every number shown comes from here
    validate.py        amount limits, field values, and yes/no
    db.py              schema + versioned migrations
    dates.py           IST and period resolution
    replies.py         reply wording and ₹ formatting
scripts/               chat_cli, process_cli, transcribe_cli, speak_cli, check_llm, evaluate
tests/                 517 tests; data/sentences.json (75 sample sentences), eval/cases.json (51 cases)
Dockerfile             Python 3.11 + ffmpeg + locked dependencies; serves port 3000 as a non-root user
docker-compose.yml     one command to start it; every variable has a default, no .env needed
```

## History

Built in order: a FastAPI scaffold with mocks; audio input and speech-to-text; a translation layer; a
rule-based English core; voice replies; the action registry, migrations and the daily target; an LLM
understanding layer beside the rules; then — once measurement showed the rules could not answer questions
in regional languages at all — the translation chain and the regexes were deleted and the model took over
understanding; the model was then put behind a provider interface (Sarvam, Claude, any OpenAI-style
provider, with failover); finally an evaluation harness, the web page and the Docker setup. `git log` has the
detail.
