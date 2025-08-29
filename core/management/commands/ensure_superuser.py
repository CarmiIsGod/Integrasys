import os
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

class Command(BaseCommand):
    help = "Crea un superusuario desde variables de entorno si no existe."

    def handle(self, *args, **kwargs):
        User = get_user_model()
        username = os.getenv("DJANGO_SUPERUSER_USERNAME")
        password = os.getenv("DJANGO_SUPERUSER_PASSWORD")
        email = os.getenv("DJANGO_SUPERUSER_EMAIL", "")

        if not username or not password:
            self.stdout.write("DJANGO_SUPERUSER_USERNAME/PASSWORD no definidos; omitiendo.")
            return

        if User.objects.filter(username=username).exists():
            self.stdout.write(f"El superusuario '{username}' ya existe. OK.")
            return

        User.objects.create_superuser(username=username, email=email, password=password)
        self.stdout.write(f"Superusuario '{username}' creado.")
