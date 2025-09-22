# -*- coding: utf-8 -*-
from pathlib import Path
from textwrap import dedent

path = Path('core/views.py')
text = path.read_text(encoding='utf-8')
old_block = dedent('''
    recipients = []
    if order.assigned_to and order.assigned_to.email:
        recipients.append(order.assigned_to.email)
    if not recipients:
        recipients = list(
            User.objects.filter(is_staff=True)
            .exclude(email="")
            .values_list("email", flat=True)
        )
if recipients:
    total_display = format(estimate.total or Decimal("0.00"), ".2f")
    subject = f"Cotizacion aprobada - {order.folio}"
    body = (
        f"La cotizacion de la orden {order.folio} fue aprobada el "
        f"{timezone.localtime(now).strftime('%Y-%m-%d %H:%M')}\\n"
        f"Total autorizado: ${total_display}\\n"
    )
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, list(recipients), fail_silently=True)
        Notification.objects.create(
            order=order,
            kind="email",
            channel="estimate_approve",
            ok=True,
            payload={"recipients": list(recipients)},
        )

customer_email = (order.device.customer.email or "").strip()
if customer_email:
    confirmation = (
        f"Gracias por aprobar la cotizacion de la orden {order.folio}\\n"
        f"Nos pondremos en contacto contigo para continuar con el servicio."
    )
    send_mail(
            subject=f"Confirmacion de cotizacion {order.folio}",
            message=confirmation,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[customer_email],
            fail_silently=True,
        )

    messages.success(request, "Gracias, hemos recibido tu aprobacion.")
''')
new_block = dedent('''
    recipients = []
    if order.assigned_to and order.assigned_to.email:
        recipients.append(order.assigned_to.email)
    if not recipients:
        recipients = list(
            User.objects.filter(is_staff=True)
            .exclude(email="")
            .values_list("email", flat=True)
        )
    if recipients:
        total_display = format(estimate.total or Decimal("0.00"), ".2f")
        subject = f"Cotizacion aprobada - {order.folio}"
        body = (
            f"La cotizacion de la orden {order.folio} fue aprobada el "
            f"{timezone.localtime(now).strftime('%Y-%m-%d %H:%M')}.\n"
            f"Total autorizado: ${total_display}.\n"
        )
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, list(recipients), fail_silently=True)
        Notification.objects.create(
            order=order,
            kind="email",
            channel="estimate_approve",
            ok=True,
            payload={"recipients": list(recipients)},
        )

    customer_email = (order.device.customer.email or "").strip()
    if customer_email:
        confirmation = (
            f"Gracias por aprobar la cotizacion de la orden {order.folio}.\n"
            f"Nos pondremos en contacto contigo para continuar con el servicio."
        )
        send_mail(
            subject=f"Confirmacion de cotizacion {order.folio}",
            message=confirmation,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[customer_email],
            fail_silently=True,
        )

    messages.success(request, "Gracias, hemos recibido tu aprobacion.")
''')
if old_block not in text:
    raise SystemExit('approve block not found')
text = text.replace(old_block, new_block, 1)

old_block_decline = dedent('''
    recipients = []
    if order.assigned_to and order.assigned_to.email:
        recipients.append(order.assigned_to.email)
    if not recipients:
        recipients = list(
            User.objects.filter(is_staff=True)
            .exclude(email="")
            .values_list("email", flat=True)
        )
    if recipients:
        subject = f"Cotizacion rechazada - {order.folio}"
        body = (
            f"La cotizacion de la orden {order.folio} fue rechazada el "
            f"{timezone.localtime(now).strftime('%Y-%m-%d %H:%M')}\\n"
        )
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, list(recipients), fail_silently=True)
        Notification.objects.create(
            order=order,
            kind="email",
            channel="estimate_decline",
            ok=True,
            payload={"recipients": list(recipients)},
        )

    customer_email = (order.device.customer.email or "").strip()
    if customer_email:
        message = (
            f"Hemos registrado el rechazo de la cotizacion de la orden {order.folio}\\n"
            f"Si necesitas cambios o ayuda adicional, por favor contactanos."
        )
        send_mail(
            subject=f"Cotizacion {order.folio} rechazada",
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[customer_email],
            fail_silently=True,
        )

    messages.success(request, "Hemos registrado tu decision.")
''')
new_block_decline = dedent('''
    recipients = []
    if order.assigned_to and order.assigned_to.email:
        recipients.append(order.assigned_to.email)
    if not recipients:
        recipients = list(
            User.objects.filter(is_staff=True)
            .exclude(email="")
            .values_list("email", flat=True)
        )
    if recipients:
        subject = f"Cotizacion rechazada - {order.folio}"
        body = (
            f"La cotizacion de la orden {order.folio} fue rechazada el "
            f"{timezone.localtime(now).strftime('%Y-%m-%d %H:%M')}.\n"
        )
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, list(recipients), fail_silently=True)
        Notification.objects.create(
            order=order,
            kind="email",
            channel="estimate_decline",
            ok=True,
            payload={"recipients": list(recipients)},
        )

    customer_email = (order.device.customer.email or "").strip()
    if customer_email:
        message = (
            f"Hemos registrado el rechazo de la cotizacion de la orden {order.folio}.\n"
            f"Si necesitas cambios o ayuda adicional, por favor contactanos."
        )
        send_mail(
            subject=f"Cotizacion {order.folio} rechazada",
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[customer_email],
            fail_silently=True,
        )

    messages.success(request, "Hemos registrado tu decision.")
''')
if old_block_decline not in text:
    raise SystemExit('decline block not found')
text = text.replace(old_block_decline, new_block_decline, 1)

path.write_text(text, encoding='utf-8')
