"""LinuxDevice: merges docker/systemd/k3s sources and adds health, USB power and reboot."""

import threading
import time
from collections.abc import Callable
from pathlib import Path

import psutil
from pa_core.docker import docker_action, docker_group_restart, docker_logs, list_docker
from pa_core.models import ActionName, ActionResult, Health, Program, UsbState
from pa_core.policy import check_action
from pa_core.runner import Runner, run

from pa_agent.config import AgentConfig
from pa_agent.sources.k3s import k3s_logs, k3s_restart, list_k3s
from pa_agent.sources.systemd import list_systemd, systemd_action, systemd_logs

MIN_LOG_LINES = 1
MAX_LOG_LINES = 500
DEFAULT_THERMAL_PATH = "/sys/class/thermal/thermal_zone0/temp"
DEFAULT_PROC_UPTIME = "/proc/uptime"
_LIDAR_ON_PREFIX = "Lidar: on"

Scheduler = Callable[[float, Callable[[], None]], None]
UptimeReader = Callable[[Runner, str], int | None]


def threading_timer(delay: float, fn: Callable[[], None]) -> None:
    timer = threading.Timer(delay, fn)
    timer.daemon = True
    timer.start()


def systemd_uptime_s(
    run_fn: Runner, unit: str, proc_uptime: str = DEFAULT_PROC_UPTIME
) -> int | None:
    """Seconds since the unit became active; None when it cannot be determined."""
    result = run_fn(["systemctl", "show", "-p", "ActiveEnterTimestampMonotonic", "--value", unit])
    if result.returncode != 0:
        return None
    try:
        entered_us = int(result.stdout.strip())
        boot_s = float(Path(proc_uptime).read_text(encoding="utf-8").split()[0])
    except (ValueError, IndexError, OSError):
        return None
    if entered_us <= 0:
        return None
    return max(0, int(boot_s - entered_us / 1_000_000))


def _split_id(pid: str) -> tuple[str, str]:
    kind, _, rest = pid.partition(":")
    return kind, rest


class LinuxDevice:
    def __init__(
        self,
        cfg: AgentConfig,
        run: Runner = run,
        reboot_delay_s: float = 2.0,
        scheduler: Scheduler = threading_timer,
        thermal_path: str = DEFAULT_THERMAL_PATH,
        uptime_reader: UptimeReader = systemd_uptime_s,
    ) -> None:
        self._cfg = cfg
        self._run = run
        self._reboot_delay_s = reboot_delay_s
        self._scheduler = scheduler
        self._thermal_path = thermal_path
        self._uptime_reader = uptime_reader

    def programs(self) -> list[Program]:
        found = [
            *list_docker(self._run, self._cfg.device_id),
            *list_systemd(self._run, self._cfg.device_id),
        ]
        if self._cfg.kubeconfig:
            found.extend(list_k3s(self._run, self._cfg.kubeconfig, self._cfg.node_devices))
        return found

    def program(self, pid: str) -> Program:
        for candidate in self.programs():
            if candidate.id != pid:
                continue
            if candidate.kind == "systemd":
                uptime = self._uptime_reader(self._run, _split_id(pid)[1])
                return candidate.model_copy(update={"uptime_s": uptime})
            return candidate
        raise KeyError(pid)

    def logs(self, pid: str, lines: int = 100) -> list[str]:
        count = max(MIN_LOG_LINES, min(MAX_LOG_LINES, lines))
        prog = self.program(pid)
        if prog.kind == "docker":
            return docker_logs(self._run, _split_id(prog.id)[1], count)
        if prog.kind == "systemd":
            return systemd_logs(self._run, _split_id(prog.id)[1], count)
        if prog.kind == "k3s" and self._cfg.kubeconfig:
            return k3s_logs(
                self._run,
                self._cfg.kubeconfig,
                prog.detail["namespace"],
                prog.detail["pod"],
                count,
            )
        return []

    def act(self, pid: str, action: ActionName) -> ActionResult:
        prog = self.program(pid)
        check_action(prog, action)
        name = _split_id(prog.id)[1]
        if prog.kind == "docker":
            return docker_action(self._run, name, action)
        if prog.kind == "systemd":
            return systemd_action(self._run, name, action)
        if prog.kind == "k3s" and self._cfg.kubeconfig:
            return k3s_restart(
                self._run, self._cfg.kubeconfig, prog.detail["namespace"], prog.detail["pod"]
            )
        return ActionResult(ok=False, message=f"unsupported program kind: {prog.kind}")

    def group_restart(self, project: str) -> ActionResult:
        containers = [
            _split_id(p.id)[1]
            for p in list_docker(self._run, self._cfg.device_id)
            if p.group == project
        ]
        if not containers:
            raise KeyError(project)
        return docker_group_restart(self._run, project, containers)

    def _read_temp(self) -> float | None:
        try:
            return int(Path(self._thermal_path).read_text(encoding="utf-8").strip()) / 1000
        except (OSError, ValueError):
            return None

    def health(self) -> Health:
        return Health(
            cpu_pct=psutil.cpu_percent(interval=None),
            mem_pct=psutil.virtual_memory().percent,
            disk_pct=psutil.disk_usage("/").percent,
            temp_c=self._read_temp(),
            uptime_s=max(0, int(time.time() - psutil.boot_time())),
        )

    def _usb_script(self) -> str:
        if self._cfg.usb_script is None:
            msg = "usb control is not configured"
            raise NotImplementedError(msg)
        return self._cfg.usb_script

    def usb(self) -> UsbState:
        result = self._run(["sudo", "-n", self._usb_script(), "status"])
        lines = result.stdout.splitlines()
        is_on = result.returncode == 0 and any(line.startswith(_LIDAR_ON_PREFIX) for line in lines)
        return UsbState(on=is_on, detail=result.stdout.strip() or result.stderr.strip())

    def set_usb(self, on: bool) -> ActionResult:
        result = self._run(["sudo", "-n", self._usb_script(), "on" if on else "off"])
        if result.returncode == 0:
            return ActionResult(ok=True, message=result.stdout.strip())
        message = result.stderr.strip() or result.stdout.strip()
        return ActionResult(ok=False, message=message or f"exited with {result.returncode}")

    def reboot(self) -> ActionResult:
        argv = ["sudo", "-n", "systemctl", "reboot"]
        self._scheduler(self._reboot_delay_s, lambda: self._run(argv))
        return ActionResult(ok=True, message="Rebooting in 2 s")
