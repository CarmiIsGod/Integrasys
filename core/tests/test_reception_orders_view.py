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

    def test_defaults_to_all_statuses(self):
        new_order = self._order(ServiceOrder.Status.NEW)
        ready_order = self._order(ServiceOrder.Status.READY_PICKUP)

        url = reverse("list_orders")
        resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        page_obj = resp.context["page_obj"]
        orders = list(page_obj.object_list)
        # Sin filtro de estado, deben venir todas las ordenes creadas.
        self.assertCountEqual(orders, ServiceOrder.objects.all())
        # El filtro de estado en contexto debe reflejar "todos" (vacío/None).
        self.assertFalse(resp.context.get("status"))
        html = resp.content.decode()
        # Debe mostrar badges para los distintos estados presentes.
        self.assertIn("badge-status-NEW", html)
        self.assertIn("badge-status-READY", html)

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
