from django.http import HttpResponse, Http404, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.contrib import messages
from django.db.models import Q, Count
from django.db.models.functions import TruncDate
from django.db import models
from django.views.decorators.http import require_POST
from django.template.loader import render_to_string
from django.core.mail import send_mail
from django.utils import timezone
from django.conf import settings
from django.contrib.auth.models import User
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from django.db import transaction
from xhtml2pdf import pisa
import base64
from io import BytesIO
import qrcode
from datetime import datetime, timedelta, time
import csv
import re
import mimetypes
from pathlib import Path
from urllib.parse import quote, urlencode
import logging

from .forms import ReceptionForm, ReceptionDeviceFormSet, InventoryItemForm, AttachmentForm, CustomerForm
from .models import (
    Customer,
    Device,
    ServiceOrder,
    StatusHistory,
    Notification,
    InventoryItem,
    InventoryMovement,
    Estimate,
    EstimateItem,
    Payment,
    Attachment,
)
from .permissions import (
    ROLE_RECEPCION,
    ROLE_GERENCIA,
    ROLE_TECNICO,
    group_required,
    is_manager,
    is_gerencia,
    is_recepcion,
    is_tecnico,
    require_manager,
)
from .utils import (
    apply_estimate_inventory,
    build_device_label,
    build_single_device_label,
    resolve_actor_role,
    log_status_snapshot,
    send_order_status_email,
    format_csv_datetime,
    notify_estimate_item_decision,
)



IVA_RATE = Decimal("0.16")
TWO_PLACES = Decimal("0.01")

logger = logging.getLogger(__name__)


def _estimate_status_label(estimate):
    if not estimate:
        return "Sin cotizacion"
    status_labels = {
        Estimate.Status.PENDING: "Pendiente",
        Estimate.Status.CLOSED_ACCEPTED: "Cerrada · aprobada",
        Estimate.Status.CLOSED_PARTIAL: "Cerrada · aceptacion parcial",
        Estimate.Status.CLOSED_REJECTED: "Cerrada · rechazada",
    }
    if getattr(estimate, "status", None) in status_labels:
        return status_labels[estimate.status]
    if estimate.approved_at:
        return f"Aprobada ({timezone.localtime(estimate.approved_at).strftime('%Y-%m-%d %H:%M')})"
    if estimate.declined_at:
        return f"Rechazada ({timezone.localtime(estimate.declined_at).strftime('%Y-%m-%d %H:%M')})"
    return "Pendiente"


def _order_devices(order):
    devices = []
    try:
        prefetched = order._prefetched_objects_cache  # type: ignore[attr-defined]
    except AttributeError:
        prefetched = {}
    if prefetched and "devices" in prefetched:
        devices = list(prefetched["devices"])
    else:
        try:
            devices = list(order.devices.all())
        except Exception:
            devices = []
    if not devices and order.device_id:
        devices = [order.device]
    return devices


def _build_estimate_form_context(request, order, estimate, *, form_items=None, note=None):
    items_qs = list(estimate.items.select_related("inventory_item").order_by("id"))
    if form_items is None:
        items = [
            {
                "description": item.description,
                "qty": str(item.qty),
                "unit_price": f"{item.unit_price.quantize(TWO_PLACES)}",
                "inventory_sku": item.inventory_item.sku if item.inventory_item else "",
            }
            for item in items_qs
        ]
    else:
        items = form_items
    has_saved_items = bool(items_qs)
    public_url = None
    if has_saved_items:
        public_url = request.build_absolute_uri(reverse("estimate_public", args=[estimate.token]))
    context_note = note if note is not None else (estimate.note or "")
    inventory_catalog = InventoryItem.objects.order_by("name").only("sku", "name")
    return {
        "order": order,
        "estimate": estimate,
        "items": items,
        "note": context_note,
        "status_label": _estimate_status_label(estimate),
        "has_saved_items": has_saved_items,
        "public_estimate_url": public_url,
        "inventory_items": inventory_catalog,
    }


def _build_estimate_message(order, customer, public_url):
    customer_name = getattr(customer, "name", "") if customer else ""
    if not customer_name:
        customer_name = "cliente"
    return f"Hola {customer_name},\nRevisa la cotizacion {order.folio}: {public_url}"


def _estimate_item_counts(estimate):
    try:
        rows = estimate.items.values("status").annotate(total=models.Count("id"))
    except Exception:
        return {}
    return {row["status"]: row["total"] for row in rows}


def _resolve_order_folio(order):
    folio = getattr(order, "folio", None)
    if folio:
        return folio
    order_id = getattr(order, "id", None)
    if order_id:
        return f"SR-{order_id}"
    return "SR-0000"


def _record_status_update(order, *, channel="status_change", ok=True, extra_payload=None, title=None):
    try:
        device = getattr(order, "device", None)
        customer = getattr(device, "customer", None) if device else None
        customer_name = getattr(customer, "name", "") if customer else ""
        payload = {
            "order_id": getattr(order, "id", None),
            "order_folio": _resolve_order_folio(order),
            "order": _resolve_order_folio(order),
            "customer": customer_name,
            "device": build_device_label(order),
            "status": getattr(order, "status", ""),
            "status_display": order.get_status_display() if hasattr(order, "get_status_display") else getattr(order, "status", ""),
        }
        if extra_payload:
            payload.update(extra_payload)
        Notification.objects.create(
            order=order,
            kind="update",
            channel=channel,
            ok=ok,
            title=title or f"Actualizacion {payload['order_folio']}",
            payload=payload,
        )
    except Exception:
        logger.exception("Error registrando notificacion de estado")


def _create_customer(name, phone, email, alt_phone=""):
    return Customer.objects.create(
        name=name,
        phone=phone or "",
        alt_phone=alt_phone or "",
        email=email or "",
    )


def _ensure_device(customer, *, brand, model, serial, notes, password_notes="", accessories_notes=""):
    serial_norm = (serial or "").strip().upper()
    password_notes_value = (password_notes or "").strip()
    accessories_notes_value = (accessories_notes or "").strip()
    device = None
    if serial_norm:
        device = (
            Device.objects.filter(customer=customer, serial__iexact=serial_norm)
            .order_by("id")
            .first()
        )
    if device is None:
        device = (
            Device.objects.filter(
                customer=customer,
                serial="",
                brand__iexact=brand,
                model__iexact=model,
            )
            .order_by("id")
            .first()
        )
    if device is None:
        return Device.objects.create(
            customer=customer,
            brand=brand,
            model=model,
            serial=serial_norm,
            notes=notes,
            password_notes=password_notes_value,
            accessories_notes=accessories_notes_value,
        )

    updates = []
    if serial_norm:
        current_serial = (device.serial or "").strip()
        if not current_serial or current_serial.upper() != serial_norm:
            device.serial = serial_norm
            updates.append("serial")
    if brand and not device.brand:
        device.brand = brand
        updates.append("brand")
    if model and not device.model:
        device.model = model
        updates.append("model")
    if notes and not device.notes:
        device.notes = notes
        updates.append("notes")
    elif notes and notes.strip() and (device.notes or "").strip() != notes.strip():
        device.notes = notes
        updates.append("notes")
    if password_notes_value and (device.password_notes or "").strip() != password_notes_value:
        device.password_notes = password_notes_value
        updates.append("password_notes")
    if accessories_notes_value and (device.accessories_notes or "").strip() != accessories_notes_value:
        device.accessories_notes = accessories_notes_value
        updates.append("accessories_notes")
    if updates:
        device.save(update_fields=updates)
    return device


_PDF_LOGO_CACHE = None


def _get_pdf_logo():
    global _PDF_LOGO_CACHE
    if _PDF_LOGO_CACHE is not None:
        return _PDF_LOGO_CACHE
    logo_file = Path(settings.BASE_DIR) / "static" / "img" / "brand" / "logo-integrasys-color.svg"
    try:
        _PDF_LOGO_CACHE = base64.b64encode(logo_file.read_bytes()).decode("ascii")
    except Exception:
        _PDF_LOGO_CACHE = ""
    return _PDF_LOGO_CACHE


def build_whatsapp_link(phone: str, text: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if not digits:
        return ""
    if not digits.startswith("52"):
        digits = "52" + digits
    return f"https://wa.me/{digits}?text={quote(text)}"


def public_status(request, token):
    try:
        order = (
            ServiceOrder.objects.select_related("customer")
            .prefetch_related("devices")
            .get(token=token)
        )
    except ServiceOrder.DoesNotExist:
        raise Http404("Orden no encontrada")
    history = order.history.order_by("created_at")
    return render(request, "public_status.html", {"order": order, "history": history})


def qr(request, token):
    url = request.build_absolute_uri(reverse("public_status", args=[token]))
    img = qrcode.make(url)
    buf = BytesIO()
    img.save(buf, format="PNG")
    resp = HttpResponse(buf.getvalue(), content_type="image/png")
    resp["Cache-Control"] = "public, max-age=86400"
    return resp


def receipt_pdf(request, token):
    order = get_object_or_404(
        ServiceOrder.objects.select_related("customer").prefetch_related("devices"),
        token=token,
    )
    public_url = request.build_absolute_uri(reverse("public_status", args=[token]))

    try:
        buf = BytesIO()
        qrcode.make(public_url).save(buf, format="PNG")
        qr_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        qr_b64 = ""

    logo_b64 = _get_pdf_logo()

    html = render_to_string(
        "receipt.html",
        {"order": order, "qr_b64": qr_b64, "public_url": public_url, "logo_b64": logo_b64},
    )

    pdf_io = BytesIO()
    try:
        result = pisa.CreatePDF(html, dest=pdf_io, encoding="utf-8")
        if result.err:
            return HttpResponse(html, content_type="text/html", status=500)
        resp = HttpResponse(pdf_io.getvalue(), content_type="application/pdf")
        resp["Content-Disposition"] = f'inline; filename="recibo-{order.folio}.pdf"'
        return resp
    except Exception:
        return HttpResponse(html, content_type="text/html", status=500)


@login_required(login_url="/admin/login/")
@group_required(ROLE_RECEPCION, ROLE_GERENCIA, ROLE_TECNICO)
def order_ticket_view(request, pk):
    order = get_object_or_404(
        ServiceOrder.objects.select_related("customer").prefetch_related("devices"),
        pk=pk,
    )
    devices = _order_devices(order)
    public_url = request.build_absolute_uri(reverse("public_status", args=[order.token]))

    qr_b64 = ""
    public_qr_url = ""
    try:
        buf = BytesIO()
        qrcode.make(public_url).save(buf, format="PNG")
        qr_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        public_qr_url = f"data:image/png;base64,{qr_b64}"
    except Exception:
        qr_b64 = ""
        public_qr_url = ""

    context = {
        "order": order,
        "devices": devices,
        "customer": order.get_customer(),
        "public_url": public_url,
        "public_status_url": public_url,
        "qr_b64": qr_b64,
        "public_qr_url": public_qr_url,
    }
    return render(request, "tickets/order_ticket.html", context)


@login_required(login_url="/admin/login/")
@require_manager
def dashboard(request):
    q = (request.GET.get("q", "") or "").strip()
    status = (request.GET.get("status", "") or "").strip()
    assignee = (request.GET.get("assignee", "") or "").strip()
    dfrom = (request.GET.get("from", "") or "").strip()
    dto = (request.GET.get("to", "") or "").strip()

    tz = timezone.get_current_timezone()
    local_now = timezone.localtime(timezone.now(), tz)

    def make_aware(value):
        if settings.USE_TZ and timezone.is_naive(value):
            return timezone.make_aware(value, tz)
        return value

    from_date = None
    from_dt = None
    if dfrom:
        try:
            from_date = datetime.strptime(dfrom, "%Y-%m-%d").date()
            from_dt = make_aware(datetime.combine(from_date, time.min))
        except ValueError:
            from_date = None
            from_dt = None

    to_date = None
    to_dt = None
    if dto:
        try:
            to_date = datetime.strptime(dto, "%Y-%m-%d").date()
            to_dt = make_aware(datetime.combine(to_date + timedelta(days=1), time.min))
        except ValueError:
            to_date = None
            to_dt = None

    qs = ServiceOrder.objects.select_related("device", "device__customer").order_by("-checkin_at")

    if q:
        search = (
            Q(folio__icontains=q)
            | Q(device__serial__icontains=q)
            | Q(device__model__icontains=q)
            | Q(device__customer__name__icontains=q)
        )
        qs = qs.filter(search)

    if status:
        qs = qs.filter(status=status)

    if assignee:
        qs = qs.filter(assigned_to_id=assignee)

    if from_dt:
        qs = qs.filter(checkin_at__gte=from_dt)

    if to_dt:
        qs = qs.filter(checkin_at__lt=to_dt)

    counts = {code: 0 for code, _ in ServiceOrder.Status.choices}
    for entry in qs.values("status").annotate(total=Count("id")):
        counts[entry["status"]] = entry["total"]

    status_colors = {
        ServiceOrder.Status.NEW: "#3B82F6",          # Recibido
        ServiceOrder.Status.IN_REVIEW: "#FACC15",    # En revision
        ServiceOrder.Status.WAITING_PARTS: "#FB923C",# En espera de repuestos
        ServiceOrder.Status.REQUIRES_AUTH: "#EF4444",# Requiere autorizacion
        ServiceOrder.Status.READY_PICKUP: "#22C55E", # Listo para recoger
        ServiceOrder.Status.DELIVERED: "#9CA3AF",    # Entregado
    }

    status_cards = [
        {
            "code": code,
            "label": label,
            "count": counts.get(code, 0),
            "color": status_colors.get(code, "#e2e8f0"),
        }
        for code, label in ServiceOrder.Status.choices
    ]

    total_count = qs.count()

    today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_count = qs.filter(checkin_at__gte=today_start).count()

    if not dfrom and not dto:
        seven_start = today_start - timedelta(days=6)
        thirty_start = today_start - timedelta(days=29)
        last7_count = qs.filter(checkin_at__gte=seven_start).count()
        last30_count = qs.filter(checkin_at__gte=thirty_start).count()
    else:
        last7_count = 0
        last30_count = 0

    durations = qs.filter(
        status=ServiceOrder.Status.DELIVERED,
        checkout_at__isnull=False,
        checkin_at__isnull=False,
    ).values_list("checkin_at", "checkout_at")

    total_days = 0.0
    done_count = 0
    for checkin_at, checkout_at in durations:
        delta = checkout_at - checkin_at
        total_days += max(delta.total_seconds(), 0) / 86400
        done_count += 1

    avg_days = round(total_days / done_count, 2) if done_count else 0.0

    recent_orders = qs[:10]

    if from_date or to_date:
        chart_start_date = from_date or (to_date - timedelta(days=29))
        chart_end_date = to_date or min(local_now.date(), chart_start_date + timedelta(days=29))
    else:
        chart_end_date = local_now.date()
        chart_start_date = chart_end_date - timedelta(days=13)

    if chart_end_date < chart_start_date:
        chart_start_date = chart_end_date

    if (chart_end_date - chart_start_date).days > 29:
        chart_start_date = chart_end_date - timedelta(days=29)

    chart_start_dt = make_aware(datetime.combine(chart_start_date, time.min))
    chart_end_dt = make_aware(datetime.combine(chart_end_date + timedelta(days=1), time.min))

    chart_data = (
        qs.filter(checkin_at__gte=chart_start_dt, checkin_at__lt=chart_end_dt)
        .annotate(day=TruncDate("checkin_at"))
        .values("day")
        .annotate(total=Count("id"))
        .order_by("day")
    )

    counts_by_day = {entry["day"]: entry["total"] for entry in chart_data}

    labels = []
    values = []
    current_date = chart_start_date
    while current_date <= chart_end_date:
        labels.append(current_date.strftime("%Y-%m-%d"))
        values.append(counts_by_day.get(current_date, 0))
        current_date += timedelta(days=1)

    chart_max = max(values) if values else 0
    step = 24
    bar_width = 16
    usable_height = 90
    chart_points = []
    for idx, (label, value) in enumerate(zip(labels, values)):
        height = 0.0
        if chart_max:
            height = round((value / chart_max) * usable_height, 2)
        y = round(100 - height, 2)
        chart_points.append(
            {
                "label": label,
                "value": value,
                "x": idx * step,
                "width": bar_width,
                "height": height,
                "y": y,
            }
        )
    chart_width = max(len(chart_points) * step, step)

    staff_users = User.objects.filter(is_staff=True).order_by("username")

    preserve_pairs = []
    if q:
        preserve_pairs.append(("q", q))
    if assignee:
        preserve_pairs.append(("assignee", assignee))
    if dfrom:
        preserve_pairs.append(("from", dfrom))
    if dto:
        preserve_pairs.append(("to", dto))
    status_preserve_query = ""
    if preserve_pairs:
        status_preserve_query = "&" + urlencode(preserve_pairs)

    context = {
        "counts": counts,
        "status_cards": status_cards,
        "total_count": total_count,
        "today_count": today_count,
        "last7_count": last7_count,
        "last30_count": last30_count,
        "avg_days": avg_days,
        "recent_orders": recent_orders,
        "labels": labels,
        "values": values,
        "chart_max": chart_max,
        "chart_points": chart_points,
        "chart_width": chart_width,
        "status": status,
        "q": q,
        "assignee": assignee,
        "dfrom": dfrom,
        "dto": dto,
        "status_choices": ServiceOrder.Status.choices,
        "staff_users": staff_users,
        "status_preserve_query": status_preserve_query,
    }
    context["is_manager"] = is_manager(request.user)
    return render(request, "dashboard.html", context)


@login_required(login_url="/admin/login/")
@group_required(ROLE_RECEPCION, ROLE_GERENCIA)
def inventory_list(request):
    q = (request.GET.get("q", "") or "").strip()
    low = request.GET.get("low") == "1"
    items = InventoryItem.objects.all().order_by("sku")
    if q:
        search = Q(sku__icontains=q) | Q(name__icontains=q) | Q(location__icontains=q)
        items = items.filter(search)
    if low:
        items = items.filter(qty__lt=models.F("min_qty"))
    context = {
        "items": items,
        "q": q,
        "low": low,
        "can_export": is_gerencia(request.user),
    }
    return render(request, "inventory_list.html", context)


@login_required(login_url="/admin/login/")
@group_required(ROLE_RECEPCION, ROLE_GERENCIA)
@require_POST
def receive_stock(request):
    sku = (request.POST.get("sku") or "").strip()
    qty_raw = (request.POST.get("qty") or "").strip()
    reason = (request.POST.get("reason") or "Entrada").strip()
    movement_type = (request.POST.get("movement_type") or "ENTRY").strip().upper()
    item = InventoryItem.objects.filter(sku=sku).first()
    if not item:
        messages.error(request, f"SKU {sku} no existe.")
        return redirect("inventory_list")
    try:
        qty = int(qty_raw)
    except ValueError:
        messages.error(request, "Cantidad invalida.")
        return redirect("inventory_list")
    if qty <= 0:
        messages.error(request, "La cantidad debe ser mayor a 0.")
        return redirect("inventory_list")

    delta = qty if movement_type != "OUT" else -qty
    if movement_type == "OUT" and item.qty + delta < 0:
        messages.error(request, "No hay suficiente inventario para registrar esta salida.")
        return redirect("inventory_list")

    InventoryMovement.objects.create(item=item, delta=delta, reason=reason, author=request.user)
    item.qty = item.qty + delta
    item.save()

    if movement_type == "OUT":
        messages.success(request, f"Salida registrada: -{qty} de {item.name} (SKU {item.sku}).")
    else:
        messages.success(request, f"Entrada registrada: +{qty} de {item.name} (SKU {item.sku}).")
    return redirect("inventory_list")


@login_required(login_url="/admin/login/")
@group_required(ROLE_RECEPCION, ROLE_GERENCIA)
def inventory_create(request):
    if request.method == "POST":
        form = InventoryItemForm(request.POST)
        if form.is_valid():
            item = form.save()
            messages.success(request, f"SKU {item.sku} creado.")
            return redirect("inventory_list")
    else:
        form = InventoryItemForm()
    context = {
        "form": form,
        "title": "Nuevo item de inventario",
        "item": None,
        "is_edit": False,
    }
    return render(request, "inventory_form.html", context)


@login_required(login_url="/admin/login/")
@group_required(ROLE_RECEPCION, ROLE_GERENCIA)
def inventory_update(request, pk):
    item = get_object_or_404(InventoryItem, pk=pk)
    if request.method == "POST":
        form = InventoryItemForm(request.POST, instance=item)
        if form.is_valid():
            form.save()
            messages.success(request, f"SKU {item.sku} actualizado.")
            return redirect("inventory_list")
    else:
        form = InventoryItemForm(instance=item)
    context = {
        "form": form,
        "title": f"Editar {item.sku}",
        "item": item,
        "is_edit": True,
    }
    return render(request, "inventory_form.html", context)


@login_required(login_url="/admin/login/")
@require_manager
def inventory_delete(request, pk):
    item = get_object_or_404(InventoryItem, pk=pk)
    has_movements = InventoryMovement.objects.filter(item=item).exists()
    if request.method == "POST":
        if has_movements:
            messages.error(
                request,
                "No puedes eliminar un item con movimientos registrados.",
            )
            return redirect("inventory_update", pk=item.pk)
        sku = item.sku
        item.delete()
        messages.success(request, f"SKU {sku} eliminado.")
        return redirect("inventory_list")
    context = {
        "item": item,
        "has_movements": has_movements,
    }
    return render(request, "inventory_confirm_delete.html", context)


@login_required(login_url="/admin/login/")
@group_required(ROLE_RECEPCION, ROLE_GERENCIA)
def customer_list(request):
    q = (request.GET.get("q", "") or "").strip()
    qs = Customer.objects.annotate(order_count=Count("orders", distinct=True)).order_by("name")
    if q:
        qs = qs.filter(
            Q(name__icontains=q)
            | Q(phone__icontains=q)
            | Q(alt_phone__icontains=q)
            | Q(email__icontains=q)
        )
    paginator = Paginator(qs, 20)
    page_number = request.GET.get("page") or 1
    page_obj = paginator.get_page(page_number)
    return render(
        request,
        "panel/customers_list.html",
        {"page_obj": page_obj, "q": q},
    )


@login_required(login_url="/admin/login/")
@group_required(ROLE_RECEPCION, ROLE_GERENCIA)
def customer_edit(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    next_url = (request.GET.get("next") or "").strip()
    if request.method == "POST":
        form = CustomerForm(request.POST, instance=customer)
        if form.is_valid():
            form.save()
            messages.success(request, "Datos del cliente actualizados.")
            if next_url:
                return redirect(next_url)
            return redirect("customer_list")
    else:
        form = CustomerForm(instance=customer)
    return render(
        request,
        "panel/customer_edit.html",
        {"form": form, "customer": customer, "next": next_url},
    )


@login_required(login_url="/admin/login/")
@group_required(ROLE_RECEPCION, ROLE_GERENCIA)
def list_orders(request):
    q = (request.GET.get("q", "") or "").strip()
    status = (request.GET.get("status", "") or "").strip()
    dfrom = (request.GET.get("from", "") or "").strip()
    dto   = (request.GET.get("to", "") or "").strip()
    export = request.GET.get("export") == "1"
    assignee = (request.GET.get("assignee", "") or "").strip()
    has_user_filters = any(
        key in request.GET for key in ("status", "q", "from", "to", "assignee", "export")
    )
    if not has_user_filters:
        status = ServiceOrder.Status.NEW

    is_superuser = request.user.is_superuser
    is_technician = is_tecnico(request.user)
    is_reception = is_recepcion(request.user)

    can_create = is_reception
    can_assign = is_reception
    can_send_estimate = is_reception
    can_export = is_gerencia(request.user)

    qs = (
        ServiceOrder.objects.select_related("customer")
        .prefetch_related("devices")
        .order_by("-checkin_at")
    )
    if is_technician:
        qs = qs.filter(assigned_to=request.user)

    if q:
        qs = qs.filter(
            Q(folio__icontains=q)
            | Q(devices__serial__icontains=q)
            | Q(devices__model__icontains=q)
            | Q(customer__name__icontains=q)
        ).distinct()
    if status:
        qs = qs.filter(status=status)
    if assignee:
        if is_technician:
            if str(request.user.pk) == assignee:
                qs = qs.filter(assigned_to=request.user)
        else:
            qs = qs.filter(assigned_to_id=assignee)

    tz = timezone.get_current_timezone()

    def _make_boundary(value, *, end=False):
        if not value:
            return None
        try:
            date_value = datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return None
        base = datetime.combine(date_value, time.min)
        if end:
            base += timedelta(days=1)
        if settings.USE_TZ:
            return timezone.make_aware(base, tz)
        return base

    start_dt = _make_boundary(dfrom)
    if start_dt:
        qs = qs.filter(checkin_at__gte=start_dt)

    end_dt = _make_boundary(dto, end=True)
    if end_dt:
        qs = qs.filter(checkin_at__lt=end_dt)

    base_params = [
        ("q", q),
        ("status", status),
        ("assignee", assignee),
        ("from", dfrom),
        ("to", dto),
    ]
    preserved = [(key, value) for key, value in base_params if value]
    export_query = urlencode(preserved + [("export", "1")]) if preserved else "export=1"
    export_csv_url = f"{request.path}?{export_query}"

    if export and not can_export:
        return render(
            request,
            "403.html",
            {"required_groups": [ROLE_GERENCIA]},
            status=403,
        )

    if export:
        resp = HttpResponse(content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = 'attachment; filename="ordenes.csv"'
        writer = csv.writer(resp)
        writer.writerow(
            ["Folio", "Cliente", "Equipo", "Estado", "Tecnico", "Total", "FechaEntrada", "FechaSalida"]
        )
        for order in qs:
            customer = order.get_customer()
            tech_name = order.assigned_to.get_username() if order.assigned_to_id else ""
            equipment = build_device_label(order)
            writer.writerow(
                [
                    order.folio,
                    getattr(customer, "name", "") if customer else "",
                    equipment,
                    order.get_status_display(),
                    tech_name,
                    format(order.approved_total, ".2f"),
                    format_csv_datetime(order.checkin_at),
                    format_csv_datetime(order.checkout_at),
                ]
            )
        return resp

    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    restricted_statuses = {"REV", "WAI", "READY"}
    status_colors = {
        ServiceOrder.Status.NEW: "#3B82F6",
        ServiceOrder.Status.IN_REVIEW: "#FACC15",
        ServiceOrder.Status.WAITING_PARTS: "#FB923C",
        ServiceOrder.Status.REQUIRES_AUTH: "#EF4444",
        ServiceOrder.Status.READY_PICKUP: "#22C55E",
        ServiceOrder.Status.DELIVERED: "#9CA3AF",
        ServiceOrder.Status.CANCELLED: "#9ca3af",
    }
    for o in page_obj.object_list:
        allowed_next = o.allowed_next_statuses()
        if is_technician:
            allowed_next = [code for code in allowed_next if code in restricted_statuses]
        o.allowed_next = allowed_next
        public_url = request.build_absolute_uri(reverse("public_status", args=[o.token]))
        customer = o.get_customer()
        customer_name = getattr(customer, "name", "") if customer else ""
        friendly_name = customer_name or "cliente"
        o.customer_obj = customer
        o.device_list = _order_devices(o)
        o.device_summary = build_device_label(o)
        if o.status == "READY":
            base = f"Hola {friendly_name}, tu orden {o.folio} est\u00e1 LISTA para recoger."
        elif o.status == "DONE":
            base = f"Hola {friendly_name}, tu orden {o.folio} fue ENTREGADA."
        else:
            base = f"Hola {friendly_name}, tu orden {o.folio} est\u00e1 {o.get_status_display()}."
        msg = f"{base} Detalle: {public_url}"
        phone_value = getattr(customer, "phone", "") if customer else ""
        o.whatsapp_link = build_whatsapp_link(phone_value, msg)
        o.status_color = status_colors.get(o.status, "#e2e8f0")

    context = {
        "page_obj": page_obj,
        "query": q,
        "status": status,
        "dfrom": dfrom,
        "dto": dto,
        "status_choices": ServiceOrder.Status.choices,
        "assignee": assignee,
        "staff_users": User.objects.filter(is_staff=True).order_by("username"),
        "can_create": can_create,
        "can_assign": can_assign,
        "can_send_estimate": can_send_estimate,
        "can_export": can_export,
        "is_technician": is_technician,
        "is_superuser": is_superuser,
        "export_csv_url": export_csv_url,
        "status_colors": status_colors,
    }
    context["is_manager"] = is_manager(request.user)
    return render(request, "reception_orders.html", context)

@login_required(login_url="/admin/login/")
@group_required(ROLE_RECEPCION, ROLE_GERENCIA)
def order_detail(request, pk):
    order = get_object_or_404(
        ServiceOrder.objects.select_related("customer").prefetch_related("devices"),
        pk=pk,
    )
    history = order.history.order_by("-created_at")
    parts = InventoryMovement.objects.filter(order=order).select_related("item").order_by("-created_at")
    allowed_next = order.allowed_next_statuses()

    is_superuser = request.user.is_superuser
    is_technician = is_tecnico(request.user)
    is_reception = is_recepcion(request.user)
    is_manager_user = is_gerencia(request.user) or is_superuser

    if is_technician:
        allowed_next = [code for code in allowed_next if code in {"REV", "WAI", "READY"}]

    status_colors = {
        ServiceOrder.Status.NEW: "#3B82F6",
        ServiceOrder.Status.IN_REVIEW: "#FACC15",
        ServiceOrder.Status.WAITING_PARTS: "#FB923C",
        ServiceOrder.Status.REQUIRES_AUTH: "#EF4444",
        ServiceOrder.Status.READY_PICKUP: "#22C55E",
        ServiceOrder.Status.DELIVERED: "#9CA3AF",
    }
    public_url = request.build_absolute_uri(reverse("public_status", args=[order.token]))
    customer = order.get_customer()
    customer_name = getattr(customer, "name", "") if customer else ""
    friendly_name = customer_name or "cliente"
    if order.status == "READY":
        base = f"Hola {friendly_name}, tu orden {order.folio} est\u00e1 LISTA para recoger."
    elif order.status == "DONE":
        base = f"Hola {friendly_name}, tu orden {order.folio} fue ENTREGADA."
    else:
        base = f"Hola {friendly_name}, tu orden {order.folio} est\u00e1 {order.get_status_display()}."
    msg = f"{base} Detalle: {public_url}"
    wa_link = build_whatsapp_link(getattr(customer, "phone", "") if customer else "", msg)
    order.status_color = status_colors.get(order.status, "#94a3b8")

    try:
        estimate = order.estimate
    except Estimate.DoesNotExist:
        estimate = None

    estimate_has_items = estimate.items.exists() if estimate else False
    estimate_public_url = None
    if estimate and estimate_has_items:
        estimate_public_url = request.build_absolute_uri(reverse("estimate_public", args=[estimate.token]))

    can_create = is_reception
    can_assign = is_reception
    can_send_estimate = is_reception

    payments = order.payments.select_related("author", "device")
    approved_total = order.approved_total
    paid_total = order.paid_total
    balance = order.balance
    can_charge = is_reception and balance > Decimal("0.00")
    order_devices = _order_devices(order)

    return render(
        request,
        "reception_order_detail.html",
        {
            "order": order,
            "history": history,
            "parts": parts,
            "allowed_next": allowed_next,
            "public_url": public_url,
            "wa_link": wa_link,
            "status_choices": ServiceOrder.Status.choices,
            "staff_users": User.objects.filter(is_staff=True).order_by("username"),
            "estimate": estimate,
            "estimate_status": _estimate_status_label(estimate),
            "estimate_has_items": estimate_has_items,
            "estimate_public_url": estimate_public_url,
            "can_create": can_create,
            "can_assign": can_assign,
            "can_send_estimate": can_send_estimate,
            "payments": payments,
            "approved_total": approved_total,
            "paid_total": paid_total,
            "balance": balance,
            "can_charge": can_charge,
            "is_technician": is_technician,
            "is_superuser": is_superuser,
            "is_manager_user": is_manager_user,
            "order_customer": customer,
            "order_devices": order_devices,
            "status_colors": status_colors,
        },
    )


@login_required(login_url="/admin/login/")
@group_required(ROLE_RECEPCION, ROLE_GERENCIA)
@require_POST
def order_open_warranty(request, pk):
    parent = get_object_or_404(ServiceOrder.objects.prefetch_related("devices"), pk=pk)
    if parent.status != ServiceOrder.Status.DELIVERED:
        messages.error(request, "Solo puedes abrir garantia desde una orden entregada.")
        return redirect("order_detail", pk=parent.pk)

    devices = _order_devices(parent)
    primary_device = devices[0] if devices else parent.device
    note_text = f"Servicio por garantia de {parent.folio}"
    with transaction.atomic():
        new_order = ServiceOrder.objects.create(
            customer=parent.get_customer(),
            device=primary_device,
            notes=note_text,
            warranty_parent=parent,
        )
        if devices:
            new_order.devices.set(devices)
    messages.success(request, f"Orden de garantia creada: {new_order.folio}")
    return redirect("order_detail", pk=new_order.pk)


@login_required(login_url="/admin/login/")
@require_manager
@require_POST
def order_cancel(request, pk):
    order = get_object_or_404(ServiceOrder, pk=pk)
    try:
        order.transition_to(ServiceOrder.Status.CANCELLED, author=request.user, author_role=resolve_actor_role(request.user), force=True)
        messages.success(request, "Orden cancelada.")
    except Exception as exc:
        messages.error(request, f"No se pudo cancelar la orden: {exc}")
    return redirect("order_detail", pk=pk)

@login_required(login_url="/admin/login/")
@group_required(ROLE_RECEPCION, ROLE_GERENCIA)
@require_POST
def add_payment(request, pk):
    order = get_object_or_404(
        ServiceOrder.objects.select_related("customer").prefetch_related("devices"),
        pk=pk,
    )

    raw_amount = (request.POST.get("amount") or "").strip()
    try:
        amount = Decimal(raw_amount)
    except (InvalidOperation, TypeError):
        messages.error(request, "Monto invalido.")
        return redirect("order_detail", pk=pk)

    if amount <= Decimal("0.00"):
        messages.error(request, "El monto debe ser mayor a cero.")
        return redirect("order_detail", pk=pk)

    method = (request.POST.get("method") or "").strip()
    reference = (request.POST.get("reference") or "").strip()

    if not method:
        messages.error(request, "Debes indicar el metodo de pago.")
        return redirect("order_detail", pk=pk)

    if len(method) > 30:
        messages.error(request, "El metodo debe tener maximo 30 caracteres.")
        return redirect("order_detail", pk=pk)

    if len(reference) > 80:
        messages.error(request, "La referencia debe tener maximo 80 caracteres.")
        return redirect("order_detail", pk=pk)

    device_id = (request.POST.get("device_id") or "").strip()
    order_devices = _order_devices(order)
    payment_device = None
    if device_id:
        payment_device = next((dev for dev in order_devices if str(dev.pk) == device_id), None)
        if not payment_device:
            messages.error(request, "Selecciona un dispositivo valido.")
            return redirect("order_detail", pk=pk)

    balance = order.balance
    if balance <= Decimal("0.00"):
        messages.error(request, "La orden no tiene saldo por cobrar.")
        return redirect("order_detail", pk=pk)

    if amount > balance:
        messages.error(request, "El monto excede el saldo por cobrar.")
        return redirect("order_detail", pk=pk)

    amount_display = ""
    balance_display = ""
    new_balance = None
    payment = None
    try:
        with transaction.atomic():
            payment = Payment.objects.create(
                order=order,
                device=payment_device,
                amount=amount,
                method=method,
                reference=reference,
                author=request.user,
            )

        amount_display = format(amount, ".2f")
        new_balance = order.balance
        balance_display = format(new_balance, ".2f")

        Status = ServiceOrder.Status
        done_value = Status.DELIVERED
        try:
            if new_balance == Decimal("0.00") and order.can_transition_to(done_value):
                actor_role = resolve_actor_role(request.user)
                order.transition_to(done_value, author=request.user, author_role=actor_role)
                _record_status_update(
                    order,
                    channel="status_auto_close",
                    extra_payload={"trigger": "payment_zero"},
                )
        except Exception:
            logger.exception("No se pudo cerrar la orden tras dejar saldo en cero")

        device_label = build_device_label(order)
        payment_device_label = build_single_device_label(payment_device) if payment_device else ""
        folio = _resolve_order_folio(order)
        customer = order.get_customer()
        customer_name = getattr(customer, "name", "") if customer else ""
        customer_email = (getattr(customer, "email", "") or "").strip() if customer else ""

        try:
            Notification.objects.create(
                order=order,
                kind="payment",
                channel="system",
                ok=True,
                title=f"Pago registrado {folio}",
                payload={
                    "order_id": order.id,
                    "order_folio": folio,
                    "order": folio,
                    "customer": customer_name,
                    "amount": amount_display,
                    "method": method,
                    "reference": reference,
                    "new_balance": balance_display,
                    "device": device_label,
                    "payment_device": payment_device_label or device_label,
                    "payment_id": payment.id if payment else None,
                },
            )
        except Exception:
            logger.exception("Error registrando notificacion de pago")

        sender_email = getattr(settings, "DEFAULT_FROM_EMAIL", "")
        if customer_email and sender_email:
            try:
                subject = f"Pago registrado {folio}"
                body_lines = [
                    f"Hola {customer_name or 'cliente'},",
                    "",
                    f"Registramos tu pago por ${amount_display} via {method}.",
                ]
                if reference:
                    body_lines.append(f"Referencia: {reference}")
                if payment_device_label:
                    body_lines.append(f"Dispositivo: {payment_device_label}.")
                body_lines.append(f"Saldo restante: ${balance_display}.")
                body_lines.append("")
                body_lines.append("Gracias por tu preferencia.")
                body = "\n".join(body_lines)
                send_mail(
                    subject=subject,
                    message=body,
                    from_email=sender_email,
                    recipient_list=[customer_email],
                    fail_silently=True,
                )
            except Exception:
                logger.exception("Error enviando correo de pago")
        elif not customer_email:
            messages.info(request, "Pago registrado, pero el cliente no tiene correo para notificar.")
    except Exception as exc:
        logger.exception("Error registrando pago")
        messages.error(request, f"No se pudo registrar el pago: {exc}")
        return redirect("order_detail", pk=pk)

    messages.success(request, "Pago registrado correctamente.")

    if new_balance is not None and new_balance > Decimal("0.00"):
        messages.info(request, f"Saldo pendiente: ${balance_display}.")

    return redirect("order_detail", pk=pk)


@login_required(login_url="/admin/login/")
@group_required(ROLE_RECEPCION, ROLE_GERENCIA)
@require_POST
def change_status_auth(request, pk):
    order = get_object_or_404(
        ServiceOrder.objects.select_related("customer").prefetch_related("devices"),
        pk=pk,
    )

    target_status = ServiceOrder.Status.REQUIRES_AUTH
    if not order.can_transition_to(target_status):
        messages.error(request, "Transicion de estado invalida.")
        return redirect("order_detail", pk=order.pk)

    if is_tecnico(request.user):
        messages.error(request, "No tienes permiso para solicitar autorizacion.")
        return redirect("order_detail", pk=order.pk)

    device_label = build_device_label(order)
    customer = order.get_customer()
    customer_name = getattr(customer, "name", "") if customer else ""
    order_folio = _resolve_order_folio(order)

    estimate, _ = Estimate.objects.get_or_create(order=order)

    actor_role = resolve_actor_role(request.user)
    try:
        order.transition_to(target_status, author=request.user, author_role=actor_role)
    except ValueError:
        messages.error(request, "Transicion de estado invalida.")
        return redirect("order_detail", pk=order.pk)

    _record_status_update(
        order,
        channel="status_requires_authorization",
        extra_payload={
            "target": ServiceOrder.Status.REQUIRES_AUTH,
            "changed_by": getattr(request.user, "username", ""),
        },
    )

    public_status_url = request.build_absolute_uri(reverse("public_status", args=[order.token]))
    public_estimate_url = request.build_absolute_uri(reverse("estimate_public", args=[estimate.token]))
    has_items = estimate.items.exists()
    notification = Notification.objects.create(
        order=order,
        kind="email",
        channel="status_auth",
        payload={
            "order_folio": order_folio,
            "order": order_folio,
            "customer": customer_name,
            "device": device_label,
        },
    )

    send_ok, error_code = send_order_status_email(
        order=order,
        notification=notification,
        status_code=ServiceOrder.Status.REQUIRES_AUTH,
        public_url=public_status_url,
        device_label=device_label,
        extra_context={
            "estimate_url": public_estimate_url,
            "has_items": has_items,
        },
    )

    if send_ok:
        messages.success(request, "Orden marcada como Requiere autorizacion y se notifico al cliente.")
    else:
        if error_code == "missing_email":
            messages.warning(request, "Orden marcada como Requiere autorizacion. Agrega correo para notificar al cliente.")
        else:
            messages.warning(
                request,
                "Orden marcada como Requiere autorizacion, pero ocurrio un error al enviar el correo.",
            )

    return redirect("order_detail", pk=order.pk)


@login_required(login_url="/admin/login/")
@group_required(ROLE_TECNICO, ROLE_GERENCIA)
@require_POST
def change_status(request, pk):
    order = get_object_or_404(
        ServiceOrder.objects.select_related("customer").prefetch_related("devices"),
        pk=pk,
    )
    target = request.POST.get("target")
    device_label = build_device_label(order)
    customer = order.get_customer()
    customer_name = getattr(customer, "name", "") if customer else ""
    order_folio = _resolve_order_folio(order)

    if not order.can_transition_to(target):
        messages.error(request, "Transicion de estado invalida.")
        return redirect("order_detail", pk=order.pk)

    if target == ServiceOrder.Status.REQUIRES_AUTH:
        messages.error(request, "Usa el flujo de autorizacion dedicado.")
        return redirect("order_detail", pk=order.pk)

    if is_tecnico(request.user):
        if order.assigned_to_id != request.user.id:
            messages.error(request, "Solo puedes actualizar tus ordenes asignadas.")
            return redirect("order_detail", pk=order.pk)
        if target not in {"REV", "WAI", "READY"}:
            messages.error(request, "Los tecnicos solo pueden cambiar a REV, WAI o READY.")
            return redirect("order_detail", pk=order.pk)

    if target == "DONE" and not request.user.is_superuser:
        messages.error(request, "Solo un superusuario puede marcar como Entregado.")
        return redirect("order_detail", pk=order.pk)

    if target == "DONE" and hasattr(order, "balance"):
        balance_value = order.balance
        if balance_value is not None and balance_value > Decimal("0.00"):
            messages.error(
                request,
                f"No puedes entregar con saldo pendiente (${balance_value:.2f}).",
            )
            return redirect("order_detail", pk=order.pk)

    if target == ServiceOrder.Status.READY_PICKUP:
        applied_ok, apply_error = apply_estimate_inventory(order, author=request.user)
        if not applied_ok:
            messages.error(
                request,
                apply_error or "No se pudo aplicar el inventario de la cotizacion.",
            )
            return redirect("order_detail", pk=order.pk)

    actor_role = resolve_actor_role(request.user)
    try:
        order.transition_to(target, author=request.user, author_role=actor_role)
    except ValueError:
        messages.error(request, "Transicion de estado invalida.")
        return redirect("order_detail", pk=order.pk)
    _record_status_update(
        order,
        channel="status_change",
        extra_payload={
            "target": target,
            "changed_by": getattr(request.user, "username", ""),
        },
    )

    if target == ServiceOrder.Status.READY_PICKUP:
        balance_value = getattr(order, "balance", None)
        if balance_value is not None and balance_value > Decimal("0.00"):
            messages.warning(
                request,
                f"Orden lista para recoger con saldo pendiente (${balance_value:.2f}). Cobra al entregar.",
            )

    if target in (ServiceOrder.Status.READY_PICKUP, ServiceOrder.Status.DELIVERED):
        public_url = request.build_absolute_uri(reverse("public_status", args=[order.token]))
        notification = Notification.objects.create(
            order=order,
            kind="email",
            channel="status_ready" if target == ServiceOrder.Status.READY_PICKUP else "status_done",
            payload={
                "order_folio": order_folio,
                "order": order_folio,
                "customer": customer_name,
                "device": device_label,
            },
        )
        send_ok, error_code = send_order_status_email(
            order=order,
            notification=notification,
            status_code=target,
            public_url=public_url,
            device_label=device_label,
        )
        if not send_ok:
            if error_code == "missing_email":
                messages.info(request, "No se envi\u00f3 correo porque el cliente no tiene email registrado.")
            else:
                  messages.warning(request, "Se actualizo el estado, pero fallo el envio de correo al cliente.")

    messages.success(request, f"Estado actualizado a {order.get_status_display()}.")
    return redirect("order_detail", pk=order.pk)


@login_required(login_url="/admin/login/")
@group_required(ROLE_RECEPCION, ROLE_GERENCIA)
def payment_receipt_pdf(request, payment_id):
    payment = get_object_or_404(
        Payment.objects.select_related("order__customer", "author", "device").prefetch_related("order__devices"),
        pk=payment_id,
    )
    order = payment.order
    customer = order.get_customer()
    company_name = getattr(settings, "DEFAULT_FROM_EMAIL", "") or "Taller"
    public_url = request.build_absolute_uri(reverse("public_status", args=[order.token]))
    order_devices = _order_devices(order)

    context = {
        "payment": payment,
        "order": order,
        "customer": customer,
        "company_name": company_name,
        "public_url": public_url,
        "order_devices": order_devices,
        "logo_b64": _get_pdf_logo(),
    }
    html = render_to_string("payment_receipt.html", context)

    pdf_io = BytesIO()
    try:
        result = pisa.CreatePDF(html, dest=pdf_io, encoding="utf-8")
    except Exception:
        return HttpResponse(html, content_type="text/html", status=500)
    if result.err:
        return HttpResponse(html, content_type="text/html", status=500)

    response = HttpResponse(pdf_io.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="recibo-pago-{order.folio}-{payment.id}.pdf"'
    return response


@login_required(login_url="/admin/login/")
@group_required(ROLE_RECEPCION, ROLE_GERENCIA, ROLE_TECNICO)
@require_POST
def assign_tech(request, pk):
    order = get_object_or_404(ServiceOrder, pk=pk)
    uid = (request.POST.get("user_id") or "").strip()

    is_technician = is_tecnico(request.user)
    if is_technician and order.assigned_to_id != request.user.id:
        messages.error(request, "Solo puedes gestionar ordenes asignadas a ti.")
        return redirect("order_detail", pk=order.pk)
    if is_technician and uid and uid != str(request.user.id):
        messages.error(request, "No puedes reasignar la orden a otro usuario.")
        return redirect("order_detail", pk=order.pk)

    user = User.objects.filter(pk=uid, is_staff=True).first()
    if not user:
        messages.error(request, "Selecciona un tecnico valido.")
        return redirect("order_detail", pk=order.pk)
    order.assigned_to = user
    order.save()
    if user.email:
        try:
            public_url = request.build_absolute_uri(reverse("order_detail", args=[order.pk]))
            send_mail(
                subject=f"Se te asigno la orden {order.folio}",
                message=f"Revisa la orden {order.folio}: {public_url}",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                fail_silently=True,
            )
        except Exception:
            pass
    messages.success(request, f"Asignado a {user.get_username()}.")
    return redirect("order_detail", pk=order.pk)

@login_required(login_url="/admin/login/")
@group_required(ROLE_RECEPCION, ROLE_GERENCIA, ROLE_TECNICO)
@require_POST
def add_note(request, pk):
    order = get_object_or_404(ServiceOrder, pk=pk)

    if is_tecnico(request.user):
        if order.assigned_to_id != request.user.id:
            messages.error(request, "Solo puedes agregar notas a tus ordenes asignadas.")
            return redirect("order_detail", pk=order.pk)

    text_value = (request.POST.get("note") or "").strip()
    if not text_value:
        messages.error(request, "Escribe una nota.")
        return redirect("order_detail", pk=order.pk)
    stamp = timezone.localtime().strftime("%Y-%m-%d %H:%M")
    author = request.user.get_username()
    order.notes = (order.notes or "") + ("" if not order.notes else "\n") + f"[{stamp}] {author}: {text_value}"
    order.save()
    messages.success(request, "Nota agregada.")
    return redirect("order_detail", pk=order.pk)

@login_required(login_url="/admin/login/")
@group_required(ROLE_RECEPCION, ROLE_GERENCIA, ROLE_TECNICO)
@require_POST
def add_part(request, pk):
    order = get_object_or_404(ServiceOrder, pk=pk)

    if is_tecnico(request.user):
        if order.assigned_to_id != request.user.id:
            messages.error(request, "Solo puedes usar partes en tus ordenes asignadas.")
            return redirect("list_orders")

    sku = (request.POST.get("sku") or "").strip()
    qty_raw = (request.POST.get("qty") or "").strip()
    reason = (request.POST.get("reason") or "").strip()

    if not sku:
        messages.error(request, "Captura el SKU.")
        return redirect("list_orders")
    try:
        qty = int(qty_raw)
    except ValueError:
        messages.error(request, "Cantidad invalida.")
        return redirect("list_orders")
    if qty <= 0:
        messages.error(request, "La cantidad debe ser mayor a 0.")
        return redirect("list_orders")

    item = InventoryItem.objects.filter(sku=sku).first()
    if not item:
        messages.error(request, f"SKU {sku} no existe.")
        return redirect("list_orders")
    if item.qty < qty:
        messages.error(request, f"Stock insuficiente ({item.qty} disponibles).")
        return redirect("list_orders")

    InventoryMovement.objects.create(
        item=item,
        delta=-qty,
        reason=reason or f"Consumo en orden {order.folio}",
        order=order,
        author=request.user,
    )
    item.qty = item.qty - qty
    item.save()

    if item.qty < item.min_qty:
        Notification.objects.create(
            order=order,
            kind="stock",
            channel="threshold",
            ok=True,
            payload={
                "sku": item.sku,
                "name": item.name,
                "qty": item.qty,
                "min": item.min_qty,
            },
        )

    messages.success(request, f"Se uso {qty} x {item.name} (SKU {item.sku}) en {order.folio}.")
    return redirect("list_orders")


@login_required(login_url="/admin/login/")
@group_required(ROLE_RECEPCION, ROLE_GERENCIA)
def customer_devices(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    if request.method == "POST":
        brand = (request.POST.get("brand") or "").strip()
        model_value = (request.POST.get("model") or "").strip()
        serial_value = (request.POST.get("serial") or "").strip()
        if not brand or not model_value:
            messages.error(request, "Marca y modelo son obligatorios.")
        else:
            Device.objects.create(customer=customer, brand=brand, model=model_value, serial=serial_value)
            messages.success(request, "Dispositivo agregado.")
            return redirect("customer_devices", pk=customer.pk)
    devices = (
        Device.objects.filter(customer=customer)
        .prefetch_related("orders")
        .annotate(order_count=Count("orders", distinct=True))
        .order_by("-id")
    )
    return render(
        request,
        "clientes/customer_devices.html",
        {
            "customer": customer,
            "devices": devices,
        },
    )


@login_required(login_url="/admin/login/")
@group_required(ROLE_RECEPCION, ROLE_GERENCIA)
def reception_home(request):
    counts = {code: 0 for code, _ in ServiceOrder.Status.choices}
    for entry in (
        ServiceOrder.objects.values("status")
        .annotate(total=Count("id"))
    ):
        counts[entry["status"]] = entry["total"]

    status_colors = {
        ServiceOrder.Status.NEW: "#3B82F6",          # Recibido
        ServiceOrder.Status.IN_REVIEW: "#FACC15",    # En revision
        ServiceOrder.Status.WAITING_PARTS: "#FB923C",# En espera de repuestos
        ServiceOrder.Status.REQUIRES_AUTH: "#EF4444",# Requiere autorizacion
        ServiceOrder.Status.READY_PICKUP: "#22C55E", # Listo para recoger
        ServiceOrder.Status.DELIVERED: "#9CA3AF",    # Entregado
    }

    status_cards = [
        {
            "code": code,
            "label": label,
            "count": counts.get(code, 0),
            "color": status_colors.get(code, "#e2e8f0"),
        }
        for code, label in ServiceOrder.Status.choices
    ]

    recent_orders = list(
        ServiceOrder.objects.select_related("customer")
        .prefetch_related("devices")
        .order_by("-checkin_at")[:10]
    )
    for o in recent_orders:
        o.device_summary = build_device_label(o)
        o.status_color = status_colors.get(o.status, "#e2e8f0")

    return render(
        request,
        "reception/home.html",
        {
            "can_export": is_gerencia(request.user),
            "status_cards": status_cards,
            "recent_orders": recent_orders,
        },
    )


@login_required(login_url="/admin/login/")
@group_required(ROLE_RECEPCION, ROLE_GERENCIA)
def reception_customer_search(request):
    query = (request.GET.get("q") or "").strip()
    if not query:
        return JsonResponse({"results": []})
    filters = (
        Q(name__icontains=query)
        | Q(email__icontains=query)
        | Q(phone__icontains=query)
        | Q(alt_phone__icontains=query)
    )
    customers = (
        Customer.objects.filter(filters)
        .order_by("name", "id")
        .only("id", "name", "email", "phone", "alt_phone")[:10]
    )
    results = []
    for customer in customers:
        phones = [p for p in (customer.phone, customer.alt_phone) if p]
        phone_label = " / ".join(phones) if phones else "Sin telefono"
        label_parts = [
            customer.name or "Sin nombre",
            phone_label,
            customer.email or "Sin correo",
        ]
        results.append(
            {
                "id": customer.pk,
                "name": customer.name,
                "phone": customer.phone,
                "alt_phone": customer.alt_phone,
                "email": customer.email,
                "label": " | ".join(label_parts),
            }
        )
    return JsonResponse({"results": results})


@login_required(login_url="/admin/login/")
@group_required(ROLE_RECEPCION, ROLE_GERENCIA)
def reception_new_order(request):
    prefill = {"name": "", "phone": "", "alt_phone": "", "email": "", "notes": "", "customer_id": ""}
    form = ReceptionForm()
    device_formset = ReceptionDeviceFormSet(prefix="devices", initial=[{}])
    if request.method == "POST":
        data = request.POST.copy()
        if "name" in data and "customer_name" not in data:
            data["customer_name"] = data.get("name", "")
            data["customer_phone"] = data.get("phone", "")
            data["customer_alt_phone"] = data.get("alt_phone", "")
            data["customer_email"] = data.get("email", "")
            data["notes"] = data.get("notes", "")
        if "devices-TOTAL_FORMS" not in data:
            legacy_brand = data.get("brand", "")
            legacy_model = data.get("model", "")
            legacy_serial = data.get("serial", "")
            legacy_notes = data.get("notes", "")
            legacy_password = data.get("password_notes", "")
            legacy_accessories = data.get("accessories_notes", "")
            data["devices-TOTAL_FORMS"] = "1"
            data["devices-INITIAL_FORMS"] = "0"
            data["devices-MIN_NUM_FORMS"] = "1"
            max_forms = getattr(ReceptionDeviceFormSet, "max_num", 20) or 20
            data["devices-MAX_NUM_FORMS"] = str(max_forms)
            data["devices-0-brand"] = legacy_brand
            data["devices-0-model"] = legacy_model
            data["devices-0-serial"] = legacy_serial
            data["devices-0-notes"] = legacy_notes
            data["devices-0-password_notes"] = legacy_password
            data["devices-0-accessories_notes"] = legacy_accessories
        form = ReceptionForm(data)
        device_formset = ReceptionDeviceFormSet(data, prefix="devices")
        customer_id_value = (data.get("customer_id") or "").strip()
        if form.is_valid() and device_formset.is_valid():
            cleaned = form.cleaned_data
            name = cleaned["customer_name"]
            phone = cleaned.get("customer_phone") or ""
            alt_phone = cleaned.get("customer_alt_phone") or ""
            email = cleaned.get("customer_email") or ""
            notes = cleaned.get("notes") or ""

            device_entries = []
            for device_form in device_formset:
                device_cleaned = getattr(device_form, "cleaned_data", None) or {}
                if device_cleaned.get("DELETE"):
                    continue
                brand = device_cleaned.get("brand", "").strip()
                model = device_cleaned.get("model", "").strip()
                if not brand and not model:
                    continue
                device_entries.append(
                    {
                        "brand": brand,
                        "model": model,
                        "serial": device_cleaned.get("serial", "").strip(),
                        "notes": device_cleaned.get("notes", "").strip(),
                        "password_notes": device_cleaned.get("password_notes", "").strip(),
                        "accessories_notes": device_cleaned.get("accessories_notes", "").strip(),
                    }
                )

            if not device_entries:
                messages.error(request, "Agrega al menos un dispositivo.")
                prefill = {
                    "name": data.get("name") or data.get("customer_name", ""),
                    "phone": data.get("phone") or data.get("customer_phone", ""),
                    "alt_phone": data.get("alt_phone") or data.get("customer_alt_phone", ""),
                    "email": data.get("email") or data.get("customer_email", ""),
                    "notes": data.get("notes") or "",
                    "customer_id": customer_id_value,
                }
                context = {"prefill": prefill, "form": form, "device_formset": device_formset}
                return render(request, "reception/new_order.html", context)

            selected_customer = None
            if customer_id_value:
                try:
                    selected_customer = Customer.objects.get(pk=int(customer_id_value))
                except (ValueError, Customer.DoesNotExist):
                    messages.error(request, "El cliente seleccionado ya no existe, vuelve a buscarlo.")
                    prefill = {
                        "name": data.get("name") or data.get("customer_name", ""),
                        "phone": data.get("phone") or data.get("customer_phone", ""),
                        "alt_phone": data.get("alt_phone") or data.get("customer_alt_phone", ""),
                        "email": data.get("email") or data.get("customer_email", ""),
                        "notes": data.get("notes") or "",
                        "customer_id": "",
                    }
                    context = {"prefill": prefill, "form": form, "device_formset": device_formset}
                    return render(request, "reception/new_order.html", context)

            with transaction.atomic():
                customer = selected_customer
                if customer is None:
                    customer = _create_customer(name, phone, email, alt_phone)
                else:
                    updates = []
                    if phone and not customer.phone:
                        customer.phone = phone
                        updates.append("phone")
                    if alt_phone and not customer.alt_phone:
                        customer.alt_phone = alt_phone
                        updates.append("alt_phone")
                    if email and not customer.email:
                        customer.email = email
                        updates.append("email")
                    if updates:
                        customer.save(update_fields=updates)

                created_devices = []
                for entry in device_entries:
                    created_devices.append(
                        _ensure_device(
                            customer,
                            brand=entry["brand"],
                            model=entry["model"],
                            serial=entry["serial"],
                            notes=entry["notes"],
                            password_notes=entry.get("password_notes", ""),
                            accessories_notes=entry.get("accessories_notes", ""),
                        )
                    )
                primary_device = created_devices[0]
                order = ServiceOrder.objects.create(
                    customer=customer,
                    device=primary_device,
                    notes=notes,
                    assigned_to=request.user if request.user.is_authenticated else None,
                )
                order.devices.set(created_devices)
                log_status_snapshot(
                    order,
                    author=request.user,
                    previous_status="",
                    new_status=order.status,
                )

            if email:
                public_url = request.build_absolute_uri(reverse("public_status", args=[order.token]))
                try:
                    send_mail(
                        subject=f"Orden {order.folio}",
                        message=f"Gracias. Consulta tu orden aqui: {public_url}",
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        recipient_list=[email],
                        fail_silently=False,
                    )
                    Notification.objects.create(
                        order=order,
                        kind="email",
                        channel="create",
                        ok=True,
                        payload={"to": email},
                    )
                except Exception as exc:
                    Notification.objects.create(
                        order=order,
                        kind="email",
                        channel="create",
                        ok=False,
                        payload={"to": email, "error": str(exc)},
                    )

            messages.success(request, f"Orden creada (Folio {getattr(order, 'folio', order.pk)}).")
            return redirect("order_detail", pk=order.pk)

        prefill = {
            "name": data.get("name") or data.get("customer_name", ""),
            "phone": data.get("phone") or data.get("customer_phone", ""),
            "alt_phone": data.get("alt_phone") or data.get("customer_alt_phone", ""),
            "email": data.get("email") or data.get("customer_email", ""),
            "notes": data.get("notes") or "",
            "customer_id": customer_id_value,
        }
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
        for error in device_formset.non_form_errors():
            messages.error(request, error)
        for form_errors in device_formset.errors:
            for field_errors in form_errors.values():
                if isinstance(field_errors, (list, tuple)):
                    for error in field_errors:
                        messages.error(request, error)
                else:
                    messages.error(request, field_errors)

    context = {"prefill": prefill, "form": form, "device_formset": device_formset}
    return render(request, "reception/new_order.html", context)
@login_required(login_url="/admin/login/")
@group_required(ROLE_RECEPCION, ROLE_GERENCIA)
def estimate_edit(request, pk):
    order = get_object_or_404(ServiceOrder, pk=pk)
    estimate, _ = Estimate.objects.get_or_create(order=order)

    if request.method == "POST" and request.POST.get("toggle_tax") == "1":
        estimate.apply_tax = not estimate.apply_tax
        subtotal = (estimate.subtotal or Decimal("0.00")).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        if estimate.apply_tax:
            tax = (subtotal * IVA_RATE).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        else:
            tax = Decimal("0.00")
        total = (subtotal + tax).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        estimate.tax = tax
        estimate.total = total
        estimate.save(update_fields=["apply_tax", "tax", "total", "updated_at"])
        if estimate.apply_tax:
            messages.info(request, "IVA activado para la cotizacion.")
        else:
            messages.info(request, "IVA desactivado para la cotizacion.")
        return redirect("estimate_edit", pk=order.pk)

    if request.method == "POST":
        descriptions = request.POST.getlist("description")
        qtys = request.POST.getlist("qty")
        unit_prices = request.POST.getlist("unit_price")
        skus = request.POST.getlist("inventory_sku")
        note = (request.POST.get("note") or "").strip()

        rows_for_context = []
        parsed_rows = []
        errors = []

        max_len = max(len(descriptions), len(qtys), len(unit_prices), len(skus))
        for idx in range(max_len):
            desc = (descriptions[idx] if idx < len(descriptions) else "").strip()
            qty_raw = (qtys[idx] if idx < len(qtys) else "").strip()
            price_raw = (unit_prices[idx] if idx < len(unit_prices) else "").strip()
            sku_raw = (skus[idx] if idx < len(skus) else "").strip()

            if not desc and not qty_raw and not price_raw and not sku_raw:
                continue

            rows_for_context.append(
                {
                    "description": desc,
                    "qty": qty_raw or "",
                    "unit_price": price_raw or "",
                    "inventory_sku": sku_raw,
                }
            )

            if not desc:
                errors.append(f"Fila {idx + 1}: descripcion requerida.")
                continue
            if not qty_raw:
                errors.append(f"Fila {idx + 1}: cantidad requerida.")
                continue
            try:
                qty = int(qty_raw)
            except ValueError:
                errors.append(f"Fila {idx + 1}: cantidad invalida.")
                continue
            if qty <= 0:
                errors.append(f"Fila {idx + 1}: la cantidad debe ser mayor a 0.")
                continue
            price_value = price_raw or "0"
            try:
                unit_price = Decimal(price_value)
            except (InvalidOperation, TypeError):
                errors.append(f"Fila {idx + 1}: precio invalido.")
                continue
            if unit_price < 0:
                errors.append(f"Fila {idx + 1}: el precio no puede ser negativo.")
                continue
            unit_price = unit_price.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
            inventory_item = None
            if sku_raw:
                inventory_item = InventoryItem.objects.filter(sku=sku_raw).first()
                if not inventory_item:
                    errors.append(f"Fila {idx + 1}: SKU {sku_raw} no existe.")
                    continue

            rows_for_context[-1]["qty"] = str(qty)
            rows_for_context[-1]["unit_price"] = f"{unit_price}"
            parsed_rows.append(
                {
                    "description": desc,
                    "qty": qty,
                    "unit_price": unit_price,
                    "inventory_item": inventory_item,
                }
            )

        if not rows_for_context:
            messages.error(request, "Agrega al menos una partida.")
            context = _build_estimate_form_context(
                request,
                order,
                estimate,
                form_items=[],
                note=note,
            )
            return render(request, "estimate_edit.html", context)

        if errors:
            for error in errors:
                messages.error(request, error)
            context = _build_estimate_form_context(
                request,
                order,
                estimate,
                form_items=rows_for_context,
                note=note,
            )
            return render(request, "estimate_edit.html", context)

        subtotal = Decimal("0.00")
        with transaction.atomic():
            estimate.items.all().delete()
            for row in parsed_rows:
                EstimateItem.objects.create(
                    estimate=estimate,
                    description=row["description"],
                    qty=row["qty"],
                    unit_price=row["unit_price"],
                    inventory_item=row["inventory_item"],
                )
                subtotal += row["unit_price"] * row["qty"]
            subtotal = subtotal.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
            if estimate.apply_tax:
                tax = (subtotal * IVA_RATE).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
            else:
                tax = Decimal("0.00")
            total = (subtotal + tax).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
            estimate.note = note
            estimate.subtotal = subtotal
            estimate.tax = tax
            estimate.total = total
            if estimate.approved_at or estimate.declined_at:
                estimate.approved_at = None
                estimate.declined_at = None
            estimate.save(update_fields=["note", "subtotal", "tax", "total", "approved_at", "declined_at"])

        messages.success(request, "Cotizacion actualizada.")
        return redirect("estimate_edit", pk=order.pk)

    context = _build_estimate_form_context(request, order, estimate)
    return render(request, "estimate_edit.html", context)


@login_required(login_url="/admin/login/")
@group_required(ROLE_RECEPCION, ROLE_GERENCIA)
@require_POST
def estimate_send(request, pk):
    order = get_object_or_404(
        ServiceOrder.objects.select_related("customer").prefetch_related("devices"),
        pk=pk,
    )
    estimate, _ = Estimate.objects.get_or_create(order=order)
    if not estimate.items.exists():
        messages.error(request, "Agrega partidas a la cotizacion antes de enviar.")
        return redirect("estimate_edit", pk=order.pk)

    customer = order.get_customer()
    email = (getattr(customer, "email", "") or "").strip() if customer else ""
    if not email:
        messages.error(request, "El cliente no tiene email registrado.")
        return redirect("estimate_edit", pk=order.pk)

    public_url = request.build_absolute_uri(reverse("estimate_public", args=[estimate.token]))
    subtotal = (estimate.subtotal or Decimal("0.00")).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    tax = (estimate.tax or Decimal("0.00")).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    total = (estimate.total or Decimal("0.00")).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    items = []
    for item in estimate.items.order_by("id"):
        raw_unit_price = item.unit_price or Decimal("0.00")
        unit_price = raw_unit_price.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        line_total = (raw_unit_price * item.qty).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        items.append(
            {"description": item.description, "qty": item.qty, "unit_price": unit_price, "line_total": line_total}
        )

    context = {
        "order": order,
        "estimate": estimate,
        "customer": customer,
        "items": items,
        "subtotal": subtotal,
        "tax": tax,
        "total": total,
        "public_url": public_url,
        "order_devices": _order_devices(order),
    }
    subject = f"Cotizacion orden {order.folio}"
    customer_name = getattr(customer, "name", "") if customer else "cliente"
    plain_message = _build_estimate_message(order, customer, public_url)
    html_message = render_to_string("estimate_email.html", context)

    send_count = send_mail(
        subject=subject,
        message=plain_message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
        html_message=html_message,
        fail_silently=True,
    )
    ok = send_count > 0
    Notification.objects.create(
        order=order,
        kind="email",
        channel="estimate_send",
        ok=ok,
        payload={
            "to": email,
            "estimate": str(estimate.token),
            "url": public_url,
            "order_folio": _resolve_order_folio(order),
            "order": _resolve_order_folio(order),
            "customer": customer_name,
        },
    )

    if ok:
        messages.success(request, "Cotizacion enviada al cliente.")
    else:
        messages.warning(request, "No se pudo enviar la cotizacion por correo.")
    return redirect("estimate_edit", pk=order.pk)


@login_required(login_url="/admin/login/")
@group_required(ROLE_RECEPCION, ROLE_GERENCIA)
@require_POST
def estimate_send_whatsapp(request, pk):
    order = get_object_or_404(
        ServiceOrder.objects.select_related("customer").prefetch_related("devices"),
        pk=pk,
    )
    estimate, _ = Estimate.objects.get_or_create(order=order)
    if not estimate.items.exists():
        messages.error(request, "Agrega partidas a la cotizacion antes de enviar.")
        return redirect("estimate_edit", pk=order.pk)

    customer = order.get_customer()
    phone = (getattr(customer, "phone", "") or "").strip() if customer else ""
    if not phone:
        messages.error(request, "El cliente no tiene telefono registrado.")
        return redirect("estimate_edit", pk=order.pk)

    public_url = request.build_absolute_uri(reverse("estimate_public", args=[estimate.token]))
    customer_name = getattr(customer, "name", "") if customer else "cliente"
    whatsapp_message = _build_estimate_message(order, customer, public_url)
    whatsapp_url = build_whatsapp_link(phone, whatsapp_message)
    if not whatsapp_url:
        messages.error(
            request,
            "No se pudo generar el enlace de WhatsApp. Verifica el telefono del cliente.",
        )
        return redirect("estimate_edit", pk=order.pk)

    Notification.objects.create(
        order=order,
        kind="whatsapp",
        channel="estimate_send_whatsapp",
        ok=True,
        payload={
            "to": phone,
            "estimate": str(estimate.token),
            "url": public_url,
            "order_folio": _resolve_order_folio(order),
            "order": _resolve_order_folio(order),
            "customer": customer_name,
        },
    )

    return redirect(whatsapp_url)



def estimate_public(request, token):
    estimate = get_object_or_404(
        Estimate.objects.select_related(
            "order",
            "order__customer",
        ).prefetch_related("items", "order__devices"),
        token=token,
    )

    subtotal = (estimate.subtotal or Decimal("0.00")).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    tax = (estimate.tax or Decimal("0.00")).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    total = (estimate.total or Decimal("0.00")).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    accepted_subtotal, accepted_tax, accepted_total = estimate.accepted_totals()

    items = []
    for item in estimate.items.order_by("id"):
        raw_unit_price = item.unit_price or Decimal("0.00")
        unit_price = raw_unit_price.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        line_total = (raw_unit_price * item.qty).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        item_status = item.status or EstimateItem.Status.PENDING
        items.append(
            {
                "id": item.id,
                "description": item.description,
                "qty": item.qty,
                "unit_price": unit_price,
                "line_total": line_total,
                "status": item_status,
                "decided_at": item.decided_at,
            }
        )

    approved_at_local = timezone.localtime(estimate.approved_at) if estimate.approved_at else None
    declined_at_local = timezone.localtime(estimate.declined_at) if estimate.declined_at else None
    is_pending = estimate.has_pending_items

    customer = estimate.order.get_customer()
    context = {
        "estimate": estimate,
        "order": estimate.order,
        "customer": customer,
        "order_devices": _order_devices(estimate.order),
        "items": items,
        "subtotal": subtotal,
        "tax": tax,
        "total": total,
        "accepted_subtotal": accepted_subtotal,
        "accepted_tax": accepted_tax,
        "accepted_total": accepted_total,
        "item_status_pending": EstimateItem.Status.PENDING,
        "item_status_accepted": EstimateItem.Status.ACCEPTED,
        "item_status_rejected": EstimateItem.Status.REJECTED,
        "status_label": _estimate_status_label(estimate),
        "note": estimate.note or "",
        "has_pending_items": estimate.has_pending_items,
        "is_closed": estimate.is_closed,
        "approved_at_local": approved_at_local,
        "declined_at_local": declined_at_local,
    }
    return render(request, "estimate_public.html", context)



@require_POST
def estimate_update_items(request, token):
    estimate = get_object_or_404(
        Estimate.objects.select_related("order").prefetch_related("items"),
        token=token,
    )
    if estimate.is_closed:
        messages.info(request, "Esta cotizacion ya esta cerrada.")
        return redirect("estimate_public", token=token)

    items = list(estimate.items.order_by("id"))
    valid_statuses = {choice for choice, _ in EstimateItem.Status.choices}
    now = timezone.now()
    updates = []
    for item in items:
        if item.status != EstimateItem.Status.PENDING:
            continue
        field_name = f"item-{item.pk}-status"
        submitted = (request.POST.get(field_name) or "").strip().upper()
        if submitted not in valid_statuses:
            continue
        if submitted == item.status:
            continue
        updates.append((item, submitted))

    if updates:
        with transaction.atomic():
            for item, new_status in updates:
                item.status = new_status
                item.decided_at = now
                item.save(update_fields=["status", "decided_at"])
                notify_estimate_item_decision(item)
            estimate.recompute_status_from_items(save=True)

    pending_ids = list(
        estimate.items.filter(status=EstimateItem.Status.PENDING).values_list("id", flat=True)
    )
    if pending_ids:
        messages.error(request, "Selecciona Acepto o No acepto en todas las partidas antes de guardar.")
        return redirect("estimate_public", token=token)

    # Close the estimate according to decisions (no pendings left)
    with transaction.atomic():
        estimate.recompute_status_from_items(save=True)
        estimate.updated_at = timezone.now()
        estimate.save(update_fields=["updated_at"])

    return redirect("estimate_public", token=token)


@require_POST
def estimate_approve(request, token):
    estimate = get_object_or_404(
        Estimate.objects.select_related(
            "order",
            "order__customer",
        ),
        token=token,
    )
    if not request.user.is_authenticated or not request.user.is_staff:
        return redirect("estimate_public", token=token)
    now = timezone.now()
    order = estimate.order
    with transaction.atomic():
        estimate.items.update(status=EstimateItem.Status.ACCEPTED, decided_at=now)
        estimate.approved_at = now
        estimate.declined_at = None
        estimate.status = Estimate.Status.CLOSED_ACCEPTED
        estimate.save(update_fields=["approved_at", "declined_at", "status"])
        actor_role = resolve_actor_role(None)
        target_status = ServiceOrder.Status.WAITING_PARTS
        fallback_status = ServiceOrder.Status.IN_REVIEW
        if order.can_transition_to(target_status):
            order.transition_to(target_status, author=None, author_role=actor_role)
        elif fallback_status and order.can_transition_to(fallback_status):
            # Avanza a revision si la orden sigue en NEW y vuelve a intentar dejarla esperando refacciones.
            order.transition_to(fallback_status, author=None, author_role=actor_role)
            if order.can_transition_to(target_status):
                order.transition_to(target_status, author=None, author_role=actor_role)

    customer = order.get_customer()
    customer_email = (getattr(customer, "email", "") or "").strip() if customer else ""
    total_value = (estimate.total or Decimal("0.00")).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    total_display = format(total_value, ".2f")
    if customer_email:
        body = (
            "Gracias por aprobar la cotizacion.\n"
            f"Total autorizado: ${total_display}.\n"
        )
        send_mail(
            subject=f"Cotizacion {order.folio} aprobada",
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[customer_email],
            fail_silently=True,
        )

    Notification.objects.create(
        order=order,
        kind="estimate",
        channel="approved",
        ok=True,
        title=f"Cotizacion {order.folio} aprobada",
        payload={
            "order_id": order.pk,
            "order_folio": _resolve_order_folio(order),
            "order": _resolve_order_folio(order),
            "folio": order.folio,
            "customer": getattr(customer, "name", "") if customer else "",
            "device": build_device_label(order),
            "total": total_display,
            "approved_at": timezone.localtime(now).isoformat(),
        },
    )

    return redirect("estimate_public", token=token)



@require_POST
def estimate_decline(request, token):
    estimate = get_object_or_404(
        Estimate.objects.select_related(
            "order",
            "order__customer",
        ),
        token=token,
    )
    if not request.user.is_authenticated or not request.user.is_staff:
        return redirect("estimate_public", token=token)
    now = timezone.now()
    order = estimate.order
    with transaction.atomic():
        estimate.items.update(status=EstimateItem.Status.REJECTED, decided_at=now)
        estimate.declined_at = now
        estimate.approved_at = None
        estimate.status = Estimate.Status.CLOSED_REJECTED
        estimate.save(update_fields=["approved_at", "declined_at", "status"])
        actor_role = resolve_actor_role(None)
        if order.can_transition_to(ServiceOrder.Status.IN_REVIEW):
            order.transition_to(ServiceOrder.Status.IN_REVIEW, author=None, author_role=actor_role)

    customer = order.get_customer()
    customer_email = (getattr(customer, "email", "") or "").strip() if customer else ""
    if customer_email:
        body = (
            "Hemos registrado el rechazo de la cotizacion.\n"
            "Si necesitas cambios o ayuda adicional, por favor contactanos.\n"
        )
        send_mail(
            subject=f"Cotizacion {order.folio} rechazada",
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[customer_email],
            fail_silently=True,
        )

    total_value = (estimate.total or Decimal("0.00")).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    total_display = format(total_value, ".2f")
    Notification.objects.create(
        order=order,
        kind="estimate",
        channel="declined",
        ok=True,
        title=f"Cotizacion {order.folio} rechazada",
        payload={
            "order_id": order.pk,
            "order_folio": _resolve_order_folio(order),
            "order": _resolve_order_folio(order),
            "folio": order.folio,
            "customer": getattr(customer, "name", "") if customer else "",
            "device": build_device_label(order),
            "total": total_display,
            "declined_at": timezone.localtime(now).isoformat(),
        },
    )

    return redirect("estimate_public", token=token)


@login_required(login_url="/admin/login/")
@group_required(ROLE_RECEPCION, ROLE_GERENCIA, ROLE_TECNICO)
def notifications_list(request):
    notifications = Notification.objects.order_by("-created_at")[:100]
    unread_count = Notification.objects.filter(seen_at__isnull=True).count()
    return render(
        request,
        "notifications/list.html",
        {"notifications": notifications, "unread_count": unread_count},
    )


@login_required(login_url="/admin/login/")
@group_required(ROLE_RECEPCION, ROLE_GERENCIA, ROLE_TECNICO)
@require_POST
def notifications_mark_all_read(request):
    Notification.objects.filter(seen_at__isnull=True).update(seen_at=timezone.now())
    messages.success(request, "Notificaciones marcadas como leídas.")
    next_url = request.POST.get("next", "")
    if next_url:
        return redirect(next_url)
    return redirect("notifications_list")

# === INTEGRASYS PATCH: ATTACHMENTS VIEW ===
def _max_file_mb():
    try:
        return int(getattr(settings, "MAX_FILE_MB", 20))
    except (TypeError, ValueError):
        return 20


def _attachment_max_bytes():
    return _max_file_mb() * 1024 * 1024


ATTACHMENT_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
ATTACHMENT_ALLOWED_EXTENSIONS = ATTACHMENT_IMAGE_EXTENSIONS | {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".txt",
    ".csv",
}
ATTACHMENT_ALLOWED_MIME_TYPES = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/plain",
    "text/csv",
}
ATTACHMENT_IMAGE_PREFIXES = ("image/",)


def _attachment_allowed(uploaded):
    name = (getattr(uploaded, "name", "") or "").strip()
    ext = Path(name).suffix.lower()
    content_type = (getattr(uploaded, "content_type", "") or "").lower()
    guessed, _ = mimetypes.guess_type(name)
    candidates = [content_type, (guessed or "").lower()]
    if any(ct.startswith(ATTACHMENT_IMAGE_PREFIXES) for ct in candidates if ct):
        return True
    if any(ct in ATTACHMENT_ALLOWED_MIME_TYPES for ct in candidates if ct):
        return True
    if ext in ATTACHMENT_ALLOWED_EXTENSIONS:
        return True
    return False


def _attachment_is_image(name):
    return Path(name or "").suffix.lower() in ATTACHMENT_IMAGE_EXTENSIONS


def _format_bytes(total):
    try:
        size = float(total)
    except (TypeError, ValueError):
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


@login_required(login_url="/admin/login/")
@group_required(ROLE_RECEPCION, ROLE_GERENCIA, ROLE_TECNICO)
def order_attachments(request, pk):
    order = get_object_or_404(ServiceOrder, pk=pk)
    existing_filenames = set(
        Path(att.file.name).name.lower()
        for att in Attachment.objects.filter(service_order=order)
    )
    caption_value = (request.POST.get("caption") or "").strip()
    if request.method == "POST":
        files = request.FILES.getlist("file")
        if not files:
            messages.warning(request, "Selecciona al menos un archivo.")
            return redirect("order_attachments", pk=order.pk)
        valid_files = []
        rejected = []
        max_file_mb = _max_file_mb()
        max_bytes = _attachment_max_bytes()
        for uploaded in files:
            file_name = getattr(uploaded, "name", "archivo")
            base_name = Path(file_name).name
            size = getattr(uploaded, "size", 0) or 0
            if size > max_bytes:
                rejected.append(f"{file_name}: excede {max_file_mb} MB.")
                continue
            if not _attachment_allowed(uploaded):
                rejected.append(f"{file_name}: tipo no permitido.")
                continue
            name_key = base_name.lower()
            if name_key in existing_filenames:
                rejected.append(f"{file_name}: ya existe en la orden.")
                continue
            existing_filenames.add(name_key)
            valid_files.append((uploaded, base_name))
        if not valid_files:
            for msg_text in rejected:
                messages.error(request, msg_text)
            return redirect("order_attachments", pk=order.pk)
        created = 0
        file_field = Attachment._meta.get_field("file")
        storage = file_field.storage
        for uploaded, base_name in valid_files:
            temp_instance = Attachment(service_order=order)
            target_name = file_field.generate_filename(temp_instance, base_name)
            if storage.exists(target_name):
                storage.delete(target_name)
            data = {"service_order": order, "file": uploaded}
            if hasattr(Attachment, "caption"):
                data["caption"] = caption_value
            Attachment.objects.create(**data)
            created += 1
        if rejected:
            for msg_text in rejected:
                messages.warning(request, f"No se adjunto {msg_text}")
        messages.success(request, f"Subidos {created} adjunto(s).")
        return redirect("order_attachments", pk=order.pk)

    attachments = list(Attachment.objects.filter(service_order=order).order_by("-id"))
    for att in attachments:
        file_obj = getattr(att, "file", None)
        raw_name = getattr(file_obj, "name", "") if file_obj else ""
        name = Path(raw_name or "").name
        size_value = getattr(file_obj, "size", 0) if file_obj else 0
        att.filename = name or raw_name
        att.size_display = _format_bytes(size_value)
        att.is_image = _attachment_is_image(name)

    max_file_mb = _max_file_mb()
    non_image_exts = sorted(ATTACHMENT_ALLOWED_EXTENSIONS - ATTACHMENT_IMAGE_EXTENSIONS)
    accept_values = ["image/*"] + non_image_exts
    accept_attr = ",".join(accept_values)
    form = AttachmentForm() if AttachmentForm else None
    context = {
        "order": order,
        "attachments": attachments,
        "form": form,
        "max_size_mb": max_file_mb,
        "total_limit_hint": f"No subas mas de {max_file_mb} MB totales por envio.",
        "allowed_extensions": ", ".join(sorted(e.lstrip(".").upper() for e in ATTACHMENT_ALLOWED_EXTENSIONS)),
        "accept_attr": accept_attr,
    }
    return render(request, "recepcion/order_attachments.html", context)


@login_required(login_url="/admin/login/")
@require_manager
@require_POST
def delete_attachment(request, pk, att_id):
    order = get_object_or_404(ServiceOrder, pk=pk)
    attachment = get_object_or_404(Attachment, pk=att_id, service_order=order)
    stored_file = attachment.file
    if stored_file:
        stored_file.delete(save=False)
    attachment.delete()
    messages.success(request, "Adjunto eliminado.")
    return redirect("order_attachments", pk=order.pk)

def export_inventory_csv(request):
    # Import local para evitar problemas de orden de carga
    from .models import InventoryItem

    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = 'attachment; filename="inventario.csv"'
    w = csv.writer(resp)

    w.writerow(["SKU", "Nombre", "Stock", "Minimo"])

    qs = InventoryItem.objects.all().order_by("sku")
    for it in qs:
        sku   = getattr(it, "sku", "")
        name  = getattr(it, "name", "")
        qty   = getattr(it, "qty", "")
        min_q = getattr(it, "min_qty", "")

        w.writerow([sku, name, qty, min_q])

    return resp



