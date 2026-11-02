from pathlib import Path

import pytest
from pa_agent.config import AgentConfig, load_config
from pa_agent.device import LinuxDevice, systemd_uptime_s
from pa_core.policy import ActionNotAllowed
from pa_core.runner import RunResult

HERE = Path(__file__).parent
DOCKER_FIXTURE = HERE.parent.parent / "core" / "tests" / "fixtures" / "docker_ps.jsonl"
SYSTEMD_FIXTURE = HERE / "fixtures" / "systemctl_units.json"
K3S_FIXTURE = HERE / "fixtures" / "k3s_pods.json"

OK = RunResult(0, "", "")
REBOOT_ARGV = ["sudo", "-n", "systemctl", "reboot"]
USB_SCRIPT = "/usr/local/bin/lidar-power"


class FakeRunner:
    """Answers by longest matching argv prefix; unknown commands succeed with no output."""

    def __init__(self, responses: dict[tuple[str, ...], RunResult] | None = None):
        self.responses = responses or {}
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> RunResult:
        self.calls.append(list(argv))
        best: RunResult | None = None
        best_len = -1
        for prefix, result in self.responses.items():
            if tuple(argv[: len(prefix)]) == prefix and len(prefix) > best_len:
                best, best_len = result, len(prefix)
        return best if best is not None else OK


class Scheduler:
    def __init__(self):
        self.calls: list[tuple[float, object]] = []

    def __call__(self, delay, fn):
        self.calls.append((delay, fn))


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _base() -> dict[tuple[str, ...], RunResult]:
    return {
        ("docker", "ps"): RunResult(0, _read(DOCKER_FIXTURE), ""),
        ("systemctl", "list-units"): RunResult(0, _read(SYSTEMD_FIXTURE), ""),
    }


def _cfg(**over) -> AgentConfig:
    base = {
        "device_id": "robot-pi",
        "token": "t",
        "cert_file": "c.pem",
        "key_file": "k.pem",
        "usb_script": USB_SCRIPT,
    }
    return AgentConfig(**{**base, **over})


def _device(runner=None, cfg=None, scheduler=None, **kw) -> tuple[LinuxDevice, FakeRunner]:
    runner = runner or FakeRunner(_base())
    device = LinuxDevice(
        cfg or _cfg(),
        run=runner,
        scheduler=scheduler or Scheduler(),
        uptime_reader=kw.pop("uptime_reader", lambda _r, _u: None),
        **kw,
    )
    return device, runner


def test_unknown_program_keyerror():
    device, runner = _device()
    bad = "docker:x;rm -rf /"
    with pytest.raises(KeyError):
        device.act(bad, "stop")
    assert not any("x;rm" in part for call in runner.calls for part in call)
    assert not any(call[:2] == ["docker", "stop"] for call in runner.calls)


def test_locked_action_refused():
    device, runner = _device()
    with pytest.raises(ActionNotAllowed):
        device.act("systemd:ssh.service", "stop")
    assert not any("stop" in call for call in runner.calls)


def test_programs_merges_docker_and_systemd():
    device, _ = _device()
    assert {p.kind for p in device.programs()} == {"docker", "systemd"}


def test_programs_includes_k3s_when_kubeconfig_set():
    responses = _base()
    responses[("k3s", "kubectl")] = RunResult(0, _read(K3S_FIXTURE), "")
    cfg = _cfg(kubeconfig="/k", node_devices={"raspberrypi-1": "pi1"})
    device, _ = _device(FakeRunner(responses), cfg)
    k3s = [p for p in device.programs() if p.kind == "k3s"]
    assert k3s
    assert {p.device for p in k3s} == {"pi1"}


def test_docker_action_uses_container_name():
    device, runner = _device()
    assert device.act("docker:sentinel-ros-lidar-1", "restart").ok
    assert ["docker", "restart", "sentinel-ros-lidar-1"] in runner.calls


def test_k3s_restart_uses_current_pod_name():
    responses = _base()
    responses[("k3s", "kubectl")] = RunResult(0, _read(K3S_FIXTURE), "")
    cfg = _cfg(kubeconfig="/k", node_devices={"raspberrypi-1": "pi1"})
    device, runner = _device(FakeRunner(responses), cfg)
    target = next(p for p in device.programs() if p.kind == "k3s" and "restart" in p.actions)
    assert device.act(target.id, "restart").ok
    expected = [
        "k3s", "kubectl", "--kubeconfig", "/k", "delete", "pod", "-n",
        target.detail["namespace"], target.detail["pod"], "--wait=false",
    ]  # fmt: skip
    assert expected in runner.calls


def test_logs_clamped():
    device, runner = _device()
    device.logs("docker:sentinel-ros-lidar-1", 10_000)
    device.logs("docker:sentinel-ros-lidar-1", -5)
    tails = [c[3] for c in runner.calls if c[:3] == ["docker", "logs", "--tail"]]
    assert tails == ["500", "1"]


def test_program_systemd_uptime_filled():
    device, _ = _device(uptime_reader=lambda _r, unit: 42 if unit == "cockpit.service" else None)
    assert device.program("systemd:cockpit.service").uptime_s == 42


def test_systemd_uptime_parsing(tmp_path):
    proc = tmp_path / "uptime"
    proc.write_text("1000.50 2000.00\n", encoding="utf-8")
    runner = FakeRunner({("systemctl", "show"): RunResult(0, "400000000\n", "")})
    assert systemd_uptime_s(runner, "x.service", str(proc)) == 600
    bad = FakeRunner({("systemctl", "show"): RunResult(0, "n/a\n", "")})
    assert systemd_uptime_s(bad, "x.service", str(proc)) is None
    assert systemd_uptime_s(runner, "x.service", str(tmp_path / "missing")) is None


def test_group_restart_robot():
    device, runner = _device()
    assert device.group_restart("sentinel-ros").ok
    restart = [c for c in runner.calls if c[:2] == ["docker", "restart"]]
    assert len(restart) == 1
    assert sorted(restart[0][2:]) == [
        "sentinel-ros-foxglove-1",
        "sentinel-ros-lidar-1",
        "sentinel-ros-rosbridge-1",
    ]


def test_group_restart_unknown_project():
    device, _ = _device()
    with pytest.raises(KeyError):
        device.group_restart("nope")


def test_health_with_thermal(tmp_path):
    thermal = tmp_path / "temp"
    thermal.write_text("48312\n", encoding="utf-8")
    device, _ = _device(thermal_path=str(thermal))
    health = device.health()
    assert health.temp_c == pytest.approx(48.312)
    assert 0 <= health.mem_pct <= 100
    assert 0 <= health.disk_pct <= 100
    assert health.uptime_s >= 0


def test_health_missing_thermal(tmp_path):
    device, _ = _device(thermal_path=str(tmp_path / "absent"))
    assert device.health().temp_c is None


def test_usb_status_parse():
    on = FakeRunner({("sudo",): RunResult(0, "Lidar: on\nports: 4\n", "")})
    device, runner = _device(on)
    assert device.usb().on is True
    assert runner.calls == [["sudo", "-n", USB_SCRIPT, "status"]]
    off = FakeRunner({("sudo",): RunResult(0, "Lidar: off\n", "")})
    assert _device(off)[0].usb().on is False


def test_usb_on_failure_message():
    msg = "Error: /dev/ldlidar did not appear after power-on. Is the lidar plugged in?"
    device, runner = _device(FakeRunner({("sudo",): RunResult(1, "", msg)}))
    result = device.set_usb(True)
    assert result.ok is False
    assert "did not appear" in result.message
    assert runner.calls == [["sudo", "-n", USB_SCRIPT, "on"]]


def test_usb_off_success_message():
    runner = FakeRunner({("sudo",): RunResult(0, "USB off\n", "")})
    result = _device(runner)[0].set_usb(False)
    assert result.ok is True
    assert result.message == "USB off"


def test_no_usb_script_not_implemented():
    device, _ = _device(cfg=_cfg(usb_script=None))
    with pytest.raises(NotImplementedError):
        device.usb()
    with pytest.raises(NotImplementedError):
        device.set_usb(True)


def test_reboot_is_deferred():
    scheduler = Scheduler()
    device, runner = _device(scheduler=scheduler)
    result = device.reboot()
    assert result.ok is True
    assert result.message == "Rebooting in 2 s"
    assert REBOOT_ARGV not in runner.calls
    assert len(scheduler.calls) == 1
    delay, fn = scheduler.calls[0]
    assert delay == 2.0
    fn()
    assert REBOOT_ARGV in runner.calls


def test_load_config(tmp_path):
    path = tmp_path / "agent.toml"
    path.write_text(
        'device_id = "pi3"\ntoken = "t"\ncert_file = "c"\nkey_file = "k"\n'
        '[node_devices]\nraspberrypi-3 = "pi3"\n',
        encoding="utf-8",
    )
    cfg = load_config(str(path))
    assert cfg.port == 7701
    assert cfg.node_devices == {"raspberrypi-3": "pi3"}
    assert cfg.usb_script is None
