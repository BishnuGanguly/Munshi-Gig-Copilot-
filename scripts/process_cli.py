"""Run the whole pipeline once and print the JSON result.

    uv run python scripts/process_cli.py note1.ogg            # a voice note
    uv run python scripts/process_cli.py "aaj swiggy se 1400 aaye"   # typed text

Uses whatever providers your .env selects. Data is kept in memory only."""
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import pipeline  # noqa: E402
from app.config import get_settings  # noqa: E402  (loads .env)
from app.core import db  # noqa: E402
from app.errors import ServiceUnavailableError  # noqa: E402


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    get_settings()
    logging.basicConfig(level="WARNING")
    arg = sys.argv[1]
    conn = db.connect(":memory:")
    try:
        path = Path(arg)
        if path.is_file():
            result = pipeline.process(conn, "cli", audio=path.read_bytes(), filename=path.name)
        else:
            result = pipeline.process(conn, "cli", text=arg)
    except (ValueError, ServiceUnavailableError) as exc:
        print(f"ERROR: {exc}")
        return 1
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
