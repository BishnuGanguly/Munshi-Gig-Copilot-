# Running and testing Munshi

Everything needed to start the app, exercise every part of it, and know what a pass or a failure means.
Commands are shown for bash and for Windows PowerShell where they differ.

## 1. What you need

| Need | Notes |
|---|---|
| [uv](https://docs.astral.sh/uv/) | Installs Python 3.11+ and every dependency; no `pip`, no system ffmpeg. |
| Docker Desktop (optional) | Only for the one-command start in section 4. |
| A Sarvam API key (optional) | Only for real speech, voice and model. Without it the app still starts. |

PowerShell users: write `curl.exe` (plain `curl` is an alias for something else) and `docker compose`
(two words).

## 2. Setup

```bash
git clone https://github.com/BishnuGanguly/Munshi-Gig-Copilot-.git
cd Munshi-Gig-Copilot-
git checkout claude/init-scaffold       # until it is merged into main
uv sync                                 # creates .venv from the locked versions
```

## 3. Configuration (`.env`)

Nothing is required. To use real services copy the template and edit it:

```bash
cp .env.example .env                    # PowerShell: Copy-Item .env.example .env
```

Minimum for the real thing:

```
BRAIN=sarvam
STT_PROVIDER=sarvam
TTS_PROVIDER=sarvam
SARVAM_API_KEY=your-key
```

One Sarvam key serves speech-to-text, text-to-speech and the language model. The other variables are listed
and explained in `.env.example`. Never commit `.env` (it is git-ignored) and never paste a key into chat or an
issue; if one leaks, revoke it in the Sarvam dashboard and make a new one.

**Reasoning models.** `sarvam-105b` thinks before answering and the thinking uses up the token budget. The app
asks for `low` reasoning when choosing an action and none when rewording. Override with
`BRAIN_REASONING_SARVAM=low|medium|high|off|omit` if replies come back empty.

## 4. Running the app

### With uv

```bash
uv run uvicorn app.main:app --port 3000
```

Open <http://localhost:3000>. Stop with Ctrl+C. The database is `data/munshi.db`.

### With Docker

```bash
docker compose up --build
```

Open <http://localhost:3000>. No `.env` is needed to start; if one exists next to `docker-compose.yml` it is
read automatically. The database lives in the named volume `munshi-data`, so it survives restarts.

```bash
docker compose logs -f munshi           # follow the logs
docker compose down                     # stop (data kept)
docker compose down -v                  # stop and delete the data volume
docker compose up --build --force-recreate   # after changing .env or code
```

The container runs as a non-root user and has a healthcheck on `/health`
(`docker compose ps` should show `healthy` after a few seconds).

### Keyless mode (what a reviewer sees with no `.env`)

- The page loads and a banner says which services have no key.
- Speech and voice use mocks: the speech mock always "hears" one fixed sentence and the voice mock is a beep.
- The model is **off**, so a typed or spoken message gets: *"The language model key is not set, so I cannot
  understand messages yet. Yes and no still work."*

## 5. Using the web page

1. Type a message such as `aaj swiggy se 1400 aaye aur petrol 250 ka dala`, or press the record button and
   speak, or upload an audio file.
2. Munshi reads the entry back and asks you to confirm. Reply `haan` / `yes` to save, `nahi` / `no` to
   discard. The Haan/Nahi buttons do the same.
3. Ask questions: `aaj kitna kamaya`, `is hafte ka kharcha`, `target 1000 set karo`, `target kitna bacha`.
   For the reports: `aaj ka hisaab` (the daily summary), `swiggy se profit kitna hua` (one app's own profit),
   `is hafte kaunsa app zyada deta hai` (profit per app, per working day).
   Log a platform cost to see it counted against that app: `swiggy ka 50 penalty laga`.
4. A voice note is answered with a voice note; typed text is answered with text only.

Rider identity on the page is a locally stored id. Different ids have separate books.

## 6. HTTP API

| Method and path | Purpose |
|---|---|
| `GET /` | the web page |
| `GET /health` | liveness: `{"status":"ok"}` |
| `GET /status` | which services are configured (booleans and names, never a key) |
| `POST /process` | form fields `user_id`, and `text` or an `audio` file; optional `voice` = `auto` (default), `always`, `never` |
| `POST /speak` | form fields `text`, `language` (e.g. `hi-IN`); returns an OGG/Opus voice note |
| `GET /docs` | interactive API explorer |

```bash
curl.exe http://localhost:3000/health
curl.exe http://localhost:3000/status
curl.exe -X POST http://localhost:3000/process -F user_id=rider1 -F "text=aaj swiggy se 1400 aaye"
curl.exe -X POST http://localhost:3000/process -F user_id=rider1 -F "text=haan"
curl.exe -X POST http://localhost:3000/process -F user_id=rider1 -F audio=@note1.ogg
curl.exe -X POST http://localhost:3000/speak -F "text=Aaj ka profit 1150 rupaye hai" -F language=hi-IN -o reply.ogg
```

(On bash use `curl` instead of `curl.exe`.) `/process` returns JSON: `transcript`, `detected_language`,
`normalized_text`, `entries`, `reply_english`, `reply_user_language`, `intent`, `warnings`, and, for a voice
reply, `reply_audio_base64`. A `400` means a bad request (empty input, unreadable or too-long audio); a `502`
means a speech or voice service is down.

## 7. Command-line tools

All run from the repository root with `uv run python scripts/<name>.py`.

| Script | What it does |
|---|---|
| `check_llm.py` | Two real model calls per provider in `BRAIN`: picks the right action, and rewords a reply without changing a number. **Run this first after setting a key.** `--provider sarvam` for one. Exits non-zero on failure. |
| `transcribe_cli.py note1.ogg` | Speech-to-text only; prints the transcript and language. |
| `speak_cli.py "text" --lang hi-IN -o reply` | Text-to-speech; writes `reply.ogg` and `reply.wav`. |
| `process_cli.py note1.ogg` or `process_cli.py "text"` | The whole pipeline once; prints the JSON. In-memory database. |
| `chat_cli.py` | Multi-turn terminal chat so confirmations work. `--debug` shows normalised text; `--fresh` empties the database; `--voice` also writes `data/last_reply.wav`. A path to an audio file sends a voice note. `/quit` leaves. |
| `evaluate.py` | Scores the pipeline on 51 labelled transcripts (section 9). |

Audio formats accepted: ogg, wav, mp3, m4a and anything ffmpeg can read, up to `MAX_AUDIO_SECONDS` (60).
Keep the audio file in the folder you run the command from, or give its path.

## 8. Automated tests

```bash
uv run pytest -q                        # whole suite, about 4 seconds, no network, no keys
uv run pytest tests/test_brain.py -q    # one file
uv run pytest -k "confirm" -q           # by name
uv run pytest -x -q                     # stop at the first failure
```

The suite (586 tests at the time of writing) is isolated from your `.env`: `tests/conftest.py` forces mock
speech and voice and `BRAIN=none`, so it never calls a real service and never spends money. It covers audio
handling, spoken-amount and script normalisation, every action and confirmation flow, the model guards
(invented numbers, unknown actions, another rider's id), provider configuration and failover, migrations,
the web page, `/status` (no key ever appears) and the Docker files' ports and defaults.

Test a different Python with `uv run --python 3.13 pytest -q`.

What the tests do **not** prove: that Sarvam's live behaviour matches the mocks (use `check_llm.py`), or that
the Docker image builds (use section 4).

## 9. Measuring accuracy (evaluation)

```bash
uv run python scripts/evaluate.py --oracle           # proves the scorer itself; no model call
uv run python scripts/evaluate.py                    # real measurement with the model in BRAIN (costs a little)
uv run python scripts/evaluate.py --lang Hindi       # English, Hinglish, Hindi or Tamil
uv run python scripts/evaluate.py --audio tests/eval/audio   # use <case-id>.ogg clips so speech-to-text is scored too
uv run python scripts/evaluate.py --json report.json
```

It reports amount, type, category and platform accuracy, completeness and intent, split by language and by
clean versus noisy input, plus stage latency and every failure with its likely cause. It exits non-zero when
clean amount accuracy is below `--target` (default 95). `--oracle` answers from the labels, so a perfect
oracle score says nothing about the model; only the run without it does. One known gap: English number
words ("two fifty") are not converted (`en-13`).

## 10. A full manual check, in order

1. `uv run pytest -q` passes.
2. `uv run python scripts/check_llm.py` prints PASS for `sarvam` (needs the key).
3. `uv run python scripts/transcribe_cli.py note1.ogg` prints a sensible transcript (needs a recording).
4. `uv run python scripts/chat_cli.py --debug`: send `aaj swiggy se 1400 aaye`, then `haan`, then
   `aaj kitna kamaya`. Expect a confirmation, "Saved", then a total of ₹1,400. Then `swiggy ka 50 penalty
   laga`, `haan`, `petrol 250`, `haan`, and `aaj ka hisaab`: expect income ₹1,400, expenses ₹300 (fuel ₹250,
   penalty ₹50), profit ₹1,100.
5. `uv run uvicorn app.main:app --port 3000`, open the page, repeat step 4 by typing and by voice.
6. `docker compose up --build`, open <http://localhost:3000>, confirm `docker compose ps` shows `healthy`.
7. Delete `.env`, run `docker compose up --build` again, and confirm the page loads with the key banner (the
   "no keys needed" requirement).

## 11. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Banner says the model key is not set | `BRAIN` is `none` or `SARVAM_API_KEY` is empty. Set both in `.env` and restart (in Docker: `docker compose up --force-recreate`). |
| Changed `.env`, nothing changed | Restart the server; Docker needs `--force-recreate`. |
| Every message answers "I could not understand" | Run `check_llm.py`. Look for an auth error (wrong key), an empty reply (raise or lower reasoning, section 3), or a model refusing. Logs show the reason. |
| Reply stays in English for a Hinglish message | Run `check_llm.py`; it fails when the reworded reply equals the English. Make sure you pulled the latest code. |
| `400` on audio | Empty, silent, unreadable or longer than 60 seconds. |
| `502` from `/process` or `/speak` | The speech or voice service rejected the call or is down; the message includes the reason. |
| `curl` behaves oddly on Windows | Use `curl.exe`. |
| `Port 3000 already in use` | Stop the other process, or change the left side of `"3000:3000"` in `docker-compose.yml` (or `--port` for uvicorn). |
| Tests behave differently on your machine | They should not read `.env`; make sure you are on the latest commit and run `uv sync`. |
| Docker build fails at `uv sync --frozen` | `uv.lock` is out of date: run `uv lock`, commit it, rebuild. |

**Logs.** Each pipeline stage logs its latency (`stage=stt latency_ms=...`) and each message logs a summary
line. Set `LOG_LEVEL=DEBUG` for more. In Docker: `docker compose logs -f munshi`.

**Reset the data.** uv: delete `data/munshi.db` (and `data/chat.db` for the chat tool). Docker:
`docker compose down -v`.

**Inspect the data.** `uv run python -c "import sqlite3; c=sqlite3.connect('data/munshi.db'); print(c.execute('select * from transactions').fetchall())"`
(tables: `users`, `transactions`, `targets`, `pending`, `messages`).

## 12. Known limits

- The Docker image has not been built by the author's tooling (no daemon was available); its steps were
  replayed outside Docker. Section 4 is the real test.
- Sarvam pricing is reported inconsistently, and parallel tool calls are not enforced on `sarvam-105b`; the app
  caps them itself.
- The evaluation has never been run against the live model; the numbers are yours to produce.
- WhatsApp is not built; the web page and `POST /process` are the interface.
- Sarvam text-to-speech: the exact language field name is retried both ways, and how `₹1,400` is read aloud
  has not been listened to.
