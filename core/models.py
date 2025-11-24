from django.db import models
from django.db import transaction, IntegrityError
from django.db.models import Q
from django.contrib.auth.models import User
from django.utils import timezone
from django.apps import apps
from decimal import Decimal, ROUND_HALF_UP
import uuid
import re



from django.conf import settings

from django.core.mail import send_mail

from django.contrib.auth import get_user_model

from django.db.models.signals import post_save

from django.dispatch import receiver

class Customer(models.Model):
    name = models.CharField(max_length=120, db_index=True)
    phone = models.CharField(max_length=30, blank=True, db_index=True)
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

    STATUS_TRANSITIONS = {
        Status.NEW: (Status.IN_REVIEW,),
        Status.IN_REVIEW: (Status.WAITING_PARTS, Status.REQUIRES_AUTH, Status.READY_PICKUP),
        Status.WAITING_PARTS: (Status.IN_REVIEW, Status.READY_PICKUP),
        Status.REQUIRES_AUTH: (Status.IN_REVIEW, Status.READY_PICKUP),
        Status.READY_PICKUP: (Status.DELIVERED,),
        Status.DELIVERED: (),
    }

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

    def can_transition_to(self, new_status):
        return new_status in self.allowed_next_statuses()

    def transition_to(self, new_status, *, author=None, author_role="", force=False):
        previous_status = self.status
        if not force and previous_status == new_status:
            return previous_status
        if not force and new_status not in self.allowed_next_statuses():
            raise ValueError(f"Transicion no permitida de {previous_status} a {new_status}")
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
        )
        return previous_status


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
        EstimateItem = apps.get_model(self._meta.app_label, "EstimateItem")
        if not Estimate or not EstimateItem:
            return Decimal("0.00")
        try:
            estimate = self.estimate
        except Estimate.DoesNotExist:
            return Decimal("0.00")
        aggregation = estimate.items.aggregate(
            total=models.Sum(
                models.F("qty") * models.F("unit_price"),
                output_field=models.DecimalField(max_digits=12, decimal_places=2),
            )
        )
        total = aggregation.get("total")
        return self._quantize_amount(total)

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


class StatusHistory(models.Model):
    order = models.ForeignKey(ServiceOrder, on_delete=models.CASCADE, related_name="history", db_index=True)
    from_status = models.CharField(max_length=10, choices=ServiceOrder.Status.choices, blank=True, default="")
    status = models.CharField(max_length=10, choices=ServiceOrder.Status.choices, db_index=True)
    author = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    author_role = models.CharField(max_length=20, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    @classmethod
    def log(cls, order, *, from_status="", to_status=None, author=None, author_role=""):
        target_status = to_status or getattr(order, "status", "")
        return cls.objects.create(
            order=order,
            from_status=from_status or "",
            status=target_status,
            author=author,
            author_role=author_role or "",
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

    def __str__(self):
        return f"Estimate {self.order.folio}"


class EstimateItem(models.Model):
    estimate = models.ForeignKey(Estimate, on_delete=models.CASCADE, related_name="items")
    description = models.CharField(max_length=140)
    qty = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    inventory_item = models.ForeignKey(InventoryItem, null=True, blank=True, on_delete=models.SET_NULL)

    def __str__(self):
        return f"{self.description} x{self.qty}"


class Notification(models.Model):
    order = models.ForeignKey(ServiceOrder, null=True, blank=True, on_delete=models.CASCADE, db_index=True)
    kind = models.CharField(max_length=20, db_index=True)     
    channel = models.CharField(max_length=20, db_index=True)  
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
