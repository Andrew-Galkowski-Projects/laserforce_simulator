"""UX-02 — the "You" badge marking the viewing Account's Self-insert.

Usage (load explicitly in every template / partial that uses it)::

    {% load self_insert_tags %}
    {{ player.name }}{% you_badge player.id %}
"""

from __future__ import annotations

from django import template
from django.template import Context
from django.utils.safestring import mark_safe

register = template.Library()

YOU_BADGE_HTML = '<span class="badge text-bg-primary ms-1 you-badge" title="Your Self-insert">You</span>'


@register.simple_tag(takes_context=True)
def you_badge(context: Context, player_id: object) -> str:
    """Render ``YOU_BADGE_HTML`` iff ``player_id`` is the context's Self-insert id.

    Returns ``""`` when ``self_insert_player_id`` is missing / ``None``, when
    ``player_id`` is not int-coercible, or when the ids differ. Never raises.
    """
    self_id = context.get("self_insert_player_id")
    if self_id is None or player_id is None or player_id == "":
        return ""
    try:
        if int(player_id) == int(self_id):  # type: ignore[call-overload]
            return mark_safe(YOU_BADGE_HTML)
    except (TypeError, ValueError):
        return ""
    return ""
