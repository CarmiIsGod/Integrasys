from django.db import models
from django.db import transaction, IntegrityError
from django.db.models import Q
from django.contrib.auth.models import User
from django.utils import timezone
from django.apps import apps
from decimal import Decimal, ROUND_HALF_UP
import uuid
import re



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
    notes = models.TextField(blank=True)

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

    device = models.ForeignKey(Device, on_delete=models.CASCADE, db_index=True)
    folio = models.CharField(max_length=20, unique=True, blank=True, editable=False)
    token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.NEW, db_index=True)
    checkin_at = models.DateTimeField(auto_now_add=True, db_index=True)
    checkout_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    assigned_to = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)

    def __str__(self):
        return f"{self.folio} - {self.device}"


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
    status = models.CharField(max_length=10, choices=ServiceOrder.Status.choices, db_index=True)
    author = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)


class Payment(models.Model):
    order = models.ForeignKey(ServiceOrder, on_delete=models.CASCADE, related_name='payments', db_index=True)
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
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)





