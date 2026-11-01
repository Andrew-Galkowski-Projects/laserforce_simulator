"""The Desktop App's reset notice, held for the life of the process.

The launcher sets it when ``migrate_or_reset`` moved the saved database aside;
``accounts/me/`` reads it as ``desktop_notice``.
"""

from __future__ import annotations

_notice: str | None = None


def set_notice(text: str | None) -> None:
    global _notice
    _notice = text


def current_notice() -> str | None:
    return _notice
