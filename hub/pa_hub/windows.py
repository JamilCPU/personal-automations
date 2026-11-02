"""LocalWindowsDevice: the hub's in-process view of the jw PC (services, apps, Docker, health)."""

import asyncio
import ntpath
import os
import re
import sys
import threading
import time
from collections.abc import Callable, Iterable
from typing import Any

import psutil
from pa_core.docker import docker_action, docker_group_restart, docker_logs, list_docker
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
PROGRAMS_TTL_S = 3.0
HEALTH_TTL_S = 2.0
RESTART_WAIT_S = 15.0
RESTART_POLL_S = 1.0
MIN_AGE_S = 600
# python.exe, pythonw.exe, python3.exe, python3.13.exe, python3.13w.exe, node.exe, java.exe
INTERPRETER = re.compile(r"(?:python(?:\d+(?:\.\d+)*)?w?|node|java)\.exe", re.IGNORECASE)
MCP_MARKERS = ("pa-mcp", "pa_mcp")
PROC_ATTRS = ["pid", "ppid", "name", "exe", "cmdline", "create_time"]
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


def _under_windows_dir(exe_path: object) -> bool:
    """True when the executable lives under the Windows directory (case-insensitive)."""
    if not isinstance(exe_path, str) or not exe_path:
        return False
    return exe_path.replace("/", "\\").lower().startswith(SYSTEM_ROOT.lower() + "\\")


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


def _is_interpreter(exe: str) -> bool:
    return INTERPRETER.fullmatch(exe) is not None


def _args(info: dict[str, Any]) -> list[str]:
    """Command-line arguments without argv[0] (a venv launcher and its child differ only there)."""
    return list(info.get("cmdline") or [])[1:]


def _is_mcp(info: dict[str, Any]) -> bool:
    text = " ".join(info.get("cmdline") or []).lower()
    return any(marker in text for marker in MCP_MARKERS)


def _created(info: dict[str, Any]) -> float:
    return float(info.get("create_time") or 0.0)


def _parent(info: dict[str, Any], snap: dict[int, dict[str, Any]]) -> dict[str, Any] | None:
    """The live parent, ignoring a reused ppid (a parent cannot be younger than its child)."""
    parent = snap.get(info.get("ppid") or 0)
    if parent is None or parent is info or _created(parent) > _created(info):
        return None
    return parent


def _ancestors(pid: int, snap: dict[int, dict[str, Any]]) -> set[int]:
    found: set[int] = set()
    info = snap.get(pid)
    while info is not None:
        parent = _parent(info, snap)
        if parent is None or parent["pid"] in found:
            break
        found.add(parent["pid"])
        info = parent
    return found


def _descendants(pids: set[int], snap: dict[int, dict[str, Any]]) -> set[int]:
    found: set[int] = set()
    frontier = set(pids)
    while frontier:
        children = {
            pid
            for pid, info in snap.items()
            if pid not in found and pid not in pids
            and (parent := _parent(info, snap)) is not None and parent["pid"] in frontier
        }
        found |= children
        frontier = children
    return found


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
        self._gen = 0
        self._programs_lock = threading.Lock()
        self._programs_cache: tuple[float, list[Program]] | None = None
        self._health_lock = threading.Lock()
        self._health_cache: tuple[float, Health] | None = None

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

    def _snapshot(self) -> dict[int, dict[str, Any]]:
        snap: dict[int, dict[str, Any]] = {}
        for proc in self._procs.process_iter(PROC_ATTRS):
            info = proc.info
            if info.get("pid") is not None:
                snap[info["pid"]] = info
        return snap

    def _locked_pids(self, snap: dict[int, dict[str, Any]]) -> set[int]:
        """The hub and MCP server, every ancestor (venv launcher chain) and every descendant."""
        seeds = {self._hub_pid} | {pid for pid, info in snap.items() if _is_mcp(info)}
        locked = set(seeds)
        for seed in seeds:
            locked |= _ancestors(seed, snap)
        return locked | _descendants(seeds, snap)

    def _launchers(self, snap: dict[int, dict[str, Any]]) -> set[int]:
        """Pids whose child runs the same arguments: the child is the real program, keep it."""
        launchers: set[int] = set()
        for info in snap.values():
            parent = _parent(info, snap)
            if parent is not None and _args(info) and _args(info) == _args(parent):
                launchers.add(parent["pid"])
        return launchers

    def _process_programs(self) -> list[Program]:
        snap = self._snapshot()
        windowed = self._windowed_pids()
        locked = self._locked_pids(snap)
        launchers = self._launchers(snap)
        now = self._now()
        out: list[Program] = []
        for pid, info in snap.items():
            if pid in launchers:
                continue
            program = self._process_program(info, windowed, now, pid in locked)
            if program is not None:
                out.append(program)
        return out

    def _process_program(
        self, info: dict[str, Any], windowed: dict[int, str], now: float, locked: bool
    ) -> Program | None:
        pid = info["pid"]
        exe = str(info.get("name") or "")
        created = info.get("create_time") or now
        age = int(now - created)
        interpreter = _is_interpreter(exe)
        if not (pid in windowed or (interpreter and age >= MIN_AGE_S)):
            return None
        script = _script_name(info.get("cmdline") or []) if interpreter else None
        name = script or windowed.get(pid) or exe or str(pid)
        protection = "locked" if locked else protection_for("process", name)
        stamp = str(int(created))
        return Program(
            id=f"{PROCESS_PREFIX}{name}#{pid}@{stamp}",
            name=name,
            kind="process",
            device=self.device_id,
            status="running",
            uptime_s=age,
            description=exe,
            system=_under_windows_dir(info.get("exe")),
            protection=protection,
            actions=allowed_actions("process", "running", protection),
            detail={"pid": str(pid), "exe": exe, "create_time": stamp},
        )

    def _docker_programs(self) -> list[Program]:
        if self._run(["docker", "info"]).returncode != 0:
            return []
        return list_docker(self._run, self.device_id)

    def _all_programs(self) -> list[Program]:
        return [*self._service_programs(), *self._docker_programs(), *self._process_programs()]

    def _find(self, pid: str) -> Program:
        if pid.startswith(PROCESS_PREFIX) and "#" in pid:
            number, _, stamp = pid.rsplit("#", 1)[1].partition("@")
            for program in self._process_programs():
                detail = program.detail
                if stamp and detail.get("pid") == number and detail.get("create_time") == stamp:
                    return program
            raise KeyError(pid)
        for program in self._all_programs():
            if program.id == pid:
                return program
        raise KeyError(pid)

    # ---- DeviceClient --------------------------------------------------

    def _invalidate_caches(self) -> None:
        """Drop cached listings after a mutation, without taking the (long-held) cache locks.

        The generation bump also stops an enumeration that began before the mutation from
        repopulating the cache with a pre-mutation snapshot.
        """
        self._gen += 1
        self._programs_cache = None
        self._health_cache = None

    def _cached_programs(self) -> list[Program]:
        """One shared enumeration per TTL; concurrent callers wait on the lock and reuse it."""
        with self._programs_lock:
            cached = self._programs_cache
            if cached is not None and self._clock() - cached[0] < PROGRAMS_TTL_S:
                return list(cached[1])
            gen = self._gen
            programs = self._all_programs()
            if gen == self._gen:
                self._programs_cache = (self._clock(), programs)
            return list(programs)

    async def programs(self) -> list[Program]:
        return await asyncio.to_thread(self._cached_programs)

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
        try:
            return await asyncio.to_thread(self._act, program, action)
        finally:
            self._invalidate_caches()

    def _act(self, program: Program, action: ActionName) -> ActionResult:
        if program.kind == "docker":
            return docker_action(self._run, program.detail["container"], action)
        if program.kind == "process":
            return self._terminate(program)
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

    def _terminate(self, program: Program) -> ActionResult:
        """Stop a process, refusing (KeyError) if its pid now belongs to a different process."""
        name = program.name
        try:
            proc = self._procs.Process(int(program.detail["pid"]))
            if str(int(proc.create_time())) != program.detail["create_time"]:
                raise KeyError(program.id)
            proc.terminate()
        except psutil.NoSuchProcess:
            return ActionResult(ok=False, message=f"{name} is no longer running")
        except psutil.Error as exc:
            return ActionResult(ok=False, message=f"could not stop {name}: {exc}")
        return ActionResult(ok=True, message=f"stopped {name}")

    def _cached_health(self) -> Health:
        with self._health_lock:
            cached = self._health_cache
            if cached is not None and self._clock() - cached[0] < HEALTH_TTL_S:
                return cached[1]
            gen = self._gen
            health = self._health()
            if gen == self._gen:
                self._health_cache = (self._clock(), health)
            return health

    async def health(self) -> Health:
        return await asyncio.to_thread(self._cached_health)

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
        try:
            return await asyncio.to_thread(self._group_restart, project)
        finally:
            self._invalidate_caches()

    def _group_restart(self, project: str) -> ActionResult:
        containers = [p.detail["container"] for p in self._docker_programs() if p.group == project]
        if not containers:
            raise KeyError(project)
        return docker_group_restart(self._run, project, containers)

    async def usb(self) -> UsbState:
        raise ActionNotAllowed(UNSUPPORTED)

    async def set_usb(self, on: bool) -> ActionResult:
        raise ActionNotAllowed(UNSUPPORTED)

    async def reboot(self) -> ActionResult:
        raise ActionNotAllowed(UNSUPPORTED)
