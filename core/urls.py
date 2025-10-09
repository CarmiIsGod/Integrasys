from django.urls import path

from . import views

urlpatterns = [
    path("panel/", views.panel, name="panel"),
    path("panel/export.csv", views.panel_export_csv, name="panel_export_csv"),
    path("panel/export_payments.csv", views.panel_export_payments, name="panel_export_payments"),
]
urlpatterns += [
    path("t/<uuid:token>/approve/", views.public_estimate_approve, name="public_estimate_approve"),
    path("t/<uuid:token>/decline/", views.public_estimate_decline, name="public_estimate_decline"),
]
