# Zero-Cost Google Sheets Curation, Calm 3-Tab Staff Workbook, Cross-Device Roaming Sync & City Directory Batch Auditor

**Last Updated:** November 22, 2026  
**Related Documentation:** [Chapter 8 - Crowdsourced Moderation Pipeline](08-crowdsourcing-and-admin-operations.md) | [Chapter 10 - Master Sources, Integrations & Curation Playbooks](10-sources-integrations-and-curation-workflows.md) | [Preservation Monitoring Automation Architecture](PRESERVATION_MONITORING_AUTOMATION_ARCHITECTURE.md)

This guide explains how **Preservation Houston** administrators manage property date, name, style, architect, photograph, and footprint overrides (such as **`1127 Key St`**, built **`1927`** per Houston City Directories vs. HCAD's rounded placeholder **`1920`**), operate the Milestone 3 **Preservation Staff & Field Workbench** (`#audit=1` Historian Audit Mode, `#polyedit=<hcad>` Zoom-20 Polygon Footprint Editor, and `#survey=1` Mobile Walk-the-Block Field Survey Mode), triage public field observations (`[SITE ACTIVITY OBSERVATION]`), synchronize the Biweekly Planning Commission Plat & 15-Point Property Monitor (`plat-monitor.html`) plus **Cross-Device Per-User Roaming Read/Unread State** (`app/js/userRoamingState.js`), maintain automated daily GitHub JSON backups (`pipeline/backups/workspace_cloud_backup.json`), and run the polite **Houston City Directory (`1866-1926`) Batch Auditor** - all with **$0/month hosting cost** and **no proprietary backend database server to maintain**.

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
│  3. LIVE MAP OVERRIDE LAYER, KEY-UPSERTED PLAT/WATCHLIST FEED & ROAMING    │
│     - Browser fetches `curated_overrides.json` + Published `Approved_Edits`│
│       CSV (`doGet` / published CSV endpoint, `cache: "no-store"`)          │
│     - Overridden HCAD accounts update polygon footprint, color, 3D height, │
│       era filter, search index, and Inspector ("✓ Verified by PH" badge)   │
│     - `plat-monitor.html` upserts `PLAT_REV:*` & `WATCH_PROP:*` rows in    │
│       place into `Plat_&_Watchlist_Sync` (`?feed=plat_sync`, CacheService) │
│     - `userRoamingState.js` syncs per-staff read/unread watermarks,        │
│       `Keep Unread` pins, UI preferences & `#audit=1` skips across         │
│       work and home computers (`_Staff_User_State` + `PropertiesService`)  │
│     - Daily GitHub Actions runs `backup_workspace_state.py` to snapshot    │
│       non-PII Workspace state into `pipeline/backups/workspace_cloud_backup.json` │
└────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Calm 3-Visible-Tab Staff Experience (Over the 13-Tab + Hidden Sync Workbook Architecture)

To prevent spreadsheet fatigue over the **13-Tab** Moderation Workbook architecture (plus `HAHC_Landmark_Candidates`, `_Staff_User_State`, and `_CityDir_Batch_Archive`), the Preservation Houston Moderation & Admin Google Sheet (*Houston Building Atlas - Curated Overrides & Moderation*) displays **only 3 calm human-facing tabs** in the bottom tab bar by default (`Admin_Hub_&_Quick_Filter`, `Pending_Submissions` containing ~150 human/curated rows, and `Field_Surveys`), while keeping machine sync ledgers, formula mirrors, batch archives, and reference catalogs neatly tucked under **`View -> Hidden sheets`** (`SheetProperties.hidden = true`). The workbook is provisioned and maintained by [`pipeline/atlas_pipeline/setup_admin_sheet.py`](../pipeline/atlas_pipeline/setup_admin_sheet.py), [`pipeline/atlas_pipeline/backup_workspace_state.py`](../pipeline/atlas_pipeline/backup_workspace_state.py), [`pipeline/atlas_pipeline/audit_historical_accuracy.py`](../pipeline/atlas_pipeline/audit_historical_accuracy.py), [`pipeline/atlas_pipeline/sync_lost_houston_sheet.py`](../pipeline/atlas_pipeline/sync_lost_houston_sheet.py), [`pipeline/atlas_pipeline/sync_field_surveys_sheet.py`](../pipeline/atlas_pipeline/sync_field_surveys_sheet.py), and [`pipeline/atlas_pipeline/build_1927_cutoff_triage.py`](../pipeline/atlas_pipeline/build_1927_cutoff_triage.py):

| Tab # | Tab Name | Visibility & Role |
| :---: | :--- | :--- |
| **0** | **`Admin_Hub_&_Quick_Filter`** | **Visible (Human Tab 1/3).** Executive telemetry dashboard (`COUNTIF`/`COUNTIFS` queue counters including Rows `19-20` `Field_Surveys` and `1927_Cutoff_Triage` queue metrics), 1-click Quick Links (`Live Atlas`, `Historian Rapid Audit (#audit=1)`, `1927 Cutoff Street-Block Walk (#audit=1&auditCat=1927_CUTOFF_TRIAGE&auditSort=street_block)`, `Mobile Field Survey (#survey=1)`, `Biweekly Plat Monitor`, `Plat Monitoring Guide Doc`, `HPL City Directories`, `HCAD`, `HAIF`, `HPO`, `Good Brick`, `Standalone TPIA Sheet`, `Published CSV`, `Historical_Audit_Queue`, `Lost_Houston_Catalog`, `Field_Surveys`, `1927_Cutoff_Triage`, and **GitHub Issue #1 External Link Health Report**), plus an interactive `QUERY` filter workbench (`Row 23` dropdowns). |
| **1** | **`Pending_Submissions`** | **Visible (Human Tab 2/3).** Clean human moderation ledger (`Columns A:W`, ~150 curated & community rows). Receives public crowdsourced corrections, custom polygon footprint overrides (`Column W` `geometry_geojson` + `[GEOMETRY_GEOJSON:{...}]` envelope), footprint bug reports (including `Building demolished - lot currently vacant`), `[SITE ACTIVITY OBSERVATION]` alerts, 1-click `PHOTO_MISATTRIBUTION_FLAG` / `CLAIM_DISPUTE_FLAG` / `NARRATIVE_ERROR_FLAG` tickets, Staff Admin approvals, and verified Historian Audit promotions. Automated `PLAT_MONITOR_REVIEW` and `PROPERTY_WATCHLIST_UPDATE` sync payloads no longer append here (they upsert by key in `Plat_&_Watchlist_Sync`), and unreviewed automated `City Directory Batch Auditor` rows live in hidden `_CityDir_Batch_Archive`. Includes **12 Pre-Built Saved Filter Views** (`Data -> Filter views`). |
| **11** | **`Field_Surveys`** | **Visible (Human Tab 3/3).** Dedicated 20-column Mobile Walk-the-Block observation ledger (`gid=913405001`, `Columns A:T`: raw observation columns `A:R` `survey_id` .. `gps_lat_lng_acc` formatted as Plain Text on `survey_id`/`hcad_num`/`observed_year_or_demo_note`, plus single-cell `ARRAYFORMULA` links `S1:T1` `open_in_atlas` / `inspect_hcad`). Synced non-destructively by `sync_field_surveys_sheet.py` with **6 Pre-Built Saved Filter Views**. Raw `FIELD_SURVEY_OBSERVATION` records never auto-mutate live standing building years until curated by staff (**SOP 15**). |
| **2** | **`Approved_Edits`** | **Hidden Formula Feed.** Live formula-filtered 16-column view (`Columns A:P`, `=IFERROR(FILTER(...))`) exposing **only** rows where `status = "Approved"` (excluding submitter PII/notes in `L:N`, while including `P1 = geometry_geojson` for zero-rebuild custom polygon footprints). Published as a read-only CSV consumed on page load by `app/js/curatedEdits.js`. |
| **3** | **`Instructions_&_Apps_Script`** | **Hidden Admin Config.** Complete in-sheet Administrator Manual (`SOP 1` through `SOP 16`), critical data-integrity guardrails, the private **Sheet Editor Admin Passkey in Cell `B6`** (*never move Cell `B6` and never keep the Sheet Restricted-off*), and the canonical Google Apps Script `Code.gs` source (`B5`). |
| **4-6** | **`Good_Brick_Awards`**, **`Building_Photos`**, **`Missing_Landmark_PDFs_TPIA`** | **Hidden Reference Ledgers.** Master ledgers for Good Brick Awards (`1979-2026`, `359` place-based awards), curated architectural photography (`building_photos.json`), and the `27` remaining TPIA-tracked unpublished City Landmarks. |
| **7** | **`Plat_&_Watchlist_Sync`** | **Hidden Key-Upserted Cloud Sync Ledger (`?feed=plat_sync`).** Two-way cloud sync ledger for [`app/plat-monitor.html`](../app/plat-monitor.html), upserting each staff Plat Review (`PLAT_REV:<cycle>:<app>`) and confirmed 15-Point Watchlist dossier (`WATCH_PROP:<id>`) **in place** (`1` row per unique key instead of append-only duplicates) and serving fast `CacheService`-backed CSV via `${SHEET_WEBHOOK_URL}?feed=plat_sync`. |
| **8-10, 12-13** | **`PC_Agenda_Current_Cycle`**, **`Historical_Audit_Queue`**, **`Lost_Houston_Catalog`**, **`1927_Cutoff_Triage`**, **`HAHC_Landmark_Candidates`** | **Hidden Reference & Pipeline Mirrors.** Automated mirrors and research queues accessible anytime via `View -> Hidden sheets` or `Admin_Hub_&_Quick_Filter` deep links. |
| **14-15** | **`_Staff_User_State`** & **`_CityDir_Batch_Archive`** | **Hidden Roaming State & Batch Archive Tabs.** `_Staff_User_State` persists per-staff-email cross-computer roaming JSON (`<= 44,000` chars, paired with `PropertiesService` and `CacheService`) for `userRoamingState.js`, while `_CityDir_Batch_Archive` holds automated pre-1927 City Directory batch candidates out of `Pending_Submissions`. |

---

## 3. `Pending_Submissions` Full 23-Column Schema (`A1:W1`)

| Col | Header | Description | Example (`1127 Key St`) |
| :---: | :--- | :--- | :--- |
| **A** | `status` | `Pending`, `Approved`, `Needs Info`, or `Rejected` (Data Validation dropdown) | `Approved` |
| **B** | `hcad_num` | **13-character text string** HCAD Account Number, or scoped `<13-digit-hcad>#<slug>` multi-structure campus/accessory ID (*never format as plain number; ~45% start with `0`*) | `0621100000014` |
| **C** | `address` | Street address | `1127 KEY ST` |
| **D** | `year_built` | Verified construction year (`1836-2026`), **`0` / `Vacant` if demolished & lot currently vacant** (hides ghost building footprint live via `suppress_only: true`), or blank if name/photo/shape-only edit | `1927` |
| **E** | `original_hcad_year` | Original HCAD CAMA year built | `1920` |
| **F** | `historic_district` | Enclosing Historic District name (optional) | `Norhill Historic District` |
| **G** | `contributing` | `Contributing` / `Noncontributing` (optional) | `Contributing` |
| **H** | `source_type` | `Houston City Directory`, `Sanborn Fire Insurance Map`, `Satellite / GIS Footprint Edit`, `HMRC Contractor Portfolio / TGCA Bulletin`, `On-Site Visual Inspection`, `FIELD_SURVEY_OBSERVATION`, etc. | `Houston City Directory` |
| **I** | `source_citation` | Archival volume/page citation, footprint defect description, `[GEOMETRY_GEOJSON:{...}]` compatibility envelope, or `[SITE ACTIVITY OBSERVATION]` field notes | `1927 Houston City Directory (Morrison & Fourmy, Street Guide p. 1007); J F Lake at 1127 Key St; lot unimproved through 1926 directory` |
| **J** | `source_url` | Direct URL to digitized archive page or contributed photograph | `https://www.ancestry.com/imageviewer/collections/2469/images/4978175` |
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
4. **Sheet-Editor Verified `🔒 Staff Admin`, Key-Upserted `Plat_&_Watchlist_Sync`, Cross-Device Per-User Roaming State (`_Staff_User_State`) & Automated GitHub Backups:**
   - When a staff member signs into `🔒 Staff Admin` in the Atlas or `Staff Sign-In` in [`plat-monitor.html?mode=activity`](https://davemorris-gcp.github.io/houston-building-atlas/plat-monitor.html?mode=activity), `isAuthorizedSheetEditor_()` verifies both that the user's email address is the **Spreadsheet Owner or an explicit Google Sheet Editor** (`ss.getEditors()`) and either a Google-signed OAuth ID token (`oauth2.googleapis.com/tokeninfo`) or the private **Sheet Editor Passkey stored in `Instructions_&_Apps_Script!B6`**.
   - **1-Round-Trip Roaming State Hydration & CRDT Merge (`userRoamingState.js`):** `verify_admin` returns `supportsRoamingSync: true` and the authenticated user's `userState` JSON directly from `CacheService` / `PropertiesService` / `_Staff_User_State` (`get_user_state` / `put_user_state`). Commutative per-key timestamp merging synchronizes `caughtUpAt` watermarks, bidirectional per-card **`● Unread · Mark Read`** / **`○ Keep Unread`** pins, Property Group **`✓ Mark Read (N)`** actions, `Mark Caught Up` **`Undo`** snapshots, monitoring UI preferences (`actStream`, `actGroupBy`, `lastMode`, `cycleViewMode`, `reviewerName`), and `#audit=1` rapid-audit skips/dismissals across work desktops, home laptops, and phones.
   - **Key-Upserted `?feed=plat_sync` Endpoint & Daily GitHub Backup (`backup_workspace_state.py`):** `PLAT_MONITOR_REVIEW` and `PROPERTY_WATCHLIST_UPDATE` writes upsert by canonical key inside `Plat_&_Watchlist_Sync` and serve via `GET ${SHEET_WEBHOOK_URL}?feed=plat_sync` (with automatic fallback to `Approved_Edits` CSV). Every day at 7:00 AM CT, `.github/workflows/update_live_permits.yml` runs `pipeline/atlas_pipeline/backup_workspace_state.py` to commit a clean non-PII JSON backup of `platReviews`, `watchProperties`, and `approvedOverrides` to `pipeline/backups/workspace_cloud_backup.json`.

*(To deploy or update `Code.gs`, copy the complete script from `Instructions_&_Apps_Script` in the Google Sheet or from [`pipeline/atlas_pipeline/setup_admin_sheet.py`](../pipeline/atlas_pipeline/setup_admin_sheet.py), deploy as **Web app -> Execute as: Me -> Who has access: Anyone**, and ensure the `/exec` URL matches `"submissionWebhookUrl"` in [`app/public/data/curated_overrides.json`](../app/public/data/curated_overrides.json), `DEFAULT_SHEET_WEBHOOK_URL` in [`app/js/userRoamingState.js`](../app/js/userRoamingState.js), and `DEFAULT_SHEET_WEBHOOK_URL` in [`pipeline/atlas_pipeline/schema.py`](../pipeline/atlas_pipeline/schema.py)).*

---

## 5. Polite Houston City Directory (`1866-1968`) Batch Auditor, Ancestry `dbId: 2469` Catalog & Pre-Compiled Index

The Houston Public Library Digital Archives hosts **`39` digitized Houston City Directories (`1866-1926`)** on OCLC ContentDM (`https://cdm17006.contentdm.oclc.org/digital/collection/citydir/search`), and Ancestry (`U.S., City Directories, 1822-1999`, `dbId: 2469`, accessible free in-branch via **Ancestry Library Edition** at any Houston Public Library or Harris County Public Library branch) hosts **`31` digitized post-1926 Houston volume entries across `28` distinct years (`1927-1968`, including `15` volumes with dedicated `Street and Avenue Guide` house-by-house sections and `23` harvested volumes)** documented in [`docs/data/houston_city_directories_ancestry_catalog.json`](data/houston_city_directories_ancestry_catalog.json) and [`docs/data/houston_city_directories_ancestry_catalog.csv`](data/houston_city_directories_ancestry_catalog.csv).

1. **Instant In-App Research Kit (`citydir_index.json`):** [`pipeline/atlas_pipeline/build_house_history_index.py`](../pipeline/atlas_pipeline/build_house_history_index.py) pre-compiles **`51,778` verified HCAD parcel sightings across `1866-1968`** (`3,055` pre-1927 HPL parcels [of which `2,350` are extended with `1927-1968` occupancy spans, Ancestry `ai` `imageId` links, and `r` occupant tuples] + `48,723` brand-new `1927-1968` parcels harvested across all `15` Street & Avenue Guide volumes at `step = 1` plus `23` Alphabetical volumes, strictly excluding all `33,183` `TX Tax Code Sec. 25.025` confidential HCAD accounts and post-1970 replacement structures), `4,682` pre-screened negative bounds (`<= 1926`), and `16` historic street-name equivalences (`Emancipation <-> Dowling`, `Jensen <-> Odin/Carr`, etc.) into `app/public/data/citydir_index.json` for instant display in every property's `History` tab (for example, [`1127 Key St` (`#hcad=0621100000014&tab=history`)](https://davemorris-gcp.github.io/houston-building-atlas/#hcad=0621100000014&tab=history) and [`1001 Bayland Ave` (`#hcad=0620620000018&tab=history`)](https://davemorris-gcp.github.io/houston-building-atlas/#hcad=0620620000018&tab=history)).
2. **Physical & Microfiche Coverage for the `11` Non-Ancestry Gap Years (`1927-1963`):** All `37` years (`1927-1963`) are verified `100%` US public domain (`0` Class R/RE renewals in [`docs/data/houston_city_directories_1927_1963_usco_clearance.csv`](data/houston_city_directories_1927_1963_usco_clearance.csv)). For the `11` years in `1927-1963` not yet on Ancestry (`1931, 1933, 1938, 1943, 1944, 1947, 1950, 1960, 1961, 1962, 1963`), researchers can consult open-stacks microfiche at **Rice University Fondren Library Basement Kelley Center (`F394 .H8 P62`, `1936-1959`)**, Rice LSC print (`F394.H8 P62`, `1959` & `1961-1963`), Woodson Research Center (`F394 .H8 M6`, `1927-1935`), or HPL Houston History Research Center (`977.64112 M878`).
3. **Polite Neighborhood Batch Auditor (`city_directory_audit.py`):** Cross-references suspect rounded-decade HCAD dates (`1890`, `1900`, `1910`, `1920`, `1930`) against ContentDM using a persistent SQLite cache (`pipeline/cache/citydir_cache.sqlite`), `--delay 2.0` polite rate limiting, browser `User-Agent` headers, and `^exact^and` contiguous phrase matching:
   - **`EARLIER_THAN_HCAD`:** The address appears in an earlier Houston City Directory than HCAD's `year_built` (with direct ContentDM page URL and OCR snippet).
   - **`ABSENT_THROUGH_1926_PREMATURE_HCAD_1920`:** HCAD lists `1920`, neighboring homes on the same block (`±2`, `±4` house numbers, e.g. `1123`, `1125`, `1129 Key St`) appear in the `1920-1926` directories, yet the target address (`1127 Key St`) is absent through `1926` - proving the house was built *after* `1926` (`1927` per the `1927` Morrison & Fourmy Street Guide) and HCAD defaulted to `1920`.

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
   - Every Monday at `8:00 AM Central` (`13:00 UTC`), GitHub Actions runs [`.github/workflows/external-link-health.yml`](../.github/workflows/external-link-health.yml), exercising all `45` stratified property archetypes in Headless Chromium and checking `1,044+` unique external URLs across `71` domains.
   - Updates **[GitHub Issue #1: External Archive Link Health Report (Automated CI Monitor)](https://github.com/davemorris-gcp/houston-building-atlas/issues/1)** in place and automatically reopens + comments on Issue `#1` if any broken or moved link is detected.
2. **One-Click Access Inside the Moderation Google Sheet:**
   - **`Admin_Hub_&_Quick_Filter` Tab (Cell `G14:H14` under *Quick Links & External Tools*):** Click **`🩺 Open Live External Archive Link Health Report (Issue #1) ↗`** right above the fold upon opening the workbook.
   - **`Instructions_&_Apps_Script` Tab (Rows `20-21`, Sections `18` & `19`):** Includes **SOP 11 (Automated CI Testing & External Archive Link Health Monitor)** alongside a direct clickable **`=HYPERLINK(...)`** button to **[GitHub Issue #1](https://github.com/davemorris-gcp/houston-building-atlas/issues/1)**.

---

## 7. 1927 (100-Year Cutoff) Undercount Triage & Idempotent Sheet Sync (`SOP 16`)

Tab **13** (**`1927_Cutoff_Triage`**, `gid=927192701`) tracks the highest-priority `5,000` City of Houston (`jurs = 061`) building parcels from the `31,876`-parcel 6-bucket 100-year demolition review threshold census (`pipeline/cache/triage_1927_cutoff_coh.csv`), sorted along contiguous odd/even street blockfaces (`district -> streetStem -> Odd/Even blockface -> houseNum -> hcad_num`) for rapid Sanborn and 1926-1927 Morrison & Fourmy Street Guide verification:

1. **Multi-Moderator Safe Filter Views (`Data -> Filter views`, `927192702..927192708`):** Includes dedicated views for each of the 6 cohorts (`1. Bucket A - Pre-1927 City Directory Hold-Backs` through `6. Bucket F - 1920 False-Positive Audit (1921-29 Plats & Absent <=1926)`) plus **`7. High-Priority Unreviewed Queue (Score >= 84)`**.
2. **Idempotent Re-Sync Preserving Moderator Statuses (`--existing-triage-csv`):**
   - Export **`1927_Cutoff_Triage`** (`File -> Download -> Comma Separated Values (.csv)`) before re-running `--apply` so non-`Unreviewed` `review_status` (`Column A`) and `reviewer_notes` (`Column T`) values are preserved on refresh.
   - Omit `--bootstrap-13th-tab` on all subsequent syncs (default re-sync clears `1927_Cutoff_Triage!A2:T` cleanly, replaces Filter Views `927192702..927192708` in place, and never re-inserts `Admin_Hub_&_Quick_Filter` Row 20 or touches `Instructions_&_Apps_Script!B6`).

```bash
uv run --directory pipeline python -m atlas_pipeline.build_1927_cutoff_triage \
  --apply \
  --existing-triage-csv /tmp/1927_Cutoff_Triage.csv \
  --sheet-batches-dir /tmp/sheet_m7_sync
```
