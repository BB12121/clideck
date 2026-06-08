import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent_console.config import delete_host, discover_ssh_hosts, load_hosts, save_host
from agent_console.collectors.ssh import (
    REMOTE_PROBE,
    REMOTE_SCREEN_INPUT_PROBE,
    REMOTE_TIMELINE_PROBE,
    collect_ssh_snapshot,
    read_ssh_screen_capture,
    read_ssh_timeline,
    send_ssh_screen_input,
    start_ssh_screen_session,
)


class ConfigAndSshCollectorTests(unittest.TestCase):
    def load_hosts_from_text(self, text: str):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agent-console.toml"
            path.write_text(text, encoding="utf-8")
            return load_hosts(path)

    def test_missing_config_defaults_to_local_host(self):
        hosts = load_hosts(Path("missing.toml"))

        self.assertEqual(len(hosts), 1)
        self.assertEqual(hosts[0].id, "local")
        self.assertEqual(hosts[0].type, "local")

    def test_minimal_toml_host_defaults_actions_disabled(self):
        hosts = self.load_hosts_from_text(
            '[[hosts]]\nid="gpu-a"\nlabel="GPU A"\ntype="ssh"\nssh="user@gpu-a"\n'
        )

        self.assertEqual(len(hosts), 2)
        self.assertEqual(hosts[0].id, "local")
        self.assertEqual(hosts[1].id, "gpu-a")
        self.assertEqual(hosts[1].label, "GPU A")
        self.assertEqual(hosts[1].type, "ssh")
        self.assertEqual(hosts[1].ssh, "user@gpu-a")
        self.assertFalse(hosts[1].enable_actions)

    def test_local_host_is_retained_when_config_has_only_remote_hosts(self):
        hosts = self.load_hosts_from_text(
            '[[hosts]]\nid="gpu-a"\nlabel="GPU A"\ntype="ssh"\nssh="user@gpu-a"\n'
        )

        self.assertEqual([host.id for host in hosts], ["local", "gpu-a"])

    def test_load_hosts_reads_password(self):
        hosts = self.load_hosts_from_text(
            '[[hosts]]\nid="gpu-a"\nlabel="GPU A"\ntype="ssh"\nssh="user@gpu-a"\npassword="secret"\n'
        )

        self.assertEqual(hosts[1].password, "secret")

    def test_save_host_writes_plaintext_password_locally(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agent-console.toml"
            save_host(
                load_hosts(Path("missing.toml"))[0].__class__(
                    id="gpu-a",
                    label="GPU A",
                    type="ssh",
                    ssh="user@gpu-a",
                    password="secret",
                ),
                path,
            )

            text = path.read_text(encoding="utf-8")
            self.assertIn('password = "secret"', text)
            self.assertEqual(load_hosts(path)[1].password, "secret")

    def test_delete_host_removes_remote_and_keeps_local_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agent-console.toml"
            host_class = load_hosts(Path("missing.toml"))[0].__class__
            save_host(host_class(id="gpu-a", label="GPU A", type="ssh", ssh="alice@gpu-a"), path)
            save_host(host_class(id="gpu-b", label="GPU B", type="ssh", ssh="bob@gpu-b"), path)

            hosts = delete_host("gpu-a", path)

            self.assertEqual([host.id for host in hosts], ["local", "gpu-b"])
            self.assertEqual([host.id for host in load_hosts(path)], ["local", "gpu-b"])

    def test_delete_host_rejects_local(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                delete_host("local", Path(tmp) / "agent-console.toml")

    def test_discover_ssh_hosts_parses_ssh_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config"
            path.write_text(
                "Host gpu-a\n  HostName 10.0.0.5\n  User alice\n\nHost *.internal\n  User ignored\n",
                encoding="utf-8",
            )

            hosts = discover_ssh_hosts([path])

        self.assertEqual(hosts, [{
            "id": "gpu-a",
            "label": "gpu-a",
            "ssh": "alice@10.0.0.5",
            "source": str(path),
            "hostname": "10.0.0.5",
            "user": "alice",
        }])

    def test_enable_actions_requires_boolean_not_quoted_false(self):
        with self.assertRaisesRegex(ValueError, "hosts\\[0\\]\\.enable_actions"):
            self.load_hosts_from_text(
                '[[hosts]]\nid="gpu-a"\nlabel="GPU A"\nenable_actions="false"\n'
            )

    def test_enable_actions_accepts_actual_boolean(self):
        hosts = self.load_hosts_from_text(
            '[[hosts]]\nid="gpu-a"\nlabel="GPU A"\nenable_actions=true\n'
        )

        self.assertTrue(hosts[1].enable_actions)

    def test_host_id_is_required_and_non_empty(self):
        cases = [
            ("missing id", '[[hosts]]\nlabel="GPU A"\n'),
            ("empty id", '[[hosts]]\nid=""\nlabel="GPU A"\n'),
        ]
        for _, text in cases:
            with self.subTest(text=text):
                with self.assertRaisesRegex(ValueError, "hosts\\[0\\]\\.id"):
                    self.load_hosts_from_text(text)

    def test_timeout_fields_require_positive_integers(self):
        cases = [
            ("poll_interval_seconds", '"fast"'),
            ("connect_timeout_seconds", "true"),
            ("command_timeout_seconds", "0"),
        ]
        for field, value in cases:
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, f"hosts\\[0\\]\\.{field}"):
                    self.load_hosts_from_text(
                        f'[[hosts]]\nid="gpu-a"\nlabel="GPU A"\n{field}={value}\n'
                    )

    def test_collect_ssh_snapshot_parses_valid_json(self):
        payload = {
            "host_id": "gpu-a",
            "host_label": "GPU A",
            "collected_at_ms": 1234,
            "sessions": [],
            "errors": [],
        }

        with patch(
            "agent_console.collectors.ssh.subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr=""),
        ):
            snapshot = collect_ssh_snapshot("gpu-a", "GPU A", "user@gpu-a")

        self.assertEqual(snapshot.host_id, "gpu-a")
        self.assertEqual(snapshot.host_label, "GPU A")
        self.assertEqual(snapshot.collected_at_ms, 1234)
        self.assertEqual(snapshot.sessions, [])
        self.assertEqual(snapshot.errors, [])

    def test_collect_ssh_snapshot_preserves_remote_sessions(self):
        payload = {
            "host_id": "gpu-a",
            "host_label": "GPU A",
            "collected_at_ms": 1234,
            "sessions": [
                {
                    "key": "gpu-a-codex-existing",
                    "host_id": "gpu-a",
                    "platform": "codex",
                    "source": "cli",
                    "status": "idle",
                    "confidence": "medium",
                    "session_id": "codex-1",
                    "cwd": "/repo",
                    "project_name": "repo",
                    "transcript_path": "/home/me/.codex/sessions/2026/06/rollout-a.jsonl",
                    "resume_command": "cd /repo && codex resume codex-1",
                    "screen_session": "1234.pdn",
                    "evidence": ["session_meta"],
                },
                {
                    "key": "gpu-a-claude-existing",
                    "host_id": "gpu-a",
                    "platform": "claude",
                    "source": "cli",
                    "status": "completed",
                    "confidence": "medium",
                    "session_id": "claude-1",
                    "evidence": ["claude_session_state"],
                },
            ],
            "screen_sessions": [
                {
                    "screen_session": "1234.pdn",
                    "name": "pdn",
                    "state": "detached",
                    "cwd": "/repo",
                    "pid": 1234,
                }
            ],
            "errors": [],
        }

        with patch(
            "agent_console.collectors.ssh.subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr=""),
        ):
            snapshot = collect_ssh_snapshot("gpu-a", "GPU A", "user@gpu-a")

        self.assertEqual([session.platform for session in snapshot.sessions], ["codex", "claude"])
        self.assertEqual(snapshot.sessions[0].session_id, "codex-1")
        self.assertEqual(snapshot.sessions[0].cwd, "/repo")
        self.assertEqual(snapshot.sessions[0].screen_session, "1234.pdn")
        self.assertEqual(snapshot.sessions[1].session_id, "claude-1")
        self.assertEqual(snapshot.screen_sessions[0]["screen_session"], "1234.pdn")
        self.assertEqual(snapshot.screen_sessions[0]["name"], "pdn")

    def test_remote_probe_scans_claude_and_codex_without_stub_error(self):
        self.assertIn(".claude", REMOTE_PROBE)
        self.assertIn(".codex", REMOTE_PROBE)
        self.assertIn("rollout-", REMOTE_PROBE)
        self.assertNotIn("probe_stub", REMOTE_PROBE)

    def test_remote_probe_exposes_screen_inventory_without_standalone_sessions(self):
        self.assertIn("discover_screen_sessions", REMOTE_PROBE)
        self.assertNotIn("append_standalone_screen_sessions", REMOTE_PROBE)
        self.assertIn('["screen", "-ls"]', REMOTE_PROBE)
        self.assertIn('"screen_sessions": screen_sessions', REMOTE_PROBE)
        self.assertNotIn('"platform": "screen"', REMOTE_PROBE)

    def test_remote_timeline_probe_reads_transcript_path(self):
        self.assertIn("__PATH_JSON__", REMOTE_TIMELINE_PROBE)
        self.assertIn("__BEFORE_JSON__", REMOTE_TIMELINE_PROBE)
        self.assertIn("timeline", REMOTE_TIMELINE_PROBE)
        self.assertIn("session_meta", REMOTE_TIMELINE_PROBE)
        self.assertIn("next_before", REMOTE_TIMELINE_PROBE)
        self.assertIn("has_more", REMOTE_TIMELINE_PROBE)

    def test_read_ssh_timeline_parses_remote_events(self):
        payload = {
            "timeline": [
                {"type": "event_msg", "payload": {"type": "user_message", "message": "hello"}}
            ],
            "error": None,
            "next_before": 4,
            "has_more": True,
        }
        with patch(
            "agent_console.collectors.ssh._run_probe",
            return_value=SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr=""),
        ) as run_probe:
            rows, error, next_before, has_more = read_ssh_timeline(
                "user@gpu-a",
                "/home/u/rollout.jsonl",
                password="secret",
                limit=5,
                before=9,
            )

        self.assertIsNone(error)
        self.assertEqual(next_before, 4)
        self.assertTrue(has_more)
        self.assertEqual(rows[0]["payload"]["message"], "hello")
        args = run_probe.call_args.args
        self.assertEqual(args[0], "user@gpu-a")
        self.assertEqual(args[2], "secret")
        self.assertIn("before = 9", args[1])

    def test_read_ssh_timeline_uses_python_none_for_missing_before_cursor(self):
        payload = {"timeline": [], "error": None, "next_before": 0, "has_more": False}
        with patch(
            "agent_console.collectors.ssh._run_probe",
            return_value=SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr=""),
        ) as run_probe:
            rows, error, next_before, has_more = read_ssh_timeline(
                "user@gpu-a",
                "/home/u/rollout.jsonl",
                limit=5,
            )

        self.assertEqual(rows, [])
        self.assertIsNone(error)
        self.assertEqual(next_before, 0)
        self.assertFalse(has_more)
        probe = run_probe.call_args.args[1]
        self.assertIn("before = None", probe)
        self.assertNotIn("before = null", probe)

    def test_send_ssh_screen_input_uses_remote_probe_and_sends_enter_separately(self):
        with patch(
            "agent_console.collectors.ssh._run_probe",
            return_value=SimpleNamespace(returncode=0, stdout="", stderr=""),
        ) as run_probe:
            error = send_ssh_screen_input(
                "user@gpu-a",
                "1234.pdn",
                "hello \"screen\"\nnext",
                password="secret",
                timeout_seconds=8,
            )

        self.assertIsNone(error)
        args = run_probe.call_args.args
        self.assertEqual(args[0], "user@gpu-a")
        self.assertIn('"1234.pdn"', args[1])
        self.assertIn('hello \\"screen\\"\\nnext', args[1])
        self.assertNotIn('next\\r', args[1])
        self.assertIn("submit = True", args[1])
        self.assertNotIn("submit = true", args[1])
        self.assertIn("__SUBMIT_JSON__", REMOTE_SCREEN_INPUT_PROBE)
        self.assertIn('["screen", "-S", screen_session, "-X", "stuff", chr(13)]', REMOTE_SCREEN_INPUT_PROBE)
        self.assertEqual(args[2], "secret")
        self.assertEqual(args[3], 8)

    def test_send_ssh_screen_input_reports_remote_failure(self):
        with patch(
            "agent_console.collectors.ssh._run_probe",
            return_value=SimpleNamespace(returncode=1, stdout="", stderr="No screen session found"),
        ):
            error = send_ssh_screen_input("user@gpu-a", "missing", "hello")

        self.assertIn("No screen session found", error)

    def test_start_ssh_screen_session_starts_codex_in_screen(self):
        payload = {
            "started": True,
            "screen_session": "codex-test",
            "cwd": "/repo",
            "command": "codex",
        }
        with patch(
            "agent_console.collectors.ssh._run_probe",
            return_value=SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr=""),
        ) as run_probe:
            result, error = start_ssh_screen_session(
                "user@gpu-a",
                cwd="/repo",
                screen_name="codex-test",
                password="secret",
                timeout_seconds=8,
            )

        self.assertIsNone(error)
        self.assertEqual(result["screen_session"], "codex-test")
        args = run_probe.call_args.args
        self.assertEqual(args[0], "user@gpu-a")
        self.assertIn('"codex-test"', args[1])
        self.assertIn('"codex --dangerously-bypass-approvals-and-sandbox"', args[1])
        self.assertIn('"\\u4f60\\u597d"', args[1])
        self.assertIn('["screen", "-S", name, "-X", "stuff", initial_prompt]', args[1])
        self.assertIn('["screen", "-S", name, "-X", "stuff", chr(13)]', args[1])
        self.assertEqual(args[2], "secret")
        self.assertEqual(args[3], 8)

    def test_start_ssh_screen_session_rejects_unsafe_screen_name(self):
        result, error = start_ssh_screen_session("user@gpu-a", screen_name="bad name; rm")

        self.assertIsNone(result)
        self.assertIn("screen name", error)

    def test_read_ssh_screen_capture_uses_remote_hardcopy_probe(self):
        payload = {"capture": "line one\nline two", "error": None}
        with patch(
            "agent_console.collectors.ssh._run_probe",
            return_value=SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr=""),
        ) as run_probe:
            capture, error = read_ssh_screen_capture(
                "user@gpu-a",
                "1234.pdn",
                password="secret",
                timeout_seconds=8,
                limit=25,
            )

        self.assertEqual(capture, "line one\nline two")
        self.assertIsNone(error)
        args = run_probe.call_args.args
        self.assertEqual(args[0], "user@gpu-a")
        self.assertIn('"1234.pdn"', args[1])
        self.assertIn('"hardcopy"', args[1])
        self.assertEqual(args[2], "secret")
        self.assertEqual(args[3], 8)

    def test_collect_ssh_snapshot_nonzero_exit_returns_error(self):
        with patch(
            "agent_console.collectors.ssh.subprocess.run",
            return_value=SimpleNamespace(returncode=255, stdout="", stderr="permission denied"),
        ):
            snapshot = collect_ssh_snapshot("gpu-a", "GPU A", "user@gpu-a")

        self.assertEqual(snapshot.host_id, "gpu-a")
        self.assertEqual(len(snapshot.errors), 1)
        self.assertEqual(snapshot.errors[0].kind, "ssh_exit")
        self.assertIn("permission denied", snapshot.errors[0].message)

    def test_collect_ssh_snapshot_invalid_json_returns_error(self):
        with patch(
            "agent_console.collectors.ssh.subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout="{", stderr=""),
        ):
            snapshot = collect_ssh_snapshot("gpu-a", "GPU A", "user@gpu-a")

        self.assertEqual(snapshot.host_id, "gpu-a")
        self.assertEqual(len(snapshot.errors), 1)
        self.assertEqual(snapshot.errors[0].kind, "invalid_json")

    def test_collect_ssh_snapshot_uses_ssh_argument_array_without_shell(self):
        payload = {
            "host_id": "gpu-a",
            "host_label": "GPU A",
            "collected_at_ms": 1234,
            "sessions": [],
            "errors": [],
        }

        with patch(
            "agent_console.collectors.ssh.subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr=""),
        ) as run_mock:
            collect_ssh_snapshot("gpu-a", "GPU A", "user@gpu-a", timeout_seconds=7)

        run_mock.assert_called_once()
        args, kwargs = run_mock.call_args
        self.assertEqual(args[0], ["ssh", "user@gpu-a", "python3", "-"])
        self.assertNotIn("shell", kwargs)
        self.assertTrue(kwargs["capture_output"])
        self.assertTrue(kwargs["text"])
        self.assertEqual(kwargs["timeout"], 7)

    @patch("agent_console.collectors.ssh._run_probe_paramiko")
    def test_collect_ssh_snapshot_uses_paramiko_when_password_is_saved(self, run_paramiko):
        payload = {
            "host_id": "gpu-a",
            "host_label": "GPU A",
            "collected_at_ms": 1234,
            "sessions": [],
            "errors": [],
        }
        run_paramiko.return_value = SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

        snapshot = collect_ssh_snapshot("gpu-a", "GPU A", "user@gpu-a", password="secret", timeout_seconds=9)

        self.assertEqual(snapshot.host_id, "gpu-a")
        run_paramiko.assert_called_once()
        args = run_paramiko.call_args.args
        self.assertEqual(args[0], "user@gpu-a")
        self.assertEqual(args[2], "secret")
        self.assertEqual(args[3], 9)


if __name__ == "__main__":
    unittest.main()
