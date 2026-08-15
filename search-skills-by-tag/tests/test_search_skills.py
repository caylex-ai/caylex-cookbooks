from __future__ import annotations

import importlib.util
import io
import json
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).parents[1] / "search_skills.py"
SPEC = importlib.util.spec_from_file_location("search_skills", MODULE_PATH)
assert SPEC and SPEC.loader
search_skills = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(search_skills)


class SearchTests(unittest.TestCase):
    def test_project_mode_repeats_url_encoded_scopes_and_pagination(self):
        calls: list[str] = []

        def request(url, token, timeout):
            calls.append(url)
            return [{"name": "one"}] if len(calls) == 1 else []

        results = search_skills.search(
            base_url="https://example.test/api/v1/",
            token="secret",
            mode="project",
            tag="finance & tax",
            scopes=["Support Team", "00000000-0000-0000-0000-000000000001"],
            page=2,
            size=1,
            all_pages=True,
            timeout=10,
            request_fn=request,
        )
        self.assertEqual(results, [{"name": "one"}])
        first = urllib.parse.urlsplit(calls[0])
        self.assertEqual(first.path, "/api/v1/skills")
        self.assertEqual(
            urllib.parse.parse_qs(first.query),
            {
                "tag": ["finance & tax"],
                "project": [
                    "Support Team",
                    "00000000-0000-0000-0000-000000000001",
                ],
                "page": ["2"],
                "size": ["1"],
            },
        )
        self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlsplit(calls[1]).query)["page"], ["3"])

    def test_global_mode_uses_canonical_endpoint_and_server_default_scope(self):
        calls: list[str] = []

        def request(url, token, timeout):
            calls.append(url)
            return []

        search_skills.search(
            base_url="https://example.test/api/v1",
            token="secret",
            mode="global",
            tag="Finance",
            scopes=None,
            page=1,
            size=50,
            all_pages=False,
            timeout=60,
            request_fn=request,
        )
        split = urllib.parse.urlsplit(calls[0])
        self.assertEqual(split.path, "/api/v1/navigator-global-skills")
        self.assertNotIn("navigator", urllib.parse.parse_qs(split.query))

    def test_single_page_does_not_fetch_again_when_full(self):
        calls = 0

        def request(url, token, timeout):
            nonlocal calls
            calls += 1
            return [{"id": 1}, {"id": 2}]

        result = search_skills.search(
            base_url="https://example.test",
            token="secret",
            mode="project",
            tag="finance",
            scopes=["all"],
            page=1,
            size=2,
            all_pages=False,
            timeout=60,
            request_fn=request,
        )
        self.assertEqual(len(result), 2)
        self.assertEqual(calls, 1)

    def test_http_error_preserves_status_and_json_detail(self):
        error = urllib.error.HTTPError(
            "https://example.test/skills",
            404,
            "Not found",
            {},
            io.BytesIO(b'{"detail":"Project missing"}'),
        )
        with mock.patch.object(
            search_skills.urllib.request, "urlopen", side_effect=error
        ):
            with self.assertRaisesRegex(
                search_skills.SearchError, "HTTP 404.*Project missing"
            ):
                search_skills.request_page(
                    "https://example.test/skills", "secret"
                )

    def test_request_rejects_non_array_json(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(
            {"items": []}
        ).encode()
        with mock.patch.object(
            search_skills.urllib.request, "urlopen", return_value=response
        ):
            with self.assertRaisesRegex(
                search_skills.SearchError, "unexpected JSON shape"
            ):
                search_skills.request_page(
                    "https://example.test/skills", "secret"
                )

    def test_parser_accepts_repeated_mode_specific_scope(self):
        args = search_skills.build_parser().parse_args(
            [
                "--token",
                "secret",
                "global",
                "finance",
                "--navigator",
                "Support",
                "--navigator",
                "support_api",
                "--all-pages",
            ]
        )
        self.assertEqual(args.mode, "global")
        self.assertEqual(args.scopes, ["Support", "support_api"])
        self.assertTrue(args.all_pages)


if __name__ == "__main__":
    unittest.main()
