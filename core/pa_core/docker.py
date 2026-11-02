"""Docker program source: list, act on and tail logs of containers via the docker CLI."""

import json
import re

from pa_core.models import ActionName, ActionResult, Program, Status
from pa_core.policy import allowed_actions, is_system, protection_for
from pa_core.runner import Runner

_PS_ARGV = ["docker", "ps", "-a", "--no-trunc", "--format", "{{json .}}"]
_SERVICE_LABEL = "com.docker.compose.service"
_PROJECT_LABEL = "com.docker.compose.project"

_STATE_TO_STATUS: dict[str, Status] = {
    "running": "running",
    "exited": "stopped",
    "created": "stopped",
    "restarting": "restarting",
    "dead": "failed",
}

_UNIT_SECONDS: dict[str, int] = {
    "second": 1,
    "minute": 60,
    "hour": 3600,
    "day": 86400,
    "week": 604800,
    "month": 2592000,
    "year": 31536000,
}
_RUNNING_FOR_RE = re.compile(
    r"^(?:about\s+)?(?P<n>\d+|an?)\s+(?P<unit>second|minute|hour|day|week|month|year)s?\s+ago$",
    re.IGNORECASE,
)


def parse_running_for(text: str) -> int | None:
    """Approximate seconds from docker's RunningFor ("2 days ago", "About an hour ago")."""
    cleaned = text.strip()
    if cleaned.lower().startswith("less than a second"):
        return 0
    match = _RUNNING_FOR_RE.match(cleaned)
    if match is None:
        return None
    count = 1 if match["n"].lower() in {"a", "an"} else int(match["n"])
    return count * _UNIT_SECONDS[match["unit"].lower()]


def _parse_labels(raw: str) -> dict[str, str]:
    labels: dict[str, str] = {}
    for item in raw.split(","):
        key, sep, value = item.partition("=")
        if sep:
            labels[key] = value
    return labels


def _to_program(row: dict, device: str) -> Program | None:
    container = row.get("Names")
    if not isinstance(container, str) or not container:
        return None
    labels = _parse_labels(str(row.get("Labels", "")))
    status = _STATE_TO_STATUS.get(str(row.get("State", "")).lower(), "unknown")
    protection = protection_for("docker", container)
    uptime = parse_running_for(str(row.get("RunningFor", ""))) if status == "running" else None
    return Program(
        id=f"docker:{container}",
        name=labels.get(_SERVICE_LABEL) or container,
        kind="docker",
        device=device,
        status=status,
        uptime_s=uptime,
        description=str(row.get("Image", "")),
        system=is_system("docker", container),
        protection=protection,
        actions=allowed_actions("docker", status, protection),
        group=labels.get(_PROJECT_LABEL),
        detail={"container": container, "status_text": str(row.get("Status", ""))},
    )


def list_docker(run: Runner, device: str) -> list[Program]:
    result = run(list(_PS_ARGV))
    if result.returncode != 0:
        return []
    programs: list[Program] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        program = _to_program(row, device)
        if program is not None:
            programs.append(program)
    return programs


def _unsafe(container: str) -> bool:
    return not container or container.startswith("-")


def _outcome(returncode: int, stdout: str, stderr: str, ok_message: str) -> ActionResult:
    if returncode == 0:
        return ActionResult(ok=True, message=stdout.strip() or ok_message)
    return ActionResult(ok=False, message=stderr.strip() or f"docker exited with {returncode}")


def docker_action(run: Runner, container: str, action: ActionName) -> ActionResult:
    if _unsafe(container):
        return ActionResult(ok=False, message=f"invalid container name: {container!r}")
    r = run(["docker", action, container])
    return _outcome(r.returncode, r.stdout, r.stderr, f"{action} {container}")


def docker_group_restart(run: Runner, project: str, containers: list[str]) -> ActionResult:
    if not containers or any(_unsafe(c) for c in containers):
        return ActionResult(ok=False, message=f"no valid containers for project {project}")
    r = run(["docker", "restart", *containers])
    return _outcome(r.returncode, r.stdout, r.stderr, f"restarted {project}")


def docker_logs(run: Runner, container: str, lines: int) -> list[str]:
    if _unsafe(container):
        return []
    r = run(["docker", "logs", "--tail", str(lines), container])
    return (r.stdout + r.stderr).splitlines()
