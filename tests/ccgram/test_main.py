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

from ccgram import main
from ccgram.main import run_bot, setup_logging
from ccgram.state_writer_lock import StateWriterLease


@pytest.fixture(autouse=True)
def _isolate_state_writer_lease(monkeypatch):
    monkeypatch.setattr(main, "_state_writer_lease", None)
    yield
    lease = main._state_writer_lease
    if lease is not None:
        lease.close()


def test_run_bot_exits_nonzero_after_sustained_polling_conflict(tmp_path) -> None:
    config = MagicMock(
        allowed_users=set(),
        claude_projects_path="/tmp/claude",
        multiplexer_name="herdr",
        state_file=tmp_path / "state.json",
    )
    application = MagicMock()
    application.bot_data = {}

    def create_application():
        lease = main._state_writer_lease
        assert lease is not None and lease.held
        assert lease.state_path == (tmp_path / "state.json").resolve()
        contender = StateWriterLease(tmp_path / "state.json")
        try:
            assert not contender.try_acquire()
        finally:
            contender.close()
        return application

    with (
        patch.dict(os.environ, {"TMUX_SESSION_NAME": "test"}),
        patch("ccgram.main.setup_logging"),
        patch("ccgram.bot.create_bot", side_effect=create_application),
        patch("ccgram.config.config", config),
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


def test_run_bot_execs_again_after_restart_callback(tmp_path) -> None:
    config = MagicMock(
        allowed_users=set(),
        claude_projects_path="/tmp/claude",
        multiplexer_name="herdr",
        state_file=tmp_path / "state.json",
    )
    application = MagicMock()
    application.bot_data = {}
    application.run_polling.side_effect = lambda **_kwargs: application.bot_data[
        "request_restart"
    ]()

    with (
        patch.dict(os.environ, {"TMUX_SESSION_NAME": "test"}),
        patch("ccgram.main.setup_logging"),
        patch("ccgram.bot.create_bot", return_value=application),
        patch("ccgram.config.config", config),
        patch("ccgram.bot.polling_conflict_requires_restart", return_value=False),
        patch("ccgram.main._install_signal_handlers"),
        patch("ccgram.main.os.execv", side_effect=SystemExit(0)) as execv,
        patch("ccgram.main._reraise_shutdown_signal") as reraise_signal,
        pytest.raises(SystemExit, match="0"),
    ):
        run_bot()

    execv.assert_called_once_with(sys.argv[0], sys.argv)
    reraise_signal.assert_not_called()


def test_run_bot_shutdown_reason_reads_signal_at_notification_time(tmp_path) -> None:
    from ccgram import main
    from ccgram.bot import GET_SHUTDOWN_REASON_KEY

    config = MagicMock(
        allowed_users=set(),
        claude_projects_path="/tmp/claude",
        multiplexer_name="herdr",
        state_file=tmp_path / "state.json",
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


@pytest.mark.parametrize("auto_detected", [False, True])
def test_ensure_tmux_session_applies_settings_before_native_lookup(
    monkeypatch, auto_detected
):
    from types import SimpleNamespace

    from ccgram.config import config
    from ccgram.multiplexer import tmux

    backend = tmux.TmuxManager(session_name="before")
    server = MagicMock()
    session = SimpleNamespace(session_name="configured")
    observed = []
    server.sessions.get.side_effect = lambda **_kwargs: (
        observed.append(
            (backend.session_name, backend.main_window_name, backend.own_window_id)
        )
        or session
    )
    backend._server = server
    monkeypatch.setattr(tmux, "tmux_manager", backend)
    monkeypatch.setattr(config, "tmux_session_name", "configured")
    monkeypatch.setattr(config, "tmux_main_window_name", "landing")
    monkeypatch.setattr(config, "own_window_id", "@9")

    main._ensure_tmux_session(auto_detected)

    assert observed == [("configured", "landing", "@9")]
    server.sessions.get.assert_called_once_with(session_name="configured", default=None)
