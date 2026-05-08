"""
Heuristic email classification for bulk mail analysis.
Operates on envelope-level data (subject, from) — no full body required.
"""


def classify_as_spam(email_data: dict) -> tuple[bool, str, float]:
    subject = (email_data.get("subject") or "").lower()
    from_addr = (email_data.get("from") or "").lower()

    high_confidence = [
        "viagra", "cialis", "lottery", "you've won", "you have won",
        "claim your prize", "inheritance", "nigerian prince",
        "bitcoin wallet", "verify account immediately",
        "account has been suspended", "urgent action required",
    ]
    medium_confidence = [
        "congratulations you", "increase your income",
        "work from home", "lose weight fast", "free money",
        "cash bonus", "make money fast", "earn from home",
    ]
    suspicious_tlds = [".tk", ".ml", ".ga", ".cf", ".gq"]
    suspicious_substrings = ["tempmail", "guerrillamail", "mailinator"]

    for kw in high_confidence:
        if kw in subject or kw in from_addr:
            return True, f"Spam keyword: '{kw}'", 0.95

    for kw in medium_confidence:
        if kw in subject:
            return True, f"Spam indicator: '{kw}'", 0.75

    for tld in suspicious_tlds:
        if tld in from_addr:
            return True, f"Suspicious TLD: {tld}", 0.85

    for substr in suspicious_substrings:
        if substr in from_addr:
            return True, f"Disposable email service: {substr}", 0.90

    return False, "", 0.0


def classify_as_advertisement(email_data: dict) -> tuple[bool, str, float]:
    subject = (email_data.get("subject") or "").lower()
    from_addr = (email_data.get("from") or "").lower()

    unsubscribe_keywords = ["unsubscribe", "opt out", "opt-out", "manage preferences"]
    ad_keywords = [
        "% off", "% discount", "sale", "coupon", "promo code", "promo ",
        "deal", "special offer", "limited time", "exclusive offer",
        "shop now", "buy now", "order now", "flash sale",
        "newsletter", "promotional", "weekly digest", "daily digest",
        "black friday", "cyber monday",
    ]
    marketing_local_parts = [
        "noreply", "no-reply", "donotreply", "do-not-reply",
        "marketing", "promotions", "promo", "newsletter",
        "deals", "offers", "news", "updates",
    ]

    for kw in unsubscribe_keywords:
        if kw in subject or kw in from_addr:
            return True, "Unsubscribe/opt-out indicator", 0.92

    hits = [kw for kw in ad_keywords if kw in subject]
    if len(hits) >= 2:
        return True, f"Multiple ad keywords: {hits[:2]}", 0.88
    if len(hits) == 1:
        return True, f"Ad keyword: '{hits[0]}'", 0.72

    local_part = from_addr.split("@")[0] if "@" in from_addr else from_addr
    for part in marketing_local_parts:
        if part in local_part:
            return True, f"Marketing sender pattern: '{part}'", 0.80

    return False, "", 0.0


def classify_as_important(email_data: dict) -> tuple[bool, str, float]:
    subject = (email_data.get("subject") or "").lower()
    from_addr = (email_data.get("from") or "").lower()

    important_keywords = {
        "financial": [
            "invoice", "receipt", "payment", "bill", "statement",
            "transaction", "charge", "refund", "tax", "w-2", "1099",
        ],
        "security": [
            "security alert", "password reset", "verification code",
            "two-factor", "2fa", "login attempt", "sign-in attempt",
            "unusual activity", "account access",
        ],
        "legal": [
            "legal notice", "court", "contract", "agreement",
            "terms of service update", "privacy policy update",
        ],
        "shipping": [
            "shipped", "out for delivery", "delivery attempted",
            "tracking number", "order confirmed", "order shipped",
            "package delivered",
        ],
        "appointment": [
            "appointment", "reservation confirmed", "booking confirmation",
            "your reservation", "reminder:", "upcoming appointment",
        ],
    }

    trusted_domains = [
        "paypal.com", "stripe.com", "square.com", "venmo.com",
        "chase.com", "wellsfargo.com", "bankofamerica.com", "citi.com",
        "amazon.com", "ebay.com",
        "usps.com", "fedex.com", "ups.com", "dhl.com",
        ".irs.gov", ".gov", ".edu",
    ]

    for category, keywords in important_keywords.items():
        for kw in keywords:
            if kw in subject:
                return True, f"Important ({category}): '{kw}'", 0.90

    for domain in trusted_domains:
        if domain in from_addr:
            return True, f"Trusted sender domain: {domain}", 0.85

    return False, "", 0.0


def classify_email(email_data: dict) -> tuple[str, str, float]:
    """
    Classify an email into one of: spam | advertisements | important | keep | uncertain.
    Returns (category, reason, confidence).
    Priority order: spam → important → advertisements → keep.
    """
    is_spam, reason, conf = classify_as_spam(email_data)
    if is_spam and conf >= 0.75:
        return "spam", reason, conf

    is_important, reason, conf = classify_as_important(email_data)
    if is_important and conf >= 0.80:
        return "important", reason, conf

    is_ad, reason, conf = classify_as_advertisement(email_data)
    if is_ad and conf >= 0.72:
        return "advertisements", reason, conf

    if is_spam:
        return "uncertain", f"Possible spam (low confidence): {reason}", conf

    return "keep", "No strong signals detected", 0.60
