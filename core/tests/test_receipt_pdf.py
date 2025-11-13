from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import Customer, Device, Payment, ServiceOrder


class ReceiptPDFTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser("staff", "s@s.com", "x")
        self.client.force_login(self.user)
        customer = Customer.objects.create(name="Prueba")
        device = Device.objects.create(customer=customer, brand="HP", model="Lap", serial="123")
        self.order = ServiceOrder.objects.create(device=device, notes="ok")

    def test_receipt_pdf_ok(self):
        url = reverse("receipt_pdf", args=[self.order.token])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")

    def test_payment_receipt_pdf_ok(self):
        payment = Payment.objects.create(order=self.order, amount=Decimal("50.00"), method="Efectivo")
        url = reverse("payment_receipt_pdf", args=[payment.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
