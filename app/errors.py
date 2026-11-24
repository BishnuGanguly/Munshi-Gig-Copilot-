"""Error types. ValueError subclasses map to HTTP 400, SttUnavailableError to 502."""


class AudioError(ValueError):
    """The uploaded audio can't be used (empty, silent, too long, undecodable)."""


class UnclearAudioError(ValueError):
    """Speech recognition returned nothing usable or low confidence."""


class SttUnavailableError(RuntimeError):
    """Every configured speech-to-text provider failed (network, auth, quota)."""
