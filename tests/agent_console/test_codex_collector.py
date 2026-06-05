import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_console.collectors.codex import (
    _render_resume_command,
    discover_codex_sessions,
    parse_codex_rollout,
)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


class CodexCollectorTests(unittest.TestCase):
    def test_parse_vscode_rollout_with_running_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rollout-vscode.jsonl"
            write_jsonl(
                path,
                [
                    {
                        "type": "session_meta",
                        "payload": {
                            "id": "sess-vscode",
                            "cwd": "/work/project-alpha",
                            "originator": "codex_vscode",
                        },
                    },
                    {
                        "type": "turn_context",
                        "payload": {
                            "model": "gpt-5.1-codex",
                            "cwd": "/work/project-alpha",
                        },
                    },
                    {
                        "type": "response_item",
                        "payload": {
                            "role": "user",
                            "content": [{"type": "input_text", "text": "Inspect the queue"}],
                        },
                    },
                    {
                        "type": "event_msg",
                        "payload": {"type": "task_started", "message": "Task started"},
                    },
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "agent_message",
                            "message": [{"text": "Reading rollout files"}],
                        },
                    },
                ],
            )

            session = parse_codex_rollout(path, "host-a", now=1_000_000.0)

            self.assertEqual(session.session_id, "sess-vscode")
            self.assertEqual(session.source, "codex_vscode")
            self.assertEqual(session.cwd, "/work/project-alpha")
            self.assertEqual(session.project_name, "project-alpha")
            self.assertEqual(session.model, "gpt-5.1-codex")
            self.assertEqual(session.status, "running")
            self.assertEqual(session.confidence, "medium")
            self.assertEqual(session.last_prompt, "Inspect the queue")
            self.assertEqual(session.last_response, "Reading rollout files")
            self.assertIn("session_meta", session.evidence)
            self.assertIn("turn_context", session.evidence)
            self.assertIn("task_started", session.evidence)
            self.assertIn("agent_message", session.evidence)

    def test_parse_task_complete_without_process_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rollout-complete.jsonl"
            write_jsonl(
                path,
                [
                    {
                        "type": "session_meta",
                        "payload": {
                            "id": "sess complete",
                            "cwd": "/work/project beta",
                            "originator": "codex_cli",
                        },
                    },
                    {
                        "type": "turn_context",
                        "payload": {"cfg": {"model": "gpt-5.1"}},
                    },
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "task_complete",
                            "last_agent_message": "Completed requested change",
                        },
                    },
                ],
            )

            session = parse_codex_rollout(path, "host-b", now=1_000_000.0)

            self.assertEqual(session.status, "completed")
            self.assertEqual(session.confidence, "medium")
            self.assertEqual(session.source, "cli")
            self.assertEqual(session.model, "gpt-5.1")
            self.assertEqual(session.last_response, "Completed requested change")
            self.assertIsNone(session.pid)
            self.assertEqual(
                session.resume_command,
                _render_resume_command("/work/project beta", "sess complete"),
            )

    def test_turn_aborted_after_task_started_is_not_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rollout-aborted.jsonl"
            write_jsonl(
                path,
                [
                    {"type": "session_meta", "payload": {"id": "aborted", "cwd": "/work/app"}},
                    {"type": "event_msg", "payload": {"type": "task_started"}},
                    {"type": "event_msg", "payload": {"type": "turn_aborted"}},
                ],
            )

            session = parse_codex_rollout(path, "host-d", now=1_000_000.0)

            self.assertEqual(session.last_event, "turn_aborted")
            self.assertEqual(session.status, "completed")
            self.assertEqual(session.confidence, "medium")
            self.assertIn("turn_aborted", session.evidence)

    def test_permission_request_marks_session_waiting(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rollout-approval.jsonl"
            write_jsonl(
                path,
                [
                    {"type": "session_meta", "payload": {"id": "approval", "cwd": "/work/app"}},
                    {"type": "event_msg", "payload": {"type": "task_started"}},
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "exec_approval_request",
                            "message": "Shell command needs approval before it can run",
                        },
                    },
                ],
            )

            session = parse_codex_rollout(path, "host-approval", now=1_000_000.0)

            self.assertEqual(session.last_event, "exec_approval_request")
            self.assertEqual(session.status, "waiting")
            self.assertEqual(session.confidence, "high")
            self.assertEqual(session.last_response, "Shell command needs approval before it can run")
            self.assertIn("permission_request", session.evidence)

    def test_permission_words_in_chat_content_do_not_mark_waiting(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rollout-permission-text.jsonl"
            write_jsonl(
                path,
                [
                    {"type": "session_meta", "payload": {"id": "text-only", "cwd": "/work/app"}},
                    {"type": "event_msg", "payload": {"type": "task_started"}},
                    {
                        "type": "response_item",
                        "payload": {
                            "item": {
                                "type": "message",
                                "role": "developer",
                                "content": [
                                    {
                                        "type": "input_text",
                                        "text": (
                                            "<permissions instructions>\n"
                                            "approval request permission pending confirm\n"
                                            "</permissions instructions>"
                                        ),
                                    }
                                ],
                            },
                        },
                    },
                    {
                        "type": "response_item",
                        "payload": {
                            "item": {
                                "type": "message",
                                "role": "assistant",
                                "content": [{"type": "output_text", "text": "Still working"}],
                            },
                        },
                    },
                ],
            )

            session = parse_codex_rollout(path, "host-text", now=1_000_000.0)

            self.assertEqual(session.status, "running")
            self.assertEqual(session.confidence, "medium")
            self.assertNotIn("permission_request", session.evidence)
            self.assertEqual(session.last_response, "Still working")

    def test_old_incomplete_task_started_uses_age_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rollout-old-started.jsonl"
            write_jsonl(
                path,
                [
                    {"type": "session_meta", "payload": {"id": "started", "cwd": "/work/app"}},
                    {"type": "event_msg", "payload": {"type": "task_started"}},
                ],
            )
            os.utime(path, (100.0, 100.0))

            session = parse_codex_rollout(path, "host-e", now=86_600.0)

            self.assertEqual(session.last_event, "task_started")
            self.assertEqual(session.status, "stale")
            self.assertEqual(session.confidence, "low")

    def test_render_resume_command_for_windows_powershell(self):
        command = _render_resume_command(
            r"C:\Users\Ada's Repo\project beta",
            "sess '42'",
            os_name="nt",
        )

        self.assertEqual(
            command,
            "Set-Location -LiteralPath 'C:\\Users\\Ada''s Repo\\project beta'; "
            "codex resume 'sess ''42'''",
        )

    def test_render_resume_command_for_posix_shell(self):
        command = _render_resume_command(
            "/work/project beta",
            "sess complete",
            os_name="posix",
        )

        self.assertEqual(command, "cd '/work/project beta' && codex resume 'sess complete'")

    def test_discover_codex_sessions_orders_newest_first_and_caps_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sessions_root = root / ".codex" / "sessions" / "2026" / "06" / "05"
            expected_ids = []
            for index in range(5):
                path = sessions_root / f"rollout-{index}.jsonl"
                session_id = f"session-{index}"
                write_jsonl(
                    path,
                    [{"type": "session_meta", "payload": {"id": session_id, "cwd": f"/work/{index}"}}],
                )
                os.utime(path, (100.0 + index, 100.0 + index))
                expected_ids.append(session_id)

            sessions = discover_codex_sessions(root, "host-c", max_files=2)

            self.assertEqual([session.session_id for session in sessions], list(reversed(expected_ids[-2:])))

    def test_discover_codex_sessions_parses_only_capped_newest_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sessions_root = root / ".codex" / "sessions" / "2026" / "06" / "05"
            for index in range(5):
                path = sessions_root / f"rollout-{index}.jsonl"
                write_jsonl(
                    path,
                    [{"type": "session_meta", "payload": {"id": f"session-{index}", "cwd": f"/work/{index}"}}],
                )
                os.utime(path, (100.0 + index, 100.0 + index))

            with patch(
                "agent_console.collectors.codex.parse_codex_rollout",
                side_effect=lambda path, host_id: path.name,
            ) as parse_mock:
                sessions = discover_codex_sessions(root, "host-f", max_files=2)

            self.assertEqual(sessions, ["rollout-4.jsonl", "rollout-3.jsonl"])
            self.assertEqual(parse_mock.call_count, 2)
            self.assertEqual(
                [call.args[0].name for call in parse_mock.call_args_list],
                ["rollout-4.jsonl", "rollout-3.jsonl"],
            )

    def test_discover_codex_sessions_skips_candidate_that_disappears_during_stat(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sessions_root = root / ".codex" / "sessions" / "2026" / "06" / "05"
            good_path = sessions_root / "rollout-good.jsonl"
            gone_path = sessions_root / "rollout-gone.jsonl"
            write_jsonl(
                good_path,
                [{"type": "session_meta", "payload": {"id": "good", "cwd": "/work/good"}}],
            )
            write_jsonl(
                gone_path,
                [{"type": "session_meta", "payload": {"id": "gone", "cwd": "/work/gone"}}],
            )
            os.utime(good_path, (100.0, 100.0))
            os.utime(gone_path, (200.0, 200.0))
            original_stat = Path.stat

            def stat_or_disappear(path: Path, *args, **kwargs):
                if path == gone_path:
                    raise OSError("file disappeared")
                return original_stat(path, *args, **kwargs)

            with patch("pathlib.Path.stat", stat_or_disappear):
                sessions = discover_codex_sessions(root, "host-g", max_files=2)

            self.assertEqual([session.session_id for session in sessions], ["good"])

    def test_discover_codex_sessions_skips_candidate_that_disappears_during_parse(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sessions_root = root / ".codex" / "sessions" / "2026" / "06" / "05"
            for name, mtime in (("gone", 200.0), ("good", 100.0)):
                path = sessions_root / f"rollout-{name}.jsonl"
                write_jsonl(
                    path,
                    [{"type": "session_meta", "payload": {"id": name, "cwd": f"/work/{name}"}}],
                )
                os.utime(path, (mtime, mtime))

            def parse_or_disappear(path: Path, host_id: str):
                if path.name == "rollout-gone.jsonl":
                    raise OSError("file disappeared")
                return path.name

            with patch(
                "agent_console.collectors.codex.parse_codex_rollout",
                side_effect=parse_or_disappear,
            ) as parse_mock:
                sessions = discover_codex_sessions(root, "host-h", max_files=2)

            self.assertEqual(sessions, ["rollout-good.jsonl"])
            self.assertEqual(parse_mock.call_count, 2)


if __name__ == "__main__":
    unittest.main()
