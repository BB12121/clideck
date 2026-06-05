from __future__ import annotations

import json
import os
import shlex
import time
from pathlib import Path, PurePosixPath, PureWindowsPath
from stat import S_ISREG
from typing import Any

from agent_console.models import HostSession, build_session_key


TERMINAL_STATUSES = {
    "error": ("waiting", "medium"),
    "task_complete": ("completed", "medium"),
    "turn_aborted": ("completed", "medium"),
}

PERMISSION_WAIT_EVENT = "permission_request"


def parse_codex_rollout(path: Path, host_id: str, now: float | None = None) -> HostSession:
    session_id: str | None = None
    source = "cli"
    cwd: str | None = None
    model: str | None = None
    last_event: str | None = None
    decisive_event: str | None = None
    last_prompt: str | None = None
    last_response: str | None = None
    evidence: list[str] = []

    def add_evidence(value: str) -> None:
        if value not in evidence:
            evidence.append(value)

    for row in _iter_jsonl(path):
        row_type = row.get("type") or row.get("kind")
        payload = _payload(row)

        if row_type == "session_meta":
            session_id = _first_text(payload, "id", "session_id")
            cwd = _first_text(payload, "cwd") or cwd
            originator = _first_text(payload, "originator")
            source = "codex_vscode" if originator == "codex_vscode" else "cli"
            add_evidence("session_meta")
        elif row_type == "turn_context":
            cwd = _first_text(payload, "cwd") or cwd
            model = _first_text(payload, "model") or _cfg_model(payload) or model
            add_evidence("turn_context")
        elif row_type == "event_msg":
            event_type = _event_type(payload)
            if not event_type:
                continue
            last_event = event_type
            add_evidence(event_type)
            if _is_permission_request(event_type, payload):
                decisive_event = PERMISSION_WAIT_EVENT
                add_evidence(PERMISSION_WAIT_EVENT)
            elif event_type == "task_started" or event_type in TERMINAL_STATUSES:
                decisive_event = event_type
            if event_type == "user_message":
                last_prompt = _event_text(payload) or last_prompt
            elif event_type in {"agent_message", "task_complete", "error"} or decisive_event == PERMISSION_WAIT_EVENT:
                last_response = _event_text(payload) or last_response
        elif row_type == "response_item":
            item = _response_item(payload)
            item_type = _first_text(item, "type") or "response_item"
            role = _first_text(item, "role")
            add_evidence(f"response_item:{item_type}")
            if _is_permission_request(item_type, item):
                last_event = item_type
                decisive_event = PERMISSION_WAIT_EVENT
                add_evidence(PERMISSION_WAIT_EVENT)
            text = _content_text(item.get("content"))
            if not text:
                text = _content_text(item.get("text") or item.get("message"))
            if role == "user":
                last_prompt = text or last_prompt
            elif role == "assistant" or decisive_event == PERMISSION_WAIT_EVENT:
                last_response = text or last_response
            elif item_type in {"function_call", "tool_call", "local_shell_call"}:
                last_event = item_type
                call_text = _call_text(item)
                if call_text:
                    add_evidence(call_text)

    stat = path.stat()
    current_time = time.time() if now is None else now
    status, confidence = _classify_status(decisive_event, current_time - stat.st_mtime)
    project_name = _project_name(cwd)
    resume_command = _render_resume_command(cwd, session_id)

    return HostSession(
        key=build_session_key(
            host_id,
            "codex",
            session_id=session_id,
            transcript_path=str(path),
        ),
        host_id=host_id,
        platform="codex",
        source=source,
        status=status,
        confidence=confidence,
        evidence=evidence,
        session_id=session_id,
        cwd=cwd,
        project_name=project_name,
        last_event=last_event,
        last_prompt=last_prompt,
        last_response=last_response,
        model=model,
        transcript_path=str(path),
        transcript_mtime_ms=int(stat.st_mtime * 1000),
        resume_command=resume_command,
    )


def discover_codex_sessions(root: Path, host_id: str, max_files: int = 200) -> list[HostSession]:
    sessions_root = root / ".codex" / "sessions"
    if max_files <= 0 or not sessions_root.exists():
        return []

    candidates: list[tuple[float, Path]] = []
    for path in sessions_root.glob("**/rollout-*.jsonl"):
        try:
            stat_result = path.stat()
        except OSError:
            continue
        if S_ISREG(stat_result.st_mode):
            candidates.append((stat_result.st_mtime, path))

    candidates.sort(key=lambda item: (item[0], str(item[1])), reverse=True)
    sessions: list[HostSession] = []
    for _, path in candidates[:max_files]:
        try:
            sessions.append(parse_codex_rollout(path, host_id))
        except OSError:
            continue
    return sessions


def _iter_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload")
    return payload if isinstance(payload, dict) else row


def _response_item(payload: dict[str, Any]) -> dict[str, Any]:
    item = payload.get("item")
    return item if isinstance(item, dict) else payload


def _first_text(data: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _cfg_model(payload: dict[str, Any]) -> str | None:
    cfg = payload.get("cfg")
    if not isinstance(cfg, dict):
        return None
    model = cfg.get("model")
    return model if isinstance(model, str) and model else None


def _event_type(payload: dict[str, Any]) -> str | None:
    event_type = _first_text(payload, "type", "event", "name")
    if event_type:
        return event_type
    role = _first_text(payload, "role")
    if role == "user":
        return "user_message"
    if role == "assistant":
        return "agent_message"
    return None


def _event_text(payload: dict[str, Any]) -> str | None:
    for key in ("last_agent_message", "codex_error_info", "message", "content", "text"):
        text = _content_text(payload.get(key))
        if text:
            return text
    return None


def _content_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = [
            text
            for item in value
            if (text := _content_text(item))
        ]
        return "\n".join(parts) if parts else None
    if isinstance(value, dict):
        for key in ("text", "content"):
            text = _content_text(value.get(key))
            if text:
                return text
    return None


def _call_text(item: dict[str, Any]) -> str | None:
    name = _first_text(item, "name", "call_id")
    command = _content_text(item.get("command") or item.get("arguments"))
    if name and command:
        return f"{name}: {command}"
    return name or command


def _is_permission_request(event_type: str | None, data: dict[str, Any]) -> bool:
    tokens = [event_type or ""]
    for key in ("type", "event", "name", "status", "reason"):
        text = _content_text(data.get(key))
        if text:
            tokens.append(text)
    haystack = " ".join(tokens).lower()
    if not haystack:
        return False
    permission_terms = (
        "approval",
        "permission",
        "approve",
        "authorization",
        "authorisation",
        "elicitation",
        "needs approval",
        "requires approval",
    )
    request_terms = ("request", "required", "requires", "needs", "waiting", "pending", "confirm")
    return any(term in haystack for term in permission_terms) and any(
        term in haystack for term in request_terms
    )


def _classify_status(decisive_event: str | None, age_seconds: float) -> tuple[str, str]:
    age_seconds = max(0, age_seconds)
    if decisive_event == PERMISSION_WAIT_EVENT:
        return "waiting", "high"
    if decisive_event in TERMINAL_STATUSES:
        return TERMINAL_STATUSES[decisive_event]
    if decisive_event == "task_started":
        if age_seconds < 300:
            return "running", "medium"
        return _age_fallback_status(age_seconds)
    if age_seconds < 300:
        return "idle", "low"
    return _age_fallback_status(age_seconds)


def _age_fallback_status(age_seconds: float) -> tuple[str, str]:
    if age_seconds < 86400:
        return "completed", "low"
    return "stale", "low"


def _resume_command(cwd: str | None, session_id: str | None) -> str | None:
    return _render_resume_command(cwd, session_id)


def _render_resume_command(
    cwd: str | None,
    session_id: str | None,
    *,
    os_name: str | None = None,
) -> str | None:
    if not cwd or not session_id:
        return None
    if (os.name if os_name is None else os_name) == "nt":
        return (
            f"Set-Location -LiteralPath {_powershell_quote(cwd)}; "
            f"codex resume {_powershell_quote(session_id)}"
        )
    return f"cd {shlex.quote(cwd)} && codex resume {shlex.quote(session_id)}"


def _powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _project_name(cwd: str | None) -> str | None:
    if not cwd:
        return None
    if "\\" in cwd:
        return PureWindowsPath(cwd).name or None
    return PurePosixPath(cwd).name or None
