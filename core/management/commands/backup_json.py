from django.core.management.base import BaseCommand
from django.core.management import call_command
from django.utils import timezone
from pathlib import Path


class Command(BaseCommand):
    help = "Crea un respaldo JSON en ./backups/"

    def handle(self, *args, **opts):
        ts = timezone.localtime().strftime("%Y%m%d_%H%M%S")
        outdir = Path("backups")
        outdir.mkdir(exist_ok=True)
        path = outdir / f"backup_{ts}.json"
        with path.open("w", encoding="utf-8") as fh:
            call_command(
                "dumpdata",
                exclude=["auth.permission", "contenttypes"],
                indent=2,
                stdout=fh,
            )
        self.stdout.write(self.style.SUCCESS(f"OK: {path}"))
