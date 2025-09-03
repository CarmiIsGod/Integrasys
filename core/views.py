from django.http import HttpResponse, Http404
from django.shortcuts import render, redirect
from django.urls import reverse
from django.contrib.auth.decorators import login_required, user_passes_test
from django import forms

from io import BytesIO
import qrcode


from .models import Customer, Device, ServiceOrder, StatusHistory



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
    img.save(buf, format="PNG")
    return HttpResponse(buf.getvalue(), content_type="image/png")



class ReceptionForm(forms.Form):
    customer_name = forms.CharField(label="Nombre", max_length=100)
    customer_phone = forms.CharField(label="Teléfono", max_length=30, required=False)
    customer_email = forms.EmailField(label="Email", required=False)
    brand = forms.CharField(label="Marca", max_length=50)
    model = forms.CharField(label="Modelo", max_length=50)
    serial = forms.CharField(label="Serie", max_length=100, required=False)
    notes = forms.CharField(label="Descripción / Falla", widget=forms.Textarea, required=False)


def staff_required(user):
    return user.is_staff


@login_required(login_url="/admin/login/")
@user_passes_test(staff_required)
def reception_new_order(request):
    if request.method == "POST":
        form = ReceptionForm(request.POST)
        if form.is_valid():
            
            c, _ = Customer.objects.get_or_create(
                email=form.cleaned_data["customer_email"] or None,
                defaults={
                    "name": form.cleaned_data["customer_name"],
                    "phone": form.cleaned_data["customer_phone"],
                },
            )
            if not c.name:
                c.name = form.cleaned_data["customer_name"]
            if form.cleaned_data["customer_phone"] and not c.phone:
                c.phone = form.cleaned_data["customer_phone"]
            c.save()

            
            d, _ = Device.objects.get_or_create(
                customer=c,
                serial=form.cleaned_data["serial"] or "",
                defaults={
                    "brand": form.cleaned_data["brand"],
                    "model": form.cleaned_data["model"],
                },
            )
            if not d.brand:
                d.brand = form.cleaned_data["brand"]
            if not d.model:
                d.model = form.cleaned_data["model"]
            d.save()

            
            order_kwargs = {"device": d, "notes": form.cleaned_data["notes"]}
            if hasattr(ServiceOrder.Status, "RECEIVED"):
                order_kwargs["status"] = ServiceOrder.Status.RECEIVED
            so = ServiceOrder.objects.create(**order_kwargs)

            
            try:
                StatusHistory.objects.create(order=so, status=so.status, author=request.user)
            except Exception:
                pass

            
            return redirect("public_status", token=so.token)
    else:
        form = ReceptionForm()

    return render(request, "reception_new_order.html", {"form": form})
