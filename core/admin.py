# -*- coding: utf-8 -*-
from django.contrib import admin
from django.utils.html import format_html
from django.core.mail import send_mail
from django.conf import settings

import csv
from django.http import HttpResponse
from django.utils import timezone

from .models import (
    Customer, Device, ServiceOrder, StatusHistory,
    InventoryItem, InventoryMovement, Notification,
    Attachment,
)

admin.site.site_header = "Integrasys - Administración"
admin.site.site_title  = "Integrasys Admin"
admin.site.index_title = "Panel de Recepción y Reparaciones"




@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "email")
    search_fields = ("name", "phone", "email")

@admin.register(Attachment)
class AttachmentAdmin(admin.ModelAdmin):
    list_display = ("id", "order", "kind", "is_public", "uploaded_by", "created_at")
    list_filter = ("kind", "is_public", "created_at")
    search_fields = ("order__folio", "file", "uploaded_by__username")
    readonly_fields = ("created_at",)

@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ("customer", "brand", "model", "serial")
    search_fields = ("serial", "customer__name", "brand", "model")

class StatusHistoryInline(admin.TabularInline):
    model = StatusHistory
    extra = 0
    readonly_fields = ("status", "author", "created_at")


def export_orders_csv(modeladmin, request, queryset):
    """
    Exporta a CSV las órdenes seleccionadas desde el admin.
    No depende de campos exactos; usa getattr para que no truene si cambia un nombre.
    """
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="service_orders.csv"'
    w = csv.writer(response)

    w.writerow(["Folio", "Cliente", "Equipo", "Estado", "Fecha ingreso", "Token"])


    for o in queryset.select_related("device__customer"):
        folio = getattr(o, "folio", o.pk)
        cliente = getattr(getattr(o, "device", None), "customer", None)
        cliente_name = getattr(cliente, "name", "") if cliente else ""

        device = getattr(o, "device", None)
        brand = getattr(device, "brand", "")
        model = getattr(device, "model", "")
        serial = getattr(device, "serial", "")
        equipo = f"{brand} {model}".strip()
        if serial:
            equipo = f"{equipo} ({serial})".strip()

        estado = str(getattr(o, "status", ""))
        checkin = getattr(o, "checkin_at", None)
        token = getattr(o, "token", "")

        def fmt(dt):
            try:
                return timezone.localtime(dt).strftime("%Y-%m-%d %H:%M") if dt else ""
            except Exception:
                return str(dt) if dt else ""

        w.writerow([folio, cliente_name, equipo, estado, fmt(checkin), token])

    return response

export_orders_csv.short_description = "Exportar órdenes seleccionadas a CSV"




@admin.register(ServiceOrder)
class ServiceOrderAdmin(admin.ModelAdmin):
    list_display = ("folio","status","device","checkin_at","checkout_at","assigned_to")
    search_fields = ("folio","device__serial","device__model","device__customer__name")
    list_filter = ("status","checkin_at")
    inlines = [StatusHistoryInline]
    readonly_fields = ("folio","token","checkin_at","checkout_at")
    
    actions = [export_orders_csv]

    def public_link(self, obj):
        return format_html('<a href="/t/{}/" target="_blank">Ver público</a>', obj.token)
    public_link.short_description = "Link público"

    def save_model(self, request, obj, form, change):
        # Detecta cambio de estado
        old_status = None
        if change:
            old = ServiceOrder.objects.get(pk=obj.pk)
            old_status = old.status

        super().save_model(request, obj, form, change)

        # Guarda historial si es nueva o si cambió el estado
        if not change or (old_status != obj.status):
            StatusHistory.objects.create(order=obj, status=obj.status, author=request.user)

            # Notificaciones SOLO para 2 estados clave
            if obj.status in (
                ServiceOrder.Status.READY_PICKUP,
                ServiceOrder.Status.REQUIRES_AUTH,
            ):
                customer = obj.device.customer
                if customer.email:
                    if obj.status == ServiceOrder.Status.READY_PICKUP:
                        subject = f"Tu equipo está listo para recoger - Folio {obj.folio}"
                        body = (
                            f"Hola {customer.name},\n\n"
                            f"Tu equipo está LISTO PARA RECOGER.\n"
                            f"Folio: {obj.folio}\n"
                            f"Consulta el estado: http://127.0.0.1:8000/t/{obj.token}/\n"
                        )
                        kind = "ready"
                    else:
                        subject = f"Requiere autorización de repuestos - Folio {obj.folio}"
                        body = (
                            f"Hola {customer.name},\n\n"
                            f"Tu orden REQUIERE AUTORIZACIÓN DE REPUESTOS.\n"
                            f"Folio: {obj.folio}\n"
                            f"Revisa detalles: http://127.0.0.1:8000/t/{obj.token}/\n"
                        )
                        kind = "requires_auth"

                    try:
                        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [customer.email], fail_silently=False)
                        Notification.objects.create(order=obj, kind=kind, channel="email", ok=True)
                    except Exception as e:
                        Notification.objects.create(order=obj, kind=kind, channel="email", ok=False, payload={"error": str(e)})

admin.site.register([InventoryItem, InventoryMovement, Notification])

