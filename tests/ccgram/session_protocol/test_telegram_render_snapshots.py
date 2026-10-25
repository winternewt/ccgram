import json
from pathlib import Path

import pytest

from ccgram.session_protocol.messages import (
    SourceEntry,
    TranscriptParserState,
    parse_records,
)
from ccgram.transcript_reader import render_protocol_records

SNAPSHOTS = json.loads((Path(__file__).parent / "telegram_snapshots.json").read_text())


@pytest.mark.parametrize("provider_name", SNAPSHOTS)
def test_canonical_records_preserve_telegram_render_snapshot(provider_name):
    fixture = SNAPSHOTS[provider_name]
    source = fixture["source"]
    state = TranscriptParserState()
    entries = [SourceEntry(i * 100, raw) for i, raw in enumerate(source)]
    records, state = parse_records(
        entries,
        provider_name,
        state,
        deduplicate_updates=True,
    )
    parsed = render_protocol_records("snapshot", provider_name, records, state)
    assert [
        {"text": m.text, "role": m.role, "contentType": m.content_type} for m in parsed
    ] == fixture["telegram"]


def test_exit_plan_mode_emits_plan_text_before_tool_controls():
    entries = [
        SourceEntry(
            0,
            {
                "type": "assistant",
                "uuid": "plan",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "plan-approval",
                            "name": "ExitPlanMode",
                            "input": {"plan": "1. Inspect the code\n2. Fix the bug"},
                        }
                    ],
                },
            },
        )
    ]
    records, state = parse_records(entries, "claude")

    messages = render_protocol_records("session", "claude", records, state)

    assert [message.content_type for message in messages] == ["text", "tool_use"]
    assert "1. Inspect the code\n2. Fix the bug" in messages[0].text
    assert "1. Inspect the code" not in messages[1].text
    assert messages[1].tool_name == "ExitPlanMode"


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            [
                {
                    "type": "response_item",
                    "payload": {
                        "id": "response-1",
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "done"}],
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "task_complete",
                        "last_agent_message": "done",
                    },
                },
            ],
            id="response-item-first",
        ),
        pytest.param(
            [
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "task_complete",
                        "last_agent_message": "done",
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "id": "response-1",
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "done"}],
                    },
                },
            ],
            id="task-complete-first",
        ),
    ],
)
def test_codex_live_mirror_renders_once_with_final_answer_phase(source):
    records, state = parse_records(
        [SourceEntry(index * 100, raw) for index, raw in enumerate(source)],
        "codex",
        deduplicate_updates=True,
    )

    messages = render_protocol_records("session", "codex", records, state)

    assert len(messages) == 1
    assert messages[0].text == "done"
    assert messages[0].phase == "final_answer"


def test_codex_cross_batch_completion_mirror_does_not_repeat_visible_text():
    response = SourceEntry(
        0,
        {
            "type": "response_item",
            "payload": {
                "id": "response-1",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "done"}],
            },
        },
    )
    task_complete = SourceEntry(
        100,
        {
            "type": "event_msg",
            "payload": {"type": "task_complete", "last_agent_message": "done"},
        },
    )

    first, state = parse_records([response], "codex", deduplicate_updates=True)
    second, _ = parse_records([task_complete], "codex", state, deduplicate_updates=True)

    assert len(first) == 1
    assert second == ()
