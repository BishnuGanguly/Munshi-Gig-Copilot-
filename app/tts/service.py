"""Voice replies: pick the text and language to speak, synthesise, convert to a WhatsApp voice note.

Never raises: if anything fails the caller still has the text reply and gets a warning back."""
import logging
import os

from app.audio import to_voice_note
from app.errors import AudioError, ServiceUnavailableError
from app.translate.languages import HINGLISH, is_english
from .base import TtsProvider

log = logging.getLogger("munshi.tts")

# Languages Bulbul speaks (Sarvam's 11). Anything else is answered in English.
TTS_LANGUAGES = {"en-IN", "hi-IN", "bn-IN", "gu-IN", "kn-IN", "ml-IN", "mr-IN", "od-IN", "pa-IN", "ta-IN", "te-IN"}


def get_tts() -> TtsProvider:
    name = os.getenv("TTS_PROVIDER", "mock")
    if name == "mock":
        from .mock import MockTts
        return MockTts()
    if name == "sarvam":
        from .sarvam import SarvamTts
        return SarvamTts()
    raise ValueError(f"Unknown TTS_PROVIDER: {name!r} (use sarvam or mock)")


def max_chars() -> int:
    return int(os.getenv("VOICE_MAX_CHARS", "400"))


def make_voice_note(reply_user: str, reply_en: str, language: str, translated: bool, lang_service,
                    tts: TtsProvider | None = None) -> tuple[bytes | None, list[str]]:
    """Return (ogg_opus_bytes | None, warnings).

    - Hinglish replies are written in Roman script for reading; for speaking they are re-rendered in Hindi
      script, which the voice reads far better.
    - If the reply could not be translated, or the language has no voice, the English reply is spoken.
    - Very long replies are not voiced (cost and listening time): the text reply stands.
    """
    warnings: list[str] = []
    text, lang = reply_user, language
    if not translated or is_english(language):
        text, lang = reply_en, "en-IN"
    elif language == HINGLISH:
        spoken, w = lang_service.from_english(reply_en, "hi-IN")
        warnings += w
        text, lang = (spoken, "hi-IN") if "reply_not_translated" not in w else (reply_en, "en-IN")
    elif language not in TTS_LANGUAGES:
        text, lang = reply_en, "en-IN"
    if len(text) > max_chars():
        return None, warnings + ["voice_skipped_long_reply"]
    try:
        provider = tts or get_tts()
        ogg = to_voice_note(provider.synthesize(text, lang))
    except (ServiceUnavailableError, AudioError, ValueError, RuntimeError) as exc:
        log.warning("voice reply failed: %s", exc)
        return None, warnings + [f"voice_reply_failed: {exc}"]
    return ogg, warnings


def speak(text: str, language: str, tts: TtsProvider | None = None) -> tuple[bytes | None, list[str]]:
    """Synthesise a reply that is ALREADY in the language to speak (the LLM-first path).

    No language juggling and no translation service: the brain's renderer produced the text and its code.
    Returns (ogg_opus_bytes | None, warnings) and never raises - the text reply always stands.
    """
    if not text.strip():
        return None, []
    if len(text) > max_chars():
        return None, ["voice_skipped_long_reply"]
    try:
        provider = tts or get_tts()
        return to_voice_note(provider.synthesize(text, language)), []
    except (ServiceUnavailableError, AudioError, ValueError, RuntimeError) as exc:
        log.warning("voice reply failed: %s", exc)
        return None, [f"voice_reply_failed: {exc}"]
