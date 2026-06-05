import json
import unittest

from agent_console.app_server import CodexAppServerClient


class FakeStdin:
    def __init__(self):
        self.writes = []

    def write(self, value):
        self.writes.append(value)

    def flush(self):
        pass


class FakeProcess:
    def __init__(self):
        self.stdin = FakeStdin()


class CodexAppServerClientTests(unittest.TestCase):
    def test_server_approval_request_is_listed_and_resolved(self):
        client = CodexAppServerClient()
        client._process = FakeProcess()

        client._handle_message(
            {
                "jsonrpc": "2.0",
                "id": "approval-1",
                "method": "item/commandExecution/requestApproval",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "itemId": "item-1",
                    "command": "npm test",
                    "cwd": "/repo",
                    "reason": "requires network access",
                    "startedAtMs": 123,
                },
            }
        )

        approvals = client.pending_approvals("thread-1")

        self.assertEqual(len(approvals), 1)
        self.assertEqual(approvals[0]["id"], "approval-1")
        self.assertEqual(approvals[0]["thread_id"], "thread-1")
        self.assertEqual(approvals[0]["kind"], "command")
        self.assertEqual(approvals[0]["title"], "Command approval")
        self.assertEqual(approvals[0]["detail"], "npm test")
        self.assertEqual(approvals[0]["reason"], "requires network access")

        resolved = client.resolve_approval("approval-1", "acceptForSession")

        self.assertTrue(resolved["resolved"])
        self.assertEqual(client.pending_approvals("thread-1"), [])
        response = json.loads(client._process.stdin.writes[-1])
        self.assertEqual(response["id"], "approval-1")
        self.assertEqual(response["result"], {"decision": "acceptForSession"})

    def test_permission_approval_decline_grants_empty_permissions(self):
        client = CodexAppServerClient()
        client._process = FakeProcess()

        client._handle_message(
            {
                "jsonrpc": "2.0",
                "id": "permissions-1",
                "method": "item/permissions/requestApproval",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "itemId": "item-1",
                    "cwd": "/repo",
                    "permissions": {"network": {"enabled": True}},
                    "reason": "needs network",
                    "startedAtMs": 123,
                },
            }
        )

        client.resolve_approval("permissions-1", "decline")

        response = json.loads(client._process.stdin.writes[-1])
        self.assertEqual(response["result"], {"permissions": {}, "scope": "turn"})


if __name__ == "__main__":
    unittest.main()
