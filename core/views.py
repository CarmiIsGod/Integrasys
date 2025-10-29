from django.http import HttpResponse, Http404
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

from .forms import ReceptionForm, InventoryItemForm, AttachmentForm
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
    is_gerencia,
    is_recepcion,
    is_tecnico,
    roles_required,
)


ALLOWED = {
    "NEW": ["REV"],
    "REV": ["WAI", "AUTH", "READY"],
    "WAI": ["REV", "READY"],
    "AUTH": ["REV", "READY"],
    "READY": ["DONE"],
    "DONE": [],
}


IVA_RATE = Decimal("0.16")
TWO_PLACES = Decimal("0.01")

logger = logging.getLogger(__name__)


def _estimate_status_label(estimate):
    if not estimate:
        return "Sin cotizacion"
    if estimate.approved_at:
        return f"Aprobada ({timezone.localtime(estimate.approved_at).strftime('%Y-%m-%d %H:%M')})"
    if estimate.declined_at:
        return f"Rechazada ({timezone.localtime(estimate.declined_at).strftime('%Y-%m-%d %H:%M')})"
    return "Pendiente"


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


def _resolve_order_folio(order):
    folio = getattr(order, "folio", None)
    if folio:
        return folio
    order_id = getattr(order, "id", None)
    if order_id:
        return f"SR-{order_id}"
    return "SR-0000"


def _build_device_label(order):
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
            "device": _build_device_label(order),
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


def build_whatsapp_link(phone: str, text: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if not digits:
        return ""
    if not digits.startswith("52"):
        digits = "52" + digits
    return f"https://wa.me/{digits}?text={quote(text)}"


def public_status(request, token):
    try:
        order = ServiceOrder.objects.select_related(
            "device", "device__customer"
        ).get(token=token)
    except ServiceOrder.DoesNotExist:
        raise Http404("Orden no encontrada")
    history = order.history.order_by("-created_at")
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
    order = get_object_or_404(ServiceOrder, token=token)
    public_url = request.build_absolute_uri(reverse("public_status", args=[token]))

    try:
        buf = BytesIO()
        qrcode.make(public_url).save(buf, format="PNG")
        qr_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        qr_b64 = ""

    html = render_to_string(
        "receipt.html",
        {"order": order, "qr_b64": qr_b64, "public_url": public_url},
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
@roles_required(ROLE_GERENCIA)
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

    status_cards = [
        {"code": code, "label": label, "count": counts.get(code, 0)}
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
    return render(request, "dashboard.html", context)


@login_required(login_url="/admin/login/")
@roles_required(ROLE_RECEPCION, ROLE_GERENCIA)
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
@roles_required(ROLE_RECEPCION, ROLE_GERENCIA)
@require_POST
def receive_stock(request):
    sku = (request.POST.get("sku") or "").strip()
    qty_raw = (request.POST.get("qty") or "").strip()
    reason = (request.POST.get("reason") or "Entrada").strip()
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

    InventoryMovement.objects.create(item=item, delta=qty, reason=reason, author=request.user)
    item.qty = item.qty + qty
    item.save()
    messages.success(request, f"Entrada registrada: +{qty} de {item.name} (SKU {item.sku}).")
    return redirect("inventory_list")


@login_required(login_url="/admin/login/")
@roles_required(ROLE_RECEPCION, ROLE_GERENCIA)
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
@roles_required(ROLE_RECEPCION, ROLE_GERENCIA)
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
@roles_required(ROLE_GERENCIA)
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
@roles_required(ROLE_RECEPCION, ROLE_GERENCIA, ROLE_TECNICO)
def list_orders(request):
    q = (request.GET.get("q", "") or "").strip()
    status = (request.GET.get("status", "") or "").strip()
    dfrom = (request.GET.get("from", "") or "").strip()
    dto   = (request.GET.get("to", "") or "").strip()
    export = request.GET.get("export") == "1"
    assignee = (request.GET.get("assignee", "") or "").strip()

    is_superuser = request.user.is_superuser
    is_technician = is_tecnico(request.user)
    is_reception = is_recepcion(request.user)

    can_create = is_reception
    can_assign = is_reception
    can_send_estimate = is_reception
    can_export = is_gerencia(request.user)

    qs = ServiceOrder.objects.select_related("device","device__customer").order_by("-checkin_at")
    if is_technician:
        qs = qs.filter(assigned_to=request.user)

    if q:
        qs = qs.filter(
            Q(folio__icontains=q)
            | Q(device__serial__icontains=q)
            | Q(device__model__icontains=q)
            | Q(device__customer__name__icontains=q)
        )
    if status:
        qs = qs.filter(status=status)
    if assignee:
        if is_technician:
            if str(request.user.pk) == assignee:
                qs = qs.filter(assigned_to=request.user)
        else:
            qs = qs.filter(assigned_to_id=assignee)

    tz = timezone.get_current_timezone()
    if dfrom:
        try:
            start_dt = tz.localize(datetime.strptime(dfrom, "%Y-%m-%d"))
            qs = qs.filter(checkin_at__gte=start_dt)
        except ValueError:
            pass
    if dto:
        try:
            end_dt = tz.localize(datetime.strptime(dto, "%Y-%m-%d")) + timedelta(days=1)
            qs = qs.filter(checkin_at__lt=end_dt)
        except ValueError:
            pass

    if export:
        resp = HttpResponse(content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = 'attachment; filename="ordenes.csv"'
        w = csv.writer(resp)
        w.writerow(["Folio","Estado","Cliente","Equipo","Serie","Check-in","Check-out","Pagado","Saldo","Link p\u00fablico"])
        for o in qs:
            public_url = request.build_absolute_uri(reverse("public_status", args=[o.token]))
            w.writerow([
                o.folio,
                o.get_status_display(),
                o.device.customer.name if o.device_id else "",
                (o.device.brand + " " + o.device.model).strip() if o.device_id else "",
                o.device.serial if o.device_id else "",
                timezone.localtime(o.checkin_at).strftime("%Y-%m-%d %H:%M"),
                timezone.localtime(o.checkout_at).strftime("%Y-%m-%d %H:%M") if o.checkout_at else "",
                str(o.paid_total),
                str(o.balance),
                public_url,
            ])
        return resp

    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    restricted_statuses = {"REV", "WAI", "READY"}
    for o in page_obj.object_list:
        allowed_next = ALLOWED.get(o.status, [])
        if is_technician:
            allowed_next = [code for code in allowed_next if code in restricted_statuses]
        o.allowed_next = allowed_next
        public_url = request.build_absolute_uri(reverse("public_status", args=[o.token]))
        if o.status == "READY":
            base = f"Hola {o.device.customer.name}, tu orden {o.folio} est\u00e1 LISTA para recoger."
        elif o.status == "DONE":
            base = f"Hola {o.device.customer.name}, tu orden {o.folio} fue ENTREGADA."
        else:
            base = f"Hola {o.device.customer.name}, tu orden {o.folio} est\u00e1 {o.get_status_display()}."
        msg = f"{base} Detalle: {public_url}"
        o.whatsapp_link = build_whatsapp_link(getattr(o.device.customer, "phone", ""), msg)

    context = {
        "page_obj": page_obj,
        "query": q,
        "status": status,
        "dfrom": dfrom,
        "dto": dto,
        "status_choices": ServiceOrder.Status.choices,
        "ALLOWED": ALLOWED,
        "assignee": assignee,
        "staff_users": User.objects.filter(is_staff=True).order_by("username"),
        "can_create": can_create,
        "can_assign": can_assign,
        "can_send_estimate": can_send_estimate,
        "can_export": can_export,
        "is_technician": is_technician,
        "is_superuser": is_superuser,
    }
    return render(request, "reception_orders.html", context)

@login_required(login_url="/admin/login/")
@roles_required(ROLE_RECEPCION, ROLE_GERENCIA, ROLE_TECNICO)
def order_detail(request, pk):
    order = get_object_or_404(
        ServiceOrder.objects.select_related("device","device__customer"),
        pk=pk,
    )
    history = order.history.order_by("-created_at")
    parts = InventoryMovement.objects.filter(order=order).select_related("item").order_by("-created_at")
    allowed_next = ALLOWED.get(order.status, [])

    is_superuser = request.user.is_superuser
    is_technician = is_tecnico(request.user)
    is_reception = is_recepcion(request.user)

    if is_technician:
        allowed_next = [code for code in allowed_next if code in {"REV", "WAI", "READY"}]

    public_url = request.build_absolute_uri(reverse("public_status", args=[order.token]))
    if order.status == "READY":
        base = f"Hola {order.device.customer.name}, tu orden {order.folio} est\u00e1 LISTA para recoger."
    elif order.status == "DONE":
        base = f"Hola {order.device.customer.name}, tu orden {order.folio} fue ENTREGADA."
    else:
        base = f"Hola {order.device.customer.name}, tu orden {order.folio} est\u00e1 {order.get_status_display()}."
    msg = f"{base} Detalle: {public_url}"
    wa_link = build_whatsapp_link(getattr(order.device.customer, "phone", ""), msg)

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

    payments = order.payments.select_related("author")
    approved_total = order.approved_total
    paid_total = order.paid_total
    balance = order.balance
    can_charge = is_reception and balance > Decimal("0.00")

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
        },
    )

@login_required(login_url="/admin/login/")
@roles_required(ROLE_RECEPCION, ROLE_GERENCIA)
@require_POST
def add_payment(request, pk):
    order = get_object_or_404(
        ServiceOrder.objects.select_related("device", "device__customer"),
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
                amount=amount,
                method=method,
                reference=reference,
                author=request.user,
            )

        amount_display = format(amount, ".2f")
        new_balance = order.balance
        balance_display = format(new_balance, ".2f")

        Status = ServiceOrder.Status
        ready_value = getattr(Status, "READY", getattr(Status, "READY_PICKUP", "READY"))
        ready_pickup_value = getattr(Status, "READY_PICKUP", ready_value)
        auth_value = getattr(Status, "AUTH", getattr(Status, "REQUIRES_AUTH", "AUTH"))
        done_value = getattr(Status, "DONE", getattr(Status, "DELIVERED", "DONE"))
        try:
            if new_balance == Decimal("0.00") and order.status in {ready_value, ready_pickup_value, auth_value}:
                order.status = done_value
                order.save(update_fields=["status"])
                try:
                    StatusHistory.objects.create(order=order, status=done_value, author=request.user)
                except Exception:
                    logger.exception("No se pudo guardar historial de cierre de orden")
                _record_status_update(
                    order,
                    channel="status_auto_close",
                    extra_payload={"trigger": "payment_zero"},
                )
        except Exception:
            logger.exception("No se pudo cerrar la orden tras dejar saldo en cero")

        device_label = _build_device_label(order)
        folio = _resolve_order_folio(order)
        customer = getattr(order.device, "customer", None)
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
@roles_required(ROLE_RECEPCION, ROLE_GERENCIA)
@require_POST
def change_status_auth(request, pk):
    order = get_object_or_404(
        ServiceOrder.objects.select_related("device__customer"),
        pk=pk,
    )

    allowed_next = ALLOWED.get(order.status, [])
    if ServiceOrder.Status.REQUIRES_AUTH not in allowed_next:
        messages.error(request, "Transicion de estado invalida.")
        return redirect("order_detail", pk=order.pk)

    if is_tecnico(request.user):
        messages.error(request, "No tienes permiso para solicitar autorizacion.")
        return redirect("order_detail", pk=order.pk)

    device_label = _build_device_label(order)
    customer = getattr(order.device, "customer", None)
    customer_name = getattr(customer, "name", "") if customer else ""
    order_folio = _resolve_order_folio(order)

    estimate, _ = Estimate.objects.get_or_create(order=order)

    order.status = ServiceOrder.Status.REQUIRES_AUTH
    order.save(update_fields=["status"])

    StatusHistory.objects.create(order=order, status=ServiceOrder.Status.REQUIRES_AUTH, author=request.user)
    _record_status_update(
        order,
        channel="status_requires_authorization",
        extra_payload={
            "target": ServiceOrder.Status.REQUIRES_AUTH,
            "changed_by": getattr(request.user, "username", ""),
        },
    )

    email = (order.device.customer.email or "").strip()
    payload = {}
    send_ok = False
    error_message = None

    if email:
        public_status_url = request.build_absolute_uri(reverse("public_status", args=[order.token]))
        public_estimate_url = request.build_absolute_uri(reverse("estimate_public", args=[estimate.token]))
        has_items = estimate.items.exists()
        payload = {
            "to": email,
            "public_status": public_status_url,
            "public_estimate": public_estimate_url,
            "has_items": has_items,
            "order_folio": order_folio,
            "order": order_folio,
            "customer": customer_name,
            "device": device_label,
            "status": order.status,
        }
        subject = f"Orden {order.folio} requiere autorizacion"
        body_lines = [
            f"Hola {order.device.customer.name},",
            "",
            f"Tu orden {order.folio} requiere autorizacion para continuar con el servicio.",
        ]
        if has_items:
            body_lines.append(f"Revisa y autoriza la cotizacion en: {public_estimate_url}")
        else:
            body_lines.append("Actualizaremos la cotizacion en breve. Mientras tanto, consulta el estado aqui:")
            body_lines.append(public_status_url)
        body_lines.append("")
        body_lines.append("Si tienes dudas, contactanos.")
        message = "\n".join(body_lines)
        try:
            send_count = send_mail(
                subject=subject,
                message=message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                fail_silently=False,
            )
            send_ok = send_count > 0
        except Exception as exc:
            error_message = str(exc)
    else:
        payload = {
            "error": "missing_email",
            "order_folio": order_folio,
            "order": order_folio,
            "customer": customer_name,
            "device": device_label,
            "status": order.status,
        }
        messages.warning(request, "El cliente no tiene correo registrado; agrega uno para notificar.")

    if error_message:
        payload["error"] = error_message

    Notification.objects.create(
        order=order,
        kind="email",
        channel="status_auth",
        ok=send_ok,
        payload=payload,
    )

    if send_ok:
        messages.success(request, "Orden marcada como Requiere autorizacion y se notifico al cliente.")
    else:
        if email:
            messages.warning(
                request,
                "Orden marcada como Requiere autorizacion, pero ocurrio un error al enviar el correo.",
            )
        else:
            messages.info(request, "Orden marcada como Requiere autorizacion sin notificacion por email.")

    return redirect("estimate_edit", pk=order.pk)


@login_required(login_url="/admin/login/")
@roles_required(ROLE_RECEPCION, ROLE_GERENCIA, ROLE_TECNICO)
@require_POST
def change_status(request, pk):
    order = get_object_or_404(ServiceOrder, pk=pk)
    target = request.POST.get("target")
    allowed = ALLOWED.get(order.status, [])

    device_label = _build_device_label(order)
    customer = getattr(order.device, "customer", None)
    customer_name = getattr(customer, "name", "") if customer else ""
    order_folio = _resolve_order_folio(order)

    if target not in allowed:
        messages.error(request, "Transicion de estado invalida.")
        return redirect("list_orders")

    if target == ServiceOrder.Status.REQUIRES_AUTH:
        messages.error(request, "Usa el flujo de autorizacion dedicado.")
        return redirect("order_detail", pk=order.pk)

    if is_tecnico(request.user):
        if order.assigned_to_id != request.user.id:
            messages.error(request, "Solo puedes actualizar tus ordenes asignadas.")
            return redirect("list_orders")
        if target not in {"REV", "WAI", "READY"}:
            messages.error(request, "Los tecnicos solo pueden cambiar a REV, WAI o READY.")
            return redirect("list_orders")

    if target == "DONE" and not request.user.is_superuser:
        messages.error(request, "Solo un superusuario puede marcar como Entregado.")
        return redirect("list_orders")

    if target == "DONE" and hasattr(order, "balance"):
        balance_value = order.balance
        if balance_value is not None and balance_value > Decimal("0.00"):
            messages.error(request, "No puedes entregar con saldo pendiente.")
            return redirect("order_detail", pk=order.pk)

    order.status = target
    order.save()

    StatusHistory.objects.create(order=order, status=target, author=request.user)
    _record_status_update(
        order,
        channel="status_change",
        extra_payload={
            "target": target,
            "changed_by": getattr(request.user, "username", ""),
        },
    )

    cust_email = (order.device.customer.email or "").strip()
    if cust_email and target in ("READY", "DONE"):
        public_url = request.build_absolute_uri(reverse("public_status", args=[order.token]))
        subject = f"Orden {order.folio} - " + ("Listo para recoger" if target == "READY" else "Entregado")
        body = (
            f"Hola {order.device.customer.name},\n\n"
            f"Tu equipo: {order.device.brand} {order.device.model} ({order.device.serial})\n"
            f"Estatus: {order.get_status_display()}\n\n"
            f"Consulta el detalle aqui: {public_url}\n\n"
            f"Gracias.\n"
        )
        try:
            send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [cust_email], fail_silently=False)
            Notification.objects.create(
                order=order,
                kind="email",
                channel="status",
                ok=True,
                payload={
                    "to": cust_email,
                    "target": target,
                    "order": order_folio,
                    "order_folio": order_folio,
                    "customer": customer_name,
                    "device": device_label,
                },
            )
        except Exception as exc:
            Notification.objects.create(
                order=order,
                kind="email",
                channel="status",
                ok=False,
                payload={
                    "to": cust_email,
                    "target": target,
                    "order": order_folio,
                    "error": str(exc),
                    "order_folio": order_folio,
                    "customer": customer_name,
                    "device": device_label,
                },
            )

    messages.success(request, f"Estado actualizado a {order.get_status_display()}.")
    return redirect("list_orders")


@login_required(login_url="/admin/login/")
@roles_required(ROLE_RECEPCION, ROLE_GERENCIA)
def payment_receipt_pdf(request, payment_id):
    payment = get_object_or_404(
        Payment.objects.select_related("order", "author", "order__device", "order__device__customer"),
        pk=payment_id,
    )
    order = payment.order
    customer = order.device.customer
    company_name = getattr(settings, "DEFAULT_FROM_EMAIL", "") or "Taller"
    public_url = request.build_absolute_uri(reverse("public_status", args=[order.token]))

    context = {
        "payment": payment,
        "order": order,
        "customer": customer,
        "company_name": company_name,
        "public_url": public_url,
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
@roles_required(ROLE_RECEPCION, ROLE_GERENCIA, ROLE_TECNICO)
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
@roles_required(ROLE_RECEPCION, ROLE_GERENCIA, ROLE_TECNICO)
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
@roles_required(ROLE_RECEPCION, ROLE_GERENCIA, ROLE_TECNICO)
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
@roles_required(ROLE_RECEPCION, ROLE_GERENCIA)
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
    devices = Device.objects.filter(customer=customer).order_by("-id")
    return render(
        request,
        "clientes/customer_devices.html",
        {
            "customer": customer,
            "devices": devices,
        },
    )


@login_required(login_url="/admin/login/")
@roles_required(ROLE_RECEPCION, ROLE_GERENCIA, ROLE_TECNICO)
def reception_home(request):
    return render(
        request,
        "reception/home.html",
        {"can_export": is_gerencia(request.user)},
    )


@login_required(login_url="/admin/login/")
@roles_required(ROLE_RECEPCION, ROLE_GERENCIA)
def reception_new_order(request):
    prefill = {
        "name": "",
        "phone": "",
        "email": "",
        "brand": "",
        "model": "",
        "serial": "",
        "notes": "",
    }
    if request.method == "POST":
        data = request.POST.copy()
        if "name" in data and "customer_name" not in data:
            data["customer_name"] = data.get("name", "")
            data["customer_phone"] = data.get("phone", "")
            data["customer_email"] = data.get("email", "")
            data["brand"] = data.get("brand", "")
            data["model"] = data.get("model", "")
            data["serial"] = data.get("serial", "")
            data["notes"] = data.get("notes", "")
        form = ReceptionForm(data)
        if form.is_valid():
            name = form.cleaned_data["customer_name"].strip()
            phone = (form.cleaned_data.get("customer_phone") or "").strip()
            email = (form.cleaned_data.get("customer_email") or "").strip()
            brand = (form.cleaned_data.get("brand") or "").strip()
            model = (form.cleaned_data.get("model") or "").strip()
            serial = (form.cleaned_data.get("serial") or "").strip()
            notes = (form.cleaned_data.get("notes") or "").strip()

            customer = None
            if email:
                customer = Customer.objects.filter(email=email).first()
            if customer is None and phone:
                customer = Customer.objects.filter(phone=phone).first()
            if customer is None:
                customer = Customer.objects.create(
                    name=name,
                    phone=phone or "",
                    email=email or "",
                )
            else:
                updated = False
                if not customer.name and name:
                    customer.name = name
                    updated = True
                if not customer.phone and phone:
                    customer.phone = phone
                    updated = True
                if not customer.email and email:
                    customer.email = email
                    updated = True
                if updated:
                    customer.save()

            device, _ = Device.objects.get_or_create(
                customer=customer,
                serial=serial or "",
                defaults={"brand": brand, "model": model, "notes": notes},
            )
            changed = False
            if not device.brand and brand:
                device.brand = brand
                changed = True
            if not device.model and model:
                device.model = model
                changed = True
            if notes and not device.notes:
                device.notes = notes
                changed = True
            if changed:
                device.save()

            order = ServiceOrder.objects.create(
                device=device,
                notes=notes,
                assigned_to=request.user if request.user.is_authenticated else None,
            )
            StatusHistory.objects.create(order=order, status=order.status, author=request.user)

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
            "email": data.get("email") or data.get("customer_email", ""),
            "brand": data.get("brand", ""),
            "model": data.get("model", ""),
            "serial": data.get("serial", ""),
            "notes": data.get("notes") or "",
        }
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)

    context = {"prefill": prefill}
    return render(request, "reception/new_order.html", context)
@login_required(login_url="/admin/login/")
@roles_required(ROLE_RECEPCION, ROLE_GERENCIA)
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
@roles_required(ROLE_RECEPCION, ROLE_GERENCIA)
@require_POST
def estimate_send(request, pk):
    order = get_object_or_404(
        ServiceOrder.objects.select_related("device__customer"),
        pk=pk,
    )
    estimate, _ = Estimate.objects.get_or_create(order=order)
    if not estimate.items.exists():
        messages.error(request, "Agrega partidas a la cotizacion antes de enviar.")
        return redirect("estimate_edit", pk=order.pk)

    email = (order.device.customer.email or "").strip()
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
        "customer": order.device.customer,
        "items": items,
        "subtotal": subtotal,
        "tax": tax,
        "total": total,
        "public_url": public_url,
    }
    subject = f"Cotizacion orden {order.folio}"
    plain_message = (
        f"Hola {order.device.customer.name},\n"
        f"Revisa la cotizacion {order.folio}: {public_url}\n"
    )
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
            "customer": order.device.customer.name,
        },
    )

    if ok:
        messages.success(request, "Cotizacion enviada al cliente.")
    else:
        messages.warning(request, "No se pudo enviar la cotizacion por correo.")
    return redirect("estimate_edit", pk=order.pk)



def estimate_public(request, token):
    estimate = get_object_or_404(
        Estimate.objects.select_related(
            "order",
            "order__device",
            "order__device__customer",
        ).prefetch_related("items"),
        token=token,
    )

    subtotal = (estimate.subtotal or Decimal("0.00")).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    tax = (estimate.tax or Decimal("0.00")).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    total = (estimate.total or Decimal("0.00")).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    items = []
    for item in estimate.items.order_by("id"):
        raw_unit_price = item.unit_price or Decimal("0.00")
        unit_price = raw_unit_price.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        line_total = (raw_unit_price * item.qty).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        items.append(
            {
                "description": item.description,
                "qty": item.qty,
                "unit_price": unit_price,
                "line_total": line_total,
            }
        )

    approved_at_local = timezone.localtime(estimate.approved_at) if estimate.approved_at else None
    declined_at_local = timezone.localtime(estimate.declined_at) if estimate.declined_at else None
    is_pending = not estimate.approved_at and not estimate.declined_at

    context = {
        "estimate": estimate,
        "order": estimate.order,
        "customer": estimate.order.device.customer,
        "items": items,
        "subtotal": subtotal,
        "tax": tax,
        "total": total,
        "status_label": _estimate_status_label(estimate),
        "note": estimate.note or "",
        "is_pending": is_pending,
        "approved_at_local": approved_at_local,
        "declined_at_local": declined_at_local,
    }
    return render(request, "estimate_public.html", context)



@require_POST
def estimate_approve(request, token):
    estimate = get_object_or_404(
        Estimate.objects.select_related(
            "order",
            "order__device",
            "order__device__customer",
        ),
        token=token,
    )
    if estimate.approved_at or estimate.declined_at:
        return redirect("estimate_public", token=token)

    now = timezone.now()
    order = estimate.order
    with transaction.atomic():
        estimate.approved_at = now
        estimate.declined_at = None
        estimate.save(update_fields=["approved_at", "declined_at"])
        order.status = ServiceOrder.Status.WAITING_PARTS
        order.save(update_fields=["status"])
        StatusHistory.objects.create(order=order, status=order.status, author=None)

    customer_email = (order.device.customer.email or "").strip()
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
            "customer": order.device.customer.name,
            "device": _build_device_label(order),
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
            "order__device",
            "order__device__customer",
        ),
        token=token,
    )
    if estimate.approved_at or estimate.declined_at:
        return redirect("estimate_public", token=token)

    now = timezone.now()
    order = estimate.order
    with transaction.atomic():
        estimate.declined_at = now
        estimate.approved_at = None
        estimate.save(update_fields=["approved_at", "declined_at"])
        order.status = ServiceOrder.Status.IN_REVIEW
        order.save(update_fields=["status"])
        StatusHistory.objects.create(order=order, status=order.status, author=None)

    customer_email = (order.device.customer.email or "").strip()
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
            "customer": order.device.customer.name,
            "device": _build_device_label(order),
            "total": total_display,
            "declined_at": timezone.localtime(now).isoformat(),
        },
    )

    return redirect("estimate_public", token=token)


@login_required(login_url="/admin/login/")
@roles_required(ROLE_RECEPCION, ROLE_GERENCIA, ROLE_TECNICO)
def notifications_list(request):
    notifications = Notification.objects.order_by("-created_at")[:100]
    unread_count = Notification.objects.filter(seen_at__isnull=True).count()
    return render(
        request,
        "notifications/list.html",
        {"notifications": notifications, "unread_count": unread_count},
    )


@login_required(login_url="/admin/login/")
@roles_required(ROLE_RECEPCION, ROLE_GERENCIA, ROLE_TECNICO)
@require_POST
def notifications_mark_all_read(request):
    Notification.objects.filter(seen_at__isnull=True).update(seen_at=timezone.now())
    messages.success(request, "Notificaciones marcadas como le&iacute;das.")
    next_url = request.POST.get("next", "")
    if next_url:
        return redirect(next_url)
    return redirect("notifications_list")

# === INTEGRASYS PATCH: ATTACHMENTS VIEW ===
ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024
ATTACHMENT_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
ATTACHMENT_ALLOWED_EXTENSIONS = ATTACHMENT_IMAGE_EXTENSIONS | {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".txt"}
ATTACHMENT_ALLOWED_MIME_TYPES = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/plain",
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
@roles_required(ROLE_RECEPCION, ROLE_GERENCIA, ROLE_TECNICO)
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
        max_size_mb = ATTACHMENT_MAX_BYTES // (1024 * 1024)
        for uploaded in files:
            file_name = getattr(uploaded, "name", "archivo")
            size = getattr(uploaded, "size", 0) or 0
            if size > ATTACHMENT_MAX_BYTES:
                rejected.append(f"{file_name}: excede {max_size_mb} MB.")
                continue
            if not _attachment_allowed(uploaded):
                rejected.append(f"{file_name}: tipo no permitido.")
                continue
            name_key = Path(file_name).name.lower()
            if name_key in existing_filenames:
                rejected.append(f"{file_name}: ya existe en la orden.")
                continue
            existing_filenames.add(name_key)
            valid_files.append(uploaded)
        if not valid_files:
            for msg_text in rejected:
                messages.error(request, msg_text)
            return redirect("order_attachments", pk=order.pk)
        created = 0
        for uploaded in valid_files:
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

    accept_attr = ",".join(sorted(ATTACHMENT_ALLOWED_EXTENSIONS))
    form = AttachmentForm() if AttachmentForm else None
    context = {
        "order": order,
        "attachments": attachments,
        "form": form,
        "max_size_mb": ATTACHMENT_MAX_BYTES // (1024 * 1024),
        "allowed_extensions": ", ".join(sorted(e.lstrip(".").upper() for e in ATTACHMENT_ALLOWED_EXTENSIONS)),
        "accept_attr": accept_attr,
    }
    return render(request, "recepcion/order_attachments.html", context)


@login_required(login_url="/admin/login/")
@roles_required(ROLE_GERENCIA)
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

# === INTEGRASYS: ExportaciÃ³n CSV de Ã³rdenes ===
@login_required(login_url="/admin/login/")
@roles_required(ROLE_GERENCIA)
def export_orders_csv(request):
    fmt = "%Y-%m-%d"
    get = request.GET.get
    dfrom = get("from") or get("desde")
    dto   = get("to")   or get("hasta")
    status = get("status") or ""
    assignee = get("assignee") or ""
    q = get("q") or ""

    qs = ServiceOrder.objects.select_related("device__customer", "assigned_to").order_by("-checkin_at")
    tz = timezone.get_current_timezone()
    if dfrom:
        try:
            start = datetime.strptime(dfrom, fmt)
            if settings.USE_TZ and timezone.is_naive(start):
                start = tz.localize(start)
            qs = qs.filter(checkin_at__gte=start)
        except ValueError:
            pass
    if dto:
        try:
            end = datetime.strptime(dto, fmt)
            if settings.USE_TZ and timezone.is_naive(end):
                end = tz.localize(end)
            qs = qs.filter(checkin_at__lt=end + timedelta(days=1))
        except ValueError:
            pass
    if status: qs = qs.filter(status=status)
    if assignee: qs = qs.filter(assigned_to_id=assignee)
    if q:
        qs = qs.filter(
            Q(folio__icontains=q) |
            Q(device__customer__name__icontains=q) |
            Q(device__brand__icontains=q) |
            Q(device__model__icontains=q) |
            Q(device__serial__icontains=q)
        )

    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = "attachment; filename=ordenes.csv"
    w = csv.writer(resp)
    w.writerow(["ID","Folio","Estado","Cliente","Telefono","Correo","Tecnico","Ingreso","Entrega","TotalAprobado","Pagado","Saldo","Cotizacion","Notas","URLPublico"])
    for o in qs:
        cliente_obj = o.device.customer if o.device_id else None
        telefono = getattr(cliente_obj, "phone", "")
        correo = getattr(cliente_obj, "email", "")
        tecnico = o.assigned_to.get_username() if o.assigned_to_id else ""
        ingreso = timezone.localtime(o.checkin_at).strftime("%Y-%m-%d %H:%M") if getattr(o, "checkin_at", None) else ""
        entrega = timezone.localtime(o.checkout_at).strftime("%Y-%m-%d %H:%M") if getattr(o, "checkout_at", None) else ""
        total_aprobado = o.approved_total.quantize(TWO_PLACES) if hasattr(o.approved_total, "quantize") else Decimal(o.approved_total or 0).quantize(TWO_PLACES)
        total_pagado = o.paid_total.quantize(TWO_PLACES) if hasattr(o.paid_total, "quantize") else Decimal(o.paid_total or 0).quantize(TWO_PLACES)
        saldo = o.balance.quantize(TWO_PLACES) if hasattr(o.balance, "quantize") else Decimal(o.balance or 0).quantize(TWO_PLACES)
        try:
            estimate = o.estimate
        except Estimate.DoesNotExist:
            estimate = None
        cotizacion = _estimate_status_label(estimate) if estimate else "Sin cotizacion"
        notas = (o.notes or "").replace("\r", " ").replace("\n", " ").strip()
        public_url = request.build_absolute_uri(reverse("public_status", args=[o.token]))
        cliente = cliente_obj.name if cliente_obj else ""
        w.writerow([o.id, o.folio, o.get_status_display(), cliente, telefono, correo, tecnico, ingreso, entrega, f"{total_aprobado}", f"{total_pagado}", f"{saldo}", cotizacion, notas, public_url])
    return resp


@login_required(login_url="/admin/login/")
@roles_required(ROLE_GERENCIA)
def export_payments_csv(request):
    fmt = "%Y-%m-%d"
    get = request.GET.get
    dfrom = (get("from") or get("desde") or "").strip()
    dto = (get("to") or get("hasta") or "").strip()
    status = (get("status") or "").strip()
    assignee = (get("assignee") or "").strip()
    query = (get("q") or "").strip()

    payments = (
        Payment.objects.select_related("order", "order__device__customer", "order__assigned_to", "author")
        .order_by("-created_at")
    )

    tz = timezone.get_current_timezone()
    if dfrom:
        try:
            start = datetime.strptime(dfrom, fmt)
            if settings.USE_TZ and timezone.is_naive(start):
                start = tz.localize(start)
            payments = payments.filter(created_at__gte=start)
        except ValueError:
            pass
    if dto:
        try:
            end = datetime.strptime(dto, fmt)
            if settings.USE_TZ and timezone.is_naive(end):
                end = tz.localize(end)
            payments = payments.filter(created_at__lt=end + timedelta(days=1))
        except ValueError:
            pass
    if status:
        payments = payments.filter(order__status=status)
    if assignee:
        payments = payments.filter(order__assigned_to_id=assignee)
    if query:
        payments = payments.filter(
            Q(order__folio__icontains=query)
            | Q(order__device__customer__name__icontains=query)
            | Q(order__device__brand__icontains=query)
            | Q(order__device__model__icontains=query)
            | Q(order__device__serial__icontains=query)
        )

    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = "attachment; filename=pagos.csv"
    w = csv.writer(resp)
    w.writerow(["PagoID", "OrdenID", "Folio", "Cliente", "Monto", "Metodo", "Referencia", "Autor", "Fecha", "EstadoOrden", "Tecnico"])

    for p in payments:
        order = p.order
        customer = order.device.customer if order and order.device_id else None
        amount = p.amount.quantize(TWO_PLACES) if hasattr(p.amount, "quantize") else Decimal(p.amount or 0).quantize(TWO_PLACES)
        fecha = timezone.localtime(p.created_at).strftime("%Y-%m-%d %H:%M") if p.created_at else ""
        w.writerow([
            p.id,
            order.id if order else "",
            order.folio if order else "",
            customer.name if customer else "",
            f"{amount}",
            p.method,
            p.reference,
            p.author.get_username() if p.author_id else "",
            fecha,
            order.get_status_display() if order else "",
            order.assigned_to.get_username() if order and order.assigned_to_id else "",
        ])
    return resp

# === Inventory CSV export (robust) ===
@login_required(login_url="/admin/login/")
@roles_required(ROLE_GERENCIA)
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



