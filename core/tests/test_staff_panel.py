from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model

from core.models import Customer, Device, ServiceOrder, StatusHistory


class StaffPanelTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="staff",
            password="pass123",
            is_staff=True,
            email="staff@example.com",
        )
        self.client = Client()
        assert self.client.login(username="staff", password="pass123")

    def _create_order(self):
        c = Customer.objects.create(name="Cliente", phone="", email="")
        d = Device.objects.create(customer=c, brand="Marca", model="Modelo", serial="SER123")
        return ServiceOrder.objects.create(device=d)

    def test_list_orders_ok(self):
        resp = self.client.get("/recepcion/ordenes/")
        self.assertEqual(resp.status_code, 200)

    def test_change_status_flow(self):
        order = self._create_order()  # status NEW por defecto
        url = reverse("change_status", args=[order.pk])
        resp = self.client.post(url, {"target": "REV"})
        # Debe redirigir a la lista
        self.assertEqual(resp.status_code, 302)

        order.refresh_from_db()
        self.assertEqual(order.status, "REV")
        self.assertTrue(StatusHistory.objects.filter(order=order, status="REV").exists())

