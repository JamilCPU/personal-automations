import sys

import pytest
from fastapi.testclient import TestClient
from pa_agent.app import create_app, main
from pa_agent.config import AgentConfig, load_config
from pa_agent.device import LinuxDevice
from pa_core.runner import RunResult
from pa_core.tls import fingerprint_sha256
from test_device import USB_SCRIPT, FakeRunner, Scheduler, _base

TOKEN = "secret-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
UNAUTH_BODY = {"success": False, "data": None, "error": "unauthorized"}
DOCKER_ID = "docker:sentinel-ros-lidar-1"


def _cfg(**over) -> AgentConfig:
    base = {
        "device_id": "robot-pi",
        "token": TOKEN,
        "cert_file": "c.pem",
        "key_file": "k.pem",
        "usb_script": USB_SCRIPT,
    }
    return AgentConfig(**{**base, **over})


def _client(cfg=None, runner=None, **kw) -> tuple[TestClient, FakeRunner]:
    cfg = cfg or _cfg()
    runner = runner or FakeRunner(_base())
    device = LinuxDevice(cfg, run=runner, scheduler=Scheduler(), uptime_reader=lambda _r, _u: None)
    return TestClient(create_app(device, cfg), **kw), runner


ROUTES = [
    ("GET", "/v1/health", None),
    ("GET", "/v1/programs", None),
    ("GET", f"/v1/program?id={DOCKER_ID}", None),
    ("GET", f"/v1/logs?id={DOCKER_ID}&lines=5", None),
    ("POST", "/v1/actions", {"id": DOCKER_ID, "action": "restart"}),
    ("POST", "/v1/groups/sentinel-ros/restart", None),
    ("GET", "/v1/usb", None),
    ("PUT", "/v1/usb", {"on": True}),
    ("POST", "/v1/reboot", None),
]


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_every_route_requires_token(method, path, body):
    client, runner = _client()
    for headers in ({}, {"Authorization": "Bearer wrong"}):
        r = client.request(method, path, json=body, headers=headers)
        assert r.status_code == 401
        assert r.json() == UNAUTH_BODY
        assert r.text == '{"success":false,"data":null,"error":"unauthorized"}'
    assert runner.calls == []


def test_unauthorized_beats_validation_error():
    client, _ = _client()
    r = client.post("/v1/actions", json={"nonsense": 1})
    assert r.status_code == 401
    assert r.json() == UNAUTH_BODY


def test_unknown_program_404():
    client, runner = _client()
    r = client.post("/v1/actions", json={"id": "docker:x;rm -rf /", "action": "stop"}, headers=AUTH)
    assert r.status_code == 404
    body = r.json()
    assert body["success"] is False
    assert body["error"].startswith("unknown program:")
    assert not any(call[:2] == ["docker", "stop"] for call in runner.calls)
    assert not any("x;rm" in part for call in runner.calls for part in call)


def test_unknown_program_and_logs_404():
    client, _ = _client()
    assert client.get("/v1/program?id=docker:nope", headers=AUTH).status_code == 404
    assert client.get("/v1/logs?id=docker:nope", headers=AUTH).status_code == 404


def test_unknown_group_404():
    client, _ = _client()
    r = client.post("/v1/groups/nope/restart", headers=AUTH)
    assert r.status_code == 404
    assert r.json()["success"] is False


def test_locked_409():
    client, runner = _client()
    body = {"id": "systemd:ssh.service", "action": "stop"}
    r = client.post("/v1/actions", json=body, headers=AUTH)
    assert r.status_code == 409
    assert r.json()["success"] is False
    assert not any("stop" in call for call in runner.calls)


def test_programs_envelope():
    client, _ = _client()
    r = client.get("/v1/programs", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["error"] is None
    assert {p["kind"] for p in body["data"]} == {"docker", "systemd"}


def test_program_and_action_ok():
    client, runner = _client()
    r = client.get(f"/v1/program?id={DOCKER_ID}", headers=AUTH)
    assert r.json()["data"]["id"] == DOCKER_ID
    r = client.post("/v1/actions", json={"id": DOCKER_ID, "action": "restart"}, headers=AUTH)
    assert r.status_code == 200
    assert r.json()["data"]["ok"] is True
    assert ["docker", "restart", "sentinel-ros-lidar-1"] in runner.calls


def test_logs_default_and_lines():
    client, runner = _client()
    client.get(f"/v1/logs?id={DOCKER_ID}", headers=AUTH)
    client.get(f"/v1/logs?id={DOCKER_ID}&lines=7", headers=AUTH)
    tails = [c[3] for c in runner.calls if c[:3] == ["docker", "logs", "--tail"]]
    assert tails == ["100", "7"]


def test_group_restart_ok():
    client, _ = _client()
    r = client.post("/v1/groups/sentinel-ros/restart", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["success"] is True


def test_health_ok():
    client, _ = _client()
    r = client.get("/v1/health", headers=AUTH)
    assert r.status_code == 200
    assert "cpu_pct" in r.json()["data"]


def test_usb_get_and_put():
    runner = FakeRunner({**_base(), ("sudo",): RunResult(0, "Lidar: on\n", "")})
    client, _ = _client(runner=runner)
    assert client.get("/v1/usb", headers=AUTH).json()["data"]["on"] is True
    r = client.put("/v1/usb", json={"on": False}, headers=AUTH)
    assert r.status_code == 200
    assert ["sudo", "-n", USB_SCRIPT, "off"] in runner.calls


def test_usb_not_supported_404():
    client, _ = _client(cfg=_cfg(usb_script=None))
    for r in (
        client.get("/v1/usb", headers=AUTH),
        client.put("/v1/usb", json={"on": True}, headers=AUTH),
    ):
        assert r.status_code == 404
        assert r.json() == {"success": False, "data": None, "error": "not supported on this device"}


def test_reboot_ok():
    client, _ = _client()
    r = client.post("/v1/reboot", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["data"]["ok"] is True


def test_validation_error_is_envelope():
    client, _ = _client()
    r = client.post("/v1/actions", json={"id": DOCKER_ID, "action": "explode"}, headers=AUTH)
    assert r.status_code == 422
    assert r.json() == {"success": False, "data": None, "error": "invalid request"}
    r = client.get(f"/v1/logs?id={DOCKER_ID}&lines=abc", headers=AUTH)
    assert r.status_code == 422
    assert r.json()["error"] == "invalid request"


def test_unhandled_error_500_envelope(caplog):
    class Boom(FakeRunner):
        def __call__(self, argv):
            msg = "kaboom-secret-detail"
            raise RuntimeError(msg)

    client, _ = _client(runner=Boom(), raise_server_exceptions=False)
    with caplog.at_level("ERROR", logger="pa_agent"):
        r = client.get("/v1/programs", headers=AUTH)
    assert r.status_code == 500
    assert r.json() == {"success": False, "data": None, "error": "internal error"}
    assert "kaboom-secret-detail" not in r.text
    assert "kaboom-secret-detail" in caplog.text


def test_hub_ip_restriction():
    client, runner = _client(cfg=_cfg(hub_ip="10.9.9.9"))
    r = client.get("/v1/programs", headers=AUTH)
    assert r.status_code == 403
    assert r.json() == {"success": False, "data": None, "error": "forbidden"}
    # Strangers get 403 even with no token: the IP check runs before token comparison.
    assert client.get("/v1/programs").status_code == 403
    assert runner.calls == []


def test_hub_ip_match_allowed():
    client, _ = _client(cfg=_cfg(hub_ip="testclient"))
    assert client.get("/v1/programs", headers=AUTH).status_code == 200
    assert client.get("/v1/programs").status_code == 401


def test_cli_init_writes_files_and_refuses_overwrite(tmp_path, capsys):
    out = tmp_path / "agent"
    argv = [
        "init", "--device-id", "pi1", "--ip", "192.0.2.101", "--out", str(out),
        "--usb-script", "/usr/local/bin/lidar-power", "--kubeconfig", "/etc/k3s.yaml",
    ]  # fmt: skip
    assert main(argv) == 0
    for name in ("agent.toml", "cert.pem", "key.pem"):
        assert (out / name).is_file()
    cfg = load_config(str(out / "agent.toml"))
    assert cfg.device_id == "pi1"
    assert cfg.cert_file == str(out / "cert.pem")
    assert cfg.key_file == str(out / "key.pem")
    assert cfg.usb_script == "/usr/local/bin/lidar-power"
    assert cfg.kubeconfig == "/etc/k3s.yaml"
    assert cfg.node_devices == {
        "raspberrypi-1": "pi1",
        "raspberrypi-2": "robot-pi",
        "raspberrypi-3": "pi3",
    }
    assert len(cfg.token) >= 43
    printed = capsys.readouterr().out
    assert cfg.token in printed
    assert fingerprint_sha256((out / "cert.pem").read_bytes()) in printed
    if sys.platform != "win32":
        assert (out / "key.pem").stat().st_mode & 0o077 == 0

    before = (out / "agent.toml").read_text(encoding="utf-8")
    assert main(argv) != 0
    assert "agent.toml" in capsys.readouterr().err
    assert (out / "agent.toml").read_text(encoding="utf-8") == before


def test_cli_init_minimal_has_no_optional_keys(tmp_path):
    out = tmp_path / "a"
    assert main(["init", "--device-id", "pi3", "--ip", "192.0.2.103", "--out", str(out)]) == 0
    cfg = load_config(str(out / "agent.toml"))
    assert cfg.usb_script is None
    assert cfg.kubeconfig is None


@pytest.mark.parametrize("hub_ip", ["10.0.0.5", "::ffff:10.0.0.5"])
def test_hub_ip_treats_ipv4_mapped_ipv6_as_ipv4(hub_ip):
    for client_host in ("10.0.0.5", "::ffff:10.0.0.5"):
        client, _ = _client(cfg=_cfg(hub_ip=hub_ip), client=(client_host, 50000))
        assert client.get("/v1/programs", headers=AUTH).status_code == 200
    client, _ = _client(cfg=_cfg(hub_ip=hub_ip), client=("::ffff:10.0.0.6", 50000))
    assert client.get("/v1/programs", headers=AUTH).status_code == 403


def test_hub_ip_unparseable_client_host_is_forbidden():
    client, _ = _client(cfg=_cfg(hub_ip="10.0.0.5"), client=("not-an-ip", 50000))
    assert client.get("/v1/programs", headers=AUTH).status_code == 403


def test_cli_init_hub_ip_written_to_config(tmp_path):
    out = tmp_path / "a"
    argv = ["init", "--device-id", "pi3", "--ip", "192.0.2.103", "--out", str(out)]
    assert main([*argv, "--hub-ip", "192.0.2.99"]) == 0
    assert load_config(str(out / "agent.toml")).hub_ip == "192.0.2.99"
