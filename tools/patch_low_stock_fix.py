# patch_low_stock_fix.py
# Arregla el bloqueo: no volver a pisar InventoryMovement/Notification en core/models.py

import os, shutil, sys, re

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

models_fp = P("core","models.py")
s = rd(models_fp)
if not s:
    sys.exit("✖ No encontré core/models.py")

mark = "# === INTEGRASYS LOW STOCK SIGNAL ==="
i = s.find(mark)
if i == -1:
    print("No encontré el bloque de LOW STOCK; nada que cambiar.")
    sys.exit(0)

pre, seg = s[:i], s[i:]

# 1) Renombrar variables que chocan con clases del módulo
seg = re.sub(r"(?m)^\s*InventoryMovement\s*=\s*apps\.get_model\('core','InventoryMovement'\)\s*$",
             "InventoryMovementModel = apps.get_model('core','InventoryMovement')", seg)
seg = re.sub(r"(?m)^\s*Notification\s*=\s*apps\.get_model\('core','Notification'\)\s*$",
             "NotificationModel = apps.get_model('core','Notification')", seg)

# 2) Ajustar el decorador y usos dentro del bloque
seg = seg.replace("sender=InventoryMovement)", "sender=InventoryMovementModel)")
seg = seg.replace("if Notification is not None:", "if NotificationModel is not None:")
seg = seg.replace("Notification.objects", "NotificationModel.objects")

out = pre + seg

backup(models_fp)
wr(models_fp, out)
print("✅ Arreglo aplicado en core/models.py (backup .bak creado).")
print("Ahora reinicia el servidor.")
