"""
Heuristic email classification for bulk mail analysis.
Operates on envelope-level data (subject, from) — no full body required.
"""


def _extract_domain(addr: str) -> str:
    """Extract the sending domain from a raw From value."""
    if "<" in addr:
        addr = addr.split("<")[-1].rstrip(">")
    return addr.split("@")[-1].strip().lower() if "@" in addr else addr.strip().lower()


def _extract_local(addr: str) -> str:
    """Extract the local part (before @) from a raw From value."""
    if "<" in addr:
        addr = addr.split("<")[-1].rstrip(">")
    return addr.split("@")[0].strip().lower() if "@" in addr else ""


def _domain_in_set(domain: str, domain_set: frozenset) -> bool:
    return domain in domain_set or any(domain.endswith("." + d) for d in domain_set)


# Financial institutions, carriers, healthcare, utilities — only send transactional mail.
# Any email from these domains is exempt from spam and auto-classified as important.
_TRANSACTIONAL_DOMAINS = frozenset([
    "paypal.com", "stripe.com", "square.com", "venmo.com", "zelle.com",
    "chase.com", "wellsfargo.com", "bankofamerica.com", "citi.com",
    "usbank.com", "tdbank.com", "capitalone.com",
    "usps.com", "fedex.com", "ups.com", "dhl.com",
    "etrade.com", "fidelity.com", "schwab.com", "vanguard.com", "tdameritrade.com",
    "healthequity.com", "optum.com", "cigna.com", "aetna.com", "unitedhealthcare.com",
    "intuit.com", "quickbooks.com", "turbotax.com",
    "myq.com", "chamberlain.com",
])

# Trusted retail/tech brands — exempt from spam, but also send promotional mail,
# so important classification requires a matching subject keyword.
_TRUSTED_MIXED_DOMAINS = frozenset([
    "amazon.com", "ebay.com", "apple.com", "google.com", "microsoft.com",
])


def _is_spam_exempt(domain: str) -> bool:
    return (
        _domain_in_set(domain, _TRANSACTIONAL_DOMAINS)
        or _domain_in_set(domain, _TRUSTED_MIXED_DOMAINS)
        or domain.endswith(".gov")
        or domain.endswith(".edu")
    )


def classify_as_spam(email_data: dict) -> tuple[bool, str, float]:
    raw_subject = email_data.get("subject") or ""
    raw_from = email_data.get("from") or ""
    subject = raw_subject.lower()
    from_addr = raw_from.lower()

    if _is_spam_exempt(_extract_domain(from_addr)):
        return False, "", 0.0

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
    # Use endswith on the extracted domain — avoids ".ga" matching inside "gavinnewsom.com"
    suspicious_tlds = [".tk", ".ml", ".ga", ".cf", ".gq"]
    disposable_services = ["tempmail", "guerrillamail", "mailinator"]
    trusted_display_names = ["paypal", "apple", "amazon", "microsoft", "google", "irs"]

    # Display-name impersonation: "PayPal <info@scammer.tk>"
    if "<" in from_addr:
        display_name = from_addr.split("<")[0].strip()
        actual_domain = _extract_domain(from_addr)
        for name in trusted_display_names:
            if name in display_name and name not in actual_domain:
                return True, f"Display name impersonation: '{name}'", 0.95

    for kw in high_confidence:
        if kw in subject or kw in from_addr:
            return True, f"Spam keyword: '{kw}'", 0.95

    for kw in medium_confidence:
        if kw in subject:
            return True, f"Spam indicator: '{kw}'", 0.75

    for svc in disposable_services:
        if svc in from_addr:
            return True, f"Disposable email service: {svc}", 0.90

    # Suspicious TLD: only spam when combined with a spammy subject.
    # Standalone suspicious TLD → low confidence, routes to "uncertain" in classify_email.
    domain = _extract_domain(from_addr)
    suspicious_tld = next((tld for tld in suspicious_tlds if domain.endswith(tld)), None)
    if suspicious_tld:
        if any(kw in subject for kw in medium_confidence + high_confidence):
            return True, f"Suspicious TLD + spam content: {suspicious_tld}", 0.85
        return True, f"Suspicious TLD: {suspicious_tld}", 0.50

    # Non-ASCII anywhere in the From field (display name or address) suggests obfuscation
    try:
        raw_from.encode("ascii")
    except UnicodeEncodeError:
        return True, "Unicode obfuscation in sender", 0.60

    return False, "", 0.0


def classify_as_advertisement(email_data: dict) -> tuple[bool, str, float]:
    raw_from = email_data.get("from") or ""
    subject = (email_data.get("subject") or "").lower()
    from_addr = raw_from.lower()

    unsubscribe_keywords = ["unsubscribe", "opt out", "opt-out", "manage preferences"]

    # Strong promotional subject keywords — one match is sufficient
    strong_ad_keywords = [
        "% off", "% discount", "coupon", "promo code",
        "flash sale", "exclusive offer", "limited time offer",
        "shop now", "buy now", "order now",
        "black friday", "cyber monday",
        # Financial marketing (not actual financial transactions)
        "debt relief", "debt consolidation", "pre-approved", "free trial",
        "you've been selected", "you qualify for", "limited offer",
        "refinance your", "lower your rate", "reduce your payment",
    ]
    # Weaker keywords — require combination or a strong sender to confirm
    weak_ad_keywords = [
        "sale", "deal", "promo ", "special offer",
        "newsletter", "weekly digest", "daily digest", "promotional",
        # Content marketing / engagement patterns
        "just for you", "made for you", "picked for you",
        "don't miss", "act now", "limited time", "sign up",
        "introducing", "round-up", "roundup", "learn more",
        "you qualify", "find out why", "top picks", "this week's",
    ]

    # High-confidence marketing sender local parts — sufficient signal on their own
    marketing_local_parts = [
        "marketing", "promotions", "newsletter", "deals", "offers",
    ]
    # Neutral/notification patterns — insufficient alone; only strengthen a weak subject signal
    weak_sender_parts = [
        "noreply", "no-reply", "donotreply", "do-not-reply",
        "news", "updates",
    ]
    # Generic commercial local parts — weaker still; only upgrade a weak subject hit
    # Exact match only to avoid catching "information@bank.com" etc.
    generic_commercial_parts = {"info", "hello", "team", "contact", "hi"}

    for kw in unsubscribe_keywords:
        if kw in subject or kw in from_addr:
            return True, "Unsubscribe/opt-out indicator", 0.92

    strong_hits = [kw for kw in strong_ad_keywords if kw in subject]
    if strong_hits:
        return True, f"Promotional keyword: '{strong_hits[0]}'", 0.88

    weak_hits = [kw for kw in weak_ad_keywords if kw in subject]
    local_part = _extract_local(from_addr)
    domain = _extract_domain(from_addr)

    strong_sender = any(part in local_part for part in marketing_local_parts)
    weak_sender = any(part in local_part for part in weak_sender_parts)

    # Generic commercial sender: exact local-part match from a non-trusted domain
    is_generic_commercial = (
        local_part in generic_commercial_parts
        and not _is_spam_exempt(domain)
    )

    # Exclamation mark in the display name is a marketing signal (e.g. "EntertainmentNow!")
    display_name_exclamation = (
        "<" in raw_from and "!" in raw_from.split("<")[0]
    )

    # Combine weak sender signals
    any_weak_sender = weak_sender or is_generic_commercial or display_name_exclamation

    if strong_sender:
        if weak_hits:
            return True, f"Marketing sender + promotional keyword: '{weak_hits[0]}'", 0.85
        return True, "Marketing sender pattern", 0.75

    if len(weak_hits) >= 2:
        return True, f"Multiple promotional keywords: {weak_hits[:2]}", 0.80
    if len(weak_hits) == 1:
        if any_weak_sender:
            return True, f"Promotional keyword + commercial sender: '{weak_hits[0]}'", 0.72
        # Single weak keyword with no sender confirmation — below the 0.72 threshold
        return True, f"Weak promotional keyword: '{weak_hits[0]}'", 0.55

    # Generic commercial sender with no subject keywords — still worth flagging weakly
    if is_generic_commercial and not _is_spam_exempt(domain):
        return True, f"Generic commercial sender: {local_part}@{domain}", 0.58

    return False, "", 0.0


def classify_as_important(email_data: dict) -> tuple[bool, str, float]:
    subject = (email_data.get("subject") or "").lower()
    from_addr = (email_data.get("from") or "").lower()

    important_keywords = {
        "financial": [
            "invoice", "receipt", "payment", "bill", "statement",
            "transaction", "charge", "refund", "tax", "w-2", "1099",
            "dividend", "interest paid", "claim", "estimate",
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
            "package delivered", "your order",
        ],
        "appointment": [
            "appointment", "reservation confirmed", "booking confirmation",
            "your reservation", "upcoming appointment",
        ],
        "transactional": [
            "confirmation", "confirmed", "order #", "order number",
            "notification", "alert", "reminder",
            "new claim", "claim received",
        ],
    }

    transactional_sender_patterns = [
        "receipts", "billing", "invoice", "payment", "transactions",
        "alerts@", "notification@", "notify@",
        "marketplace-messages", "order-update",
    ]

    domain = _extract_domain(from_addr)

    # Transactional-only senders: any email is important; keyword match raises confidence
    if _domain_in_set(domain, _TRANSACTIONAL_DOMAINS) or domain.endswith(".gov") or domain.endswith(".edu"):
        for category, keywords in important_keywords.items():
            for kw in keywords:
                if kw in subject:
                    return True, f"Important ({category}): '{kw}'", 0.92
        return True, f"Trusted transactional sender: {domain}", 0.85

    # For all other senders (including mixed-use retail/tech), require a subject keyword
    for category, keywords in important_keywords.items():
        for kw in keywords:
            if kw in subject:
                return True, f"Important ({category}): '{kw}'", 0.90

    for pattern in transactional_sender_patterns:
        if pattern in from_addr:
            return True, f"Transactional sender pattern: '{pattern}'", 0.82

    return False, "", 0.0


def classify_email(email_data: dict) -> tuple[str, str, float]:
    """
    Classify an email into one of: spam | advertisements | important | keep | uncertain.
    Returns (category, reason, confidence).
    Priority: high-confidence spam → important → advertisements → low-confidence spam → keep.
    """
    is_spam, spam_reason, spam_conf = classify_as_spam(email_data)

    # Require 0.85+ to mark as spam — lets important classification override medium signals
    if is_spam and spam_conf >= 0.85:
        return "spam", spam_reason, spam_conf

    is_important, reason, conf = classify_as_important(email_data)
    if is_important and conf >= 0.80:
        return "important", reason, conf

    is_ad, reason, conf = classify_as_advertisement(email_data)
    if is_ad and conf >= 0.72:
        return "advertisements", reason, conf

    # Low-confidence spam (suspicious TLD alone, or medium signals < 0.85) — flag for review
    if is_spam:
        return "uncertain", f"Possible spam (low confidence): {spam_reason}", spam_conf

    return "keep", "No strong signals detected", 0.60
