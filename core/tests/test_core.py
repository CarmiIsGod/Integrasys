from django.test import TestCase, Client, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse, NoReverseMatch
from django.utils import timezone
from core.models import Customer, Device, ServiceOrder, Payment, Attachment
import tempfile

@override_settings(MEDIA_ROOT=tempfile.gettempdir())
class TestCoreFlow(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(name="C", phone="1", email="c@example.com")
        self.device = Device.objects.create(customer=self.customer, brand="Asus", model="ROG", serial="X")
        self.order = ServiceOrder.objects.create(device=self.device)

    def test_create_order_generates_folio(self):
        self.assertTrue(self.order.folio.startswith("SR-"))

    def test_attachment_upload_and_public(self):
        # GIF header mínimo como bytes de prueba sirve para imagen
        f = SimpleUploadedFile("test.jpg", b"\x47\x49\x46\x38\x39\x61", content_type="image/jpeg")
        Attachment.objects.create(order=self.order, file=f, kind="image", is_public=True)
        self.assertTrue(Attachment.objects.filter(order=self.order, is_public=True).exists())

    def test_public_status_view_200(self):
        client = Client()
        try:
            url = reverse("public_status", args=[str(self.order.token)])
        except NoReverseMatch:
            url = f"/t/{self.order.token}/"
        r = client.get(url)
        self.assertEqual(r.status_code, 200)

    def test_payments_total_balance(self):
        Payment.objects.create(order=self.order, amount=100)
        Payment.objects.create(order=self.order, amount=50)
        # approved_total puede ser 0 si no hay cotización; validamos relación balance = approved - paid
        paid = self.order.paid_total
        self.assertEqual(float(paid), 150.0)
        self.assertEqual(self.order.balance, self.order.approved_total - self.order.paid_total)
