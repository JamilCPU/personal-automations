"""Clients the hub uses to reach devices; RemoteAgentClient talks pinned HTTPS to a Pi agent."""

import ssl
from typing import Any, Protocol
from urllib.parse import quote

import httpx
from pa_core.models import ActionName, ActionResult, Health, Program, UsbState
from pa_core.policy import ActionNotAllowed

from pa_hub.config import DeviceEntry

HTTP_NOT_FOUND = 404
HTTP_CONFLICT = 409
HTTP_MULTIPLE_CHOICES = 300


class DeviceOffline(Exception):
    """The device could not be reached (timeout, refused, TLS failure)."""


class HubAgentError(Exception):
    """The agent answered but not successfully (auth failure, server error, ...)."""

    def __init__(self, device_id: str, status: int, error: str) -> None:
        super().__init__(f"{device_id}: HTTP {status}: {error}")
        self.device_id = device_id
        self.status = status
        self.error = error


class DeviceClient(Protocol):
    capabilities: set[str]

    async def health(self) -> Health: ...
    async def programs(self) -> list[Program]: ...
    async def program(self, pid: str) -> Program: ...
    async def logs(self, pid: str, lines: int) -> list[str]: ...
    async def act(self, pid: str, action: ActionName) -> ActionResult: ...
    async def group_restart(self, project: str) -> ActionResult: ...
    async def usb(self) -> UsbState: ...
    async def set_usb(self, on: bool) -> ActionResult: ...
    async def reboot(self) -> ActionResult: ...


def build_ssl_context(cert_file: str) -> ssl.SSLContext:
    """Trust only the agent's own certificate; hostname (IP SAN) verification stays on."""
    ctx = ssl.create_default_context(cafile=cert_file)
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


def _error_text(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200] or f"HTTP {response.status_code}"
    if isinstance(body, dict) and body.get("error"):
        return str(body["error"])
    return f"HTTP {response.status_code}"


class RemoteAgentClient:
    def __init__(
        self, entry: DeviceEntry, timeout_s: float, action_timeout_s: float | None = None
    ) -> None:
        if not entry.url or not entry.cert_file:
            msg = f"device {entry.id}: agent devices need url and cert_file"
            raise ValueError(msg)
        if not entry.token:
            msg = f"device {entry.id}: no token configured"
            raise ValueError(msg)
        self.device_id = entry.id
        self.capabilities: set[str] = set(entry.capabilities)
        self._timeout_s = timeout_s
        self._action_timeout_s = action_timeout_s if action_timeout_s is not None else timeout_s
        self._http = httpx.AsyncClient(
            base_url=entry.url,
            verify=build_ssl_context(entry.cert_file),
            timeout=timeout_s,
            headers={"Authorization": f"Bearer {entry.token}"},
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(
        self, method: str, path: str, *, mutating: bool = False, **kwargs: Any
    ) -> Any:
        timeout = self._action_timeout_s if mutating else self._timeout_s
        try:
            response = await self._http.request(method, f"/v1{path}", timeout=timeout, **kwargs)
        except (httpx.TransportError, ssl.SSLError) as exc:
            raise DeviceOffline(self.device_id) from exc
        if response.status_code >= HTTP_MULTIPLE_CHOICES:
            error = _error_text(response)
            if response.status_code == HTTP_NOT_FOUND:
                raise KeyError(error)
            if response.status_code == HTTP_CONFLICT:
                raise ActionNotAllowed(error)
            raise HubAgentError(self.device_id, response.status_code, error)
        try:
            body = response.json()
        except ValueError as exc:
            raise HubAgentError(self.device_id, response.status_code, "invalid JSON") from exc
        if not isinstance(body, dict):
            raise HubAgentError(self.device_id, response.status_code, "response is not an envelope")
        if not body.get("success"):
            raise HubAgentError(self.device_id, response.status_code, str(body.get("error")))
        return body.get("data")

    async def health(self) -> Health:
        return Health.model_validate(await self._request("GET", "/health"))

    async def programs(self) -> list[Program]:
        data = await self._request("GET", "/programs")
        return [Program.model_validate(item) for item in data]

    async def program(self, pid: str) -> Program:
        return Program.model_validate(await self._request("GET", "/program", params={"id": pid}))

    async def logs(self, pid: str, lines: int) -> list[str]:
        return await self._request("GET", "/logs", params={"id": pid, "lines": lines})

    async def act(self, pid: str, action: ActionName) -> ActionResult:
        data = await self._request(
            "POST", "/actions", mutating=True, json={"id": pid, "action": action}
        )
        return ActionResult.model_validate(data)

    async def group_restart(self, project: str) -> ActionResult:
        path = f"/groups/{quote(project, safe='')}/restart"
        return ActionResult.model_validate(await self._request("POST", path, mutating=True))

    async def usb(self) -> UsbState:
        return UsbState.model_validate(await self._request("GET", "/usb"))

    async def set_usb(self, on: bool) -> ActionResult:
        data = await self._request("PUT", "/usb", mutating=True, json={"on": on})
        return ActionResult.model_validate(data)

    async def reboot(self) -> ActionResult:
        return ActionResult.model_validate(await self._request("POST", "/reboot", mutating=True))


class UnavailableDevice:
    """Stand-in for an agent that is not deployed yet: every call reports it offline."""

    def __init__(self, entry: DeviceEntry) -> None:
        self.device_id = entry.id
        self.capabilities: set[str] = set(entry.capabilities)

    async def health(self) -> Health:
        raise DeviceOffline(self.device_id)

    async def programs(self) -> list[Program]:
        raise DeviceOffline(self.device_id)

    async def program(self, pid: str) -> Program:
        raise DeviceOffline(self.device_id)

    async def logs(self, pid: str, lines: int) -> list[str]:
        raise DeviceOffline(self.device_id)

    async def act(self, pid: str, action: ActionName) -> ActionResult:
        raise DeviceOffline(self.device_id)

    async def group_restart(self, project: str) -> ActionResult:
        raise DeviceOffline(self.device_id)

    async def usb(self) -> UsbState:
        raise DeviceOffline(self.device_id)

    async def set_usb(self, on: bool) -> ActionResult:
        raise DeviceOffline(self.device_id)

    async def reboot(self) -> ActionResult:
        raise DeviceOffline(self.device_id)
