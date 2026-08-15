import unittest

from set_server_base_url_override import (
    CookbookError,
    apply_override,
    redact_url,
    resolve_server_instance,
    safe_api_detail,
)


class FakeAPI:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, url, token, *, method="GET", payload=None):
        self.calls.append(
            {"url": url, "token": token, "method": method, "payload": payload}
        )
        if "/projects?" in url:
            return {
                "items": [{"id": "project-1", "name": "Target"}],
                "meta": {
                    "size": 100,
                    "total": 1,
                    "has_next": False,
                    "has_prev": False,
                    "next_cursor": None,
                },
            }
        if "/server-instances?" in url and "cursor=" not in url:
            return {
                "items": [],
                "meta": {
                    "size": 100,
                    "total": 1,
                    "has_next": True,
                    "has_prev": False,
                    "next_cursor": "instances-2",
                },
            }
        if "/server-instances?" in url and "cursor=instances-2" in url:
            return {
                "items": [
                    {
                        "id": "instance-1",
                        "project_id": "project-1",
                        "server_name": "internal-api",
                        "display_name": "Internal API",
                        "server_type": "foundry",
                        "base_url_override": None,
                        "default_base_url": "https://default.example.test/v1",
                    }
                ],
                "meta": {
                    "size": 100,
                    "total": 1,
                    "has_next": False,
                    "has_prev": True,
                    "next_cursor": None,
                },
            }
        if url.endswith("/server-instances/instance-1/base-url-override"):
            self.assert_patch(method, payload)
            return {
                "server_instance_id": "instance-1",
                "base_url_override": payload["base_url_override"],
                "default_base_url": "https://default.example.test/v1",
            }
        raise AssertionError(f"Unexpected request: {method} {url}")

    @staticmethod
    def assert_patch(method, payload):
        if method != "PATCH":
            raise AssertionError(f"Expected PATCH, got {method}")
        if payload != {
            "base_url_override": "https://user:password@new.example.test/v2?key=secret"
        }:
            raise AssertionError(f"Unexpected payload: {payload!r}")


class OverrideTests(unittest.TestCase):
    def test_set_paginates_and_patches_selected_instance(self) -> None:
        api = FakeAPI()
        result = apply_override(
            base_url="https://api.example.test/api/v1",
            token="platform-secret",
            project="Target",
            server="Internal API",
            override="https://user:password@new.example.test/v2?key=secret",
            dry_run=False,
            request_fn=api,
        )

        self.assertEqual(result["status"], "updated")
        self.assertEqual(result["server_instance"]["id"], "instance-1")
        self.assertEqual(result["base_url_override"], "https://new.example.test/v2")
        rendered = str(result)
        self.assertNotIn("password", rendered)
        self.assertNotIn("secret", rendered)
        patch_calls = [call for call in api.calls if call["method"] == "PATCH"]
        self.assertEqual(len(patch_calls), 1)

    def test_dry_run_never_patches(self) -> None:
        api = FakeAPI()
        result = apply_override(
            base_url="https://api.example.test/api/v1",
            token="platform-secret",
            project="Target",
            server="internal-api",
            override=None,
            dry_run=True,
            request_fn=api,
        )
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["action"], "clear")
        self.assertFalse(any(call["method"] == "PATCH" for call in api.calls))

    def test_ambiguous_exact_name_is_rejected_client_side(self) -> None:
        def request(_url, _token, **_kwargs):
            return {
                "items": [
                    {
                        "id": "one",
                        "server_name": "same",
                        "display_name": "First",
                    },
                    {
                        "id": "two",
                        "server_name": "other",
                        "display_name": "same",
                    },
                ],
                "meta": {"has_next": False},
            }

        with self.assertRaisesRegex(CookbookError, "ambiguous"):
            resolve_server_instance(
                "https://api.example.test/api/v1",
                "token",
                "project-1",
                "same",
                request_fn=request,
            )

    def test_redact_url_removes_credentials_query_and_fragment(self) -> None:
        self.assertEqual(
            redact_url("https://user:pass@example.test:8443/v1?token=value#fragment"),
            "https://example.test:8443/v1",
        )

    def test_api_error_detail_does_not_echo_url_secrets(self) -> None:
        detail = safe_api_detail(
            "Rejected https://user:pass@example.test/v1?token=value#fragment"
        )
        self.assertEqual(detail, "Rejected https://example.test/v1")


if __name__ == "__main__":
    unittest.main()
