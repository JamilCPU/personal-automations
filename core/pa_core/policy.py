"""Program policy: system-program filter, protection levels, allowed actions."""

import re
from fnmatch import fnmatchcase

from pa_core.models import ActionName, Kind, Program, Protection, Status

LOCKED: frozenset[str] = frozenset(
    {
        "ssh.service",
        "sshd.service",
        "sshd",
        "pa-agent.service",
        "pa-hub",
        "docker.service",
        "containerd.service",
        "k3s.service",
        "k3s-agent.service",
        "NetworkManager.service",
        "systemd-networkd.service",
        # network-critical: stopping any of these cuts the box off the LAN
        "wpa_supplicant.service",
        "dbus.service",
        "dbus-broker.service",
        "networking.service",
        "dhcpcd.service",
        "systemd-resolved.service",
        # Windows network stack and firewall (matched case-insensitively)
        "WlanSvc",
        "NlaSvc",
        "netprofm",
        "Dhcp",
        "Dnscache",
        "mpssvc",
    }
)
KUBE_SYSTEM = "kube-system"
CONFIRM: frozenset[str] = frozenset({"pihole-FTL.service"})

_SYSTEMD_SYSTEM_PATTERNS: tuple[str, ...] = (
    "dbus",
    "getty@*",
    "systemd-*",
    "udev",
    "polkit",
    "cron",
    "rsyslog",
    "avahi-daemon",
    "wpa_supplicant",
    "ModemManager",
    "NetworkManager",
    "bluetooth",
    "user@*",
    "serial-getty@*",
    "triggerhappy",
    "rng-tools*",
    "dphys-swapfile",
    "alsa-*",
    "rtkit-daemon",
    "udisks2",
    "accounts-daemon",
    "colord",
    "cups*",
    "lightdm",
    "upower",
    "packagekit",
    "unattended-upgrades",
    "chrony",
    "rpcbind",
    "nfs-*",
    "containerd",
    "docker",
    "ssh",
    "sshd",
    "k3s*",
    # OS plumbing seen on Raspberry Pi OS / Ubuntu images (boot, first-run, housekeeping)
    "plymouth*",
    "initrd-*",
    "modprobe@*",
    "cloud-*",
    "apt-daily*",
    "rpi-*",
    "rpc-*",
    "e2scrub*",
    "sshd-keygen*",
    "sshd@*",
    "regenerate_ssh_host_keys",
    "cockpit-*",
    "user-runtime-dir@*",
    "netplan-*",
    "apparmor",
    "auth-rpcgss-module",
    "rc-local",
    "keyboard-setup",
    "console-setup",
    "ldconfig",
    "man-db",
    "logrotate",
    "fstrim",
    "dpkg-db-backup",
    "kmod-static-nodes",
    "getty-static",
    "emergency",
    "rescue",
    "NetworkManager-*",
    "log2ram*",
    "glamor-test",
    "rp1-test",
    "sshswitch",
)

# Structural rule: an inactive (not failed) template instance is plumbing unless allowlisted.
_TEMPLATE_PATTERN = "*@*"
_TEMPLATE_ALLOWLIST: tuple[str, ...] = ("code-server@*",)

_WINDOWS_DIR = "c:\\windows\\"
# Microsoft-shipped software outside C:\Windows: a `Microsoft` folder directly under
# Program Files* or ProgramData, Microsoft.* Store packages, and named Microsoft product dirs.
# Matched against a lower-cased, backslash-separated path.
_MICROSOFT_PATH = re.compile(
    r"^[a-z]:\\(?:"
    r"(?:program files(?: \(x86\))?|programdata)\\microsoft\\"
    r"|program files\\windowsapps\\microsoft\."
    r"|program files(?: \(x86\))?\\(?:wsl|windows [^\\]+|internet explorer)\\"
    r")"
)
# Background helpers: updaters, elevation shims, crash/telemetry agents, NVIDIA containers.
_HELPER_WORDS: tuple[str, ...] = ("update", "elevation", "crash", "telemetry", "container")
_SERVICE_SUFFIX = ".service"


class ActionNotAllowed(Exception):
    """Raised when an action is not permitted for a program."""


def _strip_service(name: str) -> str:
    return name.removesuffix(_SERVICE_SUFFIX)


def _is_idle_template(base: str, active_state: str | None) -> bool:
    if active_state != "inactive" or not fnmatchcase(base, _TEMPLATE_PATTERN):
        return False
    return not any(fnmatchcase(base, pattern) for pattern in _TEMPLATE_ALLOWLIST)


def is_system_exe(exe_path: str | None) -> bool:
    """True for executables that Windows or Microsoft ship (case-insensitive, / or \\)."""
    if not exe_path:
        return False
    path = exe_path.replace("/", "\\").lower()
    return path.startswith(_WINDOWS_DIR) or _MICROSOFT_PATH.match(path) is not None


def _is_system_service(
    name: str, binary_path: str | None, status: Status | None, display_name: str
) -> bool:
    if binary_path is None or status == "stopped" or is_system_exe(binary_path):
        return True
    text = f"{name} {display_name}".lower()
    return any(word in text for word in _HELPER_WORDS)


def protection_for(kind: Kind, name: str, extra_locked: frozenset[str] = frozenset()) -> Protection:
    if kind == "windows-service":
        locked = {n.lower() for n in LOCKED | extra_locked}
        if name.lower() in locked:
            return "locked"
        return "none"
    if name in LOCKED or name in extra_locked:
        return "locked"
    if name in CONFIRM:
        return "confirm"
    return "none"


def is_system(
    kind: Kind,
    name: str,
    namespace: str | None = None,
    binary_path: str | None = None,
    active_state: str | None = None,
    status: Status | None = None,
    display_name: str = "",
) -> bool:
    if kind == "systemd":
        base = _strip_service(name)
        if any(fnmatchcase(base, pattern) for pattern in _SYSTEMD_SYSTEM_PATTERNS):
            return True
        return _is_idle_template(base, active_state)
    if kind == "k3s":
        return namespace == KUBE_SYSTEM
    if kind == "windows-service":
        return _is_system_service(name, binary_path, status, display_name)
    return False


def allowed_actions(
    kind: Kind, status: Status, protection: Protection, namespace: str | None = None
) -> list[ActionName]:
    if protection == "locked":
        return []
    if kind == "k3s":
        return [] if namespace == KUBE_SYSTEM else ["restart"]
    if kind == "process":
        return ["stop"] if status == "running" else []
    if status in ("running", "restarting"):
        return ["stop", "restart"]
    if status == "failed":
        return ["start", "restart"]
    return ["start"]


def check_action(program: Program, action: ActionName) -> None:
    if action not in program.actions:
        msg = f"action '{action}' is not allowed for {program.id}"
        raise ActionNotAllowed(msg)
