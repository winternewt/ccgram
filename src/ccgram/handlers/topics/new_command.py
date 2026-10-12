"""``/start`` handler — welcome message, or a new session in a fresh topic.

In a named topic with no session bound, ``/start`` opens the same window
picker / directory browser a first text message would; elsewhere it shows
the welcome and the command panel.

Authorization is checked first. On unauthorized access an explanatory
reply is sent. Any in-progress directory-browser, worktree-picker, and
pending-thread state on the user is cleared (the full abort reset, same
as cancel) so a stranded re-entrancy guard or stale picker does not
interfere with the next interaction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from telegram import Message, Update
import functools

from ...config import config
from ...thread_router import thread_router
from ..messaging_pipeline.message_sender import safe_reply
from ..callback_helpers import get_thread_id
from ..commands import commands_command
from ..user_state import PENDING_THREAD_ID, PENDING_THREAD_TEXT
from .directory_browser import (
    clear_browse_state,
    clear_window_picker_state,
    clear_workspace_state,
    clear_worktree_state,
)

if TYPE_CHECKING:
    from telegram.ext import ContextTypes


async def new_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not config.is_user_allowed(user.id):
        if update.message:
            await safe_reply(update.message, "You are not authorized to use this bot.")
        return

    clear_browse_state(context.user_data)
    clear_window_picker_state(context.user_data)
    clear_worktree_state(context.user_data)
    clear_workspace_state(context.user_data)
    if context.user_data is not None:
        context.user_data.pop(PENDING_THREAD_ID, None)
        context.user_data.pop(PENDING_THREAD_TEXT, None)

    message = update.message
    thread_id = get_thread_id(update)
    if message and thread_id is not None and not _is_bound(user.id, thread_id, message):
        # Lazy: handlers.text imports this package (directory_browser,
        # worktree); a module-level import would close the cycle.
        from ..text.text_handler import start_session_flow

        if message.chat.type in ("group", "supergroup"):
            thread_router.set_group_chat_id(user.id, thread_id, message.chat.id)
        await start_session_flow(
            user.id,
            thread_id,
            "",
            context.user_data,
            functools.partial(safe_reply, message),
        )
        return

    if update.message:
        await safe_reply(
            update.message,
            "\U0001f916 *CCGram*\n\n"
            "General is the control topic. Named topics hold agent or terminal sessions.\n"
            "Use the pinned command panel; /commands opens it again.",
        )
        await commands_command(update, context)


def _is_bound(user_id: int, thread_id: int, message: Message) -> bool:
    return (
        thread_router.get_window_for_thread(user_id, thread_id, message.chat.id)
        is not None
    )
