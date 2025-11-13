# Runbook de Producción – Integrasys

## Infraestructura
- **Aplicación**: Django + Gunicorn detrás de Nginx.
- **Código**: `/opt/integrasys/app` (repositorio clonando este repo).
- **Entorno virtual**: `/opt/integrasys/.venv` (activar con `source /opt/integrasys/.venv/bin/activate`).
- **Base de datos**: PostgreSQL 16 (`integrasys`).
- **Archivos estáticos**: servidos por Nginx desde `/opt/integrasys/app/staticfiles`.

## Respaldos
1. Ejecutar manualmente:
   ```bash
   sudo /usr/local/bin/backup_integrasys.sh
   ```
2. Los respaldos quedan en `/var/backups/integrasys/AAAA-MM-DD/` (dump de Postgres y media empaquetada).

### Restaurar en base temporal
1. Copiar el backup deseado al servidor (o usar el ya existente en `/var/backups/...`).
2. Crear base temporal:
   ```bash
   sudo -u postgres createdb integrasys_restore
   ```
3. Restaurar el dump:
   ```bash
   sudo -u postgres pg_restore -d integrasys_restore /var/backups/integrasys/AAAA-MM-DD/integrasys.dump
   ```
4. Validar datos, luego borrar la base temporal con `dropdb integrasys_restore`.

## Rotación de contraseña de BD
1. Cambiar la contraseña del usuario `integrasys` en Postgres.
2. Actualizar la variable `DATABASE_URL` en el archivo `.env` del proyecto.
3. Actualizar `/root/.pgpass` para que las tareas automatizadas sigan funcionando (`chmod 600`).
4. Reiniciar Gunicorn (`sudo systemctl restart integrasys.service`).

## Healthchecks
- Verificar manualmente con:
  ```bash
  curl -I https://app.integrasyscomputacion.com.mx/healthz
  ```
- Cron `/etc/cron.d/healthz_check` ejecuta la misma petición cada 5 minutos y registra resultados para alerta temprana.

## Pruebas automatizadas
Desde el directorio del proyecto (con la venv activada):
```bash
python manage.py test
```

Mantén este documento actualizado cuando cambie la topología o el pipeline de despliegue.
