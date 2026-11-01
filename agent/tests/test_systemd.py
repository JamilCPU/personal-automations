import json
from pathlib import Path

import pytest
from pa_agent.sources.systemd import list_systemd, systemd_action, systemd_logs
from pa_core.runner import RunResult

FIXTURE = Path(__file__).parent / "fixtures" / "systemctl_units.json"
ROBOT_FIXTURE = Path(__file__).parent / "fixtures" / "systemctl_units_robot_pi.json"
LIST_ARGV = ["systemctl", "list-units", "--type=service", "--all", "--output=json", "--no-pager"]


class FakeRunner:
    def __init__(self, result: RunResult):
        self.result = result
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> RunResult:
        self.calls.append(argv)
        return self.result


def _units(*units: dict) -> FakeRunner:
    return FakeRunner(RunResult(0, json.dumps(list(units)), ""))


def _unit(name: str, load="loaded", active="active", sub="running") -> dict:
    return {"unit": name, "load": load, "active": active, "sub": sub, "description": f"{name} d"}


def _fixture_programs():
    runner = FakeRunner(RunResult(0, FIXTURE.read_text(encoding="utf-8"), ""))
    return runner, list_systemd(runner, "pi3")


def test_pi3_programs_visible():
    runner, programs = _fixture_programs()
    assert runner.calls == [LIST_ARGV]
    visible = {p.name for p in programs if not p.system}
    assert {
        "ntopng.service",
        "snapserver.service",
        "code-server@jamil.service",
        "redis-server.service",
        "bluealsa-aplay.service",
    } <= visible
    ntopng = next(p for p in programs if p.name == "ntopng.service")
    assert ntopng.id == "systemd:ntopng.service"
    assert ntopng.kind == "systemd"
    assert ntopng.device == "pi3"
    assert ntopng.uptime_s is None
    assert ntopng.description


def test_not_found_units_skipped():
    names = {p.name for p in _fixture_programs()[1]}
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    missing = {u["unit"] for u in raw if u["load"] == "not-found"}
    assert missing
    assert not names & missing


def test_ssh_locked_no_actions():
    ssh = next(p for p in _fixture_programs()[1] if p.name == "ssh.service")
    assert ssh.protection == "locked"
    assert ssh.actions == []
    assert ssh.system is True


def test_failed_unit_status():
    (p,) = list_systemd(_units(_unit("job.service", active="failed", sub="failed")), "pi1")
    assert p.status == "failed"
    assert p.actions == ["start", "restart"]


@pytest.mark.parametrize(
    ("active", "sub", "status", "actions"),
    [
        ("active", "running", "running", ["stop", "restart"]),
        ("active", "exited", "running", ["stop", "restart"]),
        ("inactive", "dead", "stopped", ["start"]),
        ("failed", "failed", "failed", ["start", "restart"]),
        ("activating", "start", "restarting", ["stop", "restart"]),
        ("reloading", "reload", "restarting", ["stop", "restart"]),
        ("deactivating", "stop-sigterm", "unknown", ["start"]),
    ],
)
def test_status_mapping(active, sub, status, actions):
    (p,) = list_systemd(_units(_unit("x.service", active=active, sub=sub)), "pi1")
    assert (p.status, p.actions) == (status, actions)


def test_pihole_needs_confirm():
    (p,) = list_systemd(_units(_unit("pihole-FTL.service")), "pi1")
    assert p.protection == "confirm"


def test_list_failure_and_bad_json_give_empty():
    assert list_systemd(FakeRunner(RunResult(1, "", "boom")), "pi1") == []
    assert list_systemd(FakeRunner(RunResult(0, "not json", "")), "pi1") == []
    assert list_systemd(FakeRunner(RunResult(0, '{"a": 1}', "")), "pi1") == []


def test_action_uses_sudo_n():
    runner = FakeRunner(RunResult(0, "", ""))
    result = systemd_action(runner, "ntopng.service", "restart")
    assert runner.calls == [["sudo", "-n", "systemctl", "restart", "ntopng.service"]]
    assert result.ok is True


def test_action_failure_reports_stderr():
    result = systemd_action(FakeRunner(RunResult(1, "", "denied\n")), "x.service", "stop")
    assert (result.ok, result.message) == (False, "denied")


@pytest.mark.parametrize("unit", ["", "-rf", "--now"])
def test_action_rejects_unsafe_names(unit):
    runner = FakeRunner(RunResult(0, "", ""))
    assert systemd_action(runner, unit, "start").ok is False
    assert runner.calls == []


def test_logs_argv_and_split():
    runner = FakeRunner(RunResult(0, "a\nb\n", ""))
    assert systemd_logs(runner, "ntopng.service", 50) == ["a", "b"]
    assert runner.calls == [
        ["journalctl", "-u", "ntopng.service", "-n", "50", "--no-pager", "-o", "short-iso"]
    ]


def test_logs_rejects_unsafe_unit():
    runner = FakeRunner(RunResult(0, "a", ""))
    assert systemd_logs(runner, "-f", 5) == []
    assert runner.calls == []


def test_robot_pi_visible_units_are_useful():
    runner = FakeRunner(RunResult(0, ROBOT_FIXTURE.read_text(encoding="utf-8"), ""))
    visible = {p.name for p in list_systemd(runner, "robot-pi") if not p.system}
    assert visible == {
        "pa-agent.service",
        "cockpit.service",
        "code-server@jamil.service",
        "upload_recordings.service",
    }


def test_inactive_template_unit_is_system_but_failed_is_not():
    runner = _units(
        _unit("foo@bar.service", active="inactive", sub="dead"),
        _unit("foo@baz.service", active="failed", sub="failed"),
        _unit("foo@qux.service", active="active", sub="running"),
        _unit("code-server@x.service", active="inactive", sub="dead"),
    )
    by_name = {p.name: p.system for p in list_systemd(runner, "d")}
    assert by_name == {
        "foo@bar.service": True,
        "foo@baz.service": False,
        "foo@qux.service": False,
        "code-server@x.service": False,
    }
