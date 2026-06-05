import unittest
import string

from agent_console.models import HostSession, build_session_key, now_ms


class ModelTests(unittest.TestCase):
    def test_session_key_is_url_safe_opaque_and_deterministic(self):
        first = build_session_key("gpu-a", "codex", session_id="abc/def:ghi jkl")
        second = build_session_key("gpu-a", "codex", session_id="abc/def:ghi jkl")

        self.assertEqual(first, second)
        self.assertTrue(first.startswith("gpu-a-codex-"))
        forbidden = {"/", "\\", " ", ":"}
        self.assertTrue(forbidden.isdisjoint(first))
        allowed = set(string.ascii_letters + string.digits + "-_")
        self.assertTrue(set(first).issubset(allowed))
        self.assertNotIn("abc", first)
        self.assertNotIn("def", first)

    def test_session_key_hashes_transcript_when_session_id_missing(self):
        key = build_session_key("gpu-a", "codex", transcript_path="/home/u/.codex/x.jsonl")
        self.assertTrue(key.startswith("gpu-a-codex-"))
        self.assertNotIn("/home/u", key)
        self.assertNotIn(":", key)

    def test_session_key_hashes_process_when_transcript_missing(self):
        key = build_session_key("gpu-a", "codex", pid=12345, process_start="2026-06-05T10:00:00")
        self.assertTrue(key.startswith("gpu-a-codex-"))
        self.assertNotIn("12345", key)
        self.assertNotIn("2026-06-05", key)

    def test_session_key_unknown_fallback_is_deterministic_and_host_scoped(self):
        first = build_session_key("gpu-a", "codex")
        second = build_session_key("gpu-a", "codex")
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("gpu-a-codex-"))

    def test_host_session_serializes_to_dict(self):
        session = HostSession(
            key="local:codex:abc",
            host_id="local",
            platform="codex",
            source="codex_vscode",
            status="idle",
            confidence="medium",
            evidence=["session_meta"],
            session_id="abc",
            cwd="/repo",
            screen_session="1234.pdn",
        )
        data = session.to_dict()
        self.assertEqual(data["platform"], "codex")
        self.assertEqual(data["cwd"], "/repo")
        self.assertEqual(data["evidence"], ["session_meta"])
        self.assertEqual(data["screen_session"], "1234.pdn")

    def test_host_snapshot_serializes_screen_sessions(self):
        from agent_console.models import HostSnapshot

        snapshot = HostSnapshot(
            host_id="gpu-a",
            host_label="GPU A",
            collected_at_ms=123,
            screen_sessions=[{"screen_session": "1234.pdn", "name": "pdn"}],
        )

        data = snapshot.to_dict()

        self.assertEqual(data["screen_sessions"], [{"screen_session": "1234.pdn", "name": "pdn"}])

    def test_now_ms_returns_integer_milliseconds(self):
        self.assertIsInstance(now_ms(), int)
        self.assertGreater(now_ms(), 1_700_000_000_000)


if __name__ == "__main__":
    unittest.main()
