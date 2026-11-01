"""Hub configuration: hub.toml merged with hub.secrets.toml (tokens)."""

import os
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

DEFAULT_PORT = 7700
DEFAULT_TIMEOUT_S = 3.0


def default_db_path() -> str:
    r"""Return the default history.db path under LOCALAPPDATA, resolved when called."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return str(Path(base) / "personal-automations" / "history.db")


class DeviceEntry(BaseModel):
    id: str
    name: str
    role: str
    ip: str
    kind: Literal["agent", "local"]
    url: str | None = None
    cert_file: str | None = None
    token: str | None = None
    token_file: str | None = None
    k3s_source: bool = False
    capabilities: list[str] = Field(default_factory=list)


class HubConfig(BaseModel):
    bind: str
    port: int = DEFAULT_PORT
    cert_file: str
    key_file: str
    clients: dict[str, str] = Field(default_factory=dict)
    devices: list[DeviceEntry] = Field(default_factory=list)
    db_path: str = Field(default_factory=default_db_path)
    timeout_s: float = DEFAULT_TIMEOUT_S


def _expand(value: str | None) -> str | None:
    return None if value is None else os.path.expandvars(os.path.expanduser(value))


def _read_token_file(path: str | None) -> str | None:
    """Return the stripped token in `path`, or None when unset/unreadable/empty."""
    if path is None:
        return None
    try:
        text = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def _resolve_device(raw: dict, device_tokens: dict[str, str]) -> DeviceEntry:
    entry = DeviceEntry(**raw)
    token = device_tokens.get(entry.id) or entry.token
    token_file = _expand(entry.token_file)
    if token is None:
        token = _read_token_file(token_file)
    return entry.model_copy(
        update={
            "cert_file": _expand(entry.cert_file),
            "token_file": token_file,
            "token": token,
        }
    )


def load_hub_config(path: str | Path, secrets_path: str | Path) -> HubConfig:
    """Merge hub.toml with hub.secrets.toml.

    Secrets file shape (never committed):
      [clients]        # bearer token = client name
      "<token>" = "phone"
      [device_tokens]  # device id = agent token (overrides token_file)
      pi1 = "<token>"
    A device's token otherwise comes from its `token_file`; a missing/unreadable
    file leaves token None (RemoteAgentClient then refuses to be constructed).
    A missing secrets file is tolerated (no clients, no overrides).
    """
    with Path(path).open("rb") as handle:
        raw = tomllib.load(handle)
    secrets: dict = {}
    secrets_file = Path(secrets_path)
    if secrets_file.exists():
        with secrets_file.open("rb") as handle:
            secrets = tomllib.load(handle)
    device_tokens = secrets.get("device_tokens", {})
    devices = [_resolve_device(d, device_tokens) for d in raw.pop("devices", [])]
    clients = {**raw.pop("clients", {}), **secrets.get("clients", {})}
    raw["cert_file"] = _expand(raw["cert_file"])
    raw["key_file"] = _expand(raw["key_file"])
    return HubConfig(**raw, clients=clients, devices=devices)
