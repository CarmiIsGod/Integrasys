from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from core.models import Customer, Device, ServiceOrder
from core.permissions import ROLE_RECEPCION


class ReceptionCustomerDeviceTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.user = self.User.objects.create_user(
            username="recepcion",
            password="pass123",
            is_staff=True,
        )
        group, _ = Group.objects.get_or_create(name=ROLE_RECEPCION)
        self.user.groups.add(group)
        self.client.force_login(self.user)
        self.url = reverse("reception_new_order")

    def _payload(self, **overrides):
        base = {
            "customer_name": "Cliente Demo",
            "customer_phone": "5551234567",
            "customer_email": "demo@example.com",
            "notes": "Datos generales",
            "devices-TOTAL_FORMS": "1",
            "devices-INITIAL_FORMS": "0",
            "devices-MIN_NUM_FORMS": "1",
            "devices-MAX_NUM_FORMS": "5",
            "devices-0-brand": "Dell",
            "devices-0-model": "XPS 13",
            "devices-0-serial": "sn-001",
            "devices-0-notes": "No enciende",
        }
        legacy_map = {
            "brand": "devices-0-brand",
            "model": "devices-0-model",
            "serial": "devices-0-serial",
            "device_notes": "devices-0-notes",
        }
        normalized = {}
        for key, value in overrides.items():
            mapped = legacy_map.get(key, key)
            normalized[mapped] = value
        base.update(normalized)
        return base

    def test_creates_customer_and_device(self):
        response = self.client.post(self.url, self._payload())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Customer.objects.count(), 1)
        self.assertEqual(Device.objects.count(), 1)
        order = ServiceOrder.objects.latest("id")
        self.assertEqual(order.device.customer.email, "demo@example.com")
        self.assertEqual(order.device.serial, "SN-001")

    def test_reuses_customer_by_email_and_updates_missing_phone(self):
        customer = Customer.objects.create(name="Cliente Demo", phone="", email="dupe@example.com")
        payload = self._payload(customer_email="DUPE@example.com", customer_phone="5588887777")
        response = self.client.post(self.url, payload)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Customer.objects.filter(email__iexact="dupe@example.com").count(), 1)
        customer.refresh_from_db()
        self.assertEqual(customer.phone, "5588887777")
        order = ServiceOrder.objects.latest("id")
        self.assertEqual(order.device.customer_id, customer.id)

    def test_reuses_customer_by_phone_when_email_missing(self):
        customer = Customer.objects.create(name="Cliente Demo", phone="5511122233", email="")
        payload = self._payload(customer_email="", customer_phone="5511122233")
        response = self.client.post(self.url, payload)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Customer.objects.count(), 1)
        order = ServiceOrder.objects.latest("id")
        self.assertEqual(order.device.customer_id, customer.id)

    def test_reuses_device_by_serial(self):
        customer = Customer.objects.create(name="Cliente Demo", phone="5553332222", email="demo@acme.com")
        device = Device.objects.create(customer=customer, brand="HP", model="Pavilion", serial="abc-999", notes="")
        payload = self._payload(
            customer_email="demo@acme.com",
            customer_phone="5553332222",
            serial="ABC-999",
            device_notes="Nueva falla",
        )
        response = self.client.post(self.url, payload)
        self.assertEqual(response.status_code, 302)
        order = ServiceOrder.objects.latest("id")
        self.assertEqual(order.device_id, device.id)
        device.refresh_from_db()
        self.assertEqual(device.serial.upper(), "ABC-999")
        self.assertEqual(device.notes, "Nueva falla")

    def test_requires_phone_or_email(self):
        payload = self._payload(customer_phone="", customer_email="")
        response = self.client.post(self.url, payload, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ServiceOrder.objects.count(), 0)
        context_messages = []
        if response.context and "messages" in response.context:
            context_messages = list(response.context["messages"])
        self.assertTrue(context_messages)
        self.assertTrue(any("telefono" in m.message.lower() for m in context_messages))
