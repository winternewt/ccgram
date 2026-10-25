from uuid import uuid4
import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer

from ccgram.api.server import build_app


async def test_terminal_watch_emits_resizes_and_suppresses_identical_frames(
    api, service, backend, monkeypatch
):
    client, _ = api
    frame = {"pane": "p0", "rows": 24, "cols": 80, "ansi": "hello", "hash": "h1"}
    sampled = asyncio.Queue()

    async def terminal(target, pane):
        await sampled.put(None)
        return dict(frame)

    monkeypatch.setattr(backend, "terminal", terminal)
    session_id = (await service.list_sessions())[0].id
    ws = await client.ws_connect("/v1/stream")
    await ws.send_json({"type": "hello", "data": {"protocol": "ccgram/1"}})
    await ws.receive_json(timeout=2)
    await ws.send_json(
        {
            "type": "terminal.watch",
            "data": {"sessionId": session_id, "pane": "p0", "intervalMs": 500},
        }
    )
    assert (await ws.receive_json(timeout=2))["data"] == frame
    await asyncio.wait_for(sampled.get(), 2)
    await asyncio.wait_for(sampled.get(), 2)
    frame["cols"] = 120
    resized = await ws.receive_json(timeout=2)
    assert resized["data"] == frame
    frame["rows"] = 40
    assert (await ws.receive_json(timeout=2))["data"] == frame
    frame.update(ansi="changed", hash="h2")
    assert (await ws.receive_json(timeout=2))["data"] == frame
    await ws.close()


@pytest.fixture
async def api(service, store):
    token = store.pair(store.make_pairing_code(), "test")["deviceToken"]
    app = build_app(service, store, allowed_hosts=frozenset({"localhost"}))
    client = TestClient(
        TestServer(app),
        headers={"Host": "localhost", "Authorization": f"Bearer {token}"},
    )
    await client.start_server()
    yield client, token
    await client.close()


async def test_http_metadata_listing_history_and_action_retry(api):
    client, _ = api
    metadata = await (await client.get("/v1/meta")).json()
    assert metadata["protocol"] == "ccgram/1"
    response = await client.get("/v1/sessions")
    sessions = await response.json()
    assert response.headers["X-CCGram-Epoch"].startswith("e_")
    session = sessions[0]
    assert "@1" not in str(session) and "%1" not in str(session)
    history = await (await client.get(f"/v1/sessions/{session['id']}/history")).json()
    assert history["historyLimit"] == "scrollback"
    intent = {
        "id": str(uuid4()),
        "bindingVersion": 1,
        "kind": "message.send",
        "data": {"text": "hello"},
    }
    first = await (
        await client.post(f"/v1/sessions/{session['id']}/actions", json=intent)
    ).json()
    second = await (
        await client.post(f"/v1/sessions/{session['id']}/actions", json=intent)
    ).json()
    assert first == second and first["outcome"] == "sent"


@pytest.mark.parametrize(
    "headers,status",
    [
        ({"Authorization": ""}, 401),
        ({"Authorization": "Bearer revoked"}, 401),
        ({"Host": "evil.example"}, 400),
        ({"Origin": "https://evil.example"}, 401),
    ],
)
async def test_auth_host_and_origin_checks(api, headers, status):
    client, _ = api
    response = await client.get("/v1/sessions", headers=headers)
    assert response.status == status
    assert "error" in await response.json()


async def test_pairing_is_only_public_route(api, store):
    client, _ = api
    code = store.make_pairing_code()
    response = await client.post(
        "/v1/devices/pair",
        headers={"Authorization": ""},
        json={"code": code, "name": "phone"},
    )
    assert response.status == 201
    token = (await response.json())["deviceToken"]
    assert (
        await client.get("/v1/meta", headers={"Authorization": f"Bearer {token}"})
    ).status == 200
    assert (
        await client.post("/v1/devices/pair", json={"code": code, "name": "phone"})
    ).status == 401


async def test_errors_have_consistent_envelope(api):
    client, _ = api
    for path, code in [
        ("/v1/absent", "not_found"),
        ("/v1/sessions/absent", "not_found"),
    ]:
        response = await client.get(path)
        assert response.status == 404
        assert (await response.json())["error"]["code"] == code
    response = await client.post(
        "/v1/sessions", json={"directory": "/tmp", "provider": "claude", "mode": "yolo"}
    )
    assert response.status == 422


async def test_websocket_welcome_resume_and_live_event(api, service):
    client, _ = api
    await service.refresh()
    boundary = service.hub.seq
    session_id = (await service.list_sessions())[0].id
    missed = service.hub.publish("session.updated", session_id, {"bindingVersion": 2})
    ws = await client.ws_connect("/v1/stream")
    await ws.send_json(
        {
            "type": "hello",
            "data": {
                "protocol": "ccgram/1",
                "resume": {"epoch": service.hub.epoch, "seq": boundary},
            },
        }
    )
    welcome = await ws.receive_json(timeout=2)
    assert welcome["data"]["resumed"] is True
    assert (await ws.receive_json(timeout=2))["seq"] == missed.seq
    await ws.send_json({"type": "subscribe", "data": {"sessionIds": "*"}})
    live = service.hub.publish("session.updated", session_id, {"bindingVersion": 3})
    assert (await ws.receive_json(timeout=2))["seq"] == live.seq
    await ws.close()


async def test_websocket_new_epoch_requires_reload_and_terminal_is_ephemeral(
    api, service
):
    client, _ = api
    session_id = (await service.list_sessions())[0].id
    ws = await client.ws_connect("/v1/stream")
    await ws.send_json(
        {
            "type": "hello",
            "data": {"protocol": "ccgram/1", "resume": {"epoch": "old", "seq": 100}},
        }
    )
    assert (await ws.receive_json(timeout=2))["data"]["resumed"] is False
    await ws.send_json(
        {
            "type": "terminal.watch",
            "data": {"sessionId": session_id, "pane": "p0", "intervalMs": 500},
        }
    )
    frame = await ws.receive_json(timeout=2)
    assert frame["type"] == "terminal.frame" and "seq" not in frame
    await ws.close()


async def test_revocation_closes_idle_websocket(api, store):
    client, token = api
    ws = await client.ws_connect("/v1/stream")
    await ws.send_json({"type": "hello", "data": {"protocol": "ccgram/1"}})
    await ws.receive_json(timeout=2)
    store.revoke(store.authenticate(token))
    await ws.receive(timeout=3)
    assert ws.close_code == 4401


async def test_pairing_rate_limit_is_reported(api):
    client, _ = api
    for _ in range(5):
        assert (
            await client.post(
                "/v1/devices/pair", json={"code": "x" * 43, "name": "phone"}
            )
        ).status == 401
    response = await client.post(
        "/v1/devices/pair", json={"code": "x" * 43, "name": "phone"}
    )
    assert response.status == 429
    assert (await response.json())["error"]["details"]["retryAfterMs"] > 0


async def test_upload_reference_download_and_agent_attachment(api, backend, tmp_path):
    client, _ = api
    response = await client.post(
        "/v1/uploads",
        data=b"hello file",
        headers={"X-Filename": "note.txt", "Content-Type": "text/plain"},
    )
    assert response.status == 201
    reference = await response.json()
    assert reference["name"] == "note.txt" and reference["size"] == 10
    download = await client.get(reference["url"])
    assert await download.read() == b"hello file"
    assert download.headers["Content-Disposition"] == "attachment"
    session = (await (await client.get("/v1/sessions")).json())[0]
    response = await client.post(
        f"/v1/sessions/{session['id']}/actions",
        json={
            "id": str(uuid4()),
            "bindingVersion": 1,
            "kind": "message.send",
            "data": {"text": "Read this", "files": [{"id": reference["id"]}]},
        },
    )
    assert response.status == 200
    assert backend.sent[0][1].startswith("Read this\n\nAttached files:\n")
    assert backend.sent[0][1].endswith(reference["id"])


@pytest.mark.parametrize("filename", ["../escape", "/tmp/file", "a\\b"])
async def test_upload_rejects_client_paths(api, filename):
    client, _ = api
    response = await client.post(
        "/v1/uploads", data=b"data", headers={"X-Filename": filename}
    )
    assert response.status == 400
