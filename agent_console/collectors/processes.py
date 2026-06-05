from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass


@dataclass
class ProcessInfo:
    pid: int
    tty: str | None = None


def pid_alive(pid: int | None) -> bool:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, SystemError):
        return False
    return True


def pid_tty(pid: int | None) -> str | None:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return None
    try:
        result = subprocess.run(
            ["ps", "-o", "tty=", "-p", str(pid)],
            capture_output=True,
            check=False,
            text=True,
            timeout=2,
        )
    except (OSError, SystemError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return normalize_tty(result.stdout.strip())


def normalize_tty(value: str | None) -> str | None:
    if not value:
        return None
    tty = value.strip()
    if not tty or tty == "?":
        return None
    if tty.startswith("/dev/"):
        return tty
    return f"/dev/{tty}"
