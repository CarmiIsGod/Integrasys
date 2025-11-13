# Integrasys – Sistema de Órdenes de Servicio

Aplicación Django para registrar recepción de equipos, seguimiento interno y comunicación con clientes.

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

Consulta `README_prod.md` para el runbook de producción y `deploy/` para ejemplos de configuración.
