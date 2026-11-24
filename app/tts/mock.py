"""Mock TTS: a short beep whose length follows the text. Zero cost, no key; for tests and plumbing checks."""
import io
import math
import struct
import wave


class MockTts:
    name = "mock"

    def synthesize(self, text: str, lang: str) -> bytes:
        rate = 16000
        seconds = min(5.0, max(0.3, 0.05 * len(text)))
        frames = b"".join(struct.pack("<h", int(9000 * math.sin(2 * math.pi * 440 * i / rate)))
                          for i in range(int(rate * seconds)))
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(frames)
        return buf.getvalue()
