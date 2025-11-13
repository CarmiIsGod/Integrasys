from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import (
    Customer,
    Device,
    Estimate,
    EstimateItem,
    InventoryItem,
    InventoryMovement,
    ServiceOrder,
)


class EstimateInventoryTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser("admin", "admin@example.com", "pass123")
        self.client.force_login(self.user)
        customer = Customer.objects.create(name="Cliente", phone="5550001234", email="c@example.com")
        device = Device.objects.create(customer=customer, brand="Dell", model="XPS", serial="SN-22")
        self.order = ServiceOrder.objects.create(device=device)

    def _prepare_estimate(self, *, qty, stock):
        item = InventoryItem.objects.create(sku="KIT-01", name="Kit", qty=stock)
        estimate = Estimate.objects.create(order=self.order)
        EstimateItem.objects.create(
            estimate=estimate,
            description="Kit",
            qty=qty,
            unit_price=Decimal("100.00"),
            inventory_item=item,
        )
        return estimate, item

    def test_ready_status_consumes_inventory(self):
        estimate, stock_item = self._prepare_estimate(qty=2, stock=5)
        self.client.post(reverse("change_status", args=[self.order.pk]), {"target": ServiceOrder.Status.IN_REVIEW})
        response = self.client.post(reverse("change_status", args=[self.order.pk]), {"target": ServiceOrder.Status.READY_PICKUP})
        self.assertEqual(response.status_code, 302)
        stock_item.refresh_from_db()
        self.assertEqual(stock_item.qty, 3)
        self.assertTrue(
            InventoryMovement.objects.filter(order=self.order, item=stock_item, delta=-2).exists()
        )
        estimate.refresh_from_db()
        self.assertTrue(estimate.inventory_applied)

    def test_ready_status_blocks_when_no_stock(self):
        estimate, stock_item = self._prepare_estimate(qty=3, stock=1)
        self.client.post(reverse("change_status", args=[self.order.pk]), {"target": ServiceOrder.Status.IN_REVIEW})
        response = self.client.post(reverse("change_status", args=[self.order.pk]), {"target": ServiceOrder.Status.READY_PICKUP})
        self.assertEqual(response.status_code, 302)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, ServiceOrder.Status.IN_REVIEW)
        stock_item.refresh_from_db()
        self.assertEqual(stock_item.qty, 1)
        estimate.refresh_from_db()
        self.assertFalse(estimate.inventory_applied)
        self.assertFalse(InventoryMovement.objects.filter(order=self.order).exists())
