"""Tests for the /upgrade restart stop path."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccgram.handlers.upgrade import _upgrade_summary

# Captured from uv 0.12.3 `uv tool upgrade ccgram` on a git-installed tool.
_CCGRAM_UPDATED = (
    "Updated ccgram v0.1.dev862 -> v0.1.dev863\n"
    " - ccgram==0.1.dev862 (from git+https://github.com/winternewt/ccgram@06908db)\n"
    " + ccgram==0.1.dev863 (from git+https://github.com/winternewt/ccgram@27a22d5)\n"
    "Installed 1 executable: ccgram\n"
)
_DEPENDENCIES_ONLY = (
    "Modified ccgram environment\n"
    " - pillow==12.0.0\n"
    " + pillow==13.0.0\n"
    " - pyromark==0.10.0\n"
    " + pyromark==0.10.1\n"
)


class _FakeProcess:
    returncode = 0

    def __init__(self, output: bytes) -> None:
        self._output = output

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._output, b""

    def kill(self) -> None:  # pragma: no cover - only on timeout
        raise AssertionError("kill must not be called")


async def test_upgrade_restart_arms_the_shutdown_watchdog() -> None:
    """A wedge in this stop path must still exit for the supervisor."""
    from ccgram.handlers import upgrade as upgrade_module

    update = MagicMock()
    update.effective_user.id = 42
    update.message.reply_text = AsyncMock(return_value=MagicMock())
    context = MagicMock()

    with (
        patch.object(upgrade_module.config, "is_user_allowed", return_value=True),
        patch.object(upgrade_module, "safe_edit", new_callable=AsyncMock),
        patch.object(
            upgrade_module.asyncio,
            "create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=_FakeProcess(b"Upgraded ccgram v1.0.0 -> v1.0.1"),
        ),
        patch("ccgram.bot.arm_shutdown_watchdog") as arm_watchdog,
        patch("ccgram.main._restart_requested", False),
    ):
        await upgrade_module.upgrade_command(update, context)

        arm_watchdog.assert_called_once_with()
        context.application.stop_running.assert_called_once_with()

        from ccgram import main as main_module

        assert main_module._restart_requested is True


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        (_CCGRAM_UPDATED, "Upgraded to v0.1.dev863"),
        (
            _DEPENDENCIES_ONLY,
            "ccgram v0.1.dev862 unchanged; "
            "updated dependencies: pillow==13.0.0, pyromark==0.10.1",
        ),
        ("something uv has not printed before", "Upgraded"),
    ],
    ids=["ccgram_updated", "dependencies_only", "unrecognised"],
)
def test_upgrade_summary_says_what_changed(output: str, expected: str) -> None:
    assert _upgrade_summary(output, "0.1.dev862") == expected
