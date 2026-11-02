"""Shared data types and small helpers (no network, no Telegram)."""
from dataclasses import dataclass


@dataclass
class Result:
    submitted_url: str
    final_url: str
    title: str
    size: str | None
    links: list[tuple[str, str]]
    note: str | None = None
    needs_click: bool = False  # page builds its link on a button click


def fmt_bytes(value) -> str | None:
    """Format a byte count (int or digit-string) as '1.50 MB'."""
    try:
        n = float(int(value))
    except (TypeError, ValueError):
        return None
    if n < 0:
        return None
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{int(n)} B" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024
    return None
