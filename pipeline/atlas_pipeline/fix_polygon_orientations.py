"""Identify and repair unrotated (cardinal axis-aligned) synthesized building polygons across all shards, overrides, and GeoJSON layers.

Root Causes Addressed:
1. Step 4c of `full_build.py` previously synthesized fallback footprints for unmatched HCAD parcels as
   North-South / East-West cardinal rectangles (`[lon ± dx, lat ± dy]`, aspect ratio `dx/dy = 1.15/0.90 = 1.278`),
   ignoring the parcel's street-grid rotation in `Parcels.gdb` (`EPSG:2278`).
2. In the initial shard build, zero-lot-line footprints in Downtown and historic districts were matched by their
   first exterior vertex (`ring[0]`) rather than their interior point (`point_on_surface`), causing some footprints
   to be missed (when `ring[0]` fell into the street right-of-way) or assigned to an adjacent parcel across a party
   wall (leaving the true parcel unmatched and triggering a Step 4c cardinal box).
3. Stacked condominium/multi-account parcels (`1120 Texas St`, etc.) and cross-shard boundary buildings
   (`SPLIT_LON_EAST = -95.361328125`) retained redundant cardinal boxes underneath observed footprints.
4. `75` entries in `curated_overrides.json` and `derived_parcel` entries in `buildings.geojson` inherited those
   cardinal boxes.

General 3-Tier Resolution:
- Tier 1 (Observed OSM / Shard Footprint Recovery): Match each affected parcel (`EPSG:2278`) against real
  observed OpenStreetMap (`osm_core_footprints.json`, `overpass_*.json`, `buildings.geojson`) and shard footprints
  using interior point (`point_on_surface`) and area overlap (`>= 25%`), clipping multi-parcel zero-lot-line
  structures cleanly to the parcel boundary and correcting party-wall `ring[0]` misattributions.
- Tier 2 (Stacked Duplicate & Cross-Shard Suppression): Drop redundant synthesized cardinal boxes when the parcel
  or building roof (`>= 18%` overlap, including across shard split boundaries) is already represented by a real
  observed building footprint.
- Tier 3 (Parcel-Oriented `EPSG:2278` Synthesis): For all remaining parcels without an external satellite/OSM
  footprint, synthesize an oriented building footprint in `EPSG:2278` aligned with the parcel's minimum rotated
  rectangle (`shapely.oriented_envelope`), sized to `bld_area / stories`, and clipped to the parcel interior so
  no footprint ever sits at the wrong angle or protrudes across lot lines.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import orjson
import pyogrio.raw
import shapely
from pyproj import Transformer
from shapely.geometry import MultiPolygon, Point, Polygon, mapping, shape
from shapely.strtree import STRtree

SPLIT_LON_EAST = -95.361328125
SPLIT_LON_WEST = -95.537109375
SPLIT_LAT = 29.76437738


def is_cardinal_5pt_rect(ring: list[list[float]] | list[tuple[float, float]]) -> bool:
    """Return True if ring is a 5-vertex axis-aligned (North-South / East-West) rectangle."""
    if not ring or len(ring) != 5:
        return False
    xs = sorted(set(round(float(c[0]), 6) for c in ring))
    ys = sorted(set(round(float(c[1]), 6) for c in ring))
    return len(xs) == 2 and len(ys) == 2


def is_step4c_syn_rect(ring: list[list[float]] | list[tuple[float, float]]) -> bool:
    """Return True if ring matches the exact Step 4c cardinal box signature."""
    if not is_cardinal_5pt_rect(ring):
        return False
    xs = sorted(set(round(float(c[0]), 6) for c in ring))
    ys = sorted(set(round(float(c[1]), 6) for c in ring))
    dx = xs[1] - xs[0]
    dy = ys[1] - ys[0]
    if dy <= 0:
        return False
    ratio = dx / dy
    # Step 4c used dx = half * 1.15, dy = half * 0.90 -> ratio = 1.2777...
    return 1.22 <= ratio <= 1.34


def compute_mrr_angle_mod90(mrr_poly: Polygon) -> float:
    """Return the orientation angle in degrees [0, 90) of a minimum rotated rectangle in EPSG:2278."""
    if mrr_poly is None or mrr_poly.is_empty or mrr_poly.geom_type != "Polygon":
        return 0.0
    coords = list(mrr_poly.exterior.coords)
    if len(coords) < 4:
        return 0.0
    dx = coords[1][0] - coords[0][0]
    dy = coords[1][1] - coords[0][1]
    return math.degrees(math.atan2(dy, dx)) % 90.0


def angle_diff_mod90(a: float, b: float) -> float:
    """Return the acute angle difference in degrees [0, 45] between two orientations mod 90°."""
    d = abs(a - b) % 90.0
    return min(d, 90.0 - d)


def build_oriented_footprint_2278(
    parcel_poly: Polygon,
    mrr_poly: Polygon,
    bld_area_sqft: float,
    stories: int,
) -> Polygon | None:
    """Construct a parcel-oriented, parcel-clipped building footprint in EPSG:2278 (US survey feet)."""
    if parcel_poly is None or parcel_poly.is_empty or parcel_poly.area <= 25.0:
        return None
    if mrr_poly is None or mrr_poly.is_empty or mrr_poly.geom_type != "Polygon":
        mrr_poly = shapely.oriented_envelope(parcel_poly)
    if mrr_poly is None or mrr_poly.is_empty or mrr_poly.geom_type != "Polygon":
        return parcel_poly

    coords = list(mrr_poly.exterior.coords)
    if len(coords) < 4:
        return parcel_poly

    x0, y0 = float(coords[0][0]), float(coords[0][1])
    x1, y1 = float(coords[1][0]), float(coords[1][1])
    x2, y2 = float(coords[2][0]), float(coords[2][1])

    dx1, dy1 = x1 - x0, y1 - y0
    dx2, dy2 = x2 - x1, y2 - y1
    L1 = math.hypot(dx1, dy1)
    L2 = math.hypot(dx2, dy2)
    if L1 < 1.0 or L2 < 1.0:
        return parcel_poly

    u1x, u1y = dx1 / L1, dy1 / L1
    u2x, u2y = dx2 / L2, dy2 / L2

    stories_clamped = max(1, int(stories or 1))
    b_area = float(bld_area_sqft or 1400.0)
    if b_area <= 0:
        b_area = 1400.0
    fp_sqft = max(600.0, min(25000.0, b_area / stories_clamped))
    pcl_area = float(parcel_poly.area)

    # For high-coverage commercial/urban parcels where the building fills most of the lot,
    # inset the parcel polygon slightly so zero-lot-line structures align cleanly with the lot.
    if fp_sqft >= 0.52 * pcl_area and pcl_area <= 45000.0:
        inset = parcel_poly.buffer(-1.8, join_style="mitre")
        if not inset.is_empty:
            if inset.geom_type == "MultiPolygon":
                inset = max(inset.geoms, key=lambda g: g.area)
            if inset.geom_type == "Polygon" and inset.area >= 0.35 * pcl_area:
                return inset

    target_area = min(fp_sqft, 0.62 * pcl_area)
    if L1 >= L2:
        lot_ratio = L1 / max(L2, 1.0)
        aspect = min(1.75, max(1.15, 0.78 * lot_ratio))
        w1 = min(math.sqrt(target_area * aspect), 0.84 * L1)
        w2 = min(math.sqrt(target_area / aspect), 0.84 * L2)
    else:
        lot_ratio = L2 / max(L1, 1.0)
        aspect = min(1.75, max(1.15, 0.78 * lot_ratio))
        w2 = min(math.sqrt(target_area * aspect), 0.84 * L2)
        w1 = min(math.sqrt(target_area / aspect), 0.84 * L1)

    w1 = max(14.0, w1)
    w2 = max(14.0, w2)

    c_pt = parcel_poly.centroid
    if not parcel_poly.contains(c_pt):
        c_pt = shapely.point_on_surface(parcel_poly)
    cx, cy = float(c_pt.x), float(c_pt.y)

    h1x, h1y = 0.5 * w1 * u1x, 0.5 * w1 * u1y
    h2x, h2y = 0.5 * w2 * u2x, 0.5 * w2 * u2y

    rect_pts = [
        (cx - h1x - h2x, cy - h1y - h2y),
        (cx + h1x - h2x, cy + h1y - h2y),
        (cx + h1x + h2x, cy + h1y + h2y),
        (cx - h1x + h2x, cy - h1y + h2y),
        (cx - h1x - h2x, cy - h1y - h2y),
    ]
    rect_poly = Polygon(rect_pts)

    # Ensure the oriented footprint stays cleanly inside the parcel boundary
    safe_parcel = parcel_poly.buffer(-1.5, join_style="mitre")
    if safe_parcel.is_empty or safe_parcel.area < 0.25 * pcl_area:
        safe_parcel = parcel_poly

    if not safe_parcel.contains(rect_poly):
        clipped = rect_poly.intersection(safe_parcel)
        if not clipped.is_empty:
            if clipped.geom_type == "MultiPolygon":
                clipped = max(clipped.geoms, key=lambda g: g.area)
            elif clipped.geom_type == "GeometryCollection":
                polys = [g for g in clipped.geoms if g.geom_type == "Polygon" and not g.is_empty]
                clipped = max(polys, key=lambda g: g.area) if polys else rect_poly
            if clipped.geom_type == "Polygon" and clipped.area >= 120.0:
                rect_poly = clipped.simplify(0.4, preserve_topology=True)

    return rect_poly


def find_best_osm_footprint_2278(
    parcel_poly: Polygon,
    osm_tree: STRtree,
    osm_polys: list[Polygon],
) -> Polygon | None:
    """Find or clip the best observed OpenStreetMap building footprint for a parcel in EPSG:2278."""
    if parcel_poly is None or parcel_poly.is_empty:
        return None
    cand_idxs = osm_tree.query(parcel_poly)
    if len(cand_idxs) == 0:
        return None

    best_poly: Polygon | None = None
    best_score = 0.0
    pcl_area = float(parcel_poly.area)

    for idx in cand_idxs:
        op = osm_polys[int(idx)]
        inter = op.intersection(parcel_poly)
        if inter.is_empty:
            continue
        inter_area = float(inter.area)
        if inter_area < 120.0:
            continue
        op_area = float(op.area)
        frac_of_bldg = inter_area / max(op_area, 1.0)
        frac_of_pcl = inter_area / max(pcl_area, 1.0)

        # Require meaningful overlap with either the building or the parcel
        if frac_of_bldg < 0.16 and frac_of_pcl < 0.22:
            continue

        # Score favors footprints mostly inside the parcel and covering a realistic portion of the parcel
        score = inter_area * (0.6 + 0.4 * frac_of_bldg)
        if score > best_score:
            best_score = score
            # If the OSM footprint is mostly inside this parcel (>= 68%) and not wildly larger than the parcel,
            # preserve the intact architectural building outline!
            if frac_of_bldg >= 0.68 and op_area <= 1.45 * pcl_area:
                best_poly = op
            else:
                # Zero-lot-line or multi-parcel block building: clip cleanly to the parcel boundary!
                clipped = inter
                if clipped.geom_type == "MultiPolygon":
                    polys = [g for g in clipped.geoms if g.geom_type == "Polygon" and not g.is_empty]
                    if not polys:
                        continue
                    clipped = max(polys, key=lambda g: g.area)
                elif clipped.geom_type == "GeometryCollection":
                    polys = [g for g in clipped.geoms if g.geom_type == "Polygon" and not g.is_empty]
                    if not polys:
                        continue
                    clipped = max(polys, key=lambda g: g.area)
                if clipped.geom_type == "Polygon" and clipped.area >= 180.0:
                    best_poly = clipped.simplify(0.35, preserve_topology=True)

    return best_poly


def batch_transform_polys_2278_to_rings_4326(
    polys_2278: list[Polygon],
    transformer_to_4326: Transformer,
) -> list[list[list[float]]]:
    """Vector-transform a list of EPSG:2278 Polygons into EPSG:4326 GeoJSON coordinate rings in C."""
    if not polys_2278:
        return []
    all_xs: list[float] = []
    all_ys: list[float] = []
    lengths: list[int] = []

    for p in polys_2278:
        if p.geom_type == "MultiPolygon":
            p = max(p.geoms, key=lambda g: g.area)
        coords = list(p.exterior.coords)
        lengths.append(len(coords))
        for pt in coords:
            all_xs.append(float(pt[0]))
            all_ys.append(float(pt[1]))

    xs_arr = np.array(all_xs, dtype=np.float64)
    ys_arr = np.array(all_ys, dtype=np.float64)
    lons_arr, lats_arr = transformer_to_4326.transform(xs_arr, ys_arr)
    lons_r = np.round(lons_arr, 7).tolist()
    lats_r = np.round(lats_arr, 7).tolist()

    rings: list[list[list[float]]] = []
    offset = 0
    for n in lengths:
        ring = [[lons_r[i], lats_r[i]] for i in range(offset, offset + n)]
        if ring and ring[0] != ring[-1]:
            ring.append(list(ring[0]))
        rings.append(ring)
        offset += n
    return rings


def run_fix_polygon_orientations() -> None:
    t0 = time.time()
    repo_root = Path(__file__).resolve().parents[2]
    cache_dir = repo_root / "pipeline" / "cache"
    aligned_dir = cache_dir / "aligned_ndjson"
    data_dir = repo_root / "app" / "public" / "data"

    transformer_to_2278 = Transformer.from_crs("EPSG:4326", "EPSG:2278", always_xy=True)
    transformer_to_4326 = Transformer.from_crs("EPSG:2278", "EPSG:4326", always_xy=True)

    # -------------------------------------------------------------------------
    # 1. Load Observed OpenStreetMap Footprints in EPSG:2278
    # -------------------------------------------------------------------------
    print("-> [1/6] Loading observed OpenStreetMap footprints into EPSG:2278 STRtree...")
    t_osm = time.time()
    osm_rings_4326: list[list[list[float]]] = []
    for fn in (
        cache_dir / "osm_core_footprints.json",
        cache_dir / "overpass_uh_and_dated.json",
        cache_dir / "overpass_zoo_ust_tmc.json",
    ):
        if not fn.exists():
            continue
        raw = orjson.loads(fn.read_bytes())
        feats = raw if isinstance(raw, list) else raw.get("features", [])
        for f in feats:
            g = f.get("geometry")
            if g and g.get("type") == "Polygon":
                ring = g["coordinates"][0]
                if len(ring) >= 4 and not is_step4c_syn_rect(ring):
                    osm_rings_4326.append(ring)

    # Also include non-cardinal observed footprints from buildings.geojson
    blds_geojson_path = data_dir / "buildings.geojson"
    blds_fc = orjson.loads(blds_geojson_path.read_bytes())
    for f in blds_fc.get("features", []):
        g = f.get("geometry")
        props = f.get("properties") or {}
        if (
            g
            and g.get("type") == "Polygon"
            and props.get("footprint_source") != "derived_parcel"
        ):
            ring = g["coordinates"][0]
            if len(ring) >= 4 and not is_cardinal_5pt_rect(ring):
                osm_rings_4326.append(ring)

    # Vector transform all OSM rings to EPSG:2278
    flat_lons: list[float] = []
    flat_lats: list[float] = []
    ring_lens: list[int] = []
    for r in osm_rings_4326:
        ring_lens.append(len(r))
        for pt in r:
            flat_lons.append(float(pt[0]))
            flat_lats.append(float(pt[1]))

    ox_arr, oy_arr = transformer_to_2278.transform(
        np.array(flat_lons, dtype=np.float64),
        np.array(flat_lats, dtype=np.float64),
    )
    osm_polys_2278: list[Polygon] = []
    off = 0
    seen_osm_keys: set[tuple[int, int, int]] = set()
    for n in ring_lens:
        pts = list(zip(ox_arr[off : off + n].tolist(), oy_arr[off : off + n].tolist()))
        off += n
        try:
            p = Polygon(pts)
            if not p.is_valid:
                p = shapely.make_valid(p)
                if p.geom_type in ("MultiPolygon", "GeometryCollection"):
                    polys = [g for g in p.geoms if g.geom_type == "Polygon" and not g.is_empty]
                    p = max(polys, key=lambda g: g.area) if polys else Polygon()
            if p.geom_type == "Polygon" and not p.is_empty and p.area >= 150.0:
                k = (int(round(p.centroid.x)), int(round(p.centroid.y)), int(round(p.area / 10.0)))
                if k not in seen_osm_keys:
                    seen_osm_keys.add(k)
                    osm_polys_2278.append(p)
        except Exception:
            continue

    osm_tree_2278 = STRtree(osm_polys_2278)
    print(f"   Indexed {len(osm_polys_2278):,} unique observed OSM footprints in {time.time() - t_osm:.1f}s.")

    # -------------------------------------------------------------------------
    # 2. Scan Shards, Overrides, and buildings.geojson for Needed HCAD Accounts
    # -------------------------------------------------------------------------
    print("-> [2/6] Scanning shards, curated_overrides.json, and buildings.geojson for candidate HCAD accounts...")
    t_scan = time.time()
    needed_hcads: set[str] = set()
    hcad_meta: dict[str, tuple[float, int]] = {}  # hcad -> (bld_area, stories)

    overrides_path = data_dir / "curated_overrides.json"
    overrides_doc = orjson.loads(overrides_path.read_bytes())
    overrides_map: dict[str, Any] = overrides_doc.get("overrides", {})

    for k, rec in overrides_map.items():
        hcad = str(rec.get("hcad_num") or k.split("#")[0]).strip()
        if len(hcad) == 13 and hcad.isdigit():
            needed_hcads.add(hcad)
            hcad_meta[hcad] = (
                float(rec.get("bld_area") or 1800.0),
                int(rec.get("stories") or 1),
            )

    for f in blds_fc.get("features", []):
        props = f.get("properties") or {}
        hcad = str(props.get("hcad_account") or props.get("hcad_num") or "").strip()
        if len(hcad) == 13 and hcad.isdigit():
            needed_hcads.add(hcad)

    quad_names = ("nw_w", "nw_e", "ne", "sw", "se")
    for q in quad_names:
        seq_path = aligned_dir / f"buildings_{q}.geojsonseq"
        with open(seq_path, "rb") as f:
            for raw_line in f:
                line = raw_line[1:] if raw_line.startswith(b"\x1e") else raw_line
                if not line:
                    continue
                feat = orjson.loads(line)
                g = feat.get("geometry")
                if not g or g.get("type") != "Polygon":
                    continue
                ring = g["coordinates"][0]
                props = feat["properties"]
                hcad = str(props.get("hcad_num") or "").strip()
                if not hcad:
                    continue
                lon0, lat0 = float(ring[0][0]), float(ring[0][1])
                in_osm_core = -95.43 <= lon0 <= -95.30 and 29.70 <= lat0 <= 29.82
                if is_cardinal_5pt_rect(ring) or in_osm_core:
                    needed_hcads.add(hcad)
                    if hcad not in hcad_meta:
                        hcad_meta[hcad] = (
                            float(props.get("bld_area") or 1400.0),
                            int(props.get("stories") or 1),
                        )

    print(f"   Identified {len(needed_hcads):,} candidate HCAD parcels in {time.time() - t_scan:.1f}s.")

    # -------------------------------------------------------------------------
    # 3. Read Candidate Parcel Geometries from Parcels.gdb & Compute Oriented Envelopes
    # -------------------------------------------------------------------------
    print("-> [3/6] Loading candidate parcel geometries from Parcels.gdb and computing oriented envelopes...")
    t_gdb = time.time()
    gdb_path = cache_dir / "Parcels" / "Parcels.gdb"
    _, _, wkb_arr, fields = pyogrio.raw.read(
        str(gdb_path),
        layer="Parcels",
        columns=["HCAD_NUM_1", "LocAddr"],
    )
    raw_hcads = fields[0]
    raw_addrs = fields[1]

    selected_indices: list[int] = []
    selected_hcads: list[str] = []
    addr_to_hcad_core: dict[str, str] = {}

    for i, rh in enumerate(raw_hcads):
        if rh is None:
            continue
        h = str(rh).strip()
        if h in needed_hcads:
            selected_indices.append(i)
            selected_hcads.append(h)
        addr_s = str(raw_addrs[i] or "").strip().upper()
        if addr_s and len(h) == 13 and h.isdigit():
            addr_to_hcad_core.setdefault(addr_s, h)

    sel_wkb = wkb_arr[np.array(selected_indices, dtype=np.int64)]
    sel_geoms = shapely.force_2d(shapely.from_wkb(sel_wkb))

    hcad_to_parcel_2278: dict[str, Polygon] = {}
    for h, g in zip(selected_hcads, sel_geoms.tolist()):
        if g is None or g.is_empty:
            continue
        if g.geom_type == "MultiPolygon":
            polys = [p for p in g.geoms if p.geom_type == "Polygon" and not p.is_empty]
            if not polys:
                continue
            poly = max(polys, key=lambda p: p.area)
        elif g.geom_type == "Polygon":
            poly = g
        else:
            continue
        if h not in hcad_to_parcel_2278 or poly.area > hcad_to_parcel_2278[h].area:
            hcad_to_parcel_2278[h] = poly

    unique_hcads_list = list(hcad_to_parcel_2278.keys())
    unique_polys_arr = np.array([hcad_to_parcel_2278[h] for h in unique_hcads_list], dtype=object)
    mrr_polys_arr = shapely.oriented_envelope(unique_polys_arr)

    hcad_to_mrr_2278: dict[str, Polygon] = {}
    hcad_to_angle: dict[str, float] = {}
    for h, mrr in zip(unique_hcads_list, mrr_polys_arr.tolist()):
        hcad_to_mrr_2278[h] = mrr
        hcad_to_angle[h] = compute_mrr_angle_mod90(mrr)

    print(
        f"   Loaded {len(hcad_to_parcel_2278):,} unique parcel polygons & oriented envelopes in {time.time() - t_gdb:.1f}s."
    )

    # -------------------------------------------------------------------------
    # 4. Compute Repaired EPSG:4326 Rings for All Candidate HCAD Parcels
    # -------------------------------------------------------------------------
    print("-> [4/6] Computing Tier 1 (OSM observed) & Tier 3 (parcel-oriented EPSG:2278) footprints...")
    t_rep = time.time()
    repaired_polys_2278: list[Polygon] = []
    repaired_hcads: list[str] = []
    repaired_tier: dict[str, str] = {}  # hcad -> "osm_observed" | "parcel_oriented"

    tier1_osm_cnt = 0
    tier3_oriented_cnt = 0

    for h in unique_hcads_list:
        pcl_poly = hcad_to_parcel_2278[h]
        mrr_poly = hcad_to_mrr_2278[h]
        b_area, stories = hcad_meta.get(h, (1400.0, 1))

        # Tier 1: Check for a real observed OpenStreetMap footprint on this parcel
        osm_match = find_best_osm_footprint_2278(pcl_poly, osm_tree_2278, osm_polys_2278)
        if osm_match is not None and not osm_match.is_empty:
            repaired_polys_2278.append(osm_match)
            repaired_hcads.append(h)
            repaired_tier[h] = "osm_observed"
            tier1_osm_cnt += 1
        else:
            # Tier 3: Synthesize a parcel-oriented, parcel-clipped footprint in EPSG:2278
            oriented_poly = build_oriented_footprint_2278(pcl_poly, mrr_poly, b_area, stories)
            if oriented_poly is not None and not oriented_poly.is_empty:
                repaired_polys_2278.append(oriented_poly)
                repaired_hcads.append(h)
                repaired_tier[h] = "parcel_oriented"
                tier3_oriented_cnt += 1

    repaired_rings_list = batch_transform_polys_2278_to_rings_4326(
        repaired_polys_2278, transformer_to_4326
    )
    repaired_ring_by_hcad: dict[str, list[list[float]]] = dict(
        zip(repaired_hcads, repaired_rings_list)
    )
    repaired_poly2278_by_hcad: dict[str, Polygon] = dict(
        zip(repaired_hcads, repaired_polys_2278)
    )
    print(
        f"   Pre-computed {len(repaired_ring_by_hcad):,} repaired footprints "
        f"({tier1_osm_cnt:,} Tier 1 OSM observed, {tier3_oriented_cnt:,} Tier 3 parcel-oriented) in {time.time() - t_rep:.1f}s."
    )

    # -------------------------------------------------------------------------
    # 5. Repair All 5 Shards (aligned_ndjson/buildings_*.geojsonseq) & Deduplicate
    # -------------------------------------------------------------------------
    print("-> [5/6] Updating all 5 countywide shards (aligned_ndjson/buildings_*.geojsonseq)...")
    t_shards = time.time()

    total_syn_replaced_osm = 0
    total_syn_replaced_oriented = 0
    total_partywall_fixed = 0
    total_stacked_syn_dropped = 0

    # Also track cross-shard boundary observed polygons within 120m of SPLIT_LON_EAST / SPLIT_LON_WEST / SPLIT_LAT
    boundary_obs_polys: list[Polygon] = []

    shard_records: dict[str, list[tuple[dict[str, Any], list[list[float]], bool, str]]] = {}

    for q in quad_names:
        seq_path = aligned_dir / f"buildings_{q}.geojsonseq"
        q_list: list[tuple[dict[str, Any], list[list[float]], bool, str]] = []
        observed_hcads_in_shard: set[str] = set()

        with open(seq_path, "rb") as f:
            raw_lines = f.read().splitlines()

        # First pass: identify which HCAD parcels already have a valid non-synthesized observed footprint
        for raw_line in raw_lines:
            line = raw_line[1:] if raw_line.startswith(b"\x1e") else raw_line
            if not line:
                continue
            feat = orjson.loads(line)
            g = feat.get("geometry")
            if not g or g.get("type") != "Polygon":
                continue
            ring = g["coordinates"][0]
            props = feat["properties"]
            hcad = str(props.get("hcad_num") or "").strip()
            is_syn = is_step4c_syn_rect(ring) or (
                is_cardinal_5pt_rect(ring)
                and angle_diff_mod90(hcad_to_angle.get(hcad, 0.0), 0.0) > 3.5
            )
            if not is_syn and hcad:
                observed_hcads_in_shard.add(hcad)

        # Second pass: repair synthesized boxes and party-wall ring[0] misattributions
        for raw_line in raw_lines:
            line = raw_line[1:] if raw_line.startswith(b"\x1e") else raw_line
            if not line:
                continue
            feat = orjson.loads(line)
            g = feat.get("geometry")
            if not g or g.get("type") != "Polygon":
                q_list.append((feat, [], False, ""))
                continue
            ring = g["coordinates"][0]
            props = feat["properties"]
            hcad = str(props.get("hcad_num") or "").strip()

            is_syn = is_step4c_syn_rect(ring) or (
                is_cardinal_5pt_rect(ring)
                and angle_diff_mod90(hcad_to_angle.get(hcad, 0.0), 0.0) > 3.5
            )

            if is_syn:
                # If this parcel ALREADY has a real observed footprint in the shard (e.g. stacked condo account),
                # drop this redundant synthesized box!
                if hcad in observed_hcads_in_shard:
                    total_stacked_syn_dropped += 1
                    continue
                new_ring = repaired_ring_by_hcad.get(hcad)
                if new_ring:
                    g["coordinates"] = [new_ring]
                    ring = new_ring
                    if repaired_tier.get(hcad) == "osm_observed":
                        total_syn_replaced_osm += 1
                        is_syn = False
                    else:
                        total_syn_replaced_oriented += 1
            else:
                # Non-synthesized footprint in historic core: check if its centroid is > 10 ft outside its assigned parcel
                lon0, lat0 = float(ring[0][0]), float(ring[0][1])
                if (
                    -95.43 <= lon0 <= -95.30
                    and 29.70 <= lat0 <= 29.82
                    and hcad in hcad_to_parcel_2278
                    and repaired_tier.get(hcad) == "osm_observed"
                ):
                    n_pts = max(1, len(ring) - 1)
                    clon = sum(pt[0] for pt in ring[:n_pts]) / n_pts
                    clat = sum(pt[1] for pt in ring[:n_pts]) / n_pts
                    cx, cy = transformer_to_2278.transform(clon, clat)
                    pcl_poly = hcad_to_parcel_2278[hcad]
                    if shapely.distance(Point(cx, cy), pcl_poly) > 10.0:
                        new_ring = repaired_ring_by_hcad.get(hcad)
                        if new_ring:
                            g["coordinates"] = [new_ring]
                            ring = new_ring
                            total_partywall_fixed += 1

            # Track observed footprints near shard split boundaries so synthesized boxes across the line are suppressed
            if not is_syn and ring:
                lon0, lat0 = float(ring[0][0]), float(ring[0][1])
                near_split = (
                    abs(lon0 - SPLIT_LON_EAST) < 0.0025
                    or abs(lon0 - SPLIT_LON_WEST) < 0.0025
                    or abs(lat0 - SPLIT_LAT) < 0.0025
                )
                if near_split:
                    try:
                        boundary_obs_polys.append(Polygon(ring))
                    except Exception:
                        pass

            q_list.append((feat, ring, is_syn, hcad))

        shard_records[q] = q_list

    boundary_tree = STRtree(boundary_obs_polys) if boundary_obs_polys else None

    # Third pass per shard: drop any remaining synthesized footprint that significantly overlaps (> 18%)
    # an observed footprint in the same shard or across a shard boundary, and write out updated .geojsonseq
    for q in quad_names:
        seq_path = aligned_dir / f"buildings_{q}.geojsonseq"
        tmp_path = aligned_dir / f"buildings_{q}.geojsonseq.tmp"
        q_list = shard_records[q]

        # Build STRtree of observed footprints within this shard that are near synthesized footprints
        obs_polys_q: list[Polygon] = []
        syn_indices_q: list[int] = []
        syn_polys_q: list[Polygon] = []

        for idx, (feat, ring, is_syn, hcad) in enumerate(q_list):
            if not ring:
                continue
            if is_syn:
                syn_indices_q.append(idx)
                syn_polys_q.append(Polygon(ring))
            else:
                obs_polys_q.append(Polygon(ring))

        drop_idx_set: set[int] = set()
        if syn_polys_q and obs_polys_q:
            obs_tree_q = STRtree(obs_polys_q)
            syn_arr = np.array(syn_polys_q, dtype=object)
            obs_arr = np.array(obs_polys_q, dtype=object)
            pairs = obs_tree_q.query(syn_arr, predicate="intersects")
            if len(pairs[0]) > 0:
                s_i = pairs[0]
                o_i = pairs[1]
                inter_a = shapely.area(shapely.intersection(syn_arr[s_i], obs_arr[o_i]))
                syn_a = shapely.area(syn_arr[s_i])
                ratio = np.where(syn_a > 0, inter_a / syn_a, 0.0)
                for si_val in s_i[ratio > 0.18].tolist():
                    drop_idx_set.add(syn_indices_q[int(si_val)])

        if syn_polys_q and boundary_tree is not None:
            syn_arr = np.array(syn_polys_q, dtype=object)
            b_arr = np.array(boundary_obs_polys, dtype=object)
            b_pairs = boundary_tree.query(syn_arr, predicate="intersects")
            if len(b_pairs[0]) > 0:
                s_i = b_pairs[0]
                b_i = b_pairs[1]
                inter_a = shapely.area(shapely.intersection(syn_arr[s_i], b_arr[b_i]))
                syn_a = shapely.area(syn_arr[s_i])
                ratio = np.where(syn_a > 0, inter_a / syn_a, 0.0)
                for si_val in s_i[ratio > 0.18].tolist():
                    drop_idx_set.add(syn_indices_q[int(si_val)])

        total_stacked_syn_dropped += len(drop_idx_set)

        written_q = 0
        with open(tmp_path, "wb") as out_f:
            for idx, (feat, ring, is_syn, hcad) in enumerate(q_list):
                if idx in drop_idx_set:
                    continue
                out_f.write(b"\x1e" + orjson.dumps(feat) + b"\n")
                written_q += 1

        tmp_path.replace(seq_path)
        print(f"   Shard {seq_path.name}: {written_q:,} clean oriented footprints written.")

    print(
        f"   Completed shard repair in {time.time() - t_shards:.1f}s:\n"
        f"     - Replaced with Tier 1 observed OSM footprint : {total_syn_replaced_osm:,}\n"
        f"     - Replaced with Tier 3 parcel-oriented polygon: {total_syn_replaced_oriented:,}\n"
        f"     - Fixed party-wall ring[0] misattributions    : {total_partywall_fixed:,}\n"
        f"     - Dropped redundant stacked synthesized boxes : {total_stacked_syn_dropped:,}"
    )

    # -------------------------------------------------------------------------
    # 6. Repair curated_overrides.json, buildings.geojson & overlays.json
    # -------------------------------------------------------------------------
    print("-> [6/6] Updating curated_overrides.json, buildings.geojson, and overlays.json...")
    ov_fixed_cnt = 0
    updated_override_centroids: dict[str, tuple[float, float]] = {}

    for k, rec in overrides_map.items():
        g = rec.get("geometry")
        if not g or g.get("type") != "Polygon":
            continue
        ring = g["coordinates"][0]
        if not is_cardinal_5pt_rect(ring):
            continue
        hcad = str(rec.get("hcad_num") or k.split("#")[0]).strip()
        # For #aux_ auxiliary structures or non-HCAD keys, check parcel orientation
        if hcad in repaired_ring_by_hcad and "#aux_" not in k:
            new_ring = repaired_ring_by_hcad[hcad]
            g["coordinates"] = [new_ring]
            ov_fixed_cnt += 1
            poly_4326 = Polygon(new_ring)
            rp = shapely.point_on_surface(poly_4326)
            updated_override_centroids[k] = (round(float(rp.x), 6), round(float(rp.y), 6))
            updated_override_centroids[hcad] = (round(float(rp.x), 6), round(float(rp.y), 6))

    overrides_path.write_text(json.dumps(overrides_doc, indent=2), encoding="utf-8")
    print(f"   Updated {ov_fixed_cnt} cardinal-box overrides in curated_overrides.json.")

    blds_fixed_cnt = 0
    for f in blds_fc.get("features", []):
        g = f.get("geometry")
        if not g or g.get("type") != "Polygon":
            continue
        ring = g["coordinates"][0]
        props = f.get("properties") or {}
        if not is_cardinal_5pt_rect(ring) and props.get("footprint_source") != "derived_parcel":
            continue
        hcad = str(props.get("hcad_account") or props.get("hcad_num") or "").strip()
        if not hcad:
            addr = str(props.get("address") or "").strip().upper()
            hcad = addr_to_hcad_core.get(addr, "")
        if hcad and hcad in repaired_ring_by_hcad:
            mrr_ang = hcad_to_angle.get(hcad, 0.0)
            if (
                props.get("footprint_source") == "derived_parcel"
                or is_step4c_syn_rect(ring)
                or angle_diff_mod90(mrr_ang, 0.0) > 3.5
            ):
                g["coordinates"] = [repaired_ring_by_hcad[hcad]]
                if props.get("footprint_source") == "derived_parcel" and repaired_tier.get(hcad) == "osm_observed":
                    props["footprint_source"] = "observed"
                blds_fixed_cnt += 1

    blds_geojson_path.write_bytes(orjson.dumps(blds_fc))
    print(f"   Updated {blds_fixed_cnt} cardinal/derived footprints in buildings.geojson.")

    # Snap Good Brick & Landmark overlay pins if their override footprint was updated
    if updated_override_centroids:
        overlays_path = data_dir / "overlays.json"
        overlays_doc = json.loads(overlays_path.read_text(encoding="utf-8"))
        snapped_pins = 0
        for layer_key in ("good_brick_awards", "landmarks"):
            fc = overlays_doc.get(layer_key, {})
            for feat in fc.get("features", []):
                p = feat.get("properties") or {}
                bid = str(p.get("building_id") or "").strip()
                hcad = str(p.get("hcad_num") or "").strip()
                new_pt = updated_override_centroids.get(bid) or updated_override_centroids.get(hcad)
                if new_pt and feat.get("geometry", {}).get("type") == "Point":
                    feat["geometry"]["coordinates"] = [new_pt[0], new_pt[1]]
                    snapped_pins += 1
        if snapped_pins > 0:
            overlays_path.write_text(json.dumps(overlays_doc), encoding="utf-8")
            print(f"   Snapped {snapped_pins} Good Brick / Landmark overlay points to repaired building interiors.")

    # Deduplicate any newly-created exact centroid stacks or overlapping polygons across all 5 shards
    from concurrent.futures import ProcessPoolExecutor
    from atlas_pipeline.full_build import deduplicate_geojsonseq_shard

    print("-> Deduplicating overlapping 3D building polygons across all 5 shards in parallel...")
    t_dedup = time.time()
    shard_paths = [aligned_dir / f"buildings_{q}.geojsonseq" for q in quad_names]
    with ProcessPoolExecutor(max_workers=len(shard_paths)) as pool:
        for s_name, before_cnt, after_cnt in pool.map(deduplicate_geojsonseq_shard, shard_paths):
            print(
                f"   Deduplicated {s_name}: {before_cnt:,} -> {after_cnt:,} clean footprints "
                f"(removed {before_cnt - after_cnt:,} overlapping/stacked duplicates)"
            )
    print(f"-> Completed shard overlap deduplication in {time.time() - t_dedup:.1f}s.")

    # Recompile all 5 PMTiles Shards in parallel via Tippecanoe
    tippecanoe_bin = "/tmp/tippecanoe/tippecanoe"
    if not Path(tippecanoe_bin).exists():
        tippecanoe_bin = str(Path.home() / ".local" / "bin" / "tippecanoe")

    print("-> Compiling 5 Web Mercator tile-aligned PMTiles v3 archives in parallel via Tippecanoe...")
    t_tip = time.time()
    procs: list[tuple[str, subprocess.Popen[bytes], Path]] = []
    for q in quad_names:
        in_seq = aligned_dir / f"buildings_{q}.geojsonseq"
        out_pmtiles = data_dir / f"houston_buildings_{q}.pmtiles"
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

    for q, proc, out_pmtiles in procs:
        _, stderr = proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"Tippecanoe failed for quadrant {q}: {stderr.decode('utf-8', errors='replace')}")
        sz = out_pmtiles.stat().st_size
        print(f"   Compiled {out_pmtiles.name}: {sz / (1024 * 1024):.1f} MB")

    print(f"-> Tippecanoe compilation finished in {time.time() - t_tip:.1f}s.")
    print(f"=== Completed Polygon Orientation & Footprint Repair in {time.time() - t0:.1f}s ===")


if __name__ == "__main__":
    run_fix_polygon_orientations()
