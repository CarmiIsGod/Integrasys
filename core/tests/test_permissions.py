from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase
from django.urls import reverse


class RequireManagerDecoratorTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.dashboard_url = reverse("dashboard")
        self.User = get_user_model()

    def test_anonymous_redirects_to_login(self):
        response = self.client.get(self.dashboard_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response.headers.get("Location", ""))

    def test_normal_user_gets_forbidden(self):
        user = self.User.objects.create_user(username="regular", password="pass123")
        assert self.client.login(username="regular", password="pass123")

        response = self.client.get(self.dashboard_url)
        self.assertEqual(response.status_code, 403)

    def test_manager_user_has_access(self):
        group, _ = Group.objects.get_or_create(name="Gerencia")
        user = self.User.objects.create_user(username="manager", password="pass123")
        user.groups.add(group)
        assert self.client.login(username="manager", password="pass123")

        response = self.client.get(self.dashboard_url)
        self.assertEqual(response.status_code, 200)
