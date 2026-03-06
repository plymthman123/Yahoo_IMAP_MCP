"""
IMAP client for Yahoo Mail.

All operations use per-call connections (fresh SSL connect → authenticate →
execute → logout) to avoid Yahoo's aggressive idle-session timeouts.
All IMAP commands use UIDs (not sequence numbers) for stability.
"""
import imaplib
import os
import ssl
import re
import sys
from contextlib import contextmanager
from typing import Generator


def _log(msg: str) -> None:
    if os.environ.get("YAHOO_MCP_DEBUG"):
        print(f"[yahoo-imap] {msg}", file=sys.stderr, flush=True)

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
    """Fetch envelope + flags for a list of UIDs; return list of summary dicts."""
    if not uid_list:
        return []
    uid_str = ",".join(u.decode() for u in uid_list)
    status, data = conn.uid("FETCH", uid_str, "(UID FLAGS ENVELOPE RFC822.SIZE)")
    if status != "OK":
        raise RuntimeError(f"IMAP FETCH envelope failed: {data}")

    emails = []
    # data is a flat list; ENVELOPE fetches return bytes items, not tuples
    for item in data:
        if isinstance(item, tuple):
            raw = item[0].decode("utf-8", errors="replace") if isinstance(item[0], bytes) else str(item[0])
        elif isinstance(item, bytes):
            raw = item.decode("utf-8", errors="replace")
        else:
            continue

        uid_match = re.search(r"UID (\d+)", raw)
        size_match = re.search(r"RFC822\.SIZE (\d+)", raw)
        flags_match = re.search(r"FLAGS \(([^)]*)\)", raw)

        # ENVELOPE fields in order: date subject from sender reply-to to cc bcc in-reply-to message-id
        env_match = re.search(r"ENVELOPE \((.+)\)\s+RFC822", raw, re.DOTALL)
        subject = ""
        from_addr = ""
        date_str = ""
        if env_match:
            env_raw = env_match.group(1)
            # Extract first quoted string as date
            parts = re.findall(r'"([^"]*)"|\(([^)]+)\)|NIL', env_raw)
            flat = [p[0] or p[1] for p in parts]
            if len(flat) > 0:
                date_str = flat[0]
            if len(flat) > 1:
                subject = flat[1]
            if len(flat) > 2:
                from_addr = flat[2]

        flags_str = flags_match.group(1) if flags_match else ""
        emails.append({
            "uid": uid_match.group(1) if uid_match else "",
            "subject": subject,
            "from": from_addr,
            "date": date_str,
            "size_bytes": int(size_match.group(1)) if size_match else 0,
            "is_read": r"\Seen" in flags_str,
        })

    return emails


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
