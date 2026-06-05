import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent_console.collectors.claude import discover_claude_sessions
from agent_console.collectors.local import collect_local_snapshot
from agent_console.collectors.processes import pid_alive, pid_tty
from agent_console.collectors.tmux import TmuxPane, parse_tmux_panes


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


class LocalCollectorTests(unittest.TestCase):
    def test_parse_tmux_panes_maps_tty_to_pane_metadata(self):
        panes = parse_tmux_panes("/dev/pts/4\tdev\t1\t2\t%5\n")

        self.assertEqual(panes["/dev/pts/4"].session, "dev")
        self.assertEqual(panes["/dev/pts/4"].window, "1")
        self.assertEqual(panes["/dev/pts/4"].pane, "%5")

    def test_discover_claude_sessions_marks_dead_pid_completed_and_sets_resume_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            write_json(
                home / ".claude" / "sessions" / "live.json",
                {
                    "pid": 999999,
                    "sessionId": "claude-123",
                    "cwd": "/repo",
                    "status": "idle",
                    "waitingFor": None,
                    "startedAt": 1,
                    "updatedAt": 2,
                    "version": 1,
                },
            )
            transcript = home / ".claude" / "projects" / "-repo" / "claude-123.jsonl"
            write_jsonl(transcript, [{"type": "assistant", "message": "done"}])

            with patch("agent_console.collectors.claude.pid_alive", return_value=False):
                sessions = discover_claude_sessions(home, "local")

            self.assertEqual(len(sessions), 1)
            session = sessions[0]
            self.assertEqual(session.platform, "claude")
            self.assertEqual(session.status, "completed")
            self.assertEqual(session.session_id, "claude-123")
            self.assertEqual(session.transcript_path, str(transcript))
            self.assertTrue(session.resume_command)
            self.assertIn("claude --resume", session.resume_command)

    def test_discover_claude_sessions_uses_core_project_slug_for_transcript(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            write_json(
                home / ".claude" / "sessions" / "slug.json",
                {
                    "pid": 123,
                    "sessionId": "claude-slug",
                    "cwd": "/work/foo_bar.baz",
                    "status": "idle",
                },
            )
            transcript = (
                home
                / ".claude"
                / "projects"
                / "-work-foo-bar-baz"
                / "claude-slug.jsonl"
            )
            write_jsonl(transcript, [{"type": "assistant", "message": "slug"}])

            with patch("agent_console.collectors.claude.pid_alive", return_value=False):
                sessions = discover_claude_sessions(home, "local")

            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].transcript_path, str(transcript))
            self.assertIn("transcript", sessions[0].evidence)

    def test_discover_claude_sessions_skips_malformed_session_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            sessions_root = home / ".claude" / "sessions"
            sessions_root.mkdir(parents=True, exist_ok=True)
            (sessions_root / "invalid-json.json").write_text("{", encoding="utf-8")
            (sessions_root / "non-dict.json").write_text(
                json.dumps([{"pid": 123}]),
                encoding="utf-8",
            )
            write_json(
                sessions_root / "string-pid.json",
                {"pid": "123", "sessionId": "string-pid", "cwd": "/repo"},
            )
            write_json(
                sessions_root / "bool-pid.json",
                {"pid": True, "sessionId": "bool-pid", "cwd": "/repo"},
            )
            write_json(
                sessions_root / "valid.json",
                {"pid": 123, "sessionId": "valid", "cwd": "/repo"},
            )
            write_jsonl(home / ".claude" / "projects" / "-repo" / "valid.jsonl", [])

            with patch("agent_console.collectors.claude.pid_alive", return_value=False):
                sessions = discover_claude_sessions(home, "local")

            self.assertEqual([session.session_id for session in sessions], ["valid"])

    def test_discover_claude_sessions_skips_dead_state_without_transcript(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            write_json(
                home / ".claude" / "sessions" / "dead-no-transcript.json",
                {
                    "pid": 38196,
                    "sessionId": "stale-claude",
                    "cwd": r"C:\Users\bihao\Desktop\clideck",
                    "status": "idle",
                },
            )

            with patch("agent_console.collectors.claude.pid_alive", return_value=False):
                sessions = discover_claude_sessions(home, "local")

            self.assertEqual(sessions, [])

    def test_process_helpers_reject_boolean_pid(self):
        with (
            patch("agent_console.collectors.processes.os.kill", return_value=None),
            patch(
                "agent_console.collectors.processes.subprocess.run",
                return_value=SimpleNamespace(returncode=0, stdout="pts/4\n"),
            ),
        ):
            self.assertFalse(pid_alive(True))
            self.assertIsNone(pid_tty(True))

    def test_process_helpers_treat_system_error_as_unavailable(self):
        with (
            patch("agent_console.collectors.processes.os.kill", side_effect=SystemError("win32 os error")),
            patch("agent_console.collectors.processes.subprocess.run", side_effect=SystemError("ps failed")),
        ):
            self.assertFalse(pid_alive(123))
            self.assertIsNone(pid_tty(123))

    def test_collect_local_snapshot_includes_codex_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            rollout = home / ".codex" / "sessions" / "2026" / "06" / "05" / "rollout-cdx.jsonl"
            write_jsonl(
                rollout,
                [{"type": "session_meta", "payload": {"id": "cdx", "cwd": "/repo"}}],
            )

            with patch("agent_console.collectors.local.list_tmux_panes", return_value={}):
                snapshot = collect_local_snapshot("local", "Local", home)

            self.assertEqual(snapshot.host_id, "local")
            self.assertEqual(snapshot.host_label, "Local")
            self.assertIn("cdx", [session.session_id for session in snapshot.sessions])
            self.assertIn("codex", [session.platform for session in snapshot.sessions])

    def test_collect_local_snapshot_keeps_codex_when_claude_collector_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            rollout = home / ".codex" / "sessions" / "2026" / "06" / "05" / "rollout-cdx.jsonl"
            write_jsonl(
                rollout,
                [{"type": "session_meta", "payload": {"id": "cdx", "cwd": "/repo"}}],
            )

            with (
                patch("agent_console.collectors.local.discover_claude_sessions", side_effect=SystemError("os error")),
                patch("agent_console.collectors.local.list_tmux_panes", return_value={}),
            ):
                snapshot = collect_local_snapshot("local", "Local", home)

            self.assertIn("cdx", [session.session_id for session in snapshot.sessions])
            self.assertEqual(len(snapshot.errors), 1)
            self.assertEqual(snapshot.errors[0].kind, "claude_collector_SystemError")

    def test_collect_local_snapshot_attaches_tmux_metadata_for_matching_tty(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            write_json(
                home / ".claude" / "sessions" / "live.json",
                {
                    "pid": 123,
                    "sessionId": "claude-tty",
                    "cwd": "/repo",
                    "status": "idle",
                    "waitingFor": None,
                    "startedAt": 1,
                    "updatedAt": 2,
                    "version": 1,
                },
            )
            write_jsonl(home / ".claude" / "projects" / "-repo" / "claude-tty.jsonl", [])

            with (
                patch("agent_console.collectors.claude.pid_alive", return_value=True),
                patch("agent_console.collectors.claude.pid_tty", return_value="/dev/pts/4"),
                patch(
                    "agent_console.collectors.local.list_tmux_panes",
                    return_value={
                        "/dev/pts/4": TmuxPane(
                            tty="/dev/pts/4",
                            session="dev",
                            window="1",
                            pane="%5",
                        )
                    },
                ),
            ):
                snapshot = collect_local_snapshot("local", "Local", home)

            session = next(item for item in snapshot.sessions if item.session_id == "claude-tty")
            self.assertEqual(session.tty, "/dev/pts/4")
            self.assertEqual(session.tmux_session, "dev")
            self.assertEqual(session.tmux_window, "1")
            self.assertEqual(session.tmux_pane, "%5")


if __name__ == "__main__":
    unittest.main()
