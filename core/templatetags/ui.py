from django import template

register = template.Library()

# Mapea los estados de ServiceOrder.Status a colores Bootstrap
BADGES = {
    "NEW": "primary",      # Recibido
    "REV": "warning",      # En revision
    "WAI": "info",         # En espera de repuestos
    "AUTH": "secondary",   # Requiere autorizacion
    "READY": "success",    # Listo para recoger
    "DONE": "dark",        # Entregado
}

@register.filter
def status_badge(code):
    """Devuelve el color Bootstrap para un codigo de estado."""
    if not code:
        return "secondary"
    return BADGES.get(str(code), "secondary")
