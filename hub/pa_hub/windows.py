"""LocalWindowsDevice: the hub's in-process view of the jw PC (services, apps, Docker, health)."""

import asyncio
import ntpath
import os
import re
import sys
import time
from collections.abc import Callable, Iterable
from typing import Any

import psutil
from pa_core.docker import docker_action, docker_logs, list_docker
from pa_core.models import ActionName, ActionResult, Health, Program, Status, UsbState
from pa_core.policy import (
    ActionNotAllowed,
    allowed_actions,
    check_action,
    is_system,
    protection_for,
)
from pa_core.runner import Runner, run

SYSTEM_ROOT = "C:\\Windows"
RESTART_WAIT_S = 15.0
RESTART_POLL_S = 1.0
MIN_AGE_S = 600
INTERPRETERS = frozenset({"python.exe", "pythonw.exe", "node.exe", "java.exe"})
UNSUPPORTED = "not supported on jw PC"
SERVICE_PREFIX = "windows-service:"
PROCESS_PREFIX = "process:"
DOCKER_PREFIX = "docker:"
GPU_FIELDS = ("temp_c", "util_pct", "mem_used_mb", "mem_total_mb")
GPU_ARGV = [
    "nvidia-smi",
    "--query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.total",
    "--format=csv,noheader,nounits",
]
_STATUS: dict[str, Status] = {
    "running": "running",
    "stopped": "stopped",
    "paused": "stopped",
    "start_pending": "restarting",
    "stop_pending": "restarting",
    "continue_pending": "restarting",
    "pause_pending": "restarting",
}
_ENV_ROOT = re.compile(r"%(?:systemroot|windir)%", re.IGNORECASE)


def normalise_binpath(raw: str) -> str:
    """Reduce a service binpath to the bare executable path (no quotes, args or env vars)."""
    text = raw.strip()
    if text.startswith('"'):
        end = text.find('"', 1)
        text = text[1:end] if end != -1 else text[1:]
    else:
        match = re.search(r"\.exe", text, re.IGNORECASE)
        if match:
            text = text[: match.end()]
    text = _ENV_ROOT.sub(lambda _m: SYSTEM_ROOT, text)
    if text.lower().startswith("\\systemroot\\"):
        text = SYSTEM_ROOT + text[len("\\systemroot") :]
    return text.removeprefix("\\??\\")


def enum_windowed_pids() -> dict[int, str]:
    """Map pid -> title of its first visible, titled top-level window (empty off Windows)."""
    if sys.platform != "win32":
        return {}
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    found: dict[int, str] = {}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        found.setdefault(pid.value, buf.value)
        return True

    user32.EnumWindows(visit, 0)
    return found


def _script_name(cmdline: list[str]) -> str | None:
    args = cmdline[1:]
    if "-jar" in args and args.index("-jar") + 1 < len(args):
        script = args[args.index("-jar") + 1]
    else:
        script = next((a for a in args if not a.startswith("-")), "")
    if not script:
        return None
    base = ntpath.basename(script.replace("/", "\\"))
    if base.lower() == "main.py":
        parent = ntpath.basename(ntpath.dirname(script.replace("/", "\\")))
        return parent or base
    return base


def _parse_gpu(text: str) -> dict[str, float] | None:
    first = text.strip().splitlines()[0] if text.strip() else ""
    parts = [p.strip() for p in first.split(",")]
    if len(parts) != len(GPU_FIELDS):
        return None
    try:
        return {k: float(v) for k, v in zip(GPU_FIELDS, parts, strict=True)}
    except ValueError:
        return None


class LocalWindowsDevice:
    capabilities: set[str]

    def __init__(
        self,
        device_id: str = "jw-pc",
        run: Runner = run,
        hub_pid: int | None = None,
        procs: Any = psutil,
        services: Callable[[], Iterable[Any]] = psutil.win_service_iter,
        windowed_pids: Callable[[], dict[int, str]] = enum_windowed_pids,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.device_id = device_id
        self.capabilities = set()
        self._run = run
        self._hub_pid = os.getpid() if hub_pid is None else hub_pid
        self._procs = procs
        self._services = services
        self._windowed_pids = windowed_pids
        self._sleep = sleep
        self._clock = clock
        self._now = now

    # ---- listing -------------------------------------------------------

    def _service_programs(self) -> list[Program]:
        out: list[Program] = []
        for svc in self._services():
            try:
                out.append(self._service_program(svc))
            except (psutil.Error, OSError):
                continue
        return out

    def _service_program(self, svc: Any) -> Program:
        name = svc.name()
        try:
            binpath: str | None = normalise_binpath(svc.binpath())
        except (psutil.Error, OSError):
            binpath = None
        status = _STATUS.get(str(svc.status()).lower(), "unknown")
        protection = protection_for("windows-service", name)
        return Program(
            id=f"{SERVICE_PREFIX}{name}",
            name=svc.display_name() or name,
            kind="windows-service",
            device=self.device_id,
            status=status,
            system=is_system("windows-service", name, binary_path=binpath),
            protection=protection,
            actions=allowed_actions("windows-service", status, protection),
            detail={"service": name, "binary": binpath or ""},
        )

    def _process_programs(self) -> list[Program]:
        windowed = self._windowed_pids()
        now = self._now()
        out: list[Program] = []
        for proc in self._procs.process_iter(["pid", "name", "cmdline", "create_time"]):
            info = proc.info
            program = self._process_program(info, windowed, now)
            if program is not None:
                out.append(program)
        return out

    def _process_program(
        self, info: dict[str, Any], windowed: dict[int, str], now: float
    ) -> Program | None:
        pid = info.get("pid")
        exe = str(info.get("name") or "")
        age = int(now - (info.get("create_time") or now))
        interpreter = exe.lower() in INTERPRETERS
        if pid is None or not (pid in windowed or (interpreter and age >= MIN_AGE_S)):
            return None
        script = _script_name(info.get("cmdline") or []) if interpreter else None
        name = script or windowed.get(pid) or exe or str(pid)
        locked = pid == self._hub_pid
        protection = "locked" if locked else protection_for("process", name)
        return Program(
            id=f"{PROCESS_PREFIX}{name}#{pid}",
            name=name,
            kind="process",
            device=self.device_id,
            status="running",
            uptime_s=age,
            description=exe,
            system=False,
            protection=protection,
            actions=allowed_actions("process", "running", protection),
            detail={"pid": str(pid), "exe": exe},
        )

    def _docker_programs(self) -> list[Program]:
        if self._run(["docker", "info"]).returncode != 0:
            return []
        return list_docker(self._run, self.device_id)

    def _all_programs(self) -> list[Program]:
        return [*self._service_programs(), *self._docker_programs(), *self._process_programs()]

    def _find(self, pid: str) -> Program:
        if pid.startswith(PROCESS_PREFIX) and "#" in pid:
            suffix = pid.rsplit("#", 1)[1]
            for program in self._process_programs():
                if program.detail.get("pid") == suffix:
                    return program
            raise KeyError(pid)
        for program in self._all_programs():
            if program.id == pid:
                return program
        raise KeyError(pid)

    # ---- DeviceClient --------------------------------------------------

    async def programs(self) -> list[Program]:
        return await asyncio.to_thread(self._all_programs)

    async def program(self, pid: str) -> Program:
        return await asyncio.to_thread(self._find, pid)

    async def logs(self, pid: str, lines: int) -> list[str]:
        program = await self.program(pid)
        if program.kind == "docker":
            container = program.detail["container"]
            return await asyncio.to_thread(docker_logs, self._run, container, lines)
        return []

    async def act(self, pid: str, action: ActionName) -> ActionResult:
        program = await self.program(pid)
        check_action(program, action)
        return await asyncio.to_thread(self._act, program, action)

    def _act(self, program: Program, action: ActionName) -> ActionResult:
        if program.kind == "docker":
            return docker_action(self._run, program.detail["container"], action)
        if program.kind == "process":
            return self._terminate(int(program.detail["pid"]), program.name)
        name = program.detail["service"]
        if action == "restart":
            return self._restart_service(name)
        return self._sc(action, name)

    def _sc(self, verb: str, name: str) -> ActionResult:
        result = self._run(["sc.exe", verb, name])
        if result.returncode == 0:
            return ActionResult(ok=True, message=f"{verb} {name}")
        text = (result.stdout or result.stderr).strip()
        return ActionResult(ok=False, message=text or f"sc.exe exited with {result.returncode}")

    def _restart_service(self, name: str) -> ActionResult:
        stopped = self._sc("stop", name)
        if not stopped.ok:
            return stopped
        deadline = self._clock() + RESTART_WAIT_S
        while True:
            if "STOPPED" in self._run(["sc.exe", "query", name]).stdout:
                return self._sc("start", name)
            if self._clock() >= deadline:
                return ActionResult(ok=False, message=f"{name} did not stop within 15 s")
            self._sleep(RESTART_POLL_S)

    def _terminate(self, pid: int, name: str) -> ActionResult:
        try:
            self._procs.Process(pid).terminate()
        except psutil.NoSuchProcess:
            return ActionResult(ok=False, message=f"{name} is no longer running")
        except psutil.Error as exc:
            return ActionResult(ok=False, message=f"could not stop {name}: {exc}")
        return ActionResult(ok=True, message=f"stopped {name}")

    async def health(self) -> Health:
        return await asyncio.to_thread(self._health)

    def _health(self) -> Health:
        p = self._procs
        return Health(
            cpu_pct=float(p.cpu_percent(interval=0.1)),
            mem_pct=float(p.virtual_memory().percent),
            disk_pct=float(p.disk_usage("C:\\").percent),
            temp_c=None,
            uptime_s=int(self._now() - p.boot_time()),
            gpu=self._gpu(),
        )

    def _gpu(self) -> dict[str, float] | None:
        result = self._run(list(GPU_ARGV))
        return _parse_gpu(result.stdout) if result.returncode == 0 else None

    async def group_restart(self, project: str) -> ActionResult:
        raise ActionNotAllowed(UNSUPPORTED)

    async def usb(self) -> UsbState:
        raise ActionNotAllowed(UNSUPPORTED)

    async def set_usb(self, on: bool) -> ActionResult:
        raise ActionNotAllowed(UNSUPPORTED)

    async def reboot(self) -> ActionResult:
        raise ActionNotAllowed(UNSUPPORTED)
