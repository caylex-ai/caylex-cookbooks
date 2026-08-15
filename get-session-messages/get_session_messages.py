#!/usr/bin/env python3
"""Export complete Caylex assistant-session message timelines."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from uuid import UUID

DEFAULT_BASE_URL = "https://api.caylex.ai/api/v1"


class CookbookError(RuntimeError):
    """A safe, actionable command-line error."""


RequestFn = Callable[..., dict[str, Any]]


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except ValueError:
        return False
    return True


def validate_iso_datetime(value: str) -> str:
    """Validate an ISO 8601 date-time and return it unchanged."""
    try:
        parsed = _parse_iso_datetime(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"{value!r} is not a valid ISO 8601 date-time"
        ) from exc
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError(
            f"{value!r} must include a timezone (for example, Z or +00:00)"
        )
    return value


def _parse_iso_datetime(value: str) -> datetime:
    candidate = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    return datetime.fromisoformat(candidate)


def request_json(
    url: str,
    token: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "caylex-session-export/1.0",
    }
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            decoded = json.loads(raw)
            detail = decoded.get("detail", raw) if isinstance(decoded, dict) else raw
        except json.JSONDecodeError:
            detail = raw
        raise CookbookError(f"API request failed with HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise CookbookError(f"Could not reach the Caylex API: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise CookbookError("Caylex API returned invalid JSON") from exc
    if not isinstance(result, dict):
        raise CookbookError("Caylex API returned JSON that was not an object")
    return result


def _url(base_url: str, path: str, params: dict[str, Any] | None = None) -> str:
    result = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    if params:
        result += "?" + urllib.parse.urlencode(params)
    return result


def iter_cursor_items(
    *,
    base_url: str,
    path: str,
    token: str,
    request_fn: RequestFn,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Fetch every item from a Caylex Page envelope."""
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    seen_cursors: set[str] = set()
    while True:
        query = dict(params or {})
        query["size"] = 100
        if cursor is not None:
            query["cursor"] = cursor
        page = request_fn(_url(base_url, path, query), token)
        page_items = page.get("items")
        meta = page.get("meta")
        if not isinstance(page_items, list) or not isinstance(meta, dict):
            raise CookbookError(f"{path} returned an invalid pagination envelope")
        if any(not isinstance(item, dict) for item in page_items):
            raise CookbookError(f"{path} returned a non-object item")
        items.extend(page_items)
        if not meta.get("has_next"):
            return items
        next_cursor = meta.get("next_cursor")
        if not isinstance(next_cursor, str) or not next_cursor:
            raise CookbookError(f"{path} reported another page without a next_cursor")
        if next_cursor in seen_cursors:
            raise CookbookError(f"{path} repeated a pagination cursor")
        seen_cursors.add(next_cursor)
        cursor = next_cursor


def resolve_project(
    base_url: str,
    token: str,
    project: str,
    *,
    request_fn: RequestFn = request_json,
) -> dict[str, Any]:
    """Resolve a project UUID or exact project name."""
    if _is_uuid(project):
        result = request_fn(_url(base_url, f"projects/{project}"), token)
        response_id = result.get("id")
        if (
            not isinstance(response_id, str)
            or not _is_uuid(response_id)
            or UUID(response_id) != UUID(project)
        ):
            raise CookbookError("Project response did not match the requested project ID")
        return result

    projects = iter_cursor_items(
        base_url=base_url,
        path="projects",
        token=token,
        request_fn=request_fn,
    )
    matches = [item for item in projects if item.get("name") == project]
    if not matches:
        raise CookbookError(f"No project has the exact name {project!r}")
    if len(matches) > 1:
        ids = ", ".join(str(item.get("id")) for item in matches)
        raise CookbookError(f"Project name {project!r} is ambiguous; matching IDs: {ids}")
    return matches[0]


def fetch_sessions(
    base_url: str,
    token: str,
    *,
    project_id: str,
    timestamp_start: str | None,
    timestamp_end: str | None,
    navigator_instance_id: str | None,
    is_playground: bool | None,
    request_fn: RequestFn = request_json,
) -> list[dict[str, Any]]:
    """Fetch all offset-paginated assistant sessions."""
    common: dict[str, Any] = {"project_id": project_id}
    if timestamp_start:
        common["timestamp_start"] = timestamp_start
    if timestamp_end:
        common["timestamp_end"] = timestamp_end
    if navigator_instance_id:
        common["navigator_instance_id"] = navigator_instance_id
    if is_playground is not None:
        common["is_playground"] = str(is_playground).lower()

    sessions: list[dict[str, Any]] = []
    offset = 0
    while True:
        params = {**common, "limit": 200, "offset": offset}
        page = request_fn(_url(base_url, "assistants/sessions", params), token)
        page_sessions = page.get("sessions")
        count = page.get("count")
        if (
            not isinstance(page_sessions, list)
            or any(not isinstance(item, dict) for item in page_sessions)
            or not isinstance(count, int)
            or count < 0
        ):
            raise CookbookError("assistants/sessions returned an invalid pagination envelope")
        sessions.extend(page_sessions)
        if len(sessions) >= count:
            return sessions
        if not page_sessions:
            raise CookbookError(
                "assistants/sessions returned an empty page before the reported count"
            )
        offset += len(page_sessions)


def fetch_message_timeline(
    base_url: str,
    token: str,
    session_id: str,
    view: str,
    *,
    request_fn: RequestFn = request_json,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Fetch every message page for one session."""
    messages: list[dict[str, Any]] = []
    session_metadata: dict[str, Any] | None = None
    cursor: str | None = None
    seen_cursors: set[str] = set()
    path = f"assistants/sessions/{session_id}/messages"
    while True:
        params: dict[str, Any] = {"view": view, "size": 100}
        if cursor is not None:
            params["cursor"] = cursor
        page = request_fn(_url(base_url, path, params), token)
        current_session = page.get("session")
        items = page.get("items")
        meta = page.get("meta")
        if (
            not isinstance(current_session, dict)
            or not isinstance(items, list)
            or any(not isinstance(item, dict) for item in items)
            or not isinstance(meta, dict)
        ):
            raise CookbookError(f"Session {session_id} returned an invalid message page")
        if session_metadata is None:
            session_metadata = current_session
        elif current_session != session_metadata:
            raise CookbookError(f"Session {session_id} metadata changed during pagination")
        messages.extend(items)
        if not meta.get("has_next"):
            total = meta.get("total")
            if isinstance(total, int) and len(messages) != total:
                raise CookbookError(
                    f"Session {session_id} returned {len(messages)} messages, expected {total}"
                )
            return session_metadata, messages
        next_cursor = meta.get("next_cursor")
        if not isinstance(next_cursor, str) or not next_cursor:
            raise CookbookError(
                f"Session {session_id} reported another page without a next_cursor"
            )
        if next_cursor in seen_cursors:
            raise CookbookError(f"Session {session_id} repeated a pagination cursor")
        seen_cursors.add(next_cursor)
        cursor = next_cursor


def build_export(
    *,
    base_url: str,
    token: str,
    project: str,
    timestamp_start: str | None,
    timestamp_end: str | None,
    view: str,
    navigator_instance_id: str | None,
    is_playground: bool | None,
    request_fn: RequestFn = request_json,
) -> dict[str, Any]:
    project_record = resolve_project(
        base_url, token, project, request_fn=request_fn
    )
    project_id = str(project_record["id"])
    summaries = fetch_sessions(
        base_url,
        token,
        project_id=project_id,
        timestamp_start=timestamp_start,
        timestamp_end=timestamp_end,
        navigator_instance_id=navigator_instance_id,
        is_playground=is_playground,
        request_fn=request_fn,
    )
    exported_sessions = []
    for summary in summaries:
        session_id = summary.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise CookbookError("Session list contained a missing session_id")
        metadata, messages = fetch_message_timeline(
            base_url, token, session_id, view, request_fn=request_fn
        )
        exported_sessions.append(
            {"summary": summary, "session": metadata, "messages": messages}
        )
    return {
        "schema_version": "1.0",
        "project": {"id": project_id, "name": project_record.get("name")},
        "filters": {
            "timestamp_start": timestamp_start,
            "timestamp_end": timestamp_end,
            "navigator_instance_id": navigator_instance_id,
            "is_playground": is_playground,
            "view": view,
        },
        "session_count": len(exported_sessions),
        "sessions": exported_sessions,
    }


def atomic_write_json(path: Path, value: dict[str, Any], *, force: bool) -> None:
    """Atomically write JSON without silently replacing an existing export."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not force:
        raise CookbookError(f"Output already exists: {path} (use --force to replace it)")
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        if temporary_name:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
        raise


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export complete Caylex assistant-session message timelines."
    )
    parser.add_argument(
        "project", help="Project UUID or exact, case-sensitive project name."
    )
    parser.add_argument("--start", type=validate_iso_datetime, dest="timestamp_start")
    parser.add_argument("--end", type=validate_iso_datetime, dest="timestamp_end")
    parser.add_argument(
        "--view", choices=("raw", "resolved"), default="raw", help="Message event view."
    )
    parser.add_argument("--navigator-instance-id", help="Filter by navigator instance UUID.")
    playground = parser.add_mutually_exclusive_group()
    playground.add_argument(
        "--playground", action="store_const", const=True, dest="is_playground"
    )
    playground.add_argument(
        "--production", action="store_const", const=False, dest="is_playground"
    )
    parser.set_defaults(is_playground=None)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--force", action="store_true", help="Replace an existing output.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument(
        "--platform-token-env",
        default="CAYLEX_PLATFORM_TOKEN",
        help="Environment variable containing the platform access token.",
    )
    args = parser.parse_args(argv)
    if (
        args.timestamp_start
        and args.timestamp_end
        and _parse_iso_datetime(args.timestamp_start)
        > _parse_iso_datetime(args.timestamp_end)
    ):
        parser.error("--start must be before or equal to --end")
    if args.navigator_instance_id and not _is_uuid(args.navigator_instance_id):
        parser.error("--navigator-instance-id must be a UUID")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        token = os.environ.get(args.platform_token_env)
        if not token:
            raise CookbookError(f"Set {args.platform_token_env} to a platform access token")
        exported = build_export(
            base_url=args.base_url,
            token=token,
            project=args.project,
            timestamp_start=args.timestamp_start,
            timestamp_end=args.timestamp_end,
            view=args.view,
            navigator_instance_id=args.navigator_instance_id,
            is_playground=args.is_playground,
        )
        atomic_write_json(args.output, exported, force=args.force)
    except (CookbookError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "output": str(args.output),
                "project_id": exported["project"]["id"],
                "session_count": exported["session_count"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
