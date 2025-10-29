# core/management/commands/backup_integrasys.py
from django.core.management.base import BaseCommand
from django.core.management import call_command
from django.conf import settings
from pathlib import Path
import tarfile
import datetime

class Command(BaseCommand):
    help = "Genera un dump JSON de la BD y comprime la carpeta media/"

    def handle(self, *args, **options):
        # Carpeta /backups en la raíz del proyecto
        base_dir = Path(settings.BASE_DIR)  # raíz del proyecto (donde está manage.py)
        backups_dir = base_dir / "backups"
        backups_dir.mkdir(exist_ok=True)

        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

        # 1) Dump JSON de la base de datos
        json_path = backups_dir / f"backup_{ts}.json"
        self.stdout.write(f"Generando dump: {json_path.name}")
        with open(json_path, "w", encoding="utf-8") as f:
            call_command(
                "dumpdata",
                "--natural-foreign",
                "--natural-primary",
                "--indent", "2",
                stdout=f,
            )

        # 2) Comprimir /media si existe
        media_root = Path(settings.MEDIA_ROOT)
        if media_root.exists():
            tar_path = backups_dir / f"media_{ts}.tar.gz"
            self.stdout.write(f"Comprimiendo media/: {tar_path.name}")
            with tarfile.open(tar_path, "w:gz") as tar:
                tar.add(media_root, arcname="media")
        else:
            self.stdout.write("MEDIA_ROOT no existe; se omite compresión de media/.")

        self.stdout.write(self.style.SUCCESS("Backup completado en /backups"))
