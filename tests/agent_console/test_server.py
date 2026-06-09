import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent_console.config import HostConfig
from agent_console.models import HostSession, HostSnapshot


class AgentConsoleServerTests(unittest.TestCase):
    def make_snapshot(self, *, key: str = "local-codex-session") -> HostSnapshot:
        return HostSnapshot(
            host_id="local",
            host_label="Local",
            collected_at_ms=123,
            sessions=[
                HostSession(
                    key=key,
                    host_id="local",
                    platform="codex",
                    source="codex",
                    status="running",
                    confidence="high",
                    session_id="session-1",
                    cwd="/repo",
                    project_name="repo",
                    last_prompt="fix api routes",
                    resume_command="cd /repo && codex resume session-1",
                )
            ],
        )

    def test_snapshot_returns_serialized_hosts_and_counts(self):
        from agent_console import server

        snapshot = self.make_snapshot()

        with patch.object(server, "collect_all_hosts_once", return_value=[snapshot]):
            response = TestClient(server.create_app()).get("/api/snapshot")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["hosts"][0]["host_id"], "local")
        self.assertEqual(data["counts"]["total"], 1)
        self.assertEqual(data["counts"]["running"], 1)

    def test_snapshot_uses_cache_after_first_collection(self):
        from agent_console import server

        snapshot = self.make_snapshot()

        with patch.object(server, "collect_all_hosts_once", return_value=[snapshot]) as collect:
            client = TestClient(server.create_app())
            first = client.get("/api/snapshot")
            second = client.get("/api/snapshot")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        collect.assert_called_once_with()

    def test_snapshot_refresh_query_recollects_and_updates_cache(self):
        from agent_console import server

        stale = self.make_snapshot()
        fresh = self.make_snapshot()
        fresh.collected_at_ms = 456
        fresh.sessions[0].status = "idle"

        with patch.object(server, "collect_all_hosts_once", side_effect=[[stale], [fresh]]) as collect:
            client = TestClient(server.create_app())
            first = client.get("/api/snapshot")
            refreshed = client.get("/api/snapshot?refresh=1")
            cached = client.get("/api/snapshot")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(refreshed.status_code, 200)
        self.assertEqual(cached.status_code, 200)
        self.assertEqual(collect.call_count, 2)
        self.assertEqual(first.json()["hosts"][0]["collected_at_ms"], 123)
        self.assertEqual(refreshed.json()["hosts"][0]["collected_at_ms"], 456)
        self.assertEqual(cached.json()["hosts"][0]["collected_at_ms"], 456)
        self.assertEqual(cached.json()["counts"]["idle"], 1)

    def test_static_refresh_button_uses_force_refresh_under_agent_console_mount(self):
        html = Path("agent_console/static/index.html").read_text(encoding="utf-8")

        self.assertIn("function appBasePath()", html)
        self.assertIn("function routeSessionKey()", html)
        self.assertIn("function sessionPageUrl(key)", html)
        self.assertIn('function apiUrl(forceRefresh = false, suffix = "/api/snapshot")', html)
        self.assertIn("`${appBasePath()}${suffix}`", html)
        self.assertIn("/sessions/${encodeURIComponent(key)}", html)
        self.assertIn('url.searchParams.set("refresh", "1")', html)
        self.assertIn("els.refresh.addEventListener(\"click\", () => load({ refresh: true }))", html)
        self.assertIn("const AUTO_REFRESH_MS = 15_000", html)
        self.assertIn("window.setInterval(autoRefresh, AUTO_REFRESH_MS)", html)
        self.assertIn("document.addEventListener(\"visibilitychange\"", html)
        self.assertIn("load({ refresh: true, quiet: true })", html)
        self.assertIn('id="host-list"', html)
        self.assertIn('id="host-port"', html)
        self.assertIn("function splitSshTarget(value)", html)
        self.assertIn("function composeSshTarget(ssh, port)", html)
        self.assertIn("Port must be numeric when provided.", html)
        self.assertIn("configuredHosts", html)
        self.assertIn("function loadConfiguredHosts(openPanel = true)", html)
        self.assertIn("function editHost(id)", html)
        self.assertIn("function deleteConfiguredHost(id)", html)
        self.assertIn('id="notify-completed"', html)
        self.assertIn('const NOTIFY_COMPLETED_KEY = "agentConsoleNotifyCompleted"', html)
        self.assertIn("function detectCompletedSessions(nextHosts)", html)
        self.assertIn("function showCompletionPopup(item)", html)
        self.assertIn("function loadSettings()", html)
        self.assertIn('apiUrl(false, "/api/settings")', html)
        self.assertIn("desktop_notifications_enabled", html)
        self.assertIn('id="test-notify"', html)
        self.assertIn('apiUrl(false, "/api/notifications/test")', html)
        self.assertIn("function testNotification()", html)
        self.assertIn('id="new-host"', html)
        self.assertIn('id="new-cwd"', html)
        self.assertIn('list="new-cwd-options"', html)
        self.assertIn('id="new-cwd-options"', html)
        self.assertIn("function remotePathSuggestions(host)", html)
        self.assertIn("function sshBasePathSuggestions(config)", html)
        self.assertIn("await loadConfiguredHosts(false)", html)
        self.assertIn("function startRemoteCodexSession()", html)
        self.assertIn('screen_name: state.newSessionScreen || null', html)
        self.assertIn("Notification.requestPermission", html)
        self.assertIn("new Notification(title, { body })", html)
        self.assertIn("detailTerminalCapture", html)
        self.assertIn("data.terminal_capture", html)
        self.assertNotIn("Screen ${session.screen_session", html)
        self.assertIn("data-edit-host", html)
        self.assertIn("data-delete-host", html)
        self.assertIn('method: "DELETE"', html)
        self.assertIn('fetch(apiUrl(false, "/api/vscode-hosts")', html)
        self.assertIn('fetch(apiUrl(false, "/api/hosts")', html)
        self.assertIn("return url.toString();", html)
        self.assertIn("function hostAccent(hostId)", html)
        self.assertIn("host-dot", html)
        self.assertIn('<th class="col-host">Host</th>', html)
        self.assertNotIn("No sessions on this host.", html)
        self.assertIn("data-detail", html)
        self.assertIn("function toggleDetails(key)", html)
        self.assertIn("DETAIL_TIMELINE_LIMIT = 80", html)
        self.assertIn("function loadDetails(key, { quiet = false } = {})", html)
        self.assertIn("state.detailTimeline = Array.isArray(data.timeline) ? data.timeline.slice(-DETAIL_TIMELINE_LIMIT).reverse() : []", html)
        self.assertIn("data-screen-input", html)
        self.assertIn("data-screen-select", html)
        self.assertIn("data-local-input", html)
        self.assertIn("data-local-send", html)
        self.assertIn("data-local-resume", html)
        self.assertIn("function sendLocalInput(key)", html)
        self.assertIn("function openLocalResume(key)", html)
        self.assertIn('"/screen-input"', html)
        self.assertIn('"/local-input"', html)
        self.assertIn('"/local-resume"', html)
        self.assertIn('"/screen-assignment"', html)
        self.assertIn("chat-transcript", html)
        self.assertIn("chat-message", html)
        self.assertIn("function messageKind(row)", html)
        self.assertIn("function messageText(row)", html)
        self.assertIn("return payload.message.trim()", html)
        self.assertIn("return directContent.trim()", html)
        self.assertNotIn("return truncateText(payload.message)", html)
        self.assertIn("function dedupeMessages(messages)", html)
        self.assertIn("function renderSessionPage()", html)
        self.assertIn("function openSessionPage(key)", html)
        self.assertIn("function closeSessionPage()", html)
        self.assertIn("function isInternalTranscriptRow(row)", html)
        self.assertIn('text.includes("<environment_context>")', html)
        self.assertIn('text.includes("<workspace_roots>")', html)
        self.assertIn("renderDetailMessages(session, { chronological: true })", html)
        self.assertIn("function scrollSessionChatToBottom()", html)
        self.assertIn("els.sessionChat.scrollTop = els.sessionChat.scrollHeight", html)
        self.assertIn("body.session-route", html)
        self.assertIn("body.session-route .topbar", html)
        self.assertIn("[hidden]", html)
        self.assertIn("display: none !important", html)
        self.assertIn("body.session-route #controls", html)
        self.assertIn("body.session-route #new-session-panel", html)
        self.assertIn("grid-template-rows: minmax(0, 1fr)", html)
        self.assertIn("window.history.pushState", html)
        self.assertIn("window.addEventListener(\"popstate\"", html)
        self.assertIn('id="session-page"', html)
        self.assertIn('id="session-chat"', html)
        self.assertIn('id="topbar"', html)
        self.assertIn("els.topbar.hidden = onSessionPage", html)
        self.assertIn('data-session-screen-input', html)
        self.assertIn('data-session-screen-send', html)
        self.assertIn('data-session-local-input', html)
        self.assertIn('data-session-local-send', html)
        self.assertIn('data-session-local-codex-input', html)
        self.assertIn('data-session-local-codex-send', html)
        self.assertIn("function sendLocalCodexPrompt(key)", html)
        self.assertIn("pendingMessages: {}", html)
        self.assertIn('function addPendingCodexTurn(key, text, assistantLabel = "Codex")', html)
        self.assertIn("function removePendingCodexTurn(key, text)", html)
        self.assertIn("function reconcilePendingMessages(key)", html)
        self.assertIn("function renderChatMessages(messages)", html)
        self.assertIn("chatMessages.length", html)
        self.assertIn("renderChatMessages(chatMessages)", html)
        self.assertIn('addPendingCodexTurn(key, sentText, "Screen")', html)
        self.assertIn("Sent to screen. Waiting for screen update.", html)
        self.assertIn("thinking-dots", html)
        self.assertIn("Codex is thinking", html)
        self.assertIn("PENDING_DETAIL_REFRESH_DELAYS", html)
        self.assertIn("function schedulePendingDetailRefresh(key)", html)
        self.assertIn("schedulePendingDetailRefresh(key);", html)
        self.assertIn("function sessionPageIsBeingUsed()", html)
        self.assertIn("function sendFromSessionTextarea(target)", html)
        self.assertIn('"/local-codex-prompt"', html)
        self.assertIn('els.sessionPage.addEventListener("keydown"', html)
        self.assertIn('event.key !== "Enter" || event.shiftKey', html)
        self.assertIn("sessionPageIsBeingUsed()) return", html)
        self.assertIn('data-session-back', html)
        self.assertIn("seenDialogueText", html)
        self.assertIn('const scopedKey = `${message.kind}:${key}`', html)
        self.assertIn("function renderChatMessage(kind, label, text, options = {})", html)
        self.assertIn("function isInternalTranscriptRow(row)", html)
        self.assertIn('role === "developer" || role === "system"', html)
        self.assertIn('!isInternalTranscriptRow(row)', html)
        self.assertIn("function renderMarkdown(text)", html)
        self.assertIn("function renderMarkdownTable(rows)", html)
        self.assertIn("function isMarkdownTableSeparator(line)", html)
        self.assertIn("chat-message ${escapeHtml(kind)} collapsible", html)
        self.assertIn('<details class="chat-message ${escapeHtml(kind)} collapsible">', html)
        self.assertIn('class="chat-bubble ${isDialogue ? "markdown" : "raw"}"', html)
        self.assertIn("markdown-path", html)
        self.assertIn("Enable remote actions", html)
        self.assertIn("approvals: {}", html)
        self.assertIn("function renderApprovalControls(host, session)", html)
        self.assertIn('"/approvals"', html)
        self.assertIn("function loadApprovals(key", html)
        self.assertIn("function resolveApproval(key, approvalId, decision)", html)
        self.assertIn("function sendScreenKey(key, action)", html)
        self.assertIn('"/screen-key"', html)
        self.assertIn("data-approval-decision", html)
        self.assertIn("data-session-screen-key", html)
        self.assertIn("Remote approval requested", html)
        self.assertIn('data-screen-action="acceptForSession"', html)
        self.assertIn("Manual keys", html)

    def test_session_page_serves_spa_html(self):
        from agent_console import server

        response = TestClient(server.create_app()).get("/sessions/local-codex-session")

        self.assertEqual(response.status_code, 200)
        self.assertIn("CliDeck", response.text)
        self.assertIn('id="session-page"', response.text)

    def test_api_hosts_returns_configured_metadata_without_password(self):
        from agent_console import server

        hosts = [
            HostConfig(
                id="gpu-a",
                label="GPU A",
                type="ssh",
                ssh="secret-user@gpu-a",
                password="secret",
                poll_interval_seconds=30,
                connect_timeout_seconds=6,
                command_timeout_seconds=20,
                enable_actions=True,
            )
        ]

        with patch.object(server, "load_hosts", return_value=hosts):
            response = TestClient(server.create_app()).get("/api/hosts")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["hosts"][0]["id"], "gpu-a")
        self.assertEqual(data["hosts"][0]["label"], "GPU A")
        self.assertEqual(data["hosts"][0]["type"], "ssh")
        self.assertEqual(data["hosts"][0]["ssh"], "secret-user@gpu-a")
        self.assertTrue(data["hosts"][0]["has_password"])
        self.assertTrue(data["hosts"][0]["enable_actions"])
        self.assertEqual(data["hosts"][0]["command_timeout_seconds"], 20)
        self.assertNotIn("password", data["hosts"][0])

    def test_api_delete_host_removes_configured_remote_and_clears_cache(self):
        from agent_console import server

        remaining_hosts = [HostConfig(id="local", label="Local", type="local")]

        with patch.object(server, "delete_host", return_value=remaining_hosts) as delete:
            client = TestClient(server.create_app())
            response = client.delete("/api/hosts/gpu-a")

        self.assertEqual(response.status_code, 200)
        delete.assert_called_once_with("gpu-a")
        self.assertEqual(response.json()["hosts"][0]["id"], "local")

    def test_api_delete_host_rejects_local(self):
        from agent_console import server

        with patch.object(server, "delete_host", side_effect=ValueError("local host cannot be deleted")):
            response = TestClient(server.create_app()).delete("/api/hosts/local")

        self.assertEqual(response.status_code, 400)
        self.assertIn("local", response.json()["detail"])

    def test_api_vscode_hosts_returns_discovered_candidates(self):
        from agent_console import server

        with (
            patch.object(server, "load_hosts", return_value=[
                HostConfig(id="gpu-a", label="GPU A", type="ssh", ssh="alice@gpu-a")
            ]),
            patch.object(server, "discover_ssh_hosts", return_value=[
                {"id": "gpu-a", "label": "gpu-a", "ssh": "alice@gpu-a", "source": "ssh-config"},
                {"id": "gpu-b", "label": "gpu-b", "ssh": "bob@gpu-b", "source": "ssh-config"},
            ]),
        ):
            response = TestClient(server.create_app()).get("/api/vscode-hosts")

        self.assertEqual(response.status_code, 200)
        data = response.json()["hosts"]
        self.assertTrue(data[0]["configured"])
        self.assertFalse(data[1]["configured"])

    def test_api_save_host_accepts_password_without_returning_it(self):
        from agent_console import server

        saved_hosts = [
            HostConfig(id="gpu-a", label="GPU A", type="ssh", ssh="alice@gpu-a", password="secret")
        ]
        payload = {
            "id": "gpu-a",
            "label": "GPU A",
            "ssh": "alice@gpu-a",
            "password": "secret",
            "command_timeout_seconds": 20,
        }

        with patch.object(server, "save_host", return_value=saved_hosts) as save:
            response = TestClient(server.create_app()).post("/api/hosts", json=payload)

        self.assertEqual(response.status_code, 200)
        saved = save.call_args.args[0]
        self.assertEqual(saved.id, "gpu-a")
        self.assertEqual(saved.password, "secret")
        data = response.json()
        self.assertTrue(data["host"]["has_password"])
        self.assertNotIn("password", data["host"])

    def test_api_save_host_preserves_existing_password_when_password_is_empty(self):
        from agent_console import server

        existing_hosts = [
            HostConfig(id="gpu-a", label="GPU A", type="ssh", ssh="alice@gpu-a", password="secret")
        ]
        payload = {
            "id": "gpu-a",
            "label": "GPU A",
            "ssh": "alice@gpu-a",
            "password": "",
            "enable_actions": True,
        }

        with (
            patch.object(server, "load_hosts", return_value=existing_hosts),
            patch.object(server, "save_host", return_value=existing_hosts) as save,
        ):
            response = TestClient(server.create_app()).post("/api/hosts", json=payload)

        self.assertEqual(response.status_code, 200)
        saved = save.call_args.args[0]
        self.assertEqual(saved.password, "secret")
        self.assertTrue(saved.enable_actions)

    def test_api_search_returns_matching_sessions_with_host_context(self):
        from agent_console import server

        snapshot = self.make_snapshot()

        with patch.object(server, "collect_all_hosts_once", return_value=[snapshot]):
            response = TestClient(server.create_app()).get("/api/search?q=api")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["q"], "api")
        self.assertEqual(len(data["matches"]), 1)
        self.assertEqual(data["matches"][0]["session"]["key"], "local-codex-session")
        self.assertEqual(data["matches"][0]["host"]["id"], "local")
        self.assertEqual(data["matches"][0]["host"]["label"], "Local")

    def test_api_search_without_query_returns_empty_matches_without_collecting(self):
        from agent_console import server

        with patch.object(server, "collect_all_hosts_once") as collect:
            response = TestClient(server.create_app()).get("/api/search")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["matches"], [])
        collect.assert_not_called()

    def test_session_timeline_returns_bounded_local_transcript_events(self):
        from agent_console import server

        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "rollout.jsonl"
            transcript.write_text(
                "\n".join(
                    [
                        json.dumps({"type": "session_meta", "payload": {"id": "session-1"}}),
                        json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "one"}}),
                        json.dumps({"type": "event_msg", "payload": {"type": "agent_message", "message": "two"}}),
                        json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "three"}}),
                    ]
                ),
                encoding="utf-8",
            )
            snapshot = self.make_snapshot()
            snapshot.sessions[0].transcript_path = str(transcript)

            with patch.object(server, "collect_all_hosts_once", return_value=[snapshot]):
                response = TestClient(server.create_app()).get(
                    "/api/sessions/local-codex-session/timeline?limit=2"
                )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIsNone(data["error"])
        self.assertEqual(data["session"]["key"], "local-codex-session")
        self.assertEqual(len(data["timeline"]), 2)
        self.assertEqual(data["timeline"][0]["type"], "event_msg")
        self.assertEqual(data["timeline"][0]["payload"]["message"], "two")
        self.assertEqual(data["timeline"][1]["payload"]["message"], "three")
        self.assertTrue(data["has_more"])
        self.assertEqual(data["next_before"], 1)

    def test_session_timeline_returns_older_local_transcript_page(self):
        from agent_console import server

        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "rollout.jsonl"
            transcript.write_text(
                "\n".join(
                    [
                        json.dumps({"type": "session_meta", "payload": {"id": "session-1"}}),
                        json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "one"}}),
                        json.dumps({"type": "event_msg", "payload": {"type": "agent_message", "message": "two"}}),
                        json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "three"}}),
                        json.dumps({"type": "event_msg", "payload": {"type": "agent_message", "message": "four"}}),
                    ]
                ),
                encoding="utf-8",
            )
            snapshot = self.make_snapshot()
            snapshot.sessions[0].transcript_path = str(transcript)

            with patch.object(server, "collect_all_hosts_once", return_value=[snapshot]):
                response = TestClient(server.create_app()).get(
                    "/api/sessions/local-codex-session/timeline?limit=2&before=2"
                )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual([row["payload"]["message"] for row in data["timeline"]], ["one", "two"])
        self.assertFalse(data["has_more"])
        self.assertEqual(data["next_before"], 0)

    def test_session_timeline_missing_or_unreadable_transcript_returns_error_field(self):
        from agent_console import server

        snapshot = self.make_snapshot()
        snapshot.sessions[0].transcript_path = "relative-or-remote.jsonl"

        with patch.object(server, "collect_all_hosts_once", return_value=[snapshot]):
            response = TestClient(server.create_app()).get("/api/sessions/local-codex-session/timeline")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["timeline"], [])
        self.assertIn("not locally readable", data["error"])

    def test_session_timeline_allows_screen_only_session_without_transcript(self):
        from agent_console import server

        snapshot = HostSnapshot(
            host_id="gpu-a",
            host_label="GPU A",
            collected_at_ms=123,
            sessions=[
                HostSession(
                    key="gpu-a-screen-session",
                    host_id="gpu-a",
                    platform="screen",
                    source="screen",
                    status="running",
                    confidence="medium",
                    screen_session="1234.pdn",
                )
            ],
            screen_sessions=[{"screen_session": "1234.pdn", "name": "pdn"}],
        )
        hosts = [
            HostConfig(
                id="gpu-a",
                label="GPU A",
                type="ssh",
                ssh="user@gpu-a",
                password="secret",
            )
        ]

        with (
            patch.object(server, "collect_all_hosts_once", return_value=[snapshot]),
            patch.object(server, "load_hosts", return_value=hosts),
            patch.object(server, "read_ssh_screen_capture", return_value=("", None)),
        ):
            response = TestClient(server.create_app()).get("/api/sessions/gpu-a-screen-session/timeline")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["timeline"], [])
        self.assertIsNone(data["error"])

    def test_session_timeline_reads_remote_transcript_for_ssh_host(self):
        from agent_console import server

        snapshot = HostSnapshot(
            host_id="gpu-a",
            host_label="GPU A",
            collected_at_ms=123,
            sessions=[
                HostSession(
                    key="gpu-a-codex-session",
                    host_id="gpu-a",
                    platform="codex",
                    source="cli",
                    status="idle",
                    confidence="medium",
                    transcript_path="/home/u/.codex/sessions/rollout.jsonl",
                    screen_session="1234.pdn",
                )
            ],
        )
        hosts = [
            HostConfig(
                id="gpu-a",
                label="GPU A",
                type="ssh",
                ssh="user@gpu-a",
                password="secret",
                command_timeout_seconds=22,
            )
        ]
        timeline = [{"type": "event_msg", "payload": {"type": "agent_message", "message": "remote"}}]

        with (
            patch.object(server, "collect_all_hosts_once", return_value=[snapshot]),
            patch.object(server, "load_hosts", return_value=hosts),
            patch.object(server, "read_ssh_timeline", return_value=(timeline, None, 0, False)) as read_remote,
            patch.object(server, "read_ssh_screen_capture", return_value=("screen text", None)) as read_screen,
        ):
            response = TestClient(server.create_app()).get("/api/sessions/gpu-a-codex-session/timeline")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["timeline"], timeline)
        self.assertEqual(data["terminal_capture"], "screen text")
        self.assertIsNone(data["terminal_error"])
        read_remote.assert_called_once_with(
            "user@gpu-a",
            "/home/u/.codex/sessions/rollout.jsonl",
            password="secret",
            timeout_seconds=22,
            limit=500,
            before=None,
        )
        read_screen.assert_called_once_with(
            "user@gpu-a",
            "1234.pdn",
            password="secret",
            timeout_seconds=22,
            limit=160,
        )

    def test_snapshot_applies_manual_screen_assignment(self):
        from agent_console import server

        snapshot = HostSnapshot(
            host_id="gpu-a",
            host_label="GPU A",
            collected_at_ms=123,
            sessions=[
                HostSession(
                    key="gpu-a-codex-session",
                    host_id="gpu-a",
                    platform="codex",
                    source="cli",
                    status="idle",
                    confidence="medium",
                    screen_session="2660238.codex",
                ),
                HostSession(
                    key="gpu-a-screen-session",
                    host_id="gpu-a",
                    platform="screen",
                    source="screen",
                    status="running",
                    confidence="medium",
                    screen_session="658429.pdn",
                )
            ],
            screen_sessions=[
                {"screen_session": "2660238.codex", "name": "codex"},
                {"screen_session": "658429.pdn", "name": "pdn"},
            ],
        )

        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "agent-console-state.json"
            state_file.write_text(
                json.dumps({"screen_assignments": {"gpu-a-codex-session": "658429.pdn"}}),
                encoding="utf-8",
            )
            with (
                patch.object(server, "SCREEN_ASSIGNMENTS_FILE", state_file),
                patch.object(server, "collect_all_hosts_once", return_value=[snapshot]),
            ):
                response = TestClient(server.create_app()).get("/api/snapshot")

        self.assertEqual(response.status_code, 200)
        sessions = response.json()["hosts"][0]["sessions"]
        self.assertEqual(len(sessions), 1)
        session = sessions[0]
        self.assertEqual(session["screen_session"], "658429.pdn")

    def test_screen_assignment_route_persists_selected_screen(self):
        from agent_console import server

        snapshot = HostSnapshot(
            host_id="gpu-a",
            host_label="GPU A",
            collected_at_ms=123,
            sessions=[
                HostSession(
                    key="gpu-a-codex-session",
                    host_id="gpu-a",
                    platform="codex",
                    source="cli",
                    status="idle",
                    confidence="medium",
                    screen_session="2660238.codex",
                )
            ],
            screen_sessions=[
                {"screen_session": "2660238.codex", "name": "codex"},
                {"screen_session": "658429.pdn", "name": "pdn"},
            ],
        )

        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "agent-console-state.json"
            with (
                patch.object(server, "SCREEN_ASSIGNMENTS_FILE", state_file),
                patch.object(server, "collect_all_hosts_once", return_value=[snapshot]),
            ):
                response = TestClient(server.create_app()).post(
                    "/api/sessions/gpu-a-codex-session/screen-assignment",
                    json={"screen_session": "658429.pdn"},
                )

            saved = json.loads(state_file.read_text(encoding="utf-8"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["session"]["screen_session"], "658429.pdn")
        self.assertEqual(saved["screen_assignments"]["gpu-a-codex-session"], "658429.pdn")

    def test_settings_route_persists_desktop_notifications_without_losing_screen_assignments(self):
        from agent_console import server

        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "agent-console-state.json"
            state_file.write_text(
                json.dumps({"screen_assignments": {"gpu-a-codex-session": "658429.pdn"}}),
                encoding="utf-8",
            )
            with patch.object(server, "SCREEN_ASSIGNMENTS_FILE", state_file):
                client = TestClient(server.create_app())
                response = client.post("/api/settings", json={"desktop_notifications_enabled": True})
                settings = client.get("/api/settings")
            saved = json.loads(state_file.read_text(encoding="utf-8"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(settings.json()["desktop_notifications_enabled"])
        self.assertTrue(saved["desktop_notifications_enabled"])
        self.assertEqual(saved["screen_assignments"]["gpu-a-codex-session"], "658429.pdn")

    def test_completed_transitions_emit_once_after_status_change(self):
        from agent_console import server

        app = server.create_app()
        running = HostSnapshot(
            host_id="gpu-a",
            host_label="GPU A",
            collected_at_ms=123,
            sessions=[
                HostSession(
                    key="gpu-a-codex-session",
                    host_id="gpu-a",
                    platform="codex",
                    source="cli",
                    status="running",
                    confidence="medium",
                    project_name="repo",
                )
            ],
        )
        completed = HostSnapshot(
            host_id="gpu-a",
            host_label="GPU A",
            collected_at_ms=456,
            sessions=[
                HostSession(
                    key="gpu-a-codex-session",
                    host_id="gpu-a",
                    platform="codex",
                    source="cli",
                    status="completed",
                    confidence="medium",
                    project_name="repo",
                )
            ],
        )

        self.assertEqual(server._completed_transitions(app, [running]), [])
        first = server._completed_transitions(app, [completed])
        second = server._completed_transitions(app, [completed])

        self.assertEqual(first, [{"title": "Agent task completed", "body": "GPU A | repo"}])
        self.assertEqual(second, [])

    def test_test_notification_route_invokes_desktop_notification(self):
        from agent_console import server

        with patch.object(server, "_show_desktop_notification") as notify:
            response = TestClient(server.create_app()).post("/api/notifications/test")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["sent"])
        notify.assert_called_once_with("CliDeck test", "Desktop notifications are working.")

    def test_screen_assignment_route_rejects_unknown_screen(self):
        from agent_console import server

        snapshot = HostSnapshot(
            host_id="gpu-a",
            host_label="GPU A",
            collected_at_ms=123,
            sessions=[
                HostSession(
                    key="gpu-a-codex-session",
                    host_id="gpu-a",
                    platform="codex",
                    source="cli",
                    status="idle",
                    confidence="medium",
                )
            ],
            screen_sessions=[{"screen_session": "658429.pdn", "name": "pdn"}],
        )

        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.object(server, "SCREEN_ASSIGNMENTS_FILE", Path(tmp) / "state.json"),
                patch.object(server, "collect_all_hosts_once", return_value=[snapshot]),
            ):
                response = TestClient(server.create_app()).post(
                    "/api/sessions/gpu-a-codex-session/screen-assignment",
                    json={"screen_session": "missing.screen"},
                )

        self.assertEqual(response.status_code, 400)
        self.assertIn("screen", response.json()["detail"])

    def test_start_screen_session_route_starts_remote_codex_screen(self):
        from agent_console import server

        hosts = [
            HostConfig(
                id="gpu-a",
                label="GPU A",
                type="ssh",
                ssh="user@gpu-a",
                password="secret",
                command_timeout_seconds=22,
                enable_actions=True,
            )
        ]
        started = {"started": True, "screen_session": "codex-test", "cwd": "/repo", "command": "codex"}
        with (
            patch.object(server, "load_hosts", return_value=hosts),
            patch.object(server, "start_ssh_screen_session", return_value=(started, None)) as start,
        ):
            response = TestClient(server.create_app()).post(
                "/api/hosts/gpu-a/screen-session",
                json={"cwd": "/repo", "screen_name": "codex-test"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["screen"]["screen_session"], "codex-test")
        start.assert_called_once_with(
            "user@gpu-a",
            cwd="/repo",
            screen_name="codex-test",
            command="codex",
            password="secret",
            timeout_seconds=22,
        )

    def test_start_screen_session_route_requires_enabled_actions(self):
        from agent_console import server

        hosts = [HostConfig(id="gpu-a", label="GPU A", type="ssh", ssh="user@gpu-a", enable_actions=False)]
        with patch.object(server, "load_hosts", return_value=hosts):
            response = TestClient(server.create_app()).post("/api/hosts/gpu-a/screen-session", json={})

        self.assertEqual(response.status_code, 403)
        self.assertIn("disabled", response.json()["detail"])

    def test_resume_command_route_returns_command_without_execution(self):
        from agent_console import server

        snapshot = self.make_snapshot()

        with patch.object(server, "collect_all_hosts_once", return_value=[snapshot]):
            response = TestClient(server.create_app()).post("/api/sessions/local-codex-session/resume-command")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["command"], "cd /repo && codex resume session-1")

    def test_kill_route_exists_and_is_disabled_by_default(self):
        from agent_console import server

        snapshot = self.make_snapshot()

        with patch.object(server, "collect_all_hosts_once", return_value=[snapshot]):
            response = TestClient(server.create_app()).post("/api/sessions/local-codex-session/kill")

        self.assertEqual(response.status_code, 403)
        self.assertIn("disabled", response.json()["detail"])

    def test_screen_input_route_is_disabled_by_default(self):
        from agent_console import server

        snapshot = HostSnapshot(
            host_id="gpu-a",
            host_label="GPU A",
            collected_at_ms=123,
            sessions=[
                HostSession(
                    key="gpu-a-codex-session",
                    host_id="gpu-a",
                    platform="codex",
                    source="screen",
                    status="running",
                    confidence="high",
                    screen_session="1234.pdn",
                )
            ],
        )
        hosts = [HostConfig(id="gpu-a", label="GPU A", type="ssh", ssh="user@gpu-a", enable_actions=False)]

        with (
            patch.object(server, "collect_all_hosts_once", return_value=[snapshot]),
            patch.object(server, "load_hosts", return_value=hosts),
        ):
            response = TestClient(server.create_app()).post(
                "/api/sessions/gpu-a-codex-session/screen-input",
                json={"text": "hello"},
            )

        self.assertEqual(response.status_code, 403)
        self.assertIn("disabled", response.json()["detail"])

    def test_screen_input_route_sends_to_remote_screen_when_actions_enabled(self):
        from agent_console import server

        snapshot = HostSnapshot(
            host_id="gpu-a",
            host_label="GPU A",
            collected_at_ms=123,
            sessions=[
                HostSession(
                    key="gpu-a-codex-session",
                    host_id="gpu-a",
                    platform="codex",
                    source="screen",
                    status="running",
                    confidence="high",
                    screen_session="1234.pdn",
                )
            ],
        )
        hosts = [
            HostConfig(
                id="gpu-a",
                label="GPU A",
                type="ssh",
                ssh="user@gpu-a",
                password="secret",
                command_timeout_seconds=22,
                enable_actions=True,
            )
        ]

        with (
            patch.object(server, "collect_all_hosts_once", return_value=[snapshot]),
            patch.object(server, "load_hosts", return_value=hosts),
            patch.object(server, "send_ssh_screen_input", return_value=None) as send_screen,
        ):
            response = TestClient(server.create_app()).post(
                "/api/sessions/gpu-a-codex-session/screen-input",
                json={"text": "hello"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["sent"])
        send_screen.assert_called_once_with(
            "user@gpu-a",
            "1234.pdn",
            "hello",
            password="secret",
            timeout_seconds=22,
            enter=True,
        )

    def test_screen_key_route_sends_remote_terminal_key_when_actions_enabled(self):
        from agent_console import server

        snapshot = HostSnapshot(
            host_id="gpu-a",
            host_label="GPU A",
            collected_at_ms=123,
            sessions=[
                HostSession(
                    key="gpu-a-codex-session",
                    host_id="gpu-a",
                    platform="codex",
                    source="screen",
                    status="waiting",
                    confidence="high",
                    screen_session="1234.pdn",
                )
            ],
        )
        hosts = [
            HostConfig(
                id="gpu-a",
                label="GPU A",
                type="ssh",
                ssh="user@gpu-a",
                password="secret",
                command_timeout_seconds=22,
                enable_actions=True,
            )
        ]

        with (
            patch.object(server, "collect_all_hosts_once", return_value=[snapshot]),
            patch.object(server, "load_hosts", return_value=hosts),
            patch.object(server, "send_ssh_screen_input", return_value=None) as send_screen,
        ):
            response = TestClient(server.create_app()).post(
                "/api/sessions/gpu-a-codex-session/screen-key",
                json={"action": "enter"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["sent"])
        send_screen.assert_called_once_with(
            "user@gpu-a",
            "1234.pdn",
            "\r",
            password="secret",
            timeout_seconds=22,
            enter=False,
        )

    def test_screen_key_route_sends_remote_approval_choice_sequence(self):
        from agent_console import server

        snapshot = HostSnapshot(
            host_id="gpu-a",
            host_label="GPU A",
            collected_at_ms=123,
            sessions=[
                HostSession(
                    key="gpu-a-codex-session",
                    host_id="gpu-a",
                    platform="codex",
                    source="screen",
                    status="waiting",
                    confidence="high",
                    screen_session="1234.pdn",
                )
            ],
        )
        hosts = [
            HostConfig(
                id="gpu-a",
                label="GPU A",
                type="ssh",
                ssh="user@gpu-a",
                password="secret",
                command_timeout_seconds=22,
                enable_actions=True,
            )
        ]

        with (
            patch.object(server, "collect_all_hosts_once", return_value=[snapshot]),
            patch.object(server, "load_hosts", return_value=hosts),
            patch.object(server, "send_ssh_screen_input", return_value=None) as send_screen,
        ):
            response = TestClient(server.create_app()).post(
                "/api/sessions/gpu-a-codex-session/screen-key",
                json={"action": "acceptForSession"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["sent"])
        send_screen.assert_called_once_with(
            "user@gpu-a",
            "1234.pdn",
            "\x1b[B\r",
            password="secret",
            timeout_seconds=22,
            enter=False,
        )

    def test_screen_input_route_requires_screen_session(self):
        from agent_console import server

        snapshot = self.make_snapshot(key="gpu-a-codex-session")
        snapshot.host_id = "gpu-a"
        snapshot.host_label = "GPU A"
        snapshot.sessions[0].host_id = "gpu-a"
        hosts = [HostConfig(id="gpu-a", label="GPU A", type="ssh", ssh="user@gpu-a", enable_actions=True)]

        with (
            patch.object(server, "collect_all_hosts_once", return_value=[snapshot]),
            patch.object(server, "load_hosts", return_value=hosts),
        ):
            response = TestClient(server.create_app()).post(
                "/api/sessions/gpu-a-codex-session/screen-input",
                json={"text": "hello"},
            )

        self.assertEqual(response.status_code, 409)
        self.assertIn("screen", response.json()["detail"])

    def test_local_input_route_sends_to_tmux_pane(self):
        from agent_console import server

        snapshot = self.make_snapshot()
        snapshot.sessions[0].tmux_pane = "%5"

        with (
            patch.object(server, "collect_all_hosts_once", return_value=[snapshot]),
            patch.object(server, "_send_local_tmux_input", return_value=None) as send_local,
        ):
            response = TestClient(server.create_app()).post(
                "/api/sessions/local-codex-session/local-input",
                json={"text": "hello"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["sent"])
        send_local.assert_called_once_with("%5", "hello", enter=True)

    def test_local_input_route_requires_tmux_pane(self):
        from agent_console import server

        snapshot = self.make_snapshot()

        with patch.object(server, "collect_all_hosts_once", return_value=[snapshot]):
            response = TestClient(server.create_app()).post(
                "/api/sessions/local-codex-session/local-input",
                json={"text": "hello"},
            )

        self.assertEqual(response.status_code, 409)
        self.assertIn("tmux", response.json()["detail"])

    def test_local_codex_prompt_route_starts_app_server_turn(self):
        from agent_console import server
        from agent_console.app_server import AppServerResult

        snapshot = self.make_snapshot()
        result = AppServerResult(mode="app-server", thread_id="session-1", turn_id="turn-1")

        with (
            patch.object(server, "collect_all_hosts_once", return_value=[snapshot]),
            patch.object(server, "_send_local_codex_app_server_prompt", return_value=(result, None)) as send_prompt,
        ):
            response = TestClient(server.create_app()).post(
                "/api/sessions/local-codex-session/local-codex-prompt",
                json={"text": "continue here"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["sent"])
        self.assertEqual(response.json()["mode"], "app-server")
        self.assertEqual(response.json()["turn_id"], "turn-1")
        send_prompt.assert_called_once()
        self.assertEqual(send_prompt.call_args.args[1:], ("session-1", "continue here"))
        self.assertEqual(send_prompt.call_args.kwargs["cwd"], "/repo")

    def test_local_codex_prompt_route_falls_back_to_exec_resume(self):
        from agent_console import server

        snapshot = self.make_snapshot()
        completed = __import__("subprocess").CompletedProcess(
            args=["codex"],
            returncode=0,
            stdout="done",
            stderr="",
        )

        with (
            patch.object(server, "collect_all_hosts_once", return_value=[snapshot]),
            patch.object(server, "_send_local_codex_app_server_prompt", return_value=(None, "app-server failed")),
            patch.object(server, "_send_local_codex_prompt", return_value=(completed, None)) as send_prompt,
        ):
            response = TestClient(server.create_app()).post(
                "/api/sessions/local-codex-session/local-codex-prompt",
                json={"text": "continue here"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["sent"])
        self.assertEqual(response.json()["mode"], "exec-resume")
        send_prompt.assert_called_once_with("session-1", "continue here", cwd="/repo")

    def test_local_codex_prompt_route_rejects_non_codex_session(self):
        from agent_console import server

        snapshot = self.make_snapshot()
        snapshot.sessions[0].platform = "claude"

        with patch.object(server, "collect_all_hosts_once", return_value=[snapshot]):
            response = TestClient(server.create_app()).post(
                "/api/sessions/local-codex-session/local-codex-prompt",
                json={"text": "continue here"},
            )

        self.assertEqual(response.status_code, 409)
        self.assertIn("Codex", response.json()["detail"])

    def test_local_codex_approval_routes_list_and_resolve_pending_requests(self):
        from agent_console import server

        class FakeAppServer:
            def __init__(self):
                self.resolved = []

            def pending_approvals(self, thread_id):
                return [
                    {
                        "id": "approval-1",
                        "thread_id": thread_id,
                        "kind": "command",
                        "title": "Command approval",
                        "detail": "npm test",
                        "reason": "needs permission",
                    }
                ]

            def resolve_approval(self, approval_id, decision, content=None):
                self.resolved.append((approval_id, decision, content))
                return {"resolved": True, "id": approval_id, "decision": decision}

        snapshot = self.make_snapshot()
        fake_app_server = FakeAppServer()
        app = server.create_app()
        app.state.agent_console.codex_app_server = fake_app_server

        with patch.object(server, "collect_all_hosts_once", return_value=[snapshot]):
            client = TestClient(app)
            listed = client.get("/api/sessions/local-codex-session/approvals")
            resolved = client.post(
                "/api/sessions/local-codex-session/approvals/approval-1",
                json={"decision": "accept"},
            )

        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["approvals"][0]["detail"], "npm test")
        self.assertEqual(resolved.status_code, 200)
        self.assertEqual(fake_app_server.resolved, [("approval-1", "accept", None)])

    def test_send_local_codex_prompt_passes_utf8_stdin(self):
        from agent_console import server

        subprocess_module = __import__("subprocess")
        completed = subprocess_module.CompletedProcess(
            args=["codex"],
            returncode=0,
            stdout=b"ok",
            stderr=b"",
        )

        with (
            patch.object(server.shutil, "which", return_value="codex"),
            patch.object(server.subprocess, "run", return_value=completed) as run,
        ):
            result, error = server._send_local_codex_prompt("session-1", "中文 prompt", cwd=None)

        self.assertIsNone(error)
        self.assertEqual(result.stdout, "ok")
        self.assertEqual(run.call_args.kwargs["input"], "中文 prompt".encode("utf-8"))
        self.assertNotIn("text", run.call_args.kwargs)

    def test_local_resume_route_launches_resume_command(self):
        from agent_console import server

        snapshot = self.make_snapshot()

        with (
            patch.object(server, "collect_all_hosts_once", return_value=[snapshot]),
            patch.object(server, "_launch_local_resume", return_value=None) as launch,
        ):
            response = TestClient(server.create_app()).post("/api/sessions/local-codex-session/local-resume")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["launched"])
        launch.assert_called_once_with("cd /repo && codex resume session-1")

    def test_app_import_mounts_agent_console_route(self):
        import app

        mounted_paths = [route.path for route in app.app.routes]
        self.assertIn("/agent-console", mounted_paths)

    def test_collect_host_uses_local_collector_for_local_hosts(self):
        from agent_console import server

        host = HostConfig(id="local", label="Local", type="local")
        snapshot = HostSnapshot(host_id="local", host_label="Local", collected_at_ms=123)

        with patch.object(server, "collect_local_snapshot", return_value=snapshot) as collect_local:
            self.assertIs(server.collect_host(host), snapshot)

        collect_local.assert_called_once_with("local", "Local")

    def test_collect_host_uses_ssh_collector_for_ssh_hosts(self):
        from agent_console import server

        host = HostConfig(
            id="gpu-a",
            label="GPU A",
            type="ssh",
            ssh="user@gpu-a",
            command_timeout_seconds=22,
        )
        snapshot = HostSnapshot(host_id="gpu-a", host_label="GPU A", collected_at_ms=123)

        with patch.object(server, "collect_ssh_snapshot", return_value=snapshot) as collect_ssh:
            self.assertIs(server.collect_host(host), snapshot)

        collect_ssh.assert_called_once_with("gpu-a", "GPU A", "user@gpu-a", password=None, timeout_seconds=22)

    def test_collect_all_hosts_once_loads_configured_hosts(self):
        from agent_console import server

        host = HostConfig(id="local", label="Local", type="local")
        snapshot = HostSnapshot(host_id="local", host_label="Local", collected_at_ms=123)

        with (
            patch.object(server, "load_hosts", return_value=[host]) as load,
            patch.object(server, "collect_host", return_value=snapshot) as collect,
        ):
            self.assertEqual(server.collect_all_hosts_once(), [snapshot])

        load.assert_called_once_with()
        collect.assert_called_once_with(host)

    def test_snapshot_isolates_malformed_host_errors(self):
        from agent_console import server

        local = HostConfig(id="local", label="Local", type="local")
        bad = HostConfig(id="bad", label="Bad SSH", type="ssh")
        local_snapshot = HostSnapshot(
            host_id="local",
            host_label="Local",
            collected_at_ms=123,
            sessions=[
                HostSession(
                    key="local:codex:abc",
                    host_id="local",
                    platform="codex",
                    source="codex",
                    status="idle",
                    confidence="high",
                )
            ],
        )

        with (
            patch.object(server, "load_hosts", return_value=[local, bad]),
            patch.object(server, "collect_local_snapshot", return_value=local_snapshot),
        ):
            response = TestClient(server.create_app(), raise_server_exceptions=False).get("/api/snapshot")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual([host["host_id"] for host in data["hosts"]], ["local", "bad"])
        self.assertEqual(data["counts"]["total"], 1)
        self.assertEqual(data["counts"]["idle"], 1)

        bad_host = data["hosts"][1]
        self.assertEqual(bad_host["host_label"], "Bad SSH")
        self.assertIsInstance(bad_host["collected_at_ms"], int)
        self.assertGreater(bad_host["collected_at_ms"], 1_700_000_000_000)
        self.assertEqual(bad_host["sessions"], [])
        self.assertEqual(bad_host["errors"][0]["host_id"], "bad")
        self.assertIn("requires an ssh target", bad_host["errors"][0]["message"])


if __name__ == "__main__":
    unittest.main()
