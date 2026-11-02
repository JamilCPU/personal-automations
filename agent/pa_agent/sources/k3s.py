"""k3s program source: list, restart and tail logs of pods via `k3s kubectl`."""

import json
import re
from datetime import UTC, datetime

from pa_core.models import ActionResult, Program, Status
from pa_core.policy import allowed_actions, is_system, protection_for
from pa_core.runner import Runner

_HASH_SUFFIX_RE = re.compile(r"-[a-z0-9]+$")
_SUCCEEDED = "Succeeded"


def _kubectl(kubeconfig: str, *args: str) -> list[str]:
    return ["k3s", "kubectl", "--kubeconfig", kubeconfig, *args]


def _unsafe(value: str) -> bool:
    return not value or value.startswith("-")


def _owner_name(meta: dict) -> str:
    refs = meta.get("ownerReferences")
    if not isinstance(refs, list) or not refs or not isinstance(refs[0], dict):
        return str(meta.get("name", ""))
    ref = refs[0]
    name = str(ref.get("name", "")) or str(meta.get("name", ""))
    if ref.get("kind") == "ReplicaSet":
        return _HASH_SUFFIX_RE.sub("", name, count=1) or name
    return name


def _container_statuses(status: dict) -> list[dict]:
    raw = status.get("containerStatuses")
    if not isinstance(raw, list):
        return []
    return [c for c in raw if isinstance(c, dict)]


def _pod_status(status: dict) -> Status:
    phase = status.get("phase")
    containers = _container_statuses(status)
    waiting = any(isinstance(c.get("state"), dict) and "waiting" in c["state"] for c in containers)
    if phase == "Failed":
        return "failed"
    if phase == "Pending" or waiting:
        return "restarting"
    if phase == "Running" and containers and all(c.get("ready") is True for c in containers):
        return "running"
    return "unknown"


def _uptime(start: object, now: datetime) -> int | None:
    if not isinstance(start, str):
        return None
    try:
        started = datetime.fromisoformat(start)
    except ValueError:
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return max(0, int((now - started).total_seconds()))


def _restarts(status: dict) -> int:
    total = 0
    for c in _container_statuses(status):
        count = c.get("restartCount")
        if isinstance(count, int):
            total += count
    return total


def _to_program(pod: object, node_devices: dict[str, str], now: datetime) -> Program | None:
    if not isinstance(pod, dict):
        return None
    meta = pod.get("metadata")
    spec = pod.get("spec")
    status = pod.get("status")
    if not isinstance(meta, dict) or not isinstance(spec, dict) or not isinstance(status, dict):
        return None
    pod_name = meta.get("name")
    namespace = meta.get("namespace")
    node = spec.get("nodeName")
    if not (isinstance(pod_name, str) and isinstance(namespace, str) and isinstance(node, str)):
        return None
    if not pod_name or not namespace or not node or status.get("phase") == _SUCCEEDED:
        return None
    device = node_devices.get(node)
    if device is None:
        return None
    owner = _owner_name(meta)
    state = _pod_status(status)
    protection = protection_for("k3s", owner)
    # Owner name + node, not the pod name: stable across pod restarts, so also the pin key.
    program_id = f"k3s:{namespace}/{owner}@{node}"
    return Program(
        id=program_id,
        name=owner,
        kind="k3s",
        device=device,
        status=state,
        uptime_s=_uptime(status.get("startTime"), now) if state == "running" else None,
        system=is_system("k3s", owner, namespace=namespace),
        protection=protection,
        actions=allowed_actions("k3s", state, protection, namespace=namespace),
        group=namespace,
        detail={
            "pod": pod_name,
            "namespace": namespace,
            "node": node,
            "restarts": str(_restarts(status)),
        },
        pin_key=program_id,
    )


def list_k3s(
    run: Runner,
    kubeconfig: str,
    node_devices: dict[str, str],
    now: datetime | None = None,
) -> list[Program]:
    result = run(_kubectl(kubeconfig, "get", "pods", "-A", "-o", "json"))
    if result.returncode != 0:
        return []
    try:
        doc = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    items = doc.get("items") if isinstance(doc, dict) else None
    if not isinstance(items, list):
        return []
    moment = now or datetime.now(UTC)
    programs = (_to_program(pod, node_devices, moment) for pod in items)
    return [p for p in programs if p is not None]


def k3s_restart(run: Runner, kubeconfig: str, namespace: str, pod: str) -> ActionResult:
    if _unsafe(namespace) or _unsafe(pod):
        return ActionResult(ok=False, message=f"invalid pod reference: {namespace!r}/{pod!r}")
    r = run(_kubectl(kubeconfig, "delete", "pod", "-n", namespace, pod, "--wait=false"))
    if r.returncode == 0:
        return ActionResult(ok=True, message=r.stdout.strip() or f"restart {namespace}/{pod}")
    return ActionResult(ok=False, message=r.stderr.strip() or f"kubectl exited with {r.returncode}")


def k3s_logs(run: Runner, kubeconfig: str, namespace: str, pod: str, lines: int) -> list[str]:
    if _unsafe(namespace) or _unsafe(pod):
        return []
    r = run(_kubectl(kubeconfig, "logs", "-n", namespace, pod, "--tail", str(lines)))
    return r.stdout.splitlines()
