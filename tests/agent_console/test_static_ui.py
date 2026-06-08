import unittest
from pathlib import Path


STATIC_HTML = Path(__file__).resolve().parents[2] / "agent_console" / "static" / "index.html"


class StaticUiTests(unittest.TestCase):
    def test_non_dialogue_events_are_grouped_in_chat_rendering(self):
        html = STATIC_HTML.read_text(encoding="utf-8")

        self.assertIn("function renderEventDetailGroup", html)
        self.assertIn("event-detail-group", html)
        self.assertIn("function renderConversationWithDetails", html)
        self.assertIn("renderConversationWithDetails(messages)", html)


if __name__ == "__main__":
    unittest.main()
