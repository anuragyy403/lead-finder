import json

import httpx
import pytest
import respx

from lead_finder import sheet
from lead_finder.enrich import Lead, base_lead
from lead_finder.places import SEARCH_URL, Place, PlacesError, RequestBudget, find_places


def api_place(i: int, **extra) -> dict:
    return {"id": f"p{i}", "displayName": {"text": f"Broker {i}"}, **extra}


@pytest.fixture(autouse=True)
def no_page_delay(monkeypatch):
    monkeypatch.setattr("lead_finder.places.PAGE_DELAY_SECONDS", 0)


@respx.mock
async def test_pagination_follows_next_page_token_and_drops_closed():
    route = respx.post(SEARCH_URL).mock(side_effect=[
        httpx.Response(200, json={"places": [api_place(1), api_place(2, businessStatus="CLOSED_PERMANENTLY")],
                                  "nextPageToken": "t2"}),
        httpx.Response(200, json={"places": [api_place(3)]}),
    ])
    async with httpx.AsyncClient() as client:
        places = await find_places(client, "KEY", "real estate agents in Bengaluru", RequestBudget(10))

    assert [p.place_id for p in places] == ["p1", "p3"]
    assert json.loads(route.calls[1].request.content)["pageToken"] == "t2"
    assert "nextPageToken" in route.calls[0].request.headers["X-Goog-FieldMask"]


@respx.mock
async def test_budget_stops_pagination():
    respx.post(SEARCH_URL).mock(return_value=httpx.Response(200, json={"places": [api_place(1)], "nextPageToken": "t"}))
    budget = RequestBudget(2)
    async with httpx.AsyncClient() as client:
        await find_places(client, "KEY", "q", budget)
    assert budget.used == 2 and budget.exhausted


@respx.mock
async def test_google_error_is_not_retried():
    route = respx.post(SEARCH_URL).mock(return_value=httpx.Response(
        403, json={"error": {"status": "PERMISSION_DENIED", "message": "Places API (New) has not been used"}}))
    async with httpx.AsyncClient() as client:
        with pytest.raises(PlacesError, match="PERMISSION_DENIED"):
            await find_places(client, "KEY", "q", RequestBudget(10))
    assert route.call_count == 1


def make_lead(i: int, **place_fields) -> Lead:
    place = Place(place_id=f"p{i}", name=f"Broker {i}", phone_national="098450 1234" + str(i), **place_fields)
    lead = base_lead(place, "real estate agents", "Bengaluru")
    lead.score, lead.flaws, lead.emails = 55, ["No website"], ["a@b.in"]
    return lead


def sheet_rows(*place_ids: str, status: str = "Interested") -> list[list[str]]:
    rows = [sheet.LEAD_HEADERS]
    for pid in place_ids:
        row = [""] * len(sheet.LEAD_HEADERS)
        row[0] = pid
        row[sheet.LEAD_HEADERS.index("Status")] = status
        rows.append(row)
    return rows


def test_new_lead_appended_with_defaults():
    plan = sheet.plan_writes(sheet_rows(), [make_lead(1)], "2026-11-02 10:00", reaudit=False)
    assert plan.new_count == 1 and not plan.updates
    row = dict(zip(sheet.LEAD_HEADERS, plan.appends[0]))
    assert row["place_id"] == "p1"
    assert row["Status"] == "Not contacted"
    assert row["WhatsApp link"] == "https://wa.me/919845012341"
    assert row["Email link"] == "mailto:a@b.in?subject=Quick%20idea%20for%20Broker%201"


def test_new_leads_go_below_header_and_existing_rows():
    assert sheet.plan_writes(sheet_rows(), [make_lead(1)], "now", reaudit=False).first_new_row == 2
    plan = sheet.plan_writes(sheet_rows("p7", "p8"), [make_lead(1)], "now", reaudit=False)
    assert plan.first_new_row == 4


def test_existing_lead_never_touches_human_columns():
    plan = sheet.plan_writes(sheet_rows("p1"), [make_lead(1, rating=4.2)], "now", reaudit=False)
    assert plan.new_count == 0 and plan.refreshed_count == 1
    touched = {u["range"] for u in plan.updates}
    for name in sheet.HUMAN_COLUMNS + sheet.AUDIT_COLUMNS + ["First seen", "place_id"]:
        col = sheet.LEAD_HEADERS.index(name) + 1
        assert sheet.rowcol_to_a1(2, col) not in touched, name


def test_reaudit_updates_score_but_not_status():
    plan = sheet.plan_writes(sheet_rows("p1"), [make_lead(1)], "now", reaudit=True)
    touched = {u["range"] for u in plan.updates}
    assert sheet.rowcol_to_a1(2, sheet.LEAD_HEADERS.index("Score") + 1) in touched
    assert sheet.rowcol_to_a1(2, sheet.LEAD_HEADERS.index("Status") + 1) not in touched


def test_missing_columns_is_a_clear_error():
    with pytest.raises(sheet.SheetError, match="missing columns"):
        sheet.plan_writes([["place_id", "Business name"]], [], "now", reaudit=False)
