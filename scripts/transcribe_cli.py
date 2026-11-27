"""Usage: python scripts/transcribe_cli.py path/to/audio.ogg

Runs the real audio cleanup + configured STT provider and prints the transcript and detected language."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.audio import prepare_audio, wav_info  # noqa: E402
from app.config import get_settings  # noqa: E402  (loads .env)
from app.errors import AudioError, SttUnavailableError, UnclearAudioError  # noqa: E402
from app.stt import get_stt  # noqa: E402


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    get_settings()
    raw = Path(sys.argv[1]).read_bytes()
    try:
        wav = prepare_audio(raw, sys.argv[1])
        duration, rms = wav_info(wav)
        print(f"cleaned audio: {duration:.1f}s, rms={rms:.0f}")
        tr = get_stt().transcribe(wav)
    except (AudioError, UnclearAudioError, SttUnavailableError) as exc:
        print(f"ERROR: {exc}")
        return 1
    print(f"language   : {tr.language}")
    print(f"confidence : {tr.confidence}")
    print(f"transcript : {tr.text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
