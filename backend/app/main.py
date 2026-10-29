import builtins
import logging
import socket
import time
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, TimeoutError

from app.api import auth, jobs, pages, workspaces
from app.api.deps import DB
from app.core.cache import cache_client, close_cache
from app.core.config import settings
from app.core.errors import fulltext_limit_response, register_error_handlers
from app.core.logging import configure_logging
from app.core.metrics import (
    DB_FAILURES,
    HTTP_DURATION,
    HTTP_INFLIGHT,
    HTTP_REJECTED,
    HTTP_REQUESTS,
    close_metrics,
    render_metrics,
)
from app.db.session import capacity_failure_reason, engine
from app.services.search import warm_search_tokenizer

configure_logging()
logger = logging.getLogger("teamwiki.requests")


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await warm_search_tokenizer()
        yield
    finally:
        try:
            await close_cache()
        finally:
            try:
                await engine.dispose()
            finally:
                close_metrics()


app = FastAPI(
    lifespan=lifespan,
    title="TeamWiki",
    version="0.4.0",
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
app.state.inflight = 0


def unavailable_response():
    return JSONResponse(
        status_code=503,
        content={"detail": {"code": "temporarily_unavailable", "message": "Service busy; retry shortly."}},
        headers={"Retry-After": "1"},
    )


@app.exception_handler(socket.gaierror)
@app.exception_handler(ConnectionError)
@app.exception_handler(builtins.TimeoutError)
@app.exception_handler(TimeoutError)
@app.exception_handler(DBAPIError)
async def database_capacity_error(request: Request, error: Exception):
    input_limit = fulltext_limit_response(error)
    if input_limit is not None:
        return input_limit
    reason = capacity_failure_reason(error)
    if reason is None:
        raise error
    DB_FAILURES.labels(reason).inc()
    logger.warning("database_capacity_exhausted", extra={"reason": reason})
    return unavailable_response()


def record_request(request: Request, start: float, status: int):
    route = request.scope.get("route")
    # Both method and route are bounded; raw paths/tenant identifiers never enter labels.
    method = (
        request.method
        if request.method in {"GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"}
        else "OTHER"
    )
    route_path = route.path if route else "unmatched"
    duration = time.perf_counter() - start
    HTTP_REQUESTS.labels(method, route_path, str(status)).inc()
    HTTP_DURATION.labels(method, route_path, str(status)).observe(duration)
    logger.info(
        "request_completed",
        extra={
            "request_id": request.state.request_id,
            "method": request.method,
            "route": route_path,
            "status": status,
            "duration_ms": round(duration * 1000, 2),
        },
    )


@app.middleware("http")
async def browser_security_and_request_log(request: Request, call_next):
    request_id = str(uuid4())
    request.state.request_id = request_id
    start = time.perf_counter()
    untrusted = False
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        request_origin = str(request.base_url).rstrip("/")
        allowed = {request_origin, *settings.allowed_origins}
        if (origin is not None and origin.rstrip("/") not in allowed) or request.headers.get(
            "sec-fetch-site"
        ) == "cross-site":
            untrusted = True
    exempt = request.url.path in {"/api/health", "/api/ready", "/internal/metrics"}
    if untrusted:
        response = JSONResponse(status_code=403, content={"detail": "Untrusted request origin"})
    elif not exempt and app.state.inflight >= settings.request_max_inflight:
        HTTP_REJECTED.inc()
        response = unavailable_response()
    else:
        if not exempt:
            app.state.inflight += 1
            HTTP_INFLIGHT.inc()
        try:
            response = await call_next(request)
        except Exception:
            record_request(request, start, 500)
            raise
        finally:
            if not exempt:
                app.state.inflight -= 1
                HTTP_INFLIGHT.dec()
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    # Session/document responses must not be persisted by browser or shared proxies.
    response.headers["Cache-Control"] = "no-store"
    record_request(request, start, response.status_code)
    return response


@app.get("/api/health", tags=["health"])
async def health():
    return {"status": "ok"}


@app.get("/internal/metrics", include_in_schema=False)
async def metrics():
    body, content_type = render_metrics()
    return Response(content=body, headers={"Content-Type": content_type})


@app.get("/api/ready", tags=["health"])
async def readiness(db: DB):
    try:
        await db.execute(text("SELECT 1"))
    except Exception as error:
        logger.exception("readiness_database_failed")
        reason = capacity_failure_reason(error)
        if reason is not None:
            DB_FAILURES.labels(reason).inc()
        await db.rollback()
        return JSONResponse(
            status_code=503,
            content={"database": "unavailable", "cache": "unknown"},
            headers={"Retry-After": "1"},
        )
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
