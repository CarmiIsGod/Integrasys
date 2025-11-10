from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand

from core.permissions import ROLE_GERENCIA, ROLE_RECEPCION, ROLE_TECNICO


class Command(BaseCommand):
    help = "Crea los grupos base y usuarios demo para Gerencia, Recepción y Técnico."

    demo_users = [
        (ROLE_GERENCIA, "gerencia", "gerencia123!", "Gerencia"),
        (ROLE_RECEPCION, "recepcion", "recepcion123!", "Recepción"),
        (ROLE_TECNICO, "tecnico", "tecnico123!", "Técnico"),
    ]

    def handle(self, *args, **options):
        group_names = [ROLE_GERENCIA, ROLE_RECEPCION, ROLE_TECNICO]
        groups = {}
        for name in group_names:
            group, created = Group.objects.get_or_create(name=name)
            groups[name] = group
            action = "Creado" if created else "Disponible"
            self.stdout.write(self.style.SUCCESS(f"{action} grupo '{name}'."))

        User = get_user_model()
        for group_name, username, password, full_name in self.demo_users:
            defaults = {
                "email": f"{username}@example.com",
                "is_staff": True,
                "first_name": full_name,
            }
            user, created = User.objects.get_or_create(username=username, defaults=defaults)
            updated_fields = []
            if created:
                user.set_password(password)
                user.is_staff = True
                updated_fields.extend(["password", "is_staff"])
            else:
                if not user.is_staff:
                    user.is_staff = True
                    updated_fields.append("is_staff")
                if not user.email:
                    user.email = defaults["email"]
                    updated_fields.append("email")
                if not user.first_name and full_name:
                    user.first_name = full_name
                    updated_fields.append("first_name")
            if updated_fields:
                user.save(update_fields=list(set(updated_fields)))
            elif created:
                user.save()
            groups[group_name].user_set.add(user)
            note = "creado" if created else "actualizado"
            self.stdout.write(self.style.SUCCESS(f"Usuario demo '{username}' {note} y asignado a {group_name}."))

        self.stdout.write(self.style.SUCCESS("Roles y usuarios demo listos."))
