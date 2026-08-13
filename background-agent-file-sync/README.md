# Background Agent File Sync

Convert a completed Caylex background task and its persisted Notion tool trace
into resource-centric JSON suitable for downstream indexing.

The exporter:

- extracts a `schema_version: "1.0"` resource manifest from the agent report,
  even when prose precedes the JSON;
- fetches every page of the task's raw tool trace;
- groups successful `notion-fetch` responses by normalized Notion page ID;
- attaches child-page and database relationships found in Notion's enhanced
  Markdown;
- attaches `notion-query-data-sources` and `notion-query-database-view`
  responses to their canonical database owner;
- attaches `notion-get-comments` responses to their page; and
- records unmatched calls instead of guessing their owner.

The script uses only the Python standard library and requires Python 3.10 or
newer.

## Expected agent manifest

The background agent's final report must contain an object beginning with:

```json
{
  "schema_version": "1.0",
  "source": "notion",
  "fetched_resources": [
    {
      "title": "Customer Readiness",
      "type": "page",
      "id": "notion-page-id",
      "url": "https://app.notion.com/..."
    }
  ],
  "unresolved_requests": []
}
```

Each manifest resource must have a matching successful `notion-fetch` call in
the trace.

## Recommended two-stage workflow

### 1. Submit and capture the background task

Set credentials in the environment rather than passing them on the command
line. The runner submits the task, waits for a terminal status, obtains its
`session_id`, downloads every page of the tool trace, and writes three files:
`submission.json`, `task-status.json`, and `raw-trace.json`.

```bash
export CAYLEX_PLATFORM_TOKEN="your_platform_access_token"
export CAYLEX_API_KEY="ck_your_navigator_api_key"
export CAYLEX_USER_EMAIL="user@example.com"

python3 run_background_file_sync.py \
  --prompt "Find and fetch every Notion page related to the requested topic." \
  --skill-ref "your-notion-sync-skill" \
  --approval-mode exclude \
  --output-dir run-output
```

For a longer prompt, save it in a file and pass `--prompt-file prompt.txt`.
The default API base URL is `https://api.caylex.ai/api/v1`; use `--base-url`
for another environment.

### 2. Build the resource-centric export

Pass the captured task response and trace to the exporter:

```bash
python3 export_notion_sync.py \
  --task-status-file run-output/task-status.json \
  --trace-file run-output/raw-trace.json \
  --output output/notion-files.json
```

## Export an existing task directly

If a background task already exists, the exporter can fetch its status and
trace itself:

```bash
export CAYLEX_PLATFORM_TOKEN="your_platform_access_token"

python3 export_notion_sync.py \
  --task-id "your-background-task-id" \
  --output output/notion-files.json
```

## Output

```json
{
  "schema_version": "1.0",
  "source": "notion",
  "task_id": "...",
  "session_id": "...",
  "files": [
    {
      "id": "...",
      "name": "Customer Readiness",
      "link": "https://app.notion.com/...",
      "type": "page",
      "content": "...",
      "related_resources": [],
      "database_queries": [],
      "comments": [],
      "fetch_tool_call_ids": []
    }
  ],
  "unresolved_requests": [],
  "unmatched_tool_calls": []
}
```

Database query responses remain structured under `database_queries`; they are
not flattened into the page body. This preserves query provenance and avoids
mixing database rows with page text.

## Test

```bash
python3 -m unittest -v
```
