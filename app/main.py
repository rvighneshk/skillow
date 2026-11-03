import logging
from contextlib import asynccontextmanager

import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import Base, SessionLocal, engine
from app.models import Tutorial
from app.routes import router
from app.services.meta_client import MetaError, public_error

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("skillow")
Base.metadata.create_all(bind=engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = SessionLocal()
    try:
        stale = db.query(Tutorial).filter(Tutorial.status.in_(("queued", "processing"))).all()
        for tutorial in stale:
            tutorial.status = "failed"
            tutorial.error = "Processing was interrupted by a server restart. Launch or upload it again."
        db.commit()
    finally:
        db.close()
    yield


app = FastAPI(title="Skillow", description="AI-powered practical learning for repair, cooking, crafts and more",
              lifespan=lifespan)


@app.exception_handler(MetaError)
@app.exception_handler(requests.RequestException)
async def ai_service_error(request: Request, exc: Exception):
    logger.warning("AI service error on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(status_code=502, content={"detail": public_error(exc)})


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "The server could not complete this request. Please try again."})


app.include_router(router)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.mount("/media", StaticFiles(directory=str(settings.storage_dir)), name="media")


@app.get("/health")
def health():
    return {"ok": True, "app": "Skillow", "meta_api_configured": bool(settings.model_api_key)}
