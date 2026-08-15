#!/usr/bin/env python3
"""Provision customer projects from a Caylex seed project."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

DEFAULT_BASE_URL = "https://api.caylex.ai/api/v1"


class CookbookError(RuntimeError):
    """Raised when provisioning cannot be completed safely."""


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
    """Make an authenticated request without including secrets in errors."""
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "caylex-provision-customer-projects/1.0",
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


def require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise CookbookError(f"API response is missing {field}")
    return value


def validate_provision_response(result: dict[str, Any], customer_name: str) -> None:
    require_string(result.get("id"), "id")
    if result.get("name") != customer_name:
        raise CookbookError("API response project name does not match the requested name")
    require_string(result.get("seed_project_id"), "seed_project_id")
    for field in ("seeded_server_count", "seeded_navigator_count"):
        if not isinstance(result.get(field), int):
            raise CookbookError(f"API response is missing {field}")
    for field in ("servers", "navigators", "warnings"):
        if not isinstance(result.get(field), list):
            raise CookbookError(f"API response is missing {field}")
    for navigator in result["navigators"]:
        if not isinstance(navigator, dict):
            raise CookbookError("API response contains an invalid navigator entry")
        require_string(navigator.get("navigator_instance_id"), "navigator_instance_id")
        api_key = navigator.get("api_key")
        if api_key is not None and (not isinstance(api_key, str) or not api_key):
            raise CookbookError("API response contains an invalid api_key")


def securely_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Atomically write JSON with owner-only permissions."""
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
            f"Output already exists: {path}; choose a new path to avoid losing one-time keys"
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


def provision_customers(
    *,
    base_url: str,
    token: str,
    customer_names: list[str],
    seed_project_id: str | None,
    seed_project_name: str | None,
    description: str | None,
    icon: str | None,
    output_path: Path,
    request_fn: Callable[..., dict[str, Any]] = request_json,
    status_fn: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    if bool(seed_project_id) == bool(seed_project_name):
        raise CookbookError(
            "Provide exactly one seed project ID or seed project name"
        )
    if not customer_names or any(not name.strip() for name in customer_names):
        raise CookbookError("Provide at least one non-empty customer project name")
    if len(set(customer_names)) != len(customer_names):
        raise CookbookError("Customer project names must be unique")
    document: dict[str, Any] = {
        "schema_version": "1.0",
        "projects": [],
        "already_existed": [],
    }
    securely_create_json(output_path, document)

    for customer_name in customer_names:
        payload: dict[str, Any] = {"name": customer_name}
        if seed_project_id:
            payload["seed_project_id"] = seed_project_id
        else:
            payload["seed_project_name"] = seed_project_name
        if description is not None:
            payload["description"] = description
        if icon is not None:
            payload["icon"] = icon
        try:
            result = request_fn(
                base_url,
                token,
                "/projects/from-seed",
                method="POST",
                payload=payload,
            )
        except APIError as exc:
            if exc.status != 409:
                raise
            document["already_existed"].append(customer_name)
            securely_write_json(output_path, document)
            if status_fn:
                status_fn(f"already exists: {customer_name}")
            continue

        validate_provision_response(result, customer_name)
        document["projects"].append(result)
        securely_write_json(output_path, document)
        if status_fn:
            status_fn(
                f"created {customer_name}: "
                f"{result['seeded_server_count']} server(s), "
                f"{result['seeded_navigator_count']} navigator(s)"
            )
            for warning in result["warnings"]:
                status_fn(f"warning for {customer_name}: {warning}")
    return document


def required_token(env_name: str) -> str:
    token = os.environ.get(env_name)
    if not token:
        raise CookbookError(f"Set {env_name} before running this script")
    return token


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Clone a seed project for one or more customers and securely save "
            "the one-time navigator keys."
        )
    )
    parser.add_argument(
        "--customer-name",
        action="append",
        required=True,
        help="New project name; repeat for multiple customers.",
    )
    seed = parser.add_mutually_exclusive_group(required=True)
    seed.add_argument("--seed-project-id")
    seed.add_argument("--seed-project-name")
    parser.add_argument("--description")
    parser.add_argument("--icon")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument(
        "--platform-token-env",
        default="CAYLEX_PLATFORM_TOKEN",
        help="Environment variable containing the platform token.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = provision_customers(
            base_url=args.base_url,
            token=required_token(args.platform_token_env),
            customer_names=args.customer_name,
            seed_project_id=args.seed_project_id,
            seed_project_name=args.seed_project_name,
            description=args.description,
            icon=args.icon,
            output_path=args.output,
            request_fn=request_json,
            status_fn=print,
        )
    except (CookbookError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"saved {len(result['projects'])} newly provisioned project(s) to "
        f"{args.output} (mode 0600)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
