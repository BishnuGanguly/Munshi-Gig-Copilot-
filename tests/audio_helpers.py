import subprocess

from app.audio import ffmpeg_path


def make_audio(fmt: str = "wav", seconds: float = 2.0, source: str = "sine=frequency=440") -> bytes:
    """Generate test audio with ffmpeg. fmt: wav | ogg (Opus, like WhatsApp) | mp3 | m4a."""
    codec = {"wav": ["-ar", "16000", "-ac", "1", "-f", "wav"], "ogg": ["-c:a", "libopus", "-f", "ogg"], "mp3": ["-f", "mp3"],
             "m4a": ["-c:a", "aac", "-movflags", "frag_keyframe+empty_moov", "-f", "mp4"]}[fmt]
    cmd = [ffmpeg_path(), "-v", "error", "-f", "lavfi", "-i", source, "-t", str(seconds), *codec, "pipe:1"]
    return subprocess.run(cmd, capture_output=True, check=True).stdout


def silent_audio(seconds: float = 2.0) -> bytes:
    return make_audio("wav", seconds, source="anullsrc=r=16000:cl=mono")
