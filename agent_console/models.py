from __future__ import annotations

import base64
import hashlib
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any


def now_ms() -> int:
    return int(time.time() * 1000)


def _hash_token(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8", errors="replace")).digest()[:18]
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _scope_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-")
    return slug or "unknown"


def build_session_key(
    host_id: str,
    platform: str,
    *,
    session_id: str | None = None,
    transcript_path: str | None = None,
    pid: int | None = None,
    process_start: str | None = None,
) -> str:
    scope = f"{_scope_slug(host_id)}-{_scope_slug(platform)}"
    if session_id:
        seed = f"{host_id}\0{platform}\0session\0{session_id}"
        return f"{scope}-session-{_hash_token(seed)}"
    if transcript_path:
        seed = f"{host_id}:{platform}:{transcript_path}"
        return f"{scope}-transcript-{_hash_token(seed)}"
    if pid is not None:
        seed = f"{host_id}:{platform}:{pid}:{process_start or ''}"
        return f"{scope}-process-{_hash_token(seed)}"
    seed = f"{host_id}:{platform}:unknown"
    return f"{scope}-unknown-{_hash_token(seed)}"


@dataclass
class CollectorError:
    host_id: str
    message: str
    kind: str = "collector_error"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HostSession:
    key: str
    host_id: str
    platform: str
    source: str
    status: str
    confidence: str
    evidence: list[str] = field(default_factory=list)
    session_id: str | None = None
    cwd: str | None = None
    project_name: str | None = None
    last_event: str | None = None
    last_prompt: str | None = None
    last_response: str | None = None
    model: str | None = None
    pid: int | None = None
    ppid: int | None = None
    tty: str | None = None
    tmux_session: str | None = None
    tmux_window: str | None = None
    tmux_pane: str | None = None
    screen_session: str | None = None
    transcript_path: str | None = None
    transcript_mtime_ms: int | None = None
    resume_command: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HostSnapshot:
    host_id: str
    host_label: str
    collected_at_ms: int
    sessions: list[HostSession] = field(default_factory=list)
    screen_sessions: list[dict[str, Any]] = field(default_factory=list)
    errors: list[CollectorError] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "host_id": self.host_id,
            "host_label": self.host_label,
            "collected_at_ms": self.collected_at_ms,
            "sessions": [session.to_dict() for session in self.sessions],
            "screen_sessions": self.screen_sessions,
            "errors": [error.to_dict() for error in self.errors],
        }
