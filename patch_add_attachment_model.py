# patch_add_attachment_model.py
# Asegura que exista el modelo Attachment y lo registra en admin (idempotente).
import os, sys, re

ROOT = os.getcwd()
def here(*p): return os.path.join(ROOT, *p)
def rd(p): return open(p, "r", encoding="utf-8").read() if os.path.exists(p) else ""
def wr(p, s):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f: f.write(s)

models_py = here("core", "models.py")
admin_py  = here("core", "admin.py")
if not os.path.exists(models_py):
    sys.exit("✖ No encontré core/models.py. Ejecuta desde la raíz del repo.")

txt = rd(models_py)
changed = False

if "from django.db import models" not in txt:
    txt = "from django.db import models\n" + txt
    changed = True

if re.search(r"\bclass\s+Attachment\s*\(", txt) is None:
    txt += """
# === INTEGRASYS PATCH: Attachment model ===
class Attachment(models.Model):
    service_order = models.ForeignKey('ServiceOrder', related_name='attachments', on_delete=models.CASCADE)
    file = models.FileField(upload_to='attachments/%Y/%m/%d/')
    caption = models.CharField(max_length=255, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.file.name if self.file else 'Attachment'} (order #{self.service_order_id})"
"""
    changed = True

if changed:
    wr(models_py, txt)

adm = rd(admin_py)
if "from django.contrib import admin" not in adm:
    adm = "from django.contrib import admin\n" + adm
if "from core.models import Attachment" not in adm:
    adm += "\nfrom core.models import Attachment\n"
if "@admin.register(Attachment)" not in adm and "class AttachmentAdmin" not in adm:
    adm += """
# === INTEGRASYS PATCH: Attachment admin ===
@admin.register(Attachment)
class AttachmentAdmin(admin.ModelAdmin):
    list_display = ("id", "service_order", "file", "caption", "uploaded_at")
    search_fields = ("caption", "file")
    list_filter = ("uploaded_at",)
"""
wr(admin_py, adm)

print("[OK] Attachment asegurado y admin registrado.")
print("Ahora ejecuta:")
print("  1) python manage.py makemigrations core")
print("  2) python manage.py migrate")
print("  3) python manage.py runserver")
