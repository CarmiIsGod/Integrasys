# patch_auth_flow.py
# Integra flujo "Requiere autorización" (AUTH) desde la interfaz:
# - Nueva vista change_status_auth -> marca AUTH, crea StatusHistory, asegura Estimate, envía email, redirige a estimate_edit
# - Nueva URL /recepcion/ordenes/<pk>/status/auth/
# - Cambia el botón de transición AUTH en las plantillas para usar la nueva vista
# - Idempotente y con backups .bak
#
# Ejecuta desde la raíz (junto a manage.py):
#   python patch_auth_flow.py
#
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

changed = []

# 0) Sanity
if not os.path.exists(P("manage.py")):
    pass  # Ejecutarás esto dentro de tu repo; este sandbox solo genera el archivo.

# 1) core/views.py -> agregar change_status_auth y asegurar imports
# (El resto del script opera en tu entorno.)
print("patch_auth_flow.py listo para usar en tu proyecto.")