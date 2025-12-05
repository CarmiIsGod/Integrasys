from django.db import models
from django.db import transaction, IntegrityError
from django.db.models import Q, Count
from django.contrib.auth.models import User
from django.utils import timezone
from django.apps import apps
from decimal import Decimal, ROUND_HALF_UP
import uuid
import re

import logging


from django.conf import settings

from django.core.mail import send_mail

from django.contrib.auth import get_user_model

from django.db.models.signals import post_save

from django.dispatch import receiver


IVA_RATE = Decimal("0.16")
logger = logging.getLogger(__name__)

class Customer(models.Model):
    name = models.CharField(max_length=120, db_index=True)
    phone = models.CharField(max_length=30, blank=True, db_index=True)
    alt_phone = models.CharField(max_length=30, blank=True, db_index=True, default="")
    email = models.EmailField(blank=True, db_index=True)

    def __str__(self):
        return self.name


class Device(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, db_index=True)
    brand = models.CharField(max_length=60, blank=True)
    model = models.CharField(max_length=60, blank=True)
    serial = models.CharField(max_length=120, blank=True, db_index=True)
    notes = models.TextField(blank=True, default="")
    password_notes = models.TextField(blank=True, default="")
    accessories_notes = models.TextField(blank=True, default="")

    def __str__(self):
        return f"{self.brand} {self.model} ({self.serial})"



def generate_folio():
    try:
        year = timezone.localdate().year
    except Exception:
        year = timezone.now().year
        
    suffix = f"-{year}"
    qs = ServiceOrder.objects.filter(folio__endswith=suffix).values_list("folio", flat=True)
        
    max_n = 0
    patt = re.compile(rf"^SR-(\d{{4,}})-{year}$")
    for f in qs:
        if not f:
            continue
        m = patt.match(f)
        if not m:
            continue
        try:
            n = int(m.group(1))
            if n > max_n:
                max_n = n
        except ValueError:
            continue
    return f"SR-{(max_n + 1):04d}-{year}"


class ServiceOrder(models.Model):
    class Status(models.TextChoices):
        NEW = "NEW", "Recibido"
        IN_REVIEW = "REV", "En revision"
        WAITING_PARTS = "WAI", "En espera de repuestos"
        REQUIRES_AUTH = "AUTH", "Requiere autorizacion de repuestos"
        READY_PICKUP = "READY", "Listo para recoger"
        DELIVERED = "DONE", "Entregado"
        CANCELLED = "CANC", "Cancelado"

    STATUS_TRANSITIONS = {
        Status.NEW: (Status.IN_REVIEW, Status.CANCELLED),
        Status.IN_REVIEW: (Status.WAITING_PARTS, Status.REQUIRES_AUTH, Status.READY_PICKUP, Status.CANCELLED),
        Status.WAITING_PARTS: (Status.IN_REVIEW, Status.READY_PICKUP, Status.CANCELLED),
        Status.REQUIRES_AUTH: (Status.IN_REVIEW, Status.READY_PICKUP, Status.CANCELLED),
        Status.READY_PICKUP: (Status.DELIVERED, Status.CANCELLED),
        Status.DELIVERED: (Status.CANCELLED,),
        Status.CANCELLED: (),
    }
    FINAL_STATUSES = {Status.DELIVERED, Status.CANCELLED}
    TECHNICIAN_ALLOWED_TARGETS = {Status.IN_REVIEW, Status.WAITING_PARTS, Status.READY_PICKUP}

    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        db_index=True,
        related_name="orders",
        null=True,
        blank=True,
    )
    device = models.ForeignKey(Device, on_delete=models.CASCADE, db_index=True)
    devices = models.ManyToManyField(Device, related_name="orders", blank=True)
    warranty_parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="warranty_children",
    )
    folio = models.CharField(max_length=20, unique=True, blank=True, editable=False)
    token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.NEW, db_index=True)
    checkin_at = models.DateTimeField(auto_now_add=True, db_index=True)
    checkout_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    assigned_to = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)

    def __str__(self):
        device_label = self.primary_device_label()
        if device_label:
            return f"{self.folio} - {device_label}"
        return self.folio

    def get_customer(self):
        if self.customer_id:
            return self.customer
        if self.device_id:
            return getattr(self.device, "customer", None)
        return None

    def primary_device(self):
        if hasattr(self, "_primary_device_cache"):
            return self._primary_device_cache
        device = None
        try:
            device = next(iter(self.devices.all()))
        except StopIteration:
            device = None
        if not device and self.device_id:
            device = self.device
        self._primary_device_cache = device
        return device

    def primary_device_label(self):
        device = self.primary_device()
        if not device:
            return ""
        parts = [value for value in (device.brand, device.model) if value]
        serial = getattr(device, "serial", "")
        if serial:
            serial_clean = serial.strip()
            if serial_clean:
                parts.append(f"({serial_clean})")
        return " ".join(parts).strip()


    def save(self, *args, **kwargs):
        """Persist with robust folio retry and checkout timestamping.

        - Retries up to 3 times to generate a unique folio if a concurrent
          insert triggers an IntegrityError on the unique constraint.
        - When status transitions to DELIVERED and checkout_at is empty,
          set checkout_at to the current time. Never clear it afterwards.
        """
        # Stamp checkout_at when transitioning to DELIVERED (do not unset later)
        try:
            old_status = None
            if self.pk:
                old = ServiceOrder.objects.only("status", "checkout_at").get(pk=self.pk)
                old_status = old.status
            if (
                old_status is not None
                and old_status != self.status
                and self.status == ServiceOrder.Status.DELIVERED
                and not self.checkout_at
            ):
                self.checkout_at = timezone.now()
        except ServiceOrder.DoesNotExist:
            pass

        if not self.customer_id and self.device_id:
            self.customer = getattr(self.device, "customer", None)

        last_error = None
        for _ in range(3):
            if not self.folio:
                self.folio = generate_folio()
            try:
                with transaction.atomic():
                    super().save(*args, **kwargs)
                last_error = None
                break
            except IntegrityError as exc:
                # Likely unique collision on folio due to concurrent save; retry.
                last_error = exc
                self.folio = ""
                continue
        if last_error:
            raise last_error

    @classmethod
    def allowed_transitions(cls):
        return cls.STATUS_TRANSITIONS

    def allowed_next_statuses(self):
        return list(self.STATUS_TRANSITIONS.get(self.status, ()))

    def allowed_next_statuses_for_user(self, user):
        """Return allowed transitions filtered by role restrictions to avoid exposing invalid buttons."""
        allowed = self.allowed_next_statuses()
        roles = self._resolve_role_flags(user)
        user_id = getattr(user, "id", None)
        if roles["is_tecnico"] and self.assigned_to_id and self.assigned_to_id != user_id:
            return []
        filtered = []
        try:
            from core.permissions import can_mark_order_done  # local import to avoid circular at module load
        except Exception:
            can_mark_order_done = lambda _u: False  # type: ignore
        for code in allowed:
            if roles["is_tecnico"] and code not in self.TECHNICIAN_ALLOWED_TARGETS:
                continue
            if code in self.FINAL_STATUSES and not (can_mark_order_done(user) or roles["is_superuser"] or roles["is_manager"] or roles["is_recepcion"]):
                continue
            filtered.append(code)
        return filtered

    @staticmethod
    def _resolve_role_flags(user):
        flags = {
            "is_authenticated": False,
            "is_superuser": False,
            "is_manager": False,
            "is_recepcion": False,
            "is_tecnico": False,
        }
        if not user or not getattr(user, "is_authenticated", False):
            return flags
        flags["is_authenticated"] = True
        flags["is_superuser"] = getattr(user, "is_superuser", False)
        if flags["is_superuser"]:
            flags["is_manager"] = True
            return flags
        try:
            from core.permissions import is_gerencia, is_recepcion, is_tecnico

            flags["is_manager"] = is_gerencia(user)
            flags["is_recepcion"] = is_recepcion(user)
            flags["is_tecnico"] = is_tecnico(user)
        except Exception:
            pass
        return flags

    @property
    def has_open_warranty_children(self):
        open_statuses = [code for code, _ in self.Status.choices if code not in self.FINAL_STATUSES]
        try:
            return self.warranty_children.filter(status__in=open_statuses).exists()
        except Exception:
            return False

    def can_transition_to(self, new_status, *, user=None, allow_reopen=False, force=False):
        ok, _ = self.validate_transition(new_status, user=user, allow_reopen=allow_reopen, force=force)
        return ok

    def validate_transition(self, new_status, *, user=None, allow_reopen=False, force=False):
        target = str(new_status)
        if not force:
            allowed = self.allowed_next_statuses()
            if not (allow_reopen and self.status in self.FINAL_STATUSES and target == self.Status.IN_REVIEW):
                if target not in allowed:
                    return False, f"Transicion no permitida de {self.status} a {target}"
        roles = self._resolve_role_flags(user)
        if roles["is_tecnico"]:
            user_id = getattr(user, "id", None)
            if self.assigned_to_id and self.assigned_to_id != user_id:
                return False, "Solo el tecnico asignado puede mover la orden."
            if target not in self.TECHNICIAN_ALLOWED_TARGETS:
                return False, "Los tecnicos solo pueden mover a Revision, Espera o Listo."
        if target == self.Status.DELIVERED:
            try:
                from core.permissions import can_mark_order_done  # local import to avoid circular import
                if not can_mark_order_done(user):
                    return False, "No tienes permisos para marcar como entregado."
            except Exception:
                return False, "No tienes permisos para marcar como entregado."
            balance_value = getattr(self, "balance", None)
            if balance_value is not None and balance_value > Decimal("0.00"):
                return False, "No puedes entregar con saldo pendiente."
            if self.has_open_warranty_children:
                return False, "No puedes entregar mientras existan garantias abiertas."
        if target == self.Status.CANCELLED and not force:
            if not (roles["is_superuser"] or roles["is_manager"]):
                return False, "Solo gerencia o superusuario puede cancelar."
        return True, None

    def transition_to(self, new_status, *, author=None, author_role="", force=False, reason=None, allow_reopen=False):
        ok, error = self.validate_transition(new_status, user=author, allow_reopen=allow_reopen, force=force)
        if not ok:
            raise ValueError(error or f"Transicion no permitida de {self.status} a {new_status}")
        previous_status = self.status
        if not force and previous_status == new_status:
            return previous_status
        update_fields = ["status"]
        self.status = new_status
        if new_status == ServiceOrder.Status.DELIVERED and not self.checkout_at:
            self.checkout_at = timezone.now()
            update_fields.append("checkout_at")
        self.save(update_fields=update_fields)
        from_status_value = previous_status or ""
        StatusHistory.log(
            order=self,
            from_status=from_status_value,
            to_status=new_status,
            author=author,
            author_role=author_role or "",
            note=reason or "",
        )
        if new_status == ServiceOrder.Status.DELIVERED:
            try:
                author_username = ""
                if author is not None:
                    author_username = getattr(author, "get_username", lambda: "")() or getattr(author, "username", "") or ""
                logger.info(
                    "order_marked_done order=%s folio=%s from=%s warranty=%s user=%s role=%s force=%s allow_reopen=%s",
                    self.pk,
                    getattr(self, "folio", ""),
                    previous_status,
                    self.is_warranty,
                    author_username,
                    author_role,
                    force,
                    allow_reopen,
                )
            except Exception:
                logger.exception("No se pudo registrar log de orden entregada")
        return previous_status

    def reopen(self, *, author=None, author_role="", reason=""):
        reason_value = (reason or "").strip()
        if self.status not in self.FINAL_STATUSES:
            raise ValueError("Solo se pueden reabrir ordenes finalizadas.")
        if not reason_value:
            raise ValueError("Debes capturar el motivo de la reapertura.")
        return self.transition_to(
            self.Status.IN_REVIEW,
            author=author,
            author_role=author_role,
            force=True,
            reason=f"Reapertura: {reason_value}",
            allow_reopen=True,
        )


    @staticmethod
    def _quantize_amount(value):
        if value is None:
            return Decimal("0.00")
        if not isinstance(value, Decimal):
            value = Decimal(value)
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


    @property
    def approved_total(self):
        Estimate = apps.get_model(self._meta.app_label, "Estimate")
        if not Estimate:
            return Decimal("0.00")
        try:
            estimate = self.estimate
        except Estimate.DoesNotExist:
            return Decimal("0.00")
        try:
            _, _, accepted_total = estimate.accepted_totals()
        except Exception:
            aggregation = estimate.items.aggregate(
                total=models.Sum(
                    models.F("qty") * models.F("unit_price"),
                    output_field=models.DecimalField(max_digits=12, decimal_places=2),
                )
            )
            accepted_total = aggregation.get("total")
        return self._quantize_amount(accepted_total)

    @property
    def approved_estimate_total(self):
        return self.approved_total

    @property
    def paid_total(self):
        Payment = apps.get_model(self._meta.app_label, "Payment")
        if Payment is None:
            return self._quantize_amount(0)
        agg = Payment.objects.filter(order=self).aggregate(total=models.Sum("amount"))
        return self._quantize_amount(agg.get("total") or 0)

    @property
    def balance(self):
        approved = self.approved_total
        paid = self.paid_total
        return self._quantize_amount(approved - paid)

    @property
    def is_warranty(self):
        return bool(self.warranty_parent_id)


class StatusHistory(models.Model):
    order = models.ForeignKey(ServiceOrder, on_delete=models.CASCADE, related_name="history", db_index=True)
    from_status = models.CharField(max_length=10, choices=ServiceOrder.Status.choices, blank=True, default="")
    status = models.CharField(max_length=10, choices=ServiceOrder.Status.choices, db_index=True)
    author = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    author_role = models.CharField(max_length=20, blank=True, default="")
    note = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    @classmethod
    def log(cls, order, *, from_status="", to_status=None, author=None, author_role="", note=""):
        target_status = to_status or getattr(order, "status", "")
        return cls.objects.create(
            order=order,
            from_status=from_status or "",
            status=target_status,
            author=author,
            author_role=author_role or "",
            note=note or "",
        )


class Payment(models.Model):
    order = models.ForeignKey(ServiceOrder, on_delete=models.CASCADE, related_name='payments', db_index=True)
    device = models.ForeignKey(Device, null=True, blank=True, on_delete=models.SET_NULL, related_name="payments")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    method = models.CharField(max_length=30, blank=True)
    reference = models.CharField(max_length=80, blank=True)
    author = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']


class InventoryItem(models.Model):
    sku = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=120, db_index=True)
    qty = models.IntegerField(default=0)
    min_qty = models.IntegerField(default=0)  # umbral para alertar bajo stock
    location = models.CharField(max_length=60, blank=True)

    def __str__(self):
        return f"{self.sku} - {self.name}"


class InventoryMovement(models.Model):
    item = models.ForeignKey(InventoryItem, on_delete=models.CASCADE, db_index=True)
    delta = models.IntegerField() 
    reason = models.CharField(max_length=140, blank=True)
    order = models.ForeignKey(ServiceOrder, null=True, blank=True, on_delete=models.SET_NULL)
    author = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        constraints = [
            models.CheckConstraint(check=~Q(delta=0), name="inv_delta_nonzero"),
        ]

class Estimate(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pendiente"
        CLOSED_ACCEPTED = "CLOSED_ACCEPTED", "Cerrada · aprobada"
        CLOSED_PARTIAL = "CLOSED_PARTIAL", "Cerrada · aceptacion parcial"
        CLOSED_REJECTED = "CLOSED_REJECTED", "Cerrada · rechazada"

    order = models.OneToOneField(ServiceOrder, on_delete=models.CASCADE, related_name="estimate")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    declined_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    tax = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    apply_tax = models.BooleanField(default=True)
    inventory_applied = models.BooleanField(default=False)
    token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )

    def __str__(self):
        return f"Estimate {self.order.folio}"

    def accepted_totals(self):
        """Return (subtotal, tax, total) using only accepted items."""
        status_code = getattr(EstimateItem.Status, "ACCEPTED", "ACC")
        aggregation = self.items.filter(status=status_code).aggregate(
            subtotal=models.Sum(
                models.F("qty") * models.F("unit_price"),
                output_field=models.DecimalField(max_digits=12, decimal_places=2),
            )
        )
        raw_subtotal = aggregation.get("subtotal") or Decimal("0.00")
        subtotal = raw_subtotal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        tax = Decimal("0.00")
        if getattr(self, "apply_tax", True):
            tax = (subtotal * IVA_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        total = (subtotal + tax).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return subtotal, tax, total

    @property
    def has_pending_items(self):
        return self.items.filter(status=EstimateItem.Status.PENDING).exists()

    def recompute_status_from_items(self, *, save=True):
        """Recalculate status based on item decisions."""
        counts = {
            EstimateItem.Status.PENDING: 0,
            EstimateItem.Status.ACCEPTED: 0,
            EstimateItem.Status.REJECTED: 0,
        }
        for row in self.items.values("status").annotate(total=Count("id")):
            if row["status"] in counts:
                counts[row["status"]] = row["total"]
        pending = counts[EstimateItem.Status.PENDING]
        accepted = counts[EstimateItem.Status.ACCEPTED]
        rejected = counts[EstimateItem.Status.REJECTED]
        total = pending + accepted + rejected

        if pending > 0:
            new_status = self.Status.PENDING
        elif accepted == total:
            new_status = self.Status.CLOSED_ACCEPTED
        elif rejected == total:
            new_status = self.Status.CLOSED_REJECTED
        elif accepted > 0 and rejected > 0:
            new_status = self.Status.CLOSED_PARTIAL
        else:
            new_status = self.Status.PENDING

        updated_fields = []
        if self.status != new_status:
            self.status = new_status
            updated_fields.append("status")
        if save and updated_fields:
            self.save(update_fields=updated_fields)
        return new_status

    @property
    def is_closed(self):
        return self.status in {
            self.Status.CLOSED_ACCEPTED,
            self.Status.CLOSED_PARTIAL,
            self.Status.CLOSED_REJECTED,
        }


class EstimateItem(models.Model):
    class Status(models.TextChoices):
        PENDING = "PEN", "Pendiente"
        ACCEPTED = "ACC", "Aceptada"
        REJECTED = "REJ", "Rechazada"

    estimate = models.ForeignKey(Estimate, on_delete=models.CASCADE, related_name="items")
    description = models.CharField(max_length=140)
    qty = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    status = models.CharField(
        max_length=3,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    inventory_item = models.ForeignKey(InventoryItem, null=True, blank=True, on_delete=models.SET_NULL)

    def __str__(self):
        return f"{self.description} x{self.qty}"


class Notification(models.Model):
    order = models.ForeignKey(ServiceOrder, null=True, blank=True, on_delete=models.CASCADE, db_index=True)
    kind = models.CharField(max_length=40, db_index=True)
    channel = models.CharField(max_length=40, db_index=True)
    ok = models.BooleanField(default=False)
    payload = models.JSONField(default=dict, blank=True)
    title = models.CharField(max_length=140, blank=True)
    seen_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)






# === INTEGRASYS PATCH: Attachment model ===
class Attachment(models.Model):
    service_order = models.ForeignKey('ServiceOrder', related_name='attachments', on_delete=models.CASCADE)
    file = models.FileField(upload_to='attachments/%Y/%m/%d/')
    caption = models.CharField(max_length=255, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.file.name if self.file else 'Attachment'} (order #{self.service_order_id})"

# === INTEGRASYS LOW STOCK SIGNAL ===
try:
    InventoryMovementModel = apps.get_model('core','InventoryMovement')
    NotificationModel = apps.get_model('core','Notification')
except Exception:
    InventoryMovementModel = None
    NotificationModel = None

if InventoryMovementModel is not None:
    @receiver(post_save, sender=InventoryMovementModel)
    def _integrasys_notify_low_stock(sender, instance, created, **kwargs):
        if not created:
            return
        item = getattr(instance, 'item', None)
        if item is None:
            return
        try:
            qty = getattr(item, 'qty', 0) or 0
            min_qty = getattr(item, 'min_qty', 0) or 0
        except Exception:
            qty = 0
            min_qty = 0
        if qty > min_qty:
            return  # stock is above threshold

        sku = getattr(item, 'sku', '')
        name = getattr(item, 'name', '')
        location = getattr(item, 'location', None) or '-'
        subject = f"Stock bajo: {sku} - {name} (qty {qty} <= min {min_qty})"
        body_lines = [
            f"Inventario bajo para {name} ({sku}).",
            f"Ubicacion: {location}",
            f"Cantidad actual: {qty}",
            f"Minimo definido: {min_qty}",
        ]
        body = "\n".join(body_lines)
        payload = {
            "sku": sku,
            "name": name,
            "qty": qty,
            "min_qty": min_qty,
            "location": location,
        }
        if NotificationModel is not None:
            NotificationModel.objects.create(
                order=None,
                kind='stock',
                channel='low_stock_signal',
                ok=True,
                payload=payload,
            )
        # Destinatarios: ADMINS/MANAGERS o staff con email
        to_emails = []
        try:
            admins = getattr(settings, 'ADMINS', ())
            if admins:
                to_emails = [e for _, e in admins if e]
            if not to_emails:
                managers = getattr(settings, 'MANAGERS', ())
                if managers:
                    to_emails = [e for _, e in managers if e]
            if not to_emails:
                User = get_user_model()
                to_emails = list(
                    User.objects.filter(is_staff=True)
                    .exclude(email='')
                    .values_list('email', flat=True)[:5]
                )
        except Exception:
            to_emails = []
        email_ok = False
        email_error = None
        if to_emails:
            try:
                send_count = send_mail(
                    subject,
                    body,
                    getattr(settings, 'DEFAULT_FROM_EMAIL', None),
                    to_emails,
                    fail_silently=False,
                )
                email_ok = send_count > 0
            except Exception as exc:
                email_error = str(exc)
        else:
            email_error = "no_recipients"
        if NotificationModel is not None:
            email_payload = {**payload, "recipients": list(to_emails)}
            if email_error:
                email_payload["error"] = email_error
            NotificationModel.objects.create(
                order=None,
                kind='email',
                channel='low_stock',
                ok=email_ok,
                payload=email_payload,
            )
