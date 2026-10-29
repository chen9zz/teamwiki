from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, IntegrityError


def fulltext_limit_response(exc: Exception) -> JSONResponse | None:
    """Reject only PostgreSQL's exact tsvector input representation overflow."""
    if not isinstance(exc, DBAPIError):
        return None
    code = getattr(exc.orig, "sqlstate", None)
    if code == "54000" and "tsvector" in str(exc.orig).lower():
        return JSONResponse(
            status_code=413,
            content={
                "detail": {
                    "code": "search_index_limit",
                    "message": (
                        "Document vocabulary exceeds the database full-text index limit; "
                        "split the document and retry."
                    ),
                }
            },
        )
    return None


def register_error_handlers(app: FastAPI) -> None:

    @app.exception_handler(IntegrityError)
    async def integrity_error_handler(request: Request, exc: IntegrityError):
        return JSONResponse(
            status_code=409,
            content={
                "detail": {
                    "code": "constraint_conflict",
                    "message": "This change conflicts with existing data.",
                },
            },
        )
