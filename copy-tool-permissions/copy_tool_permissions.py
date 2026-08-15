#!/usr/bin/env python3
"""Copy a navigator's tool permissions between Caylex projects."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

DEFAULT_BASE_URL = "https://api.caylex.ai/api/v1"
VALID_MODES = {"always_execute", "require_approval", "disabled"}


class CookbookError(RuntimeError):
    """Raised when permissions cannot be copied safely."""


class APIError(CookbookError):
    """A sanitized Caylex API error."""

    def __init__(self, status: int, method: str, path: str):
        self.status = status
        super().__init__(f"{method} {path} failed with HTTP {status}")


def request_json(
    base_url: str,
    token: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "caylex-copy-tool-permissions/1.0",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        raise APIError(exc.code, method, path) from exc
    except urllib.error.URLError as exc:
        raise CookbookError(f"{method} {path} failed: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise CookbookError(f"{method} {path} returned invalid JSON") from exc
    if not isinstance(result, dict):
        raise CookbookError(f"{method} {path} returned a non-object response")
    return result


def paginated_items(
    *,
    base_url: str,
    token: str,
    path: str,
    params: dict[str, str] | None = None,
    request_fn: Callable[..., dict[str, Any]] = request_json,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    seen_cursors: set[str] = set()
    while True:
        query_params = dict(params or {})
        query_params["size"] = "100"
        if cursor is not None:
            query_params["cursor"] = cursor
        page = request_fn(
            base_url,
            token,
            f"{path}?{urllib.parse.urlencode(query_params)}",
        )
        page_items = page.get("items")
        meta = page.get("meta")
        if not isinstance(page_items, list) or not isinstance(meta, dict):
            raise CookbookError(f"GET {path} returned an invalid paginated response")
        if any(not isinstance(item, dict) for item in page_items):
            raise CookbookError(f"GET {path} returned an invalid item")
        items.extend(page_items)
        if not meta.get("has_next"):
            return items
        next_cursor = meta.get("next_cursor")
        if not isinstance(next_cursor, str) or not next_cursor:
            raise CookbookError(f"GET {path} omitted next_cursor while has_next is true")
        if next_cursor in seen_cursors:
            raise CookbookError(f"GET {path} repeated a pagination cursor")
        seen_cursors.add(next_cursor)
        cursor = next_cursor


def require_id(item: dict[str, Any], field: str = "id") -> str:
    value = item.get(field)
    if not isinstance(value, str) or not value:
        raise CookbookError(f"API response is missing {field}")
    return value


def load_all_projects(
    *,
    base_url: str,
    token: str,
    request_fn: Callable[..., dict[str, Any]],
) -> list[dict[str, Any]]:
    return paginated_items(
        base_url=base_url,
        token=token,
        path="/projects",
        request_fn=request_fn,
    )


def resolve_project_by_name(
    projects: list[dict[str, Any]], name: str
) -> dict[str, Any]:
    matches = [project for project in projects if project.get("name") == name]
    if not matches:
        raise CookbookError(f"Project {name!r} was not found")
    if len(matches) > 1:
        raise CookbookError(f"Project name {name!r} is ambiguous")
    require_id(matches[0])
    return matches[0]


def resolve_project_by_id(
    *,
    base_url: str,
    token: str,
    project_id: str,
    request_fn: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    try:
        project = request_fn(base_url, token, f"/projects/{project_id}")
    except APIError as exc:
        if exc.status == 404:
            raise CookbookError(f"Project ID {project_id!r} was not found") from exc
        raise
    require_id(project)
    return project


def find_navigator_instance(
    *,
    base_url: str,
    token: str,
    project: dict[str, Any],
    navigator_id: str | None,
    navigator_name: str | None,
    request_fn: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    project_id = require_id(project)
    instances = paginated_items(
        base_url=base_url,
        token=token,
        path="/navigator-instances",
        params={"project_id": project_id},
        request_fn=request_fn,
    )
    if navigator_id:
        matches = [
            instance
            for instance in instances
            if instance.get("navigator_id") == navigator_id
        ]
        label = f"navigator ID {navigator_id!r}"
    else:
        matches = [
            instance
            for instance in instances
            if instance.get("navigator_name") == navigator_name
        ]
        label = f"navigator {navigator_name!r}"
    project_label = project.get("name") or project_id
    if not matches:
        raise CookbookError(f"{label} has no instance in project {project_label!r}")
    if len(matches) > 1:
        raise CookbookError(f"{label} is ambiguous in project {project_label!r}")
    require_id(matches[0])
    require_id(matches[0], "navigator_id")
    return matches[0]


def get_permissions(
    *,
    base_url: str,
    token: str,
    instance_id: str,
    request_fn: Callable[..., dict[str, Any]],
) -> list[dict[str, Any]]:
    response = request_fn(
        base_url,
        token,
        f"/navigator-instances/{instance_id}/tool-permissions",
    )
    permissions = response.get("permissions")
    if not isinstance(permissions, list) or any(
        not isinstance(permission, dict) for permission in permissions
    ):
        raise CookbookError("Tool-permissions response is invalid")
    for permission in permissions:
        require_id(permission, "tool_id")
        if permission.get("mode") not in VALID_MODES:
            raise CookbookError("Tool-permissions response contains an invalid mode")
    return permissions


def stable_key(permission: dict[str, Any]) -> tuple[str, str] | None:
    server_name = permission.get("server_name")
    tool_name = permission.get("tool_name")
    if (
        isinstance(server_name, str)
        and server_name
        and isinstance(tool_name, str)
        and tool_name
    ):
        return server_name, tool_name
    return None


def unique_stable_map(
    permissions: list[dict[str, Any]], side: str
) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for permission in permissions:
        key = stable_key(permission)
        if key is None:
            continue
        if key in result:
            raise CookbookError(
                f"{side} permissions contain duplicate stable tool identity {key!r}"
            )
        result[key] = permission
    return result


def match_permissions(
    source: list[dict[str, Any]],
    target: list[dict[str, Any]],
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    """Match by ID first, then by stable (server_name, tool_name)."""
    target_by_id = {require_id(permission, "tool_id"): permission for permission in target}
    target_by_stable = unique_stable_map(target, "Target")
    unique_stable_map(source, "Source")
    used_target_ids: set[str] = set()
    updates: list[dict[str, str]] = []
    unmatched: list[dict[str, Any]] = []

    for source_permission in source:
        source_id = require_id(source_permission, "tool_id")
        source_key = stable_key(source_permission)
        target_permission = target_by_id.get(source_id)
        if (
            target_permission is not None
            and source_key is not None
            and stable_key(target_permission) is not None
            and stable_key(target_permission) != source_key
        ):
            # A matching opaque ID with conflicting names is not safe to apply.
            target_permission = None
        if target_permission is None:
            target_permission = (
                target_by_stable.get(source_key) if source_key is not None else None
            )
        if target_permission is None:
            unmatched.append(source_permission)
            continue
        target_id = require_id(target_permission, "tool_id")
        if target_id in used_target_ids:
            raise CookbookError(
                f"Multiple source tools matched target tool ID {target_id!r}"
            )
        used_target_ids.add(target_id)
        updates.append(
            {"tool_id": target_id, "mode": str(source_permission["mode"])}
        )
    return updates, unmatched


def copy_permissions(
    *,
    base_url: str,
    token: str,
    source_project_id: str | None,
    source_project_name: str | None,
    target_project_ids: list[str],
    target_project_names: list[str],
    navigator_id: str | None,
    navigator_name: str | None,
    dry_run: bool,
    require_complete_match: bool,
    request_fn: Callable[..., dict[str, Any]] = request_json,
) -> dict[str, Any]:
    if bool(source_project_id) == bool(source_project_name):
        raise CookbookError("Provide exactly one source project ID or name")
    if bool(navigator_id) == bool(navigator_name):
        raise CookbookError("Provide exactly one navigator ID or name")
    if not target_project_ids and not target_project_names:
        raise CookbookError("Provide at least one target project")

    projects = (
        load_all_projects(base_url=base_url, token=token, request_fn=request_fn)
        if source_project_name or target_project_names
        else []
    )
    if source_project_name:
        source_project = resolve_project_by_name(projects, source_project_name)
    else:
        source_project = resolve_project_by_id(
            base_url=base_url,
            token=token,
            project_id=str(source_project_id),
            request_fn=request_fn,
        )

    target_projects = [
        resolve_project_by_id(
            base_url=base_url,
            token=token,
            project_id=project_id,
            request_fn=request_fn,
        )
        for project_id in target_project_ids
    ]
    target_projects.extend(
        resolve_project_by_name(projects, name) for name in target_project_names
    )
    target_ids = [require_id(project) for project in target_projects]
    if len(set(target_ids)) != len(target_ids):
        raise CookbookError("Each target project must be specified only once")
    if require_id(source_project) in target_ids:
        raise CookbookError("The source project cannot also be a target")

    source_instance = find_navigator_instance(
        base_url=base_url,
        token=token,
        project=source_project,
        navigator_id=navigator_id,
        navigator_name=navigator_name,
        request_fn=request_fn,
    )
    source_navigator_id = require_id(source_instance, "navigator_id")
    source_permissions = get_permissions(
        base_url=base_url,
        token=token,
        instance_id=require_id(source_instance),
        request_fn=request_fn,
    )

    results: list[dict[str, Any]] = []
    for target_project in target_projects:
        target_instance = find_navigator_instance(
            base_url=base_url,
            token=token,
            project=target_project,
            navigator_id=source_navigator_id,
            navigator_name=None,
            request_fn=request_fn,
        )
        target_instance_id = require_id(target_instance)
        target_permissions = get_permissions(
            base_url=base_url,
            token=token,
            instance_id=target_instance_id,
            request_fn=request_fn,
        )
        updates, unmatched = match_permissions(source_permissions, target_permissions)
        if require_complete_match and unmatched:
            raise CookbookError(
                f"Project {target_project.get('name') or require_id(target_project)!r} "
                f"is missing {len(unmatched)} source tool(s)"
            )
        if not dry_run and updates:
            request_fn(
                base_url,
                token,
                f"/navigator-instances/{target_instance_id}/tool-permissions",
                method="PUT",
                payload={"permissions": updates},
            )
        results.append(
            {
                "project_id": require_id(target_project),
                "project_name": target_project.get("name"),
                "navigator_instance_id": target_instance_id,
                "matched": len(updates),
                "unmatched": [
                    {
                        "tool_id": permission["tool_id"],
                        "server_name": permission.get("server_name"),
                        "tool_name": permission.get("tool_name"),
                    }
                    for permission in unmatched
                ],
                "applied": not dry_run and bool(updates),
            }
        )
    return {"dry_run": dry_run, "targets": results}


def required_token(env_name: str) -> str:
    token = os.environ.get(env_name)
    if not token:
        raise CookbookError(f"Set {env_name} before running this script")
    return token


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Copy one navigator instance's tool policy to target projects."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--source-project-id")
    source.add_argument("--source-project-name")
    parser.add_argument("--target-project-id", action="append", default=[])
    parser.add_argument("--target-project-name", action="append", default=[])
    navigator = parser.add_mutually_exclusive_group(required=True)
    navigator.add_argument("--navigator-id")
    navigator.add_argument("--navigator-name")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--require-complete-match",
        action="store_true",
        help="Fail instead of safely skipping source tools absent from a target.",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--platform-token-env", default="CAYLEX_PLATFORM_TOKEN")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = copy_permissions(
            base_url=args.base_url,
            token=required_token(args.platform_token_env),
            source_project_id=args.source_project_id,
            source_project_name=args.source_project_name,
            target_project_ids=args.target_project_id,
            target_project_names=args.target_project_name,
            navigator_id=args.navigator_id,
            navigator_name=args.navigator_name,
            dry_run=args.dry_run,
            require_complete_match=args.require_complete_match,
            request_fn=request_json,
        )
    except (CookbookError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
