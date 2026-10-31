#!/usr/bin/env python3
"""
Deterministic Cross-Dataset Historical Contradiction & Hygiene Audit Engine (Phase B).

Scans and reconciles historical claims across:
  - app/public/data/haif_index.json (HAIF building-name hygiene & replat-broadcast scrubbing)
  - pipeline/cache/coh_landmarks.json & coh_landmarks_enriched.json (GIS vs. HAHC PDF verification & demolished landmarks)
  - app/public/data/overlays.json (landmarks, good_brick_awards, lost_houston, nrhp_properties, thc_markers)
  - app/public/data/curated_overrides.json
  - app/public/data/buildings.geojson
  - app/public/data/search_index.json
  - app/public/data/citydir_index.json (1866-1926 Morrison & Fourmy City Directory occupancy bounds)

Usage:
  python3 pipeline/atlas_pipeline/audit_historical_accuracy.py --dry-run
  python3 pipeline/atlas_pipeline/audit_historical_accuracy.py --apply
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from shapely.geometry import Polygon, shape

from atlas_pipeline.lost_houston_expansion import (
    COH_DEMOLISHED_LANDMARKS_METADATA,
    COH_LANDMARK_ENRICHMENTS_ON_CURATED,
    NRHP_DEMOLISHED_METADATA,
    get_all_coh_demolished_landmarks_meta,
    sanitize_coh_landmarks_overlay,
)
from atlas_pipeline.schema import (
    HARRIS_COUNTY_BBOX,
    LOST_HOUSTON_VALID_COHORTS,
    MAX_VALID_CAMA_DATE_YEAR,
    MIN_VALID_YEAR,
    sanitize_atlas_text,
    write_compact_json,
)

ROOT_DIR = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT_DIR / "pipeline" / "cache"
DATA_DIR = ROOT_DIR / "app" / "public" / "data"

VALID_SANBORN_MATERIALS: frozenset[str] = frozenset(
    {
        "wood_frame",
        "frame",
        "brick",
        "stone",
        "stone_concrete",
        "iron_metal",
        "iron_steel",
        "iron",
        "fireproof",
    }
)


def has_excess_ring_precision(ring_coords: list[Any], max_decimals: int = 6) -> bool:
    """Return True if any vertex lon/lat in `ring_coords` exceeds `max_decimals` decimal places."""
    return any(
        "." in str(coord) and len(str(coord).split(".", 1)[1]) > max_decimals
        for pt in ring_coords
        for coord in pt[:2]
    )

# ---------------------------------------------------------------------------
# 1. HAIF Automated Building Name Hygiene Filter
# ---------------------------------------------------------------------------
POLLUTED_HAIF_SUBSTRING_RE = re.compile(
    r"(\bfor\s+sale\b|\bfor\s+lease\b|\bacres?\s+of\b|\b\d+\s+acres?\b"
    r"|\bsite\s+for\s+sale\b|\bland\s+for\s+sale\b|\blot\s+for\s+sale\b"
    r"|\bsurface\s+parking\b|\bparking\s+lot\b|\bvacant\s+lot\b"
    r"|\bproposed\b|\brumor\b|\bdemolished\b|\bteardown\b|\bretailoffice\s+remodel\b"
    r"|\bin\s+mineral\s+wells\b|\bin\s+galveston\b|\bin\s+dallas\b|\bin\s+austin\b)",
    re.IGNORECASE,
)

POLLUTED_HAIF_EXACT_RE = re.compile(
    r"^(condominiums?|apartments?|apartment\s+building(\s+with\s+retail)?|townhomes?"
    r"|gas\s+station|office\s+building|medical\s+office\s+building|retail\s+center|retail\s+building"
    r"|shopping\s+center|warehouse|new\s+development|mixed\s+use(\s+development)?|hotel|motel"
    r"|residential\s+development|commercial\s+building|industrial\s+building|single\s+family\s+homes?"
    r"|urban\s+office|yellow\s+house|bluish\s+purple\s+house"
    r"|\d+\s+(homes?|houses?|historic\s+houses?|demolished\s+homes?|commercial\s+buildings?|units?|townhomes?)"
    r"|(eight|thirty|two|three|four|five|six|seven|nine|ten)\s+homes?\s+development)$",
    re.IGNORECASE,
)

# Former demolished single-structure HAIF thread titles broadcast across >=10 replatted modern lots
REPLATTED_FORMER_SITE_BROADCAST_NAMES = {
    "north shepherd bowling lanes",
    "gene tierney home",
}


def is_polluted_haif_building_name(name: str | None, parcel_fanout: int = 1) -> tuple[bool, str]:
    """Return (is_polluted, reason) for an unverified HAIF-extracted building name."""
    raw = str(name or "").strip()
    if not raw:
        return True, "empty"
    low = raw.lower()
    if low in REPLATTED_FORMER_SITE_BROADCAST_NAMES or parcel_fanout >= 10:
        return True, f"replatted_former_site_broadcast(fanout={parcel_fanout})"
    if POLLUTED_HAIF_SUBSTRING_RE.search(raw):
        return True, "listing_parking_proposed_or_non_houston"
    if POLLUTED_HAIF_EXACT_RE.match(raw):
        return True, "generic_building_type_or_count"
    return False, ""


# ---------------------------------------------------------------------------
# 2. Verified Houston Architect Active Career Windows (for anachronism checks)
# ---------------------------------------------------------------------------
ARCHITECT_CAREER_WINDOWS: dict[str, tuple[int, int, str]] = {
    "george e. dickey": (1865, 1906, "George E. Dickey (b. 1840, active in Houston 1878-1906)"),
    "eugene t. heiner": (1872, 1902, "Eugene T. Heiner (1852-1901)"),
    "olle j. lorehn": (1886, 1940, "Olle J. Lorehn (1864-1939)"),
    "alfred c. finn": (1904, 1965, "Alfred C. Finn (1883-1964)"),
    "alfred finn": (1904, 1965, "Alfred C. Finn (1883-1964)"),
    "john staub": (1916, 1982, "John F. Staub (1892-1981)"),
    "john f. staub": (1916, 1982, "John F. Staub (1892-1981)"),
    "birdsall p. briscoe": (1904, 1972, "Birdsall P. Briscoe (1876-1971)"),
    "birdsall briscoe": (1904, 1972, "Birdsall P. Briscoe (1876-1971)"),
    "william ward watkin": (1908, 1954, "William Ward Watkin (1886-1952)"),
    "joseph finger": (1908, 1955, "Joseph Finger (1887-1953)"),
    "john s. chase": (1950, 2013, "John S. Chase (1925-2012)"),
    "lamar q. cato": (1912, 1962, "Lamar Q. Cato (1888-1960)"),
    "mackie & kamrath": (1937, 1988, "MacKie & Kamrath (est. 1937)"),
    "sanguinet & staats": (1900, 1928, "Sanguinet & Staats (Houston practice c. 1903-1926)"),
    "philip johnson": (1942, 2006, "Philip Johnson (1906-2005)"),
    "cameron fairchild": (1922, 1986, "Cameron D. Fairchild (1899-1985)"),
    "james ruskin bailey": (1906, 1956, "James Ruskin Bailey (1882-1954)"),
    "charles oliver": (1912, 1972, "Charles W. Oliver (1888-1971)"),
    "charles w. oliver": (1912, 1972, "Charles W. Oliver (1888-1971)"),
    "stayton nunn": (1918, 1975, "Stayton Nunn (active 1920s-1960s)"),
    "kenneth franzheim": (1915, 1961, "Kenneth Franzheim (1890-1959)"),
    "maurice j. sullivan": (1908, 1963, "Maurice J. Sullivan (1884-1961)"),
}


# ---------------------------------------------------------------------------
# 3. Authoritative COH Landmark Designation Report PDF Fixes & Promotions
#    (Each verified directly against cached HAHC PDF text in landmark_pdf_texts.json)
# ---------------------------------------------------------------------------
VERIFIED_COH_LANDMARK_FIXES: dict[str, dict] = {
    # 07L189: COH GIS table had a row-shift ("1889 / George E. Dickey"). HAHC Report 07L189 proves
    # Stayton Nunn designed 2128 Brentwood Dr in 1936 (completed March 1937) for Col. W. B. Bates.
    "0601550470017": {
        "code": "07L189",
        "name": "Colonel W. B. Bates House",
        "address": "2128 Brentwood Dr",
        "year_built": 1936,
        "architect": "Stayton Nunn",
        "style": "Georgian Revival",
        "report_url": "https://www.houstontx.gov/planning/HistoricPres/landmarks/07L189_Colonel_Bates_House_2128_Brentwood.pdf",
        "citation": "COH Landmark Designation Report #07L189: Designed by Houston architect Stayton Nunn in 1936 (completed March 1937) for Colonel W. B. Bates; corrects erroneous COH GIS row-shift (1889 / George E. Dickey).",
    },
    # 08PL67: COH GIS table erroneously recorded 1883 (19 years before Westmoreland was platted in 1902).
    # HAHC Protected Landmark Report 08PL67 confirms construction in 1904.
    "0370340000003": {
        "code": "08PL67",
        "name": "Elbert C. Crawford House",
        "address": "428 Westmoreland St",
        "year_built": 1904,
        "architect": "",
        "style": "Queen Anne / Colonial Revival Transitional",
        "report_url": "https://www.houstontx.gov/planning/HistoricPres/landmarks/08PL67_Crawford_House_428_Westmoreland.pdf",
        "citation": "COH Protected Landmark Designation Report #08PL67: Built in 1904 in the Westmoreland Addition (platted 1902); corrects erroneous 1883 COH GIS date.",
    },
    # 04L116: COH GIS recorded the 1938 Nunn & McGinty renovation year instead of 1922 construction.
    "0191440000010": {
        "code": "04L116",
        "name": "Sarah Francelia Bell Home",
        "address": "1111 Rosalie St",
        "year_built": 1922,
        "architect": "Stayton Nunn and Milton McGinty (1938 remodeling)",
        "style": "Brick Institutional / Classical Revival",
        "report_url": "https://www.houstontx.gov/planning/HistoricPres/landmarks/04L116_Bell_Home_1111_Rosalie.pdf",
        "citation": "COH Landmark Designation Report #04L116: Original three-story brick home built in 1922 under the bequest of Sarah Francelia Bell (remodeled in 1938 by Stayton Nunn and Milton McGinty).",
    },
    # 96L006: City National Bank Building (1947 by Alfred C. Finn; HCAD shows 1985 renovation / 1999 parcel).
    "0011370000011": {
        "code": "96L006",
        "name": "City National Bank Building",
        "address": "1001 McKinney St / 921 Main St",
        "year_built": 1947,
        "architect": "Alfred C. Finn",
        "style": "Late Art Deco / Modern",
        "target_building_id": "bld_006487",
        "report_url": "https://www.houstontx.gov/planning/HistoricPres/landmarks/96L006_City_National_Bank_921_Main.pdf",
        "citation": "COH Landmark Designation Report #96L006: 24-story City National Bank Building designed by Alfred C. Finn and completed in 1947 (HCAD reflects 1985 renovation).",
    },
    # 98L042: The National Biscuit Company Building (completed November 1910, Albert G. Zimmerman; HCAD loft footprint shows 1977).
    "1307910010002": {
        "code": "98L042",
        "name": "National Biscuit Company Building",
        "address": "15 N Chenevert St",
        "year_built": 1910,
        "architect": "Albert G. Zimmerman",
        "style": " early-20th-Century Brick & Terra-Cotta Industrial",
        "report_url": "https://www.houstontx.gov/planning/HistoricPres/landmarks/98L042_National_Biscuit_15_N_Chenevert.pdf",
        "citation": "COH Landmark Designation Report #98L042: Completed in November 1910 to designs by Nabisco architect Albert G. Zimmerman (HCAD 1977 reflects adaptive reuse).",
    },
    # 06PL28: John E. and Isabenda Edmundson House (1912 Protected Landmark; fixes "Pasonage" typo and HCAD 1930 estimate).
    "0041370000004": {
        "code": "06PL28",
        "name": "John E. and Isabenda Edmundson House (Methodist District Parsonage)",
        "address": "108 Stratford St",
        "year_built": 1912,
        "architect": "",
        "style": "Prairie / American Foursquare",
        "report_url": "https://www.houstontx.gov/planning/HistoricPres/landmarks/06PL28_Edmundson_House_108_Stratford.pdf",
        "citation": "COH Protected Landmark Designation Report #06PL28: Built in 1912 in Westmoreland/Avondale area (HCAD defaults to rounded 1930 estimate).",
    },
    # 06L146: Milroy-Muller House (1895 Houston Heights landmark vs HCAD 1935).
    "0201120000014": {
        "code": "06L146",
        "name": "Milroy-Muller House",
        "address": "1602 Harvard St",
        "year_built": 1895,
        "architect": "",
        "style": "Queen Anne Victorian",
        "report_url": "https://www.houstontx.gov/planning/HistoricPres/landmarks/06L146_Milroy-Muller_House_1602_Harvard.pdf",
        "citation": "COH Landmark Designation Report #06L146: Early Houston Heights residence built in 1895 (HCAD lists 1935 remodel date).",
    },
    # 06L165: William L. Clayton Summer House (1924 by Birdsall P. Briscoe & Sam Dixon Jr. vs HCAD 1937).
    "0601290000008": {
        "code": "06L165",
        "name": "William L. Clayton Summer House",
        "address": "3376 Inwood Dr",
        "year_built": 1924,
        "architect": "Birdsall P. Briscoe and Sam H. Dixon Jr.",
        "style": "Country Colonial Revival",
        "report_url": "https://www.houstontx.gov/planning/HistoricPres/landmarks/06L165_Clayton_Summer_House_3376_Inwood.pdf",
        "citation": "COH Landmark Designation Report #06L165: Built in 1924 as one of the earliest residences in River Oaks, designed by Birdsall P. Briscoe and Sam H. Dixon Jr.",
    },
    # 07PL43 / 08PL51: J. Vance Lewis & Pauline Gray Lewis Houses (1907 Freedmen's Town Protected Landmarks vs HCAD 1927).
    "0090790000009": {
        "code": "07PL43",
        "name": "J. Vance Lewis and Pauline Gray Lewis Houses",
        "address": "1216-1218 Wilson St / 1408 Andrews St",
        "year_built": 1907,
        "architect": "",
        "style": "Late Victorian / Vernacular",
        "report_url": "https://www.houstontx.gov/planning/HistoricPres/landmarks/07PL43_Lewis_House_1218_Wilson.pdf",
        "citation": "COH Protected Landmark Designation Reports #07PL43 & #08PL51: Built c. 1907 in Freedmen's Town for prominent attorney J. Vance Lewis and educator Pauline Gray Lewis.",
    },
    # 07PL49: Fox-Kuhlman Building (1862 Market Square Protected Landmark vs HCAD 1874).
    "0010330000009": {
        "code": "07PL49",
        "name": "Fox-Kuhlman Building",
        "address": "305-307 Travis St",
        "year_built": 1862,
        "architect": "",
        "style": "19th-Century Brick Mercantile",
        "report_url": "https://www.houstontx.gov/planning/HistoricPres/landmarks/07PL49_Fox-Kuhlman_305_Travis.pdf",
        "citation": "COH Protected Landmark Designation Report #07PL49: Constructed c. 1862 on Market Square, making it one of Houston's oldest surviving brick commercial structures.",
    },
    # 10PL93: Baker-Meyer Building (1870 Market Square Protected Landmark vs HCAD 1880).
    "0010330000007": {
        "code": "10PL93",
        "name": "Baker-Meyer Building",
        "address": "313-315 Travis St",
        "year_built": 1870,
        "architect": "",
        "style": "19th-Century Italianate Commercial",
        "report_url": "https://www.houstontx.gov/planning/HistoricPres/landmarks/10PL93_Baker-Meyer_315_Travis.pdf",
        "citation": "COH Protected Landmark Designation Report #10PL93: Constructed c. 1870 on Market Square (HCAD defaults to rounded 1880 estimate).",
    },
    # 08PL70: William A. Wilson House (1910 Woodland Heights Protected Landmark vs HCAD 1930).
    "1267940010002": {
        "code": "08PL70",
        "name": "William A. Wilson House",
        "address": "205 Bayland Ave",
        "year_built": 1910,
        "architect": "William A. Wilson Realty Co.",
        "style": "Craftsman / Prairie",
        "report_url": "https://www.houstontx.gov/planning/HistoricPres/landmarks/08PL70_Wilson_House_205_Bayland.pdf",
        "citation": "COH Protected Landmark Designation Report #08PL70: Built in 1910 in Woodland Heights for developer William A. Wilson (HCAD defaults to rounded 1930 estimate).",
    },
    # 09PL79: Sidney Sherman Brady House (1905 East End Protected Landmark vs HCAD 1965).
    "0520460000002": {
        "code": "09PL79",
        "name": "Sidney Sherman Brady House",
        "address": "3805 Wilmer St",
        "year_built": 1905,
        "architect": "",
        "style": "Colonial Revival / Vernacular",
        "report_url": "https://www.houstontx.gov/planning/HistoricPres/landmarks/09PL79_Brady_House_3805_Wilmer.pdf",
        "citation": "COH Protected Landmark Designation Report #09PL79: Built c. 1905 for Sidney Sherman Brady, grandson of Gen. Sidney Sherman (HCAD lists 1965 remodel year).",
    },
    # 08L207: Eller Wagon Works - Pittsburgh Plate Glass Building (1909 vs HCAD 1925 on 101 Crawford footprint).
    "0011060000008": {
        "code": "08L207",
        "name": "Eller Wagon Works / Pittsburgh Plate Glass Building",
        "address": "101 Crawford St",
        "year_built": 1909,
        "architect": "Alfred C. Finn (later remodel)",
        "style": "Early-20th-Century Brick Industrial Warehouse",
        "target_building_id": "bld_003438",
        "report_url": "https://www.houstontx.gov/planning/HistoricPres/landmarks/08L207_Eller_Wagon_Works_101_Crawford.pdf",
        "citation": "COH Landmark Designation Report #08L207: Constructed in 1909 at 101 Crawford Street for Eller Wagon Works / Pittsburgh Plate Glass Co. (HCAD lists 1925).",
    },
    # 0010790000001: Texas Company Building (1915 by Warren & Wetmore; HCAD footprint bld_004286 listed 2015 apartment conversion).
    "0010790000001": {
        "code": "02L101",
        "name": "Texas Company Building",
        "address": "720 San Jacinto St / 1111 Rusk St",
        "year_built": 1915,
        "architect": "Warren & Wetmore (1915); Kenneth Franzheim (1936, 1959 additions)",
        "style": "Neoclassical / Renaissance Revival Skyscraper",
        "target_building_id": "bld_004286",
        "report_url": "https://www.houstontx.gov/planning/HistoricPres/landmarks/02L101_Texas_Company_720_San_Jacinto.pdf",
        "citation": "COH Landmark Designation Report: Original 13-story Texaco headquarters built in 1915 by Warren & Wetmore with 1936 and 1959 additions (HCAD 2015 reflects adaptive residential conversion).",
    },
    # 11PL102: Sterne Building (1916 by Joseph Finger and James Ruskin Bailey; bld_006812 footprint had 1900).
    "0010330000005": {
        "code": "11PL102",
        "name": "Sterne Building",
        "address": "300 Main St",
        "year_built": 1916,
        "architect": "Joseph Finger and James Ruskin Bailey",
        "style": "Early-20th-Century Commercial Brick",
        "target_building_id": "bld_006812",
        "report_url": "https://www.houstontx.gov/planning/HistoricPres/landmarks/11PL102_Sterne_Bldg_300_Main_St.pdf",
        "citation": "COH Protected Landmark Designation Report #11PL102: Built in 1916 to designs by Finger & Bailey after an earlier 1884 George E. Dickey building burned.",
    },
    # 08L209: William T. Campbell Jr. House ("The Redbird House", 1925 by Birdsall P. Briscoe; HCAD listed 2007 restoration).
    "0601310000004": {
        "code": "08L209",
        "name": "William T. Campbell Jr. House",
        "address": "3237 Inwood Dr",
        "year_built": 1925,
        "architect": "Birdsall P. Briscoe and Sam H. Dixon Jr.",
        "style": "English Cottage / Tudor Revival",
        "target_building_id": "bld_010791",
        "report_url": "https://www.houstontx.gov/planning/HistoricPres/landmarks/09L211_Campbell_House_3237_Inwood.pdf",
        "citation": "COH Landmark Designation Report & Good Brick Award: Designed in 1925 by Birdsall P. Briscoe and Sam H. Dixon Jr. in River Oaks (HCAD 2007 reflects historic rehabilitation).",
    },
    # 04L122: Dr. H. J. and Emerence Ehlers House (1933-1934 by Cameron D. Fairchild; HCAD listed 2015 garage/addition).
    "0601550470015": {
        "code": "04L122",
        "name": "Dr. H. J. and Emerence Ehlers House",
        "address": "2112 Brentwood Dr",
        "year_built": 1933,
        "architect": "Cameron D. Fairchild",
        "style": "Colonial Revival",
        "target_building_id": "bld_010899",
        "report_url": "https://www.houstontx.gov/planning/HistoricPres/landmarks/04L122_Ehlers_House_2112_Brentwood.pdf",
        "citation": "COH Landmark Designation Report #04L122: Designed in 1933 (built 1933-1934) by Cameron D. Fairchild for Dr. H. J. and Emerence Ehlers (HCAD 2015 reflects later garage addition).",
    },
    # 0152300000001: John Henry Kirby Mansion (1894 Queen Anne residence remodeled in 1926 Tudor Revival by James Ruskin Bailey).
    "0152300000001": {
        "code": "NRHP-88001053",
        "name": "John Henry Kirby Mansion",
        "address": "2000 Smith St",
        "year_built": 1894,
        "architect": "James Ruskin Bailey (1926 Tudor Revival remodeling)",
        "style": "Tudor Revival",
        "target_building_id": "bld_004618",
        "report_url": "https://atlas.thc.texas.gov/",
        "citation": "National Register of Historic Places (#88001053): Original c. 1894 residence extensively remodeled into a 36-room Tudor Revival mansion in 1926 by architect James Ruskin Bailey.",
    },
}


def extract_primary_street(addr: str | None) -> str:
    """Extract normalized core street name without house number, directionals, or street suffix."""
    if not addr:
        return ""
    s = str(addr).upper().split("/")[0].split("(")[0].strip()
    s = re.sub(r"[^A-Z0-9\s]", " ", s)
    tokens = s.split()
    while tokens and (tokens[0].isdigit() or re.match(r"^\d+[A-Z]?$", tokens[0])):
        tokens.pop(0)
    if tokens and tokens[0] in {"N", "S", "E", "W", "NORTH", "SOUTH", "EAST", "WEST"}:
        tokens.pop(0)
    suffixes = {
        "ST", "STREET", "AVE", "AVENUE", "BLVD", "BOULEVARD", "DR", "DRIVE",
        "RD", "ROAD", "LN", "LANE", "CT", "COURT", "PL", "PLACE", "CIR", "CIRCLE",
        "PKWY", "PARKWAY", "FWY", "FREEWAY", "HWY", "HIGHWAY", "WAY", "TRL", "TRAIL",
    }
    while len(tokens) > 1 and (tokens[-1] in suffixes or tokens[-1].isdigit()):
        tokens.pop()
    return " ".join(tokens)


def geom_area(geom: dict | None) -> float:
    if not geom:
        return 0.0
    coords = geom.get("coordinates") or []
    ring = (
        coords[0]
        if geom.get("type") == "Polygon" and coords
        else coords[0][0]
        if geom.get("type") == "MultiPolygon" and coords and coords[0]
        else []
    )
    if len(ring) < 3:
        return 0.0
    return 0.5 * abs(
        sum(
            ring[i][0] * ring[(i + 1) % len(ring)][1]
            - ring[(i + 1) % len(ring)][0] * ring[i][1]
            for i in range(len(ring))
        )
    )


def run_audit(
    apply_fixes: bool = False,
    *,
    cama_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    haif_path = DATA_DIR / "haif_index.json"
    overlays_path = DATA_DIR / "overlays.json"
    overrides_path = DATA_DIR / "curated_overrides.json"
    buildings_path = DATA_DIR / "buildings.geojson"
    search_index_path = DATA_DIR / "search_index.json"
    citydir_path = DATA_DIR / "citydir_index.json"

    haif_doc = json.loads(haif_path.read_text())
    overlays_doc = json.loads(overlays_path.read_text())
    overrides_doc = json.loads(overrides_path.read_text())
    overrides = overrides_doc.get("overrides", {})
    buildings_doc = json.loads(buildings_path.read_text())
    search_index = json.loads(search_index_path.read_text())
    citydir_doc = json.loads(citydir_path.read_text())
    coh_raw_path = CACHE_DIR / "coh_landmarks.json"
    coh_enr_path = CACHE_DIR / "coh_landmarks_enriched.json"
    coh_raw = json.loads(coh_raw_path.read_text()) if coh_raw_path.exists() else []
    coh_enr = json.loads(coh_enr_path.read_text()) if coh_enr_path.exists() else []

    # Resolve verified report URLs from coh_landmarks_enriched.json so every URL matches cached live PDFs
    enr_by_code: dict[str, dict] = {}
    for r in coh_enr:
        for c in (r.get("lm_num"), r.get("plm_num")):
            if c and c != "-":
                enr_by_code[str(c).strip()] = r
    for hcad, fix in VERIFIED_COH_LANDMARK_FIXES.items():
        enr_match = enr_by_code.get(fix["code"])
        if enr_match and enr_match.get("report_pdf_url"):
            fix["report_url"] = enr_match["report_pdf_url"]

    stats: dict[str, int] = Counter()

    # -----------------------------------------------------------------------
    # STEP 1: Scrub polluted HAIF names from haif_index, buildings, overrides, search_index
    # -----------------------------------------------------------------------
    names_by_key = haif_doc.get("names_by_key", {})
    names_by_addr = haif_doc.get("names_by_addr", {})

    haif_fanout = Counter(
        v.get("n", "")
        for v in names_by_key.values()
        if isinstance(v, dict) and ("HAIF" in v.get("s", "") or "Forum" in v.get("s", "")) and v.get("n")
    )

    polluted_names_lower: set[str] = set()
    for key in list(names_by_key.keys()):
        rec = names_by_key[key]
        if not isinstance(rec, dict):
            continue
        src = str(rec.get("s") or "")
        nm = str(rec.get("n") or "")
        if ("HAIF" in src or "Forum" in src) and "Landmark" not in src and "Good Brick" not in src and "Preservation Houston" not in src:
            bad, _ = is_polluted_haif_building_name(nm, haif_fanout[nm])
            if bad:
                polluted_names_lower.add(nm.lower())
                stats["haif_names_by_key_removed"] += 1
                if apply_fixes:
                    del names_by_key[key]
                continue
        if isinstance(rec.get("a"), list):
            clean_aliases = [
                a for a in rec["a"] if not is_polluted_haif_building_name(a, haif_fanout.get(a, 1))[0]
            ]
            if len(clean_aliases) != len(rec["a"]):
                stats["haif_aliases_removed"] += len(rec["a"]) - len(clean_aliases)
                if apply_fixes:
                    if clean_aliases:
                        rec["a"] = clean_aliases
                    else:
                        rec.pop("a", None)

    for addr_k in list(names_by_addr.keys()):
        rec = names_by_addr[addr_k]
        if not isinstance(rec, dict):
            continue
        src = str(rec.get("s") or "")
        nm = str(rec.get("n") or "")
        if ("HAIF" in src or "Forum" in src) and "Landmark" not in src and "Good Brick" not in src and "Preservation Houston" not in src:
            bad, _ = is_polluted_haif_building_name(nm, haif_fanout.get(nm, 1))
            if bad:
                polluted_names_lower.add(nm.lower())
                stats["haif_names_by_addr_removed"] += 1
                if apply_fixes:
                    del names_by_addr[addr_k]
                continue
        if isinstance(rec.get("a"), list):
            clean_aliases = [
                a for a in rec["a"] if not is_polluted_haif_building_name(a, haif_fanout.get(a, 1))[0]
            ]
            if len(clean_aliases) != len(rec["a"]):
                stats["haif_aliases_removed"] += len(rec["a"]) - len(clean_aliases)
                if apply_fixes:
                    if clean_aliases:
                        rec["a"] = clean_aliases
                    else:
                        rec.pop("a", None)

    # Scrub matching polluted building_name from buildings.geojson & curated_overrides.json & search_index.json
    for feat in buildings_doc.get("features", []):
        p = feat.get("properties") or {}
        bn = str(p.get("building_name") or "").strip()
        if bn and (bn.lower() in polluted_names_lower or is_polluted_haif_building_name(bn)[0]):
            ns = str(p.get("name_source") or "")
            if not ns or "HAIF" in ns or "Forum" in ns:
                stats["buildings_polluted_names_cleared"] += 1
                if apply_fixes:
                    p.pop("building_name", None)
                    if "HAIF" in ns or "Forum" in ns:
                        p.pop("name_source", None)

    for ov_k, ov in list(overrides.items()):
        if not isinstance(ov, dict):
            continue
        bn = str(ov.get("building_name") or "").strip()
        if bn and (bn.lower() in polluted_names_lower or is_polluted_haif_building_name(bn)[0]):
            ns = str(ov.get("name_source") or "")
            if not ns or "HAIF" in ns or "Forum" in ns:
                stats["overrides_polluted_names_cleared"] += 1
                if apply_fixes:
                    ov.pop("building_name", None)

    for row in search_index:
        for field in ("name", "building_name"):
            val = str(row.get(field) or "").strip()
            if val and (val.lower() in polluted_names_lower or is_polluted_haif_building_name(val)[0]):
                if not row.get("overlay_layer") and not row.get("landmark_name"):
                    stats["search_index_polluted_names_cleared"] += 1
                    if apply_fixes:
                        if field == "name" and row.get("address"):
                            row["name"] = row["address"]
                        else:
                            row.pop(field, None)

    # -----------------------------------------------------------------------
    # STEP 2: Reconcile Demolished Landmarks & Verified COH Landmark Report Fixes
    # -----------------------------------------------------------------------
    all_coh_demo = get_all_coh_demolished_landmarks_meta()
    verified_demolished_coh_ids = set(all_coh_demo.keys())
    demolished_hcads: set[str] = set()
    landmark_feats = overlays_doc.get("landmarks", {}).get("features", [])
    for idx, feat in enumerate(landmark_feats):
        p = feat.get("properties") or {}
        raw_p = (
            coh_raw[idx].get("attributes") or coh_raw[idx].get("properties") or {}
            if idx < len(coh_raw)
            else {}
        )
        coh_id = str(p.get("id") or f"coh_lm_{idx + 1:03d}")
        raw_demod = str(raw_p.get("USER_DEMOD") or "").strip().upper() == "Y"
        is_demod = (raw_demod and coh_id != "coh_lm_089") or (coh_id in verified_demolished_coh_ids)

        if is_demod:
            stats["landmarks_marked_demolished"] += 1
            demo_meta_hcads = list((all_coh_demo.get(coh_id) or {}).get("site_hcad_nums") or [])
            cand_site_hcad = str(
                p.get("site_hcad_num")
                or p.get("hcad_num")
                or (demo_meta_hcads[0] if demo_meta_hcads else "")
                or raw_p.get("USER_HCAD_NUM")
                or ""
            ).strip()
            clean_site_hcad = (
                cand_site_hcad
                if (re.match(r"^\d{13}$", cand_site_hcad) and cand_site_hcad != "0000000000000")
                else (demo_meta_hcads[0] if demo_meta_hcads else "")
            )
            for dh in ([clean_site_hcad] + demo_meta_hcads):
                if re.match(r"^\d{13}$", str(dh or "")):
                    demolished_hcads.add(str(dh))
            if str(p.get("hcad_num") or "").strip() != "":
                stats["landmarks_non13_hcad_cleaned"] += 1
            if apply_fixes:
                p["demolished"] = True
                p["hcad_num"] = ""
                if clean_site_hcad:
                    p["site_hcad_num"] = clean_site_hcad
                p["city_tax_exemption_eligible"] = False
                p["status_note"] = "Demolished Former Landmark (City of Houston HPO Archive)"
                p.pop("alt_addresses", None)
                p.pop("haif_threads", None)
        else:
            raw_hcad = str(p.get("hcad_num") or raw_p.get("USER_HCAD_NUM") or "").strip()
            clean_hcad = raw_hcad if (re.match(r"^\d{13}$", raw_hcad) and raw_hcad != "0000000000000") else ""
            if str(p.get("hcad_num") or "").strip() != clean_hcad:
                stats["landmarks_non13_hcad_cleaned"] += 1
                if apply_fixes:
                    p["hcad_num"] = clean_hcad
            if p.get("demolished"):
                stats["landmarks_false_demolished_cleared"] += 1
                if apply_fixes:
                    p["demolished"] = False
                    p.pop("status_note", None)

        # Fix 07L182 Alabama Theatre typo ("Kenneth Franzheim. 1040, wings") & mismatched Cullen PDF
        lm_code = str(p.get("lm_num") or p.get("plm_num") or raw_p.get("USER_LM_NUM") or "").strip()
        if lm_code == "07L182":
            stats["landmarks_metadata_typos_fixed"] += 1
            if apply_fixes:
                p["architect"] = "W. Scott Dunne (1939 theater); Kenneth Franzheim (1940 retail wings)"
                if "09L212_Cullen_House" in str(p.get("secondary_pdf_url") or ""):
                    p.pop("secondary_pdf_url", None)
                if "Hugh Roy Cullen House" in str(p.get("pdf_summary") or ""):
                    p["pdf_summary"] = (
                        "Alabama Theatre and Alabama Community Shopping Center (2900-2946 S. Shepherd Dr.), "
                        "Art Deco neighborhood movie palace designed by W. Scott Dunne (1939) with retail wings "
                        "by Kenneth Franzheim (1940)."
                    )

        # Fix "Pasonage" typo on 06PL28
        if "Pasonage" in str(p.get("name") or ""):
            stats["landmarks_metadata_typos_fixed"] += 1
            if apply_fixes:
                p["name"] = str(p["name"]).replace("Pasonage", "Parsonage").replace("\u2013", "-")

        # Apply verified COH Landmark Report fixes onto overlays.landmarks too
        hcad_clean = str(p.get("hcad_num") or "").strip()
        if not is_demod and hcad_clean in VERIFIED_COH_LANDMARK_FIXES:
            fix = VERIFIED_COH_LANDMARK_FIXES[hcad_clean]
            if not fix.get("target_building_id") or lm_code == fix["code"]:
                stats["landmarks_overlay_facts_reconciled"] += 1
                if apply_fixes:
                    p["year_built"] = fix["year_built"]
                    if fix["architect"]:
                        p["architect"] = fix["architect"]
                    if fix["style"] and not p.get("style"):
                        p["style"] = fix["style"].strip()

    if apply_fixes:
        sanitize_coh_landmarks_overlay(overlays_doc.get("landmarks", {}), search_index)

    # Remove false landmark overrides on confirmed demolished landmarks (e.g. 2019 replacement at 1059 Kirby Dr)
    blds_by_hcad: dict[str, list[dict]] = {}
    for feat in buildings_doc.get("features", []):
        bp = feat.get("properties") or {}
        h = str(bp.get("hcad_num") or "").strip()
        if h:
            blds_by_hcad.setdefault(h, []).append(feat)

    for demod_hcad in demolished_hcads:
        if demod_hcad in overrides:
            stats["demolished_landmark_overrides_removed"] += 1
            if apply_fixes:
                del overrides[demod_hcad]
        for bfeat in blds_by_hcad.get(demod_hcad, []):
            bp = bfeat["properties"]
            if int(bp.get("year_built") or 0) >= 2000 and (bp.get("architect") or bp.get("building_name")):
                stats["demolished_landmark_modern_buildings_cleaned"] += 1
                if apply_fixes:
                    bp.pop("architect", None)
                    bp.pop("builder", None)
                    bp.pop("landmark_name", None)
                    bp.pop("building_name", None)
                    bp.pop("is_curated_override", None)
        if apply_fixes:
            for row in search_index:
                if str(row.get("hcad_num") or "") == demod_hcad and not row.get("overlay_layer"):
                    if int(row.get("year_built") or 0) >= 2000:
                        row.pop("architect", None)
                        row.pop("builder", None)
                        row.pop("landmark_name", None)
                        if row.get("address"):
                            row["name"] = row["address"]

    # Apply VERIFIED_COH_LANDMARK_FIXES to buildings.geojson, curated_overrides.json, haif_index.json, search_index.json
    for hcad, fix in VERIFIED_COH_LANDMARK_FIXES.items():
        bfeats = blds_by_hcad.get(hcad, [])
        target_bid = fix.get("target_building_id")
        matched_feats = [
            f for f in bfeats
            if not target_bid or str(f["properties"].get("id") or "") == target_bid
        ]
        orig_hcad_yr = None
        best_geom = None
        if matched_feats:
            best_b = max(matched_feats, key=lambda x: geom_area(x.get("geometry")))
            best_geom = best_b.get("geometry")
            orig_hcad_yr = int(
                best_b["properties"].get("original_hcad_year")
                or best_b["properties"].get("year_built")
                or 0
            ) or None

        stats["verified_coh_landmarks_promoted"] += 1
        if apply_fixes:
            for mf in matched_feats:
                mp = mf["properties"]
                prev_yr = int(mp.get("year_built") or 0)
                if prev_yr and prev_yr != fix["year_built"] and not mp.get("original_hcad_year"):
                    mp["original_hcad_year"] = prev_yr
                mp["year_built"] = fix["year_built"]
                mp["is_curated_override"] = True
                mp["building_name"] = fix["name"]
                mp["landmark_name"] = fix["name"]
                mp["name_source"] = "COH Landmark Designation Report"
                mp["year_source"] = "COH Landmark Designation Report"
                mp["landmark_code"] = fix["code"]
                mp["landmark_report_url"] = fix["report_url"]
                if fix["architect"]:
                    mp["architect"] = fix["architect"]
                elif hcad == "0601550470017":
                    mp["architect"] = "Stayton Nunn"
                if fix["style"]:
                    mp["style"] = fix["style"].strip()

            ov_key = f"{hcad}#{target_bid}" if target_bid else hcad
            ov_rec = overrides.get(ov_key) or overrides.get(hcad) or {}
            ov_rec.update({
                "hcad_num": hcad,
                "address": ov_rec.get("address") or fix["address"],
                "building_name": fix["name"],
                "landmark_name": fix["name"],
                "year_built": fix["year_built"],
                "original_hcad_year": ov_rec.get("original_hcad_year") or orig_hcad_yr,
                "bld_style": fix["style"].strip() or ov_rec.get("bld_style") or "",
                "architect": fix["architect"] if fix["architect"] else ov_rec.get("architect", ""),
                "source_type": "COH Landmark Designation Report",
                "source_citation": fix["citation"],
                "source_url": fix["report_url"],
                "landmark_report_url": fix["report_url"],
                "landmark_code": fix["code"],
                "verified_by": "City of Houston HAHC Landmark Report Audit",
                "verified_at": "2026-10-25",
            })
            if best_geom and not ov_rec.get("geometry"):
                ov_rec["geometry"] = best_geom
            overrides[ov_key] = ov_rec

            names_by_key[hcad] = {
                "n": fix["name"],
                "s": "COH Landmark Designation Report",
            }

            for row in search_index:
                if str(row.get("hcad_num") or "") == hcad and not row.get("overlay_layer"):
                    if target_bid and row.get("building_id") and str(row.get("building_id")) != target_bid:
                        continue
                    row["year_built"] = fix["year_built"]
                    row["name"] = fix["name"]
                    row["landmark_name"] = fix["name"]
                    if fix["architect"]:
                        row["architect"] = fix["architect"]

    # -----------------------------------------------------------------------
    # STEP 3: Generate Prioritized Cross-Dataset Contradiction Audit Queue
    # -----------------------------------------------------------------------
    category_slug_map = {
        "SUSPECTED_UNMARKED_LANDMARK_DEMOLITION": "demo_lm",
        "ARCHITECT_CAREER_WINDOW_CHECK": "arch_win",
        "LANDMARK_VS_ATLAS_YEAR_MISMATCH": "lm_year",
        "ADDRESS_STREET_MISMATCH": "addr_st",
        "CITY_DIRECTORY_EARLIER_THAN_HCAD": "dir_early",
        "CITY_DIRECTORY_ABSENT_THROUGH_1926": "dir_abs",
    }

    def _safe_geom_point(geom: Any) -> tuple[float, float] | None:
        if not isinstance(geom, dict):
            return None
        try:
            shp = shape(geom)
            if shp.is_empty:
                return None
            pt = shp.representative_point()
            lon_v, lat_v = round(float(pt.x), 6), round(float(pt.y), 6)
            min_lng, min_lat, max_lng, max_lat = HARRIS_COUNTY_BBOX
            if min_lng <= lon_v <= max_lng and min_lat <= lat_v <= max_lat:
                return (lon_v, lat_v)
        except Exception:
            return None
        return None

    coord_by_id: dict[str, tuple[float, float]] = {}
    coord_by_hcad: dict[str, tuple[float, float]] = {}
    primary_props_by_hcad: dict[str, dict[str, Any]] = {}

    for feat in buildings_doc.get("features", []):
        bp = feat.get("properties") or {}
        bid = str(bp.get("building_id") or bp.get("id") or "").strip()
        hc = str(bp.get("hcad_num") or "").strip()
        pt = _safe_geom_point(feat.get("geometry"))
        if pt:
            if bid:
                coord_by_id[bid] = pt
            if hc and (hc not in coord_by_hcad or bid != hc):
                coord_by_hcad[hc] = pt
        if hc and (hc not in primary_props_by_hcad or bid != hc):
            primary_props_by_hcad[hc] = bp

    for ov_k, ov in overrides.items():
        if not isinstance(ov, dict):
            continue
        pt = None
        min_lng, min_lat, max_lng, max_lat = HARRIS_COUNTY_BBOX
        if ov.get("lon") is not None and ov.get("lat") is not None:
            try:
                cand = (round(float(ov["lon"]), 6), round(float(ov["lat"]), 6))
                if min_lng <= cand[0] <= max_lng and min_lat <= cand[1] <= max_lat:
                    pt = cand
            except (TypeError, ValueError):
                pt = None
        if pt is None and ov.get("geometry"):
            pt = _safe_geom_point(ov["geometry"])
        if pt:
            coord_by_id.setdefault(str(ov_k), pt)
            hc_ov = str(ov.get("hcad_num") or str(ov_k).split("#")[0]).strip()
            if hc_ov:
                coord_by_hcad.setdefault(hc_ov, pt)

    for layer_name in ("landmarks", "good_brick_awards", "nrhp_properties", "thc_markers", "lost_houston"):
        for feat in (overlays_doc.get(layer_name) or {}).get("features", []):
            fp = feat.get("properties") or {}
            pt = _safe_geom_point(feat.get("geometry"))
            if not pt:
                continue
            hc_f = str(fp.get("hcad_num") or fp.get("site_hcad_num") or "").strip()
            if hc_f:
                coord_by_hcad.setdefault(hc_f, pt)

    for srow in search_index:
        if not isinstance(srow, dict):
            continue
        if srow.get("lng") is not None and srow.get("lat") is not None:
            try:
                cand = (round(float(srow["lng"]), 6), round(float(srow["lat"]), 6))
                min_lng, min_lat, max_lng, max_lat = HARRIS_COUNTY_BBOX
                if min_lng <= cand[0] <= max_lng and min_lat <= cand[1] <= max_lat:
                    if srow.get("id"):
                        coord_by_id.setdefault(str(srow["id"]), cand)
                    if srow.get("hcad_num"):
                        coord_by_hcad.setdefault(str(srow["hcad_num"]), cand)
            except (TypeError, ValueError):
                pass

    findings: list[dict[str, Any]] = []
    seen_finding_keys: set[tuple[str, str]] = set()
    used_finding_ids: set[str] = set()

    def _make_finding_id(category: str, dedup_target: str) -> str:
        cat_slug = category_slug_map.get(
            category,
            re.sub(r"[^a-z0-9]+", "_", category.lower()).strip("_")[:24],
        )
        tgt_slug = re.sub(r"[^A-Za-z0-9_#-]+", "_", dedup_target.strip()).strip("_") or "item"
        base_id = f"{cat_slug}:{tgt_slug}"
        fid = base_id
        seq = 2
        while fid in used_finding_ids:
            fid = f"{base_id}_{seq}"
            seq += 1
        used_finding_ids.add(fid)
        return fid

    def add_finding(
        category: str,
        priority_score: int,
        hcad_num: str,
        address: str,
        name: str,
        district: str,
        displayed_claim: str,
        conflicting_evidence: str,
        recommended_action: str,
        primary_source_url: str,
        *,
        building_id: str = "",
        atlas_year: int = 0,
        hcad_year: int = 0,
        suggested_year: int = 0,
        suggested_source_type: str = "",
        contributing: str = "",
        cdm_pointer: int = 0,
        citydir_years: list[int] | None = None,
        landmark_code: str = "",
        architect: str = "",
    ) -> None:
        target_token = hcad_num or f"{address}:{name}"
        key = (category, target_token)
        if key in seen_finding_keys:
            return
        seen_finding_keys.add(key)

        base_bp = primary_props_by_hcad.get(hcad_num, {}) if hcad_num else {}
        resolved_bid = str(
            building_id
            or base_bp.get("building_id")
            or base_bp.get("id")
            or hcad_num
            or ""
        ).strip()
        resolved_cnt = str(
            contributing
            or base_bp.get("contributing")
            or ("Contributing" if (district and district not in ("Historic Core", "Outside Historic District", "landmarks")) else "Outside Historic District")
        ).strip()
        resolved_ay = int(atlas_year or base_bp.get("year_built") or 0)
        resolved_hy = int(
            hcad_year
            or base_bp.get("original_hcad_year")
            or base_bp.get("year_built")
            or 0
        )
        coord_pair = (
            coord_by_id.get(resolved_bid)
            or coord_by_hcad.get(hcad_num)
            or None
        )
        fid = _make_finding_id(category, hcad_num or resolved_bid or target_token)

        findings.append({
            "finding_id": fid,
            "priority_score": priority_score,
            "category": category,
            "hcad_num": hcad_num,
            "building_id": resolved_bid,
            "address": sanitize_atlas_text(address),
            "building_name": sanitize_atlas_text(name),
            "district_or_context": sanitize_atlas_text(district),
            "contributing": resolved_cnt,
            "lng": coord_pair[0] if coord_pair else None,
            "lat": coord_pair[1] if coord_pair else None,
            "atlas_year": resolved_ay,
            "hcad_year": resolved_hy,
            "suggested_year": int(suggested_year or 0),
            "suggested_source_type": suggested_source_type,
            "displayed_claim": sanitize_atlas_text(displayed_claim),
            "conflicting_evidence": sanitize_atlas_text(conflicting_evidence),
            "recommended_action": sanitize_atlas_text(recommended_action),
            "primary_source_url": primary_source_url,
            "cdm_pointer": int(cdm_pointer or 0),
            "citydir_years": list(citydir_years or []),
            "landmark_code": landmark_code,
            "architect": sanitize_atlas_text(architect or str(base_bp.get("architect") or "")),
            "review_status": "Pending PH Review",
        })

    # 3A. Suspected Unmarked Regular Landmark Demolitions & Multi-Structure Landmark Year Discrepancies
    for feat in overlays_doc.get("landmarks", {}).get("features", []):
        lp = feat.get("properties") or {}
        if lp.get("demolished"):
            continue
        hcad = str(lp.get("hcad_num") or "").strip()
        lmy = int(lp.get("year_built") or 0)
        if not (re.match(r"^\d{13}$", hcad) and lmy >= 1830):
            continue
        bfeats = blds_by_hcad.get(hcad, [])
        if not bfeats:
            continue
        non_parcel_bfeats = [bf for bf in bfeats if str(bf["properties"].get("id") or "") != hcad]
        primary_bf = max(non_parcel_bfeats or bfeats, key=lambda x: geom_area(x.get("geometry")))
        primary_bp = primary_bf.get("properties") or {}
        bld_years = [
            int(bf["properties"].get("year_built") or 0)
            for bf in bfeats
            if str(bf["properties"].get("id") or "") != hcad and int(bf["properties"].get("year_built") or 0) > 0
        ] or [int(bfeats[0]["properties"].get("year_built") or 0)]
        min_by = min(bld_years)
        lm_type = str(lp.get("landmark_type") or lp.get("designation") or "Landmark")
        lm_code = str(lp.get("plm_num") or lp.get("lm_num") or "")
        pdf_url = str(lp.get("report_pdf_url") or "")

        if min_by >= 2005 and "Protected" not in lm_type:
            add_finding(
                category="SUSPECTED_UNMARKED_LANDMARK_DEMOLITION",
                priority_score=98,
                hcad_num=hcad,
                address=str(lp.get("address") or primary_bp.get("address") or ""),
                name=str(lp.get("name") or ""),
                district=str(primary_bp.get("historic_district") or lm_type),
                displayed_claim=f"Active COH {lm_type} ({lm_code}, built {lmy})",
                conflicting_evidence=f"HCAD records modern structure built {min_by} on parcel; likely demolished after 90-day waiver without COH GIS USER_DEMOD flag",
                recommended_action="Verify via Street View / HAHC minutes; if demolished, mark demolished and move historic record to Lost Houston",
                primary_source_url=pdf_url,
                building_id=str(primary_bp.get("building_id") or primary_bp.get("id") or ""),
                atlas_year=min_by,
                hcad_year=min_by,
                suggested_year=0,
                suggested_source_type="COH Landmark Designation Report",
                landmark_code=lm_code,
                architect=str(lp.get("architect") or primary_bp.get("architect") or ""),
            )
        elif min_by > 0 and min(abs(by - lmy) for by in bld_years) >= 8:
            add_finding(
                category="LANDMARK_VS_ATLAS_YEAR_MISMATCH",
                priority_score=88,
                hcad_num=hcad,
                address=str(lp.get("address") or primary_bp.get("address") or ""),
                name=str(lp.get("name") or ""),
                district=str(primary_bp.get("historic_district") or lm_type),
                displayed_claim=f"Atlas footprint year(s): {sorted(set(bld_years))}",
                conflicting_evidence=f"COH {lm_type} ({lm_code}) report lists year_built = {lmy}",
                recommended_action="Disambiguate primary landmark footprint vs. secondary buildings or later remodel year in Approved_Edits",
                primary_source_url=pdf_url,
                building_id=str(primary_bp.get("building_id") or primary_bp.get("id") or ""),
                atlas_year=min_by,
                hcad_year=int(primary_bp.get("original_hcad_year") or min_by),
                suggested_year=lmy,
                suggested_source_type="COH Landmark Designation Report",
                landmark_code=lm_code,
                architect=str(lp.get("architect") or primary_bp.get("architect") or ""),
            )

    # 3B. Architect Active-Career Window Anachronism Scan
    for feat in buildings_doc.get("features", []):
        bp = feat.get("properties") or {}
        if str(bp.get("id") or "") == str(bp.get("hcad_num") or "") and len(blds_by_hcad.get(str(bp.get("hcad_num") or ""), [])) > 1:
            continue
        arch = str(bp.get("architect") or "").strip()
        yr = int(bp.get("year_built") or 0)
        if not arch or yr < 1830:
            continue
        al = arch.lower()
        # Skip explicitly annotated later additions/remodels like "(1926 Tudor Revival remodeling)" or "(1938 Latham Hall...)"
        if re.search(r"\b(19\d\d|20\d\d)\s+(remodel|remodeling|addition|wing|hall|annex|restoration)", al):
            continue
        for key, (lo, hi, label) in ARCHITECT_CAREER_WINDOWS.items():
            if key in al and (yr < lo or yr > hi):
                add_finding(
                    category="ARCHITECT_CAREER_WINDOW_CHECK",
                    priority_score=95,
                    hcad_num=str(bp.get("hcad_num") or ""),
                    address=str(bp.get("address") or ""),
                    name=str(bp.get("building_name") or bp.get("landmark_name") or bp.get("address") or ""),
                    district=str(bp.get("historic_district") or ""),
                    displayed_claim=f"Year Built {yr} · Architect: {arch}",
                    conflicting_evidence=f"Year {yr} is outside documented career window ({lo}-{hi}) for {label}",
                    recommended_action="Check whether architect designed a later remodel/addition or whether HCAD year reflects a replacement building",
                    primary_source_url=str(bp.get("landmark_report_url") or bp.get("source_url") or ""),
                    building_id=str(bp.get("building_id") or bp.get("id") or ""),
                    atlas_year=yr,
                    hcad_year=int(bp.get("original_hcad_year") or yr),
                    suggested_year=0,
                    suggested_source_type="Architectural Monograph / Handbook of Texas",
                    architect=arch,
                )

    # 3C. Cross-Street / Address Mismatch Scan on Overrides & Good Brick Awards
    for ov_k, ov in overrides.items():
        if not isinstance(ov, dict):
            continue
        hcad = str(ov.get("hcad_num") or ov_k.split("#")[0]).strip()
        if not re.match(r"^\d{13}$", hcad) or hcad not in blds_by_hcad:
            continue
        ov_st = extract_primary_street(ov.get("address"))
        parcel_streets = {
            extract_primary_street(bf["properties"].get("address"))
            for bf in blds_by_hcad[hcad]
            if bf["properties"].get("address")
        }
        parcel_streets.discard("")
        if ov_st and parcel_streets and ov_st not in parcel_streets:
            # Allow corner-lot dual addresses containing "/" or known alias substrings
            if not any(ov_st in ps or ps in ov_st for ps in parcel_streets):
                add_finding(
                    category="ADDRESS_STREET_MISMATCH",
                    priority_score=84,
                    hcad_num=hcad,
                    address=str(ov.get("address") or ""),
                    name=str(ov.get("building_name") or ov.get("landmark_name") or ""),
                    district=str(ov.get("historic_district") or ""),
                    displayed_claim=f"Override address '{ov.get('address')}' mapped to HCAD {hcad}",
                    conflicting_evidence=f"HCAD {hcad} situs street is {sorted(parcel_streets)} (Verify corner lot vs. adjacent parcel snap)",
                    recommended_action="Confirm corner-lot secondary frontage or reassign to adjacent HCAD account",
                    primary_source_url=str(ov.get("source_url") or ""),
                    building_id=str(ov_k),
                    atlas_year=int(ov.get("year_built") or 0),
                    hcad_year=int(ov.get("original_hcad_year") or ov.get("year_built") or 0),
                    suggested_year=int(ov.get("year_built") or 0),
                    suggested_source_type=str(ov.get("source_type") or "HCAD Parcel GIS"),
                    contributing=str(ov.get("contributing") or ""),
                    architect=str(ov.get("architect") or ""),
                )

    # 3D. 1866-1926 Houston City Directory Contradictions on Un-Overridden Buildings
    absent_1926 = set(citydir_doc.get("absent_through_1926") or [])
    by_hcad_dir = citydir_doc.get("by_hcad") or {}

    good_brick_hcads = {
        str(f.get("properties", {}).get("hcad_num") or "")
        for f in overlays_doc.get("good_brick_awards", {}).get("features", [])
    }
    landmark_hcads = {
        str(f.get("properties", {}).get("hcad_num") or "")
        for f in overlays_doc.get("landmarks", {}).get("features", [])
    }

    seen_parcel_dir: set[str] = set()
    for feat in buildings_doc.get("features", []):
        bp = feat.get("properties") or {}
        hcad = str(bp.get("hcad_num") or "").strip()
        if not re.match(r"^\d{13}$", hcad) or hcad in seen_parcel_dir:
            continue
        # Prefer actual building footprint over parcel polygon
        if str(bp.get("id") or "") == hcad and len(blds_by_hcad.get(hcad, [])) > 1:
            continue
        seen_parcel_dir.add(hcad)

        if bp.get("is_curated_override") or hcad in overrides:
            continue
        yr = int(bp.get("year_built") or 0)
        if yr < 1836:
            continue
        hd = str(bp.get("historic_district") or "")
        in_hd = bool(hd and hd != "Outside Historic District")
        vis_bonus = (12 if hcad in landmark_hcads else 0) + (10 if hcad in good_brick_hcads else 0) + (6 if in_hd else 0)
        addr = str(bp.get("address") or "")
        bname = str(bp.get("building_name") or "")
        search_q = addr.replace(" ", "%20") if addr else hcad
        dir_url = f"https://cdm17006.contentdm.oclc.org/digital/collection/citydir/search/searchterm/{search_q}"

        dir_rec = by_hcad_dir.get(hcad)
        if isinstance(dir_rec, dict) and dir_rec.get("e"):
            earliest_dir = int(dir_rec["e"])
            if earliest_dir <= yr - 5:
                gap = min(25, yr - earliest_dir)
                ptr_int, cdm_item_url = resolve_citydir_cdm_url(dir_rec, dir_url)
                ocr_excerpt = sanitize_atlas_text(str(dir_rec.get("c") or dir_rec.get("x") or "")).strip()
                excerpt_short = (ocr_excerpt[:140] + "...") if len(ocr_excerpt) > 140 else ocr_excerpt
                dir_years = [int(y) for y in (dir_rec.get("y") or []) if isinstance(y, (int, float))]
                evidence_detail = (
                    f"Appears in {earliest_dir} Houston City Directory ({yr - earliest_dir} yrs earlier than HCAD"
                    + (f"; {excerpt_short})" if excerpt_short else ")")
                )
                add_finding(
                    category="CITY_DIRECTORY_EARLIER_THAN_HCAD",
                    priority_score=65 + min(20, gap // 2) + vis_bonus,
                    hcad_num=hcad,
                    address=addr,
                    name=bname,
                    district=hd or "Historic Core",
                    displayed_claim=f"HCAD Year Built: {yr} (unverified)",
                    conflicting_evidence=evidence_detail,
                    recommended_action=f"Verify same structure on Sanborn map and promote year_built <= {earliest_dir} in Approved_Edits",
                    primary_source_url=cdm_item_url,
                    building_id=str(bp.get("building_id") or bp.get("id") or ""),
                    atlas_year=yr,
                    hcad_year=yr,
                    suggested_year=earliest_dir,
                    suggested_source_type="Houston City Directory",
                    contributing=str(bp.get("contributing") or ""),
                    cdm_pointer=ptr_int,
                    citydir_years=dir_years,
                )

        if hcad in absent_1926 and yr <= 1924:
            is_rounded = yr % 10 == 0
            add_finding(
                category="CITY_DIRECTORY_ABSENT_THROUGH_1926",
                priority_score=(62 if is_rounded else 68) + vis_bonus,
                hcad_num=hcad,
                address=addr,
                name=bname,
                district=hd or "Historic Core",
                displayed_claim=f"HCAD Year Built: {yr}{' (Rounded Decade Est.)' if is_rounded else ''}",
                conflicting_evidence="Address audited as unimproved / absent through the 1926 Morrison & Fourmy Houston City Directory",
                recommended_action="Check 1928-1935 City Directories & 1924-1951 Sanborn sheets for exact post-1926 construction year",
                primary_source_url=dir_url,
                building_id=str(bp.get("building_id") or bp.get("id") or ""),
                atlas_year=yr,
                hcad_year=yr,
                suggested_year=0,
                suggested_source_type="Houston City Directory",
                contributing=str(bp.get("contributing") or ""),
            )

    # 3E. Lost Houston, Demolished COH Landmarks, and Demolished NRHP Integrity Gate (Milestone 2)
    lost_feats = overlays_doc.get("lost_houston", {}).get("features", [])
    lost_by_coh_id: set[str] = set()
    lost_by_nrhp_refnum: set[str] = set()
    lost_hcads: set[str] = set()
    lost_schema_errors = 0
    lost_extant_collision_errors = 0

    cama_pkl_path = CACHE_DIR / "cama_2026_compact.pkl"
    cama_audit_map: dict[str, Any] = {}
    if cama_override is not None:
        cama_audit_map = cama_override
    elif cama_pkl_path.exists():
        import pickle as _pickle

        cama_audit_map = _pickle.loads(cama_pkl_path.read_bytes())

    for lf in lost_feats:
        lp = lf.get("properties") or {}
        lid = str(lp.get("id") or "")
        cohort = str(lp.get("source_cohort") or "")
        rank = lp.get("symbol_rank")
        yb = int(lp.get("year_built") or 0)
        yd = int(lp.get("year_demolished") or 0)
        geom_d = lf.get("geometry") or {}
        if lp.get("coh_landmark_id"):
            lost_by_coh_id.add(str(lp["coh_landmark_id"]))
        if lp.get("nrhp_refnum"):
            lost_by_nrhp_refnum.add(str(lp["nrhp_refnum"]))
        for hk in [lp.get("hcad_num")] + list(lp.get("site_hcad_nums") or []):
            hs = str(hk or "").strip()
            if re.match(r"^\d{13}$", hs):
                lost_hcads.add(hs)

        raw_lp_json = json.dumps(lp, ensure_ascii=False)
        geom_ok = False
        if geom_d.get("type") == "Polygon":
            try:
                poly = shape(geom_d)
                minx, miny, maxx, maxy = poly.bounds
                min_lng, min_lat, max_lng, max_lat = HARRIS_COUNTY_BBOX
                ring_coords = (geom_d.get("coordinates") or [[]])[0]
                has_excess_dp = has_excess_ring_precision(ring_coords, 6)
                geom_ok = (
                    isinstance(poly, Polygon)
                    and poly.is_valid
                    and not poly.is_empty
                    and poly.exterior.is_ccw
                    and len(ring_coords) >= 4
                    and min_lng <= minx <= maxx <= max_lng
                    and min_lat <= miny <= maxy <= max_lat
                    and not has_excess_dp
                )
            except Exception:
                geom_ok = False

        mat_key = str(lp.get("sanborn_material") or "").strip()
        if (
            not lid.startswith("lost_")
            or cohort not in LOST_HOUSTON_VALID_COHORTS
            or rank not in (1, 2, 3, 4)
            or mat_key not in VALID_SANBORN_MATERIALS
            or not (MIN_VALID_YEAR <= yb < yd <= MAX_VALID_CAMA_DATE_YEAR)
            or not geom_ok
            or "\u2014" in raw_lp_json
            or "\u2013" in raw_lp_json
        ):
            lost_schema_errors += 1
            add_finding(
                category="LOST_HOUSTON_SCHEMA_VIOLATION",
                priority_score=100,
                hcad_num=str(lp.get("hcad_num") or ""),
                address=str(lp.get("address") or ""),
                name=str(lp.get("name") or lid),
                district=str(lp.get("neighborhood") or cohort),
                displayed_claim=(
                    f"id={lid} cohort={cohort} rank={rank} mat={mat_key} "
                    f"years={yb}-{yd} geom_ok={geom_ok}"
                ),
                conflicting_evidence="Lost Houston feature violates Milestone 2 schema, lifespan chronology, CCW geometry, or dash hygiene",
                recommended_action="Fix source catalog or generator in lost_houston_expansion.py",
                primary_source_url=str(lp.get("source_url") or ""),
            )

        # Verify automated cohorts (2-5) never claim demolition on a parcel with an extant pre-demolition building
        if cohort != "curated_landmark":
            for hk in [lp.get("hcad_num")] + list(lp.get("site_hcad_nums") or []):
                hs = str(hk or "").strip()
                if not re.match(r"^\d{13}$", hs):
                    continue
                crow_a = cama_audit_map.get(hs)
                cama_vacant = bool(
                    crow_a is not None
                    and float(crow_a[2] or 0.0) <= 0.0
                    and str(crow_a[3] or "").strip().upper() in ("C1", "C2", "C3")
                )
                cama_rebuilt = bool(
                    crow_a is not None
                    and float(crow_a[2] or 0.0) > 0.0
                    and int(crow_a[0] or 0) >= yd
                )
                stamped_cama_cleared_or_rebuilt = (
                    not cama_audit_map
                    and cohort in ("red_book_1915_vanished", "historic_teardown_2020_2026")
                    and (
                        "Vacant lot (2026 HCAD" in str(lp.get("replaced_by") or "")
                        or "replacement structure" in str(lp.get("replaced_by") or "")
                        or "2026 HCAD CAMA" in str(lp.get("historic_significance") or "")
                    )
                )
                # Verified COH/NRHP demolished multi-lot or rebuilt parcels and certified vacant C1/C2/C3 lots
                # may still intersect stale pre-2026 aerial shards in buildings.geojson.
                if (
                    cama_vacant
                    or cama_rebuilt
                    or stamped_cama_cleared_or_rebuilt
                    or cohort in ("coh_landmark_demolished", "nrhp_demolished")
                ):
                    continue
                for bf in blds_by_hcad.get(hs, []):
                    bp2 = bf.get("properties") or {}
                    if bp2.get("suppress_only"):
                        continue
                    ey = int(bp2.get("year_built") or 0)
                    if MIN_VALID_YEAR <= ey < yd:
                        lost_extant_collision_errors += 1
                        add_finding(
                            category="LOST_HOUSTON_EXTANT_COLLISION",
                            priority_score=100,
                            hcad_num=hs,
                            address=str(lp.get("address") or ""),
                            name=str(lp.get("name") or lid),
                            district=cohort,
                            displayed_claim=f"Lost Houston demolished {yd}",
                            conflicting_evidence=f"Extant buildings.geojson structure on HCAD {hs} built {ey} (< {yd})",
                            recommended_action="Exclude candidate via ZeroExtantCollisionGate",
                            primary_source_url=str(lp.get("source_url") or ""),
                        )

    stats["lost_houston_features_audited"] = len(lost_feats)
    stats["lost_houston_schema_errors"] = lost_schema_errors
    stats["lost_houston_extant_collision_errors"] = lost_extant_collision_errors

    for feat in overlays_doc.get("landmarks", {}).get("features", []):
        lp = feat.get("properties") or {}
        lid = str(lp.get("id") or "")
        hc = str(lp.get("hcad_num") or "").strip()
        site_hc = str(lp.get("site_hcad_num") or hc).strip()
        if hc and not re.match(r"^\d{13}$", hc):
            add_finding(
                category="LANDMARK_NON13_HCAD",
                priority_score=99,
                hcad_num=hc,
                address=str(lp.get("address") or ""),
                name=str(lp.get("name") or lid),
                district="landmarks",
                displayed_claim=f"hcad_num='{hc}'",
                conflicting_evidence="Non-13-digit string stored in landmarks.hcad_num",
                recommended_action="Run sanitize_coh_landmarks_overlay",
                primary_source_url=str(lp.get("report_pdf_url") or ""),
            )
        if lp.get("demolished") and lid not in lost_by_coh_id and site_hc not in lost_hcads:
            add_finding(
                category="DEMOLISHED_LANDMARK_MISSING_FROM_LOST_HOUSTON",
                priority_score=99,
                hcad_num=hc,
                address=str(lp.get("address") or ""),
                name=str(lp.get("name") or lid),
                district="landmarks",
                displayed_claim=f"demolished=True on {lid}",
                conflicting_evidence="Demolished COH Landmark has no matching record in overlays.lost_houston",
                recommended_action="Add landmark to COH_DEMOLISHED_LANDMARKS_METADATA or COH_LANDMARK_ENRICHMENTS_ON_CURATED",
                primary_source_url=str(lp.get("report_pdf_url") or ""),
            )

    for feat in overlays_doc.get("nrhp_properties", {}).get("features", []):
        np = feat.get("properties") or {}
        if not np.get("is_demolished_nrhp"):
            continue
        ref = str(np.get("refnum") or "")
        yb = int(np.get("year_built") or 0)
        if yb < MIN_VALID_YEAR or (ref not in lost_by_nrhp_refnum and not np.get("was_relocated")):
            add_finding(
                category="DEMOLISHED_NRHP_UNENRICHED",
                priority_score=99,
                hcad_num=str(np.get("hcad_num") or ""),
                address=str(np.get("address") or ""),
                name=str(np.get("name") or ref),
                district="nrhp_properties",
                displayed_claim=f"NRHP #{ref} year_built={yb} was_relocated={bool(np.get('was_relocated'))}",
                conflicting_evidence="Demolished NRHP property missing historic year_built or Lost Houston link",
                recommended_action="Populate NRHP_DEMOLISHED_METADATA in lost_houston_expansion.py",
                primary_source_url=str(np.get("thc_atlas_url") or ""),
            )

    # Sort findings by priority_score descending, then category, then district, then address
    findings.sort(
        key=lambda r: (
            -int(r["priority_score"]),
            r["category"],
            0 if r["district_or_context"] and r["district_or_context"] != "Historic Core" else 1,
            r["address"],
        )
    )

    cat_counts = Counter(f["category"] for f in findings)
    stats_dict = dict(stats)

    # Count HCAD rounded decade buildings in Historic Core for summary transparency
    rounded_hcad_count = 0
    for feat in buildings_doc.get("features", []):
        bp = feat.get("properties") or {}
        if str(bp.get("id") or "") == str(bp.get("hcad_num") or ""):
            continue
        yr = int(bp.get("year_built") or 0)
        if not bp.get("is_curated_override") and not bp.get("year_source") and 1890 <= yr <= 1950 and yr % 10 == 0:
            rounded_hcad_count += 1

    queue_payload = build_historical_audit_queue_payload(findings, generated_on="2026-10-30")

    summary_payload = {
        "generated_on": "2026-10-30",
        "reconciled_fixes": stats_dict,
        "historic_core_hcad_rounded_decade_estimates": rounded_hcad_count,
        "total_queued_contradictions": len(findings),
        "contradictions_by_category": dict(cat_counts),
        "top_priority_queue_preview": findings[:40],
    }

    if apply_fixes:
        write_compact_json(haif_path, haif_doc)
        write_compact_json(overlays_path, overlays_doc)
        overrides_doc["overrides"] = overrides
        write_compact_json(overrides_path, overrides_doc)
        write_compact_json(buildings_path, buildings_doc)
        write_compact_json(search_index_path, search_index)
        write_compact_json(DATA_DIR / "historical_audit_queue.json", queue_payload)
        (DATA_DIR / "historical_audit_summary.json").write_text(
            json.dumps(summary_payload, indent=2, ensure_ascii=False)
        )

        csv_path = CACHE_DIR / "historical_audit_findings.csv"
        fieldnames = [
            "finding_id",
            "priority_score",
            "category",
            "hcad_num",
            "address",
            "building_name",
            "district_or_context",
            "displayed_claim",
            "conflicting_evidence",
            "recommended_action",
            "primary_source_url",
            "review_status",
        ]
        with csv_path.open("w", newline="", encoding="utf-8") as cf:
            writer = csv.DictWriter(cf, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(findings)

    return {
        "summary": summary_payload,
        "findings": findings,
        "queue": queue_payload,
    }


def resolve_citydir_cdm_url(dir_rec: dict[str, Any] | None, fallback_url: str) -> tuple[int, str]:
    """Resolve validated ContentDM integer page pointer (`p`) and direct item URL.

    Never interpolates non-integer OCR citation strings (`c`/`x`) into `/id/<ptr>/rec/1`.
    """
    if not isinstance(dir_rec, dict):
        return 0, fallback_url
    raw_ptr = dir_rec.get("p")
    ptr_int = (
        int(raw_ptr)
        if isinstance(raw_ptr, int) and not isinstance(raw_ptr, bool)
        else (int(str(raw_ptr).strip()) if str(raw_ptr or "").strip().isdigit() else 0)
    )
    if ptr_int > 0:
        return ptr_int, f"https://cdm17006.contentdm.oclc.org/digital/collection/citydir/id/{ptr_int}/rec/1"
    return 0, fallback_url


def build_historical_audit_queue_payload(
    findings: list[dict[str, Any]],
    *,
    generated_on: str = "2026-10-30",
) -> dict[str, Any]:
    """Build the compact Milestone 3 `historical_audit_queue.json` bundle (< 1.0 MiB; DF-8, DF-9).

    Guarantees deterministic composite `fid` uniqueness across multi-category duplicate HCADs,
    validated ContentDM integer page pointer URLs, and finite countywide coordinates `[lng, lat]`
    for all standing historic contradictions while deduplicating repetitive category boilerplate
    into top-level `defaults` so the single-line bundle stays well under 1.0 MiB.
    """
    min_lng, min_lat, max_lng, max_lat = HARRIS_COUNTY_BBOX
    seen_fids: set[str] = set()
    items: list[dict[str, Any]] = []
    cat_counts: dict[str, int] = Counter()

    default_actions: dict[str, str] = {
        "CITY_DIRECTORY_EARLIER_THAN_HCAD": "Verify same structure on Sanborn map and promote earliest directory year in Approved_Edits",
        "CITY_DIRECTORY_ABSENT_THROUGH_1926": "Check 1928-1935 City Directories & 1924-1951 Sanborn sheets for exact post-1926 construction year",
        "LANDMARK_VS_ATLAS_YEAR_MISMATCH": "Disambiguate primary landmark footprint vs. secondary buildings or later remodel year in Approved_Edits",
        "ADDRESS_STREET_MISMATCH": "Confirm corner-lot secondary frontage or reassign to adjacent HCAD account",
        "SUSPECTED_UNMARKED_LANDMARK_DEMOLITION": "Verify via Street View / HAHC minutes; if demolished, mark demolished and move historic record to Lost Houston",
        "ARCHITECT_CAREER_WINDOW_CHECK": "Check whether architect designed a later remodel/addition or whether HCAD year reflects a replacement building",
    }
    default_sources: dict[str, str] = {
        "CITY_DIRECTORY_EARLIER_THAN_HCAD": "Houston City Directory",
        "CITY_DIRECTORY_ABSENT_THROUGH_1926": "Houston City Directory",
        "LANDMARK_VS_ATLAS_YEAR_MISMATCH": "COH Landmark Designation Report",
        "ADDRESS_STREET_MISMATCH": "HCAD Parcel GIS",
        "SUSPECTED_UNMARKED_LANDMARK_DEMOLITION": "COH Landmark Designation Report",
        "ARCHITECT_CAREER_WINDOW_CHECK": "Architectural Monograph / Handbook of Texas",
    }

    for row in findings:
        fid = str(row.get("finding_id") or "").strip()
        if not fid:
            raise ValueError(f"Audit finding missing finding_id: {row}")
        if fid in seen_fids:
            raise ValueError(f"Duplicate composite finding_id in audit queue: {fid}")
        seen_fids.add(fid)

        cat = str(row.get("category") or "").strip()
        cat_counts[cat] += 1

        lng = row.get("lng")
        lat = row.get("lat")
        ll_pair: list[float] | None = None
        if lng is not None and lat is not None:
            lng_f, lat_f = round(float(lng), 6), round(float(lat), 6)
            if min_lng <= lng_f <= max_lng and min_lat <= lat_f <= max_lat:
                ll_pair = [lng_f, lat_f]

        hcad_s = str(row.get("hcad_num") or "")
        bid_s = str(row.get("building_id") or hcad_s)
        ay_i = int(row.get("atlas_year") or 0)
        hy_i = int(row.get("hcad_year") or 0)
        sy_i = int(row.get("suggested_year") or 0)
        dist_s = sanitize_atlas_text(str(row.get("district_or_context") or ""))
        cnt_s = sanitize_atlas_text(str(row.get("contributing") or "Outside Historic District"))
        claim_s = sanitize_atlas_text(str(row.get("displayed_claim") or ""))
        ev_s = sanitize_atlas_text(str(row.get("conflicting_evidence") or ""))

        if cat == "CITY_DIRECTORY_EARLIER_THAN_HCAD":
            claim_s = ""
            # Keep concise page/excerpt hint here; full OCR quote hydrates from citydir_index.json in UI
            m_pg = re.search(r"\(page\s+\d+\):\s*(.+)$", ev_s)
            pg_lbl_m = re.search(r"\((page\s+\d+)\)", ev_s)
            if m_pg:
                snip = m_pg.group(1).strip().rstrip(")")
                snip = (snip[:40] + "...") if len(snip) > 40 else snip
                pg_lbl = f", {pg_lbl_m.group(1)}" if pg_lbl_m else ""
                ev_s = f"{sy_i} City Dir ({ay_i - sy_i}y earlier{pg_lbl}): {snip}"
            elif len(ev_s) > 80:
                ev_s = ev_s[:77] + "..."
        elif cat == "CITY_DIRECTORY_ABSENT_THROUGH_1926":
            claim_s = ""
            ev_s = ""

        url_s = str(row.get("primary_source_url") or "")
        raw_cdm_ptr = row.get("cdm_pointer")
        if raw_cdm_ptr not in (None, "", 0, "0"):
            if isinstance(raw_cdm_ptr, bool) or not str(raw_cdm_ptr).strip().isdigit():
                raise ValueError(f"Non-integer ContentDM cdm_pointer on {fid}: {raw_cdm_ptr!r}")
            ptr = int(str(raw_cdm_ptr).strip())
        else:
            ptr = 0
        if "cdm17006.contentdm.oclc.org/digital/collection/citydir/id/" in url_s:
            cdm_seg = url_s.split("/collection/citydir/id/", 1)[1].split("/", 1)[0]
            if not cdm_seg.isdigit() or int(cdm_seg) <= 0:
                raise ValueError(f"Invalid non-integer ContentDM item URL on {fid}: {url_s!r}")
        if cat == "CITY_DIRECTORY_ABSENT_THROUGH_1926" and ptr == 0 and "/search/searchterm/" in url_s:
            url_s = ""

        item: dict[str, Any] = {
            "fid": fid,
            "p": int(row.get("priority_score") or 0),
            "cat": cat,
            "h": hcad_s,
            "a": sanitize_atlas_text(str(row.get("address") or "")),
            "ll": ll_pair,
            "ay": ay_i,
        }
        if claim_s:
            item["cl"] = claim_s
        if ev_s:
            item["ev"] = ev_s
        if url_s:
            item["u"] = url_s
        if bid_s and bid_s != hcad_s:
            item["bid"] = bid_s
        if dist_s and dist_s not in ("Historic Core", "Outside Historic District"):
            item["d"] = dist_s
        if cnt_s and cnt_s != "Outside Historic District":
            item["cnt"] = cnt_s
        if hy_i and hy_i != ay_i:
            item["hy"] = hy_i
        if sy_i > 0:
            item["sy"] = sy_i
        st_s = sanitize_atlas_text(str(row.get("suggested_source_type") or ""))
        if st_s and st_s != default_sources.get(cat, ""):
            item["st"] = st_s
        act_s = sanitize_atlas_text(str(row.get("recommended_action") or ""))
        if cat != "CITY_DIRECTORY_EARLIER_THAN_HCAD" and act_s and act_s != default_actions.get(cat, ""):
            item["act"] = act_s
        bname = sanitize_atlas_text(str(row.get("building_name") or ""))
        if bname:
            item["n"] = bname
        if ptr > 0:
            item["dp"] = ptr
        lmc = sanitize_atlas_text(str(row.get("landmark_code") or ""))
        if lmc:
            item["lmc"] = lmc
        arc = sanitize_atlas_text(str(row.get("architect") or ""))
        if arc:
            item["arc"] = arc
        items.append(item)

    return {
        "v": 1,
        "generated_on": generated_on,
        "total": len(items),
        "categories": dict(cat_counts),
        "defaults": {
            "district": "Undesignated / Outside HD",
            "contributing": "Outside Historic District",
            "absent_1926_evidence": "Address audited as unimproved / absent through the 1926 Morrison & Fourmy Houston City Directory",
            "cdm_search_prefix": "https://cdm17006.contentdm.oclc.org/digital/collection/citydir/search/searchterm/",
            "actions": default_actions,
            "sources": default_sources,
        },
        "items": items,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run deterministic historical accuracy audit.")
    parser.add_argument("--apply", action="store_true", help="Write reconciled fixes and audit outputs to disk.")
    parser.add_argument("--dry-run", action="store_true", help="Report findings without modifying files.")
    args = parser.parse_args()

    apply_mode = bool(args.apply and not args.dry_run)
    res = run_audit(apply_fixes=apply_mode)
    summary = res["summary"]
    print(f"Mode: {'APPLY' if apply_mode else 'DRY-RUN'}")
    print("Reconciled fixes:", json.dumps(summary["reconciled_fixes"], indent=2))
    print("HCAD rounded decade estimates in Historic Core:", summary["historic_core_hcad_rounded_decade_estimates"])
    print("Total queued contradictions:", summary["total_queued_contradictions"])
    print("Contradictions by category:", json.dumps(summary["contradictions_by_category"], indent=2))
    print("\nTop 15 Priority Audit Queue items:")
    for item in res["findings"][:15]:
        print(
            f"  [{item['priority_score']}] {item['category']} | {item['hcad_num']} | "
            f"{item['address']} ({item['building_name']}) -> {item['conflicting_evidence']}"
        )


if __name__ == "__main__":
    main()
