"""Integration tests for PTB Application handler dispatch.

Tests that handlers are correctly registered and PTB routes updates
to the right handler functions. Uses a real PTB Application (``dispatch_app``)
with mocked external dependencies (Bot API, TmuxManager, SessionManager).
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram import Chat
from ccgram.session_protocol.backend import CreationOptions, Prompt, Target
from ccgram.session_protocol.models import Status
from ccgram.session_protocol.service import LocalSessionService
from ccgram.session_protocol.store import ProtocolStore

pytestmark = pytest.mark.integration

TEST_USER_ID = 12345
TEST_THREAD_ID = 42


class _SessionBackend:
    def __init__(self) -> None:
        self.target = Target(
            id="@0",
            title="test-win",
            cwd="/workspace",
            provider="claude",
            conversation_id="conversation",
            transcript=None,
            pane_id="%0",
            pane_title="claude",
            rows=24,
            cols=80,
            process_id=123,
            approval_mode="normal",
            status=Status("idle", "", "native", "2026-10-20T10:00:00Z"),
            foreground_argv=("claude",),
        )
        self.texts: list[tuple[str, bool]] = []

    async def targets(self) -> list[Target]:
        return [self.target]

    async def inspect(self, target_id: str) -> Target:
        assert target_id == self.target.id
        return self.target

    async def prompt(self, target: Target) -> Prompt | None:
        return None

    async def terminal(self, target: Target, pane: str) -> dict[str, object]:
        return {"pane": pane, "rows": 24, "cols": 80, "ansi": "", "hash": ""}

    async def send_text(
        self, target: Target, text: str, *, enter: bool, raw: bool | None = None
    ) -> bool:
        self.texts.append((text, enter))
        return True

    async def send_key(self, target: Target, key: str) -> bool:
        return True

    async def send_followup(self, target: Target, text: str) -> bool:
        self.texts.append((text, True))
        return True

    async def select_pane(self, target: Target, pane_id: str) -> Target:
        return self.target

    async def split(self, target: Target) -> str | None:
        return None

    async def close(self, target: Target) -> bool:
        return True

    async def rename(self, target: Target, title: str) -> bool:
        return True

    async def commands(self, target: Target) -> list[dict[str, str]]:
        return []

    async def create(
        self, data: dict[str, Any], *, trusted_options: CreationOptions | None = None
    ) -> Target:
        return self.target

    async def resume(self, target: Target, conversation_id: str) -> bool:
        return True


@pytest.fixture
def session_backend(dispatch_app, tmp_path: Path) -> Iterator[_SessionBackend]:
    backend = _SessionBackend()
    store = ProtocolStore(tmp_path / "protocol.sqlite3")
    dispatch_app.bot_data["session_service"] = LocalSessionService(backend, store)
    yield backend
    store.close()


class TestTextRouting:
    @pytest.mark.parametrize(
        "text",
        ["hello world", "!ls -la"],
        ids=["plain", "bang-prefix"],
    )
    async def test_text_reaches_text_handler_verbatim(
        self, dispatch_app, make_text_update, text: str
    ) -> None:
        update = make_text_update(text, bot=dispatch_app.bot)

        with (
            patch(
                "ccgram.handlers.text.text_handler.handle_text_message",
                new_callable=AsyncMock,
            ) as mock_handler,
            patch(
                "ccgram.handlers.text.text_handler.config.is_user_allowed",
                return_value=True,
            ),
        ):
            await dispatch_app.process_update(update)

        mock_handler.assert_awaited_once()
        forwarded = mock_handler.call_args[0][0].message
        assert forwarded.text == text
        assert forwarded.message_thread_id == TEST_THREAD_ID

    async def test_unauthorized_user_rejected(
        self, dispatch_app, make_text_update
    ) -> None:
        update = make_text_update("hello", bot=dispatch_app.bot, user_id=99999)

        with (
            patch(
                "ccgram.handlers.text.text_handler.handle_text_message",
                new_callable=AsyncMock,
            ) as mock_handler,
            patch(
                "ccgram.handlers.text.text_handler.config.is_user_allowed",
                return_value=False,
            ),
            patch(
                "ccgram.handlers.text.text_handler.safe_reply", new_callable=AsyncMock
            ),
        ):
            await dispatch_app.process_update(update)

        mock_handler.assert_not_awaited()


class TestCommandRouting:
    async def test_start_command_dispatched(
        self, dispatch_app, make_text_update
    ) -> None:
        update = make_text_update("/start", bot=dispatch_app.bot)

        with (
            patch(
                "ccgram.handlers.topics.new_command.safe_reply", new_callable=AsyncMock
            ) as mock_reply,
            patch(
                "ccgram.handlers.topics.new_command.config.is_user_allowed",
                return_value=True,
            ),
        ):
            await dispatch_app.process_update(update)

        mock_reply.assert_awaited_once()

    async def test_registered_command_wins_over_text_handler(
        self, dispatch_app, make_text_update
    ) -> None:
        """/history goes to its CommandHandler, never to the TEXT fallback."""
        update = make_text_update("/history", bot=dispatch_app.bot)

        with (
            patch(
                "ccgram.handlers.recovery.history.config.is_user_allowed",
                return_value=True,
            ),
            patch(
                "ccgram.handlers.text.text_handler.handle_text_message",
                new_callable=AsyncMock,
            ) as mock_text,
            patch(
                "ccgram.handlers.recovery.history.thread_router.resolve_window_for_thread",
                return_value=None,
            ),
            patch(
                "ccgram.handlers.recovery.history.safe_reply", new_callable=AsyncMock
            ) as mock_reply,
        ):
            await dispatch_app.process_update(update)

        mock_reply.assert_awaited_once()
        mock_text.assert_not_awaited()

    @pytest.mark.parametrize(
        "command",
        ["/new", "/sometool"],
        ids=["provider-command", "unknown-command"],
    )
    async def test_unregistered_command_forwarded_to_window(
        self,
        dispatch_app,
        make_text_update,
        session_backend: _SessionBackend,
        command: str,
    ) -> None:
        update = make_text_update(command, bot=dispatch_app.bot)

        with (
            patch(
                "ccgram.handlers.commands.forward.config.is_user_allowed",
                return_value=True,
            ),
            patch(
                "ccgram.handlers.commands.forward.thread_router.resolve_window_for_thread",
                return_value="@0",
            ),
            patch(
                "ccgram.multiplexer.tmux.tmux_manager.find_window_by_id",
                new_callable=AsyncMock,
                return_value=MagicMock(window_id="@0"),
            ),
            patch(
                "ccgram.handlers.commands.forward.thread_router.get_display_name",
                return_value="test-win",
            ),
            patch(
                "ccgram.handlers.commands.forward.safe_reply", new_callable=AsyncMock
            ),
            patch.object(Chat, "send_action", new_callable=AsyncMock),
        ):
            await dispatch_app.process_update(update)

        assert session_backend.texts == [(command, True)]
