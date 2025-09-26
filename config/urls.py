from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView   
from core import views

urlpatterns = [
    # Redirige la raíz al panel de órdenes (puedes cambiar a "/admin/" si prefieres)
    path("", RedirectView.as_view(url="/recepcion/ordenes/", permanent=False)),
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
    path("recepcion/ordenes/<int:pk>/status/", views.change_status, name="change_status"),
    path("recepcion/ordenes/<int:pk>/part/", views.add_part, name="add_part"),
    path("recepcion/ordenes/<int:pk>/nota/", views.add_note, name="add_note"),
    path("recepcion/ordenes/<int:pk>/assign/", views.assign_tech, name="assign_tech"),

    # Inventario
    path("inventario/", views.inventory_list, name="inventory_list"),
    path("inventario/entrada/", views.receive_stock, name="receive_stock"),

    # Cotizaciones
    path("recepcion/ordenes/<int:pk>/cotizacion/", views.estimate_edit, name="estimate_edit"),
    path("recepcion/ordenes/<int:pk>/cotizacion/enviar/", views.estimate_send, name="estimate_send"),
    path("cotizacion/<uuid:token>/", views.estimate_public, name="estimate_public"),
    path("cotizacion/<uuid:token>/aprobar/", views.estimate_approve, name="estimate_approve"),
    path("cotizacion/<uuid:token>/rechazar/", views.estimate_decline, name="estimate_decline"),
]


