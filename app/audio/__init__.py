"""Audio preparation: any common format (OGG/Opus voice notes, MP3, WAV, M4A) -> clean 16 kHz mono WAV.

Steps: decode -> reject empty / too long / silent -> trim silence + normalise volume.
"""
import array
import io
import math
import os
import subprocess
import tempfile
import wave

from app.errors import AudioError

SAMPLE_RATE = 16000
MAX_SECONDS = float(os.getenv("MAX_AUDIO_SECONDS", "60"))
SILENCE_RMS_THRESHOLD = float(os.getenv("SILENCE_RMS_THRESHOLD", "30"))  # on a 0..32767 scale
_FFMPEG_TIMEOUT = 60

# Trim leading silence and long (>1.5 s) pauses, then even out loudness (helps noisy, quiet phone recordings).
# Normalise first so a quiet recording isn't mistaken for silence by the trimmer.
_CLEAN_FILTER = (
    "dynaudnorm=f=150:g=15,"
    "silenceremove=start_periods=1:start_threshold=-45dB:"
    "stop_periods=-1:stop_threshold=-45dB:stop_duration=1.5"
)


def _ffmpeg(raw: bytes, audio_filter: str | None = None, empty_ok: bool = False) -> bytes:
    # Input goes through a temp file, not a pipe: phone M4A/MP4 files often keep their index at the end
    # and can't be decoded from a stream.
    with tempfile.NamedTemporaryFile(suffix=".bin") as src:
        src.write(raw)
        src.flush()
        cmd = ["ffmpeg", "-v", "error", "-i", src.name, "-vn"]
        if audio_filter:
            cmd += ["-af", audio_filter]
        cmd += ["-ar", str(SAMPLE_RATE), "-ac", "1", "-f", "s16le", "pipe:1"]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=_FFMPEG_TIMEOUT)
        except FileNotFoundError as exc:
            raise RuntimeError("ffmpeg is not installed. Install it (e.g. `apt install ffmpeg`).") from exc
        except subprocess.TimeoutExpired as exc:
            raise AudioError("Audio took too long to process. Please send a shorter note.") from exc
    if proc.returncode != 0 or (not proc.stdout and not empty_ok):
        raise AudioError("Could not read this audio file. Please record again.")
    return _wrap_pcm(proc.stdout)


def _wrap_pcm(pcm: bytes) -> bytes:
    """Wrap raw 16-bit mono PCM in a WAV container with a correct header."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    return buf.getvalue()


def wav_info(wav: bytes) -> tuple[float, float]:
    """Return (duration_seconds, rms) for a 16-bit mono WAV."""
    with wave.open(io.BytesIO(wav)) as w:
        frames = w.readframes(w.getnframes())
        rate = w.getframerate()
    samples = array.array("h")
    samples.frombytes(frames[: len(frames) // 2 * 2])
    if not samples:
        return 0.0, 0.0
    rms = math.sqrt(sum(s * s for s in samples) / len(samples))
    return len(samples) / rate, rms


def prepare_audio(raw: bytes, filename: str | None = None) -> bytes:
    if not raw:
        raise AudioError("Empty audio file. Please record again.")

    decoded = _ffmpeg(raw)
    duration, rms = wav_info(decoded)
    if duration <= 0.2:
        raise AudioError("Audio is empty. Please record again.")
    if duration > MAX_SECONDS:
        raise AudioError(f"Audio is too long ({duration:.0f}s). Please keep notes under {MAX_SECONDS:.0f} seconds.")
    if rms < SILENCE_RMS_THRESHOLD:
        raise AudioError("The audio is silent. Please speak closer to the phone and try again.")

    cleaned = _ffmpeg(decoded, _CLEAN_FILTER, empty_ok=True)
    cleaned_duration, _ = wav_info(cleaned)
    if cleaned_duration < 0.3:
        raise AudioError("No speech detected. Please speak and try again.")
    return cleaned
