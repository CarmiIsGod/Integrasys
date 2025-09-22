from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone
from django.db import models

from core.models import InventoryItem, Notification


class Command(BaseCommand):
    help = "Envia email a staff si hay items con stock bajo"

    def handle(self, *args, **kwargs):
        lows = InventoryItem.objects.filter(qty__lt=models.F("min_qty")).order_by("sku")
        if not lows.exists():
            self.stdout.write("Sin bajos.")
            return
        User = get_user_model()
        recipients = list(
            User.objects.filter(is_staff=True)
            .exclude(email="")
            .values_list("email", flat=True)
        )
        if not recipients:
            self.stdout.write("Sin destinatarios.")
            return
        lines = [f"{it.sku} - {it.name}: {it.qty} / min {it.min_qty}" for it in lows]
        body = "Stock bajo:\n\n" + "\n".join(lines)
        from django.core.mail import send_mail

        send_mail(
            subject=f"[Inventario] {lows.count()} items con stock bajo",
            message=body,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            recipient_list=recipients,
            fail_silently=True,
        )
        for it in lows:
            Notification.objects.create(
                order=None,
                kind="email",
                channel="low_stock",
                ok=True,
                payload={
                    "sku": it.sku,
                    "name": it.name,
                    "qty": it.qty,
                    "min": it.min_qty,
                },
            )
        self.stdout.write(f"Enviado a {len(recipients)} destinatarios.")


