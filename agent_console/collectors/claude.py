from __future__ import annotations

import json
import os
import shlex
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from agent_console.collectors.processes import pid_alive, pid_tty
from agent_console.models import HostSession, build_session_key


def discover_claude_sessions(home: Path, host_id: str) -> list[HostSession]:
    sessions_root = home / ".claude" / "sessions"
    if not sessions_root.exists():
        return []

    sessions: list[HostSession] = []
    for path in sorted(sessions_root.glob("*.json")):
        if path.stem.startswith("session-"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        session = _session_from_state(home, host_id, path, data)
        if session is not None:
            sessions.append(session)
    return sessions


def _session_from_state(
    home: Path,
    host_id: str,
    state_path: Path,
    data: dict[str, Any],
) -> HostSession | None:
    session_id = _first_text(data, "sessionId", "session_id", "id")
    cwd = _first_text(data, "cwd", "workspace")
    if not session_id and not cwd:
        return None

    pid = _int_value(data.get("pid"))
    if pid is None:
        return None
    alive = pid_alive(pid) if pid is not None else False
    tty = pid_tty(pid) if alive else None
    transcript_path = _transcript_path(home, cwd, session_id)
    transcript_mtime_ms = _mtime_ms(transcript_path)
    if not alive and not transcript_mtime_ms:
        return None
    status = _status(data, alive, pid)
    evidence = ["claude_session_state"]
    if transcript_path and transcript_path.exists():
        evidence.append("transcript")
    if alive:
        evidence.append("pid_alive")
    elif pid is not None:
        evidence.append("pid_dead")

    return HostSession(
        key=build_session_key(
            host_id,
            "claude",
            session_id=session_id,
            transcript_path=str(transcript_path) if transcript_path else str(state_path),
            pid=pid,
        ),
        host_id=host_id,
        platform="claude",
        source="cli",
        status=status,
        confidence="high" if alive else "medium",
        evidence=evidence,
        session_id=session_id,
        cwd=cwd,
        project_name=_project_name(cwd),
        pid=pid,
        tty=tty,
        transcript_path=str(transcript_path) if transcript_path else None,
        transcript_mtime_ms=transcript_mtime_ms,
        resume_command=_render_resume_command(cwd, session_id),
    )


def _status(data: dict[str, Any], alive: bool, pid: int | None) -> str:
    if pid is not None and not alive:
        return "completed"
    waiting_for = data.get("waitingFor")
    if alive and waiting_for:
        return "waiting"
    raw_status = _first_text(data, "status")
    return raw_status or ("running" if alive else "completed")


def _transcript_path(home: Path, cwd: str | None, session_id: str | None) -> Path | None:
    if not cwd or not session_id:
        return None
    return home / ".claude" / "projects" / _cwd_slug(cwd) / f"{session_id}.jsonl"


def _cwd_slug(cwd: str) -> str:
    return (
        cwd.replace("\\", "-")
        .replace("/", "-")
        .replace(":", "-")
        .replace("_", "-")
        .replace(".", "-")
    )


def _render_resume_command(
    cwd: str | None,
    session_id: str | None,
    *,
    os_name: str | None = None,
) -> str | None:
    if not session_id:
        return None
    resume = f"claude --resume {_shell_quote(session_id, os_name=os_name)}"
    if not cwd:
        return resume
    if (os.name if os_name is None else os_name) == "nt":
        return f"Set-Location -LiteralPath {_powershell_quote(cwd)}; {resume}"
    return f"cd {shlex.quote(cwd)} && {resume}"


def _shell_quote(value: str, *, os_name: str | None = None) -> str:
    if (os.name if os_name is None else os_name) == "nt":
        return _powershell_quote(value)
    return shlex.quote(value)


def _powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _first_text(data: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _int_value(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _mtime_ms(path: Path | None) -> int | None:
    if path is None:
        return None
    try:
        return int(path.stat().st_mtime * 1000)
    except OSError:
        return None


def _project_name(cwd: str | None) -> str | None:
    if not cwd:
        return None
    if "\\" in cwd:
        return PureWindowsPath(cwd).name or None
    return PurePosixPath(cwd).name or None
