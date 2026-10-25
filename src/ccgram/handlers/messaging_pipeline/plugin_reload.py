"""Type ``/reload-plugins`` into a Claude pane when the agent asks for it.

Claude tells the user to "run /reload-plugins" after it installs or edits
a plugin, and that command only works when typed into the terminal. This
module spots the request in a complete assistant answer and sends the
command as if the user had typed it, so a Telegram-only user does not have
to retype it.
"""

import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import structlog

from ...monitor_events import NewMessage
from ..telegram_origin import send_telegram_to_window

logger = structlog.get_logger()

RELOAD_PLUGINS_COMMAND = "/reload-plugins"

# Tolerates the backticks Claude usually puts around the command.
_RELOAD_REQUEST = re.compile(r"\brun\s+`?/reload-plugins\b", re.IGNORECASE)

# A turn asks once. The cooldown bounds a repeat (Claude asking again after
# the reload, or a catch-up read replaying old answers) to a single send.
RELOAD_COOLDOWN_SECONDS = 60.0
_last_sent: dict[str, float] = {}

# A restart re-reads transcript entries it cannot prove reached Telegram,
# including answers whose reload already happened. Only an answer written
# this recently is a live request; an undated one is never acted on.
RELOAD_MAX_AGE_SECONDS = 120.0


def _is_fresh(timestamp: str | None) -> bool:
    if not timestamp:
        return False
    try:
        written = datetime.fromisoformat(timestamp)
    except ValueError:
        return False
    if written.tzinfo is None:
        return False
    age = (datetime.now(UTC) - written).total_seconds()
    return age <= RELOAD_MAX_AGE_SECONDS


def requests_plugin_reload(msg: NewMessage) -> bool:
    """Whether *msg* is a fresh, complete Claude answer asking to run /reload-plugins."""
    return (
        msg.is_complete
        and msg.role == "assistant"
        and msg.content_type == "text"
        and msg.provider_name == "claude"
        and _RELOAD_REQUEST.search(msg.text) is not None
        and _is_fresh(msg.timestamp)
    )


async def send_plugin_reload(
    user_id: int,
    window_id: str,
    thread_id: int | None,
    chat_id: int | None,
    *,
    resolve_adapter: Callable[[], Any],
) -> str | None:
    """Type /reload-plugins into the window and return a notice, or None if cooling down.

    ``resolve_adapter`` returns the session adapter every pane write goes
    through; it is resolved only here, when a reload is actually sent.
    """
    now = time.monotonic()
    last = _last_sent.get(window_id)
    if last is not None and now - last < RELOAD_COOLDOWN_SECONDS:
        logger.info("Auto /reload-plugins skipped in cooldown", window_id=window_id)
        return None
    _last_sent[window_id] = now
    try:
        adapter = resolve_adapter()
    except RuntimeError as exc:
        logger.warning(
            "Auto /reload-plugins has no session adapter", window_id=window_id
        )
        return f"❌ Could not auto-send {RELOAD_PLUGINS_COMMAND}: {exc}"
    success, error = await send_telegram_to_window(
        user_id,
        window_id,
        thread_id,
        RELOAD_PLUGINS_COMMAND,
        chat_id,
        adapter=adapter,
    )
    if not success:
        logger.warning(
            "Auto /reload-plugins delivery failed", window_id=window_id, error=error
        )
        return f"❌ Could not auto-send {RELOAD_PLUGINS_COMMAND}: {error}"
    logger.info("Auto-sent /reload-plugins", window_id=window_id)
    return f"⚡ Auto-sent {RELOAD_PLUGINS_COMMAND}"


def reset_for_testing() -> None:
    _last_sent.clear()
