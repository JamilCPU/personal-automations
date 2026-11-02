import pytest
from pa_core.models import Program
from pa_core.policy import (
    ActionNotAllowed,
    allowed_actions,
    check_action,
    is_system,
    is_system_exe,
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


@pytest.mark.parametrize(
    "unit",
    [
        "wpa_supplicant.service",
        "dbus.service",
        "dbus-broker.service",
        "networking.service",
        "dhcpcd.service",
        "systemd-resolved.service",
    ],
)
def test_network_critical_units_locked(unit):
    assert protection_for("systemd", unit) == "locked"
    assert allowed_actions("systemd", "running", protection_for("systemd", unit)) == []


@pytest.mark.parametrize(
    "service", ["WlanSvc", "NlaSvc", "netprofm", "Dhcp", "Dnscache", "mpssvc", "sshd", "dnscache"]
)
def test_network_critical_windows_services_locked(service):
    assert protection_for("windows-service", service) == "locked"


def test_kube_system_pods_have_no_actions():
    assert allowed_actions("k3s", "running", "none", namespace="kube-system") == []
    assert allowed_actions("k3s", "running", "none", namespace="default") == ["restart"]


# ---- Windows auto-filter (real jw listing) ---------------------------------

PF = r"C:\Program Files"
PF86 = r"C:\Program Files (x86)"
DEFENDER = r"C:\ProgramData\Microsoft\Windows Defender\Platform\4.18.26100.4-0"


@pytest.mark.parametrize(
    ("name", "display", "status", "binary"),
    [
        # stopped services
        (
            "BraveElevationService",
            "Brave Elevation Service",
            "stopped",
            PF + r"\BraveSoftware\Brave-Browser\Application\1\elevation_service.exe",
        ),
        (
            "brave",
            "Brave Update Service (brave)",
            "stopped",
            PF86 + r"\BraveSoftware\Update\BraveUpdate.exe",
        ),
        (
            "FvSvc",
            "NVIDIA FrameView SDK service",
            "stopped",
            PF + r"\NVIDIA Corporation\FrameViewSDK\nvfvsdksvc_x64.exe",
        ),
        (
            "WMPNetworkSvc",
            "Windows Media Player Network Sharing Service",
            "stopped",
            PF + r"\Windows Media Player\wmpnetwk.exe",
        ),
        # Microsoft-shipped, running
        (
            "WinDefend",
            "Microsoft Defender Antivirus Service",
            "running",
            DEFENDER + r"\MsMpEng.exe",
        ),
        (
            "WdNisSvc",
            "Microsoft Defender Antivirus Network Inspection Service",
            "running",
            DEFENDER + r"\NisSrv.exe",
        ),
        (
            "MDCoreSvc",
            "Microsoft Defender Core Service",
            "running",
            DEFENDER + r"\MpDefenderCoreService.exe",
        ),
        ("WSLService", "WSL Service", "running", PF + r"\WSL\wslservice.exe"),
        ("EdgeThing", "Edge", "running", PF86 + r"\Microsoft\Edge\Application\x.exe"),
        # background helpers by keyword
        (
            "NvContainerLocalSystem",
            "NVIDIA LocalSystem Container",
            "running",
            PF + r"\NVIDIA Corporation\NvContainer\nvcontainer.exe",
        ),
        ("FooSvc", "Foo Updater", "running", r"D:\Foo\foo.exe"),
        ("CrashHandler", "Crash Reporter", "running", r"D:\Foo\c.exe"),
        ("BarTelemetry", "Bar", "running", r"D:\Bar\b.exe"),
        ("ElevateX", "X Elevation Service", "running", r"D:\X\x.exe"),
        # Windows dir / unknown binary (existing rule)
        ("Spooler", "Print Spooler", "running", r"c:\windows\system32\spoolsv.exe"),
        ("Hidden", "Hidden", "running", None),
    ],
)
def test_windows_service_hidden(name, display, status, binary):
    assert is_system(
        "windows-service", name, binary_path=binary, status=status, display_name=display
    )


@pytest.mark.parametrize(
    ("name", "display", "binary"),
    [
        (
            "CoworkVMService",
            "Claude",
            PF + r"\WindowsApps\Claude_1.49585.0.0_x64__pzs8sxrjxfjjc\app\resources\cowork-svc.exe",
        ),
        (
            "Steam Client Service",
            "Steam Client Service",
            PF86 + r"\Common Files\Steam\steamservice.exe",
        ),
        ("Tailscale", "Tailscale", PF + r"\Tailscale\tailscaled.exe"),
        ("ClickToRunSvc", "Office", PF + r"\Microsoft Office\root\integration\x.exe"),
    ],
)
def test_windows_service_visible(name, display, binary):
    assert not is_system(
        "windows-service", name, binary_path=binary, status="running", display_name=display
    )


@pytest.mark.parametrize(
    "exe",
    [
        r"C:\WINDOWS\explorer.exe",
        PF
        + r"\WindowsApps\Microsoft.WindowsNotepad_11.2507.26.0_x64__8wekyb3d8bbwe\Notepad\Notepad.exe",
        PF + r"\WindowsApps\Microsoft.WindowsTerminal_1.22_x64__8wekyb3d8bbwe\WindowsTerminal.exe",
        PF86 + r"\Microsoft\Edge\Application\msedge.exe",
        PF + r"\Microsoft\Copilot\copilot.exe",
        r"c:\programdata\microsoft\windows defender\x.exe",
        "C:/Program Files/WSL/wsl.exe",
    ],
)
def test_process_exe_hidden(exe):
    assert is_system_exe(exe)


@pytest.mark.parametrize(
    "exe",
    [
        None,
        "",
        PF + r"\WindowsApps\Claude_1.49585.0.0_x64__pzs8sxrjxfjjc\app\claude.exe",
        PF + r"\Microsoft Office\root\Office16\WINWORD.EXE",
        r"C:\Users\jamil\AppData\Local\Discord\app-1.0\Discord.exe",
        PF + r"\BraveSoftware\Brave-Browser\Application\brave.exe",
        r"D:\Microsoft\tool.exe",
    ],
)
def test_process_exe_visible(exe):
    assert not is_system_exe(exe)
