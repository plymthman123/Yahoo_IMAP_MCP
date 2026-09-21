"""
IMAP client for Yahoo Mail.

All operations use per-call connections (fresh SSL connect → authenticate →
execute → logout) to avoid Yahoo's aggressive idle-session timeouts.
All IMAP commands use UIDs (not sequence numbers) for stability.
"""
import email as _email_lib
import imaplib
import logging
import logging.handlers
import os
import socket
import ssl
import re
from contextlib import contextmanager
from email.header import decode_header, make_header
from typing import Generator

_logger = logging.getLogger("yahoo-imap")
if not _logger.handlers:
    _handler = logging.handlers.SysLogHandler(
        address="/var/run/syslog-ng-custom.sock",
        facility=logging.handlers.SysLogHandler.LOG_LOCAL0,
        socktype=socket.SOCK_DGRAM,
    )
    _handler.ident = "yahoo-imap: "
    _logger.addHandler(_handler)
    _logger.propagate = False

_logger.setLevel(logging.DEBUG if os.environ.get("YAHOO_MCP_DEBUG") else logging.INFO)


def _log(msg: str) -> None:
    _logger.info(msg)


def _decode_header_value(raw: str) -> str:
    """Decode an RFC2047-encoded header value (e.g. =?UTF-8?Q?...?=) to plain text."""
    try:
        return str(make_header(decode_header(raw)))
    except Exception:
        return raw

from . import config


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

@contextmanager
def imap_connection() -> Generator[imaplib.IMAP4_SSL, None, None]:
    """Open an authenticated IMAP4_SSL connection; logout on exit."""
    ssl_context = ssl.create_default_context()
    _log(f"Connecting to {config.IMAP_HOST}:{config.IMAP_PORT}")
    conn = imaplib.IMAP4_SSL(config.IMAP_HOST, config.IMAP_PORT, ssl_context=ssl_context)
    try:
        conn.login(config.YAHOO_EMAIL, config.YAHOO_APP_PASSWORD)
        _log("IMAP login successful")
        yield conn
    finally:
        try:
            conn.logout()
            _log("IMAP logout")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Folder operations
# ---------------------------------------------------------------------------

def list_folders() -> list[dict]:
    """Return all IMAP folders as a list of dicts with 'name' and 'flags'."""
    _log("list_folders()")
    with imap_connection() as conn:
        status, folder_list = conn.list()
        if status != "OK":
            raise RuntimeError(f"IMAP LIST failed: {folder_list}")

        results = []
        for item in folder_list:
            if not item:
                continue
            decoded = item.decode("utf-8", errors="replace") if isinstance(item, bytes) else item
            # IMAP LIST response format: (\Flags) "delimiter" "folder name"
            # e.g. '(\HasNoChildren) "/" "INBOX"' or '(\HasNoChildren) "/" Sent'
            match = re.match(r'\(([^)]*)\)\s+"[^"]+"\s+"?([^"]+)"?\s*$', decoded)
            if match:
                flags = match.group(1).strip()
                name = match.group(2).strip()
            else:
                # Fallback: take last token
                parts = decoded.rsplit(None, 1)
                name = parts[-1].strip('"') if parts else decoded
                flags = ""
            results.append({"name": name, "flags": flags})

        return results


# ---------------------------------------------------------------------------
# List / search
# ---------------------------------------------------------------------------

def _fetch_envelopes(conn: imaplib.IMAP4_SSL, uid_list: list[bytes]) -> list[dict]:
    """
    Fetch metadata for a list of UIDs.

    Uses BODY.PEEK[HEADER.FIELDS] instead of ENVELOPE so that Python's email
    library handles RFC2047 decoding and nested address structures — the ENVELOPE
    regex approach produced empty subjects/senders against Yahoo's IMAP server.
    PEEK ensures messages are not marked as read.
    """
    if not uid_list:
        return []
    uid_str = ",".join(u.decode() for u in uid_list)
    status, data = conn.uid(
        "FETCH", uid_str,
        "(UID FLAGS RFC822.SIZE BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])",
    )
    if status != "OK":
        raise RuntimeError(f"IMAP FETCH failed: {data}")

    uid_map: dict[str, dict] = {}
    for item in data:
        if not isinstance(item, tuple):
            continue
        meta = item[0].decode("utf-8", errors="replace") if isinstance(item[0], bytes) else str(item[0])
        header_bytes = item[1] if isinstance(item[1], bytes) else b""

        uid_m = re.search(r"UID (\d+)", meta)
        if not uid_m:
            continue
        uid = uid_m.group(1)

        size_m = re.search(r"RFC822\.SIZE (\d+)", meta)
        flags_m = re.search(r"FLAGS \(([^)]*)\)", meta)
        flags_str = flags_m.group(1) if flags_m else ""

        msg = _email_lib.message_from_bytes(header_bytes) if header_bytes else None
        uid_map[uid] = {
            "uid":        uid,
            "subject":    _decode_header_value(msg.get("Subject", "")) if msg else "",
            "from":       _decode_header_value(msg.get("From", "")) if msg else "",
            "date":       msg.get("Date", "") if msg else "",
            "size_bytes": int(size_m.group(1)) if size_m else 0,
            "is_read":    r"\Seen" in flags_str,
        }

    # Return in the same order as uid_list so callers get newest-first ordering.
    return [uid_map[u.decode()] for u in uid_list if u.decode() in uid_map]


def list_emails(folder: str, limit: int, offset: int) -> dict:
    """
    List emails in a folder newest-first with pagination.
    Returns {"emails": [...], "total": int, "has_more": bool}.
    """
    _log(f"list_emails(folder={folder!r}, limit={limit}, offset={offset})")
    with imap_connection() as conn:
        conn.select(f'"{folder}"', readonly=True)
        status, data = conn.uid("SEARCH", None, "ALL")
        if status != "OK":
            raise RuntimeError(f"IMAP SEARCH ALL failed in {folder}: {data}")

        all_uids = data[0].split() if data[0] else []
        total = len(all_uids)
        _log(f"Found {total} emails in {folder!r}, fetching {limit} from offset {offset}")
        # Reverse so index 0 = newest
        sliced = list(reversed(all_uids))[offset: offset + limit]
        emails = _fetch_envelopes(conn, sliced)
        _log(f"Returning {len(emails)} emails")
        return {"emails": emails, "total": total, "has_more": (offset + limit) < total}


def search_emails(
    folder: str,
    from_addr: str | None,
    subject: str | None,
    since: str | None,
    before: str | None,
    unread_only: bool,
    limit: int,
    offset: int,
) -> dict:
    """
    Search emails using IMAP server-side criteria.
    Dates must be in DD-Mon-YYYY format (e.g. 01-Jan-2025).
    Returns {"emails": [...], "total": int, "has_more": bool}.
    """
    criteria_parts = []
    if unread_only:
        criteria_parts.append("UNSEEN")
    if from_addr:
        criteria_parts.append(f'FROM "{from_addr}"')
    if subject:
        criteria_parts.append(f'SUBJECT "{subject}"')
    if since:
        criteria_parts.append(f"SINCE {since}")
    if before:
        criteria_parts.append(f"BEFORE {before}")

    search_str = " ".join(criteria_parts) if criteria_parts else "ALL"
    _log(f"search_emails(folder={folder!r}, criteria={search_str!r}, limit={limit}, offset={offset})")

    with imap_connection() as conn:
        conn.select(f'"{folder}"', readonly=True)
        status, data = conn.uid("SEARCH", None, search_str)
        if status != "OK":
            raise RuntimeError(f"IMAP SEARCH failed: {data}")

        all_uids = data[0].split() if data[0] else []
        total = len(all_uids)
        _log(f"Search returned {total} results, fetching {limit} from offset {offset}")
        sliced = list(reversed(all_uids))[offset: offset + limit]
        emails = _fetch_envelopes(conn, sliced)
        _log(f"Returning {len(emails)} emails")
        return {"emails": emails, "total": total, "has_more": (offset + limit) < total}


# ---------------------------------------------------------------------------
# Read a single email
# ---------------------------------------------------------------------------

def read_email(uid: str, folder: str) -> bytes:
    """
    Fetch the raw RFC822 bytes for a single email by UID.
    Raises ValueError if not found.
    """
    _log(f"read_email(uid={uid!r}, folder={folder!r})")
    with imap_connection() as conn:
        conn.select(f'"{folder}"', readonly=True)
        status, data = conn.uid("FETCH", uid, "(RFC822)")
        if status != "OK" or not data or data[0] is None:
            _log(f"ERROR: Email UID {uid} not found in {folder!r}")
            raise ValueError(f"Email UID {uid} not found in folder '{folder}'")
        # data[0] is a tuple: (b'uid FLAGS...', b'<raw RFC822 bytes>')
        if isinstance(data[0], tuple):
            return data[0][1]
        raise ValueError(f"Unexpected FETCH response for UID {uid}")


# ---------------------------------------------------------------------------
# Mutating operations
# ---------------------------------------------------------------------------

def mark_email_flags(uid: str, folder: str, flags: str, add: bool = True) -> None:
    """Add or remove IMAP flags (e.g. r'(\Seen)', r'(\Flagged)')."""
    _log(f"mark_email_flags(uid={uid!r}, folder={folder!r}, flags={flags!r}, add={add})")
    with imap_connection() as conn:
        conn.select(f'"{folder}"')
        op = "+FLAGS" if add else "-FLAGS"
        conn.uid("STORE", uid, op, flags)


def move_email(uid: str, source_folder: str, dest_folder: str) -> None:
    _log(f"move_email(uid={uid!r}, from={source_folder!r}, to={dest_folder!r})")
    """
    Move an email to dest_folder.
    Tries the IMAP MOVE extension (RFC 6851) first; falls back to COPY + DELETE.
    """
    with imap_connection() as conn:
        conn.select(f'"{source_folder}"')

        # Try RFC 6851 MOVE extension
        status, _ = conn.uid("MOVE", uid, f'"{dest_folder}"')
        if status == "OK":
            return

        # Fallback: COPY then mark deleted + expunge
        status, _ = conn.uid("COPY", uid, f'"{dest_folder}"')
        if status != "OK":
            raise RuntimeError(f"IMAP COPY to '{dest_folder}' failed for UID {uid}")
        conn.uid("STORE", uid, "+FLAGS", r"(\Deleted)")
        conn.expunge()


def move_emails_bulk(uids: list[str], source_folder: str, dest_folder: str) -> int:
    """
    Move multiple emails in a single IMAP session.
    Returns the number of emails moved.
    """
    if not uids:
        return 0
    _log(f"move_emails_bulk(count={len(uids)}, from={source_folder!r}, to={dest_folder!r})")
    uid_set = ",".join(uids)
    with imap_connection() as conn:
        conn.select(f'"{source_folder}"')
        status, _ = conn.uid("MOVE", uid_set, f'"{dest_folder}"')
        if status == "OK":
            return len(uids)
        # Fallback: COPY + DELETE
        status, _ = conn.uid("COPY", uid_set, f'"{dest_folder}"')
        if status != "OK":
            raise RuntimeError(f"IMAP COPY to '{dest_folder}' failed")
        conn.uid("STORE", uid_set, "+FLAGS", r"(\Deleted)")
        conn.expunge()
        return len(uids)


def fetch_envelopes_since(
    folder: str,
    since: str | None,
    limit: int,
    include_read: bool = True,
    before_uid: str | None = None,
) -> dict:
    """
    Fetch envelope data, optionally limited to messages since an IMAP date.

    Results are newest-first. When ``before_uid`` is supplied, only messages
    with lower UIDs are considered, allowing callers to request a subsequent
    batch without relying on mutable server-side state. When ``since`` is
    omitted, the search covers the entire selected folder.
    """
    if include_read:
        criteria = f"SINCE {since}" if since else "ALL"
    else:
        criteria = f"UNSEEN SINCE {since}" if since else "UNSEEN"
    _log(
        f"fetch_envelopes_since(folder={folder!r}, since={since!r}, "
        f"limit={limit}, include_read={include_read}, before_uid={before_uid!r})"
    )
    with imap_connection() as conn:
        conn.select(f'"{folder}"', readonly=True)
        status, data = conn.uid("SEARCH", None, criteria)
        if status != "OK":
            raise RuntimeError(f"IMAP SEARCH failed: {data}")
        all_uids = data[0].split() if data[0] else []
        if before_uid is not None:
            try:
                cursor = int(before_uid)
            except ValueError as exc:
                raise ValueError("before_uid must be a numeric IMAP UID") from exc
            all_uids = [uid for uid in all_uids if int(uid) < cursor]

        total = len(all_uids)
        newest_first = list(reversed(all_uids))
        sliced = newest_first[:limit]
        emails = _fetch_envelopes(conn, sliced)
        next_cursor = emails[-1]["uid"] if emails else None
        _log(f"fetch_envelopes_since returning {len(emails)} of {total}")
        return {
            "emails": emails,
            "total": total,
            "has_more": len(newest_first) > len(sliced),
            "next_cursor": next_cursor,
        }
