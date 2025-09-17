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
from xhtml2pdf import pisa
import base64
from io import BytesIO
import qrcode
from datetime import datetime, timedelta
import csv

from .forms import ReceptionForm
from .models import Customer, Device, ServiceOrder, StatusHistory


# Transiciones permitidas por código de estado
ALLOWED = {
    "NEW": ["REV"],
    "REV": ["WAI", "AUTH", "READY"],
    "WAI": ["REV", "READY"],
    "AUTH": ["REV", "READY"],
    "READY": ["DONE"],
    "DONE": [],
}


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

    # QR → base64 (si algo falla, lo dejamos vacío y no reventamos)
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
            # Si xhtml2pdf no pudo convertir, devolvemos el HTML para ver qué falló
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
    """Listado interno con búsqueda y paginación."""
    query = (request.GET.get("q", "") or "").strip()
    qs = ServiceOrder.objects.select_related(
        "device", "device__customer"
    ).order_by("-checkin_at")

    if query:
        qs = qs.filter(
            Q(folio__icontains=query)
            | Q(device__serial__icontains=query)
            | Q(device__model__icontains=query)
            | Q(device__customer__name__icontains=query)
        )

    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(request.GET.get("page"))
    return render(
        request,
        "reception_orders.html",
        {"page_obj": page_obj, "query": query, "ALLOWED": ALLOWED},
    )


@login_required(login_url="/admin/login/")
@user_passes_test(staff_required)
@require_POST
def change_status(request, pk):
    """Cambia el estado de una orden (validando transiciones) y registra historial."""
    order = get_object_or_404(ServiceOrder, pk=pk)
    target = request.POST.get("target")
    allowed = ALLOWED.get(order.status, [])

    if target not in allowed:
        messages.error(request, "Transición de estado inválida.")
        return redirect("list_orders")

    order.status = target
    order.save()  # ServiceOrder.save ya llena checkout_at si pasa a DONE
    StatusHistory.objects.create(order=order, status=target, author=request.user)
    messages.success(request, f"Estado actualizado a {order.get_status_display()}.")
    return redirect("list_orders")


@login_required(login_url="/admin/login/")
@user_passes_test(staff_required)
def reception_new_order(request):
    """Recepción: crea/reutiliza cliente y equipo, abre orden y redirige al estado público."""
    if request.method == "POST":
        form = ReceptionForm(request.POST)
        if form.is_valid():
            # Normaliza entradas (evitar None)
            name = form.cleaned_data["customer_name"].strip()
            phone = (form.cleaned_data.get("customer_phone") or "").strip()
            email = (form.cleaned_data.get("customer_email") or "").strip()
            brand = (form.cleaned_data.get("brand") or "").strip()
            model = (form.cleaned_data.get("model") or "").strip()
            serial = (form.cleaned_data.get("serial") or "").strip()
            notes = form.cleaned_data.get("notes") or ""

            # Cliente: reutiliza por email, luego phone; nunca uses None
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
                # Completa datos faltantes si aplica
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

            # Dispositivo: crea o reutiliza por serial con defaults
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

            # Orden + historial inicial
            order = ServiceOrder.objects.create(
                device=device, notes=notes, assigned_to=request.user
            )
            # Email opcional al crear la orden
            if email:
                from .models import Notification
                public_url = request.build_absolute_uri(reverse("public_status", args=[order.token]))
                try:
                    send_mail(
                        subject=f"Orden {order.folio}",
                        message=f"Consulta tu orden: {public_url}",
                        from_email=None,
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
                except Exception:
                    pass
            StatusHistory.objects.create(
                order=order, status=order.status, author=request.user
            )

            return redirect("public_status", token=order.token)
    else:
        form = ReceptionForm()

    return render(request, "reception_new_order.html", {"form": form})

