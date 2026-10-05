"""Spatial join of building footprints to HCAD/COH tax parcels with inset footprint synthesis."""

from __future__ import annotations

import copy
import math
from typing import Any

from shapely.affinity import scale
from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree
from shapely.validation import make_valid

from atlas_pipeline.schema import estimate_stories_and_height

SQFT_PER_DEG2_AT_HOUSTON = (111_139.0 * 3.28084) * (96_486.0 * 3.28084)


def _ensure_valid_polygon(geom: BaseGeometry) -> BaseGeometry | None:
    if geom is None or geom.is_empty:
        return None
    if not geom.is_valid:
        geom = make_valid(geom)
    if geom.geom_type == "GeometryCollection":
        polys = [g for g in geom.geoms if g.geom_type in ("Polygon", "MultiPolygon") and not g.is_empty]
        if not polys:
            return None
        geom = max(polys, key=lambda g: g.area)
    if geom.geom_type in ("Polygon", "MultiPolygon") and not geom.is_empty:
        return geom
    return None


def derive_building_footprint_from_parcel(
    parcel_geom: BaseGeometry,
    bld_area_sqft: float,
    land_area_sqft: float,
) -> BaseGeometry | None:
    """Derive a clean oriented architectural building footprint inside a parcel when no external footprint exists."""
    valid_parcel = _ensure_valid_polygon(parcel_geom)
    if valid_parcel is None:
        return None

    target_poly: Polygon
    if isinstance(valid_parcel, MultiPolygon):
        target_poly = max(valid_parcel.geoms, key=lambda g: g.area)
    elif isinstance(valid_parcel, Polygon):
        target_poly = valid_parcel
    else:
        return None

    if target_poly.area <= 0:
        return None

    # Estimate lot coverage fraction (clamped between 0.22 and 0.68 of lot area)
    if bld_area_sqft > 0 and land_area_sqft > 0:
        stories_est = 2.0 if bld_area_sqft > 2200 and land_area_sqft < 8000 else 1.0
        ground_area_ratio = (bld_area_sqft / stories_est) / max(land_area_sqft, bld_area_sqft)
        coverage = max(0.24, min(0.65, ground_area_ratio))
    else:
        coverage = 0.38

    linear_scale = math.sqrt(coverage)

    try:
        mrr = target_poly.minimum_rotated_rectangle
        if isinstance(mrr, Polygon) and mrr.area > 0:
            scaled_rect = scale(mrr, xfact=linear_scale * 0.86, yfact=linear_scale * 0.92, origin="centroid")
            clipped = scaled_rect.intersection(target_poly.buffer(-0.000015))
            clipped_valid = _ensure_valid_polygon(clipped)
            if clipped_valid is not None and clipped_valid.area > target_poly.area * 0.08:
                return clipped_valid
    except Exception:
        pass

    scaled = scale(target_poly, xfact=linear_scale, yfact=linear_scale, origin="centroid")
    return _ensure_valid_polygon(scaled.intersection(target_poly))


def join_footprints_to_parcels(
    parcel_features: list[dict[str, Any]],
    footprint_features: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Spatially join observed building footprints to parcels and synthesize footprints for built parcels missing one."""
    parcel_geoms: list[BaseGeometry] = []
    valid_parcels: list[dict[str, Any]] = []

    for pf in parcel_features:
        raw_geom = pf.get("geometry")
        if not raw_geom:
            continue
        geom = _ensure_valid_polygon(shape(raw_geom))
        if geom is None:
            continue
        parcel_geoms.append(geom)
        valid_parcels.append({
            "type": "Feature",
            "geometry": mapping(geom),
            "properties": copy.deepcopy(pf.get("properties", {})),
        })

    if not valid_parcels:
        return [], []

    tree = STRtree(parcel_geoms)
    matched_parcel_indices: set[int] = set()
    joined_buildings: list[dict[str, Any]] = []
    bld_counter = 1

    for fp in footprint_features:
        raw_geom = fp.get("geometry")
        if not raw_geom:
            continue
        fp_geom = _ensure_valid_polygon(shape(raw_geom))
        if fp_geom is None or fp_geom.area <= 0:
            continue

        rep_pt = fp_geom.representative_point()
        candidate_idxs = tree.query(fp_geom)
        best_idx: int | None = None
        best_overlap = 0.0

        for idx in candidate_idxs:
            p_geom = parcel_geoms[int(idx)]
            if p_geom.contains(rep_pt):
                best_idx = int(idx)
                break
            if p_geom.intersects(fp_geom):
                overlap = p_geom.intersection(fp_geom).area
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_idx = int(idx)

        if best_idx is None:
            continue

        fp_props = fp.get("properties") or {}
        if not fp_props.get("osm_id") and best_idx in matched_parcel_indices:
            continue

        matched_parcel_indices.add(best_idx)
        parent_props = copy.deepcopy(valid_parcels[best_idx]["properties"])

        fp_area_sqft = fp_geom.area * SQFT_PER_DEG2_AT_HOUSTON
        stories, height_m = estimate_stories_and_height(
            bld_area=float(parent_props.get("bld_area") or 0.0),
            footprint_area_sqft=fp_area_sqft,
            use_category=str(parent_props.get("use_category") or "Residential"),
            osm_levels=fp_props.get("building:levels") or fp_props.get("levels"),
            osm_height=fp_props.get("height"),
        )

        parent_props["id"] = f"bld_{bld_counter:06d}"
        parent_props["stories"] = stories
        parent_props["height_m"] = height_m
        parent_props["footprint_source"] = "observed"

        joined_buildings.append({
            "type": "Feature",
            "geometry": mapping(fp_geom),
            "properties": parent_props,
        })
        bld_counter += 1

    # For parcels with a structure (or historic designation) that didn't have an external footprint, synthesize one
    for idx, pf in enumerate(valid_parcels):
        if idx in matched_parcel_indices:
            continue
        props = pf["properties"]
        year_built = int(props.get("year_built") or 0)
        bld_area = float(props.get("bld_area") or 0.0)
        land_area = float(props.get("land_area") or 0.0)
        use_cat = str(props.get("use_category") or "")
        is_historic = props.get("contributing") in ("Contributing", "Non-Contributing") or bool(props.get("landmark_name"))

        if year_built == 0 and bld_area <= 0 and not is_historic and use_cat == "Vacant / Exempt":
            continue

        derived_geom = derive_building_footprint_from_parcel(
            parcel_geoms[idx],
            bld_area_sqft=bld_area,
            land_area_sqft=land_area,
        )
        if derived_geom is None:
            continue

        b_props = copy.deepcopy(props)
        b_props["id"] = f"bld_{bld_counter:06d}"
        b_props["footprint_source"] = "derived_parcel"
        joined_buildings.append({
            "type": "Feature",
            "geometry": mapping(derived_geom),
            "properties": b_props,
        })
        bld_counter += 1

    return joined_buildings, valid_parcels
