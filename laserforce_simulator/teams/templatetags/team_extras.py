from django import template

register = template.Library()


@register.filter
def lookup(dictionary, key):
    # Be defensive: dictionary may be None or not support .get
    try:
        return dictionary.get(key, [])
    except Exception:
        return []


@register.filter
def div(value, arg):
    """Safe division: return value/arg or 0 if not possible."""
    try:
        a = float(value)
        b = float(arg)
        if b == 0:
            return 0
        return a / b
    except Exception:
        return 0


@register.filter
def mul(value, arg):
    """Safe multiplication: return value*arg or 0 if not possible."""
    try:
        a = float(value)
        b = float(arg)
        return a * b
    except Exception:
        return 0


@register.filter
def mul_int(value, arg):
    """Multiply and return an integer (whole number) for display."""
    try:
        a = float(value)
        b = float(arg)
        return int(a * b)
    except Exception:
        return 0


@register.filter
def specials_total(specials_used, perf):
    """Compute total specials cost used and return tuple-like string.
    Usage in template: {{ perf.specials_used|mul:perf.special_cost }}
    or to compute balance elsewhere. Kept for convenience if needed.
    """
    try:
        cost = float(perf.special_cost)
        used = float(specials_used)
        return used * cost
    except Exception:
        return 0


@register.filter
def specials_total_int(specials_used, perf):
    """Compute specials_used * perf.special_cost and return whole number."""
    try:
        cost = float(perf.special_cost)
        used = float(specials_used)
        return int(used * cost)
    except Exception:
        return 0


@register.filter
def count_attr_false(iterable, attr_name):
    """Count items in iterable where the given attribute is falsy (False or missing).

    Usage: {{ red_performances|count_attr_false:"was_eliminated" }}
    """
    try:
        count = 0
        for item in iterable:
            # use getattr to handle missing attributes defensively
            if not getattr(item, attr_name, False):
                count += 1
        return count
    except Exception:
        return 0


@register.filter
def count_survivors(iterable):
    """Count items in iterable where the player is alive (final_lives > 0).

    This replaces older checks that relied on a boolean `was_eliminated` field.
    """
    try:
        count = 0
        for item in iterable:
            try:
                if getattr(item, "final_lives", 1) > 0:
                    count += 1
            except Exception:
                continue
        return count
    except Exception:
        return 0


@register.filter
def is_eliminated(item):
    """Return True if the given player round state represents an eliminated player.

    Uses `was_eliminated_at` when present, otherwise falls back to `final_lives == 0`.
    """
    try:
        # Prefer explicit timestamp field if present. A sentinel value (e.g. 901)
        # is treated as not eliminated.
        ts = getattr(item, "was_eliminated_at", None)
        if ts is not None:
            # treat None or large sentinel (>1000) as not eliminated
            try:
                val = int(ts)
                if val > 900 or val == 0:
                    return False
                return True
            except Exception:
                return False
        # Fallback to final_lives
        return getattr(item, "final_lives", 1) <= 0
    except Exception:
        return False
