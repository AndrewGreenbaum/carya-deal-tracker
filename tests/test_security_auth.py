"""Authentication regressions use only local ASGI requests and synthetic keys."""
from threading import Event
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import src.auth as auth_module
from src.auth import Authentication, AuthenticationMiddleware

KEY = "test-access-key-0123456789-abcdef01"


def client_for(key=KEY, *, socket_closed=None):
    manager = Authentication(lambda: [key], audience="unit-test")
    app = FastAPI()
    app.add_middleware(AuthenticationMiddleware, authentication=manager)

    @app.get("/private")
    def private():
        return {"private": True}

    @app.websocket("/socket")
    async def socket(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_text("private update")
        await websocket.close()

    @app.websocket("/persistent-socket")
    async def persistent_socket(websocket: WebSocket):
        await websocket.accept()
        try:
            while True:
                await websocket.send_text(await websocket.receive_text())
        except WebSocketDisconnect:
            pass
        finally:
            if socket_closed is not None:
                socket_closed.set()

    return TestClient(app), manager


def login(client):
    response = client.post("/auth/login", json={"api_key": KEY})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    return response.json()["access_token"]


def test_private_reads_require_api_key_or_signed_session():
    client, manager = client_for()
    assert client.get("/private").status_code == 401
    assert client.get("/private", headers={"X-API-Key": KEY}).status_code == 200
    token = login(client)
    response = client.get("/private", headers={"Authorization": "Bearer " + token})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert manager.session_expiry(token)


@pytest.mark.parametrize("key", ["", "dev-key", "x" * 32, "change-me"])
def test_missing_or_default_server_key_fails_closed(key):
    client, manager = client_for(key)
    with pytest.raises(RuntimeError, match="random API access key"):
        manager.configured_keys()
    assert client.post("/auth/login", json={"api_key": key}).status_code == 503
    assert client.get("/private", headers={"X-API-Key": key}).status_code == 503


def test_tampering_expiry_cross_instance_and_logout_rejected(monkeypatch):
    client, manager = client_for()
    token = login(client)
    headers = {"Authorization": "Bearer " + token}
    parts = token.split(".")
    parts[1] = str(int(parts[1]) + 1)
    assert client.get("/private", headers={"Authorization": "Bearer " + ".".join(parts)}).status_code == 401
    assert manager.session_expiry(token[:-1] + "é") is None
    another, _ = client_for()
    assert another.get("/private", headers=headers).status_code == 401
    assert client.post("/auth/logout", headers=headers).status_code == 204
    assert client.get("/private", headers=headers).status_code == 401
    token = login(client)
    expires = int(token.split(".")[1])
    monkeypatch.setattr(auth_module, "time", SimpleNamespace(time=lambda: expires + 1))
    assert manager.session_expiry(token) is None


def test_login_is_throttled_and_input_is_bounded():
    client, _ = client_for()
    assert client.post("/auth/login", content=b"x" * 4097).status_code == 413
    for _ in range(4):
        assert client.post("/auth/login", json={"api_key": "wrong"}).status_code == 401
    assert client.post("/auth/login", json={"api_key": KEY}).status_code == 429


def test_socket_requires_session_without_url_credentials():
    client, _ = client_for()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/socket"):
            pass
    token = login(client)
    with client.websocket_connect("/socket", subprotocols=["carya-session", token]) as ws:
        assert ws.receive_text() == "private update"


def test_token_in_query_string_does_not_authenticate():
    client, _ = client_for()
    token = login(client)
    assert client.get("/private", params={"token": token}).status_code == 401
    assert client.get("/private", params={"token": KEY}).status_code == 401


def test_open_socket_closes_at_session_expiry():
    client, manager = client_for()
    manager.lifetime = 1
    token = login(client)
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect("/persistent-socket", subprotocols=["carya-session", token]) as ws:
            ws.receive_text()
    assert closed.value.code == 1008


def test_logout_prevents_socket_reconnection():
    client, _ = client_for()
    token = login(client)
    assert client.post("/auth/logout", headers={"Authorization": "Bearer " + token}).status_code == 204
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/socket", subprotocols=["carya-session", token]):
            pass


def test_logout_closes_an_existing_socket_without_waiting_for_expiry():
    socket_closed = Event()
    client, _ = client_for(socket_closed=socket_closed)
    token = login(client)
    with client.websocket_connect("/persistent-socket", subprotocols=["carya-session", token]) as ws:
        ws.send_text("before logout")
        assert ws.receive_text() == "before logout"
        assert client.post("/auth/logout", headers={"Authorization": "Bearer " + token}).status_code == 204
        # Bound the failure time: an unpatched server leaves this socket open for an hour.
        assert socket_closed.wait(timeout=2), "Logout left the authenticated socket running"
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_text()
        assert closed.value.code == 1008


def test_logout_does_not_close_another_sessions_socket():
    client, _ = client_for()
    token = login(client)
    other_token = login(client)
    with client.websocket_connect("/persistent-socket", subprotocols=["carya-session", other_token]) as ws:
        assert client.post("/auth/logout", headers={"Authorization": "Bearer " + token}).status_code == 204
        ws.send_text("still authenticated")
        assert ws.receive_text() == "still authenticated"
