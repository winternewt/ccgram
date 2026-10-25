"""Small adapters for provider fixtures used with the shared transcript parser."""

from typing import Any

from ccgram.session_protocol.messages import (
    SourceEntry,
    TranscriptParserState,
    build_messages,
    parse_records,
)
from ccgram.session_protocol.models import Message


def source_entries(entries: list[dict[str, Any]]) -> list[SourceEntry]:
    return [SourceEntry(offset, entry) for offset, entry in enumerate(entries)]


def protocol_messages(
    entries: list[dict[str, Any]], provider: str
) -> tuple[Message, ...]:
    return build_messages(source_entries(entries), "test-session", provider)


def protocol_records(
    entries: list[dict[str, Any]],
    provider: str,
    state: TranscriptParserState | None = None,
):
    return parse_records(source_entries(entries), provider, state)


def message_text(message: Message) -> str:
    return "\n".join(part.text or "" for part in message.parts if part.type == "text")
