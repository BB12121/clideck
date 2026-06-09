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

    def test_session_chat_loads_older_timeline_on_top_scroll(self):
        html = STATIC_HTML.read_text(encoding="utf-8")

        self.assertIn("detailBefore", html)
        self.assertIn("detailHasMore", html)
        self.assertIn("async function loadOlderDetails", html)
        self.assertIn("&before=${encodeURIComponent(state.detailBefore)}", html)
        self.assertIn('els.sessionChat.addEventListener("scroll"', html)
        self.assertIn("els.sessionChat.scrollTop <= 48", html)

    def test_pending_screen_turns_have_background_reconciliation_refreshes(self):
        html = STATIC_HTML.read_text(encoding="utf-8")

        self.assertIn("PENDING_DETAIL_REFRESH_DELAYS", html)
        self.assertIn("function schedulePendingDetailRefresh", html)
        self.assertIn("function transcriptHasResponseAfterUser", html)
        self.assertIn("schedulePendingDetailRefresh(key);", html)
        self.assertNotIn("transcriptHasAssistantAfterUser", html)


if __name__ == "__main__":
    unittest.main()
