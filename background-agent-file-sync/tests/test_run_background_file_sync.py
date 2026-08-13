import unittest

from run_background_file_sync import (
    fetch_complete_trace,
    run_background_file_sync,
    submit_task,
)


class FakeAPI:
    def __init__(self) -> None:
        self.calls = []
        self.status_calls = 0

    def __call__(self, url, token, *, method="GET", payload=None):
        self.calls.append(
            {
                "url": url,
                "token": token,
                "method": method,
                "payload": payload,
            }
        )
        if url.endswith("/agent-task") and method == "POST":
            return {
                "task_id": "task-1",
                "status": "PENDING",
                "resolved_skill": {
                    "name": "example-sync-skill",
                    "source": "global",
                },
            }
        if url.endswith("/agent-task/task-1"):
            self.status_calls += 1
            if self.status_calls == 1:
                return {
                    "task_id": "task-1",
                    "status": "RUNNING",
                    "session_id": "session-1",
                }
            return {
                "task_id": "task-1",
                "status": "COMPLETED",
                "session_id": "session-1",
                "report": '{"schema_version":"1.0","fetched_resources":[]}',
            }
        if "offset=0" in url:
            return {
                "tool_call_groups": [{"id": "group-1", "tool_calls": []}],
                "meta": {
                    "total_groups": 2,
                    "offset": 0,
                    "limit": 1,
                    "has_next": True,
                },
            }
        if "offset=1" in url:
            return {
                "tool_call_groups": [{"id": "group-2", "tool_calls": []}],
                "meta": {
                    "total_groups": 2,
                    "offset": 1,
                    "limit": 1,
                    "has_next": False,
                },
            }
        raise AssertionError(f"Unexpected request: {method} {url}")


class RunBackgroundFileSyncTests(unittest.TestCase):
    def test_submission_omits_optional_fields_when_absent(self) -> None:
        api = FakeAPI()
        response = submit_task(
            base_url="https://api.example.test/api/v1",
            platform_token="platform-token",
            caylex_api_key="api-key",
            user_email="user@example.com",
            prompt="Fetch requested files.",
            skill_ref=None,
            approval_mode="exclude",
            model=None,
            request_fn=api,
        )

        self.assertEqual(response["task_id"], "task-1")
        self.assertEqual(
            api.calls[0]["payload"],
            {
                "caylex_api_key": "api-key",
                "user_email": "user@example.com",
                "prompt": "Fetch requested files.",
                "approval_mode": "exclude",
            },
        )

    def test_trace_pagination_combines_every_group(self) -> None:
        api = FakeAPI()
        trace = fetch_complete_trace(
            base_url="https://api.example.test/api/v1",
            platform_token="platform-token",
            session_id="session-1",
            request_fn=api,
        )

        self.assertEqual(
            [group["id"] for group in trace["tool_call_groups"]],
            ["group-1", "group-2"],
        )
        self.assertFalse(trace["meta"]["has_next"])

    def test_runs_submit_poll_and_trace_lifecycle(self) -> None:
        api = FakeAPI()
        statuses = []
        submissions = []
        terminal_statuses = []

        submission, status, trace = run_background_file_sync(
            base_url="https://api.example.test/api/v1",
            platform_token="platform-token",
            caylex_api_key="api-key",
            user_email="user@example.com",
            prompt="Fetch requested files.",
            skill_ref="example-sync-skill",
            approval_mode="exclude",
            model=None,
            poll_interval=0,
            timeout=30,
            request_fn=api,
            sleep_fn=lambda _: None,
            status_callback=lambda task_status, session_id: statuses.append(
                (task_status, session_id)
            ),
            submission_callback=submissions.append,
            task_status_callback=terminal_statuses.append,
        )

        self.assertEqual(submission["resolved_skill"]["source"], "global")
        self.assertEqual(status["status"], "COMPLETED")
        self.assertEqual(len(trace["tool_call_groups"]), 2)
        self.assertEqual(
            statuses,
            [("RUNNING", "session-1"), ("COMPLETED", "session-1")],
        )
        self.assertEqual(api.calls[0]["payload"]["skill_ref"], "example-sync-skill")
        self.assertEqual(submissions[0]["task_id"], "task-1")
        self.assertEqual(terminal_statuses[0]["status"], "COMPLETED")


if __name__ == "__main__":
    unittest.main()
