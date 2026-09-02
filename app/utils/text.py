"""Small text helpers shared across layers."""

from typing import Optional


def truncate(text: Optional[str], limit: int) -> str:
    """Trim text to a limit, marking it with an ellipsis when cut."""
    if not text:
        return ""
    collapsed = text.strip()
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"
