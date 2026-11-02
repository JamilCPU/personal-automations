from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pa_core.auth import bearer_auth, install_error_handlers


def _client() -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    auth = bearer_auth({"tok-a": "alice", "tok-b": "bob"})

    @app.get("/who")
    def who(name: str = Depends(auth)):
        return {"name": name}

    return TestClient(app)


def test_returns_client_name_for_each_token():
    client = _client()
    assert client.get("/who", headers={"Authorization": "Bearer tok-a"}).json() == {"name": "alice"}
    assert client.get("/who", headers={"Authorization": "Bearer tok-b"}).json() == {"name": "bob"}


def test_rejects_missing_wrong_and_malformed():
    client = _client()
    bad_headers = [
        {},
        {"Authorization": "Bearer nope"},
        {"Authorization": "tok-a"},
        {"Authorization": "Basic tok-a"},
    ]
    for headers in bad_headers:
        r = client.get("/who", headers=headers)
        assert r.status_code == 401
        assert r.json() == {"success": False, "data": None, "error": "unauthorized"}
