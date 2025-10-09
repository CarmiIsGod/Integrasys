from functools import wraps

from django.http import HttpResponseForbidden
from django.contrib.auth.views import redirect_to_login


def manager_required(view):
    """Restrict access to superusers or members of the Gerencia group."""

    @wraps(view)
    def _wrapped(request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if user.is_superuser or user.groups.filter(name="Gerencia").exists():
            return view(request, *args, **kwargs)
        return HttpResponseForbidden("No autorizado")

    return _wrapped
