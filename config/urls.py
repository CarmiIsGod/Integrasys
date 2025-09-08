from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView
from core import views

urlpatterns = [
    path("", RedirectView.as_view(url="/admin/", permanent=False)),
    path("accounts/login/", RedirectView.as_view(url="/admin/login/", permanent=False)),
    path("admin/", admin.site.urls),
    path('accounts/', include('django.contrib.auth.urls')),
    path("t/<uuid:token>/", views.public_status, name="public_status"),
    path("t/<uuid:token>/qr.png", views.qr, name="qr"),
    path("recepcion/nueva-orden/", views.reception_new_order, name="reception_new_order"),
]


