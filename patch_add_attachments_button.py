# patch_add_attachments_button.py
# Agrega un botón "Adjuntos" en la lista y/o detalle de órdenes (idempotente).
import os, re, sys

ROOT = os.getcwd()
TPL = os.path.join(ROOT, "templates")

def rd(p): 
    try: return open(p, "r", encoding="utf-8").read()
    except: return ""
def wr(p,s):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p,"w",encoding="utf-8") as f: f.write(s)

if not os.path.isdir(TPL):
    sys.exit("✖ No encontré la carpeta 'templates/'.")
candidates = []

# Recorre templates y junta candidatos que parecen listar órdenes
for base,_,files in os.walk(TPL):
    for f in files:
        if not f.endswith(".html"): continue
        p = os.path.join(base,f)
        s = rd(p)
        low = s.lower()
        # heurísticas para lista/detalle de órdenes
        if ("recepcion" in low and "ordenes" in low) or re.search(r"\bfor\s+order\s+in\b", s):
            candidates.append((p,s))

patched = False
for p,s in candidates:
    if "order_attachments" in s and ("Adjuntos" in s or "adjuntos" in s):
        continue  # ya está
    new = s

    # Inserta botón cerca de acciones típicas
    new = re.sub(
        r'(<a[^>]+>\s*(Ver|Detalle|Detalle de la orden)\s*</a>)',
        r'\1&nbsp; <a href="{% url \'order_attachments\' order.id %}" class="btn btn-secondary btn-sm">Adjuntos</a>',
        new, flags=re.I
    )
    # Si no lo encontró, añade una celda de acciones simple dentro del loop
    if new == s:
        new = re.sub(
            r'(\{\%\s*for\s+order\s+in[^\%]*\%\})',
            r"\1\n    <!-- INTEGRASYS: botón Adjuntos -->\n    <a href=\"{% url 'order_attachments' order.id %}\" class=\"btn btn-secondary btn-sm\">Adjuntos</a>\n",
            new, flags=re.S
        )
    if new != s:
        wr(p,new)
        print(f"[OK] Botón Adjuntos agregado en: {os.path.relpath(p, ROOT)}")
        patched = True
        break

if not patched:
    print("⚠ No pude insertar automáticamente. Dime el nombre del template de la lista/detalle y te doy el parche exacto.")
