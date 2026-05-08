# Debugging the Yahoo IMAP MCP Server

## Enabling Debug Logs

Debug logging is off by default. Set the `YAHOO_MCP_DEBUG` environment variable to enable it.

Logs are written to **stderr** (not stdout) to avoid interfering with the MCP stdio protocol.

---

## Option 1: Run manually in a terminal

Start the server in a terminal window with debug logging enabled:

```bash
YAHOO_MCP_DEBUG=1 PYTHONPATH=src python -m yahoo_imap_mcp.server
```

`[yahoo-imap]` and `[yahoo-smtp]` lines will print as Claude calls the tools.

> **Note:** Use `python` (Anaconda) not `python3` (resolves to macOS system Python 3.9).

---

## Option 2: Enable in Claude desktop app

Add `YAHOO_MCP_DEBUG` to the `env` block in `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
"env": {
  "PYTHONPATH": "/Users/aaronbruneau/VSCodeProjects/Yahoo_IMAP_MCP/src",
  "YAHOO_MCP_DEBUG": "1"
}
```

Restart the Claude app after editing. Remove `YAHOO_MCP_DEBUG` when done.

---

## Example log output

Single-email operation:
```
[yahoo-imap] list_emails(folder='INBOX', limit=20, offset=0)
[yahoo-imap] Connecting to imap.mail.yahoo.com:993
[yahoo-imap] IMAP login successful
[yahoo-imap] Found 10432 emails in 'INBOX', fetching 20 from offset 0
[yahoo-imap] Returning 20 emails
[yahoo-imap] IMAP logout
```

Bulk analysis (`analyze_emails`):
```
[yahoo-imap] fetch_envelopes_since(folder='INBOX', since='06-May-2026', limit=50)
[yahoo-imap] Connecting to imap.mail.yahoo.com:993
[yahoo-imap] IMAP login successful
[yahoo-imap] fetch_envelopes_since returning 47 of 47
[yahoo-imap] IMAP logout
```

Bulk delete (`bulk_delete_by_category` with `dry_run=False`):
```
[yahoo-imap] move_emails_bulk(count=12, from='INBOX', to='Trash')
[yahoo-imap] Connecting to imap.mail.yahoo.com:993
[yahoo-imap] IMAP login successful
[yahoo-imap] IMAP logout
```

---

## Token Usage Comparison

| Approach | Tokens for 50 emails |
|----------|----------------------|
| `read_email` × 50 (old) | ~260,000 — **exceeds 200K limit** |
| `analyze_emails(limit=50)` | ~15,000 |

`analyze_emails` fetches `BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)]`, not full RFC822 bodies. The original IMAP ENVELOPE approach was replaced because Yahoo's server returns nested address structures the regex parser couldn't extract reliably — subjects and senders came back empty. Classification happens locally in `email_classifier.py` with no additional API calls.
