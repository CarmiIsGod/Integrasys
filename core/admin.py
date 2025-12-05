from django.contrib import admin
from django.contrib import messages
from django.utils.html import format_html
from django.urls import reverse

import csv
from django.http import HttpResponse

from .models import (
    Customer, Device, ServiceOrder, StatusHistory,
    InventoryItem, InventoryMovement, Notification
)
from .utils import build_device_label, log_status_snapshot, send_order_status_email, format_csv_datetime, resolve_actor_role

admin.site.site_header = "Integrasys - Administración"
admin.site.site_title  = "Integrasys Admin"
admin.site.index_title = "Panel de Recepción y Reparaciones"




@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "email")
    search_fields = ("name", "phone", "email")
    
@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ("customer", "brand", "model", "serial")
    search_fields = ("serial", "customer__name", "brand", "model")
    exclude = ("notes",)

class StatusHistoryInline(admin.TabularInline):
    model = StatusHistory
    extra = 0
    fields = ("from_status", "status", "author", "author_role", "note", "created_at")
    readonly_fields = ("from_status", "status", "author", "author_role", "note", "created_at")


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
            return format_csv_datetime(dt)

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
        target_status = obj.status
        status_changed = False
        if change:
            old = ServiceOrder.objects.get(pk=obj.pk)
            old_status = old.status
            status_changed = old_status != target_status

        if status_changed:
            ok, error = obj.validate_transition(target_status, user=request.user)
            if not ok:
                self.message_user(request, error or "Transicion de estado invalida.", level=messages.ERROR)
                obj.status = old_status
                super().save_model(request, obj, form, change)
                return
            obj.status = old_status

        super().save_model(request, obj, form, change)

        if status_changed:
            actor_role = resolve_actor_role(request.user)
            try:
                obj.transition_to(target_status, author=request.user, author_role=actor_role)
            except ValueError as exc:
                self.message_user(request, str(exc), level=messages.ERROR)
                return
        elif not change:
            log_status_snapshot(
                obj,
                author=request.user,
                previous_status=old_status or "",
                new_status=obj.status,
            )

        if (not change or status_changed) and obj.status in (
            ServiceOrder.Status.READY_PICKUP,
            ServiceOrder.Status.REQUIRES_AUTH,
        ):
            public_url = request.build_absolute_uri(reverse("public_status", args=[obj.token]))
            device_label = build_device_label(obj)
            extra_context = {}
            if obj.status == ServiceOrder.Status.REQUIRES_AUTH and hasattr(obj, "estimate"):
                estimate = obj.estimate
                extra_context["estimate_url"] = request.build_absolute_uri(
                    reverse("estimate_public", args=[estimate.token])
                )
                extra_context["has_items"] = estimate.items.exists()
            notification = Notification.objects.create(
                order=obj,
                kind="email",
                channel="admin_status",
                payload={
                    "order_folio": obj.folio,
                    "order": obj.folio,
                    "customer": getattr(getattr(obj.device, "customer", None), "name", ""),
                    "device": device_label,
                },
            )
            send_order_status_email(
                order=obj,
                notification=notification,
                status_code=obj.status,
                public_url=public_url,
                device_label=device_label,
                extra_context=extra_context,
            )
admin.site.register([InventoryItem, InventoryMovement, Notification])

from core.models import Attachment

# === INTEGRASYS PATCH: Attachment admin ===
@admin.register(Attachment)
class AttachmentAdmin(admin.ModelAdmin):
    list_display = ("id", "service_order", "file", "caption", "uploaded_at")
    search_fields = ("caption", "file")
    list_filter = ("uploaded_at",)
