# The Houston Building Atlas v2 — Preservation Houston

An open-source, zero-hosting-cost interactive historical mapping application for **[Preservation Houston](https://www.preservationhouston.org/atlas/map)**, built to replace the legacy Google Earth Engine implementation with a modern **PMTiles v3 + MapLibre GL JS** vector architecture.

## Key Capabilities

1. **Actual Building Outlines + Tax Parcel Toggle:**
   - Renders individual **building footprint polygons** (observed OpenStreetMap/planimetric outlines spatially joined to HCAD tax parcels, supplemented by oriented architectural footprints synthesized from parcel geometry and CAMA floor area).
   - Users can switch seamlessly between **Buildings**, **Buildings + Lots**, and **Tax Parcels** (the classic v1 view).
2. **Three Cartographic Color Lenses:**
   - **Age of Structure (`year_built`):** Supports both the new **Archival 9-Step Spectrum** (Deep Crimson/Terracotta `1836` $\rightarrow$ Ochre/Amber `1900s–1920s` $\rightarrow$ Sage/Verdigris `1940s–1970s` $\rightarrow$ Indigo `2000s+`) and the **Classic Atlas v1 (`YlOrRd`)** Earth Engine ramp.
   - **Historic Preservation Status:** Color-codes structures by `Protected Landmark (PLM)`, `Designated Landmark (LM)`, `Contributing in Historic District`, `Non-Contributing`, and `Outside Historic District`.
   - **Land Use Category:** Color-codes by `Single-Family / Residential`, `Multi-Family`, `Commercial`, `Civic / Institutional`, `Industrial`, and `Vacant / Untaxed`.
3. **Interactive Growth Time-Lapse & Live Viewport Decade Histogram:**
   - Play/Pause time-lapse scrubber ($1\times / 2\times / 5\times$ speed) that animates Houston's structural growth from `1836` to `2026`.
   - Optional **Sync Houston Annexation Boundary** toggle that expands the City of Houston's historical boundaries decade-by-decade alongside building construction.
   - Live **Viewport Decade Histogram** (`1840s`–`2020s`) that recalculates as you pan/zoom; click any decade bar to solo that decade.
4. **3D Extruded Building View:**
   - One-click **3D Extrusion** mode tilts the camera and extrudes building footprints by stories/height (`height_m`) derived from OSM building levels and HCAD building-to-footprint floor area ratios.
5. **Rich Civic & Preservation Overlays:**
   - City of Houston Designated Landmarks & Protected Landmarks (`509` sites)
   - City of Houston Historic Districts (`22` districts)
   - Freedmen's Town Heritage District
   - National Register of Historic Places (NRHP) Districts
   - Texas Historical Commission (THC) Markers (`596` in Harris County)
   - City of Houston Annexation History by Decade (`1836–2020`)
6. **Instant Search, Curated Neighborhood Tours & Property Inspector Drawer:**
   - Instant client-side search across addresses, landmarks, historic districts, and 13-digit HCAD account numbers.
   - One-click guided neighborhood jumps (*Old Sixth Ward*, *Houston Heights*, *Freedmen's Town*, *1836 Townsite & Market Square*, *Avondale & Westmoreland*, *Boulevard Oaks & Broadacres*, *Glenbrook Valley*).
   - Slide-over **Archival Property Record Drawer** with direct links to official HCAD property records and shareable URL hash permalinks.

---

## Repository Structure

```text
houston-building-atlas/
├── pipeline/                        # Offline Python 3.13 geospatial ETL & PMTiles compiler
│   ├── pyproject.toml               # uv project configuration
│   ├── atlas_pipeline/
│   │   ├── schema.py                # HCAD CAMA + COH Historic normalization & height estimation
│   │   ├── spatial_join.py          # STRtree spatial join (building footprints <-> parcels)
│   │   ├── fetch_data.py            # COH ArcGIS REST, HCAD bulk ZIP, and OSM Overpass fetchers
│   │   ├── tile_builder.py          # PMTiles v3 MVT compiler, search index & stats generator
│   │   └── cli.py                   # CLI entrypoint (--mode core | --mode full)
│   └── tests/                       # Pytest + Node.js verification suite
├── app/                             # Zero-build static web application (HTML5 + CSS + ES Modules)
│   ├── index.html                   # Main application shell
│   ├── server.py                    # HTTP Range-capable (RFC 7233 HTTP 206) static server
│   ├── styles/index.css             # Archival Cartographic Studio theme
│   ├── js/
│   │   ├── palettes.js              # Color ramps, MapLibre expressions & curated tours
│   │   ├── filterStore.js           # Reactive state store, filter compiler & URL hash sync
│   │   ├── mapController.js         # MapLibre GL JS + PMTiles rendering & viewport telemetry
│   │   └── app.js                   # UI controls, time-lapse player, search & inspector drawer
│   └── public/data/                 # Generated static data bundle (PMTiles + GeoJSON + JSON)
└── docs/superpowers/                # Design specification & implementation plan
```

---

## Quick Start

### 1. Run the Data Pipeline

The pipeline uses [`uv`](https://docs.astral.sh/uv/) for reproducible Python dependency management:

```bash
cd pipeline

# Fast Historic Houston Core build (~15,000 structures, all 22 Historic Districts, 509 Landmarks, 596 THC Markers)
uv run python -m atlas_pipeline.cli --mode core

# Full County build (~1.5M+ HCAD parcels + CAMA tables from download.hcad.org)
uv run python -m atlas_pipeline.cli --mode full
```

### 2. Run the Automated Test Suite

```bash
cd pipeline
uv run pytest -v
```

### 3. Launch the Local Web Application

Because `PMTiles` uses HTTP `Range: bytes=...` (`206 Partial Content`) requests, use `app/server.py`:

```bash
python3 app/server.py --port 8085
```

Then open `http://localhost:8085` (or `http://davemorris2.c.googlers.com:8085` on Cloudtop).

---

## Zero-Cost ($0/Month) Production Deployment

1. **Static Frontend (`app/`):** Deploy to **Cloudflare Pages** or **GitHub Pages** ($0/month).
2. **Vector Tiles (`houston_atlas.pmtiles`):** Upload `app/public/data/houston_atlas.pmtiles` to a public **Cloudflare R2** bucket with CORS enabled for `GET` and `HEAD` (`Range` header allowed). Cloudflare R2 charges **$0 egress fees** and natively supports HTTP `206 Partial Content` byte-range requests.
