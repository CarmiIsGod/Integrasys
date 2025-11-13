# Operación en Producción – Integrasys

## Infra
- App: Django 5.x (Gunicorn + Nginx)
- DB: PostgreSQL 16 (localhost; DB: `integrasys`; user: `integrasys`)
- Backups: `/usr/local/bin/backup_integrasys.sh` (cron 03:00) → `/var/backups/integrasys/AAAA-MM-DD/`
- Health check: `/etc/cron.d/healthz_check` (cada 5 min) reinicia Gunicorn si `/healthz` falla.

## Smoke test
```sh
curl -I https://app.integrasyscomputacion.com.mx/healthz
python manage.py shell -c "from django.db import connection; print(connection.vendor, connection.settings_dict['NAME'])"
psql -h 127.0.0.1 -U integrasys -d integrasys -P pager=off -c "select current_database(), current_user, now();"




