from functools import wraps

from django.core.exceptions import PermissionDenied
from django.http import HttpResponseForbidden


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
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if not _user_has_any_role(request.user, roles):
                raise PermissionDenied("No tienes permiso para acceder a esta vista.")
            return view_func(request, *args, **kwargs)

        return _wrapped

    return decorator


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


def require_manager(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not is_manager(getattr(request, "user", None)):
            return HttpResponseForbidden("Se requiere rol de Gerencia o superusuario.")
        return view_func(request, *args, **kwargs)

    return _wrapped
