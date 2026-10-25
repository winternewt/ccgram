"""Google Antigravity CLI (agy) provider behind AgentProvider protocol.

Antigravity CLI is an AI coding agent surface with directory-scoped sessions,
JSONL transcript logs, custom step index / timestamp fields, and tool calls.

Transcript format:
  - Location: ``~/.gemini/antigravity-cli/brain/<conversation-id>/.system_generated/logs/transcript.jsonl``
  - Entries: ``{"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "created_at": "...", "content": "..."}``
  - Assistant responses: ``{"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE", ...}``
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import time
from urllib.parse import unquote, urlparse

from ccgram.providers._jsonl import JsonlProvider
from ccgram.providers.base import (
    ProviderCapabilities,
    RESUME_ID_RE,
    ResumableSession,
    SessionStartEvent,
)

_TRANSCRIPT_MAX_AGE_SECS = 120.0
_MAX_CWD_SCAN_LINES = 30

# Antigravity CLI known slash commands
_ANTIGRAVITY_BUILTINS: dict[str, str] = {
    "/about": "Show version info",
    "/agents": "Manage agent configurations",
    "/auth": "Manage authentication",
    "/bug": "Submit a bug report",
    "/chat": "Save, resume, list, or delete named sessions",
    "/clear": "Clear screen and chat context",
    "/commands": "Manage custom slash commands",
    "/compress": "Summarize chat context to save tokens",
    "/copy": "Copy last response to clipboard",
    "/directory": "Manage accessible directories",
    "/directories": "Manage accessible directories",
    "/docs": "Open full Antigravity CLI docs",
    "/editor": "Set editor preference",
    "/extensions": "Manage extensions",
    "/help": "Display available commands",
    "/hooks": "Manage hooks",
    "/ide": "Manage IDE integration",
    "/init": "Generate project context",
    "/mcp": "List MCP servers and tools",
    "/memory": "Show or manage project context",
    "/model": "Switch model mid-session",
    "/permissions": "Manage trust and permissions",
    "/plan": "Switch to plan mode",
    "/policies": "List active policies",
    "/privacy": "Display privacy notice",
    "/quit": "Exit Antigravity CLI",
    "/rewind": "Restart from an earlier message",
    "/settings": "View and edit settings",
    "/skills": "Enable, list, or reload agent skills",
    "/stats": "Show session statistics",
    "/theme": "Change theme",
    "/tools": "List accessible tools",
}

# Role mapping for Antigravity transcript entry types and sources


def _get_platform_executable_candidates() -> tuple[Path, ...]:
    home = Path.home()
    return (
        home / ".local" / "bin" / "agy",
        home / ".gemini" / "antigravity-cli" / "bin" / "agy",
        home / ".antigravity" / "bin" / "agy",
        Path("/usr/local/bin/agy"),
        Path("/opt/homebrew/bin/agy"),
        Path("/usr/bin/agy"),
        home / ".local" / "bin" / "antigravity",
        home / ".gemini" / "antigravity-cli" / "bin" / "antigravity",
        home / ".antigravity" / "bin" / "antigravity",
        Path("/usr/local/bin/antigravity"),
        Path("/opt/homebrew/bin/antigravity"),
        Path("/usr/bin/antigravity"),
    )


def resolve_antigravity_executable() -> str:
    """Resolve Antigravity CLI executable using deterministic precedence."""
    override = os.environ.get("CCGRAM_ANTIGRAVITY_COMMAND", "").strip()
    if override:
        return override

    for name in ("agy", "antigravity"):
        which_path = shutil.which(name)
        if which_path:
            return name

    for candidate in _get_platform_executable_candidates():
        try:
            expanded = candidate.expanduser().resolve()
            if expanded.is_file() and os.access(expanded, os.X_OK):
                return str(expanded)
        except OSError:
            continue

    return "agy"


def get_antigravity_brain_dirs() -> list[Path]:
    """Return ordered list of existing Antigravity brain directories."""
    data_dir_override = os.environ.get("CCGRAM_ANTIGRAVITY_DATA_DIR", "").strip()
    if data_dir_override:
        try:
            path = Path(data_dir_override).expanduser().resolve()
        except OSError, ValueError:
            return []
        return [path] if path.is_dir() else []

    home = Path.home()
    candidates = (
        home / ".gemini" / "antigravity-cli" / "brain",
        home / ".antigravity" / "brain",
        home / ".config" / "antigravity" / "brain",
        home / ".local" / "share" / "antigravity" / "brain",
    )

    dirs: list[Path] = []
    for candidate in candidates:
        try:
            expanded = candidate.expanduser().resolve()
            if expanded.is_dir() and expanded not in dirs:
                dirs.append(expanded)
        except OSError:
            continue
    return dirs


_WORKSPACE_KEYS = frozenset({"cwd", "directorypath", "workspace", "directory"})


def _decode_workspace_path(value: str) -> str | None:
    """Decode one absolute workspace path or local ``file://`` URI."""
    candidate = value.strip().strip("[]")
    if candidate.startswith("file://"):
        parsed = urlparse(candidate)
        if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
            return None
        candidate = unquote(parsed.path)
    if not candidate.startswith("/"):
        return None
    return candidate.rstrip("/\\") or "/"


def _extract_line_cwd_matches(line: str) -> list[str]:
    """Extract high-confidence workspace paths from one JSONL entry."""
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return []

    if not isinstance(payload, dict):
        return []

    results: list[str] = []
    for key, value in payload.items():
        if str(key).lower() not in _WORKSPACE_KEYS or not isinstance(value, str):
            continue
        decoded = _decode_workspace_path(value)
        if decoded and decoded not in results:
            results.append(decoded)
    return results


def _resolve_workspace_path(raw_path: str) -> str | None:
    try:
        return str(Path(raw_path).expanduser().resolve())
    except OSError, ValueError:
        return None


def _read_last_conversations(brain_dir: Path) -> dict[str, str]:
    """Return unambiguous exact workspace-to-conversation mappings."""
    cache_file = brain_dir.parent / "cache" / "last_conversations.json"
    try:
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError, ValueError:
        return {}
    if not isinstance(payload, dict):
        return {}

    conversations: dict[str, str] = {}
    ambiguous_workspaces: set[str] = set()
    for raw_cwd, raw_session_id in payload.items():
        if not isinstance(raw_cwd, str) or not isinstance(raw_session_id, str):
            continue
        if RESUME_ID_RE.fullmatch(raw_session_id) is None:
            continue
        resolved_cwd = _resolve_workspace_path(raw_cwd)
        if resolved_cwd is None or resolved_cwd in ambiguous_workspaces:
            continue
        previous = conversations.get(resolved_cwd)
        if previous is not None and previous != raw_session_id:
            conversations.pop(resolved_cwd, None)
            ambiguous_workspaces.add(resolved_cwd)
            continue
        conversations[resolved_cwd] = raw_session_id
    return conversations


def _conversation_transcript(brain_dir: Path, session_id: str) -> Path:
    return brain_dir / session_id / ".system_generated" / "logs" / "transcript.jsonl"


def _read_antigravity_workspace_cwd(
    log_file: Path,
    *,
    target_cwd: str | None = None,
) -> str | None:
    """Read an exact workspace CWD from the transcript prefix."""
    resolved_target = _resolve_workspace_path(target_cwd) if target_cwd else None
    if target_cwd and resolved_target is None:
        return None

    try:
        with log_file.open(encoding="utf-8") as transcript:
            for index, line in enumerate(transcript):
                if index >= _MAX_CWD_SCAN_LINES:
                    break
                for raw_path in _extract_line_cwd_matches(line):
                    resolved_candidate = _resolve_workspace_path(raw_path)
                    if resolved_candidate is None:
                        continue
                    if resolved_target is not None:
                        if resolved_candidate == resolved_target:
                            return resolved_candidate
                        continue
                    if Path(resolved_candidate).is_dir():
                        return resolved_candidate
    except OSError:
        return None
    return None


def _match_antigravity_cwd(log_file: Path, target_cwd: str) -> bool:
    """Return whether the transcript declares exactly ``target_cwd``."""
    if not target_cwd:
        return True
    return _read_antigravity_workspace_cwd(log_file, target_cwd=target_cwd) is not None


def _collect_brain_candidates(
    brain_dir: Path, age_limit: float, now: float
) -> list[tuple[float, Path, str]]:
    """Collect valid transcript candidates from the brain directory."""
    candidates: list[tuple[float, Path, str]] = []
    if not brain_dir.is_dir():
        return candidates
    try:
        conversation_dirs = list(brain_dir.iterdir())
    except OSError:
        return candidates
    for conversation_dir in conversation_dirs:
        if not conversation_dir.is_dir():
            continue
        session_id = conversation_dir.name
        log_file = conversation_dir / ".system_generated" / "logs" / "transcript.jsonl"
        if not log_file.is_file():
            continue
        try:
            mtime = log_file.stat().st_mtime
            if age_limit > 0 and now - mtime > age_limit:
                continue
            candidates.append((mtime, log_file, session_id))
        except OSError:
            continue
    return candidates


def _cached_resumable_sessions(
    brain_dir: Path,
    resolved_cwd: str | None,
    seen_ids: set[str],
) -> list[tuple[float, ResumableSession]]:
    sessions: list[tuple[float, ResumableSession]] = []
    for workspace_cwd, session_id in _read_last_conversations(brain_dir).items():
        if resolved_cwd is not None and workspace_cwd != resolved_cwd:
            continue
        if resolved_cwd is None and not Path(workspace_cwd).is_dir():
            continue
        log_file = _conversation_transcript(brain_dir, session_id)
        try:
            mtime = log_file.stat().st_mtime
        except OSError:
            continue
        if session_id in seen_ids:
            continue
        seen_ids.add(session_id)
        sessions.append(
            (
                mtime,
                ResumableSession(
                    session_id=session_id,
                    summary=session_id[:12],
                    cwd=workspace_cwd,
                    provider_name="antigravity",
                    mtime=mtime,
                ),
            )
        )
    return sessions


def _mapped_transcript_candidate(
    brain_dirs: list[Path],
    resolved_cwd: str,
    age_limit: float,
    now: float,
) -> tuple[bool, tuple[float, Path, str] | None]:
    candidates: list[tuple[float, Path, str]] = []
    for brain_dir in brain_dirs:
        session_id = _read_last_conversations(brain_dir).get(resolved_cwd)
        if session_id is None:
            continue
        log_file = _conversation_transcript(brain_dir, session_id)
        try:
            mtime = log_file.stat().st_mtime
        except OSError:
            continue
        if age_limit > 0 and now - mtime > age_limit:
            continue
        candidates.append((mtime, log_file, session_id))
    return len(candidates) > 1, candidates[0] if len(candidates) == 1 else None


class AntigravityProvider(JsonlProvider):
    """Provider for Google Antigravity CLI (agy)."""

    _CAPS = ProviderCapabilities(
        name="antigravity",
        launch_command="agy",
        supports_hook=False,
        supports_resume=True,
        supports_resume_picker=True,
        supports_continue=True,
        supports_structured_transcript=True,
        supports_incremental_read=True,
        supports_status_snapshot=True,
        uses_pane_title=False,
        uses_pyte_status_parsing=False,
        builtin_commands=tuple(sorted(_ANTIGRAVITY_BUILTINS.keys())),
        supports_user_command_discovery=False,
        has_yolo_confirmation=False,
        tui_picker_commands=frozenset(
            {
                "agents",
                "auth",
                "chat",
                "editor",
                "extensions",
                "ide",
                "model",
                "privacy",
                "rewind",
                "settings",
                "theme",
            }
        ),
    )
    _BUILTINS = _ANTIGRAVITY_BUILTINS

    def make_launch_args(
        self,
        resume_id: str | None = None,
        use_continue: bool = False,
    ) -> str:
        """Build CLI launch arguments for Antigravity."""
        args: list[str] = []
        if resume_id:
            if not RESUME_ID_RE.match(resume_id):
                raise ValueError(f"Invalid resume_id: {resume_id!r}")
            args.append(f"--conversation {resume_id}")
        elif use_continue:
            args.append("--continue")

        return " ".join(args)

    def discover_resumable_sessions(
        self,
        *,
        cwd: str | None = None,
        limit: int | None = None,
    ) -> list[ResumableSession]:
        """Discover conversations with a verified project workspace."""
        resolved_cwd = _resolve_workspace_path(cwd) if cwd else None
        if cwd and resolved_cwd is None:
            return []

        candidates: list[tuple[float, ResumableSession]] = []
        seen_ids: set[str] = set()
        for brain_dir in get_antigravity_brain_dirs():
            candidates.extend(
                _cached_resumable_sessions(brain_dir, resolved_cwd, seen_ids)
            )
            for mtime, log_file, session_id in _collect_brain_candidates(
                brain_dir, 0, time.time()
            ):
                if session_id in seen_ids:
                    continue
                workspace_cwd = _read_antigravity_workspace_cwd(
                    log_file,
                    target_cwd=resolved_cwd,
                )
                if workspace_cwd is None:
                    continue
                seen_ids.add(session_id)
                candidates.append(
                    (
                        mtime,
                        ResumableSession(
                            session_id=session_id,
                            summary=session_id[:12],
                            cwd=workspace_cwd,
                            provider_name="antigravity",
                            mtime=mtime,
                        ),
                    )
                )

        candidates.sort(key=lambda item: item[0], reverse=True)
        sessions = [session for _, session in candidates]
        return sessions[:limit] if limit is not None else sessions

    def discover_transcript(
        self,
        cwd: str,
        window_key: str,
        *,
        max_age: float | None = None,
    ) -> SessionStartEvent | None:
        """Discover latest Antigravity CLI transcript on disk matching window."""
        resolved_cwd = _resolve_workspace_path(cwd) if cwd else None
        if resolved_cwd is None:
            return None

        brain_dirs = get_antigravity_brain_dirs()
        if not brain_dirs:
            return None

        age_limit = _TRANSCRIPT_MAX_AGE_SECS if max_age is None else max_age
        now = time.time()

        ambiguous_mapping, selected = _mapped_transcript_candidate(
            brain_dirs, resolved_cwd, age_limit, now
        )
        if ambiguous_mapping:
            return None
        if selected is None:
            candidates: list[tuple[float, Path, str]] = []
            for brain_dir in brain_dirs:
                candidates.extend(_collect_brain_candidates(brain_dir, age_limit, now))
            candidates.sort(reverse=True)
            selected = next(
                (
                    candidate
                    for candidate in candidates
                    if _match_antigravity_cwd(candidate[1], resolved_cwd)
                ),
                None,
            )
            if selected is None:
                return None

        _, latest_file, session_id = selected

        return SessionStartEvent(
            session_id=session_id,
            cwd=resolved_cwd,
            transcript_path=str(latest_file),
            window_key=window_key,
        )

    def has_output_since(self, transcript_path: str, offset: int) -> bool:
        """Check if any transcript output appeared after *offset*."""
        try:
            return os.path.getsize(transcript_path) > offset
        except OSError:
            return False

    def build_status_snapshot(
        self,
        transcript_path: str,
        *,
        display_name: str = "",
        session_id: str = "",
        cwd: str = "",
    ) -> str | None:
        """Build status snapshot for Antigravity sessions."""
        size = (
            os.path.getsize(transcript_path) if os.path.exists(transcript_path) else 0
        )
        return (
            f"🌀 [{display_name}] Antigravity session active.\n"
            f"📁 `{cwd}`\n"
            f"📄 `{os.path.basename(transcript_path)}` ({size} bytes)\n"
            f"⭐ ID: `{session_id[:8]}`"
        )
