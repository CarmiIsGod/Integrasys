from django.contrib.auth.models import Group, Permission, User
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand

from core.models import (
    Customer,
    Device,
    Estimate,
    InventoryItem,
    InventoryMovement,
    ServiceOrder,
    StatusHistory,
)


class Command(BaseCommand):
    help = "Create or update default groups for reception and technician roles."

    def handle(self, *args, **options):
        group_specs = {
            "Recepcion": {
                ServiceOrder: ["view", "add", "change"],
                Customer: ["view", "add"],
                Device: ["view", "add"],
                StatusHistory: ["view", "add"],
                Estimate: ["view", "add", "change"],
            },
            "Tecnico": {
                ServiceOrder: ["view", "change"],
                StatusHistory: ["view", "add"],
                InventoryItem: ["view"],
                InventoryMovement: ["view", "add"],
                Estimate: ["view"],
            },
        }

        ct_cache = {}

        def get_permission(model, action):
            content_type = ct_cache.setdefault(model, ContentType.objects.get_for_model(model))
            codename = f"{action}_{model._meta.model_name}"
            try:
                return Permission.objects.get(content_type=content_type, codename=codename)
            except Permission.DoesNotExist:
                self.stdout.write(self.style.WARNING(
                    f"Permission '{codename}' not found for {model.__name__}."
                ))
                return None

        first_superuser = User.objects.filter(is_superuser=True).order_by("id").first()

        for name, model_actions in group_specs.items():
            group, created = Group.objects.get_or_create(name=name)
            perms = []
            for model, actions in model_actions.items():
                for action in actions:
                    perm = get_permission(model, action)
                    if perm:
                        perms.append(perm)
            unique_perms = list({perm.pk: perm for perm in perms}.values())
            group.permissions.set(unique_perms)
            group.save()

            if created and first_superuser:
                group.user_set.add(first_superuser)
                self.stdout.write(self.style.SUCCESS(
                    f"Added {first_superuser.get_username()} to newly created group '{name}'."
                ))

            self.stdout.write(self.style.SUCCESS(
                f"Configured group '{name}' with {len(unique_perms)} permissions."
            ))
