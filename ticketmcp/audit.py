"""Structured audit log: one JSON line per tool call, successful or not."""
from __future__ import annotations

import json
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable

_TEXT_PREVIEW = 80


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _redact(args: dict[str, Any]) -> dict[str, Any]:
    """Free text can be long and sensitive; the log keeps a preview and the length, not the whole thing."""
    out: dict[str, Any] = {}
    for key, value in args.items():
        if key == "text" and isinstance(value, str):
            out["text"] = value[:_TEXT_PREVIEW] + ("..." if len(value) > _TEXT_PREVIEW else "")
            out["text_len"] = len(value)
        else:
            out[key] = value
    return out


class AuditLog:
    def __init__(self, path: str | None = None, clock: Callable[[], str] = _now) -> None:
        self.path = path
        self._clock = clock
        self.entries: deque[dict[str, Any]] = deque(maxlen=1000)

    def record(self, tool: str, args: dict[str, Any], outcome: str, error_code: str | None,
               duration_ms: float, replayed: bool, writes_enabled: bool) -> dict[str, Any]:
        entry = {
            "ts": self._clock(), "tool": tool, "args": _redact(args), "outcome": outcome,
            "error_code": error_code, "duration_ms": round(duration_ms, 3), "replayed": replayed,
            "writes_enabled": writes_enabled,
        }
        self.entries.append(entry)
        if self.path:
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, sort_keys=True) + "\n")
        return entry
