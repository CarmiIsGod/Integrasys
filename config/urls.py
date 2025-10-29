from django.contrib import admin
from django.urls import path, include, re_path  # INTEGRASYS, , re_path
from core import views as core_views  # INTEGRASYS, re_path, include
from django.views.generic import RedirectView   
from core import views
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path("recepcion/", core_views.reception_home, name="reception_home"),
    path("reportes/inventario.csv", core_views.export_inventory_csv, name="export_inventory_csv"),  # INTEGRASYS
    path("reportes/ordenes.csv", core_views.export_orders_csv, name="export_orders_csv"),  # INTEGRASYS
    path("reportes/pagos.csv", core_views.export_payments_csv, name="export_payments_csv"),
    path("recepcion/orden/<int:pk>/adjuntos/<int:att_id>/eliminar/", core_views.delete_attachment, name="delete_attachment"),  # INTEGRASYS
    # Redirige la raíz al panel de órdenes (puedes cambiar a "/admin/" si prefieres)
    path("", RedirectView.as_view(url="/recepcion/", permanent=False)),
    # (Opcional) que /accounts/login/ mande al login del admin
    path("accounts/login/", RedirectView.as_view(url="/admin/login/", permanent=False)),

    path("admin/", admin.site.urls),
    path("accounts/", include("django.contrib.auth.urls")),
    path("panel/", views.dashboard, name="dashboard"),

    path("t/<uuid:token>/", views.public_status, name="public_status"),
    path("t/<uuid:token>/qr.png", views.qr, name="qr"),
    path("t/<uuid:token>/recibo.pdf", views.receipt_pdf, name="receipt_pdf"),

    path("recepcion/nueva-orden/", views.reception_new_order, name="reception_new_order"),
    path("recepcion/ordenes/", views.list_orders, name="list_orders"),
    path("recepcion/ordenes/<int:pk>/", views.order_detail, name="order_detail"),
    path("recepcion/ordenes/<int:pk>/pago/", views.add_payment, name="add_payment"),
    path("recepcion/ordenes/p/<int:payment_id>/recibo.pdf", views.payment_receipt_pdf, name="payment_receipt_pdf"),
    path("recepcion/ordenes/<int:pk>/status/", views.change_status, name="change_status"),
    path("recepcion/ordenes/<int:pk>/status/auth/", views.change_status_auth, name="change_status_auth"),
    path("recepcion/ordenes/<int:pk>/part/", views.add_part, name="add_part"),
    path("recepcion/ordenes/<int:pk>/nota/", views.add_note, name="add_note"),
    path("recepcion/ordenes/<int:pk>/assign/", views.assign_tech, name="assign_tech"),
    path("clientes/<int:pk>/dispositivos/", core_views.customer_devices, name="customer_devices"),

    # Inventario
    path("inventario/", views.inventory_list, name="inventory_list"),
    path("inventario/entrada/", views.receive_stock, name="receive_stock"),
    path("inventario/nuevo/", views.inventory_create, name="inventory_create"),
    path("inventario/<int:pk>/editar/", views.inventory_update, name="inventory_update"),
    path("inventario/<int:pk>/eliminar/", views.inventory_delete, name="inventory_delete"),

    # Cotizaciones
    path("recepcion/ordenes/<int:pk>/cotizacion/", views.estimate_edit, name="estimate_edit"),
    path("recepcion/ordenes/<int:pk>/cotizacion/enviar/", views.estimate_send, name="estimate_send"),
    path("cotizacion/<uuid:token>/", views.estimate_public, name="estimate_public"),
    path("cotizacion/<uuid:token>/aprobar/", views.estimate_approve, name="estimate_approve"),
    path("cotizacion/<uuid:token>/rechazar/", views.estimate_decline, name="estimate_decline"),
    path("recepcion/orden/<int:pk>/adjuntos/", views.order_attachments, name="order_attachments"),  # INTEGRASYS
    path("notificaciones/", views.notifications_list, name="notifications_list"),
    path("notificaciones/marcar-todas/", views.notifications_mark_all_read, name="notifications_mark_all_read"),
]


if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

from django.views.static import serve  # INTEGRASYS

# INTEGRASYS: fallback para /media/ (dev) aunque DEBUG sea False
urlpatterns += [re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT})]

# Fallback para /media/ en dev aunque DEBUG sea False
urlpatterns += [re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT})]
