"""End-to-end pipeline.

    audio or text
      -> ffmpeg clean-up            app/audio/
      -> speech-to-text             app/stt/            (Sarvam reports the language of the audio)
      -> digits and platform names  app/text/
      -> the model picks an action, our code runs it and computes every number
      -> the model rewords our English reply in the rider's language        app/core/brain.py
      -> voice note                 app/tts/

The rider never picks a language. Every stage is logged with its latency.
"""
import base64
import json
import logging
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field

from app.audio import prepare_audio
from app.core import brain, db
from app.stt import get_stt
from app.tts.service import speak

log = logging.getLogger("munshi.pipeline")

VOICE_MODES = ("auto", "always", "never")


@dataclass
class ProcessResult:
    transcript: str                 # what speech-to-text heard, or the text as typed
    detected_language: str          # BCP-47; from the audio, or from the language the reply was written in
    normalized_text: str            # amounts as digits, platform names canonical - what the model read
    entries: list = field(default_factory=list)
    reply_english: str = ""         # our sentence; every figure in it came from SQL
    reply_user_language: str = ""    # the same sentence in the rider's language
    warnings: list = field(default_factory=list)
    intent: str = "unknown"
    reply_audio: bytes | None = field(default=None, repr=False)   # OGG/Opus voice note, when one was made

    def to_dict(self) -> dict:
        d = asdict(self)
        audio = d.pop("reply_audio")
        d["reply_audio_mime"] = "audio/ogg; codecs=opus" if audio else None
        d["reply_audio_base64"] = base64.b64encode(audio).decode() if audio else None
        return d


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
            text: str | None = None, voice: str = "auto") -> ProcessResult:
    """voice: "auto" = answer a voice note with a voice note (typed text gets text only),
    "always", or "never"."""
    if audio is None and not (text and text.strip()):
        raise ValueError("Provide either an audio file or non-empty text")
    if voice not in VOICE_MODES:
        raise ValueError(f"voice must be one of {', '.join(VOICE_MODES)}")

    timings: dict[str, int] = {}
    t0 = time.perf_counter()
    db.ensure_user(conn, user_id)

    audio_language = None
    if audio is not None:
        with stage("audio", timings):
            wav = prepare_audio(audio, filename)
        with stage("stt", timings):
            tr = get_stt().transcribe(wav)
        transcript, audio_language = tr.text, tr.language
    else:
        transcript = text.strip()

    with stage("brain", timings):
        result = brain.handle(conn, user_id, transcript)
    outcome = result.outcome
    warnings = list(result.warnings)

    # Trust the audio's own language over the one inferred from the written reply.
    language = audio_language or result.reply_language
    speak_language = brain.speakable(audio_language or result.reply_language)

    reply_audio = None
    if voice == "always" or (voice == "auto" and audio is not None):
        with stage("voice", timings):
            reply_audio, w = speak(result.reply_user, speak_language)
            warnings += w

    total_ms = int((time.perf_counter() - t0) * 1000)
    db.log_message(conn, user_id, transcript=transcript, detected_language=language,
                   normalized_text=result.normalized_text, reply_english=outcome.reply,
                   reply_user_language=result.reply_user, latency_ms=total_ms, intent=outcome.intent,
                   actions_json=json.dumps(outcome.actions, ensure_ascii=False, default=str))
    log.info("user=%s language=%s total_ms=%d timings=%s warnings=%s", user_id, language, total_ms,
             timings, warnings)

    return ProcessResult(transcript, language, result.normalized_text, outcome.entries, outcome.reply,
                         result.reply_user, warnings, outcome.intent, reply_audio)
