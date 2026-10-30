#!/usr/bin/env python3
"""
Builds Shareable Neighborhood & Council District Preservation Scorecards for The Houston Building Atlas.

Responsibilities:
  1. Fetches and caches official City of Houston 2024 Council District boundaries
     (`pipeline/cache/coh_council_districts_2024.geojson`, 11 districts A-K) and enriches
     them with verified January 2024 - January 2028 Council Member roster & contact metadata.
  2. Hydrates `overlays.json["land_use_protections"]` with statutory Chapter 42 ordinance
     sunset lifecycle metadata (`resolve_ch42_ordinance_lifecycle`, DF-1) so pre-July 24, 2015
     SMBL and SMLS ordinances with null GIS Expiration_Date receive derived 20-year statutory
     sunsets (`is_estimated_sunset: True`), and idempotently upserts `overlays.json["council_districts"]`
     and `search_index.json` (`council_dist_*`, DF-7).
  3. Builds explicit two-grain spatial indices (DF-4):
       - `StandingBuildingIndex`: active footprints across all 5 `aligned_ndjson/*.geojsonseq`
         shards reconciled with `curated_overrides.json`, excluding `suppress_only: True`
         and `year_built <= 0`.
       - `UniqueParcelIndex`: unique normalized 13-digit HCAD parcels from `Parcels.gdb`
         (`EPSG:2278`) joined to `Real_acct_owner.zip/real_acct.txt` for situs-matched
         resident occupancy, family trust/estate vs. corporate LLC ownership, SFR share,
         and dual-mode SMLS/SMBL protection coverage (DF-3).
  4. Aggregates 2020-2026 demolition & infill velocity from `Real_acct_owner.zip/permits.txt`
     (Types 13/33/43, rejecting 1899/1900 SQL/OLE null-date sentinels and non-main-building
     pool/garage/interior scopes) merged & deduplicated by `(hcad13, year)` with cached 2026
     YTD City of Houston structural demolition permits (`SD`/`4D`).
  5. Writes compact single-line `app/public/data/neighborhood_scorecards.json` (`< 2.5 MB`)
     across 9 boundary families (`council_districts`, `historic_districts`, `heritage_districts`,
     `nrhp_districts`, `potential_historic_districts`, `super_neighborhoods`, `holc_1937_zones`,
     `neighborhoods`, and `platted_subdivisions` with >= 10 standing structures).
"""

from __future__ import annotations

import ast
import concurrent.futures
import datetime
import glob
import json
import re
import ssl
import time
import urllib.parse
import urllib.request
import zipfile
from collections import Counter
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pyogrio.raw
import shapely
from pyproj import Transformer
from shapely import STRtree
from shapely.geometry import MultiPolygon, Point, Polygon, mapping, shape
from shapely.validation import make_valid

from atlas_pipeline.schema import (
    MAX_VALID_CAMA_DATE_YEAR,
    MIN_VALID_YEAR,
    classify_main_structure_demolition_permit,
    is_boundary_search_entry,
    is_demolished_vacant_marker,
    is_main_structure_demolition_scope,
    normalize_hcad_date,
    normalize_year,
    resolve_ch42_ordinance_lifecycle,
    sanitize_atlas_text,
    upsert_search_index_slice,
    write_compact_json,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = REPO_ROOT / "pipeline" / "cache"
DATA_DIR = REPO_ROOT / "app" / "public" / "data"

COUNCIL_GEOJSON_URL = (
    "https://services.arcgis.com/NummVBqZSIJKUeVR/arcgis/rest/services/"
    "Council_District_2024/FeatureServer/0/query?where=1%3D1&outFields=*&outSR=4326&f=geojson"
)
COH_WFSERVLET_URL = "http://cohtora.houstontx.gov/ibi_apps/WFServlet"

SCORECARD_SCHEMA_VERSION = "1.0"
COUNCIL_TERM_EXPIRES = "2028-01"
MIN_SUBDIVISION_STANDING_STRUCTURES = 10
RIBBON_SMBL_RATIO_THRESHOLD_FT = 20.0
RIBBON_SMBL_BUFFER_FT = 26.0

# Verified sitting Houston City Council roster (January 2024 - January 2028 term).
# Note: District C seat filled via special election by Council Member Joe Panzarella
# (replacing former CM Abbie Kamin still listed in COH's 2024 static GIS attribute table).
COH_COUNCIL_ROSTER_2024_2028: dict[str, dict[str, str]] = {
    "A": {
        "member": "Amy Peck",
        "phone": "832-393-3010",
        "email": "districta@houstontx.gov",
        "url": "https://www.houstontx.gov/council/a/index.html",
    },
    "B": {
        "member": "Tarsha Jackson",
        "phone": "832-393-3009",
        "email": "districtb@houstontx.gov",
        "url": "https://www.houstontx.gov/council/b/index.html",
    },
    "C": {
        "member": "Joe Panzarella",
        "phone": "832-393-3004",
        "email": "districtc@houstontx.gov",
        "url": "https://www.houstontx.gov/council/c/index.html",
    },
    "D": {
        "member": "Carolyn Evans-Shabazz",
        "phone": "832-393-3001",
        "email": "districtd@houstontx.gov",
        "url": "https://www.houstontx.gov/council/d/index.html",
    },
    "E": {
        "member": "Fred Flickinger",
        "phone": "832-393-3008",
        "email": "districte@houstontx.gov",
        "url": "https://www.houstontx.gov/council/e/index.html",
    },
    "F": {
        "member": "Tiffany D. Thomas",
        "phone": "832-393-3002",
        "email": "districtf@houstontx.gov",
        "url": "https://www.houstontx.gov/council/f/index.html",
    },
    "G": {
        "member": "Mary Nan Huffman",
        "phone": "832-393-3007",
        "email": "districtg@houstontx.gov",
        "url": "https://www.houstontx.gov/council/g/index.html",
    },
    "H": {
        "member": "Mario Castillo",
        "phone": "832-393-3003",
        "email": "districth@houstontx.gov",
        "url": "https://www.houstontx.gov/council/h/index.html",
    },
    "I": {
        "member": "Joaquin Martinez",
        "phone": "832-393-3011",
        "email": "districti@houstontx.gov",
        "url": "https://www.houstontx.gov/council/i/index.html",
    },
    "J": {
        "member": "Edward Pollard",
        "phone": "832-393-3015",
        "email": "districtj@houstontx.gov",
        "url": "https://www.houstontx.gov/council/j/index.html",
    },
    "K": {
        "member": "Martha Castex-Tatum",
        "phone": "832-393-3016",
        "email": "districtk@houstontx.gov",
        "url": "https://www.houstontx.gov/council/k/index.html",
    },
}

DECADE_BINS: tuple[int, ...] = (
    1830,
    1840,
    1850,
    1860,
    1870,
    1880,
    1890,
    1900,
    1910,
    1920,
    1930,
    1940,
    1950,
    1960,
    1970,
    1980,
    1990,
    2000,
    2010,
    2020,
)
DEMO_YEARS: tuple[int, ...] = (2020, 2021, 2022, 2023, 2024, 2025, 2026)

SCORECARD_FIELD_LEGEND: dict[str, str] = {
    "id": "Canonical boundary feature ID matching overlays.json",
    "ly": "Boundary overlay layer key (e.g. council_districts, historic_districts, neighborhoods)",
    "nm": "Human-readable boundary display name",
    "tp": "Boundary classification label",
    "cx": "Representative interior point longitude (WGS84)",
    "cy": "Representative interior point latitude (WGS84)",
    "bb": "Bounding box [minLng, minLat, maxLng, maxLat] in WGS84",
    "ac": "Boundary planar land area in acres (EPSG:2278)",
    "bc": "Total standing dated building footprints inside boundary (suppress_only & year=0 excluded)",
    "ey": "Earliest recorded construction year among standing footprints",
    "my": "Median construction year among standing footprints",
    "p20": "Standing footprints built prior to 1920",
    "p40": "Standing footprints built prior to 1940",
    "p60": "Standing footprints built prior to 1960",
    "p75": "Standing footprints built prior to 1975 (50-year NRHP age horizon)",
    "n20": "Standing footprints built in or after 2020 (post-2020 infill volume)",
    "dh": "Decade histogram counts aligned with top-level decade_bins array (1830s..2020s)",
    "lm": "Total designated City of Houston Landmarks inside boundary",
    "plm": "Protected Landmarks (PLM) with substantive anti-demolition protection",
    "vlm": "Non-protected Landmarks subject only to 90-day demolition waiver delay",
    "gb": "Preservation Houston Good Brick Award recipient sites inside boundary",
    "nrp": "Individual National Register of Historic Places (NRHP) listed properties",
    "lhc": "Documented Lost Houston demolished landmark sites inside boundary",
    "pc": "Total unique normalized 13-digit HCAD tax parcels inside boundary",
    "rpc": "Unique residential parcels (HCAD State Class A* single-family/townhome/condo & B* multi-family)",
    "sfc": "Unique single-family detached/townhome parcels (HCAD State Class A1)",
    "occ": "Unique residential parcels with situs-matched homestead occupancy (individual or family trust at situs)",
    "ftc": "Unique non-corporate family trust / living trust / estate residential parcels",
    "llc": "Unique residential parcels owned by corporate LLC / LP / INC / investment vehicles",
    "abs": "Unique non-corporate, non-trust absentee individual residential parcels",
    "chp": "Unique residential parcels protected by an enacted COH Chapter 42 SMLS or SMBL ordinance",
    "slp": "Unique residential parcels covered by an enacted Special Minimum Lot Size (SMLS) ordinance",
    "sbp": "Unique residential parcels covered by an enacted Special Minimum Building Line (SMBL) ordinance",
    "chx": "Unique residential parcels inside a Ch. 42 ordinance expiring in 2026-2031 (20-year sunset horizon)",
    "slo": "Count of intersecting enacted SMLS ordinances",
    "sbo": "Count of intersecting enacted SMBL ordinances",
    "exo": "List of expiring 2026-2031 Ch. 42 ordinances [{o, t, y, est}] requiring neighborhood renewal",
    "cdo": "Count of intersecting COH Chapter 33 Conservation Districts",
    "hdp": "Standing building footprints inside an enacted COH Chapter 33 Historic or Heritage District",
    "dm": "Annual unique-parcel main-building structural demolitions aligned with demo_years (2020..2026 YTD)",
    "dmt": "Cumulative unique-parcel structural demolitions recorded across 2020-2026 YTD",
    "dmp40": "2020-2026 structural demolitions matched to a pre-1940 structure history where known",
    "sn": "Primary overlapping Super Neighborhood name (where applicable)",
    "cd": "Overlapping City Council District letter(s) (e.g. 'C' or 'C, H')",
    "cc": "Overlapping registered Civic Club / HOA name (where applicable)",
    "cm": "Council District metadata {letter, member, phone, email, url, term_expires}",
    "hl": "1937 HOLC Redlining metadata {grade, code, temp_f, delta_vs_a}",
    "pl": "Historic Plat metadata {year_label, citation, deed_citation, has_deed_docs}",
    "ad": "Boolean true when a surveyed potential historic district has since been designated as a COH Historic District",
}

SPARSE_ZERO_SCORECARD_KEYS: tuple[str, ...] = (
    "lm",
    "plm",
    "vlm",
    "gb",
    "nrp",
    "lhc",
    "chp",
    "slp",
    "sbp",
    "chx",
    "slo",
    "sbo",
    "cdo",
    "hdp",
    "dmt",
    "dmp40",
)

_ADDR_ABBR_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bSTREET\b"), "ST"),
    (re.compile(r"\bAVENUE\b"), "AVE"),
    (re.compile(r"\bBOULEVARD\b"), "BLVD"),
    (re.compile(r"\bDRIVE\b"), "DR"),
    (re.compile(r"\bROAD\b"), "RD"),
    (re.compile(r"\bLANE\b"), "LN"),
    (re.compile(r"\bCOURT\b"), "CT"),
    (re.compile(r"\bCIRCLE\b"), "CIR"),
    (re.compile(r"\bPLACE\b"), "PL"),
    (re.compile(r"\bPARKWAY\b"), "PKWY"),
    (re.compile(r"\bPKY\b"), "PKWY"),
    (re.compile(r"\bFREEWAY\b"), "FWY"),
    (re.compile(r"\bHIGHWAY\b"), "HWY"),
    (re.compile(r"\bSPEEDWAY\b"), "SPWY"),
    (re.compile(r"\bTERRACE\b"), "TER"),
    (re.compile(r"\bTRAIL\b"), "TRL"),
    (re.compile(r"\bSQUARE\b"), "SQ"),
    (re.compile(r"\bNORTH\b"), "N"),
    (re.compile(r"\bSOUTH\b"), "S"),
    (re.compile(r"\bEAST\b"), "E"),
    (re.compile(r"\bWEST\b"), "W"),
)
_RE_UNIT_TAIL = re.compile(
    r"\s+(?:APT|UNIT|STE|SUITE|FL|FLOOR|BLDG|BLD|RM|ROOM|LOT|TRLR|#)\b.*$"
)
_RE_CORP_ENTITY = re.compile(
    r"(?:\b(?:L\s*L\s*C|LLC|PLLC|L\s*P|LP|LLP|LTD|INC|INCORPORATED|CORP|CORPORATION|"
    r"HOLDINGS|INVESTMENTS|INVESTORS|VENTURES|CAPITAL|PARTNERS|PARTNERSHIP|"
    r"HOMES|BUILDERS|BUILDING\s+CO|DEVELOPMENT|DEVELOPERS|REALTY|REAL\s+ESTATE|"
    r"PROPERTIES|ENTERPRISES|EQUITY|ACQUISITIONS|REIT|MORTGAGE|BANK|NATIONAL\s+ASSN)\b)",
    re.IGNORECASE,
)
_RE_PUBLIC_EXEMPT = re.compile(
    r"(?:\b(?:CITY\s+OF\s+HOUSTON|HARRIS\s+COUNTY|STATE\s+OF\s+TEXAS|TXDOT|"
    r"METROPOLITAN\s+TRANSIT|HOUSTON\s+ISD|INDEPENDENT\s+SCHOOL|HOUSING\s+AUTHORITY|"
    r"FLOOD\s+CONTROL|UNITED\s+STATES|PORT\s+OF\s+HOUSTON|DEPT\s+OF\s+TRANSP)\b)",
    re.IGNORECASE,
)
_RE_FAMILY_TRUST_ESTATE = re.compile(
    r"(?:\b(?:TRUST|TRUSTEE|TRUSTEES|LIVING\s+TR|REV\s+TR|REVOCABLE\s+TR|"
    r"FAM\s+TR|FAMILY\s+TR|IRREVOCABLE\s+TR|TESTAMENTARY\s+TR|"
    r"ESTATE\s+OF|EST\s+OF|HEIRS\s+OF|\bESTATE\b|\bTRS\b)\b)",
    re.IGNORECASE,
)
def round_coords(geom_dict: dict[str, Any], decimals: int = 5) -> dict[str, Any]:
    def _rc(c: Any) -> Any:
        if isinstance(c, (int, float)):
            return round(float(c), decimals)
        return [_rc(x) for x in c]

    return {"type": geom_dict["type"], "coordinates": _rc(geom_dict["coordinates"])}


def normalize_hcad13(raw_val: Any) -> str:
    """Normalize an HCAD account string to its canonical 13-digit identifier, or '' if invalid."""
    if raw_val is None:
        return ""
    s = str(raw_val).split("#")[0].strip()
    digits = re.sub(r"\D", "", s)
    if len(digits) == 13 and digits != "0000000000000":
        return digits
    return ""


def normalize_situs_compare_key(addr: Any) -> str:
    """Normalize a street address into a compact comparison key for situs-vs-mailing matching."""
    if not addr:
        return ""
    s = str(addr).upper().strip()
    if not s or s.startswith("0 ") or s == "0":
        return ""
    if re.search(r"\bP\.?\s*O\.?\s*BOX\b", s):
        return ""
    s = re.sub(r"\bHOUSTON\s+HARRIS\s+TX.*$", "", s)
    s = re.sub(r"\bHOUSTON\s*,?\s*TX.*$", "", s)
    s = re.sub(r"\s+\d{5}(?:-\d{4})?\s*$", "", s)
    s = re.sub(r"[.,'\"/\\()-]", lambda m: "/" if m.group(0) == "/" else " ", s)
    s = re.sub(r"\s*#\s*\S+.*$", "", s)
    s = _RE_UNIT_TAIL.sub("", s)
    for pat, rep in _ADDR_ABBR_RULES:
        s = pat.sub(rep, s)
    s = re.sub(r"\s+", " ", s).strip()
    if not re.match(r"^\d+\s+[A-Z0-9]", s):
        return ""
    m_strip = re.match(
        r"^(\d+(?:\s+1/2)?\s+(?:[NSEW]\s+)?[A-Z0-9\s]+?\s+"
        r"(?:ST|AVE|BLVD|DR|RD|LN|CT|CIR|PL|PKWY|FWY|HWY|WAY|TER|TRL|SQ|LOOP))\s+"
        r"(?:[A-Z]|\d+|A\s*&\s*B|REAR|FRONT)$",
        s,
    )
    if m_strip:
        s = m_strip.group(1).strip()
    return s


def classify_parcel_ownership(
    owner_name: Any,
    situs_addr_1: Any,
    mail_addr_1: Any,
    mail_addr_2: Any = "",
) -> tuple[str, bool]:
    """Classify a parcel owner into (`bucket`, `situs_matched`) (DF-4).

    Mutually exclusive buckets for residential parcels:
      - `"public_exempt"`: Municipal/county/state/ISD/public entities.
      - `"corporate"`: LLC, LP, INC, CORP, builders, holdings, capital/investment vehicles.
        Checked BEFORE situs address matching so a vacant teardown lot whose corporate LLC
        entered the situs address as its mailing line is never miscounted as owner-occupied.
      - `"family_trust"`: Individual/family living trusts, revocable trusts, trustees, and estates.
        Separately exposes `situs_matched=True` when the family trust mails to the home itself.
      - `"resident"`: Non-corporate, non-trust individual whose normalized mailing address
        matches the normalized situs property address.
      - `"absentee"`: Non-corporate, non-trust individual mailing to a different address.
    """
    owner = str(owner_name or "").upper().strip()
    if _RE_PUBLIC_EXEMPT.search(owner):
        return "public_exempt", False
    if _RE_CORP_ENTITY.search(owner):
        return "corporate", False

    situs_key = normalize_situs_compare_key(situs_addr_1)
    mail1_key = normalize_situs_compare_key(mail_addr_1)
    mail2_key = normalize_situs_compare_key(mail_addr_2)
    situs_matched = False
    if situs_key:
        if mail1_key and (
            situs_key == mail1_key
            or mail1_key.startswith(situs_key + " ")
            or situs_key.startswith(mail1_key + " ")
        ):
            situs_matched = True
        elif mail2_key and (
            situs_key == mail2_key
            or mail2_key.startswith(situs_key + " ")
            or situs_key.startswith(mail2_key + " ")
        ):
            situs_matched = True

    if _RE_FAMILY_TRUST_ESTATE.search(owner):
        return "family_trust", situs_matched
    if situs_matched:
        return "resident", True
    return "absentee", False


def is_main_structure_demolition_desc(desc: Any) -> bool:
    """Return True when a permit description represents a primary building teardown (DF-4 / DF-5).

    Delegates to `schema.is_main_structure_demolition_scope` so Cohort 5 Lost Houston
    and Scorecard `dp`/`dy`/`dp40` totals share a single source of truth.
    """
    return is_main_structure_demolition_scope(desc, allow_empty=True)


def parse_demolition_permit_row(
    *,
    acct: Any,
    permit_type: Any,
    dscr: Any,
    issue_date: Any,
    roll_yr: Any = None,
    min_year: int = 2020,
    max_year: int = MAX_VALID_CAMA_DATE_YEAR,
) -> tuple[str, int] | None:
    """Validate an HCAD `permits.txt` row and return `(hcad13, issue_year)` if it is a main-structure demo (DF-4).

    Strictly passes `allow_fallback=False` to `normalize_hcad_date` so Microsoft COM/OLE/SQL
    null-date sentinels (`12/30/1899`, `01/01/1900`) NEVER fall back to the appraisal tax-roll
    `yr` column (`roll_yr`). Accepts only HCAD demo permit types `13`, `33`, and `43`.
    """
    ptype = str(permit_type or "").strip()
    if ptype not in ("13", "33", "43"):
        return None
    hcad13 = normalize_hcad13(acct)
    if not hcad13:
        return None
    _iso, yr = normalize_hcad_date(
        issue_date,
        roll_yr,
        allow_fallback=False,
        min_year=min_year,
        max_year=max_year,
    )
    if yr < min_year or yr > max_year:
        return None
    if not is_main_structure_demolition_desc(dscr):
        return None
    return (hcad13, yr)


def iter_reconciled_standing_buildings(
    shard_features: Iterable[dict[str, Any]],
    overrides: dict[str, dict[str, Any]],
) -> Iterator[dict[str, Any]]:
    """Stream reconciled standing structures across shard features and `curated_overrides.json` (DF-4).

    Enforces parity with `mapController.js` `_applyCuratedOverridesInMemory` / `_buildShardLayerFilter`:
      1. Suppresses shard footprints whose `hcad_num` matches a parcel-level override with explicit
         `geometry`, `suppress_only: True`, `replace_parcel_shards: True`, or `suppress_shard_hcads`.
      2. Applies attribute-only parcel overrides (`year_built`, `historic_district`, `contributing`,
         `landmark_name`, `landmark_type`) to matching shard footprints when override lacks `geometry`.
      3. Appends standing override polygons (expanding `MultiPolygon` overrides into constituent
         `Polygon` footprints so industrial/campus structure counts match rendered footprints).
      4. Excludes all `suppress_only` / demolished-vacant markers and any record with `year_built < 1836`.
    """
    suppressed_shard_hcads: set[str] = set()
    suppressed_entity_ids: set[str] = set()
    attr_only_by_hcad: dict[str, dict[str, Any]] = {}
    attr_only_by_id: dict[str, dict[str, Any]] = {}
    standalone_overrides: list[tuple[str, dict[str, Any]]] = []

    for raw_key, ov in (overrides or {}).items():
        if not isinstance(ov, dict):
            continue
        key = str(raw_key).strip()
        is_sub = "#" in key or bool(ov.get("is_building_override"))
        hcad = normalize_hcad13(ov.get("hcad_num") or key.split("#")[0])
        bid = str(ov.get("building_id") or ov.get("id") or key).strip()

        for extra_h in ov.get("suppress_shard_hcads") or []:
            eh = normalize_hcad13(extra_h)
            if eh:
                suppressed_shard_hcads.add(eh)

        is_vacant = bool(ov.get("suppress_only")) or is_demolished_vacant_marker(
            ov.get("year_built"),
            footprint_issue=str(ov.get("footprint_issue") or ""),
            footprint_notes=str(ov.get("footprint_notes") or ""),
            source_type=str(ov.get("source_type") or ""),
            source_citation=str(ov.get("source_citation") or ""),
            suppress_only=ov.get("suppress_only") if isinstance(ov.get("suppress_only"), bool) else None,
        )
        if is_vacant:
            if is_sub and not ov.get("replace_parcel_shards"):
                suppressed_entity_ids.add(key)
                if bid:
                    suppressed_entity_ids.add(bid)
            elif hcad:
                suppressed_shard_hcads.add(hcad)
            continue

        has_geom = bool(ov.get("geometry") and ov["geometry"].get("coordinates"))
        if ov.get("replace_parcel_shards") and hcad:
            suppressed_shard_hcads.add(hcad)

        if has_geom:
            if not is_sub and hcad:
                suppressed_shard_hcads.add(hcad)
            elif is_sub and bid:
                suppressed_entity_ids.add(bid)
                suppressed_entity_ids.add(key)
            standalone_overrides.append((key, ov))
        else:
            if is_sub:
                attr_only_by_id[key] = ov
                if bid:
                    attr_only_by_id[bid] = ov
            elif hcad:
                attr_only_by_hcad[hcad] = ov

    def _build_records(props: dict[str, Any], geom: dict[str, Any] | None) -> Iterator[dict[str, Any]]:
        if not geom or not geom.get("coordinates"):
            return
        if props.get("suppress_only"):
            return
        yr = normalize_year(props.get("year_built"))
        if yr < MIN_VALID_YEAR:
            return
        hcad = normalize_hcad13(props.get("hcad_num") or props.get("id"))
        hd = sanitize_atlas_text(str(props.get("historic_district") or "").strip())
        if hd in ("Outside Historic District", "Outside City District", "None", "null"):
            hd = ""
        lm_type = str(props.get("landmark_type") or "").strip()
        lm_name = sanitize_atlas_text(str(props.get("landmark_name") or "").strip())
        if lm_name.lower() in ("no", "none", "0", "false", "no designation", "n/a", "undesignated"):
            lm_name = ""
        gtype = geom.get("type")
        if gtype == "MultiPolygon":
            for idx_p, poly_coords in enumerate(geom.get("coordinates") or []):
                if poly_coords:
                    yield {
                        "id": f"{props.get('id') or hcad}#fp_{idx_p}",
                        "hcad_num": hcad,
                        "year_built": yr,
                        "historic_district": hd,
                        "landmark_type": lm_type,
                        "has_landmark": bool(lm_type or lm_name),
                        "geometry": {"type": "Polygon", "coordinates": poly_coords},
                    }
        else:
            yield {
                "id": str(props.get("id") or props.get("building_id") or hcad),
                "hcad_num": hcad,
                "year_built": yr,
                "historic_district": hd,
                "landmark_type": lm_type,
                "has_landmark": bool(lm_type or lm_name),
                "geometry": geom,
            }

    for feat in shard_features:
        p = feat.get("properties") or {}
        fid = str(p.get("id") or p.get("building_id") or "").strip()
        hcad = normalize_hcad13(p.get("hcad_num") or fid)
        if fid and fid in suppressed_entity_ids:
            continue
        if hcad and hcad in suppressed_shard_hcads:
            continue
        ov = attr_only_by_id.get(fid) or (attr_only_by_hcad.get(hcad) if hcad else None)
        if ov:
            merged = dict(p)
            for fld in ("year_built", "historic_district", "contributing", "landmark_name", "landmark_type", "use_category"):
                if ov.get(fld) not in (None, ""):
                    merged[fld] = ov[fld]
            yield from _build_records(merged, feat.get("geometry"))
        else:
            yield from _build_records(p, feat.get("geometry"))

    for key, ov in standalone_overrides:
        merged = dict(ov)
        merged.setdefault("id", key)
        yield from _build_records(merged, ov.get("geometry"))


def reconcile_standing_buildings(
    shard_features: Iterable[dict[str, Any]],
    overrides: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Materialize `iter_reconciled_standing_buildings` as a list for unit and reconciliation checks."""
    return list(iter_reconciled_standing_buildings(shard_features, overrides))


def match_ch42_protected_parcels(
    parcel_geoms_2278: Any,
    parcel_pts_2278: Any,
    parcel_areas_sqft: np.ndarray,
    is_residential_mask: np.ndarray,
    protections_2278: list[dict[str, Any]],
) -> dict[str, Any]:
    """Match unique residential parcels in EPSG:2278 against hole-aware SMLS & ribbon SMBL ordinances (DF-3).

    Dual-mode matching rules:
      - `SMLS` and full-lot `SMBL` polygons (`area / length >= 20 ft`): matches residential parcels
        whose interior point lies inside the polygon (strictly excluding interior cutout holes) OR
        whose planar overlap covers >= 35% of the parcel area (preventing buffered boundary slivers
        from spilling onto neighboring parcels).
      - Narrow front-setback `SMBL` ribbons (`area / length < 20 ft`): buffers the ribbon by 26 ft
        (~8 m) so street-frontage setback strips reliably catch the fronting blockface parcels
        without jumping across a 50-60 ft street ROW to the un-designated opposite side.

    Returns boolean numpy arrays (`smls_mask`, `smbl_mask`, `any_mask`, `expiring_mask`) whose
    protected residential counts are mathematically bounded to `[0, sum(is_residential_mask)]`.
    """
    n = len(parcel_geoms_2278)
    smls_mask = np.zeros(n, dtype=bool)
    smbl_mask = np.zeros(n, dtype=bool)
    expiring_mask = np.zeros(n, dtype=bool)

    if n == 0 or not protections_2278:
        return {
            "smls_mask": smls_mask,
            "smbl_mask": smbl_mask,
            "any_mask": smls_mask | smbl_mask,
            "expiring_mask": expiring_mask,
        }

    pt_tree = STRtree(parcel_pts_2278)
    poly_tree = STRtree(parcel_geoms_2278)

    for prot in protections_2278:
        ptype = prot.get("protection_type")
        if ptype not in ("smls", "smbl"):
            continue
        status = prot.get("sunset_status") or "unknown"
        if status in ("pending", "inactive", "expired"):
            continue
        geom = prot.get("geom_2278")
        if geom is None or geom.is_empty:
            continue

        perim = max(float(geom.length), 1.0)
        thickness_ratio = float(geom.area) / perim
        is_ribbon = ptype == "smbl" and thickness_ratio < RIBBON_SMBL_RATIO_THRESHOLD_FT

        matched_idx: set[int] = set()
        if is_ribbon:
            buffered = geom.buffer(RIBBON_SMBL_BUFFER_FT)
            cand_idx = poly_tree.query(buffered, predicate="intersects")
            for idx_raw in cand_idx:
                i = int(idx_raw)
                if is_residential_mask[i]:
                    matched_idx.add(i)
        else:
            pt_hits = pt_tree.query(geom, predicate="intersects")
            for idx_raw in pt_hits:
                i = int(idx_raw)
                if is_residential_mask[i]:
                    matched_idx.add(i)
            # Catch split/irregular lots whose interior point sits outside a partial boundary
            # while requiring >= 35% true area overlap so neighbor slivers never spill over.
            poly_hits = poly_tree.query(geom, predicate="intersects")
            for idx_raw in poly_hits:
                i = int(idx_raw)
                if not is_residential_mask[i] or i in matched_idx:
                    continue
                p_area = float(parcel_areas_sqft[i])
                if p_area <= 0.0:
                    continue
                try:
                    ov_area = float(shapely.area(shapely.intersection(parcel_geoms_2278[i], geom)))
                except Exception:
                    ov_area = 0.0
                if ov_area >= 0.35 * p_area:
                    matched_idx.add(i)

        if matched_idx:
            arr_idx = np.fromiter(matched_idx, dtype=np.int64)
            if ptype == "smls":
                smls_mask[arr_idx] = True
            else:
                smbl_mask[arr_idx] = True
            if status == "expiring_soon":
                expiring_mask[arr_idx] = True

    any_mask = smls_mask | smbl_mask
    return {
        "smls_mask": smls_mask,
        "smbl_mask": smbl_mask,
        "any_mask": any_mask,
        "expiring_mask": expiring_mask,
    }


def fetch_and_cache_council_districts(cache_path: Path) -> list[dict[str, Any]]:
    """Fetch or read cached COH 2024 City Council Districts and attach verified 2024-2028 roster."""
    if not cache_path.exists():
        print(f"   Fetching COH City Council Districts (2024) -> {cache_path.name}...")
        req = urllib.request.Request(
            COUNCIL_GEOJSON_URL,
            headers={"User-Agent": "HoustonBuildingAtlas/1.0"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw_bytes = resp.read()
        cache_path.write_bytes(raw_bytes)

    raw_fc = json.loads(cache_path.read_text(encoding="utf-8"))
    features_out: list[dict[str, Any]] = []
    seen_letters: set[str] = set()

    for feat in raw_fc.get("features", []):
        props = feat.get("properties") or {}
        geom_raw = feat.get("geometry")
        letter = str(props.get("DISTRICT") or "").strip().upper()
        if not letter or letter not in COH_COUNCIL_ROSTER_2024_2028 or not geom_raw:
            continue
        if letter in seen_letters:
            continue
        seen_letters.add(letter)

        sg = shape(geom_raw)
        if not sg.is_valid:
            sg = make_valid(sg)
        sg_simp = sg.simplify(0.00004, preserve_topology=True)
        # Snap to the same 1e-5 (~1.1 m) grid used by round_coords(..., 5) so
        # narrow COH highway/waterway annexation ribbons cannot self-intersect
        # or collapse into <4-point degenerate rings after rounding (DF-5).
        sg_grid = shapely.set_precision(sg_simp, grid_size=1e-5)
        if not sg_grid.is_valid:
            sg_grid = shapely.set_precision(make_valid(sg_grid), grid_size=1e-5)
        poly_parts: list[Polygon] = []
        for part in getattr(sg_grid, "geoms", (sg_grid,)):
            if isinstance(part, Polygon) and not part.is_empty:
                poly_parts.append(part)
            elif isinstance(part, MultiPolygon) and not part.is_empty:
                poly_parts.extend(p for p in part.geoms if not p.is_empty)
        if not poly_parts:
            continue
        sg_clean = poly_parts[0] if len(poly_parts) == 1 else MultiPolygon(poly_parts)
        rep = sg_clean.representative_point()
        roster = COH_COUNCIL_ROSTER_2024_2028[letter]

        clean_props = {
            "id": f"council_dist_{letter.lower()}",
            "district_letter": letter,
            "district_code": letter,
            "name": f"City Council District {letter}",
            "type": "City of Houston Council District",
            "council_member": roster["member"],
            "council_phone": roster["phone"],
            "council_email": roster["email"],
            "council_url": roster["url"],
            "council_term_expires": COUNCIL_TERM_EXPIRES,
            "label_lng": round(float(rep.x), 5),
            "label_lat": round(float(rep.y), 5),
            "description": sanitize_atlas_text(
                f"Houston City Council District {letter} (Council Member {roster['member']}, "
                f"2024-2028 term). City Council votes on all Chapter 33 Historic Districts, "
                f"Protected Landmark designations, and Chapter 42 Special Minimum Lot Size / "
                f"Building Line ordinances."
            ),
            "citations": [
                {
                    "label": f"COH Council District {letter} Office ({roster['member']})",
                    "url": roster["url"],
                }
            ],
        }
        features_out.append({
            "type": "Feature",
            "geometry": round_coords(mapping(sg_clean), 5),
            "properties": clean_props,
        })

    features_out.sort(key=lambda f: f["properties"]["district_letter"])
    if len(features_out) != 11:
        raise RuntimeError(f"Expected 11 COH Council Districts (A-K), found {len(features_out)}")
    return features_out


def hydrate_land_use_protections_lifecycle(overlays: dict[str, Any], *, ref_year: int = MAX_VALID_CAMA_DATE_YEAR) -> dict[str, int]:
    """Hydrate every `overlays['land_use_protections']` feature with Ch. 42 statutory sunset metadata (DF-1)."""
    fc = overlays.get("land_use_protections") or {}
    feats = fc.get("features") or []
    counts = Counter()
    for feat in feats:
        p = feat.get("properties")
        if not isinstance(p, dict):
            continue
        ptype = str(p.get("protection_type") or "").strip().lower()
        # Preserve original raw GIS expiration in `gis_expiration_date` on first run so reruns stay idempotent!
        if "gis_expiration_date" not in p:
            raw_exp = str(p.get("expiration_date") or "").strip()
            p["gis_expiration_date"] = "" if p.get("is_estimated_sunset") else raw_exp
        lifecycle = resolve_ch42_ordinance_lifecycle(
            ordinance=p.get("ordinance", ""),
            effective_date=p.get("effective_date", ""),
            expiration_date=p.get("gis_expiration_date", ""),
            status=p.get("status", "Approved"),
            protection_type=ptype,
            ref_year=ref_year,
        )
        p["expiration_date"] = lifecycle["expiration_date"]
        p["expiration_year"] = lifecycle["expiration_year"]
        p["expiration_label"] = lifecycle["expiration_label"]
        p["statutory_term_years"] = lifecycle["statutory_term_years"]
        p["is_estimated_sunset"] = lifecycle["is_estimated_sunset"]
        p["sunset_status"] = lifecycle["sunset_status"]
        counts[f"{ptype}:{lifecycle['sunset_status']}"] += 1
        if lifecycle["is_estimated_sunset"]:
            counts[f"{ptype}:estimated"] += 1
    return dict(counts)


def build_council_search_entries(council_features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build search_index.json entries for all 11 City Council Districts."""
    entries: list[dict[str, Any]] = []
    for feat in council_features:
        p = feat["properties"]
        geom = shape(feat["geometry"])
        letter = p["district_letter"]
        member = p["council_member"]
        bcount = int(p.get("building_count") or 0)
        pre40 = int(p.get("pre_1940_count") or 0)
        med = p.get("median_year") or "N/A"
        entries.append({
            "id": p["id"],
            "is_neighborhood_entry": True,
            "place_type": "City Council District",
            "overlay_layer": "councilDistricts",
            "name": p["name"],
            "building_name": f"{p['name']} ({member})",
            "label": f"{p['name']} ({member})",
            "alt_names": [
                f"District {letter}",
                f"Council District {letter}",
                f"Houston Council District {letter}",
                member,
            ],
            "address": sanitize_atlas_text(
                f"Houston City Council District {letter} · CM {member} · "
                f"{bcount:,} structures ({pre40:,} pre-1940) · Median {med}"
            ),
            "sublabel": sanitize_atlas_text(
                f"City Council District · CM {member} · {bcount:,} structures"
            ),
            "category": "Council District",
            "year_built": int(p.get("median_year") or 1960),
            "earliest_year": int(p.get("earliest_year") or 0),
            "building_count": bcount,
            "pre_1940_count": pre40,
            "lng": p["label_lng"],
            "lon": p["label_lng"],
            "lat": p["label_lat"],
            "bbox": [round(float(v), 5) for v in geom.bounds],
            "zoom": 11.8,
        })
    return entries


def upsert_council_districts_in_overlays_and_search(
    overlays: dict[str, Any],
    search_index_list: list[dict[str, Any]],
    council_features: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Idempotently upsert `overlays['council_districts']` and `council_dist_*` in search index (DF-7)."""
    overlays["council_districts"] = {
        "type": "FeatureCollection",
        "features": council_features,
    }
    council_entries = build_council_search_entries(council_features)
    return upsert_search_index_slice(
        search_index_list,
        slice_predicate=lambda it: (
            is_boundary_search_entry(it)
            and (
                str(it.get("overlay_layer") or "") == "councilDistricts"
                or str(it.get("id") or "").startswith("council_dist_")
            )
        ),
        new_entries=council_entries,
        prepend=True,
    )


def _make_ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _query_coh_webfocus_window(
    seltd: str,
    srh: str,
    bdt: datetime.date,
    edt: datetime.date,
    ptype: str,
    ssl_ctx: ssl.SSLContext,
) -> list[dict[str, str]]:
    params = urllib.parse.urlencode({
        "IBIF_ex": "online_per_se.fex",
        "IBIAPP_app": "soldpermits",
        "SELTD": seltd,
        "SRH": srh,
        "BDT": bdt.strftime("%Y/%m/%d"),
        "EDT": edt.strftime("%Y/%m/%d"),
        "PTYPE": ptype,
    })
    url = f"{COH_WFSERVLET_URL}?{params}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, context=ssl_ctx, timeout=20) as r:
            res = r.read().decode("utf-8", errors="ignore")
    except Exception:
        return []
    m_ar = re.search(r"ARstrings=(\[\x27Search.*?\]);\s*T_cont", res, re.S)
    m_tc = re.search(r"T_cont\[NumOfTable\]\s*=\s*(\[\[\[.*?\]);", res, re.S)
    if not m_ar or not m_tc:
        return []
    try:
        ar_strings = ast.literal_eval(m_ar.group(1))
        t_cont = ast.literal_eval(m_tc.group(1))
    except Exception:
        return []
    out: list[dict[str, str]] = []
    for row in t_cont:
        if len(row) < 7:
            continue
        vals = [str(ar_strings[cell[0]]).strip() for cell in row[:7]]
        proj_no, p_desc, owner_occ, addr, proj_desc, val, p_type = vals
        out.append({
            "project_no": proj_no,
            "permit_desc": p_desc,
            "owner_occupant": owner_occ,
            "raw_address": addr,
            "use_desc": proj_desc,
            "valuation": val,
            "permit_type": p_type,
        })
    return out


def fetch_or_load_coh_2026_demo_permits(cache_path: Path) -> list[dict[str, str]]:
    """Fetch and cache 2026 YTD City of Houston structural demolition permits (`SD` / `4D`)."""
    if cache_path.exists():
        try:
            doc = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(doc, dict) and isinstance(doc.get("permits"), list) and doc["permits"]:
                valid_rows = [
                    r for r in doc["permits"]
                    if str(r.get("permit_type") or "").strip().upper() in ("SD", "4D")
                ]
                if valid_rows and len(valid_rows) == len(doc["permits"]):
                    return valid_rows
        except Exception:
            pass

    print("   Fetching 2026 YTD COH structural demolition permits across 4-day slices...")
    ssl_ctx = _make_ssl_ctx()
    start_dt = datetime.date(2026, 1, 1)
    end_dt = min(datetime.date.today(), datetime.date(2026, 12, 31))
    slices: list[tuple[datetime.date, datetime.date]] = []
    cur = start_dt
    step = datetime.timedelta(days=4)
    while cur <= end_dt:
        e_cur = min(end_dt, cur + step)
        slices.append((cur, e_cur))
        cur = e_cur + datetime.timedelta(days=1)

    tasks: list[tuple[str, str, datetime.date, datetime.date, str]] = []
    for bdt, edt in slices:
        tasks.append(("BU", "DEMO", bdt, edt, "ALL"))
        tasks.append(("PT", " ", bdt, edt, "SD"))

    dedup: dict[tuple[str, str, str], dict[str, str]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        futs = [pool.submit(_query_coh_webfocus_window, *t, ssl_ctx) for t in tasks]
        for fut in concurrent.futures.as_completed(futs):
            for r in fut.result():
                ptype_up = str(r.get("permit_type") or "").strip().upper()
                if ptype_up not in ("SD", "4D"):
                    continue
                key = (r["project_no"], ptype_up, r.get("raw_address", ""))
                dedup[key] = r

    permits_list = list(dedup.values())
    if permits_list:
        cache_path.write_text(
            json.dumps(
                {
                    "fetched_through": end_dt.isoformat(),
                    "count": len(permits_list),
                    "permits": permits_list,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    print(f"   Cached {len(permits_list):,} COH 2026 YTD SD/4D permit records.")
    return permits_list


def _to_2278_shapely(geom_wgs84: Any, transformer: Transformer) -> Any:
    return shapely.transform(
        geom_wgs84,
        lambda xy: np.column_stack(transformer.transform(xy[:, 0], xy[:, 1])),
    )


def main() -> None:
    t0 = time.time()
    print("=== Building Houston Neighborhood & Council District Preservation Scorecards ===")

    overlays_path = DATA_DIR / "overlays.json"
    search_index_path = DATA_DIR / "search_index.json"
    overrides_path = DATA_DIR / "curated_overrides.json"
    scorecards_out_path = DATA_DIR / "neighborhood_scorecards.json"

    print("1. Loading overlays.json, search_index.json, and curated_overrides.json...")
    overlays = json.loads(overlays_path.read_text(encoding="utf-8"))
    search_index = json.loads(search_index_path.read_text(encoding="utf-8"))
    overrides = json.loads(overrides_path.read_text(encoding="utf-8"))

    lifecycle_counts = hydrate_land_use_protections_lifecycle(overlays)
    print(f"   Hydrated land_use_protections lifecycle stats: {lifecycle_counts}")

    council_cache_path = CACHE_DIR / "coh_council_districts_2024.geojson"
    council_features = fetch_and_cache_council_districts(council_cache_path)
    print(f"   Loaded {len(council_features)} COH Council Districts (A-K).")

    to_2278 = Transformer.from_crs("EPSG:4326", "EPSG:2278", always_xy=True)

    # 2. Build StandingBuildingIndex across all 5 countywide shards reconciled with curated_overrides.json
    print("2. Building StandingBuildingIndex across 5 countywide shards + curated_overrides.json...")
    shard_paths = sorted(glob.glob(str(CACHE_DIR / "aligned_ndjson" / "buildings_*.geojsonseq")))
    if not shard_paths:
        raise RuntimeError("Missing aligned_ndjson building shards in pipeline/cache/aligned_ndjson/")

    # Stream shard records cleanly via iter_reconciled_standing_buildings without keeping polygon rings in RAM
    bldg_xs: list[float] = []
    bldg_ys: list[float] = []
    bldg_years_list: list[int] = []
    bldg_hcads_set_pre40: set[str] = set()
    hcad_earliest_year: dict[str, int] = {}

    def _centroid_xy(geom: dict[str, Any] | None) -> list[tuple[float, float]]:
        if not geom:
            return []
        gtype = geom.get("type")
        coords = geom.get("coordinates") or []
        if gtype == "Polygon" and coords and coords[0]:
            ring = coords[0]
            n_pts = len(ring)
            if n_pts == 0:
                return []
            sx = sum(pt[0] for pt in ring)
            sy = sum(pt[1] for pt in ring)
            return [(sx / n_pts, sy / n_pts)]
        if gtype == "MultiPolygon" and coords:
            pts_out: list[tuple[float, float]] = []
            for poly in coords:
                if poly and poly[0]:
                    ring = poly[0]
                    n_pts = len(ring)
                    pts_out.append((sum(pt[0] for pt in ring) / n_pts, sum(pt[1] for pt in ring) / n_pts))
            return pts_out
        if gtype == "Point" and len(coords) >= 2:
            return [(float(coords[0]), float(coords[1]))]
        return []

    def _iter_raw_shard_features() -> Iterator[dict[str, Any]]:
        for sp in shard_paths:
            with open(sp, "r", encoding="utf-8") as fh:
                for line in fh:
                    s_ln = line.strip().lstrip("\x1e")
                    if s_ln:
                        yield json.loads(s_ln)

    for rec in iter_reconciled_standing_buildings(_iter_raw_shard_features(), overrides):
        yr = rec["year_built"]
        hcad = rec["hcad_num"]
        for cx, cy in _centroid_xy(rec["geometry"]):
            bldg_xs.append(cx)
            bldg_ys.append(cy)
            bldg_years_list.append(yr)
        if hcad:
            prev_yr = hcad_earliest_year.get(hcad)
            if prev_yr is None or yr < prev_yr:
                hcad_earliest_year[hcad] = yr
            if yr < 1940:
                bldg_hcads_set_pre40.add(hcad)

    bldg_years = np.asarray(bldg_years_list, dtype=np.int32)
    bldg_pts_wgs84 = shapely.points(
        np.asarray(bldg_xs, dtype=np.float64),
        np.asarray(bldg_ys, dtype=np.float64),
    )
    bldg_tree = STRtree(bldg_pts_wgs84)
    print(f"   Indexed {len(bldg_years):,} standing dated footprints in {time.time() - t0:.1f}s.")

    # Compute which standing footprints lie inside an active COH Chapter 33 Historic or Heritage District
    coh_hd_geoms = []
    for k_hd in ("historic_districts", "heritage_districts"):
        for feat in overlays.get(k_hd, {}).get("features", []):
            g = feat.get("geometry")
            if g:
                sg = shape(g)
                if not sg.is_valid:
                    sg = make_valid(sg)
                coh_hd_geoms.append(sg)
    bldg_in_coh_hd = np.zeros(len(bldg_years), dtype=bool)
    for hg in coh_hd_geoms:
        hits = bldg_tree.query(hg, predicate="intersects")
        if len(hits):
            bldg_in_coh_hd[hits] = True

    # 3. Parse HCAD CAMA real_acct.txt & permits.txt from Real_acct_owner.zip
    print("3. Parsing HCAD CAMA real_acct.txt (ownership/state_class/situs) & permits.txt (2020-2026 demos)...")
    t_cama = time.time()
    cama_zip_path = CACHE_DIR / "Real_acct_owner.zip"
    acct_flags: dict[str, int] = {}
    # Bit flags per hcad13:
    # 1 = residential (A* or B*)
    # 2 = single_family (A1)
    # 4 = situs_matched (owner occupied at situs)
    # 8 = family_trust
    # 16 = corporate
    # 32 = absentee_ind
    FLAG_RES = 1
    FLAG_SFR = 2
    FLAG_SITUS = 4
    FLAG_TRUST = 8
    FLAG_CORP = 16
    FLAG_ABS = 32

    situs_to_hcad_coh: dict[str, str] = {}
    demo_parcels_by_year: dict[int, set[str]] = {yr: set() for yr in DEMO_YEARS}
    demo_pre40_hcads: set[str] = set()

    with zipfile.ZipFile(cama_zip_path, "r") as zf:
        with zf.open("real_acct.txt") as fp:
            _hdr = fp.readline()
            for raw_line in fp:
                cols = raw_line.decode("latin1", errors="replace").rstrip("\r\n").split("\t")
                if len(cols) < 50:
                    continue
                hcad = normalize_hcad13(cols[0])
                if not hcad:
                    continue
                sc = cols[20].strip().upper()
                owner = cols[2].strip()
                mail1 = cols[3].strip()
                mail2 = cols[4].strip()
                situs1 = cols[17].strip()
                yr_impr = normalize_year(cols[33])
                if yr_impr >= MIN_VALID_YEAR:
                    prev_yr = hcad_earliest_year.get(hcad)
                    if prev_yr is None or yr_impr < prev_yr:
                        hcad_earliest_year[hcad] = yr_impr
                    if yr_impr < 1940:
                        bldg_hcads_set_pre40.add(hcad)

                is_res = sc.startswith("A") or sc.startswith("B")
                is_sfr = sc == "A1"
                flags = 0
                if is_res:
                    flags |= FLAG_RES
                    if is_sfr:
                        flags |= FLAG_SFR
                    bucket, situs_ok = classify_parcel_ownership(owner, situs1, mail1, mail2)
                    if situs_ok and bucket in ("resident", "family_trust"):
                        flags |= FLAG_SITUS
                    if bucket == "family_trust":
                        flags |= FLAG_TRUST
                    elif bucket == "corporate":
                        flags |= FLAG_CORP
                    elif bucket == "absentee":
                        flags |= FLAG_ABS
                acct_flags[hcad] = flags

                jurs = cols[70] if len(cols) > 70 else ""
                if "061" in jurs and situs1:
                    sk = normalize_situs_compare_key(situs1)
                    if sk and sk not in situs_to_hcad_coh:
                        situs_to_hcad_coh[sk] = hcad

        with zf.open("permits.txt") as fp:
            _hdr = fp.readline()
            for raw_line in fp:
                cols = raw_line.decode("latin1", errors="replace").rstrip("\r\n").split("\t")
                if len(cols) < 11:
                    continue
                parsed = parse_demolition_permit_row(
                    acct=cols[0],
                    permit_type=cols[6],
                    dscr=cols[4],
                    issue_date=cols[9],
                    roll_yr=cols[10],
                    min_year=2020,
                    max_year=MAX_VALID_CAMA_DATE_YEAR,
                )
                if parsed is not None:
                    hcad, yr = parsed
                    demo_parcels_by_year[yr].add(hcad)
                    if hcad in bldg_hcads_set_pre40:
                        demo_pre40_hcads.add(hcad)

    # Merge 2026 YTD COH structural demolition permits (`SD` / `4D`), deduplicated by `(hcad13, 2026)`
    coh_2026_cache = CACHE_DIR / "coh_2026_structural_demo_permits.json"
    coh_2026_permits = fetch_or_load_coh_2026_demo_permits(coh_2026_cache)
    coh_2026_matched = 0
    for prm in coh_2026_permits:
        ok_coh, _, _ = classify_main_structure_demolition_permit(
            prm.get("use_desc"),
            prm.get("permit_type"),
            "2026",
            raw_address=prm.get("raw_address"),
            permit_desc=prm.get("permit_desc"),
            min_year=2026,
            max_year=MAX_VALID_CAMA_DATE_YEAR,
        )
        if not ok_coh:
            continue
        sk = normalize_situs_compare_key(prm.get("raw_address", ""))
        if not sk:
            continue
        hcad = situs_to_hcad_coh.get(sk)
        if hcad:
            demo_parcels_by_year[2026].add(hcad)
            coh_2026_matched += 1
            if hcad in bldg_hcads_set_pre40:
                demo_pre40_hcads.add(hcad)

    print(
        f"   Parsed {len(acct_flags):,} HCAD accounts & demo permits in {time.time() - t_cama:.1f}s "
        f"(2026 YTD COH matched: {coh_2026_matched:,}; annual unique demo parcels: "
        f"{ {y: len(s) for y, s in demo_parcels_by_year.items()} })."
    )

    # 4. Read Parcels.gdb (EPSG:2278) -> UniqueParcelIndex + Dual-Mode Ch. 42 Protection Matching
    print("4. Reading Parcels.gdb (EPSG:2278) into UniqueParcelIndex...")
    t_pcl = time.time()
    gdb_path = str(CACHE_DIR / "Parcels" / "Parcels.gdb")
    meta, fids, geom_wkb, field_data = pyogrio.raw.read(
        gdb_path,
        layer="Parcels",
        columns=["HCAD_NUM_1", "CurrOwner", "LocAddr", "mail_addr_1", "mail_addr_2", "Shape_Area"],
        read_geometry=True,
    )
    raw_hcads = field_data[0]
    raw_owners = field_data[1]
    raw_locaddrs = field_data[2]
    raw_mail1 = field_data[3]
    raw_mail2 = field_data[4]
    raw_areas = field_data[5]

    keep_indices: list[int] = []
    seen_pcl_hcads: set[str] = set()
    uniq_hcads: list[str] = []
    uniq_flags: list[int] = []
    uniq_areas: list[float] = []

    for idx_row in range(len(raw_hcads)):
        wkb = geom_wkb[idx_row]
        if wkb is None:
            continue
        hcad = normalize_hcad13(raw_hcads[idx_row])
        if not hcad or hcad in seen_pcl_hcads:
            continue
        seen_pcl_hcads.add(hcad)
        keep_indices.append(idx_row)
        uniq_hcads.append(hcad)
        fl = acct_flags.get(hcad)
        if fl is None:
            # Fallback if parcel is in GIS but missing from real_acct.txt
            bkt, s_ok = classify_parcel_ownership(
                raw_owners[idx_row],
                raw_locaddrs[idx_row],
                raw_mail1[idx_row],
                raw_mail2[idx_row],
            )
            fl = 0
            if s_ok and bkt in ("resident", "family_trust"):
                fl |= FLAG_SITUS
            if bkt == "family_trust":
                fl |= FLAG_TRUST
            elif bkt == "corporate":
                fl |= FLAG_CORP
            elif bkt == "absentee":
                fl |= FLAG_ABS
        uniq_flags.append(fl)
        a_val = raw_areas[idx_row]
        uniq_areas.append(float(a_val) if a_val and float(a_val) > 0 else 0.0)

    del fids, raw_hcads, raw_owners, raw_locaddrs, raw_mail1, raw_mail2, raw_areas, field_data

    keep_wkb = geom_wkb[np.asarray(keep_indices, dtype=np.int64)]
    del geom_wkb
    pcl_geoms_2278 = shapely.from_wkb(keep_wkb)
    del keep_wkb
    pcl_pts_2278 = shapely.point_on_surface(pcl_geoms_2278)

    pcl_flags_arr = np.asarray(uniq_flags, dtype=np.uint8)
    pcl_areas_arr = np.asarray(uniq_areas, dtype=np.float64)
    pcl_res_mask = (pcl_flags_arr & FLAG_RES) != 0
    pcl_sfr_mask = (pcl_flags_arr & FLAG_SFR) != 0
    pcl_occ_mask = pcl_res_mask & ((pcl_flags_arr & FLAG_SITUS) != 0)
    pcl_trust_mask = pcl_res_mask & ((pcl_flags_arr & FLAG_TRUST) != 0)
    pcl_corp_mask = pcl_res_mask & ((pcl_flags_arr & FLAG_CORP) != 0)
    pcl_abs_mask = pcl_res_mask & ((pcl_flags_arr & FLAG_ABS) != 0)

    pcl_demo_masks: dict[int, np.ndarray] = {}
    for yr in DEMO_YEARS:
        yr_set = demo_parcels_by_year[yr]
        pcl_demo_masks[yr] = np.fromiter((h in yr_set for h in uniq_hcads), dtype=bool, count=len(uniq_hcads))
    all_demo_set: set[str] = set().union(*demo_parcels_by_year.values())
    pcl_demo_any_mask = np.fromiter((h in all_demo_set for h in uniq_hcads), dtype=bool, count=len(uniq_hcads))
    pcl_demo_pre40_mask = np.fromiter((h in demo_pre40_hcads for h in uniq_hcads), dtype=bool, count=len(uniq_hcads))

    print(
        f"   Loaded {len(uniq_hcads):,} unique HCAD parcel geometries in {time.time() - t_pcl:.1f}s "
        f"(residential: {int(pcl_res_mask.sum()):,}, SFR: {int(pcl_sfr_mask.sum()):,})."
    )

    # Transform land_use_protections into EPSG:2278 and compute dual-mode parcel coverage
    print("5. Matching parcels against hole-aware SMLS polygons & narrow SMBL setback ribbons (DF-3)...")
    t_prot = time.time()
    protections_2278: list[dict[str, Any]] = []
    for feat in overlays.get("land_use_protections", {}).get("features", []):
        p = feat.get("properties") or {}
        g = feat.get("geometry")
        if not g:
            continue
        try:
            sg_wgs = shape(g)
            if not sg_wgs.is_valid:
                sg_wgs = make_valid(sg_wgs)
            sg_2278 = _to_2278_shapely(sg_wgs, to_2278)
            if not sg_2278.is_valid:
                sg_2278 = make_valid(sg_2278)
            protections_2278.append({
                "protection_type": p.get("protection_type"),
                "ordinance": str(p.get("ordinance") or ""),
                "expiration_year": int(p.get("expiration_year") or 0),
                "is_estimated_sunset": bool(p.get("is_estimated_sunset")),
                "sunset_status": str(p.get("sunset_status") or "unknown"),
                "geom_wgs": sg_wgs,
                "geom_2278": sg_2278,
            })
        except Exception:
            pass

    ch42_match = match_ch42_protected_parcels(
        pcl_geoms_2278,
        pcl_pts_2278,
        pcl_areas_arr,
        pcl_res_mask,
        protections_2278,
    )
    pcl_smls_mask = ch42_match["smls_mask"]
    pcl_smbl_mask = ch42_match["smbl_mask"]
    pcl_ch42_mask = ch42_match["any_mask"]
    pcl_ch42_exp_mask = ch42_match["expiring_mask"]
    del pcl_geoms_2278  # Free polygon WKB memory now that interior points + masks are computed!

    pcl_pt_tree_2278 = STRtree(pcl_pts_2278)
    print(
        f"   Matched Ch. 42 protections in {time.time() - t_prot:.1f}s: "
        f"any={int(pcl_ch42_mask.sum()):,} res parcels (SMLS={int(pcl_smls_mask.sum()):,}, "
        f"SMBL={int(pcl_smbl_mask.sum()):,}, expiring 2026-2031={int(pcl_ch42_exp_mask.sum()):,})."
    )

    # 6. Build supplementary point spatial indices for Landmarks, Good Brick, NRHP Props, Lost Houston, Protections, Council Districts
    def _build_overlay_point_tree(layer_key: str) -> tuple[STRtree | None, list[dict[str, Any]]]:
        pts_l = []
        props_l = []
        for feat in overlays.get(layer_key, {}).get("features", []):
            g = feat.get("geometry")
            if not g:
                continue
            sg = shape(g)
            rep = sg if sg.geom_type == "Point" else sg.representative_point()
            pts_l.append(rep)
            props_l.append(feat.get("properties") or {})
        return (STRtree(pts_l) if pts_l else None), props_l

    lm_tree, lm_props = _build_overlay_point_tree("landmarks")
    gb_tree, _gb_props = _build_overlay_point_tree("good_brick_awards")
    nrp_tree, _nrp_props = _build_overlay_point_tree("nrhp_properties")
    lh_tree, _lh_props = _build_overlay_point_tree("lost_houston")

    prot_geoms_wgs = [pr["geom_wgs"] for pr in protections_2278]
    prot_tree_wgs = STRtree(prot_geoms_wgs) if prot_geoms_wgs else None

    cd_geoms_wgs = [shape(f["geometry"]) for f in council_features]
    cd_letters = [f["properties"]["district_letter"] for f in council_features]
    cd_tree_wgs = STRtree(cd_geoms_wgs)

    sn_feats = overlays.get("super_neighborhoods", {}).get("features", [])
    sn_geoms_wgs = [shape(f["geometry"]) for f in sn_feats]
    sn_names = [str(f["properties"].get("name") or "") for f in sn_feats]
    sn_tree_wgs = STRtree(sn_geoms_wgs) if sn_geoms_wgs else None

    # 7. Compute scorecards across all 9 boundary families
    boundary_families: list[tuple[str, str, list[dict[str, Any]]]] = [
        ("council_districts", "City Council District", council_features),
        ("historic_districts", "COH Historic District", overlays.get("historic_districts", {}).get("features", [])),
        ("heritage_districts", "COH Heritage District", overlays.get("heritage_districts", {}).get("features", [])),
        ("nrhp_districts", "National Register District", overlays.get("nrhp_districts", {}).get("features", [])),
        (
            "potential_historic_districts",
            "Surveyed Potential Historic District",
            overlays.get("potential_historic_districts", {}).get("features", []),
        ),
        ("super_neighborhoods", "Super Neighborhood", sn_feats),
        ("holc_1937_zones", "1937 HOLC Redlining Zone", overlays.get("holc_1937_zones", {}).get("features", [])),
        ("neighborhoods", "Neighborhood", overlays.get("neighborhoods", {}).get("features", [])),
        ("platted_subdivisions", "Platted Subdivision", overlays.get("platted_subdivisions", {}).get("features", [])),
    ]

    scorecards: list[dict[str, Any]] = []
    family_counts: dict[str, int] = {}

    for layer_key, default_type_label, feat_list in boundary_families:
        t_fam = time.time()
        kept_in_family = 0
        for feat in feat_list:
            p = feat.get("properties") or {}
            geom_raw = feat.get("geometry")
            if not geom_raw:
                continue
            fid = str(p.get("id") or "").strip()
            name = sanitize_atlas_text(str(p.get("name") or "").strip())
            if not fid or not name:
                continue

            sg_wgs = shape(geom_raw)
            if not sg_wgs.is_valid:
                sg_wgs = make_valid(sg_wgs)
            if sg_wgs.is_empty:
                continue

            # Standing building hits (WGS84)
            if layer_key == "platted_subdivisions" and not bool(p.get("is_historic_plat")):
                continue
            b_hits = bldg_tree.query(sg_wgs, predicate="intersects")
            bc = int(len(b_hits))
            if layer_key == "platted_subdivisions" and bc < MIN_SUBDIVISION_STANDING_STRUCTURES:
                continue

            sg_2278 = _to_2278_shapely(sg_wgs, to_2278)
            if not sg_2278.is_valid:
                sg_2278 = make_valid(sg_2278)
            area_acres = round(float(sg_2278.area) / 43560.0, 2)

            if bc > 0:
                yrs_sub = bldg_years[b_hits]
                ey = int(yrs_sub.min())
                my = int(round(float(np.median(yrs_sub))))
                p20 = int(np.sum(yrs_sub < 1920))
                p40 = int(np.sum(yrs_sub < 1940))
                p60 = int(np.sum(yrs_sub < 1960))
                p75 = int(np.sum(yrs_sub < 1975))
                n20 = int(np.sum(yrs_sub >= 2020))
                hdp = int(np.sum(bldg_in_coh_hd[b_hits]))
                dec_vals = np.clip((yrs_sub // 10) * 10, DECADE_BINS[0], DECADE_BINS[-1])
                dh = [int(np.sum(dec_vals == db)) for db in DECADE_BINS]
            else:
                ey = 0
                my = 0
                p20 = p40 = p60 = p75 = n20 = hdp = 0
                dh = [0] * len(DECADE_BINS)

            # Unique parcel hits (EPSG:2278)
            p_hits = pcl_pt_tree_2278.query(sg_2278, predicate="intersects")
            pc = int(len(p_hits))
            if pc > 0:
                rpc = int(np.sum(pcl_res_mask[p_hits]))
                sfc = int(np.sum(pcl_sfr_mask[p_hits]))
                occ = int(np.sum(pcl_occ_mask[p_hits]))
                ftc = int(np.sum(pcl_trust_mask[p_hits]))
                llc = int(np.sum(pcl_corp_mask[p_hits]))
                abs_c = int(np.sum(pcl_abs_mask[p_hits]))
                chp = int(np.sum(pcl_ch42_mask[p_hits]))
                slp = int(np.sum(pcl_smls_mask[p_hits]))
                sbp = int(np.sum(pcl_smbl_mask[p_hits]))
                chx = int(np.sum(pcl_ch42_exp_mask[p_hits]))
                dm = [int(np.sum(pcl_demo_masks[yr][p_hits])) for yr in DEMO_YEARS]
                dmt = int(np.sum(pcl_demo_any_mask[p_hits]))
                dmp40 = int(np.sum(pcl_demo_pre40_mask[p_hits]))
            else:
                rpc = sfc = occ = ftc = llc = abs_c = chp = slp = sbp = chx = dmt = dmp40 = 0
                dm = [0] * len(DEMO_YEARS)

            # Designation point counts (exclude demolished former landmarks; separate Protected vs 90-day waiver vs COA-protected HD landmarks)
            lm_hits = lm_tree.query(sg_wgs, predicate="intersects") if lm_tree else []
            lm_total = 0
            plm_cnt = 0
            vlm_cnt = 0
            for li in lm_hits:
                lp = lm_props[int(li)]
                if lp.get("demolished"):
                    continue
                lm_total += 1
                tier = str(lp.get("protection_tier") or "").strip().lower()
                desig = str(lp.get("designation") or lp.get("landmark_type") or lp.get("type") or "").strip()
                if (
                    bool(lp.get("is_protected_landmark"))
                    or tier == "plm"
                    or "protected" in desig.lower()
                    or desig == "PLM"
                ):
                    plm_cnt += 1
                elif tier == "lm_waiver_vulnerable" or (not tier and not lp.get("in_historic_district")):
                    vlm_cnt += 1
            gb_cnt = int(len(gb_tree.query(sg_wgs, predicate="intersects"))) if gb_tree else 0
            nrp_cnt = int(len(nrp_tree.query(sg_wgs, predicate="intersects"))) if nrp_tree else 0
            lh_cnt = int(len(lh_tree.query(sg_wgs, predicate="intersects"))) if lh_tree else 0

            # Intersecting enacted Ch. 42 & Conservation District ordinances
            smls_ords: set[str] = set()
            smbl_ords: set[str] = set()
            cons_cnt = 0
            expiring_list: list[dict[str, Any]] = []
            seen_exp_keys: set[tuple[str, str]] = set()
            if prot_tree_wgs is not None:
                pr_hits = prot_tree_wgs.query(sg_wgs, predicate="intersects")
                for pri in pr_hits:
                    pr = protections_2278[int(pri)]
                    try:
                        inter_a = float(shapely.area(shapely.intersection(sg_2278, pr["geom_2278"])))
                    except Exception:
                        inter_a = 0.0
                    if inter_a < 250.0:  # ignore tiny < 250 sq ft boundary touch slivers
                        continue
                    pt = pr["protection_type"]
                    st = pr["sunset_status"]
                    if pt == "conservation":
                        cons_cnt += 1
                        continue
                    if st in ("pending", "inactive", "expired"):
                        continue
                    ord_id = pr["ordinance"] or f"idx_{int(pri)}"
                    if pt == "smls":
                        smls_ords.add(ord_id)
                    elif pt == "smbl":
                        smbl_ords.add(ord_id)
                    if st == "expiring_soon":
                        ek = (pt, ord_id)
                        if ek not in seen_exp_keys:
                            seen_exp_keys.add(ek)
                            expiring_list.append({
                                "o": pr["ordinance"] or "Unnumbered",
                                "t": pt.upper(),
                                "y": pr["expiration_year"],
                                "est": bool(pr["is_estimated_sunset"]),
                            })
            expiring_list.sort(key=lambda x: (x["y"], x["t"], x["o"]))

            # Overlapping Council District(s) & Super Neighborhood
            rep_pt = sg_wgs.representative_point()
            cx = round(float(p.get("label_lng") if p.get("label_lng") is not None else rep_pt.x), 5)
            cy = round(float(p.get("label_lat") if p.get("label_lat") is not None else rep_pt.y), 5)
            bbox = [round(float(v), 5) for v in sg_wgs.bounds]

            if layer_key == "council_districts":
                cd_str = str(p["district_letter"])
                # Backfill standing-building metrics directly onto overlays["council_districts"] feature props!
                p["building_count"] = bc
                p["earliest_year"] = ey
                p["median_year"] = my
                p["pre_1940_count"] = p40
                p["pre_1960_count"] = p60
                p["landmark_count"] = lm_total
                p["plm_count"] = plm_cnt
                p["good_brick_count"] = gb_cnt
                p["smls_block_count"] = len(smls_ords)
                p["smbl_block_count"] = len(smbl_ords)
                p["expiring_ch42_count"] = len(expiring_list)
                p["area_acres"] = area_acres
                p["area_sq_mi"] = round(area_acres / 640.0, 2)
                p["ch42_protected_count"] = chp
                p["ch42_protected_pct"] = round((chp * 100.0 / rpc), 1) if rpc > 0 else 0.0
            else:
                cd_hits = cd_tree_wgs.query(sg_wgs, predicate="intersects")
                hit_letters: list[str] = []
                for cdi in cd_hits:
                    idx_cd = int(cdi)
                    try:
                        ov_a = float(sg_wgs.intersection(cd_geoms_wgs[idx_cd]).area)
                    except Exception:
                        ov_a = 0.0
                    if ov_a >= max(1e-8, float(sg_wgs.area) * 0.03):
                        hit_letters.append(cd_letters[idx_cd])
                if not hit_letters:
                    for cdi in cd_tree_wgs.query(rep_pt, predicate="intersects"):
                        hit_letters.append(cd_letters[int(cdi)])
                cd_str = ", ".join(sorted(set(hit_letters)))

            sn_name = sanitize_atlas_text(str(p.get("super_neighborhood") or "").strip())
            if not sn_name and layer_key == "holc_1937_zones":
                sns = p.get("super_neighborhoods") or []
                if sns:
                    sn_name = sanitize_atlas_text(", ".join(str(x) for x in sns[:2]))
            if not sn_name and layer_key != "super_neighborhoods" and sn_tree_wgs is not None:
                for sni in sn_tree_wgs.query(rep_pt, predicate="intersects"):
                    sn_name = sanitize_atlas_text(sn_names[int(sni)])
                    break

            rec: dict[str, Any] = {
                "id": fid,
                "ly": layer_key,
                "nm": name,
                "tp": sanitize_atlas_text(str(p.get("type") or p.get("tier") or default_type_label)),
                "cx": cx,
                "cy": cy,
                "bb": bbox,
                "ac": area_acres,
                "bc": bc,
                "ey": ey,
                "my": my,
                "p20": p20,
                "p40": p40,
                "p60": p60,
                "p75": p75,
                "n20": n20,
                "dh": dh,
                "lm": lm_total,
                "plm": plm_cnt,
                "vlm": vlm_cnt,
                "gb": gb_cnt,
                "nrp": nrp_cnt,
                "lhc": lh_cnt,
                "pc": pc,
                "rpc": rpc,
                "sfc": sfc,
                "occ": occ,
                "ftc": ftc,
                "llc": llc,
                "abs": abs_c,
                "chp": chp,
                "slp": slp,
                "sbp": sbp,
                "chx": chx,
                "slo": len(smls_ords),
                "sbo": len(smbl_ords),
                "exo": expiring_list[:16],
                "cdo": cons_cnt,
                "hdp": hdp,
                "dm": dm,
                "dmt": dmt,
                "dmp40": dmp40,
            }
            if sn_name:
                rec["sn"] = sn_name
            if cd_str:
                rec["cd"] = cd_str
            civic = sanitize_atlas_text(str(p.get("civic_club") or "").strip())
            if civic:
                rec["cc"] = civic

            if layer_key == "council_districts":
                rec["cm"] = {
                    "letter": p["district_letter"],
                    "member": p["council_member"],
                    "phone": p["council_phone"],
                    "email": p["council_email"],
                    "url": p["council_url"],
                    "term_expires": COUNCIL_TERM_EXPIRES,
                }
            elif layer_key == "holc_1937_zones":
                rec["hl"] = {
                    "grade": str(p.get("holc_grade") or ""),
                    "code": str(p.get("zone_code") or ""),
                    "temp_f": p.get("af_temp_f"),
                    "delta_vs_a": p.get("af_temp_delta_vs_a"),
                }
            elif layer_key == "platted_subdivisions":
                rec["pl"] = {
                    "year_label": sanitize_atlas_text(str(p.get("plat_year_label") or "")),
                    "citation": sanitize_atlas_text(str(p.get("plat_citation") or "")),
                    "deed_citation": sanitize_atlas_text(str(p.get("deed_citation") or "")),
                    "has_deed_docs": bool(p.get("has_deed_docs")),
                }
            elif layer_key == "potential_historic_districts" and p.get("already_designated_coh_hd"):
                rec["ad"] = True

            # Omit sparse zero fields to keep payload cleanly under 2.5 MB;
            # decodeScorecardRecord in app/js/preservationOrganizer.js restores defaults.
            for zk in SPARSE_ZERO_SCORECARD_KEYS:
                if not rec.get(zk):
                    rec.pop(zk, None)
            if not rec.get("exo"):
                rec.pop("exo", None)
            if not any(rec.get("dm") or ()):
                rec.pop("dm", None)

            scorecards.append(rec)
            kept_in_family += 1

        family_counts[layer_key] = kept_in_family
        print(f"   [{layer_key}] computed {kept_in_family:,} scorecards in {time.time() - t_fam:.1f}s")

    # 8. Upsert council_districts into overlays.json and search_index.json AFTER backfilling council feature stats
    updated_search_index = upsert_council_districts_in_overlays_and_search(
        overlays,
        search_index,
        council_features,
    )

    write_compact_json(overlays_path, overlays)
    write_compact_json(search_index_path, updated_search_index)
    print(
        f"   Updated {overlays_path.name} ({overlays_path.stat().st_size / 1e6:.2f} MB) and "
        f"{search_index_path.name} ({len(updated_search_index):,} entries, {search_index_path.stat().st_size / 1e6:.2f} MB)."
    )

    payload = {
        "schema_version": SCORECARD_SCHEMA_VERSION,
        "generated_at": datetime.date.today().isoformat(),
        "council_term_expires": COUNCIL_TERM_EXPIRES,
        "min_subdivision_standing_structures": MIN_SUBDIVISION_STANDING_STRUCTURES,
        "decade_bins": list(DECADE_BINS),
        "demo_years": list(DEMO_YEARS),
        "demo_2026_is_ytd": True,
        "family_counts": family_counts,
        "field_legend": SCORECARD_FIELD_LEGEND,
        "council_roster": COH_COUNCIL_ROSTER_2024_2028,
        "scorecards": scorecards,
    }
    write_compact_json(scorecards_out_path, payload)
    out_mb = scorecards_out_path.stat().st_size / (1024 * 1024)
    print(
        f"=== Wrote {len(scorecards):,} scorecards to {scorecards_out_path.name} "
        f"({out_mb:.2f} MB) in {time.time() - t0:.1f}s ==="
    )
    if out_mb >= 2.5:
        raise RuntimeError(f"neighborhood_scorecards.json exceeded 2.5 MB budget ({out_mb:.2f} MB)")


if __name__ == "__main__":
    main()
