import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the project root (two levels up from this file)
_project_root = Path(__file__).parent.parent.parent.parent
load_dotenv(_project_root / ".env")

YAHOO_EMAIL: str = os.environ["YAHOO_EMAIL"]
YAHOO_APP_PASSWORD: str = os.environ["YAHOO_APP_PASSWORD"]

# Yahoo server constants
IMAP_HOST = "imap.mail.yahoo.com"
IMAP_PORT = 993
SMTP_HOST = "smtp.mail.yahoo.com"
SMTP_PORT = 465  # SMTP_SSL (not STARTTLS)

# Operational defaults
DEFAULT_LIMIT = 20
MAX_LIMIT = 100

# Yahoo folder names
TRASH_FOLDER = "Trash"
SPAM_FOLDER = "Bulk Mail"  # Yahoo calls it "Bulk Mail", not "Spam"
SENT_FOLDER = "Sent"
