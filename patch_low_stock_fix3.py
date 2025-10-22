# patch_low_stock_fix3.py
# Repara el bloque "INTEGRASYS LOW STOCK SIGNAL" en core/models.py:
# - Asegura imports
# - Reescribe el bloque con indentación correcta y sin chocar nombres de clases

import os, shutil, sys, re

ROOT = os.getcwd()
P = lambda *x: os.path.join(ROOT, *x)

def rd(fp): 
    return open(fp, "r", encoding="utf-8").read() if os.path.exists(fp) else ""

def wr(fp, s):
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    with open(fp, "w", encoding="utf-8", newline="\n") as f:
        f.write(s)

def backup(fp):
    if os.path.exists(fp) and not os.path.exists(fp + ".bak"):
        shutil.copy2(fp, fp + ".bak")

models_fp = P("core","models.py")
s = rd(models_fp)
if not s:
    sys.exit("✖ No encontré core/models.py")

# 1) Asegura imports necesarios (sin duplicar)
def ensure_import(txt, stmt):
    if stmt in txt:
        return txt
    # Inserta antes de la primera clase
    i = txt.find("\nclass ")
    if i == -1:
        return txt + "\n" + stmt + "\n"
    return txt[:i] + "\n" + stmt + "\n" + txt[i:]

for imp in [
    "from django.apps import apps",
    "from django.conf import settings",
    "from django.core.mail import send_mail",
    "from django.contrib.auth import get_user_model",
    "from django.db.models.signals import post_save",
    "from django.dispatch import receiver",
]:
    s = ensure_import(s, imp)

# 2) Reemplazar/crear bloque LOW STOCK con identación correcta
mark = "# === INTEGRASYS LOW STOCK SIGNAL ==="
new_block = (
    mark + "\n"
    "try:\n"
    "    InventoryMovementModel = apps.get_model('core','InventoryMovement')\n"
    "    NotificationModel = apps.get_model('core','Notification')\n"
    "except Exception:\n"
    "    InventoryMovementModel = None\n"
    "    NotificationModel = None\n"
    "\n"
    "if InventoryMovementModel is not None:\n"
    "    @receiver(post_save, sender=InventoryMovementModel)\n"
    "    def _integrasys_notify_low_stock(sender, instance, created, **kwargs):\n"
    "        if not created:\n"
    "            return\n"
    "        item = getattr(instance, 'item', None)\n"
    "        if item is None:\n"
    "            return\n"
    "        try:\n"
    "            qty = getattr(item, 'qty', 0) or 0\n"
    "            min_qty = getattr(item, 'min_qty', 0) or 0\n"
    "        except Exception:\n"
    "            qty = 0\n"
    "            min_qty = 0\n"
    "        if qty > min_qty:\n"
    "            return  # no está bajo\n"
    "\n"
    "        sku = getattr(item, 'sku', '')\n"
    "        name = getattr(item, 'name', '')\n"
    "        location = getattr(item, 'location', None) or '-'\n"
    "        subject = f\"Stock bajo: {sku} - {name} (qty {qty} ≤ min {min_qty})\"\n"
    "        body = (\n"
    "            f\"Inventario bajo para {name} ({sku}).\\n\"\n"
    "            f\"Ubicación: {location}\\n\"\n"
    "            f\"Cantidad actual: {qty}\\n\"\n"
    "            f\"Mínimo definido: {min_qty}\\n\"\n"
    "        )\n"
    "        # Notification interna\n"
    "        try:\n"
    "            if NotificationModel is not None:\n"
    "                NotificationModel.objects.create(kind='stock', target=sku, subject=subject, body=body, ok=True)\n"
    "        except Exception:\n"
    "            pass\n"
    "        # Destinatarios: ADMINS/MANAGERS o staff con email\n"
    "        to_emails = []\n"
    "        try:\n"
    "            admins = getattr(settings, 'ADMINS', ())\n"
    "            if admins:\n"
    "                to_emails = [e for _, e in admins if e]\n"
    "            if not to_emails:\n"
    "                managers = getattr(settings, 'MANAGERS', ())\n"
    "                if managers:\n"
    "                    to_emails = [e for _, e in managers if e]\n"
    "            if not to_emails:\n"
    "                User = get_user_model()\n"
    "                to_emails = list(User.objects.filter(is_staff=True).exclude(email='').values_list('email', flat=True)[:5])\n"
    "        except Exception:\n"
    "            to_emails = []\n"
    "        if to_emails:\n"
    "            try:\n"
    "                send_mail(subject, body, getattr(settings, 'DEFAULT_FROM_EMAIL', None), to_emails, fail_silently=True)\n"
    "            except Exception:\n"
    "                pass\n"
)

if mark in s:
    pre = s.split(mark)[0]
    # Reemplaza desde el mark hasta el final del archivo con el bloque limpio
    fixed = pre.rstrip() + "\n\n" + new_block + "\n"
else:
    fixed = s.rstrip() + "\n\n" + new_block + "\n"

backup(models_fp)
wr(models_fp, fixed)
print("✅ Bloque LOW STOCK reescrito con indentación correcta (backup .bak creado).")
print("Ahora ejecuta:  python manage.py runserver")
