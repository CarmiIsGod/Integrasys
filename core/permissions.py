from functools import wraps
from urllib.parse import quote

from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render


ROLE_RECEPCION = "Recepcion"
ROLE_GERENCIA = "Gerencia"
ROLE_TECNICO = "Tecnico"


def user_in_group(user, group_name):
    if not getattr(user, "is_authenticated", False):
        return False
    return user.groups.filter(name=group_name).exists()


def _user_has_any_role(user, roles):
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    return user.groups.filter(name__in=list(roles)).exists()


def has_role(user, *roles):
    return _user_has_any_role(user, roles)


def roles_required(*roles):
    return group_required(*roles)


def is_gerencia(user):
    return has_role(user, ROLE_GERENCIA)


def is_recepcion(user):
    return has_role(user, ROLE_RECEPCION, ROLE_GERENCIA)


def is_tecnico(user):
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return False
    return user_in_group(user, ROLE_TECNICO)


def is_manager(user):
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    return user_in_group(user, ROLE_GERENCIA)


def can_mark_order_done(user):
    """
    True only for roles allowed to marcar Entregado/DONE (or disparar auto-cierre):
    - Superuser
    - Gerencia
    - Recepcion
    Tecnicos no pueden.
    """
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    if is_gerencia(user):
        return True
    if is_recepcion(user):
        return True
    return False


def require_manager(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not is_manager(getattr(request, "user", None)):
            return HttpResponseForbidden("Se requiere rol de Gerencia o superusuario.")
        return view_func(request, *args, **kwargs)

    return _wrapped


def _redirect_to_admin_login(request):
    login_url = "/admin/login/"
    if hasattr(request, "get_full_path"):
        next_url = quote(request.get_full_path())
        if next_url:
            return redirect(f"{login_url}?next={next_url}")
    return redirect(login_url)


def group_required(*group_names):
    allowed = tuple(group_names)

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            user = getattr(request, "user", None)
            if not getattr(user, "is_authenticated", False):
                return _redirect_to_admin_login(request)
            if getattr(user, "is_superuser", False):
                return view_func(request, *args, **kwargs)
            if not allowed or user.groups.filter(name__in=allowed).exists():
                return view_func(request, *args, **kwargs)
            context = {"required_groups": allowed}
            return render(request, "403.html", context=context, status=403)

        return _wrapped

    return decorator
