from __future__ import annotations

import json
import queue
import shutil
import subprocess
import threading
from dataclasses import dataclass
from typing import Any


APP_SERVER_TIMEOUT_SECONDS = 20


@dataclass
class AppServerResult:
    mode: str
    thread_id: str
    turn_id: str | None = None
    response: dict[str, Any] | None = None


class CodexAppServerClient:
    def __init__(self, *, command: str | None = None) -> None:
        self.command = command
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._next_id = 1
        self._pending: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._pending_approvals: dict[str, dict[str, Any]] = {}
        self._notifications: list[dict[str, Any]] = []
        self._reader: threading.Thread | None = None

    def close(self) -> None:
        with self._lock:
            process = self._process
            self._process = None
        if process and process.poll() is None:
            process.terminate()

    def start_turn(
        self,
        thread_id: str,
        text: str,
        *,
        cwd: str | None = None,
        path: str | None = None,
    ) -> AppServerResult:
        self._ensure_started()
        resume_params: dict[str, Any] = {"threadId": thread_id}
        if cwd:
            resume_params["cwd"] = cwd
        if path:
            resume_params["path"] = path
        self.request("thread/resume", resume_params)
        result = self.request(
            "turn/start",
            {
                "threadId": thread_id,
                "input": [{"type": "text", "text": text}],
            },
            timeout=APP_SERVER_TIMEOUT_SECONDS,
        )
        turn = result.get("turn") if isinstance(result.get("turn"), dict) else {}
        turn_id = turn.get("id") if isinstance(turn.get("id"), str) else None
        return AppServerResult(mode="app-server", thread_id=thread_id, turn_id=turn_id, response=result)

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: int = APP_SERVER_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        self._ensure_started()
        request_id, response_queue = self._register_request()
        message = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
        process = self._process
        if not process or not process.stdin:
            raise RuntimeError("Codex app-server is not running")
        try:
            process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            process.stdin.flush()
            response = response_queue.get(timeout=timeout)
        except queue.Empty as exc:
            self._pending.pop(request_id, None)
            raise RuntimeError(f"Codex app-server request timed out: {method}") from exc
        except OSError as exc:
            self._pending.pop(request_id, None)
            raise RuntimeError(f"Codex app-server write failed: {exc}") from exc
        if "error" in response:
            raise RuntimeError(_jsonrpc_error_text(response["error"]))
        result = response.get("result")
        return result if isinstance(result, dict) else {}

    def notifications(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._notifications)

    def pending_approvals(self, thread_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            approvals = list(self._pending_approvals.values())
        if thread_id:
            approvals = [row for row in approvals if row.get("thread_id") == thread_id]
        approvals.sort(key=lambda row: str(row.get("started_at_ms") or ""))
        return approvals

    def resolve_approval(
        self,
        approval_id: str,
        decision: str,
        content: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            approval = self._pending_approvals.pop(approval_id, None)
        if approval is None:
            raise KeyError(f"approval request not found: {approval_id}")
        result = _approval_result(approval, decision, content)
        self._write_jsonrpc_response(approval["request_id"], result)
        return {"resolved": True, "id": approval_id, "decision": decision}

    def _ensure_started(self) -> None:
        with self._lock:
            if self._process and self._process.poll() is None:
                return
            codex = self.command or _find_codex_command()
            if not codex:
                raise RuntimeError("codex CLI was not found on PATH")
            self._process = subprocess.Popen(
                [codex, "app-server", "--stdio"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            self._reader = threading.Thread(target=self._read_loop, daemon=True)
            self._reader.start()
        self.request(
            "initialize",
            {
                "clientInfo": {"name": "clideck", "version": "0.1"},
                "capabilities": {"experimentalApi": True},
            },
            timeout=APP_SERVER_TIMEOUT_SECONDS,
        )

    def _register_request(self) -> tuple[int, queue.Queue[dict[str, Any]]]:
        with self._lock:
            request_id = self._next_id
            self._next_id += 1
            response_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
            self._pending[request_id] = response_queue
            return request_id, response_queue

    def _read_loop(self) -> None:
        process = self._process
        if not process or not process.stdout:
            return
        for line in process.stdout:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            self._handle_message(message)

    def _handle_message(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        if isinstance(method, str):
            approval = _approval_from_server_request(message)
            if approval is not None:
                with self._lock:
                    self._pending_approvals[approval["id"]] = approval
            with self._lock:
                self._notifications.append(message)
                self._notifications = self._notifications[-500:]
            return

        request_id = message.get("id")
        if isinstance(request_id, int):
            response_queue = self._pending.pop(request_id, None)
            if response_queue:
                response_queue.put(message)

    def _write_jsonrpc_response(self, request_id: str | int, result: dict[str, Any]) -> None:
        process = self._process
        if not process or not process.stdin:
            raise RuntimeError("Codex app-server is not running")
        message = {"jsonrpc": "2.0", "id": request_id, "result": result}
        try:
            process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            process.stdin.flush()
        except OSError as exc:
            raise RuntimeError(f"Codex app-server approval response failed: {exc}") from exc


def _find_codex_command() -> str | None:
    return shutil.which("codex.cmd") or shutil.which("codex.exe") or shutil.which("codex")


def _jsonrpc_error_text(error: Any) -> str:
    if isinstance(error, dict):
        message = error.get("message")
        code = error.get("code")
        if message and code is not None:
            return f"{message} ({code})"
        if message:
            return str(message)
    return str(error)


def _approval_from_server_request(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params")
    if not isinstance(method, str) or not isinstance(params, dict):
        return None
    if not isinstance(request_id, (str, int)):
        return None
    if method not in {
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
        "item/permissions/requestApproval",
        "mcpServer/elicitation/request",
        "execCommandApproval",
        "applyPatchApproval",
    }:
        return None

    approval_id = str(request_id)
    thread_id = _first_text(params, "threadId", "conversationId")
    kind = _approval_kind(method)
    return {
        "id": approval_id,
        "request_id": request_id,
        "method": method,
        "kind": kind,
        "thread_id": thread_id,
        "turn_id": _first_text(params, "turnId"),
        "item_id": _first_text(params, "itemId", "callId"),
        "title": _approval_title(kind),
        "detail": _approval_detail(kind, params),
        "reason": _first_text(params, "reason"),
        "cwd": _first_text(params, "cwd"),
        "started_at_ms": params.get("startedAtMs"),
        "params": params,
    }


def _approval_kind(method: str) -> str:
    if "commandExecution" in method or method == "execCommandApproval":
        return "command"
    if "fileChange" in method or method == "applyPatchApproval":
        return "file_change"
    if "permissions" in method:
        return "permissions"
    if "elicitation" in method:
        return "elicitation"
    return "approval"


def _approval_title(kind: str) -> str:
    return {
        "command": "Command approval",
        "file_change": "File change approval",
        "permissions": "Permission approval",
        "elicitation": "Input requested",
    }.get(kind, "Approval requested")


def _approval_detail(kind: str, params: dict[str, Any]) -> str:
    if kind == "command":
        command = params.get("command")
        if isinstance(command, list):
            return " ".join(str(part) for part in command)
        if isinstance(command, str):
            return command
    if kind == "file_change":
        grant_root = _first_text(params, "grantRoot")
        if grant_root:
            return grant_root
        file_changes = params.get("fileChanges")
        if isinstance(file_changes, dict) and file_changes:
            return ", ".join(str(path) for path in list(file_changes)[:6])
    if kind == "permissions":
        cwd = _first_text(params, "cwd")
        permissions = params.get("permissions")
        if cwd:
            return cwd
        if isinstance(permissions, dict):
            return json.dumps(permissions, ensure_ascii=False)
    if kind == "elicitation":
        return _first_text(params, "message", "serverName") or "MCP server requested input"
    return _first_text(params, "reason") or "Approval requested"


def _approval_result(
    approval: dict[str, Any],
    decision: str,
    content: dict[str, Any] | None,
) -> dict[str, Any]:
    method = approval.get("method")
    params = approval.get("params") if isinstance(approval.get("params"), dict) else {}
    if method == "execCommandApproval":
        return {"decision": _legacy_exec_decision(decision)}
    if method == "applyPatchApproval":
        return {"decision": _legacy_patch_decision(decision)}
    if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
        return {"decision": _modern_decision(decision)}
    if method == "item/permissions/requestApproval":
        if decision in {"accept", "acceptForSession"}:
            scope = "session" if decision == "acceptForSession" else "turn"
            permissions = params.get("permissions") if isinstance(params.get("permissions"), dict) else {}
            return {"permissions": permissions, "scope": scope}
        return {"permissions": {}, "scope": "turn"}
    if method == "mcpServer/elicitation/request":
        action = decision if decision in {"accept", "decline", "cancel"} else "decline"
        result: dict[str, Any] = {"action": action}
        if action == "accept" and content is not None:
            result["content"] = content
        return result
    return {"decision": decision}


def _modern_decision(decision: str) -> str:
    if decision in {"accept", "acceptForSession", "decline", "cancel"}:
        return decision
    if decision == "approved":
        return "accept"
    if decision == "denied":
        return "decline"
    if decision == "abort":
        return "cancel"
    return "decline"


def _legacy_exec_decision(decision: str) -> str:
    return {
        "accept": "approved",
        "acceptForSession": "approved_for_session",
        "decline": "denied",
        "cancel": "abort",
    }.get(decision, decision if decision in {"approved", "approved_for_session", "denied", "abort"} else "denied")


def _legacy_patch_decision(decision: str) -> str:
    return {
        "accept": "approved",
        "acceptForSession": "approved_for_session",
        "decline": "denied",
        "cancel": "abort",
    }.get(decision, decision if decision in {"approved", "approved_for_session", "denied", "abort"} else "denied")


def _first_text(data: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return None
