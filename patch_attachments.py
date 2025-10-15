# patch_attachments.py
# Parchea: formulario de adjuntos, vista, url, template y MEDIA en dev.
# Seguro e idempotente. Ejecutar desde la raiz (donde esta manage.py).
import os
import sys


ROOT = os.getcwd()


def here(*parts: str) -> str:
    return os.path.join(ROOT, *parts)


def read(path: str) -> str:
    return open(path, "r", encoding="utf-8").read() if os.path.exists(path) else ""


def write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def ensure_contains(path: str, sentinel: str, block: str, where: str = "end") -> bool:
    """Si el archivo no contiene 'sentinel', inserta 'block' en 'where'."""
    current = read(path)
    if not current:
        write(path, block)
        return True

    if sentinel in current:
        return False

    if where == "start":
        new_content = block + ("" if block.endswith("\n") else "\n") + current
    else:
        if not current.endswith("\n"):
            current += "\n"
        if not block.endswith("\n"):
            block += "\n"
        new_content = current + block
    write(path, new_content)
    return True


def ensure_lines_in_urls(urls_txt: str) -> tuple[str, bool]:
    """Agrega imports, ruta y static/media en config/urls.py de forma idempotente."""
    if not urls_txt:
        return urls_txt, False

    lines = urls_txt.splitlines()
    updated = False

    def has_line(predicate):
        return any(predicate(line.strip()) for line in lines)

    # Determinar la posicion donde terminan los imports consecutivos al inicio.
    import_end = 0
    while import_end < len(lines):
        stripped = lines[import_end].strip()
        if not stripped:
            break
        if stripped.startswith("from ") or stripped.startswith("import "):
            import_end += 1
            continue
        break

    def add_import(import_line: str) -> None:
        nonlocal import_end, updated
        if has_line(lambda ln: ln == import_line):
            return
        lines.insert(import_end, import_line)
        import_end += 1
        updated = True

    # Asegurar imports necesarios.
    add_import("from django.conf import settings")
    add_import("from django.conf.urls.static import static")
    if not has_line(lambda ln: ln.startswith("from core import views")):
        add_import("from core import views")

    # Asegurar la ruta de adjuntos.
    route_signature = 'name="order_attachments"'
    single_quote_signature = "name='order_attachments'"
    if route_signature not in urls_txt and single_quote_signature not in urls_txt:
        route_line = '    path("recepcion/orden/<int:pk>/adjuntos/", views.order_attachments, name="order_attachments"),  # INTEGRASYS'
        try:
            urlpatterns_start = next(
                idx for idx, line in enumerate(lines) if line.strip().startswith("urlpatterns") and "[" in line
            )
            urlpatterns_end = next(
                idx for idx in range(urlpatterns_start + 1, len(lines)) if lines[idx].strip() == "]"
            )
        except StopIteration:
            # Si no hay urlpatterns definidos, creamos el bloque al final.
            lines.append("")
            lines.append("urlpatterns = [")
            lines.append(route_line)
            lines.append("]")
            updated = True
        else:
            lines.insert(urlpatterns_end, route_line)
            updated = True

    # Asegurar el append de static() en modo DEBUG.
    static_call = "urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)"
    if static_call not in urls_txt:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("if settings.DEBUG:")
        lines.append(f"    {static_call}")
        updated = True

    new_text = "\n".join(lines)
    if not new_text.endswith("\n"):
        new_text += "\n"
    return new_text, updated


def main() -> None:
    if not os.path.exists(here("manage.py")):
        sys.exit("ERROR: Ejecuta este script desde la carpeta raiz del proyecto (donde esta manage.py).")

    changed: list[str] = []

    # 1) core/forms.py  -------------------------------------------------------
    forms_py = here("core", "forms.py")
    forms_block = """# === INTEGRASYS PATCH: ATTACHMENTS FORM ===
from django import forms
from core.models import Attachment


class AttachmentForm(forms.ModelForm):
    class Meta:
        model = Attachment
        fields = [
            name
            for name in ("file", "caption")
            if any(getattr(f, "name", "") == name for f in Attachment._meta.fields)
        ]
        widgets = {
            "file": forms.ClearableFileInput(attrs={"multiple": True}),
        }
"""
    if ensure_contains(forms_py, "INTEGRASYS PATCH: ATTACHMENTS FORM", forms_block, "end"):
        changed.append("core/forms.py")

    # 2) core/views.py  -------------------------------------------------------
    views_py = here("core", "views.py")
    views_block = """# === INTEGRASYS PATCH: ATTACHMENTS VIEW ===
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from core.models import ServiceOrder, Attachment

try:
    from core.forms import AttachmentForm
except Exception:
    AttachmentForm = None


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
        for uploaded in files:
            data = {"service_order": order, "file": uploaded}
            if any(getattr(field, "name", None) == "caption" for field in Attachment._meta.fields):
                data["caption"] = caption
            Attachment.objects.create(**data)
            created += 1
        messages.success(request, f"Subidos {created} adjunto(s).")
        return redirect("/recepcion/ordenes/")
    existing = Attachment.objects.filter(service_order=order).order_by("-id")
    return render(
        request,
        "recepcion/order_attachments.html",
        {
            "order": order,
            "attachments": existing,
            "form": AttachmentForm() if AttachmentForm else None,
        },
    )
"""
    if ensure_contains(views_py, "INTEGRASYS PATCH: ATTACHMENTS VIEW", views_block, "end"):
        changed.append("core/views.py")

    # 3) config/urls.py  -------------------------------------------------------
    urls_py = here("config", "urls.py")
    urls_txt = read(urls_py)
    if not urls_txt:
        sys.exit("ERROR: No encontre config/urls.py")

    new_urls_txt, urls_changed = ensure_lines_in_urls(urls_txt)
    if urls_changed:
        write(urls_py, new_urls_txt)
        changed.append("config/urls.py")

    # 4) config/settings.py (MEDIA)  ------------------------------------------
    settings_py = here("config", "settings.py")
    settings_txt = read(settings_py)
    if settings_txt and "MEDIA_URL" not in settings_txt:
        media_block = """
# === INTEGRASYS PATCH: MEDIA ===
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
"""
        if not settings_txt.endswith("\n"):
            settings_txt += "\n"
        settings_txt += media_block.lstrip("\n")
        write(settings_py, settings_txt)
        changed.append("config/settings.py")

    # 5) template  -------------------------------------------------------------
    tpl_path = here("templates", "recepcion", "order_attachments.html")
    if not os.path.exists(tpl_path):
        tpl_content = """{% load static %}
<!doctype html>
<html>
<head><meta charset="utf-8"><title>Adjuntos Orden #{{ order.id }}</title></head>
<body>
  <h1>Adjuntar archivos a la Orden #{{ order.id }}</h1>
  <form method="post" enctype="multipart/form-data">
    {% csrf_token %}
    <p><label>Comentario (opcional): <input type="text" name="caption"></label></p>
    <p><input type="file" name="file" multiple required></p>
    <button type="submit">Subir</button>
  </form>
  <h2>Adjuntos existentes</h2>
  <ul>
    {% for attachment in attachments %}
      <li><a href="{{ attachment.file.url }}" target="_blank">{{ attachment.file.name }}</a>{% if attachment.caption %} - {{ attachment.caption }}{% endif %}</li>
    {% empty %}
      <li>Sin adjuntos aun.</li>
    {% endfor %}
  </ul>
  <p><a href="/recepcion/ordenes/">Volver</a></p>
</body>
</html>
"""
        write(tpl_path, tpl_content)
        changed.append("templates/recepcion/order_attachments.html")

    print("[OK] Parche aplicado.")
    if changed:
        print("Archivos modificados/creados:")
        for entry in changed:
            print(" -", entry)
    else:
        print("No hubo cambios (ya estaba aplicado).")
    print("\nSigue estos comandos:")
    print("  1) python manage.py collectstatic --noinput")
    print("  2) python manage.py runserver")
    print("  3) Abre /recepcion/ordenes/, toma el ID y visita /recepcion/orden/<ID>/adjuntos/")


if __name__ == "__main__":
    main()
