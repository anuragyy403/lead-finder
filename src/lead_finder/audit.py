"""Website pre-audit: the checks that become the sales pitch (HTTPS, mobile, WhatsApp, speed...)."""

import re
from dataclasses import dataclass, field

import httpx
from selectolax.lexbor import LexborHTMLParser as HTMLParser

PAGESPEED_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
COPYRIGHT_RE = re.compile(r"(?:©|&copy;|copyright)\s*(?:\d{4}\s*[-–]\s*)?((?:19|20)\d{2})", re.IGNORECASE)
WHATSAPP_MARKERS = ("wa.me/", "api.whatsapp.com", "whatsapp://", "web.whatsapp.com")


@dataclass
class SiteAudit:
    website_kind: str = "none"    # none | social | real
    reachable: bool = False
    https: bool = False
    viewport: bool = False
    meta_description: bool = False
    whatsapp: bool = False
    contact_form: bool = False
    copyright_year: int | None = None
    pagespeed: int | None = None  # mobile performance score 0-100
    notes: list[str] = field(default_factory=list)


def audit_html(html: str, final_url: str, ssl_failed: bool, extra_html: str = "") -> SiteAudit:
    """Checks on the homepage HTML (plus the contact page for WhatsApp/form)."""
    tree = HTMLParser(html)
    both = html + extra_html
    lowered = both.lower()

    years = [int(y) for y in COPYRIGHT_RE.findall(html)]
    description = tree.css_first("meta[name='description' i]")
    return SiteAudit(
        website_kind="real",
        reachable=True,
        https=final_url.startswith("https://") and not ssl_failed,
        viewport=tree.css_first("meta[name='viewport' i]") is not None,
        meta_description=bool(description and (description.attributes.get("content") or "").strip()),
        whatsapp=any(m in lowered for m in WHATSAPP_MARKERS),
        contact_form="<form" in lowered,
        copyright_year=max(years) if years else None,
    )


async def pagespeed_score(client: httpx.AsyncClient, api_key: str, url: str) -> int | None:
    """Google PageSpeed mobile performance score. Slow (20-60 s); None if it fails."""
    params = {"url": url, "strategy": "mobile", "category": "performance"}
    if api_key:
        params["key"] = api_key
    try:
        resp = await client.get(PAGESPEED_URL, params=params, timeout=90)
        resp.raise_for_status()
        score = resp.json()["lighthouseResult"]["categories"]["performance"]["score"]
    except (httpx.HTTPError, KeyError, ValueError, TypeError):
        return None
    return round(score * 100) if score is not None else None
