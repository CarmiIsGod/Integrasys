from pathlib import Path
text = Path('core/views.py').read_text(encoding='utf-8')
start = text.index("f\"{timezone.localtime(now)")
end = text.index("f\"Total autorizado", start)
print(repr(text[start:end]))
