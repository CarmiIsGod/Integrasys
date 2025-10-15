# patch_all_stable.py
# ✅ Parche maestro, estable, con backups .bak e idempotente.
# - Adjuntos: vista subir/borrar, template, widget multiple, rutas, /media
# - Botones "Adjuntos" en lista/detalle
# - Export CSV de órdenes con filtros
# - Protege /panel (dashboard) para staff/superuser
# - Arregla imports (include, re_path) y mapeo /media en dev
#
# Ejecuta desde la raíz (donde está manage.py):
#   python patch_all_stable.py

import os, re, shutil, sys

ROOT = os.getcwd()
P = lambda *x: os.path.join(ROOT, *x)

def rd(fp): return open(fp, "r", encoding="utf-8").read() if os.path.exists(fp) else ""
def wr(fp, s):
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    with open(fp, "w", encoding="utf-8") as f: f.write(s)
def backup(fp):
    if os.path.exists(fp) and not os.path.exists(fp + ".bak"):
        shutil.copy2(fp, fp + ".bak")

def ensure_header_import(txt: str, line: str) -> (str, bool):
    if line in txt: return txt, False
    # Inserta justo al inicio, antes de cualquier otra cosa
    return (line + "\n" + txt), True

def ensure_after_first_import(txt: str, line: str) -> (str, bool):
    if line in txt: return txt, False
    lines = txt.splitlines()
    idx = None
    for i, L in enumerate(lines[:50]):
        if L.strip().startswith(("from ", "import ")):
            idx = i
            break
    if idx is None:
        return (line + "\n" + txt), True
    # Inserta después del primer import
    lines.insert(idx+1, line)
    return ("\n".join(lines) + ("\n" if not txt.endswith("\n") else "")), True

def ensure_urls_imports(u: str) -> (str, bool):
    changed = False
    # from django.urls import path, include, re_path
    m = re.search(r"from\s+django\.urls\s+import\s+([^\n]+)", u)
    if m:
        items = [x.strip() for x in m.group(1).split(",")]
        for need in ["path", "include", "re_path"]:
            if need not in items: items.append(need)
        rep = "from django.urls import " + ", ".join(dict.fromkeys(items))
        u = u[:m.start()] + rep + u[m.end():]
        changed = True
    else:
        u = "from django.urls import path, include, re_path\n" + u
        changed = True
    # settings/static
    if "from django.conf import settings" not in u:
        u = u.replace("from django.urls", "from django.conf import settings\nfrom django.conf.urls.static import static\nfrom django.urls")
        changed = True
    if "from django.conf.urls.static import static" not in u:
        u = u.replace("from django.conf import settings", "from django.conf import settings\nfrom django.conf.urls.static import static")
        changed = True
    # serve
    if "from django.views.static import serve" not in u:
        u += "\nfrom django.views.static import serve  # INTEGRASYS\n"
        changed = True
    return u, changed

def ensure_media_patterns(u: str) -> (str, bool):
    changed = False
    if "static(settings.MEDIA_URL" not in u:
        u += "\nif settings.DEBUG:\n    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)\n"
        changed = True
    if "re_path(r'^media/(?P<path>.*)'" not in u:
        u += "\n# Fallback para /media/ en dev aunque DEBUG sea False\n"
        u += "urlpatterns += [re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT})]\n"
        changed = True
    return u, changed

changed_files = []

# ------- 0) Comprobaciones mínimas -------
if not os.path.exists(P("manage.py")):
    sys.exit("✖ Ejecuta este script desde la raíz del proyecto (donde está manage.py).")

# ======= 1) core/models.py: asegurar modelo Attachment (no pisa lo existente) =======
models_py = P("core", "models.py")
if not os.path.exists(models_py):
    sys.exit("✖ No encontré core/models.py")

backup(models_py)
m = rd(models_py)
m0 = m
if "from django.db import models" not in m:
    m = "from django.db import models\n" + m
if re.search(r"\bclass\s+Attachment\s*\(", m) is None:
    m += """

# === INTEGRASYS PATCH: Attachment model ===
class Attachment(models.Model):
    service_order = models.ForeignKey('ServiceOrder', related_name='attachments', on_delete=models.CASCADE)
    file = models.FileField(upload_to='attachments/%Y/%m/%d/')
    caption = models.CharField(max_length=255, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.file.name if self.file else 'Attachment'} (order #{self.service_order_id})"
"""
if m != m0:
    wr(models_py, m); changed_files.append("core/models.py")

# ======= 2) core/admin.py: registrar Attachment =======
admin_py = P("core", "admin.py")
backup(admin_py)
adm = rd(admin_py)
adm0 = adm
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
if adm != adm0:
    wr(admin_py, adm); changed_files.append("core/admin.py")

# ======= 3) core/forms.py: widget multiupload seguro =======
forms_py = P("core", "forms.py")
backup(forms_py)
f = rd(forms_py)
if f:
    f0 = f
    if "class MultiFileInput(" not in f:
        # inserta clase tras import forms
        if "from django import forms" in f:
            f = f.replace("from django import forms", "from django import forms\n\nclass MultiFileInput(forms.ClearableFileInput):\n    allow_multiple_selected = True\n")
        else:
            f = "from django import forms\n\nclass MultiFileInput(forms.ClearableFileInput):\n    allow_multiple_selected = True\n\n" + f
    # reemplaza cualquier ClearableFileInput(...) por MultiFileInput(...)
    f = re.sub(r"forms\.ClearableFileInput\(", "MultiFileInput(", f)
    if f != f0:
        wr(forms_py, f); changed_files.append("core/forms.py")

# ======= 4) core/views.py: vistas adjuntos + delete + CSV + protege dashboard =======
views_py = P("core", "views.py")
backup(views_py)
v = rd(views_py)
if not v:
    sys.exit("✖ No encontré core/views.py")

v0 = v
# Imports necesarios
for imp in [
    "from django.contrib import messages",
    "from django.contrib.auth.decorators import login_required, user_passes_test",
    "from django.shortcuts import get_object_or_404, redirect, render",
    "from django.views.decorators.http import require_POST",
    "from django.http import HttpResponse",
    "from django.db.models import Q",
    "from django.apps import apps",
    "import csv",
]:
    v, ch = ensure_after_first_import(v, imp)
    if ch: pass

# Model getters
if "ServiceOrder = apps.get_model('core', 'ServiceOrder')" not in v:
    v += "\nServiceOrder = apps.get_model('core', 'ServiceOrder')\n"
if "Attachment = apps.get_model('core', 'Attachment')" not in v:
    v += "Attachment = apps.get_model('core', 'Attachment')\n"

# Vista subir/listar adjuntos
if "def order_attachments(" not in v:
    v += """
# === INTEGRASYS PATCH: ATTACHMENTS VIEW ===
@login_required
def order_attachments(request, pk):
    order = get_object_or_404(ServiceOrder, pk=pk)
    if request.method == "POST":
        files = request.FILES.getlist("file")
        caption = request.POST.get("caption", "")
        if not files:
            messages.warning(request, "Selecciona al menos un archivo.")
            return redirect("order_attachments", pk=order.pk)
        created = 0
        for f in files:
            data = {"service_order": order, "file": f}
            if any(getattr(fld, "name", None) == "caption" for fld in Attachment._meta.fields):
                data["caption"] = caption
            Attachment.objects.create(**data); created += 1
        messages.success(request, f"Subidos {created} adjunto(s).")
        return redirect("order_attachments", pk=order.pk)
    existing = Attachment.objects.filter(service_order=order).order_by("-id")
    return render(request, "recepcion/order_attachments.html", {
        "order": order,
        "attachments": existing,
    })
"""

# Vista eliminar adjunto (POST)
if "def delete_attachment(" not in v:
    v += """
# === INTEGRASYS PATCH: delete_attachment ===
@login_required
@require_POST
def delete_attachment(request, pk, att_id):
    order = get_object_or_404(ServiceOrder, pk=pk)
    att = get_object_or_404(Attachment, pk=att_id, service_order=order)
    att.delete()
    messages.success(request, "Adjunto eliminado.")
    return redirect("order_attachments", pk=order.pk)
"""

# CSV export
if "def export_orders_csv(" not in v:
    v += """
# === INTEGRASYS: Exportación CSV de órdenes ===
@login_required
@user_passes_test(lambda u: u.is_staff or u.is_superuser)
def export_orders_csv(request):
    fmt = "%Y-%m-%d"
    get = request.GET.get
    dfrom = get("from") or get("desde")
    dto   = get("to")   or get("hasta")
    status = get("status") or ""
    assignee = get("assignee") or ""
    q = get("q") or ""

    qs = ServiceOrder.objects.select_related("device__customer")
    from datetime import datetime as _dt
    if dfrom:
        try: qs = qs.filter(checkin_at__date__gte=_dt.strptime(dfrom, fmt).date())
        except Exception: pass
    if dto:
        try: qs = qs.filter(checkin_at__date__lte=_dt.strptime(dto, fmt).date())
        except Exception: pass
    if status: qs = qs.filter(status=status)
    if assignee: qs = qs.filter(assigned_to_id=assignee)
    if q:
        qs = qs.filter(
            Q(folio__icontains=q) |
            Q(device__customer__name__icontains=q) |
            Q(device__brand__icontains=q) |
            Q(device__model__icontains=q) |
            Q(device__serial__icontains=q)
        )

    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = "attachment; filename=ordenes.csv"
    w = csv.writer(resp)
    w.writerow(["ID","Folio","Cliente","Equipo","Serie","Estado","Ingreso"])
    for o in qs.order_by("-checkin_at"):
        cliente = getattr(o.device.customer, "name", "")
        equipo  = f"{getattr(o.device, 'brand', '')} {getattr(o.device, 'model', '')}".strip()
        serie   = getattr(o.device, "serial", "")
        estado  = o.get_status_display() if hasattr(o, "get_status_display") else getattr(o, "status", "")
        ingreso = o.checkin_at.strftime("%Y-%m-%d %H:%M") if getattr(o, "checkin_at", None) else ""
        w.writerow([o.id, o.folio, cliente, equipo, serie, estado, ingreso])
    return resp
"""

# Proteger dashboard /panel (si existe y no tiene decoradores)
if re.search(r"def\s+dashboard\s*\(", v) and "@login_required" not in v:
    v = re.sub(r"(?m)^(def\s+dashboard\s*\()",
               "@login_required\n@user_passes_test(lambda u: u.is_staff or u.is_superuser)\n\1",
               v)

if v != v0:
    wr(views_py, v); changed_files.append("core/views.py")

# ======= 5) config/urls.py: rutas + imports + /media =======
urls_py = P("config", "urls.py")
backup(urls_py)
u = rd(urls_py)
if not u:
    sys.exit("✖ No encontré config/urls.py")

u0 = u
u, ch1 = ensure_urls_imports(u)
if "from core import views as core_views" not in u:
    u = u.replace("from django.urls", "from core import views as core_views  # INTEGRASYS\nfrom django.urls")
    ch1 = True

def ensure_urlpattern(u: str, sig: str, line: str) -> (str, bool):
    if sig in u: return u, False
    anchor = "urlpatterns = ["
    if anchor in u:
        return (u.replace(anchor, anchor + "\n    " + line)), True
    else:
        return (u + "\nurlpatterns += [\n    " + line + "\n]\n"), True

# adjuntos subir/listar
u, ch2 = ensure_urlpattern(u, 'name="order_attachments"', 'path("recepcion/orden/<int:pk>/adjuntos/", core_views.order_attachments, name="order_attachments"),  # INTEGRASYS')
# adjuntos eliminar
u, ch3 = ensure_urlpattern(u, 'name="delete_attachment"', 'path("recepcion/orden/<int:pk>/adjuntos/<int:att_id>/eliminar/", core_views.delete_attachment, name="delete_attachment"),  # INTEGRASYS')
# export csv
u, ch4 = ensure_urlpattern(u, 'name="export_orders_csv"', 'path("reportes/ordenes.csv", core_views.export_orders_csv, name="export_orders_csv"),  # INTEGRASYS')
# media
u, ch5 = ensure_media_patterns(u)

if any([ch1, ch2, ch3, ch4, ch5]) or u != u0:
    wr(urls_py, u); changed_files.append("config/urls.py")

# ======= 6) settings.py: MEDIA =======
settings_py = P("config", "settings.py")
backup(settings_py)
s = rd(settings_py)
if s:
    s0 = s
    if "MEDIA_URL" not in s:
        s += """

# === INTEGRASYS PATCH: MEDIA ===
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
"""
    if s != s0:
        wr(settings_py, s); changed_files.append("config/settings.py")

# ======= 7) template de adjuntos =======
tpl_adj = P("templates", "recepcion", "order_attachments.html")
if not os.path.exists(tpl_adj):
    backup(tpl_adj)
    wr(tpl_adj, """{% load static %}
<!doctype html>
<html><head><meta charset="utf-8"><title>Adjuntos Orden #{{ order.id }}</title></head>
<body>
  <h1>Adjuntar archivos a la Orden #{{ order.id }}</h1>
  {% if messages %}<ul>{% for m in messages %}<li>{{ m }}</li>{% endfor %}</ul>{% endif %}
  <form method="post" enctype="multipart/form-data">
    {% csrf_token %}
    <p><label>Comentario (opcional): <input type="text" name="caption"></label></p>
    <p><input type="file" name="file" multiple required></p>
    <button type="submit">Subir</button>
  </form>
  <h2>Adjuntos existentes</h2>
  <ul>
    {% for a in attachments %}
      <li>
        <a href="{{ a.file.url }}" target="_blank">{{ a.file.name }}</a>
        {% if a.caption %} — {{ a.caption }}{% endif %}
        — {{ a.file.size|default:0 }} bytes — {{ a.uploaded_at }}
        <form method="post" action="{% url 'delete_attachment' order.id a.id %}" style="display:inline" onsubmit="return confirm('¿Eliminar este adjunto?');">
          {% csrf_token %}<button type="submit">Eliminar</button>
        </form>
      </li>
    {% empty %}<li>Sin adjuntos aún.</li>{% endfor %}
  </ul>
  <p><a href="/recepcion/ordenes/">Volver</a></p>
</body></html>
""")
    changed_files.append("templates/recepcion/order_attachments.html")

# ======= 8) botones "Adjuntos" en lista y detalle =======
list_tpl = P("templates", "reception_orders.html")
detail_tpl = P("templates", "reception_order_detail.html")

# Lista: insertar tras "Detalle"
if os.path.exists(list_tpl):
    backup(list_tpl)
    t = rd(list_tpl)
    t0 = t
    if "order_attachments" not in t:
        t = t.replace(
            '| <a href="{% url \'order_detail\' o.pk %}" target="_blank">Detalle</a>',
            '| <a href="{% url \'order_detail\' o.pk %}" target="_blank">Detalle</a>\n            | <a href="{% url \'order_attachments\' o.pk %}" target="_blank">Adjuntos</a> {% if o.attachments.count %}<span class="muted">({{ o.attachments.count }})</span>{% endif %}'
        )
    # Botón Exportar CSV: cambiar query vieja por la nueva ruta, si aplica
    t = t.replace(
        'href="?q={{ query }}&status={{ status }}&from={{ dfrom }}&to={{ dto }}&export=1"',
        'href="{% url \'export_orders_csv\' %}?q={{ query }}&status={{ status }}&assignee={{ assignee }}&from={{ dfrom }}&to={{ dto }}"'
    )
    if t != t0:
        wr(list_tpl, t); changed_files.append("templates/reception_orders.html")

# Detalle: añadir botón en bloque de acciones "Resumen"
if os.path.exists(detail_tpl):
    backup(detail_tpl)
    d = rd(detail_tpl)
    d0 = d
    if "order_attachments" not in d:
        d = d.replace(
            '{% if wa_link %}<a class="btn success" href="{{ wa_link }}" target="_blank">WhatsApp</a>{% endif %}',
            '{% if wa_link %}<a class="btn success" href="{{ wa_link }}" target="_blank">WhatsApp</a>{% endif %}\n        <a class="btn secondary" href="{% url \'order_attachments\' order.pk %}">Adjuntar archivos</a>'
        )
        if d == d0:
            # plan B: añade el botón al final del bloque de acciones
            d = d.replace(
                '</div>\n    </div>',
                '  <a class="btn secondary" href="{% url \'order_attachments\' order.pk %}">Adjuntar archivos</a>\n      </div>\n    </div>'
            )
    if d != d0:
        wr(detail_tpl, d); changed_files.append("templates/reception_order_detail.html")

# ======= FIN =======
print("✅ Parche aplicado.")
if changed_files:
    print("Archivos modificados (backup .bak creado si existían):")
    for f in changed_files:
        print(" -", f)
else:
    print("Sin cambios (todo ya estaba aplicado).")

print("\nSiguiente paso:")
print("  1) python manage.py makemigrations core")
print("  2) python manage.py migrate")
print("  3) python manage.py runserver")
print("Pruebas rápidas:")
print("  a) /recepcion/orden/<ID>/adjuntos/  (subir/borrar, queda en la misma página)")
print("  b) /reportes/ordenes.csv?from=2025-10-01&to=2025-10-31  (descarga CSV)")
print("  c) /panel  -> debe pedir login y exigir staff/superuser")
