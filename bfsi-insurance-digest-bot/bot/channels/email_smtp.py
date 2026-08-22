"""Delivery by e-mail over SMTP — Gmail by default.

Credentials come from the environment:

    GMAIL_ADDRESS        the sending account, e.g. you@gmail.com
    GMAIL_APP_PASSWORD   a 16-character Google App Password, not the login one
    EMAIL_TO             recipients, comma-separated (defaults to the sender)
    EMAIL_FROM_NAME      optional display name on the From: header

Gmail refuses ordinary account passwords over SMTP. An App Password requires
2-Step Verification on the account; the README walks through it.
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from ..config import Config
from . import DeliveryResult

log = logging.getLogger(__name__)


class EmailError(RuntimeError):
    pass


def sender() -> str:
    return (os.environ.get("GMAIL_ADDRESS") or os.environ.get("SMTP_USERNAME") or "").strip()


def password() -> str:
    return (
        os.environ.get("GMAIL_APP_PASSWORD") or os.environ.get("SMTP_PASSWORD") or ""
    ).strip()


def recipients() -> list[str]:
    raw = os.environ.get("EMAIL_TO") or sender()
    return [part.strip() for part in raw.replace(";", ",").split(",") if part.strip()]


def configured() -> bool:
    return bool(sender() and password() and recipients())


def missing_settings() -> list[str]:
    missing = []
    if not sender():
        missing.append("GMAIL_ADDRESS")
    if not password():
        missing.append("GMAIL_APP_PASSWORD")
    if not recipients():
        missing.append("EMAIL_TO")
    return missing


def build_message(subject: str, text_body: str, html_body: str) -> EmailMessage:
    """A multipart/alternative message: plain text first, HTML as the richer
    alternative. Clients that cannot render HTML still get a readable brief."""
    message = EmailMessage()
    from_name = (os.environ.get("EMAIL_FROM_NAME") or "BFSI Digest Bot").strip()
    message["From"] = formataddr((from_name, sender()))
    message["To"] = ", ".join(recipients())
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain=sender().split("@")[-1] or "localhost")
    # Mail clients thread by subject; a daily brief should not collapse into one
    # ever-growing conversation.
    message["X-Entity-Ref-ID"] = message["Message-ID"]
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")
    return message


def _connect(config: Config, timeout: float):
    settings = config.email
    if settings.use_ssl:
        context = ssl.create_default_context()
        return smtplib.SMTP_SSL(
            settings.smtp_host, settings.smtp_port, timeout=timeout, context=context
        )
    server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=timeout)
    server.ehlo()
    server.starttls(context=ssl.create_default_context())
    server.ehlo()
    return server


def verify(config: Config, timeout: float = 30.0) -> str:
    """Log in and disconnect, without sending anything."""
    if not configured():
        raise EmailError("missing " + ", ".join(missing_settings()))
    try:
        with _connect(config, timeout) as server:
            server.login(sender(), password())
    except smtplib.SMTPAuthenticationError as exc:
        raise EmailError(
            "SMTP rejected the login. For Gmail this almost always means the "
            "password is a normal account password rather than a 16-character "
            f"App Password. ({exc.smtp_code})"
        ) from exc
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        raise EmailError(f"{type(exc).__name__}: {exc}") from exc
    return (
        f"{sender()} authenticated on {config.email.smtp_host}:{config.email.smtp_port} "
        f"→ {', '.join(recipients())}"
    )


def send(
    subject: str, text_body: str, html_body: str, config: Config, timeout: float = 45.0
) -> DeliveryResult:
    if not configured():
        return DeliveryResult("email", False, "not configured: " + ", ".join(missing_settings()))

    message = build_message(subject, text_body, html_body)
    try:
        with _connect(config, timeout) as server:
            server.login(sender(), password())
            server.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        return DeliveryResult(
            "email",
            False,
            "login rejected — Gmail needs a 16-character App Password, not the "
            f"account password ({exc.smtp_code})",
        )
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        return DeliveryResult("email", False, f"{type(exc).__name__}: {exc}")

    return DeliveryResult(
        "email", True, f"to {', '.join(recipients())}", recipients=len(recipients()), messages=1
    )
