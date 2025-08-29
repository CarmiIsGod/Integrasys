import os
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

class Command(BaseCommand):
    help = "Crea/actualiza un superusuario desde variables de entorno."

    def handle(self, *args, **kwargs):
        User = get_user_model()
        username = (os.getenv("DJANGO_SUPERUSER_USERNAME") or "").strip()
        password = os.getenv("DJANGO_SUPERUSER_PASSWORD")
        email = os.getenv("DJANGO_SUPERUSER_EMAIL") or ""
        force = (os.getenv("DJANGO_SUPERUSER_FORCE") or "").lower() in ("1","true","yes")

        if not username:
            self.stdout.write("ENSURE_SUPERUSER: sin USERNAME -> me salgo.")
            return

        u, created = User.objects.get_or_create(username=username, defaults={"email": email})
        changed = False

        if created:
            u.is_staff = True
            u.is_superuser = True
            u.is_active = True
            if password:
                u.set_password(password)
            else:
                u.set_unusable_password()
            u.save()
            self.stdout.write(f"ENSURE_SUPERUSER: creado '{username}'.")
            return

        # Ya existe
        if force and password:
            u.set_password(password)
            changed = True

        # Asegurar flags correctos
        if not u.is_staff or not u.is_superuser or not u.is_active:
            u.is_staff = True
            u.is_superuser = True
            u.is_active = True
            changed = True

        if changed:
            u.save()
            self.stdout.write(f"ENSURE_SUPERUSER: actualizado '{username}'.")
        else:
            self.stdout.write(f"ENSURE_SUPERUSER: sin cambios para '{username}'.")
