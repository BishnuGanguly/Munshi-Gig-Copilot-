from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse

from app import pipeline
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


@router.post("/process")
async def process(
    request: Request,
    user_id: str = Form(...),
    text: str | None = Form(None),
    audio: UploadFile | None = File(None),
    voice: str = Form("auto"),
) -> dict:
    raw = await audio.read() if audio is not None else None
    try:
        result = pipeline.process(request.app.state.db, user_id, audio=raw,
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
