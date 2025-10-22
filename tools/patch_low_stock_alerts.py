# patch_low_stock_alerts.py
# Alerta proactiva de inventario bajo (email + Notification) vía signal post_save.
# - No toca vistas ni templates; pura lógica.
# - Idempotente y crea backups .bak.
#
# Ejecuta:
#   python patch_low_stock_alerts.py

import os, shutil, sys

ROOT = os.getcwd()
P = lambda *x: os.path.join(ROOT, *x)

def rd(fp): 
    return open(fp, "r", encoding="utf-8").read() if os.path.exists(fp) else ""

def wr(fp, s):
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    with open(fp, "w", encoding="utf-8") as f: 
        f.write(s)

def backup(fp):
    if os.path.exists(fp) and not os.path.exists(fp + ".bak"):
        shutil.copy2(fp, fp + ".bak")

if not os.path.exists(P("manage.py")):
    sys.exit("✖ Ejecuta este script en la raíz del proyecto (donde está manage.py).")

models_fp = P("core","models.py")
m = rd(models_fp)
if not m:
    sys.exit("✖ No encontré core/models.py")

backup(models_fp)
m0 = m

def ensure_import(txt, stmt):
    if stmt in txt: 
        return txt, False
    i = txt.find("\nclass ")
    if i == -1:
        return (txt + "\n" + stmt + "\n"), True
    return (txt[:i] + "\n" + stmt + "\n" + txt[i:]), True

# Asegurar imports necesarios (solo texto, sin ejecutar Django aquí)
for imp in [
    "from django.apps import apps",
    "from django.conf import settings",
    "from django.core.mail import send_mail",
    "from django.contrib.auth import get_user_model",
    "from django.db.models.signals import post_save",
    "from django.dispatch import receiver",
]:
    m, _ = ensure_import(m, imp)

# Bloque signal (idempotente)
if "INTEGRASYS LOW STOCK SIGNAL" not in m:
    m += r'''

# === INTEGRASYS LOW STOCK SIGNAL ===
try:
    InventoryMovement = apps.get_model('core','InventoryMovement')
    Notification = apps.get_model('core','Notification')
except Exception:
    InventoryMovement = None
    Notification = None

if InventoryMovement is not None:
    @receiver(post_save, sender=InventoryMovement)
    def _integrasys_notify_low_stock(sender, instance, created, **kwargs):
        if not created:
            return
        item = getattr(instance, "item", None)
        if item is None:
            return
        try:
            qty = getattr(item, "qty", 0) or 0
            min_qty = getattr(item, "min_qty", 0) or 0
        except Exception:
            qty = 0; min_qty = 0
        if qty > min_qty:
            return  # no está bajo

        sku = getattr(item, "sku", "")
        name = getattr(item, "name", "")
        location = getattr(item, "location", None) or "-"
        subject = f"Stock bajo: {sku} - {name} (qty {qty} ≤ min {min_qty})"
        body = (
            f"Inventario bajo para {name} ({sku}).\n"
            f"Ubicación: {location}\n"
            f"Cantidad actual: {qty}\n"
            f"Mínimo definido: {min_qty}\n"
        )
        # Notification interna
        try:
            if Notification is not None:
                Notification.objects.create(kind="stock", target=sku, subject=subject, body=body, ok=True)
        except Exception:
            pass
        # Destinatarios: ADMINS/MANAGERS o staff con email
        to_emails = []
        try:
            admins = getattr(settings, "ADMINS", ())
            if admins:
                to_emails = [e for _, e in admins if e]
            if not to_emails:
                managers = getattr(settings, "MANAGERS", ())
                if managers:
                    to_emails = [e for _, e in managers if e]
            if not to_emails:
                User = get_user_model()
                to_emails = list(User.objects.filter(is_staff=True).exclude(email="").values_list("email", flat=True)[:5])
        except Exception:
            to_emails = []
        if to_emails:
            try:
                send_mail(subject, body, getattr(settings, "DEFAULT_FROM_EMAIL", None), to_emails, fail_silently=True)
            except Exception:
                pass
'''
    wr(models_fp, m)

print("✅ Parche aplicado en core/models.py (backup .bak creado).")
print("\nPrueba rápida:")
print(" 1) Usa una refacción hasta dejar qty ≤ min_qty (desde el detalle de la orden, 'Refacciones usadas').")
print(" 2) Verifica que se cree un Notification(kind='stock').")
print(" 3) Si tienes ADMINS/MANAGERS o staff con email y EMAIL_* configurado, debe llegar correo.")
