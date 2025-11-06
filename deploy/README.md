# Guia de despliegue

## Variables de entorno
- `SECRET_KEY`: clave secreta de Django.
- `DEBUG`: usar `False` en producción.
- `ALLOWED_HOSTS`: dominios o IPs que servirán la app.
- `CSRF_TRUSTED_ORIGINS`: URLs completas (https://) permitidas para CSRF.
- `DATABASE_URL`: cadena de conexión (Postgres recomendado).
- `MAX_FILE_MB`: límite por archivo para adjuntos (ej. `20`).
- `SECURE_HSTS_SECONDS`: segundos para HSTS (opcional; activa HSTS si es >0).
- `EMAIL_BACKEND`: backend de correo (usar `django.core.mail.backends.smtp.EmailBackend` en producción).
- `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, `DEFAULT_FROM_EMAIL`: credenciales SMTP.

## Probar correo SMTP
1. Configura las variables SMTP en `.env`.
2. Ejecuta:
   ```
   python manage.py shell -c "from django.core.mail import send_mail; send_mail('Prueba Integrasys','Correo de prueba','notificaciones@example.com',['tu-correo@example.com'])"
   ```
3. Verifica que el mensaje llegue al buzón configurado; revisa también logs del servidor si falla.

## Checklist de despliegue
1. `git pull` en el servidor (como el usuario del servicio).
2. Activar el entorno virtual: `source .venv/bin/activate`.
3. Instalar dependencias: `pip install -r requirements.txt`.
4. Ejecutar migraciones: `python manage.py migrate`.
5. Inicializar roles si es la primera vez: `python manage.py bootstrap_roles`.
6. Recopilar estaticos: `python manage.py collectstatic --noinput`.
7. Reiniciar Gunicorn: `sudo systemctl restart gunicorn_integrasys`.
8. Validar configuración de Nginx y recargar: `sudo nginx -t && sudo systemctl reload nginx`.
9. Revisar logs de Gunicorn/Nginx para asegurar que no haya errores.
