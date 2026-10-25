"""/split — add a sibling pane to the current topic's window.

Splits the bound window/tab into a new pane (the multi-pane "agent team"
shape). With an argument, the text is run in the new pane — e.g.
``/split claude`` spawns a sibling agent, ``/split npm test`` starts a watcher.
The new pane is discoverable via /panes and the existing multi-pane scanning.

Backend-neutral: rides ``multiplexer.split_window``. Tmux supports sibling
pane handles; Herdr rejects splits because a raw new-pane locator cannot satisfy
the guarded opaque-target API.
"""

from __future__ import annotations

import structlog
from typing import TYPE_CHECKING

from ..thread_router import thread_router

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

logger = structlog.get_logger()


async def split_command(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
    """Handle /split [command] — split the topic's window into a new pane."""
    # Lazy: config singleton resolved at call time so tests can swap it
    from ..config import config

    # Lazy: messaging_pipeline ↔ handler cycle through status_bubble
    from .messaging_pipeline.message_sender import safe_reply

    # Lazy: callback_helpers only used when we have a real update
    from .callback_helpers import get_thread_id

    user = update.effective_user
    if not user or not config.is_user_allowed(user.id):
        return
    if not update.message:
        return

    thread_id = get_thread_id(update)
    if thread_id is None:
        await safe_reply(update.message, "❌ Use this command inside a topic.")
        return

    window_id = thread_router.get_window_for_thread(user.id, thread_id)
    if not window_id:
        await safe_reply(update.message, "❌ This topic is not bound to any session.")
        return

    # Lazy: defer the protocol backend/provider graph until an authorized,
    # bound /split command needs a terminal action.
    from ..session_protocol.telegram import adapter_for

    adapter = adapter_for(context.bot_data)

    command = " ".join(context.args).strip() if context.args else ""
    # Herdr cannot return a durable sibling target from a raw split pane. Reject
    # before creation so `/split <command>` never claims to have run a command.
    action_id = f"telegram-message:{update.update_id}"
    success, result = await adapter.split(window_id, action_id=action_id)
    if not success:
        await safe_reply(
            update.message, f"❌ {result or 'Could not split the window.'}"
        )
        return
    new_pane = result

    if command:
        sent, detail = await adapter.send_text(
            window_id,
            command,
            pane_id=new_pane,
            submit=True,
            terminal_input=True,
            action_id=f"{action_id}:command",
        )
        if not sent:
            await safe_reply(update.message, f"❌ {detail or 'Could not run command.'}")
            return
        await safe_reply(
            update.message,
            f"✅ Split into pane `{new_pane}` and ran `{command}`. Use /panes to view.",
        )
    else:
        await safe_reply(
            update.message,
            f"✅ Split into pane `{new_pane}`. Use /panes to view.",
        )
