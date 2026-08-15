import json
import stat
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from generate_navigator_api_keys import CookbookError, generate_keys


def page(items, *, has_next=False, next_cursor=None):
    return {
        "items": items,
        "meta": {"has_next": has_next, "next_cursor": next_cursor},
    }


class FakeAPI:
    def __init__(self) -> None:
        self.calls = []

    def __call__(self, base_url, token, path, *, method="GET", payload=None):
        self.calls.append((path, method, payload))
        route, _, query = path.partition("?")
        params = urllib.parse.parse_qs(query)
        cursor = params.get("cursor", [None])[0]
        if route == "/projects":
            if cursor is None:
                return page(
                    [{"id": "other", "name": "Other"}],
                    has_next=True,
                    next_cursor="projects-2",
                )
            return page([{"id": "project-1", "name": "Customer Project"}])
        if route == "/navigator-instances":
            self._assert_project_id(params)
            if cursor is None:
                return page(
                    [
                        {
                            "id": "instance-new",
                            "navigator_id": "navigator-1",
                            "navigator_name": "Support",
                        }
                    ],
                    has_next=True,
                    next_cursor="navigators-2",
                )
            return page(
                [
                    {
                        "id": "instance-existing",
                        "navigator_id": "navigator-2",
                        "navigator_name": "Sales",
                    }
                ]
            )
        if route == "/navigator-instances/instance-new/api-keys":
            if method == "GET":
                return page([])
            return {
                "id": "key-new",
                "preview": "ck_new…",
                "key": "ck_key-new.full-one-time-secret",
            }
        if route == "/navigator-instances/instance-existing/api-keys":
            return page([{"id": "key-existing", "name": "runtime"}])
        raise AssertionError(f"Unexpected request: {method} {path}")

    def _assert_project_id(self, params):
        if params.get("project_id") != ["project-1"]:
            raise AssertionError("Missing resolved project_id")


class GenerateNavigatorApiKeysTests(unittest.TestCase):
    def test_complete_pagination_idempotence_and_secure_output(self) -> None:
        api = FakeAPI()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "keys.json"
            result = generate_keys(
                base_url="https://example.test/api/v1",
                token="platform-secret",
                project_id=None,
                project_name="Customer Project",
                key_name="runtime",
                description=None,
                expires_at=None,
                output_path=output,
                print_secrets=False,
                request_fn=api,
            )

            self.assertEqual(len(result["created"]), 1)
            self.assertEqual(
                result["created"][0]["key"], "ck_key-new.full-one-time-secret"
            )
            self.assertEqual(
                result["skipped_existing"][0]["navigator_instance_id"],
                "instance-existing",
            )
            self.assertEqual(json.loads(output.read_text()), result)
            self.assertEqual(
                stat.S_IMODE(output.stat().st_mode),
                stat.S_IRUSR | stat.S_IWUSR,
            )
            posts = [call for call in api.calls if call[1] == "POST"]
            self.assertEqual(len(posts), 1)
            self.assertIn("instance-new", posts[0][0])

    def test_requires_explicit_secret_destination(self) -> None:
        with self.assertRaisesRegex(CookbookError, "exactly one"):
            generate_keys(
                base_url="https://example.test/api/v1",
                token="platform-secret",
                project_id="project-1",
                project_name=None,
                key_name="runtime",
                description=None,
                expires_at=None,
                output_path=None,
                print_secrets=False,
                request_fn=FakeAPI(),
            )

    def test_stdout_mode_emits_created_key_before_later_failure(self) -> None:
        emitted = []

        def failing_api(base_url, token, path, *, method="GET", payload=None):
            route, _, _query = path.partition("?")
            if route == "/projects/project-1":
                return {"id": "project-1", "name": "Customer Project"}
            if route == "/navigator-instances":
                return page(
                    [
                        {"id": "instance-1", "navigator_name": "First"},
                        {"id": "instance-2", "navigator_name": "Second"},
                    ]
                )
            if route == "/navigator-instances/instance-1/api-keys":
                if method == "GET":
                    return page([])
                return {"id": "key-1", "preview": "ck_one…", "key": "ck_first"}
            if route == "/navigator-instances/instance-2/api-keys":
                if method == "GET":
                    return page([])
                raise CookbookError("simulated later failure")
            raise AssertionError(path)

        with self.assertRaisesRegex(CookbookError, "later failure"):
            generate_keys(
                base_url="https://example.test/api/v1",
                token="platform-secret",
                project_id="project-1",
                project_name=None,
                key_name="runtime",
                description=None,
                expires_at=None,
                output_path=None,
                print_secrets=True,
                request_fn=failing_api,
                secret_fn=emitted.append,
            )

        self.assertEqual([record["key"] for record in emitted], ["ck_first"])

    def test_project_with_no_navigator_instances_is_clear_error(self) -> None:
        def empty_api(base_url, token, path, *, method="GET", payload=None):
            if path == "/projects/project-1":
                return {"id": "project-1", "name": "Empty Project"}
            if path.startswith("/navigator-instances?"):
                return page([])
            raise AssertionError(path)

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(CookbookError, "has no navigator instances"):
                generate_keys(
                    base_url="https://example.test/api/v1",
                    token="platform-secret",
                    project_id="project-1",
                    project_name=None,
                    key_name="runtime",
                    description=None,
                    expires_at=None,
                    output_path=Path(directory) / "keys.json",
                    print_secrets=False,
                    request_fn=empty_api,
                )


if __name__ == "__main__":
    unittest.main()
