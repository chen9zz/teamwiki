import logging
import time
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api import auth, jobs, pages, workspaces
from app.api.deps import DB
from app.core.cache import cache_client
from app.core.config import settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging

configure_logging()
logger = logging.getLogger("teamwiki.requests")
app = FastAPI(
    title="TeamWiki",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    description=(
        "Tenant-scoped collaborative knowledge base. Sign in using /api/auth/login; "
        "the HttpOnly session cookie authenticates subsequent API calls. "
        "Page mutations require the currently observed version; stale saves return 409."
    ),
)
register_error_handlers(app)


@app.middleware("http")
async def browser_security_and_request_log(request: Request, call_next):
    request_id = str(uuid4())
    request.state.request_id = request_id
    start = time.perf_counter()
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        request_origin = str(request.base_url).rstrip("/")
        allowed = {request_origin, *settings.allowed_origins}
        if (origin is not None and origin.rstrip("/") not in allowed) or request.headers.get(
            "sec-fetch-site"
        ) == "cross-site":
            response = JSONResponse(status_code=403, content={"detail": "Untrusted request origin"})
        else:
            response = await call_next(request)
    else:
        response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    # Session/document responses must not be persisted by browser or shared proxies.
    response.headers["Cache-Control"] = "no-store"
    route = request.scope.get("route")
    logger.info(
        "request_completed",
        extra={
            "request_id": request_id,
            "method": request.method,
            "route": route.path if route else "unmatched",
            "status": response.status_code,
            "duration_ms": round((time.perf_counter() - start) * 1000, 2),
        },
    )
    return response


@app.get("/api/health", tags=["health"])
async def health():
    return {"status": "ok"}


@app.get("/api/ready", tags=["health"])
async def readiness(db: DB):
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        logger.exception("readiness_database_failed")
        await db.rollback()
        return JSONResponse(status_code=503, content={"database": "unavailable", "cache": "unknown"})
    try:
        async with cache_client() as redis:
            await redis.ping()
        cache = "ok"
    except Exception:
        cache = "degraded"
    return {"database": "ok", "cache": cache}


app.include_router(auth.router, prefix="/api")
app.include_router(workspaces.router, prefix="/api")
app.include_router(pages.router, prefix="/api")

app.include_router(jobs.router, prefix="/api")
