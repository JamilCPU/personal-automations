"""Hub HTTPS API (the contract the phone app and MCP server code against)."""

import re
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi.responses import Response
from pa_core.auth import bearer_auth, envelope_response, install_error_handlers
from pa_core.models import ActionName, ActionResult, ApiResponse, Program, UsbState, ok
from pa_core.policy import ActionNotAllowed
from pydantic import BaseModel

from pa_hub.clients import DeviceOffline, HubAgentError
from pa_hub.history import DEFAULT_LIMIT, MAX_LIMIT, HistoryEntry
from pa_hub.service import DeviceSummary, HubService, ProgramDetail

DEFAULT_LOG_LINES = 100
MAX_LOG_LINES = 2000
MAX_AGENT_ERROR_CHARS = 120
HTTP_NOT_FOUND = 404
HTTP_CONFLICT = 409
HTTP_BAD_GATEWAY = 502
HTTP_UNAVAILABLE = 503

AGENT_HEADER = "x-pa-agent"
AGENT_RE = re.compile(r"[A-Za-z0-9._-]{1,40}")

Closer = Callable[[], Awaitable[None]]


def history_client(token_client: str, agent: str | None) -> str:
    """`{token client}:{agent}` when the X-PA-Agent header is valid, else the token client."""
    if agent and AGENT_RE.fullmatch(agent):
        return f"{token_client}:{agent}"
    return token_client


class ActionRequest(BaseModel):
    device: str
    id: str
    action: ActionName


class UsbRequest(BaseModel):
    on: bool


def pair_uri(url: str, token: str, fingerprint: str) -> str:
    """The pairing URI the phone app scans: scheme `pa://pair`, every value URL-encoded."""
    return (
        f"pa://pair?url={quote(url, safe='')}"
        f"&token={quote(token, safe='')}&fp={quote(fingerprint, safe='')}"
    )


def _agent_error_text(exc: HubAgentError, name: str) -> str:
    error = exc.error.strip()
    if error and len(error) <= MAX_AGENT_ERROR_CHARS and "\n" not in error:
        return f"{name}: {error}"
    return f"{name}: agent error"


def _install_domain_errors(app: FastAPI, names: dict[str, str]) -> None:
    def display(device_id: str) -> str:
        return names.get(device_id, device_id)

    @app.exception_handler(KeyError)
    async def _not_found(_request: Request, exc: KeyError) -> Response:
        message = str(exc.args[0]) if exc.args else "not found"
        return envelope_response(HTTP_NOT_FOUND, message)

    @app.exception_handler(ActionNotAllowed)
    async def _conflict(_request: Request, exc: ActionNotAllowed) -> Response:
        return envelope_response(HTTP_CONFLICT, str(exc) or "action not allowed")

    @app.exception_handler(DeviceOffline)
    async def _offline(_request: Request, exc: DeviceOffline) -> Response:
        device_id = str(exc.args[0]) if exc.args else ""
        return envelope_response(HTTP_UNAVAILABLE, f"{display(device_id)} is offline")

    @app.exception_handler(HubAgentError)
    async def _agent(_request: Request, exc: HubAgentError) -> Response:
        return envelope_response(HTTP_BAD_GATEWAY, _agent_error_text(exc, display(exc.device_id)))


def create_app(
    service: HubService,
    clients: dict[str, str],
    names: dict[str, str],
    closers: Sequence[Closer] = (),
) -> FastAPI:
    """Build the API. `clients` maps bearer token -> client name; `names` device id -> display."""

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        for closer in closers:
            await closer()

    app = FastAPI(
        title="pa-hub", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    install_error_handlers(app)
    _install_domain_errors(app, names)
    client = bearer_auth(clients)
    router = APIRouter(prefix="/v1", dependencies=[Depends(client)])

    def actor(request: Request, token_client: str = Depends(client)) -> str:
        return history_client(token_client, request.headers.get(AGENT_HEADER))

    @router.get("/devices")
    async def devices() -> ApiResponse[list[DeviceSummary]]:
        return ok(await service.devices())

    @router.get("/devices/{device_id}")
    async def device(device_id: str) -> ApiResponse[DeviceSummary]:
        return ok(await service.device(device_id))

    @router.get("/programs")
    async def programs(
        device: str | None = None, include_system: bool = False
    ) -> ApiResponse[list[Program]]:
        return ok(await service.programs(device, include_system))

    @router.get("/program")
    async def program(device: str, id: str) -> ApiResponse[ProgramDetail]:
        return ok(await service.program(device, id))

    @router.get("/logs")
    async def logs(
        device: str, id: str, lines: int = Query(DEFAULT_LOG_LINES, ge=1, le=MAX_LOG_LINES)
    ) -> ApiResponse[list[str]]:
        return ok(await service.logs(device, id, lines))

    @router.post("/actions")
    async def actions(body: ActionRequest, name: str = Depends(actor)) -> ApiResponse[ActionResult]:
        return ok(await service.act(name, body.device, body.id, body.action))

    @router.post("/devices/{device_id}/groups/{project}/restart")
    async def group_restart(
        device_id: str, project: str, name: str = Depends(actor)
    ) -> ApiResponse[ActionResult]:
        return ok(await service.group_restart(name, device_id, project))

    @router.get("/devices/{device_id}/usb")
    async def usb(device_id: str) -> ApiResponse[UsbState]:
        return ok(await service.usb(device_id))

    @router.put("/devices/{device_id}/usb")
    async def set_usb(
        device_id: str, body: UsbRequest, name: str = Depends(actor)
    ) -> ApiResponse[ActionResult]:
        return ok(await service.set_usb(name, device_id, body.on))

    @router.post("/devices/{device_id}/reboot")
    async def reboot(device_id: str, name: str = Depends(actor)) -> ApiResponse[ActionResult]:
        return ok(await service.reboot(name, device_id))

    @router.get("/history")
    async def history(
        limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT), device: str | None = None
    ) -> ApiResponse[list[HistoryEntry]]:
        return ok(await service.recent_history(limit, device))

    app.include_router(router)
    return app
