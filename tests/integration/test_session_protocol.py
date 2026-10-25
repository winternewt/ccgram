"""ccgram/1 conformance through a real isolated tmux server and a test TUI."""

import asyncio
import shlex
import shutil
import sys
import time
from uuid import uuid4
from functools import partial

import libtmux
import pytest
from aiohttp.test_utils import TestClient, TestServer

from ccgram.api.server import build_app
from ccgram.hooks.adapters import ClaudeHookAdapter
from ccgram.multiplexer.tmux import TmuxManager
from ccgram.providers.base import HookEvent
from ccgram.session_protocol.backend import MultiplexerSessionBackend
from ccgram.session_protocol.service import LocalSessionService
from ccgram.session_protocol.store import ProtocolStore
from ccgram.window_state_store import WindowState, window_store

pytestmark = [
    pytest.mark.integration,
    pytest.mark.timeout(90),
    pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed"),
]

_TUI = r"""
import json, os, sys, termios, tty
from datetime import UTC, datetime
from pathlib import Path
path = Path(sys.argv[1])
original = termios.tcgetattr(sys.stdin.fileno())
tty.setcbreak(sys.stdin.fileno())
def draw(text):
    print("\033[2J\033[H" + text, flush=True)
def entry(value):
    with path.open("a") as stream:
        stream.write(json.dumps(value) + "\n")
draw("Ready")
text = ""
waiting = False
while True:
    char = sys.stdin.read(1)
    if waiting and char in "12":
        waiting = False
        draw("Ready")
        continue
    if char in "\r\n":
        if text == "quit":
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, original)
            os.execve("/bin/sh", ["sh"], {**os.environ, "PS1": "⌘0⌘"})
        if text == "ask":
            entry({"uuid":"call-entry", "type":"assistant", "message":{"content":[{"type":"tool_use","id":"call","name":"Bash","input":{"command":"uv run pytest"}}]}})
            waiting = True
            draw("Do you want to proceed?\nBash: uv run pytest\n❯ 1. Yes\n  2. No\nEsc to cancel")
        else:
            entry({"uuid":"user-" + str(os.getpid()) + "-" + str(path.stat().st_size), "type":"user", "timestamp":datetime.now(UTC).isoformat(), "message":{"content":text}})
            draw("Ready")
        text = ""
    else:
        text += char
"""


async def wait_until(check):
    async with asyncio.timeout(8):
        while True:
            value = await check()
            if value:
                return value
            await asyncio.sleep(0.05)


async def post_action(client, service, session_id, kind, data):
    current = await service.get_session(session_id)
    response = await client.post(
        f"/v1/sessions/{session_id}/actions",
        json={
            "id": str(uuid4()),
            "bindingVersion": current.binding_version,
            "kind": kind,
            "data": data,
        },
    )
    assert response.status == 200, await response.text()
    return await response.json()


@pytest.fixture
async def terminal_api(tmp_path, monkeypatch):
    monkeypatch.setattr("ccgram.config.config.prompt_mode", "wrap")
    script = tmp_path / "claude"
    script.write_text(_TUI, encoding="utf-8")
    transcript = tmp_path / "conversation.jsonl"
    transcript.touch()
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(script))} {shlex.quote(str(transcript))}"
    monkeypatch.setenv("CCGRAM_CLAUDE_COMMAND", command)
    server = libtmux.Server(
        socket_name=f"ccgram-protocol-{uuid4().hex}", config_file="/dev/null"
    )
    tmux_session = server.new_session(
        session_name="protocol",
        window_name="agent",
        start_directory=str(tmp_path),
        window_command=f"exec {command}",
    )
    mux = TmuxManager(session_name="protocol")
    mux._server = server
    window_id = tmux_session.windows[0].window_id
    assert window_id is not None
    conversation = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    window_store.window_states[window_id] = WindowState(
        provider_name="claude",
        session_id=conversation,
        transcript_path=str(transcript),
        cwd=str(tmp_path),
    )
    store = ProtocolStore(tmp_path / "protocol.sqlite3")
    service = LocalSessionService(MultiplexerSessionBackend(mux, str(tmp_path)), store)
    token = store.pair(store.make_pairing_code(), "conformance")["deviceToken"]
    client = TestClient(
        TestServer(build_app(service, store, allowed_hosts=frozenset({"localhost"}))),
        headers={"Host": "localhost", "Authorization": f"Bearer {token}"},
    )
    try:
        await client.start_server()
        yield client, service, window_id, conversation
    finally:
        await client.close()
        store.close()
        server.kill()


async def test_real_tmux_list_history_send_respond_resume_and_replay(terminal_api):
    client, service, window_id, conversation = terminal_api

    async def agent_snapshot():
        sessions = await service.list_sessions()
        return next((s for s in sessions if s.provider == "claude"), None)

    session = await wait_until(agent_snapshot)
    response = await client.get("/v1/sessions")
    assert response.status == 200
    assert (await response.json())[0]["id"] == session.id

    act = partial(post_action, client, service, session.id)
    assert (await act("message.send", {"text": "hello"}))["outcome"] == "sent"

    async def user_message():
        page = await service.history(session.id, None, 50)
        return next((m for m in page.messages if m.role == "user"), None)

    user = await wait_until(user_message)
    assert user.parts[0].text == "hello" and user.action_id
    assert (await act("message.send", {"text": "ask"}))["outcome"] == "sent"

    async def screen_request():
        snapshot = await service.get_session(session.id)
        return snapshot.open_requests[0] if snapshot.open_requests else None

    assert (await wait_until(screen_request)).source == "screen"
    hook = ClaudeHookAdapter().normalize(
        {
            "hook_event_name": "PermissionRequest",
            "session_id": conversation,
            "tool_name": "Bash",
            "tool_input": {"command": "uv run pytest"},
        }
    )
    assert hook is not None
    await service.observe_hook(
        HookEvent(
            hook.canonical_event_name,
            f"protocol:{window_id}",
            conversation,
            hook.data,
            time.time(),
        ),
        "protocol:",
    )
    request = await screen_request()
    assert request is not None
    assert request.source == "hook" and request.options
    assert (await act("request.respond", {"requestId": request.id, "optionId": "1"}))[
        "outcome"
    ] == "sent"
    assert not (await service.get_session(session.id)).open_requests
    ws = await client.ws_connect("/v1/stream")
    await ws.send_json(
        {
            "type": "hello",
            "data": {
                "protocol": "ccgram/1",
                "resume": {"epoch": service.hub.epoch, "seq": service.hub.seq - 1},
            },
        }
    )
    assert (await ws.receive_json(timeout=2))["data"]["resumed"] is True
    assert (await ws.receive_json(timeout=2))["class"] == "durable"
    await ws.close()
    assert (await act("message.send", {"text": "quit"}))["outcome"] == "sent"

    async def shell_snapshot():
        snapshot = await service.get_session(session.id)
        return snapshot if snapshot.provider == "shell" else None

    shell = await wait_until(shell_snapshot)
    assert "session.resume" in shell.capabilities
    assert (await act("session.resume", {"conversationId": conversation}))[
        "outcome"
    ] == "sent"
    assert (await wait_until(agent_snapshot)).id == session.id
