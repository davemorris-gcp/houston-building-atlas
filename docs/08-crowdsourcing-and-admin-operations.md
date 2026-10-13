# Chapter 8: Crowdsourced Moderation Pipeline, Live HCAD API & Admin Operations

[← Chapter 7: Waterways, Railroads, Permits & Contractors](07-waterways-railroads-permits-and-contractors.md) | [Back to Master README](../README.md)

---

## 1. Zero-Cost Community Crowdsourcing & Google Sheets Moderation Pipeline

Because archival research across 1.51 million structures is an ongoing community effort ("History in Progress"), the Houston Building Atlas includes a built-in **Suggest Archival Correction / Report Footprint Error** workflow that requires **zero database hosting costs**.

| ![Footprint & Geometry Error Reporting Modal](images/08_footprint_error_modal.jpg) | ![Live HCAD Appraisal Record Fetch in the Inspector Panel](images/08_hcad_live_record_inspector.jpg) |
| :---: | :---: |
| *Footprint & Geometry Error Reporting Modal* | *Live HCAD Appraisal Record Fetch in the Inspector Panel* |

### 1.1 Dual-Purpose Submission Modal (`#correction-modal`)
From any building's Inspector Panel, users or researchers can click:
- **Suggest Archival Date / Data Correction:** Pre-fills the property's address, 13-digit HCAD account number, current `year_built`, and historic district, allowing the user to submit a corrected construction year (`1836-2026`), architectural style, architect, original general contractor/builder, building permit citation, archival source type (*Houston City Directory*, *Sanborn Fire Insurance Map*, *Harris County Deed / Mechanic's Lien*, *HMRC Contractor Archive*, *Historical Newspaper*), and an optional historical photograph URL (`photo_url`, `photo_year`, `photo_caption`).
- **Report Missing or Incorrect Building Footprint:** Pre-selects `Footprint / Geometry Error` and populates a structured checklist (`Merged with neighboring building`, `Missing building footprint on parcel`, `Demolished structure still shown`, `Incorrect polygon rotation/shape`, or `Wrong building matched to HCAD account`).

### 1.2 Architecture of the Google Sheets Moderation Backend (`app/docs/google_apps_script_webhook.js`)
1. **Submission Ingestion (`POST` to Google Apps Script Web App):**
   - Public submissions are sent to a Google Apps Script webhook tied to Preservation Houston's Moderation Workbook and appended as `Pending` rows in the **`Pending_Submissions`** tab.
   - Simultaneously, the user's browser saves the submission in `localStorage` (`ph_atlas_local_submissions`) and displays a `"Pending Your Local Review"` badge so the researcher can immediately preview their suggested date and photo on their own map.
2. **One-Click Staff Approval (`Admin_Hub_&_Quick_Filter` & `Approved_Edits`):**
   - Preservation Houston staff review incoming submissions using the workbook's **`Admin_Hub_&_Quick_Filter`** dashboard or any of the **12 Saved Filter Views** (*Pending Date Corrections*, *Footprint & Geometry Errors*, *Submissions with Archival Photos*, *City Directory Citations*, *Sanborn Map Citations*, etc.).
   - Changing a row's `Status` dropdown to **`Approved`** triggers an `onEdit(e)` Google Apps Script hook that copies the verified record into the published **`Approved_Edits`** tab.
3. **Live Client-Side CSV Hydration (`csvUrl` in `config.js`) & Offline Bundling:**
   - On startup, the atlas fetches the published `Approved_Edits` CSV endpoint (`cache: "no-store"`), parses approved overrides in memory, and applies them on top of both `buildings.geojson` and the 5 countywide PMTiles shards in real time.
   - Staff with an unlocked **Staff Admin** session (`#btn-admin-unlock`) can also click **Approve & Apply Directly** inside the map UI to push verified corrections straight into `Approved_Edits` and `curated_overrides.json`.

---

## 2. Live HCAD Property Appraisal & Token-Brokered API Integration (`hcadService.js`)

While the countywide PMTiles shards and `permits_<prefix3>.json` files bundle HCAD structural dates, square footages, land uses, and permit histories for instant offline rendering, researchers often need to inspect **live, current-year HCAD ownership, legal descriptions, land/improvement appraised values, and building sub-area breakdowns**.

`app/js/hcadService.js` implements a resilient multi-tier live HCAD lookup service when the user clicks **Fetch Live HCAD Appraisal Record** in the Inspector Panel:
1. **Tier 1 - HCAD Public ArcGIS REST FeatureServer (`arcweb.hcad.org`):**
   - Queries HCAD's live public parcel & account summary layer by 13-digit `HCAD_NUM`, returning current owner name, mailing address, legal description (`lgl_1..4`), state property class, land area, total building area, market value, and appraised value.
2. **Tier 2 - Encrypted Token Handshake with `api.hcad.org` (`server.py` Proxy):**
   - HCAD's modern Blazor web portal (`search.hcad.org`) authenticates requests to `https://api.hcad.org/api/RealProperty/...` using a short-lived AES-encrypted bearer token minted by `https://search.hcad.org/api/Token`.
   - When running via `app/server.py`, the local backend can proxy the token handshake and fetch detailed sub-building CAMA tables (`building_res`, `building_other`, `extra_features`, `deeds`) on demand, caching responses in `sessionStorage` so repeated clicks are instantaneous.

---

## 3. O(1) Global Search Index & Pre-Warmed Instant Autocomplete (`search_index.json`)

With `1,507,553` countywide buildings, `7,242` curated inner-core buildings, `1,387` neighborhoods, `5,573` platted subdivisions, `509` designated landmarks, `359` Good Brick Awards, `73` Lost Houston sites, `225` waterways, `80` railroads, and `29,467` HAIF threads, naive linear scanning on keystroke would freeze the browser main thread.

Instead, the atlas uses a two-stage search architecture (`app/public/data/search_index.json` + in-memory spatial/overlay indices in `app.js`):
1. **Pre-Warming on Idle (`requestIdleCallback`):**
   - One second after initial map load, `_ensureSearchIndexLoaded()` asynchronously fetches and indexes `search_index.json` (`12 MB` uncompressed, `~1.8 MB` gzipped) in the background so that by the time the user clicks the search bar (`#search-input`), the index is already warm in memory.
2. **Multi-Faceted Instant Querying:**
   - Typing in `#search-input` searches across:
     - **Street Addresses & 13-Digit HCAD Account Numbers** (`408 Marshall St`, `0370370000007`)
     - **Canonical Building Names & Historical Aliases (`AKA`)** (`Gulf Building`, `Capitol Hotel`, `Transco Tower`, `Last Concert Café`)
     - **Architects & General Contractors** (`Alfred C. Finn`, `William Ward Watkin`, `John Staub`, `Russell Brown`, `Joseph Finger`, `MacKie & Kamrath`)
     - **Architectural Styles** (`Art Deco`, `Queen Anne`, `Craftsman`, `Mid-Century Modern`, `Spanish Eclectic`)
     - **Neighborhoods, Historic Wards, Platted Subdivisions, Waterways & Railroads** (`Freedmen's Town`, `Sixth Ward`, `Woodland Heights`, `Columbia Tap`, `Buffalo Bayou`)
     - **Lost Houston Demolished Landmarks** (`† The Capitol Hotel (1883-1912)`, `† Majestic Theatre`)
   - Selecting any result automatically enables the corresponding map layer if hidden, flies the camera smoothly to the feature's geometry, opens the full archival dossier in the Inspector Panel, and pushes a browser history entry (`window.history.pushState`) so the user can navigate back and forth with the browser **Back** and **Forward** buttons.

---

## 4. Complete Data Pipeline Rebuild Checklist (`pipeline/atlas_pipeline/`)

All 26 data processing scripts live under `pipeline/atlas_pipeline/` and are managed with `uv` (`Python 3.12+`, `pyogrio`, `shapely`, `pyproj`, `numpy`, `scipy`, `pillow`). To run a full rebuild or incremental update:

| Pipeline Script | Primary Input Sources | Output Artifacts Updated | Purpose |
| :--- | :--- | :--- | :--- |
| `ingest.py` / `build_countywide_shards.py` | HCAD `Parcels.gdb`, CAMA `.txt` tables, OSM Overpass, Microsoft `MSBFP2` | Raw quadrant GeoJSONs | Spatial join of 1.85M footprints to 1.55M HCAD parcels (`EPSG:2278 -> EPSG:4326`). |
| `recover_dropped_footprints.py` | `Parcels.gdb` (`MultiPolygon` sub-parts), OSM Overpass | Quadrant GeoJSONs, `buildings.geojson` | Recovers 6,315 footprints on multi-block `MultiPolygon` parcels (`301 St Joseph Pkwy`) and performs 2-stage `STRtree` deduplication. |
| `fix_polygon_orientations.py` | `Parcels.gdb`, Quadrant GeoJSONs | 5 `.pmtiles` shards, `buildings.geojson` | Replaces 155,497 cardinal fallback boxes with `shapely.oriented_envelope` rotated footprints (`-34.65°` Borden Survey grid) and fixes 5,609 `ring[0]` party-wall shifts. |
| `fix_warehouse_district.py` | HCAD sub-buildings, OSM, aerial survey | `curated_overrides.json`, `buildings.geojson` | Removes 420m Elysian Viaduct bridge deck from `902 Hardy St`, splits `Last Concert Café & Richey-Fant House`, suppresses transit canopies. |
| `fix_industrial_multi_footprints.py` | OSM `man_made=storage_tank`, Esri `World_Basemap_v2` MVT tiles | 5 `.pmtiles` shards, `curated_overrides.json` | Restores 4,746 multi-building industrial footprints and extracts 7,292 circular refinery storage tanks (`12,038` total footprints). |
| `enrich_institutions.py` | Rice GIS, TSU GIS, UH GIS, Hermann Park / Zoo / Heritage Society | `buildings.geojson`, `curated_overrides.json` | Splits tax-exempt super-parcels into 525 individually dated campus buildings (excluding Rice `Covered Walkway` roofs). |
| `resolve_undated_countywide.py` | HCAD `extra_features`, `parcel_tieback`, `permits`, `deeds`, `subd_code` | 5 `.pmtiles` shards, `curated_overrides.json` | 4-phase resolution bringing all 43,983 undated (`year_built == 0`) footprints to 100.00% dated coverage with `year_source` provenance. |
| `city_directory_audit.py` | HPL ContentDM (`cdm17006.contentdm.oclc.org`, `1866-1926` directories) | `curated_overrides.json` | Audits 3,176 rounded-decade properties using `^exact^and` phrase matching and browser `User-Agent` headers. |
| `good_brick_awards.py` | Preservation Houston Good Brick Archive (`1979-2026`) | `overlays.json` (`good_brick_awards`) | Filters 460 raw entries to 359 place-based awards (`339` sites) and snaps coordinates onto verified building footprints. |
| `crawl_landmark_reports.py` & `apply_landmark_report_enrichments.py` | `archaeology.houstontx.gov` (`var mydata`) + Wayback CDX API | `overlays.json`, `curated_overrides.json`, `buildings.geojson` | Links 448 of 509 COH Landmarks to official HPO Designation Report PDFs, fixes 11 HPO filename typos, fills +140 dates and +144 styles. |
| `crawl_haif_forum.py`, `apply_haif_enrichments.py`, `enrich_building_names.py` | Wayback CDX (`29,467` HAIF threads) + `media.invisioncic.com` | `haif_index.json`, `building_photos.json`, `search_index.json` | Links 7,741 footprints to 3,385 HAIF threads, populates `building_name` + `alt_names` (`4,833` HCADs), fixes URL-escaped UTF-8 and slash slugs. |
| `extract_peter_brown_geopdf.py` & `build_neighborhoods.py` | 2009 Peter Brown GeoPDF (`/OC2`, `/OC13`, `/OC4`), COH GIS | `overlays.json` (`neighborhoods`, `historic_wards`, `annexations`) | Extracts 661 Peter Brown neighborhoods (`1,387` total), fixes 11 COH GIS typos, reconstructs 1839-1905 Charter Wards, separates 1963 highway spokes. |
| `build_deed_restrictions.py` | `Sub_poly.zip`, `Parcels.gdb`, `real_acct.txt` (`lgl_2`+`lgl_3`), COH Ch. 42 | `overlays.json` (`subdivisions`), `deed_restrictions_catalog.json` | Synthesizes 5,573 platted subdivisions, fixes WHCA `BELTZ -> Belt 3` & `lgl_3` bugs, links 65 local deed PDFs + 910 SMLS/SMBL/Conservation zones. |
| `build_lost_houston_and_sanborn.py` | LOC Sanborn IIIF API (`626` sheets), historical surveys | `sanborn_catalog.json`, `overlays.json` (`lost_houston`) | Indexes 10 Sanborn volumes (`1885-1955`) at cached `pct:12.5`/`pct:25` scales and maps 73 demolished landmarks fitted to `-34.65°` Borden Survey parcels. |
| `build_waterways_and_railroads.py` | USGS NHD, HCFCD (`M3` basins), TNRIS, FraN, TxDOT, 1869-1954 maps | `overlays.json` (`waterways`, `railroads`, `rail_depots`) | Builds 218 open bayous/creeks + 7 buried historic gullies (`1,896.8 km`), 80 rail/streetcar corridors, and 14 historic rail depots. |
| `build_permits_and_contractors.py` | HCAD `permits.txt` (`1.3M`), `building_res.txt`, HMRC ContentDM (`T.G.C.A.`) | `permits/permits_<prefix3>.json` (`152` shards), `overlays.json` | Builds 152 permit shards (`1.26M` accounts), extracts 1917-1929 HMRC contractor monographs & T.G.C.A. awards, filters `permit_type=15` (`OMITTED IMPS`). |
| `update_pmtiles_from_overrides.py` & `split_pmtiles_quadrants.py` | Quadrant GeoJSONs, `curated_overrides.json`, `tippecanoe v2.82.0` | `houston_buildings_{nw_w,nw_e,ne,sw,se}.pmtiles` | Compiles the 5 Zoom-11 tile-aligned PMTiles shards (`< 80 MB` each, `1,507,553` footprints, zero dropped buildings at `z >= 12`). |

---

[← Chapter 7: Waterways, Railroads, Permits & Contractors](07-waterways-railroads-permits-and-contractors.md) | [Back to Master README](../README.md)
