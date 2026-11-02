"""Google Sheet I/O: Targets tab (what to search) and Maps Leads tab (results).

Rule: the script only ever writes system columns. Human columns (Status, Assigned to, Notes,
Last contacted) are filled with defaults for new rows and never touched again.
"""

from dataclasses import dataclass

import gspread
from gspread.utils import rowcol_to_a1

from .enrich import Lead

TARGETS_TAB = "Targets"
LEADS_TAB = "Maps Leads"
SOURCE = "Google Maps – Lead Finder"

TARGET_HEADERS = ["Active", "Category", "City", "Place type (optional)", "Last run", "Leads found"]
SEED_TARGET = [True, "real estate agents", "Bengaluru", "", "", ""]

LEAD_HEADERS = [
    "place_id", "Business name", "Category", "City", "Address", "Phone", "Email", "Website",
    "Maps link", "Rating", "Reviews", "Score", "Flaws", "Contact status", "WhatsApp link",
    "Email link", "Source", "First seen", "Last updated",
    "Status", "Assigned to", "Notes", "Last contacted",
]
HUMAN_COLUMNS = ["Status", "Assigned to", "Notes", "Last contacted"]
# Maps fields refreshed on every run (storage policy D27: keep Maps data fresh, <= ~30 days old)
REFRESH_COLUMNS = ["Business name", "Address", "Phone", "Website", "Maps link", "Rating", "Reviews", "Last updated"]
# Website-derived fields, rewritten only with --reaudit
AUDIT_COLUMNS = ["Email", "Score", "Flaws", "Contact status", "WhatsApp link", "Email link"]

STATUS_VALUES = ["Not contacted", "Contacted", "Replied", "Interested", "Client", "Not interested"]


class SheetError(Exception):
    pass


@dataclass
class Target:
    row: int  # sheet row number (1-based, header is row 1)
    category: str
    city: str
    place_type: str

    @property
    def query(self) -> str:
        return f"{self.category} in {self.city}"


def open_spreadsheet(service_account_info: dict, sheet_id: str) -> gspread.Spreadsheet:
    client = gspread.service_account_from_dict(service_account_info)
    try:
        return client.open_by_key(sheet_id)
    except (gspread.SpreadsheetNotFound, PermissionError) as e:
        raise SheetError(
            f"Can't open sheet {sheet_id}. Check SHEET_ID, and that the sheet is shared (Editor) with "
            f"{service_account_info.get('client_email')}"
        ) from e


def _tab(spreadsheet: gspread.Spreadsheet, title: str) -> gspread.Worksheet:
    try:
        return spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound as e:
        raise SheetError(f"Tab '{title}' not found. Run: lead-finder setup-sheet") from e


def _column_map(header: list[str], required: list[str], tab: str) -> dict[str, int]:
    """Header name -> 1-based column number, so columns can be reordered safely."""
    cols = {name.strip(): i + 1 for i, name in enumerate(header) if name.strip()}
    missing = [h for h in required if h not in cols]
    if missing:
        raise SheetError(f"Tab '{tab}' is missing columns: {', '.join(missing)}")
    return cols


# ---------- Targets ----------

def read_targets(spreadsheet: gspread.Spreadsheet) -> list[Target]:
    rows = _tab(spreadsheet, TARGETS_TAB).get_all_values()
    if not rows:
        raise SheetError("Targets tab is empty. Run: lead-finder setup-sheet")
    cols = _column_map(rows[0], TARGET_HEADERS, TARGETS_TAB)

    def cell(row: list[str], name: str) -> str:
        i = cols[name] - 1
        return row[i].strip() if i < len(row) else ""

    targets = []
    for n, row in enumerate(rows[1:], start=2):
        if cell(row, "Active").upper() != "TRUE":
            continue
        category, city = cell(row, "Category"), cell(row, "City")
        if category and city:
            targets.append(Target(n, category, city, cell(row, "Place type (optional)")))
    return targets


def update_targets(spreadsheet: gspread.Spreadsheet, results: dict[int, int], now: str) -> None:
    ws = _tab(spreadsheet, TARGETS_TAB)
    cols = _column_map(ws.row_values(1), TARGET_HEADERS, TARGETS_TAB)
    data = []
    for row, count in results.items():
        data.append({"range": rowcol_to_a1(row, cols["Last run"]), "values": [[now]]})
        data.append({"range": rowcol_to_a1(row, cols["Leads found"]), "values": [[count]]})
    if data:
        ws.batch_update(data)


# ---------- Maps Leads ----------

def lead_values(lead: Lead, now: str) -> dict[str, object]:
    p = lead.place
    return {
        "place_id": p.place_id,
        "Business name": p.name,
        "Category": lead.category,
        "City": lead.city,
        "Address": p.address,
        "Phone": lead.phone,
        "Email": ", ".join(lead.emails[:2]),
        "Website": p.website,
        "Maps link": p.maps_url,
        "Rating": p.rating if p.rating is not None else "",
        "Reviews": p.reviews if p.reviews is not None else "",
        "Score": lead.score,
        "Flaws": "; ".join(lead.flaws),
        "Contact status": lead.contact_status,
        "WhatsApp link": lead.whatsapp_link,
        "Email link": lead.email_link,
        "Source": SOURCE,
        "First seen": now,
        "Last updated": now,
        "Status": STATUS_VALUES[0],
    }


@dataclass
class WritePlan:
    appends: list[list[object]]
    first_new_row: int  # sheet row where the first appended lead goes (right after the last used row)
    updates: list[dict]  # gspread batch_update entries: {"range": "B5", "values": [[...]]}
    new_count: int
    refreshed_count: int


def existing_place_ids(rows: list[list[str]]) -> dict[str, int]:
    """place_id -> sheet row number, from all values of the Maps Leads tab (header included)."""
    if not rows:
        return {}
    col = _column_map(rows[0], ["place_id"], LEADS_TAB)["place_id"] - 1
    return {r[col]: n for n, r in enumerate(rows[1:], start=2) if col < len(r) and r[col]}


def plan_writes(rows: list[list[str]], leads: list[Lead], now: str, reaudit: bool) -> WritePlan:
    """Decide what to write, without touching the sheet (pure, so it is easy to test)."""
    header = rows[0] if rows else []
    cols = _column_map(header, LEAD_HEADERS, LEADS_TAB)
    width = max(cols.values())
    existing = existing_place_ids(rows)
    update_columns = REFRESH_COLUMNS + (AUDIT_COLUMNS if reaudit else [])

    appends, updates, refreshed = [], [], 0
    for lead in leads:
        values = lead_values(lead, now)
        row = existing.get(lead.place.place_id)
        if row is None:
            line: list[object] = [""] * width
            for name, value in values.items():
                line[cols[name] - 1] = value
            appends.append(line)
        else:
            refreshed += 1
            for name in update_columns:
                updates.append({"range": rowcol_to_a1(row, cols[name]), "values": [[values[name]]]})
    return WritePlan(appends, max(len(rows), 1) + 1, updates, len(appends), refreshed)


def read_leads(spreadsheet: gspread.Spreadsheet) -> list[list[str]]:
    return _tab(spreadsheet, LEADS_TAB).get_all_values()


def write_leads(spreadsheet: gspread.Spreadsheet, plan: WritePlan) -> None:
    ws = _tab(spreadsheet, LEADS_TAB)
    if plan.appends:  # one API call for all new rows
        # Write at an exact row. The Sheets "append" API guesses where the table ends, and with the frozen,
        # filtered header it inserted the new rows above the header instead of below it.
        last_row = plan.first_new_row + len(plan.appends) - 1
        if last_row > ws.row_count:
            ws.add_rows(last_row - ws.row_count)
        ws.update(values=plan.appends, range_name=rowcol_to_a1(plan.first_new_row, 1), raw=True)
    if plan.updates:  # one API call for all refreshed cells
        ws.batch_update(plan.updates, raw=True)


# ---------- One-time setup ----------

def _header_format(sheet_id: int, columns: int) -> list[dict]:
    return [
        {"updateSheetProperties": {
            "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
            "fields": "gridProperties.frozenRowCount",
        }},
        {"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1, "endColumnIndex": columns},
            "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
            "fields": "userEnteredFormat.textFormat.bold",
        }},
    ]


def _setup_targets(spreadsheet: gspread.Spreadsheet, ws: gspread.Worksheet) -> None:
    ws.update(values=[TARGET_HEADERS, SEED_TARGET], range_name="A1", raw=True)
    spreadsheet.batch_update({"requests": _header_format(ws.id, len(TARGET_HEADERS)) + [
        {"setDataValidation": {
            "range": {"sheetId": ws.id, "startRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 1},
            "rule": {"condition": {"type": "BOOLEAN"}},
        }},
    ]})


def _setup_leads(spreadsheet: gspread.Spreadsheet, ws: gspread.Worksheet) -> None:
    ws.update(values=[LEAD_HEADERS], range_name="A1", raw=True)
    status_col = LEAD_HEADERS.index("Status")
    score_col = LEAD_HEADERS.index("Score")
    first_human = min(LEAD_HEADERS.index(h) for h in HUMAN_COLUMNS)
    spreadsheet.batch_update({"requests": _header_format(ws.id, len(LEAD_HEADERS)) + [
        # Hide place_id: it's the dedupe key, people don't need to see it
        {"updateDimensionProperties": {
            "range": {"sheetId": ws.id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1},
            "properties": {"hiddenByUser": True},
            "fields": "hiddenByUser",
        }},
        # Status dropdown
        {"setDataValidation": {
            "range": {"sheetId": ws.id, "startRowIndex": 1, "startColumnIndex": status_col, "endColumnIndex": status_col + 1},
            "rule": {
                "condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": v} for v in STATUS_VALUES]},
                "showCustomUi": True,
                "strict": True,
            },
        }},
        # Warning-only protection on script columns: people can edit, but get an "are you sure?"
        {"addProtectedRange": {"protectedRange": {
            "range": {"sheetId": ws.id, "startColumnIndex": 0, "endColumnIndex": first_human},
            "description": "Filled by the Lead Finder script. Editing place_id creates duplicates.",
            "warningOnly": True,
        }}},
        # Score colour: white (0) -> green (100)
        {"addConditionalFormatRule": {"index": 0, "rule": {
            "ranges": [{"sheetId": ws.id, "startRowIndex": 1, "startColumnIndex": score_col, "endColumnIndex": score_col + 1}],
            "gradientRule": {
                "minpoint": {"color": {"red": 1, "green": 1, "blue": 1}, "type": "NUMBER", "value": "0"},
                "maxpoint": {"color": {"red": 0.34, "green": 0.73, "blue": 0.54}, "type": "NUMBER", "value": "100"},
            },
        }}},
        {"setBasicFilter": {"filter": {"range": {"sheetId": ws.id, "endColumnIndex": len(LEAD_HEADERS)}}}},
    ]})


def setup_sheet(spreadsheet: gspread.Spreadsheet) -> list[str]:
    """Create and format the Targets and Maps Leads tabs. Safe to run again: existing tabs are left alone."""
    done: list[str] = []
    tabs = {ws.title: ws for ws in spreadsheet.worksheets()}

    if TARGETS_TAB in tabs:
        done.append(f"'{TARGETS_TAB}' already exists, left unchanged")
    else:
        blank = tabs.get("Sheet1")
        if blank is not None and len(tabs) == 1 and not blank.get_all_values():
            blank.update_title(TARGETS_TAB)
            ws = blank
        else:
            ws = spreadsheet.add_worksheet(TARGETS_TAB, rows=100, cols=len(TARGET_HEADERS))
        _setup_targets(spreadsheet, ws)
        done.append(f"Created '{TARGETS_TAB}' with the first target: real estate agents – Bengaluru")

    if LEADS_TAB in tabs:
        done.append(f"'{LEADS_TAB}' already exists, left unchanged")
    else:
        ws = spreadsheet.add_worksheet(LEADS_TAB, rows=1000, cols=len(LEAD_HEADERS))
        _setup_leads(spreadsheet, ws)
        done.append(f"Created '{LEADS_TAB}' (place_id hidden, Status dropdown, warning on script columns)")
    return done
