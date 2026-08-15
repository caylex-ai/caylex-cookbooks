#!/usr/bin/env python3
"""Set or clear a Caylex Foundry server instance's base URL override."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable
from uuid import UUID

DEFAULT_BASE_URL = "https://api.caylex.ai/api/v1"


class CookbookError(RuntimeError):
    """A safe, actionable command-line error."""


RequestFn = Callable[..., dict[str, Any]]
URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except ValueError:
        return False
    return True


def safe_api_detail(value: Any) -> str:
    """Return useful API error text without echoing secret-bearing URL parts."""
    if isinstance(value, list):
        messages = [
            str(item.get("msg"))
            for item in value
            if isinstance(item, dict) and item.get("msg")
        ]
        return "; ".join(messages) or "request rejected"
    if not isinstance(value, str):
        return "request rejected"
    return URL_RE.sub(lambda match: redact_url(match.group(0)) or "[redacted URL]", value)


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
        "User-Agent": "caylex-base-url-override/1.0",
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
            detail = (
                safe_api_detail(decoded.get("detail"))
                if isinstance(decoded, dict)
                else "request rejected"
            )
        except json.JSONDecodeError:
            detail = "non-JSON error response"
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


def resolve_server_instance(
    base_url: str,
    token: str,
    project_id: str,
    server: str,
    *,
    request_fn: RequestFn = request_json,
) -> dict[str, Any]:
    """Resolve an instance UUID or exact server/display name within a project."""
    if _is_uuid(server):
        result = request_fn(_url(base_url, f"server-instances/{server}"), token)
        response_id = result.get("id")
        if (
            not isinstance(response_id, str)
            or not _is_uuid(response_id)
            or UUID(response_id) != UUID(server)
        ):
            raise CookbookError(
                "Server instance response did not match the requested instance ID"
            )
        response_project_id = result.get("project_id")
        if (
            not isinstance(response_project_id, str)
            or not _is_uuid(response_project_id)
            or UUID(response_project_id) != UUID(project_id)
        ):
            raise CookbookError("Server instance does not belong to the selected project")
        return result

    instances = iter_cursor_items(
        base_url=base_url,
        path="server-instances",
        token=token,
        request_fn=request_fn,
        params={"project_id": project_id},
    )
    matches = [
        item
        for item in instances
        if item.get("server_name") == server or item.get("display_name") == server
    ]
    if not matches:
        raise CookbookError(
            f"No server in the selected project has exact server/display name {server!r}"
        )
    if len(matches) > 1:
        choices = ", ".join(
            f"{item.get('id')} ({item.get('server_name')!r}, "
            f"{item.get('display_name')!r})"
            for item in matches
        )
        raise CookbookError(f"Server name {server!r} is ambiguous: {choices}")
    return matches[0]


def redact_url(value: Any) -> str | None:
    """Remove URL credentials, query parameters, and fragments before display."""
    if not isinstance(value, str) or not value:
        return None
    parsed = urllib.parse.urlsplit(value)
    hostname = parsed.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    authority = hostname
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port is not None:
        authority += f":{port}"
    return urllib.parse.urlunsplit((parsed.scheme, authority, parsed.path, "", ""))


def apply_override(
    *,
    base_url: str,
    token: str,
    project: str,
    server: str,
    override: str | None,
    dry_run: bool,
    request_fn: RequestFn = request_json,
) -> dict[str, Any]:
    project_record = resolve_project(
        base_url, token, project, request_fn=request_fn
    )
    project_id = str(project_record["id"])
    instance = resolve_server_instance(
        base_url, token, project_id, server, request_fn=request_fn
    )
    if str(instance.get("server_type", "")).lower() != "foundry":
        raise CookbookError(
            "Base URL overrides are supported only for Foundry-generated servers"
        )

    instance_id = str(instance["id"])
    action = "clear" if override is None else "set"
    response: dict[str, Any] | None = None
    if not dry_run:
        response = request_fn(
            _url(base_url, f"server-instances/{instance_id}/base-url-override"),
            token,
            method="PATCH",
            payload={"base_url_override": override},
        )
        if str(response.get("server_instance_id")) != instance_id:
            raise CookbookError("Update response did not match the selected server instance")

    source = response if response is not None else instance
    return {
        "status": "dry_run" if dry_run else "updated",
        "action": action,
        "project": {"id": project_id, "name": project_record.get("name")},
        "server_instance": {
            "id": instance_id,
            "server_name": instance.get("server_name"),
            "display_name": instance.get("display_name"),
            "server_type": instance.get("server_type"),
        },
        "previous_base_url_override": redact_url(instance.get("base_url_override")),
        "base_url_override": (
            redact_url(override) if dry_run else redact_url(source.get("base_url_override"))
        ),
        "default_base_url": redact_url(source.get("default_base_url")),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Set or clear a Foundry server instance base URL override."
    )
    parser.add_argument(
        "project", help="Project UUID or exact, case-sensitive project name."
    )
    parser.add_argument(
        "server",
        help="Server instance UUID or exact, case-sensitive server/display name.",
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--set", dest="override", metavar="URL")
    action.add_argument("--clear", action="store_true")
    parser.add_argument(
        "--dry-run", action="store_true", help="Resolve and validate selection only."
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument(
        "--platform-token-env",
        default="CAYLEX_PLATFORM_TOKEN",
        help="Environment variable containing the platform access token.",
    )
    args = parser.parse_args(argv)
    if args.override is not None:
        parsed = urllib.parse.urlsplit(args.override)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            parser.error("--set must be an absolute http(s) URL with a host")
        try:
            parsed.port
        except ValueError as exc:
            parser.error(f"--set contains an invalid port: {exc}")
    if args.clear:
        args.override = None
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        token = os.environ.get(args.platform_token_env)
        if not token:
            raise CookbookError(f"Set {args.platform_token_env} to a platform access token")
        result = apply_override(
            base_url=args.base_url,
            token=token,
            project=args.project,
            server=args.server,
            override=args.override,
            dry_run=args.dry_run,
        )
    except CookbookError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
