from django.contrib import admin
from django.utils.html import format_html
from django.core.mail import send_mail
from django.conf import settings

from .models import (
    Customer, Device, ServiceOrder, StatusHistory,
    InventoryItem, InventoryMovement, Notification
)

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

class StatusHistoryInline(admin.TabularInline):
    model = StatusHistory
    extra = 0
    readonly_fields = ("status", "author", "created_at")

@admin.register(ServiceOrder)
class ServiceOrderAdmin(admin.ModelAdmin):
    list_display = ("folio", "device", "status", "checkin_at", "public_link")
    list_filter = ("status", "checkin_at")
    search_fields = ("folio", "device__serial", "device__customer__name")
    inlines = [StatusHistoryInline]
    readonly_fields = ("token",)

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
                        subject = f"Tu equipo está listo para recoger — Folio {obj.folio}"
                        body = (
                            f"Hola {customer.name},\n\n"
                            f"Tu equipo está LISTO PARA RECOGER.\n"
                            f"Folio: {obj.folio}\n"
                            f"Consulta el estado: http://127.0.0.1:8000/t/{obj.token}/\n"
                        )
                        kind = "ready"
                    else:
                        subject = f"Requiere autorización de repuestos — Folio {obj.folio}"
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
