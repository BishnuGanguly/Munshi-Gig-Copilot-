"""Chat with the whole pipeline in your terminal (multi-turn, so confirmations work).

    uv run python scripts/chat_cli.py            # type messages; a path to an audio file sends a voice note
    uv run python scripts/chat_cli.py --debug    # also show language, normalised text and English text
    uv run python scripts/chat_cli.py --fresh    # start with an empty database
    uv run python scripts/chat_cli.py --voice    # answer every message with a voice note: data/last_reply.wav

Uses the providers in your .env. Data is kept in data/chat.db. Type /quit to leave."""
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import pipeline  # noqa: E402
from app.config import get_settings  # noqa: E402  (loads .env)
from app.audio import to_pcm_wav  # noqa: E402
from app.core import db  # noqa: E402
from app.errors import ServiceUnavailableError  # noqa: E402


def main() -> int:
    get_settings()
    logging.basicConfig(level="WARNING")
    debug = "--debug" in sys.argv
    voice = "always" if "--voice" in sys.argv else "auto"
    path = ROOT / "data" / "chat.db"
    if "--fresh" in sys.argv and path.exists():
        path.unlink()
    conn = db.connect(str(path))
    from app.core.brain import enabled
    if not enabled():
        print("NOTE: BRAIN is not set, so nothing can be understood. See .env.example.")
    print("Munshi chat. Try: Got 1400 from Swiggy and spent 250 on petrol   (/quit to leave)")
    while True:
        try:
            line = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line == "/quit":
            return 0
        try:
            f = Path(line.strip('"'))
            if f.is_file():
                result = pipeline.process(conn, "cli", audio=f.read_bytes(), filename=f.name, voice=voice)
            else:
                result = pipeline.process(conn, "cli", text=line, voice=voice)
        except (ValueError, ServiceUnavailableError) as exc:
            print(f"munshi> ERROR: {exc}")
            continue
        if debug:
            print(f"   [{result.detected_language}] heard: {result.transcript!r}\n   normalised: {result.normalized_text!r}"
                  f"\n   english: {result.english_text!r}\n   warnings: {result.warnings}")
        print(f"munshi> {result.reply_user_language}")
        if result.reply_audio:
            out = ROOT / "data" / "last_reply.wav"
            out.write_bytes(to_pcm_wav(result.reply_audio))
            print(f"   (voice reply saved: {out})")


if __name__ == "__main__":
    raise SystemExit(main())
