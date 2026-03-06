"""
Build outbound MIME messages for send, reply, and forward.
"""
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid
from typing import Any

from . import config


def build_new_email(
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
    body_html: str | None = None,
) -> MIMEMultipart:
    """Build a new outbound email (plain text, or multipart/alternative if HTML provided)."""
    if body_html:
        msg: MIMEMultipart = MIMEMultipart("alternative")
    else:
        msg = MIMEMultipart()

    msg["From"] = config.YAHOO_EMAIL
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=config.YAHOO_EMAIL.split("@")[1])

    if cc:
        msg["Cc"] = ", ".join(cc)

    msg.attach(MIMEText(body, "plain", "utf-8"))
    if body_html:
        msg.attach(MIMEText(body_html, "html", "utf-8"))

    return msg


def build_reply(
    original: dict[str, Any],
    reply_body: str,
    reply_all: bool = False,
) -> MIMEMultipart:
    """
    Build a reply to an existing email.
    Sets In-Reply-To and References headers for proper mail threading.
    Optionally CC all original recipients (reply_all=True).
    """
    msg: MIMEMultipart = MIMEMultipart()
    msg["From"] = config.YAHOO_EMAIL
    msg["To"] = original.get("from", "")
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=config.YAHOO_EMAIL.split("@")[1])

    original_subject = original.get("subject", "")
    msg["Subject"] = original_subject if original_subject.startswith("Re:") else f"Re: {original_subject}"

    # Threading headers
    orig_msg_id = original.get("message_id", "").strip()
    if orig_msg_id:
        msg["In-Reply-To"] = orig_msg_id
        existing_refs = (original.get("references") or "").strip()
        msg["References"] = f"{existing_refs} {orig_msg_id}".strip()

    if reply_all:
        all_addrs = original.get("to", []) + original.get("cc", [])
        cc_list = [a for a in all_addrs if config.YAHOO_EMAIL not in a]
        if cc_list:
            msg["Cc"] = ", ".join(cc_list)

    # Quote the original message
    original_text = original.get("text_body", "")
    quoted = "\n".join(f"> {line}" for line in original_text.splitlines())
    full_body = f"{reply_body}\n\n{quoted}"
    msg.attach(MIMEText(full_body, "plain", "utf-8"))

    return msg


def build_forward(
    original: dict[str, Any],
    to: list[str],
    forward_note: str = "",
) -> MIMEMultipart:
    """
    Build a forward including the original message headers and body.
    """
    msg: MIMEMultipart = MIMEMultipart()
    msg["From"] = config.YAHOO_EMAIL
    msg["To"] = ", ".join(to)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=config.YAHOO_EMAIL.split("@")[1])

    original_subject = original.get("subject", "")
    msg["Subject"] = original_subject if original_subject.startswith("Fwd:") else f"Fwd: {original_subject}"

    original_to = ", ".join(original.get("to", []))
    forward_body = (
        f"{forward_note}\n\n"
        "---------- Forwarded message ----------\n"
        f"From: {original.get('from', '')}\n"
        f"Date: {original.get('date', '')}\n"
        f"Subject: {original.get('subject', '')}\n"
        f"To: {original_to}\n\n"
        f"{original.get('text_body', '')}"
    ).lstrip("\n")

    msg.attach(MIMEText(forward_body, "plain", "utf-8"))
    return msg
