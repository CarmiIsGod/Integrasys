# convert_fixture_inplace.py
import sys, json
from pathlib import Path

if len(sys.argv) < 2:
    print("Uso: python convert_fixture_inplace.py fixtures/dev_core.json")
    sys.exit(1)

p = Path(sys.argv[1])
raw = p.read_bytes()

try:
    text = raw.decode("utf-8")
    src_enc = "utf-8"
except UnicodeDecodeError:
    text = raw.decode("latin-1")  # fallback para 0xF3, etc.
    src_enc = "latin-1"

data = json.loads(text)  # valida JSON
p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"Reescrito {p} de {src_enc} -> utf-8 OK ({p.stat().st_size} bytes)")
