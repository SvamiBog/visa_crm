"""Outgoing email: confirmation codes and security notices.

Sent over SMTP (SMTP_* variables). Locally and on staging the letters go to Mailpit, which
catches every letter instead of delivering it (docs/staging.md); in production to the mail
provider. Without SMTP, local development and tests keep letters in OUTBOX; any other
environment refuses to send. Letter contents are never logged outside local development.
"""

import asyncio
import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Mail:
    to: str
    subject: str
    text: str


class MailNotConfigured(RuntimeError):
    """No SMTP server is set for this environment."""


class MailFailed(RuntimeError):
    """The SMTP server did not take the letter."""


# Local development and tests without SMTP: the letters stay here.
OUTBOX: list[Mail] = []


def _send_smtp(settings: Settings, mail: Mail) -> None:
    message = EmailMessage()
    message["From"] = settings.mail_from
    message["To"] = mail.to
    message["Subject"] = mail.subject
    message.set_content(mail.text)
    context = ssl.create_default_context()
    try:
        if settings.smtp_tls == "ssl":
            server: smtplib.SMTP = smtplib.SMTP_SSL(
                settings.smtp_host, settings.smtp_port, timeout=15, context=context
            )
        else:
            server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15)
        with server:
            if settings.smtp_tls == "starttls":
                server.starttls(context=context)
            if settings.smtp_user:
                server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(message)
    except (OSError, smtplib.SMTPException) as error:
        # The reason only: the address and the letter are personal data.
        raise MailFailed(type(error).__name__) from error


async def send_mail(mail: Mail) -> None:
    settings = get_settings()
    if not settings.smtp_host:
        if settings.env not in ("local", "test"):
            raise MailNotConfigured()
        OUTBOX.append(mail)
        if settings.env == "local":
            logger.warning("Mail (no SMTP set, not sent):\n%s\n\n%s", mail.subject, mail.text)
        return
    await asyncio.to_thread(_send_smtp, settings, mail)
