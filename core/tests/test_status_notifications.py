from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import Customer, Device, Notification, ServiceOrder


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class StatusNotificationTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.user = self.User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="pass123",
        )
        self.client.force_login(self.user)

    def _create_order(self):
        customer = Customer.objects.create(name="Cliente", email="cliente@example.com", phone="5551112233")
        device = Device.objects.create(customer=customer, brand="Dell", model="XPS", serial="SN-10")
        return ServiceOrder.objects.create(device=device)

    def test_ready_status_sends_email_and_notification(self):
        order = self._create_order()
        # Llevar a estado REV para permitir READY
        self.client.post(reverse("change_status", args=[order.pk]), {"target": ServiceOrder.Status.IN_REVIEW})
        mail.outbox.clear()
        resp = self.client.post(
            reverse("change_status", args=[order.pk]),
            {"target": ServiceOrder.Status.READY_PICKUP},
        )
        self.assertEqual(resp.status_code, 302)
        notifications = Notification.objects.filter(order=order, channel="status_ready")
        self.assertTrue(notifications.exists())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(f"/t/{order.token}/", mail.outbox[0].body)

    def test_requires_auth_status_emails_customer(self):
        order = self._create_order()
        self.client.post(reverse("change_status", args=[order.pk]), {"target": ServiceOrder.Status.IN_REVIEW})
        mail.outbox.clear()
        resp = self.client.post(reverse("change_status_auth", args=[order.pk]))
        self.assertEqual(resp.status_code, 302)
        notifications = Notification.objects.filter(order=order, channel="status_auth")
        self.assertTrue(notifications.exists())
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        self.assertIn(f"/t/{order.token}/", body)
