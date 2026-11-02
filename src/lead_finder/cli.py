"""Command line: lead-finder check | setup-sheet | search "<query>" | run [--dry-run] [--reaudit]"""

import argparse
import asyncio
import sys

import httpx

from . import sheet
from .config import ConfigError, load_settings
from .pipeline import run
from .places import PlacesError, RequestBudget, find_places


def _short(text: str, width: int) -> str:
    text = str(text)
    return text if len(text) <= width else text[: width - 1] + "…"


def print_table(rows: list[list[object]], widths: list[int]) -> None:
    for row in rows:
        print("  ".join(_short(v, w).ljust(w) for v, w in zip(row, widths)))


def cmd_check(_: argparse.Namespace) -> None:
    settings = load_settings()
    print("✓ .env loaded")
    spreadsheet = sheet.open_spreadsheet(settings.service_account_info, settings.sheet_id)
    print(f"✓ Sheet opened: {spreadsheet.title}")
    targets = sheet.read_targets(spreadsheet)
    print(f"✓ Targets tab read: {len(targets)} active target(s)")
    for t in targets:
        print(f"    - {t.query}" + (f" (type: {t.place_type})" if t.place_type else ""))
    sheet.read_leads(spreadsheet)
    print("✓ Maps Leads tab read")
    print("OK")


def cmd_setup_sheet(_: argparse.Namespace) -> None:
    settings = load_settings()
    spreadsheet = sheet.open_spreadsheet(settings.service_account_info, settings.sheet_id)
    for line in sheet.setup_sheet(spreadsheet):
        print(f"✓ {line}")
    print(f"Open it: https://docs.google.com/spreadsheets/d/{settings.sheet_id}/edit")


def cmd_search(args: argparse.Namespace) -> None:
    """Milestone M1: print what Google returns for one query. Writes nothing."""
    settings = load_settings(need_sheet=False)
    budget = RequestBudget(min(args.pages, 3))

    async def go():
        async with httpx.AsyncClient(timeout=30) as client:
            return await find_places(client, settings.google_api_key, args.query, budget)

    places = asyncio.run(go())
    print(f"{len(places)} businesses for '{args.query}' ({budget.used} request(s) used)\n")
    rows = [["#", "Name", "Phone", "Website", "Rating", "Reviews"]]
    rows += [[i, p.name, p.phone_national, p.website or "-", p.rating or "-", p.reviews or "-"]
             for i, p in enumerate(places, 1)]
    print_table(rows, [3, 38, 16, 38, 6, 7])


def cmd_run(args: argparse.Namespace) -> None:
    settings = load_settings()
    summary = asyncio.run(run(settings, dry_run=args.dry_run, reaudit=args.reaudit))

    print("\nTop leads this run:")
    rows = [["Score", "Name", "Phone", "Email", "Flaws"]]
    rows += [[lead.score, lead.place.name, lead.phone or "-", (lead.emails or ["-"])[0], "; ".join(lead.flaws)]
             for lead in summary.leads[:15]]
    print_table(rows, [5, 32, 16, 28, 60])

    print("\nRun summary")
    print(f"  Targets searched : {summary.targets}")
    print(f"  Places requests  : {summary.requests_used} / {summary.request_limit}"
          + ("  (stopped early: request limit reached)" if summary.stopped_by_budget else ""))
    print(f"  Businesses found : {summary.found}")
    print(f"  New leads        : {summary.new}" + ("  (dry run: nothing written)" if args.dry_run else ""))
    print(f"  Refreshed        : {summary.refreshed}")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # business names can contain any script

    parser = argparse.ArgumentParser(prog="lead-finder", description="Find and score local business leads.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="test .env, sheet access and tabs").set_defaults(func=cmd_check)
    sub.add_parser("setup-sheet", help="create the Targets and Maps Leads tabs").set_defaults(func=cmd_setup_sheet)
    p = sub.add_parser("search", help="print results for one query (writes nothing)")
    p.add_argument("query", help='e.g. "real estate agents in Bengaluru"')
    p.add_argument("--pages", type=int, default=1, help="pages of 20 to fetch (max 3)")
    p.set_defaults(func=cmd_search)
    p = sub.add_parser("run", help="search all active targets and update the sheet")
    p.add_argument("--dry-run", action="store_true", help="do everything except write to the sheet")
    p.add_argument("--reaudit", action="store_true", help="re-check websites of existing leads too")
    p.set_defaults(func=cmd_run)

    args = parser.parse_args()
    try:
        args.func(args)
    except (ConfigError, sheet.SheetError, PlacesError) as e:
        print(f"\n✗ {e}", file=sys.stderr)
        sys.exit(1)
