from django.http import HttpResponse, Http404
from django.shortcuts import render
from .models import ServiceOrder
import qrcode
from io import BytesIO
from django.urls import reverse

def public_status(request, token):
    try:
        order = ServiceOrder.objects.select_related("device", "device__customer").get(token=token)
    except ServiceOrder.DoesNotExist:
        raise Http404("Orden no encontrada")
    return render(request, "public_status.html", {"order": order})

def qr(request, token):
    url = request.build_absolute_uri(reverse("public_status", kwargs={"token": token}))
    img = qrcode.make(url)
    buf = BytesIO()
    img.save(buf, format='PNG')
    return HttpResponse(buf.getvalue(), content_type="image/png")
