"""Shared error envelope for the RegRadar API.

Every error response takes the same shape:
{"error": {"code": "...", "message": "...", "request_id": "..."}}
"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from regradar.api.middleware.request_id import request_id_ctx


class ApiError(HTTPException):
    """An HTTPException that renders through the shared error envelope."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=message, headers=headers)
        self.code = code


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.detail,
                    "request_id": request_id_ctx.get(),
                }
            },
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "Invalid request parameters.",
                    "request_id": request_id_ctx.get(),
                }
            },
        )
