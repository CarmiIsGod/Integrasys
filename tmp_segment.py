from pathlib import Path
text = Path('core/views.py').read_text(encoding='utf-8')
start = text.index('f"La cotizacion de la orden {order.folio} fue aprobada el "')
print(repr(text[start:start+70]))
