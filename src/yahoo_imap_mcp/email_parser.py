"""
Parse raw RFC822 email bytes into a clean Python dict.
Uses only stdlib + BeautifulSoup (html.parser) for HTML-to-text.
"""
import email as email_lib
from email import policy
from email.header import decode_header, make_header
from typing import Any

from bs4 import BeautifulSoup


def decode_mime_header(value: str | None) -> str:
    """Decode RFC2047 encoded-word headers (e.g. =?UTF-8?B?...?=) to plain text."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _html_to_text(html: str) -> str:
    """Convert HTML to readable plain text using BeautifulSoup html.parser."""
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text(separator="\n", strip=True)


def parse_raw_email(raw_bytes: bytes) -> dict[str, Any]:
    """
    Parse RFC822 bytes into a structured dict.

    Returns:
        {
          "message_id": str,
          "subject": str,
          "from": str,
          "to": list[str],
          "cc": list[str],
          "date": str,
          "text_body": str,
          "html_body": str,
          "attachments": [{"filename": str, "content_type": str, "size_bytes": int}],
          "in_reply_to": str | None,
          "references": str | None,
        }
    """
    msg = email_lib.message_from_bytes(raw_bytes, policy=policy.compat32)

    text_body = ""
    html_body = ""
    attachments: list[dict] = []

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))
            filename = part.get_filename()

            if filename or "attachment" in disposition:
                payload = part.get_payload(decode=True) or b""
                attachments.append({
                    "filename": decode_mime_header(filename) if filename else "unnamed",
                    "content_type": content_type,
                    "size_bytes": len(payload),
                })
            elif content_type == "text/plain" and not text_body:
                charset = part.get_content_charset() or "utf-8"
                payload = part.get_payload(decode=True) or b""
                text_body = payload.decode(charset, errors="replace")
            elif content_type == "text/html" and not html_body:
                charset = part.get_content_charset() or "utf-8"
                payload = part.get_payload(decode=True) or b""
                html_body = payload.decode(charset, errors="replace")
    else:
        content_type = msg.get_content_type()
        charset = msg.get_content_charset() or "utf-8"
        payload = msg.get_payload(decode=True) or b""
        decoded = payload.decode(charset, errors="replace")
        if content_type == "text/html":
            html_body = decoded
        else:
            text_body = decoded

    # If no plain text, derive from HTML
    if not text_body and html_body:
        text_body = _html_to_text(html_body)

    return {
        "message_id": msg.get("Message-ID", "").strip(),
        "subject": decode_mime_header(msg.get("Subject")),
        "from": decode_mime_header(msg.get("From")),
        "to": [decode_mime_header(a) for a in (msg.get_all("To") or [])],
        "cc": [decode_mime_header(a) for a in (msg.get_all("Cc") or [])],
        "date": str(msg.get("Date", "")),
        "text_body": text_body,
        "html_body": html_body,
        "attachments": attachments,
        "in_reply_to": msg.get("In-Reply-To"),
        "references": msg.get("References"),
    }
