"""
Recover observed building footprints and uncovered multi-block / multi-wing sections
that were dropped between quadrant_ndjson and aligned_ndjson due to:
  1. MultiPolygon parcel collapse in fix_polygon_orientations.py (which kept only the
     single largest sub-polygon per HCAD_NUM_1 and overwrote observed footprints on
     the other sub-polygons or multi-building parcels in the historic core), and
  2. Compound footprint dropping in deduplicate_geojsonseq_shard (which dropped large
     Microsoft/OSM footprints spanning multiple sub-parcels or wings whenever >= 2
     smaller footprints covered part of one block/wing, leaving the uncovered block/wing blank).
"""

from __future__ import annotations

from collections import defaultdict
import csv
import json
from pathlib import Path
import subprocess
import time
from typing import Any

import numpy as np
import orjson
import pyogrio.raw
from pyproj import Transformer
import shapely
from shapely.geometry import MultiPolygon, Polygon
from shapely.strtree import STRtree

SPLIT_LAT = 29.76437738
SPLIT_LON_EAST = -95.361328125
SPLIT_LON_WEST = -95.537109375
DEG2_TO_M2 = 111320.0 * 96486.0
MIN_AREA_M2 = 110.0
MIN_AREA_DEG2 = MIN_AREA_M2 / DEG2_TO_M2


def get_quadrant(lon: float, lat: float) -> str:
    if lat >= SPLIT_LAT:
        if lon < SPLIT_LON_EAST:
            return "nw_w" if lon < SPLIT_LON_WEST else "nw_e"
        return "ne"
    return "sw" if lon < SPLIT_LON_EAST else "se"


def extract_polygons(geom: Any) -> list[Polygon]:
    if geom is None or geom.is_empty:
        return []
    if geom.geom_type == "Polygon":
        return [geom]
    if hasattr(geom, "geoms"):
        out: list[Polygon] = []
        for g in geom.geoms:
            out.extend(extract_polygons(g))
        return out
    return []


def run_recover_dropped_footprints() -> None:
    t0 = time.time()
    repo_root = Path(__file__).resolve().parents[2]
    cache_dir = repo_root / "pipeline" / "cache"
    aligned_dir = cache_dir / "aligned_ndjson"
    quad_dir = cache_dir / "quadrant_ndjson"
    data_dir = repo_root / "app" / "public" / "data"
    quad_names = ("nw_w", "nw_e", "ne", "sw", "se")

    # 1. Load undated_buildings_resolved_report.csv for 100% dated provenance
    resolved_undated: dict[str, tuple[int, int, str]] = {}
    resolved_csv = cache_dir / "undated_buildings_resolved_report.csv"
    if resolved_csv.exists():
        with open(resolved_csv, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                h = row["hcad_num"].strip()
                yr = int(row["resolved_year_built"] or 0)
                if h and yr >= 1836:
                    resolved_undated[h] = (yr, (yr // 10) * 10, row["year_source"].strip())

    # 2. Load curated_overrides.json
    overrides_path = data_dir / "curated_overrides.json"
    overrides_doc = json.loads(overrides_path.read_text(encoding="utf-8"))
    overrides_map: dict[str, dict[str, Any]] = overrides_doc.get("overrides", {})
    suppress_hcads: set[str] = set()
    ov_polys_by_q: dict[str, list[Polygon]] = defaultdict(list)

    for k, rec in overrides_map.items():
        h = str(rec.get("hcad_num") or k.split("#")[0]).strip()
        if rec.get("suppress_only"):
            suppress_hcads.add(h)
            continue
        if not k.startswith(h + "#") or rec.get("replace_parcel_shards"):
            if h:
                suppress_hcads.add(h)
        for sh in rec.get("suppress_shard_hcads") or []:
            suppress_hcads.add(str(sh).strip())
        g = rec.get("geometry")
        if g:
            polys = (
                [Polygon(g["coordinates"][0])]
                if g["type"] == "Polygon"
                else [Polygon(c[0]) for c in g["coordinates"]]
            )
            for p in polys:
                if not p.is_empty:
                    c0 = p.exterior.coords[0]
                    ov_polys_by_q[get_quadrant(float(c0[0]), float(c0[1]))].append(p)

    # 3. Index aligned_ndjson centroids, rendered polygons per shard, and hcad metadata
    print("-> [1/5] Indexing aligned_ndjson centroids and rendered polygons across all 5 shards...")
    aligned_centroids: set[tuple[int, int]] = set()
    aligned_hcad_meta: dict[str, tuple[int, int, str | None]] = {}
    rend_polys_by_q: dict[str, list[Polygon]] = {q: list(ov_polys_by_q[q]) for q in quad_names}

    for q in quad_names:
        p_a = aligned_dir / f"buildings_{q}.geojsonseq"
        with open(p_a, "rb") as f:
            for line in f:
                line = line.lstrip(b"\x1e").strip()
                if not line:
                    continue
                feat = orjson.loads(line)
                g = feat.get("geometry")
                if not g or g.get("type") != "Polygon":
                    continue
                ring = g["coordinates"][0]
                n_pts = max(1, len(ring) - 1)
                cx = sum(pt[0] for pt in ring[:n_pts]) / n_pts
                cy = sum(pt[1] for pt in ring[:n_pts]) / n_pts
                aligned_centroids.add((round(cx, 5), round(cy, 5)))
                props = feat["properties"]
                h = str(props.get("hcad_num") or "").strip()
                yr = int(props.get("year_built") or 0)
                if h and yr >= 1836:
                    aligned_hcad_meta[h] = (yr, int(props.get("decade") or (yr // 10) * 10), props.get("year_source"))
                if h not in suppress_hcads:
                    rend_polys_by_q[q].append(Polygon(ring))

    print(f"   Indexed {len(aligned_centroids):,} aligned centroids in {time.time() - t0:.1f}s.")

    # 4. Scan quadrant_ndjson for observed footprints absent from aligned_centroids
    print("-> [2/5] Scanning quadrant_ndjson for dropped/partially-uncovered observed footprints...")
    t_scan = time.time()
    cands_by_q: dict[str, list[tuple[dict[str, Any], Polygon, str]]] = defaultdict(list)

    for q_src in quad_names:
        p_q = quad_dir / f"buildings_{q_src}.geojsonseq"
        with open(p_q, "rb") as f:
            for line in f:
                line = line.lstrip(b"\x1e").strip()
                if not line:
                    continue
                feat = orjson.loads(line)
                g = feat.get("geometry")
                if not g or g.get("type") != "Polygon":
                    continue
                ring = g["coordinates"][0]
                # Skip 5-vertex cardinal synthesized rectangles
                if (
                    len(ring) == 5
                    and abs(ring[0][1] - ring[1][1]) < 1e-7
                    and abs(ring[1][0] - ring[2][0]) < 1e-7
                ):
                    continue
                props = feat["properties"]
                h = str(props.get("hcad_num") or "").strip()
                if not h or h in suppress_hcads:
                    continue
                yr = int(props.get("year_built") or 0)
                if yr < 1836:
                    if h in aligned_hcad_meta:
                        yr, dec, ys = aligned_hcad_meta[h]
                        props["year_built"] = yr
                        props["decade"] = dec
                        if ys:
                            props["year_source"] = ys
                    elif h in resolved_undated:
                        yr, dec, ys = resolved_undated[h]
                        props["year_built"] = yr
                        props["decade"] = dec
                        if ys:
                            props["year_source"] = ys
                if yr < 1836 and int(props.get("bld_area") or 0) == 0:
                    continue
                n_pts = max(1, len(ring) - 1)
                cx = sum(pt[0] for pt in ring[:n_pts]) / n_pts
                cy = sum(pt[1] for pt in ring[:n_pts]) / n_pts
                if (round(cx, 5), round(cy, 5)) in aligned_centroids:
                    continue
                area_deg2 = 0.5 * abs(
                    sum(ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1] for i in range(n_pts))
                )
                if area_deg2 < MIN_AREA_DEG2:
                    continue
                poly = Polygon(ring)
                if not poly.is_valid:
                    poly = shapely.make_valid(poly)
                    polys = extract_polygons(poly)
                    if not polys:
                        continue
                    poly = max(polys, key=lambda p: p.area)
                q_dst = get_quadrant(float(ring[0][0]), float(ring[0][1]))
                cands_by_q[q_dst].append((feat, poly, h))

    uncov_by_q: dict[str, list[tuple[dict[str, Any], Polygon, str, list[Polygon]]]] = defaultdict(list)
    needed_hcads: set[str] = {"1274570010001"}

    for q in quad_names:
        c_list = cands_by_q[q]
        if not c_list:
            continue
        rend_arr = np.array(rend_polys_by_q[q], dtype=object)
        rend_tree = STRtree(rend_arr)
        c_arr = np.array([it[1] for it in c_list], dtype=object)
        c_areas = shapely.area(c_arr)
        pairs = rend_tree.query(c_arr, predicate="intersects")
        inter_areas = shapely.area(shapely.intersection(c_arr[pairs[0]], rend_arr[pairs[1]]))
        cov_sum = np.zeros(len(c_arr), dtype=np.float64)
        np.add.at(cov_sum, pairs[0], inter_areas)

        hits_map: dict[int, list[Polygon]] = defaultdict(list)
        for ci, ri in zip(pairs[0].tolist(), pairs[1].tolist()):
            hits_map[ci].append(rend_arr[ri])

        for ci, (feat, poly, h) in enumerate(c_list):
            a = float(c_areas[ci])
            if a <= 0:
                continue
            cov = float(cov_sum[ci]) / a
            uncov_a = a - float(cov_sum[ci])
            if cov < 0.62 and uncov_a >= MIN_AREA_DEG2:
                needed_hcads.add(h)
                uncov_by_q[q].append((feat, poly, h, hits_map.get(ci, [])))

    print(
        f"   Found {sum(len(v) for v in uncov_by_q.values()):,} uncovered candidates across {len(needed_hcads):,} HCADs in {time.time() - t_scan:.1f}s."
    )

    # 5. Load ALL parcel sub-polygons for needed_hcads from Parcels.gdb
    print("-> [3/5] Loading parcel sub-polygons from Parcels.gdb and clipping/carving uncovered wings...")
    t_gdb = time.time()
    gdb_dir = cache_dir / "Parcels" / "Parcels.gdb"
    _, _, wkb_arr, fields = pyogrio.raw.read(str(gdb_dir), layer="Parcels", columns=["HCAD_NUM_1"])
    raw_hcads = fields[0]
    sel_idx: list[int] = []
    sel_h: list[str] = []
    for i, rh in enumerate(raw_hcads):
        if rh is not None:
            h = str(rh).strip()
            if h in needed_hcads:
                sel_idx.append(i)
                sel_h.append(h)

    sel_geoms_2278 = shapely.force_2d(shapely.from_wkb(wkb_arr[np.array(sel_idx, dtype=np.int64)]))
    transformer_to_4326 = Transformer.from_crs("EPSG:2278", "EPSG:4326", always_xy=True)

    hcad_subpolys_4326: dict[str, list[Polygon]] = defaultdict(list)
    for h, g in zip(sel_h, sel_geoms_2278.tolist()):
        for p in extract_polygons(g):
            coords = np.asarray(p.exterior.coords)
            lons, lats = transformer_to_4326.transform(coords[:, 0], coords[:, 1])
            ring_4326 = [[round(float(x), 7), round(float(y), 7)] for x, y in zip(lons, lats)]
            if len(ring_4326) >= 4:
                p4326 = Polygon(ring_4326)
                if not p4326.is_valid:
                    p4326 = shapely.make_valid(p4326)
                for vp in extract_polygons(p4326):
                    if not vp.is_empty:
                        hcad_subpolys_4326[h].append(vp)

    recovered_by_q: dict[str, list[dict[str, Any]]] = defaultdict(list)
    core_recovered_features: list[dict[str, Any]] = []
    rec_seq_counter = 0

    for q in quad_names:
        cand_records = uncov_by_q[q]
        if not cand_records:
            continue
        # Sort largest area first so when deduplicating among recovered candidates we prefer cleaner/larger footprints
        cand_records.sort(key=lambda it: it[1].area, reverse=True)
        accepted_grid_q: dict[tuple[int, int], list[Polygon]] = defaultdict(list)

        for feat, poly, h, hit_polys in cand_records:
            subpolys = hcad_subpolys_4326.get(h, [])
            work_geom: Any = poly
            if subpolys:
                pcl_union = shapely.unary_union(subpolys).buffer(0.000012)
                inter_pcl = poly.intersection(pcl_union)
                # Only keep portion inside parcel union (prevents multi-block footprints from bridging across streets
                # and prevents party-wall misattributed footprints from duplicating buildings on neighbor parcels!)
                if inter_pcl.is_empty or (inter_pcl.area / poly.area) < 0.25:
                    continue
                work_geom = inter_pcl

            if hit_polys:
                work_geom = work_geom.difference(shapely.unary_union(hit_polys).buffer(0.000018))

            for part in extract_polygons(work_geom):
                area_m2 = part.area * DEG2_TO_M2
                if area_m2 < MIN_AREA_M2:
                    continue
                eroded = part.buffer(-0.000038)
                if eroded.is_empty or (eroded.area / part.area) < 0.35:
                    continue
                # Check against already accepted recovered polygons in 3x3 grid neighborhood (~500m cells)
                minx, miny, maxx, maxy = part.bounds
                gx0, gx1 = int(minx * 200), int(maxx * 200)
                gy0, gy1 = int(miny * 200), int(maxy * 200)
                dup = False
                for gx in range(gx0 - 1, gx1 + 2):
                    for gy in range(gy0 - 1, gy1 + 2):
                        for ap in accepted_grid_q.get((gx, gy), ()):
                            if part.intersects(ap):
                                ia = part.intersection(ap).area
                                if ia / min(part.area, ap.area) > 0.20:
                                    dup = True
                                    break
                        if dup:
                            break
                    if dup:
                        break
                if dup:
                    continue

                for gx in range(gx0, gx1 + 1):
                    for gy in range(gy0, gy1 + 1):
                        accepted_grid_q[(gx, gy)].append(part)
                rec_seq_counter += 1
                new_ring = [[round(float(x), 7), round(float(y), 7)] for x, y in part.exterior.coords]
                if new_ring[0] != new_ring[-1]:
                    new_ring.append(new_ring[0])
                new_props = dict(feat["properties"])
                new_props["id"] = f"{h}#rec_{rec_seq_counter}"
                new_feat = {
                    "type": "Feature",
                    "geometry": {"type": "Polygon", "coordinates": [new_ring]},
                    "properties": new_props,
                }
                q_target = get_quadrant(float(new_ring[0][0]), float(new_ring[0][1]))
                recovered_by_q[q_target].append(new_feat)

                lon0, lat0 = float(new_ring[0][0]), float(new_ring[0][1])
                if -95.42 <= lon0 <= -95.33 and 29.71 <= lat0 <= 29.81:
                    core_Props = dict(new_props)
                    core_Props["hcad_account"] = h
                    core_Props["footprint_source"] = "observed"
                    core_recovered_features.append(
                        {
                            "type": "Feature",
                            "geometry": {"type": "Polygon", "coordinates": [new_ring]},
                            "properties": core_Props,
                        }
                    )

    total_rec = sum(len(v) for v in recovered_by_q.values())
    print(
        f"   Recovered {total_rec:,} clean parcel-clipped building footprints ({len(core_recovered_features):,} in historic core) in {time.time() - t_gdb:.1f}s."
    )

    # 6. Append recovered footprints to aligned_ndjson shards & update buildings.geojson + curated_overrides.json
    print("-> [4/5] Writing updated aligned_ndjson shards, buildings.geojson, and curated_overrides.json...")
    camden_rings: list[list[list[float]]] = []

    for q in quad_names:
        seq_path = aligned_dir / f"buildings_{q}.geojsonseq"
        rec_list = recovered_by_q[q]
        if rec_list:
            with open(seq_path, "ab") as f:
                for rf in rec_list:
                    f.write(b"\x1e" + orjson.dumps(rf) + b"\n")
        print(f"   Shard buildings_{q}.geojsonseq: appended +{len(rec_list):,} recovered footprints.")

    # Collect all 5 building footprints for 1274570010001 (301 St Joseph Pkwy — Camden City Centre)
    with open(aligned_dir / "buildings_sw.geojsonseq", "rb") as f:
        for line in f:
            line = line.lstrip(b"\x1e").strip()
            if not line:
                continue
            feat = orjson.loads(line)
            if feat["properties"].get("hcad_num") == "1274570010001":
                camden_rings.append(feat["geometry"]["coordinates"][0])

    print(f"   Verified 1274570010001 (301 St Joseph Pkwy / Camden City Centre) now has {len(camden_rings)} footprints in buildings_sw.geojsonseq.")

    if len(camden_rings) >= 4:
        overrides_map["1274570010001"] = {
            "hcad_num": "1274570010001",
            "building_name": "Camden City Centre",
            "alt_names": ["Camden City Centre Apartments", "301 St. Joseph Parkway"],
            "name_source": "Preservation Houston Curated Archive",
            "address": "301 ST JOSEPH PKY",
            "year_built": 2006,
            "decade": 2000,
            "use_category": "Residential",
            "bld_style": "Contemporary Multi-Block Mid-Rise Residential",
            "stories": 4,
            "height_m": 14.0,
            "bld_area": 550461,
            "land_area": 232179,
            "owner": "2009 CPT COMMUNITY OWNER LLC",
            "source_type": "HCAD + OpenStreetMap + Microsoft US Building Footprints",
            "source_citation": "Four-block mid-rise residential complex south of Bethel Baptist Church (bounded by Ruthven St, Heiner St, Pierce St / St. Joseph Pkwy, and Arthur St, bisected by Crosby St and Cleveland St).",
            "status": "Approved",
            "geometry": {
                "type": "MultiPolygon",
                "coordinates": [[r] for r in camden_rings],
            },
        }
        overrides_path.write_text(json.dumps(overrides_doc, indent=2), encoding="utf-8")
        print("   Updated curated_overrides.json with MultiPolygon override for 1274570010001 (Camden City Centre).")

    # Update buildings.geojson with core_recovered_features
    blds_geojson_path = data_dir / "buildings.geojson"
    blds_fc = orjson.loads(blds_geojson_path.read_bytes())
    existing_core_polys = [
        Polygon(f["geometry"]["coordinates"][0])
        for f in blds_fc.get("features", [])
        if f.get("geometry", {}).get("type") == "Polygon"
    ]
    core_tree = STRtree(existing_core_polys) if existing_core_polys else None
    added_core = 0
    for cf in core_recovered_features:
        cp = Polygon(cf["geometry"]["coordinates"][0])
        if core_tree is not None:
            hits = core_tree.query(cp, predicate="intersects")
            if any(cp.intersection(existing_core_polys[i]).area / min(cp.area, existing_core_polys[i].area) > 0.25 for i in hits):
                continue
        blds_fc["features"].append(cf)
        added_core += 1
    blds_geojson_path.write_bytes(orjson.dumps(blds_fc))
    print(f"   Added +{added_core:,} recovered historic core footprints to buildings.geojson.")

    # 7. Recompile all 5 PMTiles shards in parallel via Tippecanoe
    tippecanoe_bin = "/tmp/tippecanoe/tippecanoe"
    if not Path(tippecanoe_bin).exists():
        tippecanoe_bin = str(Path.home() / ".local" / "bin" / "tippecanoe")

    print("-> [5/5] Compiling 5 Web Mercator tile-aligned PMTiles v3 archives in parallel via Tippecanoe...")
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
    print(f"=== Completed Dropped Building Footprint Recovery in {time.time() - t0:.1f}s ===")


if __name__ == "__main__":
    run_recover_dropped_footprints()
