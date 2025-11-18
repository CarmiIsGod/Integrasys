from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from core.models import Attachment, Customer, Device, ServiceOrder


class AttachmentMultiUploadTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.User = get_user_model()
        self.user = self.User.objects.create_user(username="staff", password="pass123")
        recepcion, _ = Group.objects.get_or_create(name="Recepcion")
        self.user.groups.add(recepcion)
        assert self.client.login(username="staff", password="pass123")

        customer = Customer.objects.create(name="Cliente", phone="", email="")
        device = Device.objects.create(customer=customer, brand="Marca", model="Modelo", serial="ABC123")
        self.order = ServiceOrder.objects.create(customer=customer, device=device)
        self.order.devices.set([device])

    def _upload(self, files, **extra):
        url = reverse("order_attachments", args=[self.order.pk])
        data = {"caption": extra.get("caption", "Notas"), "file": files}
        return self.client.post(url, data, follow=True)

    def test_multiple_files_create_multiple_attachments(self):
        file_one = SimpleUploadedFile("doc1.pdf", b"a" * 1024, content_type="application/pdf")
        file_two = SimpleUploadedFile("doc2.pdf", b"b" * 1024, content_type="application/pdf")
        response = self._upload([file_one, file_two])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Attachment.objects.filter(service_order=self.order).count(), 2)

    def test_file_over_max_size_is_rejected(self):
        small_file = SimpleUploadedFile("ok.pdf", b"a" * 1024, content_type="application/pdf")
        large_bytes = 2 * 1024 * 1024  # 2 MB
        large_file = SimpleUploadedFile("big.pdf", b"x" * large_bytes, content_type="application/pdf")
        with self.settings(MAX_FILE_MB=1):
            response = self._upload([large_file, small_file])
        self.assertEqual(response.status_code, 200)
        attachments = Attachment.objects.filter(service_order=self.order)
        self.assertEqual(attachments.count(), 1)
        self.assertTrue(attachments.first().file.name.endswith("ok.pdf"))
        self.assertContains(response, "excede 1 MB")
