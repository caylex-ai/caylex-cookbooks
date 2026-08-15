import json
import stat
import tempfile
import unittest
from pathlib import Path

from provision_customer_projects import APIError, CookbookError, provision_customers


class FakeAPI:
    def __init__(self) -> None:
        self.calls = []

    def __call__(self, base_url, token, path, *, method="GET", payload=None):
        self.calls.append((base_url, token, path, method, payload))
        if payload["name"] == "Existing Customer":
            raise APIError(409, method, path)
        return {
            "id": f"project-{payload['name']}",
            "name": payload["name"],
            "seed_project_id": "seed-1",
            "seeded_server_count": 2,
            "seeded_navigator_count": 1,
            "servers": [
                {
                    "server_id": "server-1",
                    "server_name": "Example",
                    "server_instance_id": "server-instance-1",
                }
            ],
            "navigators": [
                {
                    "navigator_id": "navigator-1",
                    "navigator_name": "Support",
                    "navigator_instance_id": "navigator-instance-1",
                    "api_key_id": "key-1",
                    "api_key": "ck_one-time-secret",
                }
            ],
            "warnings": [],
        }


class ProvisionCustomerProjectsTests(unittest.TestCase):
    def test_writes_one_time_keys_with_mode_0600_and_handles_409(self) -> None:
        api = FakeAPI()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "keys.json"
            result = provision_customers(
                base_url="https://example.test/api/v1",
                token="platform-secret",
                customer_names=["New Customer", "Existing Customer"],
                seed_project_id=None,
                seed_project_name="Model Project",
                description=None,
                icon=None,
                output_path=output,
                request_fn=api,
            )

            saved = json.loads(output.read_text())
            self.assertEqual(saved, result)
            self.assertEqual(saved["projects"][0]["name"], "New Customer")
            self.assertEqual(
                saved["projects"][0]["navigators"][0]["api_key"],
                "ck_one-time-secret",
            )
            self.assertEqual(saved["already_existed"], ["Existing Customer"])
            self.assertEqual(
                stat.S_IMODE(output.stat().st_mode),
                stat.S_IRUSR | stat.S_IWUSR,
            )

    def test_refuses_to_overwrite_existing_secret_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "keys.json"
            output.write_text("existing")
            with self.assertRaisesRegex(CookbookError, "already exists"):
                provision_customers(
                    base_url="https://example.test/api/v1",
                    token="platform-secret",
                    customer_names=["New Customer"],
                    seed_project_id="seed-1",
                    seed_project_name=None,
                    description=None,
                    icon=None,
                    output_path=output,
                    request_fn=FakeAPI(),
                )

    def test_rejects_duplicate_customer_names_before_requests(self) -> None:
        api = FakeAPI()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(CookbookError, "must be unique"):
                provision_customers(
                    base_url="https://example.test/api/v1",
                    token="platform-secret",
                    customer_names=["Same", "Same"],
                    seed_project_id="seed-1",
                    seed_project_name=None,
                    description=None,
                    icon=None,
                    output_path=Path(directory) / "keys.json",
                    request_fn=api,
                )
        self.assertEqual(api.calls, [])


if __name__ == "__main__":
    unittest.main()
