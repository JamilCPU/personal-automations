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
