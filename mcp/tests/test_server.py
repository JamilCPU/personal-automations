"""MCP server tests: the hub is mocked with respx, tools run through an in-memory session."""

import json

import httpx
import pytest
import respx
from mcp.shared.memory import create_connected_server_and_client_session
from pa_mcp.server import BearerAuthMiddleware, HubClient, build_server, main

HUB = "https://hub.test:7700"
TOKEN = "mcp-token"
EXPECTED_TOOLS = {
    "list_devices",
    "get_device",
    "list_programs",
    "get_program",
    "get_program_logs",
    "program_action",
    "restart_group",
    "get_usb_power",
    "set_usb_power",
    "reboot_device",
    "recent_history",
    "pin_program",
}


def _server() -> object:
    return build_server(HubClient(HUB, TOKEN, cert=None))


async def _call(name: str, args: dict | None = None):
    async with create_connected_server_and_client_session(_server()) as session:
        return await session.call_tool(name, args or {})


def _text(result) -> str:
    return result.content[0].text


@pytest.fixture
def hub():
    with respx.mock(base_url=HUB, assert_all_called=False) as mock:
        yield mock


async def test_tools_registered_with_device_names() -> None:
    async with create_connected_server_and_client_session(_server()) as session:
        tools = {t.name: t for t in (await session.list_tools()).tools}
    assert set(tools) == EXPECTED_TOOLS
    text = " ".join(t.description or "" for t in tools.values())
    for needle in ("jw-pc", "jw PC", "robot-pi", "Robot Pi", "pi1", "Pi1", "pi3", "Pi3"):
        assert needle in text
    assert "all ports" in (tools["set_usb_power"].description or "").lower()


async def test_list_devices_passthrough(hub) -> None:
    route = hub.get("/v1/devices").respond(
        json={"success": True, "data": [{"id": "pi3", "name": "Pi3"}], "error": None}
    )
    result = await _call("list_devices")
    assert not result.isError
    assert [json.loads(c.text) for c in result.content] == [{"id": "pi3", "name": "Pi3"}]
    assert route.calls.last.request.headers["authorization"] == f"Bearer {TOKEN}"


async def test_hub_error_becomes_tool_error(hub) -> None:
    hub.get("/v1/devices/robot-pi").respond(
        503, json={"success": False, "data": None, "error": "Robot Pi is offline"}
    )
    result = await _call("get_device", {"device": "robot-pi"})
    assert result.isError
    assert "Robot Pi is offline" in _text(result)


async def test_unreachable_hub_becomes_tool_error(hub) -> None:
    hub.get("/v1/devices").mock(side_effect=httpx.ConnectError("boom"))
    result = await _call("list_devices")
    assert result.isError
    assert "hub" in _text(result).lower()


async def test_action_posts_body(hub) -> None:
    route = hub.post("/v1/actions").respond(
        json={"success": True, "data": {"ok": True, "message": "restarted"}, "error": None}
    )
    result = await _call(
        "program_action",
        {"device": "robot-pi", "program_id": "docker:sentinel-ros-lidar-1", "action": "restart"},
    )
    assert not result.isError
    assert json.loads(route.calls.last.request.content) == {
        "device": "robot-pi",
        "id": "docker:sentinel-ros-lidar-1",
        "action": "restart",
    }


async def test_pin_program_puts_body(hub) -> None:
    route = hub.put("/v1/pins").respond(
        json={"success": True, "data": {"ok": True, "message": "pinned Discord"}, "error": None}
    )
    result = await _call(
        "pin_program",
        {"device": "jw-pc", "program_id": "process:Discord#7@1000", "pinned": True},
    )
    assert not result.isError
    assert json.loads(route.calls.last.request.content) == {
        "device": "jw-pc",
        "id": "process:Discord#7@1000",
        "pinned": True,
    }


async def test_list_programs_mentions_pinning() -> None:
    async with create_connected_server_and_client_session(_server()) as session:
        tools = {t.name: t for t in (await session.list_tools()).tools}
    assert "pinned" in (tools["list_programs"].description or "").lower()


async def test_list_programs_query_and_usb_body(hub) -> None:
    progs = hub.get("/v1/programs").respond(json={"success": True, "data": [], "error": None})
    usb = hub.put("/v1/devices/robot-pi/usb").respond(
        json={"success": True, "data": {"ok": True, "message": "on"}, "error": None}
    )
    await _call("list_programs", {"device": "pi3", "include_system": True})
    params = dict(progs.calls.last.request.url.params)
    assert params == {"device": "pi3", "include_system": "true"}
    await _call("set_usb_power", {"device": "robot-pi", "on": False})
    assert json.loads(usb.calls.last.request.content) == {"on": False}


async def test_logs_group_history_reboot_routes(hub) -> None:
    logs = hub.get("/v1/logs").respond(json={"success": True, "data": ["a"], "error": None})
    group = hub.post("/v1/devices/pi1/groups/sentinel/restart").respond(
        json={"success": True, "data": {"ok": True, "message": "x"}, "error": None}
    )
    reboot = hub.post("/v1/devices/pi3/reboot").respond(
        json={"success": True, "data": {"ok": True, "message": "x"}, "error": None}
    )
    history = hub.get("/v1/history").respond(json={"success": True, "data": [], "error": None})
    await _call("get_program_logs", {"device": "pi3", "program_id": "ntopng", "lines": 5})
    assert dict(logs.calls.last.request.url.params) == {
        "device": "pi3",
        "id": "ntopng",
        "lines": "5",
    }
    await _call("restart_group", {"device": "pi1", "project": "sentinel"})
    await _call("reboot_device", {"device": "pi3"})
    await _call("recent_history", {"limit": 3})
    assert group.called
    assert reboot.called
    assert dict(history.calls.last.request.url.params) == {"limit": "3"}


async def _asgi(token: str | None, sent: dict) -> httpx.Response:
    async def inner(scope, receive, send) -> None:
        sent["reached"] = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    app = BearerAuthMiddleware(inner, "secret-token")
    headers = {} if token is None else {"Authorization": token}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as client:
        return await client.post("/mcp", headers=headers)


async def test_http_transport_rejects_missing_token() -> None:
    sent: dict = {}
    for header in (None, "Bearer wrong", "Basic secret-token"):
        resp = await _asgi(header, sent)
        assert resp.status_code == 401
        assert resp.json() == {"success": False, "data": None, "error": "unauthorized"}
    assert "reached" not in sent


async def test_http_transport_accepts_valid_token() -> None:
    sent: dict = {}
    resp = await _asgi("Bearer secret-token", sent)
    assert resp.status_code == 200
    assert sent["reached"]


def test_http_mode_refuses_without_token(monkeypatch, capsys) -> None:
    monkeypatch.delenv("PA_MCP_HTTP_TOKEN", raising=False)
    monkeypatch.setenv("PA_HUB_URL", HUB)
    monkeypatch.setenv("PA_HUB_TOKEN", TOKEN)
    assert main(["--http"]) == 1
    assert "PA_MCP_HTTP_TOKEN" in capsys.readouterr().err


def test_gen_token_prints_fresh_token(capsys) -> None:
    assert main(["gen-token"]) == 0
    first = capsys.readouterr().out.strip()
    assert main(["gen-token"]) == 0
    assert len(first) >= 32
    assert first != capsys.readouterr().out.strip()


async def test_real_http_app_requires_token() -> None:
    app = BearerAuthMiddleware(_server().streamable_http_app(), "secret-token")
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        denied = await client.post("/mcp")
        passed = await client.post("/mcp", headers={"Authorization": "Bearer secret-token"})
    assert denied.status_code == 401
    assert passed.status_code != 401


async def test_non_http_scopes_rejected_but_lifespan_passes() -> None:
    seen: list[str] = []

    async def inner(scope, receive, send) -> None:
        seen.append(scope["type"])

    app = BearerAuthMiddleware(inner, "t")
    sent: list[dict] = []

    async def send(msg) -> None:
        sent.append(msg)

    await app({"type": "websocket", "headers": []}, None, send)
    await app({"type": "lifespan"}, None, send)
    assert seen == ["lifespan"]
    assert sent == [{"type": "websocket.close", "code": 1008}]


def test_http_mode_refuses_without_tls_files(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setenv("PA_MCP_HTTP_TOKEN", "x")
    monkeypatch.setenv("PA_HUB_URL", HUB)
    monkeypatch.setenv("PA_HUB_TOKEN", TOKEN)
    monkeypatch.setenv("PA_MCP_TLS_CERT", str(tmp_path / "nope.pem"))
    monkeypatch.setenv("PA_MCP_TLS_KEY", str(tmp_path / "nope.key"))
    assert main(["--http"]) == 1
    assert "TLS file not found" in capsys.readouterr().err


async def test_path_params_are_encoded(hub) -> None:
    route = hub.get(url__regex=r".*/v1/devices/.*").respond(
        json={"success": True, "data": {}, "error": None}
    )
    await _call("get_device", {"device": "a/b ?x"})
    assert route.calls.last.request.url.raw_path.startswith(b"/v1/devices/a%2Fb%20%3Fx")


async def test_agent_header_carries_client_name(hub) -> None:
    from mcp.types import Implementation

    route = hub.get("/v1/devices").respond(json={"success": True, "data": [], "error": None})
    info = Implementation(name="claude-code", version="1.0")
    async with create_connected_server_and_client_session(_server(), client_info=info) as session:
        await session.call_tool("list_devices", {})
    assert route.calls.last.request.headers["x-pa-agent"] == "claude-code"


async def test_agent_header_sent_on_actions(hub) -> None:
    from mcp.types import Implementation

    route = hub.post("/v1/devices/pi3/reboot").respond(
        json={"success": True, "data": {"ok": True, "message": ""}, "error": None}
    )
    info = Implementation(name="codex", version="1.0")
    async with create_connected_server_and_client_session(_server(), client_info=info) as session:
        await session.call_tool("reboot_device", {"device": "pi3"})
    assert route.calls.last.request.headers["x-pa-agent"] == "codex"


def test_http_host_defaults_to_hub_lan_ip() -> None:
    from pa_mcp.server import _build_parser

    assert _build_parser().parse_args(["--http"]).host == "192.168.0.197"
