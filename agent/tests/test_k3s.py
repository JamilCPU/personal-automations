import json
from datetime import UTC, datetime
from pathlib import Path

from pa_agent.sources.k3s import k3s_logs, k3s_restart, list_k3s
from pa_core.runner import RunResult

FIXTURE = Path(__file__).parent / "fixtures" / "k3s_pods.json"
KUBECONFIG = "/etc/rancher/k3s/k3s.yaml"
NODES = {"raspberrypi-1": "pi1", "raspberrypi-2": "robot-pi", "raspberrypi-3": "pi3"}
NOW = datetime(2026, 11, 1, tzinfo=UTC)
LIST_ARGV = ["k3s", "kubectl", "--kubeconfig", KUBECONFIG, "get", "pods", "-A", "-o", "json"]


class FakeRunner:
    def __init__(self, result: RunResult):
        self.result = result
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> RunResult:
        self.calls.append(argv)
        return self.result


def _runner_for(payload: dict) -> FakeRunner:
    return FakeRunner(RunResult(0, json.dumps(payload), ""))


def _fixture_programs():
    runner = FakeRunner(RunResult(0, FIXTURE.read_text(encoding="utf-8"), ""))
    programs = list_k3s(runner, KUBECONFIG, NODES, now=NOW)
    return runner, programs


def _pod(name="p", ns="lab", node="raspberrypi-1", phase="Running", owners=None, statuses=None):
    status: dict = {"phase": phase, "startTime": "2026-10-31T00:00:00Z"}
    if statuses is not None:
        status["containerStatuses"] = statuses
    meta: dict = {"name": name, "namespace": ns}
    if owners is not None:
        meta["ownerReferences"] = owners
    spec = {"nodeName": node} if node else {}
    return {"items": [{"metadata": meta, "spec": spec, "status": status}]}


def test_lab_workloads():
    runner, programs = _fixture_programs()
    assert runner.calls == [LIST_ARGV]
    by_id = {p.id: p for p in programs}
    assert "k3s:lab/gitea@raspberrypi-1" in by_id
    assert "k3s:lab/ai-news@raspberrypi-1" in by_id
    assert by_id["k3s:lab/mission-control@raspberrypi-2"].device == "robot-pi"
    assert "k3s:lab/openclaw-gateway@raspberrypi-3" in by_id
    gitea = by_id["k3s:lab/gitea@raspberrypi-1"]
    assert gitea.name == "gitea"
    assert gitea.kind == "k3s"
    assert gitea.status == "running"
    assert gitea.uptime_s is not None and gitea.uptime_s > 0
    assert gitea.detail["namespace"] == "lab"
    assert gitea.detail["node"] == "raspberrypi-1"
    assert gitea.detail["pod"].startswith("gitea-")
    assert gitea.detail["restarts"].isdigit()


def test_kube_system_is_system():
    _, programs = _fixture_programs()
    kube = [p for p in programs if p.detail["namespace"] == "kube-system"]
    assert kube
    assert all(p.system for p in kube)
    assert all(p.actions == [] for p in kube)
    assert not any(p.system for p in programs if p.detail["namespace"] == "lab")


def test_completed_jobs_skipped():
    _, programs = _fixture_programs()
    assert not any(p.name.startswith("helm-install-") for p in programs)


def test_only_restart_allowed():
    _, programs = _fixture_programs()
    user = [p for p in programs if p.detail["namespace"] != "kube-system"]
    assert user
    assert all(p.actions == ["restart"] for p in user)


def test_restart_argv_uses_current_pod_name():
    _, programs = _fixture_programs()
    gitea = next(p for p in programs if p.id == "k3s:lab/gitea@raspberrypi-1")
    runner = FakeRunner(RunResult(0, 'pod "x" deleted', ""))
    result = k3s_restart(runner, KUBECONFIG, "lab", gitea.detail["pod"])
    assert result.ok
    assert runner.calls == [
        ["k3s", "kubectl", "--kubeconfig", KUBECONFIG, "delete", "pod",
         "-n", "lab", gitea.detail["pod"], "--wait=false"]
    ]  # fmt: skip


def test_restart_rejects_unsafe_names():
    runner = FakeRunner(RunResult(0, "", ""))
    assert not k3s_restart(runner, KUBECONFIG, "lab", "--all").ok
    assert not k3s_restart(runner, KUBECONFIG, "", "pod").ok
    assert not k3s_restart(runner, KUBECONFIG, "-A", "pod").ok
    assert not k3s_restart(runner, KUBECONFIG, "lab", "").ok
    assert runner.calls == []


def test_restart_failure_reports_stderr():
    runner = FakeRunner(RunResult(1, "", "not found"))
    result = k3s_restart(runner, KUBECONFIG, "lab", "pod")
    assert not result.ok
    assert result.message == "not found"


def test_logs_argv_and_unsafe():
    runner = FakeRunner(RunResult(0, "a\nb\n", ""))
    assert k3s_logs(runner, KUBECONFIG, "lab", "pod", 50) == ["a", "b"]
    assert runner.calls == [
        ["k3s", "kubectl", "--kubeconfig", KUBECONFIG, "logs", "-n", "lab", "pod", "--tail", "50"]
    ]
    assert k3s_logs(runner, KUBECONFIG, "lab", "-x", 5) == []
    assert k3s_logs(runner, KUBECONFIG, "", "pod", 5) == []


def test_owner_naming():
    rs = [{"kind": "ReplicaSet", "name": "web-5d8f9c7b6"}]
    ds = [{"kind": "DaemonSet", "name": "agent-ds"}]
    payload = {
        "items": [
            *_pod("web-5d8f9c7b6-abcde", owners=rs)["items"],
            *_pod("agent-ds-xyz12", owners=ds)["items"],
            *_pod("bare-pod", owners=None)["items"],
        ]
    }
    names = {p.name for p in list_k3s(_runner_for(payload), KUBECONFIG, NODES, now=NOW)}
    assert names == {"web", "agent-ds", "bare-pod"}


def test_unknown_node_and_unscheduled_skipped():
    payload = {
        "items": [
            *_pod("a", node="mystery-node")["items"],
            *_pod("b", node="", phase="Pending")["items"],
            *_pod("c")["items"],
        ]
    }
    programs = list_k3s(_runner_for(payload), KUBECONFIG, NODES, now=NOW)
    assert [p.name for p in programs] == ["c"]


def test_status_mapping():
    ready = [{"ready": True, "restartCount": 0, "state": {"running": {}}}]
    crash = [
        {
            "ready": False,
            "restartCount": 4,
            "state": {"waiting": {"reason": "CrashLoopBackOff"}},
        }
    ]
    cases = {
        "ok": _pod("ok", statuses=ready),
        "crash": _pod("crash", statuses=crash),
        "pend": _pod("pend", phase="Pending"),
        "bad": _pod("bad", phase="Failed"),
        "odd": _pod("odd", phase="Unknown"),
        "notready": _pod("notready", statuses=[{"ready": False, "state": {"running": {}}}]),
    }
    items = [c["items"][0] for c in cases.values()]
    programs = list_k3s(_runner_for({"items": items}), KUBECONFIG, NODES, now=NOW)
    status = {p.name: p.status for p in programs}
    assert status == {
        "ok": "running",
        "crash": "restarting",
        "pend": "restarting",
        "bad": "failed",
        "odd": "unknown",
        "notready": "unknown",
    }
    crash_prog = next(p for p in programs if p.name == "crash")
    assert crash_prog.detail["restarts"] == "4"
    assert crash_prog.uptime_s is None


def test_bad_output_returns_empty():
    assert list_k3s(FakeRunner(RunResult(1, "", "boom")), KUBECONFIG, NODES) == []
    assert list_k3s(FakeRunner(RunResult(0, "not json", "")), KUBECONFIG, NODES) == []
    assert list_k3s(FakeRunner(RunResult(0, "[]", "")), KUBECONFIG, NODES) == []
