"""
SMTP client for Yahoo Mail.
Uses port 465 with SMTP_SSL (SSL from connection start), not STARTTLS.
"""
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart

from . import config


def send_message(msg: MIMEMultipart) -> dict:
    """
    Send a composed MIME message via Yahoo SMTP.

    Collects recipients from To, Cc, and Bcc headers.
    Returns {"sent": True, "message_id": str}.
    """
    ssl_context = ssl.create_default_context()

    with smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, context=ssl_context) as server:
        server.login(config.YAHOO_EMAIL, config.YAHOO_APP_PASSWORD)

        # Gather all recipients from To + Cc + Bcc
        recipients: list[str] = []
        for header in ("To", "Cc", "Bcc"):
            value = msg.get(header, "")
            if value:
                recipients.extend(addr.strip() for addr in value.split(",") if addr.strip())

        server.sendmail(
            from_addr=config.YAHOO_EMAIL,
            to_addrs=recipients,
            msg=msg.as_string(),
        )

    return {"sent": True, "message_id": msg.get("Message-ID", "")}
