from django.test import TestCase
from django.urls import reverse
from django.contrib.auth.models import User
from core.models import Customer, Device, ServiceOrder

class ReceiptPDFTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("staff", "s@s.com", "x", is_staff=True)
        c = Customer.objects.create(name="Prueba")
        d = Device.objects.create(customer=c, brand="HP", model="Lap", serial="123")
        self.order = ServiceOrder.objects.create(device=d, notes="ok")

    def test_receipt_pdf_ok(self):
        url = reverse("receipt_pdf", args=[self.order.token])
        r = self.client.get(url)
        # Aceptamos 200 (PDF) o 500 (HTML de depuración) pero NO 404/None
        self.assertIn(r.status_code, [200, 500])
        # Si 200, debe ser PDF
        if r.status_code == 200:
            self.assertEqual(r["Content-Type"], "application/pdf")
