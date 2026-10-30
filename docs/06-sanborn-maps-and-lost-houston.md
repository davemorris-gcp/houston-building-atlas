# Chapter 6: Library of Congress & UT Austin PCL Sanborn Fire Insurance Atlases, Historical Map Spyglass & Lost Houston

[← Chapter 5: Neighborhoods, Wards, Subdivisions & Deeds](05-neighborhoods-wards-subdivisions-and-deeds.md) | [Back to Master README](../README.md) | [Next: Chapter 7 - Waterways, Railroads, Permits & Contractors →](07-waterways-railroads-permits-and-contractors.md)

---

## 1. Georeferenced Historical Maps (`1869-1969`) & Interactive Spyglass Lens

To allow researchers to compare extant footprints directly against historical cartography and aerial photography, the atlas integrates **18 georeferenced historical map and aerial layers (`1869-1969`)** with both full-overlay opacity blending and a draggable **Spyglass Lens** (`#spy=map_id`).

| ![Interactive Spyglass Lens Comparing 1869 W. E. Wood Map to Extant Buildings](images/06_spyglass_1869_wood_map.jpg) | ![Library of Congress 1907 Sanborn Fire Insurance Atlas Sheet 1 (Downtown / Allen's Landing)](images/06_source_loc_sanborn_1907_sheet3.jpg) |
| :---: | :---: |
| *Interactive Spyglass Lens Comparing 1869 W. E. Wood Map to Extant Buildings* | *Library of Congress 1907 Sanborn Fire Insurance Atlas Sheet 1 (Downtown / Allen's Landing)* |

### 1.1 Solving the Spyglass Lens Zoom-Offset & 3D Perspective Math (`mapController.js`)
The Spyglass Lens renders a circular magnifying portal over the WebGL canvas using an HTML5 2D overlay canvas (`#spyglass-canvas`). Two subtle coordinate-projection mismatches were diagnosed and fixed:
1. **Why the Spyglass Initially Looked Zoomed-Out (`512 px` vs. `256 px` World Size):**
   - Standard slippy-map tile math defines world size at zoom 0 as `256 pixels` ($256 \times 2^z$). However, MapLibre GL JS renders camera zoom levels using a **`512-pixel` world size at zoom 0** ($512 \times 2^z$), which is exactly $2\times$ larger (+1 zoom level) than a standard `256 px` raster tile pyramid!
   - When the initial Spyglass renderer computed tile coordinates using `256 * Math.pow(2, vpZoom)` centered on `map.getCenter()`, the historical map inside the circle was rendered at **half the scale (`0.5x`)** of the underlying MapLibre vector buildings, and drifted radially away from the cursor whenever the mouse moved away from the screen center!
   - **Fix:** The Spyglass renderer now computes `effectiveTileZoom = vpZoom + 1` and maps each screen pixel $(x, y)$ inside the lens circle through `map.unproject([x, y])` (and for tilted 3D views, samples a projective grid across the circle), guaranteeing pixel-accurate alignment between the historical map inside the lens and the modern buildings underneath at every zoom level, pan position, pitch, and bearing.
2. **Preventing Basemap Bleed-Through at High Zoom (`maxzoom` Clamping):**
   - Historical map tile services (`COH 1869 W. E. Wood`, `1895 Whitty`, `1913 Comey`, `Rice Fondren 1930-1969 Aerials`, `USGS Historical Topos`) have varying native maximum zoom levels (`z = 16` to `z = 19`). Requesting `z + 1` tiles above a service's native `maxzoom` returned `HTTP 404` or transparent blank tiles, causing the Spyglass to go blank when zooming in on an individual house. Clamping `tileZ = Math.min(maxZoom, Math.round(vpZoom + 1))` ensures smooth over-zooming down to parcel scale.

---

## 2. Digitized Sanborn Fire Insurance Atlases (`1877-1955`, `16` Volumes, `878` Sheets across LOC & UT Austin PCL)

Between 1877 and 1955, the **Sanborn Map Company** surveyed Houston block by block to assess fire insurance underwriting risk, recording building footprints, construction materials (**Yellow** = wood frame, **Pink** = brick, **Blue** = stone/concrete, **Gray** = iron/tin), roof composition, story heights, fire walls, and historic street numbers (including pre-1911 address numbers before Houston's citywide street renumbering ordinance).

`pipeline/atlas_pipeline/build_lost_houston_and_sanborn.py` and `build_ut_pcl_sanborn.py` index **`878` high-resolution digitized sheets across `16` Houston Sanborn volumes (`1877-1955`)** in `app/public/data/sanborn_catalog.json`:
- **`10` Library of Congress (`LOC`) Volumes (`1885`, `1890`, `1896`, `1907 Vol. 1`, `1907 Vol. 2`, `1924-1950 Vol. 1-4`, `1924-1955` - `626` IIIF Sheets)**; and
- **`6` University of Texas at Austin Perry-Castaneda Library (`UT PCL`) Volumes (`1877`, `1917`, `1919`, `1924 Vol. 2`, `1924 Vol. 3`, `1948 Vol. 6` - `252` Sheets mirrored locally as `504` optimized WebP plates in `app/public/data/sanborn_ut/` so inline views never hit UT's cross-origin Anubis PoW challenge; see [Chapter 9, Section 3.2](09-preservation-practice-social-history-and-hahc.md#32-expanding-sanborn-fire-insurance-coverage-from-10-loc-volumes-to-16-volumes-1877-1955-878-sheets))**.

### Four Library of Congress (`loc.gov`) IIIF & Metadata Pitfalls Solved

1. **LOC IIIF CDN Power-of-Two Scale Pre-Caching (`pct:12.5` & `pct:25` vs. `pct:35` Cold Decode):**
   - The Library of Congress serves Sanborn sheets from master JPEG2000 (`.jp2`) files via `https://tile.loc.gov/image-services/iiif/...`.
   - When our initial viewer requested arbitrary percentage scales such as `pct:35` or `pct:50`, every sheet took **4 to 7 seconds** to load because LOC's IIIF server had to decode the multi-megabyte JPEG2000 wavelet file on the fly (`X-Cache: Miss from cloudfront`).
   - Testing revealed that LOC's CloudFront CDN pre-warms and caches **exact power-of-two wavelet decomposition levels (`pct:6.25`, `pct:12.5`, `pct:25`, `pct:100`)** (`X-Cache: Hit from cloudfront`, `< 60 ms` response time!). Switching thumbnails to `pct:12.5` and modal full-sheet previews to `pct:25` (with a `High-Res Zoom (pct:100)` toggle) made browsing all 626 sheets instantaneous.
2. **Non-Sequential IIIF File Suffixes (`-covr`, `-0001s` Skeleton Maps, `-1101`, `-1201`):**
   - You cannot construct LOC Sanborn IIIF URLs by formatting `f"...-{sheet_num:04d}"`.
   - In the 1907 and 1924 volumes, LOC inserted unnumbered title covers (`-covr`), graphic index maps (`-0001`), **skeleton maps (`-0001s`, `-0002s`, `-0003s`, `-0004s`)**, and decimal supplement sheets (`-1101`, `-1201`), shifting the sequential image file number (`0007`) up to **6 positions ahead** of the printed Sanborn sheet number (`Sheet 3`)!
   - Our crawler (`build_lost_houston_and_sanborn.py`) fetched the full `?fo=json` manifest for all 10 LOC items and recorded each sheet's true `service_id`, `thumb`, `full`, and printed sheet `code`.
3. **Printed Sheet Number (`code`) vs. Sequential Manifest Index (`sp`) Resolution:**
   - When a Lost Houston landmark or historic building cites `"1907 Vol. 1 Sheet 3"`, matching against `sp == 3` opened the *Graphic Index Map* instead of *Sheet 3*! `_resolve_sanborn_sheet()` in `app.js` matches the printed Sanborn sheet `code` first before falling back to sequential `sp`.
4. **Canonical LOC Item URL Format (`/item/sanborn08580_...` vs. `/resource/g4034hm...`):**
   - LOC `/resource/g4034hm.g4034hm_g08580_1907_1/?sp=7` URLs fail (`HTTP 404`) when underscores are used in the resource slug (`g08580190701` has no underscores). All viewer links use the canonical `/item/{item_id}/?sp={sp}` URL format, which resolves `200 OK` across every volume.

---

## 3. Lost Houston: 622 Verified Demolished Historic Structures Across 5 Cohorts (`1837-2026`)

Over the past 190 years, Houston has lost not only its famed Downtown architectural monuments - from the **1837 First Capitol of the Republic of Texas** and **1883 Capitol Hotel** on the Rice Hotel site, to the **1901 Binz Building**, **1904 Carnegie Library**, **1910 City Auditorium**, **1911 Galveston-Houston Electric Interurban Terminal**, **1923 Majestic Theatre**, **1926 Lamar Hotel**, **1928 Shamrock Hotel**, and **1956 Houston Contemporary Arts Museum** - but also hundreds of Freedmen's Town, Third Ward, and Fifth Ward sanctuaries, neighborhood schools, designated landmarks, and undesignated early-20th-century bungalows.

![Lost Houston Demolished Landmarks in Downtown (The Capitol Hotel, 1883-1912)](images/06_lost_houston_capitol_hotel.jpg)

`pipeline/atlas_pipeline/build_lost_houston_and_sanborn.py` and `lost_houston_expansion.py` (together with `lost_houston_catalog.py` and `lost_houston_catalog_2.py`) act as the sole single-owner producer of `overlays.json["lost_houston"]`: **`622` verified, non-overlapping demolished historic structures (`1837-2026`)**, synthesized systematically across **5 authoritative archival and municipal source cohorts**:

| Cohort Key (`source_cohort`) | Label (`source_cohort_label`) | Count | Primary Sources & Evidentiary Provenance (`provenance_tier`) |
| :--- | :--- | :---: | :--- |
| **`curated_landmark`** | `Curated Survey & Archival Landmark` | **`85`** | Preservation Houston Lost Houston Survey, `1877-1955` LOC/UT Sanborn plates, HABS, HMRC Russell Brown / *T.G.C.A.* monographs, and verified major civic/midcentury losses (`Tier 1` / `Tier 2`). Includes `coh_lm_081` (*1937 Jefferson Davis Hospital*, HPO `#98LD74`) reconciled directly onto `lost_jefferson_davis_hospital_1937`. |
| **`coh_landmark_demolished`** | `City of Houston Designated Landmark (Demolished)` | **`7`** | City of Houston Historic Preservation Office Designated Landmarks confirmed demolished or destroyed by fire (`USER_DEMOD == 'Y'` / `DEMOLISHED` / `DESTROYED BY FIRE` in COH GIS + HPO reports, covering `Bill Mraz Dance Hall`, `Mallalieu UMC`, `Beatty-West Building`, `Tuttle House`, `Mount Carmel Missionary Baptist Church`, `T. J. & Ruth Bettes House`, and `Lucian Carroll House`; `Tier 1`). Erroneous COH GIS string glitches on extant `1206 Nance St` (`coh_lm_089`) and unconfirmed remodel flags (`98L060`, `12L273`, `13L278`) are strictly blocked. |
| **`nrhp_demolished`** | `National Register Property (Demolished / Gone)` | **`15`** | National Park Service / Texas Historical Commission `"Gone"` NRHP listings in Harris County (`Tier 1`), while relocated **Arthur B. Cohn House** (`85002771`, standing today at Discovery Green / Avenida de las Americas) is enriched with `year_built: 1905` in `overlays["nrhp_properties"]` and blocked from `lost_houston`. All `16` `"Gone"` NRHP rows carry verified non-zero `year_built` values in both `overlays.json` and `search_index.json`. |
| **`red_book_1915_vanished`** | `1915 Red Book of Houston (Vanished Place)` | **`205`** | Rice University Woodson Research Center *1915 Red Book of Houston* cleared sites (`PointAddress`/`StreetAddress` rooftop precision only) covering vanished historic Black schools, sanctuaries, photo-documented homes (with inline Quartex IIIF plates), and businesses inside 1907 Sanborn Atlas coverage (`Tier 1` / `Tier 2`), exposing original pre-renaming street strings (`historic_address_1915`) and honest `year_built_is_circa` / `year_demolished_is_circa` lifespans. |
| **`historic_teardown_2020_2026`** | `2020-2026 Pre-1940 Historic Teardown` | **`310`** | Two-stage verified full main-building structural demolition permits (`2020-2026 YTD` across HCAD `permits.txt` Types `13/33/43` and COH `SD`/`4D` permits screened by `schema.classify_main_structure_demolition_permit`) cross-confirmed against cleared/post-2000-rebuilt `2026` CAMA state classes with pre-demolition `aligned_ndjson/` roof geometry where surveyed (`Tier 2` / `Tier 3`, labeling rounded HCAD decade years as `c. YYYY` + `HCAD Est.`). |

### 3.1 Zero-Extant-Collision Gate, Zoom-Tiered `symbol_rank` Decluttering & `-34.65°` Borden Parcel Fitting
Scaling *Lost Houston* from `74` Downtown monuments to `622` countywide historic losses required three engineering safeguards:
1. **`ZeroExtantCollisionGate` (`lost_houston_expansion.py`):**
   - Deterministically blocks any automated Cohort 2-5 candidate colliding by 13-digit HCAD ID (`hcad_num` / `site_hcad_nums`), canonical historical-alias street key (`normalize_houston_address_keys`), designation ID, `<= 14m` protected-point proximity, or `> 20%` polygon area overlap against **(a)** extant COH Landmarks (`501`), **(b)** surviving Good Brick Award winners, **(c)** the `29` surviving `1915 Red Book` buildings, **(d)** the `222` extant NRHP properties, **(e)** all `relocated_structures` and `was_relocated` overrides, **(f)** `14` pinned iconic monuments, and **(g)** standing pre-1945 inner-core and countywide buildings (including tax-exempt churches with `$0` taxable `impr_val` such as *Wesley Chapel AME*). Verified dynamically on every pipeline build by `audit_historical_accuracy.py` (`lost_houston_extant_collision_errors == 0`).
2. **MapLibre Spec-Compliant Zoom-Tiered `symbol_rank` Hierarchy (`mapController.js`):**
   - To keep district zooms legible across `622` polygons without violating the MapLibre Style Specification (which forbids nesting `["zoom"]` inside composite boolean filters) and without starving rank-1 collision boxes behind invisible `text-opacity: 0` glyphs, `"lost-houston-labels-src"` drives **three zoom-bounded symbol layers** (`LOST_HOUSTON_LABEL_TIERS`) sorted by `symbol-sort-key: ["coalesce", ["to-number", ["get", "symbol_rank"]], 4]`:
     - **`symbol_rank == 1` (`minzoom: 12.5`):** `92` major architectural/civic monuments (`85` curated landmarks + `7` demolished COH landmarks).
     - **`symbol_rank == 2` (`minzoom: 14.5`):** `51` demolished NRHP properties (`15`) and vanished 1915 *Red Book* churches, schools, hospitals, and halls (`36`).
     - **`symbol_rank >= 3` (`minzoom: 15.8`):** `479` parcel-level *Red Book* civic enterprises (`48` `rank 3`), *Red Book* storefronts/residences (`121` `rank 4`), and `2020-2026` pre-1940 teardowns (`310` `rank 4`).
   - Identical `symbol_rank` gating and `featureMatchesLostHoustonFilter(p, state)` temporal parity run inside the 2D Canvas fallback renderer (`drawBoundaryLabels`), while all polygon fills/lines/extrusions render continuously from `z >= 11` using a shared Sanborn material palette resolver (`resolveSanbornMaterialColors` in `palettes.js`).
3. **Footprint Geometry Provenance & `-34.65°` Borden Survey Fitting (`fit_oriented_ghost_footprint`):**
   - Every feature records an explicit `footprint_provenance` badge (`surveyed_sanborn_or_parcel`, `surveyed_catalog`, `ms_osm_pre_demo_footprint`, `ghost_parcel_aligned` fitted via 9x9 point-in-polygon search inside `Parcels.gdb` at `-34.65°` Borden Survey or parcel bearing, or `ghost_street_aligned`).

### 3.2 Qualifier-Aware Lifespans, Photo Isolation, Bidirectional Cross-Referencing & Historical Snapshot Mode
- **Honest Qualifier-Aware Lifespan Labels:** Numeric `year_built` and `year_demolished` integers power GPU timeline math strictly behind the scenes while `year_built_display`, `year_demolished_display`, and `lifespan_label` render honest historical qualifiers (`c. 1905 - pre-1968`, `On or before 1915 - post-1950`, `c. 1920 (HCAD Est.) - 2023`) across map labels, hover tooltips, portfolio map pins, `search_index.json` chips, and the Property Inspector.
- **Strict Demolished-vs-Extant Photo Isolation (`photoService.js`):** All `184` Lost Houston photo records (`129` curated monument archival views + `55` Rice Woodson *1915 Red Book* IIIF plates across `338` total curated records in `building_photos.json`) carry `is_lost_houston: true`, index exclusively under `byBuildingId["lost_..."]`, and never pollute parcel-wide `byHcad` or `byAddress` carousels of modern replacement homes/towers standing on the site today; similarly, inspecting any `lost_*` structure (with or without a curated photo) never falls back to standing-building parcel photos or live 42m Wikimedia Commons streetscape geosearches.
- **Clicking an Extant Building on a Demolished Site (`findLostHoustonAtSite`):** Clicking an extant replacement building (matching by 13-digit HCAD / `site_hcad_nums`, or geometric containment inside a verified surveyed multi-lot block footprint or same-address historic site) displays a dedicated **"Previous Historic Structure(s) on This Site"** dossier card in the Inspector Panel while keeping the standing building's own photographs cleanly separated.
- **Historical Snapshot Mode (`#histSnap=1`) & Synchronized Equity Rollups:** Enabling **Historical Snapshot Mode** filters both extant buildings (`year_built <= maxYear`) and Lost Houston structures (`year_built <= maxYear AND year_demolished > maxYear`), while `reconcile_holc_lost_houston_rollups` and `build_neighborhood_scorecards.py` synchronize all `622` losses identically across all `47` 1937 HOLC Redlining zones (`393` assigned residential-zone sites: `44` Grade A, `42` Grade B, `139` Grade C, `168` Grade D) and all `4,242` Neighborhood & Council District Preservation Scorecards (`lhc`).

---

[← Chapter 5: Neighborhoods, Wards, Subdivisions & Deeds](05-neighborhoods-wards-subdivisions-and-deeds.md) | [Back to Master README](../README.md) | [Next: Chapter 7 - Waterways, Railroads, Permits & Contractors →](07-waterways-railroads-permits-and-contractors.md)
