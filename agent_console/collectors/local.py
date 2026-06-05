from __future__ import annotations

from pathlib import Path

from agent_console.collectors.claude import discover_claude_sessions
from agent_console.collectors.codex import discover_codex_sessions
from agent_console.collectors.tmux import list_tmux_panes
from agent_console.models import CollectorError, HostSnapshot, HostSession, now_ms


def collect_local_snapshot(
    host_id: str,
    host_label: str,
    home: Path | None = None,
) -> HostSnapshot:
    root = Path.home() if home is None else home
    sessions: list[HostSession] = []
    errors: list[CollectorError] = []
    for name, collector in (("claude", discover_claude_sessions), ("codex", discover_codex_sessions)):
        try:
            sessions.extend(collector(root, host_id))
        except Exception as exc:
            errors.append(
                CollectorError(
                    host_id=host_id,
                    kind=f"{name}_collector_{exc.__class__.__name__}",
                    message=str(exc),
                )
            )
    try:
        _attach_tmux_metadata(sessions)
    except Exception as exc:
        errors.append(
            CollectorError(
                host_id=host_id,
                kind=f"tmux_collector_{exc.__class__.__name__}",
                message=str(exc),
            )
        )
    sessions.sort(key=lambda item: item.transcript_mtime_ms or 0, reverse=True)
    return HostSnapshot(
        host_id=host_id,
        host_label=host_label,
        collected_at_ms=now_ms(),
        sessions=sessions,
        errors=errors,
    )


def _attach_tmux_metadata(sessions: list[HostSession]) -> None:
    panes = list_tmux_panes()
    for session in sessions:
        if not session.tty:
            continue
        pane = panes.get(session.tty)
        if pane is None:
            continue
        session.tmux_session = pane.session
        session.tmux_window = pane.window
        session.tmux_pane = pane.pane
