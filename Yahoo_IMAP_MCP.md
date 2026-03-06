# Yahoo IMAP MCP Server

A local Python MCP server that gives Claude Code full access to a Yahoo Mail account via IMAP (read/manage) and SMTP (send).

---

## Project Structure

```
Yahoo_IMAP_MCP/
├── .env                          # Credentials (never commit this)
├── .env.example                  # Template for credentials
├── .gitignore
├── .mcp.json                     # Project-scoped Claude Code MCP registration
├── requirements.txt
├── Yahoo_IMAP_MCP.md             # This file
└── src/
    └── yahoo_imap_mcp/
        ├── __init__.py
        ├── server.py             # FastMCP entry point + all 10 @tool decorators
        ├── imap_client.py        # All IMAP operations (per-call connections, UID-based)
        ├── smtp_client.py        # SMTP send via port 465 SSL
        ├── email_parser.py       # RFC822 → structured dict (MIME, HTML→text, headers)
        ├── email_builder.py      # Build MIME messages for send/reply/forward
        └── config.py             # .env loader + Yahoo server constants
```

---

## Prerequisites

- Python 3.11+
- Yahoo Mail account with App Password enabled

### Generating a Yahoo App Password

1. Go to [myaccount.yahoo.com](https://myaccount.yahoo.com)
2. Security → App Passwords
3. Create an app password for "Other App" (name it anything, e.g. "Claude MCP")
4. Copy the 16-character password (format: `xxxx-xxxx-xxxx-xxxx`)

---

## Setup

```bash
# 1. Install dependencies
cd /Users/aaronbruneau/VSCodeProjects/Yahoo_IMAP_MCP
pip3 install -r requirements.txt

# 2. Create credentials file
cp .env.example .env
# Edit .env and fill in your email and app password

# 3. Test the server starts
PYTHONPATH=src python3 -m yahoo_imap_mcp.server

# 4. Register with Claude Code (project scope via .mcp.json is automatic)
# OR register as user-scoped so it's available in all projects:
claude mcp add --transport stdio \
  --env PYTHONPATH=/Users/aaronbruneau/VSCodeProjects/Yahoo_IMAP_MCP/src \
  --scope user \
  yahoo-mail -- python3 -m yahoo_imap_mcp.server

# 5. Verify registration
claude mcp list
```

---

## Available MCP Tools

| Tool | Description |
|------|-------------|
| `list_folders` | List all IMAP folders/mailboxes |
| `list_emails` | List emails in a folder (paginated, newest first) |
| `read_email` | Read full email content by UID; marks as read |
| `search_emails` | Server-side IMAP SEARCH (sender, subject, date range, unread) |
| `move_email` | Move an email to any folder by UID |
| `delete_email` | Move an email to Trash |
| `mark_as_spam` | Move an email to Yahoo's "Bulk Mail" spam folder |
| `send_email` | Compose and send a new email |
| `reply_email` | Reply preserving In-Reply-To/References threading |
| `forward_email` | Forward with original headers quoted in body |

---

## Yahoo Server Settings

| | Value |
|-|-------|
| IMAP host | `imap.mail.yahoo.com` |
| IMAP port | `993` (SSL) |
| SMTP host | `smtp.mail.yahoo.com` |
| SMTP port | `465` (SSL) |
| Auth | App Password (not account password) |

---

## Architecture Notes

### Per-Call IMAP Connections
Yahoo drops idle IMAP sessions aggressively (~5-10 min). Opening a fresh SSL connection per tool call avoids this and is thread-safe. Latency overhead is ~100-200ms per call.

### UID-Based Operations
All IMAP ops use `conn.uid(...)` instead of sequence numbers. UIDs are stable across reconnections and EXPUNGE operations; sequence numbers shift when messages are deleted.

### Async Bridge
`imaplib` and `smtplib` are blocking. All calls run in a `ThreadPoolExecutor(max_workers=3)` via `loop.run_in_executor()` so they don't block the FastMCP async event loop.

### IMAP MOVE Extension
Tries RFC 6851 `MOVE` command first; falls back to `COPY` + `STORE \Deleted` + `EXPUNGE` if the server doesn't support it.

---

## Yahoo-Specific Gotchas

- **Spam folder is "Bulk Mail"** — not "Spam" or "Junk"
- **SMTP uses port 465** with `SMTP_SSL` — NOT port 587 with STARTTLS
- **Folder names with spaces must be quoted** in IMAP commands: `'"Bulk Mail"'`
- **App Password required** — the regular Yahoo account password will fail with auth errors
- **IMAP SEARCH date format**: `DD-Mon-YYYY` (e.g. `01-Jan-2025`)

---

## Dependencies

```
mcp>=1.0.0          # MCP Python SDK (FastMCP)
python-dotenv>=1.0.0
beautifulsoup4>=4.12.0
```

All IMAP/SMTP/email parsing uses Python standard library (`imaplib`, `smtplib`, `email`).
BeautifulSoup uses Python's built-in `html.parser` — no additional C extensions needed.

---

## Claude Code Registration (`.mcp.json`)

```json
{
  "mcpServers": {
    "yahoo-mail": {
      "type": "stdio",
      "command": "python3",
      "args": ["-m", "yahoo_imap_mcp.server"],
      "env": {
        "PYTHONPATH": "/Users/aaronbruneau/VSCodeProjects/Yahoo_IMAP_MCP/src"
      }
    }
  }
}
```
