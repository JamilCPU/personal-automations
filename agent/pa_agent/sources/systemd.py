"""systemd program source: list, act on and tail logs of services via systemctl."""

import json

from pa_core.models import ActionName, ActionResult, Program, Status
from pa_core.policy import allowed_actions, is_system, protection_for
from pa_core.runner import Runner

_LIST_ARGV = ["systemctl", "list-units", "--type=service", "--all", "--output=json", "--no-pager"]
_NOT_FOUND = "not-found"

_ACTIVE_TO_STATUS: dict[str, Status] = {
    "active": "running",
    "inactive": "stopped",
    "failed": "failed",
    "activating": "restarting",
    "reloading": "restarting",
}


def _to_program(row: object, device: str) -> Program | None:
    if not isinstance(row, dict):
        return None
    unit = row.get("unit")
    if not isinstance(unit, str) or not unit or row.get("load") == _NOT_FOUND:
        return None
    active = str(row.get("active", "")).lower()
    status = _ACTIVE_TO_STATUS.get(active, "unknown")
    protection = protection_for("systemd", unit)
    program_id = f"systemd:{unit}"
    return Program(
        id=program_id,
        name=unit,
        kind="systemd",
        device=device,
        status=status,
        description=str(row.get("description", "")),
        system=is_system("systemd", unit, active_state=active),
        protection=protection,
        actions=allowed_actions("systemd", status, protection),
        detail={"sub": str(row.get("sub", ""))},
        pin_key=program_id,
    )


def list_systemd(run: Runner, device: str) -> list[Program]:
    result = run(list(_LIST_ARGV))
    if result.returncode != 0:
        return []
    try:
        rows = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    if not isinstance(rows, list):
        return []
    programs = (_to_program(row, device) for row in rows)
    return [p for p in programs if p is not None]


def _unsafe(unit: str) -> bool:
    return not unit or unit.startswith("-")


def systemd_action(run: Runner, unit: str, action: ActionName) -> ActionResult:
    if _unsafe(unit):
        return ActionResult(ok=False, message=f"invalid unit name: {unit!r}")
    r = run(["sudo", "-n", "systemctl", action, unit])
    if r.returncode == 0:
        return ActionResult(ok=True, message=r.stdout.strip() or f"{action} {unit}")
    return ActionResult(
        ok=False, message=r.stderr.strip() or f"systemctl exited with {r.returncode}"
    )


def systemd_logs(run: Runner, unit: str, lines: int) -> list[str]:
    if _unsafe(unit):
        return []
    r = run(["journalctl", "-u", unit, "-n", str(lines), "--no-pager", "-o", "short-iso"])
    return r.stdout.splitlines()
