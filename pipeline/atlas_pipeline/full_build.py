"""High-performance C-vectorized full Harris County (~1.85M buildings / 1.55M parcels) ETL & Tippecanoe PMTiles builder."""

from __future__ import annotations

import csv
import io
import math
import os
import pickle
import re
import subprocess
import time
import zipfile
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
import orjson
import pyogrio.raw
import shapely
from pyproj import Transformer
from shapely.strtree import STRtree

from atlas_pipeline.fetch_data import (
    COH_HISTORIC_PARCELS_URL,
    fetch_all_overlays,
    fetch_arcgis_geojson_paginated,
)
from atlas_pipeline.schema import (
    classify_use_category,
    compute_decade,
    estimate_stories_and_height,
    normalize_year,
)

# Web Mercator Zoom 11 tile-aligned split boundaries (x=480/481, x=481/482, y=846/847)
# so no tile at Zoom >= 11 ever straddles a shard boundary.
SPLIT_LON_EAST = -95.361328125
SPLIT_LON_WEST = -95.537109375
SPLIT_LAT = 29.76437738

FIRST_COORD_RE = re.compile(r"\[\[\[(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)\]")


def deduplicate_geojsonseq_shard(seq_path: Path) -> tuple[str, int, int]:
    """
    Deduplicate overlapping 3D building footprint polygons within a .geojsonseq shard:
      1. Collapse exact/near-exact centroid stacks (~1.1m) from stacked condominium/townhome parcels.
      2. Drop oversized multi-lot compound polygons that cover >= 2 smaller constituent buildings.
      3. Resolve remaining polygon overlaps (> 12% intersection of min area) via Shapely STRtree,
         prioritizing dated structures (year_built >= 1836), observed footprints over synthesized
         rectangles, older structures (Preservation Houston oldest-structure rule), and larger footprints.
    """
    raw_lines = seq_path.read_bytes().splitlines()
    initial_count = len(raw_lines)
    if initial_count == 0:
        return (seq_path.name, 0, 0)

    centroid_buckets: dict[tuple[int, int], tuple[tuple[int, int, int], dict[str, Any], list[list[float]], int, bool]] = {}
    for line in raw_lines:
        if line.startswith(b"\x1e"):
            line = line[1:]
        if not line:
            continue
        f = orjson.loads(line)
        g = f.get("geometry") or {}
        coords = g.get("coordinates")
        if not coords or not coords[0]:
            continue
        ring = coords[0] if g.get("type") == "Polygon" else coords[0][0]
        if not ring or len(ring) < 3:
            continue
        n_pts = max(1, len(ring) - 1)
        cx = sum(pt[0] for pt in ring[:n_pts]) / n_pts
        cy = sum(pt[1] for pt in ring[:n_pts]) / n_pts
        yr = int(f["properties"].get("year_built") or 0)
        is_syn = len(ring) == 5 and ring[0][1] == ring[1][1] and ring[1][0] == ring[2][0]
        prio = (0 if yr >= 1836 else 1, 1 if is_syn else 0, yr if yr >= 1836 else 9999)
        key = (round(cx, 5), round(cy, 5))
        if key not in centroid_buckets or prio < centroid_buckets[key][0]:
            centroid_buckets[key] = (prio, f, ring, yr, is_syn)

    items = list(centroid_buckets.values())
    n = len(items)
    polys_arr = np.array([shapely.Polygon(it[2]) for it in items], dtype=object)
    areas = shapely.area(polys_arr)
    tree = shapely.STRtree(polys_arr)
    pairs = tree.query(polys_arr, predicate="intersects")
    mask = pairs[0] < pairs[1]
    i_arr = pairs[0][mask]
    j_arr = pairs[1][mask]

    keep = np.ones(n, dtype=bool)
    if len(i_arr) > 0:
        inter_areas = shapely.area(shapely.intersection(polys_arr[i_arr], polys_arr[j_arr]))
        min_areas = np.minimum(areas[i_arr], areas[j_arr])
        ratio = np.where(min_areas > 0, inter_areas / min_areas, 0.0)
        sig = ratio > 0.12
        si = i_arr[sig]
        sj = j_arr[sig]
        sratio = ratio[sig]

        # Drop compound/multi-lot polygons that cover >= 2 smaller buildings (< 0.55x area) with > 35% overlap
        contained_children: dict[int, list[int]] = defaultdict(list)
        for idx_a, idx_b, r in zip(si.tolist(), sj.tolist(), sratio.tolist()):
            if r > 0.35:
                if areas[idx_a] > areas[idx_b] * 1.8:
                    contained_children[idx_a].append(idx_b)
                elif areas[idx_b] > areas[idx_a] * 1.8:
                    contained_children[idx_b].append(idx_a)

        for parent_idx, children in contained_children.items():
            if len(children) >= 2:
                keep[parent_idx] = False

        order = sorted(
            range(n),
            key=lambda idx: (
                0 if items[idx][3] >= 1836 else 1,
                1 if items[idx][4] else 0,
                items[idx][3] if items[idx][3] >= 1836 else 9999,
                -float(areas[idx]),
            ),
        )
        rank = np.empty(n, dtype=np.int32)
        for r_pos, idx in enumerate(order):
            rank[idx] = r_pos

        adj: dict[int, list[int]] = defaultdict(list)
        for idx_a, idx_b in zip(si.tolist(), sj.tolist()):
            if not keep[idx_a] or not keep[idx_b]:
                continue
            if rank[idx_a] < rank[idx_b]:
                adj[idx_a].append(idx_b)
            else:
                adj[idx_b].append(idx_a)

        for u in order:
            if not keep[u]:
                continue
            for v in adj.get(u, ()):
                keep[v] = False

    kept_count = 0
    with open(seq_path, "wb") as out_f:
        for idx in range(n):
            if keep[idx]:
                kept_count += 1
                out_f.write(b"\x1e" + orjson.dumps(items[idx][1]) + b"\n")

    return (seq_path.name, initial_count, kept_count)


def load_or_build_cama_lookup(cache_dir: Path) -> dict[str, tuple[int, int, int, str, str, str]]:
    """
    Parse 2026 HCAD Real_building_land.zip and Real_acct_owner.zip into a compact dictionary:
      acct -> (year_built, remodel_year, bld_area_sqft, use_code, style_desc, subdivision)
    Caches binary pickle to cache_dir / 'cama_2026_compact.pkl' for instant reloads.
    """
    pkl_path = cache_dir / "cama_2026_compact.pkl"
    if pkl_path.exists() and pkl_path.stat().st_size > 1_000_000:
        print("-> Loading cached 2026 HCAD CAMA lookup table...")
        with open(pkl_path, "rb") as f:
            return pickle.load(f)

    t0 = time.time()
    bld_zip = cache_dir / "Real_building_land.zip"
    acct_zip = cache_dir / "Real_acct_owner.zip"

    # Temporary mutable dict: acct -> [yr, rem_yr, area, use_cd, style, subdiv]
    records: dict[str, list[Any]] = {}

    print("-> Parsing 2026 HCAD Real_building_land.zip (building_res.txt & building_other.txt)...")
    with zipfile.ZipFile(bld_zip, "r") as zf:
        for member in ("building_res.txt", "building_other.txt"):
            if member not in zf.namelist():
                continue
            with zf.open(member, "r") as raw_f:
                text_f = io.TextIOWrapper(raw_f, encoding="latin1", errors="replace")
                header = text_f.readline().rstrip("\r\n").split("\t")
                col_idx = {name: i for i, name in enumerate(header)}
                i_acct = col_idx.get("acct", 0)
                i_use = col_idx.get("property_use_cd", 1)
                i_struct = col_idx.get("structure_dscr", 6)
                i_qual = col_idx.get("dscr", 11)
                i_date = col_idx.get("date_erected", 12)
                i_rem = col_idx.get("yr_remodel", 14)
                i_sqft = col_idx.get("im_sq_ft", 19)
                i_act = col_idx.get("act_ar", 20)

                for line in text_f:
                    parts = line.rstrip("\r\n").split("\t")
                    if len(parts) <= i_date:
                        continue
                    acct = parts[i_acct].strip()
                    if not acct:
                        continue
                    yr = normalize_year(parts[i_date])
                    rem = normalize_year(parts[i_rem]) if len(parts) > i_rem else 0
                    sqft_str = parts[i_sqft].strip() if len(parts) > i_sqft else ""
                    if not sqft_str and len(parts) > i_act:
                        sqft_str = parts[i_act].strip()
                    try:
                        area = int(float(sqft_str)) if sqft_str else 0
                    except ValueError:
                        area = 0
                    use_cd = parts[i_use].strip() if len(parts) > i_use else ""
                    style = parts[i_struct].strip() if len(parts) > i_struct else ""

                    existing = records.get(acct)
                    if existing is None:
                        records[acct] = [yr, rem, area, use_cd, style, ""]
                    else:
                        if yr > 0 and (existing[0] == 0 or yr < existing[0]):
                            existing[0] = yr
                        if rem > existing[1]:
                            existing[1] = rem
                        existing[2] += area
                        if not existing[3] and use_cd:
                            existing[3] = use_cd
                        if not existing[4] and style:
                            existing[4] = style

    print(f"   Parsed {len(records):,} building CAMA accounts in {time.time() - t0:.1f}s.")

    t1 = time.time()
    print("-> Parsing 2026 HCAD Real_acct_owner.zip (real_acct.txt)...")
    with zipfile.ZipFile(acct_zip, "r") as zf:
        with zf.open("real_acct.txt", "r") as raw_f:
            text_f = io.TextIOWrapper(raw_f, encoding="latin1", errors="replace")
            header = text_f.readline().rstrip("\r\n").split("\t")
            col_idx = {name: i for i, name in enumerate(header)}
            i_acct = col_idx.get("acct", 0)
            i_st = col_idx.get("state_class", 20)
            i_yr_impr = col_idx.get("yr_impr", 33)
            i_bld_ar = col_idx.get("bld_ar", 38)
            i_lgl1 = col_idx.get("lgl_1", 65)

            for line in text_f:
                parts = line.rstrip("\r\n").split("\t")
                if len(parts) <= i_st:
                    continue
                acct = parts[i_acct].strip()
                if not acct:
                    continue
                st_cls = parts[i_st].strip()
                lgl1 = parts[i_lgl1].strip() if len(parts) > i_lgl1 else ""
                existing = records.get(acct)
                if existing is not None:
                    if not existing[3] and st_cls:
                        existing[3] = st_cls
                    if existing[0] == 0 and len(parts) > i_yr_impr:
                        yr_i = normalize_year(parts[i_yr_impr])
                        if yr_i > 0:
                            existing[0] = yr_i
                    if existing[2] == 0 and len(parts) > i_bld_ar:
                        try:
                            existing[2] = int(float(parts[i_bld_ar].strip() or "0"))
                        except ValueError:
                            pass
                    existing[5] = lgl1
                else:
                    yr_i = normalize_year(parts[i_yr_impr]) if len(parts) > i_yr_impr else 0
                    try:
                        b_ar = int(float(parts[i_bld_ar].strip() or "0")) if len(parts) > i_bld_ar else 0
                    except ValueError:
                        b_ar = 0
                    if yr_i > 0 or b_ar > 0 or st_cls:
                        records[acct] = [yr_i, 0, b_ar, st_cls, "", lgl1]

    compact: dict[str, tuple[int, int, int, str, str, str]] = {
        k: (v[0], v[1], v[2], v[3], v[4], v[5]) for k, v in records.items()
    }
    print(f"   Completed full CAMA lookup ({len(compact):,} accounts) in {time.time() - t1:.1f}s.")
    with open(pkl_path, "wb") as f:
        pickle.dump(compact, f, protocol=pickle.HIGHEST_PROTOCOL)
    return compact


def load_historic_overrides(
    cache_dir: Path, overlays: dict[str, list[dict[str, Any]]]
) -> dict[str, dict[str, str]]:
    """Build a lookup by 13-digit HCAD account number for Historic Districts & Landmarks."""
    by_hcad: dict[str, dict[str, str]] = {}

    hist_parcels_cache = cache_dir / "coh_historic_parcels.json"
    if hist_parcels_cache.exists():
        hist_parcels = orjson.loads(hist_parcels_cache.read_bytes())
    else:
        hist_parcels = fetch_arcgis_geojson_paginated(
            COH_HISTORIC_PARCELS_URL, where="1=1", page_size=2000
        )

    for pf in hist_parcels:
        p = pf.get("properties") or {}
        hcad = str(p.get("HCAD_NUM") or "").strip()
        if not hcad:
            continue
        dist = str(p.get("Historic_District") or p.get("DISTRICT") or "").strip()
        contrib_raw = str(p.get("Classification") or "").strip()
        if contrib_raw in ("Contributing", "NonContributing", "Non-Contributing"):
            contrib = "Non-Contributing" if "Non" in contrib_raw else "Contributing"
        else:
            contrib = ""
        lm_desig = str(p.get("Landmark_Designation") or "").strip()
        if lm_desig.lower() in ("no designation", "none", "null", "n/a", "no"):
            lm_desig = ""
        entry: dict[str, str] = {}
        if dist:
            entry["historic_district"] = dist
        if contrib:
            entry["contributing"] = contrib
        if lm_desig:
            entry["landmark_type"] = "Protected Landmark" if "Protected" in lm_desig else "Landmark"
        if entry:
            by_hcad[hcad] = entry

    for lm in overlays.get("landmarks", []):
        lp = lm.get("properties") or {}
        hcad = str(lp.get("hcad_num") or "").strip()
        if not hcad:
            continue
        entry = by_hcad.setdefault(hcad, {})
        if lp.get("name"):
            entry["landmark_name"] = str(lp["name"]).strip()
        if lp.get("designation"):
            entry["landmark_type"] = str(lp["designation"]).strip()
        if lp.get("architect"):
            entry["architect"] = str(lp["architect"]).strip()
        if lp.get("historic_district") and not entry.get("historic_district"):
            entry["historic_district"] = str(lp["historic_district"]).strip()

    return by_hcad


def run_full_county_build(cache_dir: Path, output_dir: Path) -> dict[str, Any]:
    """
    Execute the complete Harris County build (~1.85M building footprints + 1.55M tax parcels)
    and compile multi-quadrant PMTiles v3 archives (<85 MB per file) ready for GitHub Pages.
    """
    t_start = time.time()
    cache_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Fetch/load overlays (Landmarks, Historic Districts, Heritage Districts, NRHP, THC Markers, Annexations)
    overlays = fetch_all_overlays(cache_dir)
    hist_by_hcad = load_historic_overrides(cache_dir, overlays)
    print(f"   Loaded {len(hist_by_hcad):,} historic district & landmark HCAD account overrides.")

    # 2. Load 2026 HCAD CAMA lookup
    cama_lookup = load_or_build_cama_lookup(cache_dir)

    # 3. Ensure Parcels.gdb is extracted on local SSD for fast GDAL reading
    gdb_dir = cache_dir / "Parcels" / "Parcels.gdb"
    if not gdb_dir.exists():
        print("-> Extracting Parcels.gdb from Parcels.zip to local SSD...")
        with zipfile.ZipFile(cache_dir / "Parcels.zip", "r") as zf:
            zf.extractall(cache_dir)

    print("-> Reading 1,546,683 Harris County parcels from Parcels.gdb via pyogrio...")
    t_gdb = time.time()
    meta, fids, geoms_wkb, fields = pyogrio.raw.read(
        str(gdb_dir),
        layer="Parcels",
        columns=["HCAD_NUM_1", "CurrOwner", "LocAddr", "yr_impr", "Shape_Area"],
    )
    parcel_geoms_2278 = shapely.from_wkb(geoms_wkb)
    del geoms_wkb
    print(f"   Loaded {len(parcel_geoms_2278):,} parcel geometries in {time.time() - t_gdb:.1f}s.")

    hcad_nums_arr = fields[0]
    owners_arr = fields[1]
    addrs_arr = fields[2]
    yr_impr_arr = fields[3]
    shape_area_arr = fields[4]

    print("-> Building C-level GEOS STRtree index over 1,546,683 parcels...")
    t_tree = time.time()
    tree = STRtree(parcel_geoms_2278)
    print(f"   STRtree built in {time.time() - t_tree:.1f}s.")

    # 4. Stream and spatially join all 1,847,281 Microsoft footprints + 47,651 OSM footprints in batches
    transformer_to_2278 = Transformer.from_crs("EPSG:4326", "EPSG:2278", always_xy=True)
    transformer_to_4326 = Transformer.from_crs("EPSG:2278", "EPSG:4326", always_xy=True)

    ndjson_dir = cache_dir / "quadrant_ndjson"
    ndjson_dir.mkdir(parents=True, exist_ok=True)
    quad_names = ("nw_w", "nw_e", "ne", "sw", "se")
    quad_files = {
        q: open(ndjson_dir / f"buildings_{q}.geojsonseq", "wb") for q in quad_names
    }

    decade_counter: Counter[str] = Counter()
    contrib_counter: Counter[str] = Counter()
    use_counter: Counter[str] = Counter()
    district_counter: Counter[str] = Counter()

    matched_parcel_mask = np.zeros(len(parcel_geoms_2278), dtype=bool)
    total_buildings = 0
    dated_count = 0
    observed_fp_count = 0
    earliest_year = 9999
    latest_year = 0

    def get_quadrant(lon: float, lat: float) -> str:
        if lat >= SPLIT_LAT:
            if lon < SPLIT_LON_EAST:
                return "nw_w" if lon < SPLIT_LON_WEST else "nw_e"
            return "ne"
        return "sw" if lon < SPLIT_LON_EAST else "se"

    def build_parcel_props(p_idx: int, bld_id: int, is_observed: bool) -> dict[str, Any]:
        raw_acct = hcad_nums_arr[p_idx]
        acct = str(raw_acct).strip() if raw_acct is not None else ""
        cama = cama_lookup.get(acct)

        yr = cama[0] if cama else 0
        if yr == 0:
            yr = normalize_year(yr_impr_arr[p_idx])
        rem_yr = cama[1] if cama else 0
        bld_area = cama[2] if cama else 0
        use_cd = cama[3] if cama else ""
        style = cama[4] if cama else ""

        dec = compute_decade(yr)
        use_cat = classify_use_category(use_cd, "", bld_area)
        land_sqft = int(float(shape_area_arr[p_idx] or 0.0))
        stories, height_m = estimate_stories_and_height(
            bld_area=float(bld_area),
            footprint_area_sqft=float(max(bld_area, 900)) if bld_area > 0 else 1100.0,
            use_category=use_cat,
        )

        addr_raw = addrs_arr[p_idx]
        addr = str(addr_raw).strip() if addr_raw is not None else ""

        props: dict[str, Any] = {
            "id": acct or f"b{bld_id}",
            "hcad_num": acct,
            "year_built": yr,
            "decade": dec,
            "use_category": use_cat,
            "stories": stories,
            "height_m": height_m,
        }
        if addr:
            props["address"] = addr
        if bld_area > 0:
            props["bld_area"] = bld_area
        if land_sqft > 0:
            props["land_area"] = land_sqft
        if rem_yr > 1836:
            props["remodel_year"] = rem_yr
        if style and style != use_cat:
            props["bld_style"] = style

        hist = hist_by_hcad.get(acct)
        if hist:
            props.update(hist)

        return props

    def record_stats(props: dict[str, Any], is_observed: bool = True) -> None:
        nonlocal total_buildings, dated_count, observed_fp_count, earliest_year, latest_year
        total_buildings += 1
        yr = props["year_built"]
        dec = props["decade"]
        if yr >= 1836 and dec >= 1830:
            dated_count += 1
            decade_counter[str(dec)] += 1
            if yr < earliest_year:
                earliest_year = yr
            if yr > latest_year:
                latest_year = yr
        else:
            decade_counter["unknown"] += 1

        contrib = props.get("contributing", "Outside Historic District")
        contrib_counter[contrib] += 1
        use_counter[props["use_category"]] += 1
        dist = props.get("historic_district")
        if dist:
            district_counter[dist] += 1
        if is_observed:
            observed_fp_count += 1

    def get_parcel_year(pcl_idx: int) -> int:
        raw_acct = hcad_nums_arr[pcl_idx]
        acct = str(raw_acct).strip() if raw_acct is not None else ""
        cama = cama_lookup.get(acct)
        yr = cama[0] if cama else normalize_year(yr_impr_arr[pcl_idx])
        hist = hist_by_hcad.get(acct)
        if hist and hist.get("year_built", 0) >= 1836:
            if yr < 1836 or hist["year_built"] < yr:
                yr = hist["year_built"]
        return yr if yr >= 1836 else 9999

    def process_footprint_batch(
        geom_dicts: list[dict[str, Any]], lons: list[float], lats: list[float]
    ) -> None:
        if not geom_dicts:
            return
        xs_2278, ys_2278 = transformer_to_2278.transform(
            np.array(lons, dtype=np.float64), np.array(lats, dtype=np.float64)
        )
        pts_2278 = shapely.points(xs_2278, ys_2278)
        # Query STRtree: returns [2, K] array of (point_idx, parcel_idx)
        pairs = tree.query(pts_2278, predicate="within")
        pt_to_parcel: dict[int, int] = {}
        for pt_i, pcl_i in zip(pairs[0].tolist(), pairs[1].tolist()):
            # Mark all stacked parcels (e.g. condominium/townhome units) at this location as matched
            matched_parcel_mask[pcl_i] = True
            if pt_i not in pt_to_parcel:
                pt_to_parcel[pt_i] = pcl_i
            else:
                if get_parcel_year(pcl_i) < get_parcel_year(pt_to_parcel[pt_i]):
                    pt_to_parcel[pt_i] = pcl_i

        for idx_in_batch, geom_d in enumerate(geom_dicts):
            pcl_idx = pt_to_parcel.get(idx_in_batch)
            if pcl_idx is None:
                continue
            matched_parcel_mask[pcl_idx] = True
            props = build_parcel_props(pcl_idx, total_buildings + 1, is_observed=True)
            record_stats(props, is_observed=True)
            q = get_quadrant(lons[idx_in_batch], lats[idx_in_batch])
            feat_bytes = (
                b"\x1e"
                + orjson.dumps({"type": "Feature", "geometry": geom_d, "properties": props})
                + b"\n"
            )
            quad_files[q].write(feat_bytes)

    # 4a. Process OpenStreetMap observed footprints first
    osm_cache = cache_dir / "osm_core_footprints.json"
    if osm_cache.exists():
        print("-> Spatially joining cached OpenStreetMap building footprints...")
        osm_feats = orjson.loads(osm_cache.read_bytes())
        b_geoms, b_lons, b_lats = [], [], []
        for f in osm_feats:
            g = f.get("geometry")
            if not g or g.get("type") != "Polygon":
                continue
            coords = g.get("coordinates")
            if not coords or not coords[0]:
                continue
            ring = coords[0]
            n_pts = max(1, len(ring) - 1)
            lon = sum(pt[0] for pt in ring[:n_pts]) / n_pts
            lat = sum(pt[1] for pt in ring[:n_pts]) / n_pts
            b_geoms.append(g)
            b_lons.append(float(lon))
            b_lats.append(float(lat))
        process_footprint_batch(b_geoms, b_lons, b_lats)
        print(f"   Joined {total_buildings:,} OpenStreetMap footprints.")

    # 4b. Stream all 1,847,281 Microsoft footprints from cache/harris_ms_footprints.ndjson in batches of 150,000
    ms_ndjson = cache_dir / "harris_ms_footprints.ndjson"
    print(f"-> Streaming and spatially joining 1,847,281 Microsoft building footprints from {ms_ndjson.name}...")
    t_ms = time.time()
    batch_geoms: list[dict[str, Any]] = []
    batch_lons: list[float] = []
    batch_lats: list[float] = []
    BATCH_SIZE = 150_000

    with open(ms_ndjson, "rb") as f:
        for line_num, raw_line in enumerate(f, 1):
            try:
                feat = orjson.loads(raw_line)
                g = feat.get("geometry")
                if not g:
                    continue
                ring = g["coordinates"][0]
                # Compute fast average of outer ring vertices as representative interior point
                n_pts = max(1, len(ring) - 1)
                lon = sum(pt[0] for pt in ring[:n_pts]) / n_pts
                lat = sum(pt[1] for pt in ring[:n_pts]) / n_pts
                batch_geoms.append(g)
                batch_lons.append(lon)
                batch_lats.append(lat)
            except Exception:
                continue

            if len(batch_geoms) >= BATCH_SIZE:
                process_footprint_batch(batch_geoms, batch_lons, batch_lats)
                batch_geoms.clear()
                batch_lons.clear()
                batch_lats.clear()
                print(
                    f"   Processed {line_num:,} footprints -> {total_buildings:,} matched Harris County buildings ({time.time() - t_ms:.1f}s)..."
                )

    if batch_geoms:
        process_footprint_batch(batch_geoms, batch_lons, batch_lats)
        batch_geoms.clear()
        batch_lons.clear()
        batch_lats.clear()

    print(
        f"   Completed observed footprint spatial join: {total_buildings:,} observed building footprints in {time.time() - t_ms:.1f}s."
    )

    # 4c. Synthesize building footprints for any unmatched HCAD parcel that has a structure (year_built >= 1836 or bld_area > 0)
    print("-> Synthesizing footprints for remaining HCAD parcels with recorded structures...")
    t_syn = time.time()
    unmatched_indices = np.where(~matched_parcel_mask)[0]
    syn_indices: list[int] = []
    for p_idx in unmatched_indices.tolist():
        raw_acct = hcad_nums_arr[p_idx]
        acct = str(raw_acct).strip() if raw_acct is not None else ""
        cama = cama_lookup.get(acct)
        yr = cama[0] if cama else normalize_year(yr_impr_arr[p_idx])
        bld_ar = cama[2] if cama else 0
        if yr >= 1836 or bld_ar > 0 or acct in hist_by_hcad:
            if parcel_geoms_2278[p_idx] is not None and not shapely.is_empty(parcel_geoms_2278[p_idx]):
                syn_indices.append(p_idx)

    if syn_indices:
        syn_geoms_2278 = parcel_geoms_2278[syn_indices]
        centroids_2278 = shapely.centroid(syn_geoms_2278)
        cx_2278 = shapely.get_x(centroids_2278)
        cy_2278 = shapely.get_y(centroids_2278)
        clons, clats = transformer_to_4326.transform(cx_2278, cy_2278)

        for i, p_idx in enumerate(syn_indices):
            lon = float(clons[i])
            lat = float(clats[i])
            if not (-96.1 <= lon <= -94.8 and 29.4 <= lat <= 30.3):
                continue
            props = build_parcel_props(p_idx, total_buildings + 1, is_observed=False)
            record_stats(props, is_observed=False)
            # Create a realistic ~12m x 10m building footprint polygon around the parcel centroid
            b_area = props.get("bld_area", 1400)
            stories = max(1, int(props.get("stories", 1)))
            fp_sqft = max(600, min(15000, b_area / stories))
            half_side_deg = math.sqrt(fp_sqft) * 0.0000014
            dx = half_side_deg * 1.15
            dy = half_side_deg * 0.90
            poly_geom = {
                "type": "Polygon",
                "coordinates": [[
                    [round(lon - dx, 6), round(lat - dy, 6)],
                    [round(lon + dx, 6), round(lat - dy, 6)],
                    [round(lon + dx, 6), round(lat + dy, 6)],
                    [round(lon - dx, 6), round(lat + dy, 6)],
                    [round(lon - dx, 6), round(lat - dy, 6)],
                ]],
            }
            q = get_quadrant(lon, lat)
            feat_bytes = (
                b"\x1e"
                + orjson.dumps({"type": "Feature", "geometry": poly_geom, "properties": props})
                + b"\n"
            )
            quad_files[q].write(feat_bytes)

    for fh in quad_files.values():
        fh.close()
    print(
        f"   Synthesized {len(syn_indices):,} additional HCAD structure footprints in {time.time() - t_syn:.1f}s. Raw buildings: {total_buildings:,}."
    )

    # 4d. Deduplicate overlapping 3D building polygons across all 5 shards in parallel
    print("-> Deduplicating overlapping 3D building polygons across all 5 shards in parallel...")
    t_dedup = time.time()
    shard_paths = [ndjson_dir / f"buildings_{q}.geojsonseq" for q in quad_names]
    dedup_total = 0
    with ProcessPoolExecutor(max_workers=len(shard_paths)) as pool:
        for s_name, before_cnt, after_cnt in pool.map(deduplicate_geojsonseq_shard, shard_paths):
            dedup_total += after_cnt
            print(f"   {s_name}: {before_cnt:,} -> {after_cnt:,} clean non-overlapping buildings")
    total_buildings = dedup_total
    print(f"   Completed spatial overlap deduplication in {time.time() - t_dedup:.1f}s: {total_buildings:,} unique structures.")

    # 5. Compile each quadrant's GeoJSONSeq into a <85 MB PMTiles v3 archive using Tippecanoe in parallel
    tippecanoe_bin = "/tmp/tippecanoe/tippecanoe"
    if not Path(tippecanoe_bin).exists():
        tippecanoe_bin = str(Path.home() / ".local" / "bin" / "tippecanoe")

    print("-> Compiling 5 Web Mercator tile-aligned PMTiles v3 archives in parallel via Tippecanoe...")
    t_tip = time.time()
    procs: list[tuple[str, subprocess.Popen[bytes], Path]] = []
    for q in quad_names:
        in_seq = ndjson_dir / f"buildings_{q}.geojsonseq"
        out_pmtiles = output_dir / f"houston_buildings_{q}.pmtiles"
        cmd = [
            tippecanoe_bin,
            "-o",
            str(out_pmtiles),
            "--force",
            "-l",
            "buildings",
            "-Z",
            "10",
            "-z",
            "15",
            "--hilbert",
            "--read-parallel",
            "--drop-densest-as-needed",
            "--extend-zooms-if-still-dropping",
            "--maximum-tile-bytes=2500000",
            "--maximum-tile-features=400000",
            "--simplification=2",
            str(in_seq),
        ]
        procs.append((q, subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE), out_pmtiles))

    shard_files: list[str] = []
    total_pmtiles_bytes = 0
    for q, proc, out_pmtiles in procs:
        _, stderr = proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"Tippecanoe failed for quadrant {q}: {stderr.decode('utf-8', errors='replace')}")
        sz = out_pmtiles.stat().st_size
        total_pmtiles_bytes += sz
        shard_files.append(out_pmtiles.name)
        print(f"   Compiled {out_pmtiles.name}: {sz / (1024 * 1024):.1f} MB")

    # Also include the core houston_atlas.pmtiles (which has historic core parcels + buildings) in the manifest
    manifest = {
        "version": 2,
        "mode": "full_county",
        "total_buildings": total_buildings,
        "building_shards": shard_files,
        "core_pmtiles": "houston_atlas.pmtiles",
    }
    (output_dir / "pmtiles_manifest.json").write_bytes(orjson.dumps(manifest, option=orjson.OPT_INDENT_2))

    # Update stats_summary.json with full countywide totals
    ordered_decades = {str(d): decade_counter.get(str(d), 0) for d in range(1830, 2030, 10)}
    ordered_decades["unknown"] = decade_counter.get("unknown", 0)

    stats_summary = {
        "total_buildings": total_buildings,
        "dated_buildings": dated_count,
        "observed_footprints": observed_fp_count,
        "earliest_year": earliest_year if earliest_year != 9999 else 1836,
        "latest_year": latest_year if latest_year != 0 else 2026,
        "total_landmarks": len(overlays.get("landmarks", [])),
        "total_historic_districts": len(overlays.get("historic_districts", [])),
        "total_heritage_districts": len(overlays.get("heritage_districts", [])),
        "total_nrhp_districts": len(overlays.get("nrhp_districts", [])),
        "total_thc_markers": len(overlays.get("thc_markers", [])),
        "decade_counts": ordered_decades,
        "contributing_counts": dict(contrib_counter),
        "use_category_counts": dict(use_counter),
        "top_historic_districts": dict(district_counter.most_common(25)),
        "pmtiles_size_bytes": total_pmtiles_bytes,
        "pmtiles_shards": shard_files,
    }
    (output_dir / "stats_summary.json").write_bytes(orjson.dumps(stats_summary, option=orjson.OPT_INDENT_2))
    print(f"=== Full Harris County Build Complete in {time.time() - t_start:.1f}s ===")
    return stats_summary
