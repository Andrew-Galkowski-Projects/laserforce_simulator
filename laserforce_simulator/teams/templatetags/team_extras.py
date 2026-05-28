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
