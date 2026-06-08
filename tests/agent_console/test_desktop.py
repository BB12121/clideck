import socket
import unittest
from unittest.mock import patch

from agent_console import desktop


class DesktopLauncherTests(unittest.TestCase):
    def test_choose_port_returns_preferred_when_available(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])

        self.assertEqual(desktop.choose_port("127.0.0.1", port), port)

    def test_choose_port_returns_alternate_when_preferred_is_in_use(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            used_port = int(sock.getsockname()[1])

            alternate = desktop.choose_port("127.0.0.1", used_port)

        self.assertIsInstance(alternate, int)
        self.assertNotEqual(alternate, used_port)
        self.assertGreater(alternate, 0)

    def test_run_server_reuses_existing_ready_service(self):
        target = desktop.ServerTarget(host="127.0.0.1", port=7878)

        with patch.object(desktop, "is_ready", return_value=True):
            server, thread = desktop.run_server(target)

        self.assertIsNone(server)
        self.assertIsNone(thread)

    def test_main_reuses_existing_preferred_service(self):
        opened_urls = []

        def fake_is_ready(url):
            return url == "http://127.0.0.1:7878/agent-console/"

        with (
            patch.object(desktop, "is_ready", side_effect=fake_is_ready),
            patch.object(desktop, "choose_port") as choose_port,
            patch.object(desktop, "run_server", return_value=(None, None)),
            patch.object(desktop, "wait_until_ready", return_value=True),
            patch.object(desktop, "open_window", side_effect=opened_urls.append),
        ):
            result = desktop.main(["--host", "127.0.0.1", "--port", "7878"])

        self.assertEqual(result, 0)
        choose_port.assert_not_called()
        self.assertEqual(opened_urls, ["http://127.0.0.1:7878/agent-console/"])

    def test_main_stops_started_server_after_window_closes(self):
        fake_server = type("FakeServer", (), {"should_exit": False})()
        fake_thread = type("FakeThread", (), {"join": lambda self, timeout=None: None})()

        with (
            patch.object(desktop, "is_ready", return_value=False),
            patch.object(desktop, "choose_port", return_value=9000),
            patch.object(desktop, "run_server", return_value=(fake_server, fake_thread)),
            patch.object(desktop, "wait_until_ready", return_value=True),
            patch.object(desktop, "open_window", return_value=None),
        ):
            result = desktop.main(["--host", "127.0.0.1", "--port", "7878"])

        self.assertEqual(result, 0)
        self.assertTrue(fake_server.should_exit)


if __name__ == "__main__":
    unittest.main()
