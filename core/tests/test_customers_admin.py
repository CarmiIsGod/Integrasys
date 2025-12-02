from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase
from django.urls import reverse

from core.models import Customer, Device, ServiceOrder
from core.permissions import ROLE_GERENCIA, ROLE_RECEPCION


class CustomerAdminTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.client = Client()
        self.group_gerencia, _ = Group.objects.get_or_create(name=ROLE_GERENCIA)
        self.group_recepcion, _ = Group.objects.get_or_create(name=ROLE_RECEPCION)

    def _login_with_roles(self, *roles):
        user = self.User.objects.create_user(
            username="_".join(roles) or "staff",
            password="pass123",
            is_staff=True,
        )
        for role in roles:
            if role == ROLE_GERENCIA:
                user.groups.add(self.group_gerencia)
            if role == ROLE_RECEPCION:
                user.groups.add(self.group_recepcion)
        self.client.logout()
        assert self.client.login(username=user.username, password="pass123")
        return user

    def _create_customer_with_orders(self, orders=0, **kwargs):
        customer = Customer.objects.create(**kwargs)
        for idx in range(orders):
            device = Device.objects.create(
                customer=customer,
                brand="Marca",
                model=f"Modelo{idx}",
                serial=f"SN-{customer.id}-{idx}",
            )
            order = ServiceOrder.objects.create(customer=customer, device=device)
            order.devices.set([device])
        return customer

    def test_recepcion_user_can_list_customers(self):
        self._create_customer_with_orders(
            orders=1,
            name="Cliente Uno",
            phone="5551234567",
            alt_phone="",
            email="cliente1@example.com",
        )
        self._login_with_roles(ROLE_RECEPCION)
        resp = self.client.get(reverse("customer_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Cliente Uno")

    def test_search_filters_by_email_and_counts_orders(self):
        target = self._create_customer_with_orders(
            orders=2,
            name="Alpha",
            phone="5551112222",
            alt_phone="5552223333",
            email="alpha@example.com",
        )
        self._create_customer_with_orders(
            orders=1,
            name="Beta",
            phone="5559998888",
            alt_phone="",
            email="beta@example.com",
        )
        self._login_with_roles(ROLE_GERENCIA)
        resp = self.client.get(reverse("customer_list"), {"q": "alpha@example.com"})
        self.assertEqual(resp.status_code, 200)
        page_obj = resp.context["page_obj"]
        self.assertEqual(len(page_obj.object_list), 1)
        self.assertEqual(page_obj.object_list[0].pk, target.pk)
        self.assertEqual(page_obj.object_list[0].order_count, 2)

    def test_edit_form_loads_and_updates_customer(self):
        customer = self._create_customer_with_orders(
            orders=0,
            name="Editar Cliente",
            phone="5550001111",
            alt_phone="",
            email="old@example.com",
        )
        self._login_with_roles(ROLE_RECEPCION)
        resp = self.client.get(reverse("customer_edit", args=[customer.pk]))
        self.assertEqual(resp.status_code, 200)
        data = {
            "name": "Cliente Actualizado",
            "phone": "5553334444",
            "alt_phone": "5554445555",
            "email": "new@example.com",
        }
        resp = self.client.post(reverse("customer_edit", args=[customer.pk]), data)
        self.assertEqual(resp.status_code, 302)
        customer.refresh_from_db()
        self.assertEqual(customer.name, "Cliente Actualizado")
        self.assertEqual(customer.phone, "555 333 4444")
        self.assertEqual(customer.alt_phone, "555 444 5555")
        self.assertEqual(customer.email, "new@example.com")

    def test_unauthorized_users_blocked(self):
        customer = self._create_customer_with_orders(
            orders=0,
            name="Cliente",
            phone="5550009999",
            alt_phone="",
            email="cliente@example.com",
        )
        # Staff sin rol
        self._login_with_roles()
        resp = self.client.get(reverse("customer_list"))
        self.assertEqual(resp.status_code, 403)

        self.client.logout()
        resp = self.client.get(reverse("customer_edit", args=[customer.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp["Location"].startswith("/admin/login/"))
