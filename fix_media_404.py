# fix_media_404.py
# Asegura que /media/ se sirva en dev aunque DEBUG no esté a True.
import os, sys

ROOT = os.getcwd()
urls_py = os.path.join(ROOT, "config", "urls.py")
if not os.path.exists(urls_py):
    sys.exit("✖ No encontré config/urls.py. Ejecuta este script desde la raíz del proyecto.")

with open(urls_py, "r", encoding="utf-8") as f:
    txt = f.read()

changed = False

# Asegura imports
if "from django.conf import settings" not in txt or "from django.conf.urls.static import static" not in txt:
    txt = txt.replace("from django.urls import path",
                      "from django.urls import path\nfrom django.conf import settings\nfrom django.conf.urls.static import static")
    changed = True

# re_path + serve para fallback
if "re_path" not in txt:
    txt = txt.replace("from django.urls import path", "from django.urls import path, re_path")
    changed = True
if "from django.views.static import serve" not in txt:
    txt += "\nfrom django.views.static import serve  # INTEGRASYS\n"
    changed = True

# Bloque estándar (si faltara)
if "static(settings.MEDIA_URL" not in txt:
    txt += "\n# INTEGRASYS: servir media en dev (bloque estándar)\nurlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)\n"
    changed = True

# Fallback incondicional (por si DEBUG está en False en local)
if "delete_attachment" not in txt:  # solo para encontrar buen ancla, no es requisito
    pass
if "re_path(r'^media/(?P<path>.*)'" not in txt:
    txt += "\n# INTEGRASYS: fallback para /media/ (dev) aunque DEBUG sea False\n"
    txt += "urlpatterns += [re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT})]\n"
    changed = True

if changed:
    with open(urls_py, "w", encoding="utf-8") as f:
        f.write(txt)
    print("[OK] urls.py actualizado para servir /media/ en dev.")
else:
    print("[OK] urls.py ya tenía el mapeo de /media/. Sin cambios.")

print("Ahora reinicia el server y prueba de nuevo el enlace /media/…")
