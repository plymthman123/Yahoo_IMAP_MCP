"""
SMTP client for Yahoo Mail.
Uses port 465 with SMTP_SSL (SSL from connection start), not STARTTLS.
"""
import logging
import logging.handlers
import os
import socket
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart

from . import config

_logger = logging.getLogger("yahoo-smtp")
if not _logger.handlers:
    _handler = logging.handlers.SysLogHandler(
        address="/var/run/syslog",
        facility=logging.handlers.SysLogHandler.LOG_LOCAL0,
        socktype=socket.SOCK_DGRAM,
    )
    _handler.ident = "yahoo-smtp: "
    _logger.addHandler(_handler)
    _logger.propagate = False

_logger.setLevel(logging.DEBUG if os.environ.get("YAHOO_MCP_DEBUG") else logging.INFO)


def _log(msg: str) -> None:
    _logger.info(msg)


def send_message(msg: MIMEMultipart) -> dict:
    """
    Send a composed MIME message via Yahoo SMTP.

    Collects recipients from To, Cc, and Bcc headers.
    Returns {"sent": True, "message_id": str}.
    """
    ssl_context = ssl.create_default_context()
    _log(f"Connecting to {config.SMTP_HOST}:{config.SMTP_PORT}")

    with smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, context=ssl_context) as server:
        server.login(config.YAHOO_EMAIL, config.YAHOO_APP_PASSWORD)
        _log("SMTP login successful")

        # Gather all recipients from To + Cc + Bcc
        recipients: list[str] = []
        for header in ("To", "Cc", "Bcc"):
            value = msg.get(header, "")
            if value:
                recipients.extend(addr.strip() for addr in value.split(",") if addr.strip())

        _log(f"Sending to {recipients}, subject={msg.get('Subject', '')!r}")
        server.sendmail(
            from_addr=config.YAHOO_EMAIL,
            to_addrs=recipients,
            msg=msg.as_string(),
        )
        _log("Email sent successfully")

    return {"sent": True, "message_id": msg.get("Message-ID", "")}
