from django.contrib import admin
from django.urls import path
from core import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("t/<uuid:token>/", views.public_status, name="public_status"),
    path("t/<uuid:token>/qr.png", views.qr, name="qr"),
]
