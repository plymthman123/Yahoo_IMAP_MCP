"""
Yahoo Mail MCP Server.

Exposes 10 tools to Claude Code for reading, searching, managing,
and sending emails via Yahoo IMAP/SMTP.

All blocking IMAP/SMTP calls run in a ThreadPoolExecutor so they
don't block the FastMCP async event loop.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor

from mcp.server.fastmcp import FastMCP

from . import config, imap_client, smtp_client, email_parser, email_builder

mcp = FastMCP(name="yahoo-mail", version="1.0.0")

# Thread pool for blocking IMAP/SMTP calls (max 3 concurrent connections)
_executor = ThreadPoolExecutor(max_workers=3)


async def _run(fn, *args, **kwargs):
    """Run a blocking function in the thread pool without blocking the event loop."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, lambda: fn(*args, **kwargs))


# ---------------------------------------------------------------------------
# Tool 1: list_folders
# ---------------------------------------------------------------------------

@mcp.tool()
async def list_folders() -> list[dict]:
    """
    List all IMAP folders/mailboxes in the Yahoo Mail account.

    Returns a list of dicts with keys:
      - name (str): folder name (e.g. "INBOX", "Sent", "Bulk Mail")
      - flags (str): IMAP flags string
    """
    return await _run(imap_client.list_folders)


# ---------------------------------------------------------------------------
# Tool 2: list_emails
# ---------------------------------------------------------------------------

@mcp.tool()
async def list_emails(
    folder: str = "INBOX",
    limit: int = 20,
    offset: int = 0,
) -> dict:
    """
    List emails in a folder, newest first, with pagination.

    Args:
        folder: IMAP folder name. Default: "INBOX".
                Use list_folders to discover available folder names.
        limit:  Number of emails to return (1–100). Default: 20.
        offset: Number of emails to skip for pagination. Default: 0.

    Returns:
        {
          "emails": [{"uid", "subject", "from", "date", "size_bytes", "is_read"}, ...],
          "total": int,
          "has_more": bool
        }
    """
    limit = min(max(1, limit), config.MAX_LIMIT)
    return await _run(imap_client.list_emails, folder, limit, offset)


# ---------------------------------------------------------------------------
# Tool 3: read_email
# ---------------------------------------------------------------------------

@mcp.tool()
async def read_email(uid: str, folder: str = "INBOX") -> dict:
    """
    Read the full content of an email by its IMAP UID. Marks the email as read.

    Args:
        uid:    IMAP UID of the email (from list_emails or search_emails).
        folder: Folder containing the email. Default: "INBOX".

    Returns:
        {
          "message_id": str,
          "subject": str,
          "from": str,
          "to": [str],
          "cc": [str],
          "date": str,
          "text_body": str,
          "html_body": str,
          "attachments": [{"filename", "content_type", "size_bytes"}],
          "in_reply_to": str | null,
          "references": str | null
        }
    """
    raw_bytes = await _run(imap_client.read_email, uid, folder)
    parsed = email_parser.parse_raw_email(raw_bytes)
    # Mark as read (separate connection is fine; per-call architecture)
    await _run(imap_client.mark_email_flags, uid, folder, r"(\Seen)", True)
    return parsed


# ---------------------------------------------------------------------------
# Tool 4: search_emails
# ---------------------------------------------------------------------------

@mcp.tool()
async def search_emails(
    folder: str = "INBOX",
    from_addr: str | None = None,
    subject: str | None = None,
    since: str | None = None,
    before: str | None = None,
    unread_only: bool = False,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    """
    Search emails using server-side IMAP SEARCH criteria.

    Args:
        folder:      Folder to search. Default: "INBOX".
        from_addr:   Filter by sender email address (partial match supported).
        subject:     Filter by subject keyword (partial match supported).
        since:       Include emails on or after this date. Format: DD-Mon-YYYY
                     (e.g. "01-Jan-2025").
        before:      Include emails before this date. Format: DD-Mon-YYYY.
        unread_only: If True, return only unread emails. Default: False.
        limit:       Max results to return (1–100). Default: 20.
        offset:      Skip this many results for pagination. Default: 0.

    Returns:
        {"emails": [...], "total": int, "has_more": bool}
    """
    limit = min(max(1, limit), config.MAX_LIMIT)
    return await _run(
        imap_client.search_emails,
        folder, from_addr, subject, since, before, unread_only, limit, offset,
    )


# ---------------------------------------------------------------------------
# Tool 5: move_email
# ---------------------------------------------------------------------------

@mcp.tool()
async def move_email(uid: str, source_folder: str, dest_folder: str) -> dict:
    """
    Move an email to a different folder.

    Args:
        uid:           IMAP UID of the email.
        source_folder: Current folder containing the email.
        dest_folder:   Destination folder name (use list_folders to find names).

    Returns:
        {"moved": True, "uid": str, "to_folder": str}
    """
    await _run(imap_client.move_email, uid, source_folder, dest_folder)
    return {"moved": True, "uid": uid, "to_folder": dest_folder}


# ---------------------------------------------------------------------------
# Tool 6: delete_email
# ---------------------------------------------------------------------------

@mcp.tool()
async def delete_email(uid: str, folder: str = "INBOX") -> dict:
    """
    Delete an email by moving it to the Trash folder.
    This is a soft delete — the email can be recovered from Trash.

    Args:
        uid:    IMAP UID of the email.
        folder: Current folder of the email. Default: "INBOX".

    Returns:
        {"deleted": True, "uid": str, "moved_to": "Trash"}
    """
    await _run(imap_client.move_email, uid, folder, config.TRASH_FOLDER)
    return {"deleted": True, "uid": uid, "moved_to": config.TRASH_FOLDER}


# ---------------------------------------------------------------------------
# Tool 7: mark_as_spam
# ---------------------------------------------------------------------------

@mcp.tool()
async def mark_as_spam(uid: str, folder: str = "INBOX") -> dict:
    """
    Move an email to Yahoo's spam/junk folder ("Bulk Mail").

    Args:
        uid:    IMAP UID of the email.
        folder: Current folder of the email. Default: "INBOX".

    Returns:
        {"marked_as_spam": True, "uid": str, "moved_to": "Bulk Mail"}
    """
    await _run(imap_client.move_email, uid, folder, config.SPAM_FOLDER)
    return {"marked_as_spam": True, "uid": uid, "moved_to": config.SPAM_FOLDER}


# ---------------------------------------------------------------------------
# Tool 8: send_email
# ---------------------------------------------------------------------------

@mcp.tool()
async def send_email(
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
    body_html: str | None = None,
) -> dict:
    """
    Compose and send a new email.

    Args:
        to:        List of recipient email addresses.
        subject:   Email subject line.
        body:      Plain text body.
        cc:        Optional list of CC addresses.
        body_html: Optional HTML body. When provided alongside body, creates a
                   multipart/alternative message.

    Returns:
        {"sent": True, "message_id": str}
    """
    msg = email_builder.build_new_email(to, subject, body, cc, body_html)
    return await _run(smtp_client.send_message, msg)


# ---------------------------------------------------------------------------
# Tool 9: reply_email
# ---------------------------------------------------------------------------

@mcp.tool()
async def reply_email(
    uid: str,
    reply_body: str,
    folder: str = "INBOX",
    reply_all: bool = False,
) -> dict:
    """
    Reply to an existing email, preserving message threading headers.

    Args:
        uid:        IMAP UID of the email to reply to.
        reply_body: Text of the reply.
        folder:     Folder containing the original email. Default: "INBOX".
        reply_all:  If True, reply to all original recipients (To + Cc).
                    Default: False.

    Returns:
        {"sent": True, "message_id": str, "in_reply_to": str}
    """
    raw_bytes = await _run(imap_client.read_email, uid, folder)
    original = email_parser.parse_raw_email(raw_bytes)
    msg = email_builder.build_reply(original, reply_body, reply_all)
    result = await _run(smtp_client.send_message, msg)
    result["in_reply_to"] = original.get("message_id", "")
    return result


# ---------------------------------------------------------------------------
# Tool 10: forward_email
# ---------------------------------------------------------------------------

@mcp.tool()
async def forward_email(
    uid: str,
    to: list[str],
    forward_note: str = "",
    folder: str = "INBOX",
) -> dict:
    """
    Forward an existing email to new recipients.

    Args:
        uid:          IMAP UID of the email to forward.
        to:           List of recipient email addresses.
        forward_note: Optional text to prepend before the forwarded message body.
        folder:       Folder containing the original email. Default: "INBOX".

    Returns:
        {"sent": True, "message_id": str, "forwarded_subject": str}
    """
    raw_bytes = await _run(imap_client.read_email, uid, folder)
    original = email_parser.parse_raw_email(raw_bytes)
    msg = email_builder.build_forward(original, to, forward_note)
    result = await _run(smtp_client.send_message, msg)
    result["forwarded_subject"] = original.get("subject", "")
    return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()  # stdio transport (default for Claude Code)
