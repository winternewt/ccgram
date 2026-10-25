"""Provider-native records to messages. History and live use this one builder.

No Telegram formatting or mutable counters. Rebuilding a transcript prefix gives
identical IDs/revisions after a restart. Tool pairing precedes pagination.
"""

from __future__ import annotations

import json
import re
import difflib
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from datetime import UTC, datetime
from typing import Any

from .models import Message, Part

_PART_FIELDS = frozenset(part_field.name for part_field in fields(Part)) - {"id"}
_CHAT_ROLES = ("user", "assistant")
_CODEX_USER_CONTEXT_RE = re.compile(
    r"^(?:<(?:environment_context|permissions|user_instructions)(?:\s|>)|"
    r"# AGENTS\.md instructions for )"
)

_ANTIGRAVITY_METADATA_RE = re.compile(
    r"<(ADDITIONAL_METADATA|USER_SETTINGS_CHANGE|USER_INFORMATION|USER_RULES|SKILLS|PLUGINS|SUBAGENTS|MESSAGING|CONVERSATION_TRANSCRIPT|ARTIFACTS|SLASH_COMMANDS|GUIDELINES|COMMUNICATION_STYLE)>[\s\S]*?</\1>",
    re.IGNORECASE,
)
_ANTIGRAVITY_USER_REQUEST_RE = re.compile(r"</?USER_REQUEST>", re.IGNORECASE)
_CLAUDE_SYSTEM_TAG_RE = re.compile(
    r"<(?:bash-input|bash-stdout|bash-stderr|local-command-caveat|system-reminder|"
    r"teammate-message|agent-message|cross-session-message|task-notification)\b",
    re.IGNORECASE,
)
_CLAUDE_COMMAND_NAME_RE = re.compile(r"<command-name>(.*?)</command-name>", re.DOTALL)
_CLAUDE_LOCAL_STDOUT_RE = re.compile(
    r"<local-command-stdout>(.*?)</local-command-stdout>", re.DOTALL
)
_ANSI_SGR_RE = re.compile(r"\x1b\[[0-9;]*m")
# Claude Code appends this trust notice to every message relayed from a peer
# session. It is addressed to the model, so it is noise in a topic.
_CLAUDE_PEER_NOTICE_RE = re.compile(
    r"\s*This came from another Claude session — .*?(?=\n\s*\n|\Z)", re.DOTALL
)
# A peer session's message: the header Claude Code puts before it, the wrapper,
# and the sender name that is the only useful attribute.
_CLAUDE_PEER_HEADER = "Another Claude session sent a message:"
_CLAUDE_PEER_MESSAGE_RE = re.compile(
    r"(?:Another Claude session sent a message:\s*)?"
    r"<cross-session-message\b([^>]*)>\s*(.*?)\s*</cross-session-message>",
    re.DOTALL,
)
_CLAUDE_PEER_NAME_RE = re.compile(r'from-name="([^"]*)"')
# Shown in place of a compaction summary. The summary itself is the agent's
# context, not a turn in the conversation, and relaying it costs six Telegram
# messages; the user still needs to know the earlier turns are gone.
COMPACT_NOTICE = "\U0001f5dc Context compacted \u2014 earlier turns are now a summary."


@dataclass(frozen=True, slots=True)
class SourceEntry:
    offset: int
    data: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TranscriptRecord:
    key: str
    role: str
    time: str | None
    parts: tuple[dict[str, Any], ...]
    results: tuple[tuple[str, Any, bool], ...] = ()
    complete: bool = True
    phase: str | None = None


def read_entries(
    source: Path | bytes, provider: str | None = None
) -> list[SourceEntry]:
    """Parse complete JSONL records from a path or immutable byte snapshot.

    Byte offsets always refer to the original transcript. An unfinished final
    line is ignored; the next poll will read it after it is completed.
    """
    raw = source.read_bytes() if isinstance(source, Path) else source
    result = []
    if provider == "gemini":
        try:
            document = json.loads(raw)
        except ValueError, UnicodeDecodeError:
            document = None
        if isinstance(document, dict) and isinstance(document.get("messages"), list):
            # Gemini stores one JSON snapshot rather than JSONL. Its native
            # message id is the stable identity; the array index is the
            # fallback source ordinal for records without an id.
            return [
                SourceEntry(index, entry)
                for index, entry in enumerate(document["messages"])
                if isinstance(entry, dict)
            ]
    result, _ = read_complete_entries(raw)
    return result


def read_complete_entries(raw: bytes, start: int = 0) -> tuple[list[SourceEntry], int]:
    """Parse the complete JSONL lines of ``raw``, which begins at byte ``start``.

    Returns the entries with absolute offsets and the offset where parsing
    stopped: the end of the last complete line, where the next read resumes.
    """
    result = []
    offset = start
    for line in raw.splitlines(keepends=True):
        if not line.endswith(b"\n"):
            break
        try:
            data = json.loads(line)
        except ValueError, UnicodeDecodeError:
            data = None
        if isinstance(data, dict):
            result.append(SourceEntry(offset, data))
        offset += len(line)
    return result, offset


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(
            str(item.get("text", "")) for item in value if isinstance(item, dict)
        )
    return json.dumps(value, ensure_ascii=False) if value is not None else ""


def tool_kind(name: str) -> str:
    lower = name.lower()
    for kind, names in (
        ("read", ("read",)),
        ("edit", ("edit", "write", "patch")),
        ("search", ("grep", "glob", "search", "find")),
        ("execute", ("bash", "shell", "exec", "command")),
        ("fetch", ("fetch", "web")),
        ("think", ("think",)),
    ):
        if any(word in lower for word in names):
            return kind
    return "other"


def _tool(call_id: str, name: str, arguments: Any) -> dict[str, Any]:
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            arguments = {"text": arguments}
    title = name
    if isinstance(arguments, dict):
        title = next(
            (
                str(arguments[k])
                for k in ("command", "cmd", "file_path", "path", "query", "pattern")
                if k in arguments
            ),
            name,
        )
    diff = None
    if name in {"Edit", "NotebookEdit"} and isinstance(arguments, dict):
        old = arguments.get("old_string")
        new = arguments.get("new_string")
        if isinstance(old, str) and isinstance(new, str) and old and new:
            lines = list(
                difflib.unified_diff(
                    old.splitlines(keepends=True),
                    new.splitlines(keepends=True),
                    lineterm="",
                )
            )[2:]
            diff_text = "\n".join(lines)
            if diff_text:
                diff = {
                    "text": diff_text,
                    "added": sum(line.startswith("+") for line in lines),
                    "removed": sum(line.startswith("-") for line in lines),
                }
    return {
        "type": "tool",
        "tool_call_id": call_id,
        "name": name,
        "kind": tool_kind(name),
        "status": "running",
        "title": title,
        "input": arguments,
        "diff": diff,
    }


def _content(content: Any) -> tuple[list[dict[str, Any]], list[tuple[str, Any, bool]]]:
    if isinstance(content, str):
        return ([{"type": "text", "text": content}] if content else []), []
    parts: list[dict[str, Any]] = []
    results: list[tuple[str, Any, bool]] = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict):
            continue
        kind = block.get("type", "text")
        if not isinstance(kind, str):
            continue
        if kind in {"text", "input_text", "output_text"} and block.get("text"):
            parts.append({"type": "text", "text": str(block["text"])})
        elif kind in {"thinking", "reasoning"}:
            parts.append(
                {
                    "type": "thinking",
                    "text": str(block.get("thinking", block.get("text", ""))),
                }
            )
        elif kind in {"tool_use", "toolCall"}:
            parts.append(
                _tool(
                    str(block.get("id", "")),
                    str(block.get("name", "unknown")),
                    block.get("input", block.get("arguments", {})),
                )
            )
        elif kind == "tool_result":
            results.append(
                (
                    str(block.get("tool_use_id", "")),
                    block.get("content"),
                    bool(block.get("is_error")),
                )
            )
        elif kind in {"image", "image_url", "input_image"}:
            # Agent-local paths and embedded data are not public API URLs.
            parts.append(
                {
                    "type": "notice",
                    "level": "info",
                    "text": "Image in transcript; view it in the terminal",
                }
            )
    return parts, results


@dataclass(slots=True)
class _CodexState:
    stream_key: str | None = None
    stream_text: str | None = None
    last_key: str | None = None
    last_text: str | None = None
    last_phase: str | None = None
    task_complete_mirror_text: str | None = None


@dataclass(slots=True)
class TranscriptParserState:
    """Cross-batch state needed only for provider update/dedup semantics."""

    codex: _CodexState = field(default_factory=_CodexState)
    gemini_message_ids: set[str] = field(default_factory=set)
    gemini_tool_call_ids: set[str] = field(default_factory=set)
    gemini_result_signatures: set[tuple[str, str, bool]] = field(default_factory=set)
    tool_names: dict[str, str] = field(default_factory=dict)
    tool_summaries: dict[str, str] = field(default_factory=dict)
    tool_diffs: dict[str, dict[str, Any]] = field(default_factory=dict)
    pending_tools: dict[str, str] = field(default_factory=dict)
    claude_local_command_name: str | None = None


@dataclass(frozen=True, slots=True)
class TranscriptBatch:
    """Normalized live records and the source checkpoint that owns them."""

    session_id: str
    provider_name: str
    records: tuple[tuple[SourceEntry, TranscriptRecord], ...]
    byte_checkpoint: int
    tool_names: tuple[tuple[str, str], ...] = ()
    tool_summaries: tuple[tuple[str, str], ...] = ()
    tool_diffs: tuple[tuple[str, dict[str, Any]], ...] = ()


def _codex(entry: SourceEntry, state: _CodexState) -> TranscriptRecord | None:
    raw = entry.data
    if raw.get("type") not in ("response_item", "input_item"):
        return None
    data = raw.get("payload")
    if not isinstance(data, dict):
        return None
    timestamp = raw.get("timestamp")
    key = str(data.get("id") or f"o{entry.offset}")
    kind = data.get("type")
    if raw.get("type") == "input_item":
        if data.get("role") != "user" or kind not in (None, "message"):
            return None
        return _codex_message(data, key, timestamp, state)
    if not isinstance(kind, str):
        return None
    if kind == "message" and data.get("role") in _CHAT_ROLES:
        if data.get("role") == "user":
            parts, _ = _content(data.get("content"))
            first_text = next(
                (part["text"] for part in parts if part.get("type") == "text"), ""
            )
            if _CODEX_USER_CONTEXT_RE.match(first_text.lstrip()):
                return None
        return _codex_message(data, key, timestamp, state)
    return _codex_other_item(data, key, timestamp, state)


def _codex_other_item(
    data: dict[str, Any], key: str, timestamp: Any, state: _CodexState
) -> TranscriptRecord | None:
    kind = data.get("type")
    if kind in {"function_call", "custom_tool_call"}:
        part = _tool(
            str(data.get("call_id", "")),
            str(data.get("name", "unknown")),
            data.get("arguments", data.get("input", {})),
        )
        state.stream_key = None
        state.stream_text = None
        return TranscriptRecord(key, "agent", timestamp, (part,))
    if kind in {"function_call_output", "custom_tool_call_output"}:
        return TranscriptRecord(
            key,
            "agent",
            timestamp,
            (),
            ((str(data.get("call_id", "")), data.get("output"), False),),
        )
    if kind == "reasoning":
        return TranscriptRecord(
            key,
            "agent",
            timestamp,
            (
                {
                    "type": "thinking",
                    "text": _text(data.get("summary", data.get("content"))),
                },
            ),
        )
    return None


def _codex_message(
    data: dict[str, Any], key: str, timestamp: Any, state: _CodexState
) -> TranscriptRecord | None:
    parts, results = _content(data.get("content"))
    role = "agent" if data.get("role") == "assistant" else str(data.get("role"))
    if role != "agent":
        state.stream_key = None
        state.stream_text = None
        state.last_key = None
        state.last_text = None
        state.last_phase = None
        state.task_complete_mirror_text = None
    text = "\n".join(str(part["text"]) for part in parts if part.get("type") == "text")
    if role == "agent" and text:
        if text == state.task_complete_mirror_text:
            state.task_complete_mirror_text = None
            return None
        state.task_complete_mirror_text = None
        if state.stream_key:
            key = state.stream_key
        state.stream_key = None
        state.stream_text = None
        state.last_key = key
        state.last_text = text
    phase = data.get("phase")
    if role == "agent" and text:
        state.last_phase = phase if isinstance(phase, str) and phase else None
    return TranscriptRecord(
        key,
        role,
        timestamp if isinstance(timestamp, str) else None,
        tuple(parts),
        tuple(results),
        phase=phase if isinstance(phase, str) and phase else None,
    )


def _codex_event(entry: SourceEntry, state: _CodexState) -> TranscriptRecord | None:
    raw = entry.data
    payload = raw.get("payload")
    if not isinstance(payload, dict):
        return None
    timestamp = raw.get("timestamp")
    kind = payload.get("type")
    if kind == "agent_message":
        text = payload.get("message")
        if not isinstance(text, str) or not text:
            return None
        if not state.stream_key and text == state.last_text:
            return None
        key = state.stream_key or f"o{entry.offset}"
        state.stream_key = key
        state.stream_text = text
        state.last_key = key
        state.last_text = text
        state.last_phase = None
        return TranscriptRecord(
            key,
            "agent",
            timestamp,
            ({"type": "text", "text": text},),
            complete=False,
        )
    if kind == "task_complete":
        text = payload.get("last_agent_message")
        if not isinstance(text, str) or not text:
            state.stream_key = None
            state.stream_text = None
            return None
        if (
            not state.stream_key
            and text == state.last_text
            and state.last_phase == "final_answer"
        ):
            state.stream_key = None
            state.stream_text = None
            state.task_complete_mirror_text = text
            return None
        key = state.stream_key or state.last_key or f"o{entry.offset}"
        state.stream_key = None
        state.stream_text = None
        state.last_key = key
        state.last_text = text
        state.last_phase = "final_answer"
        state.task_complete_mirror_text = text
        return TranscriptRecord(
            key,
            "agent",
            timestamp,
            ({"type": "text", "text": text},),
            phase="final_answer",
        )
    return None


def _gemini_output(call: dict[str, Any]) -> Any:
    output = call.get("resultDisplay") or call.get("result")
    if not isinstance(output, list):
        return output
    responses = []
    for item in output:
        response = item.get("functionResponse") if isinstance(item, dict) else None
        response = response.get("response") if isinstance(response, dict) else None
        if isinstance(response, dict):
            value = next(
                (response[k] for k in ("output", "error", "result") if response.get(k)),
                "",
            )
            responses.append(_text(value))
        elif isinstance(item, dict) and item.get("text"):
            responses.append(str(item["text"]))
    return "\n".join(responses)


def _gemini(entry: SourceEntry) -> TranscriptRecord | None:
    data = entry.data
    kind = data.get("type")
    if not isinstance(kind, str):
        return None
    if kind not in {"user", "gemini", "info", "error"}:
        return None
    key = str(data.get("id") or f"o{entry.offset}")
    content = data.get("content", "")
    parts, results = _content(content)
    if not parts:
        display_content = data.get("displayContent", "")
        parts, fallback_results = _content(display_content)
        if parts:
            content = display_content
        results.extend(fallback_results)
    if kind in {"info", "error"}:
        parts = [
            {
                "type": "notice",
                "level": "error" if kind == "error" else "info",
                "text": _text(content) or _text(data.get("displayContent", "")),
            }
        ]
    calls = data.get("toolCalls")
    for call in calls if isinstance(calls, list) else []:
        if not isinstance(call, dict):
            continue
        call_id = str(call.get("id", ""))
        parts.append(
            _tool(
                call_id,
                str(call.get("name", call.get("displayName", "unknown"))),
                call.get("args", {}),
            )
        )
        output = _gemini_output(call)
        if output is not None:
            results.append((call_id, output, call.get("status") == "error"))
    return TranscriptRecord(
        key,
        "user" if kind == "user" else "agent",
        data.get("timestamp"),
        tuple(parts),
        tuple(results),
    )


def _chat(entry: SourceEntry, provider: str) -> TranscriptRecord | None:
    data = entry.data
    msg = data.get("message")
    if not isinstance(msg, dict):
        return None
    role = msg.get("role", data.get("type"))
    key = str(data.get("uuid" if provider == "claude" else "id") or f"o{entry.offset}")
    timestamp = data.get("timestamp") or msg.get("timestamp")
    if isinstance(timestamp, (int, float)):
        timestamp = datetime.fromtimestamp(timestamp / 1000, UTC).isoformat()
    if not isinstance(role, str):
        return None
    if role == "toolResult":
        return TranscriptRecord(
            key,
            "agent",
            timestamp,
            (),
            (
                (
                    str(msg.get("toolCallId", "")),
                    msg.get("content"),
                    bool(msg.get("isError")),
                ),
            ),
        )
    if role not in _CHAT_ROLES:
        return None
    parts, results = _content(msg.get("content"))
    if provider == "pi" and role == "assistant" and msg.get("stopReason") == "error":
        error_message = msg.get("errorMessage")
        error_text = (
            error_message.strip()
            if isinstance(error_message, str) and error_message.strip()
            else "Provider error"
        )
        parts.append({"type": "notice", "level": "error", "text": error_text})
    return TranscriptRecord(
        key,
        "agent" if role == "assistant" else role,
        timestamp,
        tuple(parts),
        tuple(results),
    )


def _claude(
    entry: SourceEntry, state: TranscriptParserState
) -> TranscriptRecord | None:
    data = entry.data
    kind = data.get("type")
    if kind == "user" and data.get("isCompactSummary") and not data.get("isSidechain"):
        return _claude_compact_notice(entry)
    if _claude_hidden(data):
        return None
    message = data.get("message")
    if not isinstance(message, dict) or message.get("role", kind) != kind:
        return None
    record = _chat(entry, "claude")
    if record is None:
        return None
    if record.role == "user":
        content = message.get("content")
        blocks = content if isinstance(content, list) else []
        if isinstance(content, str):
            blocks = [{"type": "text", "text": content}]
        text = "\n".join(
            part["text"]
            for part in blocks
            if isinstance(part, dict)
            and part.get("type") == "text"
            and isinstance(part.get("text"), str)
        )
        local_command = _claude_local_command(record, text, state)
        if local_command is not record:
            return local_command
    parts = _claude_parts(record)
    if record.role == "user" and not parts and not record.results:
        return None
    return replace(record, parts=parts)


def _claude_parts(record: TranscriptRecord) -> tuple[dict[str, Any], ...]:
    """A record's parts with ANSI styling and hidden user text removed."""
    parts = []
    for part in record.parts:
        text = part.get("text")
        if isinstance(text, str):
            text = _ANSI_SGR_RE.sub("", text).strip()
            if record.role == "user":
                text = _claude_user_text(text)
                if text is None:
                    continue
            part = {**part, "text": text}
        parts.append(part)
    return tuple(parts)


def _claude_hidden(data: dict[str, Any]) -> bool:
    """Whether an entry is the agent's context rather than a conversation turn."""
    return (
        data.get("type") not in _CHAT_ROLES
        or any(data.get(flag) for flag in ("isSidechain", "isCompactSummary"))
        or bool(data.get("isMeta") and not _is_claude_peer_message(data))
    )


def _claude_user_text(text: str) -> str | None:
    """User text as a topic shows it, or None when nothing of it is shown."""
    text = _render_claude_peer_messages(text)
    if _CLAUDE_SYSTEM_TAG_RE.search(text):
        return None
    return _CLAUDE_PEER_NOTICE_RE.sub("", text).strip() or None


def _is_claude_peer_message(data: dict[str, Any]) -> bool:
    """Whether a meta entry is a message relayed from another Claude session.

    Claude Code records those as ``isMeta`` user entries; every other meta entry
    is context for the model, but a peer's message belongs in the topic.
    """
    origin = data.get("origin")
    if isinstance(origin, dict) and origin.get("kind") == "peer":
        return True
    message = data.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return isinstance(content, str) and (
        content.lstrip().startswith(_CLAUDE_PEER_HEADER)
        or "<cross-session-message" in content
    )


def _render_claude_peer_messages(text: str) -> str:
    """Replace each wrapped peer message with ``📨 <sender>:`` and its body."""

    def render(match: re.Match[str]) -> str:
        name = _CLAUDE_PEER_NAME_RE.search(match.group(1))
        sender = name.group(1) if name and name.group(1) else "Peer session"
        return f"📨 {sender}:\n{match.group(2)}"

    return _CLAUDE_PEER_MESSAGE_RE.sub(render, text)


def _claude_compact_notice(entry: SourceEntry) -> TranscriptRecord:
    """Announce a compaction in place of the summary Claude writes for itself.

    Claude Code records the compacted history as a ``user`` entry carrying the
    whole summary (20k characters in the observed case) and flags it
    ``isCompactSummary``: context handed back to the agent, not something the
    user said.
    """
    data = entry.data
    timestamp = data.get("timestamp")
    return TranscriptRecord(
        str(data.get("uuid") or f"o{entry.offset}"),
        "agent",
        timestamp if isinstance(timestamp, str) else None,
        ({"type": "notice", "level": "info", "text": COMPACT_NOTICE},),
    )


def _claude_local_command(
    record: TranscriptRecord, text: str, state: TranscriptParserState
) -> TranscriptRecord | None:
    command = _CLAUDE_COMMAND_NAME_RE.search(text)
    stdout = _CLAUDE_LOCAL_STDOUT_RE.search(text)
    if command is not None:
        state.claude_local_command_name = command.group(1).strip()
    if stdout is not None:
        command_name = state.claude_local_command_name or ""
        state.claude_local_command_name = None
        return replace(
            record,
            role="agent",
            parts=(
                {
                    "type": "notice",
                    "level": "info",
                    "text": stdout.group(1).strip(),
                    "source": "local_command",
                    "command": command_name,
                },
            ),
            results=(),
        )
    if command is not None:
        return None
    return record


def _pi(entry: SourceEntry) -> TranscriptRecord | None:
    data = entry.data
    if data.get("type") not in ("message", "bashExecution"):
        return None
    message = data.get("message")
    message = message if isinstance(message, dict) else None
    execution = (
        message
        if message is not None and message.get("role") == "bashExecution"
        else data
        if data.get("type") == "bashExecution"
        else None
    )
    if execution is None:
        return _chat(entry, "pi")
    if execution.get("excludeFromContext") or data.get("excludeFromContext"):
        return None

    command = execution.get("command")
    output = execution.get("output")
    command = command if isinstance(command, str) else ""
    output = output if isinstance(output, str) else ""
    exit_code = execution.get("exitCode")
    cancelled = bool(execution.get("cancelled"))
    lines = []
    if command:
        lines.append(f"$ {command}")
    if output:
        lines.append(output)
    if cancelled:
        lines.append("(cancelled)")
    elif isinstance(exit_code, int) and exit_code != 0:
        lines.append(f"exit code {exit_code}")
    if not lines:
        return None

    timestamp = data.get("timestamp") or execution.get("timestamp")
    return TranscriptRecord(
        str(data.get("id") or execution.get("id") or f"o{entry.offset}"),
        "agent",
        timestamp if isinstance(timestamp, str) else None,
        (
            {
                "type": "notice",
                "level": (
                    "error"
                    if not cancelled and isinstance(exit_code, int) and exit_code != 0
                    else "info"
                ),
                "text": "\n".join(lines),
            },
        ),
    )


def _antigravity_role(data: dict[str, Any], kind: str) -> str | None:
    role_map = {
        "user": "user",
        "user_input": "user",
        "user_explicit": "user",
        "user_implicit": "user",
        "planner_response": "assistant",
        "model": "assistant",
        "assistant": "assistant",
        "info": "assistant",
        "error": "assistant",
    }
    expected = role_map.get(kind.lower())
    if expected is None or data.get("role", expected) != expected:
        return None
    return expected


def _antigravity_clean_user_parts(parts: list[dict[str, Any]]) -> None:
    for part in parts:
        text = part.get("text")
        if isinstance(text, str):
            text = _ANTIGRAVITY_METADATA_RE.sub("", text)
            part["text"] = _ANTIGRAVITY_USER_REQUEST_RE.sub("", text).strip()


def _antigravity_tool_parts(data: dict[str, Any]) -> list[dict[str, Any]]:
    calls = data.get("tool_calls")
    if not isinstance(calls, list):
        return []
    parts = []
    for call in calls:
        if not isinstance(call, dict):
            continue
        call_id = str(
            call.get("id") or call.get("tool_call_id") or call.get("name") or ""
        )
        parts.append(
            _tool(call_id, str(call.get("name") or "unknown"), call.get("input", {}))
        )
    return parts


def _antigravity(entry: SourceEntry) -> TranscriptRecord | None:
    data = entry.data
    kind = str(data.get("type", "")).upper()
    role = _antigravity_role(data, kind)
    timestamp = data.get("created_at") or data.get("timestamp")
    if not isinstance(timestamp, str):
        timestamp = None
    key = str(data.get("id") or data.get("uuid") or f"o{entry.offset}")
    parts, results = _content(data.get("content", ""))
    if role == "user":
        _antigravity_clean_user_parts(parts)
    parts.extend(_antigravity_tool_parts(data))
    if kind in {"RUN_COMMAND", "TOOL_RESULT", "EXECUTE_RESULT", "RESULT"}:
        call_id = str(
            data.get("tool_call_id") or data.get("id") or data.get("tool_name") or ""
        )
        if data.get("content") is not None:
            results.append((call_id, data.get("content"), bool(data.get("is_error"))))
        role = "agent"
        parts = []
    if role is None:
        return None
    if not parts and not results:
        return None
    return TranscriptRecord(
        key,
        "agent" if role == "assistant" else role,
        timestamp,
        tuple(parts),
        tuple(results),
    )


def _track_tool_state(record: TranscriptRecord, state: TranscriptParserState) -> None:
    for part in record.parts:
        call_id = part.get("tool_call_id")
        name = part.get("name")
        summary = part.get("title")
        if isinstance(call_id, str) and call_id and isinstance(name, str):
            state.tool_names[call_id] = name
            if isinstance(summary, str):
                state.tool_summaries[call_id] = summary
            diff = part.get("diff")
            if isinstance(diff, dict):
                state.tool_diffs[call_id] = diff
            state.pending_tools[call_id] = name
    for call_id, _, _ in record.results:
        if call_id:
            state.pending_tools.pop(call_id, None)


def _coalesce_codex_completion_mirror(
    records: list[tuple[SourceEntry, TranscriptRecord]],
    entry: SourceEntry,
    record: TranscriptRecord,
    previous: _CodexState,
) -> bool:
    """Merge metadata-only completion mirrors without emitting duplicate text."""
    if (
        previous.stream_key is not None
        or previous.last_phase == "final_answer"
        or record.phase != "final_answer"
        or record.key != previous.last_key
        or _record_text(record) != previous.last_text
    ):
        return False
    if records:
        _, prior_record = records[-1]
        if (
            prior_record.complete
            and prior_record.key == record.key
            and _record_text(prior_record) == _record_text(record)
        ):
            records[-1] = (entry, record)
            return True
    return True


def _record_text(record: TranscriptRecord) -> str:
    return "\n".join(
        str(part["text"]) for part in record.parts if part.get("type") == "text"
    )


def parse_records(
    entries: list[SourceEntry],
    provider: str,
    state: TranscriptParserState | None = None,
    *,
    deduplicate_updates: bool = False,
) -> tuple[tuple[tuple[SourceEntry, TranscriptRecord], ...], TranscriptParserState]:
    """Interpret provider records once for both protocol history and live adapters.

    The returned source entry keeps the byte/index checkpoint beside each
    normalized record. Live consumers may pass state across polls to suppress
    repeated Gemini snapshot records and to keep Codex stream identity stable.
    """
    parser_state = state or TranscriptParserState()
    records: list[tuple[SourceEntry, TranscriptRecord]] = []
    for entry in entries:
        previous_codex = replace(parser_state.codex) if provider == "codex" else None
        record = (
            _codex_event(entry, parser_state.codex)
            if provider == "codex" and entry.data.get("type") == "event_msg"
            else _codex(entry, parser_state.codex)
            if provider == "codex"
            else _gemini(entry)
            if provider == "gemini"
            else _antigravity(entry)
            if provider == "antigravity"
            else _claude(entry, parser_state)
            if provider == "claude"
            else _pi(entry)
            if provider == "pi"
            else None
        )
        if record is None or record.role not in {"user", "agent"}:
            continue
        if deduplicate_updates and provider == "gemini":
            record = _deduplicate_gemini_record(record, entry, parser_state)
            if record is None:
                continue
        if (
            deduplicate_updates
            and provider == "codex"
            and previous_codex is not None
            and _coalesce_codex_completion_mirror(
                records, entry, record, previous_codex
            )
        ):
            continue
        _track_tool_state(record, parser_state)
        records.append((entry, record))
    return tuple(records), parser_state


def _deduplicate_gemini_record(
    record: TranscriptRecord, entry: SourceEntry, state: TranscriptParserState
) -> TranscriptRecord | None:
    parts = _gemini_unique_parts(record, entry, state)
    results = _gemini_unique_results(record, state)
    if not parts and not results:
        return None
    return replace(record, parts=parts, results=results)


def _gemini_unique_parts(
    record: TranscriptRecord, entry: SourceEntry, state: TranscriptParserState
) -> tuple[dict[str, Any], ...]:
    message_id = entry.data.get("id")
    key = message_id if isinstance(message_id, str) else ""
    parts = list(record.parts)
    if key and key in state.gemini_message_ids:
        parts = [part for part in parts if part.get("type") != "text"]
    elif key:
        state.gemini_message_ids.add(key)
    visible_tool_calls = []
    for part in parts:
        call_id = part.get("tool_call_id")
        if not isinstance(call_id, str) or not call_id:
            visible_tool_calls.append(part)
        elif call_id not in state.gemini_tool_call_ids:
            state.gemini_tool_call_ids.add(call_id)
            visible_tool_calls.append(part)
    if key:
        visible_tool_calls.extend(_gemini_tool_calls_with_results(entry, state))
    return tuple(visible_tool_calls)


def _gemini_tool_calls_with_results(
    entry: SourceEntry, state: TranscriptParserState
) -> list[dict[str, Any]]:
    calls = entry.data.get("toolCalls")
    if not isinstance(calls, list):
        return []
    parts = []
    for call in calls:
        if not isinstance(call, dict):
            continue
        call_id = call.get("id")
        if not isinstance(call_id, str) or not call_id:
            continue
        if _gemini_output(call) is None or call_id in state.gemini_tool_call_ids:
            continue
        state.gemini_tool_call_ids.add(call_id)
        parts.append(
            _tool(
                call_id,
                str(call.get("name", call.get("displayName", "unknown"))),
                call.get("args", {}),
            )
        )
    return parts


def _gemini_unique_results(
    record: TranscriptRecord, state: TranscriptParserState
) -> tuple[tuple[str, Any, bool], ...]:
    results = []
    for call_id, output, failed in record.results:
        signature = (
            call_id,
            json.dumps(output, ensure_ascii=False, sort_keys=True, default=str),
            failed,
        )
        if signature in state.gemini_result_signatures:
            continue
        state.gemini_result_signatures.add(signature)
        results.append((call_id, output, failed))
    return tuple(results)


def build_messages(
    entries: list[SourceEntry],
    conversation_id: str,
    provider: str,
    generation: int = 0,
    *,
    source_range: tuple[int, int | None] | None = None,
) -> tuple[Message, ...]:
    """Build full replacements, joining results before a caller slices pages.

    Repeated native IDs (Gemini updates) increment revision once per source
    record; each distinct result line contributes once to its owner message.
    """
    builder = MessageBuilder(conversation_id, provider, generation)
    builder.add(entries)
    return builder.messages(source_range)


class MessageBuilder:
    """The messages of one transcript generation, extended as records arrive.

    Adding entries in several batches gives the messages one batch of all of
    them would: a record replaces its message, a tool result joins the latest
    call with its id wherever the two appear, and a revision counts the source
    records that contributed. Only messages a batch touched are rebuilt.
    """

    def __init__(self, conversation_id: str, provider: str, generation: int = 0):
        self.conversation_id = conversation_id
        self.provider = provider
        self.generation = generation
        self._state = TranscriptParserState()
        # A message as its latest record left it, before tool results join.
        self._bases: dict[str, Message] = {}
        self._record_offsets: dict[str, set[int]] = {}
        self._messages: dict[str, Message] = {}
        self._contributors: dict[str, frozenset[int]] = {}
        self._calls: dict[str, tuple[str, int]] = {}
        self._owned: dict[str, set[str]] = {}
        # Per call id: the offsets of its result lines, and the last one's output.
        self._results: dict[str, tuple[set[int], str, bool]] = {}

    def add(self, entries: list[SourceEntry]) -> None:
        records, _ = parse_records(entries, self.provider, self._state)
        dirty: dict[str, None] = {}
        prefix = f"g{self.generation}/" if self.generation else ""
        for entry, record in records:
            for call_id, output, failed in record.results:
                offsets = self._results.get(call_id, (set(), "", False))[0]
                offsets.add(entry.offset)
                self._results[call_id] = offsets, _text(output), failed
                owner = self._calls.get(call_id)
                if owner is not None:
                    dirty[owner[0]] = None
            if not record.parts:
                continue
            message_id = f"{self.conversation_id}/{prefix}{record.key}"
            self._record_offsets.setdefault(message_id, set()).add(entry.offset)
            parts = tuple(
                Part(
                    id=f"{message_id}#{i}",
                    **{k: v for k, v in part.items() if k in _PART_FIELDS},
                )
                for i, part in enumerate(record.parts)
            )
            self._bases[message_id] = Message(
                message_id,
                0,
                self.conversation_id,
                record.role,
                record.time,
                record.complete,
                parts,
                phase=record.phase,
            )
            for i, part in enumerate(parts):
                if part.tool_call_id:
                    previous = self._calls.get(part.tool_call_id)
                    if previous is not None:
                        self._owned[previous[0]].discard(part.tool_call_id)
                        dirty[previous[0]] = None
                    self._calls[part.tool_call_id] = message_id, i
                    self._owned.setdefault(message_id, set()).add(part.tool_call_id)
            self._messages.setdefault(message_id, self._bases[message_id])
            dirty[message_id] = None
        for message_id in dirty:
            self._rebuild(message_id)

    def _rebuild(self, message_id: str) -> None:
        base = self._bases[message_id]
        contributors = set(self._record_offsets[message_id])
        parts = list(base.parts)
        # Results join in source order of their last line, so where two calls
        # claim one part the later result shows, as in one pass over all lines.
        joined = sorted(
            (max(self._results[call_id][0]), call_id)
            for call_id in self._owned.get(message_id, ())
            if call_id in self._results
        )
        for _, call_id in joined:
            offsets, text, failed = self._results[call_id]
            index = self._calls[call_id][1]
            if index >= len(parts):
                continue
            parts[index] = replace(
                parts[index],
                status="failed" if failed else "completed",
                output={"text": text, "truncated": False},
            )
            contributors |= offsets
        self._messages[message_id] = replace(
            base, revision=len(contributors), parts=tuple(parts)
        )
        self._contributors[message_id] = frozenset(contributors)

    def messages(
        self, source_range: tuple[int, int | None] | None = None
    ) -> tuple[Message, ...]:
        if source_range is None:
            return tuple(self._messages.values())
        start, end = source_range
        return tuple(
            message
            for message in self._messages.values()
            if any(
                offset >= start and (end is None or offset < end)
                for offset in self._contributors[message.id]
            )
        )


_FEED_SLICE_BYTES = 4 << 20


class TranscriptFeed:
    """A transcript's messages, fed the bytes its observations return.

    Same generation means the bytes already fed are unchanged, so each feed
    parses only what follows them. An unfinished last line waits in
    ``_pending`` until a later feed completes it. Gemini keeps one JSON
    document rather than lines, so it is parsed whole on every change.
    """

    def __init__(self, conversation_id: str, provider: str, generation: int):
        self.builder = MessageBuilder(conversation_id, provider, generation)
        self._position = 0
        self._pending = b""

    @property
    def seen(self) -> int:
        """The source bytes fed so far."""
        return self._position + len(self._pending)

    def feed(self, start: int, content: bytes) -> bool:
        """Take ``content``, the source from byte ``start``; False if bytes are missing.

        Bytes already fed are skipped; a ``start`` past them leaves a gap only
        a whole read can fill.
        """
        if start > self.seen:
            return False
        new = content[self.seen - start :]
        if not new:
            return True
        if self.builder.provider == "gemini":
            if start != 0:
                return False
            self.builder = MessageBuilder(
                self.builder.conversation_id, "gemini", self.builder.generation
            )
            self.builder.add(read_entries(content, "gemini"))
            self._position, self._pending = len(content), b""
            return True
        data = self._pending + new if self._pending else new
        done = 0
        # Slices bound the decoded entries held at once, whatever the size fed.
        while done < len(data):
            cut = data.rfind(b"\n", done, done + _FEED_SLICE_BYTES) + 1
            if cut <= done:
                cut = data.find(b"\n", done + _FEED_SLICE_BYTES) + 1
                if cut <= 0:
                    break
            entries, end = read_complete_entries(data[done:cut], self._position + done)
            self.builder.add(entries)
            if end != self._position + cut:
                # A line that is not a JSONL line stops parsing, as in one pass.
                done = end - self._position
                break
            done = cut
        self._pending = data[done:]
        self._position += done
        return True
