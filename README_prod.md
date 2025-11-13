# Operación en Producción – Integrasys

## Infraestructura
- **Aplicación**: Django 5.x + Gunicorn detrás de Nginx.
- **Código**: `/opt/integrasys/app` (repo `CarmiIsGod/Integrasys`).
- **Entorno virtual**: `/opt/integrasys/.venv` (activar con `source /opt/integrasys/.venv/bin/activate`).
- **Base de datos**: PostgreSQL 16 (`integrasys` en `localhost`, user `integrasys`).
- **Archivos estáticos**: servidos por Nginx desde `/opt/integrasys/app/staticfiles`.
- **Backups**: `/usr/local/bin/backup_integrasys.sh` (cron 03:00) → `/var/backups/integrasys/AAAA-MM-DD/`.
- **Health check**: `/etc/cron.d/healthz_check` (cada 5 min) pega a `/healthz` y reinicia Gunicorn si falla.

## Smoke test

Desde el servidor (con la venv activada y estando en `/home/integrasys/app`):

```sh
curl -I https://app.integrasyscomputacion.com.mx/healthz
python manage.py shell -c "from django.db import connection; print(connection.vendor, connection.settings_dict['NAME'])"
psql -h 127.0.0.1 -U integrasys -d integrasys -P pager=off -c "select current_database(), current_user, now();"
