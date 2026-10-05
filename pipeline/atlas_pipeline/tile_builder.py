"""PMTiles v3 MVT compiler, GeoJSON bundle writer, search index, and stats summary generator."""

from __future__ import annotations

import gzip
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

import mapbox_vector_tile
from pmtiles.tile import Compression, TileType, zxy_to_tileid
from pmtiles.writer import write as pmtiles_write
from shapely.geometry import box, shape
from shapely.strtree import STRtree


def _lonlat_to_tile(lon: float, lat: float, z: int) -> tuple[int, int]:
    lat_rad = math.radians(max(-85.0511, min(85.0511, lat)))
    n = 1 << z
    xtile = int((lon + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return max(0, min(n - 1, xtile)), max(0, min(n - 1, ytile))


def _tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    n = 1 << z
    lon_min = x / n * 360.0 - 180.0
    lon_max = (x + 1) / n * 360.0 - 180.0
    lat_rad_max = math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / n)))
    lat_rad_min = math.atan(math.sinh(math.pi * (1.0 - 2.0 * (y + 1) / n)))
    return lon_min, math.degrees(lat_rad_min), lon_max, math.degrees(lat_rad_max)


def build_search_index(
    buildings: list[dict[str, Any]],
    overlays: dict[str, list[dict[str, Any]]],
    max_entries: int = 8000,
) -> list[dict[str, Any]]:
    """Build a lightweight client-side autocomplete search index for landmarks, districts, and properties."""
    items: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    # 1. Historic Districts & Heritage Districts
    for dist_key, category_label in (
        ("historic_districts", "Historic District"),
        ("heritage_districts", "Heritage District"),
        ("nrhp_districts", "National Register District"),
    ):
        for feat in overlays.get(dist_key, []):
            props = feat.get("properties") or {}
            name = str(props.get("name") or props.get("NAME") or "").strip()
            if not name or name.lower() in seen_keys:
                continue
            geom = feat.get("geometry")
            if not geom:
                continue
            pt = shape(geom).representative_point()
            seen_keys.add(name.lower())
            items.append({
                "type": "district",
                "category": category_label,
                "label": name,
                "sublabel": category_label,
                "lon": round(pt.x, 6),
                "lat": round(pt.y, 6),
                "zoom": 15.2,
                "hcad_num": "",
                "year_built": 0,
            })

    # 2. Landmarks (LM & PLM)
    for feat in overlays.get("landmarks", []):
        props = feat.get("properties") or {}
        name = str(props.get("name") or "").strip()
        addr = str(props.get("address") or "").strip()
        desig = str(props.get("designation") or "Landmark").strip()
        geom = feat.get("geometry")
        if not geom:
            continue
        pt = shape(geom).representative_point()
        label = f"{name} ({addr})" if name and addr else (name or addr)
        if not label or label.lower() in seen_keys:
            continue
        seen_keys.add(label.lower())
        items.append({
            "type": "landmark",
            "category": desig,
            "label": label,
            "sublabel": f"{desig} • Built {props.get('year_built') or 'Unknown'}",
            "lon": round(pt.x, 6),
            "lat": round(pt.y, 6),
            "zoom": 17.5,
            "hcad_num": str(props.get("hcad_num") or ""),
            "year_built": int(props.get("year_built") or 0),
        })

    # 3. Buildings / Properties (prioritize landmarks, historic district properties, and dated structures)
    sorted_buildings = sorted(
        buildings,
        key=lambda b: (
            0 if b["properties"].get("landmark_name") else 1,
            0 if b["properties"].get("contributing") == "Contributing" else 1,
            b["properties"].get("year_built") or 9999,
        ),
    )

    for bld in sorted_buildings:
        if len(items) >= max_entries:
            break
        props = bld.get("properties") or {}
        addr = str(props.get("address") or "").strip()
        hcad = str(props.get("hcad_num") or "").strip()
        lm_name = str(props.get("landmark_name") or "").strip()
        if not addr and not lm_name:
            continue
        dedupe_key = f"{addr}|{hcad}".lower()
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)

        pt = shape(bld["geometry"]).representative_point()
        yr = int(props.get("year_built") or 0)
        dist = str(props.get("historic_district") or "").strip()
        sub_parts = []
        if yr > 0:
            sub_parts.append(f"Built {yr}")
        if dist:
            sub_parts.append(dist)
        if hcad:
            sub_parts.append(f"HCAD #{hcad}")

        items.append({
            "type": "building",
            "category": props.get("contributing") if dist else props.get("use_category", "Property"),
            "label": f"{lm_name} — {addr}" if lm_name and addr else (addr or lm_name),
            "sublabel": " • ".join(sub_parts),
            "lon": round(pt.x, 6),
            "lat": round(pt.y, 6),
            "zoom": 17.5,
            "hcad_num": hcad,
            "year_built": yr,
            "id": str(props.get("id") or ""),
        })

    return items


def build_stats_summary(
    buildings: list[dict[str, Any]],
    overlays: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Compute aggregate statistics by decade, contributing status, and land use for the Atlas UI."""
    decade_counter: Counter[str] = Counter()
    contrib_counter: Counter[str] = Counter()
    use_counter: Counter[str] = Counter()
    district_counter: Counter[str] = Counter()

    dated_count = 0
    observed_fp_count = 0
    earliest_year = 9999
    latest_year = 0

    for bld in buildings:
        props = bld.get("properties") or {}
        yr = int(props.get("year_built") or 0)
        dec = int(props.get("decade") or 0)
        if yr > 0 and dec >= 1830:
            dated_count += 1
            decade_counter[str(dec)] += 1
            earliest_year = min(earliest_year, yr)
            latest_year = max(latest_year, yr)
        else:
            decade_counter["unknown"] += 1

        contrib = str(props.get("contributing") or "Outside Historic District")
        contrib_counter[contrib] += 1

        use_cat = str(props.get("use_category") or "Residential")
        use_counter[use_cat] += 1

        dist = str(props.get("historic_district") or "").strip()
        if dist:
            district_counter[dist] += 1

        if props.get("footprint_source") == "observed":
            observed_fp_count += 1

    ordered_decades = {
        str(d): decade_counter.get(str(d), 0)
        for d in range(1830, 2030, 10)
    }
    ordered_decades["unknown"] = decade_counter.get("unknown", 0)

    return {
        "total_buildings": len(buildings),
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
    }


def compile_pmtiles_archive(
    output_pmtiles: Path,
    buildings: list[dict[str, Any]],
    parcels: list[dict[str, Any]],
    min_zoom: int = 12,
    max_zoom: int = 15,
) -> int:
    """Compile buildings and parcels into a spec-compliant PMTiles v3 MVT vector tile archive."""
    bld_geoms = [shape(f["geometry"]) for f in buildings if f.get("geometry")]
    pcl_geoms = [shape(f["geometry"]) for f in parcels if f.get("geometry")]

    if not bld_geoms and not pcl_geoms:
        return 0

    all_geoms = bld_geoms + pcl_geoms
    minx = min(g.bounds[0] for g in all_geoms)
    miny = min(g.bounds[1] for g in all_geoms)
    maxx = max(g.bounds[2] for g in all_geoms)
    maxy = max(g.bounds[3] for g in all_geoms)

    bld_tree = STRtree(bld_geoms) if bld_geoms else None
    pcl_tree = STRtree(pcl_geoms) if pcl_geoms else None

    tiles_by_id: list[tuple[int, bytes]] = []

    for z in range(min_zoom, max_zoom + 1):
        tx_min, ty_max = _lonlat_to_tile(minx, miny, z)
        tx_max, ty_min = _lonlat_to_tile(maxx, maxy, z)

        for tx in range(tx_min, tx_max + 1):
            for ty in range(ty_min, ty_max + 1):
                tb_minx, tb_miny, tb_maxx, tb_maxy = _tile_bounds(z, tx, ty)
                tile_box = box(tb_minx, tb_miny, tb_maxx, tb_maxy)

                layers_payload = []

                if bld_tree is not None:
                    b_idxs = bld_tree.query(tile_box, predicate="intersects")
                    if len(b_idxs) > 0:
                        b_feats = []
                        for i in b_idxs:
                            idx = int(i)
                            clipped = bld_geoms[idx].intersection(tile_box)
                            if not clipped.is_empty:
                                b_feats.append({
                                    "geometry": clipped,
                                    "properties": buildings[idx]["properties"],
                                })
                        if b_feats:
                            layers_payload.append({
                                "name": "buildings",
                                "features": b_feats,
                            })

                if pcl_tree is not None and z >= 13:
                    p_idxs = pcl_tree.query(tile_box, predicate="intersects")
                    if len(p_idxs) > 0:
                        p_feats = []
                        for i in p_idxs:
                            idx = int(i)
                            clipped = pcl_geoms[idx].intersection(tile_box)
                            if not clipped.is_empty:
                                p_feats.append({
                                    "geometry": clipped,
                                    "properties": parcels[idx]["properties"],
                                })
                        if p_feats:
                            layers_payload.append({
                                "name": "parcels",
                                "features": p_feats,
                            })

                if not layers_payload:
                    continue

                mvt_bytes = mapbox_vector_tile.encode(
                    layers_payload,
                    default_options={
                        "quantize_bounds": (tb_minx, tb_miny, tb_maxx, tb_maxy),
                        "extents": 4096,
                    },
                )
                compressed_tile = gzip.compress(mvt_bytes)
                tile_id = zxy_to_tileid(z, tx, ty)
                tiles_by_id.append((tile_id, compressed_tile))

    tiles_by_id.sort(key=lambda item: item[0])

    with pmtiles_write(str(output_pmtiles)) as writer:
        for tile_id, raw_bytes in tiles_by_id:
            writer.write_tile(tile_id, raw_bytes)

        header = {
            "tile_type": TileType.MVT,
            "tile_compression": Compression.GZIP,
            "min_zoom": min_zoom,
            "max_zoom": max_zoom,
            "min_lon_e7": int(minx * 1e7),
            "min_lat_e7": int(miny * 1e7),
            "max_lon_e7": int(maxx * 1e7),
            "max_lat_e7": int(maxy * 1e7),
            "center_zoom": min(14, max_zoom),
            "center_lon_e7": int(((minx + maxx) / 2.0) * 1e7),
            "center_lat_e7": int(((miny + maxy) / 2.0) * 1e7),
        }
        metadata = {
            "name": "The Houston Building Atlas v2",
            "description": "Preservation Houston Building Footprints & Tax Parcels Vector Tileset",
            "attribution": "Preservation Houston • HCAD • City of Houston Historic Preservation • OpenStreetMap",
            "vector_layers": [
                {"id": "buildings", "description": "Building footprints with HCAD & Historic Preservation attributes"},
                {"id": "parcels", "description": "Tax parcel boundaries with HCAD & Historic Preservation attributes"},
            ],
        }
        writer.finalize(header, metadata)

    return output_pmtiles.stat().st_size


def write_atlas_bundle(
    output_dir: Path,
    buildings: list[dict[str, Any]],
    parcels: list[dict[str, Any]],
    overlays: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Write the complete static data bundle (.pmtiles, .geojson, search_index.json, stats_summary.json)."""
    output_dir.mkdir(parents=True, exist_ok=True)

    pmtiles_path = output_dir / "houston_atlas.pmtiles"
    pmtiles_size = compile_pmtiles_archive(pmtiles_path, buildings, parcels, min_zoom=12, max_zoom=14)

    buildings_fc = {"type": "FeatureCollection", "features": buildings}
    (output_dir / "buildings.geojson").write_text(json.dumps(buildings_fc, separators=(",", ":")))

    parcels_fc = {"type": "FeatureCollection", "features": parcels}
    (output_dir / "parcels.geojson").write_text(json.dumps(parcels_fc, separators=(",", ":")))

    overlays_fc = {
        k: {"type": "FeatureCollection", "features": v}
        for k, v in overlays.items()
    }
    (output_dir / "overlays.json").write_text(json.dumps(overlays_fc, separators=(",", ":")))

    search_index = build_search_index(buildings, overlays)
    (output_dir / "search_index.json").write_text(json.dumps(search_index, separators=(",", ":")))

    stats_summary = build_stats_summary(buildings, overlays)
    stats_summary["pmtiles_size_bytes"] = pmtiles_size
    (output_dir / "stats_summary.json").write_text(json.dumps(stats_summary, indent=2))

    return stats_summary
