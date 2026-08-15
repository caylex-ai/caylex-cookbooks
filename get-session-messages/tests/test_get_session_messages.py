import tempfile
import unittest
from pathlib import Path

from get_session_messages import (
    CookbookError,
    atomic_write_json,
    build_export,
    fetch_message_timeline,
    fetch_sessions,
)


class FakeAPI:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, url, token, **_kwargs):
        self.calls.append(url)
        if "/projects?" in url and "cursor=" not in url:
            return {
                "items": [{"id": "project-1", "name": "Example"}],
                "meta": {
                    "size": 100,
                    "total": 2,
                    "has_next": True,
                    "has_prev": False,
                    "next_cursor": "projects-2",
                },
            }
        if "/projects?" in url and "cursor=projects-2" in url:
            return {
                "items": [{"id": "project-2", "name": "Target"}],
                "meta": {
                    "size": 100,
                    "total": 2,
                    "has_next": False,
                    "has_prev": True,
                    "next_cursor": None,
                },
            }
        if "/assistants/sessions?" in url and "offset=0" in url:
            return {
                "sessions": [{"session_id": "session-1", "session_name": "First"}],
                "count": 2,
            }
        if "/assistants/sessions?" in url and "offset=1" in url:
            return {
                "sessions": [{"session_id": "session-2", "session_name": "Second"}],
                "count": 2,
            }
        if "/sessions/session-1/messages?" in url and "cursor=" not in url:
            return {
                "session": {"session_id": "session-1"},
                "items": [{"message_id": "message-1"}],
                "meta": {
                    "size": 100,
                    "total": 2,
                    "has_next": True,
                    "has_prev": False,
                    "next_cursor": "messages-2",
                },
            }
        if "/sessions/session-1/messages?" in url and "cursor=messages-2" in url:
            return {
                "session": {"session_id": "session-1"},
                "items": [{"message_id": "message-2"}],
                "meta": {
                    "size": 100,
                    "total": 2,
                    "has_next": False,
                    "has_prev": True,
                    "next_cursor": None,
                },
            }
        if "/sessions/session-2/messages?" in url:
            return {
                "session": {"session_id": "session-2"},
                "items": [],
                "meta": {
                    "size": 100,
                    "total": 0,
                    "has_next": False,
                    "has_prev": False,
                    "next_cursor": None,
                },
            }
        raise AssertionError(f"Unexpected request: {url}")


class SessionExporterTests(unittest.TestCase):
    def test_export_paginates_projects_sessions_and_each_timeline(self) -> None:
        api = FakeAPI()
        result = build_export(
            base_url="https://api.example.test/api/v1",
            token="secret-token",
            project="Target",
            timestamp_start="2026-01-01T00:00:00Z",
            timestamp_end="2026-02-01T00:00:00Z",
            view="resolved",
            navigator_instance_id=None,
            is_playground=False,
            request_fn=api,
        )

        self.assertEqual(result["project"], {"id": "project-2", "name": "Target"})
        self.assertEqual(result["session_count"], 2)
        self.assertEqual(
            [m["message_id"] for m in result["sessions"][0]["messages"]],
            ["message-1", "message-2"],
        )
        session_urls = [url for url in api.calls if "/assistants/sessions?" in url]
        self.assertEqual(len(session_urls), 2)
        self.assertTrue(all("is_playground=false" in url for url in session_urls))
        self.assertTrue(
            all("timestamp_start=2026-01-01T00%3A00%3A00Z" in url for url in session_urls)
        )
        self.assertTrue(
            all("view=resolved" in url for url in api.calls if "/messages?" in url)
        )
        self.assertNotIn("secret-token", "\n".join(api.calls))

    def test_session_pagination_rejects_premature_empty_page(self) -> None:
        def request(_url, _token):
            return {"sessions": [], "count": 1}

        with self.assertRaisesRegex(CookbookError, "empty page"):
            fetch_sessions(
                "https://api.example.test/api/v1",
                "token",
                project_id="project-1",
                timestamp_start=None,
                timestamp_end=None,
                navigator_instance_id=None,
                is_playground=None,
                request_fn=request,
            )

    def test_message_pagination_requires_next_cursor(self) -> None:
        def request(_url, _token):
            return {
                "session": {"session_id": "session-1"},
                "items": [],
                "meta": {"total": 1, "has_next": True, "next_cursor": None},
            }

        with self.assertRaisesRegex(CookbookError, "without a next_cursor"):
            fetch_message_timeline(
                "https://api.example.test/api/v1",
                "token",
                "session-1",
                "raw",
                request_fn=request,
            )

    def test_atomic_write_refuses_existing_output_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "export.json"
            output.write_text("original", encoding="utf-8")
            with self.assertRaisesRegex(CookbookError, "already exists"):
                atomic_write_json(output, {"new": True}, force=False)
            self.assertEqual(output.read_text(encoding="utf-8"), "original")


if __name__ == "__main__":
    unittest.main()
