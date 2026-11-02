"""Turn an audit into a 0-100 score plus a human-readable flaw list. Weights live in scoring.toml."""

import tomllib
from datetime import date
from pathlib import Path

from .audit import SiteAudit


def load_weights(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def score_lead(audit: SiteAudit, rating: float | None, weights: dict, today: date | None = None) -> tuple[int, list[str]]:
    year = (today or date.today()).year
    flaws: list[tuple[str, str]] = []  # (weight key, text shown to sales)

    if audit.website_kind == "none":
        flaws.append(("no_website", "No website"))
    elif audit.website_kind == "social":
        flaws.append(("no_website", "Only a social media / directory page, no own website"))
    elif not audit.reachable:
        flaws.append(("website_down", "Website not loading"))
    else:
        if not audit.https:
            flaws.append(("no_https", "Not secure (no HTTPS)"))
        if audit.pagespeed is not None and audit.pagespeed < weights["slow_site_threshold"]:
            flaws.append(("slow_site", f"Slow on mobile (PageSpeed {audit.pagespeed}/100)"))
        if not audit.whatsapp:
            flaws.append(("no_whatsapp", "No WhatsApp button"))
        if not audit.viewport:
            flaws.append(("no_viewport", "Not mobile-friendly"))
        if audit.copyright_year and year - audit.copyright_year >= weights["old_copyright_years"]:
            flaws.append(("old_copyright", f"Old copyright year (© {audit.copyright_year})"))
        if not audit.meta_description:
            flaws.append(("no_meta_description", "No meta description (weak Google listing)"))
        if not audit.contact_form:
            flaws.append(("no_contact_form", "No contact / enquiry form"))

    website_flaw = any(weights.get(key, 0) > 0 for key, _ in flaws)
    if rating is not None and rating >= weights["good_rating"] and website_flaw:
        flaws.append(("good_rating_weak_site", f"Good rating ({rating}★) but weak website"))

    score = min(100, sum(weights.get(key, 0) for key, _ in flaws))
    return score, [text for _, text in flaws]
