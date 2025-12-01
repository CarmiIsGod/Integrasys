import csv
from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import (
    Customer,
    Device,
    Estimate,
    EstimateItem,
    InventoryItem,
    InventoryMovement,
    Payment,
    ServiceOrder,
    StatusHistory,
)
from core.permissions import ROLE_RECEPCION


class StaffPanelTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.client = Client()

        # SUPERUSER: bypassea cualquier gate (require_manager, permisos, etc.)
        self.user = self.User.objects.create_user(
            username="admin",
            password="pass123",
            email="admin@example.com",
            is_staff=True,
            is_superuser=True,
        )
        assert self.client.login(username="admin", password="pass123")

    def _create_order(self):
        c = Customer.objects.create(name="Cliente", phone="", email="")
        d = Device.objects.create(customer=c, brand="Marca", model="Modelo", serial="SER123")
        order = ServiceOrder.objects.create(customer=c, device=d)
        order.devices.set([d])
        return order

    def test_list_orders_ok(self):
        resp = self.client.get("/recepcion/ordenes/")
        self.assertEqual(resp.status_code, 200)

    def test_dashboard_has_reception_shortcut(self):
        resp = self.client.get(reverse("dashboard"))
        self.assertEqual(resp.status_code, 200)
        reception_url = reverse("reception_home")
        self.assertContains(resp, "Ir a Recepción")
        self.assertIn(reception_url, resp.content.decode("utf-8"))

    def test_change_status_flow(self):
        order = self._create_order()  # status NEW por defecto
        url = reverse("change_status", args=[order.pk])
        resp = self.client.post(url, {"target": "REV"})
        self.assertEqual(resp.status_code, 302)

        order.refresh_from_db()
        self.assertEqual(order.status, "REV")
        history_entry = StatusHistory.objects.filter(order=order, status="REV").first()
        self.assertIsNotNone(history_entry)
        self.assertEqual(history_entry.from_status, "NEW")

    def test_list_orders_filters_by_status_and_assignee(self):
        tech = self.User.objects.create_user(username="tecnico", password="pass123", is_staff=True)
        other = self.User.objects.create_user(username="otro", password="pass123", is_staff=True)

        order_match = self._create_order()
        order_match.status = ServiceOrder.Status.IN_REVIEW
        order_match.assigned_to = tech
        order_match.save(update_fields=["status", "assigned_to"])

        order_other = self._create_order()
        order_other.status = ServiceOrder.Status.IN_REVIEW
        order_other.assigned_to = other
        order_other.save(update_fields=["status", "assigned_to"])

        resp = self.client.get(
            reverse("list_orders"),
            {
                "status": ServiceOrder.Status.IN_REVIEW,
                "assignee": str(tech.id),
            },
        )
        self.assertEqual(resp.status_code, 200)
        page_orders = list(resp.context["page_obj"].object_list)
        self.assertEqual(len(page_orders), 1)
        self.assertEqual(page_orders[0].pk, order_match.pk)

    def test_list_orders_filters_by_date_range(self):
        order_in = self._create_order()
        order_out = self._create_order()
        target_date = timezone.now() - timedelta(days=2)
        prev_date = timezone.now() - timedelta(days=10)
        ServiceOrder.objects.filter(pk=order_in.pk).update(checkin_at=target_date)
        ServiceOrder.objects.filter(pk=order_out.pk).update(checkin_at=prev_date)
        date_str = timezone.localtime(target_date).date().isoformat()
        resp = self.client.get(
            reverse("list_orders"),
            {"from": date_str, "to": date_str},
        )
        self.assertEqual(resp.status_code, 200)
        page_orders = list(resp.context["page_obj"].object_list)
        self.assertEqual([o.pk for o in page_orders], [order_in.pk])

    def test_list_orders_export_csv_respects_filters(self):
        tech = self.User.objects.create_user(username="csvtech", password="pass123", is_staff=True)
        in_order = self._create_order()
        checkin = timezone.now() - timedelta(days=2)
        checkout = checkin + timedelta(hours=4)
        ServiceOrder.objects.filter(pk=in_order.pk).update(
            assigned_to=tech,
            checkin_at=checkin,
            checkout_at=checkout,
        )
        in_order.refresh_from_db()

        out_order = self._create_order()
        older = timezone.now() - timedelta(days=40)
        ServiceOrder.objects.filter(pk=out_order.pk).update(checkin_at=older)

        date_str = checkin.date().strftime("%Y-%m-%d")
        ServiceOrder.objects.filter(pk=in_order.pk).update(status=ServiceOrder.Status.IN_REVIEW)
        ServiceOrder.objects.filter(pk=out_order.pk).update(status=ServiceOrder.Status.WAITING_PARTS)

        resp = self.client.get(
            reverse("list_orders"),
            {
                "from": date_str,
                "to": date_str,
                "export": "1",
                "status": ServiceOrder.Status.IN_REVIEW,
                "assignee": str(tech.pk),
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "text/csv; charset=utf-8")

        rows = list(csv.reader(StringIO(resp.content.decode("utf-8"))))
        self.assertEqual(
            rows[0],
            ["Folio", "Cliente", "Equipo", "Estado", "Tecnico", "Total", "FechaEntrada", "FechaSalida"],
        )
        self.assertEqual(len(rows), 2)
        data_row = rows[1]
        self.assertEqual(data_row[0], in_order.folio)
        self.assertEqual(data_row[4], tech.get_username())
        self.assertTrue(data_row[6].startswith('="'))
        self.assertTrue(date_str in data_row[6])
        self.assertTrue(data_row[7].startswith('="'))
        self.assertTrue(date_str in data_row[7])

    def test_recepcion_user_cannot_export_csv(self):
        recep_group = Group.objects.create(name=ROLE_RECEPCION)
        recep_user = self.User.objects.create_user(username="recep", password="pass123", is_staff=True)
        recep_user.groups.add(recep_group)

        self.client.logout()
        self.assertTrue(self.client.login(username="recep", password="pass123"))

        resp = self.client.get(reverse("list_orders"), {"export": "1"})
        self.assertEqual(resp.status_code, 403)

    def test_full_order_flow_generates_payment_pdf(self):
        sku = "KIT-01"
        InventoryItem.objects.create(sku=sku, name="Kit reparacion", qty=5, min_qty=1)
        tech = self.User.objects.create_user(username="flujo_tech", password="pass123", is_staff=True)

        new_order_payload = {
            "customer_name": "Cliente Demo",
            "customer_phone": "5551234567",
            "customer_email": "cliente@example.com",
            "notes": "Orden prioridad alta",
            "devices-TOTAL_FORMS": "1",
            "devices-INITIAL_FORMS": "0",
            "devices-MIN_NUM_FORMS": "1",
            "devices-MAX_NUM_FORMS": "5",
            "devices-0-brand": "Dell",
            "devices-0-model": "XPS 15",
            "devices-0-serial": "SN-001",
            "devices-0-notes": "No enciende",
        }
        resp = self.client.post(reverse("reception_new_order"), new_order_payload)
        self.assertEqual(resp.status_code, 302)

        order = ServiceOrder.objects.order_by("-id").first()
        self.assertIsNotNone(order)

        estimate = Estimate.objects.create(order=order)
        EstimateItem.objects.create(
            estimate=estimate,
            description="Servicio general",
            qty=1,
            unit_price=Decimal("500.00"),
        )

        resp = self.client.post(reverse("assign_tech", args=[order.pk]), {"user_id": tech.pk})
        self.assertEqual(resp.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.assigned_to_id, tech.pk)

        resp = self.client.post(
            reverse("add_part", args=[order.pk]),
            {"sku": sku, "qty": "1", "reason": "Diagnostico"},
        )
        self.assertEqual(resp.status_code, 302)
        item = InventoryItem.objects.get(sku=sku)
        self.assertEqual(item.qty, 4)
        self.assertTrue(InventoryMovement.objects.filter(order=order, item=item, delta=-1).exists())

        resp = self.client.post(reverse("change_status", args=[order.pk]), {"target": ServiceOrder.Status.IN_REVIEW})
        self.assertEqual(resp.status_code, 302)
        resp = self.client.post(reverse("change_status", args=[order.pk]), {"target": ServiceOrder.Status.READY_PICKUP})
        self.assertEqual(resp.status_code, 302)

        # Aceptar todas las partidas para permitir cobro
        for est_item in estimate.items.all():
            est_item.status = EstimateItem.Status.ACCEPTED
            est_item.save(update_fields=["status"])
        estimate.recompute_status_from_items(save=True)
        order.refresh_from_db()
        current_balance = order.balance

        resp = self.client.post(
            reverse("add_payment", args=[order.pk]),
            {"amount": f"{current_balance}", "method": "Efectivo", "reference": "ABC123"},
        )
        self.assertEqual(resp.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.balance, Decimal("0.00"))

        resp = self.client.post(reverse("change_status", args=[order.pk]), {"target": ServiceOrder.Status.DELIVERED})
        self.assertEqual(resp.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, ServiceOrder.Status.DELIVERED)
        self.assertIsNotNone(order.checkout_at)

        payment = Payment.objects.filter(order=order).latest("id")
        pdf_resp = self.client.get(reverse("payment_receipt_pdf", args=[payment.pk]))
        self.assertEqual(pdf_resp.status_code, 200)
        self.assertEqual(pdf_resp["Content-Type"], "application/pdf")

    def test_open_warranty_creates_new_order(self):
        order = self._create_order()
        order.status = ServiceOrder.Status.DELIVERED
        order.save(update_fields=["status"])
        order.devices.set([order.device])
        resp = self.client.post(reverse("order_open_warranty", args=[order.pk]))
        self.assertEqual(resp.status_code, 302)
        new_order = ServiceOrder.objects.exclude(pk=order.pk).latest("id")
        self.assertEqual(new_order.warranty_parent, order)
        self.assertEqual(new_order.customer, order.customer)
        self.assertTrue(new_order.devices.exists())

    def test_cancel_order_changes_status(self):
        order = self._create_order()
        resp = self.client.post(reverse("order_cancel", args=[order.pk]))
        self.assertEqual(resp.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, ServiceOrder.Status.CANCELLED)






