"""The full run: Targets -> Places search -> dedupe -> contacts + audit + score -> Maps Leads."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

from . import sheet
from .config import Settings
from .enrich import Lead, base_lead, enrich_all
from .places import Place, RequestBudget, find_places
from .scoring import load_weights

IST = timezone(timedelta(hours=5, minutes=30))  # fixed offset: India has no daylight saving


def now_ist() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d %H:%M")


@dataclass
class RunSummary:
    targets: int
    requests_used: int
    request_limit: int
    found: int
    new: int
    refreshed: int
    stopped_by_budget: bool
    leads: list[Lead]


async def search_targets(
    targets: list[sheet.Target], api_key: str, budget: RequestBudget
) -> tuple[dict[str, tuple[Place, sheet.Target]], bool]:
    """Search every target; dedupe by place_id (first target that found a place keeps it)."""
    found: dict[str, tuple[Place, sheet.Target]] = {}
    async with httpx.AsyncClient(timeout=30) as client:
        for target in targets:
            if budget.exhausted:
                return found, True
            places = await find_places(client, api_key, target.query, budget, target.place_type or None)
            print(f"  {target.query}: {len(places)} results")
            for place in places:
                found.setdefault(place.place_id, (place, target))
    return found, False


async def run(settings: Settings, dry_run: bool = False, reaudit: bool = False) -> RunSummary:
    spreadsheet = sheet.open_spreadsheet(settings.service_account_info, settings.sheet_id)
    targets = sheet.read_targets(spreadsheet)
    if not targets:
        raise sheet.SheetError("No active targets. Tick 'Active' on at least one row in the Targets tab.")

    print(f"Searching {len(targets)} target(s), max {settings.max_requests_per_run} Places requests...")
    budget = RequestBudget(settings.max_requests_per_run)
    found, stopped = await search_targets(targets, settings.google_api_key, budget)

    rows = sheet.read_leads(spreadsheet)
    existing = sheet.existing_place_ids(rows)
    to_audit = [(p, t) for pid, (p, t) in found.items() if reaudit or pid not in existing]
    to_refresh = [(p, t) for pid, (p, t) in found.items() if not reaudit and pid in existing]

    print(f"Checking {len(to_audit)} website(s) (contacts, HTTPS, mobile, speed)... this can take a few minutes")
    weights = load_weights(settings.scoring_file)
    audited = await enrich_all(
        [(p, t.category, t.city) for p, t in to_audit],
        settings.google_api_key, weights, settings.contact_email,
    )
    # Existing leads: only Maps fields get refreshed, no website re-audit
    leads = audited + [base_lead(p, t.category, t.city) for p, t in to_refresh]

    now = now_ist()
    plan = sheet.plan_writes(rows, leads, now, reaudit)
    if not dry_run:
        sheet.write_leads(spreadsheet, plan)
        new_ids = set(found) - set(existing)
        per_target = {t.row: 0 for t in targets}
        for pid in new_ids:
            per_target[found[pid][1].row] += 1
        sheet.update_targets(spreadsheet, per_target, now)

    return RunSummary(
        targets=len(targets),
        requests_used=budget.used,
        request_limit=budget.limit,
        found=len(found),
        new=plan.new_count,
        refreshed=plan.refreshed_count,
        stopped_by_budget=stopped,
        leads=sorted(audited, key=lambda lead: -lead.score),
    )
