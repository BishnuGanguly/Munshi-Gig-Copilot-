import pytest

from app.audio import prepare_audio, wav_info
from app.errors import AudioError
from tests.audio_helpers import make_audio, silent_audio


@pytest.mark.parametrize("fmt", ["wav", "ogg", "mp3", "m4a"])
def test_formats_become_16k_mono_wav(fmt):
    wav = prepare_audio(make_audio(fmt, 2.0))
    duration, rms = wav_info(wav)
    assert wav[:4] == b"RIFF"
    assert 1.0 < duration < 3.0
    assert rms > 100


def test_empty_rejected():
    with pytest.raises(AudioError):
        prepare_audio(b"")


def test_garbage_rejected():
    with pytest.raises(AudioError):
        prepare_audio(b"not audio at all" * 100)


def test_silent_rejected():
    with pytest.raises(AudioError, match="silent|No speech"):
        prepare_audio(silent_audio())


def test_too_long_rejected(monkeypatch):
    monkeypatch.setattr("app.audio.MAX_SECONDS", 3.0)
    with pytest.raises(AudioError, match="too long"):
        prepare_audio(make_audio("wav", 5.0))


def test_quiet_audio_is_normalised():
    quiet = make_audio("wav", 2.0, source="sine=frequency=440,volume=0.05")
    _, rms_before = wav_info(quiet)
    _, rms_after = wav_info(prepare_audio(quiet))
    assert rms_after > rms_before * 1.5
