# patch_attachments_enhance.py
# - Mantiene la página de adjuntos después de subir (no te manda a la lista)
# - Agrega vista y URL para eliminar adjuntos (POST + confirmación)
# - Mejora el template: mensajes, tamaño/fecha y botón "Eliminar"
import os, re, sys

ROOT = os.getcwd()
def here(*p): return os.path.join(ROOT, *p)
def rd(p): return open(p, "r", encoding="utf-8").read() if os.path.exists(p) else ""
def wr(p, s):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f: f.write(s)

# 1) core/views.py
views_py = here("core", "views.py")
v = rd(views_py)
if not v:
    sys.exit("✖ No encontré core/views.py")

changed = False

# 1a) redirigir de vuelta a la misma página
v2 = v.replace('return redirect("/recepcion/ordenes/")',
               'return redirect("order_attachments", pk=order.pk)')
if v2 != v:
    v = v2; changed = True

# 1b) añadir delete_attachment si falta
if "def delete_attachment(" not in v:
    block = """
# === INTEGRASYS PATCH: delete_attachment ===
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect
from django.contrib import messages
from django.apps import apps

Attachment = apps.get_model('core', 'Attachment')
ServiceOrder = apps.get_model('core', 'ServiceOrder')

@login_required
@require_POST
def delete_attachment(request, pk, att_id):
    order = get_object_or_404(ServiceOrder, pk=pk)
    att = get_object_or_404(Attachment, pk=att_id, service_order=order)
    att.delete()
    messages.success(request, "Adjunto eliminado.")
    return redirect("order_attachments", pk=order.pk)
"""
    if not v.endswith("\n"): v += "\n"
    v += block; changed = True

if changed: wr(views_py, v)

# 2) config/urls.py
urls_py = here("config", "urls.py")
u = rd(urls_py)
if not u: sys.exit("✖ No encontré config/urls.py")

changed_u = False
if "from core import views as core_views" not in u:
    u = u.replace("from django.urls import path",
                  "from django.urls import path\nfrom core import views as core_views  # INTEGRASYS")
    changed_u = True

if "name=\"delete_attachment\"" not in u and "name='delete_attachment'" not in u:
    anchor = "urlpatterns = ["
    route = 'path("recepcion/orden/<int:pk>/adjuntos/<int:att_id>/eliminar/", core_views.delete_attachment, name="delete_attachment"),  # INTEGRASYS'
    if anchor in u:
        u = u.replace(anchor, anchor + "\n    " + route)
    else:
        u += "\nurlpatterns += [\n    " + route + "\n]\n"
    changed_u = True

if changed_u: wr(urls_py, u)

# 3) templates/recepcion/order_attachments.html
tpl = here("templates", "recepcion", "order_attachments.html")
t = rd(tpl)
if not t: sys.exit("✖ No encontré templates/recepcion/order_attachments.html")

if "INTEGRASYS PATCH: messages" not in t:
    # bloque de mensajes
    t = t.replace("<body>", "<body>\n  <!-- INTEGRASYS PATCH: messages -->\n  {% if messages %}\n    <ul>\n    {% for m in messages %}\n      <li>{{ m }}</li>\n    {% endfor %}\n    </ul>\n  {% endif %}\n")
    # lista enriquecida + botón eliminar
    t = re.sub(
        r"<ul>\s*{% for a in attachments %}.*?{% endfor %}\s*</ul>",
        """<ul>
    {% for a in attachments %}
      <li>
        <a href="{{ a.file.url }}" target="_blank">{{ a.file.name }}</a>
        {% if a.caption %} — {{ a.caption }}{% endif %}
        — {{ a.file.size|default:0 }} bytes — {{ a.uploaded_at }}
        <form method="post" action="{% url 'delete_attachment' order.id a.id %}" style="display:inline" onsubmit="return confirm('¿Eliminar este adjunto?');">
          {% csrf_token %}
          <button type="submit">Eliminar</button>
        </form>
      </li>
    {% empty %}
      <li>Sin adjuntos aún.</li>
    {% endfor %}
  </ul>""",
        t, flags=re.S
    )
    wr(tpl, t)

print("✅ Parche de mejora aplicado. Arranca el servidor y prueba en /recepcion/orden/<ID>/adjuntos/")
