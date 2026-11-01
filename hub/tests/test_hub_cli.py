from pathlib import Path

import pytest
from pa_hub.cli import build_devices, main
from pa_hub.clients import DeviceOffline, UnavailableDevice
from pa_hub.config import load_hub_config


def init(tmp_path: Path) -> Path:
    assert main(["init", "--out", str(tmp_path)]) == 0
    return tmp_path


def test_init_writes_files_and_refuses_overwrite(tmp_path: Path, capsys) -> None:
    init(tmp_path)
    for name in ("hub.toml", "hub.secrets.toml", "hub/cert.pem", "hub/key.pem"):
        assert (tmp_path / name).is_file()
    cfg = load_hub_config(tmp_path / "hub.toml", tmp_path / "hub.secrets.toml")
    assert sorted(cfg.clients.values()) == ["mcp", "phone"]
    assert len(cfg.devices) == 4
    assert cfg.cert_file == str(tmp_path / "hub" / "cert.pem")
    out = capsys.readouterr().out
    assert not any(token in out for token in cfg.clients)
    assert main(["init", "--out", str(tmp_path)]) == 1


def test_pair_prints_uri_with_token(tmp_path: Path, capsys) -> None:
    init(tmp_path)
    cfg = load_hub_config(tmp_path / "hub.toml", tmp_path / "hub.secrets.toml")
    capsys.readouterr()
    assert main(["pair", "--client", "phone", "--config", str(tmp_path / "hub.toml")]) == 0
    last = capsys.readouterr().out.strip().splitlines()[-1]
    phone = next(t for t, n in cfg.clients.items() if n == "phone")
    assert last.startswith("pa://pair?url=https%3A%2F%2F192.0.2.99%3A7700&token=")
    assert phone in last
    assert main(["pair", "--client", "nobody", "--config", str(tmp_path / "hub.toml")]) == 1


def test_add_agent_copies_files(tmp_path: Path) -> None:
    init(tmp_path)
    cert = tmp_path / "src.pem"
    cert.write_text("CERT")
    config = str(tmp_path / "hub.toml")
    assert main(["add-agent", "--device", "pi3", "--config", config]) == 1
    args = ["add-agent", "--device", "pi3", "--cert", str(cert), "--token", "T", "--config", config]
    assert main(args) == 0
    assert (tmp_path / "agents" / "pi3" / "token").read_text() == "T"
    assert main(["add-agent", "--device", "jw-pc", "--config", config]) == 1


async def test_undeployed_agent_becomes_offline_stand_in(tmp_path: Path) -> None:
    init(tmp_path)
    cfg = load_hub_config(tmp_path / "hub.toml", tmp_path / "hub.secrets.toml")
    devices, _ = build_devices(cfg)
    assert isinstance(devices["pi3"], UnavailableDevice)
    with pytest.raises(DeviceOffline):
        await devices["pi3"].health()
