from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from core.models import Customer, Device, ServiceOrder
from core.permissions import ROLE_RECEPCION


class ReceptionOrdersViewTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user("recepcion", password="pass123", is_staff=True)
        group, _ = Group.objects.get_or_create(name=ROLE_RECEPCION)
        self.user.groups.add(group)
        self.client.force_login(self.user)
        self.customer = Customer.objects.create(name="Cliente Demo", phone="5551234567", email="demo@example.com")
        self.device = Device.objects.create(customer=self.customer, brand="Apple", model="iPhone", serial="SN-1")

    def _order(self, status):
        return ServiceOrder.objects.create(
            customer=self.customer,
            device=self.device,
            status=status,
        )

    def test_defaults_to_new_status_filter(self):
        new_order = self._order(ServiceOrder.Status.NEW)
        self._order(ServiceOrder.Status.READY_PICKUP)

        url = reverse("list_orders")
        resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        page_obj = resp.context["page_obj"]
        self.assertTrue(all(o.status == ServiceOrder.Status.NEW for o in page_obj.object_list))
        self.assertEqual(resp.context.get("status"), ServiceOrder.Status.NEW)
        html = resp.content.decode()
        self.assertIn("badge-status-NEW", html)

    def test_respects_explicit_status_filter(self):
        self._order(ServiceOrder.Status.NEW)
        ready = self._order(ServiceOrder.Status.READY_PICKUP)

        url = reverse("list_orders")
        resp = self.client.get(url, {"status": ServiceOrder.Status.READY_PICKUP})

        self.assertEqual(resp.status_code, 200)
        page_obj = resp.context["page_obj"]
        self.assertEqual(len(page_obj.object_list), 1)
        self.assertEqual(page_obj.object_list[0].pk, ready.pk)
        self.assertEqual(resp.context.get("status"), ServiceOrder.Status.READY_PICKUP)

    def test_table_has_no_sku_column(self):
        self._order(ServiceOrder.Status.NEW)
        resp = self.client.get(reverse("list_orders"))
        html = resp.content.decode()
        self.assertNotIn("<th>SKU", html)
