"""Free, offline fallback using faster-whisper. Optional dependency: pip install faster-whisper.
Whisper is weaker than Sarvam on code-mixed Indian speech; use it as a backup."""
import math
import os
import tempfile

from app.errors import SttUnavailableError
from .base import Transcript

_BCP47 = {"hi": "hi-IN", "en": "en-IN", "ta": "ta-IN", "kn": "kn-IN", "bn": "bn-IN", "te": "te-IN",
          "mr": "mr-IN", "gu": "gu-IN", "ml": "ml-IN", "pa": "pa-IN", "ur": "ur-IN"}


class WhisperStt:
    name = "faster_whisper"

    def __init__(self, model_size: str | None = None) -> None:
        self.model_size = model_size or os.getenv("WHISPER_MODEL", "small")
        self._model = None

    def _load(self):
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise SttUnavailableError("faster-whisper is not installed (pip install faster-whisper).") from exc
            self._model = WhisperModel(self.model_size, device="cpu", compute_type="int8")
        return self._model

    def transcribe(self, wav_bytes: bytes) -> Transcript:
        model = self._load()
        fd, path = tempfile.mkstemp(suffix=".wav")  # closed before use: Windows can't share open temp files
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(wav_bytes)
            segments, info = model.transcribe(path, task="transcribe", vad_filter=True)
            segments = list(segments)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        text = " ".join(s.text.strip() for s in segments).strip()
        conf = math.exp(sum(s.avg_logprob for s in segments) / len(segments)) if segments else 0.0
        return Transcript(text=text, language=_BCP47.get(info.language, info.language), confidence=round(conf, 3))
