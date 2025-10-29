from typing import Dict

from .permissions import is_recepcion, is_tecnico, is_gerencia
from .models import Notification


def nav_notifications(request) -> Dict[str, object]:
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}
    if not (is_recepcion(user) or is_tecnico(user) or is_gerencia(user)):
        return {}

    base_qs = Notification.objects.filter(kind__in=("estimate", "stock")).order_by("-created_at")
    unread_count = base_qs.filter(seen_at__isnull=True).count()
    latest = list(base_qs[:5])
    return {
        "nav_notifications": latest,
        "nav_notifications_unread": unread_count,
    }
