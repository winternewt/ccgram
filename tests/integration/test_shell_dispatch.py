"""Integration tests for PTB dispatch routing to the shell handler.

Text in a shell-bound topic must reach ``handle_text_message`` unchanged —
including a ``!``-prefixed raw command, which must not be mistaken for a
Telegram bot command — and ``sh:``-prefixed callbacks must reach the shell
callback handler only after the allowlist check passes.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from ccgram.handlers.callback_data import CB_SHELL_RUN
from ccgram.session_protocol.backend import CreationOptions, Prompt, Target
from ccgram.session_protocol.models import Status
from ccgram.session_protocol.service import LocalSessionService
from ccgram.session_protocol.store import ProtocolStore

pytestmark = pytest.mark.integration


class _SessionBackend:
    def __init__(self) -> None:
        self.target = Target(
            id="@0",
            title="test-win",
            cwd="/workspace",
            provider="shell",
            conversation_id="conversation",
            transcript=None,
            pane_id="%0",
            pane_title="bash",
            rows=24,
            cols=80,
            process_id=123,
            approval_mode="normal",
            status=Status("idle", "", "native", "2026-10-20T10:00:00Z"),
            foreground_argv=("bash",),
        )

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
        return True

    async def send_key(self, target: Target, key: str) -> bool:
        return True

    async def send_followup(self, target: Target, text: str) -> bool:
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


async def test_shell_callback_dispatches_to_shell_handler(
    dispatch_app, make_callback_update, session_backend: _SessionBackend
) -> None:
    update = make_callback_update(f"{CB_SHELL_RUN}@0", bot=dispatch_app.bot)

    with (
        patch(
            "ccgram.handlers.shell.shell_commands.handle_shell_callback",
            new_callable=AsyncMock,
        ) as mock_shell_cb,
        patch(
            "ccgram.handlers.callback_registry.config.is_user_allowed",
            return_value=True,
        ),
    ):
        await dispatch_app.process_update(update)

    mock_shell_cb.assert_awaited_once()
    assert mock_shell_cb.call_args[0][2].startswith(CB_SHELL_RUN)
    adapter = mock_shell_cb.call_args.args[5]
    assert adapter.service.backend is session_backend


async def test_shell_callback_from_unauthorized_user_is_refused(
    dispatch_app, make_callback_update
) -> None:
    """The allowlist gate lives in dispatch(), so the handler never runs."""
    update = make_callback_update(f"{CB_SHELL_RUN}@0", bot=dispatch_app.bot)

    with (
        patch(
            "ccgram.handlers.shell.shell_commands.handle_shell_callback",
            new_callable=AsyncMock,
        ) as mock_shell_cb,
        patch(
            "ccgram.handlers.callback_registry.config.is_user_allowed",
            return_value=False,
        ),
    ):
        await dispatch_app.process_update(update)

    mock_shell_cb.assert_not_awaited()


async def test_unregistered_callback_prefix_reaches_no_handler(
    dispatch_app, make_callback_update
) -> None:
    update = make_callback_update("zz:nothing:@0", bot=dispatch_app.bot)

    with (
        patch(
            "ccgram.handlers.shell.shell_commands.handle_shell_callback",
            new_callable=AsyncMock,
        ) as mock_shell_cb,
        patch(
            "ccgram.handlers.callback_registry.config.is_user_allowed",
            return_value=True,
        ),
    ):
        await dispatch_app.process_update(update)

    mock_shell_cb.assert_not_awaited()
