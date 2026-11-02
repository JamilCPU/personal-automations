"""Agent configuration, loaded from a TOML file."""

import tomllib

from pydantic import BaseModel, Field


class AgentConfig(BaseModel):
    device_id: str
    token: str
    bind: str = "0.0.0.0"
    port: int = 7701
    cert_file: str
    key_file: str
    usb_script: str | None = None
    kubeconfig: str | None = None
    node_devices: dict[str, str] = Field(default_factory=dict)
    hub_ip: str | None = None


def load_config(path: str) -> AgentConfig:
    with open(path, "rb") as handle:
        return AgentConfig.model_validate(tomllib.load(handle))
