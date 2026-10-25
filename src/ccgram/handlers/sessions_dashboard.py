"""Sessions dashboard — /sessions command showing all bound sessions.

Displays a summary of all thread-bound sessions for the current user
with alive/dead status indicators, per-session action buttons (Esc,
Screenshot, Kill with two-step confirmation, and Live when the topic has
queued transcript messages), cwd details, and refresh/new-session actions.

Key functions:
  - sessions_command(): /sessions command handler
  - handle_sessions_refresh(): refresh button callback
  - handle_sessions_kill(): first Kill tap — show confirmation
  - handle_sessions_kill_confirm(): second tap — kill and unbind
  - handle_sessions_live(): first Live tap — show confirmation
  - handle_sessions_live_confirm(): second tap — skip the queued backlog
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING
import structlog

from telegram import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.error import TelegramError
from ..config import config
from ..session import session_manager
from ..telegram_client import PTBTelegramClient, TelegramClient
from ..thread_router import thread_router
from ..multiplexer import multiplexer as tmux_manager
from ..multiplexer.base import canonical_window_id
from ..multiplexer.reconciliation import (
    list_windows_for_reconciliation,
    window_presence,
)
from ..window_query import view_window
from ..session_protocol.telegram import TelegramSessionAdapter, adapter_for
from .callback_data import (
    CB_SESSIONS_KILL,
    CB_SESSIONS_KILL_CONFIRM,
    CB_SESSIONS_LIVE,
    CB_SESSIONS_LIVE_CONFIRM,
    CB_SESSIONS_NEW,
    CB_SESSIONS_REFRESH,
    CB_STATUS_ESC,
    CB_STATUS_SCREENSHOT,
)
from .callback_helpers import user_owns_window
from .callback_tokens import compact_callback_data, resolve_callback_data
from .callback_registry import register
from .cleanup import clear_topic_state
from .messaging_pipeline.backlog import get_backlog_snapshot
from .topics.topic_deletion import cleanup_retired_topics, retire_topic_binding
from .messaging_pipeline.message_sender import safe_edit, safe_reply, safe_send

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = structlog.get_logger()

_REFRESH_BTN = InlineKeyboardButton(
    "\U0001f504 Refresh", callback_data=CB_SESSIONS_REFRESH
)
_NEW_TOPIC_NAME = "New session"
_NEW_BTN = InlineKeyboardButton("\u2795 New Session", callback_data=CB_SESSIONS_NEW)

# Green running, black stopped, white when the multiplexer could not be asked.
_LIVENESS_MARKER = {True: "\U0001f7e2", False: "\u26ab", None: "\u26aa"}


async def _build_dashboard(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Build dashboard text and keyboard for a user's sessions."""
    bindings = thread_router.get_all_thread_windows(user_id)

    if not bindings:
        keyboard = InlineKeyboardMarkup([[_REFRESH_BTN, _NEW_BTN]])
        return (
            "No active sessions.\n\nTap New Session to start one.",
            keyboard,
        )

    # The complete listing, not the adoption-filtered one: every id here is
    # already bound, and a live window a backend merely will not auto-adopt
    # would otherwise show as stopped. None means the backend could not be
    # asked, which is not the same as every session being stopped.
    all_windows = await list_windows_for_reconciliation()
    # Folded on both sides: a binding persisted in different case from the
    # live listing is the same window, and showing it stopped also strips
    # its Esc, Screenshot and Kill controls.
    live_ids = (
        None
        if all_windows is None
        else {canonical_window_id(w.window_id) for w in all_windows}
    )

    lines: list[str] = []
    action_rows: list[list[InlineKeyboardButton]] = []
    for thread_id, window_id in sorted(bindings.items()):
        display_name = thread_router.get_display_name(window_id)
        view = view_window(window_id)
        alive = None if live_ids is None else canonical_window_id(window_id) in live_ids
        status = _LIVENESS_MARKER[alive]

        # Session line with provider + mode tags and cwd detail
        provider_tag = f" [{view.provider_name}]" if view and view.provider_name else ""
        mode_tag = " [YOLO]" if view and view.approval_mode == "yolo" else ""
        line = f"{status} {display_name}{provider_tag}{mode_tag}"
        if view and view.cwd:
            line += f"\n    {view.cwd}"
        lines.append(line)

        if alive:
            row: list[InlineKeyboardButton] = [
                InlineKeyboardButton(
                    "\u238b Esc",
                    callback_data=compact_callback_data(
                        CB_STATUS_ESC, f"{CB_STATUS_ESC}{window_id}", window_id
                    ),
                ),
                InlineKeyboardButton(
                    "\U0001f4f8",
                    callback_data=compact_callback_data(
                        CB_STATUS_SCREENSHOT,
                        f"{CB_STATUS_SCREENSHOT}{window_id}",
                        window_id,
                    ),
                ),
                InlineKeyboardButton(
                    f"\U0001f5d1 Kill {display_name}",
                    callback_data=compact_callback_data(
                        CB_SESSIONS_KILL, f"{CB_SESSIONS_KILL}{window_id}", window_id
                    ),
                ),
            ]
            queued = get_backlog_snapshot(user_id, window_id, thread_id).pending_count
            if queued:
                row.append(
                    InlineKeyboardButton(
                        f"\u23ed Live ({queued})",
                        callback_data=compact_callback_data(
                            CB_SESSIONS_LIVE,
                            f"{CB_SESSIONS_LIVE}{window_id}",
                            window_id,
                        ),
                    )
                )
            action_rows.append(row)

    text = "Sessions\n\n" + "\n".join(lines)
    rows = action_rows + [[_REFRESH_BTN, _NEW_BTN]]
    return text, InlineKeyboardMarkup(rows)


async def sessions_command(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /sessions — show dashboard of all bound sessions."""
    user = update.effective_user
    if not user or not update.message:
        return

    if not config.is_user_allowed(user.id):
        await safe_reply(update.message, "You are not authorized to use this bot.")
        return

    text, keyboard = await _build_dashboard(user.id)
    await safe_reply(update.message, text, reply_markup=keyboard)


async def handle_sessions_refresh(query: CallbackQuery, user_id: int) -> None:
    """Handle refresh button — re-render the dashboard in-place."""
    text, keyboard = await _build_dashboard(user_id)
    await safe_edit(query, text, reply_markup=keyboard)


async def handle_sessions_kill(
    query: CallbackQuery, _user_id: int, window_id: str
) -> None:
    """First Kill tap — show confirmation prompt."""
    display = thread_router.get_display_name(window_id)
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"\u26a0 Confirm kill {display}",
                    callback_data=compact_callback_data(
                        CB_SESSIONS_KILL_CONFIRM,
                        f"{CB_SESSIONS_KILL_CONFIRM}{window_id}",
                        window_id,
                    ),
                ),
            ],
            [_REFRESH_BTN],
        ]
    )
    await safe_edit(
        query,
        f"Kill session '{display}'?\n\nThis will terminate the Claude Code process.",
        reply_markup=keyboard,
    )


async def handle_sessions_kill_confirm(
    query: CallbackQuery,
    user_id: int,
    window_id: str,
    client: TelegramClient,
    adapter: TelegramSessionAdapter,
) -> None:
    """Second tap — kill the tmux window, unbind all users, refresh dashboard."""
    display = thread_router.get_display_name(window_id)

    # find_window_by_id answers None both for a window that is gone and for a
    # backend that could not be reached, and everything below deletes the
    # routing to this session. Unknown changes nothing: the session would
    # survive the outage with no way left to reach it.
    presence = await window_presence(window_id, tmux_manager)
    if presence is None:
        await safe_edit(
            query,
            f"\u26a0 Could not reach the multiplexer, so '{display}' was left "
            "alone. Nothing was killed or unbound.",
            reply_markup=None,
        )
        return

    if presence and not (await adapter.close(window_id))[0]:
        # The window was there a moment ago and the kill did not succeed, so
        # it may still be running. Clearing the bindings now would strand it.
        await safe_edit(
            query,
            f"\u26a0 Could not kill '{display}'. Nothing was unbound.",
            reply_markup=None,
        )
        return

    for uid, chat_id, tid, bound_wid in list(
        thread_router.iter_thread_bindings_with_chat()
    ):
        if canonical_window_id(bound_wid) == canonical_window_id(window_id):

            async def clear_state_before_delete() -> None:
                await clear_topic_state(
                    uid,
                    tid,
                    client,
                    window_id=bound_wid,
                    chat_id=chat_id,
                )

            await retire_topic_binding(
                client,
                uid,
                tid,
                bound_wid,
                router=thread_router,
                chat_id=chat_id,
                before_delete=clear_state_before_delete,
            )

    session_manager.flush_state()
    await cleanup_retired_topics(client, router=thread_router)

    logger.info(
        "sessions_kill_confirm: killed window %s (%s), user=%d",
        window_id,
        display,
        user_id,
    )

    # Re-render dashboard
    text, keyboard = await _build_dashboard(user_id)
    headline = "\U0001f5d1 Killed" if presence else "\U0001f5d1 Was already gone:"
    await safe_edit(query, f"{headline} '{display}'\n\n{text}", reply_markup=keyboard)


async def handle_sessions_live(
    query: CallbackQuery, _user_id: int, window_id: str
) -> None:
    """First Live tap — show confirmation prompt."""
    display = thread_router.get_display_name(window_id)
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"\u23ed Confirm jump to live: {display}",
                    callback_data=compact_callback_data(
                        CB_SESSIONS_LIVE_CONFIRM,
                        f"{CB_SESSIONS_LIVE_CONFIRM}{window_id}",
                        window_id,
                    ),
                ),
            ],
            [_REFRESH_BTN],
        ]
    )
    await safe_edit(
        query,
        f"Jump '{display}' to live?\n\nQueued transcript messages for this "
        "topic are dropped and never sent.",
        reply_markup=keyboard,
    )


async def handle_sessions_live_confirm(
    query: CallbackQuery, user_id: int, window_id: str
) -> str:
    """Second tap — skip the topic's queued backlog; return the toast text."""
    # Lazy: session_monitor reaches handler routing through bootstrap callbacks.
    from ..session_monitor import get_active_monitor

    display = thread_router.get_display_name(window_id)
    binding = next(
        (
            (tid, chat_id)
            for uid, chat_id, tid, bound_wid in thread_router.iter_thread_bindings_with_chat()
            if uid == user_id
            and canonical_window_id(bound_wid) == canonical_window_id(window_id)
        ),
        None,
    )
    monitor = get_active_monitor()
    if binding is None or monitor is None:
        return "Session unavailable"
    thread_id, bound_chat_id = binding
    chat_id = bound_chat_id or thread_router.resolve_chat_id(user_id, thread_id)
    intent = await monitor.request_backlog_skip(user_id, window_id, thread_id, chat_id)
    text, keyboard = await _build_dashboard(user_id)
    if intent is None:
        await safe_edit(query, text, reply_markup=keyboard)
        return "Backlog changed; try again"
    logger.info(
        "sessions_live_confirm: skipped backlog",
        window_id=window_id,
        skipped=intent.skipped_count,
        user_id=user_id,
    )
    await safe_edit(
        query, f"\u23ed Jumped '{display}' to live\n\n{text}", reply_markup=keyboard
    )
    return f"Jumping to live ({intent.skipped_count} queued item(s))"


async def handle_sessions_new(
    query: CallbackQuery,
    user_id: int,
    user_data: dict | None,
    client: TelegramClient,
) -> None:
    """Create a topic and start the session flow inside it.

    The directory browser (or window picker) is posted into the new topic,
    so its callbacks arrive from the thread the flow is pending on.
    """
    chat = query.message.chat if query.message else None
    if chat is None:
        await query.answer("Cannot create a topic here", show_alert=True)
        return
    try:
        topic = await client.create_forum_topic(chat_id=chat.id, name=_NEW_TOPIC_NAME)
    except TelegramError as exc:
        logger.warning("sessions_new: topic creation failed: %s", exc)
        await query.answer(f"Could not create a topic: {exc}", show_alert=True)
        return
    thread_id = topic.message_thread_id
    if chat.type in ("group", "supergroup"):
        thread_router.set_group_chat_id(user_id, thread_id, chat.id)
    await query.answer("New topic created")
    # Lazy: handlers.text imports handlers.topics, which this module imports.
    from .text.text_handler import start_session_flow

    await start_session_flow(
        user_id,
        thread_id,
        "",
        user_data,
        functools.partial(safe_send, client, chat.id, message_thread_id=thread_id),
    )


@register(
    CB_SESSIONS_REFRESH,
    CB_SESSIONS_NEW,
    CB_SESSIONS_KILL_CONFIRM,
    CB_SESSIONS_KILL,
    CB_SESSIONS_LIVE_CONFIRM,
    CB_SESSIONS_LIVE,
)
async def _dispatch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    user = update.effective_user
    if not user:
        return

    data = resolve_callback_data(query.data, user.id, user_owns_window)
    if data is None:
        await query.answer("This button has expired", show_alert=True)
        return

    if data == CB_SESSIONS_REFRESH:
        await handle_sessions_refresh(query, user.id)
        await query.answer("Refreshed")
    elif data == CB_SESSIONS_NEW:
        await handle_sessions_new(
            query, user.id, context.user_data, PTBTelegramClient(context.bot)
        )
    else:
        await _dispatch_window_action(query, user.id, data, context)


# Confirm prefixes first: "sess:kill:" is not a prefix of "sess:killok:", but
# keeping the longer form first makes that independent of the spelling.
_WINDOW_ACTION_PREFIXES = (
    CB_SESSIONS_KILL_CONFIRM,
    CB_SESSIONS_KILL,
    CB_SESSIONS_LIVE_CONFIRM,
    CB_SESSIONS_LIVE,
)


async def _dispatch_window_action(
    query: CallbackQuery,
    user_id: int,
    data: str,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Route a per-session button after checking the user owns its window."""
    prefix = next((p for p in _WINDOW_ACTION_PREFIXES if data.startswith(p)), None)
    if prefix is None:
        return
    window_id = data[len(prefix) :]
    if not user_owns_window(user_id, window_id):
        await query.answer("Not your session", show_alert=True)
        return
    if prefix == CB_SESSIONS_KILL_CONFIRM:
        await handle_sessions_kill_confirm(
            query,
            user_id,
            window_id,
            PTBTelegramClient(context.bot),
            adapter_for(context.bot_data),
        )
        await query.answer("Killed")
    elif prefix == CB_SESSIONS_KILL:
        await handle_sessions_kill(query, user_id, window_id)
        await query.answer()
    elif prefix == CB_SESSIONS_LIVE_CONFIRM:
        await query.answer(
            await handle_sessions_live_confirm(query, user_id, window_id)
        )
    else:
        await handle_sessions_live(query, user_id, window_id)
        await query.answer()
