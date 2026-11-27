"""Error types. ValueError subclasses map to HTTP 400, ServiceUnavailableError to 502."""


class AudioError(ValueError):
    """The uploaded audio can't be used (empty, silent, too long, undecodable)."""


class UnclearAudioError(ValueError):
    """Speech recognition returned nothing usable or low confidence."""


class ServiceUnavailableError(RuntimeError):
    """An external service (speech or translation provider) failed."""


class SttUnavailableError(ServiceUnavailableError):
    """Every configured speech-to-text provider failed (network, auth, quota)."""



class TtsUnavailableError(ServiceUnavailableError):
    """Text-to-speech failed (network, auth, quota). Never blocks the text reply."""


class LlmUnavailableError(ServiceUnavailableError):
    """A model provider failed (network, auth, quota, malformed reply). Never fatal: the caller falls back
    to the next provider, or answers honestly that it did not understand."""
