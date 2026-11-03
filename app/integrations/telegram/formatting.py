"""Message formatting helpers. Replies use HTML parse mode with all dynamic text
escaped; MarkdownV2 needs too much escaping to be reliable with LLM output."""

import html
import re
from datetime import timedelta

TELEGRAM_MESSAGE_LIMIT = 4096
MAX_PAUSE = timedelta(days=90)

_DURATION_RE = re.compile(r"^(\d{1,4})\s*([mhd])$", re.IGNORECASE)
_UNITS = {"m": "minutes", "h": "hours", "d": "days"}


def escape(text: str) -> str:
    return html.escape(text, quote=False)


def split_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Split on paragraph, then line, then hard boundaries to respect `limit`."""
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []
    chunks: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(paragraph) > limit:
            cut = paragraph.rfind("\n", 0, limit)
            if cut <= 0:
                cut = paragraph.rfind(" ", 0, limit)
            if cut <= 0:
                cut = limit
            chunks.append(paragraph[:cut].rstrip())
            paragraph = paragraph[cut:].lstrip()
        current = paragraph
    if current:
        chunks.append(current)
    return chunks


def parse_duration(value: str) -> timedelta | None:
    """'30m', '12h', '3d' -> timedelta. None if invalid, zero or above `MAX_PAUSE`."""
    match = _DURATION_RE.match(value.strip())
    if not match:
        return None
    duration = timedelta(**{_UNITS[match.group(2).lower()]: int(match.group(1))})
    if not timedelta(0) < duration <= MAX_PAUSE:
        return None
    return duration
