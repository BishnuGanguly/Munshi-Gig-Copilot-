"""Turn text into speech with the configured TTS provider and save it.

    uv run python scripts/speak_cli.py "Aaj ka profit 1150 rupaye hai" --lang hi-IN
    uv run python scripts/speak_cli.py "Saved. Today's profit is ₹1,150." --lang en-IN -o reply

Writes <name>.ogg (the WhatsApp voice-note format) and <name>.wav (plays in any Windows player).
Set TTS_PROVIDER=sarvam and SARVAM_API_KEY in .env for a real voice; the default mock just beeps."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.audio import to_pcm_wav, to_voice_note  # noqa: E402
from app.config import get_settings  # noqa: E402  (loads .env)
from app.errors import AudioError, ServiceUnavailableError  # noqa: E402
from app.tts import get_tts  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text")
    ap.add_argument("--lang", default="en-IN", help="hi-IN, en-IN, ta-IN, te-IN, kn-IN, bn-IN, mr-IN ...")
    ap.add_argument("-o", "--out", default="reply", help="output name without extension (default: reply)")
    args = ap.parse_args()
    get_settings()
    try:
        provider = get_tts()
        wav = provider.synthesize(args.text, args.lang)
        ogg = to_voice_note(wav)
        Path(f"{args.out}.ogg").write_bytes(ogg)
        Path(f"{args.out}.wav").write_bytes(to_pcm_wav(wav))
    except (ServiceUnavailableError, AudioError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 1
    print(f"provider={provider.name} -> {args.out}.ogg ({len(ogg)} bytes) and {args.out}.wav")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
