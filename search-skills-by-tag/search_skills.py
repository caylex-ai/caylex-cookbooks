#!/usr/bin/env python3
"""Search project or navigator-global Caylex skills by exact tag."""

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


class SearchError(RuntimeError):
    """A Caylex API or response validation error."""


def request_page(
    url: str, token: str, timeout: float = 60
) -> list[dict[str, Any]]:
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "caylex-search-skills/1.0",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        try:
            detail = json.dumps(json.loads(detail), ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            pass
        raise SearchError(f"GET {url} failed: HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SearchError(f"GET {url} failed: {exc.reason}") from exc
    except ValueError as exc:
        raise SearchError(f"GET {url} could not be sent: {exc}") from exc

    try:
        decoded = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SearchError(f"GET {url} returned invalid JSON") from exc
    if not isinstance(decoded, list) or not all(
        isinstance(item, dict) for item in decoded
    ):
        raise SearchError(f"GET {url} returned an unexpected JSON shape")
    return decoded


def search(
    *,
    base_url: str,
    token: str,
    mode: str,
    tag: str,
    scopes: list[str] | None,
    page: int,
    size: int,
    all_pages: bool,
    timeout: float,
    request_fn: Callable[[str, str, float], list[dict[str, Any]]] = request_page,
) -> list[dict[str, Any]]:
    endpoint = "/skills" if mode == "project" else "/navigator-global-skills"
    scope_parameter = "project" if mode == "project" else "navigator"
    current_page = page
    results: list[dict[str, Any]] = []

    while True:
        query: list[tuple[str, str]] = [("tag", tag)]
        query.extend((scope_parameter, scope) for scope in scopes or [])
        query.extend([("page", str(current_page)), ("size", str(size))])
        url = f"{base_url.rstrip('/')}{endpoint}?{urllib.parse.urlencode(query)}"
        items = request_fn(url, token, timeout)
        results.extend(items)
        if not all_pages or len(items) < size:
            return results
        current_page += 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--token",
        default=os.environ.get("CAYLEX_PLATFORM_TOKEN"),
        help="platform token (default: CAYLEX_PLATFORM_TOKEN)",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--timeout", type=float, default=60)
    subparsers = parser.add_subparsers(dest="mode", required=True)

    project = subparsers.add_parser("project", help="search project-owned skills")
    project.add_argument("tag", help="exact, case-sensitive tag")
    project.add_argument(
        "--project",
        action="append",
        dest="scopes",
        metavar="NAME_OR_UUID",
        help="project scope; repeat for multiple projects (default: all)",
    )

    global_parser = subparsers.add_parser(
        "global", help="search Navigator Library global skills"
    )
    global_parser.add_argument("tag", help="exact, case-sensitive tag")
    global_parser.add_argument(
        "--navigator",
        action="append",
        dest="scopes",
        metavar="NAME_API_NAME_OR_UUID",
        help="navigator scope; repeat for multiple navigators (default: all)",
    )

    for subparser in (project, global_parser):
        subparser.add_argument("--page", type=int, default=1, help="1-based page")
        subparser.add_argument(
            "--size", type=int, default=50, help="items per page (1-200)"
        )
        subparser.add_argument(
            "--all-pages",
            action="store_true",
            help="fetch consecutive pages until a short page",
        )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.token:
        parser.error("provide --token or set CAYLEX_PLATFORM_TOKEN")
    if args.page < 1:
        parser.error("--page must be at least 1")
    if not 1 <= args.size <= 200:
        parser.error("--size must be between 1 and 200")
    if not args.tag.strip():
        parser.error("tag must not be empty")

    try:
        results = search(
            base_url=args.base_url,
            token=args.token,
            mode=args.mode,
            tag=args.tag,
            scopes=args.scopes,
            page=args.page,
            size=args.size,
            all_pages=args.all_pages,
            timeout=args.timeout,
        )
    except SearchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    json.dump(results, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
