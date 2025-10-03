from django.core.management.base import BaseCommand, CommandError
from django.core.management import call_command
from pathlib import Path


class Command(BaseCommand):
    help = "Restaura un respaldo JSON (uso: python manage.py restore_json backups/archivo.json)"

    def add_arguments(self, parser):
        parser.add_argument("path", type=str)

    def handle(self, *args, **opts):
        path = Path(opts["path"])
        if not path.exists():
            raise CommandError(f"No existe: {path}")
        # No se hace flush para evitar pérdida accidental; el usuario controla el archivo.
        call_command("loaddata", str(path))
        self.stdout.write(self.style.SUCCESS(f"Restaurado: {path}"))
