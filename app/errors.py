from typing import Any
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or []
        self.headers = headers or {}


def _error_response(
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details or [],
            }
        },
        headers=headers,
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
        return _error_response(
            exc.status_code, exc.code, exc.message, exc.details, exc.headers
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details = [
            {"field": ".".join(str(part) for part in error["loc"]), "message": error["msg"]}
            for error in exc.errors()
        ]
        return _error_response(422, "validation_error", "Request validation failed", details)

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(
        _: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        default_messages = {
            404: ("not_found", "Resource not found"),
            405: ("method_not_allowed", "Method not allowed"),
        }
        code, message = default_messages.get(
            exc.status_code, ("http_error", str(exc.detail))
        )
        return _error_response(exc.status_code, code, message, headers=exc.headers)

    @app.exception_handler(Exception)
    async def unexpected_error_handler(_: Request, exc: Exception) -> JSONResponse:
        logging.getLogger("app").exception("Unhandled API exception", exc_info=exc)
        return _error_response(
            500, "internal_server_error", "An unexpected error occurred"
        )
