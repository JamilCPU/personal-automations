"""Program policy: system-program filter, protection levels, allowed actions."""

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
    }
)
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
)

_WINDOWS_DIR = "c:\\windows\\"
_SERVICE_SUFFIX = ".service"


class ActionNotAllowed(Exception):
    """Raised when an action is not permitted for a program."""


def _strip_service(name: str) -> str:
    return name.removesuffix(_SERVICE_SUFFIX)


def protection_for(
    kind: Kind, name: str, extra_locked: frozenset[str] = frozenset()
) -> Protection:
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
) -> bool:
    if kind == "systemd":
        base = _strip_service(name)
        return any(fnmatchcase(base, pattern) for pattern in _SYSTEMD_SYSTEM_PATTERNS)
    if kind == "k3s":
        return namespace == "kube-system"
    if kind == "windows-service":
        return binary_path is None or binary_path.lower().startswith(_WINDOWS_DIR)
    return False


def allowed_actions(
    kind: Kind, status: Status, protection: Protection
) -> list[ActionName]:
    if protection == "locked":
        return []
    if kind == "k3s":
        return ["restart"]
    if kind == "process":
        return ["stop"] if status == "running" else []
    if status == "running":
        return ["stop", "restart"]
    return ["start"]


def check_action(program: Program, action: ActionName) -> None:
    if action not in program.actions:
        msg = f"action '{action}' is not allowed for {program.id}"
        raise ActionNotAllowed(msg)
