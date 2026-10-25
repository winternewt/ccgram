import json

import pytest

from ccgram.session_protocol.messages import (
    SourceEntry,
    build_messages,
    parse_records,
    read_entries,
)


@pytest.mark.parametrize("kind", ["user", "gemini", "info", "error"])
@pytest.mark.parametrize("content", [None, "", [{"meta": "ignored"}]])
def test_gemini_uses_display_content_when_content_has_no_parts(kind, content):
    entry = SourceEntry(
        0,
        {"type": kind, "content": content, "displayContent": [{"text": "visible"}]},
    )
    messages = build_messages([entry], "conv", "gemini")
    assert len(messages) == 1
    assert messages[0].parts[0].text == "visible"


def test_gemini_fallback_keeps_primary_tool_results():
    entries = [
        SourceEntry(
            0,
            {
                "type": "gemini",
                "id": "call",
                "toolCalls": [{"id": "t1", "name": "read", "args": {}}],
            },
        ),
        SourceEntry(
            100,
            {
                "type": "gemini",
                "id": "result",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "done"}
                ],
                "displayContent": "visible",
            },
        ),
    ]
    messages = build_messages(entries, "conv", "gemini")
    assert messages[0].parts[0].status == "completed"
    assert messages[0].parts[0].output == {"text": "done", "truncated": False}
    assert messages[1].parts[0].text == "visible"


def test_gemini_primary_content_takes_precedence_over_display_content():
    messages = build_messages(
        [
            SourceEntry(
                0,
                {"type": "gemini", "content": "primary", "displayContent": "fallback"},
            )
        ],
        "conv",
        "gemini",
    )
    assert messages[0].parts[0].text == "primary"


@pytest.mark.parametrize(
    "provider,entry",
    [
        (
            "claude",
            {
                "type": "assistant",
                "uuid": "native",
                "message": {
                    "id": "not-entry-id",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "hi"}],
                },
            },
        ),
        (
            "codex",
            {
                "type": "response_item",
                "payload": {
                    "id": "native",
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "hi"}],
                },
            },
        ),
        ("gemini", {"type": "gemini", "id": "native", "content": "hi"}),
        (
            "pi",
            {
                "type": "message",
                "id": "native",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "hi"}],
                },
            },
        ),
    ],
)
def test_native_message_identity_matches_history_and_live(provider, entry):
    source = [SourceEntry(200, entry)]
    live = build_messages(source, "conv", provider)
    history = build_messages(list(source), "conv", provider)
    assert live == history
    assert [(m.id, m.revision) for m in live] == [("conv/native", 1)]
    assert live[0].parts[0].id == "conv/native#0"
    assert live[0].parts[0].text == "hi"


def test_codex_no_id_uses_byte_offset_and_ignores_duplicate_event_messages():
    entries = [
        SourceEntry(
            10,
            {
                "type": "event_msg",
                "payload": {"type": "agent_message", "message": "hi"},
            },
        ),
        SourceEntry(
            200,
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "hi"}],
                },
            },
        ),
    ]
    assert [m.id for m in build_messages(entries, "conv", "codex")] == ["conv/o10"]


def test_codex_response_item_keeps_the_streamed_message_id_after_reread():
    streamed = SourceEntry(
        10,
        {"type": "event_msg", "payload": {"type": "agent_message", "message": "draft"}},
    )
    final = SourceEntry(
        200,
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "final"}],
            },
        },
    )

    before = build_messages([streamed], "conv", "codex")
    after = build_messages([streamed, final], "conv", "codex")
    streamed_records, parser_state = parse_records([streamed], "codex")
    appended_records, _ = parse_records([final], "codex", parser_state)

    assert before[0].id == after[0].id == "conv/o10"
    assert after[0].revision == 2
    assert after[0].parts[0].text == "final"
    assert streamed_records[0][1].key == appended_records[0][1].key == "o10"


def test_codex_response_then_matching_event_is_a_mirror_not_a_new_message():
    entries = [
        SourceEntry(
            10,
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "id": "native-1",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "done"}],
                },
            },
        ),
        SourceEntry(
            200,
            {
                "type": "event_msg",
                "payload": {"type": "agent_message", "message": "done"},
            },
        ),
    ]

    messages = build_messages(entries, "conv", "codex")

    assert [(message.id, message.parts[0].text) for message in messages] == [
        ("conv/native-1", "done")
    ]


def test_codex_same_text_in_later_user_turn_keeps_distinct_native_ids():
    def response(offset: int, message_id: str, role: str, text: str) -> SourceEntry:
        return SourceEntry(
            offset,
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "id": message_id,
                    "role": role,
                    "content": [
                        {
                            "type": "input_text" if role == "user" else "output_text",
                            "text": text,
                        }
                    ],
                },
            },
        )

    messages = build_messages(
        [
            response(0, "u1", "user", "first"),
            response(100, "a1", "assistant", "Done"),
            response(200, "u2", "user", "second"),
            response(300, "a2", "assistant", "Done"),
        ],
        "conv",
        "codex",
    )

    assert [message.id for message in messages] == [
        "conv/u1",
        "conv/a1",
        "conv/u2",
        "conv/a2",
    ]


def test_tool_pairing_updates_original_message_across_page_boundary():
    call = SourceEntry(
        0,
        {
            "type": "assistant",
            "uuid": "call",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool1",
                        "name": "Bash",
                        "input": {"command": "uv run pytest"},
                    }
                ],
            },
        },
    )
    middle = SourceEntry(
        300, {"type": "assistant", "uuid": "middle", "message": {"content": "waiting"}}
    )
    result = SourceEntry(
        500,
        {
            "type": "user",
            "uuid": "result",
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": "tool1", "content": "passed"}
                ]
            },
        },
    )
    before = build_messages([call, middle], "conv", "claude")
    after = build_messages([call, middle, result], "conv", "claude")
    assert before[0].revision == 1
    assert after[0].revision == 2
    assert len(after) == 2
    assert after[0].parts[0].status == "completed"
    assert after[0].parts[0].output == {"text": "passed", "truncated": False}


def test_gemini_repeated_native_id_increments_revision_for_content_updates():
    first = SourceEntry(0, {"id": "m1", "type": "gemini", "content": "first"})
    second = SourceEntry(100, {"id": "m1", "type": "gemini", "content": "second"})
    assert build_messages([first, second], "conv", "gemini")[0].revision == 2
    assert (
        build_messages([first, second], "conv", "gemini")[0].parts[0].text == "second"
    )


def test_reader_uses_utf8_byte_offsets_and_retries_partial_line(tmp_path):
    path = tmp_path / "session.jsonl"
    first = (
        json.dumps({"type": "gemini", "content": "שלום"}, ensure_ascii=False).encode()
        + b"\n"
    )
    path.write_bytes(first + b'{"type":"user","content":"hi"}')
    assert len(read_entries(path)) == 1
    with path.open("ab") as file:
        file.write(b"\n")
    assert [e.offset for e in read_entries(path)] == [0, len(first)]


def test_read_entries_accepts_the_verified_snapshot_bytes(tmp_path):
    first = b'{"type":"user","message":{"role":"user","content":"hi"}}\n'
    second = b'{"type":"assistant","message":{"role":"assistant","content":"ok"}}\n'
    path = tmp_path / "transcript.jsonl"
    path.write_bytes(first + second)

    from_path = read_entries(path)
    from_snapshot = read_entries(first + second)

    assert from_snapshot == from_path
    assert [entry.offset for entry in from_snapshot] == [0, len(first)]


def test_read_entries_normalizes_gemini_json_snapshot_for_history_and_live(tmp_path):
    path = tmp_path / "session.json"
    path.write_text(
        json.dumps(
            {
                "messages": [
                    {"id": "u1", "type": "user", "content": "question"},
                    {
                        "id": "a1",
                        "type": "gemini",
                        "content": "answer",
                        "toolCalls": [
                            {
                                "id": "tool1",
                                "name": "read_file",
                                "args": {"path": "README.md"},
                            }
                        ],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    entries = read_entries(path, "gemini")
    history = build_messages(entries, "conv", "gemini")
    live, _ = parse_records(entries, "gemini", deduplicate_updates=True)
    assert [message.id for message in history] == ["conv/u1", "conv/a1"]
    assert history[0].parts[0].text == "question"
    assert history[1].parts[1].tool_call_id == "tool1"
    assert [entry.offset for entry, _ in live] == [0, 1]


def test_codex_event_stream_and_task_complete_share_a_replaceable_message():
    entries = [
        SourceEntry(
            10,
            {
                "type": "event_msg",
                "payload": {"type": "agent_message", "message": "working"},
            },
        ),
        SourceEntry(
            30,
            {
                "type": "event_msg",
                "payload": {
                    "type": "task_complete",
                    "last_agent_message": "working and done",
                },
            },
        ),
    ]

    messages = build_messages(entries, "conv", "codex")

    assert len(messages) == 1
    assert messages[0].id == "conv/o10"
    assert messages[0].revision == 2
    assert messages[0].parts[0].text == "working and done"
    assert messages[0].complete is True
    assert messages[0].phase == "final_answer"


@pytest.mark.parametrize(
    ("entries", "expected_records"),
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
            2,
            id="response-item-then-task-complete",
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
            1,
            id="task-complete-then-response-item",
        ),
        pytest.param(
            [
                {
                    "type": "response_item",
                    "payload": {
                        "id": "response-1",
                        "type": "message",
                        "role": "assistant",
                        "phase": "final_answer",
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
            1,
            id="response-item-already-has-final-phase",
        ),
    ],
)
def test_codex_task_complete_mirror_preserves_final_answer_metadata(
    entries, expected_records
):
    source_entries = [
        SourceEntry(index * 100, entry) for index, entry in enumerate(entries)
    ]
    records, _ = parse_records(source_entries, "codex")
    messages = build_messages(source_entries, "conv", "codex")

    assert len(records) == expected_records
    assert len(messages) == 1
    assert messages[0].parts[0].text == "done"
    assert messages[0].phase == "final_answer"


def test_codex_task_complete_still_completes_streamed_message():
    entries = [
        SourceEntry(
            10,
            {
                "type": "event_msg",
                "payload": {"type": "agent_message", "message": "draft"},
            },
        ),
        SourceEntry(
            20,
            {
                "type": "event_msg",
                "payload": {"type": "task_complete", "last_agent_message": "final"},
            },
        ),
    ]

    messages = build_messages(entries, "conv", "codex")

    assert len(messages) == 1
    assert messages[0].revision == 2
    assert messages[0].parts[0].text == "final"
    assert messages[0].complete is True


def test_codex_identical_response_text_in_a_separate_turn_is_kept():
    entries = [
        SourceEntry(
            10,
            {
                "type": "response_item",
                "payload": {
                    "id": "first",
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "same"}],
                },
            },
        ),
        SourceEntry(
            20,
            {
                "type": "event_msg",
                "payload": {"type": "task_complete", "last_agent_message": "same"},
            },
        ),
        SourceEntry(
            30,
            {
                "type": "response_item",
                "payload": {"type": "message", "role": "user", "content": []},
            },
        ),
        SourceEntry(
            40,
            {
                "type": "response_item",
                "payload": {
                    "id": "second",
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "same"}],
                },
            },
        ),
    ]

    messages = build_messages(entries, "conv", "codex")

    assert [(message.id, message.parts[0].text) for message in messages] == [
        ("conv/first", "same"),
        ("conv/second", "same"),
    ]


def test_codex_same_text_with_distinct_native_ids_in_one_turn_keeps_final_phase():
    entries = [
        SourceEntry(
            10,
            {
                "type": "response_item",
                "payload": {
                    "id": "commentary-1",
                    "type": "message",
                    "role": "assistant",
                    "phase": "commentary",
                    "content": [{"type": "output_text", "text": "Done"}],
                },
            },
        ),
        SourceEntry(
            20,
            {
                "type": "response_item",
                "payload": {
                    "id": "final-1",
                    "type": "message",
                    "role": "assistant",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": "Done"}],
                },
            },
        ),
    ]

    messages = build_messages(entries, "conv", "codex")
    live_records, _ = parse_records(entries, "codex", deduplicate_updates=True)

    assert [
        (message.id, message.parts[0].text, message.phase) for message in messages
    ] == [
        ("conv/commentary-1", "Done", "commentary"),
        ("conv/final-1", "Done", "final_answer"),
    ]
    assert [record.key for _, record in live_records] == [
        "commentary-1",
        "final-1",
    ]


def test_codex_consumes_only_one_response_mirror_after_task_complete():
    entries = [
        SourceEntry(
            0,
            {
                "type": "event_msg",
                "payload": {"type": "task_complete", "last_agent_message": "Done"},
            },
        ),
        SourceEntry(
            10,
            {
                "type": "response_item",
                "payload": {
                    "id": "mirror",
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Done"}],
                },
            },
        ),
        SourceEntry(
            20,
            {
                "type": "response_item",
                "payload": {
                    "id": "next-message",
                    "type": "message",
                    "role": "assistant",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": "Done"}],
                },
            },
        ),
    ]

    records, _ = parse_records(entries, "codex", deduplicate_updates=True)

    assert [(record.key, record.phase) for _, record in records] == [
        ("o0", "final_answer"),
        ("next-message", "final_answer"),
    ]
