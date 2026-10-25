"""Private upload references; clients never choose a server filesystem path."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .models import ProtocolError
from .store import ProtocolStore, new_id

MAX_UPLOAD_NAME = 200
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


class UploadStore:
    def __init__(self, directory: Path, store: ProtocolStore) -> None:
        self.directory = directory
        self.store = store

    def save(self, name: str, mime: str, content: bytes) -> dict[str, Any]:
        if (
            not name
            or len(name) > MAX_UPLOAD_NAME
            or any(c in name for c in "/\\\x00\r\n")
        ):
            raise ProtocolError(
                400, "invalid_request", "Upload name must be a plain filename"
            )
        if not content or len(content) > MAX_UPLOAD_BYTES:
            raise ProtocolError(
                400, "invalid_request", "Upload must contain 1 byte to 20 MiB"
            )
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        upload_id = new_id("u_")
        path = self.directory / upload_id
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            reference = {
                "id": upload_id,
                "url": f"/v1/uploads/{upload_id}",
                "name": name,
                "size": len(content),
                "mime": mime,
            }
            with self.store.db:
                self.store.db.execute(
                    "INSERT INTO metadata VALUES (?, ?)",
                    ("upload:" + upload_id, json.dumps(reference)),
                )
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return reference

    def get(self, upload_id: str) -> tuple[Path, dict[str, Any]]:
        row = self.store.db.execute(
            "SELECT value FROM metadata WHERE key=?", ("upload:" + upload_id,)
        ).fetchone()
        if row is None:
            raise ProtocolError(404, "not_found", "Upload not found")
        # Metadata keys originate only from save(), never from client filenames.
        path = self.directory / upload_id
        if not path.is_file():
            raise ProtocolError(404, "not_found", "Upload content is unavailable")
        return path, json.loads(row[0])

    def message_text(self, text: str, references: list[dict[str, Any]]) -> str:
        paths = [str(self.get(reference["id"])[0]) for reference in references]
        return text + ("\n\nAttached files:\n" + "\n".join(paths) if paths else "")
