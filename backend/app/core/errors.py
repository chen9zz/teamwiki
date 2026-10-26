from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError


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
