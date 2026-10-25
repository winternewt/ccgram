"""Claude transcript records normalized by the shared session protocol."""

from ccgram.session_protocol.messages import SourceEntry, build_messages, parse_records
from ccgram.transcript_reader import render_protocol_records


class TestCanonicalClaudeTranscript:
    @staticmethod
    def _entries(*data: dict) -> list[SourceEntry]:
        return [SourceEntry(index * 100, item) for index, item in enumerate(data)]

    def test_normalizes_assistant_user_and_thinking_parts(self) -> None:
        messages = build_messages(
            self._entries(
                {
                    "type": "assistant",
                    "uuid": "assistant-1",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "answer"},
                            {"type": "thinking", "thinking": "reasoning"},
                        ],
                    },
                },
                {
                    "type": "user",
                    "uuid": "user-1",
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": "question"}],
                    },
                },
            ),
            "claude-test",
            "claude",
        )

        assert [message.role for message in messages] == ["agent", "user"]
        assert [part.type for part in messages[0].parts] == ["text", "thinking"]
        assert [part.text for part in messages[0].parts] == ["answer", "reasoning"]
        assert messages[1].parts[0].text == "question"

    def test_tool_result_completes_original_call(self) -> None:
        messages = build_messages(
            self._entries(
                {
                    "type": "assistant",
                    "uuid": "call-1",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "tool-1",
                                "name": "Read",
                                "input": {"file_path": "src/app.py"},
                            }
                        ],
                    },
                },
                {
                    "type": "user",
                    "uuid": "result-1",
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "tool-1",
                                "content": "file contents",
                            }
                        ],
                    },
                },
            ),
            "claude-test",
            "claude",
        )

        assert len(messages) == 1
        assert messages[0].id == "claude-test/call-1"
        tool = messages[0].parts[0]
        assert tool.type == "tool"
        assert tool.name == "Read"
        assert tool.tool_call_id == "tool-1"
        assert tool.status == "completed"
        assert tool.output == {"text": "file contents", "truncated": False}

    def test_summary_and_file_history_records_are_skipped(self) -> None:
        entries = self._entries(
            {"type": "summary", "summary": "old summary"},
            {"type": "file-history-snapshot", "data": {}},
            {
                "type": "assistant",
                "uuid": "visible",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "visible reply"}],
                },
            },
        )

        records, _ = parse_records(entries, "claude")
        messages = build_messages(entries, "claude-test", "claude")

        assert [entry.data["type"] for entry, _ in records] == ["assistant"]
        assert [message.parts[0].text for message in messages] == ["visible reply"]

    def test_telegram_rendering_keeps_user_text_and_formats_thinking(self) -> None:
        entries = self._entries(
            {
                "type": "user",
                "uuid": "user-1",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "  question  "}],
                },
            },
            {
                "type": "assistant",
                "uuid": "assistant-1",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "reasoning"},
                        {"type": "text", "text": "answer"},
                    ],
                },
            },
        )
        records, state = parse_records(entries, "claude")

        rendered = render_protocol_records("session", "claude", records, state)

        assert [(event.role, event.content_type) for event in rendered] == [
            ("user", "text"),
            ("assistant", "thinking"),
            ("assistant", "text"),
        ]
        assert rendered[0].text == "question"
        assert "reasoning" in rendered[1].text
        assert rendered[2].text == "answer"

    def test_tool_result_keeps_failure_state_and_rendered_output(self) -> None:
        entries = self._entries(
            {
                "type": "assistant",
                "uuid": "call",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool-1",
                            "name": "Bash",
                            "input": {"command": "false"},
                        }
                    ],
                },
            },
            {
                "type": "user",
                "uuid": "result",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool-1",
                            "content": "Permission denied",
                            "is_error": True,
                        }
                    ],
                },
            },
        )

        messages = build_messages(entries, "session", "claude")
        records, state = parse_records(entries, "claude")
        rendered = render_protocol_records("session", "claude", records, state)

        tool = messages[0].parts[0]
        assert tool.status == "failed"
        assert rendered[-1].content_type == "tool_result"
        assert "⚠️" in rendered[-1].text
        assert "Permission denied" in rendered[-1].text

    def test_local_command_output_is_not_exposed_as_raw_transcript_markup(self) -> None:
        entries = self._entries(
            {
                "type": "user",
                "uuid": "command",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "<command-name>/status</command-name>"
                                "<local-command-stdout>all good</local-command-stdout>"
                            ),
                        }
                    ],
                },
            }
        )
        messages = build_messages(entries, "session", "claude")
        assert len(messages) == 1
        assert messages[0].role == "agent"
        assert messages[0].parts[0].text == "all good"
        records, state = parse_records(entries, "claude")

        rendered = render_protocol_records("session", "claude", records, state)

        assert len(rendered) == 1
        assert rendered[0].content_type == "local_command"
        assert "/status" in rendered[0].text
        assert "all good" in rendered[0].text
        assert "<local-command-stdout>" not in rendered[0].text

    def test_local_command_stdout_uses_prior_invocation_across_batches(self) -> None:
        invocation = self._entries(
            {
                "type": "user",
                "uuid": "command-invocation",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "<command-name>/status</command-name>",
                        }
                    ],
                },
            }
        )
        stdout = self._entries(
            {
                "type": "user",
                "uuid": "command-stdout",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "<local-command-stdout>ready</local-command-stdout>",
                        }
                    ],
                },
            }
        )
        invocation_records, state = parse_records(invocation, "claude")
        assert invocation_records == ()
        stdout_records, state = parse_records(stdout, "claude", state)
        assert len(stdout_records) == 1
        assert stdout_records[0][1].role == "agent"
        rendered = render_protocol_records("session", "claude", stdout_records, state)
        assert len(rendered) == 1
        assert rendered[0].content_type == "local_command"
        assert "/status" in rendered[0].text
        assert "ready" in rendered[0].text
        assert "<local-command-stdout>" not in rendered[0].text

        combined = build_messages(invocation + stdout, "session", "claude")
        assert len(combined) == 1
        assert combined[0].role == "agent"
        assert combined[0].parts[0].text == "ready"

    def test_standalone_local_command_stdout_is_an_agent_notice(self) -> None:
        entries = self._entries(
            {
                "type": "user",
                "uuid": "command-stdout",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "<local-command-stdout>ready</local-command-stdout>",
                        }
                    ],
                },
            }
        )

        messages = build_messages(entries, "session", "claude")
        records, state = parse_records(entries, "claude")
        rendered = render_protocol_records("session", "claude", records, state)

        assert len(messages) == 1
        assert messages[0].role == "agent"
        assert messages[0].parts[0].text == "ready"
        assert len(rendered) == 1
        assert rendered[0].role == "assistant"
        assert rendered[0].content_type == "local_command"
        assert rendered[0].text == "`ready`"

    def test_internal_user_metadata_is_not_rendered_as_chat(self) -> None:
        entries = self._entries(
            {
                "type": "user",
                "uuid": "metadata",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "<system-reminder>internal</system-reminder>",
                        }
                    ],
                },
            }
        )
        records, state = parse_records(entries, "claude")

        rendered = render_protocol_records("session", "claude", records, state)

        assert rendered == []

    def test_ansi_control_sequences_are_removed_from_telegram_text(self) -> None:
        entries = self._entries(
            {
                "type": "assistant",
                "uuid": "ansi",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "\x1b[32mgreen\x1b[0m text"}],
                },
            }
        )
        records, state = parse_records(entries, "claude")

        rendered = render_protocol_records("session", "claude", records, state)

        assert [event.text for event in rendered] == ["green text"]

    def test_edit_tool_result_keeps_diff_summary_for_telegram(self) -> None:
        entries = self._entries(
            {
                "type": "assistant",
                "uuid": "edit",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool-1",
                            "name": "Edit",
                            "input": {
                                "file_path": "main.py",
                                "old_string": "old line",
                                "new_string": "new line",
                            },
                        }
                    ],
                },
            },
            {
                "type": "user",
                "uuid": "result",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool-1",
                            "content": "OK",
                        }
                    ],
                },
            },
        )
        records, state = parse_records(entries, "claude")

        rendered = render_protocol_records("session", "claude", records, state)

        assert rendered[-1].content_type == "tool_result"
        assert "+1" in rendered[-1].text
        assert "−1" in rendered[-1].text
