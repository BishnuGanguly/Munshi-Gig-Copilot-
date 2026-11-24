"""Central configuration, read from environment variables (.env supported if python-dotenv is installed)."""
import os

try:  # optional dependency; .env loading is a convenience only
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


class Settings:
    def __init__(self) -> None:
        self.stt_provider = os.getenv("STT_PROVIDER", "mock")
        self.translate_provider = os.getenv("TRANSLATE_PROVIDER", "mock")
        self.db_path = os.getenv("DB_PATH", "data/munshi.db")
        self.log_level = os.getenv("LOG_LEVEL", "INFO")


def get_settings() -> Settings:
    return Settings()
