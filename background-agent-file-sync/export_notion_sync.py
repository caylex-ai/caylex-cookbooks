#!/usr/bin/env python3
"""Export a completed Caylex Notion background task into resource-centric JSON."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

TERMINAL_STATUSES = {"COMPLETED", "INCOMPLETE", "FAILED", "CANCELLED"}
MANIFEST_START_RE = re.compile(
    r'\{\s*"schema_version"\s*:\s*"1\.0"',
    re.DOTALL,
)
NOTION_ID_RE = re.compile(
    r"(?<![0-9a-f])"
    r"(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{32})"
    r"(?![0-9a-f])",
    re.IGNORECASE,
)
COLLECTION_RE = re.compile(r"collection://[0-9a-f-]+", re.IGNORECASE)
RESOURCE_TAG_RE = re.compile(r"<(page|database)\b([^>]*)>", re.IGNORECASE)
ATTRIBUTE_RE = re.compile(r'([\w-]+)="([^"]*)"')
NOTION_URL_RE = re.compile(
    r"https://(?:www\.)?(?:notion\.so|app\.notion\.com|[\w-]+\.notion\.site)/[^\s\"<>]+",
    re.IGNORECASE,
)


class ExportError(RuntimeError):
    """Raised when a task or trace cannot be converted safely."""


def normalize_notion_id(value: Any) -> str | None:
    """Return a lowercase, hyphen-free Notion UUID from an ID or URL."""
    if not isinstance(value, str):
        return None
    decoded = urllib.parse.unquote(value)
    match = NOTION_ID_RE.search(decoded)
    return match.group(0).replace("-", "").lower() if match else None


def parse_json_text(value: Any) -> Any:
    """Decode a JSON string when possible, preserving non-JSON text."""
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def extract_manifest(task_status: dict[str, Any]) -> dict[str, Any]:
    """Extract the first schema 1.0 manifest even when prose precedes it."""
    candidates = [
        task_status.get("report"),
        (task_status.get("result") or {}).get("text")
        if isinstance(task_status.get("result"), dict)
        else None,
    ]
    decoder = json.JSONDecoder()
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        match = MANIFEST_START_RE.search(candidate)
        if not match:
            continue
        try:
            manifest, _ = decoder.raw_decode(candidate[match.start() :])
        except json.JSONDecodeError as exc:
            raise ExportError(f"Found manifest marker but JSON was invalid: {exc}") from exc
        if isinstance(manifest, dict) and manifest.get("schema_version") == "1.0":
            return manifest
    raise ExportError('Could not find a valid {"schema_version":"1.0", ...} manifest')


def flatten_tool_calls(trace: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        call
        for group in trace.get("tool_call_groups", [])
        if isinstance(group, dict)
        for call in group.get("tool_calls", [])
        if isinstance(call, dict)
    ]


def tool_name(call: dict[str, Any]) -> str:
    tool = call.get("tool")
    return str(tool.get("name") or "") if isinstance(tool, dict) else ""


def parsed_result(call: dict[str, Any]) -> Any:
    result_data = call.get("result_data")
    if not isinstance(result_data, dict):
        return result_data
    return parse_json_text(result_data.get("text"))


def extract_related_resources(content: Any) -> list[dict[str, Any]]:
    if not isinstance(content, str):
        return []
    relationships: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None, str | None]] = set()
    for match in RESOURCE_TAG_RE.finditer(content):
        relation_type, raw_attributes = match.groups()
        attributes = dict(ATTRIBUTE_RE.findall(raw_attributes))
        url = attributes.get("url")
        resource_id = normalize_notion_id(url)
        key = (relation_type.lower(), resource_id, url)
        if key in seen:
            continue
        seen.add(key)
        relationship: dict[str, Any] = {
            "type": relation_type.lower(),
            "id": resource_id,
            "link": url,
        }
        if relation_type.lower() == "database":
            relationship["inline"] = attributes.get("inline", "").lower() == "true"
            source = attributes.get("data-source-url")
            if source:
                relationship["data_source_urls"] = [source]
        relationships.append(relationship)
    return relationships


def collect_page_urls(value: Any) -> list[str]:
    serialized = (
        json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    )
    return sorted(set(NOTION_URL_RE.findall(serialized)))


def build_export(
    task_status: dict[str, Any],
    trace: dict[str, Any],
) -> dict[str, Any]:
    manifest = extract_manifest(task_status)
    calls = flatten_tool_calls(trace)
    successful_fetches = [
        call
        for call in calls
        if tool_name(call) == "notion-fetch" and call.get("success") is True
    ]

    fetches_by_resource: dict[
        str, list[tuple[dict[str, Any], Any]]
    ] = defaultdict(list)
    for call in successful_fetches:
        output = parsed_result(call)
        output_url = output.get("url") if isinstance(output, dict) else None
        keys = {
            resource_id
            for resource_id in (
                normalize_notion_id((call.get("parameters") or {}).get("id")),
                normalize_notion_id(output_url),
            )
            if resource_id
        }
        for resource_id in keys:
            fetches_by_resource[resource_id].append((call, output))

    files: list[dict[str, Any]] = []
    files_by_id: dict[str, dict[str, Any]] = {}
    for resource in manifest.get("fetched_resources", []):
        if not isinstance(resource, dict):
            continue
        resource_id = normalize_notion_id(
            resource.get("id") or resource.get("notion_id") or resource.get("url")
        )
        if not resource_id:
            raise ExportError(f"Manifest resource has no usable Notion ID: {resource!r}")
        candidates = fetches_by_resource.get(resource_id, [])
        if not candidates:
            raise ExportError(
                f"Manifest resource {resource_id} has no successful notion-fetch response"
            )
        _, output = max(
            candidates,
            key=lambda item: len(
                json.dumps(item[1], ensure_ascii=False, default=str)
            ),
        )
        output_dict = output if isinstance(output, dict) else {}
        metadata = output_dict.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        content = output_dict.get("text", output)
        file_object: dict[str, Any] = {
            "id": resource_id,
            "name": output_dict.get("title") or resource.get("title"),
            "link": output_dict.get("url") or resource.get("url"),
            "type": metadata.get("type") or resource.get("type"),
            "content": content,
            "related_resources": extract_related_resources(content),
            "database_queries": [],
            "comments": [],
            "fetch_tool_call_ids": sorted(
                {
                    str(candidate_call.get("id"))
                    for candidate_call, _ in candidates
                    if candidate_call.get("id")
                }
            ),
        }
        files.append(file_object)
        files_by_id[resource_id] = file_object

    # A collection URL can be mentioned by a containing page and by the database
    # itself. Prefer the fetched database as its canonical owner.
    direct_collection_owners: dict[str, set[str]] = defaultdict(set)
    collection_references: dict[str, set[str]] = defaultdict(set)
    for resource_id, candidates in fetches_by_resource.items():
        for _, output in candidates:
            if not isinstance(output, dict):
                continue
            serialized = json.dumps(output, ensure_ascii=False)
            sources = set(COLLECTION_RE.findall(serialized))
            for source in sources:
                collection_references[source].add(resource_id)
                metadata = output.get("metadata")
                if isinstance(metadata, dict) and metadata.get("type") == "database":
                    direct_collection_owners[source].add(resource_id)

    unmatched_tool_calls: list[dict[str, Any]] = []
    for call in calls:
        name = tool_name(call)
        if call.get("success") is not True:
            continue
        parameters = call.get("parameters")
        parameters = parameters if isinstance(parameters, dict) else {}

        if name in {"notion-query-data-sources", "notion-query-database-view"}:
            data = parameters.get("data")
            data = data if isinstance(data, dict) else parameters
            sources = data.get("data_source_urls") or []
            if isinstance(sources, str):
                sources = [sources]
            sources = [source for source in sources if isinstance(source, str)]
            direct_owners = (
                set().union(
                    *(direct_collection_owners.get(source, set()) for source in sources)
                )
                if sources
                else set()
            )
            owners = direct_owners
            if not owners and sources:
                owners = set().union(
                    *(collection_references.get(source, set()) for source in sources)
                )
            owners &= files_by_id.keys()
            if not owners or (not direct_owners and len(owners) != 1):
                unmatched_tool_calls.append(
                    {
                        "tool_call_id": str(call.get("id") or ""),
                        "tool_name": name,
                        "reason": "database owner was missing or ambiguous",
                        "candidate_resource_ids": sorted(owners),
                        "data_source_urls": sources,
                    }
                )
                continue
            response = parsed_result(call)
            query_record = {
                "tool_call_id": str(call.get("id") or ""),
                "data_source_urls": sources,
                "view_url": data.get("view_url"),
                "query": data.get("query"),
                "response": response,
                "row_links": collect_page_urls(response),
            }
            if len(owners) > 1:
                query_record["shared_owner_ids"] = sorted(owners)
            for owner_id in sorted(owners):
                files_by_id[owner_id]["database_queries"].append(dict(query_record))

        elif name == "notion-get-comments":
            page_id = normalize_notion_id(parameters.get("page_id"))
            if not page_id or page_id not in files_by_id:
                unmatched_tool_calls.append(
                    {
                        "tool_call_id": str(call.get("id") or ""),
                        "tool_name": name,
                        "reason": "comment page was not present in the manifest",
                        "candidate_resource_ids": [page_id] if page_id else [],
                    }
                )
                continue
            files_by_id[page_id]["comments"].append(
                {
                    "tool_call_id": str(call.get("id") or ""),
                    "discussion_id": parameters.get("discussion_id"),
                    "include_all_blocks": bool(parameters.get("include_all_blocks")),
                    "include_resolved": bool(parameters.get("include_resolved")),
                    "response": parsed_result(call),
                }
            )

    return {
        "schema_version": "1.0",
        "source": "notion",
        "task_id": task_status.get("task_id"),
        "session_id": task_status.get("session_id"),
        "files": files,
        "unresolved_requests": manifest.get("unresolved_requests", []),
        "unmatched_tool_calls": unmatched_tool_calls,
    }


def request_json(url: str, token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "caylex-notion-sync-export/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ExportError(f"HTTP {exc.code} from {url}: {detail}") from exc
    if not isinstance(payload, dict):
        raise ExportError(f"Expected a JSON object from {url}")
    return payload


def fetch_live_inputs(
    base_url: str,
    task_id: str,
    token: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    base_url = base_url.rstrip("/")
    task_status = request_json(f"{base_url}/agent-task/{task_id}", token)
    if task_status.get("status") not in TERMINAL_STATUSES:
        raise ExportError(
            f"Task is not terminal: {task_status.get('status')}. Retry after completion."
        )
    session_id = task_status.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise ExportError("Completed task response did not include session_id")

    groups: list[dict[str, Any]] = []
    offset = 0
    while True:
        query = urllib.parse.urlencode({"offset": offset, "limit": 100})
        page = request_json(
            f"{base_url}/queries/query-logs/{session_id}/trace?{query}",
            token,
        )
        page_groups = page.get("tool_call_groups", [])
        if not isinstance(page_groups, list):
            raise ExportError("Trace response tool_call_groups was not a list")
        groups.extend(group for group in page_groups if isinstance(group, dict))
        meta = page.get("meta")
        if not isinstance(meta, dict) or not meta.get("has_next"):
            return task_status, {
                "tool_call_groups": groups,
                "meta": {
                    "total_groups": len(groups),
                    "offset": 0,
                    "limit": 100,
                    "has_next": False,
                },
            }
        offset += int(meta.get("limit") or 100)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export a completed Caylex Notion task into resource-centric JSON."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--task-id", help="Fetch task status and trace from Caylex.")
    source.add_argument(
        "--task-status-file",
        type=Path,
        help="Read a previously saved task status response.",
    )
    parser.add_argument(
        "--trace-file",
        type=Path,
        help="Trace JSON for offline mode; required with --task-status-file.",
    )
    parser.add_argument(
        "--base-url",
        default="https://api.caylex.ai/api/v1",
        help="Caylex API base URL.",
    )
    parser.add_argument(
        "--platform-token-env",
        default="CAYLEX_PLATFORM_TOKEN",
        help="Environment variable containing the platform token.",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--raw-trace-output",
        type=Path,
        help="Optionally save the complete paginated raw trace.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.task_id:
            token = os.environ.get(args.platform_token_env)
            if not token:
                raise ExportError(
                    f"Set {args.platform_token_env} before using --task-id"
                )
            task_status, trace = fetch_live_inputs(
                args.base_url,
                args.task_id,
                token,
            )
        else:
            if not args.trace_file:
                raise ExportError("--trace-file is required with --task-status-file")
            task_status = json.loads(args.task_status_file.read_text())
            trace = json.loads(args.trace_file.read_text())

        exported = build_export(task_status, trace)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(exported, ensure_ascii=False, indent=2) + "\n"
        )
        if args.raw_trace_output:
            args.raw_trace_output.parent.mkdir(parents=True, exist_ok=True)
            args.raw_trace_output.write_text(
                json.dumps(trace, ensure_ascii=False, indent=2) + "\n"
            )
    except (ExportError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "output": str(args.output),
                "files": len(exported["files"]),
                "unmatched_tool_calls": len(exported["unmatched_tool_calls"]),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
