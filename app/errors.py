"""Error types. ValueError subclasses map to HTTP 400, ServiceUnavailableError to 502."""


class AudioError(ValueError):
    """The uploaded audio can't be used (empty, silent, too long, undecodable)."""


class UnclearAudioError(ValueError):
    """Speech recognition returned nothing usable or low confidence."""


class ServiceUnavailableError(RuntimeError):
    """An external service (speech or translation provider) failed."""


class SttUnavailableError(ServiceUnavailableError):
    """Every configured speech-to-text provider failed (network, auth, quota)."""


class TranslateUnavailableError(ServiceUnavailableError):
    """Every configured translation provider failed (network, auth, quota)."""
