"""MCP server: the same lab actions as the phone app, driven through the hub API."""

import argparse
import hmac
import json
import os
import secrets
import ssl
import sys
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import quote

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import Field
from starlette.types import ASGIApp, Receive, Scope, Send

READ_TIMEOUT_S = 5.0
ACTION_TIMEOUT_S = 35.0
DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 7702
TOKEN_BYTES = 32
HTTP_ERROR_STATUS = 400
HTTP_UNAUTHORIZED = 401
BEARER_PREFIX = "Bearer "
WS_POLICY_VIOLATION = 1008
TLS_CERT_ENV = "PA_MCP_TLS_CERT"
TLS_KEY_ENV = "PA_MCP_TLS_KEY"

DEVICES_TEXT = (
    'Devices: "jw-pc" (jw PC), "robot-pi" (Robot Pi), "pi1" (Pi1), "pi3" (Pi3). '
    "Pass the id (for example jw-pc), not the display name."
)
INSTRUCTIONS = (
    "Drive the home lab: see which programs run on each device, read logs, start, stop or "
    "restart programs, restart a project group, switch USB power, reboot a device. "
    f"{DEVICES_TEXT} Actions run immediately; there is no confirmation step."
)
PROGRAM_ID_TEXT = (
    "program_id is the id returned by list_programs (for example docker:<container> or "
    "k3s:<pod>). k3s programs only support restart."
)

ProgramAction = Literal["start", "stop", "restart"]


class HubClient:
    """Thin async client for the hub API. Returns `data` or raises ToolError(error)."""

    def __init__(self, url: str, token: str, cert: str | None) -> None:
        self._url = url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._verify: ssl.SSLContext | bool = (
            ssl.create_default_context(cafile=cert) if cert else True
        )

    async def call(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        timeout = READ_TIMEOUT_S if method == "GET" else ACTION_TIMEOUT_S
        try:
            async with httpx.AsyncClient(
                base_url=self._url, headers=self._headers, verify=self._verify, timeout=timeout
            ) as client:
                response = await client.request(method, f"/v1{path}", params=params, json=body)
        except httpx.TimeoutException as exc:
            raise ToolError("hub request timed out") from exc
        except httpx.HTTPError as exc:
            raise ToolError(f"hub unreachable: {type(exc).__name__}") from exc
        return _unwrap(response)


def _unwrap(response: httpx.Response) -> Any:
    try:
        envelope = response.json()
    except ValueError:
        envelope = None
    if isinstance(envelope, dict) and "success" in envelope:
        if envelope["success"] and response.status_code < HTTP_ERROR_STATUS:
            return envelope.get("data")
        raise ToolError(str(envelope.get("error") or f"hub error {response.status_code}"))
    raise ToolError(f"hub returned unexpected response (HTTP {response.status_code})")


def _seg(value: str) -> str:
    """Percent-encode one URL path segment."""
    return quote(value, safe="")


def _query(params: dict[str, Any]) -> dict[str, str]:
    """Render query values the way FastAPI parses them (lowercase booleans)."""
    return {k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in params.items()}


def build_server(
    hub: HubClient, host: str = DEFAULT_HTTP_HOST, port: int = DEFAULT_HTTP_PORT
) -> FastMCP:
    """Create the FastMCP server with every lab tool bound to `hub`."""
    mcp = FastMCP("personal-automations", instructions=INSTRUCTIONS, host=host, port=port)

    @mcp.tool(
        description=(
            "List all devices with online state, health, program counts, capabilities and "
            f"USB state. {DEVICES_TEXT}"
        )
    )
    async def list_devices() -> Any:
        return await hub.call("GET", "/devices")

    @mcp.tool(
        description=f"Show one device's summary (online, health, programs). {DEVICES_TEXT}"
    )
    async def get_device(device: str) -> Any:
        return await hub.call("GET", f"/devices/{_seg(device)}")

    @mcp.tool(
        description=(
            "List programs (Docker containers, services, k3s pods, processes) on one device, "
            "or on all devices when device is omitted. System programs are hidden unless "
            f"include_system is true. {DEVICES_TEXT}"
        )
    )
    async def list_programs(device: str | None = None, include_system: bool = False) -> Any:
        params: dict[str, Any] = {"include_system": include_system}
        if device:
            params["device"] = device
        return await hub.call("GET", "/programs", params=_query(params))

    @mcp.tool(
        description=(
            f"Show one program's details and recent history. {PROGRAM_ID_TEXT} {DEVICES_TEXT}"
        )
    )
    async def get_program(device: str, program_id: str) -> Any:
        return await hub.call("GET", "/program", params={"device": device, "id": program_id})

    @mcp.tool(
        description=(
            f"Fetch the last N log lines (default 100, max 2000) of a program. {PROGRAM_ID_TEXT}"
        )
    )
    async def get_program_logs(
        device: str, program_id: str, lines: Annotated[int, Field(ge=1, le=2000)] = 100
    ) -> Any:
        return await hub.call(
            "GET", "/logs", params={"device": device, "id": program_id, "lines": lines}
        )

    @mcp.tool(
        description=(
            "Start, stop or restart a program. Runs immediately. Protected programs are "
            f"refused by the hub. {PROGRAM_ID_TEXT} {DEVICES_TEXT}"
        )
    )
    async def program_action(device: str, program_id: str, action: ProgramAction) -> Any:
        return await hub.call(
            "POST", "/actions", body={"device": device, "id": program_id, "action": action}
        )

    @mcp.tool(
        description=(
            "Restart every program of a project group (for example a docker compose project) "
            f"on a device. Runs immediately. {DEVICES_TEXT}"
        )
    )
    async def restart_group(device: str, project: str) -> Any:
        return await hub.call("POST", f"/devices/{_seg(device)}/groups/{_seg(project)}/restart")

    @mcp.tool(
        description=(
            "Read the USB power state. USB power covers all ports at once and exists only on "
            'the Robot Pi (device id "robot-pi").'
        )
    )
    async def get_usb_power(device: str) -> Any:
        return await hub.call("GET", f"/devices/{_seg(device)}/usb")

    @mcp.tool(
        description=(
            "Switch USB power on or off for ALL ports at once (no per-port control). Only the "
            'Robot Pi (device id "robot-pi") has USB power. Runs immediately.'
        )
    )
    async def set_usb_power(device: str, on: bool) -> Any:
        return await hub.call("PUT", f"/devices/{_seg(device)}/usb", body={"on": on})

    @mcp.tool(
        description=(
            "Reboot a device now, with no confirmation. There is no shutdown action. "
            f"{DEVICES_TEXT}"
        )
    )
    async def reboot_device(device: str) -> Any:
        return await hub.call("POST", f"/devices/{_seg(device)}/reboot")

    @mcp.tool(
        description="Show the most recent actions taken through the hub (client, device, result)."
    )
    async def recent_history(limit: Annotated[int, Field(ge=1, le=500)] = 20) -> Any:
        return await hub.call("GET", "/history", params={"limit": limit})

    return mcp


class BearerAuthMiddleware:
    """ASGI middleware: HTTP requests need `Authorization: Bearer <token>` (constant-time)."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self._app = app
        self._token = token.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope["type"]
        if kind == "lifespan" or (kind == "http" and self._authorized(scope)):
            await self._app(scope, receive, send)
            return
        if kind != "http":
            if kind == "websocket":
                await send({"type": "websocket.close", "code": WS_POLICY_VIOLATION})
            return
        body = json.dumps({"success": False, "data": None, "error": "unauthorized"}).encode()
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
            (b"www-authenticate", b"Bearer"),
        ]
        await send(
            {"type": "http.response.start", "status": HTTP_UNAUTHORIZED, "headers": headers}
        )
        await send({"type": "http.response.body", "body": body})

    def _authorized(self, scope: Scope) -> bool:
        header = dict(scope.get("headers", [])).get(b"authorization", b"").decode("latin-1")
        if not header.startswith(BEARER_PREFIX):
            return False
        return hmac.compare_digest(header[len(BEARER_PREFIX) :].strip().encode(), self._token)


def _require_env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    if not value:
        print(f"pa-mcp: environment variable {name} is required", file=sys.stderr)
        return None
    return value


def _hub_from_env() -> HubClient | None:
    url, token = _require_env("PA_HUB_URL"), _require_env("PA_HUB_TOKEN")
    if url is None or token is None:
        return None
    return HubClient(url, token, os.environ.get("PA_HUB_CERT") or None)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pa-mcp")
    parser.add_argument("--http", action="store_true", help="serve streamable HTTP, not stdio")
    parser.add_argument("--host", default=DEFAULT_HTTP_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_HTTP_PORT)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("gen-token", help="print a fresh PA_MCP_HTTP_TOKEN value")
    return parser


def _default_hub_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "personal-automations" / "hub"


def _tls_files() -> tuple[str, str] | None:
    """The TLS cert/key for HTTP mode (env overrides, else the hub's own); None if missing."""
    hub_dir = _default_hub_dir()
    cert = os.environ.get(TLS_CERT_ENV) or str(hub_dir / "cert.pem")
    key = os.environ.get(TLS_KEY_ENV) or str(hub_dir / "key.pem")
    missing = [p for p in (cert, key) if not Path(p).is_file()]
    if missing:
        print(
            f"pa-mcp: TLS file not found: {', '.join(missing)} "
            f"(set {TLS_CERT_ENV} / {TLS_KEY_ENV})",
            file=sys.stderr,
        )
        return None
    return cert, key


def _serve_http(hub: HubClient, host: str, port: int, token: str, tls: tuple[str, str]) -> None:
    import uvicorn

    app = BearerAuthMiddleware(build_server(hub, host, port).streamable_http_app(), token)
    uvicorn.run(app, host=host, port=port, ssl_certfile=tls[0], ssl_keyfile=tls[1])


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "gen-token":
        print(secrets.token_urlsafe(TOKEN_BYTES))
        return 0
    http_token = _require_env("PA_MCP_HTTP_TOKEN") if args.http else None
    hub = _hub_from_env()
    if hub is None or (args.http and http_token is None):
        return 1
    if http_token is not None:
        tls = _tls_files()
        if tls is None:
            return 1
        _serve_http(hub, args.host, args.port, http_token, tls)
    else:
        build_server(hub).run("stdio")
    return 0
