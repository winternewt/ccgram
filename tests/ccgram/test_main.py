"""Tests for process exit behavior after bot polling stops."""

import logging
import os
from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
from telegram.error import RetryAfter

from ccgram.main import run_bot, setup_logging


def test_run_bot_exits_nonzero_after_sustained_polling_conflict() -> None:
    config = MagicMock(
        allowed_users=set(),
        claude_projects_path="/tmp/claude",
        multiplexer_name="herdr",
    )
    application = MagicMock()

    with (
        patch.dict(os.environ, {"TMUX_SESSION_NAME": "test"}),
        patch("ccgram.main.setup_logging"),
        patch("ccgram.config.config", config),
        patch("ccgram.bot.create_bot", return_value=application),
        patch("ccgram.bot.polling_conflict_requires_restart", return_value=True),
        patch("ccgram.main._install_signal_handlers"),
        pytest.raises(SystemExit, match="1") as exc_info,
    ):
        run_bot()

    assert exc_info.value.code == 1
    application.run_polling.assert_called_once()


@pytest.fixture
def restored_logging() -> Iterator[None]:
    """Undo the global logging state ``setup_logging`` installs.

    It clears root handlers and attaches a filter to a named third-party
    logger; leaving either in place would follow the rest of the session.
    """
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    rate_limiter = logging.getLogger("telegram.ext.AIORateLimiter")
    saved_filters = rate_limiter.filters[:]
    try:
        yield
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
        rate_limiter.filters[:] = saved_filters


def test_exhausted_rate_limit_keeps_its_message_and_drops_the_traceback(
    restored_logging: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """PTB logs the give-up with ``exception()``; only the stack is noise."""
    setup_logging("INFO")

    try:
        raise RetryAfter(3)
    except RetryAfter as exc:
        logging.getLogger("telegram.ext.AIORateLimiter").exception(
            "Rate limit hit after maximum of %d retries", 5, exc_info=exc
        )

    err = capsys.readouterr().err
    assert "Rate limit hit after maximum of 5 retries" in err
    assert "Traceback" not in err
    assert "RetryAfter" not in err


def test_the_filter_does_not_reach_other_telegram_loggers(
    restored_logging: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """Negative control: the stack still prints where it carries information."""
    setup_logging("INFO")

    try:
        raise RetryAfter(3)
    except RetryAfter as exc:
        logging.getLogger("telegram.ext.Application").exception(
            "unhandled", exc_info=exc
        )

    err = capsys.readouterr().err
    assert "Traceback" in err
    assert "RetryAfter" in err
