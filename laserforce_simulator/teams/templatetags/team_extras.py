from django import template

register = template.Library()


@register.filter
def lookup(dictionary, key):
    # Be defensive: dictionary may be None or not support .get
    try:
        return dictionary.get(key, [])
    except Exception:
        return []
