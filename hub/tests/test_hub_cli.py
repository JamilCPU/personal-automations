from pathlib import Path

import pytest
from cryptography import x509
from pa_hub.cli import build_devices, listen_sockets, main
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
    assert sorted(cfg.clients.values()) == ["mcp-http", "mcp-stdio", "phone"]
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
    assert last.startswith("pa://pair?url=https%3A%2F%2F192.168.0.197%3A7700&token=")
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


def test_add_agent_validates_before_writing(tmp_path: Path) -> None:
    init(tmp_path)
    config = str(tmp_path / "hub.toml")
    assert main(["add-agent", "--device", "pi3", "--token", "T", "--config", config]) == 1
    assert not (tmp_path / "agents" / "pi3" / "token").exists()
    missing = str(tmp_path / "nope.pem")
    args = ["add-agent", "--device", "pi3", "--cert", missing, "--token", "T", "--config", config]
    assert main(args) == 1


async def test_missing_cert_yields_offline_device(tmp_path: Path) -> None:
    init(tmp_path)
    agent_dir = tmp_path / "agents" / "pi3"
    agent_dir.mkdir(parents=True)
    (agent_dir / "token").write_text("T")
    cfg = load_hub_config(tmp_path / "hub.toml", tmp_path / "hub.secrets.toml")
    devices, _ = build_devices(cfg)
    assert isinstance(devices["pi3"], UnavailableDevice)


def test_init_defaults_to_jw_ip(tmp_path: Path) -> None:
    init(tmp_path)
    cfg = load_hub_config(tmp_path / "hub.toml", tmp_path / "hub.secrets.toml")
    local = next(d for d in cfg.devices if d.kind == "local")
    assert local.ip == "192.168.0.197"
    assert cfg.bind == ["192.168.0.197", "127.0.0.1"]
    cert = x509.load_pem_x509_certificate((tmp_path / "hub" / "cert.pem").read_bytes())
    sans = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert {str(ip) for ip in sans.get_values_for_type(x509.IPAddress)} == {
        "192.168.0.197",
        "127.0.0.1",
    }


def test_init_custom_ip_replaces_bind_and_device(tmp_path: Path) -> None:
    assert main(["init", "--out", str(tmp_path), "--ip", "10.1.2.3"]) == 0
    cfg = load_hub_config(tmp_path / "hub.toml", tmp_path / "hub.secrets.toml")
    assert cfg.bind == ["10.1.2.3", "127.0.0.1"]
    assert next(d for d in cfg.devices if d.kind == "local").ip == "10.1.2.3"


def test_bind_accepts_single_string(tmp_path: Path) -> None:
    init(tmp_path)
    toml = tmp_path / "hub.toml"
    text = toml.read_text(encoding="utf-8")
    toml.write_text(text.replace('bind = ["192.168.0.197", "127.0.0.1"]', 'bind = "0.0.0.0"'), encoding="utf-8")
    assert load_hub_config(toml, tmp_path / "hub.secrets.toml").bind == ["0.0.0.0"]


def test_listen_sockets_bind_each_host_once() -> None:
    socks = listen_sockets(["127.0.0.1", "127.0.0.1"], 0)
    try:
        assert len(socks) == 1
        assert socks[0].getsockname()[0] == "127.0.0.1"
    finally:
        for sock in socks:
            sock.close()
