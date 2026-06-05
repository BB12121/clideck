from __future__ import annotations

import subprocess
from dataclasses import dataclass

from agent_console.collectors.processes import normalize_tty


@dataclass
class TmuxPane:
    tty: str
    session: str
    window: str
    pane: str


def parse_tmux_panes(raw: str) -> dict[str, TmuxPane]:
    panes: dict[str, TmuxPane] = {}
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        tty = normalize_tty(parts[0])
        if not tty:
            continue
        panes[tty] = TmuxPane(
            tty=tty,
            session=parts[1],
            window=parts[2],
            pane=parts[-1],
        )
    return panes


def list_tmux_panes() -> dict[str, TmuxPane]:
    try:
        result = subprocess.run(
            [
                "tmux",
                "list-panes",
                "-a",
                "-F",
                "#{pane_tty}\t#{session_name}\t#{window_index}\t#{pane_id}",
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if result.returncode != 0:
        return {}
    return parse_tmux_panes(result.stdout)
