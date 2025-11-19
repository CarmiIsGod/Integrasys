from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Customer, Device, ServiceOrder
from core.permissions import ROLE_GERENCIA


class ExportEncodingTests(TestCase):
    def setUp(self):
        group, _ = Group.objects.get_or_create(name=ROLE_GERENCIA)
        self.user = get_user_model().objects.create_user(
            username="manager",
            password="pass123",
            is_staff=True,
        )
        self.user.groups.add(group)
        self.client.force_login(self.user)

    def _create_order(self):
        customer = Customer.objects.create(
            name="José Ñandú",
            phone="5558889999",
            email="jose@example.com",
        )
        device = Device.objects.create(
            customer=customer,
            brand="HP",
            model="Pavilion",
            serial="ENC-001",
        )
        checkin = timezone.now()
        order = ServiceOrder.objects.create(
            customer=customer,
            device=device,
            checkin_at=checkin,
        )
        order.devices.add(device)
        return order

    def test_orders_export_preserves_accents(self):
        order = self._create_order()
        start = order.checkin_at.date().isoformat()
        url = reverse("panel_export_orders")
        response = self.client.get(f"{url}?start={start}&end={start}")
        self.assertEqual(response.status_code, 200)

        payload = b"".join(response.streaming_content)
        text = payload.decode("utf-8-sig")
        self.assertIn("José Ñandú", text)
