"""Data acquisition and normalization from City of Houston GIS, HCAD bulk downloads, and OpenStreetMap."""

from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import httpx
import shapefile
from pyproj import Transformer
from shapely.geometry import Polygon, mapping, shape

from atlas_pipeline.schema import normalize_parcel_record, normalize_year
from atlas_pipeline.spatial_join import _ensure_valid_polygon

# Verified Public Endpoints (No Auth / API Key Required)
COH_HISTORIC_PARCELS_URL = (
    "https://services.arcgis.com/NummVBqZSIJKUeVR/arcgis/rest/services/"
    "Authoritative_Historic_Layer_and_Updated_Landmarks/FeatureServer/2/query"
)
COH_LANDMARKS_URL = (
    "https://services.arcgis.com/NummVBqZSIJKUeVR/arcgis/rest/services/"
    "Authoritative_Historic_Layer_and_Updated_Landmarks/FeatureServer/0/query"
)
COH_CADASTRAL_PARCELS_URL = (
    "https://mycity2.houstontx.gov/gisweb01/rest/services/HoustonMap/Cadastral/MapServer/0/query"
)
COH_HISTORIC_DISTRICTS_URL = (
    "https://mycity2.houstontx.gov/gisweb01/rest/services/HoustonMap/Planning_and_Development/MapServer/8/query"
)
COH_HERITAGE_DISTRICTS_URL = (
    "https://mycity2.houstontx.gov/gisweb01/rest/services/HoustonMap/Planning_and_Development/MapServer/41/query"
)
COH_NRHP_DISTRICTS_URL = (
    "https://mycity2.houstontx.gov/gisweb02/rest/services/HoustonMap/Planning_and_Development/MapServer/9/query"
)
COH_ANNEXATION_BASE_URL = (
    "https://mycity2.houstontx.gov/gisweb01/rest/services/PDD/Annexation_History/MapServer"
)
THC_MARKERS_URL = (
    "https://services7.arcgis.com/2hv9bZMrcgZpr7i9/arcgis/rest/services/historical_marker/FeatureServer/0/query"
)
OVERPASS_URLS = [
    "https://z.overpass-api.de/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
]

HCAD_PARCELS_ZIP_URL = "https://download.hcad.org/data/GIS/Parcels.zip"
HCAD_BUILDING_LAND_ZIP_URL = "https://download.hcad.org/data/CAMA/2025/Real_building_land.zip"
HCAD_ACCT_OWNER_ZIP_URL = "https://download.hcad.org/data/CAMA/2025/Real_acct_owner.zip"

ANNEXATION_LAYERS: list[tuple[int, int, str]] = [
    (14, 1836, "1836 Original Townsite"),
    (13, 1840, "1839–1840 Annexation"),
    (12, 1900, "1900s Expansion"),
    (11, 1910, "1910s Expansion"),
    (10, 1920, "1920s Expansion"),
    (9, 1930, "1930s Expansion"),
    (8, 1940, "1940s Post-War Boom"),
    (7, 1950, "1950s Expansion"),
    (6, 1960, "1960s Space Age Expansion"),
    (5, 1970, "1970s Expansion"),
    (4, 1980, "1980s Expansion"),
    (3, 1990, "1990s Expansion"),
    (2, 2000, "2000s Expansion"),
    (1, 2010, "2010s Expansion"),
    (0, 2020, "2020s City Boundary"),
]

# Key historic core bounding boxes (min_lon, min_lat, max_lon, max_lat) for additional Cadastral context
CORE_INFILL_BOXES: list[tuple[str, tuple[float, float, float, float]]] = [
    ("downtown_market_square", (-95.370, 29.754, -95.355, 29.766)),
    ("freedmens_fourth_ward", (-95.386, 29.751, -95.372, 29.760)),
    ("third_ward_elgin", (-95.374, 29.726, -95.356, 29.740)),
    ("montrose_westmoreland", (-95.394, 29.738, -95.376, 29.750)),
]


def fetch_arcgis_geojson_paginated(
    url: str,
    cache_file: Path | None = None,
    where: str = "1=1",
    out_fields: str = "*",
    bbox: tuple[float, float, float, float] | None = None,
    page_size: int = 1000,
    max_features: int = 25000,
) -> list[dict[str, Any]]:
    """Fetch features from an ArcGIS REST FeatureServer/MapServer endpoint with pagination and caching."""
    if cache_file and cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text())
            if isinstance(cached, list) and len(cached) > 0:
                return cached
        except Exception:
            pass

    features: list[dict[str, Any]] = []
    offset = 0

    with httpx.Client(timeout=45.0, follow_redirects=True) as client:
        while len(features) < max_features:
            params: dict[str, Any] = {
                "where": where,
                "outFields": out_fields,
                "returnGeometry": "true",
                "outSR": "4326",
                "f": "geojson",
                "resultOffset": offset,
                "resultRecordCount": page_size,
            }
            if bbox is not None:
                params["geometry"] = f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}"
                params["geometryType"] = "esriGeometryEnvelope"
                params["inSR"] = "4326"
                params["spatialRel"] = "esriSpatialRelIntersects"

            resp = client.get(url, params=params)
            resp.raise_for_status()
            payload = resp.json()
            batch = payload.get("features") or []
            if not batch:
                break

            features.extend(batch)
            if len(batch) < page_size and not payload.get("exceededTransferLimit"):
                break
            offset += len(batch)

    if cache_file and features:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(features, separators=(",", ":")))

    return features


def fetch_osm_footprints_for_boxes(
    cache_file: Path,
    bboxes: list[tuple[float, float, float, float]],
) -> list[dict[str, Any]]:
    """Fetch OpenStreetMap building polygons via Overpass API across a list of (min_lon, min_lat, max_lon, max_lat) boxes."""
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text())
            if isinstance(cached, list) and len(cached) > 0:
                return cached
        except Exception:
            pass

    elements: list[dict[str, Any]] = []
    seen_way_ids: set[int] = set()

    with httpx.Client(
        timeout=30.0,
        headers={"User-Agent": "PreservationHoustonBuildingAtlas/2.0"},
    ) as client:
        for min_lon, min_lat, max_lon, max_lat in bboxes:
            overpass_query = (
                f'[out:json][timeout:25];'
                f'way["building"]({min_lat:.4f},{min_lon:.4f},{max_lat:.4f},{max_lon:.4f});'
                f'out body geom;'
            )
            for endpoint in OVERPASS_URLS:
                try:
                    resp = client.post(endpoint, data={"data": overpass_query})
                    if resp.status_code == 200:
                        data = resp.json()
                        batch_els = data.get("elements") or []
                        for el in batch_els:
                            wid = el.get("id")
                            if wid not in seen_way_ids:
                                seen_way_ids.add(wid)
                                elements.append(el)
                        break
                except Exception as exc:
                    print(f"  [Overpass warning] {endpoint}: {exc}")

    features: list[dict[str, Any]] = []
    for el in elements:
        if el.get("type") != "way":
            continue
        geom_pts = el.get("geometry") or []
        if len(geom_pts) < 4:
            continue
        coords = [(pt["lon"], pt["lat"]) for pt in geom_pts]
        if coords[0] != coords[-1]:
            coords.append(coords[0])
        try:
            poly = _ensure_valid_polygon(Polygon(coords))
            if poly is None or poly.area <= 0:
                continue
            features.append({
                "type": "Feature",
                "geometry": mapping(poly),
                "properties": el.get("tags") or {},
            })
        except Exception:
            continue

    if features:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(features, separators=(",", ":")))

    return features


def fetch_all_overlays(cache_dir: Path) -> dict[str, list[dict[str, Any]]]:
    """Fetch and normalize all historic overlay layers (Landmarks, Districts, Heritage, NRHP, THC Markers, Annexations)."""
    print("-> Fetching COH Historic Landmarks (509 designations)...")
    raw_landmarks = fetch_arcgis_geojson_paginated(
        COH_LANDMARKS_URL,
        cache_file=cache_dir / "coh_landmarks.json",
        page_size=1000,
    )
    landmarks: list[dict[str, Any]] = []
    for feat in raw_landmarks:
        geom = feat.get("geometry")
        if not geom:
            continue
        p = feat.get("properties") or {}
        pt = shape(geom).representative_point()
        desig_raw = str(p.get("LandmarkDesignation") or "Landmark").strip()
        desig = "Protected Landmark" if "Protected" in desig_raw or desig_raw == "PLM" else "Landmark"
        landmarks.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(pt.x, 6), round(pt.y, 6)]},
            "properties": {
                "name": str(p.get("USER_SITE_NAME") or p.get("Landmark_Name") or "Historic Landmark").strip(),
                "address": str(p.get("USER_SITE_ADDRESS") or "").strip(),
                "designation": desig,
                "year_built": normalize_year(p.get("USER_YR_BUILT")),
                "architect": str(p.get("USER_ARCHITECT___BUILDER") or "").strip(),
                "style": str(p.get("USER_STYLE") or "").strip(),
                "historic_district": str(p.get("USER_HISTORIC_DISTRICT") or "").strip(),
                "hcad_num": str(p.get("USER_HCAD_NUM") or "").strip(),
            },
        })

    print("-> Fetching COH Historic Districts (22 districts)...")
    raw_hdist = fetch_arcgis_geojson_paginated(
        COH_HISTORIC_DISTRICTS_URL,
        cache_file=cache_dir / "coh_historic_districts.json",
    )
    historic_districts: list[dict[str, Any]] = []
    for feat in raw_hdist:
        geom = feat.get("geometry")
        if not geom:
            continue
        p = feat.get("properties") or {}
        historic_districts.append({
            "type": "Feature",
            "geometry": geom,
            "properties": {
                "name": str(p.get("Historic_District") or p.get("SITE_NAME") or p.get("NAME") or "Historic District").strip(),
                "type": "City of Houston Historic District",
            },
        })

    print("-> Fetching COH Heritage Districts...")
    raw_herit = fetch_arcgis_geojson_paginated(
        COH_HERITAGE_DISTRICTS_URL,
        cache_file=cache_dir / "coh_heritage_districts.json",
    )
    heritage_districts: list[dict[str, Any]] = []
    for feat in raw_herit:
        geom = feat.get("geometry")
        if not geom:
            continue
        p = feat.get("properties") or {}
        heritage_districts.append({
            "type": "Feature",
            "geometry": geom,
            "properties": {
                "name": str(p.get("DISTRICT_NAME") or p.get("NAME") or "Freedmen's Town Heritage District").strip(),
                "type": "City of Houston Heritage District",
            },
        })

    print("-> Fetching National Register Historic Districts (NRHP)...")
    raw_nrhp = fetch_arcgis_geojson_paginated(
        COH_NRHP_DISTRICTS_URL,
        cache_file=cache_dir / "coh_nrhp_districts.json",
    )
    nrhp_districts: list[dict[str, Any]] = []
    for feat in raw_nrhp:
        geom = feat.get("geometry")
        if not geom:
            continue
        p = feat.get("properties") or {}
        nrhp_districts.append({
            "type": "Feature",
            "geometry": geom,
            "properties": {
                "name": str(p.get("RESNAME") or p.get("District_Name") or p.get("NAME") or "National Register District").strip(),
                "type": "National Register of Historic Places District",
            },
        })

    print("-> Fetching Texas Historical Commission (THC) Markers in Harris County...")
    raw_thc = fetch_arcgis_geojson_paginated(
        THC_MARKERS_URL,
        cache_file=cache_dir / "thc_markers_harris.json",
        bbox=(-95.95, 29.50, -94.90, 30.18),
        page_size=1000,
    )
    thc_markers: list[dict[str, Any]] = []
    for feat in raw_thc:
        geom = feat.get("geometry")
        if not geom:
            continue
        p = feat.get("properties") or {}
        thc_markers.append({
            "type": "Feature",
            "geometry": geom,
            "properties": {
                "name": str(p.get("indexname") or p.get("title") or p.get("MarkerTitle") or "THC Historical Marker").strip(),
                "address": str(p.get("address") or p.get("location") or "").strip(),
                "marker_num": str(p.get("markernum") or p.get("MarkerNum") or "").strip(),
            },
        })

    print("-> Fetching COH Annexation History by Decade (1836–2020)...")
    annexations_cache = cache_dir / "coh_annexations.json"
    if annexations_cache.exists():
        annexations = json.loads(annexations_cache.read_text())
    else:
        annexations = []
        for layer_id, decade_num, label in ANNEXATION_LAYERS:
            layer_url = f"{COH_ANNEXATION_BASE_URL}/{layer_id}/query"
            try:
                raw_annex = fetch_arcgis_geojson_paginated(
                    layer_url,
                    cache_file=cache_dir / f"annex_{decade_num}.json",
                    out_fields="OBJECTID",
                    page_size=500,
                )
                for feat in raw_annex:
                    raw_geom = feat.get("geometry")
                    if not raw_geom:
                        continue
                    simplified = shape(raw_geom).simplify(0.00025, preserve_topology=True)
                    if simplified.is_empty:
                        continue
                    annexations.append({
                        "type": "Feature",
                        "geometry": mapping(simplified),
                        "properties": {
                            "decade": decade_num,
                            "era_label": label,
                        },
                    })
            except Exception as exc:
                print(f"  [Annexation warning] Layer {layer_id} ({label}): {exc}")
        if annexations:
            annexations_cache.write_text(json.dumps(annexations, separators=(",", ":")))

    return {
        "landmarks": landmarks,
        "historic_districts": historic_districts,
        "heritage_districts": heritage_districts,
        "nrhp_districts": nrhp_districts,
        "thc_markers": thc_markers,
        "annexations": annexations,
    }


def fetch_core_parcels_and_footprints(
    cache_dir: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Fetch Historic Houston Core parcels, infill Cadastral parcels, OSM building footprints, and overlays."""
    overlays = fetch_all_overlays(cache_dir)

    # Build lookup of Landmark attributes by HCAD_NUM
    raw_landmarks = fetch_arcgis_geojson_paginated(
        COH_LANDMARKS_URL,
        cache_file=cache_dir / "coh_landmarks.json",
    )
    landmark_by_hcad: dict[str, dict[str, Any]] = {}
    landmark_poly_features: list[dict[str, Any]] = []
    for lf in raw_landmarks:
        props = lf.get("properties") or {}
        hcad = str(props.get("USER_HCAD_NUM") or "").strip()
        if hcad:
            landmark_by_hcad[hcad] = props
        if lf.get("geometry") and lf["geometry"].get("type") in ("Polygon", "MultiPolygon"):
            landmark_poly_features.append(lf)

    print("-> Fetching COH Authoritative Historic Parcels (7,424 historic district parcels)...")
    raw_hist_parcels = fetch_arcgis_geojson_paginated(
        COH_HISTORIC_PARCELS_URL,
        cache_file=cache_dir / "coh_historic_parcels.json",
        page_size=2000,
    )

    normalized_parcels: list[dict[str, Any]] = []
    seen_hcad: set[str] = set()

    for pf in raw_hist_parcels:
        geom = pf.get("geometry")
        if not geom:
            continue
        raw_p = pf.get("properties") or {}
        hcad = str(raw_p.get("HCAD_NUM") or "").strip()
        if hcad and hcad in seen_hcad:
            continue
        if hcad:
            seen_hcad.add(hcad)

        lm_override = landmark_by_hcad.get(hcad)
        norm_p = normalize_parcel_record(raw_p, historic_override=raw_p, landmark_override=lm_override)
        normalized_parcels.append({
            "type": "Feature",
            "geometry": geom,
            "properties": norm_p,
        })

    # Also add standalone landmark parcels across Houston (outside historic districts)
    for lf in landmark_poly_features:
        raw_p = lf.get("properties") or {}
        hcad = str(raw_p.get("USER_HCAD_NUM") or "").strip()
        if hcad and hcad in seen_hcad:
            continue
        if hcad:
            seen_hcad.add(hcad)
        norm_p = normalize_parcel_record(raw_p, landmark_override=raw_p)
        normalized_parcels.append({
            "type": "Feature",
            "geometry": lf["geometry"],
            "properties": norm_p,
        })

    # Fetch dense Cadastral infill parcels for Downtown, Fourth Ward/Freedmen's Town, Third Ward, and Montrose
    print("-> Fetching COH Cadastral infill parcels for Downtown, Freedmen's Town, Montrose & Third Ward...")
    cadastral_fields = (
        "TAX_ID,YR_IMPR,SITE_ADDR_1,STR_NUM,STR_NAME,STR_SFX,OWNER_MAILTO,"
        "TOTAL_BUILDING_AREA,TOTAL_LAND_AREA,STATE_CLASS,LANDUSE_DSCR,GROUP_DSCR,"
        "ECON_BLD_CLASS,LEGAL_DSCR_1,PDLandMark"
    )
    for box_name, bbox in CORE_INFILL_BOXES:
        try:
            infill_raw = fetch_arcgis_geojson_paginated(
                COH_CADASTRAL_PARCELS_URL,
                cache_file=cache_dir / f"cadastral_{box_name}.json",
                out_fields=cadastral_fields,
                bbox=bbox,
                page_size=1000,
                max_features=2500,
            )
            for pf in infill_raw:
                geom = pf.get("geometry")
                if not geom:
                    continue
                raw_p = pf.get("properties") or {}
                hcad = str(raw_p.get("TAX_ID") or "").strip()
                if hcad and hcad in seen_hcad:
                    continue
                if hcad:
                    seen_hcad.add(hcad)
                lm_override = landmark_by_hcad.get(hcad)
                norm_p = normalize_parcel_record(raw_p, landmark_override=lm_override)
                normalized_parcels.append({
                    "type": "Feature",
                    "geometry": geom,
                    "properties": norm_p,
                })
        except Exception as exc:
            print(f"  [Cadastral infill warning] {box_name}: {exc}")

    # Compute bounding boxes for OSM building footprint query
    osm_query_boxes = [
        (-95.415, 29.775, -95.375, 29.812),  # Houston Heights, Woodland Heights, Norhill, Germantown
        (-95.405, 29.735, -95.352, 29.774),  # Old Sixth Ward, Freedmen's Town, Downtown, Montrose, Avondale
        (-95.410, 29.718, -95.355, 29.738),  # Boulevard Oaks, Broadacres, Third Ward, Riverside Terrace
        (-95.280, 29.660, -95.245, 29.685),  # Glenbrook Valley Historic District
    ]
    print("-> Fetching OpenStreetMap building footprints via Overpass API...")
    osm_footprints = fetch_osm_footprints_for_boxes(
        cache_file=cache_dir / "osm_core_footprints.json",
        bboxes=osm_query_boxes,
    )

    # Supplement with Microsoft US Building Footprints (MSBFP2 via Esri Living Atlas)
    # for areas like Glenbrook Valley where OpenStreetMap only mapped one side of the street.
    ms_fp_cache = cache_dir / "msbfp2_core_footprints.json"
    if ms_fp_cache.exists():
        ms_footprints = json.loads(ms_fp_cache.read_text())
    else:
        print("-> Fetching Microsoft US Building Footprints (MSBFP2) infill...")
        ms_footprints = []
        msbfp_url = "https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/MSBFP2/FeatureServer/0/query"
        for bbox in [
            (-95.280, 29.660, -95.245, 29.685),  # Glenbrook Valley Historic District
            (-95.390, 29.748, -95.370, 29.760),  # Freedmen's Town & Old Sixth Ward infill
        ]:
            try:
                feats = fetch_arcgis_geojson_paginated(
                    url=msbfp_url,
                    bbox=bbox,
                    page_size=2000,
                    max_features=4000,
                )
                ms_footprints.extend(feats)
            except Exception as exc:
                print(f"  [MSBFP2 warning] {bbox}: {exc}")
        ms_fp_cache.write_text(json.dumps(ms_footprints))

    combined_footprints = osm_footprints + ms_footprints
    print(
        f"   Fetched {len(normalized_parcels):,} normalized parcels and "
        f"{len(combined_footprints):,} observed building footprints "
        f"({len(osm_footprints):,} OSM + {len(ms_footprints):,} Microsoft)."
    )

    return normalized_parcels, combined_footprints, overlays


def download_file_stream(url: str, dest: Path) -> Path:
    """Download a large ZIP archive with streaming if not already cached."""
    if dest.exists() and dest.stat().st_size > 1_000_000:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"-> Downloading {url} -> {dest.name}...")
    with httpx.Client(timeout=300.0, follow_redirects=True) as client:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=1024 * 256):
                    f.write(chunk)
    return dest


def fetch_full_hcad_parcels(
    cache_dir: Path,
    limit: int | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Download and parse full Harris County HCAD bulk ZIP archives (Parcels.zip + CAMA tables)."""
    overlays = fetch_all_overlays(cache_dir)

    parcels_zip = download_file_stream(HCAD_PARCELS_ZIP_URL, cache_dir / "Parcels.zip")
    bld_zip = download_file_stream(HCAD_BUILDING_LAND_ZIP_URL, cache_dir / "Real_building_land.zip")
    acct_zip = download_file_stream(HCAD_ACCT_OWNER_ZIP_URL, cache_dir / "Real_acct_owner.zip")

    # 1. Parse earliest year_built, remodel_year, style, and total building area per account from Real_building_land.zip
    cama_by_acct: dict[str, dict[str, Any]] = {}
    with zipfile.ZipFile(bld_zip, "r") as zf:
        for member in zf.namelist():
            if not member.lower().endswith(".txt") or "building" not in member.lower():
                continue
            with zf.open(member, "r") as raw_f:
                reader = csv.DictReader(io.TextIOWrapper(raw_f, encoding="latin1", errors="replace"), delimiter="\t")
                for row in reader:
                    acct = str(row.get("acct") or "").strip()
                    if not acct:
                        continue
                    yr = normalize_year(row.get("date_erect") or row.get("yr_impr"))
                    rem_yr = normalize_year(row.get("yr_remodel"))
                    area = float(row.get("im_sq_ft") or row.get("act_ar") or 0.0)
                    style = str(row.get("style_desc") or row.get("dscr") or "").strip()
                    rec = cama_by_acct.get(acct)
                    if rec is None:
                        cama_by_acct[acct] = {
                            "date_erect": yr,
                            "yr_remodel": rem_yr,
                            "im_sq_ft": area,
                            "style_desc": style,
                        }
                    else:
                        if yr > 0 and (rec["date_erect"] == 0 or yr < rec["date_erect"]):
                            rec["date_erect"] = yr
                        if rem_yr > rec["yr_remodel"]:
                            rec["yr_remodel"] = rem_yr
                        rec["im_sq_ft"] += area

    # 2. Parse owner, site address, state_class, and land_area from Real_acct_owner.zip (real_acct.txt)
    with zipfile.ZipFile(acct_zip, "r") as zf:
        for member in zf.namelist():
            if "real_acct" not in member.lower():
                continue
            with zf.open(member, "r") as raw_f:
                reader = csv.DictReader(io.TextIOWrapper(raw_f, encoding="latin1", errors="replace"), delimiter="\t")
                for row in reader:
                    acct = str(row.get("acct") or "").strip()
                    if not acct:
                        continue
                    rec = cama_by_acct.setdefault(acct, {})
                    rec["SITE_ADDR_1"] = str(row.get("site_addr_1") or "").strip()
                    rec["OWNER_MAILTO"] = str(row.get("mailto") or "").strip()
                    rec["STATE_CLASS"] = str(row.get("state_class") or "").strip()
                    rec["TOTAL_LAND_AREA"] = row.get("land_ar") or 0
                    rec["LEGAL_DSCR_1"] = str(row.get("lgl_1") or "").strip()

    # 3. Stream Parcels.zip shapefile and transform EPSG:2278 -> EPSG:4326
    transformer = Transformer.from_crs("EPSG:2278", "EPSG:4326", always_xy=True)
    extract_dir = cache_dir / "parcels_shp"
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(parcels_zip, "r") as zf:
        zf.extractall(extract_dir)

    shp_files = list(extract_dir.glob("*.shp"))
    if not shp_files:
        raise FileNotFoundError("No .shp file found inside Parcels.zip")

    sf = shapefile.Reader(str(shp_files[0]), encoding="latin1")
    field_names = [f[0] for f in sf.fields[1:]]
    normalized_parcels: list[dict[str, Any]] = []

    for idx, shape_rec in enumerate(sf.iterShapeRecords()):
        if limit and idx >= limit:
            break
        rec_dict = dict(zip(field_names, shape_rec.record))
        acct = str(rec_dict.get("HCAD_NUM") or rec_dict.get("acct") or "").strip()
        merged_raw = {**rec_dict, **(cama_by_acct.get(acct) or {})}
        norm_p = normalize_parcel_record(merged_raw)

        pts = shape_rec.shape.points
        if len(pts) < 4:
            continue
        lonlat_pts = [transformer.transform(x, y) for x, y in pts]
        poly = _ensure_valid_polygon(Polygon(lonlat_pts))
        if poly is None:
            continue
        normalized_parcels.append({
            "type": "Feature",
            "geometry": mapping(poly),
            "properties": norm_p,
        })

    return normalized_parcels, [], overlays
