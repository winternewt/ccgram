"""Shared provider base behavior for JSONL-backed CLI providers."""

from typing import ClassVar

from ccgram.providers.base import (
    DiscoveredCommand,
    ProviderCapabilities,
    RESUME_ID_RE,
    ResumableSession,
    SessionStartEvent,
    StatusUpdate,
)


def extract_bang_output(pane_text: str, command: str) -> str | None:
    """Extract ``!`` command output from captured pane text.

    Looks for a line starting with ``! <command_prefix>`` and returns it.
    Exact format is assumed pending empirical verification.
    """
    if not pane_text or not command:
        return None
    for line in pane_text.splitlines():
        if line.strip().startswith(f"! {command}"):
            return line.strip()
    return None


# ── Base class for hookless JSONL providers ──────────────────────────────


class JsonlProvider:
    """Base class for hookless providers that use JSONL transcripts.

    Subclasses must set ``_CAPS`` and ``_BUILTINS``, and override
    ``make_launch_args`` if their resume syntax differs from ``--resume <id>``.
    Transcript parsing is handled by ``session_protocol.messages``.
    """

    _CAPS: ClassVar[ProviderCapabilities]
    _BUILTINS: ClassVar[dict[str, str]] = {}

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._CAPS

    def make_launch_args(
        self,
        resume_id: str | None = None,
        use_continue: bool = False,  # noqa: ARG002 — protocol signature
    ) -> str:
        if resume_id:
            if not RESUME_ID_RE.match(resume_id):
                raise ValueError(f"Invalid resume_id: {resume_id!r}")
            return f"--resume {resume_id}"
        return ""

    def discover_resumable_sessions(
        self,
        *,
        cwd: str | None = None,  # noqa: ARG002 — protocol signature
        limit: int | None = None,  # noqa: ARG002 — protocol signature
    ) -> list[ResumableSession]:
        return []

    def parse_terminal_status(
        self,
        pane_text: str,  # noqa: ARG002
        *,
        pane_title: str = "",  # noqa: ARG002
    ) -> StatusUpdate | None:
        # Non-Claude CLIs lack spinner-based status lines. Their bottom chrome
        # (e.g. "[INSERT] ~/path ..." for Gemini, "? for shortcuts ..." for Codex)
        # is not useful as a status indicator. Subclasses override for interactive
        # UI detection (see GeminiProvider).
        return None

    def extract_bash_output(self, pane_text: str, command: str) -> str | None:
        return extract_bang_output(pane_text, command)

    def requires_pane_title_for_detection(
        self,
        pane_current_command: str,  # noqa: ARG002 — protocol signature
    ) -> bool:
        return False

    def detect_from_pane_title(
        self,
        pane_current_command: str,  # noqa: ARG002 — protocol signature
        pane_title: str,  # noqa: ARG002 — protocol signature
    ) -> bool:
        return False

    def discover_transcript(
        self,
        cwd: str,  # noqa: ARG002 — protocol signature
        window_key: str,  # noqa: ARG002 — protocol signature
        *,
        max_age: float | None = None,  # noqa: ARG002 — protocol signature
    ) -> SessionStartEvent | None:
        return None

    def discover_commands(
        self,
        base_dir: str,  # noqa: ARG002 — protocol signature
    ) -> list[DiscoveredCommand]:
        return [
            DiscoveredCommand(name=name, description=desc, source="builtin")
            for name, desc in self._BUILTINS.items()
        ]

    def build_status_snapshot(
        self,
        transcript_path: str,  # noqa: ARG002 — protocol signature
        *,
        display_name: str = "",  # noqa: ARG002
        session_id: str = "",  # noqa: ARG002
        cwd: str = "",  # noqa: ARG002
    ) -> str | None:
        return None

    def has_output_since(
        self,
        transcript_path: str,  # noqa: ARG002 — protocol signature
        offset: int,  # noqa: ARG002
    ) -> bool:
        return False

    async def scrape_current_mode(
        self,
        window_id: str,  # noqa: ARG002 — protocol signature
    ) -> str | None:
        return None

    async def seed_task_state(
        self,
        window_id: str,  # noqa: ARG002 — protocol signature
        session_id: str,  # noqa: ARG002
        transcript_path: str,  # noqa: ARG002
    ) -> None:
        return None

    def apply_task_entries(
        self,
        window_id: str,  # noqa: ARG002 — protocol signature
        session_id: str,  # noqa: ARG002
        entries: list[dict],  # noqa: ARG002
    ) -> None:
        return None
