"""Bearer-token auth and envelope-shaped error handlers shared by agent and hub."""

import hmac
import logging
from collections.abc import Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from pa_core.models import fail

UNAUTHORIZED = "unauthorized"
_BEARER_PREFIX = "bearer "
_LOGGER = logging.getLogger("pa_agent")


def envelope_response(status_code: int, message: str) -> Response:
    """A failure envelope as a compact JSON response."""
    return Response(
        content=fail(message).model_dump_json(),
        status_code=status_code,
        media_type="application/json",
    )


def bearer_auth(tokens: dict[str, str]) -> Callable[[Request], str]:
    """Build a FastAPI dependency mapping a valid bearer token to its client name.

    Every configured token is compared with hmac.compare_digest and the loop never
    short-circuits, so timing does not reveal which (or whether any) token matched.
    """

    def dependency(request: Request) -> str:
        header = request.headers.get("authorization", "")
        presented = (
            header[len(_BEARER_PREFIX) :] if header.lower().startswith(_BEARER_PREFIX) else ""
        )
        presented_bytes = presented.encode("utf-8")
        matched: str | None = None
        for token, client in tokens.items():
            if hmac.compare_digest(presented_bytes, token.encode("utf-8")) and presented:
                matched = client
        if matched is None:
            raise HTTPException(status_code=401, detail=UNAUTHORIZED)
        return matched

    return dependency


def install_error_handlers(app: FastAPI) -> None:
    """Make HTTP, validation and unexpected errors all use the ApiResponse failure envelope."""

    @app.exception_handler(StarletteHTTPException)
    async def _http(_request: Request, exc: StarletteHTTPException) -> Response:
        detail = exc.detail if isinstance(exc.detail, str) else "error"
        return envelope_response(exc.status_code, detail)

    @app.exception_handler(RequestValidationError)
    async def _validation(_request: Request, _exc: RequestValidationError) -> Response:
        return envelope_response(422, "invalid request")

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> Response:
        _LOGGER.error(
            "unhandled error on %s %s", request.method, request.url.path,
            exc_info=(type(exc), exc, exc.__traceback__),
        )  # fmt: skip
        return envelope_response(500, "internal error")
