#!/usr/bin/env python3
"""Import GitHub issues into Beads as epics, including comments.

Defaults to open issues only and skips labels/assignees. Idempotent by
tracking GitHub issue numbers in Beads external_ref as gh-<number>.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable


def run(
    cmd: list[str], input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        cmd,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{stderr}")
    return result


def gh_cmd(base: list[str], repo: str | None) -> list[str]:
    if repo:
        return base + ["--repo", repo]
    return base


def load_existing_refs() -> dict[str, str]:
    result = run(["bd", "export", "--force"])
    existing: dict[str, str] = {}
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        ref = obj.get("external_ref")
        if ref:
            existing[ref] = obj.get("id")
    return existing


def existing_comment_urls(bead_id: str) -> set[str]:
    result = run(["bd", "comments", bead_id, "--json"])
    text = result.stdout.strip()
    if not text or text == "null":
        return set()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return set()
    urls: set[str] = set()
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            body = item.get("text") or item.get("body") or ""
            if not isinstance(body, str):
                continue
            lines = body.splitlines()
            if not lines:
                continue
            if not lines[0].startswith("Imported from GitHub by "):
                continue
            if len(lines) > 1:
                url = lines[1].strip()
                if url.startswith("http"):
                    urls.add(url)
    return urls


def list_issue_numbers(repo: str | None, state: str, limit: int) -> list[int]:
    cmd = gh_cmd(
        [
            "gh",
            "issue",
            "list",
            "--state",
            state,
            "--limit",
            str(limit),
            "--json",
            "number",
        ],
        repo,
    )
    result = run(cmd)
    return [item["number"] for item in json.loads(result.stdout)]


def load_issue(repo: str | None, number: int) -> dict[str, object]:
    cmd = gh_cmd(
        [
            "gh",
            "issue",
            "view",
            str(number),
            "--json",
            "number,title,body,url,author,createdAt,comments",
        ],
        repo,
    )
    result = run(cmd)
    return json.loads(result.stdout)


def format_description(issue: dict[str, object]) -> str:
    url = str(issue["url"])
    author = issue.get("author") or {}
    author_login = "unknown"
    if isinstance(author, dict):
        author_login = author.get("login") or "unknown"
    created_at = issue.get("createdAt") or "unknown"
    body = (issue.get("body") or "").strip() or "(no description)"
    return "\n".join(
        [
            f"GitHub: {url}",
            f"Opened by @{author_login} on {created_at}",
            "",
            body,
        ]
    )


def add_comment(bead_id: str, author: str, text: str) -> None:
    with tempfile.NamedTemporaryFile("w", delete=False) as tmp:
        tmp.write(text)
        tmp_path = tmp.name
    try:
        run(["bd", "comments", "add", bead_id, "--author", author, "-f", tmp_path])
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def format_comment(comment: dict[str, object]) -> tuple[str, str, str]:
    author = comment.get("author") or {}
    author_login = "unknown"
    if isinstance(author, dict):
        author_login = author.get("login") or "unknown"
    created_at = comment.get("createdAt") or "unknown"
    url = comment.get("url") or ""
    body = (comment.get("body") or "").rstrip() or "(no comment body)"
    text = "\n".join(
        [
            f"Imported from GitHub by @{author_login} on {created_at}",
            str(url),
            "",
            body,
            "",
        ]
    )
    return author_login, str(url), text


def create_issue_epic(title: str, external_ref: str, description: str) -> str:
    result = run(
        [
            "bd",
            "create",
            "--type",
            "epic",
            "--title",
            title,
            "--external-ref",
            external_ref,
            "--body-file",
            "-",
            "--silent",
        ],
        input_text=description,
    )
    return result.stdout.strip()


def import_issue(
    issue: dict[str, object], existing_refs: dict[str, str], dry_run: bool
) -> tuple[str | None, int, bool]:
    number = int(issue["number"])
    title = str(issue["title"])
    external_ref = f"gh-{number}"

    bead_id = existing_refs.get(external_ref)
    created = False
    if not bead_id:
        if dry_run:
            created = True
            print(f"Would create epic for issue #{number}: {title}")
        else:
            description = format_description(issue)
            bead_id = create_issue_epic(title, external_ref, description)
            existing_refs[external_ref] = bead_id
            created = True
            print(f"Created {bead_id} for issue #{number}.")
    else:
        print(f"Using existing {bead_id} for issue #{number}.")

    comments = issue.get("comments")
    if not isinstance(comments, list) or not comments:
        return bead_id, 0, created

    added = 0

    if dry_run:
        seen_urls = existing_comment_urls(bead_id) if bead_id else set()
        for comment in comments:
            if not isinstance(comment, dict):
                continue
            url = str(comment.get("url") or "")
            if url and url in seen_urls:
                continue
            added += 1
        if added:
            print(f"  Would add {added} comment(s).")
        return bead_id, added, created

    seen_urls = existing_comment_urls(bead_id)
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        author_login, url, text = format_comment(comment)
        if url and url in seen_urls:
            continue
        add_comment(bead_id, author_login, text)
        if url:
            seen_urls.add(url)
        added += 1

    if added:
        print(f"  Added {added} comment(s).")
    return bead_id, added, created


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import GitHub issues into Beads epics with comments."
    )
    parser.add_argument(
        "--repo",
        help="Override repo for gh commands (OWNER/REPO).",
    )
    parser.add_argument(
        "--state",
        default="open",
        help="Issue state to import (default: open).",
    )
    parser.add_argument(
        "--limit",
        default=1000,
        type=int,
        help="Maximum number of issues to query (default: 1000).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions without creating issues or comments.",
    )
    return parser.parse_args(list(argv))


def main(argv: Iterable[str]) -> int:
    args = parse_args(argv)

    issue_numbers = list_issue_numbers(args.repo, args.state, args.limit)
    if not issue_numbers:
        print("No matching GitHub issues found.")
        return 0

    existing_refs = load_existing_refs()
    created = 0
    reused = 0
    comments_added = 0

    for number in issue_numbers:
        issue = load_issue(args.repo, number)
        _, added, was_created = import_issue(issue, existing_refs, args.dry_run)
        comments_added += added
        if was_created:
            created += 1
        else:
            reused += 1

    if args.dry_run:
        print(
            "Dry run: would import "
            f"{len(issue_numbers)} issue(s): {created} created, {reused} reused."
        )
        if comments_added:
            print(f"Dry run: would import {comments_added} comment(s).")
        return 0

    print(
        f"Imported {len(issue_numbers)} issue(s): {created} created, {reused} reused."
    )
    if comments_added:
        print(f"Imported {comments_added} comment(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
