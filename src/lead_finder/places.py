"""Google Places API (New) Text Search: find businesses for a query like "real estate agents in Bengaluru"."""

import asyncio

import httpx
from pydantic import BaseModel
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

# The field mask decides both what Google returns and what we pay for.
# Phone, website and rating put this request in the Text Search Enterprise SKU.
# nextPageToken MUST be listed, or pagination silently stops after page 1.
FIELD_MASK = ",".join([  # noqa: FLY002
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.nationalPhoneNumber",
    "places.internationalPhoneNumber",
    "places.websiteUri",
    "places.rating",
    "places.userRatingCount",
    "places.googleMapsUri",
    "places.primaryType",
    "places.businessStatus",
    "nextPageToken",
])

MAX_PAGES = 3  # Google returns at most 60 results (3 pages of 20) per query
PAGE_DELAY_SECONDS = 1.0


class PlacesError(Exception):
    """A request Google rejected (bad key, API not enabled, bad field mask...). Not retried."""


class _Retryable(Exception):
    """429 / 5xx: Google is busy, worth retrying."""


class RequestBudget:
    """Counts Places API calls and refuses to go past the per-run cap."""

    def __init__(self, limit: int):
        self.limit = limit
        self.used = 0

    @property
    def exhausted(self) -> bool:
        return self.used >= self.limit

    def spend(self) -> None:
        self.used += 1


class Place(BaseModel):
    place_id: str
    name: str
    address: str = ""
    phone_national: str = ""
    phone_international: str = ""
    website: str = ""
    rating: float | None = None
    reviews: int | None = None
    maps_url: str = ""
    primary_type: str = ""
    business_status: str = ""

    @classmethod
    def from_api(cls, data: dict) -> "Place":
        return cls(
            place_id=data["id"],
            name=data.get("displayName", {}).get("text", ""),
            address=data.get("formattedAddress", ""),
            phone_national=data.get("nationalPhoneNumber", ""),
            phone_international=data.get("internationalPhoneNumber", ""),
            website=data.get("websiteUri", ""),
            rating=data.get("rating"),
            reviews=data.get("userRatingCount"),
            maps_url=data.get("googleMapsUri", ""),
            primary_type=data.get("primaryType", ""),
            business_status=data.get("businessStatus", ""),
        )


def _google_error_message(resp: httpx.Response) -> str:
    try:
        err = resp.json().get("error", {})
        return f"{resp.status_code} {err.get('status', '')}: {err.get('message', resp.text)}"
    except ValueError:
        return f"{resp.status_code}: {resp.text[:300]}"


@retry(
    retry=retry_if_exception_type((_Retryable, httpx.TransportError)),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, max=20),
    reraise=True,
)
async def search_text(
    client: httpx.AsyncClient,
    api_key: str,
    query: str,
    budget: RequestBudget,
    page_token: str | None = None,
    included_type: str | None = None,
) -> dict:
    """One Text Search request (one page of up to 20 places)."""
    body: dict = {"textQuery": query, "pageSize": 20, "regionCode": "IN", "languageCode": "en"}
    if page_token:
        body["pageToken"] = page_token
    if included_type:
        body["includedType"] = included_type
        body["strictTypeFiltering"] = True

    budget.spend()  # every attempt is a billed request, retries included
    resp = await client.post(
        SEARCH_URL,
        json=body,
        headers={"X-Goog-Api-Key": api_key, "X-Goog-FieldMask": FIELD_MASK},
    )
    if resp.status_code == 429 or resp.status_code >= 500:
        raise _Retryable(_google_error_message(resp))
    if resp.status_code >= 400:
        raise PlacesError(_google_error_message(resp))
    return resp.json()


async def find_places(
    client: httpx.AsyncClient,
    api_key: str,
    query: str,
    budget: RequestBudget,
    included_type: str | None = None,
) -> list[Place]:
    """All pages for one query, stopping early if the run's request budget runs out.

    Permanently closed businesses are dropped.
    """
    places: list[Place] = []
    page_token = None
    for page in range(MAX_PAGES):
        if budget.exhausted:
            break
        if page:
            await asyncio.sleep(PAGE_DELAY_SECONDS)
        data = await search_text(client, api_key, query, budget, page_token, included_type)
        places += [
            Place.from_api(p) for p in data.get("places", [])
            if p.get("businessStatus") != "CLOSED_PERMANENTLY"
        ]
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return places
