from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Customer, Device, Estimate, EstimateItem, ServiceOrder


class EstimatePublicSelectionTests(TestCase):
    def setUp(self):
        customer = Customer.objects.create(name="Cliente", phone="5550001111", email="c@example.com")
        device = Device.objects.create(customer=customer, brand="Dell", model="XPS")
        self.order = ServiceOrder.objects.create(customer=customer, device=device)
        self.order.devices.set([device])

    def _build_estimate(self):
        estimate = Estimate.objects.create(order=self.order)
        item1 = EstimateItem.objects.create(
            estimate=estimate,
            description="Test 1",
            qty=1,
            unit_price=Decimal("100.00"),
        )
        item2 = EstimateItem.objects.create(
            estimate=estimate,
            description="Test 2",
            qty=1,
            unit_price=Decimal("50.00"),
        )
        item3 = EstimateItem.objects.create(
            estimate=estimate,
            description="Test 3",
            qty=2,
            unit_price=Decimal("60.00"),
        )
        return estimate, [item1, item2, item3]

    def test_default_status_is_pending(self):
        estimate = Estimate.objects.create(order=self.order)
        item = EstimateItem.objects.create(
            estimate=estimate,
            description="Revision",
            qty=1,
            unit_price=Decimal("10.00"),
        )
        self.assertEqual(item.status, EstimateItem.Status.PENDING)
        self.assertIsNone(item.decided_at)

    def test_partial_selection_updates_pending_only_and_totals(self):
        estimate, items = self._build_estimate()
        url = reverse("estimate_update_items", args=[estimate.token])
        response = self.client.post(
            url,
            {
                f"item-{items[0].id}-status": EstimateItem.Status.ACCEPTED,
                f"item-{items[1].id}-status": EstimateItem.Status.REJECTED,
                f"item-{items[2].id}-status": EstimateItem.Status.ACCEPTED,
            },
        )
        self.assertEqual(response.status_code, 302)

        items[0].refresh_from_db()
        items[1].refresh_from_db()
        items[2].refresh_from_db()
        self.assertEqual(items[0].status, EstimateItem.Status.ACCEPTED)
        self.assertIsNotNone(items[0].decided_at)
        self.assertEqual(items[1].status, EstimateItem.Status.REJECTED)
        self.assertIsNotNone(items[1].decided_at)
        self.assertEqual(items[2].status, EstimateItem.Status.ACCEPTED)
        self.assertIsNotNone(items[2].decided_at)

        response = self.client.get(reverse("estimate_public", args=[estimate.token]))
        self.assertEqual(response.status_code, 200)
        # Accepted total: (100 + 120) * 1.16 = 255.20
        self.assertEqual(response.context["accepted_total"], Decimal("255.20"))
        self.order.refresh_from_db()
        self.assertEqual(self.order.approved_total, Decimal("255.20"))
        self.assertEqual(self.order.balance, Decimal("255.20"))

    def test_status_label_updates_when_no_pending(self):
        estimate, items = self._build_estimate()
        # Accept first, reject second, accept third
        items[0].status = EstimateItem.Status.ACCEPTED
        items[0].decided_at = timezone.now()
        items[1].status = EstimateItem.Status.REJECTED
        items[1].decided_at = timezone.now()
        items[2].status = EstimateItem.Status.ACCEPTED
        items[2].decided_at = timezone.now()
        EstimateItem.objects.bulk_update(items, ["status", "decided_at"])
        estimate.recompute_status_from_items(save=True)

        response = self.client.get(reverse("estimate_public", args=[estimate.token]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["status_label"], "Cerrada · aceptacion parcial")
        self.assertFalse(response.context["has_pending_items"])

    def test_all_rejected_sets_closed_rejected(self):
        estimate, items = self._build_estimate()
        url = reverse("estimate_update_items", args=[estimate.token])
        response = self.client.post(
            url,
            {
                f"item-{items[0].id}-status": EstimateItem.Status.REJECTED,
                f"item-{items[1].id}-status": EstimateItem.Status.REJECTED,
                f"item-{items[2].id}-status": EstimateItem.Status.REJECTED,
            },
        )
        self.assertEqual(response.status_code, 302)
        estimate.refresh_from_db()
        self.assertEqual(estimate.status, Estimate.Status.CLOSED_REJECTED)
        response = self.client.get(reverse("estimate_public", args=[estimate.token]))
        self.assertContains(response, "Cerrada \u00b7 rechazada")

    def test_decided_items_are_not_modified(self):
        estimate, items = self._build_estimate()
        from django.utils import timezone

        decided_ts = timezone.now()
        items[0].status = EstimateItem.Status.ACCEPTED
        items[0].decided_at = decided_ts
        items[0].save(update_fields=["status", "decided_at"])

        url = reverse("estimate_update_items", args=[estimate.token])
        response = self.client.post(
            url,
            {
                f"item-{items[0].id}-status": EstimateItem.Status.REJECTED,
                f"item-{items[1].id}-status": EstimateItem.Status.ACCEPTED,
                f"item-{items[2].id}-status": EstimateItem.Status.REJECTED,
            },
        )
        self.assertEqual(response.status_code, 302)

        items[0].refresh_from_db()
        items[1].refresh_from_db()
        items[2].refresh_from_db()
        self.assertEqual(items[0].status, EstimateItem.Status.ACCEPTED)
        self.assertEqual(items[1].status, EstimateItem.Status.ACCEPTED)
        self.assertEqual(items[2].status, EstimateItem.Status.REJECTED)
        self.assertEqual(items[0].decided_at, decided_ts)
        self.assertIsNotNone(items[1].decided_at)
        self.assertIsNotNone(items[2].decided_at)
