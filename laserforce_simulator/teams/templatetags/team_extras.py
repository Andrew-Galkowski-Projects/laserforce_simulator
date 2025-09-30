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
