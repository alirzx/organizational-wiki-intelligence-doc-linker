from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.core.config import get_settings
from app.runtime import get_storage, get_vector

settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        get_storage().ensure_bucket()
    except Exception:
        logger.exception("MinIO startup validation failed")
    try:
        get_vector().ensure_collection()
    except Exception:
        logger.exception("Qdrant collection initialization failed")
    yield


app = FastAPI(
    title="Organizational Wiki Intelligence - Document Linker",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(router)
