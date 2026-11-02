from pathlib import Path

import pytest
from pa_core.docker import (
    docker_action,
    docker_group_restart,
    docker_logs,
    list_docker,
    parse_running_for,
)
from pa_core.runner import RunResult

FIXTURE = Path(__file__).parent / "fixtures" / "docker_ps.jsonl"


class FakeRunner:
    def __init__(self, result: RunResult):
        self.result = result
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> RunResult:
        self.calls.append(argv)
        return self.result


def _ps_runner() -> FakeRunner:
    return FakeRunner(RunResult(0, FIXTURE.read_text(encoding="utf-8"), ""))


def test_lists_robot_containers():
    runner = _ps_runner()
    programs = list_docker(runner, "robot-pi")
    assert runner.calls == [["docker", "ps", "-a", "--no-trunc", "--format", "{{json .}}"]]
    assert {p.name for p in programs} == {"lidar", "foxglove", "rosbridge"}
    assert all(p.group == "sentinel-ros" for p in programs)
    assert all(p.id.startswith("docker:sentinel-ros-") for p in programs)
    assert all(p.kind == "docker" and p.device == "robot-pi" for p in programs)


def test_running_container_policy_and_uptime():
    p = next(p for p in list_docker(_ps_runner(), "robot-pi") if p.name == "lidar")
    assert p.status == "running"
    assert p.system is False
    assert p.protection == "none"
    assert p.actions == ["stop", "restart"]
    assert p.uptime_s == 6 * 3600


def test_docker_down_returns_empty():
    assert list_docker(FakeRunner(RunResult(1, "", "Cannot connect")), "d") == []


def test_docker_cli_missing_returns_empty():
    assert list_docker(FakeRunner(RunResult(127, "", "docker not found")), "d") == []


def _line(name: str, state: str, labels: str = "", running_for: str = "1 day ago") -> str:
    import json

    return json.dumps({"Names": name, "State": state, "Labels": labels, "RunningFor": running_for})


@pytest.mark.parametrize(
    ("state", "status", "uptime", "actions"),
    [
        ("running", "running", 86400, ["stop", "restart"]),
        ("exited", "stopped", None, ["start"]),
        ("created", "stopped", None, ["start"]),
        ("restarting", "restarting", None, ["stop", "restart"]),
        ("dead", "failed", None, ["start", "restart"]),
        ("weird", "unknown", None, ["start"]),
    ],
)
def test_state_mapping(state, status, uptime, actions):
    out = FakeRunner(RunResult(0, _line("web", state) + "\n", ""))
    (p,) = list_docker(out, "d")
    assert (p.status, p.uptime_s, p.actions) == (status, uptime, actions)
    assert p.name == "web"
    assert p.group is None
    assert p.id == "docker:web"


def test_bad_lines_are_skipped():
    out = FakeRunner(RunResult(0, "not json\n\n" + _line("ok", "running") + "\n[1]\n", ""))
    assert [p.name for p in list_docker(out, "d")] == ["ok"]


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("Less than a second ago", 0),
        ("5 seconds ago", 5),
        ("About a minute ago", 60),
        ("5 minutes ago", 300),
        ("About an hour ago", 3600),
        ("6 hours ago", 21600),
        ("2 days ago", 172800),
        ("3 weeks ago", 3 * 604800),
        ("2 months ago", 2 * 2592000),
        ("About a year ago", 31536000),
        ("garbage", None),
        ("", None),
    ],
)
def test_parse_running_for(text, seconds):
    assert parse_running_for(text) == seconds


def test_action_argv():
    runner = FakeRunner(RunResult(0, "sentinel-ros-lidar-1\n", ""))
    result = docker_action(runner, "sentinel-ros-lidar-1", "restart")
    assert runner.calls == [["docker", "restart", "sentinel-ros-lidar-1"]]
    assert result.ok is True


def test_action_failure_message_is_stderr():
    runner = FakeRunner(RunResult(1, "", "no such container\n"))
    result = docker_action(runner, "x", "stop")
    assert (result.ok, result.message) == (False, "no such container")


def test_action_rejects_option_like_container():
    runner = FakeRunner(RunResult(0, "", ""))
    result = docker_action(runner, "--help", "stop")
    assert result.ok is False
    assert runner.calls == []


def test_group_restart_argv():
    runner = FakeRunner(RunResult(0, "", ""))
    result = docker_group_restart(runner, "sentinel-ros", ["a", "b"])
    assert runner.calls == [["docker", "restart", "a", "b"]]
    assert result.ok is True


def test_group_restart_empty_is_failure():
    runner = FakeRunner(RunResult(0, "", ""))
    result = docker_group_restart(runner, "p", [])
    assert result.ok is False
    assert runner.calls == []


def test_logs_tail():
    runner = FakeRunner(RunResult(0, "a\nb\n", "c\n"))
    lines = docker_logs(runner, "web", 50)
    assert runner.calls == [["docker", "logs", "--tail", "50", "web"]]
    assert lines == ["a", "b", "c"]


def test_logs_rejects_option_like_container():
    runner = FakeRunner(RunResult(0, "", ""))
    assert docker_logs(runner, "-f", 10) == []
    assert runner.calls == []


def test_pin_key_is_container_id():
    programs = list_docker(_ps_runner(), "robot-pi")
    assert programs
    assert all(p.pin_key == p.id for p in programs)
