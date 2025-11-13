from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import (
    Customer,
    Device,
    Estimate,
    InventoryItem,
    InventoryMovement,
    Notification,
    ServiceOrder,
    StatusHistory,
)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class FlowEndToEndTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser("admin", "admin@example.com", "pass123")
        self.client.force_login(self.user)
        self.inventory_item = InventoryItem.objects.create(sku="KIT-900", name="Kit completo", qty=4)

    def test_full_flow_happy_path(self):
        reception_payload = {
            "customer_name": "Cliente Demo",
            "customer_phone": "5551112233",
            "customer_email": "cliente@example.com",
            "brand": "Dell",
            "model": "XPS",
            "serial": "ABC-123",
            "notes": "No enciende",
        }
        resp = self.client.post(reverse("reception_new_order"), reception_payload)
        self.assertEqual(resp.status_code, 302)
        order = ServiceOrder.objects.latest("id")

        resp = self.client.post(reverse("assign_tech", args=[order.pk]), {"user_id": self.user.pk})
        self.assertEqual(resp.status_code, 302)

        estimate_url = reverse("estimate_edit", args=[order.pk])
        estimate_payload = {
            "description": ["Kit de reparacion"],
            "qty": ["2"],
            "unit_price": ["250.00"],
            "inventory_sku": [self.inventory_item.sku],
            "note": "Incluye mano de obra",
        }
        resp = self.client.post(estimate_url, estimate_payload)
        self.assertEqual(resp.status_code, 302)
        estimate = Estimate.objects.get(order=order)

        resp = self.client.post(reverse("change_status", args=[order.pk]), {"target": ServiceOrder.Status.IN_REVIEW})
        self.assertEqual(resp.status_code, 302)
        resp = self.client.post(reverse("change_status", args=[order.pk]), {"target": ServiceOrder.Status.READY_PICKUP})
        self.assertEqual(resp.status_code, 302)

        payment_url = reverse("add_payment", args=[order.pk])
        resp = self.client.post(payment_url, {"amount": "500.00", "method": "Efectivo", "reference": "PAGO-1"})
        self.assertEqual(resp.status_code, 302)

        resp = self.client.post(reverse("change_status", args=[order.pk]), {"target": ServiceOrder.Status.DELIVERED})
        self.assertEqual(resp.status_code, 302)

        order.refresh_from_db()
        self.assertEqual(order.status, ServiceOrder.Status.DELIVERED)
        self.assertIsNotNone(order.checkout_at)
        self.assertEqual(order.balance, Decimal("0"))

        history_count = StatusHistory.objects.filter(order=order).count()
        self.assertGreaterEqual(history_count, 4)
        self.assertTrue(Notification.objects.filter(order=order, channel__in=["status_ready", "status_done"]).exists())

        self.inventory_item.refresh_from_db()
        self.assertEqual(self.inventory_item.qty, 2)
        self.assertTrue(InventoryMovement.objects.filter(order=order, item=self.inventory_item).exists())

        receipt_resp = self.client.get(reverse("receipt_pdf", args=[order.token]))
        self.assertEqual(receipt_resp.status_code, 200)
        payment = order.payments.first()
        payment_resp = self.client.get(reverse("payment_receipt_pdf", args=[payment.pk]))
        self.assertEqual(payment_resp.status_code, 200)

        self.assertGreaterEqual(len(mail.outbox), 2)
