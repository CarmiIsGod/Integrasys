from datetime import datetime, timedelta, time
import csv

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import StreamingHttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from .models import Payment, ServiceOrder
from .permissions import require_manager
from .utils import build_device_label, build_single_device_label


DATE_INPUT_FMT = "%Y-%m-%d"


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, DATE_INPUT_FMT).date()
    except (TypeError, ValueError):
        return None


def _build_range(start_date, end_date):
    tz = timezone.get_current_timezone()
    start_dt = datetime.combine(start_date, time.min)
    end_dt = datetime.combine(end_date + timedelta(days=1), time.min)
    if settings.USE_TZ:
        if timezone.is_naive(start_dt):
            start_dt = timezone.make_aware(start_dt, tz)
        if timezone.is_naive(end_dt):
            end_dt = timezone.make_aware(end_dt, tz)
    return start_dt, end_dt


def _render_form(request, *, errors=None, initial=None, status=200):
    start = request.GET.get("start", "")
    end = request.GET.get("end", "")
    if initial:
        start = initial.get("start", start)
        end = initial.get("end", end)
    context = {
        "errors": errors or [],
        "start": start,
        "end": end,
        "orders_url": reverse("panel_export_orders"),
        "payments_url": reverse("panel_export_payments"),
    }
    return render(request, "panel/exports.html", context, status=status)


class Echo:
    """Minimal write-only buffer for csv.writer used in streaming responses."""

    def write(self, value):
        return value


def _stream_csv(rows, *, filename):
    pseudo_buffer = Echo()
    writer = csv.writer(pseudo_buffer)

    def stream():
        yield "\ufeff"
        for row in rows:
            yield writer.writerow(row)

    response = StreamingHttpResponse(stream(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required(login_url="/admin/login/")
@require_manager
def exports_home(request):
    default_end = timezone.localdate()
    default_start = default_end - timedelta(days=7)
    initial = {"start": default_start.isoformat(), "end": default_end.isoformat()}
    return _render_form(request, initial=initial)


@login_required(login_url="/admin/login/")
@require_manager
def export_orders_csv(request):
    start = _parse_date(request.GET.get("start"))
    end = _parse_date(request.GET.get("end"))
    if not start or not end:
        errors = ["Debes indicar fecha inicial y final en formato YYYY-MM-DD."]
        return _render_form(request, errors=errors, status=400)
    if start > end:
        errors = ["La fecha inicial no puede ser posterior a la final."]
        return _render_form(request, errors=errors, status=400)

    start_dt, end_dt = _build_range(start, end)
    orders = (
        ServiceOrder.objects.select_related("customer", "assigned_to")
        .prefetch_related("devices")
        .filter(checkin_at__gte=start_dt, checkin_at__lt=end_dt)
        .order_by("checkin_at")
    )

    def iter_rows():
        yield [
            "OrdenID",
            "Folio",
            "Estado",
            "Cliente",
            "Ingreso",
            "Entrega",
            "Tecnico",
            "Total Aprobado",
            "Pagado",
            "Saldo",
            "Link Publico",
            "Equipos",
        ]
        for order in orders:
            customer = order.get_customer()
            ingreso = timezone.localtime(order.checkin_at) if order.checkin_at else None
            entrega = timezone.localtime(order.checkout_at) if order.checkout_at else None
            yield [
                order.id,
                order.folio,
                order.get_status_display(),
                customer.name if customer else "",
                ingreso.strftime("%Y-%m-%d %H:%M") if ingreso else "",
                entrega.strftime("%Y-%m-%d %H:%M") if entrega else "",
                order.assigned_to.get_username() if order.assigned_to_id else "",
                f"{order.approved_total}",
                f"{order.paid_total}",
                f"{order.balance}",
                request.build_absolute_uri(reverse("public_status", args=[order.token])),
                build_device_label(order),
            ]

    filename = f"ordenes_{start.isoformat()}_{end.isoformat()}.csv"
    return _stream_csv(iter_rows(), filename=filename)


@login_required(login_url="/admin/login/")
@require_manager
def export_payments_csv(request):
    start = _parse_date(request.GET.get("start"))
    end = _parse_date(request.GET.get("end"))
    if not start or not end:
        errors = ["Debes indicar fecha inicial y final en formato YYYY-MM-DD."]
        return _render_form(request, errors=errors, status=400)
    if start > end:
        errors = ["La fecha inicial no puede ser posterior a la final."]
        return _render_form(request, errors=errors, status=400)

    start_dt, end_dt = _build_range(start, end)
    payments = (
        Payment.objects.select_related("order__customer", "author", "device")
        .prefetch_related("order__devices")
        .filter(created_at__gte=start_dt, created_at__lt=end_dt)
        .order_by("created_at")
    )

    def iter_rows():
        yield [
            "PagoID",
            "OrdenID",
            "Folio",
            "Cliente",
            "Monto",
            "Metodo",
            "Referencia",
            "Autor",
            "Fecha",
            "Estado Orden",
            "Dispositivo",
        ]
        for payment in payments:
            order = payment.order
            customer = order.get_customer() if order else None
            fecha = timezone.localtime(payment.created_at) if payment.created_at else None
            yield [
                payment.id,
                order.id if order else "",
                order.folio if order else "",
                customer.name if customer else "",
                f"{payment.amount}",
                payment.method,
                payment.reference,
                payment.author.get_username() if payment.author_id else "",
                fecha.strftime("%Y-%m-%d %H:%M") if fecha else "",
                order.get_status_display() if order else "",
                build_single_device_label(payment.device) if payment.device_id else "",
            ]

    filename = f"pagos_{start.isoformat()}_{end.isoformat()}.csv"
    return _stream_csv(iter_rows(), filename=filename)
