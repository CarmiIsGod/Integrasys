# Integrasys / Sistema de Órdenes de Servicio

Aplicación en Django para registrar recepción de equipos, seguimiento interno y comunicación con clientes.

## MVP
- Recepción crea clientes, dispositivos y órdenes con folio y token público.
- Panel interno para Gerencia/Recepción/Técnicos con filtros, asignación de técnicos, adjuntos y pagos.
- Cotizaciones (Estimate) con soporte de inventario y autorizaciones por correo.
- Notificaciones por correo y panel para estados clave (Listo para recoger / Requiere autorización).
- Generación de recibos PDF (entrada y pagos) y exportación CSV.

## Roles
- **Gerencia**: acceso total, reportes y exportaciones.
- **Recepción**: captura de órdenes, pagos, asignación de técnicos.
- **Técnico**: sólo puede gestionar órdenes asignadas y actualizar estados permitidos.

## Endpoints principales
- `/recepcion/` panel operativo (requiere autenticación).
- `/t/<token>/` vista pública para que el cliente consulte historial y descargas.
- `/healthz` healthcheck usado por cron y monitoreo.

## Entornos y despliegue
- **Producción oficial**: VPS en IONOS (`app.integrasyscomputacion.com.mx`) con:
  - Django 5.x + Gunicorn + Nginx
  - PostgreSQL 16 local (`integrasys` / usuario `integrasys`)
  - Backups diarios con `/usr/local/bin/backup_integrasys.sh` → `/var/backups/integrasys/AAAA-MM-DD/`
- **Render**: entorno antiguo usado para pruebas.  
  - **Estado**: deprecado, no se mantiene ni se usa como entorno de producción.

Consulta `README_prod.md` para el runbook de producción y `deploy/` para ejemplos de configuración.
