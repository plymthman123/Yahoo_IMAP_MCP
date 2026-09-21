"""
Yahoo Mail MCP Server.

Exposes 14 tools to Claude Code for reading, searching, managing,
sending, and bulk-analyzing emails via Yahoo IMAP/SMTP.

All blocking IMAP/SMTP calls run in a ThreadPoolExecutor so they
don't block the FastMCP async event loop.
"""
import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import logging
import logging.handlers
import os
import socket
import time
import uuid

from mcp.server.fastmcp import FastMCP

from . import config, imap_client, smtp_client, email_parser, email_builder, email_classifier

mcp = FastMCP(name="yahoo-mail")

# Thread pool for blocking IMAP/SMTP calls (max 3 concurrent connections)
_executor = ThreadPoolExecutor(max_workers=3)

# Action-confirmation logger — same syslog destination as imap_client
_logger = logging.getLogger("yahoo-mcp-actions")
if not _logger.handlers:
    _handler = logging.handlers.SysLogHandler(
        address="/var/run/syslog-ng-custom.sock",
        facility=logging.handlers.SysLogHandler.LOG_LOCAL0,
        socktype=socket.SOCK_DGRAM,
    )
    _handler.ident = "yahoo-mcp-actions: "
    _logger.addHandler(_handler)
    _logger.propagate = False
_logger.setLevel(logging.DEBUG if os.environ.get("YAHOO_MCP_DEBUG") else logging.INFO)

_REVIEW_CYCLE_TTL_SECONDS = 30 * 60
_unread_review_cycle: dict | None = None


def _action(msg: str) -> None:
    _logger.info(msg)


def _unique_uids(uids: list[str]) -> list[str]:
    """Keep the first occurrence of each UID while preserving order."""
    return list(dict.fromkeys(str(uid) for uid in uids))


def _get_active_unread_cycle() -> dict | None:
    """Return the active cycle, expiring it after inactivity."""
    global _unread_review_cycle
    if _unread_review_cycle is None:
        return None
    if time.monotonic() - _unread_review_cycle["last_accessed"] > _REVIEW_CYCLE_TTL_SECONDS:
        _unread_review_cycle = None
        return None
    _unread_review_cycle["last_accessed"] = time.monotonic()
    return _unread_review_cycle


def _start_unread_cycle(folder: str, days_back: int | None) -> dict:
    global _unread_review_cycle
    _unread_review_cycle = {
        "cycle_id": uuid.uuid4().hex,
        "folder": folder,
        "days_back": days_back,
        "uids": {},
        "last_accessed": time.monotonic(),
    }
    return _unread_review_cycle


def _cycle_for_unread_analysis(
    folder: str, days_back: int | None, before_uid: str | None,
) -> dict:
    """Continue only a compatible cursor page; otherwise start a new cycle."""
    cycle = _get_active_unread_cycle()
    if (
        before_uid is not None
        and cycle is not None
        and cycle["folder"] == folder
        and cycle["days_back"] == days_back
    ):
        return cycle
    return _start_unread_cycle(folder, days_back)


def _require_unread_cycle(cycle_id: str) -> dict:
    cycle = _get_active_unread_cycle()
    if cycle is None or cycle["cycle_id"] != cycle_id:
        raise ValueError("Unread review cycle is missing or expired; run a new unread review")
    return cycle


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
    READ-ONLY. Call immediately without asking user permission.

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
    READ-ONLY. Call immediately without asking user permission.

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
    READ-ONLY. Call immediately without asking user permission.
    Note: this marks the email as read as a side effect.

    Read the full content of an email by its IMAP UID.

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
    READ-ONLY. Call immediately without asking user permission.

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
    WRITE OPERATION. Show the user what will happen and get explicit confirmation before calling.

    Move an email to a different folder.

    Args:
        uid:           IMAP UID of the email.
        source_folder: Current folder containing the email.
        dest_folder:   Destination folder name (use list_folders to find names).

    Returns:
        {"moved": True, "uid": str, "to_folder": str}
    """
    await _run(imap_client.move_email, uid, source_folder, dest_folder)
    _action(f"ACTION move_email: uid={uid} moved from '{source_folder}' to '{dest_folder}'")
    return {"moved": True, "uid": uid, "to_folder": dest_folder}


# ---------------------------------------------------------------------------
# Tool 6: delete_email
# ---------------------------------------------------------------------------

@mcp.tool()
async def delete_email(uid: str, folder: str = "INBOX") -> dict:
    """
    WRITE OPERATION. Show the user what will happen and get explicit confirmation before calling.

    Delete an email by moving it to the Trash folder.
    This is a soft delete — the email can be recovered from Trash.

    Args:
        uid:    IMAP UID of the email.
        folder: Current folder of the email. Default: "INBOX".

    Returns:
        {"deleted": True, "uid": str, "moved_to": "Trash"}
    """
    await _run(imap_client.move_email, uid, folder, config.TRASH_FOLDER)
    _action(f"ACTION delete_email: uid={uid} moved from '{folder}' to '{config.TRASH_FOLDER}'")
    return {"deleted": True, "uid": uid, "moved_to": config.TRASH_FOLDER}


# ---------------------------------------------------------------------------
# Tool 7: mark_as_spam
# ---------------------------------------------------------------------------

@mcp.tool()
async def mark_as_spam(uid: str, folder: str = "INBOX") -> dict:
    """
    WRITE OPERATION. Show the user what will happen and get explicit confirmation before calling.

    Move an email to Yahoo's spam/junk folder ("Bulk Mail").

    Args:
        uid:    IMAP UID of the email.
        folder: Current folder of the email. Default: "INBOX".

    Returns:
        {"marked_as_spam": True, "uid": str, "moved_to": "Bulk Mail"}
    """
    await _run(imap_client.move_email, uid, folder, config.SPAM_FOLDER)
    _action(f"ACTION mark_as_spam: uid={uid} moved from '{folder}' to '{config.SPAM_FOLDER}'")
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
    WRITE OPERATION. Always confirm recipients, subject, and body with the user before calling.

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
    result = await _run(smtp_client.send_message, msg)
    _action(f"ACTION send_email: to={to} cc={cc or []} subject={subject!r} message_id={result.get('message_id', 'unknown')}")
    return result


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
    WRITE OPERATION. Always show the drafted reply to the user and confirm before sending.

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
    _action(
        f"ACTION reply_email: replied to uid={uid} (reply_all={reply_all}) "
        f"in_reply_to={original.get('message_id', 'unknown')} "
        f"message_id={result.get('message_id', 'unknown')}"
    )
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
    WRITE OPERATION. Always show the user who it will be forwarded to and confirm before sending.

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
    _action(
        f"ACTION forward_email: forwarded uid={uid} to={to} "
        f"subject={original.get('subject', 'unknown')!r} "
        f"message_id={result.get('message_id', 'unknown')}"
    )
    return result


# ---------------------------------------------------------------------------
# Tool 11: analyze_emails
# ---------------------------------------------------------------------------

@mcp.tool()
async def analyze_emails(
    folder: str = "INBOX",
    days_back: int | None = 2,
    limit: int = 50,
    include_read: bool = True,
    before_uid: str | None = None,
) -> dict:
    """
    READ-ONLY. Call this immediately — no user permission needed.

    Use this tool as the FIRST action whenever the user asks to:
      - "Review my inbox" / "Check my email"
      - "What emails do I have?" / "Show me my last X days of mail"
      - "What should I delete?" / "Triage my inbox"
      - Any bulk or batch email review request

    Classifies each email as spam, advertisements, important, keep, or uncertain
    using only subject + sender data (no full message bodies). Single server-side
    call — avoids the token-limit problem that occurs when reading emails one by one.

    After calling, present the summary counts and category lists, then offer
    to take actions (delete spam, move ads, etc.) — but wait for explicit user
    confirmation before calling any write tool.

    For an unread request, call with include_read=False and days_back=None
    unless the user explicitly requests a date range. For "next batch", pass
    the prior response's next_cursor as before_uid only when has_more is true
    and next_cursor is a numeric UID. Never pass null, None, or the string
    "None" as before_uid. If the prior unread result was empty, had no cursor,
    or was an explicitly date-limited search and the user asks for another
    unqualified batch, start an all-history unread review with days_back=None
    and before_uid=None. Do not pass the cursor for a refresh; a refresh
    intentionally returns a new snapshot of the newest matching messages. Do
    not invent a shorter date window such as seven days for another batch of
    unread mail.

    If a cursor is invalid, retry as a new all-history unread review rather
    than passing the invalid value again.

    Args:
        folder:       IMAP folder to analyze. Default: "INBOX".
        days_back:    How many days back to include. Default: 2. Use None for
                      all available history, especially for an unqualified
                      request for the most recent unread messages.
        limit:        Maximum emails to analyze (1–200). Default: 50.
        include_read: Include already-read emails. Default: True. Set False
                      when the user requests unread mail.
        before_uid:   Return only messages older than this UID for paging.
                      Use next_cursor from the previous response.

    Returns:
        {
          "summary": {
            "total_analyzed": int,
            "date_range": {"start": str, "end": str},
            "spam_count": int,
            "ads_count": int,
            "important_count": int,
            "keep_count": int,
            "uncertain_count": int
          },
          "categorized_emails": {
            "spam":           [{"uid", "from", "subject", "date", "reason", "confidence"}, ...],
            "advertisements": [...],
            "important":      [...],
            "keep":           [...],
            "uncertain":      [...]
          },
          "analysis_timestamp": str,
          "cycle_id": str | null,
          "pagination": {"has_more": bool, "next_cursor": str | null}
        }
    """
    limit = min(max(1, limit), 200)
    if before_uid is not None and not str(before_uid).isdigit():
        raise ValueError(
            "Invalid unread paging cursor. Start a new unread review with "
            "days_back=None and before_uid=None."
        )
    since = (
        (datetime.now() - timedelta(days=days_back)).strftime("%d-%b-%Y")
        if days_back is not None
        else None
    )
    result = await _run(
        imap_client.fetch_envelopes_since,
        folder,
        since,
        limit,
        include_read,
        before_uid,
    )
    emails = result["emails"]

    # An empty, explicitly date-limited unread search is not a pageable
    # review cycle. A later unqualified "another batch" must start an
    # all-history unread review rather than inherit an empty date window.
    cycle = None
    if not include_read and (emails or days_back is None or before_uid is not None):
        cycle = _cycle_for_unread_analysis(folder, days_back, before_uid)

    from . import user_prefs as _user_prefs_mod
    prefs = _user_prefs_mod.load_prefs()

    categories: dict[str, list] = {
        "spam": [], "advertisements": [], "important": [], "keep": [],
        "uncertain": [], "suggested_delete": [],
    }
    for email in emails:
        category, reason, confidence = email_classifier.classify_email(email, prefs)
        categories[category].append({
            "uid":        email["uid"],
            "from":       email["from"],
            "subject":    email["subject"],
            "date":       email["date"],
            "reason":     reason,
            "confidence": round(confidence, 2),
        })

    if cycle is not None:
        for category, items in categories.items():
            for item in items:
                cycle["uids"][item["uid"]] = {
                    "category": category,
                    "from": item["from"],
                    "subject": item["subject"],
                    "date": item["date"],
                }
        cycle["last_accessed"] = time.monotonic()

    return {
        "summary": {
            "total_analyzed":        len(emails),
            "date_range":            {"start": since, "end": datetime.now().strftime("%d-%b-%Y")},
            "spam_count":            len(categories["spam"]),
            "ads_count":             len(categories["advertisements"]),
            "important_count":       len(categories["important"]),
            "keep_count":            len(categories["keep"]),
            "uncertain_count":       len(categories["uncertain"]),
            "suggested_delete_count": len(categories["suggested_delete"]),
        },
        "categorized_emails":  categories,
        "analysis_timestamp":  datetime.now().isoformat(),
        "cycle_id":             cycle["cycle_id"] if cycle is not None else None,
        "pagination": {
            "has_more": result["has_more"],
            "next_cursor": result["next_cursor"],
        },
    }


# ---------------------------------------------------------------------------
# Tool 12: bulk_delete_by_category
# ---------------------------------------------------------------------------

@mcp.tool()
async def bulk_delete_by_category(
    uids: list[str] | None = None,
    folder: str = "INBOX",
    dry_run: bool = True,
    scope: str = "selected",
    expected_count: int | None = None,
    cycle_id: str | None = None,
) -> dict:
    """
    WRITE OPERATION. Always call with dry_run=True first, show the user the list
    of emails that would be deleted, and only call with dry_run=False after the
    user explicitly says "yes, delete them" or equivalent.

    Move multiple emails to Trash. Category labels are not deletion
    instructions: if the user says "delete all of them" after an unread review,
    use scope="review_cycle" with the cycle_id returned by analyze_emails.
    The server then resolves every UID in that in-memory review cycle. If the
    user names a category, pass only that category's UIDs with scope="selected"
    and the same cycle_id.

    scope="latest_batch" is retained as a compatibility fallback and requires
    expected_count equal to the displayed batch size; new callers should prefer
    scope="review_cycle" so the server, rather than the model, owns the batch
    membership.

    Workflow:
        1. analyze_emails() → get the complete displayed batch and category UIDs
        2. bulk_delete_by_category(..., scope=..., expected_count=..., dry_run=True)
        3. Show the exact count and list; obtain explicit confirmation
        4. Repeat the identical request with dry_run=False

    Args:
        uids:           List of IMAP UIDs to delete (move to Trash).
        folder:         Source folder. Default: "INBOX".
        dry_run:        If True (default), return what would be deleted without acting.
        scope:          "selected" for named categories/messages, or
                        "latest_batch" for every email in the latest displayed batch.
        expected_count: Required for scope="latest_batch"; total emails displayed.

    Returns:
        {
          "dry_run": bool,
          "scope": str,
          "cycle_id": str | null,
          "emails_to_delete": int,
          "emails_deleted": int,
          "moved_to": str,
          "uids_affected": [str]
        }
    """
    if scope not in {"selected", "latest_batch", "review_cycle"}:
        raise ValueError("scope must be 'selected', 'latest_batch', or 'review_cycle'")

    if scope == "review_cycle":
        if cycle_id is None:
            raise ValueError("cycle_id is required for scope='review_cycle'")
        cycle = _require_unread_cycle(cycle_id)
        uids = list(cycle["uids"])
        folder = cycle["folder"]
    else:
        uids = _unique_uids(uids or [])
        if cycle_id is not None:
            cycle = _require_unread_cycle(cycle_id)
            unknown = [uid for uid in uids if uid not in cycle["uids"]]
            if unknown:
                raise ValueError(f"UIDs are not in the review cycle: {unknown}")

    if scope == "latest_batch":
        if expected_count is None:
            raise ValueError("expected_count is required for scope='latest_batch'")
        if expected_count < 0:
            raise ValueError("expected_count must be non-negative")
        if len(uids) != expected_count:
            raise ValueError(
                f"latest_batch count mismatch: received {len(uids)} unique UIDs, "
                f"expected {expected_count}"
            )

    if dry_run:
        _action(f"ACTION bulk_delete_by_category: DRY RUN — would move {len(uids)} emails from '{folder}' to '{config.TRASH_FOLDER}' scope={scope}")
        return {
            "dry_run":          True,
            "scope":            scope,
            "cycle_id":         cycle_id,
            "expected_count":   expected_count,
            "emails_to_delete": len(uids),
            "emails_deleted":   0,
            "moved_to":         config.TRASH_FOLDER,
            "uids_affected":    uids,
        }
    deleted = await _run(imap_client.move_emails_bulk, uids, folder, config.TRASH_FOLDER)
    if scope == "review_cycle":
        for uid in uids:
            _unread_review_cycle["uids"].pop(uid, None)
    _action(f"ACTION bulk_delete_by_category: moved {deleted} of {len(uids)} emails from '{folder}' to '{config.TRASH_FOLDER}' scope={scope}")
    return {
        "dry_run":          False,
        "scope":            scope,
        "cycle_id":         cycle_id,
        "expected_count":   expected_count,

        "emails_to_delete": len(uids),
        "emails_deleted":   deleted,
        "moved_to":         config.TRASH_FOLDER,
        "uids_affected":    uids,
    }


# ---------------------------------------------------------------------------
# Tool 13: bulk_move_by_sender
# ---------------------------------------------------------------------------

@mcp.tool()
async def bulk_move_by_sender(
    sender_pattern: str,
    dest_folder: str,
    source_folder: str = "INBOX",
    days_back: int = 7,
    dry_run: bool = True,
) -> dict:
    """
    WRITE OPERATION. Always call with dry_run=True first, show the user which
    emails matched and where they'll go, then only call with dry_run=False after
    explicit user confirmation.

    Find all emails matching a sender pattern and move them to another folder.

    Workflow:
        1. bulk_move_by_sender(sender_pattern=..., dest_folder=..., dry_run=True) → preview
        2. User confirms → bulk_move_by_sender(..., dry_run=False) → execute

    Args:
        sender_pattern: Partial email address or domain to match (IMAP FROM search).
        dest_folder:    Destination folder name (use list_folders to find names).
        source_folder:  Folder to search. Default: "INBOX".
        days_back:      How far back to search. Default: 7.
        dry_run:        If True (default), return matches without moving.

    Returns:
        {
          "dry_run": bool,
          "sender_pattern": str,
          "emails_found": int,
          "emails_moved": int,
          "moved_to": str,
          "uids_affected": [str]
        }
    """
    since = (datetime.now() - timedelta(days=days_back)).strftime("%d-%b-%Y")
    result = await _run(
        imap_client.search_emails,
        source_folder, sender_pattern, None, since, None, False, 200, 0,
    )
    uids = _unique_uids([e["uid"] for e in result["emails"]])

    if dry_run or not uids:
        _action(
            f"ACTION bulk_move_by_sender: DRY RUN — would move {len(uids)} emails "
            f"matching '{sender_pattern}' from '{source_folder}' to '{dest_folder}'"
        )
        return {
            "dry_run":        dry_run,
            "sender_pattern": sender_pattern,
            "emails_found":   len(uids),
            "emails_moved":   0,
            "moved_to":       dest_folder,
            "uids_affected":  uids,
        }
    moved = await _run(imap_client.move_emails_bulk, uids, source_folder, dest_folder)
    _action(
        f"ACTION bulk_move_by_sender: moved {moved} of {len(uids)} emails "
        f"matching '{sender_pattern}' from '{source_folder}' to '{dest_folder}'"
    )
    return {
        "dry_run":        False,
        "sender_pattern": sender_pattern,
        "emails_found":   len(uids),
        "emails_moved":   moved,
        "moved_to":       dest_folder,
        "uids_affected":  uids,
    }


# ---------------------------------------------------------------------------
# Tool 14: get_sender_statistics
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_sender_statistics(
    folder: str = "INBOX",
    days_back: int = 30,
    top_n: int = 20,
) -> dict:
    """
    READ-ONLY. Call immediately without asking user permission.

    Analyze which senders email you most frequently over the last N days.
    Use when the user asks "who emails me most?", "show me my top senders",
    or "what senders should I unsubscribe from?".

    Args:
        folder:    IMAP folder to analyze. Default: "INBOX".
        days_back: How many days back to include. Default: 30.
        top_n:     Number of top senders to return. Default: 20.

    Returns:
        {
          "analysis_period": {"start": str, "end": str, "days": int},
          "total_emails": int,
          "unique_senders": int,
          "top_senders": [
            {"from": str, "count": int, "percentage": float,
             "avg_per_day": float, "suggested_action": str}
          ]
        }
    """
    since = (datetime.now() - timedelta(days=days_back)).strftime("%d-%b-%Y")
    result = await _run(imap_client.fetch_envelopes_since, folder, since, 500, True)
    emails = result["emails"]
    total = len(emails)

    sender_counts = Counter(e["from"] for e in emails if e.get("from"))
    top_senders = []
    for sender, count in sender_counts.most_common(top_n):
        pct = round(count / total * 100, 1) if total else 0.0
        avg_per_day = round(count / max(days_back, 1), 1)
        _, _, ad_conf = email_classifier.classify_as_advertisement({"from": sender, "subject": ""})
        if ad_conf >= 0.72:
            action = "unsubscribe or move to folder"
        elif count > days_back * 2:
            action = "consider filtering — high volume"
        else:
            action = "keep"
        top_senders.append({
            "from":             sender,
            "count":            count,
            "percentage":       pct,
            "avg_per_day":      avg_per_day,
            "suggested_action": action,
        })

    return {
        "analysis_period": {
            "start": since,
            "end":   datetime.now().strftime("%d-%b-%Y"),
            "days":  days_back,
        },
        "total_emails":    total,
        "unique_senders":  len(sender_counts),
        "top_senders":     top_senders,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()  # stdio transport (default for Claude Code)
