# Lead Finder

Finds local businesses on Google Maps (by category + city), checks how weak their website is, scores them, and
writes a ranked lead list to a Google Sheet. The sales team then contacts the best leads by hand, using the
one-click WhatsApp and email links in the sheet.

```
Targets tab ──> Google Places search ──> website check + PageSpeed ──> score ──> Maps Leads tab ──> team
```

Nothing is sent to any business automatically. The script only builds the list.

## How the sheet works

**Targets tab**: what to search for. One row per search.

| Column | Fill in | Example |
| --- | --- | --- |
| Active | Tick to include in the next run | ☑ |
| Category | Kind of business | `real estate agents` |
| City | City | `Bengaluru` |
| Place type (optional) | Leave blank, or a Google place type to filter results | `real_estate_agency` |
| Last run / Leads found | Filled by the script (new leads from the last run) | |

Each row is searched as "Category in City". Google returns at most 60 businesses per search (3 requests). To get
more, split a city into areas: `real estate agents` / `Whitefield, Bengaluru`.

**Maps Leads tab**: the results, one row per business.

- The script fills: name, address, phone, email, website, rating, **Score**, **Flaws**, WhatsApp link, email link.
- The team fills: **Status**, **Assigned to**, **Notes**, **Last contacted**. The script never overwrites these.
- Re-running is safe: businesses already in the sheet are not added again. Their Maps details (phone, rating…)
  are refreshed.
- Don't delete or edit the hidden `place_id` column. It's how the script recognises businesses it has seen.

**Contacting leads** (team guidelines):

1. Filter or sort **Score** high → low. Read the **Flaws**: they are your talking points.
2. Set **Assigned to** before messaging, so two people never contact the same business.
3. Click the **WhatsApp link** (or **Email link**), write a personal message, and send it yourself.
4. Update **Status** and **Last contacted**.

- Use your own **WhatsApp Business number**, never a personal or shared one, and start at most **20–30 new chats a
  day** per number. More than that risks the number being banned.
- If a business says no or asks not to be contacted, set **Status = Not interested**. That means never contact
  them again.

**Score**: higher = weaker online presence = better lead. Points per flaw are in [`scoring.toml`](scoring.toml)
(e.g. no website 40, slow site 15, no WhatsApp button 10), capped at 100. Change the numbers there to re-tune; no
code change needed.

## Run it from GitHub (Run button)

1. Open the repo on GitHub → **Actions** tab → **Find leads** (left side).
2. Click **Run workflow**, choose the options, and click the green **Run workflow** button.
   - *Test only*: does everything except write to the sheet.
   - *Re-check websites*: also re-audits leads already in the sheet (slower).
   - *Max requests*: the cap on Google Places requests for this run.
3. Wait a few minutes, then open the sheet.

The run log shows only counts, never lead details, because this repo is public and so are its logs.

### One-time setup for the Run button

In the repo on GitHub: **Settings → Secrets and variables → Actions → New repository secret**. Add:

| Secret | Value |
| --- | --- |
| `GOOGLE_API_KEY` | The same value as in your local `.env` |
| `SHEET_ID` | The same value as in your local `.env` |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | The **whole contents** of the service account `.json` file in `.secrets/` |
| `CONTACT_EMAIL` | Optional: an email shown to websites the script visits |

## Sheet button (run it from inside the Google Sheet)

Once set up, the sheet gets a **Lead Finder** menu: fill in Targets, click **Lead Finder → Find leads now**, and new
leads appear in Maps Leads about 5–10 minutes later. It starts the GitHub workflow above, so nobody's computer needs
to be on. The code is in [`apps_script/Code.gs`](apps_script/Code.gs).

| Menu item | What it does |
| --- | --- |
| Find leads now | Searches all active targets and writes to the sheet |
| Test run (writes nothing) | Same, but nothing is written |
| Check last run | Shows whether the last run is still going, finished or failed |
| Set GitHub token | One-time setup (below) |

One click spends at most 3 Google requests per active target, capped at 60.

While a run is going, **Run status** (column H of the Targets tab) says how long it's been running, and changes
to ✅ or ❌ when it ends. It updates every minute, even with the sheet closed.

### One-time setup

1. Add the GitHub secrets from **One-time setup for the Run button** above. The button can't work without them.
2. **Create a GitHub token** that can only start this workflow: GitHub → your photo → **Settings → Developer
   settings → Personal access tokens → Fine-grained tokens → Generate new token**.
   - Name: `lead-finder sheet button`. Expiration: the longest allowed.
   - Repository access: **Only select repositories** → `lead-finder`.
   - Permissions → Repository permissions → **Actions: Read and write**. Nothing else.
   - **Generate token** and copy it (it's shown once).
3. **Add the script to the sheet**: open the sheet → **Extensions → Apps Script** → delete what's in `Code.gs` →
   paste the whole of [`apps_script/Code.gs`](apps_script/Code.gs) → **Save** (💾) → close the tab.
4. **Reload the sheet.** A **Lead Finder** menu appears next to Help.
5. **Lead Finder → Set GitHub token** → Google asks for permission the first time: **Continue** → choose your account
   → **Advanced → Go to … (unsafe)** → **Allow**. It says "unsafe" only because this is your own script, not a
   published app. Then paste the token → **OK**. It should say *Token saved and working*.
6. Optional: a big button on the Targets tab. **Insert → Drawing** → draw a box with the text "Find leads" → **Save
   and close** → click the drawing → **⋮ → Assign script** → type `findLeads` → **OK**.

**Updating the script:** when `apps_script/Code.gs` changes, repeat step 3. If Google asks for permission again on the
next click, allow it the same way as in step 5 (a new version may need one more permission).

The token is stored in the sheet's script settings, which anyone who can edit the sheet could see. That's why it
gets only one permission: it can start this workflow, nothing else. When it expires, create a new one and use
**Set GitHub token** again.

## Run it on your computer

Needs Python 3.13 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                      # install
cp .env.example .env         # then fill in .env (see the comments inside it)
uv run lead-finder check     # test: .env, sheet access, tabs
```

| Command | What it does |
| --- | --- |
| `uv run lead-finder check` | Tests the settings and that the sheet can be opened |
| `uv run lead-finder setup-sheet` | Creates the Targets and Maps Leads tabs (safe to repeat) |
| `uv run lead-finder search "dentists in Pune"` | Prints Google's results for one search. Writes nothing |
| `uv run lead-finder run --dry-run` | Full run, prints the top leads, writes nothing |
| `uv run lead-finder run` | Full run, writes to the sheet |
| `uv run lead-finder run --reaudit` | Also re-checks websites of leads already in the sheet |

Without uv: `.venv/Scripts/python.exe -m lead_finder <command>` (Windows) works the same.

Tests: `uv run pytest`. Lint: `uv run ruff check src tests`.

## Setting up from scratch

1. **Google Cloud project** with billing linked. Enable **Places API (New)**, **PageSpeed Insights API** and
   **Google Sheets API**.
2. **API key**, restricted to Places API (New) + PageSpeed Insights API → `GOOGLE_API_KEY`.
3. **Quota cap** on Places Text Search (≈150 requests/day) and a **budget alert**. The cap is what actually stops
   spending; the alert only emails.
4. **Service account** → download its JSON key into `.secrets/` → `GOOGLE_SERVICE_ACCOUNT_FILE`.
5. **Google Sheet**: create it, copy the ID from its URL (`/d/<ID>/edit`) → `SHEET_ID`. Click **Share** and add the
   service account's email (`client_email` in the JSON) as **Editor**.
6. `uv run lead-finder setup-sheet`, then tick **Active** on a Targets row and run.

## Troubleshooting

| Message | Fix |
| --- | --- |
| `Missing settings in .env` | Fill in the listed values. A file path that's "not found" must match the real file name in `.secrets/` |
| `Can't open sheet … shared (Editor) with …` | Share the sheet with that email as **Editor**, or fix `SHEET_ID` |
| `Tab 'Targets' not found` | Run `setup-sheet` |
| `No active targets` | Tick **Active** on at least one Targets row |
| `stopped early: request limit reached` | Raise `MAX_REQUESTS_PER_RUN` (or *Max requests* on GitHub), or run fewer targets |
| A Places error mentioning the API key or billing | Check the key's API restrictions and that billing is linked |

## Safety

- `.env` and `.secrets/` are never committed (see `.gitignore`). Never paste keys into code, issues or chat.
- If a key leaks: delete it in Google Cloud → Credentials, create a new one, and update `.env` and the GitHub
  secret.
- The script respects `robots.txt` and visits at most 5 websites at a time. Mark businesses that ask not to be
  contacted as **Not interested**.
