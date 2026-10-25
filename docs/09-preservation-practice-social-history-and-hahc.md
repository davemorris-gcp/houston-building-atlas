# Chapter 9: Preservation Practice Stack, House History Research Kit, Social History Equity Layers, Guided Chapters & HAHC COA Tracker

[← Chapter 8: Crowdsourcing, Live HCAD API & Admin Operations](08-crowdsourcing-and-admin-operations.md) | [Back to Master README](../README.md) | [Next: Chapter 10 - Master Data Sources, External Integrations & Curation Playbooks →](10-sources-integrations-and-curation-workflows.md)

---

## 1. Responsive 4-Tab Property Inspector, Desktop Wide Researcher View, Mobile Bottom Sheet & Printable Archival Dossier

As the Houston Building Atlas expanded to incorporate multi-agency regulatory alerts, legal protection rules, digitized city directories, Sanborn fire insurance plates, social-history overlays, and HAHC precedent dockets, a single vertical scroll inside a narrow sidebar was replaced by a structured, responsive research workspace (`app/index.html`, `app/css/styles.css`, `app/js/app.js`):

1. **Persistent Header + 4 Scoped Research Tabs (`role="tablist"`):**
   - **Persistent Property Summary Header:** Displays the street address, primary building name and historical `AKA` aliases, verified `year_built` + provenance badge (`year_source`), HCAD 13-digit account number, photo carousel (`⇄ Then & Now` comparison slider + Street View Time Machine `2007-Present`), active demolition/asbestos or HAHC docket alert banners, and quick action buttons (`🖨 Printable Dossier`, `✎ Suggest Correction`, `🔗 Copy Link`).
   - **Tab 1 - `Overview`:** Displays the **3-Pillar Legal Protection & Historic Tax-Credit Scorecard**, designation badges (`PLM`, `LM-in-HD`, `90-Day Waiver LM`, `COH Historic District`, `NRHP`, `Good Brick Award`, `THC Marker`), architectural style, architect and general contractor portfolio buttons, neighborhood/ward/subdivision context, and social-history chips (`1915 Red Book of Houston`, `1937 HOLC Redlining Grade`).
   - **Tab 2 - `History`:** Houses the **One-Click 5-Step House History Research Kit**, **Sanborn Fire Insurance Atlas (`1877-1955`)** inline volume/sheet launcher, **Houston City Directory (`1866-1926`)** verified OCR sightings and pre-screened absence bounds, **Previous Historic Structures on This Site (`Lost Houston`)**, **HAIF Community Architecture Discussions**, and full Landmark Designation Report PDF links.
   - **Tab 3 - `Permits`:** Combines active **COH Sold Permits & TX DSHS Asbestos/Demolition Notifications** (`active_permit_alerts.json`), **Monthly HAHC COA & Designation Filings** (`hahc_coa_tracker.json`), **1917-1929 HMRC Contractor Monographs & *T.G.C.A. Monthly Bulletin* Contract Awards**, and the on-demand **HCAD Building Permit & Appraiser Remodel Timeline** (`permits_<prefix3>.json`).
   - **Tab 4 - `HCAD & Parcel`:** Surfaces parcel dimensions, state property classification, multi-building campus/sub-building breakdowns, **3-Tier Subdivision Deed Restrictions & Chapter 42 SMLS/SMBL Protections** (`65` mirrored deed PDFs), and the **Fetch Live HCAD Appraisal Record** button (`hcadLink.js`).
2. **Desktop Wide 2-Column Researcher Split View (`⤢ Wide View`, `#btn-toggle-inspector-wide`):**
   - On viewports `>= 1100px`, clicking **`⤢ Wide View`** expands `#inspector-drawer` from `440px` to `760px` (`820px` on `>= 1500px` monitors), rendering the persistent photograph/summary column alongside the active research tab in a 2-column CSS Grid (`localStorage` key `ph_inspector_wide = "1"`) while dynamically updating `mapCtrl.setPadding({ right })` so map fly-to centering never hides the selected building behind the drawer.
3. **Mobile 3-State Bottom Sheet (`Peek` / `Half` / `Full`):**
   - On viewports `<= 920px`, `#inspector-drawer` converts into a bottom sheet (`188px` Peek, `54dvh` Half, `90dvh` Full) controlled via the drag handle (`#inspector-mobile-drag-handle`) or `↕` cycle button (`#btn-cycle-mobile-sheet`), keeping WebGL map gestures responsive above the sheet.
4. **Printable Archival Property Dossier (`#dossier-modal`):**
   - Clicking **`🖨 Printable Property Sheet`** compiles all 4 tabs of provenance, legal protections, Sanborn/City Directory citations, permit timelines, and archival photographs into a clean `@media print` dossier formatted for Letter PDF export or physical house-history binders.
5. **Crowdsourced `[SITE ACTIVITY OBSERVATION]` Field Alert Flow:**
   - Clicking **`🚨 Report Site Activity to PH`** on any property (or inside an Active Permit Alert banner) opens `#correction-modal` in a dedicated field-observation mode (`source_type = "On-Site Visual Inspection"`), allowing neighbors or field volunteers to report physical demolition equipment, wooden TABC 60-day notices, replat signs, utility disconnects, or leasing banners directly into Preservation Houston's `Pending_Submissions` moderation queue.

---

## 2. Historic Preservation Practice Stack: Legal Protection Scorecard, 90-Day Waiver Landmarks & Surveyed Districts

One of the most dangerous misconceptions in Houston preservation is assuming that every "historic landmark" or "National Register" badge protects a building from demolition. `pipeline/atlas_pipeline/build_preservation_layers.py` and `app/js/app.js` make Houston's statutory preservation framework explicit on every parcel:

### 2.1 Three-Tier City of Houston Landmark Legal Distinction (`COH Code Ch. 33, Sec. 33-250`)
Across the `509` designated City of Houston Landmarks in `overlays.json` (`landmarks`):
- **`200` Protected Landmarks (`PLM` - Permanent Demolition Protection):** Owner-consented covenant recorded under Chapter 33 prohibiting demolition except under strict economic hardship or unusual and compelling circumstances approved by HAHC.
- **`86` Standard Landmarks Inside a COH Historic District (`LM-in-HD` - District COA Protected):** Protected against arbitrary demolition by the enclosing City Historic District's Certificate of Appropriateness (`COA`) ordinance.
- **`223` Vulnerable `90-Day Waiver Landmarks` (`LM` Outside Historic Districts):** Under **City of Houston Code of Ordinances Chapter 33, Section 33-250**, a standard Landmark (`LM`) located outside a City Historic District is subject only to a **90-day waiting period**; if HAHC does not issue a COA for relocation or demolition, a demolition waiver issues automatically after 90 days!
  - Users can filter the `COH Landmarks (509)` map layer directly via the inline **`All (509)` | `Protected (286)` | `⚠ 90-Day Waiver (223)`** sub-filter pills (`#lmFilter=all|protected|waiver_lm`), rendering the `223` vulnerable Waiver Landmarks with high-visibility amber-red warning halos.

### 2.2 Plain-English 3-Pillar Legal Protection & Historic Tax-Credit Scorecard
At the top of every property's `Overview` tab (`renderProtectionScorecardHtml` in `app/js/app.js`), the Inspector synthesizes spatial and attribute joins across all statutory layers into three plain-English answers:
1. **Pillar 1 - Demolition & Exterior Alteration Rule:** Distinguishes `Permanent HAHC COA Required` (`PLM`, `Contributing` in `22` standard COH Historic Districts), `90-Day Relocation/Documentation Delay Only` (**Old Sixth Ward Protected Historic District** non-PLM structures and `223` standalone `90-Day Waiver Landmarks`), and `No Municipal Demolition Review` (including standalone NRHP listings, which regulate federal undertakings under Section 106 but **never block private-owner demolition**).
2. **Pillar 2 - Lot-Split & Front Setback Armor:** Evaluates active **Chapter 42 Special Minimum Lot Size (`SMLS`), Special Minimum Building Line (`SMBL`), Conservation Districts** (`910` polygons), and **Private Subdivision Deed Restrictions** (`deed_restrictions_catalog.json`).
3. **Pillar 3 - Historic Tax Credits & Property Tax Exemptions:** Computes eligibility across **City of Houston Historic Site Tax Exemption (`Ch. 44-5`)**, **Texas Historic Preservation Tax Credit (`25%` state franchise/insurance credit on QREs `>= $5,000` for NRHP/RTHL/SAL income-producing or nonprofit buildings)**, and **Federal Historic Rehabilitation Tax Credit (`20%` federal credit for certified income-producing properties in NRHP districts or individual NRHP listings)**.

### 2.3 Seven-Tier `Historic Status` Cartographic Color Lens & Three New Preservation Overlays
- **`Historic Status` Color Mode (`?color=preservation_status`):** Recolors all `1,507,553` countywide footprints into a 7-tier preservation hierarchy:
  1. `Protected Landmark (PLM)` (`#10B981` Emerald)
  2. `COH Historic District - Contributing / LM-in-HD` (`#14B8A6` Teal)
  3. `90-Day Waiver Landmark (Vulnerable LM)` (`#F59E0B` Amber)
  4. `National Register (NRHP) Individual or District` (`#3B82F6` Royal Blue)
  5. `Surveyed Potential Historic District (2019 COH Plan)` (`#A855F7` Violet)
  6. `Undesignated Pre-1976 (50+ Year NRHP-Age Eligible)` (`#FB7185` Coral Rose)
  7. `Post-1976 Modern Fabric / Non-Contributing` (`#334155` Neutral Slate)
- **Three Added Preservation & Cultural Overlays (`build_preservation_layers.py`):**
  1. **Surveyed Potential Historic Districts (`60` polygons, `potential_historic_districts`):** Extracted from the City of Houston's *2019 Historic Preservation Plan* (`Potential_Historic_Districts` GIS survey covering *Riverside Terrace*, *Idylwood*, *Eastwood*, *Independence Heights*, *Third Ward*, *Fifth Ward*, *Near Northside*, *Garden Oaks*, *Oak Forest*, *Pecan Park*, *Lindale Park*, and `49` other eligible areas).
  2. **Individual National Register (`NRHP`) Properties (`238` Harris County listings, `nrhp_properties`):** Resolves `222` extant individual NRHP properties and **`16` demolished `"Gone"` NRHP landmarks** (marked with `†` dashed memorials, providing empirical proof that NRHP listing alone does not prevent demolition), with validated 8-digit leading-zero **Texas Historical Commission (`THC`)** nomination PDF links (`thc.texas.gov/public/upload/preserve/national_register/national_register_listed/NR-{refnum8}.pdf` or National Archives `catalog.archives.gov` search).
  3. **State & Civic Cultural Arts Districts (`7` polygons, `cultural_districts`):** Texas Commission on the Arts (`TCA`) and Houston Arts Alliance cultural districts (*East End*, *Fifth Ward*, *Midtown*, *Houston Museum District*, *Theater District*, *Third Ward*, *Washington Avenue Arts District*).
- **Collapsible 4-Category Accordion Layers Panel (`#overlay-layer-list`):** Organizes all `22` overlays into four scannable `<details>` groups with live active-layer count badges:
  1. *Advocacy & Early Warning* (`Permit & Demo Watch`, `Planning Commission Plats & Variances`, `HAHC COA & Designations`)
  2. *Historic Designations & Protections* (`COH Landmarks` + `PLM/Waiver` filter, `COH Historic Districts`, `Heritage Districts`, `NRHP Districts`, `NRHP Individual Properties`, `Surveyed Potential Historic Districts`, `THC Markers`, `Good Brick Awards`, `Ch. 42 SMLS/SMBL/Conservation`, `Cultural Arts Districts`)
  3. *Social History, Redlining & Lost Fabric* (`1915 Red Book of Houston`, `1937 HOLC Redlining Zones`, `Lost Houston Demolished Landmarks`)
  4. *Neighborhoods, Civic Geography & Infrastructure* (`Neighborhoods & Civic Clubs`, `COH Super Neighborhoods`, `Historic Wards 1839-1905`, `Annexation History 1836-2020`, `Platted Subdivisions & Deeds`, `Historical Waterways & Buried Gullies`, `Historical Railroads, Streetcars & Depots`, `Relocated Structures & HMRC Contractors`).

---

## 3. One-Click 5-Step House History Research Kit & UT Austin PCL Sanborn Expansion (`16` Volumes, `878` Sheets)

To turn Preservation Houston's public *House History Workshop* curriculum into an instant per-parcel research workflow, every building's `History` tab automatically generates a **One-Click 5-Step House History Research Kit** (`pipeline/atlas_pipeline/build_house_history_index.py` & `build_ut_pcl_sanborn.py`):

### 3.1 Pre-Indexed Houston City Directories (`1866-1926`, `citydir_index.json`)
- `build_house_history_index.py` compiles `app/public/data/citydir_index.json` across all **`39` digitized Houston Public Library ContentDM City Directories (`1866-1926`)**, bundling:
  - **`2,859` verified OCR address sightings** (earliest confirmed directory year, volume title, direct ContentDM page URL, and extracted OCR text snippet);
  - **`4,682` pre-screened negative bounds** (proving an address was absent through `1926` while adjacent houses on the same block were occupied, flagging premature rounded `1920` HCAD placeholder dates);
  - **`16` historic Houston street-name equivalences** (automatically generating dual search pills when a property sits on a renamed thoroughfare, such as `Emancipation Ave <-> Dowling St`, `Jensen Dr <-> Odin Ave / Carr St`, `Montrose Blvd <-> Grant St`, `Bagby St`, `Westheimer Rd <-> Hathaway`, `Washington Ave`, and pre-1911 numbering hints).

### 3.2 Expanding Sanborn Fire Insurance Coverage from 10 LOC Volumes to 16 Volumes (`1877-1955`, `878` Sheets)
While the Library of Congress hosts `10` Houston Sanborn volumes (`626` sheets, `1885-1955`), six critical Houston volumes - including **Houston's earliest `1877` Sanborn survey**, the **`1917` Ship Channel / Magnolia Park industrial sheets**, **`1919` Sugar Land / Harris County border sheets**, and the **`1924 Vol. 2`, `1924 Vol. 3`, and `1948 Vol. 6` neighborhood residential volumes** (covering *Norhill*, *Woodland Heights*, *Houston Heights*, *Montrose*, *Eastwood*, and *Third Ward*) - are digitized exclusively in the **University of Texas at Austin Perry-Castaneda Library (`UT PCL`) Map Collection** (`maps.lib.utexas.edu`).

`pipeline/atlas_pipeline/build_ut_pcl_sanborn.py` solved two technical obstacles to integrate all `252` UT PCL sheets seamlessly alongside LOC:
1. **Bypassing Cross-Origin `Anubis` Proof-of-Work (`PoW`) Image Challenges via First-Party WebP Mirroring:**
   - UT Austin's web server (`maps.lib.utexas.edu`) sits behind an **Anubis** anti-bot SHA-256 JavaScript Proof-of-Work challenge gate. While a human clicking a link in a top-level browser tab passes the 1-second Anubis JS challenge automatically, cross-origin `<img src="https://maps.lib.utexas.edu/...">` tags embedded inside another domain receive an `HTTP 200 text/html` Anubis challenge page instead of JPEG bytes and fail to render!
   - To guarantee instant, 100% reliable inline sheet rendering with zero runtime dependency on UT's Anubis gate, `build_ut_pcl_sanborn.py` generated **504 optimized local WebP plates** (`252` crisp thumbnails + `252` `1800px` reading plates, `51.6 MB` total) inside `app/public/data/sanborn_ut/`, while linking the `Open Full Master JPEG on UT Austin PCL ↗` button to the official UT PCL archive URL for top-level tab viewing.
2. **Spatial Index Mapping Across `29` Volume/Subdivision Polygons:**
   - Expanded `app/public/data/sanborn_catalog.json` to **`16` digitized volumes (`1877-1955`), `878` sheets, and `29` spatial coverage polygons**, so clicking `1127 Key St` in Norhill immediately surfaces **`1924 Vol. 2 (UT Austin PCL)`** (`Sheet 273`) right inside the Property Inspector and `#sanborn-browser-modal`.

### 3.3 The Five Steps Assembled Automatically for Every Parcel
1. **Step 1 - Sanborn Fire Insurance Atlases (`1877-1955`):** Spatially matched volumes (`LOC` + `UT Austin PCL`) with 1-click inline sheet viewer and `1907` georeferenced map mosaic toggle.
2. **Step 2 - Houston City Directories (`1866-1926`):** Verified `citydir_index.json` OCR hits/absences + 1-click HPL ContentDM exact-phrase search pills (including historic street names).
3. **Step 3 - Subdivision Plat & Deed Chain (`1836-Present`):** Matched historic subdivision name, 1-click local mirrored Deed Restriction PDFs (`65` PDFs), **Harris County Clerk Map Records (`MC.aspx`)**, and **Real Property Deed Search (`RP.aspx`)**.
4. **Step 4 - Building Permits, Remodels & Contractor Ledger:** Matched 1917-1929 HMRC/*T.G.C.A.* contractor entries, 1950-2026 HCAD permit timeline, and live COH Sold Permits / TX DSHS Asbestos lookups.
5. **Step 5 - Official Historic Surveys & Community Forum (`THC`, `HPO`, `HAIF`):** Matched HPO Landmark PDF, THC Atlas / 8-digit NRHP nomination PDF, and linked HAIF architecture threads.

---

## 4. Social History, African American Heritage & Redlining Equity Layers (`build_social_history_layers.py`)

To situate Houston's architectural survival and demolition risk within its full social and civil-rights geography, `pipeline/atlas_pipeline/build_social_history_layers.py` adds two deeply cross-referenced overlays:

### 4.1 The 1915 *Red Book of Houston: A Compendium of Social, Professional, Religious, Educational and Industrial Interests of Houston's Colored Population* (`970` Mapped Sites)
- **Primary Archival Sources:** Ingests the **Rice University Woodson Research Center / Center for Research Computing** *1915 Red Book of Houston* spatial database (`services5.arcgis.com/9s1YtFmLS0YTl10F/arcgis/rest/services/Red_Book_of_Houston_WFL1/FeatureServer`, `970` mapped churches, schools, physicians' offices, fraternal lodges, Black-owned businesses, and residences) paired with **`85` high-resolution digitized book plates** from Rice University's Quartex Digital Collections (`digitalcollections.rice.edu` / `iiif.quartexcollections.com`).
- **Address Hygiene & Parcel Survival Audit:**
  - Repaired ArcGIS `StreetName` field concatenations and fractional `1/2` upstairs-suite house numbers (`409 1/2 Milam St -> 409 Milam St`), matching every historic site against extant `2026` HCAD parcels and `1907`/`1924` Sanborn footprints.
  - Quantifies the exact physical survival of Black Houston's 1915 civic and commercial landscape:
    - **`29` Surviving Pre-1925 Structures (`3.0%`):** Including **1875 Antioch Missionary Baptist Church** (`500 Clay St`), **1910 Rutherford B. H. Yates House** (`1314 Andrews St`), **Bethel Institutional Missionary Baptist Church** frame sanctuary walls (`801 Andrews St`), and surviving Freedmen's Town / Old Sixth Ward / Third Ward cottages.
    - **`323` Rebuilt / Later-Structure Parcels (`33.3%`):** Historic congregation or neighborhood parcels carrying post-1925 replacement buildings.
    - **`618` Cleared Lots, Surface Parking, or Freeway Rights-of-Way (`63.7%`):** Sites erased by **I-45 Pierce Elevated / Freedmen's Town trench**, **I-69 / US-59**, **I-10**, urban renewal, and downtown surface parking.

### 4.2 1937 HOLC "Residential Security" Redlining Zones (`47` Zones) & Modern Urban Heat / Preservation Disparity Audit
- **Primary Sources:** Integrates the **University of Richmond *Mapping Inequality* v3.0** digitized polygons for the Federal Home Loan Bank Board / **Home Owners' Loan Corporation (`HOLC`) 1937 Houston Residential Security Map** (`47` graded zones: `7` Grade A *"Best"*, `14` Grade B *"Still Desirable"*, `15` Grade C *"Definitely Declining"*, `11` Grade D *"Hazardous"*, plus the georeferenced 1937 NARA RG 195 raster scan in the Historical Map catalog), cross-joined with:
  1. Verbatim **1937 HOLC Area Description (`clarifying_remarks`, `detrimental_influences`, `inhabitants` appraiser sheets)**;
  2. **2020 Houston-Galveston Area Council / HARC / NOAA Urban Heat Watch** afternoon heat-index deltas (`+°F` vs. Grade A canopy baseline) and impervious surface metrics; and
  3. Live spatial joins (`holc_1937_grade_summary` in `overlays.json`) measuring `2026` building survival, City Historic District coverage, Chapter 42 SMLS/SMBL protection, and *1915 Red Book* overlap across Grades `A`, `B`, `C`, and `D`:
     - **`77.4%` (`568` of `734` HOLC-graded *1915 Red Book* sites)** were placed inside **Grade D ("Hazardous" / Redlined)** zones in 1937, while **`0`** were placed in Grade A.
     - Grade A and Grade B neighborhoods hold **`153`** and **`192`** Chapter 42 SMLS/SMBL lot-protection zones respectively, whereas all `11` historically redlined Grade D neighborhoods combined hold only **`8`** Chapter 42 protection blocks - explaining why townhome lot-split pressure and demolition permits concentrate disproportionately in historically redlined wards.

---

## 5. Guided Citywide Visual Chapters ("How Houston Was Built") & Architect / Builder / Style Map Portfolios

Implemented in `app/js/guidedChaptersAndPortfolios.js` (`#guided-chapters-modal` and `#portfolio-banner`):

1. **Six Interactive Citywide Visual Chapters (`#chapter=<id>`):**
   - Unlike single-pin walking tours, each chapter reconfigures the entire WebGL map state - switching basemap, chronological slider window, color lens, historical raster overlay, and vector layers simultaneously while presenting clickable exemplar stops:
     1. **`ch1_allen_brothers_wards` (*1836-1905: Bayou Landing, Borden Grid & The Four Charter Wards*):** `-34.65°` 1836 Borden Survey grid, 1869 W. E. Wood map, authentic `9 sq. mi.` Charter Wards, and buried Downtown gullies.
     2. **`ch2_railroads_and_streetcars` (*1853-1938: "Where Seventeen Railroads Meet the Sea" & Streetcar Suburbs*):** 19th-century rail trunks, 1891-1938 electric streetcar corridors, and early streetcar additions (*Houston Heights*, *Woodland Heights*, *Montrose*, *Eastwood*).
     3. **`ch3_red_book_and_freedmens_town` (*1865-1925: Emancipation, Freedmen's Town & The 1915 Red Book of Houston*):** All `970` *1915 Red Book* sites, surviving sanctuaries, and freeway corridor erasures.
     4. **`ch4_oil_boom_and_master_architects` (*1915-1941: Oil Boom Skyscrapers, Garden Estates & Master Builders*):** Art Deco towers (*Gulf Building*), **Alfred C. Finn**, **John F. Staub**, **Joseph Finger**, **Birdsall P. Briscoe**, **William Ward Watkin**, and *T.G.C.A.* contractors.
     5. **`ch5_holc_redlining_and_covenants` (*1937-1968: HOLC Redlining, Private Deed Covenants & Protection Disparities*):** `47` 1937 HOLC zones overlaid with Chapter 42 SMLS/SMBL blocks, deed covenants, and HARC Urban Heat Watch metrics.
     6. **`ch6_freeways_suburbs_and_preservation` (*1950-2026: Freeway Loops, Lost Houston & Modern Preservation Frontlines*):** `1963` defensive highway annexation spokes, `81` *Lost Houston* demolished landmarks, `223` vulnerable `90-Day Waiver Landmarks`, and live Permit/Plat early-warning layers.
2. **Countywide Architect, Master Builder & Architectural Style Map Portfolios (`#portfolio=architect:<Name>` / `#portfolio=style:<Style>`):**
   - Clicking any **Architect**, **General Contractor / Builder**, or **Architectural Style** pill inside any property's Inspector Panel activates **Map Portfolio Mode**: dims non-matching structures across Harris County, highlights every extant AND demolished (*Lost Houston*) work by that designer or style with high-contrast gold halos, opens a floating monograph banner with chronological timeline cards, and encodes a shareable deep link (`#portfolio=architect:Alfred%20C.%20Finn` -> `45` structures `1913-1956`; `#portfolio=style:Art%20Deco`).

---

## 6. Monthly HAHC Agenda, Certificate of Appropriateness (`COA`) & Designation Precedent Tracker (`fetch_hahc_tracker.py`)

To help homeowners inside Houston's `23` City Historic Districts and `509` Landmarks prepare Certificate of Appropriateness (`COA`) applications - and to give Preservation Houston staff instant visibility into monthly commission dockets - `pipeline/atlas_pipeline/fetch_hahc_tracker.py` (run daily/monthly by `.github/workflows/update_live_permits.yml`) compiles `app/public/data/hahc_coa_tracker.json` and `overlays.json` (`hahc_coa_filings`):

1. **Automated Harvesting of Official HAHC Agendas, Staff Reports & Action Minutes (`2024-2026`):**
   - Crawls `https://www.houstontx.gov/planning/Commissions/HAHC.html`, parsing **`403` official COA, Certificate of Remediation (`COR`), Demolition/Relocation, and Landmark/Protected Landmark Designation filings across `283` unique parcels and `29` monthly HAHC meetings (`March 2024 - October 2026`)**.
   - Normalizes application types (`Alteration - Addition`, `Alteration - Windows/Doors/Siding`, `New Construction - Garage/Residence`, `Demolition`, `Relocation`, `Protected Landmark Designation`), staff recommendations, and final commission outcomes (`Approved`, `Approved with Conditions`, `Deferred`, `Denied`, `Denied - COR Issued`), linking directly to the official City of Houston HAHC Agenda and Action Report PDFs.
2. **Three Integrated User Surfaces:**
   - **Map Overlay (`HAHC COA & Designations (283)` under *Advocacy & Early Warning*):** Color-codes active monthly docket items (`#EF4444` Pulsing Red), approved COAs (`#14B8A6` Teal), and denials / Certificates of Remediation / demolition reviews (`#F59E0B` Amber).
   - **Parcel HAHC Docket Card (Property Inspector `Permits` & `Overview` Tabs):** Whenever a user clicks one of the `283` parcels with HAHC history, displays every COA filing date, item number, work scope, outcome badge, and official staff-report/agenda PDF link.
   - **District HAHC Precedents Card & Full Searchable Docket Modal (`#hahc-tracker-modal`):** Clicking **any parcel inside a City Historic District** (even a house that has never filed a COA itself!) renders a **District HAHC COA Precedents Summary** (for example, *Norhill Historic District*: approval rate, addition/window/garage precedent counts, and 1-click button to open `#hahc-tracker-modal` pre-filtered to that district so a homeowner or architect can study recently approved vs. denied designs on neighboring blocks).

---

[← Chapter 8: Crowdsourcing, Live HCAD API & Admin Operations](08-crowdsourcing-and-admin-operations.md) | [Back to Master README](../README.md) | [Next: Chapter 10 - Master Data Sources, External Integrations & Curation Playbooks →](10-sources-integrations-and-curation-workflows.md)
