from datetime import date

import httpx
import respx

from lead_finder.audit import SiteAudit, audit_html
from lead_finder.config import PROJECT_ROOT
from lead_finder.contacts import extract_emails, fetch_page, find_contact_link, is_social_only, normalise_phone
from lead_finder.scoring import load_weights, score_lead

WEIGHTS = load_weights(PROJECT_ROOT / "scoring.toml")


def test_mobile_phone_normalised_for_whatsapp():
    phone = normalise_phone("098450 12345")
    assert phone.digits == "919845012345"
    assert phone.display == "+91 98450 12345"
    assert phone.is_mobile


def test_landline_is_not_whatsapp():
    phone = normalise_phone("080 4123 4567")
    assert phone is not None and not phone.is_mobile


def test_bad_phone_is_none():
    assert normalise_phone("") is None
    assert normalise_phone("12") is None


def test_social_pages_are_not_websites():
    assert is_social_only("https://www.facebook.com/somebroker")
    assert is_social_only("https://somebroker.business.site/")
    assert not is_social_only("https://www.somebroker.in/")


def test_emails_filtered_and_same_domain_first():
    html = """<body>
      <a href="mailto:Sales@Broker.in?subject=hi">mail</a>
      write to owner@gmail.com or logo@2x.png or test@example.com
      <img src="icon@3x.webp"> info@broker.in.
    </body>"""
    assert extract_emails(html, "broker.in") == ["sales@broker.in", "info@broker.in", "owner@gmail.com"]


def test_contact_link_same_site_only():
    html = """<a href="https://facebook.com/contact">fb</a>
              <a href="/reach-us">Reach Us</a>"""
    assert find_contact_link(html, "https://broker.in/") == "https://broker.in/reach-us"


def test_audit_html_checks():
    html = """<html><head><meta name="viewport" content="width=device-width">
      <meta name="description" content="Flats in Whitefield"></head>
      <body><a href="https://wa.me/919845012345">Chat</a> © 2018 - 2021 Broker</body></html>"""
    audit = audit_html(html, "https://broker.in/", ssl_failed=False)
    assert audit.https and audit.viewport and audit.meta_description and audit.whatsapp
    assert audit.copyright_year == 2021
    assert not audit.contact_form
    assert audit_html("<form></form>", "http://x.in/", False).https is False


def test_score_no_website():
    score, flaws = score_lead(SiteAudit(website_kind="none"), rating=4.6, weights=WEIGHTS)
    assert score == 50  # no website 40 + good rating, weak site 10
    assert flaws == ["No website", "Good rating (4.6★) but weak website"]


def test_score_weak_real_site_capped_and_ordered():
    audit = SiteAudit(website_kind="real", reachable=True, https=False, pagespeed=30, copyright_year=2019)
    score, flaws = score_lead(audit, rating=3.9, weights=WEIGHTS, today=date(2026, 11, 2))
    assert score == 15 + 15 + 10 + 10 + 5
    assert "Slow on mobile (PageSpeed 30/100)" in flaws
    assert "No meta description (weak Google listing)" in flaws  # shown, 0 points


def test_good_site_scores_zero():
    audit = SiteAudit(
        website_kind="real", reachable=True, https=True, viewport=True, meta_description=True,
        whatsapp=True, contact_form=True, copyright_year=2026, pagespeed=90,
    )
    assert score_lead(audit, rating=4.8, weights=WEIGHTS, today=date(2026, 11, 2)) == (0, [])


@respx.mock
async def test_slow_site_gets_a_second_try_before_counting_as_down():
    route = respx.get("https://slow.example/").mock(side_effect=[
        httpx.ReadTimeout("slow"),
        httpx.Response(200, html="<html>ok</html>"),
    ])
    async with httpx.AsyncClient() as client:
        page = await fetch_page(client, "https://slow.example/")
    assert page.ok and route.call_count == 2


@respx.mock
async def test_site_that_times_out_twice_is_down():
    respx.get("https://dead.example/").mock(side_effect=httpx.ConnectTimeout("dead"))
    async with httpx.AsyncClient() as client:
        page = await fetch_page(client, "https://dead.example/")
    assert not page.ok and "after retry" in page.error
