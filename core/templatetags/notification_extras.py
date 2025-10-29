from django import template

register = template.Library()


def _extract(payload, key):
    if payload is None:
        return None
    try:
        if isinstance(payload, dict):
            return payload.get(key)
        return getattr(payload, key, None)
    except Exception:
        return None


@register.filter(name="payload_get")
def payload_get(payload, key):
    return _extract(payload, key)


@register.simple_tag
def payload_first(payload, *keys):
    for key in keys:
        value = _extract(payload, key)
        if value not in (None, "", []):
            return value
    return ""


@register.filter(name="has_value")
def has_value(value):
    return value not in (None, "", [])
