#!/usr/bin/env python3
"""Create idempotently named runtime keys for a project's navigators."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

DEFAULT_BASE_URL = "https://api.caylex.ai/api/v1"


class CookbookError(RuntimeError):
    """Raised when keys cannot be created or stored safely."""


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
        "User-Agent": "caylex-generate-navigator-api-keys/1.0",
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
        query = urllib.parse.urlencode(query_params)
        page = request_fn(base_url, token, f"{path}?{query}")
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


def resolve_project(
    *,
    base_url: str,
    token: str,
    project_id: str | None,
    project_name: str | None,
    request_fn: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    if bool(project_id) == bool(project_name):
        raise CookbookError("Provide exactly one project ID or project name")
    if project_id:
        try:
            project = request_fn(base_url, token, f"/projects/{project_id}")
        except APIError as exc:
            if exc.status == 404:
                raise CookbookError(f"Project ID {project_id!r} was not found") from exc
            raise
        require_id(project)
        return project

    projects = paginated_items(
        base_url=base_url,
        token=token,
        path="/projects",
        request_fn=request_fn,
    )
    matches = [project for project in projects if project.get("name") == project_name]
    if not matches:
        raise CookbookError(f"Project {project_name!r} was not found")
    if len(matches) > 1:
        raise CookbookError(f"Project name {project_name!r} is ambiguous")
    require_id(matches[0])
    return matches[0]


def securely_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def securely_create_json(path: Path, payload: dict[str, Any]) -> None:
    """Reserve a new output path and write JSON with owner-only permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            stat.S_IRUSR | stat.S_IWUSR,
        )
    except FileExistsError as exc:
        raise CookbookError(
            f"Output already exists: {path}; choose a new path to protect one-time keys"
        ) from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def generate_keys(
    *,
    base_url: str,
    token: str,
    project_id: str | None,
    project_name: str | None,
    key_name: str,
    description: str | None,
    expires_at: str | None,
    output_path: Path | None,
    print_secrets: bool,
    request_fn: Callable[..., dict[str, Any]] = request_json,
    status_fn: Callable[[str], None] | None = None,
    secret_fn: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    if bool(output_path) == bool(print_secrets):
        raise CookbookError("Choose exactly one of a secure output path or stdout opt-in")
    if not key_name.strip():
        raise CookbookError("Key name cannot be empty")
    project = resolve_project(
        base_url=base_url,
        token=token,
        project_id=project_id,
        project_name=project_name,
        request_fn=request_fn,
    )
    resolved_project_id = require_id(project)
    navigators = paginated_items(
        base_url=base_url,
        token=token,
        path="/navigator-instances",
        params={"project_id": resolved_project_id},
        request_fn=request_fn,
    )
    if not navigators:
        raise CookbookError(
            f"Project {project.get('name', resolved_project_id)!r} has no navigator instances"
        )

    document: dict[str, Any] = {
        "schema_version": "1.0",
        "project_id": resolved_project_id,
        "project_name": project.get("name"),
        "key_name": key_name,
        "created": [],
        "skipped_existing": [],
    }
    if output_path:
        securely_create_json(output_path, document)

    for navigator in navigators:
        instance_id = require_id(navigator)
        navigator_name = navigator.get("navigator_name")
        if not isinstance(navigator_name, str) or not navigator_name:
            navigator_name = instance_id
        existing = paginated_items(
            base_url=base_url,
            token=token,
            path=f"/navigator-instances/{instance_id}/api-keys",
            request_fn=request_fn,
        )
        if any(key.get("name") == key_name for key in existing):
            document["skipped_existing"].append(
                {"navigator_instance_id": instance_id, "navigator_name": navigator_name}
            )
            if output_path:
                securely_write_json(output_path, document)
            if status_fn:
                status_fn(f"key already exists: {navigator_name}")
            continue

        payload: dict[str, Any] = {"name": key_name}
        if description is not None:
            payload["description"] = description
        else:
            payload["description"] = (
                f"{key_name} key for project {project.get('name') or resolved_project_id}"
            )
        if expires_at is not None:
            payload["expires_at"] = expires_at
        created = request_fn(
            base_url,
            token,
            f"/navigator-instances/{instance_id}/api-keys",
            method="POST",
            payload=payload,
        )
        key_id = require_id(created)
        raw_key = created.get("key")
        preview = created.get("preview")
        if not isinstance(raw_key, str) or not raw_key:
            raise CookbookError("Key creation response did not include the one-time key")
        if not isinstance(preview, str) or not preview:
            raise CookbookError("Key creation response did not include preview")
        created_record = {
            "navigator_instance_id": instance_id,
            "navigator_name": navigator_name,
            "api_key_id": key_id,
            "preview": preview,
            "key": raw_key,
        }
        document["created"].append(created_record)
        if output_path:
            securely_write_json(output_path, document)
        elif secret_fn:
            secret_fn(created_record)
        if status_fn:
            status_fn(f"created key for {navigator_name} ({preview})")
    return document


def required_token(env_name: str) -> str:
    token = os.environ.get(env_name)
    if not token:
        raise CookbookError(f"Set {env_name} before running this script")
    return token


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create one idempotently named API key per navigator instance."
    )
    project = parser.add_mutually_exclusive_group(required=True)
    project.add_argument("--project-id")
    project.add_argument("--project-name")
    parser.add_argument("--key-name", default="runtime")
    parser.add_argument("--description")
    parser.add_argument(
        "--expires-at",
        help="Optional ISO-8601 timestamp accepted by the Caylex API.",
    )
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--output", type=Path)
    destination.add_argument(
        "--print-secrets",
        action="store_true",
        help="Explicitly print full one-time keys to stdout.",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--platform-token-env", default="CAYLEX_PLATFORM_TOKEN")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = generate_keys(
            base_url=args.base_url,
            token=required_token(args.platform_token_env),
            project_id=args.project_id,
            project_name=args.project_name,
            key_name=args.key_name,
            description=args.description,
            expires_at=args.expires_at,
            output_path=args.output,
            print_secrets=args.print_secrets,
            request_fn=request_json,
            status_fn=lambda message: print(message, file=sys.stderr),
            secret_fn=(
                lambda record: print(
                    json.dumps({"event": "created", **record}, ensure_ascii=False),
                    flush=True,
                )
                if args.print_secrets
                else None
            ),
        )
    except (CookbookError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.print_secrets:
        print(
            json.dumps(
                {
                    "event": "complete",
                    "project_id": result["project_id"],
                    "created_count": len(result["created"]),
                    "skipped_existing_count": len(result["skipped_existing"]),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    else:
        print(
            f"saved {len(result['created'])} one-time key(s) to {args.output} "
            "(mode 0600)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
