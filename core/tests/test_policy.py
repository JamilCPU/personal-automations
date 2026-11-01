import pytest
from pa_core.models import Program
from pa_core.policy import (
    ActionNotAllowed,
    allowed_actions,
    check_action,
    is_system,
    protection_for,
)


def _program(protection, actions):
    return Program(
        id="systemd:x",
        name="x",
        kind="systemd",
        device="pi1",
        status="running",
        system=False,
        protection=protection,
        actions=actions,
    )


def test_locked_has_no_actions():
    assert allowed_actions("systemd", "running", "locked") == []


def test_running_service_actions():
    assert allowed_actions("systemd", "running", "none") == ["stop", "restart"]
    assert allowed_actions("windows-service", "running", "none") == ["stop", "restart"]


def test_stopped_container_actions():
    assert allowed_actions("docker", "stopped", "none") == ["start"]


def test_restarting_and_failed_actions():
    assert allowed_actions("systemd", "restarting", "none") == ["stop", "restart"]
    assert allowed_actions("systemd", "failed", "none") == ["start", "restart"]
    assert allowed_actions("systemd", "failed", "locked") == []
    assert allowed_actions("k3s", "failed", "none") == ["restart"]
    assert allowed_actions("process", "failed", "none") == []


def test_k3s_restart_only():
    assert allowed_actions("k3s", "running", "none") == ["restart"]
    assert allowed_actions("k3s", "stopped", "none") == ["restart"]


def test_process_stop_only():
    assert allowed_actions("process", "running", "none") == ["stop"]
    assert allowed_actions("process", "stopped", "none") == []


def test_ssh_and_agent_locked():
    assert protection_for("systemd", "ssh.service") == "locked"
    assert protection_for("systemd", "pa-agent.service") == "locked"
    assert protection_for("windows-service", "sshd") == "locked"
    assert protection_for("systemd", "ntopng.service") == "none"


def test_extra_locked():
    assert protection_for("docker", "db", frozenset({"db"})) == "locked"


def test_pihole_confirm():
    assert protection_for("systemd", "pihole-FTL.service") == "confirm"
    assert allowed_actions("systemd", "running", "confirm") == ["stop", "restart"]


@pytest.mark.parametrize(
    "name",
    [
        "getty@tty1.service",
        "getty@tty1",
        "systemd-logind.service",
        "cron.service",
        "k3s-agent.service",
    ],
)
def test_is_system_true(name):
    assert is_system("systemd", name)


@pytest.mark.parametrize(
    "name",
    [
        "ntopng.service",
        "snapserver.service",
        "code-server@jamil.service",
        "redis-server.service",
        "bluealsa-aplay.service",
        "pihole-FTL.service",
    ],
)
def test_is_system_false(name):
    assert not is_system("systemd", name)


def test_is_system_k3s_and_others():
    assert is_system("k3s", "coredns", namespace="kube-system")
    assert not is_system("k3s", "mission-control", namespace="lab")
    assert not is_system("docker", "anything")
    assert not is_system("process", "anything")


def test_is_system_windows_service():
    assert is_system("windows-service", "Spooler", binary_path=None)
    assert is_system("windows-service", "Spooler", binary_path=r"c:\windows\system32\spoolsv.exe")
    assert not is_system("windows-service", "MyApp", binary_path=r"D:\Apps\myapp.exe")


def test_check_action_raises():
    locked = _program("locked", [])
    with pytest.raises(ActionNotAllowed):
        check_action(locked, "stop")
    check_action(_program("none", ["stop"]), "stop")


@pytest.mark.parametrize(
    "name",
    [
        "plymouth-start.service",
        "initrd-cleanup.service",
        "modprobe@drm.service",
        "cloud-init-local.service",
        "apt-daily.service",
        "rpi-eeprom-update.service",
        "rpc-statd-notify.service",
        "e2scrub_all.service",
        "sshd-keygen.service",
        "cockpit-wsinstance-http.service",
        "user-runtime-dir@1000.service",
        "logrotate.service",
        "NetworkManager-wait-online.service",
    ],
)
def test_is_system_plumbing_patterns(name):
    assert is_system("systemd", name)


def test_is_system_structural_template_rule():
    assert is_system("systemd", "foo@bar.service", active_state="inactive")
    assert not is_system("systemd", "foo@bar.service", active_state="failed")
    assert not is_system("systemd", "foo@bar.service", active_state="active")
    assert not is_system("systemd", "foo@bar.service")
    assert not is_system("systemd", "code-server@jamil.service", active_state="inactive")
    assert not is_system("systemd", "plain.service", active_state="inactive")
