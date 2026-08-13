import json
import unittest

from export_notion_sync import build_export, extract_manifest, normalize_notion_id


ROOT_ID = "11111111111111111111111111111111"
CHILD_ID = "22222222222222222222222222222222"
DATABASE_ID = "33333333333333333333333333333333"
ROW_ID = "44444444444444444444444444444444"
SOURCE_URL = "collection://aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def tool_call(
    call_id: str,
    name: str,
    parameters: dict,
    output: dict,
) -> dict:
    return {
        "id": call_id,
        "success": True,
        "parameters": parameters,
        "result_data": {
            "status": "success",
            "text": json.dumps(output),
        },
        "tool": {
            "name": name,
            "server": {"name": "Notion"},
        },
    }


class ExportNotionSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = {
            "schema_version": "1.0",
            "source": "notion",
            "fetched_resources": [
                {
                    "title": "Root",
                    "type": "page",
                    "id": ROOT_ID,
                    "url": f"https://app.notion.com/p/{ROOT_ID}",
                },
                {
                    "title": "Child",
                    "type": "page",
                    "id": CHILD_ID,
                    "url": f"https://app.notion.com/p/{CHILD_ID}",
                },
                {
                    "title": "Inline database",
                    "type": "database",
                    "id": DATABASE_ID,
                    "url": f"https://app.notion.com/p/{DATABASE_ID}",
                },
            ],
            "unresolved_requests": [],
        }
        self.task_status = {
            "task_id": "task-1",
            "session_id": "session-1",
            "status": "COMPLETED",
            "report": (
                "Finished collecting resources.\n\n"
                + json.dumps(self.manifest, separators=(",", ":"))
                + "\nTrailing text is ignored."
            ),
        }
        root_content = (
            f'<page url="https://app.notion.com/p/{CHILD_ID}">\n'
            f'<database url="https://app.notion.com/p/{DATABASE_ID}" '
            f'inline="true" data-source-url="{SOURCE_URL}">'
        )
        database_content = (
            f'<database url="https://app.notion.com/p/{DATABASE_ID}" inline="true">\n'
            f'<data-source url="{{{{{SOURCE_URL}}}}}">'
        )
        self.trace = {
            "tool_call_groups": [
                {
                    "id": "group-1",
                    "tool_calls": [
                        tool_call(
                            "fetch-root",
                            "notion-fetch",
                            {"id": ROOT_ID},
                            {
                                "metadata": {"type": "page"},
                                "title": "Root",
                                "url": f"https://app.notion.com/p/{ROOT_ID}",
                                "text": root_content,
                            },
                        ),
                        tool_call(
                            "fetch-child",
                            "notion-fetch",
                            {"id": CHILD_ID},
                            {
                                "metadata": {"type": "page"},
                                "title": "Child",
                                "url": f"https://app.notion.com/p/{CHILD_ID}",
                                "text": "Child body",
                            },
                        ),
                        tool_call(
                            "fetch-db",
                            "notion-fetch",
                            {"id": DATABASE_ID},
                            {
                                "metadata": {"type": "database"},
                                "title": "Inline database",
                                "url": f"https://app.notion.com/p/{DATABASE_ID}",
                                "text": database_content,
                            },
                        ),
                        tool_call(
                            "query-db",
                            "notion-query-data-sources",
                            {
                                "data": {
                                    "query": f'SELECT * FROM "{SOURCE_URL}"',
                                    "data_source_urls": [SOURCE_URL],
                                }
                            },
                            {
                                "data_source_ids": [SOURCE_URL],
                                "has_more": False,
                                "results": [
                                    {
                                        "Name": "Row",
                                        "url": f"https://app.notion.com/p/{ROW_ID}",
                                    }
                                ],
                            },
                        ),
                        tool_call(
                            "comments-root",
                            "notion-get-comments",
                            {"page_id": ROOT_ID, "include_all_blocks": True},
                            {"discussions": [{"id": "discussion-1", "comments": ["Note"]}]},
                        ),
                    ],
                }
            ],
            "meta": {"total_groups": 1, "has_next": False},
        }

    def test_extracts_manifest_after_prose(self) -> None:
        self.assertEqual(extract_manifest(self.task_status), self.manifest)

    def test_normalizes_ids_and_urls(self) -> None:
        self.assertEqual(
            normalize_notion_id("11111111-1111-1111-1111-111111111111"),
            ROOT_ID,
        )
        self.assertEqual(
            normalize_notion_id(f"https://app.notion.com/p/{ROOT_ID}?pvs=204"),
            ROOT_ID,
        )

    def test_groups_fetches_queries_and_comments_by_resource(self) -> None:
        exported = build_export(self.task_status, self.trace)
        files = {item["id"]: item for item in exported["files"]}

        self.assertEqual(
            files[ROOT_ID]["content"].splitlines()[0],
            f'<page url="https://app.notion.com/p/{CHILD_ID}">',
        )
        self.assertEqual(
            files[ROOT_ID]["comments"][0]["tool_call_id"],
            "comments-root",
        )
        self.assertEqual(
            {item["id"] for item in files[ROOT_ID]["related_resources"]},
            {CHILD_ID, DATABASE_ID},
        )

        database = files[DATABASE_ID]
        self.assertEqual(len(database["database_queries"]), 1)
        self.assertEqual(database["database_queries"][0]["tool_call_id"], "query-db")
        self.assertEqual(
            database["database_queries"][0]["row_links"],
            [f"https://app.notion.com/p/{ROW_ID}"],
        )
        self.assertEqual(files[ROOT_ID]["database_queries"], [])
        self.assertEqual(exported["unmatched_tool_calls"], [])


if __name__ == "__main__":
    unittest.main()
