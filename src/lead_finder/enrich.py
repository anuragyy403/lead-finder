"""Phases 2-3 for one business: contacts, website audit, score, and one-click contact links."""

import asyncio
from dataclasses import dataclass, field
from urllib.parse import quote

import httpx

from .audit import SiteAudit, audit_html, pagespeed_score
from .contacts import (
    extract_emails,
    fetch_page,
    find_contact_link,
    host_of,
    is_social_only,
    normalise_phone,
    robots_allows,
)
from .places import Place
from .scoring import score_lead


@dataclass
class Lead:
    place: Place
    category: str
    city: str
    phone: str = ""
    phone_digits: str = ""
    is_mobile: bool = False
    emails: list[str] = field(default_factory=list)
    audit: SiteAudit = field(default_factory=SiteAudit)
    score: int = 0
    flaws: list[str] = field(default_factory=list)

    @property
    def contact_status(self) -> str:
        if self.emails:
            return "has_email"
        return "phone_only" if self.phone else "no_contact"

    @property
    def whatsapp_link(self) -> str:
        return f"https://wa.me/{self.phone_digits}" if self.is_mobile else ""

    @property
    def email_link(self) -> str:
        if not self.emails:
            return ""
        subject = quote(f"Quick idea for {self.place.name}")
        return f"mailto:{self.emails[0]}?subject={subject}"


def user_agent(contact_email: str) -> str:
    contact = f"; contact: {contact_email}" if contact_email else ""
    return f"LeadFinder/0.1 (business website check{contact})"


async def _audit_website(
    client: httpx.AsyncClient, api_key: str, ua: str, url: str, pagespeed_slots: asyncio.Semaphore
) -> tuple[SiteAudit, list[str]]:
    if is_social_only(url):
        return SiteAudit(website_kind="social"), []

    if not await robots_allows(client, url, ua):
        audit = SiteAudit(website_kind="real", reachable=True, https=url.startswith("https://"))
        audit.notes.append("robots.txt blocks bots; site not scanned")
        return audit, []

    home = await fetch_page(client, url)
    if not home.ok:
        audit = SiteAudit(website_kind="real", reachable=False)
        audit.notes.append(home.error)
        return audit, []

    contact_html = ""
    contact_url = find_contact_link(home.html, home.url)
    if contact_url and contact_url != home.url and await robots_allows(client, contact_url, ua):
        contact = await fetch_page(client, contact_url)
        contact_html = contact.html

    audit = audit_html(home.html, home.url, home.ssl_failed, contact_html)
    emails = extract_emails(home.html + contact_html, host_of(home.url))
    async with pagespeed_slots:
        audit.pagespeed = await pagespeed_score(client, api_key, home.url)
    return audit, emails


def base_lead(place: Place, category: str, city: str) -> Lead:
    """A lead with Maps data and a normalised phone, before any website work."""
    lead = Lead(place=place, category=category, city=city)
    phone = normalise_phone(place.phone_international or place.phone_national)
    if phone:
        lead.phone, lead.phone_digits, lead.is_mobile = phone.display, phone.digits, phone.is_mobile
    return lead


async def enrich(
    client: httpx.AsyncClient,
    place: Place,
    category: str,
    city: str,
    api_key: str,
    weights: dict,
    ua: str,
    pagespeed_slots: asyncio.Semaphore,
) -> Lead:
    lead = base_lead(place, category, city)
    if place.website:
        try:
            lead.audit, lead.emails = await _audit_website(client, api_key, ua, place.website, pagespeed_slots)
        except Exception as e:  # noqa: BLE001 — one broken website must never stop the whole run
            lead.audit = SiteAudit(website_kind="real", reachable=False, notes=[f"audit error: {e}"])

    lead.score, lead.flaws = score_lead(lead.audit, place.rating, weights)
    return lead


async def enrich_all(
    places: list[tuple[Place, str, str]],
    api_key: str,
    weights: dict,
    contact_email: str = "",
    concurrency: int = 5,
) -> list[Lead]:
    """Enrich many businesses at once, but at most `concurrency` websites at a time."""
    ua = user_agent(contact_email)
    site_slots = asyncio.Semaphore(concurrency)
    pagespeed_slots = asyncio.Semaphore(3)

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(10.0), follow_redirects=True, headers={"User-Agent": ua}
    ) as client:
        async def one(item: tuple[Place, str, str]) -> Lead:
            place, category, city = item
            async with site_slots:
                return await enrich(client, place, category, city, api_key, weights, ua, pagespeed_slots)

        return await asyncio.gather(*(one(item) for item in places))
