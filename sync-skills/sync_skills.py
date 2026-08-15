#!/usr/bin/env python3
"""Safely reconcile local skill directories with Caylex."""

from __future__ import annotations

import argparse
import ast
import io
import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_BASE_URL = "https://api.caylex.ai/api/v1"
PAGE_SIZE = 200


class SyncError(RuntimeError):
    """A local validation or Caylex API error."""


@dataclass(frozen=True)
class LocalSkill:
    name: str
    slug_hint: str
    directory: Path
    skill_md: Path


def _plain_yaml_scalar(value: str) -> str:
    value = value.strip()
    if not value:
        raise SyncError("frontmatter 'name' must not be empty")
    if value.startswith('"'):
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError) as exc:
            raise SyncError("invalid double-quoted frontmatter 'name'") from exc
        if not isinstance(parsed, str):
            raise SyncError("frontmatter 'name' must be a string")
        return parsed.strip()
    if value.startswith("'"):
        if len(value) < 2 or not value.endswith("'"):
            raise SyncError("invalid single-quoted frontmatter 'name'")
        return value[1:-1].replace("''", "'").strip()

    # In a plain YAML scalar, a comment starts at a # preceded by whitespace.
    value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
    if not value:
        raise SyncError("frontmatter 'name' must not be empty")
    return value


def parse_frontmatter_name(skill_md: Path) -> str:
    """Read a top-level YAML name scalar without requiring PyYAML."""
    try:
        text = skill_md.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise SyncError(f"cannot read {skill_md}: {exc}") from exc
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SyncError(f"{skill_md} must start with YAML frontmatter ('---')")

    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        raise SyncError(f"{skill_md} has no closing frontmatter delimiter")
    frontmatter = lines[1:end]

    for index, line in enumerate(frontmatter):
        match = re.match(r"^name\s*:\s*(.*)$", line)
        if not match:
            continue
        raw = match.group(1)
        if raw.strip() in {"|", ">", "|-", ">-", "|+", ">+"}:
            continuation: list[str] = []
            for following in frontmatter[index + 1 :]:
                if following and not following[0].isspace():
                    break
                if following.strip():
                    continuation.append(following.strip())
            separator = "\n" if raw.lstrip().startswith("|") else " "
            name = separator.join(continuation).strip()
        else:
            name = _plain_yaml_scalar(raw)
        if not name:
            raise SyncError(f"{skill_md} has an empty frontmatter 'name'")
        return name
    raise SyncError(f"{skill_md} is missing top-level frontmatter 'name'")


def slug_hint(name: str) -> str:
    """Approximate the API slug for matching list results without dependencies."""
    ascii_name = (
        unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    )
    return re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")


def discover_skills(skills_dir: Path) -> list[LocalSkill]:
    if not skills_dir.is_dir():
        raise SyncError(f"skills directory does not exist: {skills_dir}")
    skills: list[LocalSkill] = []
    seen_names: dict[str, Path] = {}
    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        name = parse_frontmatter_name(skill_md)
        folded = name.casefold()
        if folded in seen_names:
            raise SyncError(
                f"duplicate local skill name {name!r}: {seen_names[folded]} and {skill_md}"
            )
        seen_names[folded] = skill_md
        skills.append(LocalSkill(name, slug_hint(name), skill_md.parent, skill_md))
    return skills


def build_upload(skill: LocalSkill) -> tuple[str, str, bytes]:
    """Return (filename, content type, body), ZIP-bundling directories as needed."""
    files = sorted(
        path
        for path in skill.directory.rglob("*")
        if path.is_file() and not path.is_symlink()
    )
    extras = [path for path in files if path != skill.skill_md]
    if not extras:
        return "SKILL.md", "text/markdown", skill.skill_md.read_bytes()

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(skill.directory).as_posix())
    return f"{skill.directory.name}.zip", "application/zip", buffer.getvalue()


def encode_multipart(
    field_name: str, filename: str, content_type: str, content: bytes
) -> tuple[bytes, str]:
    boundary = f"caylex-{uuid.uuid4().hex}"
    disposition_name = field_name.replace('"', "")
    disposition_filename = filename.replace('"', "")
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{disposition_name}"; '
        f'filename="{disposition_filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8") + content + f"\r\n--{boundary}--\r\n".encode("ascii")
    return body, f"multipart/form-data; boundary={boundary}"


class CaylexClient:
    def __init__(self, base_url: str, token: str, timeout: float = 60) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        *,
        query: list[tuple[str, str]] | None = None,
        body: bytes | None = None,
        content_type: str | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "User-Agent": "caylex-sync-skills/1.0",
        }
        if content_type:
            headers["Content-Type"] = content_type
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").strip()
            try:
                parsed = json.loads(detail)
                detail = json.dumps(parsed, ensure_ascii=False)
            except (json.JSONDecodeError, TypeError):
                pass
            raise SyncError(f"{method} {url} failed: HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise SyncError(f"{method} {url} failed: {exc.reason}") from exc
        except ValueError as exc:
            raise SyncError(f"{method} {url} could not be sent: {exc}") from exc
        if not payload:
            return None
        try:
            return json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise SyncError(f"{method} {url} returned invalid JSON") from exc


def owner_path(mode: str, owner: str) -> str:
    if not owner:
        raise SyncError(f"{mode} owner name must not be empty")
    if "/" in owner:
        raise SyncError(
            f"{mode} owner names containing '/' cannot be addressed by the "
            "backend's by-name routes"
        )
    encoded = urllib.parse.quote(owner, safe="")
    if mode == "project":
        return f"/projects/by-name/{encoded}/skills"
    return f"/navigator-global-skills/by-name/{encoded}/skills"


def list_all_remote(client: CaylexClient, base_path: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    page = 1
    while True:
        payload = client.request(
            "GET",
            base_path,
            query=[("page", str(page)), ("size", str(PAGE_SIZE))],
        )
        if not isinstance(payload, list) or not all(
            isinstance(item, dict) for item in payload
        ):
            raise SyncError("skill list endpoint returned an unexpected JSON shape")
        results.extend(payload)
        if len(payload) < PAGE_SIZE:
            return results
        page += 1


def match_remote(
    local: LocalSkill, remote: list[dict[str, Any]]
) -> dict[str, Any] | None:
    for item in remote:
        if item.get("name") == local.name:
            return item
    for item in remote:
        name = item.get("name")
        if isinstance(name, str) and name.casefold() == local.name.casefold():
            return item
    if local.slug_hint:
        for item in remote:
            if item.get("slug") == local.slug_hint:
                return item
    return None


def sync(
    *,
    client: CaylexClient,
    mode: str,
    owner: str,
    skills_dir: Path,
    prune: bool,
    dry_run: bool,
) -> None:
    local = discover_skills(skills_dir)
    base_path = owner_path(mode, owner)
    remote = list_all_remote(client, base_path)
    matched_remote_ids: set[str] = set()

    for skill in local:
        existing = match_remote(skill, remote)
        if existing is not None and existing.get("id") is not None:
            matched_remote_ids.add(str(existing["id"]))
        action = "update" if existing else "add"
        if dry_run:
            print(f"would {action}: {skill.name}")
            continue
        filename, upload_type, content = build_upload(skill)
        body, multipart_type = encode_multipart(
            "file", filename, upload_type, content
        )
        path = base_path
        method = "POST"
        if existing:
            identifier = str(existing.get("slug") or existing.get("name") or skill.name)
            path = f"{base_path}/{urllib.parse.quote(identifier, safe='')}"
            method = "PUT"
        client.request(method, path, body=body, content_type=multipart_type)
        completed_action = "updated" if existing else "added"
        print(f"{completed_action}: {skill.name}")

    if not prune:
        return
    for item in remote:
        remote_id = str(item.get("id", ""))
        if remote_id and remote_id in matched_remote_ids:
            continue
        name = item.get("name")
        identifier = item.get("slug") or name
        if not isinstance(name, str) or not isinstance(identifier, str):
            raise SyncError("skill list item is missing its name or slug")
        if dry_run:
            print(f"would remove: {name}")
            continue
        path = f"{base_path}/{urllib.parse.quote(identifier, safe='')}"
        client.request("DELETE", path)
        print(f"removed: {name}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("project", "global"))
    parser.add_argument("owner", help="exact project or Navigator Library display name")
    parser.add_argument("skills_dir", type=Path, help="directory containing */SKILL.md")
    parser.add_argument(
        "--token",
        default=os.environ.get("CAYLEX_PLATFORM_TOKEN"),
        help="platform token (default: CAYLEX_PLATFORM_TOKEN)",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument(
        "--prune",
        action="store_true",
        help="delete remote skills absent locally (off by default)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="list changes without POST, PUT, or DELETE",
    )
    parser.add_argument("--timeout", type=float, default=60)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.token:
        print(
            "error: provide --token or set CAYLEX_PLATFORM_TOKEN",
            file=sys.stderr,
        )
        return 2
    try:
        sync(
            client=CaylexClient(args.base_url, args.token, args.timeout),
            mode=args.mode,
            owner=args.owner,
            skills_dir=args.skills_dir,
            prune=args.prune,
            dry_run=args.dry_run,
        )
    except SyncError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
