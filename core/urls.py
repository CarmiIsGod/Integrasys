from django.urls import path

from . import views

urlpatterns = [
    path("panel/", views.panel, name="panel"),
    path("panel/export.csv", views.panel_export_csv, name="panel_export_csv"),
]
