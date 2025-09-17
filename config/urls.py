from django.contrib import admin
from django.urls import path, include
from core import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("django.contrib.auth.urls")),
    path("t/<uuid:token>/", views.public_status, name="public_status"),
    path("t/<uuid:token>/qr.png", views.qr, name="qr"),
    path("t/<uuid:token>/recibo.pdf", views.receipt_pdf, name="receipt_pdf"),
    path("recepcion/nueva-orden/", views.reception_new_order, name="reception_new_order"),
    path("recepcion/ordenes/", views.list_orders, name="list_orders"),
    path("recepcion/ordenes/<int:pk>/", views.order_detail, name="order_detail"),
    path("recepcion/ordenes/<int:pk>/status/", views.change_status, name="change_status"),
    path("recepcion/ordenes/<int:pk>/part/", views.add_part, name="add_part"),
    path("recepcion/ordenes/<int:pk>/nota/", views.add_note, name="add_note"),
    path("recepcion/ordenes/<int:pk>/assign/", views.assign_tech, name="assign_tech"),
]
