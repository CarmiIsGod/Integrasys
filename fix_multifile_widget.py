# fix_multifile_widget.py
# Corrige core/forms.py para usar un widget que sí soporte multiple files.
import os, re, sys

ROOT = os.getcwd()
path = os.path.join(ROOT, "core", "forms.py")
if not os.path.exists(path):
    sys.exit("✖ No encontré core/forms.py. Corre este script desde la raíz del proyecto.")

with open(path, "r", encoding="utf-8") as f:
    s = f.read()

changed = False

# Inserta el widget MultiFileInput si no existe
if "class MultiFileInput(" not in s:
    insert_after = "from django import forms"
    idx = s.find(insert_after)
    add = "\n\nclass MultiFileInput(forms.ClearableFileInput):\n    allow_multiple_selected = True\n"
    if idx != -1:
        # Inserta después del import de forms
        nl = s.find("\n", idx)
        s = s[:nl+1] + add + s[nl+1:]
    else:
        s = "from django import forms\n" + add + "\n" + s
    changed = True

# Reemplaza cualquier ClearableFileInput(...) por MultiFileInput(...)
new_s = re.sub(r"forms\.ClearableFileInput\(", "MultiFileInput(", s)
if new_s != s:
    s = new_s
    changed = True

if changed:
    with open(path, "w", encoding="utf-8") as f:
        f.write(s)
    print("[OK] forms.py actualizado: ahora usa MultiFileInput con multiple=True.")
else:
    print("[OK] forms.py ya tenía el widget correcto; sin cambios.")

print("Listo. Ahora ejecuta makemigrations/migrate y arranca el server.")
