from __future__ import annotations

import httpx
from datetime import datetime
from typing import Any


def _error_detail(exc: Exception) -> str:
    """Pull a human-readable message out of an httpx error."""
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            return exc.response.json().get("detail", exc.response.text)
        except Exception:
            return exc.response.text
    return str(exc)


def _format_time(iso: str | None) -> tuple[str, str]:
    """Return (short, full) local-time strings for an ISO timestamp."""
    if not iso:
        return ("", "")
    local = datetime.fromisoformat(iso).astimezone()
    return (local.strftime("%H:%M"), local.strftime("%Y-%m-%d %H:%M:%S"))
