"""Normalized feature schema and HCAD/COH attribute mapping for Houston Building Atlas v2."""

from __future__ import annotations

import math
from typing import Any

MIN_VALID_YEAR = 1836
MAX_VALID_YEAR = 2027


def normalize_year(raw_val: Any) -> int:
    """Parse a raw year-built value into an integer year in [1836, 2027], or 0 if invalid/unknown."""
    if raw_val is None:
        return 0
    if isinstance(raw_val, (int, float)):
        if math.isnan(raw_val):
            return 0
        yr = int(round(raw_val))
    else:
        s = str(raw_val).strip()
        if not s:
            return 0
        try:
            yr = int(round(float(s)))
        except ValueError:
            return 0

    if MIN_VALID_YEAR <= yr <= MAX_VALID_YEAR:
        return yr
    return 0


def compute_decade(year: int) -> int:
    """Return the decade floor (e.g. 1910 for 1914) or 0 if year <= 0."""
    if year < MIN_VALID_YEAR:
        return 0
    return (year // 10) * 10


def classify_use_category(state_class: str, landuse_desc: str, group_desc: str = "") -> str:
    """Map HCAD state class and COH land-use description into a high-level use category."""
    sc = (state_class or "").strip().upper()
    lu = f"{landuse_desc or ''} {group_desc or ''}".strip().lower()

    if sc.startswith("B") or "multi-family" in lu or "duplex" in lu or "two-family" in lu or "apartment" in lu or "condo" in lu:
        return "Multi-Family"
    if sc.startswith("A") or "single-family" in lu or "residential" in lu or "townhouse" in lu:
        return "Residential"
    if sc.startswith("X") or "exempt" in lu or "public" in lu or "institutional" in lu or "church" in lu or "school" in lu or "religious" in lu or "civic" in lu:
        return "Civic / Institutional"
    if sc.startswith("F2") or "industrial" in lu or "warehouse" in lu or "manufacturing" in lu:
        return "Industrial"
    if sc.startswith("F1") or "commercial" in lu or "office" in lu or "retail" in lu:
        return "Commercial"
    if sc.startswith("C") or sc.startswith("D") or "vacant" in lu or "undeveloped" in lu or "agricultural" in lu:
        return "Vacant / Exempt"
    return "Residential"


def estimate_stories_and_height(
    bld_area: float,
    footprint_area_sqft: float,
    use_category: str,
    osm_levels: Any = None,
    osm_height: Any = None,
) -> tuple[float, float]:
    """Estimate building stories and 3D extrusion height in meters."""
    stories = 0.0
    height_m = 0.0

    if osm_levels is not None:
        try:
            val = float(str(osm_levels).strip().split(";")[0])
            if 1.0 <= val <= 100.0:
                stories = val
        except ValueError:
            pass

    if osm_height is not None:
        try:
            raw_h = str(osm_height).strip().lower().replace("m", "").strip()
            val_h = float(raw_h)
            if 2.5 <= val_h <= 350.0:
                height_m = val_h
        except ValueError:
            pass

    if stories <= 0.0:
        if bld_area > 0 and footprint_area_sqft > 150:
            ratio = bld_area / footprint_area_sqft
            stories = max(1.0, min(round(ratio * 2) / 2, 60.0 if use_category in ("Commercial", "Multi-Family", "Civic / Institutional") else 3.5))
        else:
            if use_category == "Commercial":
                stories = 2.0
            elif use_category == "Multi-Family":
                stories = 2.5
            elif use_category == "Civic / Institutional":
                stories = 2.0
            elif use_category == "Industrial":
                stories = 1.5
            elif use_category == "Vacant / Exempt":
                stories = 1.0
            else:
                stories = 1.5 if bld_area > 2200 else 1.0

    if height_m <= 0.0:
        floor_h = 3.8 if use_category in ("Commercial", "Civic / Institutional", "Industrial") else 3.5
        height_m = round(stories * floor_h, 1)

    return round(stories, 1), round(height_m, 1)


def _clean_str(val: Any) -> str:
    if val is None:
        return ""
    s = str(val).strip()
    if s.lower() in ("null", "none", "nan", "<null>"):
        return ""
    return " ".join(s.split())


def _clean_float(val: Any) -> float:
    if val is None:
        return 0.0
    try:
        v = float(val)
        return 0.0 if math.isnan(v) or v < 0 else round(v, 1)
    except (ValueError, TypeError):
        return 0.0


def normalize_parcel_record(
    raw_props: dict[str, Any],
    historic_override: dict[str, Any] | None = None,
    landmark_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize a parcel attribute dict from COH Cadastral / HCAD + Historic layers into the 20-field schema."""
    hist = historic_override or {}
    lm = landmark_override or {}

    hcad_num = (
        _clean_str(raw_props.get("TAX_ID"))
        or _clean_str(raw_props.get("HCAD_NUM"))
        or _clean_str(hist.get("HCAD_NUM"))
        or _clean_str(lm.get("USER_HCAD_NUM"))
    )

    address = (
        _clean_str(raw_props.get("SITE_ADDR_1"))
        or _clean_str(raw_props.get("Site_addr_1"))
        or _clean_str(hist.get("Site_addr_1"))
        or _clean_str(lm.get("USER_SITE_ADDRESS"))
        or _clean_str(raw_props.get("LocAddr"))
    )
    if not address:
        str_num = _clean_str(raw_props.get("STR_NUM") or raw_props.get("Str_num"))
        str_name = _clean_str(raw_props.get("STR_NAME") or raw_props.get("Str_name"))
        str_sfx = _clean_str(raw_props.get("STR_SFX") or raw_props.get("Str_sfx"))
        address = " ".join(p for p in (str_num, str_name, str_sfx) if p)

    yr_cadastral = normalize_year(
        raw_props.get("YR_IMPR")
        or raw_props.get("Yr_Impr")
        or raw_props.get("date_erect")
    )
    yr_hist = normalize_year(hist.get("Yr_Impr"))
    yr_lm = normalize_year(lm.get("USER_YR_BUILT"))

    candidates = [y for y in (yr_lm, yr_hist, yr_cadastral) if y > 0]
    year_built = min(candidates) if candidates else 0
    remodel_year = normalize_year(raw_props.get("yr_remodel"))

    owner = (
        _clean_str(raw_props.get("OWNER_MAILTO"))
        or _clean_str(raw_props.get("Owner_Mailto"))
        or _clean_str(hist.get("Owner_Mailto"))
        or _clean_str(lm.get("USER_OWNER"))
        or _clean_str(raw_props.get("CurrOwner"))
    )

    bld_area = _clean_float(
        raw_props.get("TOTAL_BUILDING_AREA")
        or raw_props.get("Total_Building_Area")
        or hist.get("Total_Building_Area")
        or raw_props.get("im_sq_ft")
    )
    land_area = _clean_float(
        raw_props.get("TOTAL_LAND_AREA")
        or raw_props.get("Total_Land_Area")
        or hist.get("Total_Land_Area")
    )

    state_class = _clean_str(
        raw_props.get("STATE_CLASS")
        or raw_props.get("State_Class")
        or hist.get("State_Class")
    )
    landuse_desc = _clean_str(
        raw_props.get("LANDUSE_DSCR")
        or raw_props.get("landuse_dscr")
        or hist.get("landuse_dscr")
    )
    group_desc = _clean_str(
        raw_props.get("GROUP_DSCR")
        or raw_props.get("group_dscr")
        or hist.get("group_dscr")
    )
    use_category = classify_use_category(state_class, landuse_desc, group_desc)

    historic_district = (
        _clean_str(hist.get("Historic_District_Name"))
        or _clean_str(raw_props.get("Historic_District_Name"))
        or _clean_str(lm.get("USER_HISTORIC_DISTRICT"))
    )

    raw_contrib = _clean_str(
        hist.get("Building_Classification")
        or raw_props.get("Building_Classification")
    )
    if raw_contrib == "Contributing":
        contributing = "Contributing"
    elif raw_contrib in ("NonContributing", "Non-Contributing"):
        contributing = "Non-Contributing"
    else:
        contributing = "Outside Historic District"

    landmark_name = (
        _clean_str(lm.get("USER_SITE_NAME"))
        or _clean_str(raw_props.get("PDLandMark"))
    )
    if landmark_name.lower() in (
        "no",
        "none",
        "0",
        "false",
        "no designation",
        "not a landmark",
        "n/a",
        "undesignated",
    ):
        landmark_name = ""

    raw_lm_type = _clean_str(
        lm.get("LandmarkDesignation")
        or hist.get("Landmark_Designation")
        or raw_props.get("Landmark_Designation")
        or raw_props.get("DESIG_TYPE")
    )
    if raw_lm_type in ("Protected Landmark", "PLM"):
        landmark_type = "Protected Landmark"
    elif raw_lm_type in ("Landmark", "LM") or landmark_name:
        landmark_type = "Landmark"
    else:
        landmark_type = ""

    architect = _clean_str(lm.get("USER_ARCHITECT___BUILDER"))
    bld_style = (
        _clean_str(lm.get("USER_STYLE"))
        or _clean_str(raw_props.get("style_desc"))
        or _clean_str(raw_props.get("ECON_BLD_CLASS"))
        or _clean_str(hist.get("Econ_Bld_Class"))
    )
    subdivision = (
        _clean_str(raw_props.get("LEGAL_DSCR_1"))
        or _clean_str(raw_props.get("Legal_Dscr_1"))
        or _clean_str(hist.get("Legal_Dscr_1"))
        or _clean_str(lm.get("USER_SUBDIVISION"))
    )

    stories, height_m = estimate_stories_and_height(
        bld_area=bld_area,
        footprint_area_sqft=bld_area * 0.65 if bld_area > 0 else 0.0,
        use_category=use_category,
    )

    return {
        "id": f"pcl_{hcad_num or id(raw_props)}",
        "hcad_num": hcad_num,
        "address": address,
        "year_built": year_built,
        "decade": compute_decade(year_built),
        "remodel_year": remodel_year,
        "owner": owner,
        "bld_area": bld_area,
        "land_area": land_area,
        "stories": stories,
        "height_m": height_m,
        "use_category": use_category,
        "landuse_desc": landuse_desc or use_category,
        "bld_style": bld_style,
        "subdivision": subdivision,
        "historic_district": historic_district,
        "contributing": contributing,
        "landmark_name": landmark_name,
        "landmark_type": landmark_type,
        "architect": architect,
    }
