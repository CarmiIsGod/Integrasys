from pathlib import Path
import json, sys

src = Path("fixtures/dev_core.json")
dst = Path("fixtures/dev_core_utf8.json")

if not src.exists():
    print("NO EXISTE:", src.resolve())
    sys.exit(1)

raw = src.read_bytes()
try:
    text = raw.decode("utf-8")
    print("Decodificado como UTF-8")
except UnicodeDecodeError:
    text = raw.decode("latin-1")
    print("Decodificado como LATIN-1")

data = json.loads(text)  # valida JSON
dst.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
print("Escrito:", dst.resolve())