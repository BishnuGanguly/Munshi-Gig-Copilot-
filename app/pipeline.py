"""End-to-end pipeline:
audio/text -> transcript -> language -> normalise -> English -> parse -> English reply -> user's language.
The user never picks a language: it is detected from each message and the reply matches it.
Every stage is logged with its latency."""
import logging
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field

from app.audio import prepare_audio
from app.core import db
from app.core.parser import parse_english
from app.core.tools import handle_english
from app.stt import get_stt
from app.translate import get_language_service
from app.translate.languages import DEFAULT_LANGUAGE, detect_text_language, normalize_code

log = logging.getLogger("munshi.pipeline")


@dataclass
class ProcessResult:
    transcript: str
    detected_language: str
    normalized_text: str
    english_text: str
    entries: list = field(default_factory=list)
    reply_english: str = ""
    reply_user_language: str = ""
    warnings: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@contextmanager
def stage(name: str, timings: dict):
    start = time.perf_counter()
    try:
        yield
    finally:
        ms = int((time.perf_counter() - start) * 1000)
        timings[name] = ms
        log.info("stage=%s latency_ms=%d", name, ms)


def process(conn, user_id: str, audio: bytes | None = None, filename: str | None = None,
            text: str | None = None) -> ProcessResult:
    if audio is None and not (text and text.strip()):
        raise ValueError("Provide either an audio file or non-empty text")

    timings: dict[str, int] = {}
    t0 = time.perf_counter()
    db.ensure_user(conn, user_id)
    row = conn.execute("SELECT last_language FROM users WHERE id = ?", (user_id,)).fetchone()
    last_language = row["last_language"] if row else None

    if audio is not None:
        with stage("audio", timings):
            wav = prepare_audio(audio, filename)
        with stage("stt", timings):
            tr = get_stt().transcribe(wav)
        transcript = tr.text
        language = normalize_code(tr.language) or normalize_code(last_language) or DEFAULT_LANGUAGE
    else:
        transcript = text.strip()
        language = detect_text_language(transcript, last_language)

    lang = get_language_service()
    warnings: list[str] = []
    with stage("normalize", timings):
        normalized = lang.normalize(transcript)
    with stage("to_english", timings):
        english_text, w = lang.to_english(normalized, language)
        warnings += w
    with stage("parse", timings):
        entries = parse_english(english_text)
    with stage("reply", timings):
        reply_en = handle_english(english_text, entries)
    with stage("from_english", timings):
        reply_user, w = lang.from_english(reply_en, language)
        warnings += w

    total_ms = int((time.perf_counter() - t0) * 1000)
    db.ensure_user(conn, user_id, language)
    db.log_message(conn, user_id, transcript=transcript, detected_language=language, english_text=english_text,
                  reply_english=reply_en, reply_user_language=reply_user, latency_ms=total_ms)
    log.info("user=%s language=%s total_ms=%d timings=%s warnings=%s", user_id, language, total_ms, timings, warnings)

    return ProcessResult(transcript, language, normalized, english_text, entries, reply_en, reply_user, warnings)
