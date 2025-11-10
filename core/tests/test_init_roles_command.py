from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase

from core.permissions import ROLE_GERENCIA, ROLE_RECEPCION, ROLE_TECNICO


class InitRolesCommandTests(TestCase):
    def test_init_roles_creates_groups_and_users(self):
        call_command("init_roles")
        User = get_user_model()

        specs = [
            ("gerencia", ROLE_GERENCIA),
            ("recepcion", ROLE_RECEPCION),
            ("tecnico", ROLE_TECNICO),
        ]
        for username, group_name in specs:
            user = User.objects.get(username=username)
            group = Group.objects.get(name=group_name)
            self.assertTrue(user.is_staff)
            self.assertTrue(group.user_set.filter(pk=user.pk).exists())

    def test_init_roles_is_idempotent(self):
        call_command("init_roles")
        call_command("init_roles")
        User = get_user_model()
        self.assertEqual(User.objects.filter(username="gerencia").count(), 1)
        self.assertEqual(User.objects.filter(username="recepcion").count(), 1)
        self.assertEqual(User.objects.filter(username="tecnico").count(), 1)
