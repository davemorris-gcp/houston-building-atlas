# Preservation Houston Monitoring Automation Architecture & Policy Guide

**Last Updated:** October 25, 2026  
**Related Codebase Components:**
- [`pipeline/atlas_pipeline/fetch_plat_and_watchlist.py`](../pipeline/atlas_pipeline/fetch_plat_and_watchlist.py) (Biweekly Planning Commission Agenda, Section K DPV Variances, Public Hearing Notices & Multi-Agency Watchlist Pipeline)
- [`pipeline/atlas_pipeline/fetch_live_permits.py`](../pipeline/atlas_pipeline/fetch_live_permits.py) (Daily COH Sold Permits & TX DSHS Asbestos/Demolition Notification Harvester)
- [`pipeline/atlas_pipeline/fetch_hahc_tracker.py`](../pipeline/atlas_pipeline/fetch_hahc_tracker.py) (Monthly HAHC COA & Landmark Designation Docket Harvester)
- [`.github/workflows/update_live_permits.yml`](../.github/workflows/update_live_permits.yml) (Daily Scheduled GitHub Actions Runner)
- [`app/plat-monitor.html`](../app/plat-monitor.html) (Plat & Property Monitor Web Application with Live Google Sheet Two-Way Cloud Sync)
- [`docs/10-sources-integrations-and-curation-workflows.md`](10-sources-integrations-and-curation-workflows.md) & [**GitHub Issue #1 (Live External Archive Link Health Report)**](https://github.com/davemorris-gcp/houston-building-atlas/issues/1)

---

## 1. Overview of the Two Preservation Monitoring Workflows

Preservation Houston operates two connected early-warning workflows designed by Emily Ardoin:

1. **Workflow A: Biweekly Planning Commission Plat & Variance Review**
   - Screens 100 to 140 subdivision plats, replats, Section K Development Plat Variances (`DPV`), and Section IV Public Hearing notices every two weeks before the Thursday 2:30 p.m. Houston Planning Commission meeting.
2. **Workflow B: 15-Point Endangered Property Multi-Agency Watchlist**
   - Tracks threatened historic landmarks and Tier 1 / Tier 2 properties across 15 city, county, state, and commercial data sources spanning four stages: `Every Check`, `Sale & Ownership`, `Plans & Use`, and `Demolition Signals`.

---

## 2. Workflow A (Biweekly Plat Review): What Is Automated vs. Human-in-the-Loop

| Review Stage | Automation Status | Technical Implementation & Why Some Steps Require a Human |
| :--- | :--- | :--- |
| **Agenda Ingestion (Excel + PDF Outline + Public Notices)** | **100% Automated** | `fetch_plat_and_watchlist.py` automatically downloads `Plat-Current-Agenda-Spreadsheet-*.xlsx` (`houstontx.gov/planning/DevelopRegs/dev_reports.html`), `PC-Draft-Agenda-Outline-*.pdf` (`commiss_plan.html`), and all active Notice PDFs (`houstontx.gov/planning/notices/`). This solves a major gap in the City's Excel spreadsheet, which omits **Section I.K Development Plat Variances (`DPV`)** and **Section IV Public Hearing Date Establishments**. |
| **Pass 1: Rule-Based Screening (~130 items -> ~60 items)** | **100% Automated** | Automatically separates City Historic District items (`Designated`), Extraterritorial Jurisdiction (`ETJ`) items, and non-issues (first-time plats with no previous subdivision, street dedications `SP`, general plans `GP`, and utility lift/water stations). |
| **Pass 2: Parcel Address, Age & Designation Resolution** | **~85% Automated / ~15% Assisted** | **Automated:** Joins Column Z (`HCAD Account No.`) and parsed notice addresses against 1,650,000+ Atlas buildings, 1,221,007 Harris County parcel addresses, Landmarks, Historic Districts, NRHP, Good Brick Awards, Ch. 42 SMLS/SMBL protections, and 5,573 dissolved historic subdivision polygons.<br>**Why ~15% require a 15-second human check:** On roughly 40% of rows in the City's spreadsheet, the applicant's surveyor leaves Column Z (`HCAD Account No.`) blank, and plats list legal descriptions (`Lot 4, Block 2, Hyde Park Addition`) or new developer marketing names (`Checkout on Ella`) rather than street addresses. Furthermore, many CAD plat PDFs on `sablobgtxpptprod.blob.core.usgovcloudapi.net` convert title-block text into stroked vector linework or raster images rather than selectable text. When no exact HCAD ID is extractable, the pipeline matches the `Previous/Original Subdivision` polygon to report the addition's earliest/median year built and provides a 1-click link to the Plat PDF and HCAD GIS map. |
| **Pass 3: Current Physical Condition & Demolition Lag Verification** | **Human-in-the-Loop (10 sec/item)** | **Why it cannot be automated:** HCAD appraisal rolls lag recent demolitions by 6 to 18 months (a parcel listed as having a 1925 house in HCAD may already be a cleared lot), and Google Maps Terms of Service prohibit automated scraping of Street View imagery. The reviewer clicks the 1-click `Street View` link to visually confirm the structure is still standing. |
| **Pass 3: Historical Significance & Advocacy Escalation (Tier 1 / 2 / 3)** | **Human Judgment** | Deciding whether an undesignated pre-1976 structure warrants submitting a formal 3-page comment letter to `speakercomments.pc@houstontx.gov` (due Wednesday before the meeting) or alerting a City Council office requires human preservation judgment. |

---

## 3. Workflow B (15-Point Property Watchlist): Deep Technical & Policy Breakdown

Out of the 15 monitoring points in the Endangered Property Watchlist, **8 points are automated via official Open Data APIs**, and **7 points are semi-automated / human-in-the-loop**.

### Part 3A: The 8 Automated Checklist Points (Public APIs & Static Feeds)

1. **Check #4: Corporate Entity Verification (`TX Comptroller Sales & Franchise Tax`)**
   - **Endpoints:** Texas Open Data Portal (`data.texas.gov`) Socrata REST APIs: Active Sales Tax Permit Holders (`jrea-zgmq.json`) and Active Franchise Taxpayers (`9cir-efmm.json`).
   - **What it catches:** Identifies new tenant/operator LLCs and DBAs (such as `Smith St C Store LLC` / `"Brake Time"` at `2000 Smith St` 12 months before the demolition permit) and resolves taxpayer numbers and officer addresses.
2. **Check #5: HCAD Ownership & Classification (`HCADGIS` / CAMA)**
   - **Endpoints:** Live HCAD ArcGIS REST MapServer (`arcweb.hcad.org`) and annual HCAD CAMA real property tables.
   - **What it catches:** Owner of record, mailing address, appraised/market/improvement/land value shifts, and state land-use class changes.
3. **Check #6: Liquor Licensing (`TABC Public Inquiry & Open Data`)**
   - **Endpoints:** `data.texas.gov` Socrata REST APIs: TABC Pending Original License Applications (`mxm5-tdpj.json`) and Active Licenses (`7hf9-qc9f.json`).
   - **What it catches:** Pending beer/wine/liquor retailer permits filed by convenience stores, gas stations, bars, or restaurants across all perimeter addresses of a watched block.
4. **Check #9: Replats & Variances (`COH Planning Commission & PlatTracker`)**
   - **Endpoints:** Biweekly COH Plat Spreadsheets, Agenda Outline PDFs (`Section K DPV`), Public Hearing Notice PDFs, and the PowerApps Entity Grid endpoint (`plattracker.powerappsportals.us`).
5. **Check #10: Accessibility Registration (`TDLR Architectural Barriers - TABS`)**
   - **Endpoint:** Live JSON POST endpoint at `https://www.tdlr.texas.gov/TABS/Search/SearchProjects` (`FacilityCounty=HARRIS`).
   - **What it catches:** Mandatory architectural barriers registrations for commercial construction or renovation projects >= \$50,000 during the design phase (caught `TABS2026022839` for the \$2,000,000 `"Braketime Smith Road"` convenience store at `530 Pierce St` four months before the demolition permit).
6. **Check #13: Code Complaints & Dangerous Buildings (`Houston 311`)**
   - **Endpoint:** City of Houston 311 ArcGIS FeatureServer (`mycity2.houstontx.gov/gisweb01/rest/services/311/HOUSTON311_RECENT_SR_SNOW/FeatureServer/0`).
   - **What it catches:** Open structure, nuisance, and dangerous building investigations that can lead to municipal demolition orders.
7. **Check #14: Demolition, Sewer Disconnect & Building Permits (`COH Sold Permits`)**
   - **Endpoints:** Weekly COH Sold Permits `.xlsx` reports (`houstonpermittingcenter.org`) and `cohtora.houstontx.gov`.
8. **Check #15: Asbestos & Demolition Notifications (`TX DSHS`)**
   - **Endpoint:** Texas Department of State Health Services Asbestos Notification search (`vo.ras.dshs.state.tx.us/datamart/notificationSearchAction.do`).
   - **What it catches:** Mandatory 10-working-day advance notifications required before any commercial abatement or demolition begins (`#2026005825` at `2000 Smith St`).

---

### Part 3B: The 7 Non-Automatable / Semi-Automated Checklist Points (and Exact Reasons Why)

| Check # & Activity | Target Portal | Why It Cannot Be Fully Automated (Technical, Legal & Policy Constraints) | Policy-Compliant Assisted Solution |
| :--- | :--- | :--- | :--- |
| **#1: Published Newspaper Legal Notices** | `texaspublicnotices.com` | **1. Ephemeral ASP.NET Session State:** Runs on legacy ASP.NET WebForms with session-bound URLs (`/S(...)/default.aspx`), encrypted `__VIEWSTATE` tokens, and anti-bot rate limits; no REST API or deep-link query URL exists.<br>**2. Copyright & ToS:** Operated by the Texas Press Association aggregating copyrighted newspaper notices; automated scraping is prohibited by Terms of Use.<br>*(Note: TABC notices and COH Planning notices that appear here are already captured via Check #6 and Check #9).* | 1-Click Copy pills for primary address, secondary perimeter addresses, and LLC names + direct portal launch button. |
| **#2: Physical Site Visit & Posted Signage** | Physical Property / `Google Street View` | **1. Physical-World Signal:** Wooden TABC 60-day signs, replat/variance signs, leasing banners (e.g. Greenberg & Co. banner at 2000 Smith St), temporary chain-link fencing, dumpsters, and utility disconnect markings appear on site before or without any digital database entry.<br>**2. Street View Capture Lag & ToS:** Street View imagery updates every 12 to 24 months and Google Maps ToS prohibits automated image scraping. | Mobile-friendly `Report Site Activity to PH` button in the Atlas Inspector + 1-click log buttons in the Watchlist. |
| **#3: Deed Transfers, Deeds of Trust & Options** | Harris County Clerk (`cclerk.hctx.net/applications/websearch/RP.aspx`) | **1. ASP.NET ViewState + CAPTCHA + Login Wall for PDFs:** The County Clerk Real Property search requires interactive session cookies, ASP.NET `__VIEWSTATE` form posts, and anti-automation gates; viewing recorded instrument PDFs (such as Deed of Trust `#RP-2026-399473` naming lender `Greenberg Finance Co.`) requires a logged-in public user account.<br>**2. Statutory Bulk Access Rules:** Bulk index feeds are governed by paid title-plant subscriptions under Tex. Local Gov't Code § 191.008.<br>**3. Why Check #5 (HCAD) isn't enough:** HCAD deed updates lag the County Clerk by 3 to 8 weeks and omit Deeds of Trust (lenders) and recorded Purchase Options. | 1-Click Copy Grantor/Grantee pills (Owner Name + Buyer LLCs) that copy the exact entity string to the clipboard and open `cclerk.hctx.net` in a new tab. |
| **#7: Consumable Hemp Retail Licensing** | TX DSHS (`vo.ras.dshs.state.tx.us/datamart/login.do`) | **Authenticated Portal Gate (`login.do`):** Unlike the DSHS Asbestos module, DSHS places Consumable Hemp Retail Registrations behind an authenticated user login session and does not publish the table to `data.texas.gov` Socrata. | 1-Click Copy Address/LLC pill + direct link to `login.do` when tracking convenience store / retail redevelopments. |
| **#8: Pending Building Plans (GovQA Open Records & COH ILMS)** | `houstontx.govqa.us` & `pdinet.pd.houstontx.gov/cohilms/webs/plan_lookup.asp` | **1. GovQA Public Information Act (TPIA):** Unissued architectural drawings, site plans, and pre-application notes are not public on the web; obtaining them requires submitting a formal Texas Public Information Act request (Tex. Gov't Code Ch. 552) under a human requester account.<br>**2. ILMS Lookup Limitation:** The public COH ILMS Plan Status portal **only allows searching by an 8-digit Project Number** (e.g. `26052089`); it does not expose a public search-by-address API until a permit is sold. | **1-Click GovQA TPIA Request Generator** that pre-writes the exact legal request covering all block-perimeter addresses, HCAD ID, and LLCs, copies it to the clipboard, and opens GovQA. |
| **#11: Street/Alley/Easement Abandonments & TIRZ Agendas** | `houstontx.gov/citysec/` & 27 separate TIRZ sites (e.g. `midtownhouston.com`) | **1. Decentralized Unstructured PDFs:** Houston's 27 TIRZ boards (Midtown Redevelopment Authority, Downtown TIRZ, etc.) host board packets on independent websites with no central API.<br>**2. Metes-and-Bounds Descriptions:** Joint Referral Committee (JRC) easement/alley abandonments on City Council agendas are drafted using legal tract descriptions (`Block 417 SSBB`) rather than street addresses. | 1-Click Copy Legal Description / Street Name pills + direct links to City Secretary and TIRZ agendas. |
| **#12: TCEQ Environmental Registrations (PST / Stormwater)** | `tceq.texas.gov/permitting/central_registry` | **Legacy Session State & Unnormalized Location Strings:** TCEQ Central Registry (`crpub`) uses stateful sessions where underground Petroleum Storage Tank (`PST`) and stormwater (`TXR150000`) filings are often indexed by intersection (`NEC SMITH AND PIERCE`) or Customer Number (`CN605820570`) rather than normalized street address, and require human inspection to separate historic closed tanks from new construction filings. | 1-Click Copy Address / Owner LLC (`CN`) pills + direct launch to TCEQ Central Registry. |
| **Market Listings & Social Media (Guide Rows 14-15)** | `LoopNet`, `Crexi`, `HAIF`, Social Media | **Strict Terms of Service & Anti-Bot Firewalls:** CoStar (`LoopNet`), `Crexi`, and Meta strictly prohibit automated scraping in their Terms of Service and enforce Cloudflare/Akamai bot challenges. | 1-Click Google Search / HAIF query links that open standard browser searches across all block addresses and LLC names. |

---

## 4. Shipped Policy-Compliant Assisted Workflow Controls in `app/plat-monitor.html` (Zero Scraping / Zero ToS Violations)

Because we strictly respect government portal boundaries (no bypassing logins, CAPTCHAs, or ASP.NET session walls) and website Terms of Service (no scraping `texaspublicnotices.com`, `LoopNet`, `Crexi`, or Google Street View), `app/plat-monitor.html` implements five **Human-in-the-Loop Assisted Launchers**:

1. **One-Click Copy & Launch Pills on Every Non-Automated Checklist Row (`buildAssistedPortalPills` on `#1`, `#3`, `#7`, `#8`, `#11`, `#12`):**
   - Instead of making the reviewer re-type `2000 SMITH ST`, `530 PIERCE ST`, `2026 SMITH REALTY LLC`, or `SMITH ST C STORE LLC` into five different legacy government forms, each checklist card in the 15-Point Watchlist renders **1-Click Copy & Open** pills (`Copy '2000 SMITH ST' & Open Portal`, `Copy '530 PIERCE ST'`, `Copy '2026 SMITH REALTY LLC'`, `Copy HCAD 0152300000001`).
   - Clicking a pill copies the exact search string to the system clipboard (`navigator.clipboard.writeText`) and opens the official government search page in a new browser tab so the reviewer only has to press `Ctrl+V` / `Cmd+V` and `Enter`.
2. **One-Click Texas Public Information Act (GovQA) Open Records Request Generator (`openGovQaRequestModal` on Check #8):**
   - Because unissued architectural plans and rejected plan reviews require a formal Texas Public Information Act (TPIA) request on `houstontx.govqa.us`, clicking **Generate GovQA Open Records Request** opens `#govqaModal` with a ready-to-paste TPIA request naming:
     - Every perimeter street address on the block (`2000 Smith St`, `530 Pierce St`, `514 Pierce St`, `2019 Brazos St`),
     - The HCAD account number and legal description (`Block 442 SSBB`),
     - Every known owner/tenant LLC (`2026 Smith Realty LLC`, `Smith St C Store LLC`), and
     - Any known ILMS Project Numbers (`#26052089`).
3. **Direct Deep-Linking for Known 8-Digit COH ILMS Project Numbers (Check #8):**
   - Once a reviewer discovers an 8-digit ILMS Plan Review project number (from a site placard, TABS registration, or GovQA response), storing that `ilmsProjectNo` on the watchlist property unlocks direct lookup on `pdinet.pd.houstontx.gov/cohilms/webs/plan_lookup.asp`.
4. **One-Click `✓ Mark Checked` Timestamp Button (`[data-quick-check]`):**
   - Every checklist item (`#1` through `#15`) includes a 1-click **`✓ Mark Checked`** button that stamps the calendar date (`Checked YYYY-MM-DD by [Reviewer] - No new filings`) and syncs immediately to the shared Google Sheet (`Plat_&_Watchlist_Sync`), turning a 15-minute manual logging chore into a 30-second clipboard-assisted pass.
5. **Pass 2 Missing-HCAD Assisted Resolver (`buildMissingHcadHelperBox`):**
   - When a Planning Commission plat row in the City's Excel spreadsheet has a blank Column Z (`HCAD Account No.`), `buildMissingHcadHelperBox()` automatically highlights the `Previous/Original Subdivision` age metrics, provides 1-click links to the Plat PDF, HCAD GIS viewer, and the Houston Building Atlas map (`?hcad=` / subdivision view) so the reviewer can click the exact corner parcel in 10 seconds and lock in its HCAD ID for all future cycles.

