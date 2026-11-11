"""FoodBridge API entry point. Run with:  uvicorn app.main:app --reload"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.router import api_router
from app.config import settings
from app.database.session import SessionLocal, init_db
from app.errors import AppError
from app.services.expiry import expire_overdue

log = logging.getLogger("foodbridge")
logging.basicConfig(level=logging.INFO)


async def _expiry_loop():
    """Background task: every minute, mark overdue donations as EXPIRED."""
    while True:
        try:
            db = SessionLocal()
            try:
                changed = await asyncio.to_thread(expire_overdue, db)
                if changed:
                    log.info("Expired %s overdue donation(s)", changed)
            finally:
                db.close()
        except Exception:  # never let the loop die
            log.exception("Expiry check failed")
        await asyncio.sleep(settings.EXPIRY_CHECK_SECONDS)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    if settings.SECRET_KEY_SOURCE != "env":
        log.warning("SECRET_KEY is not set (or is a placeholder): using a generated development key stored in "
                    "backend/.dev_secret_key. Set a long random SECRET_KEY in backend/.env for real use.")
    task = asyncio.create_task(_expiry_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="FoodBridge API",
    description="Connecting surplus food with people who need it. "
                "FoodBridge facilitates redistribution; it does not guarantee food safety.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(CORSMiddleware, allow_origins=settings.CORS_ORIGINS, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])


# ---- Error handling: every error leaves the API as {"detail": "...", "code": "..."} ----

@app.exception_handler(AppError)
async def handle_app_error(_request: Request, exc: AppError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message, "code": exc.code})


@app.exception_handler(StarletteHTTPException)
async def handle_http_error(_request: Request, exc: StarletteHTTPException):
    """Unknown routes (404), wrong methods (405) etc. use the same {detail, code} shape as every other error."""
    codes = {404: "not_found", 405: "method_not_allowed"}
    return JSONResponse(status_code=exc.status_code, headers=getattr(exc, "headers", None),
                        content={"detail": str(exc.detail), "code": codes.get(exc.status_code, "http_error")})


@app.exception_handler(RequestValidationError)
async def handle_validation_error(_request: Request, exc: RequestValidationError):
    errors = []
    for err in exc.errors():
        field = ".".join(str(p) for p in err.get("loc", []) if p not in ("body", "query", "path"))
        message = str(err.get("msg", "Invalid value")).replace("Value error, ", "")
        errors.append({"field": field, "message": message})
    summary = errors[0]["message"] if len(errors) == 1 else "Some fields need your attention."
    return JSONResponse(status_code=422, content={"detail": summary, "code": "validation_error", "errors": errors})


@app.exception_handler(SQLAlchemyError)
async def handle_db_error(_request: Request, exc: SQLAlchemyError):
    log.exception("Database error: %s", exc)          # details go to the server log, not the user
    return JSONResponse(status_code=500, content={"detail": "A database error occurred. Please try again.",
                                                  "code": "database_error"})


@app.exception_handler(Exception)
async def handle_unexpected(_request: Request, exc: Exception):
    log.exception("Unhandled error: %s", exc)
    return JSONResponse(status_code=500, content={"detail": "Something went wrong on our side. Please try again.",
                                                  "code": "server_error"})


@app.get("/api/health", tags=["Health"])
def health():
    """Liveness plus a real database round-trip (HTTP 503 if the database cannot be reached)."""
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        log.exception("Health check: database unreachable")
        return JSONResponse(status_code=503, content={"status": "unavailable", "app": settings.APP_NAME,
                                                      "database": "unreachable"})
    return {"status": "ok", "app": settings.APP_NAME, "database": "ok"}


app.include_router(api_router)
