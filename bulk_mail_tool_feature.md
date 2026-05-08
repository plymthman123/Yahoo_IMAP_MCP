# Bulk Mail Analysis Tool - Feature Specification

## Context & Problem Statement

### Current Issue
When reviewing Yahoo emails through the chatbot, the conversation history accumulates all email metadata and content, causing token limit exceeded errors:
- Error: `prompt is too long: 204,865 tokens > 200,000 maximum`
- Occurs when reviewing multiple emails (10+ at a time)
- Full email content stored in chat history is inefficient
- Poor user experience with token management issues

### Root Cause
The current workflow:
1. Chatbot calls `list_emails()` → returns metadata
2. Chatbot calls `read_email()` for each email → returns full content
3. All responses accumulated in chat history
4. Each subsequent request includes entire history
5. Token limit exceeded after reviewing 20-30 emails

### Proposed Solution
Add a **bulk mail analysis tool** to the Yahoo IMAP MCP server that:
- Processes emails server-side
- Returns only categorized summaries
- Reduces token usage by 10-15x
- Provides structured, actionable data
- Enables efficient batch operations

---

## Tool Specification

### Primary Tool: `analyze_emails`

```python
@mcp.tool()
async def analyze_emails(
    folder: str = "INBOX",
    days_back: int = 2,
    limit: int = 50,
    include_read: bool = True,
    custom_categories: dict = None
) -> dict:
    """
    Bulk analyze and categorize emails with smart classification.

    Args:
        folder: Mail folder to analyze (default: "INBOX")
        days_back: How many days back to review (default: 2)
        limit: Maximum emails to analyze (default: 50, max: 200)
        include_read: Include already-read emails (default: True)
        custom_categories: Optional custom classification rules

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
            "spam": [
              {
                "uid": str,
                "from": str,
                "subject": str,
                "date": str,
                "reason": str,
                "confidence": float
              }
            ],
            "advertisements": [...],
            "important": [...],
            "keep": [...],
            "uncertain": [...]
          },
          "recommendations": {
            "auto_delete_safe": [uid_list],
            "review_needed": [uid_list],
            "suggested_rules": [rule_list]
          },
          "analysis_timestamp": str
        }
    """
```

---

## Classification Logic

### Spam Detection Rules

```python
def classify_as_spam(email_data):
    """
    Identify spam emails using heuristics.
    Returns: (is_spam: bool, reason: str, confidence: float)
    """
    subject = email_data.get('subject', '').lower()
    from_addr = email_data.get('from', '').lower()
    body_preview = email_data.get('preview', '').lower()

    spam_indicators = {
        'high_confidence': [
            'viagra', 'cialis', 'lottery', 'winner', 'prince',
            'inheritance', 'nigerian', 'bitcoin wallet',
            'verify account immediately', 'suspended account',
            'click here now', 'act now', 'limited time'
        ],
        'medium_confidence': [
            'congratulations you won', 'claim your prize',
            'increase your income', 'work from home',
            'lose weight fast', 'free money', 'cash bonus'
        ],
        'suspicious_patterns': [
            r'\$\$\$',      # Multiple dollar signs
            r'!!!+',         # Multiple exclamation marks
            r'[A-Z]{10,}',  # Excessive caps
            r'\d{1,3}% OFF', # Percentage discounts
        ]
    }

    for keyword in spam_indicators['high_confidence']:
        if keyword in subject or keyword in body_preview:
            return True, f"High-confidence spam keyword: '{keyword}'", 0.95

    for keyword in spam_indicators['medium_confidence']:
        if keyword in subject or keyword in body_preview:
            return True, f"Spam indicator: '{keyword}'", 0.75

    suspicious_domains = ['.tk', '.ml', '.ga', 'tempmail', 'guerrillamail']
    for domain in suspicious_domains:
        if domain in from_addr:
            return True, f"Suspicious domain: {domain}", 0.85

    return False, "", 0.0
```

### Advertisement Detection Rules

```python
def classify_as_advertisement(email_data):
    """
    Identify marketing/promotional emails.
    Returns: (is_ad: bool, reason: str, confidence: float)
    """
    subject = email_data.get('subject', '').lower()
    from_addr = email_data.get('from', '').lower()

    ad_keywords = [
        'unsubscribe', 'promotional', 'newsletter', 'sale',
        'discount', 'offer', 'deal', 'coupon', 'promo',
        'limited offer', 'shop now', 'buy now', 'save up to',
        'exclusive offer', 'special offer', 'today only'
    ]

    marketing_domains = [
        'marketing', 'promo', 'newsletter', 'news',
        'noreply', 'no-reply', 'deals', 'offers',
        'email', 'mail', 'info'
    ]

    if 'unsubscribe' in subject or 'unsubscribe' in from_addr:
        return True, "Contains unsubscribe link", 0.90

    keyword_count = sum(1 for kw in ad_keywords if kw in subject)
    if keyword_count >= 2:
        return True, f"Multiple ad keywords ({keyword_count})", 0.85
    elif keyword_count == 1:
        return True, "Marketing keyword present", 0.70

    for domain in marketing_domains:
        if domain in from_addr.split('@')[-1]:
            return True, f"Marketing domain: {domain}", 0.75

    return False, "", 0.0
```

### Important Email Detection

```python
def classify_as_important(email_data):
    """
    Identify important emails that should be kept.
    Returns: (is_important: bool, reason: str, confidence: float)
    """
    subject = email_data.get('subject', '').lower()
    from_addr = email_data.get('from', '').lower()

    important_keywords = {
        'financial': ['invoice', 'receipt', 'payment', 'bill', 'statement', 'transaction'],
        'security': ['security', 'password', 'verification', 'two-factor', '2fa', 'login'],
        'legal': ['legal', 'contract', 'agreement', 'terms', 'policy update'],
        'shipping': ['shipped', 'delivery', 'tracking', 'order confirmed'],
        'personal': ['appointment', 'reservation', 'booking', 'confirmation']
    }

    important_domains = [
        'paypal', 'stripe', 'square', 'venmo',
        'bank', 'chase', 'wellsfargo', 'bofa',
        'amazon', 'ebay', 'usps', 'fedex', 'ups',
        'irs.gov', 'gov', 'edu'
    ]

    for category, keywords in important_keywords.items():
        for keyword in keywords:
            if keyword in subject:
                return True, f"Important {category} keyword: '{keyword}'", 0.90

    for domain in important_domains:
        if domain in from_addr:
            return True, f"Important sender: {domain}", 0.85

    return False, "", 0.0
```

### Master Classification Function

```python
def classify_email(email_data):
    """
    Classify an email into one category with confidence score.
    Returns: (category: str, reason: str, confidence: float)
    """
    # Priority order: spam > important > advertisement > keep

    is_spam, reason, conf = classify_as_spam(email_data)
    if is_spam and conf >= 0.75:
        return 'spam', reason, conf

    is_important, reason, conf = classify_as_important(email_data)
    if is_important and conf >= 0.80:
        return 'important', reason, conf

    is_ad, reason, conf = classify_as_advertisement(email_data)
    if is_ad and conf >= 0.70:
        return 'advertisements', reason, conf

    if (is_spam and conf < 0.75) or (is_ad and conf < 0.70):
        return 'uncertain', f"Low confidence: {reason}", conf

    return 'keep', 'Personal or legitimate email', 0.60
```

---

## Implementation Options

### Option 1: Pure Heuristic (Recommended First)
- Fast (no API calls), no token usage, deterministic
- May miss nuanced cases, requires rule maintenance
- **Best for:** Initial implementation, high-volume processing

### Option 2: Hybrid Approach
- Apply heuristics first, use AI only for "uncertain" emails
- Best balance of accuracy and cost
- **Best for:** Production use with accuracy requirements

### Option 3: Full LLM Classification
- Send email metadata to Claude for classification
- Highest accuracy but slower and costs tokens
- **Best for:** Small batches, high-value emails

---

## Additional Useful Tools

### `bulk_delete_by_category`

```python
@mcp.tool()
async def bulk_delete_by_category(
    analysis_id: str,
    categories: list[str],
    dry_run: bool = True
) -> dict:
    """
    Delete emails from specified categories after review.

    Args:
        analysis_id: ID from previous analyze_emails result
        categories: List of categories to delete (e.g., ["spam", "advertisements"])
        dry_run: If True, show what would be deleted without deleting

    Returns:
        {
          "dry_run": bool,
          "emails_to_delete": int,
          "emails_deleted": int,
          "categories_processed": [str],
          "uids_affected": [str]
        }
    """
```

### `bulk_move_by_sender`

```python
@mcp.tool()
async def bulk_move_by_sender(
    sender_email: str,
    dest_folder: str,
    days_back: int = 30,
    create_rule: bool = False
) -> dict:
    """
    Move all emails from a specific sender to a folder.

    Args:
        sender_email: Email address of sender
        dest_folder: Destination folder
        days_back: How far back to search
        create_rule: Create a filter rule for future emails
    """
```

### `get_sender_statistics`

```python
@mcp.tool()
async def get_sender_statistics(
    folder: str = "INBOX",
    days_back: int = 30,
    top_n: int = 20
) -> dict:
    """
    Analyze who sends you the most emails.

    Returns top senders with count, percentage, avg per day, suggested action.
    """
```

### `auto_unsubscribe`

```python
@mcp.tool()
async def auto_unsubscribe(
    sender_email: str = None,
    category: str = "advertisements",
    dry_run: bool = True
) -> dict:
    """
    Find and process unsubscribe links in emails.
    dry_run=True shows links without clicking them.
    """
```

### `create_smart_filters`

```python
@mcp.tool()
async def create_smart_filters(
    analysis_id: str,
    auto_apply: bool = False
) -> dict:
    """
    Generate filter rules based on email analysis patterns.
    """
```

---

## Token Efficiency Comparison

### Current Approach (Inefficient)
```
list_emails(limit=10)   →  ~2K tokens
read_email(uid=1..10)   →  ~5K tokens each
Total for 10 emails:    ~52K tokens
Total for 50 emails:    ~260K tokens  ← exceeds 200K limit
```

### With `analyze_emails` (Efficient)
```
analyze_emails(days_back=2, limit=50)  →  ~15K tokens
Returns structured summary of 50 emails
15x improvement in token usage
```

---

## User Experience Flow

### Before (Current)
```
User: "Review my last 2 days of email, 10 at a time"
Bot:  [reads 10 emails → 50K tokens]
User: "Show me 10 more"
Bot:  [reads 10 more → 100K tokens]
User: "Show me 10 more"
Bot:  [reads 10 more → 150K tokens]
User: "Show me 10 more"
Bot:  ERROR - Token limit exceeded (204K tokens)
```

### After (With Bulk Tool)
```
User: "Review my last 2 days of email"
Bot:  [calls analyze_emails(days_back=2, limit=50) → 15K tokens]
      "Found 47 emails:
        - 3 spam (safe to delete)
        - 15 advertisements (unsubscribe candidates)
        - 8 important (keep)
        - 21 personal (keep)
      
      Would you like to:
      1. Delete spam?
      2. Unsubscribe from ad senders?
      3. Review uncertain emails?
      4. See details for any category?"
```

---

## Integration with v11chatbot.py

### Detecting Bulk Email Tasks

```python
def is_bulk_email_task(message: str) -> bool:
    """Detect when user wants bulk email review."""
    keywords = [
        'review my email', 'check my inbox', 'last 2 days',
        'analyze my mail', 'go through my email', 'bulk review'
    ]
    return any(kw in message.lower() for kw in keywords)

# In chat function, before calling claude_api:
if is_bulk_email_task(message):
    # Use analyze_emails tool instead of individual reads
    # Return compact summary instead of full content
    pass
```

### History Management for Email Tasks

```python
def truncate_history_for_email_task(history, max_messages=10):
    """Keep history lean during bulk email operations."""
    if len(history) > max_messages:
        return history[:2] + history[-(max_messages-2):]
    return history
```

---

## Files to Modify

1. **`server.py`** (or equivalent MCP server file) — Add new `@mcp.tool()` functions
2. **`v11chatbot.py`** — Add `is_bulk_email_task()` detection and history management
3. **`config.py`** — Add classification rule configuration

---

## Testing Plan

1. Run `analyze_emails(days_back=2, limit=10)` on known inbox
2. Verify spam/ad detection accuracy manually
3. Test `bulk_delete_by_category` with `dry_run=True`
4. Confirm token usage stays under 20K for 50-email analysis
5. Integration test: full review workflow from chatbot

---

## Future Enhancements

- Machine learning model trained on user's past decisions
- Calendar integration (flag emails with dates/events)
- Contact book integration (prioritize known senders)
- Scheduled daily digest of analyzed emails
- Export analysis results to CSV/JSON
