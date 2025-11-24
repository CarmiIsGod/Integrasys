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
            "devices-MAX_NUM_FORMS": "20",
            "devices-0-brand": "Dell",
            "devices-0-model": "XPS 13",
            "devices-0-serial": "sn-001",
            "devices-0-notes": "No enciende",
            "devices-0-password_notes": "",
            "devices-0-accessories_notes": "",
        }
        legacy_map = {
            "brand": "devices-0-brand",
            "model": "devices-0-model",
            "serial": "devices-0-serial",
            "device_notes": "devices-0-notes",
            "password_notes": "devices-0-password_notes",
            "accessories_notes": "devices-0-accessories_notes",
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

    def test_creates_new_customer_even_if_email_exists(self):
        Customer.objects.create(name="Cliente Original", phone="1234567890", email="dupe@example.com")
        payload = self._payload(customer_name="Cliente Nuevo", customer_email="dupe@example.com")
        response = self.client.post(self.url, payload)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Customer.objects.filter(email__iexact="dupe@example.com").count(), 2)
        order = ServiceOrder.objects.latest("id")
        self.assertEqual(order.customer.name, "Cliente Nuevo")

    def test_reuses_customer_when_customer_id_is_present(self):
        customer = Customer.objects.create(name="Cliente Existente", phone="5512345678", email="cliente@demo.com")
        payload = self._payload(
            customer_name="Intento Override",
            customer_phone="0000000000",
            customer_email="otro@example.com",
            customer_id=str(customer.id),
        )
        response = self.client.post(self.url, payload)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Customer.objects.count(), 1)
        order = ServiceOrder.objects.latest("id")
        self.assertEqual(order.customer_id, customer.id)
        customer.refresh_from_db()
        self.assertEqual(customer.name, "Cliente Existente")

    def test_reuses_device_by_serial(self):
        customer = Customer.objects.create(name="Cliente Demo", phone="5553332222", email="demo@acme.com")
        device = Device.objects.create(customer=customer, brand="HP", model="Pavilion", serial="abc-999", notes="")
        payload = self._payload(
            customer_email="demo@acme.com",
            customer_phone="5553332222",
            customer_id=str(customer.id),
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

    def test_stores_password_and_accessories_notes(self):
        payload = self._payload(
            password_notes="4321",
            accessories_notes="Cargador original, funda",
        )
        response = self.client.post(self.url, payload)
        self.assertEqual(response.status_code, 302)
        device = Device.objects.latest("id")
        self.assertEqual(device.password_notes, "4321")
        self.assertEqual(device.accessories_notes, "Cargador original, funda")

    def test_invalid_customer_id_shows_error(self):
        payload = self._payload(customer_id="9999")
        response = self.client.post(self.url, payload, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Customer.objects.count(), 0)
        context_messages = []
        if response.context and "messages" in response.context:
            context_messages = list(response.context["messages"])
        self.assertTrue(any("vuelve a buscarlo" in m.message.lower() for m in context_messages))

    def test_customer_search_endpoint_returns_matches(self):
        Customer.objects.create(name="Diego Demo", phone="5554443322", email="diego@example.com")
        url = reverse("reception_customer_search")
        response = self.client.get(url, {"q": "diego"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("results", payload)
        self.assertTrue(any(item["email"] == "diego@example.com" for item in payload["results"]))

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

    def test_customer_devices_view_lists_orders(self):
        response = self.client.post(self.url, self._payload())
        self.assertEqual(response.status_code, 302)
        order = ServiceOrder.objects.latest("id")
        customer = order.device.customer
        device = order.device
        second = ServiceOrder.objects.create(customer=customer, device=device)
        order.devices.set([device])
        second.devices.set([device])

        url = reverse("customer_devices", args=[customer.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, device.brand)
        self.assertContains(resp, order.folio)
        self.assertContains(resp, second.folio)

    def test_order_detail_shows_device_failures(self):
        customer = Customer.objects.create(name="Cliente Failas", phone="5552221111", email="faila@example.com")
        device_one = Device.objects.create(
            customer=customer,
            brand="Lenovo",
            model="ThinkPad",
            serial="S11",
            notes="No enciende, posible corto.",
            password_notes="PIN 0000",
            accessories_notes="Dock y cargador",
        )
        device_two = Device.objects.create(
            customer=customer,
            brand="Asus",
            model="Miau",
            serial="S22",
            notes="Pantalla rota, sin imagen.",
            accessories_notes="Teclado externo",
        )
        order = ServiceOrder.objects.create(customer=customer, device=device_one)
        order.devices.set([device_one, device_two])

        url = reverse("order_detail", args=[order.pk])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No enciende, posible corto.")
        self.assertContains(response, "Pantalla rota, sin imagen.")
        self.assertContains(response, "PIN 0000")
        self.assertContains(response, "Dock y cargador")
        self.assertContains(response, "Teclado externo")
