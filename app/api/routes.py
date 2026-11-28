from contextlib import contextmanager
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse

from app import pipeline
from app.core import db
from app.status import status as service_status
from app.audio import to_voice_note
from app.tts import get_tts
from app.tts.service import TTS_LANGUAGES
from app.errors import ServiceUnavailableError

router = APIRouter()
INDEX_HTML = (Path(__file__).resolve().parent.parent / "web" / "index.html").read_text(encoding="utf-8")


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def index() -> HTMLResponse:
    """The app's main screen: talk or type to Munshi, read and hear the reply."""
    return HTMLResponse(INDEX_HTML)


@router.get("/status")
def status() -> dict:
    """Which services are configured, for the key banner. Never contains a secret."""
    return service_status()


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@contextmanager
def request_db(app):
    """A connection for one request. A file database gets a fresh connection per request (SQLite in WAL mode
    lets them run side by side); an in-memory one exists only on its single connection, so requests take
    turns on it - that is only ever the test suite."""
    if app.state.db_path == ":memory:":
        with app.state.db_lock:
            yield app.state.db
        return
    conn = db.connect(app.state.db_path, migrate=False)
    try:
        yield conn
    finally:
        conn.close()


# A plain `def`, not `async def`: the pipeline makes blocking network calls (speech, model, voice), and FastAPI
# runs a plain function in its thread pool. Inside an `async def` those calls would stall every other request.
@router.post("/process")
def process(
    request: Request,
    user_id: str = Form(...),
    text: str | None = Form(None),
    audio: UploadFile | None = File(None),
    voice: str = Form("auto"),
) -> dict:
    raw = audio.file.read() if audio is not None else None
    try:
        with request_db(request.app) as conn:
            result = pipeline.process(conn, user_id, audio=raw,
                                      filename=audio.filename if audio else None, text=text, voice=voice)
    except ValueError as exc:  # AudioError / UnclearAudioError are ValueErrors
        raise HTTPException(status_code=400, detail=str(exc))
    except ServiceUnavailableError as exc:
        raise HTTPException(status_code=502, detail=f"A speech service is unavailable: {exc}")
    return result.to_dict()


@router.post("/speak")
def speak(text: str = Form(...), language: str = Form("en-IN")) -> Response:
    """Turn text into a voice note (OGG/Opus) with the configured TTS provider. For testing voices."""
    if language not in TTS_LANGUAGES:
        raise HTTPException(status_code=400, detail=f"language must be one of {', '.join(sorted(TTS_LANGUAGES))}")
    try:
        ogg = to_voice_note(get_tts().synthesize(text, language))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ServiceUnavailableError as exc:
        raise HTTPException(status_code=502, detail=f"Voice service unavailable: {exc}")
    return Response(content=ogg, media_type="audio/ogg")
