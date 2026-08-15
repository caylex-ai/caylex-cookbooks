import unittest
import urllib.parse

from copy_tool_permissions import CookbookError, copy_permissions, match_permissions


def page(items, *, has_next=False, next_cursor=None):
    return {
        "items": items,
        "meta": {"has_next": has_next, "next_cursor": next_cursor},
    }


class FakeAPI:
    def __init__(self, *, include_target_instance=True) -> None:
        self.calls = []
        self.include_target_instance = include_target_instance

    def __call__(self, base_url, token, path, *, method="GET", payload=None):
        self.calls.append((path, method, payload))
        route, _, query = path.partition("?")
        params = urllib.parse.parse_qs(query)
        cursor = params.get("cursor", [None])[0]
        if route == "/projects":
            if cursor is None:
                return page(
                    [{"id": "source-project", "name": "Model"}],
                    has_next=True,
                    next_cursor="projects-2",
                )
            return page([{"id": "target-project", "name": "Customer"}])
        if route == "/navigator-instances":
            project_id = params["project_id"][0]
            if project_id == "source-project":
                if cursor is None:
                    return page([], has_next=True, next_cursor="source-navs-2")
                return page(
                    [
                        {
                            "id": "source-instance",
                            "navigator_id": "navigator-1",
                            "navigator_name": "Support",
                        }
                    ]
                )
            if project_id == "target-project":
                instances = (
                    [
                        {
                            "id": "target-instance",
                            "navigator_id": "navigator-1",
                            "navigator_name": "Support",
                        }
                    ]
                    if self.include_target_instance
                    else []
                )
                return page(instances)
        if route == "/navigator-instances/source-instance/tool-permissions":
            return {
                "permissions": [
                    {
                        "tool_id": "source-tool-id",
                        "server_name": "CRM",
                        "tool_name": "lookup_customer",
                        "mode": "require_approval",
                    },
                    {
                        "tool_id": "same-id",
                        "server_name": "CRM",
                        "tool_name": "update_customer",
                        "mode": "disabled",
                    },
                    {
                        "tool_id": "missing-source-tool",
                        "server_name": "Other",
                        "tool_name": "not_connected",
                        "mode": "always_execute",
                    },
                ]
            }
        if route == "/navigator-instances/target-instance/tool-permissions":
            if method == "GET":
                return {
                    "permissions": [
                        {
                            "tool_id": "different-target-id",
                            "server_name": "CRM",
                            "tool_name": "lookup_customer",
                            "mode": "always_execute",
                        },
                        {
                            "tool_id": "same-id",
                            "server_name": "CRM",
                            "tool_name": "update_customer",
                            "mode": "always_execute",
                        },
                    ]
                }
            return {"id": "target-instance"}
        raise AssertionError(f"Unexpected request: {method} {path}")


def run_copy(api, *, dry_run):
    return copy_permissions(
        base_url="https://example.test/api/v1",
        token="platform-secret",
        source_project_id=None,
        source_project_name="Model",
        target_project_ids=[],
        target_project_names=["Customer"],
        navigator_id=None,
        navigator_name="Support",
        dry_run=dry_run,
        require_complete_match=False,
        request_fn=api,
    )


class CopyToolPermissionsTests(unittest.TestCase):
    def test_matches_changed_tool_ids_by_stable_names_and_applies(self) -> None:
        api = FakeAPI()
        result = run_copy(api, dry_run=False)

        put = next(call for call in api.calls if call[1] == "PUT")
        self.assertEqual(
            put[2],
            {
                "permissions": [
                    {
                        "tool_id": "different-target-id",
                        "mode": "require_approval",
                    },
                    {"tool_id": "same-id", "mode": "disabled"},
                ]
            },
        )
        self.assertEqual(result["targets"][0]["matched"], 2)
        self.assertEqual(
            result["targets"][0]["unmatched"][0]["tool_id"],
            "missing-source-tool",
        )
        self.assertTrue(result["targets"][0]["applied"])

    def test_dry_run_never_updates_permissions(self) -> None:
        api = FakeAPI()
        result = run_copy(api, dry_run=True)
        self.assertFalse(any(call[1] == "PUT" for call in api.calls))
        self.assertFalse(result["targets"][0]["applied"])

    def test_missing_target_instance_is_clear_error(self) -> None:
        with self.assertRaisesRegex(
            CookbookError, "has no instance in project 'Customer'"
        ):
            run_copy(FakeAPI(include_target_instance=False), dry_run=True)

    def test_duplicate_stable_identity_fails_instead_of_guessing(self) -> None:
        source = [
            {
                "tool_id": "one",
                "server_name": "CRM",
                "tool_name": "lookup",
                "mode": "disabled",
            },
            {
                "tool_id": "two",
                "server_name": "CRM",
                "tool_name": "lookup",
                "mode": "always_execute",
            },
        ]
        target = [
            {
                "tool_id": "target",
                "server_name": "CRM",
                "tool_name": "lookup",
                "mode": "always_execute",
            }
        ]
        with self.assertRaisesRegex(CookbookError, "duplicate stable"):
            match_permissions(source, target)

    def test_conflicting_same_id_does_not_override_stable_identity(self) -> None:
        source = [
            {
                "tool_id": "reused-id",
                "server_name": "CRM",
                "tool_name": "lookup",
                "mode": "require_approval",
            }
        ]
        target = [
            {
                "tool_id": "reused-id",
                "server_name": "Other",
                "tool_name": "delete",
                "mode": "always_execute",
            },
            {
                "tool_id": "correct-id",
                "server_name": "CRM",
                "tool_name": "lookup",
                "mode": "always_execute",
            },
        ]
        updates, unmatched = match_permissions(source, target)
        self.assertEqual(
            updates,
            [{"tool_id": "correct-id", "mode": "require_approval"}],
        )
        self.assertEqual(unmatched, [])


if __name__ == "__main__":
    unittest.main()
