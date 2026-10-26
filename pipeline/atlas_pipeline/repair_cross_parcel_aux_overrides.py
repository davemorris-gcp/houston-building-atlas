"""Audit and repair cross-parcel #aux_ footprint overrides in curated_overrides.json.

Early first-vertex (ring[0]) spatial joins could assign a neighboring house whose roof eave
overhung a shared lot line to an adjacent parcel. When that adjacent parcel already had a
curated override, secondary-structure promotion froze the neighbor's main building into
curated_overrides.json as '<hcad_num>#aux_N', painting the wrong address/year over the neighbor
and leaving several neighbor parcels without a footprint in the countywide PMTiles shards.

This script:
  1. Verifies every '#aux_' override polygon's interior point (shapely.point_on_surface) in
     EPSG:2278 against Parcels.gdb.
  2. Purges cross-parcel '#aux_' overrides (re-keying only when the true parcel is itself an
     overridden multi-structure parcel whose shard features are suppressed).
  3. Restores or re-tags any swallowed neighbor footprints in aligned_ndjson/buildings_*.geojsonseq
     with authoritative HCAD CAMA / historic-core metadata (preserving 100% dated coverage).
  4. Deduplicates and recompiles modified PMTiles shards via Tippecanoe and updates
     pmtiles_manifest.json.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import time
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
import orjson
import pyogrio.raw
import shapely
from pyproj import Transformer
from shapely.geometry import shape

from atlas_pipeline.full_build import (
    SPLIT_LAT,
    SPLIT_LON_EAST,
    SPLIT_LON_WEST,
    classify_use_category,
    compute_decade,
    deduplicate_geojsonseq_shard,
    estimate_stories_and_height,
    normalize_year,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = REPO_ROOT / "pipeline" / "cache"
DATA_DIR = REPO_ROOT / "app" / "public" / "data"
ALIGNED_DIR = CACHE_DIR / "aligned_ndjson"


def get_quadrant(lon: float, lat: float) -> str:
    """Return the Zoom-11 tile-aligned PMTiles shard key for (lon, lat)."""
    if lat >= SPLIT_LAT:
        if lon < SPLIT_LON_EAST:
            return "nw_w" if lon < SPLIT_LON_WEST else "nw_e"
        return "ne"
    return "sw" if lon < SPLIT_LON_EAST else "se"


def wgs84_geom_to_2278(geom_dict: dict[str, Any], tf_to_2278: Transformer) -> shapely.Geometry:
    """Project a GeoJSON geometry dict from EPSG:4326 to 2D EPSG:2278."""
    g = shapely.force_2d(shape(geom_dict))
    coords = shapely.get_coordinates(g)
    xs, ys = tf_to_2278.transform(coords[:, 0], coords[:, 1])
    return shapely.set_coordinates(g, np.column_stack([xs, ys]))


def identify_cross_parcel_aux_overrides(
    overrides_map: dict[str, dict[str, Any]],
    parcel_tree: shapely.STRtree,
    parcel_hcads: np.ndarray,
    tf_to_2278: Transformer,
) -> list[tuple[str, str, str, int]]:
    """Return [(aux_key, tagged_hcad, true_hcad, true_parcel_idx)] for cross-parcel #aux_ entries."""
    mismatches: list[tuple[str, str, str, int]] = []
    for key, rec in list(overrides_map.items()):
        if "#aux_" not in key or not isinstance(rec, dict):
            continue
        geom = rec.get("geometry")
        if not geom:
            continue
        base_hcad = key.split("#")[0].strip()
        g2278 = wgs84_geom_to_2278(geom, tf_to_2278)
        pt = shapely.point_on_surface(g2278)
        hits = parcel_tree.query(pt, predicate="within")
        if len(hits) == 0:
            continue
        found_hcads = [str(parcel_hcads[idx] or "").strip() for idx in hits]
        if base_hcad in found_hcads:
            continue
        for idx in hits:
            candidate = str(parcel_hcads[idx] or "").strip()
            if candidate:
                mismatches.append((key, base_hcad, candidate, int(idx)))
                break
    return mismatches


def reconcile_cross_parcel_overrides(
    overrides_map: dict[str, dict[str, Any]],
    mismatches: list[tuple[str, str, str, int]],
    parcel_addrs: np.ndarray | list[Any],
    parcel_yrs: np.ndarray | list[Any],
    parcel_areas: np.ndarray | list[Any],
    cama_by_hcad: dict[str, dict[str, Any]],
    bgeo_by_hcad: dict[str, dict[str, Any]],
    tf_to_2278: Transformer,
) -> tuple[
    list[str],
    list[tuple[str, str]],
    dict[str, list[tuple[str, str, str, dict[str, Any], dict[str, Any]]]],
]:
    """Mutate overrides_map in place to purge/re-key cross-parcel #aux_ records and return pending shard repairs."""
    purged_keys: list[str] = []
    rekeyed_keys: list[tuple[str, str]] = []
    pending_by_quad: dict[str, list[tuple[str, str, str, dict[str, Any], dict[str, Any]]]] = {}

    for aux_key, base_hcad, true_hcad, p_idx in mismatches:
        old_rec = overrides_map.pop(aux_key)
        purged_keys.append(aux_key)
        geom_dict = old_rec["geometry"]
        poly_4326 = shape(geom_dict)
        poly_2278 = wgs84_geom_to_2278(geom_dict, tf_to_2278)
        fp_sqft = float(shapely.area(poly_2278))

        existing_true_ov_keys = [
            k for k in overrides_map if k == true_hcad or k.startswith(f"{true_hcad}#")
        ]
        overlaps_existing_ov = False
        for tk in existing_true_ov_keys:
            tg = overrides_map[tk].get("geometry")
            if tg:
                tpoly = shape(tg)
                inter = poly_4326.intersection(tpoly).area / max(min(poly_4326.area, tpoly.area), 1e-12)
                if inter >= 0.30:
                    overlaps_existing_ov = True
                    break

        if existing_true_ov_keys and not overlaps_existing_ov:
            base_true_ov = overrides_map.get(true_hcad, {})
            next_idx = 1
            while f"{true_hcad}#aux_{next_idx}" in overrides_map:
                next_idx += 1
            new_key = f"{true_hcad}#aux_{next_idx}"
            yr_ov = int(base_true_ov.get("year_built") or old_rec.get("year_built") or 0)
            hd_ov = str(
                base_true_ov.get("historic_district") or old_rec.get("historic_district") or ""
            ).strip()
            contrib_ov = str(
                base_true_ov.get("contributing")
                or old_rec.get("contributing")
                or ("Contributing" if hd_ov else "Outside Historic District")
            ).strip()
            rekeyed_entry: dict[str, Any] = {
                "id": new_key,
                "building_id": new_key,
                "hcad_num": true_hcad,
                "address": base_true_ov.get("address") or str(parcel_addrs[p_idx] or "").strip(),
                "building_name": base_true_ov.get("building_name") or old_rec.get("building_name") or "",
                "year_built": yr_ov,
                "decade": compute_decade(yr_ov) if yr_ov >= 1836 else 0,
                "stories": float(old_rec.get("stories") or base_true_ov.get("stories") or 1.0),
                "height_m": float(old_rec.get("height_m") or base_true_ov.get("height_m") or 4.0),
                "use_category": base_true_ov.get("use_category")
                or old_rec.get("use_category")
                or "Civic / Institutional",
                "historic_district": hd_ov,
                "contributing": contrib_ov,
                "is_building_override": True,
                "source_type": base_true_ov.get("source_type")
                or old_rec.get("source_type")
                or "HCAD CAMA & Parcel Geometry Verification",
                "geometry": geom_dict,
            }
            overrides_map[new_key] = rekeyed_entry
            rekeyed_keys.append((aux_key, new_key))
            continue

        cama = cama_by_hcad.get(true_hcad, {})
        bgeo_p = bgeo_by_hcad.get(true_hcad, {})

        yr = (
            int(bgeo_p.get("year_built") or 0)
            or int(cama.get("bldg_year") or 0)
            or int(cama.get("yr_impr") or 0)
            or normalize_year(parcel_yrs[p_idx])
        )
        year_source = ""
        addr = (
            str(cama.get("site_addr_1") or "").strip()
            or str(parcel_addrs[p_idx] or "").strip()
            or str(bgeo_p.get("address") or "").strip()
        )
        old_yr = int(old_rec.get("year_built") or 0)
        if yr < 1836 and old_yr >= 1836:
            # Contiguous institutional campus strip inheriting verified adjacent building year
            yr = old_yr
            year_source = "campus_contiguity"
            if not addr or addr.startswith("0 "):
                addr = str(old_rec.get("address") or addr).strip()

        bld_area = int(cama.get("bld_ar") or bgeo_p.get("bld_area") or 0)
        land_area = int(cama.get("land_ar") or float(parcel_areas[p_idx] or 0.0))
        state_cls = str(cama.get("state_class") or "").strip()
        use_cat = (
            str(bgeo_p.get("use_category") or "").strip()
            or classify_use_category(state_cls, "", bld_area)
        )
        eff_fp_sqft = fp_sqft if fp_sqft >= 150.0 else float(max(bld_area, 900))
        stories, height_m = estimate_stories_and_height(
            bld_area=float(bld_area),
            footprint_area_sqft=eff_fp_sqft,
            use_category=use_cat,
        )

        props: dict[str, Any] = {
            "id": true_hcad,
            "hcad_num": true_hcad,
            "year_built": yr,
            "decade": compute_decade(yr) if yr >= 1836 else 0,
            "use_category": use_cat,
            "stories": stories,
            "height_m": height_m,
        }
        if addr:
            props["address"] = addr
        if bld_area > 0:
            props["bld_area"] = bld_area
        if land_area > 0:
            props["land_area"] = land_area
        if year_source:
            props["year_source"] = year_source
        rem_yr = int(cama.get("remodel_year") or bgeo_p.get("remodel_year") or 0)
        if rem_yr > 1836:
            props["remodel_year"] = rem_yr
        for hist_k in ("historic_district", "contributing", "landmark_name"):
            if bgeo_p.get(hist_k):
                props[hist_k] = bgeo_p[hist_k]

        rp = shapely.point_on_surface(poly_4326)
        quad = get_quadrant(float(rp.x), float(rp.y))
        pending_by_quad.setdefault(quad, []).append((aux_key, base_hcad, true_hcad, geom_dict, props))

    return purged_keys, rekeyed_keys, pending_by_quad


def reconcile_quadrant_features(
    features: list[dict[str, Any]],
    items: list[tuple[str, str, str, dict[str, Any], dict[str, Any]]],
    quad: str = "",
) -> tuple[int, int]:
    """Mutate a quadrant's GeoJSON features list to re-tag or restore swallowed neighbor footprints."""
    retagged = 0
    restored = 0
    for _aux_key, base_hcad, true_hcad, geom_dict, true_props in items:
        target_poly = shape(geom_dict)
        minx, miny, maxx, maxy = target_poly.bounds
        matched_existing = False
        already_present_on_true = False

        for feat in features:
            fg = feat.get("geometry")
            if not fg:
                continue
            ring = fg["coordinates"][0] if fg.get("type") == "Polygon" else fg["coordinates"][0][0]
            xs = [pt[0] for pt in ring]
            ys = [pt[1] for pt in ring]
            if max(xs) < minx or min(xs) > maxx or max(ys) < miny or min(ys) > maxy:
                continue
            fpoly = shape(fg)
            inter = target_poly.intersection(fpoly).area / max(min(target_poly.area, fpoly.area), 1e-12)
            if inter >= 0.35:
                fh = str((feat.get("properties") or {}).get("hcad_num") or "").strip()
                if fh == true_hcad:
                    already_present_on_true = True
                    break
                if fh == base_hcad and inter >= 0.50:
                    feat["properties"] = dict(true_props)
                    retagged += 1
                    matched_existing = True
                    if quad:
                        print(
                            f"   [{quad}] Re-tagged shard footprint {base_hcad} -> {true_hcad} "
                            f"({true_props.get('address')}, {true_props.get('year_built')})"
                        )
                    break

        if not already_present_on_true and not matched_existing:
            features.append(
                {
                    "type": "Feature",
                    "geometry": geom_dict,
                    "properties": dict(true_props),
                }
            )
            restored += 1
            if quad:
                print(
                    f"   [{quad}] Restored swallowed footprint for {true_hcad} "
                    f"({true_props.get('address')}, {true_props.get('year_built')})"
                )
    return retagged, restored


def _load_cama_for_accounts(
    target_hcads: set[str],
    cache_dir: Path,
) -> dict[str, dict[str, Any]]:
    """Load HCAD real_acct.txt and building_res/other metadata for a target set of accounts."""
    info: dict[str, dict[str, Any]] = {h: {} for h in target_hcads}
    acct_zip = cache_dir / "Real_acct_owner.zip"
    if acct_zip.exists():
        with zipfile.ZipFile(acct_zip) as zf:
            with zf.open("real_acct.txt") as rf:
                tf = io.TextIOWrapper(rf, encoding="latin-1")
                hdr = [c.strip() for c in tf.readline().rstrip("\r\n").split("\t")]
                idx_acct = hdr.index("acct")
                for line in tf:
                    parts = line.rstrip("\r\n").split("\t")
                    if len(parts) <= idx_acct:
                        continue
                    acct = parts[idx_acct].strip()
                    if acct in target_hcads:
                        row = dict(zip(hdr, [x.strip() for x in parts]))
                        info[acct]["site_addr_1"] = row.get("site_addr_1", "")
                        info[acct]["yr_impr"] = normalize_year(row.get("yr_impr", ""))
                        info[acct]["bld_ar"] = int(float(row.get("bld_ar") or 0))
                        info[acct]["land_ar"] = int(float(row.get("land_ar") or 0))
                        info[acct]["state_class"] = row.get("state_class", "")

    bldg_zip = cache_dir / "Real_building_land.zip"
    if bldg_zip.exists():
        with zipfile.ZipFile(bldg_zip) as zf:
            for member in ("building_res.txt", "building_other.txt"):
                if member not in zf.namelist():
                    continue
                with zf.open(member) as rf:
                    tf = io.TextIOWrapper(rf, encoding="latin-1")
                    hdr = [c.strip() for c in tf.readline().rstrip("\r\n").split("\t")]
                    idx_acct = hdr.index("acct")
                    for line in tf:
                        parts = line.rstrip("\r\n").split("\t")
                        if len(parts) <= idx_acct:
                            continue
                        acct = parts[idx_acct].strip()
                        if acct not in target_hcads:
                            continue
                        row = dict(zip(hdr, [x.strip() for x in parts]))
                        yr = normalize_year(row.get("date_erected") or row.get("yr_remodel") or "")
                        prev_yr = int(info[acct].get("bldg_year") or 0)
                        if yr >= 1836 and (prev_yr == 0 or yr < prev_yr):
                            info[acct]["bldg_year"] = yr
                        rem = normalize_year(row.get("yr_remodel") or "")
                        if rem >= 1836:
                            info[acct]["remodel_year"] = max(rem, int(info[acct].get("remodel_year") or 0))
                        style = (row.get("structure_dscr") or row.get("dscr") or "").strip()
                        if style and not info[acct].get("style"):
                            info[acct]["style"] = style
    return info


def _find_tippecanoe() -> str:
    for candidate in (
        shutil.which("tippecanoe"),
        "/tmp/tippecanoe/tippecanoe",
        str(Path.home() / ".local" / "bin" / "tippecanoe"),
    ):
        if candidate and Path(candidate).exists():
            return str(candidate)
    raise FileNotFoundError("tippecanoe executable not found on PATH or ~/.local/bin/tippecanoe")


def run_repair(
    cache_dir: Path = CACHE_DIR,
    data_dir: Path = DATA_DIR,
    aligned_dir: Path = ALIGNED_DIR,
    cache_bust: str | None = None,
    recompile_pmtiles: bool = True,
) -> dict[str, Any]:
    t0 = time.time()
    gdb_path = cache_dir / "Parcels" / "Parcels.gdb"
    if not gdb_path.exists():
        raise FileNotFoundError(f"Missing Parcels.gdb at {gdb_path}")

    print("-> [1/4] Loading Parcels.gdb and building EPSG:2278 STRtree...")
    _, _, geoms_wkb, fields = pyogrio.raw.read(
        str(gdb_path),
        layer="Parcels",
        columns=["HCAD_NUM_1", "CurrOwner", "LocAddr", "yr_impr", "Shape_Area"],
    )
    parcel_hcads = np.asarray(fields[0], dtype=object)
    parcel_addrs = np.asarray(fields[2], dtype=object)
    parcel_yrs = np.asarray(fields[3], dtype=object)
    parcel_areas = np.asarray(fields[4], dtype=object)

    parcel_geoms = shapely.force_2d(shapely.from_wkb(geoms_wkb))
    del geoms_wkb
    parcel_tree = shapely.STRtree(parcel_geoms)
    tf_to_2278 = Transformer.from_crs("EPSG:4326", "EPSG:2278", always_xy=True)

    overrides_path = data_dir / "curated_overrides.json"
    overrides_doc = json.loads(overrides_path.read_text(encoding="utf-8"))
    overrides_map: dict[str, dict[str, Any]] = overrides_doc.get("overrides", overrides_doc)

    mismatches = identify_cross_parcel_aux_overrides(
        overrides_map, parcel_tree, parcel_hcads, tf_to_2278
    )
    print(f"   Found {len(mismatches)} cross-parcel #aux_ entries in curated_overrides.json.")
    if not mismatches:
        return {"purged": 0, "rekeyed": 0, "restored_to_shards": 0, "retagged_in_shards": 0}

    target_hcads = {true_hcad for _, _, true_hcad, _ in mismatches}
    cama_by_hcad = _load_cama_for_accounts(target_hcads, cache_dir)

    bgeo_path = data_dir / "buildings.geojson"
    bgeo_by_hcad: dict[str, dict[str, Any]] = {}
    if bgeo_path.exists():
        bgeo_doc = orjson.loads(bgeo_path.read_bytes())
        for feat in bgeo_doc.get("features", []):
            p = feat.get("properties") or {}
            h = str(p.get("hcad_num") or "").strip()
            if h in target_hcads and h not in bgeo_by_hcad:
                bgeo_by_hcad[h] = p

    print("-> [2/4] Purging cross-parcel #aux_ overrides and preparing shard restorations...")
    purged_keys, rekeyed_keys, pending_by_quad = reconcile_cross_parcel_overrides(
        overrides_map=overrides_map,
        mismatches=mismatches,
        parcel_addrs=parcel_addrs,
        parcel_yrs=parcel_yrs,
        parcel_areas=parcel_areas,
        cama_by_hcad=cama_by_hcad,
        bgeo_by_hcad=bgeo_by_hcad,
        tf_to_2278=tf_to_2278,
    )

    # Write cleaned curated_overrides.json in compact production format
    overrides_path.write_text(json.dumps(overrides_doc, separators=(",", ":")), encoding="utf-8")
    print(
        f"   Updated curated_overrides.json: purged {len(purged_keys)} cross-parcel #aux_ entries "
        f"(re-keyed {len(rekeyed_keys)} to true overridden parcel)."
    )

    # Verify 0 cross-parcel #aux_ entries remain
    remaining = identify_cross_parcel_aux_overrides(
        overrides_map, parcel_tree, parcel_hcads, tf_to_2278
    )
    if remaining:
        raise RuntimeError(f"Expected 0 remaining cross-parcel #aux_ entries, found: {remaining}")

    print("-> [3/4] Updating affected .geojsonseq shards and deduplicating overlaps...")
    retagged_total = 0
    restored_total = 0
    modified_quads: list[str] = []

    for quad, items in sorted(pending_by_quad.items()):
        seq_path = aligned_dir / f"buildings_{quad}.geojsonseq"
        if not seq_path.exists():
            raise FileNotFoundError(f"Missing shard file: {seq_path}")

        raw_lines = seq_path.read_bytes().splitlines()
        features: list[dict[str, Any]] = []
        for line in raw_lines:
            if line.startswith(b"\x1e"):
                line = line[1:]
            if line:
                features.append(orjson.loads(line))

        retagged_q, restored_q = reconcile_quadrant_features(features, items, quad=quad)
        retagged_total += retagged_q
        restored_total += restored_q

        if retagged_q > 0 or restored_q > 0:
            tmp_path = seq_path.with_suffix(".geojsonseq.tmp")
            with open(tmp_path, "wb") as out_f:
                for feat in features:
                    out_f.write(b"\x1e" + orjson.dumps(feat) + b"\n")
            tmp_path.replace(seq_path)
            s_name, before_cnt, after_cnt = deduplicate_geojsonseq_shard(seq_path)
            print(f"   Deduplicated {s_name}: {before_cnt:,} -> {after_cnt:,} footprints.")
            modified_quads.append(quad)

    if recompile_pmtiles and modified_quads:
        print(f"-> [4/4] Recompiling {len(modified_quads)} modified PMTiles shards via Tippecanoe...")
        tippecanoe_bin = _find_tippecanoe()
        procs: list[tuple[str, subprocess.Popen[bytes], Path]] = []
        for quad in modified_quads:
            in_seq = aligned_dir / f"buildings_{quad}.geojsonseq"
            out_pmtiles = data_dir / f"houston_buildings_{quad}.pmtiles"
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
            procs.append((quad, subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE), out_pmtiles))

        for quad, proc, out_pmtiles in procs:
            _, stderr = proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(
                    f"Tippecanoe failed for quadrant {quad}: {stderr.decode('utf-8', errors='replace')}"
                )
            sz_mb = out_pmtiles.stat().st_size / (1024 * 1024)
            print(f"   Compiled {out_pmtiles.name}: {sz_mb:.2f} MB")

    total_buildings = 0
    for quad in ("nw_w", "nw_e", "ne", "sw", "se"):
        sp = aligned_dir / f"buildings_{quad}.geojsonseq"
        if sp.exists():
            with open(sp, "rb") as f:
                total_buildings += sum(1 for line in f if line.strip())

    manifest_path = data_dir / "pmtiles_manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["total_buildings"] = total_buildings
        if cache_bust:
            manifest["cache_bust"] = cache_bust
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(
            f"   Updated pmtiles_manifest.json: total_buildings={total_buildings:,}, "
            f"cache_bust={manifest.get('cache_bust')}"
        )

    print(
        f"=== Completed Cross-Parcel #aux_ Repair in {time.time() - t0:.1f}s "
        f"(purged={len(purged_keys)}, rekeyed={len(rekeyed_keys)}, "
        f"retagged_in_shards={retagged_total}, restored_to_shards={restored_total}) ==="
    )
    return {
        "purged": len(purged_keys),
        "rekeyed": len(rekeyed_keys),
        "retagged_in_shards": retagged_total,
        "restored_to_shards": restored_total,
        "modified_quads": modified_quads,
        "total_buildings": total_buildings,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit and repair cross-parcel #aux_ footprint overrides.")
    parser.add_argument(
        "--cache-bust",
        default=None,
        help="Optional ?v= cache-buster stamp to write into pmtiles_manifest.json (defaults to preserving existing stamp).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    os.environ.setdefault("PYTHONPATH", str(REPO_ROOT / "pipeline"))
    cli_args = _parse_args()
    run_repair(cache_bust=cli_args.cache_bust)
