import importlib
import json
import shlex
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import quote

import pytest

from ccgram import providers as providers_pkg
from ccgram.providers import resolve_launch_command
from ccgram.providers.antigravity import AntigravityProvider
from ccgram.session_protocol.backend import CreationOptions, Prompt, Target
from ccgram.session_protocol.models import ProtocolError, Status, utc_now
from ccgram.session_protocol.service import LocalSessionService
from ccgram.session_protocol.store import ProtocolStore
from ccgram.thread_router import ThreadRouter

resume_command = importlib.import_module("ccgram.handlers.recovery.resume_command")
resume_picker = importlib.import_module("ccgram.handlers.recovery.resume_picker")


class _ResumeBackend:
    def __init__(self) -> None:
        self.target = Target(
            id="@9",
            title="project",
            cwd="/workspace",
            provider="antigravity",
            conversation_id="agy-session",
            transcript=None,
            pane_id="%9",
            pane_title="agy",
            rows=24,
            cols=80,
            process_id=99,
            approval_mode="normal",
            status=Status("idle", "", "screen", utc_now()),
            host="test-host",
            resume_provider="antigravity",
            foreground_argv=("agy",),
        )
        self.created: tuple[dict, CreationOptions] | None = None

    async def targets(self) -> list[Target]:
        return [self.target]

    async def inspect(self, target_id: str) -> Target:
        assert target_id == self.target.id
        return self.target

    async def prompt(self, target: Target) -> Prompt | None:
        return None

    async def terminal(self, target: Target, pane: str) -> dict[str, Any]:
        raise AssertionError("terminal is unused in this test")

    async def send_text(
        self, target: Target, text: str, *, enter: bool, raw: bool | None = None
    ) -> bool:
        raise AssertionError("send_text is unused in this test")

    async def send_key(self, target: Target, key: str) -> bool:
        raise AssertionError("send_key is unused in this test")

    async def send_followup(self, target: Target, text: str) -> bool:
        raise AssertionError("send_followup is unused in this test")

    async def select_pane(self, target: Target, pane_id: str) -> Target:
        raise AssertionError("select_pane is unused in this test")

    async def split(self, target: Target) -> str | None:
        raise AssertionError("split is unused in this test")

    async def close(self, target: Target) -> bool:
        raise AssertionError("close is unused in this test")

    async def rename(self, target: Target, title: str) -> bool:
        raise AssertionError("rename is unused in this test")

    async def commands(self, target: Target) -> list[dict[str, str]]:
        raise AssertionError("commands are unused in this test")

    async def create(
        self,
        data: dict[str, Any],
        *,
        trusted_options: CreationOptions | None = None,
    ) -> Target:
        if trusted_options is None:
            raise AssertionError("creation options are required in this test")
        self.created = data, trusted_options
        if trusted_options.resume_id == "invalid/session":
            raise ProtocolError(400, "invalid_request", "Invalid Antigravity session")
        return replace(
            self.target,
            cwd=data["directory"],
            provider=data["provider"],
            conversation_id=trusted_options.resume_id or "agy-session",
            approval_mode=trusted_options.approval_mode,
        )

    async def resume(self, target: Target, conversation_id: str) -> bool:
        raise AssertionError("resume is unused in this test")


def _real_session_context(tmp_path: Path, backend: _ResumeBackend):
    store = ProtocolStore(tmp_path / "session-protocol.sqlite3")
    service = LocalSessionService(backend, store)
    context = MagicMock()
    context.bot_data = {"session_service": service}
    return store, service, context


class TestAntigravityWorkspaceDiscovery:
    @pytest.mark.parametrize(
        "encoded", [False, True], ids=["literal-space", "encoded-space"]
    )
    def test_discovers_workspace_with_spaces(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, encoded: bool
    ) -> None:
        workspace = tmp_path / "workspace with spaces"
        workspace.mkdir()
        brain = tmp_path / "brain"
        log_dir = brain / "conversation-1" / ".system_generated" / "logs"
        log_dir.mkdir(parents=True)
        workspace_uri_path = quote(str(workspace)) if encoded else str(workspace)
        (log_dir / "transcript.jsonl").write_text(
            json.dumps({"cwd": f"file://{workspace_uri_path}"}) + "\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("CCGRAM_ANTIGRAVITY_DATA_DIR", str(brain))

        sessions = AntigravityProvider().discover_resumable_sessions()

        assert [(session.session_id, session.cwd) for session in sessions] == [
            ("conversation-1", str(workspace.resolve()))
        ]

    def test_discovers_real_cli_layout_from_last_conversations(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        brain = tmp_path / "brain"
        session_id = "a4186fac-9bf9-40d2-b655-02bdd21cf0dd"
        log_dir = brain / session_id / ".system_generated" / "logs"
        log_dir.mkdir(parents=True)
        (log_dir / "transcript.jsonl").write_text(
            json.dumps(
                {
                    "type": "USER_INPUT",
                    "source": "USER_EXPLICIT",
                    "content": "hello",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        cache = tmp_path / "cache" / "last_conversations.json"
        cache.parent.mkdir()
        cache.write_text(json.dumps({str(workspace): session_id}), encoding="utf-8")
        monkeypatch.setenv("CCGRAM_ANTIGRAVITY_DATA_DIR", str(brain))

        sessions = AntigravityProvider().discover_resumable_sessions(cwd=str(workspace))

        assert [(session.session_id, session.cwd) for session in sessions] == [
            (session_id, str(workspace.resolve()))
        ]

    def test_discovery_ignores_pytest_environment_marker(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        brain = tmp_path / "brain"
        log_dir = brain / "conversation-1" / ".system_generated" / "logs"
        log_dir.mkdir(parents=True)
        (log_dir / "transcript.jsonl").write_text(
            json.dumps({"cwd": str(workspace)}) + "\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("CCGRAM_ANTIGRAVITY_DATA_DIR", str(brain))
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "inherited-marker")

        sessions = AntigravityProvider().discover_resumable_sessions()

        assert [session.session_id for session in sessions] == ["conversation-1"]


class TestProviderScopedResumeDiscovery:
    def test_global_scan_uses_only_selected_provider(self, monkeypatch) -> None:
        provider = MagicMock()
        provider.discover_resumable_sessions.return_value = [
            SimpleNamespace(
                session_id="agy-session",
                summary="Antigravity",
                cwd="/workspace",
                mtime=10.0,
                msg_count=None,
                provider_name="antigravity",
            )
        ]
        get_provider = MagicMock(return_value=provider)
        monkeypatch.setattr(providers_pkg, "get_provider_for_window", get_provider)

        sessions = resume_command.scan_all_sessions("antigravity")

        get_provider.assert_called_once_with("", provider_name="antigravity")
        provider.discover_resumable_sessions.assert_called_once_with()
        assert [session.provider_name for session in sessions] == ["antigravity"]

    def test_cwd_scan_uses_only_selected_provider(self, tmp_path, monkeypatch) -> None:
        provider = MagicMock()
        provider.discover_resumable_sessions.return_value = [
            SimpleNamespace(
                session_id="agy-session",
                summary="Antigravity",
                cwd=str(tmp_path),
                mtime=10.0,
                msg_count=None,
                provider_name="antigravity",
            )
        ]
        get_provider = MagicMock(return_value=provider)
        monkeypatch.setattr(providers_pkg, "get_provider_for_window", get_provider)

        sessions = resume_picker.scan_sessions_for_cwd(
            str(tmp_path), provider_name="antigravity"
        )

        get_provider.assert_called_once_with("", provider_name="antigravity")
        provider.discover_resumable_sessions.assert_called_once_with(
            cwd=str(tmp_path.resolve()), limit=6
        )
        assert [session.provider_name for session in sessions] == ["antigravity"]


class TestProviderScopedResumeLaunch:
    async def test_global_resume_launches_selected_provider(
        self, tmp_path, monkeypatch
    ) -> None:
        provider = MagicMock()
        provider.capabilities.name = "antigravity"
        provider.capabilities.supports_hook = True
        monkeypatch.setattr(
            resume_command, "get_provider_for_window", MagicMock(return_value=provider)
        )
        backend = _ResumeBackend()
        store, service, context = _real_session_context(tmp_path, backend)
        router = MagicMock()
        router.get_window_for_thread.return_value = None
        router.resolve_chat_id.return_value = -100999
        router.begin_topic_provisioning.return_value = SimpleNamespace(
            claim_id="claim-1"
        )
        router.commit_topic_provisioning.return_value = True
        session_state = MagicMock()
        sync = MagicMock()
        sync.wait_for_session_map_entry = AsyncMock(return_value=True)
        monkeypatch.setattr(resume_command, "thread_router", router)
        monkeypatch.setattr(resume_command, "session_manager", session_state)
        monkeypatch.setattr(resume_command, "session_map_sync", sync)
        monkeypatch.setattr(resume_command.window_query, "view_window", MagicMock())
        monkeypatch.setattr(
            resume_command.window_query,
            "resolve_window_alias",
            lambda window_id: window_id,
        )

        try:
            result = await resume_command._create_resume_window(
                1,
                2,
                "agy-session",
                str(tmp_path),
                provider_name="antigravity",
                context=context,
            )

            assert result == (True, "Resumed session 'project'", "project", "@9")
            assert backend.created == (
                {"provider": "antigravity", "directory": str(tmp_path)},
                CreationOptions("normal", "agy-session", False),
            )
            assert sync.wait_for_session_map_entry.await_args.args[0] == "@9"
            router.attach_provisioning_target.assert_any_call("claim-1", "@9")
            router.commit_topic_provisioning.assert_called_once_with(
                "claim-1", window_name="project"
            )
            session_state.set_window_origin.assert_called_once_with(
                "@9", resume_command.CCGRAM_CREATED_WINDOW_ORIGIN
            )
            snapshots = await service.list_sessions()
            assert len(snapshots) == 1
            assert snapshots[0].id.startswith("s_")
            assert result[3] == "@9"
        finally:
            store.close()

    async def test_invalid_session_does_not_unbind_old_window(
        self, tmp_path, monkeypatch
    ) -> None:
        provider = MagicMock()
        provider.capabilities.name = "antigravity"
        provider.capabilities.supports_hook = True
        backend = _ResumeBackend()
        store, _service, context = _real_session_context(tmp_path, backend)
        thread_router = ThreadRouter(
            schedule_save=lambda: None,
            has_window_state=lambda _window_id: True,
        )
        thread_router.bind_thread(1, 2, "@3", chat_id=-100999)
        monkeypatch.setattr(resume_command, "thread_router", thread_router)
        monkeypatch.setattr(resume_command, "session_manager", MagicMock())
        monkeypatch.setattr(
            resume_command.window_query,
            "view_window",
            MagicMock(return_value=SimpleNamespace(approval_mode="normal")),
        )
        monkeypatch.setattr(resume_command, "tmux_manager", MagicMock())
        monkeypatch.setattr(
            resume_command, "get_provider_for_window", MagicMock(return_value=provider)
        )

        try:
            result = await resume_command._create_resume_window(
                1,
                2,
                "invalid/session",
                str(tmp_path),
                provider_name="antigravity",
                chat_id=-100999,
                context=context,
            )

            assert result == (False, "Invalid Antigravity session", "", "")
            creation = backend.created
            assert creation is not None
            assert creation[1].resume_id == "invalid/session"
            assert thread_router.get_window_for_thread(1, 2, -100999) == "@3"
            assert thread_router.iter_topic_provisionings() == []
            resume_command.tmux_manager.create_window.assert_not_called()
            resume_command.tmux_manager.close_window.assert_not_called()
        finally:
            store.close()


class TestAntigravityExecutableQuoting:
    def test_automatic_executable_path_with_spaces_is_shell_safe(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = tmp_path / "home with spaces"
        executable = home / ".local" / "bin" / "agy"
        executable.parent.mkdir(parents=True)
        executable.write_text("#!/bin/sh\n", encoding="utf-8")
        executable.chmod(0o755)
        monkeypatch.delenv("CCGRAM_ANTIGRAVITY_COMMAND", raising=False)
        monkeypatch.setattr("pathlib.Path.home", lambda: home)
        monkeypatch.setattr("shutil.which", lambda _name: None)

        command = resolve_launch_command("antigravity")

        assert shlex.split(command) == [str(executable.resolve())]
