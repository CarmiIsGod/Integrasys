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

from xhtml2pdf import pisa
import base64
from io import BytesIO
import qrcode
from datetime import datetime, timedelta
import csv

import re
from urllib.parse import quote


from .forms import ReceptionForm
from .models import Customer, Device, ServiceOrder, StatusHistory, Notification



ALLOWED = {
    "NEW": ["REV"],
    "REV": ["WAI", "AUTH", "READY"],
    "WAI": ["REV", "READY"],
    "AUTH": ["REV", "READY"],
    "READY": ["DONE"],
    "DONE": [],
}

def build_whatsapp_link(phone: str, text: str) -> str:
    """Devuelve un wa.me link al número (MX por default) con texto prellenado."""
    digits = re.sub(r"\D", "", phone or "")
    if not digits:
        return ""
    
    if not digits.startswith("52"):
        digits = "52" + digits
    return f"https://wa.me/{digits}?text={quote(text)}"


def public_status(request, token):
    """Página pública del estado de una orden (por token)."""
    try:
        order = ServiceOrder.objects.select_related(
            "device", "device__customer"
        ).get(token=token)
    except ServiceOrder.DoesNotExist:
        raise Http404("Orden no encontrada")
    history = order.history.order_by("-created_at")
    return render(request, "public_status.html", {"order": order, "history": history})


def qr(request, token):
    """PNG con el QR a la página pública de la orden (cacheado 1 día)."""
    url = request.build_absolute_uri(reverse("public_status", args=[token]))
    img = qrcode.make(url)
    buf = BytesIO()
    img.save(buf, format="PNG")
    resp = HttpResponse(buf.getvalue(), content_type="image/png")
    resp["Cache-Control"] = "public, max-age=86400"  # 1 día
    return resp


def receipt_pdf(request, token):
    """Genera el recibo PDF con QR y datos de la orden. Siempre devuelve HttpResponse."""
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
    """Listado interno con búsqueda, filtros por estado/fechas, export CSV y paginación."""
    q = (request.GET.get("q", "") or "").strip()
    status = (request.GET.get("status", "") or "").strip()
    dfrom = (request.GET.get("from", "") or "").strip()  
    dto = (request.GET.get("to", "") or "").strip()      
    export = request.GET.get("export") == "1"

    qs = ServiceOrder.objects.select_related("device", "device__customer").order_by("-checkin_at")

    if q:
        qs = qs.filter(
            Q(folio__icontains=q)
            | Q(device__serial__icontains=q)
            | Q(device__model__icontains=q)
            | Q(device__customer__name__icontains=q)
        )
    if status:
        qs = qs.filter(status=status)

    
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
        w.writerow(["Folio", "Estado", "Cliente", "Equipo", "Serie", "Check-in", "Check-out", "Link público"])
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
        msg = (
            f"Hola {o.device.customer.name}, tu orden {o.folio} "
            f"({o.device.brand} {o.device.model}) está {o.get_status_display()}. "
            f"Detalle: {public_url}"
        )
        o.whatsapp_link = build_whatsapp_link(getattr(o.device.customer, "phone", ""), msg)


    
    for o in page_obj.object_list:
        o.allowed_next = ALLOWED.get(o.status, [])

    context = {
        "page_obj": page_obj,
        "query": q,
        "status": status,
        "dfrom": dfrom,
        "dto": dto,
        "status_choices": ServiceOrder.Status.choices,
        "ALLOWED": ALLOWED,
    }
    return render(request, "reception_orders.html", context)


@login_required(login_url="/admin/login/")
@user_passes_test(staff_required)
@require_POST
def change_status(request, pk):
    """Cambia el estado (validando transiciones), registra historial y notifica por email (READY/DONE)."""
    order = get_object_or_404(ServiceOrder, pk=pk)
    target = request.POST.get("target")
    allowed = ALLOWED.get(order.status, [])

    if target not in allowed:
        messages.error(request, "Transición de estado inválida.")
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
            Notification.objects.create(
                order=order, kind="email", channel="status", ok=True,
                payload={"to": cust_email, "target": target}
            )
        except Exception as e:
            Notification.objects.create(
                order=order, kind="email", channel="status", ok=False,
                payload={"to": cust_email, "target": target, "error": str(e)}
            )

    messages.success(request, f"Estado actualizado a {order.get_status_display()}.")
    return redirect("list_orders")


@login_required(login_url="/admin/login/")
@user_passes_test(staff_required)
def reception_new_order(request):
    """Recepción: crea/reutiliza cliente y equipo, abre orden, manda correo (si hay) y redirige al estado público."""
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
                    customer.name = name; updated = True
                if not customer.phone and phone:
                    customer.phone = phone; updated = True
                if not customer.email and email:
                    customer.email = email; updated = True
                if updated:
                    customer.save()

            
            device, _ = Device.objects.get_or_create(
                customer=customer,
                serial=serial or "",
                defaults={"brand": brand, "model": model, "notes": notes},
            )
            changed = False
            if not device.brand and brand:
                device.brand = brand; changed = True
            if not device.model and model:
                device.model = model; changed = True
            if notes and (device.notes or "") == "":
                device.notes = notes; changed = True
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
