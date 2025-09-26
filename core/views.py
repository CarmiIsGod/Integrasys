from django.http import HttpResponse, Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib.auth.decorators import login_required, user_passes_test
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
from urllib.parse import quote, urlencode

from .forms import ReceptionForm
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


def staff_required(user):
    return user.is_staff

def user_in_group(user, group_name):
    if not getattr(user, 'is_authenticated', False):
        return False
    return user.groups.filter(name=group_name).exists()


@login_required(login_url="/admin/login/")
@user_passes_test(staff_required)
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
@user_passes_test(staff_required)
def inventory_list(request):
    q = (request.GET.get("q", "") or "").strip()
    low = request.GET.get("low") == "1"
    items = InventoryItem.objects.all().order_by("sku")
    if q:
        search = Q(sku__icontains=q) | Q(name__icontains=q) | Q(location__icontains=q)
        items = items.filter(search)
    if low:
        items = items.filter(qty__lt=models.F("min_qty"))
    return render(request, "inventory_list.html", {"items": items, "q": q, "low": low})


@login_required(login_url="/admin/login/")
@user_passes_test(staff_required)
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
@user_passes_test(staff_required)
def list_orders(request):
    q = (request.GET.get("q", "") or "").strip()
    status = (request.GET.get("status", "") or "").strip()
    dfrom = (request.GET.get("from", "") or "").strip()
    dto   = (request.GET.get("to", "") or "").strip()
    export = request.GET.get("export") == "1"
    assignee = (request.GET.get("assignee", "") or "").strip()

    is_superuser = request.user.is_superuser
    is_technician = user_in_group(request.user, "Tecnico") and not is_superuser
    is_reception = user_in_group(request.user, "Recepcion")

    can_create = is_superuser or is_reception
    can_assign = is_superuser or is_reception
    can_send_estimate = is_superuser or is_reception

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
        "is_technician": is_technician,
        "is_superuser": is_superuser,
    }
    return render(request, "reception_orders.html", context)

@login_required(login_url="/admin/login/")
@user_passes_test(staff_required)
def order_detail(request, pk):
    order = get_object_or_404(
        ServiceOrder.objects.select_related("device","device__customer"),
        pk=pk,
    )
    history = order.history.order_by("-created_at")
    parts = InventoryMovement.objects.filter(order=order).select_related("item").order_by("-created_at")
    allowed_next = ALLOWED.get(order.status, [])

    is_superuser = request.user.is_superuser
    is_technician = user_in_group(request.user, "Tecnico") and not is_superuser
    is_reception = user_in_group(request.user, "Recepcion")

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

    can_create = is_superuser or is_reception
    can_assign = is_superuser or is_reception
    can_send_estimate = is_superuser or is_reception

    payments = order.payments.select_related("author")
    paid_total = order.paid_total
    balance = order.balance
    can_charge = (is_superuser or is_reception) and balance > Decimal("0.00")

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
            "paid_total": paid_total,
            "balance": balance,
            "can_charge": can_charge,
            "is_technician": is_technician,
            "is_superuser": is_superuser,
        },
    )

@login_required(login_url="/admin/login/")
@user_passes_test(staff_required)
@require_POST
def add_payment(request, pk):
    order = get_object_or_404(ServiceOrder, pk=pk)

    if not request.user.is_superuser and not user_in_group(request.user, "Recepcion"):
        messages.error(request, "No tienes permisos para registrar pagos.")
        return redirect("order_detail", pk=pk)

    raw_amount = (request.POST.get("amount") or "").strip()

    try:
        amount = Decimal(raw_amount)
    except (InvalidOperation, TypeError):
        messages.error(request, "Monto invalido.")
        return redirect("order_detail", pk=pk)

    if amount <= Decimal("0.00"):
        messages.error(request, "El monto debe ser mayor a cero.")
        return redirect("order_detail", pk=pk)

    balance = order.balance
    if balance <= Decimal("0.00"):
        messages.error(request, "La orden no tiene saldo por cobrar.")
        return redirect("order_detail", pk=pk)

    if amount > balance:
        messages.error(request, "El monto excede el saldo por cobrar.")
        return redirect("order_detail", pk=pk)

    method = (request.POST.get("method") or "").strip()
    reference = (request.POST.get("reference") or "").strip()

    payment = Payment.objects.create(
        order=order,
        amount=amount,
        method=method,
        reference=reference,
        author=request.user,
    )

    customer = order.device.customer
    customer_email = (getattr(customer, "email", "") or "").strip()
    if customer_email:
        receipt_url = request.build_absolute_uri(reverse("payment_receipt_pdf", args=[payment.id]))
        sent = send_mail(
            subject=f"Pago registrado {order.folio}",
            message=f"Gracias. Recibo PDF: {receipt_url}",
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[customer_email],
            fail_silently=True,
        )
        ok = sent > 0
        payload = {
            "to": customer_email,
            "payment_id": payment.id,
            "order": order.folio,
            "receipt_url": receipt_url,
            "sent": sent,
        }
        if not ok:
            payload["error"] = "Email no enviado"
        Notification.objects.create(order=order, kind="email", channel="payment", ok=ok, payload=payload)

    amount_display = format(amount, ".2f")
    messages.success(
        request,
        f"Pago registrado por ${amount_display}.",
    )
    return redirect("order_detail", pk=pk)


@login_required(login_url="/admin/login/")
@user_passes_test(staff_required)
@require_POST
def change_status(request, pk):
    order = get_object_or_404(ServiceOrder, pk=pk)
    target = request.POST.get("target")
    allowed = ALLOWED.get(order.status, [])

    if target not in allowed:
        messages.error(request, "Transicion de estado invalida.")
        return redirect("list_orders")

    if user_in_group(request.user, "Tecnico") and not request.user.is_superuser:
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
            messages.error(request, "No puedes marcar como Entregado: hay saldo pendiente.")
            return redirect("order_detail", pk=order.pk)

    order.status = target
    order.save()

    StatusHistory.objects.create(order=order, status=target, author=request.user)

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
            Notification.objects.create(order=order, kind="email", channel="status", ok=True,
                                        payload={"to": cust_email, "target": target})
        except Exception as exc:
            Notification.objects.create(order=order, kind="email", channel="status", ok=False,
                                        payload={"to": cust_email, "target": target, "error": str(exc)})

    messages.success(request, f"Estado actualizado a {order.get_status_display()}.")
    return redirect("list_orders")

@login_required(login_url="/admin/login/")
@user_passes_test(staff_required)
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
@user_passes_test(staff_required)
@require_POST
def assign_tech(request, pk):
    order = get_object_or_404(ServiceOrder, pk=pk)
    uid = (request.POST.get("user_id") or "").strip()

    is_superuser = request.user.is_superuser
    is_technician = user_in_group(request.user, "Tecnico") and not is_superuser
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
@user_passes_test(staff_required)
@require_POST
def add_note(request, pk):
    order = get_object_or_404(ServiceOrder, pk=pk)

    if user_in_group(request.user, "Tecnico") and not request.user.is_superuser:
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
@user_passes_test(staff_required)
@require_POST
def add_part(request, pk):
    order = get_object_or_404(ServiceOrder, pk=pk)

    if user_in_group(request.user, "Tecnico") and not request.user.is_superuser:
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
@user_passes_test(staff_required)
def reception_new_order(request):
    if not (request.user.is_superuser or user_in_group(request.user, "Recepcion")):
        messages.error(request, "No tienes permiso para crear ordenes de servicio.")
        return redirect("list_orders")

    if request.method == "POST":
        form = ReceptionForm(request.POST)
        if form.is_valid():
            name = form.cleaned_data["customer_name"].strip()
            phone = (form.cleaned_data.get("customer_phone") or "").strip()
            email = (form.cleaned_data.get("customer_email") or "").strip()
            brand = (form.cleaned_data.get("brand") or "").strip()
            model = (form.cleaned_data.get("model") or "").strip()
            serial = (form.cleaned_data.get("serial") or "").strip()
            notes = form.cleaned_data.get("notes") or ""

            customer = None
            if email:
                customer = Customer.objects.filter(email=email).first()
            if customer is None and phone:
                customer = Customer.objects.filter(phone=phone).first()
            if customer is None:
                customer = Customer.objects.create(
                    name=name, phone=phone or "", email=email or ""
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
            if notes and (device.notes or "") == "":
                device.notes = notes
                changed = True
            if changed:
                device.save()

            order = ServiceOrder.objects.create(
                device=device, notes=notes, assigned_to=request.user
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
                        order=order, kind="email", channel="create", ok=True,
                        payload={"to": email}
                    )
                except Exception as exc:
                    Notification.objects.create(
                        order=order, kind="email", channel="create", ok=False,
                        payload={"to": email, "error": str(exc)}
                    )

            return redirect("public_status", token=order.token)
    else:
        form = ReceptionForm()

    return render(request, "reception_new_order.html", {"form": form})
@login_required(login_url="/admin/login/")
@user_passes_test(staff_required)
def estimate_edit(request, pk):
    order = get_object_or_404(ServiceOrder, pk=pk)
    estimate, _ = Estimate.objects.get_or_create(order=order)

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
            tax = (subtotal * IVA_RATE).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
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
@user_passes_test(staff_required)
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
        payload={"to": email, "estimate": str(estimate.token), "url": public_url},
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
    if customer_email:
        total_display = format(
            (estimate.total or Decimal("0.00")).quantize(TWO_PLACES, rounding=ROUND_HALF_UP),
            ".2f",
        )
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

    return redirect("estimate_public", token=token)
