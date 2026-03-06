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

```
[yahoo-imap] list_emails(folder='INBOX', limit=20, offset=0)
[yahoo-imap] Connecting to imap.mail.yahoo.com:993
[yahoo-imap] IMAP login successful
[yahoo-imap] Found 10432 emails in 'INBOX', fetching 20 from offset 0
[yahoo-imap] Returning 20 emails
[yahoo-imap] IMAP logout
```
