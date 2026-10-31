# Zero-Cost Google Sheets Curation, 12-Tab Moderation Workbook & City Directory Batch Auditor

**Last Updated:** October 30, 2026  
**Related Documentation:** [Chapter 8 - Crowdsourced Moderation Pipeline](08-crowdsourcing-and-admin-operations.md) | [Chapter 10 - Master Sources, Integrations & Curation Playbooks](10-sources-integrations-and-curation-workflows.md) | [Preservation Monitoring Automation Architecture](PRESERVATION_MONITORING_AUTOMATION_ARCHITECTURE.md)

This guide explains how **Preservation Houston** administrators manage property date, name, style, architect, photograph, and footprint overrides (such as **`1127 Key St`**, built **`1928`** per Houston City Directories vs. HCAD's rounded placeholder **`1920`**), operate the Milestone 3 **Preservation Staff & Field Workbench** (`#audit=1` Historian Audit Mode, `#polyedit=<hcad>` Zoom-20 Polygon Footprint Editor, and `#survey=1` Mobile Walk-the-Block Field Survey Mode), triage public field observations (`[SITE ACTIVITY OBSERVATION]`), synchronize the Biweekly Planning Commission Plat & 15-Point Property Monitor (`plat-monitor.html`), and run the polite **Houston City Directory (`1866-1926`) Batch Auditor** - all with **$0/month hosting cost** and **no proprietary backend database server to maintain**.

---

## 1. How the Zero-Backend Architecture Works

```text
┌────────────────────────────────────────────────────────────────────────────┐
│  1. PUBLIC, STAFF & FIELD SUBMISSIONS (`#correction-modal` / Workbench)    │
│     Visitor or Staff submits Proposed Year, Building Name/AKA, Style,      │
│     Architect, Photo URL, Zoom-20 Polygon Footprint Override (`Col W`),    │
│     Site Activity Alert, or Walk-the-Block Field Survey (`#survey=1`)      │
│     -> POSTs to Google Apps Script Web App (`doPost`)                      │
│     -> Appended to `Pending_Submissions` (`A:W`) or upserted into          │
│        `Field_Surveys` (`gid=913405001`, isolated from live year changes)  │
└───────────────────────────────────────┬────────────────────────────────────┘
                                        │
                                        ▼
┌────────────────────────────────────────────────────────────────────────────┐
│  2. PRESERVATION HOUSTON STAFF MODERATION (`Admin_Hub_&_Quick_Filter`)     │
│     Staff reviews evidence link (City Directory, Sanborn Map, Deed, Photo) │
│     via 12 Multi-Admin Saved Filter Views + 1-click Atlas/HCAD links       │
│     -> Changes Column A (`status`) dropdown from "Pending" to "Approved"   │
│     -> `Approved_Edits` (`=FILTER(...)`, Columns `A:P`) updates instantly  │
└───────────────────────────────────────┬────────────────────────────────────┘
                                        │
                                        ▼
┌────────────────────────────────────────────────────────────────────────────┐
│  3. LIVE MAP OVERRIDE LAYER & TWO-WAY PLAT/WATCHLIST CLOUD SYNC            │
│     - Browser fetches `curated_overrides.json` + Published `Approved_Edits`│
│       CSV (`doGet` / published CSV endpoint, `cache: "no-store"`)          │
│     - Overridden HCAD accounts update polygon footprint, color, 3D height, │
│       era filter, search index, and Inspector ("✓ Verified by PH" badge)   │
│     - `plat-monitor.html` syncs Plat Reviews & 15-Point Watchlist logs     │
│       two-way with `Plat_&_Watchlist_Sync` for authenticated Sheet Editors │
└────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Complete 12-Tab Workbook Architecture

The Preservation Houston Moderation & Admin Google Sheet (*Houston Building Atlas - Curated Overrides & Moderation*) is organized into **12 purpose-built tabs** provisioned by [`pipeline/atlas_pipeline/setup_admin_sheet.py`](../pipeline/atlas_pipeline/setup_admin_sheet.py), [`pipeline/atlas_pipeline/audit_historical_accuracy.py`](../pipeline/atlas_pipeline/audit_historical_accuracy.py), [`pipeline/atlas_pipeline/sync_lost_houston_sheet.py`](../pipeline/atlas_pipeline/sync_lost_houston_sheet.py), and [`pipeline/atlas_pipeline/sync_field_surveys_sheet.py`](../pipeline/atlas_pipeline/sync_field_surveys_sheet.py):

| Tab # | Tab Name | Role & Access |
| :---: | :--- | :--- |
| **0** | **`Admin_Hub_&_Quick_Filter`** | Executive telemetry dashboard (`COUNTIF`/`COUNTIFS` queue counters including Row `19` `Field_Surveys` queue metrics), 1-click Quick Links (`Live Atlas`, `Historian Rapid Audit (#audit=1)`, `Mobile Field Survey (#survey=1)`, `Biweekly Plat Monitor`, `Plat Monitoring Guide Doc`, `HPL City Directories`, `HCAD`, `HAIF`, `HPO`, `Good Brick`, `Standalone TPIA Sheet`, `Published CSV`, `Historical_Audit_Queue`, `Lost_Houston_Catalog`, `Field_Surveys`, and **GitHub Issue #1 External Link Health Report**), plus an interactive `QUERY` filter workbench (`Row 22` dropdowns). |
| **1** | **`Pending_Submissions`** | Primary moderation ledger (`Columns A:W`). Receives all public crowdsourced corrections, custom polygon footprint overrides (`Column W` `geometry_geojson` + `[GEOMETRY_GEOJSON:{...}]` envelope), footprint bug reports (including `Building demolished - lot currently vacant`), `[SITE ACTIVITY OBSERVATION]` alerts, 1-click `PHOTO_MISATTRIBUTION_FLAG` / `CLAIM_DISPUTE_FLAG` / `NARRATIVE_ERROR_FLAG` tickets, City Directory / Historian Audit decisions, fallback `FIELD_SURVEY_OBSERVATION` envelopes, and direct Staff Admin approvals. Includes **12 Pre-Built Saved Filter Views** (`Data -> Filter views`) so multiple admins can triage simultaneously without colliding. |
| **2** | **`Approved_Edits`** | Live formula-filtered 16-column view (`Columns A:P`, `=IFERROR(FILTER(...))`) exposing **only** rows where `status = "Approved"` (excluding submitter PII/notes in `L:N`, while including `P1 = geometry_geojson` for zero-rebuild custom polygon footprints). Published as a read-only CSV consumed on page load by `app/js/curatedEdits.js`. |
| **3** | **`Instructions_&_Apps_Script`** | Complete in-sheet Administrator Manual (`SOP 1` through `SOP 15`), critical data-integrity guardrails, the private **Sheet Editor Admin Passkey in Cell `B6`** (*never move Cell `B6` and never keep the Sheet Restricted-off*), and the canonical Google Apps Script `Code.gs` source (`B5`). |
| **4** | **`Good_Brick_Awards`** | Master ledger of Preservation Houston Good Brick, Martha Peterson, Stewart Title, and Pier & Beam awards (`1979-2026`, `359` place-based awards across `338` sites), consumed by `good_brick_awards.py`. |
| **5** | **`Building_Photos`** | Curated historical and modern architectural photography ledger (`338` verified photos: `154` standing-building photos and `184` Lost Houston archival images across `133` demolished sites from HABS, HPO, HMRC, HAIF, Portal to Texas History, Wikimedia Commons, and community submissions), ingested non-destructively for Lost Houston backfill via `sync_lost_houston_sheet.py --ingest-building-photos-csv` (never re-run one-time bootstrap `seed_building_photos.py`, which only carries the 33 initial seed photos). |
| **6** | **`Missing_Landmark_PDFs_TPIA`** | Tracks the `61` post-2016 City of Houston Designated Landmarks missing official HPO Designation Report PDFs on `archaeology.houstontx.gov` (paired with a standalone shareable TPIA request Google Sheet). |
| **7** | **`Plat_&_Watchlist_Sync`** | Two-way cloud sync ledger for [`app/plat-monitor.html`](../app/plat-monitor.html), storing staff biweekly Plat Reviews (`PLAT_REV:<cycle>:<app>`, carrying 9-field cross-cycle provenance via `carriedFields` & `humanEditedFields`) and human-confirmed 15-Point Endangered Property Watchlist dossiers (`WATCH_PROP:<id>`, reconciled with daily 8-agency `[AUTO-SWEEP]` findings via field-level ownership partitioning, preserving `broker`, lossless ISO `loggedAt`/`statusChangedAt`/`archivedAt` timestamps for `What's New & Daily Log`, and timestamped `REMOVED_WATCHLIST_TOMBSTONE` / `REMOVED_TIER3_TOMBSTONE` deletions). |
| **8** | **`PC_Agenda_Current_Cycle`** | Automated snapshot mirror of the active biweekly Houston Planning Commission plat, Section I.K Development Plat Variance (`DPV`), and Section III/IV Public Hearing cycle. |
| **9** | **`Historical_Audit_Queue`** | Top `250` prioritized cross-dataset contradictions in the workbook (paired with the compact `2,600`-item in-browser `app/public/data/historical_audit_queue.json` feeding `#audit=1`: `SUSPECTED_UNMARKED_LANDMARK_DEMOLITION`, `ARCHITECT_CAREER_WINDOW_CHECK`, `LANDMARK_VS_ATLAS_YEAR_MISMATCH`, `ADDRESS_STREET_MISMATCH`, `CITY_DIRECTORY_EARLIER_THAN_HCAD`, `CITY_DIRECTORY_ABSENT_THROUGH_1926`) generated by `audit_historical_accuracy.py` for structured staff verification. |
| **10** | **`Lost_Houston_Catalog`** | Read-only 34-column reference catalog (`gid=824622001`, protected via `warningOnly` sheet guard) mapping all `622` Milestone 2 demolished structures across 5 provenance cohorts (`85` curated landmarks, `7` COH + `15` NRHP designated landmarks, `205` vanished *1915 Red Book* sites, `310` `2020-2026` pre-1940 teardowns), spatially joined with Council District, 1937 HOLC Grade, COH Historic/Heritage District, `133` photographed sites (`489` unphotographed backfill queue), and **7 Pre-Built Saved Filter Views** synced by `sync_lost_houston_sheet.py`. |
| **11** | **`Field_Surveys`** | Dedicated 20-column Mobile Walk-the-Block observation ledger (`gid=913405001`, `Columns A:T`: raw observation columns `A:R` `survey_id` .. `gps_lat_lng_acc` formatted as Plain Text on `survey_id`/`hcad_num`/`observed_year_or_demo_note`, plus single-cell `ARRAYFORMULA` links `S1:T1` `open_in_atlas` / `inspect_hcad`). Synced non-destructively by `sync_field_surveys_sheet.py` with **6 Pre-Built Saved Filter Views** (`All Field Surveys`, `Unreviewed Queue`, `Critical Demolition / Vacant Threat`, `Significant Alterations (D/F Integrity)`, `Proposed Contributing Upgrades`, `Needs Photo Follow-Up`). Raw `FIELD_SURVEY_OBSERVATION` records never auto-mutate live standing building years until curated by staff (**SOP 15**). |

---

## 3. `Pending_Submissions` Full 23-Column Schema (`A1:W1`)

| Col | Header | Description | Example (`1127 Key St`) |
| :---: | :--- | :--- | :--- |
| **A** | `status` | `Pending`, `Approved`, `Needs Info`, or `Rejected` (Data Validation dropdown) | `Approved` |
| **B** | `hcad_num` | **13-character text string** HCAD Account Number, or scoped `<13-digit-hcad>#<slug>` multi-structure campus/accessory ID (*never format as plain number; ~45% start with `0`*) | `0621100000014` |
| **C** | `address` | Street address | `1127 KEY ST` |
| **D** | `year_built` | Verified construction year (`1836-2026`), **`0` / `Vacant` if demolished & lot currently vacant** (hides ghost building footprint live via `suppress_only: true`), or blank if name/photo/shape-only edit | `1928` |
| **E** | `original_hcad_year` | Original HCAD CAMA year built | `1920` |
| **F** | `historic_district` | Enclosing Historic District name (optional) | `Norhill Historic District` |
| **G** | `contributing` | `Contributing` / `Noncontributing` (optional) | `Contributing` |
| **H** | `source_type` | `Houston City Directory`, `Sanborn Fire Insurance Map`, `Satellite / GIS Footprint Edit`, `HMRC Contractor Portfolio / TGCA Bulletin`, `On-Site Visual Inspection`, `FIELD_SURVEY_OBSERVATION`, etc. | `Houston City Directory` |
| **I** | `source_citation` | Archival volume/page citation, footprint defect description, `[GEOMETRY_GEOJSON:{...}]` compatibility envelope, or `[SITE ACTIVITY OBSERVATION]` field notes | `1928 Houston City Directory (Morrison & Fourmy); lot unimproved through 1926 directory` |
| **J** | `source_url` | Direct URL to digitized archive page or contributed photograph | `https://cdm17006.contentdm.oclc.org/digital/collection/citydir/search/searchterm/1127%20Key` |
| **K** | `verified_by` | Reviewer / archivist credit (auto-filled when approved via `🔒 Staff Admin`) | `Preservation Houston Archival Review` |
| **L** | `submitted_by` | Submitter name/email (*stays private inside the Restricted Sheet; excluded from `Approved_Edits`*) | `Community Researcher` |
| **M** | `submitted_at` | ISO date (`YYYY-MM-DD`) | `2026-10-05` |
| **N** | `notes` | Internal reviewer triage notes (*excluded from `Approved_Edits`*) | `Verified via HPL City Directories` |
| **O** | `building_name` | Canonical historic or architectural building name (optional) | `Cheek-Neal Coffee Building` |
| **P** | `alt_names` | Semicolon-separated historical or colloquial `AKA` aliases (optional) | `Sampson Street Lofts; Maxwell House Plant` |
| **Q** | `bld_style` | Architectural style override (optional) | `1920s Craftsman Bungalow` |
| **R** | `architect` | Original architect or master builder override (optional) | `Alfred C. Finn` |
| **S** | `triage_category` | Auto-computed `ARRAYFORMULA` queue classifier (`🚨 Action Needed: Community Review`, `📐 Footprint / Geometry Issue`, `📋 Field Survey Observation`, `🏷️ Building Name / Alias`, `📷 Photo Contribution`, `📚 Archival Batch Candidate`, `✅ Approved & Live`, etc.) | `✅ Approved & Live` |
| **T** | `open_in_atlas` | Auto-computed `ARRAYFORMULA` 1-click `=HYPERLINK(".../#hcad=" & B2, "🗺️ Open in Atlas ↗")` | `🗺️ Open in Atlas ↗` |
| **U** | `inspect_hcad` | Auto-computed `ARRAYFORMULA` 1-click HCAD parcel lookup link | `🏛️ HCAD ↗` |
| **V** | `row_num` | Auto-computed `ARRAYFORMULA` row index used by `Admin_Hub_&_Quick_Filter` deep-jump links | `2` |
| **W** | `geometry_geojson` | Validated simple CCW `Polygon` GeoJSON (`<= 64` vertices, `<= 49,000` chars, rounded to 6 decimal places) authored in the `#polyedit=<hcad>` Staff Polygon Editor and projected onto `Approved_Edits` Column `P` (`geometry_geojson`) | `{"type":"Polygon","coordinates":[[[-95.383412,29.791845],...]]}` |

---

## 4. Authenticated Google Apps Script Webhook (`Code.gs`)

Unlike naive public webhooks, the canonical Google Apps Script stored in `Instructions_&_Apps_Script` (`setup_admin_sheet.py`) keeps the Google Sheet **strictly Restricted** to Preservation Houston staff editors while supporting four secure operations:

1. **`doGet(e)` / Published `Approved_Edits` CSV:** Exposes *only* the `Approved_Edits` tab (`status = "Approved"`, zero submitter PII, Columns `A:P` including `P1 = geometry_geojson`) as read-only CSV so public browsers load verified edits and custom polygon footprints immediately.
2. **Public `doPost(e)` (`Pending` Enforcement):** Unauthenticated public submissions from `#correction-modal` or `#polyedit` are always forced to `status = "Pending"` regardless of request payload fields, sanitized against spreadsheet formula injection (`=`, `+`, `-`, `@`, `\t`, `\r`), and written below the last populated row in Column `B` (`hcad_num`) across `Columns A:W` so `ARRAYFORMULA` helper columns `S:V` never shift `appendRow`.
3. **Walk-the-Block `field_survey` Routing (`Field_Surveys` Isolation):** Dedicated `action: "field_survey"` payloads from `#survey=1` upsert deterministically by `survey_id` into `Field_Surveys` (`gid=913405001`, preserving any non-`'Unreviewed'` staff `review_status` on offline retry flushes). If an older deployed Apps Script encounters a fallback `[FIELD_SURVEY_JSON:{...}]` envelope in `Pending_Submissions`, `sync_field_surveys_sheet.py --existing-surveys-csv <Field_Surveys.csv> --ingest-pending-csv <Pending_Submissions.csv> --output-dir <batch_dir>` migrates and merges those rows cleanly while `curatedEdits.js` and `sync_curated_edits.py` skip `FIELD_SURVEY_OBSERVATION` so raw field notes never alter live map years.
4. **Sheet-Editor Verified `🔒 Staff Admin` & `Plat_&_Watchlist_Sync` (`isAuthorizedSheetEditor_`):** When a staff member signs into `🔒 Staff Admin` in the Atlas or `Staff Sign-In` in `plat-monitor.html`, `isAuthorizedSheetEditor_()` verifies both:
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
