"""Publish a spec's tickets as GitHub sub-issues with native blocked-by edges.

Reads a JSON list of tickets, in dependency order (blockers first):

    [{"key": "A", "title": "X-1a: Foundation", "body_file": "x1_tA.md"},
     {"key": "B", "title": "X-1b: Screen", "body_file": "x1_tB.md", "blocked_by": ["A"]}]

``body_file`` is relative to the JSON file. Each ticket becomes an issue
labelled ``ready-for-agent``, a sub-issue of the spec, with one blocked-by edge
per blocker. Re-running is safe: an open issue with the same title is reused,
and a link or an edge that already exists is skipped, so a run that died half
way (ANG-06-18: #445 created, then a cp1252 decode error) is finished by
running it again. Installed ``gh`` predates ``--parent``, hence ``gh api``.

Usage:
    python scripts/publish_tickets.py <spec number> <tickets.json>
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

Action = tuple[str, ...]


@dataclass(frozen=True)
class Ticket:
    key: str
    title: str
    body_file: str
    blocked_by: tuple[str, ...]


def load_tickets(raw: list[dict], base: Path) -> list[Ticket]:
    """The JSON entries as Tickets, each ``body_file`` resolved against ``base``."""
    return [
        Ticket(
            key=entry["key"],
            title=entry["title"],
            body_file=str(base / entry["body_file"]),
            blocked_by=tuple(entry.get("blocked_by", ())),
        )
        for entry in raw
    ]


def validate(tickets: list[Ticket]) -> None:
    """Raise unless keys are unique and every blocker comes earlier in the list."""
    seen: set[str] = set()
    for ticket in tickets:
        if ticket.key in seen:
            raise ValueError(f"duplicate key {ticket.key}")
        for blocker in ticket.blocked_by:
            if blocker not in seen:
                raise ValueError(
                    f"{ticket.key} is blocked by {blocker}, which is not listed before it"
                )
        seen.add(ticket.key)


def plan(
    tickets: list[Ticket],
    existing: dict[str, int],
    sub_issues: set[int],
    edges: dict[int, set[int]],
) -> list[Action]:
    """The actions still missing, in order: ``("create", key)``, ``("link", key)``
    (make it a sub-issue of the spec) and ``("block", key, blocker_key)``.

    ``existing`` maps an open issue's title to its number, ``sub_issues`` holds
    the spec's sub-issue numbers and ``edges`` each issue's blocker numbers.
    """
    numbers = {t.key: existing.get(t.title) for t in tickets}
    actions: list[Action] = []
    for ticket in tickets:
        number = numbers[ticket.key]
        if number is None:
            actions.append(("create", ticket.key))
        if number is None or number not in sub_issues:
            actions.append(("link", ticket.key))
        for blocker in ticket.blocked_by:
            blocker_number = numbers[blocker]
            already = (
                number is not None
                and blocker_number is not None
                and blocker_number in edges.get(number, set())
            )
            if not already:
                actions.append(("block", ticket.key, blocker))
    return actions


def _gh(*args: str) -> str:
    # UTF-8, not the Windows console code page: issue bodies hold em dashes.
    return subprocess.run(
        ["gh", *args], check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout.strip()


def _gh_json(*args: str) -> list | dict:
    return json.loads(_gh(*args) or "null")


def _repo() -> str:
    return _gh("repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner")


def _db_id(repo: str, number: int) -> int:
    return int(_gh("api", f"repos/{repo}/issues/{number}", "--jq", ".id"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("spec", type=int, help="the spec issue number")
    parser.add_argument("tickets", type=Path, help="the tickets JSON file")
    args = parser.parse_args()

    tickets = load_tickets(
        json.loads(args.tickets.read_text(encoding="utf-8")), args.tickets.parent
    )
    validate(tickets)
    repo = _repo()

    titles = {t.title for t in tickets}
    listed = _gh_json(
        "issue", "list", "--state", "open", "--limit", "200", "--json", "number,title"
    )
    existing = {i["title"]: i["number"] for i in listed if i["title"] in titles}
    sub_issues = {
        i["number"]
        for i in _gh_json(
            "api", "--paginate", f"repos/{repo}/issues/{args.spec}/sub_issues"
        )
    }
    edges = {
        number: {
            i["number"]
            for i in _gh_json(
                "api", f"repos/{repo}/issues/{number}/dependencies/blocked_by"
            )
        }
        for number in existing.values()
    }

    numbers = dict((t.key, existing[t.title]) for t in tickets if t.title in existing)
    by_key = {t.key: t for t in tickets}
    for action in plan(tickets, existing, sub_issues, edges):
        kind, key = action[0], action[1]
        ticket = by_key[key]
        if kind == "create":
            url = _gh(
                "issue",
                "create",
                "--title",
                ticket.title,
                "--label",
                "ready-for-agent",
                "--body-file",
                ticket.body_file,
            )
            numbers[key] = int(url.rsplit("/", 1)[1])
            print(f"created #{numbers[key]}  {ticket.title}")
        elif kind == "link":
            _gh(
                "api",
                "--method",
                "POST",
                f"repos/{repo}/issues/{args.spec}/sub_issues",
                "-F",
                f"sub_issue_id={_db_id(repo, numbers[key])}",
            )
            print(f"linked  #{numbers[key]} under #{args.spec}")
        else:
            blocker = numbers[action[2]]
            _gh(
                "api",
                "--method",
                "POST",
                f"repos/{repo}/issues/{numbers[key]}/dependencies/blocked_by",
                "-F",
                f"issue_id={_db_id(repo, blocker)}",
            )
            print(f"blocked #{numbers[key]} by #{blocker}")
    print(" ".join(f"{t.key}=#{numbers[t.key]}" for t in tickets))
    return 0


if __name__ == "__main__":
    sys.exit(main())
