from decimal import Decimal
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Customer, Device, Payment, ServiceOrder


class ExportCSVTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.today = timezone.localdate()

        self.User = get_user_model()
        self.manager = self.User.objects.create_user(username="manager", password="pass123")
        gerencia, _ = Group.objects.get_or_create(name="Gerencia")
        self.manager.groups.add(gerencia)

        self.regular = self.User.objects.create_user(username="regular", password="pass123")

        self.customer = Customer.objects.create(name="Cliente 1", phone="555", email="c1@example.com")
        device_recent = Device.objects.create(customer=self.customer, brand="Marca", model="Modelo", serial="ABC123")
        device_old = Device.objects.create(customer=self.customer, brand="Marca2", model="Modelo2", serial="XYZ987")

        self.recent_order = ServiceOrder.objects.create(customer=self.customer, device=device_recent)
        self.recent_order.devices.set([device_recent])
        self.old_order = ServiceOrder.objects.create(customer=self.customer, device=device_old)
        self.old_order.devices.set([device_old])

        recent_checkin = self._aware_datetime(days_ago=1)
        old_checkin = self._aware_datetime(days_ago=10)
        ServiceOrder.objects.filter(pk=self.recent_order.pk).update(checkin_at=recent_checkin)
        ServiceOrder.objects.filter(pk=self.old_order.pk).update(checkin_at=old_checkin)
        self.recent_order.refresh_from_db()
        self.old_order.refresh_from_db()

        self.recent_payment = Payment.objects.create(
            order=self.recent_order,
            device=device_recent,
            amount=Decimal("150.00"),
            method="Efectivo",
            reference="RECENT",
            author=self.manager,
        )
        self.old_payment = Payment.objects.create(
            order=self.old_order,
            device=device_old,
            amount=Decimal("200.00"),
            method="Tarjeta",
            reference="OLD",
            author=self.manager,
        )

        recent_created = self._aware_datetime(days_ago=1)
        old_created = self._aware_datetime(days_ago=10)
        Payment.objects.filter(pk=self.recent_payment.pk).update(created_at=recent_created)
        Payment.objects.filter(pk=self.old_payment.pk).update(created_at=old_created)

    def _stream_content(self, response):
        if hasattr(response, "streaming_content"):
            chunks = list(response.streaming_content)
            return "".join(chunk.decode(response.charset) if isinstance(chunk, bytes) else chunk for chunk in chunks)
        return response.content.decode(response.charset)

    def _aware_datetime(self, *, days_ago):
        base_date = self.today - timedelta(days=days_ago)
        dt = timezone.datetime.combine(base_date, timezone.datetime.min.time())
        if settings.USE_TZ:
            return timezone.make_aware(dt, timezone.get_current_timezone())
        return dt

    def test_orders_requires_manager(self):
        self.client.force_login(self.regular)
        url = reverse("panel_export_orders")
        response = self.client.get(url, {"start": self.today.isoformat(), "end": self.today.isoformat()})
        self.assertEqual(response.status_code, 403)

    def test_payments_requires_manager(self):
        self.client.force_login(self.regular)
        url = reverse("panel_export_payments")
        response = self.client.get(url, {"start": self.today.isoformat(), "end": self.today.isoformat()})
        self.assertEqual(response.status_code, 403)

    def test_orders_csv_filters_by_range(self):
        self.client.force_login(self.manager)
        url = reverse("panel_export_orders")
        start = (self.today - timedelta(days=2)).isoformat()
        end = self.today.isoformat()
        response = self.client.get(url, {"start": start, "end": end})
        self.assertEqual(response.status_code, 200)
        content = self._stream_content(response)
        self.assertIn("OrdenID", content)
        self.assertIn(self.recent_order.folio, content)
        self.assertNotIn(self.old_order.folio, content)

    def test_orders_csv_dates_are_text(self):
        self.client.force_login(self.manager)
        url = reverse("panel_export_orders")
        start = (self.today - timedelta(days=2)).isoformat()
        end = self.today.isoformat()
        response = self.client.get(url, {"start": start, "end": end})
        content = self._stream_content(response).replace("\ufeff", "")
        lines = [line for line in content.splitlines() if line.strip()]
        self.assertGreaterEqual(len(lines), 2)
        data_row = lines[1]
        self.assertIn('="', data_row)

    def test_payments_csv_filters_by_range(self):
        self.client.force_login(self.manager)
        url = reverse("panel_export_payments")
        start = (self.today - timedelta(days=2)).isoformat()
        end = self.today.isoformat()
        response = self.client.get(url, {"start": start, "end": end})
        self.assertEqual(response.status_code, 200)
        content = self._stream_content(response)
        self.assertIn("PagoID", content)
        self.assertIn(self.recent_payment.reference, content)
        self.assertNotIn(self.old_payment.reference, content)

    def test_payments_csv_dates_are_text(self):
        self.client.force_login(self.manager)
        url = reverse("panel_export_payments")
        start = (self.today - timedelta(days=2)).isoformat()
        end = self.today.isoformat()
        response = self.client.get(url, {"start": start, "end": end})
        content = self._stream_content(response).replace("\ufeff", "")
        lines = [line for line in content.splitlines() if line.strip()]
        self.assertGreaterEqual(len(lines), 2)
        data_row = lines[1]
        self.assertIn('="', data_row)
