"""Normalized feature schema and HCAD/COH attribute mapping for Houston Building Atlas v2."""

from __future__ import annotations

import datetime
import json
import math
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

MIN_VALID_YEAR = 1836
MAX_VALID_YEAR = 2027
MAX_VALID_CAMA_DATE_YEAR = 2026
MAX_STANDING_OVERRIDE_YEAR = 2035
ACTIVE_CACHE_BUST = "20261029a"
QUAD_NAMES: tuple[str, ...] = ("nw_w", "nw_e", "ne", "sw", "se")

DEMOLISHED_VACANT_ISSUE = "Building demolished - lot currently vacant"
DEMOLISHED_REPLACED_ISSUE = "Building demolished & replaced by newer structure"
DEMOLISHED_VACANT_CITATION_TAG = "[DEMOLISHED / VACANT LOT - HIDE FOOTPRINT]"

_EXPLICIT_VACANT_YEAR_TOKENS: frozenset[str] = frozenset({
    "0",
    "vacant",
    "vacant lot",
    "demolished",
    "none",
    "n/a",
})
_RE_DEMOLISHED_VACANT_TEXT = re.compile(
    r"\[demolished\s*/\s*vacant\s+lot"
    r"|demolished\s*[-:/]?\s*lot\s+currently\s+vacant"
    r"|vacant\s+lot\s*\(hide\s+footprint\)"
    r"|\bsuppress_only\b",
    re.IGNORECASE,
)
_RE_LEGACY_DEMOLISHED_ISSUE = re.compile(
    r"building\s+demolished\s+or\s+replaced",
    re.IGNORECASE,
)
_RE_VACANT_LOT_CUE = re.compile(
    r"\bvacant\b|\bcleared\b|\bempty\s+lot\b|\bno\s+replacement\b",
    re.IGNORECASE,
)


def is_demolished_vacant_marker(
    year_raw: Any = None,
    *,
    footprint_issue: str = "",
    footprint_notes: str = "",
    source_type: str = "",
    source_citation: str = "",
    suppress_only: bool | None = None,
) -> bool:
    """Return True when an override/submission marks a demolished structure on a currently vacant lot.

    Supports both entry paths requested by curators:
      1. Explicit `0` or `"Vacant"` / `"Demolished"` in `year_built` / `suggested_year_built`
         (unless `suppress_only=False` is explicitly asserted on a non-vacant payload).
      2. Selecting `"Building demolished - lot currently vacant"` (or `[DEMOLISHED / VACANT LOT]`
         in `source_type` / `source_citation`, or legacy `"Building demolished or replaced"` with
         blank/`0` year and `"vacant"` in notes/citation), even when Google Apps Script `doPost`
         normalizes `0` to `""` in the sheet's `year_built` column.

    Never returns True when a valid replacement construction year (`MIN_VALID_YEAR..MAX_STANDING_OVERRIDE_YEAR`)
    is supplied unless `suppress_only=True` is explicitly set.
    """
    if suppress_only is True:
        return True

    raw_str = "" if year_raw is None else str(year_raw).strip()
    low_yr = raw_str.lower()
    if low_yr in _EXPLICIT_VACANT_YEAR_TOKENS:
        if suppress_only is False and low_yr == "0":
            return False
        return True

    parsed_yr = 0
    if raw_str:
        try:
            fval = float(raw_str)
            if not math.isfinite(fval):
                return False
            parsed_yr = int(round(fval))
        except (ValueError, TypeError):
            return False

    if MIN_VALID_YEAR <= parsed_yr <= MAX_STANDING_OVERRIDE_YEAR:
        return False
    if parsed_yr != 0:
        return False

    combined_meta = " | ".join(
        s for s in (footprint_issue, source_type, source_citation) if s
    )
    if _RE_DEMOLISHED_VACANT_TEXT.search(combined_meta):
        return True

    issue_and_src = f"{footprint_issue} | {source_type}"
    notes_and_cite = f"{footprint_notes} | {source_citation}"
    if _RE_LEGACY_DEMOLISHED_ISSUE.search(issue_and_src) and _RE_VACANT_LOT_CUE.search(notes_and_cite):
        return True

    return False

INFERRED_FALLBACK_YEAR_SOURCES: frozenset[str] = frozenset({
    "hcad_permit",
    "campus_contiguity",
    "adjacent_owner",
    "hcad_extra_feature",
    "hcad_tieback",
    "hcad_deed",
    "subdivision_median",
    "blockface_median",
})

AUTHORITATIVE_YEAR_SOURCES: frozenset[str] = frozenset({
    "",
    "hcad_cama",
    "curated_landmark",
    "good_brick_award",
    "archival",
})

VALID_CONTRIBUTING_VALUES: frozenset[str] = frozenset({
    "Contributing",
    "Non-Contributing",
    "Outside Historic District",
    "Tax-Exempt Campus Structure",
})

_RE_MDY_DATE = re.compile(
    r"^(\d{1,2})/(\d{1,2})/(\d{4})(?:[\sT].*)?$"
)
_RE_YMD_DATE = re.compile(
    r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:[\sT].*)?$"
)


def is_hcad_null_date_sentinel(year: int, month: int, day: int) -> bool:
    """Return True when (year, month, day) is an HCAD Microsoft COM/OLE/SQL null-date sentinel.

    HCAD CAMA tabular exports (permits.txt, deeds.txt, real_acct.txt splt_dt/new_own_dt)
    serialize null Microsoft COM/OLE Automation VT_DATE / Access / Excel serial-0 dates as
    12/30/1899 (or 12/31/1899), and SQL Server DATETIME / Lotus-Excel epoch defaults as
    01/01/1900 or 01/02/1900. Genuine non-sentinel 1900 calendar dates (e.g. 06/15/1900)
    are preserved.
    """
    if month <= 0 or day <= 0:
        return True
    if year == 1899:
        return True
    if year == 1900 and month == 1 and day in (1, 2):
        return True
    return False


def normalize_hcad_date(
    date_str: Any,
    fallback_yr_str: Any = None,
    *,
    allow_fallback: bool = True,
    min_year: int = MIN_VALID_YEAR,
    max_year: int = MAX_VALID_CAMA_DATE_YEAR,
) -> tuple[str | None, int]:
    """Parse an HCAD date string into (iso_yyyy_mm_dd_or_none, valid_year_or_0).

    Rejects SQL/OLE null-date sentinels (year 1899, 01/01/1900, 01/02/1900, 00/00/0000,
    invalid calendar dates, and years outside [min_year, max_year]).

    Source-specific fallback rules:
      - deeds.txt passes allow_fallback=True with clerk_yr, which records the County Clerk
        instrument filing year when dos is a 12/30/1899 null sentinel.
      - permits.txt passes allow_fallback=False because permits.txt yr stores the HCAD
        appraisal tax-roll cycle year (e.g. 2008 or 2025) rather than permit issuance year.
    """
    s = "" if date_str is None else str(date_str).strip()
    if s and s not in ("0", "00/00/0000", "0000-00-00"):
        y_int = m_int = d_int = 0
        matched = False
        m_mdy = _RE_MDY_DATE.match(s)
        if m_mdy:
            m_int, d_int, y_int = int(m_mdy.group(1)), int(m_mdy.group(2)), int(m_mdy.group(3))
            matched = True
        else:
            m_ymd = _RE_YMD_DATE.match(s)
            if m_ymd:
                y_int, m_int, d_int = int(m_ymd.group(1)), int(m_ymd.group(2)), int(m_ymd.group(3))
                matched = True

        if matched and not is_hcad_null_date_sentinel(y_int, m_int, d_int):
            if min_year <= y_int <= max_year:
                try:
                    dt_obj = datetime.date(y_int, m_int, d_int)
                    return dt_obj.isoformat(), y_int
                except ValueError:
                    pass

    if allow_fallback and fallback_yr_str is not None:
        fb_yr = normalize_year(fallback_yr_str)
        if fb_yr != 1899 and min_year <= fb_yr <= max_year:
            return None, fb_yr

    return None, 0


def parse_hcad_date_year(
    date_str: Any,
    fallback_yr_str: Any = None,
    *,
    allow_fallback: bool = True,
    min_year: int = MIN_VALID_YEAR,
    max_year: int = MAX_VALID_CAMA_DATE_YEAR,
) -> int:
    """Return the validated integer year in [min_year, max_year] for an HCAD date field, or 0."""
    _, yr = normalize_hcad_date(
        date_str,
        fallback_yr_str,
        allow_fallback=allow_fallback,
        min_year=min_year,
        max_year=max_year,
    )
    return yr


def write_compact_json(path: Path | str, payload: Any) -> None:
    """Serialize payload as single-line compact UTF-8 JSON to keep data bundles within budget."""
    target = Path(path)
    tmp_path = target.with_name(f"{target.name}.tmp")
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    tmp_path.write_text(serialized, encoding="utf-8")
    tmp_path.replace(target)


def sanitize_atlas_text(val: str) -> str:
    """Normalize typography so public JSON artifacts contain zero U+2014 em-dashes."""
    return val.replace(" \u2014 ", " - ").replace("\u2014", "-")


def compile_quadrant_pmtiles(
    shard_dir: Path,
    output_dir: Path,
    quad_names: tuple[str, ...] = QUAD_NAMES,
) -> tuple[list[str], int]:
    """Compile quadrant .geojsonseq shards into Web Mercator PMTiles v3 archives in parallel via Tippecanoe."""
    tippecanoe_bin = shutil.which("tippecanoe") or "/tmp/tippecanoe/tippecanoe"
    if not Path(tippecanoe_bin).exists():
        tippecanoe_bin = str(Path.home() / ".local" / "bin" / "tippecanoe")

    print("-> Compiling 5 Web Mercator tile-aligned PMTiles v3 archives in parallel via Tippecanoe...")
    t_tip = time.time()
    procs: list[tuple[str, subprocess.Popen[bytes], Path]] = []
    for q in quad_names:
        in_seq = shard_dir / f"buildings_{q}.geojsonseq"
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
            raise RuntimeError(
                f"Tippecanoe failed for quadrant {q}: {stderr.decode('utf-8', errors='replace')}"
            )
        sz = out_pmtiles.stat().st_size
        total_pmtiles_bytes += sz
        shard_files.append(out_pmtiles.name)
        print(f"   Compiled {out_pmtiles.name}: {sz / (1024 * 1024):.1f} MB")
    print(f"-> Tippecanoe compilation finished in {time.time() - t_tip:.1f}s.")
    return shard_files, total_pmtiles_bytes


def normalize_override_record(key: str, record: dict[str, Any]) -> dict[str, Any]:
    """Enforce integer decade, canonical IDs, clean typography, and valid contributing enum on an override record."""
    out = dict(record)
    for fld, fval in list(out.items()):
        if isinstance(fval, str) and "\u2014" in fval:
            out[fld] = sanitize_atlas_text(fval)
        elif isinstance(fval, list) and any(isinstance(x, str) and "\u2014" in x for x in fval):
            out[fld] = [sanitize_atlas_text(x) if isinstance(x, str) else x for x in fval]

    clean_key = str(key).strip()
    raw_prefix = clean_key.split("#")[0].strip()
    hcad = str(out.get("hcad_num") or (raw_prefix if raw_prefix.isdigit() else "")).strip()
    if hcad:
        out["hcad_num"] = hcad
    if not out.get("id"):
        out["id"] = clean_key
    if "#" in clean_key and not out.get("building_id"):
        out["building_id"] = clean_key

    if bool(out.get("suppress_only")):
        out["suppress_only"] = True
        out["year_built"] = 0
        out["decade"] = 0
        out.pop("geometry", None)
    else:
        yr = normalize_year(out.get("year_built"))
        if yr >= MIN_VALID_YEAR:
            out["year_built"] = yr
            out["decade"] = compute_decade(yr)
        elif "decade" in out:
            raw_dec = out.get("decade")
            if isinstance(raw_dec, str):
                dec_digits = re.sub(r"\D", "", raw_dec)
                out["decade"] = int(dec_digits) if len(dec_digits) == 4 else 0
            elif isinstance(raw_dec, (int, float)):
                out["decade"] = int(raw_dec)
            else:
                out["decade"] = 0

    contrib = str(out.get("contributing") or "").strip()
    if contrib == "NonContributing":
        contrib = "Non-Contributing"
    if contrib not in VALID_CONTRIBUTING_VALUES:
        out["contributing"] = "Outside Historic District"
    else:
        out["contributing"] = contrib

    return out


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
