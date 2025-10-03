from decimal import Decimal, InvalidOperation
from django import template

register = template.Library()


@register.filter
def money(value):
    """Formatea a $1,234.56. Soporta None, str, Decimal, float/int."""
    try:
        if value is None or value == "":
            value = Decimal("0")
        if not isinstance(value, Decimal):
            value = Decimal(str(value))
        return f"${value:,.2f}"
    except (InvalidOperation, ValueError, TypeError):
        try:
            return f"${Decimal('0'):,.2f}"
        except Exception:
            return "$0.00"
