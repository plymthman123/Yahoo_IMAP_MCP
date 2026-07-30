"""
User preference layer for email classification.
Loaded from a JSON file and checked before the heuristic rules run.

File location: $YAHOO_MCP_USER_PREFS_PATH or ~/.yahoo_mcp_user_prefs.json
"""
import json
import logging
import os
from datetime import date

logger = logging.getLogger("yahoo-mcp.user_prefs")

_DEFAULT_PREFS_PATH = os.path.join(os.path.expanduser("~"), ".yahoo_mcp_user_prefs.json")
PREFS_PATH = os.environ.get("YAHOO_MCP_USER_PREFS_PATH", _DEFAULT_PREFS_PATH)

_EMPTY: dict = {
    "always_spam":      [],   # domains always classified as spam
    "always_delete":    [],   # domains always moved to trash (alias for always_spam at classifier level)
    "always_keep":      [],   # domains always kept (never spam/ads)
    "always_important": [],   # domains always marked important
    "suggested_delete": [],   # domains surfaced in a separate review bucket (learner-flagged misclassifications)
    "reclassify":       [],   # [{"domain": str, "from": str, "to": str}]
    "last_updated":     None,
    "session_count":    0,
}


def load_prefs(path: str = PREFS_PATH) -> dict:
    try:
        with open(path) as fh:
            data = json.load(fh)
        merged = dict(_EMPTY)
        merged.update(data)
        return merged
    except FileNotFoundError:
        return dict(_EMPTY)
    except Exception as exc:
        logger.warning("Failed to load user prefs from %s: %s", path, exc)
        return dict(_EMPTY)


def save_prefs(prefs: dict, path: str = PREFS_PATH) -> None:
    prefs["last_updated"] = date.today().isoformat()
    try:
        with open(path, "w") as fh:
            json.dump(prefs, fh, indent=2)
    except Exception as exc:
        logger.error("Failed to save user prefs to %s: %s", path, exc)


def merge_proposed(current: dict, proposed: dict) -> dict:
    """Merge proposed additions into current prefs (no duplicates, lists only grow)."""
    merged = dict(current)
    for key in ("always_spam", "always_delete", "always_keep", "always_important", "suggested_delete"):
        if key in proposed:
            existing = set(merged.get(key, []))
            existing.update(proposed[key])
            merged[key] = sorted(existing)
    if "reclassify" in proposed:
        existing_rc = list(merged.get("reclassify", []))
        existing_domains = {r["domain"] for r in existing_rc}
        supported_categories = {"spam", "advertisements", "important", "keep", "uncertain"}
        category_aliases = {
            "transactional": "important",
            "newsletters": "advertisements",
            "politics": "keep",
        }
        for rule in proposed["reclassify"]:
            if not isinstance(rule, dict) or not rule.get("domain"):
                continue
            target = category_aliases.get(rule.get("to"), rule.get("to"))
            if target not in supported_categories:
                logger.warning("Ignoring unsupported reclassification target: %r", rule.get("to"))
                continue
            normalized_rule = dict(rule)
            normalized_rule["to"] = target
            if normalized_rule["domain"] not in existing_domains:
                existing_rc.append(normalized_rule)
        merged["reclassify"] = existing_rc
    return merged
