from django.test import TestCase
from django.urls import reverse

from core.models import Customer, Device, ServiceOrder, StatusHistory


class PublicTokenViewTests(TestCase):
    def setUp(self):
        customer = Customer.objects.create(name="Cliente", email="c@example.com")
        device = Device.objects.create(customer=customer, brand="Dell", model="XPS", serial="SN-9")
        self.order = ServiceOrder.objects.create(customer=customer, device=device)
        self.order.devices.set([device])
        StatusHistory.log(self.order, from_status="", to_status=ServiceOrder.Status.NEW, author=None)
        StatusHistory.log(
            self.order,
            from_status=ServiceOrder.Status.NEW,
            to_status=ServiceOrder.Status.IN_REVIEW,
            author=None,
        )

    def test_public_status_shows_history(self):
        url = reverse("public_status", args=[self.order.token])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Recibido")
        self.assertContains(response, "En revision")

    def test_public_status_invalid_token(self):
        bad_url = reverse("public_status", args=["00000000-0000-0000-0000-000000000000"])
        response = self.client.get(bad_url)
        self.assertEqual(response.status_code, 404)
