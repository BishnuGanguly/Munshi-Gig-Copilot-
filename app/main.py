import logging
import threading

from fastapi import FastAPI

from app.api.routes import router
from app.config import get_settings
from app.core import db
from app.llm import get_provider


def create_app(db_path: str | None = None) -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    app = FastAPI(title="Munshi Gig Copilot")
    # Fail at startup, not on the first rider's message, if BRAIN names a provider that does not exist.
    # Nothing is contacted and no key is needed: building a provider is cheap.
    provider = get_provider()
    logging.getLogger("munshi").info("model provider: %s", provider.name if provider else "none (BRAIN=none)")
    # Migrate once at startup. Requests then open their own connection (see app/api/routes.py), so a slow
    # speech or model call for one rider never holds up another.
    app.state.db_path = db_path or settings.db_path
    app.state.db = db.connect(app.state.db_path)
    app.state.db_lock = threading.Lock()
    app.include_router(router)
    return app


app = create_app()
