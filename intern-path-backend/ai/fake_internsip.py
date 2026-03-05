import re
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup


EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
PHONE_PATTERN = re.compile(r"(?:\+91[-\s]?)?[6-9]\d{9}")
MONEY_WITH_CONTEXT_PATTERN = re.compile(
    r"(?:₹|rs\.?|inr)\s?([0-9][0-9,]{1,8})(?:\s*(?:/|-)?\s*(month|monthly|per month|year|yearly|per year|annum|pa))?"
)


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _normalize_domain(netloc: str) -> str:
    domain = netloc.lower().replace("www.", "")
    return domain.split(":")[0]


def _parse_money(value: str) -> int | None:
    digits = re.sub(r"[^0-9]", "", value)
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def scrape_website(url: str):
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        response = requests.get(url, timeout=12, headers=headers, allow_redirects=True)
        if response.status_code >= 400:
            return None

        soup = BeautifulSoup(response.text, "html.parser")

        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        page_title = _clean_text(soup.title.get_text(" ") if soup.title else "")
        meta_description_tag = soup.find("meta", attrs={"name": "description"})
        meta_description = _clean_text(meta_description_tag.get("content", "")) if meta_description_tag else ""

        body_text = _clean_text(soup.get_text(separator=" "))
        links = [
            (a.get("href") or "").strip().lower()
            for a in soup.find_all("a")
            if (a.get("href") or "").strip()
        ]

        return {
            "text": body_text,
            "title": page_title,
            "meta_description": meta_description,
            "links": links,
            "final_url": response.url,
        }
    except Exception:
        return None


def calculate_risk(page_data: dict, input_url: str):
    text = page_data["text"]
    full_text = " ".join([
        text,
        page_data.get("title", ""),
        page_data.get("meta_description", ""),
        " ".join(page_data.get("links", [])),
    ])

    score = 0
    risk_reasons = []
    trust_signals = []

    final_url = page_data.get("final_url") or input_url
    parsed = urlparse(final_url)
    domain = _normalize_domain(parsed.netloc)
    base_domain = ".".join(domain.split(".")[-2:]) if "." in domain else domain

    payment_keywords = [
        "registration fee",
        "training fee",
        "security deposit",
        "course fee",
        "pay now",
        "payment required",
        "application fee",
        "upi",
        "gpay",
        "phonepe",
    ]
    payment_hits = [kw for kw in payment_keywords if kw in full_text]
    if payment_hits:
        score += 4
        risk_reasons.append(
            f"Mentions upfront payment terms ({', '.join(payment_hits[:3])})"
        )

    emails = list(dict.fromkeys(EMAIL_PATTERN.findall(full_text)))
    public_email_domains = {
        "gmail.com",
        "yahoo.com",
        "outlook.com",
        "hotmail.com",
        "protonmail.com",
    }
    public_emails = [e for e in emails if e.split("@")[-1].lower() in public_email_domains]
    official_emails = [e for e in emails if e.split("@")[-1].lower().endswith(base_domain)]

    if public_emails and not official_emails:
        score += 2
        risk_reasons.append(f"Only public email contact found ({public_emails[0]})")
    elif official_emails:
        trust_signals.append(f"Official domain email found ({official_emails[0]})")

    stipend_values = []
    annual_values = []
    for raw_value, period in MONEY_WITH_CONTEXT_PATTERN.findall(full_text):
        amount = _parse_money(raw_value)
        if amount is None:
            continue

        normalized_period = (period or "").strip().lower()
        if normalized_period in {"year", "yearly", "per year", "annum", "pa"}:
            annual_values.append(amount)
            continue

        if normalized_period in {"month", "monthly", "per month"}:
            stipend_values.append(amount)
            continue

        window_pattern = re.compile(
            rf"(?:₹|rs\.?|inr)\s?{re.escape(raw_value)}[^\.\n]{{0,35}}",
            re.IGNORECASE,
        )
        snippet_match = window_pattern.search(full_text)
        snippet = snippet_match.group(0) if snippet_match else ""

        if any(token in snippet for token in ["year", "annum", "lpa", "ctc", "per year", "pa"]):
            annual_values.append(amount)
        else:
            stipend_values.append(amount)

    high_monthly_stipend = [value for value in stipend_values if value >= 50000]
    if high_monthly_stipend:
        score += 2
        risk_reasons.append(f"Unusually high monthly internship pay claim detected (₹{high_monthly_stipend[0]})")
    elif stipend_values:
        trust_signals.append(f"Stipend appears realistic (₹{min(stipend_values)})")

    if annual_values and not high_monthly_stipend:
        trust_signals.append("Yearly CTC/salary numbers detected and treated separately from stipend")

    urgency_keywords = [
        "apply immediately",
        "limited seats",
        "hurry up",
        "only today",
        "last chance",
        "urgent hiring",
    ]
    urgency_hits = [kw for kw in urgency_keywords if kw in full_text]
    if urgency_hits:
        score += 1
        risk_reasons.append(f"Pressure language found ({', '.join(urgency_hits[:2])})")

    whatsapp_present = "whatsapp" in full_text
    phones = PHONE_PATTERN.findall(full_text)
    has_formal_contact = bool(emails) or any("contact" in link for link in page_data.get("links", []))
    if whatsapp_present and phones and not has_formal_contact:
        score += 2
        risk_reasons.append("Relies on WhatsApp/phone contact without verifiable official contact page")

    scam_phrases = [
        "easy money",
        "guaranteed job",
        "100% placement",
        "no interview",
        "earn daily",
        "instant joining",
    ]
    scam_hits = [phrase for phrase in scam_phrases if phrase in full_text]
    if scam_hits:
        score += 3
        risk_reasons.append(f"Scam-like phrases detected ({', '.join(scam_hits[:3])})")

    company_identity_tokens = [
        "about us",
        "our company",
        "linkedin.com/company",
        "privacy policy",
        "terms and conditions",
        "registered office",
    ]
    identity_hits = [token for token in company_identity_tokens if token in full_text]
    if len(identity_hits) <= 1:
        score += 2
        risk_reasons.append("Limited company verification details found (about/policy/registered presence is weak)")
    else:
        trust_signals.append("Company and policy information appears present")

    domain_parts = domain.split(".") if domain else []
    suspicious_tlds = {"xyz", "top", "click", "rest", "buzz"}
    has_suspicious_tld = bool(domain_parts and domain_parts[-1] in suspicious_tlds)
    random_subdomain = bool(
        domain_parts
        and re.fullmatch(r"(?=.*\d)[a-z0-9-]{12,}", domain_parts[0] or "")
    )
    too_many_levels = len(domain_parts) > 4

    if has_suspicious_tld or random_subdomain or too_many_levels:
        score += 1
        risk_reasons.append(f"Domain pattern looks unusual ({domain})")
    else:
        trust_signals.append(f"Domain format looks normal ({domain})")

    if final_url.startswith("https://"):
        trust_signals.append("Uses HTTPS")
    else:
        score += 1
        risk_reasons.append("Site is not using HTTPS")

    if len(text) < 250:
        score += 1
        risk_reasons.append("Very little content available to verify legitimacy")

    if score >= 8:
        risk = "High Risk"
    elif score >= 4:
        risk = "Medium Risk"
    else:
        risk = "Low Risk"

    confidence = min(96, 58 + (len(risk_reasons) * 6) + (score * 2))

    reasons = risk_reasons[:6]
    if len(reasons) < 6:
        reasons.extend(trust_signals[: 6 - len(reasons)])
    if not reasons:
        reasons = ["No major red flags detected from available public content"]

    return {
        "risk_level": risk,
        "risk_score": score,
        "confidence_percentage": confidence,
        "reasons": reasons,
    }


def analyze_internship(url: str):
    page_data = scrape_website(url)
    if not page_data:
        return {
            "risk_level": "High Risk",
            "risk_score": 9,
            "confidence_percentage": 92,
            "reasons": [
                "Unable to access or parse this website, so legitimacy cannot be verified",
                "Use trusted internship portals or verified company career pages",
            ],
        }
    return calculate_risk(page_data, url)