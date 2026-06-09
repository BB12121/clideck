from __future__ import annotations

import base64
import json
import re
import subprocess
from typing import Any

from agent_console.models import CollectorError, HostSession, HostSnapshot, now_ms


CODEX_FULL_ACCESS_COMMAND = "codex --dangerously-bypass-approvals-and-sandbox"
DEFAULT_REMOTE_CODEX_START_PROMPT = "你好"


def _text_to_utf8_b64(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.encode("utf-16", errors="surrogatepass").decode("utf-16", errors="replace")
    return base64.b64encode(normalized.encode("utf-8")).decode("ascii")


REMOTE_PROBE = r"""
import base64
import glob
import hashlib
import json
import os
import shlex
import stat
import subprocess
import time
from pathlib import PurePosixPath

HOST_ID = __HOST_ID_JSON__
HOST_LABEL = __HOST_LABEL_JSON__
MAX_CODEX_FILES = 200


def hash_token(value):
    digest = hashlib.sha256(value.encode("utf-8", errors="replace")).digest()[:18]
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def scope_slug(value):
    chars = []
    for char in value:
        if ("A" <= char <= "Z") or ("a" <= char <= "z") or ("0" <= char <= "9") or char in "-_":
            chars.append(char)
        else:
            chars.append("-")
    slug = "".join(chars).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "unknown"


def build_key(host_id, platform, kind, value):
    scope = "%s-%s" % (scope_slug(host_id), scope_slug(platform))
    seed = "%s\0%s\0%s\0%s" % (host_id, platform, kind, value or "")
    return "%s-%s-%s" % (scope, kind, hash_token(seed))


def first_text(data, *keys):
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def int_value(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def content_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = [part for item in value for part in [content_text(item)] if part]
        return "\n".join(parts) if parts else None
    if isinstance(value, dict):
        for key in ("text", "content", "message"):
            text = content_text(value.get(key))
            if text:
                return text
    return None


def project_name(cwd):
    if not cwd:
        return None
    return PurePosixPath(cwd).name or None


def mtime_ms(path):
    try:
        return int(os.stat(path).st_mtime * 1000)
    except OSError:
        return None


def pid_alive(pid):
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def iter_jsonl(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    yield row
    except OSError:
        return


def payload(row):
    value = row.get("payload")
    return value if isinstance(value, dict) else row


def response_item(data):
    item = data.get("item")
    return item if isinstance(item, dict) else data


def render_codex_resume(cwd, session_id):
    if not cwd or not session_id:
        return None
    return "cd %s && codex resume %s" % (shlex.quote(cwd), shlex.quote(session_id))


def render_claude_resume(cwd, session_id):
    if not session_id:
        return None
    command = "claude --resume %s" % shlex.quote(session_id)
    if cwd:
        return "cd %s && %s" % (shlex.quote(cwd), command)
    return command


def cwd_slug(cwd):
    return cwd.replace("/", "-").replace("_", "-").replace(".", "-")


def discover_claude(home):
    root = os.path.join(home, ".claude", "sessions")
    if not os.path.isdir(root):
        return []
    sessions = []
    for path in sorted(glob.glob(os.path.join(root, "*.json"))):
        name = os.path.basename(path)
        if name.startswith("session-"):
            continue
        data = read_json(path)
        if not data:
            continue
        session_id = first_text(data, "sessionId", "session_id", "id")
        cwd = first_text(data, "cwd", "workspace")
        if not session_id and not cwd:
            continue
        pid = int_value(data.get("pid"))
        alive = pid_alive(pid)
        transcript = None
        if cwd and session_id:
            transcript = os.path.join(home, ".claude", "projects", cwd_slug(cwd), session_id + ".jsonl")
        evidence = ["claude_session_state"]
        if transcript and os.path.exists(transcript):
            evidence.append("transcript")
        if pid is not None:
            evidence.append("pid_alive" if alive else "pid_dead")
        waiting_for = data.get("waitingFor")
        raw_status = first_text(data, "status")
        if pid is not None and not alive:
            status_value = "completed"
        elif alive and waiting_for:
            status_value = "waiting"
        else:
            status_value = raw_status or ("running" if alive else "completed")
        sessions.append({
            "key": build_key(HOST_ID, "claude", "session", session_id or cwd or path),
            "host_id": HOST_ID,
            "platform": "claude",
            "source": "cli",
            "status": status_value,
            "confidence": "high" if alive else "medium",
            "evidence": evidence,
            "session_id": session_id,
            "cwd": cwd,
            "project_name": project_name(cwd),
            "pid": pid,
            "transcript_path": transcript,
            "transcript_mtime_ms": mtime_ms(transcript) if transcript else None,
            "resume_command": render_claude_resume(cwd, session_id),
        })
    return sessions


TERMINAL_STATUSES = {
    "error": ("waiting", "medium"),
    "task_complete": ("completed", "medium"),
    "turn_aborted": ("completed", "medium"),
}

PERMISSION_WAIT_EVENT = "permission_request"
CODEX_FULL_ACCESS_COMMAND = "codex --dangerously-bypass-approvals-and-sandbox"


def is_permission_request(event_type, data):
    tokens = [event_type or ""]
    for key in ("type", "event", "name", "status", "reason"):
        text = content_text(data.get(key))
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


def classify_codex(decisive_event, age_seconds):
    age_seconds = max(0, age_seconds)
    if decisive_event == PERMISSION_WAIT_EVENT:
        return "waiting", "high"
    if decisive_event in TERMINAL_STATUSES:
        return TERMINAL_STATUSES[decisive_event]
    if decisive_event == "task_started" and age_seconds < 300:
        return "running", "medium"
    if age_seconds < 300:
        return "idle", "low"
    if age_seconds < 86400:
        return "completed", "low"
    return "stale", "low"


def parse_codex(path):
    session_id = None
    source = "cli"
    cwd = None
    model = None
    last_event = None
    decisive_event = None
    last_prompt = None
    last_response = None
    evidence = []

    def add_evidence(value):
        if value not in evidence:
            evidence.append(value)

    for row in iter_jsonl(path):
        row_type = row.get("type") or row.get("kind")
        data = payload(row)
        if row_type == "session_meta":
            session_id = first_text(data, "id", "session_id") or session_id
            cwd = first_text(data, "cwd") or cwd
            source = "codex_vscode" if first_text(data, "originator") == "codex_vscode" else source
            add_evidence("session_meta")
        elif row_type == "turn_context":
            cwd = first_text(data, "cwd") or cwd
            cfg = data.get("cfg") if isinstance(data.get("cfg"), dict) else {}
            model = first_text(data, "model") or first_text(cfg, "model") or model
            add_evidence("turn_context")
        elif row_type == "event_msg":
            event_type = first_text(data, "type", "event", "name")
            if not event_type:
                role = first_text(data, "role")
                event_type = "user_message" if role == "user" else "agent_message" if role == "assistant" else None
            if not event_type:
                continue
            last_event = event_type
            add_evidence(event_type)
            if is_permission_request(event_type, data):
                decisive_event = PERMISSION_WAIT_EVENT
                add_evidence(PERMISSION_WAIT_EVENT)
            elif event_type == "task_started" or event_type in TERMINAL_STATUSES:
                decisive_event = event_type
            text = None
            for key in ("last_agent_message", "codex_error_info", "message", "content", "text"):
                text = content_text(data.get(key))
                if text:
                    break
            if event_type == "user_message":
                last_prompt = text or last_prompt
            elif event_type in ("agent_message", "task_complete", "error") or decisive_event == PERMISSION_WAIT_EVENT:
                last_response = text or last_response
        elif row_type == "response_item":
            item = response_item(data)
            item_type = first_text(item, "type") or "response_item"
            role = first_text(item, "role")
            add_evidence("response_item:" + item_type)
            if is_permission_request(item_type, item):
                last_event = item_type
                decisive_event = PERMISSION_WAIT_EVENT
                add_evidence(PERMISSION_WAIT_EVENT)
            text = content_text(item.get("content")) or content_text(item.get("text") or item.get("message"))
            if role == "user":
                last_prompt = text or last_prompt
            elif role == "assistant" or decisive_event == PERMISSION_WAIT_EVENT:
                last_response = text or last_response
            elif item_type in ("function_call", "tool_call", "local_shell_call"):
                last_event = item_type

    try:
        file_stat = os.stat(path)
    except OSError:
        return None
    status_value, confidence = classify_codex(decisive_event, time.time() - file_stat.st_mtime)
    return {
        "key": build_key(HOST_ID, "codex", "session" if session_id else "transcript", session_id or path),
        "host_id": HOST_ID,
        "platform": "codex",
        "source": source,
        "status": status_value,
        "confidence": confidence,
        "evidence": evidence,
        "session_id": session_id,
        "cwd": cwd,
        "project_name": project_name(cwd),
        "last_event": last_event,
        "last_prompt": last_prompt,
        "last_response": last_response,
        "model": model,
        "transcript_path": path,
        "transcript_mtime_ms": int(file_stat.st_mtime * 1000),
        "resume_command": render_codex_resume(cwd, session_id),
    }


def discover_codex(home):
    root = os.path.join(home, ".codex", "sessions")
    if not os.path.isdir(root):
        return []
    candidates = []
    for path in glob.glob(os.path.join(root, "**", "rollout-*.jsonl"), recursive=True):
        try:
            file_stat = os.stat(path)
        except OSError:
            continue
        if stat.S_ISREG(file_stat.st_mode):
            candidates.append((file_stat.st_mtime, path))
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    sessions = []
    for _, path in candidates[:MAX_CODEX_FILES]:
        session = parse_codex(path)
        if session is not None:
            sessions.append(session)
    return sessions


def process_cwd(pid):
    try:
        return os.readlink("/proc/%s/cwd" % pid)
    except OSError:
        return None


def parse_screen_name(args):
    try:
        parts = shlex.split(args)
    except ValueError:
        parts = args.split()
    for index, part in enumerate(parts):
        if part == "-S" and index + 1 < len(parts):
            return parts[index + 1]
        if part.startswith("-S") and len(part) > 2:
            return part[2:]
    return None


def screen_sockets():
    sockets = {}
    for entry in screen_inventory():
        token = entry.get("screen_session")
        name = entry.get("screen_name")
        if token:
            sockets[token] = token
        if name:
            sockets[name] = token
    return sockets


def screen_inventory():
    try:
        result = subprocess.run(["screen", "-ls"], capture_output=True, text=True, timeout=3)
    except Exception:
        return []
    entries = []
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped or "." not in stripped or "(" not in stripped:
            continue
        token = stripped.split(None, 1)[0]
        if "." not in token:
            continue
        name = token.split(".", 1)[1]
        state = "attached" if "(Attached)" in stripped else "detached" if "(Detached)" in stripped else "screen"
        entries.append({
            "screen_session": token,
            "screen_name": name,
            "screen_state": state,
            "screen_line": stripped,
        })
    return entries


def process_rows():
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid=,ppid=,tty=,comm=,args="],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except Exception:
        return {}
    rows = {}
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 4)
        if len(parts) < 5:
            continue
        pid_text, ppid_text, tty, comm, args = parts
        try:
            pid = int(pid_text)
            ppid = int(ppid_text)
        except ValueError:
            continue
        rows[pid] = {
            "pid": pid,
            "ppid": ppid,
            "tty": None if tty == "?" else tty,
            "comm": comm,
            "args": args,
            "cwd": process_cwd(pid),
        }
    return rows


def screen_for_process(pid, processes, sockets):
    seen = set()
    current = processes.get(pid)
    while current and current["pid"] not in seen:
        seen.add(current["pid"])
        comm = current.get("comm") or ""
        args = current.get("args") or ""
        if comm == "SCREEN" or args.startswith("SCREEN ") or " SCREEN " in args:
            name = parse_screen_name(args)
            if name:
                return sockets.get(name, name)
            return sockets.get(str(current["pid"]), str(current["pid"]))
        current = processes.get(current.get("ppid"))
    return None


def agent_processes():
    sockets = screen_sockets()
    if not sockets:
        return []
    processes = process_rows()
    agents = []
    for process in processes.values():
        args = process.get("args") or ""
        command = " %s " % args
        platform = None
        if " codex " in command or command.endswith(" codex"):
            platform = "codex"
        elif " claude " in command or command.endswith(" claude"):
            platform = "claude"
        if not platform:
            continue
        screen = screen_for_process(process["pid"], processes, sockets)
        if not screen:
            continue
        agents.append({
            "platform": platform,
            "pid": process["pid"],
            "ppid": process["ppid"],
            "tty": process["tty"],
            "cwd": process["cwd"],
            "screen_session": screen,
        })
    return agents


def descendants(pid, processes):
    children = []
    frontier = [pid]
    seen = set()
    while frontier:
        parent = frontier.pop(0)
        if parent in seen:
            continue
        seen.add(parent)
        direct = [row for row in processes.values() if row.get("ppid") == parent]
        children.extend(direct)
        frontier.extend(row["pid"] for row in direct)
    return children


def cwd_for_screen(screen_pid, processes):
    for process in descendants(screen_pid, processes):
        if process.get("cwd"):
            return process.get("cwd")
    screen_process = processes.get(screen_pid)
    if screen_process:
        return screen_process.get("cwd")
    return None


def screen_command(screen_pid, processes):
    screen_process = processes.get(screen_pid)
    if not screen_process:
        return None
    args = screen_process.get("args")
    if not args:
        return None
    return args[:260]


def discover_screen_sessions():
    processes = process_rows()
    screens = []
    for entry in screen_inventory():
        token = entry.get("screen_session")
        if not token:
            continue
        try:
            pid = int(token.split(".", 1)[0])
        except ValueError:
            pid = None
        cwd = cwd_for_screen(pid, processes) if pid is not None else None
        name = entry.get("screen_name") or token
        state = entry.get("screen_state") or "screen"
        screens.append({
            "screen_session": token,
            "name": name,
            "state": state,
            "cwd": cwd,
            "project_name": project_name(cwd) or name,
            "command": screen_command(pid, processes) if pid is not None else None,
            "pid": pid,
        })
    return screens


def attach_screen_sessions(sessions):
    agents = agent_processes()
    if not agents:
        return sessions
    ordered = sorted(sessions, key=lambda item: item.get("transcript_mtime_ms") or 0, reverse=True)
    attached = set()
    for agent in agents:
        matched = None
        for session in ordered:
            key = session.get("key")
            if key in attached:
                continue
            if session.get("platform") != agent.get("platform"):
                continue
            if agent.get("cwd") and session.get("cwd") == agent.get("cwd"):
                matched = session
                break
        if matched is None:
            cwd = agent.get("cwd")
            matched = {
                "key": build_key(HOST_ID, agent["platform"], "process", str(agent["pid"])),
                "host_id": HOST_ID,
                "platform": agent["platform"],
                "source": "screen",
                "status": "running",
                "confidence": "high",
                "evidence": ["process", "screen"],
                "cwd": cwd,
                "project_name": project_name(cwd),
            }
            sessions.append(matched)
        else:
            evidence = matched.setdefault("evidence", [])
            if "screen" not in evidence:
                evidence.append("screen")
        matched["pid"] = matched.get("pid") or agent.get("pid")
        matched["ppid"] = matched.get("ppid") or agent.get("ppid")
        matched["tty"] = matched.get("tty") or agent.get("tty")
        matched["screen_session"] = agent.get("screen_session")
        attached.add(matched.get("key"))
    return sessions


def main():
    home = os.path.expanduser("~")
    sessions = []
    screen_sessions = []
    errors = []
    for name, collector in (("claude", discover_claude), ("codex", discover_codex)):
        try:
            sessions.extend(collector(home))
        except Exception as exc:
            errors.append({
                "host_id": HOST_ID,
                "kind": name + "_probe_error",
                "message": str(exc),
            })
    try:
        sessions = attach_screen_sessions(sessions)
        screen_sessions = discover_screen_sessions()
    except Exception as exc:
        errors.append({
            "host_id": HOST_ID,
            "kind": "screen_probe_error",
            "message": str(exc),
        })
    print(json.dumps({
        "host_id": HOST_ID,
        "host_label": HOST_LABEL,
        "collected_at_ms": int(time.time() * 1000),
        "sessions": sessions,
        "screen_sessions": screen_sessions,
        "errors": errors,
    }))


main()
"""


REMOTE_TIMELINE_PROBE = r"""
import json
from pathlib import Path

path = Path(__PATH_JSON__)
limit = __LIMIT_JSON__
before = __BEFORE_JSON__

def main():
    if not path.is_absolute() or not path.is_file():
        print(json.dumps({"timeline": [], "error": "remote transcript is not readable"}))
        return
    rows = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if isinstance(row, dict) and row.get("type") != "session_meta":
                    rows.append(row)
    except OSError as exc:
        print(json.dumps({"timeline": [], "error": str(exc)}))
        return
    end = len(rows) if before is None else min(max(0, before), len(rows))
    start = max(0, end - limit)
    print(json.dumps({
        "timeline": rows[start:end],
        "error": None,
        "next_before": start,
        "has_more": start > 0,
    }))

main()
"""


REMOTE_SCREEN_INPUT_PROBE = r"""
import base64
import json
import os
import subprocess
import tempfile
import time

screen_session = __SCREEN_SESSION_JSON__
text_b64 = __TEXT_B64_JSON__
text = base64.b64decode(text_b64).decode("utf-8", errors="replace") if isinstance(text_b64, str) else ""
submit = __SUBMIT_JSON__

def send_text(session, value):
    fd, path = tempfile.mkstemp(prefix="agent-console-input-", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
        result = subprocess.run(
            ["screen", "-S", session, "-X", "readbuf", path],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return result
        return subprocess.run(
            ["screen", "-S", session, "-X", "paste", "."],
            capture_output=True,
            text=True,
            timeout=5,
        )
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

def main():
    if not isinstance(screen_session, str) or not screen_session.strip():
        print("screen session is required")
        raise SystemExit(2)
    if not isinstance(text, str) or not text:
        print("text is required")
        raise SystemExit(2)
    result = send_text(screen_session, text)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "screen input failed").strip()
        print(detail)
        raise SystemExit(result.returncode)
    if submit:
        time.sleep(0.08)
        result = subprocess.run(
            ["screen", "-S", screen_session, "-X", "stuff", chr(13)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "screen submit failed").strip()
            print(detail)
            raise SystemExit(result.returncode)
    print(json.dumps({"sent": True}))

main()
"""


REMOTE_SCREEN_CAPTURE_PROBE = r"""
import json
import os
import subprocess
import tempfile

screen_session = __SCREEN_SESSION_JSON__
limit = __LIMIT_JSON__

def main():
    if not isinstance(screen_session, str) or not screen_session.strip():
        print(json.dumps({"capture": "", "error": "screen session is required"}))
        return
    fd, path = tempfile.mkstemp(prefix="agent-console-screen-", suffix=".txt")
    os.close(fd)
    try:
        result = subprocess.run(
            ["screen", "-S", screen_session, "-X", "hardcopy", "-h", path],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "screen hardcopy failed").strip()
            print(json.dumps({"capture": "", "error": detail}))
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                lines = handle.read().splitlines()
        except OSError as exc:
            print(json.dumps({"capture": "", "error": str(exc)}))
            return
        if limit > 0:
            lines = lines[-limit:]
        text = "\n".join(line.rstrip() for line in lines).strip()
        print(json.dumps({"capture": text, "error": None}))
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

main()
"""


REMOTE_START_SCREEN_PROBE = r"""
import base64
import json
import os
import re
import subprocess
import tempfile
import time

cwd = __CWD_JSON__
screen_name = __SCREEN_NAME_JSON__
command = __COMMAND_JSON__
initial_prompt_b64 = __INITIAL_PROMPT_B64_JSON__
initial_prompt = (
    base64.b64decode(initial_prompt_b64).decode("utf-8", errors="replace")
    if isinstance(initial_prompt_b64, str)
    else None
)

def slug(value):
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", value or "").strip(".-")
    return text[:80] or time.strftime("codex-%Y%m%d-%H%M%S")

def screen_exists(name):
    try:
        result = subprocess.run(["screen", "-ls"], capture_output=True, text=True, timeout=5)
    except Exception:
        return False
    for line in result.stdout.splitlines():
        parts = line.strip().split()
        if not parts:
            continue
        token = parts[0]
        if token == name or token.endswith("." + name):
            return True
    return False

def wait_for_screen(name, timeout_seconds=6):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if screen_exists(name):
            return True
        time.sleep(0.2)
    return screen_exists(name)

def send_text(session, value):
    fd, path = tempfile.mkstemp(prefix="agent-console-initial-", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
        result = subprocess.run(
            ["screen", "-S", session, "-X", "readbuf", path],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return result
        return subprocess.run(
            ["screen", "-S", session, "-X", "paste", "."],
            capture_output=True,
            text=True,
            timeout=5,
        )
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

def main():
    name = slug(screen_name)
    if not name.startswith("codex") and screen_name is None:
        name = "codex-" + name
    target_cwd = os.path.expanduser(cwd or "~")
    if not os.path.isdir(target_cwd):
        print(json.dumps({"started": False, "error": "cwd does not exist: " + target_cwd}))
        return
    shell_command = "cd " + json.dumps(target_cwd) + " && exec " + command
    result = subprocess.run(
        ["screen", "-dmS", name, "bash", "-lc", shell_command],
        capture_output=True,
        text=True,
        timeout=8,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "screen start failed").strip()
        print(json.dumps({"started": False, "error": detail}))
        return
    if not wait_for_screen(name):
        print(json.dumps({
            "started": False,
            "error": "screen session exited before it was ready; command may have failed: " + shell_command,
        }))
        return
    initial_prompt_sent = False
    if isinstance(initial_prompt, str) and initial_prompt.strip():
        time.sleep(2.0)
        if not screen_exists(name):
            print(json.dumps({"started": False, "error": "screen session disappeared before initial prompt"}))
            return
        result = send_text(name, initial_prompt)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "screen initial prompt failed").strip()
            print(json.dumps({"started": False, "error": detail}))
            return
        if not screen_exists(name):
            print(json.dumps({"started": False, "error": "screen session disappeared before initial submit"}))
            return
        result = subprocess.run(
            ["screen", "-S", name, "-X", "stuff", chr(13)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "screen initial submit failed").strip()
            print(json.dumps({"started": False, "error": detail}))
            return
        initial_prompt_sent = True
    print(json.dumps({
        "started": True,
        "screen_session": name,
        "cwd": target_cwd,
        "command": command,
        "initial_prompt": initial_prompt if initial_prompt_sent else None,
        "initial_prompt_sent": initial_prompt_sent,
    }))

main()
"""


def collect_ssh_snapshot(
    host_id: str,
    host_label: str,
    ssh_target: str,
    password: str | None = None,
    timeout_seconds: int = 15,
) -> HostSnapshot:
    probe = (
        REMOTE_PROBE.replace("__HOST_ID_JSON__", json.dumps(host_id))
        .replace("__HOST_LABEL_JSON__", json.dumps(host_label))
    )
    try:
        result = _run_probe(ssh_target, probe, password, timeout_seconds)
    except Exception as exc:
        return _error_snapshot(host_id, host_label, "ssh_exception", str(exc))

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        message = f"ssh exited with code {result.returncode}"
        if detail:
            message = f"{message}: {detail}"
        return _error_snapshot(host_id, host_label, "ssh_exit", message)

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return _error_snapshot(host_id, host_label, "invalid_json", str(exc))

    if not isinstance(payload, dict):
        return _error_snapshot(host_id, host_label, "invalid_json", "SSH probe returned non-object JSON")

    return HostSnapshot(
        host_id=_text(payload.get("host_id"), host_id),
        host_label=_text(payload.get("host_label"), host_label),
        collected_at_ms=_int(payload.get("collected_at_ms"), now_ms()),
        sessions=_parse_sessions(payload.get("sessions"), host_id),
        screen_sessions=_parse_screen_sessions(payload.get("screen_sessions")),
        errors=_parse_errors(payload.get("errors"), host_id),
    )


def read_ssh_timeline(
    ssh_target: str,
    transcript_path: str,
    password: str | None = None,
    timeout_seconds: int = 15,
    limit: int = 20,
    before: int | None = None,
) -> tuple[list[dict[str, Any]], str | None, int | None, bool]:
    before_literal = "None" if before is None else json.dumps(max(0, before))
    probe = (
        REMOTE_TIMELINE_PROBE.replace("__PATH_JSON__", json.dumps(transcript_path))
        .replace("__LIMIT_JSON__", json.dumps(max(0, min(limit, 200))))
        .replace("__BEFORE_JSON__", before_literal)
    )
    try:
        result = _run_probe(ssh_target, probe, password, timeout_seconds)
    except Exception as exc:
        return [], str(exc), None, False
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        return [], detail or f"ssh exited with code {result.returncode}", None, False
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return [], f"invalid timeline JSON: {exc}", None, False
    if not isinstance(payload, dict):
        return [], "remote timeline returned non-object JSON", None, False
    rows = payload.get("timeline")
    if not isinstance(rows, list):
        return [], _text(payload.get("error"), "remote timeline returned no events"), None, False
    return (
        [row for row in rows if isinstance(row, dict)],
        _text(payload.get("error"), None),
        _optional_int(payload.get("next_before")),
        bool(payload.get("has_more")),
    )


def read_ssh_screen_capture(
    ssh_target: str,
    screen_session: str,
    password: str | None = None,
    timeout_seconds: int = 15,
    limit: int = 200,
) -> tuple[str, str | None]:
    probe = (
        REMOTE_SCREEN_CAPTURE_PROBE.replace("__SCREEN_SESSION_JSON__", json.dumps(screen_session))
        .replace("__LIMIT_JSON__", json.dumps(max(0, min(limit, 1000))))
    )
    try:
        result = _run_probe(ssh_target, probe, password, timeout_seconds)
    except Exception as exc:
        return "", str(exc)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        return "", detail or f"ssh exited with code {result.returncode}"
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return "", f"invalid screen capture JSON: {exc}"
    if not isinstance(payload, dict):
        return "", "remote screen capture returned non-object JSON"
    return _text(payload.get("capture"), ""), _text(payload.get("error"), None)


def send_ssh_screen_input(
    ssh_target: str,
    screen_session: str,
    text: str,
    password: str | None = None,
    timeout_seconds: int = 15,
    enter: bool = True,
) -> str | None:
    probe = (
        REMOTE_SCREEN_INPUT_PROBE.replace("__SCREEN_SESSION_JSON__", json.dumps(screen_session))
        .replace("__TEXT_B64_JSON__", json.dumps(_text_to_utf8_b64(text)))
        .replace("__SUBMIT_JSON__", repr(bool(enter)))
    )
    try:
        result = _run_probe(ssh_target, probe, password, timeout_seconds)
    except Exception as exc:
        return str(exc)
    if result.returncode != 0:
        return (result.stderr or result.stdout or f"ssh exited with code {result.returncode}").strip()
    return None


def start_ssh_screen_session(
    ssh_target: str,
    cwd: str | None = None,
    screen_name: str | None = None,
    command: str = "codex",
    initial_prompt: str | None = DEFAULT_REMOTE_CODEX_START_PROMPT,
    password: str | None = None,
    timeout_seconds: int = 15,
) -> tuple[dict[str, Any] | None, str | None]:
    if command not in {"codex"}:
        return None, "unsupported command"
    remote_command = CODEX_FULL_ACCESS_COMMAND if command == "codex" else command
    if screen_name and not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", screen_name):
        return None, "screen name may only contain letters, numbers, dot, underscore, and dash"
    probe = (
        REMOTE_START_SCREEN_PROBE.replace("__CWD_JSON__", json.dumps(cwd or "~"))
        .replace("__SCREEN_NAME_JSON__", json.dumps(screen_name))
        .replace("__COMMAND_JSON__", json.dumps(remote_command))
        .replace("__INITIAL_PROMPT_B64_JSON__", json.dumps(_text_to_utf8_b64(initial_prompt)))
    )
    try:
        result = _run_probe(ssh_target, probe, password, timeout_seconds)
    except Exception as exc:
        return None, str(exc)
    if result.returncode != 0:
        return None, (result.stderr or result.stdout or f"ssh exited with code {result.returncode}").strip()
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return None, f"invalid start screen JSON: {exc}"
    if not isinstance(payload, dict):
        return None, "remote start screen returned non-object JSON"
    if payload.get("started") is not True:
        return None, _text(payload.get("error"), "remote screen did not start")
    return payload, None


def _run_probe(ssh_target: str, probe: str, password: str | None, timeout_seconds: int):
    if password:
        return _run_probe_paramiko(ssh_target, probe, password, timeout_seconds)
    return subprocess.run(
        ["ssh", ssh_target, "python3", "-"],
        input=probe,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )


def _run_probe_paramiko(ssh_target: str, probe: str, password: str, timeout_seconds: int):
    try:
        import paramiko
    except ModuleNotFoundError as exc:  # pragma: no cover - dependency is declared
        raise RuntimeError("password SSH requires paramiko to be installed") from exc

    username, hostname, port = _parse_ssh_target(ssh_target)
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=hostname,
            port=port,
            username=username,
            password=password,
            timeout=timeout_seconds,
            banner_timeout=timeout_seconds,
            auth_timeout=timeout_seconds,
            look_for_keys=False,
            allow_agent=False,
        )
        stdin, stdout, stderr = client.exec_command("python3 -", timeout=timeout_seconds)
        stdin.write(probe)
        stdin.channel.shutdown_write()
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        code = stdout.channel.recv_exit_status()
        return _ProbeResult(returncode=code, stdout=out, stderr=err)
    finally:
        client.close()


class _ProbeResult:
    def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _parse_ssh_target(ssh_target: str) -> tuple[str | None, str, int]:
    target = ssh_target.strip()
    username: str | None = None
    if "@" in target:
        username, target = target.split("@", 1)
    port = 22
    if ":" in target and not target.startswith("["):
        host_part, port_part = target.rsplit(":", 1)
        if port_part.isdigit():
            target = host_part
            port = int(port_part)
    return username or None, target.strip("[]"), port


def _parse_sessions(value: Any, host_id: str) -> list[HostSession]:
    if not isinstance(value, list):
        return []

    sessions: list[HostSession] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        try:
            sessions.append(
                HostSession(
                    key=_text(item.get("key"), ""),
                    host_id=_text(item.get("host_id"), host_id),
                    platform=_text(item.get("platform"), "unknown"),
                    source=_text(item.get("source"), "ssh"),
                    status=_text(item.get("status"), "idle"),
                    confidence=_text(item.get("confidence"), "low"),
                    evidence=_text_list(item.get("evidence")),
                    session_id=_optional_text(item.get("session_id")),
                    cwd=_optional_text(item.get("cwd")),
                    project_name=_optional_text(item.get("project_name")),
                    last_event=_optional_text(item.get("last_event")),
                    last_prompt=_optional_text(item.get("last_prompt")),
                    last_response=_optional_text(item.get("last_response")),
                    model=_optional_text(item.get("model")),
                    pid=_optional_int(item.get("pid")),
                    ppid=_optional_int(item.get("ppid")),
                    tty=_optional_text(item.get("tty")),
                    tmux_session=_optional_text(item.get("tmux_session")),
                    tmux_window=_optional_text(item.get("tmux_window")),
                    tmux_pane=_optional_text(item.get("tmux_pane")),
                    screen_session=_optional_text(item.get("screen_session")),
                    transcript_path=_optional_text(item.get("transcript_path")),
                    transcript_mtime_ms=_optional_int(item.get("transcript_mtime_ms")),
                    resume_command=_optional_text(item.get("resume_command")),
                )
            )
        except TypeError:
            continue
    return [session for session in sessions if session.key]


def _parse_errors(value: Any, host_id: str) -> list[CollectorError]:
    if not isinstance(value, list):
        return []

    errors: list[CollectorError] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        errors.append(
            CollectorError(
                host_id=_text(item.get("host_id"), host_id),
                message=_text(item.get("message"), ""),
                kind=_text(item.get("kind"), "collector_error"),
            )
        )
    return errors


def _parse_screen_sessions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    screens: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        screen_session = _optional_text(item.get("screen_session"))
        if not screen_session:
            continue
        row: dict[str, Any] = {
            "screen_session": screen_session,
            "name": _optional_text(item.get("name")) or screen_session,
            "state": _optional_text(item.get("state")),
            "cwd": _optional_text(item.get("cwd")),
            "project_name": _optional_text(item.get("project_name")),
            "command": _optional_text(item.get("command")),
            "pid": _optional_int(item.get("pid")),
        }
        screens.append(row)
    return screens


def _error_snapshot(host_id: str, host_label: str, kind: str, message: str) -> HostSnapshot:
    return HostSnapshot(
        host_id=host_id,
        host_label=host_label,
        collected_at_ms=now_ms(),
        sessions=[],
        errors=[CollectorError(host_id=host_id, kind=kind, message=message)],
    )


def _text(value: Any, default: str | None) -> str | None:
    if isinstance(value, str):
        return value
    return default


def _optional_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    return None


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    return default
