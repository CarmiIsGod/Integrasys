# config/settings.py
from pathlib import Path
import os
import sys

BASE_DIR = Path(__file__).resolve().parent.parent

# --- dotenv opcional (para CI no es requerido) ---
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(BASE_DIR / ".env")
except Exception:
    pass

# --- Core flags (defaults amigables para local/CI) ---
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    if "test" in sys.argv:
        SECRET_KEY = "test-secret"
    else:
        raise RuntimeError("SECRET_KEY no configurada; define SECRET_KEY en el entorno.")

debug_env = os.getenv("DJANGO_DEBUG", os.getenv("DEBUG", "0"))
DEBUG = str(debug_env).lower() in ("true", "1", "yes")

default_hosts = os.getenv("ALLOWED_HOSTS", "")
if default_hosts:
    ALLOWED_HOSTS = [h.strip() for h in default_hosts.split(",") if h.strip()]
else:
    ALLOWED_HOSTS = ["app.integrasyscomputacion.com.mx"]
if DEBUG:
    for host in ("127.0.0.1", "localhost"):
        if host not in ALLOWED_HOSTS:
            ALLOWED_HOSTS.append(host)
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "20"))

# CSRF comunes; puedes ampliar por env
CSRF_TRUSTED_ORIGINS = [
    "https://*.github.dev",
    "https://*.onrender.com",
    "https://app.integrasyscomputacion.com.mx",
]
EXTRA_CSRF = os.getenv("CSRF_TRUSTED_ORIGINS_EXTRA", "")
if EXTRA_CSRF:
    CSRF_TRUSTED_ORIGINS += [o.strip() for o in EXTRA_CSRF.split(",") if o.strip()]
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                # tu CP de notificaciones:
                "core.context_processors.nav_notifications",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# --- DB: usa DATABASE_URL si existe; si no, SQLite (ideal para CI/local) ---
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
try:
    import dj_database_url  # type: ignore
except Exception:
    dj_database_url = None  # noqa

if DATABASE_URL and dj_database_url:
    DATABASES = {
        "default": dj_database_url.parse(DATABASE_URL, conn_max_age=600)
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

# --- Static / Media ---
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

# --- Storage backends (para code/tests/prod) ---
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "31536000"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
X_FRAME_OPTIONS = "DENY"

if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    if SECURE_HSTS_SECONDS:
        SECURE_HSTS_INCLUDE_SUBDOMAINS = True
        SECURE_HSTS_PRELOAD = True
else:
    SECURE_HSTS_SECONDS = 0

if "test" in sys.argv:
    SECURE_SSL_REDIRECT = False
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False
    SECURE_HSTS_SECONDS = 0
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False
    SECURE_HSTS_PRELOAD = False

# --- i18n / TZ ---
LANGUAGE_CODE = "es-mx"
TIME_ZONE = "America/Mexico_City"
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Email (seguro para CI/local) ---
EMAIL_BACKEND = os.getenv(
    "EMAIL_BACKEND",
    "django.core.mail.backends.console.EmailBackend",
)
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "noreply@tallerpc.local")
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("EMAIL_PORT") or 0)
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "False").lower() in ("true", "1", "yes")

# --- Auth redirects ---
LOGIN_URL = "/admin/login/"
LOGIN_REDIRECT_URL = "/admin/"
