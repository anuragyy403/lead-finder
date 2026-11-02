"""Contact details: phone normalisation, polite website fetching, email extraction."""

import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
import phonenumbers
from phonenumbers import PhoneNumberFormat, PhoneNumberType
from selectolax.lexbor import LexborHTMLParser as HTMLParser

# "Websites" that are really just a social page or link hub, not a site the business owns.
SOCIAL_HOSTS = (
    "facebook.com", "fb.com", "instagram.com", "linktr.ee", "wa.me", "whatsapp.com",
    "youtube.com", "twitter.com", "x.com", "linkedin.com", "business.site", "justdial.com",
    "sulekha.com", "magicbricks.com", "99acres.com", "housing.com", "g.page",
)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
JUNK_EMAIL_PARTS = (
    "example.", "domain.com", "yourdomain", "email.com", "sentry", "wixpress", "@2x",
    "godaddy", "schema.org", "w3.org", "noreply", "no-reply",
)
JUNK_EMAIL_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js")
CONTACT_WORDS = ("contact", "reach-us", "reach us", "get-in-touch", "get in touch", "enquiry", "inquiry")


@dataclass
class Phone:
    display: str       # +91 98765 43210
    digits: str        # 919876543210 (for wa.me)
    is_mobile: bool    # landlines can't use WhatsApp


def normalise_phone(raw: str) -> Phone | None:
    if not raw:
        return None
    try:
        num = phonenumbers.parse(raw, "IN")
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(num):
        return None
    kind = phonenumbers.number_type(num)
    return Phone(
        display=phonenumbers.format_number(num, PhoneNumberFormat.INTERNATIONAL),
        digits=phonenumbers.format_number(num, PhoneNumberFormat.E164).lstrip("+"),
        is_mobile=kind in (PhoneNumberType.MOBILE, PhoneNumberType.FIXED_LINE_OR_MOBILE),
    )


def host_of(url: str) -> str:
    host = urlparse(url if "//" in url else f"//{url}").hostname or ""
    return host.lower().removeprefix("www.")


def is_social_only(url: str) -> bool:
    host = host_of(url)
    return any(host == s or host.endswith("." + s) for s in SOCIAL_HOSTS)


@dataclass
class Page:
    url: str                # final URL after redirects
    html: str = ""
    ok: bool = False
    ssl_failed: bool = False
    error: str = ""


SLOW_SITE_TIMEOUT = 30.0  # second try for a timed-out site, so a slow site isn't scored as "down"


async def fetch_page(client: httpx.AsyncClient, url: str, retry_slow: bool = True) -> Page:
    """GET a page. If HTTPS fails on the certificate, fall back to plain HTTP and remember it.

    A timeout gets one more try with a longer limit: many small-business sites are just slow.
    """
    try:
        resp = await client.get(url)
    except httpx.TimeoutException as e:
        if not retry_slow:
            return Page(url=url, error=f"{type(e).__name__}: {e}")
        try:
            resp = await client.get(url, timeout=SLOW_SITE_TIMEOUT)
        except httpx.HTTPError as e2:
            return Page(url=url, error=f"{type(e2).__name__} (after retry): {e2}")
    except httpx.ConnectError as e:
        message = str(e).upper()
        if url.startswith("https://") and ("SSL" in message or "CERTIFICATE" in message):
            page = await fetch_page(client, "http://" + url.removeprefix("https://"))
            page.ssl_failed = True
            return page
        return Page(url=url, error=f"connect failed: {e}")
    except httpx.HTTPError as e:
        return Page(url=url, error=f"{type(e).__name__}: {e}")

    content_type = resp.headers.get("content-type", "")
    ok = resp.status_code < 400 and ("html" in content_type or not content_type)
    return Page(
        url=str(resp.url),
        html=resp.text if ok else "",
        ok=ok,
        error="" if ok else f"HTTP {resp.status_code}",
    )


async def robots_allows(client: httpx.AsyncClient, url: str, user_agent: str) -> bool:
    """Honour robots.txt. If robots.txt is missing or unreadable, crawling is allowed."""
    parts = urlparse(url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    try:
        resp = await client.get(robots_url)
    except httpx.HTTPError:
        return True
    if resp.status_code >= 400:
        return True
    parser = RobotFileParser()
    parser.parse(resp.text.splitlines())
    return parser.can_fetch(user_agent, url)


def find_contact_link(html: str, base_url: str) -> str | None:
    """A same-site link whose text or URL looks like a contact page (/contact-us, /reach-us...)."""
    base_host = host_of(base_url)
    for a in HTMLParser(html).css("a[href]"):
        href = a.attributes.get("href") or ""
        text = (a.text() or "").lower()
        if href.startswith(("mailto:", "tel:", "#", "javascript:")):
            continue
        if any(w in href.lower() or w in text for w in CONTACT_WORDS):
            full = urljoin(base_url, href)
            if host_of(full) == base_host:
                return full
    return None


def _clean_email(email: str) -> str | None:
    email = email.strip().strip(".").lower()
    if any(p in email for p in JUNK_EMAIL_PARTS) or email.endswith(JUNK_EMAIL_SUFFIXES):
        return None
    return email


def extract_emails(html: str, site_host: str = "") -> list[str]:
    """Emails from mailto: links and page text, junk removed, same-domain emails first."""
    tree = HTMLParser(html)
    found: list[str] = []
    for a in tree.css("a[href^='mailto:' i]"):
        address = (a.attributes.get("href") or "")[7:].split("?")[0]
        found.append(address)
    body = tree.body.text(separator=" ") if tree.body else html
    found += EMAIL_RE.findall(body)

    emails: list[str] = []
    for raw in found:
        email = _clean_email(raw)
        if email and EMAIL_RE.fullmatch(email) and email not in emails:
            emails.append(email)
    if site_host:
        emails.sort(key=lambda e: not e.endswith("@" + site_host))
    return emails
