import logging

from fastapi import FastAPI

from app.api.routes import router
from app.config import get_settings
from app.core import db


def create_app(db_path: str | None = None) -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    app = FastAPI(title="Munshi Gig Copilot")
    app.state.db = db.connect(db_path or settings.db_path)
    app.include_router(router)
    return app


app = create_app()
