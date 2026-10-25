from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from ccgram.session_protocol.models import Message, Part, ProtocolError
from ccgram.session_protocol.messages import SourceEntry, build_messages
from ccgram.session_resolver import SessionResolver


def _message(message_id: str, role: str, text: str) -> Message:
    return Message(
        id=message_id,
        revision=1,
        conversation_id="conv",
        role=role,
        time=None,
        complete=True,
        parts=(Part(id=f"{message_id}#0", type="text", text=text),),
    )


def _service(messages: tuple[Message, ...]):
    service = SimpleNamespace(
        for_target=AsyncMock(
            return_value=(
                None,
                SimpleNamespace(
                    id="session-1", conversation=SimpleNamespace(id="conv")
                ),
            )
        ),
        history=AsyncMock(
            return_value=SimpleNamespace(messages=messages, prev_cursor=None)
        ),
        history_by_source_range=AsyncMock(
            return_value=SimpleNamespace(messages=messages)
        ),
    )
    return service


@pytest.mark.asyncio
async def test_history_translates_protocol_messages_to_legacy_telegram_dto():
    resolver = SessionResolver()
    resolver.bind_session_service(
        cast(
            Any,
            _service(
                (
                    _message("conv/u1", "user", "question"),
                    _message("conv/a1", "agent", "answer"),
                )
            ),
        )
    )

    messages, total = await resolver.get_recent_messages("@1")

    assert messages == [
        {
            "role": "user",
            "text": "question",
            "content_type": "text",
            "timestamp": None,
        },
        {
            "role": "assistant",
            "text": "answer",
            "content_type": "text",
            "timestamp": None,
        },
    ]
    assert total == 2


@pytest.mark.asyncio
async def test_history_includes_claude_exit_plan_before_tool_and_result():
    transcript = [
        SourceEntry(
            0,
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "plan-1",
                            "name": "ExitPlanMode",
                            "input": {"plan": "1. Inspect the API\n2. Add tests"},
                        }
                    ]
                },
            },
        ),
        SourceEntry(
            100,
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "plan-1",
                            "content": "Plan accepted",
                        }
                    ]
                },
            },
        ),
    ]
    protocol_messages = build_messages(transcript, "claude-history", "claude")
    resolver = SessionResolver()
    resolver.bind_session_service(cast(Any, _service(protocol_messages)))

    messages, _ = await resolver.get_recent_messages("@1")

    assert [message["text"] for message in messages] == [
        "1. Inspect the API\n2. Add tests",
        "ExitPlanMode",
        "Plan accepted",
    ]
    assert [message["content_type"] for message in messages] == [
        "text",
        "tool_use",
        "tool_result",
    ]


@pytest.mark.asyncio
async def test_history_forwards_unread_byte_range_to_shared_service():
    resolver = SessionResolver()
    service = _service((_message("conv/a1", "agent", "answer"),))
    resolver.bind_session_service(cast(Any, service))

    messages, _ = await resolver.get_recent_messages("@1", start_byte=100, end_byte=200)

    service.history_by_source_range.assert_awaited_once_with("session-1", 100, 200)
    service.history.assert_not_awaited()
    assert messages[0]["text"] == "answer"


@pytest.mark.asyncio
async def test_history_fails_closed_without_bound_session_service():
    with pytest.raises(ProtocolError, match="Session service is not initialized"):
        await SessionResolver().get_recent_messages("@1")
