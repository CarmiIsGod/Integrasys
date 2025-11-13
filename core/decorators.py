from django.contrib.auth.decorators import user_passes_test

def group_required(*names):
    """
    Permite acceso si el usuario está autenticado y pertenece
    a cualquiera de los grupos indicados, o si es superuser.
    """
    def check(u):
        if u.is_superuser:
            return True
        return u.is_authenticated and u.groups.filter(name__in=names).exists()
    # usamos el login del admin como puerta
    return user_passes_test(check, login_url='/admin/login/')
