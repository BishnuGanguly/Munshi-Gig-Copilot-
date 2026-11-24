"""Audio preparation. Step 1 is a passthrough; Step 2 adds ffmpeg conversion to 16 kHz mono WAV."""


def prepare_audio(raw: bytes, filename: str | None = None) -> bytes:
    if not raw:
        raise ValueError("Empty audio file")
    return raw
