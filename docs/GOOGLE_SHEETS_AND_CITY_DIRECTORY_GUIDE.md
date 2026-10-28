# Zero-Cost Google Sheets Curation, 9-Tab Moderation Workbook & City Directory Batch Auditor

**Last Updated:** October 25, 2026  
**Related Documentation:** [Chapter 8 - Crowdsourced Moderation Pipeline](08-crowdsourcing-and-admin-operations.md) | [Chapter 10 - Master Sources, Integrations & Curation Playbooks](10-sources-integrations-and-curation-workflows.md)

This guide explains how **Preservation Houston** administrators manage property date, name, style, architect, photograph, and footprint overrides (such as **`1127 Key St`**, built **`1928`** per Houston City Directories vs. HCAD's rounded placeholder **`1920`**), triage public field observations (`[SITE ACTIVITY OBSERVATION]`), synchronize the Biweekly Planning Commission Plat & 15-Point Property Monitor (`plat-monitor.html`), and run the polite **Houston City Directory (`1866-1926`) Batch Auditor** - all with **$0/month hosting cost** and **no proprietary backend database server to maintain**.

---

## 1. How the Zero-Backend Architecture Works

```text
┌────────────────────────────────────────────────────────────────────────────┐
│  1. PUBLIC & FIELD SUBMISSIONS ("✎ Suggest Correction" / "🚨 Site Alert")  │
│     Visitor clicks a building -> submits Proposed Year, Building Name/AKA, │
│     Style/Architect, Photo URL, Footprint Issue, or Site Activity Alert    │
│     -> POSTs to Google Apps Script Web App (`doPost`)                      │
│     -> Appended to `Pending_Submissions` with status = "Pending"           │
│     (Staff signed into `🔒 Staff Admin` Mode can publish as "Approved")    │
└───────────────────────────────────────┬────────────────────────────────────┘
                                        │
                                        ▼
┌────────────────────────────────────────────────────────────────────────────┐
│  2. PRESERVATION HOUSTON STAFF MODERATION (`Admin_Hub_&_Quick_Filter`)     │
│     Staff reviews evidence link (City Directory, Sanborn Map, Deed, Photo) │
│     via 12 Multi-Admin Saved Filter Views + 1-click Atlas/HCAD links       │
│     -> Changes Column A (`status`) dropdown from "Pending" to "Approved"   │
│     -> `Approved_Edits` (`=FILTER(...)`) updates automatically             │
└───────────────────────────────────────┬────────────────────────────────────┘
                                        │
                                        ▼
┌────────────────────────────────────────────────────────────────────────────┐
│  3. LIVE MAP OVERRIDE LAYER & TWO-WAY PLAT/WATCHLIST CLOUD SYNC            │
│     - Browser fetches `curated_overrides.json` + Published `Approved_Edits`│
│       CSV (`doGet` / published CSV endpoint, `cache: "no-store"`)          │
│     - Overridden HCAD accounts update polygon color, 3D extrusion, era     │
│       filter, search index, and Inspector ("✓ Verified by PH" badge)       │
│     - `plat-monitor.html` syncs Plat Reviews & 15-Point Watchlist logs     │
│       two-way with `Plat_&_Watchlist_Sync` for authenticated Sheet Editors │
└────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Complete 9-Tab Workbook Architecture

The Preservation Houston Moderation & Admin Google Sheet (*Houston Building Atlas - Curated Overrides & Moderation*) is organized into **9 purpose-built tabs** provisioned by [`pipeline/atlas_pipeline/setup_admin_sheet.py`](../pipeline/atlas_pipeline/setup_admin_sheet.py):

| Tab # | Tab Name | Role & Access |
| :---: | :--- | :--- |
| **0** | **`Admin_Hub_&_Quick_Filter`** | Executive telemetry dashboard (`COUNTIF`/`COUNTIFS` queue counters), 1-click Quick Links (`Live Atlas`, `Biweekly Plat Monitor`, `Plat Monitoring Guide Doc`, `HPL City Directories`, `HCAD`, `HAIF`, `HPO`, `Good Brick`, `Standalone TPIA Sheet`, `Published CSV`, and **GitHub Issue #1 External Link Health Report**), plus an interactive `QUERY` filter workbench (`Row 18` dropdowns). |
| **1** | **`Pending_Submissions`** | Primary moderation ledger (`Columns A:V`). Receives all public crowdsourced corrections, footprint bug reports, `[SITE ACTIVITY OBSERVATION]` alerts, City Directory batch candidates, and direct Staff Admin approvals. Includes **12 Pre-Built Saved Filter Views** (`Data -> Filter views`) so multiple admins can triage simultaneously without colliding. |
| **2** | **`Approved_Edits`** | Live formula-filtered view (`=IFERROR(FILTER(...))`) exposing **only** rows where `status = "Approved"` (excluding submitter PII/notes in `L:N`). Published as a read-only CSV consumed on page load by `app/js/curatedEdits.js`. |
| **3** | **`Instructions_&_Apps_Script`** | Complete in-sheet Administrator Manual (`SOP 1` through `SOP 13`), critical data-integrity guardrails, the private **Sheet Editor Admin Passkey in Cell `B6`** (*never move Cell `B6` and never keep the Sheet Restricted-off*), and the canonical Google Apps Script `Code.gs` source. |
| **4** | **`Good_Brick_Awards`** | Master ledger of Preservation Houston Good Brick, Martha Peterson, Stewart Title, and Pier & Beam awards (`1979-2026`, `359` place-based awards across `338` sites), consumed by `good_brick_awards.py`. |
| **5** | **`Building_Photos`** | Curated historical and modern architectural photography ledger (`288` photos across HABS, HPO, HMRC, HAIF, Wikimedia Commons, and approved community submissions), synced via `seed_building_photos.py`. |
| **6** | **`Missing_Landmark_PDFs_TPIA`** | Tracks the `61` post-2016 City of Houston Designated Landmarks missing official HPO Designation Report PDFs on `archaeology.houstontx.gov` (paired with a standalone shareable TPIA request Google Sheet). |
| **7** | **`Plat_&_Watchlist_Sync`** | Two-way cloud sync ledger for [`app/plat-monitor.html`](../app/plat-monitor.html), storing staff biweekly Plat Reviews (`PLAT_REV:...`) and 15-Point Endangered Property Watchlist findings (`WATCH_PROP:...`). |
| **8** | **`PC_Agenda_Current_Cycle`** | Automated snapshot mirror of the active biweekly Houston Planning Commission plat & Section K Development Plat Variance (`DPV`) cycle. |

---

## 3. `Pending_Submissions` Full 22-Column Schema (`A1:V1`)

| Col | Header | Description | Example (`1127 Key St`) |
| :---: | :--- | :--- | :--- |
| **A** | `status` | `Pending`, `Approved`, `Needs Info`, or `Rejected` (Data Validation dropdown) | `Approved` |
| **B** | `hcad_num` | **13-character text string** HCAD Account Number (*never format as plain number; ~45% start with `0`*) | `0621100000014` |
| **C** | `address` | Street address | `1127 KEY ST` |
| **D** | `year_built` | Verified construction year (`1836-2026`), **`0` / `Vacant` if demolished & lot currently vacant** (hides ghost building footprint live via `suppress_only: true`), or blank if name/photo/shape-only edit | `1928` |
| **E** | `original_hcad_year` | Original HCAD CAMA year built | `1920` |
| **F** | `historic_district` | Enclosing Historic District name (optional) | `Norhill Historic District` |
| **G** | `contributing` | `Contributing` / `Noncontributing` (optional) | `Contributing` |
| **H** | `source_type` | `Houston City Directory`, `Sanborn Fire Insurance Map`, `HMRC Contractor Portfolio / TGCA Bulletin`, `On-Site Visual Inspection`, etc. | `Houston City Directory` |
| **I** | `source_citation` | Archival volume/page citation, footprint defect description, or `[SITE ACTIVITY OBSERVATION]` field notes | `1928 Houston City Directory (Morrison & Fourmy); lot unimproved through 1926 directory` |
| **J** | `source_url` | Direct URL to digitized archive page or contributed photograph | `https://cdm17006.contentdm.oclc.org/digital/collection/citydir/search/searchterm/1127%20Key` |
| **K** | `verified_by` | Reviewer / archivist credit (auto-filled when approved via `🔒 Staff Admin`) | `Preservation Houston Archival Review` |
| **L** | `submitted_by` | Submitter name/email (*stays private inside the Restricted Sheet; excluded from `Approved_Edits`*) | `Community Researcher` |
| **M** | `submitted_at` | ISO date (`YYYY-MM-DD`) | `2026-10-05` |
| **N** | `notes` | Internal reviewer triage notes (*excluded from `Approved_Edits`*) | `Verified via HPL City Directories` |
| **O** | `building_name` | Canonical historic or architectural building name (optional) | `Cheek-Neal Coffee Building` |
| **P** | `alt_names` | Semicolon-separated historical or colloquial `AKA` aliases (optional) | `Sampson Street Lofts; Maxwell House Plant` |
| **Q** | `bld_style` | Architectural style override (optional) | `1920s Craftsman Bungalow` |
| **R** | `architect` | Original architect or master builder override (optional) | `Alfred C. Finn` |
| **S** | `triage_category` | Auto-computed `ARRAYFORMULA` queue classifier (`🚨 Action Needed: Community Review`, `📐 Footprint / Geometry Issue`, `🏷️ Building Name / Alias`, `📷 Photo Contribution`, `📚 Archival Batch Candidate`, `✅ Approved & Live`, etc.) | `✅ Approved & Live` |
| **T** | `open_in_atlas` | Auto-computed `ARRAYFORMULA` 1-click `=HYPERLINK(".../#hcad=" & B2, "🗺️ Open in Atlas ↗")` | `🗺️ Open in Atlas ↗` |
| **U** | `inspect_hcad` | Auto-computed `ARRAYFORMULA` 1-click HCAD parcel lookup link | `🏛️ HCAD ↗` |
| **V** | `row_num` | Auto-computed `ARRAYFORMULA` row index used by `Admin_Hub_&_Quick_Filter` deep-jump links | `2` |

---

## 4. Authenticated Google Apps Script Webhook (`Code.gs`)

Unlike naive public webhooks, the canonical Google Apps Script stored in `Instructions_&_Apps_Script` (`setup_admin_sheet.py`) keeps the Google Sheet **strictly Restricted** to Preservation Houston staff editors while supporting three secure operations:

1. **`doGet(e)` / Published `Approved_Edits` CSV:** Exposes *only* the `Approved_Edits` tab (`status = "Approved"`, zero submitter PII) as read-only CSV so public browsers load verified edits immediately.
2. **Public `doPost(e)` (`Pending` Enforcement):** Unauthenticated public submissions from `#correction-modal` are always forced to `status = "Pending"` regardless of request payload fields, and written below the last populated row in Column `B` (`hcad_num`) so `ARRAYFORMULA` helper columns `S:V` never shift `appendRow`.
3. **Sheet-Editor Verified `🔒 Staff Admin` & `Plat_&_Watchlist_Sync` (`isAuthorizedSheetEditor_`):** When a staff member signs into `🔒 Staff Admin` in the Atlas or `Staff Sign-In` in `plat-monitor.html`, `isAuthorizedSheetEditor_()` verifies both:
   - That the user's email address is the **Spreadsheet Owner or an explicit Google Sheet Editor** (`ss.getEditors()`), **and**
   - Either a Google-signed OAuth ID token (`oauth2.googleapis.com/tokeninfo`) or the private **Sheet Editor Passkey stored in `Instructions_&_Apps_Script!B6`**.

*(To deploy or update `Code.gs`, copy the complete script from `Instructions_&_Apps_Script` in the Google Sheet or from [`pipeline/atlas_pipeline/setup_admin_sheet.py`](../pipeline/atlas_pipeline/setup_admin_sheet.py), deploy as **Web app -> Execute as: Me -> Who has access: Anyone**, and ensure the `/exec` URL matches `"submissionWebhookUrl"` in [`app/public/data/curated_overrides.json`](../app/public/data/curated_overrides.json)).*

---

## 5. Polite Houston City Directory (`1866-1926`) Batch Auditor & Pre-Compiled Index

The Houston Public Library Digital Archives hosts **`39` digitized Houston City Directories (`1866-1926`)** on OCLC ContentDM (`https://cdm17006.contentdm.oclc.org/digital/collection/citydir/search`).

1. **Instant In-App Research Kit (`citydir_index.json`):** [`pipeline/atlas_pipeline/build_house_history_index.py`](../pipeline/atlas_pipeline/build_house_history_index.py) pre-compiles `2,859` verified OCR sightings, `4,682` pre-screened negative bounds, and `16` historic street-name equivalences (`Emancipation <-> Dowling`, `Jensen <-> Odin/Carr`, etc.) into `app/public/data/citydir_index.json` for instant display in every property's `History` tab.
2. **Polite Neighborhood Batch Auditor (`city_directory_audit.py`):** Cross-references suspect rounded-decade HCAD dates (`1890`, `1900`, `1910`, `1920`, `1930`) against ContentDM using a persistent SQLite cache (`pipeline/cache/citydir_cache.sqlite`), `--delay 2.0` polite rate limiting, browser `User-Agent` headers, and `^exact^and` contiguous phrase matching:
   - **`EARLIER_THAN_HCAD`:** The address appears in an earlier Houston City Directory than HCAD's `year_built` (with direct ContentDM page URL and OCR snippet).
   - **`ABSENT_THROUGH_1926_PREMATURE_HCAD_1920`:** HCAD lists `1920`, neighboring homes on the same block (`±2`, `±4` house numbers, e.g. `1123`, `1125`, `1129 Key St`) appear in the `1920-1926` directories, yet the target address (`1127 Key St`) is absent through `1926` - proving the house was built *after* `1926` (`c. 1927-1929`, such as `1928`) and HCAD defaulted to `1920`.

```bash
# Audit a single property address
uv run --directory pipeline python -m atlas_pipeline.city_directory_audit \
  --address "1127 KEY ST" \
  --delay 2.0

# Audit a batch of rounded-decade properties in a Historic District
uv run --directory pipeline python -m atlas_pipeline.city_directory_audit \
  --district "Norhill" \
  --limit 20 \
  --delay 2.0 \
  --output-csv cache/norhill_audit_candidates.csv
```

---

## 6. Monitoring External Archive Link Health from the Google Sheet ([GitHub Issue #1](https://github.com/davemorris-gcp/houston-building-atlas/issues/1))

To ensure Preservation Houston staff and public researchers never encounter silent `HTTP 404` or `Maintenance.aspx` soft-404 links across external municipal, county, state, and university archives:

1. **Automated Weekly CI Link Monitor ([GitHub Issue #1](https://github.com/davemorris-gcp/houston-building-atlas/issues/1)):**
   - Every Monday at `8:00 AM Central` (`13:00 UTC`), GitHub Actions runs [`.github/workflows/external-link-health.yml`](../.github/workflows/external-link-health.yml), exercising all `45` stratified property archetypes in Headless Chromium and checking `776` unique external URLs across `55` domains.
   - Updates **[GitHub Issue #1: External Archive Link Health Report (Automated CI Monitor)](https://github.com/davemorris-gcp/houston-building-atlas/issues/1)** in place and automatically reopens + comments on Issue `#1` if any broken or moved link is detected.
2. **One-Click Access Inside the Moderation Google Sheet:**
   - **`Admin_Hub_&_Quick_Filter` Tab (Cell `G14:H14` under *Quick Links & External Tools*):** Click **`🩺 Open Live External Archive Link Health Report (Issue #1) ↗`** right above the fold upon opening the workbook.
   - **`Instructions_&_Apps_Script` Tab (Rows `20-21`, Sections `18` & `19`):** Includes **SOP 11 (Automated CI Testing & External Archive Link Health Monitor)** alongside a direct clickable **`=HYPERLINK(...)`** button to **[GitHub Issue #1](https://github.com/davemorris-gcp/houston-building-atlas/issues/1)**.
