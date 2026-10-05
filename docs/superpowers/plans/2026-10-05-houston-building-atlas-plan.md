# The Houston Building Atlas v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the complete Houston Building Atlas v2—including an automated Python geospatial data & PMTiles pipeline (`pipeline/`) and a zero-backend, 60 FPS interactive web mapping application (`app/`) for Preservation Houston.

**Architecture:** A two-part repository where `pipeline/` fetches HCAD, City of Houston ArcGIS REST layers, and Open Building Footprints, spatially joins parcel and historic preservation attributes onto building footprint polygons, and outputs a static `.pmtiles` vector tileset + GeoJSON/JSON indices into `app/public/data/`. The `app/` directory is a static modular web application using MapLibre GL JS + PMTiles that renders 2D/3D building footprints, tax parcels, historic districts, landmarks, and annexation boundaries with `<16ms` GPU filtering, an animated time-lapse scrubber, a live viewport decade histogram, and a property/landmark inspector drawer.

**Tech Stack:** Python 3.13 (`uv`, `shapely`, `pyproj`, `httpx`, `pmtiles`, `mapbox-vector-tile`, `pytest`), HTML5/CSS3/ESModules + MapLibre GL JS + PMTiles JS protocol.

**Spec:** `/usr/local/google/home/davemorris/houston-building-atlas/docs/superpowers/specs/2026-10-05-houston-building-atlas-design.md`

## Global Constraints

- Hosting cost must be **$0/month** (100% static assets + HTTP Range Request `.pmtiles` + GeoJSON fallbacks; zero paid API keys required).
- Every building footprint and parcel feature must adhere to the 20-field normalized schema (`id`, `hcad_num`, `address`, `year_built`, `decade`, `remodel_year`, `owner`, `bld_area`, `land_area`, `stories`, `height_m`, `use_category`, `landuse_desc`, `bld_style`, `subdivision`, `historic_district`, `contributing`, `landmark_name`, `landmark_type`, `architect`).
- Both `--mode core` (Historic Houston Core slice: Downtown, Freedmen's Town, The Heights, Old Sixth Ward, Montrose, Midtown, Third Ward, Museum District, Glenbrook Valley) and `--mode full` (Harris County bulk `Parcels.zip` + `Real_building_land.zip` + `Real_acct_owner.zip`) must be supported by the pipeline CLI.
- All interactive UI controls must have unique, descriptive DOM IDs and support URL query-string state persistence (`?lat=...&lng=...&zoom=...&minYear=...&maxYear=...&lens=...`).

---

## File Structure

| File | Action | Responsibility |
| :--- | :--- | :--- |
| `pipeline/pyproject.toml` | Create | Python `uv` project configuration and dependencies (`shapely`, `pyproj`, `httpx`, `pmtiles`, `mapbox-vector-tile`, `pytest`) |
| `pipeline/atlas_pipeline/__init__.py` | Create | Package exports |
| `pipeline/atlas_pipeline/schema.py` | Create | Feature normalization, year/decade parsing, land-use categorization, and HCAD/COH attribute mapping |
| `pipeline/atlas_pipeline/spatial_join.py` | Create | R-tree (`shapely.STRtree`) spatial join of building footprints to parcels + architectural footprint inset synthesizer for parcels missing external footprints |
| `pipeline/atlas_pipeline/fetch_data.py` | Create | Live downloader for City of Houston ArcGIS REST layers (Cadastral Parcels, Authoritative Historic Parcels, Landmarks `LM`/`PLM`, Historic/Heritage/NRHP Districts, Annexation History 1836–2020, THC Markers), OpenStreetMap Overpass Building Footprints, and HCAD bulk ZIP archives |
| `pipeline/atlas_pipeline/tile_builder.py` | Create | Generates `houston_atlas.pmtiles` (MVT vector tiles across zooms 10–16), `buildings.geojson`, `parcels.geojson`, `overlays.json`, `search_index.json`, and `stats_summary.json` |
| `pipeline/atlas_pipeline/cli.py` | Create | CLI entrypoint (`python -m atlas_pipeline.cli --mode core\|full`) |
| `pipeline/tests/test_schema.py` | Create | Unit tests for normalization, year parsing, decade bucketing, and land-use classification |
| `pipeline/tests/test_spatial_join.py` | Create | Unit tests for footprint-to-parcel spatial joins and inset building footprint derivation |
| `pipeline/tests/test_tile_builder.py` | Create | Unit tests for `.pmtiles`, search index, and stats summary generation |
| `app/index.html` | Create | Semantic HTML5 shell for The Houston Building Atlas v2 |
| `app/styles/index.css` | Create | Archival Cartographic design system tokens, responsive layout, glassmorphic controls, timeline dock, histogram, and inspector drawer styles |
| `app/src/config/palettes.ts` (`app/js/palettes.js`) | Create | Color ramps (`archival`, `classic_ylorrd`, `contributing`, `use_category`), MapLibre paint expressions, and neighborhood presets |
| `app/src/state/filterStore.ts` (`app/js/filterStore.js`) | Create | Reactive state store, MapLibre filter expression builder, decade histogram calculator, and URL query sync |
| `app/src/map/MapController.ts` (`app/js/mapController.js`) | Create | MapLibre GL JS + PMTiles initialization, 2D/3D building footprint & parcel layers, historic overlays, hover tooltips, and click selection |
| `app/src/components/AppUI.ts` (`app/js/app.js`) | Create | Wires Header Search & Neighborhood Tours, Lens & Layer Sidebar, Interactive Timeline & Time-Lapse Scrubber, Live Decade Histogram, and Property/Landmark Inspector Drawer |
| `pipeline/tests/test_frontend_logic.py` | Create | Automated verification of frontend data contracts, filter logic, and generated bundle assets |

---

### Task 1: Pipeline Schema & Attribute Normalization (`pipeline/atlas_pipeline/schema.py`)

**Files:**
- Create: `pipeline/pyproject.toml`
- Create: `pipeline/atlas_pipeline/__init__.py`
- Create: `pipeline/atlas_pipeline/schema.py`
- Test: `pipeline/tests/test_schema.py`

**Interfaces:**
- Produces:
  - `normalize_year(raw_val: Any) -> int`
  - `compute_decade(year: int) -> int`
  - `classify_use_category(state_class: str, landuse_desc: str, group_desc: str) -> str`
  - `normalize_parcel_record(raw_props: dict[str, Any], historic_override: dict[str, Any] | None, landmark_override: dict[str, Any] | None) -> dict[str, Any]`

- [ ] **Step 1: Write the failing test in `pipeline/tests/test_schema.py`**

```python
import pytest
from atlas_pipeline.schema import (
    normalize_year,
    compute_decade,
    classify_use_category,
    normalize_parcel_record,
)


def test_normalize_year_valid_and_invalid():
    assert normalize_year("1912") == 1912
    assert normalize_year(1928.0) == 1928
    assert normalize_year("0") == 0
    assert normalize_year(None) == 0
    assert normalize_year("1750") == 0  # Before Houston founding range
    assert normalize_year("2035") == 0


def test_compute_decade():
    assert compute_decade(1836) == 1830
    assert compute_decade(1914) == 1910
    assert compute_decade(0) == 0


def test_classify_use_category():
    assert classify_use_category("A1", "Single-family Residential", "") == "Residential"
    assert classify_use_category("B1", "Multi-Family", "") == "Multi-Family"
    assert classify_use_category("F1", "Commercial", "") == "Commercial"
    assert classify_use_category("X1", "Exempt Public", "") == "Civic / Institutional"
    assert classify_use_category("F2", "Industrial", "") == "Industrial"
    assert classify_use_category("C1", "Vacant", "") == "Vacant / Exempt"


def test_normalize_parcel_record_merges_historic_and_landmark():
    raw_cadastral = {
        "TAX_ID": "0010020030004",
        "SITE_ADDR_1": "1506 HEIGHTS BLVD",
        "YR_IMPR": "1914",
        "OWNER_MAILTO": "JANE DOE",
        "TOTAL_BUILDING_AREA": 2450,
        "TOTAL_LAND_AREA": 6600,
        "STATE_CLASS": "A1",
        "LANDUSE_DSCR": "Single-family Residential",
        "ECON_BLD_CLASS": "1B",
        "LEGAL_DSCR_1": "LT 4 BLK 12 HOUSTON HEIGHTS",
    }
    historic_info = {
        "Historic_District_Name": "Houston Heights Historic District South",
        "Building_Classification": "Contributing",
    }
    landmark_info = {
        "USER_SITE_NAME": "Historic Heights Bungalow",
        "LandmarkDesignation": "Protected Landmark",
        "USER_YR_BUILT": 1912,
        "USER_ARCHITECT___BUILDER": "A. C. Finn",
        "USER_STYLE": "Craftsman Bungalow",
    }
    norm = normalize_parcel_record(raw_cadastral, historic_info, landmark_info)
    assert norm["hcad_num"] == "0010020030004"
    assert norm["address"] == "1506 HEIGHTS BLVD"
    assert norm["year_built"] == 1912  # Landmark historic date takes precedence when earlier/valid
    assert norm["decade"] == 1910
    assert norm["contributing"] == "Contributing"
    assert norm["historic_district"] == "Houston Heights Historic District South"
    assert norm["landmark_name"] == "Historic Heights Bungalow"
    assert norm["landmark_type"] == "Protected Landmark"
    assert norm["architect"] == "A. C. Finn"
    assert norm["bld_style"] == "Craftsman Bungalow"
```

- [ ] **Step 2: Run test to verify it fails**
Run: `cd /usr/local/google/home/davemorris/houston-building-atlas/pipeline && uv run pytest tests/test_schema.py -v`

- [ ] **Step 3: Implement `pipeline/atlas_pipeline/schema.py` and pass tests**

- [ ] **Step 4: Commit Task 1**

---

### Task 2: Spatial Join & Building Footprint Derivation (`pipeline/atlas_pipeline/spatial_join.py`)

**Files:**
- Create: `pipeline/atlas_pipeline/spatial_join.py`
- Test: `pipeline/tests/test_spatial_join.py`

**Interfaces:**
- Consumes: normalized parcel feature dicts from `schema.py`
- Produces:
  - `derive_building_footprint_from_parcel(parcel_geom: BaseGeometry, bld_area_sqft: float, land_area_sqft: float) -> BaseGeometry`
  - `join_footprints_to_parcels(parcel_features: list[dict[str, Any]], footprint_features: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]`

- [ ] **Step 1: Write the failing test in `pipeline/tests/test_spatial_join.py`**
- [ ] **Step 2: Run test to verify it fails**
- [ ] **Step 3: Implement `spatial_join.py` using `shapely.STRtree` so observed footprints inside parcels inherit parcel/historic attributes, and parcels with buildings lacking an external footprint get a clean, oriented architectural footprint polygon inside the lot**
- [ ] **Step 4: Run test to verify it passes and commit Task 2**

---

### Task 3: Live Data Fetcher & Tile/Index Compiler (`pipeline/atlas_pipeline/fetch_data.py`, `tile_builder.py`, `cli.py`)

**Files:**
- Create: `pipeline/atlas_pipeline/fetch_data.py`
- Create: `pipeline/atlas_pipeline/tile_builder.py`
- Create: `pipeline/atlas_pipeline/cli.py`
- Test: `pipeline/tests/test_tile_builder.py`

**Interfaces:**
- Consumes: `normalize_parcel_record`, `join_footprints_to_parcels`
- Produces:
  - `app/public/data/houston_atlas.pmtiles`
  - `app/public/data/buildings.geojson`
  - `app/public/data/parcels.geojson`
  - `app/public/data/overlays.json`
  - `app/public/data/search_index.json`
  - `app/public/data/stats_summary.json`

- [ ] **Step 1: Write unit tests in `pipeline/tests/test_tile_builder.py`**
- [ ] **Step 2: Implement `fetch_data.py`, `tile_builder.py`, and `cli.py`**
- [ ] **Step 3: Run `uv run pytest` and execute `uv run python -m atlas_pipeline.cli --mode core` to populate `app/public/data/` with real City of Houston + HCAD + OpenStreetMap building footprints & historic preservation layers**
- [ ] **Step 4: Commit Task 3**

---

### Task 4: Interactive Web Application (`app/`) & End-to-End Verification

**Files:**
- Create: `app/index.html`
- Create: `app/styles/index.css`
- Create: `app/js/palettes.js`
- Create: `app/js/filterStore.js`
- Create: `app/js/mapController.js`
- Create: `app/js/app.js`
- Test: `pipeline/tests/test_frontend_logic.py`

- [ ] **Step 1: Write automated frontend data & contract verification tests**
- [ ] **Step 2: Build the Archival Cartographic UI (`index.html`, `styles/index.css`, `js/palettes.js`, `js/filterStore.js`, `js/mapController.js`, `js/app.js`)**
- [ ] **Step 3: Run all tests (`uv run pytest`) and launch the HTTP server**
- [ ] **Step 4: Verify live in headless Chrome via DevTools MCP (`navigate_page`, `list_console_messages`, `take_screenshot`), test interactive filtering, lens switching, 3D extrusion, and property inspector, and commit Task 4**
