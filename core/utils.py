from typing import Optional

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.core.mail import send_mail

from core.models import InventoryMovement, ServiceOrder, StatusHistory
from core.permissions import is_gerencia, is_recepcion, is_tecnico


def resolve_actor_role(user: Optional[AnonymousUser]) -> str:
    if not getattr(user, "is_authenticated", False):
        return "Publico"
    if getattr(user, "is_superuser", False):
        return "Superusuario"
    if is_gerencia(user):
        return "Gerencia"
    if is_recepcion(user):
        return "Recepcion"
    if is_tecnico(user):
        return "Tecnico"
    return "Staff"


def log_status_snapshot(order, *, author=None, previous_status="", new_status=None):
    role = resolve_actor_role(author)
    return StatusHistory.log(
        order,
        from_status=previous_status,
        to_status=new_status,
        author=author,
        author_role=role,
    )


def build_device_label(order):
    device = getattr(order, "device", None)
    if not device:
        return "Equipo"
    parts = []
    for attr in ("brand", "model"):
        value = getattr(device, attr, "")
        if value:
            parts.append(value)
    serial = getattr(device, "serial", "")
    if serial:
        if parts:
            parts.append(f"({serial})")
        else:
            parts.append(serial)
    label = " ".join(parts).strip()
    if label:
        return label
    try:
        return str(device)
    except Exception:
        return "Equipo"


def send_order_status_email(
    *,
    order,
    notification,
    status_code,
    public_url,
    device_label="",
    extra_context=None,
):
    customer = getattr(order.device, "customer", None)
    customer_email = (getattr(customer, "email", "") or "").strip() if customer else ""
    customer_name = getattr(customer, "name", "") if customer else ""
    resolved_label = device_label or build_device_label(order)
    payload = notification.payload or {}
    payload.update(
        {
            "to": customer_email,
            "status": status_code,
            "order_folio": getattr(order, "folio", ""),
            "order": getattr(order, "folio", ""),
            "customer": customer_name,
            "device": resolved_label,
            "public_url": public_url,
        }
    )
    if extra_context:
        payload.update(extra_context)

    if not customer_email:
        payload["error"] = "missing_email"
        notification.ok = False
        notification.payload = payload
        notification.save(update_fields=["ok", "payload"])
        return False, "missing_email"

    subject, body = _compose_status_email(
        order,
        status_code=status_code,
        customer_name=customer_name,
        public_url=public_url,
        device_label=resolved_label,
        extra_context=extra_context or {},
    )
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "") or "notificaciones@integrasys.local"

    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=from_email,
            recipient_list=[customer_email],
            fail_silently=False,
        )
        notification.ok = True
    except Exception as exc:
        payload["error"] = str(exc)
        notification.ok = False
        notification.payload = payload
        notification.save(update_fields=["ok", "payload"])
        return False, str(exc)

    notification.payload = payload
    notification.save(update_fields=["ok", "payload"])
    return True, None


def apply_estimate_inventory(order, *, author=None):
    try:
        estimate = order.estimate
    except ServiceOrder.estimate.RelatedObjectDoesNotExist:
        return True, None
    if getattr(estimate, "inventory_applied", False):
        return True, None
    consumables = list(estimate.items.select_related("inventory_item").filter(inventory_item__isnull=False))
    if not consumables:
        estimate.inventory_applied = True
        estimate.save(update_fields=["inventory_applied"])
        return True, None
    insufficient = []
    for item in consumables:
        stock_item = item.inventory_item
        if stock_item.qty < item.qty:
            insufficient.append(stock_item.sku or stock_item.name)
    if insufficient:
        return False, f"Stock insuficiente para: {', '.join(insufficient)}"
    for item in consumables:
        stock_item = item.inventory_item
        InventoryMovement.objects.create(
            item=stock_item,
            delta=-item.qty,
            reason=f"Consumo cotizacion {order.folio}",
            order=order,
            author=author,
        )
        stock_item.qty -= item.qty
        stock_item.save(update_fields=["qty"])
    estimate.inventory_applied = True
    estimate.save(update_fields=["inventory_applied"])
    return True, None


def _compose_status_email(
    order,
    *,
    status_code,
    customer_name,
    public_url,
    device_label,
    extra_context,
):
    friendly_name = customer_name or "cliente"
    lines = [f"Hola {friendly_name},"]
    status_display = order.get_status_display()
    if status_code == ServiceOrder.Status.READY_PICKUP:
        subject = f"Tu orden {order.folio} esta lista para recoger"
        lines.append("")
        lines.append(f"Tu equipo {device_label or ''} esta listo para entrega.")
        lines.append(f"Estatus: {status_display}.")
    elif status_code == ServiceOrder.Status.REQUIRES_AUTH:
        subject = f"Tu orden {order.folio} requiere autorizacion"
        lines.append("")
        lines.append("Necesitamos tu autorizacion para continuar con el servicio.")
        estimate_url = extra_context.get("estimate_url")
        if estimate_url:
            lines.append(f"Revisa la cotizacion aqui: {estimate_url}")
        lines.append("Puedes consultar el estado general en el enlace publico.")
    elif status_code == ServiceOrder.Status.DELIVERED:
        subject = f"Tu orden {order.folio} ha sido entregada"
        lines.append("")
        lines.append("Confirmamos que entregamos tu equipo.")
        if device_label:
            lines.append(f"Equipo: {device_label}.")
        lines.append("Si tienes dudas contactanos.")
    else:
        subject = f"Actualizacion de tu orden {order.folio}"
        lines.append("")
        lines.append(f"Estatus: {status_display}.")
    lines.append("")
    lines.append(f"Detalle publico: {public_url}")
    lines.append("")
    lines.append("Gracias por tu confianza.")
    body = "\n".join(lines)
    return subject, body
