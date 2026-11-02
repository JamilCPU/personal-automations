import socket
import ssl
import threading
from pathlib import Path

import httpx
import pytest
import respx
from pa_core.models import ActionResult, Program
from pa_core.policy import ActionNotAllowed
from pa_core.tls import make_self_signed
from pa_hub.clients import DeviceOffline, HubAgentError, RemoteAgentClient, build_ssl_context
from pa_hub.config import DeviceEntry, load_hub_config

BASE = "https://10.0.0.5:7701"
HUB_DIR = Path(__file__).parent.parent


@pytest.fixture
def cert_file(tmp_path: Path) -> str:
    cert, _ = make_self_signed("pi-test", ["10.0.0.5"])
    path = tmp_path / "cert.pem"
    path.write_bytes(cert)
    return str(path)


@pytest.fixture
def entry(cert_file: str) -> DeviceEntry:
    return DeviceEntry(
        id="pi-test",
        name="Pi",
        role="Network",
        ip="10.0.0.5",
        kind="agent",
        url=BASE,
        cert_file=cert_file,
        token="tok123",
        capabilities=["usb", "reboot"],
    )


@pytest.fixture
def client(entry: DeviceEntry) -> RemoteAgentClient:
    return RemoteAgentClient(entry, timeout_s=1.0)


def envelope(data=None, error=None):
    return {"success": error is None, "data": data, "error": error}


PROGRAM = {
    "id": "docker:web",
    "name": "web",
    "kind": "docker",
    "device": "pi-test",
    "status": "running",
    "system": False,
    "protection": "none",
    "actions": ["stop", "restart"],
}


@respx.mock
async def test_unwraps_programs(client):
    respx.get(f"{BASE}/v1/programs").respond(200, json=envelope([PROGRAM]))
    programs = await client.programs()
    assert [type(p) for p in programs] == [Program]
    assert programs[0].id == "docker:web"


@respx.mock
async def test_404_to_keyerror(client):
    respx.get(f"{BASE}/v1/program").respond(404, json=envelope(error="unknown program: x"))
    with pytest.raises(KeyError, match="unknown program: x"):
        await client.program("x")


@respx.mock
async def test_409_to_not_allowed(client):
    respx.post(f"{BASE}/v1/actions").respond(409, json=envelope(error="locked"))
    with pytest.raises(ActionNotAllowed, match="locked"):
        await client.act("docker:web", "stop")


@respx.mock
@pytest.mark.parametrize("exc", [httpx.ConnectTimeout("t"), httpx.ConnectError("c")])
async def test_timeout_to_offline(client, exc):
    respx.get(f"{BASE}/v1/health").mock(side_effect=exc)
    with pytest.raises(DeviceOffline, match="pi-test"):
        await client.health()


@respx.mock
async def test_sends_bearer(client):
    route = respx.get(f"{BASE}/v1/programs").respond(200, json=envelope([]))
    await client.programs()
    assert route.calls.last.request.headers["authorization"] == "Bearer tok123"


@respx.mock
@pytest.mark.parametrize("status", [401, 403, 500])
async def test_auth_and_server_errors_are_not_offline(client, status):
    respx.get(f"{BASE}/v1/programs").respond(status, json=envelope(error="nope"))
    with pytest.raises(HubAgentError) as info:
        await client.programs()
    assert info.value.status == status
    assert info.value.device_id == "pi-test"


@respx.mock
async def test_group_restart_url_encodes_project(client):
    route = respx.post(url__regex=r".*/v1/groups/.*/restart").respond(
        200, json=envelope({"ok": True, "message": "ok"})
    )
    await client.group_restart("my proj/x")
    assert route.calls.last.request.url.raw_path == b"/v1/groups/my%20proj%2Fx/restart"


@respx.mock
async def test_logs_usb_reboot_paths(client):
    respx.get(f"{BASE}/v1/logs", params={"id": "a", "lines": "5"}).respond(
        200, json=envelope(["l1"])
    )
    put = respx.put(f"{BASE}/v1/usb").respond(200, json=envelope({"ok": True, "message": ""}))
    respx.post(f"{BASE}/v1/reboot").respond(200, json=envelope({"ok": True, "message": ""}))
    assert await client.logs("a", 5) == ["l1"]
    assert isinstance(await client.set_usb(True), ActionResult)
    assert put.calls.last.request.content == b'{"on":true}'
    assert isinstance(await client.reboot(), ActionResult)


def test_capabilities_from_entry(client):
    assert client.capabilities == {"usb", "reboot"}


def test_requires_token(entry):
    with pytest.raises(ValueError, match="token"):
        RemoteAgentClient(entry.model_copy(update={"token": None}), timeout_s=1.0)


def _tls_server(tmp_path: Path, cert: bytes, key: bytes) -> socket.socket:
    (tmp_path / "srv-cert.pem").write_bytes(cert)
    (tmp_path / "srv-key.pem").write_bytes(key)
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(tmp_path / "srv-cert.pem", tmp_path / "srv-key.pem")
    sock = socket.create_server(("127.0.0.1", 0))
    sock.listen(2)

    def serve() -> None:
        for _ in range(2):
            try:
                conn, _addr = sock.accept()
                with server_ctx.wrap_socket(conn, server_side=True):
                    pass
            except OSError:
                continue

    threading.Thread(target=serve, daemon=True).start()
    return sock


def _handshake(ctx: ssl.SSLContext, port: int) -> None:
    with (
        socket.create_connection(("127.0.0.1", port), timeout=2) as raw,
        ctx.wrap_socket(raw, server_hostname="127.0.0.1"),
    ):
        pass


def test_ssl_context_pins_own_cert_and_rejects_others(tmp_path):
    cert, key = make_self_signed("srv", ["127.0.0.1"])
    other_cert, _ = make_self_signed("srv", ["127.0.0.1"])
    (tmp_path / "pinned.pem").write_bytes(cert)
    (tmp_path / "other.pem").write_bytes(other_cert)
    sock = _tls_server(tmp_path, cert, key)
    port = sock.getsockname()[1]
    pinned = build_ssl_context(str(tmp_path / "pinned.pem"))
    assert pinned.check_hostname is True
    assert pinned.verify_mode == ssl.CERT_REQUIRED
    _handshake(pinned, port)  # pinned cert + IP SAN verifies
    with pytest.raises(ssl.SSLError):
        _handshake(build_ssl_context(str(tmp_path / "other.pem")), port)
    sock.close()


def test_config_merges_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "lad"))
    secrets = tmp_path / "hub.secrets.toml"
    secrets.write_text(
        '[clients]\n"phone-token" = "phone"\n"mcp-token" = "mcp"\n'
        '[device_tokens]\npi1 = "pi1-token"\n',
        encoding="utf-8",
    )
    cfg = load_hub_config(HUB_DIR / "hub.example.toml", secrets)
    assert cfg.clients == {"phone-token": "phone", "mcp-token": "mcp"}
    by_id = {d.id: d for d in cfg.devices}
    assert {d.id: d.name for d in cfg.devices} == {
        "jw-pc": "jw PC",
        "robot-pi": "Robot Pi",
        "pi1": "Pi1",
        "pi3": "Pi3",
    }
    assert "pi1-token" not in repr(by_id["pi1"])
    assert by_id["pi1"].token == "pi1-token"
    assert by_id["pi1"].k3s_source is True
    assert by_id["robot-pi"].capabilities == ["usb", "reboot"]
    assert by_id["jw-pc"].capabilities == []
    assert cfg.db_path == str(tmp_path / "lad" / "personal-automations" / "history.db")
    assert str(tmp_path / "lad") in (by_id["pi3"].cert_file or "")


def test_token_file_read(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    token = tmp_path / "personal-automations" / "agents" / "pi3" / "token"
    token.parent.mkdir(parents=True)
    token.write_text("filetok\n", encoding="utf-8")
    cfg = load_hub_config(HUB_DIR / "hub.example.toml", tmp_path / "missing.toml")
    by_id = {d.id: d for d in cfg.devices}
    assert by_id["pi3"].token == "filetok"
    assert by_id["pi1"].token is None


def _timeout_gate(needed: float):
    def side_effect(request: httpx.Request) -> httpx.Response:
        if request.extensions["timeout"]["read"] < needed:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, json=envelope({"ok": True, "message": "done"}))

    return side_effect


@respx.mock
async def test_mutating_calls_use_action_timeout(entry):
    client = RemoteAgentClient(entry, timeout_s=1.0, action_timeout_s=10.0)
    respx.post(f"{BASE}/v1/actions").mock(side_effect=_timeout_gate(5.0))
    respx.post(f"{BASE}/v1/reboot").mock(side_effect=_timeout_gate(5.0))
    respx.put(f"{BASE}/v1/usb").mock(side_effect=_timeout_gate(5.0))
    respx.post(f"{BASE}/v1/groups/lab/restart").mock(side_effect=_timeout_gate(5.0))
    assert (await client.act("docker:web", "restart")).ok
    assert (await client.reboot()).ok
    assert (await client.set_usb(True)).ok
    assert (await client.group_restart("lab")).ok


@respx.mock
async def test_reads_keep_short_timeout(entry):
    client = RemoteAgentClient(entry, timeout_s=1.0, action_timeout_s=10.0)
    respx.get(f"{BASE}/v1/programs").mock(side_effect=_timeout_gate(5.0))
    with pytest.raises(DeviceOffline):
        await client.programs()


@respx.mock
@pytest.mark.parametrize("body", [[1, 2], "text", 7, None])
async def test_non_dict_json_body_is_agent_error(client, body):
    respx.get(f"{BASE}/v1/programs").respond(200, json=body)
    with pytest.raises(HubAgentError):
        await client.programs()
