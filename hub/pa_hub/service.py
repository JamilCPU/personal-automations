"""HubService: fans out to every device client, merges results, records mutating actions."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TypeVar

from pa_core.models import ActionName, ActionResult, Health, Program, UsbState
from pa_core.policy import ActionNotAllowed
from pydantic import BaseModel

from pa_hub.clients import DeviceClient, DeviceOffline, HubAgentError
from pa_hub.config import DeviceEntry
from pa_hub.history import HistoryEntry, HistoryStore

K3S_PREFIX = "k3s:"
DETAIL_HISTORY_LIMIT = 10
DEFAULT_ACTION_TIMEOUT_S = 30.0
T = TypeVar("T")

logger = logging.getLogger(__name__)


class DeviceSummary(BaseModel):
    id: str
    name: str
    role: str
    ip: str
    online: bool
    health: Health | None = None
    programs_running: int = 0
    programs_total: int = 0
    capabilities: list[str] = []
    usb: UsbState | None = None


class ProgramDetail(BaseModel):
    program: Program
    history: list[HistoryEntry]


class _Snapshot(BaseModel):
    health: Health | None = None
    programs: list[Program] = []
    usb: UsbState | None = None


async def _none() -> None:
    return None


class HubService:
    def __init__(
        self,
        devices: dict[str, DeviceClient],
        entries: dict[str, DeviceEntry],
        history: HistoryStore,
        timeout_s: float,
        action_timeout_s: float = DEFAULT_ACTION_TIMEOUT_S,
    ) -> None:
        self._clients = devices
        self._entries = entries
        self._history = history
        self._timeout_s = timeout_s
        self._action_timeout_s = action_timeout_s
        self._order = {device_id: index for index, device_id in enumerate(entries)}
        self._k3s_source = next((i for i, e in entries.items() if e.k3s_source), None)

    # ---- helpers -------------------------------------------------------

    def _client(self, device_id: str) -> DeviceClient:
        client = self._clients.get(device_id)
        if client is None or device_id not in self._entries:
            raise KeyError(f"unknown device: {device_id}")
        return client

    def _route(self, device_id: str, pid: str) -> DeviceClient:
        """Validate the device, then pick the client that owns `pid` (k3s -> source)."""
        client = self._client(device_id)
        if not pid.startswith(K3S_PREFIX):
            return client
        if self._k3s_source is None or self._k3s_source not in self._clients:
            raise KeyError("no k3s source device configured")
        return self._clients[self._k3s_source]

    def _require(self, device_id: str, capability: str) -> DeviceClient:
        client = self._client(device_id)
        if capability not in self._entries[device_id].capabilities:
            raise ActionNotAllowed(f"{device_id} does not support {capability}")
        return client

    async def _call(self, device_id: str, call: Awaitable[T], *, mutating: bool = False) -> T:
        timeout = self._action_timeout_s if mutating else self._timeout_s
        try:
            return await asyncio.wait_for(call, timeout=timeout)
        except TimeoutError as exc:
            raise DeviceOffline(device_id) from exc

    async def _listing_call(self, device_id: str, call: Awaitable[T]) -> T | None:
        """Run a listing call; unreachable or misbehaving devices yield None."""
        try:
            return await self._call(device_id, call)
        except DeviceOffline:
            return None
        except HubAgentError as exc:
            logger.warning("treating %s as offline: %s", device_id, exc)
            return None
        except Exception:
            # One misbehaving client (bad payload, bug) must not 500 the whole dashboard.
            logger.exception("treating %s as offline after an unexpected error", device_id)
            return None

    async def _snapshot(self, device_id: str) -> _Snapshot:
        client = self._clients[device_id]
        with_usb = "usb" in self._entries[device_id].capabilities
        usb_call = self._listing_call(device_id, client.usb()) if with_usb else _none()
        health, programs, usb = await asyncio.gather(
            self._listing_call(device_id, client.health()),
            self._listing_call(device_id, client.programs()),
            usb_call,
        )
        if health is None or programs is None:
            # No health or no usable program list: the device is offline for the dashboard.
            return _Snapshot()
        return _Snapshot(health=health, programs=programs, usb=usb)

    async def _snapshots(self) -> dict[str, _Snapshot]:
        ids = [i for i in self._entries if i in self._clients]
        results = await asyncio.gather(*(self._snapshot(i) for i in ids))
        return dict(zip(ids, results, strict=True))

    def _sorted(self, programs: list[Program]) -> list[Program]:
        fallback = len(self._order)
        return sorted(
            programs,
            key=lambda p: (self._order.get(p.device, fallback), p.name.lower()),
        )

    async def _record(
        self,
        client_name: str,
        device_id: str,
        target: str,
        action: str,
        call: Callable[[], Awaitable[ActionResult]],
    ) -> ActionResult:
        try:
            result = await call()
        except (KeyError, ActionNotAllowed, DeviceOffline, HubAgentError) as exc:
            await self._add(client_name, device_id, target, action, ok=False, message=str(exc))
            raise
        await self._add(
            client_name, device_id, target, action, ok=result.ok, message=result.message
        )
        return result

    async def _add(
        self, client_name: str, device_id: str, target: str, action: str, *, ok: bool, message: str
    ) -> None:
        entry = HistoryEntry(
            ts=datetime.now(UTC),
            client=client_name,
            device=device_id,
            target=target,
            action=action,
            ok=ok,
            message=message,
        )
        await asyncio.to_thread(self._history.add, entry)

    # ---- reads ---------------------------------------------------------

    def _summary(self, device_id: str, snaps: dict[str, _Snapshot]) -> DeviceSummary:
        entry = self._entries[device_id]
        snap = snaps[device_id]
        online = snap.health is not None
        running = total = 0
        if online:
            visible = [
                p
                for s in snaps.values()
                for p in s.programs
                if p.device == device_id and not p.system
            ]
            total = len(visible)
            running = sum(1 for p in visible if p.status == "running")
        return DeviceSummary(
            id=device_id,
            name=entry.name,
            role=entry.role,
            ip=entry.ip,
            online=online,
            health=snap.health,
            programs_running=running,
            programs_total=total,
            capabilities=list(entry.capabilities),
            usb=snap.usb,
        )

    async def devices(self) -> list[DeviceSummary]:
        snaps = await self._snapshots()
        return [self._summary(i, snaps) for i in snaps]

    async def device(self, device_id: str) -> DeviceSummary:
        self._client(device_id)
        return next(d for d in await self.devices() if d.id == device_id)

    async def programs(self, device: str | None, include_system: bool) -> list[Program]:
        if device is not None:
            self._client(device)
        snaps = await self._snapshots()
        merged = [p for s in snaps.values() for p in s.programs]
        if device is not None:
            merged = [p for p in merged if p.device == device]
        if not include_system:
            merged = [p for p in merged if not p.system]
        return self._sorted(merged)

    async def program(self, device_id: str, pid: str) -> ProgramDetail:
        client = self._route(device_id, pid)
        program = await self._call(device_id, client.program(pid))
        history = await asyncio.to_thread(
            self._history.recent, DETAIL_HISTORY_LIMIT, device_id, pid
        )
        return ProgramDetail(program=program, history=history)

    async def logs(self, device_id: str, pid: str, lines: int) -> list[str]:
        client = self._route(device_id, pid)
        return await self._call(device_id, client.logs(pid, lines))

    async def usb(self, device_id: str) -> UsbState:
        client = self._require(device_id, "usb")
        return await self._call(device_id, client.usb())

    async def recent_history(self, limit: int, device: str | None = None) -> list[HistoryEntry]:
        return await asyncio.to_thread(self._history.recent, limit, device)

    # ---- mutations -----------------------------------------------------

    async def act(self, client: str, device_id: str, pid: str, action: ActionName) -> ActionResult:
        async def call() -> ActionResult:
            target = self._route(device_id, pid)
            return await self._call(device_id, target.act(pid, action), mutating=True)

        return await self._record(client, device_id, pid, action, call)

    async def group_restart(self, client: str, device_id: str, project: str) -> ActionResult:
        async def call() -> ActionResult:
            target = self._client(device_id)
            return await self._call(device_id, target.group_restart(project), mutating=True)

        return await self._record(client, device_id, f"group:{project}", "restart", call)

    async def set_usb(self, client: str, device_id: str, on: bool) -> ActionResult:
        async def call() -> ActionResult:
            target = self._require(device_id, "usb")
            return await self._call(device_id, target.set_usb(on), mutating=True)

        return await self._record(client, device_id, "usb", "on" if on else "off", call)

    async def reboot(self, client: str, device_id: str) -> ActionResult:
        async def call() -> ActionResult:
            target = self._require(device_id, "reboot")
            return await self._call(device_id, target.reboot(), mutating=True)

        return await self._record(client, device_id, "reboot", "reboot", call)
