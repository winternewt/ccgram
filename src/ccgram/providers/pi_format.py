"""Read source metadata from Pi transcript files."""

import json
from typing import Any


def read_session_header(file_path: str) -> dict[str, str] | None:
    """Read a valid Pi session ID and working directory from the first line."""
    try:
        with open(file_path, encoding="utf-8") as transcript:
            first_line = transcript.readline()
    except OSError:
        return None
    if not first_line:
        return None
    try:
        entry: Any = json.loads(first_line)
    except json.JSONDecodeError:
        return None
    if not isinstance(entry, dict) or entry.get("type") != "session":
        return None

    session_id = entry.get("id")
    cwd = entry.get("cwd")
    if not isinstance(session_id, str) or not session_id:
        return None
    if not isinstance(cwd, str) or not cwd:
        return None
    return {"id": session_id, "cwd": cwd}
