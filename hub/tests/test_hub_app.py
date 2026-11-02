from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from pa_core.models import ActionResult
from pa_hub.app import create_app, pair_uri
from pa_hub.clients import DeviceOffline, HubAgentError
from pa_hub.service import HubService
from test_service import FakeClient, standard

PHONE = "phone-token"
MCP = "mcp-token"
TOKENS = {PHONE: "phone", MCP: "mcp"}


def make_client(tmp_path: Path, **overrides: FakeClient) -> tuple[TestClient, HubService]:
    service, _ = standard(tmp_path, **overrides)
    app = create_app(service, TOKENS, {i: e.name for i, e in service._entries.items()})
    return TestClient(app, raise_server_exceptions=False), service


def auth(token: str = PHONE) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


ROUTES = [
    ("GET", "/v1/devices", None),
    ("GET", "/v1/devices/pi1", None),
    ("GET", "/v1/programs", None),
    ("GET", "/v1/program?device=pi1&id=s:x", None),
    ("GET", "/v1/logs?device=pi1&id=s:x", None),
    ("POST", "/v1/actions", {"device": "pi1", "id": "s:x", "action": "restart"}),
    ("POST", "/v1/devices/pi1/groups/lab/restart", None),
    ("GET", "/v1/devices/robot-pi/usb", None),
    ("PUT", "/v1/devices/robot-pi/usb", {"on": True}),
    ("POST", "/v1/devices/pi3/reboot", None),
    ("GET", "/v1/history", None),
]


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_routes_require_token(tmp_path: Path, method: str, path: str, body: dict | None) -> None:
    client, _ = make_client(tmp_path)
    assert client.request(method, path, json=body).status_code == 401
    bad = client.request(method, path, json=body, headers=auth("nope"))
    assert bad.status_code == 401
    assert bad.json() == {"success": False, "data": None, "error": "unauthorized"}


def test_happy_paths(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    devices = client.get("/v1/devices", headers=auth()).json()
    assert devices["success"] is True
    assert [d["id"] for d in devices["data"]] == ["jw-pc", "pi1", "robot-pi", "pi3"]
    assert client.get("/v1/devices/pi1", headers=auth()).json()["data"]["id"] == "pi1"
    progs = client.get("/v1/programs?device=jw-pc", headers=auth()).json()["data"]
    assert [p["name"] for p in progs] == ["alpha", "Beta"]
    detail = client.get("/v1/program?device=pi1&id=s:x", headers=auth()).json()["data"]
    assert detail["program"]["id"] == "s:x"
    logs = client.get("/v1/logs?device=pi1&id=s:x&lines=5", headers=auth()).json()
    assert logs["data"] == ["line"]
    assert client.get("/v1/devices/robot-pi/usb", headers=auth()).json()["data"]["on"] is True
    assert (
        client.put("/v1/devices/robot-pi/usb", json={"on": False}, headers=auth()).json()["data"]
        == ActionResult(ok=True, message="usb").model_dump()
    )


def test_client_name_from_token(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    body = {"device": "pi1", "id": "s:x", "action": "restart"}
    assert client.post("/v1/actions", json=body, headers=auth(MCP)).status_code == 200
    history = client.get("/v1/history", headers=auth()).json()["data"]
    assert history[0]["client"] == "mcp"
    assert history[0]["target"] == "s:x"
    assert client.get("/v1/history?device=pi3", headers=auth()).json()["data"] == []


def test_offline_503_message(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path, **{"robot-pi": FakeClient(error=DeviceOffline("robot-pi"))})
    response = client.get("/v1/program?device=robot-pi&id=s:r", headers=auth())
    assert response.status_code == 503
    assert response.json()["error"] == "ROBOT-PI is offline"


def test_unknown_device_404(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    for path in ("/v1/devices/nope", "/v1/programs?device=nope", "/v1/devices/nope/usb"):
        response = client.get(path, headers=auth())
        assert response.status_code == 404, path
        assert response.json()["success"] is False


def test_action_not_allowed_409(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    response = client.post("/v1/devices/pi1/reboot", headers=auth())
    assert response.status_code == 409


def test_agent_error_502_and_internal_500(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path, pi3=FakeClient(error=HubAgentError("pi3", 500, "boom")))
    response = client.get("/v1/program?device=pi3&id=x", headers=auth())
    assert response.status_code == 502
    assert response.json()["error"] == "PI3: boom"
    client2, _ = make_client(tmp_path, pi3=FakeClient(error=RuntimeError("secret detail")))
    response = client2.get("/v1/program?device=pi3&id=x", headers=auth())
    assert response.status_code == 500
    assert response.json()["error"] == "internal error"


def test_validation_422(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    response = client.post("/v1/actions", json={"device": "pi1"}, headers=auth())
    assert response.status_code == 422
    assert response.json()["error"] == "invalid request"


def test_pair_uri_format() -> None:
    uri = pair_uri("https://192.168.0.99:7700", "a+b/c=", "ab12")
    assert uri == ("pa://pair?url=https%3A%2F%2F192.168.0.99%3A7700&token=a%2Bb%2Fc%3D&fp=ab12")
    query = parse_qs(urlparse(uri).query)
    assert query == {"url": ["https://192.168.0.99:7700"], "token": ["a+b/c="], "fp": ["ab12"]}


@pytest.mark.parametrize(
    ("agent", "expected"),
    [
        ("claude-code", "mcp:claude-code"),
        ("Codex_1.2", "mcp:Codex_1.2"),
        (None, "mcp"),
        ("", "mcp"),
        ("bad agent", "mcp"),
        ("x" * 41, "mcp"),
        ("evil:name", "mcp"),
    ],
)
def test_agent_header_marks_history(tmp_path: Path, agent: str | None, expected: str) -> None:
    client, _ = make_client(tmp_path)
    headers = auth(MCP)
    if agent is not None:
        headers["X-PA-Agent"] = agent
    body = {"device": "pi1", "id": "s:x", "action": "restart"}
    assert client.post("/v1/actions", json=body, headers=headers).status_code == 200
    client.post("/v1/devices/pi3/reboot", headers=headers)
    history = client.get("/v1/history", headers=auth()).json()["data"]
    assert {h["client"] for h in history} == {expected}
