"""JSONL session resolution — window-to-session lookup and message history.

Resolves tmux windows to Claude Code session files on disk, reading JSONL
transcripts to extract session summaries and message history.

Key class: SessionResolver (singleton instantiated as ``session_resolver``).
Key type: ClaudeSession.

Responsibilities:
  - Build JSONL file paths from session_id + cwd.
  - Resolve window_id → ClaudeSession by reading the JSONL on disk.
  - Find users bound to a given session_id (cross-reference with thread_router).
  - Read paginated message history from a session's JSONL file.
"""

from __future__ import annotations

import asyncio
import structlog
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .config import config
from .providers import get_provider_for_window
from .session_protocol.messages import parse_records, read_entries
from .session_protocol.models import (
    HistoryPage,
    Message,
    ProtocolError,
    SessionSnapshot,
)
from .thread_router import thread_router
from .window_state_ports import identity_state
from .window_state_store import window_store

logger = structlog.get_logger()


class _SessionHistoryService(Protocol):
    async def for_target(self, target_id: str) -> tuple[Any, SessionSnapshot]: ...

    async def history(
        self,
        session_id: str,
        cursor: str | None,
        limit: int,
        *,
        conversation: str | None = None,
    ) -> HistoryPage: ...

    async def history_by_source_range(
        self, session_id: str, start_byte: int, end_byte: int | None = None
    ) -> HistoryPage: ...


@dataclass
class ClaudeSession:
    """Information about a Claude Code session."""

    session_id: str
    summary: str
    message_count: int
    file_path: str


class SessionResolver:
    """Resolves tmux windows to Claude session files and reads message history."""

    def __init__(self) -> None:
        self._session_service: _SessionHistoryService | None = None

    def bind_session_service(self, service: _SessionHistoryService | None) -> None:
        """Bind live session queries, or clear the binding during shutdown."""
        self._session_service = service

    def _build_session_file_path(self, session_id: str, cwd: str) -> Path | None:
        """Build the direct file path for a session from session_id and cwd."""
        if not session_id or not cwd:
            return None
        encoded_cwd = cwd.replace("/", "-")
        return config.claude_projects_path / encoded_cwd / f"{session_id}.jsonl"

    def _session_from_transcript_path(
        self,
        window_id: str,
        identity: identity_state.IdentityProjection,
    ) -> ClaudeSession | None:
        """Build a lightweight session object from persisted transcript_path."""
        transcript = identity.transcript_path
        if not transcript:
            return None
        if not transcript.exists():
            return None
        summary = (
            identity.window_name
            or thread_router.get_display_name(window_id)
            or "Untitled"
        )
        return ClaudeSession(
            session_id=identity.session_id,
            summary=summary,
            message_count=-1,
            file_path=str(transcript),
        )

    def _resolve_session_file(
        self, session_id: str, cwd: str, window_id: str
    ) -> Path | None:
        """Return the JSONL path for a session, using glob fallback if needed.

        When the direct path is missing, searches via glob. If found and the
        decoded cwd is an existing directory, updates the window's stored cwd.
        """
        file_path = self._build_session_file_path(session_id, cwd)
        if file_path and file_path.exists():
            return file_path

        pattern = f"*/{session_id}.jsonl"
        matches = list(config.claude_projects_path.glob(pattern))
        if not matches:
            return None

        file_path = matches[0]
        logger.debug("Found session via glob: %s", file_path)
        encoded_dir = file_path.parent.name
        decoded_cwd = encoded_dir.replace("-", "/")
        if window_id and decoded_cwd.startswith("/") and Path(decoded_cwd).is_dir():
            current_cwd = identity_state.get_cwd(window_id)
            if current_cwd and current_cwd != decoded_cwd:
                logger.debug(
                    "Glob fallback: updating cwd for window %s: %r -> %r",
                    window_id,
                    current_cwd,
                    decoded_cwd,
                )
                window_store.update_cwd(window_id, decoded_cwd)
        return file_path

    async def _read_session_summary(
        self, file_path: Path, session_id: str, window_id: str
    ) -> ClaudeSession | None:
        """Read a JSONL session file and extract summary and message count."""
        summary = ""
        last_user_msg = ""
        provider = get_provider_for_window(
            window_id, provider_name=identity_state.get_provider_name(window_id)
        )
        try:
            entries = await asyncio.to_thread(
                read_entries, file_path, provider.capabilities.name
            )
        except OSError:
            return None

        for entry in entries:
            if entry.data.get("type") == "summary":
                value = entry.data.get("summary")
                if isinstance(value, str) and value:
                    summary = value
        records, _ = parse_records(entries, provider.capabilities.name)
        for _, record in records:
            if record.role == "user":
                text = "\n".join(
                    part["text"]
                    for part in record.parts
                    if isinstance(part.get("text"), str)
                ).strip()
                if text:
                    last_user_msg = text

        if not summary:
            summary = last_user_msg[:50] if last_user_msg else "Untitled"

        return ClaudeSession(
            session_id=session_id,
            summary=summary,
            message_count=len(entries),
            file_path=str(file_path),
        )

    async def _get_session_direct(
        self, session_id: str, cwd: str, window_id: str = ""
    ) -> ClaudeSession | None:
        """Get a ClaudeSession directly from session_id and cwd (no scanning)."""
        file_path = self._resolve_session_file(session_id, cwd, window_id)
        if not file_path:
            return None
        return await self._read_session_summary(file_path, session_id, window_id)

    async def resolve_session_for_window(self, window_id: str) -> ClaudeSession | None:
        """Resolve a tmux window to the best matching Claude session.

        Uses persisted session_id + cwd to construct file path directly.
        Returns None if no session is associated with this window.
        """
        # Ensure a row exists (preserves prior get_window_state() semantics).
        window_store.get_window_state(window_id)
        identity = identity_state.get_identity(window_id)
        if identity is None or not identity.session_id or not identity.cwd:
            return None

        direct = self._session_from_transcript_path(window_id, identity)
        if direct:
            return direct

        session = await self._get_session_direct(
            identity.session_id, identity.cwd, window_id
        )
        if session:
            return session

        provider = get_provider_for_window(
            window_id, provider_name=identity.provider_name
        )
        if not provider.capabilities.supports_hook:
            logger.debug(
                "Hookless session unresolved for window_id %s "
                "(sid=%s, transcript_path=%s); keeping state",
                window_id,
                identity.session_id,
                identity.transcript_path,
            )
            return None

        logger.debug(
            "Session file no longer exists for window_id %s (sid=%s, cwd=%s)",
            window_id,
            identity.session_id,
            identity.cwd,
        )
        window_store.clear_session_fields(window_id)
        return None

    def find_users_for_session(
        self,
        session_id: str,
    ) -> list[tuple[int, str, int, int | None]]:
        """Find all users whose thread-bound window maps to the given session_id."""
        result: list[tuple[int, str, int, int | None]] = []
        # Bindings can briefly retain a superseded id while state has already
        # folded onto its canonical target.  Use the one alias-convergence
        # contract rather than attempting a second digest/locator mapping here.
        # Lazy: window_resolver imports session-state types; defer to preserve
        # the read-only query layer's cold-import boundary.
        from .window_resolver import resolve_window_alias

        for (
            user_id,
            chat_id,
            thread_id,
            window_id,
        ) in thread_router.iter_thread_bindings_with_chat():
            canonical_id = resolve_window_alias(window_id)
            if identity_state.get_session_id(canonical_id) == session_id:
                result.append((user_id, canonical_id, thread_id, chat_id))
        return result

    async def get_recent_messages(
        self,
        window_id: str,
        *,
        start_byte: int = 0,
        end_byte: int | None = None,
    ) -> tuple[list[dict], int]:
        """Read live history through the session service's bound snapshot."""
        service = self._session_service
        if service is None:
            raise ProtocolError(
                503, "backend_unavailable", "Session service is not initialized"
            )
        _, snapshot = await service.for_target(window_id)
        if start_byte or end_byte is not None:
            page = await service.history_by_source_range(
                snapshot.id, start_byte, end_byte
            )
            messages = page.messages
        else:
            pages = []
            page = await service.history(
                snapshot.id,
                None,
                200,
                conversation=snapshot.conversation.id
                if snapshot.conversation
                else None,
            )
            pages.append(page)
            while page.prev_cursor:
                page = await service.history(
                    snapshot.id,
                    page.prev_cursor,
                    200,
                    conversation=snapshot.conversation.id
                    if snapshot.conversation
                    else None,
                )
                pages.append(page)
            messages = tuple(
                message
                for older_page in reversed(pages)
                for message in older_page.messages
            )
        all_messages = _legacy_history_messages(messages)
        return all_messages, len(all_messages)


def _legacy_history_messages(messages: tuple[Message, ...]) -> list[dict[str, Any]]:
    """Keep the current Telegram history DTO over normalized protocol messages."""
    all_messages: list[dict[str, Any]] = []
    for message in messages:
        for part in message.parts:
            if part.type == "tool" and part.name == "ExitPlanMode":
                tool_input = part.input
                plan = tool_input.get("plan") if isinstance(tool_input, dict) else None
                if isinstance(plan, str) and plan.strip():
                    all_messages.append(
                        {
                            "role": "assistant",
                            "text": plan.strip(),
                            "content_type": "text",
                            "timestamp": message.time,
                        }
                    )
            text = part.text or part.title or part.name or ""
            content_type = {
                "text": "text",
                "thinking": "thinking",
                "tool": "tool_use",
            }.get(part.type, part.type)
            if text:
                all_messages.append(
                    {
                        "role": "user" if message.role == "user" else "assistant",
                        "text": text,
                        "content_type": content_type,
                        "timestamp": message.time,
                    }
                )
            if part.output:
                output = part.output
                output_text = (
                    output.get("text", "") if isinstance(output, dict) else str(output)
                )
                if output_text:
                    all_messages.append(
                        {
                            "role": "assistant",
                            "text": output_text,
                            "content_type": "tool_result",
                            "timestamp": message.time,
                        }
                    )

    return all_messages


session_resolver = SessionResolver()
