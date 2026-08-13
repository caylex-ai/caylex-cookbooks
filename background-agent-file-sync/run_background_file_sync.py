#!/usr/bin/env python3
"""Run a Caylex background file-sync task and download its complete tool trace."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

TERMINAL_STATUSES = {"COMPLETED", "INCOMPLETE", "FAILED"}
APPROVAL_MODES = {"exclude", "human", "deferred_human", "auto_approve"}


class RunnerError(RuntimeError):
    """Raised when a background task cannot be submitted or retrieved."""


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
        "User-Agent": "caylex-background-file-sync/1.0",
    }
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url,
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RunnerError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RunnerError(f"Request to {url} failed: {exc.reason}") from exc
    if not isinstance(result, dict):
        raise RunnerError(f"Expected a JSON object from {url}")
    return result


def submit_task(
    *,
    base_url: str,
    platform_token: str,
    caylex_api_key: str,
    user_email: str,
    prompt: str,
    skill_ref: str | None,
    approval_mode: str,
    model: str | None,
    request_fn: Callable[..., dict[str, Any]] = request_json,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "caylex_api_key": caylex_api_key,
        "user_email": user_email,
        "prompt": prompt,
        "approval_mode": approval_mode,
    }
    if skill_ref:
        payload["skill_ref"] = skill_ref
    if model:
        payload["model"] = model
    return request_fn(
        f"{base_url.rstrip('/')}/agent-task",
        platform_token,
        method="POST",
        payload=payload,
    )


def wait_for_terminal_status(
    *,
    base_url: str,
    platform_token: str,
    task_id: str,
    poll_interval: float,
    timeout: float,
    request_fn: Callable[..., dict[str, Any]] = request_json,
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
    status_callback: Callable[[str, str | None], None] | None = None,
) -> dict[str, Any]:
    deadline = monotonic_fn() + timeout
    last_status: str | None = None
    while True:
        task_status = request_fn(
            f"{base_url.rstrip('/')}/agent-task/{task_id}",
            platform_token,
        )
        status = str(task_status.get("status") or "")
        session_id = task_status.get("session_id")
        if status != last_status and status_callback:
            status_callback(
                status,
                str(session_id) if session_id is not None else None,
            )
        last_status = status
        if status in TERMINAL_STATUSES:
            return task_status
        if monotonic_fn() >= deadline:
            raise RunnerError(
                f"Timed out after {timeout:g}s waiting for task {task_id}; "
                f"last status was {status or 'unknown'}"
            )
        sleep_fn(poll_interval)


def fetch_complete_trace(
    *,
    base_url: str,
    platform_token: str,
    session_id: str,
    request_fn: Callable[..., dict[str, Any]] = request_json,
) -> dict[str, Any]:
    groups: list[dict[str, Any]] = []
    offset = 0
    while True:
        query = urllib.parse.urlencode({"offset": offset, "limit": 100})
        page = request_fn(
            f"{base_url.rstrip('/')}/queries/query-logs/{session_id}/trace?{query}",
            platform_token,
        )
        page_groups = page.get("tool_call_groups")
        if not isinstance(page_groups, list):
            raise RunnerError("Trace response tool_call_groups was not a list")
        groups.extend(group for group in page_groups if isinstance(group, dict))
        meta = page.get("meta")
        if not isinstance(meta, dict) or not meta.get("has_next"):
            return {
                "tool_call_groups": groups,
                "meta": {
                    "total_groups": len(groups),
                    "offset": 0,
                    "limit": 100,
                    "has_next": False,
                },
            }
        offset += int(meta.get("limit") or 100)


def run_background_file_sync(
    *,
    base_url: str,
    platform_token: str,
    caylex_api_key: str,
    user_email: str,
    prompt: str,
    skill_ref: str | None,
    approval_mode: str,
    model: str | None,
    poll_interval: float,
    timeout: float,
    request_fn: Callable[..., dict[str, Any]] = request_json,
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
    status_callback: Callable[[str, str | None], None] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    submission = submit_task(
        base_url=base_url,
        platform_token=platform_token,
        caylex_api_key=caylex_api_key,
        user_email=user_email,
        prompt=prompt,
        skill_ref=skill_ref,
        approval_mode=approval_mode,
        model=model,
        request_fn=request_fn,
    )
    task_id = submission.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        raise RunnerError("Task submission did not return task_id")

    task_status = wait_for_terminal_status(
        base_url=base_url,
        platform_token=platform_token,
        task_id=task_id,
        poll_interval=poll_interval,
        timeout=timeout,
        request_fn=request_fn,
        sleep_fn=sleep_fn,
        monotonic_fn=monotonic_fn,
        status_callback=status_callback,
    )
    session_id = task_status.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise RunnerError("Terminal task response did not include session_id")
    trace = fetch_complete_trace(
        base_url=base_url,
        platform_token=platform_token,
        session_id=session_id,
        request_fn=request_fn,
    )
    return submission, task_status, trace


def read_prompt(args: argparse.Namespace) -> str:
    if args.prompt is not None:
        prompt = args.prompt
    else:
        prompt = args.prompt_file.read_text()
    prompt = prompt.strip()
    if not prompt:
        raise RunnerError("Prompt cannot be empty")
    return prompt


def required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RunnerError(f"Set {name} before running this script")
    return value


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Submit a Caylex background file-sync task, wait for completion, "
            "and save its task result and complete tool trace."
        )
    )
    prompt = parser.add_mutually_exclusive_group(required=True)
    prompt.add_argument(
        "--prompt",
        help="Background task prompt. Prefer --prompt-file for long prompts.",
    )
    prompt.add_argument("--prompt-file", type=Path)
    parser.add_argument(
        "--skill-ref",
        help="Optional project or global Caylex skill name/slug.",
    )
    parser.add_argument(
        "--approval-mode",
        choices=sorted(APPROVAL_MODES),
        default="exclude",
        help="Use exclude for read-only synchronization tasks.",
    )
    parser.add_argument("--model", help="Optional allowed OpenRouter model ID.")
    parser.add_argument(
        "--base-url",
        default="https://api.caylex.ai/api/v1",
        help="Caylex API base URL.",
    )
    parser.add_argument(
        "--platform-token-env",
        default="CAYLEX_PLATFORM_TOKEN",
    )
    parser.add_argument(
        "--api-key-env",
        default="CAYLEX_API_KEY",
    )
    parser.add_argument(
        "--user-email-env",
        default="CAYLEX_USER_EMAIL",
    )
    parser.add_argument("--poll-interval", type=float, default=5)
    parser.add_argument("--timeout", type=float, default=3600)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        prompt = read_prompt(args)
        submission, task_status, trace = run_background_file_sync(
            base_url=args.base_url,
            platform_token=required_env(args.platform_token_env),
            caylex_api_key=required_env(args.api_key_env),
            user_email=required_env(args.user_email_env),
            prompt=prompt,
            skill_ref=args.skill_ref,
            approval_mode=args.approval_mode,
            model=args.model,
            poll_interval=args.poll_interval,
            timeout=args.timeout,
            status_callback=lambda status, session_id: print(
                json.dumps({"status": status, "session_id": session_id}),
                flush=True,
            ),
        )
        write_json(args.output_dir / "submission.json", submission)
        write_json(args.output_dir / "task-status.json", task_status)
        write_json(args.output_dir / "raw-trace.json", trace)
    except (RunnerError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    summary = {
        "task_id": task_status.get("task_id"),
        "session_id": task_status.get("session_id"),
        "status": task_status.get("status"),
        "submission": str(args.output_dir / "submission.json"),
        "task_status": str(args.output_dir / "task-status.json"),
        "raw_trace": str(args.output_dir / "raw-trace.json"),
        "tool_call_groups": len(trace.get("tool_call_groups", [])),
    }
    print(json.dumps(summary, indent=2))
    return 0 if task_status.get("status") in {"COMPLETED", "INCOMPLETE"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
