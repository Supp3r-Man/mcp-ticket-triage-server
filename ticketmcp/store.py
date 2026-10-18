"""In-memory ticket store: search with cursor pagination, lookups and the two write operations."""
from __future__ import annotations

import base64
import copy
from datetime import datetime, timezone
from typing import Any, Callable

from .data import AGENTS, QUEUES, STATUSES, make_tickets


class ToolError(Exception):
    """A tool-level failure. Becomes an MCP result with isError=true, not a JSON-RPC error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _num(ticket_id: str) -> int:
    return int(ticket_id.split("-", 1)[1])


def _summary(t: dict[str, Any]) -> dict[str, Any]:
    return {k: t[k] for k in ("id", "subject", "queue", "status", "priority", "assignee", "customer", "created")}


def encode_cursor(ticket_id: str) -> str:
    return base64.urlsafe_b64encode(f"after:{ticket_id}".encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> int:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        prefix, _, ticket_id = raw.partition(":")
        if prefix != "after":
            raise ValueError("bad prefix")
        return _num(ticket_id)
    except (ValueError, IndexError, UnicodeDecodeError):
        raise ToolError("invalid_cursor", "cursor is not one returned by this server; start again without a cursor") from None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class TicketStore:
    def __init__(self, tickets: list[dict[str, Any]] | None = None, clock: Callable[[], str] = _now) -> None:
        self._tickets = {t["id"]: t for t in (tickets if tickets is not None else make_tickets())}
        self._order = sorted(self._tickets, key=_num)
        self._clock = clock

    def search(self, queue: str | None = None, status: str | None = None, priority: str | None = None,
               assignee: str | None = None, query: str | None = None, limit: int = 10,
               cursor: str | None = None) -> dict[str, Any]:
        after = decode_cursor(cursor) if cursor else 0
        needle = query.lower() if query else None

        def matches(t: dict[str, Any]) -> bool:
            if queue and t["queue"] != queue:
                return False
            if status and t["status"] != status:
                return False
            if priority and t["priority"] != priority:
                return False
            if assignee and (t["assignee"] or "unassigned") != assignee:
                return False
            if needle and needle not in t["subject"].lower() and needle not in t["body"].lower():
                return False
            return True

        hits = [self._tickets[i] for i in self._order if matches(self._tickets[i])]
        remaining = [t for t in hits if _num(t["id"]) > after]
        page = remaining[:limit]
        more = len(remaining) > limit
        return {
            "tickets": [_summary(t) for t in page],
            "next_cursor": encode_cursor(page[-1]["id"]) if more else None,
            "total_matches": len(hits),
        }

    def get(self, ticket_id: str) -> dict[str, Any]:
        t = self._tickets.get(ticket_id)
        if t is None:
            raise ToolError("not_found", f"no ticket {ticket_id}")
        return copy.deepcopy(t)

    def queues(self) -> dict[str, Any]:
        rows = []
        for q in QUEUES:
            in_q = [t for t in self._tickets.values() if t["queue"] == q]
            row: dict[str, Any] = {"queue": q, "total": len(in_q)}
            for s in STATUSES:
                row[s] = sum(1 for t in in_q if t["status"] == s)
            row["unassigned"] = sum(1 for t in in_q if t["assignee"] is None)
            rows.append(row)
        return {"queues": rows}

    def add_note(self, ticket_id: str, text: str, author: str) -> dict[str, Any]:
        t = self._tickets.get(ticket_id)
        if t is None:
            raise ToolError("not_found", f"no ticket {ticket_id}")
        note = {"id": len(t["notes"]) + 1, "author": author, "text": text, "created": self._clock()}
        t["notes"].append(note)
        return {"ticket_id": ticket_id, "note": copy.deepcopy(note), "note_count": len(t["notes"])}

    def assign(self, ticket_id: str, assignee: str | None) -> dict[str, Any]:
        t = self._tickets.get(ticket_id)
        if t is None:
            raise ToolError("not_found", f"no ticket {ticket_id}")
        if assignee is not None and assignee not in AGENTS:
            raise ToolError("unknown_assignee", f"assignee must be one of {', '.join(AGENTS)} (or null to unassign)")
        previous = t["assignee"]
        t["assignee"] = assignee
        return {"ticket_id": ticket_id, "assignee": assignee, "previous_assignee": previous}
