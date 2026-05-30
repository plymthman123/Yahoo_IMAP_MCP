# Yahoo IMAP MCP Server — Project Summary

## What Was Built

A local Python MCP (Model Context Protocol) server that gives Claude Code full access to a Yahoo Mail account via IMAP and SMTP. The server runs locally on your machine, keeping credentials private, and exposes 14 email tools that Claude Code can call conversationally — including bulk mail analysis that classifies an entire inbox slice without hitting Claude's token limits. A persistent user-preferences layer allows classifications to improve automatically after each managed session in the AI Chat Assistant.

---

## Files Created

```
Yahoo_IMAP_MCP/
├── Yahoo_IMAP_MCP.md              # Full architecture docs and setup guide
├── project_summary.md             # This file
├── bulk_mail_tool_feature.md      # Design spec for the bulk analysis feature
├── .mcp.json                      # Project-scoped Claude Code MCP registration
├── .env.example                   # Credentials template
├── .gitignore                     # Excludes .env and Python cache files
├── requirements.txt               # Python dependencies
└── src/
    └── yahoo_imap_mcp/
        ├── __init__.py
        ├── server.py              # FastMCP entry point + all 14 @tool decorators
        ├── imap_client.py         # All IMAP read/move/delete/bulk operations
        ├── email_classifier.py    # Heuristic spam/ad/important classifier (user-prefs aware)
        ├── user_prefs.py          # User preference layer — load/save/merge prefs
        ├── smtp_client.py         # SMTP send via Yahoo port 465 SSL
        ├── email_parser.py        # RFC822 bytes → structured Python dict
        ├── email_builder.py       # MIME message construction (send/reply/forward)
        └── config.py              # .env loader + Yahoo server constants
```

---

## Tools Exposed to Claude Code

### Single-email tools (original 10)

| Tool | Description |
|------|-------------|
| `list_folders` | List all IMAP folders/mailboxes |
| `list_emails` | List emails in a folder, newest first, with pagination |
| `read_email` | Read full email content by UID; marks as read |
| `search_emails` | Server-side IMAP search by sender, subject, date, unread status |
| `move_email` | Move an email to any folder |
| `delete_email` | Move an email to Trash (soft delete) |
| `mark_as_spam` | Move an email to Yahoo's "Bulk Mail" spam folder |
| `send_email` | Compose and send a new email |
| `reply_email` | Reply with proper threading headers (In-Reply-To, References) |
| `forward_email` | Forward with original message quoted in body |

### Bulk analysis tools (added)

| Tool | Description |
|------|-------------|
| `analyze_emails` | Classify up to 200 emails by date range into spam / ads / important / keep / uncertain — envelope-only, no full body fetch. Applies user preferences before heuristics. |
| `bulk_delete_by_category` | Move a list of UIDs to Trash; `dry_run=True` by default for safety |
| `bulk_move_by_sender` | Find all emails matching a sender pattern and move them to a folder |
| `get_sender_statistics` | Rank senders by volume over N days with suggested actions |

---

## Key Technical Decisions

- **Per-call IMAP connections** — Yahoo drops idle sessions aggressively; a fresh SSL connect/login/logout per tool call avoids this entirely.
- **UID-based IMAP operations** — UIDs are stable across reconnections; sequence numbers shift on deletions and are unreliable.
- **Async bridge** — `imaplib`/`smtplib` are blocking. All calls run in a `ThreadPoolExecutor` via `run_in_executor()` so the FastMCP async event loop is never blocked.
- **IMAP MOVE extension** — Tries RFC 6851 `MOVE` first; falls back to `COPY` + `STORE \Deleted` + `EXPUNGE` for compatibility.
- **html.parser** — BeautifulSoup uses Python's built-in HTML parser for HTML-to-text conversion; no C extension required.
- **Yahoo SMTP on port 465** — Uses `SMTP_SSL` (full SSL from handshake), not `STARTTLS` on port 587, which is required for Yahoo.
- **Header-fields bulk analysis** — `analyze_emails` fetches `BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)]` rather than full RFC822 bodies or the IMAP ENVELOPE command. Python's `email` library parses the header lines and RFC2047-encoded subjects are decoded via `_decode_header_value()`. The original ENVELOPE regex approach produced empty subjects/senders against Yahoo's server (nested address structures), causing everything to classify as "keep". Classifying 50 emails costs ~15K tokens vs ~260K+ when reading each email individually.
- **Heuristic classifier** — `email_classifier.py` uses keyword/domain/pattern matching with confidence scores. Classification priority order: **user preferences → high-confidence spam (≥0.85) → important → advertisements → low-confidence spam (→ uncertain) → keep**. Key design points:
  - `_TRANSACTIONAL_DOMAINS` (financial institutions, carriers, healthcare, utilities) are spam-exempt and auto-classified as important regardless of subject.
  - `_TRUSTED_MIXED_DOMAINS` (Amazon, Apple, Google, etc.) are spam-exempt but require a subject keyword to be classified as important, since they also send promotional mail.
  - Suspicious TLD alone (e.g. `.ga`) returns confidence 0.50 → routes to `uncertain` instead of `spam`. Requires TLD + spammy subject to reach 0.85 spam threshold.
  - TLD check uses `domain.endswith(tld)` (not substring) to prevent `.ga` from matching inside domains like `gavinnewsom.com`.
  - Sender signals have three tiers: *strong* (`marketing`, `promotions`, `newsletter`, `deals`, `offers` local parts — sufficient alone), *weak* (`noreply`, `no-reply`, `donotreply`, `news`, `updates` — needs a subject signal), and *generic commercial* (`info`, `hello`, `team`, `contact`, `hi` — exact local-part match from a non-trusted domain; also needs a subject signal). Display name containing `!` counts as a weak sender signal.
  - Ad subject keywords split into *strong* (one match sufficient: `% off`, `coupon`, `flash sale`, `debt relief`, `debt consolidation`, `pre-approved`, `free trial`, etc.) and *weak* (need combination or sender signal: `sale`, `deal`, `newsletter`, `just for you`, `act now`, `limited time`, `top picks`, etc.).
  - Display-name impersonation detection: `"PayPal <info@scammer.tk>"` → spam (0.95).
  - Unicode/non-ASCII in the From field → weak spam signal (0.60) → routes to `uncertain`.
  - No external API calls required.
- **User preferences layer** — `user_prefs.py` manages a JSON file (`~/.yahoo_mcp_user_prefs.json`, overridable via `YAHOO_MCP_USER_PREFS_PATH`) that stores per-domain override rules. `classify_email()` checks this before any heuristic runs, so user preferences always win. The file is loaded once per `analyze_emails` call. See the **User Preferences** section below for the full rule schema.
- **Action-confirmation logging** — `server.py` logs every write operation to the same syslog socket as `imap_client`, under the `yahoo-mcp-actions` identity. Every tool that mutates email state emits a structured log line after success (e.g. `ACTION bulk_delete_by_category: moved 47 of 47 emails from 'INBOX' to 'Trash'`). Dry-run calls are also logged so LLM previews are distinguishable from actual executions.
- **Bulk move in single session** — `move_emails_bulk` passes a comma-separated UID set to a single IMAP MOVE/COPY command, avoiding one connection per email.

---

## User Preferences

The classifier supports a persistent user-preferences layer that overrides heuristics on a per-domain basis. Rules are populated automatically by the AI Chat Assistant's post-session learner after each Yahoo Mail session, or can be edited manually.

### File Location

`$YAHOO_MCP_USER_PREFS_PATH` (env var) or `~/.yahoo_mcp_user_prefs.json`

### Schema

```json
{
  "always_spam":      ["spammer.tk", "scam-domain.com"],
  "always_delete":    ["tiktok.com"],
  "always_keep":      ["lightstream.com"],
  "always_important": ["healthequity.com"],
  "reclassify": [
    {"domain": "llbean.com", "from": "important", "to": "advertisements"}
  ],
  "last_updated": "2026-05-30",
  "session_count": 4
}
```

### Rule Semantics

| Key | Effect on `classify_email()` | Confidence |
|---|---|---|
| `always_spam` | Returns `"spam"` | 1.0 |
| `always_delete` | Returns `"spam"` (routed to trash) | 1.0 |
| `always_important` | Returns `"important"` | 1.0 |
| `always_keep` | Returns `"keep"` (never spam or ads) | 1.0 |
| `reclassify` | Returns the `to` category for the matching domain | 0.95 |

### How Rules Get Updated

The AI Chat Assistant's Yahoo Mail Agent includes a post-session learner (`agents/yahoo_email/learner.py`) that fires automatically when the user clicks **Done / Return to Chat**. It:
1. Parses the session's full API history to identify which emails were deleted/spammed/moved and their senders
2. Sends a compact action summary to the OpenAI model with instructions to extract high-confidence domain rules
3. Merges proposed additions into the prefs file (existing rules are never removed; the file only grows)

The learner will not propose rules for transactional senders (banks, carriers, PayPal) or major platforms (Amazon, Google, Apple, Microsoft).

---

## Next Steps

### 1. Install Dependencies

```bash
cd /Users/aaronbruneau/VSCodeProjects/Yahoo_IMAP_MCP
pip3 install -r requirements.txt
```

### 2. Generate a Yahoo App Password

Regular Yahoo account passwords will not work with IMAP/SMTP. You must use an App Password:

1. Go to [myaccount.yahoo.com](https://myaccount.yahoo.com)
2. Navigate to **Security → App Passwords**
3. Click **Generate app password**, name it anything (e.g. "Claude MCP")
4. Copy the 16-character password (format: `xxxx-xxxx-xxxx-xxxx`)

### 3. Create Your Credentials File

```bash
cp .env.example .env
```

Edit `.env` and fill in your details:

```ini
YAHOO_EMAIL=yourname@yahoo.com
YAHOO_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
```

Optionally, set a custom path for the user preferences file:

```ini
YAHOO_MCP_USER_PREFS_PATH=/path/to/your/user_prefs.json
```

### 4. Test the Server

```bash
PYTHONPATH=src python3 -m yahoo_imap_mcp.server
```

The server will start silently and wait on stdin — this is correct behaviour for an MCP stdio transport. Press `Ctrl+C` to stop.

### 5. Register with Claude Code

The `.mcp.json` file in the project root registers the server automatically for project-scoped use (when you `cd` into this directory). To make it available across all Claude Code projects, register it as user-scoped:

```bash
claude mcp add --transport stdio \
  --env PYTHONPATH=/Users/aaronbruneau/VSCodeProjects/Yahoo_IMAP_MCP/src \
  --scope user \
  yahoo-mail -- python3 -m yahoo_imap_mcp.server
```

Verify registration:

```bash
claude mcp list
```

### 6. Test with Claude Code

Once registered, open a Claude Code session and try:

- *"List my email folders"*
- *"Show me my last 10 unread emails"*
- *"Search for emails from john@example.com in the last 30 days"*
- *"Read email UID 12345"*
- *"Move that email to my Work folder"*
- *"Send an email to jane@example.com with subject 'Hello' and body 'Testing the MCP server'"*

**Bulk analysis flow:**

- *"Analyze my inbox for the last 2 days"* → calls `analyze_emails` (applies user prefs first, then heuristics)
- *"Delete all the spam ones"* → calls `bulk_delete_by_category` with `dry_run=True` first
- *"Yes, go ahead and delete them"* → confirms, calls with `dry_run=False`
- *"Who sends me the most email over the last 30 days?"* → calls `get_sender_statistics`
- *"Move everything from deals@store.com to my Promotions folder"* → calls `bulk_move_by_sender`

---

## Troubleshooting

| Problem | Likely Cause | Fix |
|---------|-------------|-----|
| `SMTPAuthenticationError` | Wrong password or using account password | Use an App Password (see step 2) |
| `IMAP LOGIN failed` | Same as above | Same fix |
| `KeyError: YAHOO_EMAIL` | `.env` file missing or not in project root | Create `.env` from `.env.example` |
| Folder not found | Wrong folder name | Run `list_folders` first to see exact names |
| `ModuleNotFoundError: mcp` | Dependencies not installed | Run `pip3 install -r requirements.txt` |
| Server not showing in `claude mcp list` | Not registered | Run the `claude mcp add` command in step 5 |
| User prefs not applying | File path mismatch | Check `YAHOO_MCP_USER_PREFS_PATH` or confirm `~/.yahoo_mcp_user_prefs.json` exists |
