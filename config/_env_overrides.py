ALLOWED_HOSTS = [
    "app.integrasyscomputacion.com.mx",
    
    "localhost",
    "127.0.0.1",
]

CSRF_TRUSTED_ORIGINS = ["https://app.integrasyscomputacion.com.mx"]

SECURE_SSL_REDIRECT   = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE    = True

SECURE_HSTS_SECONDS = 604800
SECURE_HSTS_INCLUDE_SUBDOMAINS   = False
SECURE_HSTS_PRELOAD              = False

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# --- security headers via Django ---
X_FRAME_OPTIONS = 'DENY'
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
