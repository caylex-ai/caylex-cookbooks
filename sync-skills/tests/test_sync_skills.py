from __future__ import annotations

import importlib.util
import io
import sys
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).parents[1] / "sync_skills.py"
SPEC = importlib.util.spec_from_file_location("sync_skills", MODULE_PATH)
assert SPEC and SPEC.loader
sync_skills = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = sync_skills
SPEC.loader.exec_module(sync_skills)


class FakeClient:
    def __init__(self, pages: list[list[dict]] | None = None) -> None:
        self.pages = pages or [[]]
        self.calls: list[tuple[str, str, list[tuple[str, str]] | None]] = []

    def request(self, method, path, *, query=None, body=None, content_type=None):
        self.calls.append((method, path, query))
        if method == "GET":
            page = int(dict(query or []).get("page", "1"))
            return self.pages[page - 1] if page <= len(self.pages) else []
        return None


class FrontmatterTests(unittest.TestCase):
    def test_parses_quoted_name_and_ignores_body_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "SKILL.md"
            path.write_text(
                "---\ndescription: example\nname: \"Review: #1\"\n---\nname: Wrong\n",
                encoding="utf-8",
            )
            self.assertEqual(sync_skills.parse_frontmatter_name(path), "Review: #1")

    def test_parses_yaml_single_quote_escaping_and_plain_comment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            quoted = root / "quoted.md"
            quoted.write_text("---\nname: 'It''s Ready'\n---\n", encoding="utf-8")
            plain = root / "plain.md"
            plain.write_text("---\nname: Code Review # note\n---\n", encoding="utf-8")
            self.assertEqual(sync_skills.parse_frontmatter_name(quoted), "It's Ready")
            self.assertEqual(sync_skills.parse_frontmatter_name(plain), "Code Review")

    def test_rejects_name_outside_frontmatter(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "SKILL.md"
            path.write_text("---\ndescription: x\n---\nname: Nope\n", encoding="utf-8")
            with self.assertRaises(sync_skills.SyncError):
                sync_skills.parse_frontmatter_name(path)


class BundleTests(unittest.TestCase):
    def _make_skill(self, root: Path) -> object:
        directory = root / "review"
        directory.mkdir()
        skill_md = directory / "SKILL.md"
        skill_md.write_text(
            "---\nname: Code Review\ndescription: Review code.\n---\n",
            encoding="utf-8",
        )
        return sync_skills.LocalSkill(
            "Code Review", "code-review", directory, skill_md
        )

    def test_raw_skill_md_when_no_extra_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            skill = self._make_skill(Path(temporary))
            filename, content_type, body = sync_skills.build_upload(skill)
            self.assertEqual(filename, "SKILL.md")
            self.assertEqual(content_type, "text/markdown")
            self.assertIn(b"name: Code Review", body)

    def test_zip_contains_skill_md_and_bundled_files_at_archive_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            skill = self._make_skill(Path(temporary))
            scripts = skill.directory / "scripts"
            scripts.mkdir()
            (scripts / "check.py").write_text("print('ok')\n", encoding="utf-8")
            filename, content_type, body = sync_skills.build_upload(skill)
            self.assertEqual(filename, "review.zip")
            self.assertEqual(content_type, "application/zip")
            with zipfile.ZipFile(io.BytesIO(body)) as archive:
                self.assertEqual(
                    sorted(archive.namelist()), ["SKILL.md", "scripts/check.py"]
                )


class ApiBehaviorTests(unittest.TestCase):
    def test_owner_names_and_skill_identifiers_are_url_encoded(self):
        self.assertEqual(
            sync_skills.owner_path("project", "Support EU"),
            "/projects/by-name/Support%20EU/skills",
        )
        client = FakeClient(
            [[{"id": "1", "name": "Code / Review", "slug": "code-review"}]]
        )
        with tempfile.TemporaryDirectory() as temporary:
            skill_dir = Path(temporary) / "skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                "---\nname: Code / Review\ndescription: d\n---\n", encoding="utf-8"
            )
            sync_skills.sync(
                client=client,
                mode="project",
                owner="Support EU",
                skills_dir=Path(temporary),
                prune=False,
                dry_run=False,
            )
        self.assertEqual(client.calls[-1][0], "PUT")
        self.assertTrue(client.calls[-1][1].endswith("/code-review"))

    def test_owner_name_with_slash_is_rejected(self):
        with self.assertRaisesRegex(sync_skills.SyncError, "containing '/'"):
            sync_skills.owner_path("global", "Support / EU")

    def test_list_paginates_until_short_page(self):
        full = [{"id": str(i)} for i in range(sync_skills.PAGE_SIZE)]
        client = FakeClient([full, [{"id": "last"}]])
        result = sync_skills.list_all_remote(client, "/skills")
        self.assertEqual(len(result), sync_skills.PAGE_SIZE + 1)
        self.assertEqual([dict(call[2])["page"] for call in client.calls], ["1", "2"])

    def test_prune_requires_flag_and_dry_run_never_mutates(self):
        remote = [{"id": "remote", "name": "Old Skill", "slug": "old-skill"}]
        with tempfile.TemporaryDirectory() as temporary:
            no_prune = FakeClient([remote])
            sync_skills.sync(
                client=no_prune,
                mode="global",
                owner="Support Navigator",
                skills_dir=Path(temporary),
                prune=False,
                dry_run=False,
            )
            self.assertEqual([call[0] for call in no_prune.calls], ["GET"])

            preview = FakeClient([remote])
            sync_skills.sync(
                client=preview,
                mode="global",
                owner="Support Navigator",
                skills_dir=Path(temporary),
                prune=True,
                dry_run=True,
            )
            self.assertEqual([call[0] for call in preview.calls], ["GET"])

    def test_http_error_includes_status_and_response_body(self):
        error = urllib.error.HTTPError(
            "https://example.test/skills",
            422,
            "Unprocessable",
            {},
            io.BytesIO(b'{"detail":"bad skill"}'),
        )
        client = sync_skills.CaylexClient("https://example.test", "token")
        with mock.patch.object(
            sync_skills.urllib.request, "urlopen", side_effect=error
        ):
            with self.assertRaisesRegex(sync_skills.SyncError, "HTTP 422.*bad skill"):
                client.request("GET", "/skills")


if __name__ == "__main__":
    unittest.main()
