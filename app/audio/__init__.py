"""Audio preparation: any common format (OGG/Opus voice notes, MP3, WAV, M4A) -> clean 16 kHz mono WAV.

Steps: decode -> reject empty / too long / silent -> trim silence + normalise volume.
"""
import array
import io
import logging
import math
import os
import shutil
import subprocess
import tempfile
import wave

from app.errors import AudioError

log = logging.getLogger("munshi.audio")
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


def ffmpeg_path() -> str:
    """Find ffmpeg: FFMPEG_BIN env var, then the binary bundled by `imageio-ffmpeg` (installed by uv),
    then whatever `ffmpeg` is on PATH."""
    explicit = os.getenv("FFMPEG_BIN")
    if explicit:
        return explicit
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # package missing or no bundled binary for this platform
        return shutil.which("ffmpeg") or "ffmpeg"


def _run_ffmpeg(raw: bytes, out_args: list[str], audio_filter: str | None = None, empty_ok: bool = False) -> bytes:
    # Input goes through a temp file, not a pipe: phone M4A/MP4 files often keep their index at the end
    # and can't be decoded from a stream. The file is closed before ffmpeg opens it (Windows can't share
    # an open NamedTemporaryFile with another process).
    fd, src_path = tempfile.mkstemp(suffix=".bin")
    try:
        with os.fdopen(fd, "wb") as src:
            src.write(raw)
        cmd = [ffmpeg_path(), "-v", "error", "-i", src_path, "-vn"]
        if audio_filter:
            cmd += ["-af", audio_filter]
        cmd += [*out_args, "pipe:1"]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=_FFMPEG_TIMEOUT)
        except FileNotFoundError as exc:
            raise RuntimeError("ffmpeg not found. Run `uv sync` (installs a bundled copy) or set FFMPEG_BIN in .env.") from exc
        except subprocess.TimeoutExpired as exc:
            raise AudioError("Audio took too long to process. Please send a shorter note.") from exc
    finally:
        try:
            os.unlink(src_path)
        except OSError:
            pass
    if proc.returncode != 0:
        log.warning("ffmpeg failed (code %s): %s", proc.returncode, proc.stderr.decode(errors="replace")[:500])
    if proc.returncode != 0 or (not proc.stdout and not empty_ok):
        raise AudioError("Could not read this audio file. Please record again.")
    return proc.stdout


def _ffmpeg(raw: bytes, audio_filter: str | None = None, empty_ok: bool = False) -> bytes:
    pcm = _run_ffmpeg(raw, ["-ar", str(SAMPLE_RATE), "-ac", "1", "-f", "s16le"], audio_filter, empty_ok)
    return _wrap_pcm(pcm)


def _wrap_pcm(pcm: bytes, rate: int = SAMPLE_RATE) -> bytes:
    """Wrap raw 16-bit mono PCM in a WAV container with a correct header."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


# ------------------------------------------------------------------ output side (voice replies)
VOICE_RATE = 24000


def to_pcm_wav(raw: bytes, rate: int = VOICE_RATE) -> bytes:
    """Any audio -> 16-bit mono WAV at `rate` (used to join text-to-speech chunks)."""
    return _wrap_pcm(_run_ffmpeg(raw, ["-ar", str(rate), "-ac", "1", "-f", "s16le"]), rate)


def concat_wavs(wavs: list[bytes], rate: int = VOICE_RATE) -> bytes:
    if len(wavs) == 1:
        return to_pcm_wav(wavs[0], rate)
    pcm = b""
    for w in wavs:
        with wave.open(io.BytesIO(to_pcm_wav(w, rate))) as f:
            pcm += f.readframes(f.getnframes())
    return _wrap_pcm(pcm, rate)


def to_voice_note(audio: bytes) -> bytes:
    """Any audio -> OGG/Opus mono, the format WhatsApp shows as a playable voice note."""
    return _run_ffmpeg(audio, ["-ac", "1", "-ar", str(VOICE_RATE), "-c:a", "libopus", "-b:a", "24k",
                               "-application", "voip", "-f", "ogg"])


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
