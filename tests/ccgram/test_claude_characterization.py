"""Characterization tests locking Claude-specific behaviors.

These tests document current Claude Code integration behavior that is
specific to the Claude provider — NOT covered by the generic contract
tests in test_provider_contracts.py.
"""

import pytest

from ccgram.cc_commands import CC_BUILTINS
from ccgram.providers.base import UUID_RE
from ccgram.terminal_parser import (
    STATUS_SPINNERS,
    UI_PATTERNS,
    extract_interactive_content,
    parse_status_line,
)
from ccgram.expandable_quote import EXPANDABLE_QUOTE_END, EXPANDABLE_QUOTE_START
from ccgram.providers.claude import ClaudeProvider
from ccgram.session_protocol.messages import SourceEntry, build_messages, parse_records

# ── Hook payload format ──────────────────────────────────────────────────


class TestClaudeHookPayloadFormat:
    def test_uuid_validation_accepts_valid(self) -> None:
        assert UUID_RE.match("a1b2c3d4-e5f6-7890-abcd-ef1234567890")

    @pytest.mark.parametrize(
        "invalid",
        [
            "",
            "not-a-uuid",
            "A1B2C3D4-E5F6-7890-ABCD-EF1234567890",  # uppercase rejected
            "a1b2c3d4e5f6-7890-abcd-ef1234567890",  # missing dash
            "a1b2c3d4-e5f6-7890-abcd-ef123456789",  # too short
        ],
    )
    def test_uuid_validation_rejects_invalid(self, invalid: str) -> None:
        assert UUID_RE.match(invalid) is None


# ── ClaudeProvider-specific method tests ─────────────────────────────────


class TestClaudeProviderMethods:
    def test_invalid_resume_id_raises(self) -> None:
        provider = ClaudeProvider()
        with pytest.raises(ValueError, match="Invalid resume_id"):
            provider.make_launch_args(resume_id="not-a-uuid")

    def test_shared_parser_preserves_tool_call_and_result(
        self, make_jsonl_entry
    ) -> None:
        entries = [
            make_jsonl_entry(
                "assistant",
                [
                    {
                        "type": "tool_use",
                        "id": "tu1",
                        "name": "Read",
                        "input": {"file_path": "/tmp/test.py"},
                    }
                ],
            ),
            make_jsonl_entry(
                "user",
                [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tu1",
                        "content": "file contents",
                    }
                ],
            ),
        ]
        messages = build_messages(
            [SourceEntry(index * 100, entry) for index, entry in enumerate(entries)],
            "conv",
            "claude",
        )

        assert len(messages) == 1
        assert messages[0].parts[0].name == "Read"
        assert messages[0].parts[0].tool_call_id == "tu1"
        assert messages[0].parts[0].output["text"] == "file contents"

    def test_spinner_status_parsed(self) -> None:
        provider = ClaudeProvider()
        pane = "output\n✻ Reading file\n" + "─" * 30 + "\n❯ \n" + "─" * 30 + "\n"
        result = provider.parse_terminal_status(pane)
        assert result is not None
        assert "Reading file" in result.raw_text
        assert result.is_interactive is False


# ── Transcript format ────────────────────────────────────────────────────


class TestClaudeTranscriptFormat:
    def test_expquote_sentinels(self) -> None:
        assert EXPANDABLE_QUOTE_START == "\x02EXPQUOTE_START\x02"
        assert EXPANDABLE_QUOTE_END == "\x02EXPQUOTE_END\x02"

    def test_exit_plan_mode_emits_plan_text(self, make_jsonl_entry) -> None:
        plan_text = "## Plan\n1. Do thing\n2. Do other thing"
        entries = [
            make_jsonl_entry(
                "assistant",
                [
                    {
                        "type": "tool_use",
                        "id": "epm1",
                        "name": "ExitPlanMode",
                        "input": {"plan": plan_text},
                    }
                ],
            )
        ]
        messages = build_messages([SourceEntry(0, entries[0])], "claude-test", "claude")
        assert len(messages) == 1
        assert messages[0].parts[0].type == "tool"
        assert messages[0].parts[0].name == "ExitPlanMode"
        assert messages[0].parts[0].input == {"plan": plan_text}

    def test_summary_entries_skipped(self, make_jsonl_entry) -> None:
        entries = [
            {"type": "summary", "summary": "conversation summary"},
            make_jsonl_entry("assistant", [{"type": "text", "text": "hello"}]),
        ]
        source = [
            SourceEntry(index * 100, entry) for index, entry in enumerate(entries)
        ]
        records, _ = parse_records(source, "claude")
        result = build_messages(source, "claude-test", "claude")
        assert len(records) == len(result) == 1
        assert result[0].parts[0].text == "hello"

    def test_file_history_snapshot_skipped(self) -> None:
        entries = [
            {"type": "file-history-snapshot", "data": {}},
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "hi"}]},
            },
        ]
        source = [
            SourceEntry(index * 100, entry) for index, entry in enumerate(entries)
        ]
        records, _ = parse_records(source, "claude")
        result = build_messages(source, "claude-test", "claude")
        assert len(records) == len(result) == 1
        assert result[0].parts[0].text == "hi"


# ── Terminal signatures ──────────────────────────────────────────────────


class TestClaudeTerminalSignatures:
    def test_exact_spinner_charset(self) -> None:
        expected = frozenset(["·", "✻", "✽", "✶", "✳", "✢"])
        assert expected == STATUS_SPINNERS

    def test_all_ui_types_defined(self) -> None:
        names = {p.name for p in UI_PATTERNS}
        expected = {
            "ExitPlanMode",
            "AskUserQuestion",
            "PermissionPrompt",
            "RestoreCheckpoint",
            "Settings",
            "SelectModel",
            "SelectionUI",
        }
        assert names == expected

    def test_two_separator_layout_recognized(self) -> None:
        sep = "─" * 30
        pane = f"output\n✻ Reading file\n{sep}\n❯ \n{sep}\n"
        result = parse_status_line(pane)
        assert result is not None
        assert "Reading file" in result

    def test_interactive_ui_detection(self) -> None:
        pane = (
            "  Would you like to proceed?\n"
            "  ─────────────────────────────────\n"
            "  Yes     No\n"
            "  ─────────────────────────────────\n"
            "  ctrl-g to edit in vim\n"
        )
        result = extract_interactive_content(pane)
        assert result is not None
        assert result.name == "ExitPlanMode"


# ── Command discovery ────────────────────────────────────────────────────


class TestClaudeCommandDiscovery:
    def test_cc_builtins_include_core_commands(self) -> None:
        # Core CC commands that have shipped for multiple versions and are
        # exercised by other tests. The full set may grow as the docs evolve;
        # see code.claude.com/docs/en/commands.
        core = {
            "clear",
            "compact",
            "effort",
            "help",
            "init",
            "mcp",
            "memory",
            "model",
            "permissions",
            "plan",
            "rc",
            "remote-control",
            "status",
            "tasks",
            # All 12 picker commands — must remain in CC_BUILTINS.
            "agents",
            "copy",
            "diff",
            "release-notes",
            "rewind",
            "settings",
            "skills",
            "theme",
            "tui",
        }
        assert core <= set(CC_BUILTINS.keys())

    def test_cc_builtins_exclude_bot_native_collisions(self) -> None:
        # /resume collides with bot-native /resume (handlers/recovery/resume_command.py)
        assert "resume" not in CC_BUILTINS

    def test_claude_builtins_do_not_advertise_new(self) -> None:
        assert "new" not in CC_BUILTINS
