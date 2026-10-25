import asyncio
import hashlib
import json
import time
from dataclasses import replace
from tempfile import TemporaryFile
from unittest.mock import AsyncMock

import pytest

from ccgram.providers.base import HookEvent
from ccgram.session_protocol.backend import Prompt
from ccgram.session_protocol.requests import PendingHook
from ccgram.session_protocol.runtime import ProtocolRuntime


@pytest.mark.parametrize("queued", [False, True])
@pytest.mark.parametrize("elapsed", [1, 31])
async def test_stale_unmatched_raw_hook_preserves_newer_controls(
    service, backend, store, queued, elapsed
):
    session = (await service.list_sessions())[0]
    now = time.time()
    backend.current_prompt = Prompt("git status\n1. Yes\n2. No", "permission")
    service.requests.observe_hook(
        session.id,
        PendingHook("conv", "permission", "Bash", {"command": "git status"}, now),
    )
    await service.get_session(session.id)
    original = service.requests.get(session.id)
    assert original is not None
    stale = HookEvent(
        "PermissionRequest",
        "@1",
        "conv",
        {"tool_name": "Bash", "tool_input_hash": "missing"},
        now - elapsed,
    )
    with TemporaryFile() as lock:
        if queued:
            ProtocolRuntime(store, service, lock).observe_hook(stale)
        else:
            await service.observe_hook(stale, "")
    assert service.requests.is_current(session.id, original)


async def test_replacement_expiring_in_queue_never_restores_old_approval(
    service, backend, store, tmp_path, monkeypatch
):
    now = time.time()
    tool_input = {"file_path": "/tmp/file", "content": "new"}
    path = tmp_path / "transcript.jsonl"
    path.write_text(
        json.dumps(
            {
                "uuid": "tool",
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call",
                            "name": "Edit",
                            "input": tool_input,
                        }
                    ]
                },
            }
        )
        + "\n"
    )
    backend.target = replace(backend.target, transcript=path)
    session = (await service.list_sessions())[0]
    backend.current_prompt = Prompt("/tmp/file\n1. Yes\n2. No", "permission")
    service.requests.observe_hook(
        session.id,
        PendingHook(
            "conv",
            "permission",
            "Edit",
            {"file_path": "/tmp/file", "content": "old"},
            now,
        ),
    )
    await service.get_session(session.id)
    original = service.requests.get(session.id)
    assert original is not None
    digest = hashlib.sha256(
        json.dumps(tool_input, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    with TemporaryFile() as lock:
        runtime = ProtocolRuntime(store, service, lock)
        runtime.observe_hook(
            HookEvent(
                "PermissionRequest",
                "@1",
                "conv",
                {"tool_name": "Edit", "tool_input_hash": digest},
                now + 1,
            )
        )
        monkeypatch.setattr(
            "ccgram.session_protocol.requests.time.time", lambda: now + 32
        )
        monkeypatch.setattr(
            "ccgram.session_protocol.runtime.asyncio.sleep",
            AsyncMock(side_effect=asyncio.CancelledError),
        )
        with pytest.raises(asyncio.CancelledError):
            await runtime.poll("")
    assert not service.requests.is_current(session.id, original)
    snapshot = await service.get_session(session.id)
    assert snapshot.open_requests[0].source == "screen"
