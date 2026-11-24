from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from app import pipeline
from app.errors import ServiceUnavailableError

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.post("/process")
async def process(
    request: Request,
    user_id: str = Form(...),
    text: str | None = Form(None),
    audio: UploadFile | None = File(None),
) -> dict:
    raw = await audio.read() if audio is not None else None
    try:
        result = pipeline.process(request.app.state.db, user_id, audio=raw,
                                  filename=audio.filename if audio else None, text=text)
    except ValueError as exc:  # AudioError / UnclearAudioError are ValueErrors
        raise HTTPException(status_code=400, detail=str(exc))
    except ServiceUnavailableError as exc:
        raise HTTPException(status_code=502, detail=f"A language or speech service is unavailable: {exc}")
    return result.to_dict()
