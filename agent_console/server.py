from __future__ import annotations

import asyncio
import base64
import json
import os
import platform
import shlex
import shutil
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from agent_console.app_server import AppServerResult, CodexAppServerClient
from agent_console.collectors.local import collect_local_snapshot
from agent_console.collectors.ssh import (
    collect_ssh_snapshot,
    read_ssh_screen_capture,
    read_ssh_timeline,
    send_ssh_screen_input,
    start_ssh_screen_session,
)
from agent_console.config import HostConfig, delete_host, discover_ssh_hosts, load_hosts, save_host
from agent_console.models import CollectorError, HostSnapshot, now_ms


HERE = Path(__file__).parent
STATIC_DIR = HERE / "static"
INDEX_FILE = STATIC_DIR / "index.html"
MAX_TIMELINE_EVENTS = 500
SCREEN_ASSIGNMENTS_FILE = Path("agent-console-state.json")
NOTIFICATION_POLL_SECONDS = 15
COMPLETED_STATUS = "completed"
LOCAL_CODEX_EXEC_TIMEOUT_SECONDS = 900


class SnapshotState:
    def __init__(self) -> None:
        self.snapshots: list[HostSnapshot] = []
        self.last_refresh_ms: int | None = None
        self.notification_task: asyncio.Task | None = None
        self.known_session_statuses: dict[str, str] = {}
        self.notified_completed_keys: set[str] = set()
        self.codex_app_server: CodexAppServerClient | None = None


def collect_host(host: HostConfig) -> HostSnapshot:
    if host.type == "ssh":
        if not host.ssh:
            raise ValueError(f"SSH host {host.id!r} requires an ssh target")
        return collect_ssh_snapshot(
            host.id,
            host.label,
            host.ssh,
            password=host.password,
            timeout_seconds=host.command_timeout_seconds,
        )

    return collect_local_snapshot(host.id, host.label)


def collect_all_hosts_once() -> list[HostSnapshot]:
    snapshots: list[HostSnapshot] = []
    for host in load_hosts():
        try:
            snapshots.append(collect_host(host))
        except Exception as exc:
            snapshots.append(_error_snapshot(host, exc))
    _apply_screen_assignments(snapshots)
    return snapshots


def refresh_snapshots(app: FastAPI) -> list[HostSnapshot]:
    snapshots = collect_all_hosts_once()
    _apply_screen_assignments(snapshots)
    state = _state(app)
    state.snapshots = snapshots
    state.last_refresh_ms = now_ms()
    return snapshots


def start_notification_monitor(app: FastAPI) -> None:
    state = _state(app)
    if state.notification_task and not state.notification_task.done():
        return
    state.notification_task = asyncio.create_task(_notification_monitor(app))


async def stop_notification_monitor(app: FastAPI) -> None:
    state = _state(app)
    task = state.notification_task
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    state.notification_task = None


async def _notification_monitor(app: FastAPI) -> None:
    while True:
        try:
            if _load_desktop_notifications_enabled():
                snapshots = refresh_snapshots(app)
                for item in _completed_transitions(app, snapshots):
                    _show_desktop_notification(item["title"], item["body"])
        except Exception as exc:
            print(f"[agent-console-notify] error: {exc}")
        await asyncio.sleep(NOTIFICATION_POLL_SECONDS)


def _completed_transitions(app: FastAPI, snapshots: list[HostSnapshot]) -> list[dict[str, str]]:
    state = _state(app)
    current: dict[str, tuple[str, HostSnapshot, Any]] = {}
    for host in snapshots:
        for session in host.sessions:
            current[session.key] = (session.status or "", host, session)

    completed: list[dict[str, str]] = []
    for key, (status, host, session) in current.items():
        previous = state.known_session_statuses.get(key)
        if (
            previous
            and previous != COMPLETED_STATUS
            and status == COMPLETED_STATUS
            and key not in state.notified_completed_keys
        ):
            state.notified_completed_keys.add(key)
            title = "Agent task completed"
            project = session.project_name or session.cwd or session.session_id or session.key
            body = f"{host.host_label or host.host_id} | {project}"
            completed.append({"title": title, "body": body})

    state.known_session_statuses = {key: value[0] for key, value in current.items()}
    return completed


def _show_desktop_notification(title: str, body: str) -> None:
    if platform.system() == "Windows":
        balloon_script = "\n".join(
            [
                "Add-Type -AssemblyName System.Windows.Forms",
                "Add-Type -AssemblyName System.Drawing",
                "$n = New-Object System.Windows.Forms.NotifyIcon",
                "$n.Icon = [System.Drawing.SystemIcons]::Information",
                f"$n.BalloonTipTitle = {_powershell_single_quote(title)}",
                f"$n.BalloonTipText = {_powershell_single_quote(body[:240])}",
                "$n.Visible = $true",
                "$n.ShowBalloonTip(8000)",
                "Start-Sleep -Seconds 9",
                "$n.Dispose()",
            ]
        )
        subprocess.Popen(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", balloon_script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        popup_script = (
            "$shell = New-Object -ComObject WScript.Shell; "
            f"$null = $shell.Popup({_powershell_single_quote(body[:500])}, 12, {_powershell_single_quote(title)}, 64)"
        )
        encoded_popup = base64.b64encode(popup_script.encode("utf-16le")).decode("ascii")
        subprocess.Popen(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-EncodedCommand", encoded_popup],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return

    if shutil.which("notify-send"):
        subprocess.Popen(["notify-send", title, body], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        client = _state(app).codex_app_server
        if client is not None:
            client.close()

    app = FastAPI(title="CliDeck", lifespan=lifespan)
    app.state.agent_console = SnapshotState()

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="agent-console-static")

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        if INDEX_FILE.exists():
            return HTMLResponse(INDEX_FILE.read_text(encoding="utf-8"))
        return HTMLResponse(
            "<!doctype html><html><head><title>CliDeck</title></head>"
            "<body><h1>CliDeck</h1></body></html>"
        )

    @app.get("/sessions/{key:path}", response_class=HTMLResponse)
    def session_page(key: str) -> HTMLResponse:
        return index()

    @app.get("/api/snapshot")
    def api_snapshot(refresh: bool = False) -> dict:
        hosts = refresh_snapshots(app) if refresh else _snapshots(app, collect_if_empty=True)
        return {
            "hosts": [host.to_dict() for host in hosts],
            "counts": _counts(hosts),
            "last_refresh_ms": _state(app).last_refresh_ms,
        }

    @app.get("/api/hosts")
    def api_hosts() -> dict:
        return {"hosts": [_host_metadata(host) for host in load_hosts()]}

    @app.get("/api/vscode-hosts")
    def api_vscode_hosts() -> dict:
        configured = {host.ssh for host in load_hosts() if host.ssh}
        candidates = []
        for row in discover_ssh_hosts():
            candidate = dict(row)
            candidate["configured"] = candidate.get("ssh") in configured
            candidates.append(candidate)
        return {"hosts": candidates}

    @app.get("/api/settings")
    def api_settings() -> dict:
        return {"desktop_notifications_enabled": _load_desktop_notifications_enabled()}

    @app.post("/api/settings")
    def api_save_settings(payload: dict[str, Any] = Body(...)) -> dict:
        enabled = payload.get("desktop_notifications_enabled")
        if not isinstance(enabled, bool):
            raise HTTPException(status_code=400, detail="desktop_notifications_enabled must be a boolean")
        _save_desktop_notifications_enabled(enabled)
        return {"desktop_notifications_enabled": enabled}

    @app.post("/api/notifications/test")
    def api_test_notification() -> dict:
        _show_desktop_notification("CliDeck test", "Desktop notifications are working.")
        return {"sent": True}

    @app.post("/api/hosts")
    def api_save_host(payload: dict[str, Any] = Body(...)) -> dict:
        host = _host_from_payload(payload)
        host = _preserve_existing_password(host)
        hosts = save_host(host)
        _state(app).snapshots = []
        _state(app).last_refresh_ms = None
        return {
            "host": _host_metadata(host),
            "hosts": [_host_metadata(row) for row in hosts],
        }

    @app.delete("/api/hosts/{host_id}")
    def api_delete_host(host_id: str) -> dict:
        try:
            hosts = delete_host(host_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _state(app).snapshots = []
        _state(app).last_refresh_ms = None
        return {"hosts": [_host_metadata(row) for row in hosts]}

    @app.post("/api/hosts/{host_id}/screen-session")
    def api_start_screen_session(host_id: str, payload: dict[str, Any] = Body(...)) -> dict:
        host_config = _host_config_by_id(host_id)
        if host_config is None:
            raise HTTPException(status_code=404, detail="host not found")
        if not host_config.enable_actions:
            raise HTTPException(status_code=403, detail="session actions are disabled for this host")
        if host_config.type != "ssh" or not host_config.ssh:
            raise HTTPException(status_code=409, detail="screen sessions can only be created on SSH hosts")
        cwd = _payload_optional_text(payload, "cwd") or "~"
        screen_name = _payload_optional_text(payload, "screen_name")
        result, error = start_ssh_screen_session(
            host_config.ssh,
            cwd=cwd,
            screen_name=screen_name,
            command="codex",
            password=host_config.password,
            timeout_seconds=host_config.command_timeout_seconds,
        )
        if error:
            raise HTTPException(status_code=502, detail=error)
        _state(app).snapshots = []
        _state(app).last_refresh_ms = None
        return {"started": True, "host": _host_metadata(host_config), "screen": result}

    @app.get("/api/search")
    def api_search(q: str = "") -> dict:
        query = q.strip()
        if not query:
            return {"q": q, "matches": []}
        matches = _search_sessions(_snapshots(app, collect_if_empty=True), query)
        return {"q": q, "matches": matches}

    @app.get("/api/sessions/{key}/timeline")
    def api_session_timeline(key: str, limit: int = MAX_TIMELINE_EVENTS, before: int | None = None) -> dict:
        session, host = _find_session_or_404(app, key)
        bounded_limit = max(0, min(limit, MAX_TIMELINE_EVENTS))
        bounded_before = max(0, before) if before is not None else None
        host_config = _host_config_by_id(host.host_id)
        timeline, error, next_before, has_more = _read_timeline(
            session.transcript_path,
            bounded_limit,
            host_config,
            before=bounded_before,
            allow_missing=session.source == "screen" and not session.transcript_path,
        )
        terminal_capture, terminal_error = _read_screen_capture(session, host_config)
        return {
            "session": session.to_dict(),
            "host": _snapshot_host_context(host),
            "timeline": timeline,
            "error": error,
            "next_before": next_before,
            "has_more": has_more,
            "terminal_capture": terminal_capture,
            "terminal_error": terminal_error,
        }

    @app.post("/api/sessions/{key}/resume-command")
    def api_resume_command(key: str) -> dict:
        session, host = _find_session_or_404(app, key)
        return {
            "session": session.to_dict(),
            "host": _snapshot_host_context(host),
            "command": session.resume_command,
        }

    @app.post("/api/sessions/{key}/kill")
    def api_kill(key: str) -> dict:
        session, _host = _find_session_or_404(app, key)
        host_config = _host_config_by_id(session.host_id)
        if host_config is None or not host_config.enable_actions:
            raise HTTPException(status_code=403, detail="session actions are disabled for this host")
        raise HTTPException(status_code=501, detail="kill action is not implemented")

    @app.post("/api/sessions/{key}/screen-input")
    def api_screen_input(key: str, payload: dict[str, Any] = Body(...)) -> dict:
        session, host = _find_session_or_404(app, key)
        host_config = _host_config_by_id(host.host_id)
        if host_config is None or not host_config.enable_actions:
            raise HTTPException(status_code=403, detail="session actions are disabled for this host")
        if host_config.type != "ssh" or not host_config.ssh:
            raise HTTPException(status_code=409, detail="screen input is only available for SSH hosts")
        if not session.screen_session:
            raise HTTPException(status_code=409, detail="session is not attached to a screen session")
        text = _screen_input_text(payload)
        error = send_ssh_screen_input(
            host_config.ssh,
            session.screen_session,
            text,
            password=host_config.password,
            timeout_seconds=host_config.command_timeout_seconds,
            enter=True,
        )
        if error:
            raise HTTPException(status_code=502, detail=error)
        return {
            "sent": True,
            "session": session.to_dict(),
            "host": _snapshot_host_context(host),
            "screen_session": session.screen_session,
        }

    @app.post("/api/sessions/{key}/screen-key")
    def api_screen_key(key: str, payload: dict[str, Any] = Body(...)) -> dict:
        session, host = _find_session_or_404(app, key)
        host_config = _host_config_by_id(host.host_id)
        if host_config is None or not host_config.enable_actions:
            raise HTTPException(status_code=403, detail="session actions are disabled for this host")
        if host_config.type != "ssh" or not host_config.ssh:
            raise HTTPException(status_code=409, detail="screen key input is only available for SSH hosts")
        if not session.screen_session:
            raise HTTPException(status_code=409, detail="session is not attached to a screen session")
        text = _screen_key_text(payload)
        error = send_ssh_screen_input(
            host_config.ssh,
            session.screen_session,
            text,
            password=host_config.password,
            timeout_seconds=host_config.command_timeout_seconds,
            enter=False,
        )
        if error:
            raise HTTPException(status_code=502, detail=error)
        return {
            "sent": True,
            "session": session.to_dict(),
            "host": _snapshot_host_context(host),
            "screen_session": session.screen_session,
            "action": payload.get("action"),
        }

    @app.post("/api/sessions/{key}/local-input")
    def api_local_input(key: str, payload: dict[str, Any] = Body(...)) -> dict:
        session, host = _find_session_or_404(app, key)
        if host.host_id != "local":
            raise HTTPException(status_code=409, detail="local input is only available for the local host")
        if not session.tmux_pane:
            raise HTTPException(status_code=409, detail="session is not attached to a local tmux pane")
        text = _screen_input_text(payload)
        error = _send_local_tmux_input(session.tmux_pane, text, enter=True)
        if error:
            raise HTTPException(status_code=502, detail=error)
        return {
            "sent": True,
            "session": session.to_dict(),
            "host": _snapshot_host_context(host),
            "tmux_pane": session.tmux_pane,
        }

    @app.post("/api/sessions/{key}/local-codex-prompt")
    def api_local_codex_prompt(key: str, payload: dict[str, Any] = Body(...)) -> dict:
        session, host = _find_session_or_404(app, key)
        if host.host_id != "local":
            raise HTTPException(status_code=409, detail="local Codex prompt is only available for the local host")
        if session.platform != "codex":
            raise HTTPException(status_code=409, detail="local Codex prompt is only available for Codex sessions")
        if not session.session_id:
            raise HTTPException(status_code=409, detail="session has no Codex session id")
        text = _screen_input_text(payload)
        result, error = _send_local_codex_app_server_prompt(
            app,
            session.session_id,
            text,
            cwd=session.cwd,
            path=session.transcript_path,
        )
        if error:
            result, error = _send_local_codex_prompt(session.session_id, text, cwd=session.cwd)
            if error:
                raise HTTPException(status_code=502, detail=error)
            _state(app).snapshots = []
            _state(app).last_refresh_ms = None
            return {
                "sent": True,
                "mode": "exec-resume",
                "session": session.to_dict(),
                "host": _snapshot_host_context(host),
                "stdout": result.stdout[-4000:],
                "stderr": result.stderr[-4000:],
            }
        _state(app).snapshots = []
        _state(app).last_refresh_ms = None
        return {
            "sent": True,
            "mode": result.mode,
            "turn_id": result.turn_id,
            "session": session.to_dict(),
            "host": _snapshot_host_context(host),
        }

    @app.get("/api/sessions/{key}/approvals")
    def api_session_approvals(key: str) -> dict:
        session, host = _find_session_or_404(app, key)
        if host.host_id != "local":
            raise HTTPException(status_code=409, detail="app-server approvals are only available for local Codex")
        if session.platform != "codex" or not session.session_id:
            raise HTTPException(status_code=409, detail="session has no local Codex session id")
        client = _state(app).codex_app_server
        approvals = client.pending_approvals(session.session_id) if client is not None else []
        return {
            "session": session.to_dict(),
            "host": _snapshot_host_context(host),
            "approvals": approvals,
        }

    @app.post("/api/sessions/{key}/approvals/{approval_id}")
    def api_resolve_session_approval(
        key: str,
        approval_id: str,
        payload: dict[str, Any] = Body(...),
    ) -> dict:
        session, host = _find_session_or_404(app, key)
        if host.host_id != "local":
            raise HTTPException(status_code=409, detail="app-server approvals are only available for local Codex")
        if session.platform != "codex" or not session.session_id:
            raise HTTPException(status_code=409, detail="session has no local Codex session id")
        client = _state(app).codex_app_server
        if client is None:
            raise HTTPException(status_code=404, detail="no app-server approval requests are pending")
        decision = _approval_decision(payload)
        content = payload.get("content") if isinstance(payload.get("content"), dict) else None
        if not any(row.get("id") == approval_id for row in client.pending_approvals(session.session_id)):
            raise HTTPException(status_code=404, detail="approval request is not pending for this session")
        try:
            result = client.resolve_approval(approval_id, decision, content=content)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            **result,
            "session": session.to_dict(),
            "host": _snapshot_host_context(host),
        }

    @app.post("/api/sessions/{key}/local-resume")
    def api_local_resume(key: str) -> dict:
        session, host = _find_session_or_404(app, key)
        if host.host_id != "local":
            raise HTTPException(status_code=409, detail="local resume is only available for the local host")
        if not session.resume_command:
            raise HTTPException(status_code=409, detail="session has no resume command")
        error = _launch_local_resume(session.resume_command)
        if error:
            raise HTTPException(status_code=502, detail=error)
        return {
            "launched": True,
            "session": session.to_dict(),
            "host": _snapshot_host_context(host),
            "command": session.resume_command,
        }

    @app.post("/api/sessions/{key}/screen-assignment")
    def api_screen_assignment(key: str, payload: dict[str, Any] = Body(...)) -> dict:
        session, host = _find_session_or_404(app, key)
        screen_session = _screen_assignment_value(payload)
        if screen_session and not _host_has_screen(host, screen_session):
            raise HTTPException(status_code=400, detail="screen_session is not available on this host")
        _save_screen_assignment(key, screen_session)
        session.screen_session = screen_session
        return {
            "session": session.to_dict(),
            "host": _snapshot_host_context(host),
            "screen_session": screen_session,
        }

    return app


def _state(app: FastAPI) -> SnapshotState:
    state = getattr(app.state, "agent_console", None)
    if isinstance(state, SnapshotState):
        return state
    state = SnapshotState()
    app.state.agent_console = state
    return state


def _snapshots(app: FastAPI, *, collect_if_empty: bool) -> list[HostSnapshot]:
    state = _state(app)
    if collect_if_empty and not state.snapshots:
        return refresh_snapshots(app)
    return state.snapshots


def _host_metadata(host: HostConfig) -> dict[str, Any]:
    return {
        "id": host.id,
        "label": host.label,
        "type": host.type,
        "ssh": host.ssh,
        "root": host.root,
        "has_password": bool(host.password),
        "enable_actions": host.enable_actions,
        "poll_interval_seconds": host.poll_interval_seconds,
        "connect_timeout_seconds": host.connect_timeout_seconds,
        "command_timeout_seconds": host.command_timeout_seconds,
    }


def _host_from_payload(payload: dict[str, Any]) -> HostConfig:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="request body must be an object")
    host_id = _payload_text(payload, "id")
    label = _payload_text(payload, "label", host_id)
    ssh_target = _payload_text(payload, "ssh")
    return HostConfig(
        id=host_id,
        label=label,
        type="ssh",
        ssh=ssh_target,
        password=_payload_optional_text(payload, "password"),
        root=_payload_optional_text(payload, "root"),
        poll_interval_seconds=_payload_positive_int(payload, "poll_interval_seconds", 10),
        connect_timeout_seconds=_payload_positive_int(payload, "connect_timeout_seconds", 5),
        command_timeout_seconds=_payload_positive_int(payload, "command_timeout_seconds", 15),
        enable_actions=_payload_bool(payload, "enable_actions", False),
    )


def _preserve_existing_password(host: HostConfig) -> HostConfig:
    if host.password:
        return host
    for existing in load_hosts():
        if existing.id == host.id and existing.password:
            return HostConfig(
                id=host.id,
                label=host.label,
                type=host.type,
                ssh=host.ssh,
                password=existing.password,
                root=host.root,
                poll_interval_seconds=host.poll_interval_seconds,
                connect_timeout_seconds=host.connect_timeout_seconds,
                command_timeout_seconds=host.command_timeout_seconds,
                enable_actions=host.enable_actions,
            )
    return host


def _payload_text(payload: dict[str, Any], field: str, default: str | None = None) -> str:
    value = payload.get(field, default)
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(status_code=400, detail=f"{field} must be a non-empty string")
    return value.strip()


def _payload_optional_text(payload: dict[str, Any], field: str) -> str | None:
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{field} must be a string")
    return value


def _payload_positive_int(payload: dict[str, Any], field: str, default: int) -> int:
    value = payload.get(field, default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise HTTPException(status_code=400, detail=f"{field} must be a positive integer")
    return value


def _payload_bool(payload: dict[str, Any], field: str, default: bool) -> bool:
    value = payload.get(field, default)
    if not isinstance(value, bool):
        raise HTTPException(status_code=400, detail=f"{field} must be a boolean")
    return value


def _screen_input_text(payload: dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="request body must be an object")
    value = payload.get("text")
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(status_code=400, detail="text must be a non-empty string")
    if len(value) > 8000:
        raise HTTPException(status_code=400, detail="text must be at most 8000 characters")
    return value


def _screen_key_text(payload: dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="request body must be an object")
    action = payload.get("action")
    if not isinstance(action, str):
        raise HTTPException(status_code=400, detail="action must be a string")
    mapping = {
        "enter": "\r",
        "escape": "\x1b",
        "up": "\x1b[A",
        "down": "\x1b[B",
        "left": "\x1b[D",
        "right": "\x1b[C",
        "ctrl_c": "\x03",
        "accept": "\r",
        "acceptForSession": "\x1b[B\r",
        "decline": "\x1b[B\x1b[B\r",
        "cancel": "\x03",
    }
    if action not in mapping:
        raise HTTPException(status_code=400, detail="unsupported screen key action")
    return mapping[action]


def _approval_decision(payload: dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="request body must be an object")
    decision = payload.get("decision")
    if decision not in {"accept", "acceptForSession", "decline", "cancel"}:
        raise HTTPException(status_code=400, detail="unsupported approval decision")
    return decision


def _screen_assignment_value(payload: dict[str, Any]) -> str | None:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="request body must be an object")
    value = payload.get("screen_session")
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(status_code=400, detail="screen_session must be a string")
    return value.strip()


def _send_local_tmux_input(tmux_pane: str, text: str, *, enter: bool = True) -> str | None:
    payload = text + ("\n" if enter else "")
    try:
        load = subprocess.run(
            ["tmux", "load-buffer", "-"],
            input=payload,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if load.returncode != 0:
            return (load.stderr or load.stdout or "tmux load-buffer failed").strip()
        paste = subprocess.run(
            ["tmux", "paste-buffer", "-t", tmux_pane],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if paste.returncode != 0:
            return (paste.stderr or paste.stdout or "tmux paste-buffer failed").strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc)
    return None


def _send_local_codex_app_server_prompt(
    app: FastAPI,
    session_id: str,
    text: str,
    *,
    cwd: str | None = None,
    path: str | None = None,
) -> tuple[AppServerResult | None, str | None]:
    try:
        return _codex_app_server(app).start_turn(
            session_id,
            text,
            cwd=cwd if cwd and Path(cwd).exists() else None,
            path=path if path and Path(path).exists() else None,
        ), None
    except Exception as exc:
        return None, str(exc)


def _codex_app_server(app: FastAPI) -> CodexAppServerClient:
    state = _state(app)
    if state.codex_app_server is None:
        state.codex_app_server = CodexAppServerClient()
    return state.codex_app_server


def _send_local_codex_prompt(
    session_id: str,
    text: str,
    *,
    cwd: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], str | None]:
    codex = shutil.which("codex.cmd") or shutil.which("codex.exe") or shutil.which("codex")
    if not codex:
        empty = subprocess.CompletedProcess(args=["codex"], returncode=127, stdout="", stderr="")
        return empty, "codex CLI was not found on PATH"
    workdir = cwd if cwd and Path(cwd).exists() else None
    command = [
        codex,
        "exec",
        "resume",
        "--skip-git-repo-check",
        session_id,
        "-",
    ]
    try:
        raw_result = subprocess.run(
            command,
            input=text.encode("utf-8"),
            capture_output=True,
            cwd=workdir,
            timeout=LOCAL_CODEX_EXEC_TIMEOUT_SECONDS,
        )
        result = subprocess.CompletedProcess(
            args=raw_result.args,
            returncode=raw_result.returncode,
            stdout=_decode_process_output(raw_result.stdout),
            stderr=_decode_process_output(raw_result.stderr),
        )
    except subprocess.TimeoutExpired as exc:
        empty = subprocess.CompletedProcess(
            args=command,
            returncode=124,
            stdout=_decode_process_output(exc.stdout),
            stderr=_decode_process_output(exc.stderr),
        )
        return empty, f"codex exec resume timed out after {LOCAL_CODEX_EXEC_TIMEOUT_SECONDS} seconds"
    except OSError as exc:
        empty = subprocess.CompletedProcess(args=command, returncode=1, stdout="", stderr="")
        return empty, str(exc)
    if result.returncode != 0:
        return result, (result.stderr or result.stdout or "codex exec resume failed").strip()
    return result, None


def _decode_process_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.decode("utf-8", errors="replace")


def _launch_local_resume(command: str) -> str | None:
    try:
        if os.name == "nt":
            subprocess.Popen(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "Start-Process powershell -ArgumentList @('-NoExit','-Command',"
                    f"{_powershell_single_quote(command)})",
                ]
            )
        else:
            shell_command = shlex.quote(command)
            subprocess.Popen(["sh", "-lc", f"x-terminal-emulator -e sh -lc {shell_command} >/dev/null 2>&1 &"])
    except OSError as exc:
        return str(exc)
    return None


def _powershell_single_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _host_has_screen(host: HostSnapshot, screen_session: str) -> bool:
    return any(row.get("screen_session") == screen_session for row in host.screen_sessions)


def _load_console_state(path: Path | None = None) -> dict[str, Any]:
    state_path = SCREEN_ASSIGNMENTS_FILE if path is None else path
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_console_state(data: dict[str, Any], path: Path | None = None) -> None:
    state_path = SCREEN_ASSIGNMENTS_FILE if path is None else path
    state_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def _load_screen_assignments(path: Path | None = None) -> dict[str, str]:
    data = _load_console_state(path)
    assignments = data.get("screen_assignments")
    if not isinstance(assignments, dict):
        return {}
    return {key: value for key, value in assignments.items() if isinstance(key, str) and isinstance(value, str)}


def _save_screen_assignment(key: str, screen_session: str | None, path: Path | None = None) -> None:
    state_path = SCREEN_ASSIGNMENTS_FILE if path is None else path
    payload = _load_console_state(state_path)
    assignments = _load_screen_assignments(state_path)
    if screen_session:
        assignments[key] = screen_session
    else:
        assignments.pop(key, None)
    payload["screen_assignments"] = assignments
    _write_console_state(payload, state_path)


def _load_desktop_notifications_enabled(path: Path | None = None) -> bool:
    return _load_console_state(path).get("desktop_notifications_enabled") is True


def _save_desktop_notifications_enabled(enabled: bool, path: Path | None = None) -> None:
    payload = _load_console_state(path)
    payload["desktop_notifications_enabled"] = enabled
    _write_console_state(payload, path)


def _apply_screen_assignments(hosts: list[HostSnapshot]) -> None:
    assignments = _load_screen_assignments()
    for host in hosts:
        available = {row.get("screen_session") for row in host.screen_sessions}
        if assignments:
            for session in host.sessions:
                assigned = assignments.get(session.key)
                if assigned and assigned in available:
                    session.screen_session = assigned
        occupied = {
            session.screen_session
            for session in host.sessions
            if session.screen_session and not (session.platform == "screen" and session.source == "screen")
        }
        if occupied:
            host.sessions = [
                session
                for session in host.sessions
                if not (
                    session.platform == "screen"
                    and session.source == "screen"
                    and session.screen_session in occupied
                )
            ]


def _snapshot_host_context(host: HostSnapshot) -> dict[str, Any]:
    return {
        "id": host.host_id,
        "label": host.host_label,
    }


def _host_config_by_id(host_id: str) -> HostConfig | None:
    for host in load_hosts():
        if host.id == host_id:
            return host
    return None


def _find_session_or_404(app: FastAPI, key: str):
    for host in _snapshots(app, collect_if_empty=True):
        for session in host.sessions:
            if session.key == key:
                return session, host
    raise HTTPException(status_code=404, detail="session not found")


def _search_sessions(hosts: list[HostSnapshot], query: str) -> list[dict[str, Any]]:
    needle = query.casefold()
    matches: list[dict[str, Any]] = []
    for host in hosts:
        host_context = _snapshot_host_context(host)
        for session in host.sessions:
            haystack = _session_search_text(session, host)
            if needle in haystack.casefold():
                matches.append(
                    {
                        "host": host_context,
                        "session": session.to_dict(),
                    }
                )
    return matches


def _session_search_text(session, host: HostSnapshot) -> str:
    fields: list[Any] = [
        host.host_id,
        host.host_label,
        session.key,
        session.host_id,
        session.platform,
        session.source,
        session.status,
        session.confidence,
        session.session_id,
        session.cwd,
        session.project_name,
        session.last_event,
        session.last_prompt,
        session.last_response,
        session.model,
        session.tty,
        session.tmux_session,
        session.tmux_window,
        session.tmux_pane,
        session.screen_session,
        session.transcript_path,
        session.resume_command,
    ]
    fields.extend(session.evidence)
    return "\n".join(str(value) for value in fields if value is not None)


def _read_timeline(
    transcript_path: str | None,
    limit: int,
    host_config: HostConfig | None = None,
    *,
    before: int | None = None,
    allow_missing: bool = False,
) -> tuple[list[dict[str, Any]], str | None, int | None, bool]:
    if not transcript_path:
        return [], None if allow_missing else "session has no transcript path", None, False
    if limit <= 0:
        return [], None, before, bool(before)
    path = Path(transcript_path)
    if host_config and host_config.type == "ssh" and host_config.ssh:
        return read_ssh_timeline(
            host_config.ssh,
            transcript_path,
            password=host_config.password,
            timeout_seconds=host_config.command_timeout_seconds,
            limit=limit,
            before=before,
        )
    if not path.is_absolute() or not path.is_file():
        return [], "transcript is not locally readable", None, False

    events: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict) and row.get("type") != "session_meta":
                    events.append(row)
    except OSError as exc:
        return [], f"transcript is not locally readable: {exc}", None, False
    end = min(max(0, before), len(events)) if before is not None else len(events)
    start = max(0, end - limit)
    return events[start:end], None, start, start > 0


def _read_screen_capture(session, host_config: HostConfig | None) -> tuple[str, str | None]:
    if not session.screen_session:
        return "", None
    if host_config is None or host_config.type != "ssh" or not host_config.ssh:
        return "", None
    return read_ssh_screen_capture(
        host_config.ssh,
        session.screen_session,
        password=host_config.password,
        timeout_seconds=host_config.command_timeout_seconds,
        limit=160,
    )


def _counts(hosts: list[HostSnapshot]) -> dict[str, int]:
    counts = {
        "total": 0,
        "running": 0,
        "waiting": 0,
        "idle": 0,
        "stale": 0,
        "completed": 0,
    }
    for host in hosts:
        for session in host.sessions:
            counts["total"] += 1
            if session.status in counts:
                counts[session.status] += 1
    return counts


def _error_snapshot(host: HostConfig, exc: Exception) -> HostSnapshot:
    return HostSnapshot(
        host_id=host.id,
        host_label=host.label,
        collected_at_ms=now_ms(),
        sessions=[],
        errors=[
            CollectorError(
                host_id=host.id,
                kind=exc.__class__.__name__,
                message=str(exc),
            )
        ],
    )
