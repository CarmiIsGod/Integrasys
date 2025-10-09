# fix_encoding.py
from pathlib import Path
from datetime import datetime

REPL = {
    "\u201c": '"',  # “
    "\u201d": '"',  # ”
    "\u2018": "'",  # ‘
    "\u2019": "'",  # ’
    "\u2013": "-",  # –
    "\u2014": "-",  # —
    "\u2026": "...",# …
    "\u00a0": " ",  # NBSP
    "\u200b": "",   # zero-width space
    "\u200c": "",   # zero-width non-joiner
    "\u200d": "",   # zero-width joiner
}

def normalize_text(raw: bytes) -> str:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")
    for bad, good in REPL.items():
        text = text.replace(bad, good)
    return text

def ensure_encoding_cookie(text: str) -> str:
    first_line = text.splitlines(True)[:1]
    if first_line and "coding" in first_line[0]:
        return text
    return "# -*- coding: utf-8 -*-\n" + text

def process_file(p: Path):
    raw = p.read_bytes()
    new = normalize_text(raw)
    if new.encode("utf-8", errors="ignore") != raw:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = p.with_suffix(p.suffix + f".bak_{ts}")
        bak.write_bytes(raw)
        new = ensure_encoding_cookie(new)
        p.write_text(new, encoding="utf-8", newline="\n")
        return True
    return False

def main():
    changed = 0
    # procesa todos los .py dentro de core/
    for py in Path("core").rglob("*.py"):
        if process_file(py):
            changed += 1
            print(f"Fixed: {py}")
    print(f"Done. Files changed: {changed}")

if __name__ == "__main__":
    main()
