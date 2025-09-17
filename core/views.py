from django.http import HttpResponse, Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.contrib import messages
from django.db.models import Q
from django.views.decorators.http import require_POST
from django.template.loader import render_to_string
from django.core.mail import send_mail
from django.utils import timezone
from django.conf import settings
from django.contrib.auth.models import User

from xhtml2pdf import pisa
import base64
from io import BytesIO
import qrcode
from datetime import datetime, timedelta
import csv
import re
from urllib.parse import quote

from .forms import ReceptionForm
from .models import (
    Customer,
    Device,
    ServiceOrder,
    StatusHistory,
    Notification,
    InventoryItem,
    InventoryMovement,
)


ALLOWED = {
    "NEW": ["REV"],
    "REV": ["WAI", "AUTH", "READY"],
    "WAI": ["REV", "READY"],
    "AUTH": ["REV", "READY"],
    "READY": ["DONE"],
    "DONE": [],
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


@login_required(login_url="/admin/login/")
@user_passes_test(staff_required)
def list_orders(request):
    q = (request.GET.get("q", "") or "").strip()
    status = (request.GET.get("status", "") or "").strip()
    dfrom = (request.GET.get("from", "") or "").strip()
    dto   = (request.GET.get("to", "") or "").strip()
    export = request.GET.get("export") == "1"
    assignee = (request.GET.get("assignee", "") or "").strip()

    qs = ServiceOrder.objects.select_related("device","device__customer").order_by("-checkin_at")

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
        qs = qs.filter(assigned_to_id=assignee)

    tz = timezone.get_current_timezone()
    if dfrom:
        try:
            start = tz.localize(datetime.strptime(dfrom, "%Y-%m-%d"))
            qs = qs.filter(checkin_at__gte=start)
        except ValueError:
            pass
    if dto:
        try:
            end = tz.localize(datetime.strptime(dto, "%Y-%m-%d")) + timedelta(days=1)
            qs = qs.filter(checkin_at__lt=end)
        except ValueError:
            pass

    if export:
        resp = HttpResponse(content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = 'attachment; filename="ordenes.csv"'
        w = csv.writer(resp)
        w.writerow(["Folio","Estado","Cliente","Equipo","Serie","Check-in","Check-out","Link público"])
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
                public_url,
            ])
        return resp

    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    for o in page_obj.object_list:
        o.allowed_next = ALLOWED.get(o.status, [])
        public_url = request.build_absolute_uri(reverse("public_status", args=[o.token]))
        if o.status == "READY":
            base = f"Hola {o.device.customer.name}, tu orden {o.folio} está LISTA para recoger."
        elif o.status == "DONE":
            base = f"Hola {o.device.customer.name}, tu orden {o.folio} fue ENTREGADA."
        else:
            base = f"Hola {o.device.customer.name}, tu orden {o.folio} está {o.get_status_display()}."
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

    public_url = request.build_absolute_uri(reverse("public_status", args=[order.token]))
    if order.status == "READY":
        base = f"Hola {order.device.customer.name}, tu orden {order.folio} está LISTA para recoger."
    elif order.status == "DONE":
        base = f"Hola {order.device.customer.name}, tu orden {order.folio} fue ENTREGADA."
    else:
        base = f"Hola {order.device.customer.name}, tu orden {order.folio} está {order.get_status_display()}."
    msg = f"{base} Detalle: {public_url}"
    wa_link = build_whatsapp_link(getattr(order.device.customer, "phone", ""), msg)

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
        },
    )


@login_required(login_url="/admin/login/")
@user_passes_test(staff_required)
@require_POST
def change_status(request, pk):
    order = get_object_or_404(ServiceOrder, pk=pk)
    target = request.POST.get("target")
    allowed = ALLOWED.get(order.status, [])

    if target not in allowed:
        messages.error(request, "Transición de estado inválida.")
        return redirect("list_orders")

    if target == "DONE" and not request.user.is_superuser:
        messages.error(request, "Solo un superusuario puede marcar como Entregado.")
        return redirect("list_orders")

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
            f"Consulta el detalle aquí: {public_url}\n\n"
            f"Gracias.\n"
        )
        try:
            send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [cust_email], fail_silently=False)
            Notification.objects.create(order=order, kind="email", channel="status", ok=True,
                                        payload={"to": cust_email, "target": target})
        except Exception as e:
            Notification.objects.create(order=order, kind="email", channel="status", ok=False,
                                        payload={"to": cust_email, "target": target, "error": str(e)})

    messages.success(request, f"Estado actualizado a {order.get_status_display()}.")
    return redirect("list_orders")


@login_required(login_url="/admin/login/")
@user_passes_test(staff_required)
@require_POST
def assign_tech(request, pk):
    order = get_object_or_404(ServiceOrder, pk=pk)
    uid = (request.POST.get("user_id") or "").strip()
    user = User.objects.filter(pk=uid, is_staff=True).first()
    if not user:
        messages.error(request, "Selecciona un técnico válido.")
        return redirect("order_detail", pk=order.pk)
    order.assigned_to = user
    order.save()
    if user.email:
        try:
            public_url = request.build_absolute_uri(reverse("order_detail", args=[order.pk]))
            send_mail(
                subject=f"Se te asignó la orden {order.folio}",
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
    text = (request.POST.get("note") or "").strip()
    if not text:
        messages.error(request, "Escribe una nota.")
        return redirect("order_detail", pk=order.pk)
    stamp = timezone.localtime().strftime("%Y-%m-%d %H:%M")
    author = request.user.get_username()
    order.notes = (order.notes or "") + ("" if not order.notes else "\n") + f"[{stamp}] {author}: {text}"
    order.save()
    messages.success(request, "Nota agregada.")
    return redirect("order_detail", pk=order.pk)


@login_required(login_url="/admin/login/")
@user_passes_test(staff_required)
@require_POST
def add_part(request, pk):
    order = get_object_or_404(ServiceOrder, pk=pk)
    sku = (request.POST.get("sku") or "").strip()
    qty_raw = (request.POST.get("qty") or "").strip()
    reason = (request.POST.get("reason") or "").strip()

    if not sku:
        messages.error(request, "Captura el SKU.")
        return redirect("list_orders")
    try:
        qty = int(qty_raw)
    except ValueError:
        messages.error(request, "Cantidad inválida.")
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

    messages.success(request, f"Se usó {qty} x {item.name} (SKU {item.sku}) en {order.folio}.")
    return redirect("list_orders")


@login_required(login_url="/admin/login/")
@user_passes_test(staff_required)
def reception_new_order(request):
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
                        message=f"Gracias. Consulta tu orden aquí: {public_url}",
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        recipient_list=[email],
                        fail_silently=False,
                    )
                    Notification.objects.create(
                        order=order, kind="email", channel="create", ok=True,
                        payload={"to": email}
                    )
                except Exception as e:
                    Notification.objects.create(
                        order=order, kind="email", channel="create", ok=False,
                        payload={"to": email, "error": str(e)}
                    )

            return redirect("public_status", token=order.token)
    else:
        form = ReceptionForm()

    return render(request, "reception_new_order.html", {"form": form})
