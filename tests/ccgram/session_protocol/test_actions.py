import asyncio
import time
from dataclasses import replace
from uuid import uuid4

import pytest

from ccgram.session_protocol.actions import parse_action
from ccgram.session_protocol.backend import Prompt
from ccgram.session_protocol.input_lock import input_lock
from ccgram.session_protocol.models import Action, ProtocolError
from ccgram.session_protocol.requests import PendingHook


def action(version=1, kind="message.send", **data):
    return Action(str(uuid4()), version, kind, data or {"text": "hello"})


async def test_retry_and_concurrent_retry_send_keys_once(service, backend):
    session = (await service.list_sessions())[0]
    intent = action()
    first, second = await asyncio.gather(
        service.execute(session.id, intent), service.execute(session.id, intent)
    )
    assert first == second
    assert first[0].outcome == "sent"
    assert backend.sent == [("text", "hello", True)]
    assert await service.execute(session.id, intent) == first


async def test_stale_binding_rejected_before_any_input_and_deduplicated(
    service, backend
):
    session = (await service.list_sessions())[0]
    backend.target = replace(backend.target, provider="codex", process_id=456)
    intent = action()
    result, status = await service.execute(session.id, intent)
    assert status == 409
    assert result.error and result.error["code"] == "binding_conflict"
    assert result.error["details"]["bindingVersion"] == 2
    assert backend.sent == []
    assert await service.execute(session.id, intent) == (result, status)


async def test_timeout_or_partial_send_is_unknown_and_never_retried(service, backend):
    session = (await service.list_sessions())[0]
    backend.fail = True
    intent = action()
    result, status = await service.execute(session.id, intent)
    assert status == 200 and result.outcome == "unknown"
    backend.fail = False
    assert await service.execute(session.id, intent) == (result, status)
    assert len(backend.sent) == 1


async def test_unverified_foreground_never_receives_keys(service, backend):
    backend.target = replace(backend.target, process_id=0, foreground_argv=())
    session = (await service.list_sessions())[0]
    result, status = await service.execute(session.id, action())
    assert result.outcome == "rejected" and status == 503
    assert backend.sent == []


async def test_shell_does_not_accept_agent_message(service, backend):
    backend.target = replace(backend.target, provider="shell")
    session = (await service.list_sessions())[0]
    result, status = await service.execute(session.id, action())
    assert result.outcome == "rejected" and status == 422
    assert backend.sent == []


async def test_paste_is_literal_without_enter(service, backend):
    session = (await service.list_sessions())[0]
    result, _ = await service.execute(
        session.id, action(kind="terminal.paste", text="echo hello")
    )
    assert result.outcome == "sent"
    assert backend.sent == [("text", "echo hello", False)]


async def test_open_request_requires_explicit_dismissal(service, backend):
    backend.current_prompt = Prompt("Allow command?\n1. Yes\n2. No", "PermissionPrompt")
    session = (await service.list_sessions())[0]
    result, status = await service.execute(session.id, action())
    assert status == 409 and result.error and result.error["code"] == "request_open"
    assert backend.sent == []
    backend.clear_on_input = True
    dismissed, status = await service.execute(
        session.id, action(text="new message", dismissRequest=True)
    )
    assert dismissed.outcome == "sent" and status == 200
    assert backend.sent == [("key", "Escape", False), ("text", "new message", True)]
    assert service.requests.get(session.id) is None


async def test_unconfirmed_dismissal_never_forwards_text(service, backend):
    backend.current_prompt = Prompt("Allow command?\n1. Yes\n2. No", "PermissionPrompt")
    session = (await service.list_sessions())[0]
    result, _ = await service.execute(
        session.id, action(text="must not send", dismissRequest=True)
    )
    assert result.outcome != "sent"
    assert backend.sent == [("key", "Escape", False)]
    assert service.requests.get(session.id) is not None


async def test_hook_response_needs_exact_prompt_and_closes_after_verified_answer(
    service, backend
):
    session = (await service.list_sessions())[0]
    service.requests.observe_hook(
        session.id,
        PendingHook(
            "conv", "permission", "Bash", {"command": "uv run pytest"}, time.time()
        ),
    )
    backend.current_prompt = Prompt(
        "Bash uv run pytest\n❯ 1. Yes\n2. Yes for this session\n3. No",
        "PermissionPrompt",
    )
    session = await service.get_session(session.id)
    request = session.open_requests[0]
    assert request.source == "hook"
    assert [o.id for o in request.options] == ["1", "3"]
    backend.clear_on_input = True
    result, status = await service.execute(
        session.id, action(kind="request.respond", requestId=request.id, optionId="1")
    )
    assert status == 200 and result.outcome == "sent"
    assert service.requests.get(session.id) is None
    assert backend.sent == [("text", "1", False)]


async def test_screen_prompt_cannot_be_approved(service, backend):
    backend.current_prompt = Prompt(
        "Bash uv run pytest\n1. Yes\n2. No", "PermissionPrompt"
    )
    session = (await service.list_sessions())[0]
    request = session.open_requests[0]
    assert request.source == "screen" and not request.options
    result, status = await service.execute(
        session.id, action(kind="request.respond", requestId=request.id, optionId="1")
    )
    assert status == 422 and result.outcome == "rejected"
    assert backend.sent == []


async def test_mobile_input_waits_for_telegram_session_lock(service, backend):
    session = (await service.list_sessions())[0]
    async with input_lock(backend.target.id):
        pending = asyncio.create_task(service.execute(session.id, action()))
        await asyncio.sleep(0)
        assert backend.sent == []
    result, status = await pending
    assert result.outcome == "sent" and status == 200


@pytest.mark.parametrize(
    "change",
    [
        {"bindingVersion": True},
        {"kind": []},
        {"data": {"text": "\ud800"}},
        {"data": {"text": "x" * 65537}},
        {"bindingVersion": 0},
        {"kind": "approval.raise"},
        {"id": "not-uuid"},
        {"data": {"text": ""}},
        {"data": {"text": "hi", "dismissRequest": "yes"}},
        {"kind": "terminal.key", "data": {"keys": ["; rm -rf"]}},
        {"kind": "terminal.key", "data": {"keys": []}},
        {
            "kind": "request.respond",
            "data": {"requestId": "r", "optionId": "1", "answer": "yes"},
        },
    ],
)
def test_invalid_actions_are_rejected_at_boundary(change):
    body = {
        "id": str(uuid4()),
        "bindingVersion": 1,
        "kind": "message.send",
        "data": {"text": "hello"},
        **change,
    }
    with pytest.raises(ProtocolError):
        parse_action(body)
