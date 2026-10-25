"""Tests for process exit behavior after bot polling stops."""

import io
import logging
import os
import signal
import sys
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


def test_run_bot_execs_again_after_restart_callback() -> None:
    config = MagicMock(
        allowed_users=set(),
        claude_projects_path="/tmp/claude",
        multiplexer_name="herdr",
    )
    application = MagicMock()
    application.bot_data = {}
    application.run_polling.side_effect = lambda **_kwargs: application.bot_data[
        "request_restart"
    ]()

    with (
        patch.dict(os.environ, {"TMUX_SESSION_NAME": "test"}),
        patch("ccgram.main.setup_logging"),
        patch("ccgram.config.config", config),
        patch("ccgram.bot.create_bot", return_value=application),
        patch("ccgram.bot.polling_conflict_requires_restart", return_value=False),
        patch("ccgram.main._install_signal_handlers"),
        patch("ccgram.main.os.execv", side_effect=SystemExit(0)) as execv,
        patch("ccgram.main._reraise_shutdown_signal") as reraise_signal,
        pytest.raises(SystemExit, match="0"),
    ):
        run_bot()

    execv.assert_called_once_with(sys.argv[0], sys.argv)
    reraise_signal.assert_not_called()


def test_run_bot_shutdown_reason_reads_signal_at_notification_time() -> None:
    from ccgram import main
    from ccgram.bot import GET_SHUTDOWN_REASON_KEY

    config = MagicMock(
        allowed_users=set(),
        claude_projects_path="/tmp/claude",
        multiplexer_name="herdr",
    )
    application = MagicMock()
    application.bot_data = {}

    def finish_polling(**_kwargs) -> None:
        get_reason = application.bot_data[GET_SHUTDOWN_REASON_KEY]
        assert get_reason() == "Clean exit"
        with patch.object(main, "_shutdown_signal", signal.SIGTERM):
            assert get_reason() == "Received SIGTERM"

    application.run_polling.side_effect = finish_polling
    with (
        patch.dict(os.environ, {"TMUX_SESSION_NAME": "test"}),
        patch("ccgram.main.setup_logging"),
        patch("ccgram.config.config", config),
        patch("ccgram.bot.create_bot", return_value=application),
        patch("ccgram.bot.polling_conflict_requires_restart", return_value=False),
        patch("ccgram.main._install_signal_handlers"),
        patch("ccgram.main._reraise_shutdown_signal"),
        patch.object(main, "_shutdown_signal", 0),
    ):
        run_bot()

    application.run_polling.assert_called_once()


class TestSignalDiagnostics:
    def test_signal_handler_records_signum_and_raises(self) -> None:
        from ccgram import main

        stderr_capture = io.StringIO()
        with (
            patch.object(main, "_shutdown_signal", 0),
            patch("ccgram.bot.arm_shutdown_watchdog") as arm_watchdog,
            patch("sys.stderr", stderr_capture),
        ):
            with pytest.raises(SystemExit):
                main._on_signal(signal.SIGINT)
            assert main._shutdown_signal == signal.SIGINT

        arm_watchdog.assert_called_once_with(exit_code=128 + signal.SIGINT)
        output = stderr_capture.getvalue()
        assert "SIGINT" in output
        assert "call stack" not in output

    def test_install_uses_loop_add_signal_handler(self) -> None:
        from ccgram.main import _install_signal_handlers, _on_signal

        loop = MagicMock()
        _install_signal_handlers(loop)

        registered = {call.args[0] for call in loop.add_signal_handler.call_args_list}
        assert registered == {signal.SIGINT, signal.SIGTERM, signal.SIGQUIT}
        for call in loop.add_signal_handler.call_args_list:
            assert call.args[1] is _on_signal
            assert call.args[2] == call.args[0]
