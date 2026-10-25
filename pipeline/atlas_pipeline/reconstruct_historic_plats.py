#!/usr/bin/env python3
"""
Reconstructs Pre-Replat Historic Subdivision Boundaries ("De-Replatting") and
Calibrates Original Platting Years across Harris County Recording Series:

1. Decodes Harris County's four archival recording sequences:
   - Pre-1905 & Post-1905 Harris County Deed Records (DR Vol. A-Z & Vol. 1-3100)
   - Post-1905 Harris County Map Records (Map Book Vol. 1/1A-75)
   - Early Harris County Clerk Instrument Numbers (#1-#2,200,000) & Film Codes
   - HCAD Tax Assessor Alphabetical Block Books (001-038) & Chronological Volumes (050-152)
   Anchored against ~60 primary-source Houston subdivision platting dates.

2. Geometrically reconstructs unbroken historic parent subdivision footprints in
   EPSG:2278 (Texas South Central ft) across `overlays.json["platted_subdivisions"]`:
   - Pass 1: Fills interior modern townhome/commercial replat doughnut holes (<= 15 acres)
     while subtracting any genuine neighboring historic subdivision inside.
   - Pass 2: Absorbs enclosed and edge-notched modern infill replats (< 2.2 acres,
     Vol_Page >= 115-000 or post-1978 Clerk filings / R/P cutouts) from `Sub_poly.zip`
     uniquely into their best-matching historic parent addition.
   - Pass 3: Applies an 8-ft miter survey-sliver weld while enforcing strict zero-new-overlap
     disjointness against neighboring historic parent subdivisions.

3. Suppresses absorbed < 2.2-acre modern infill replats and standalone < 1.5-acre
   post-1985 townhome replat cutouts from cluttering `overlays.json["platted_subdivisions"]`
   and `search_index.json` (`overlay_layer == "plattedSubdivisions"`), while preserving
   all other downstream layers in `overlays.json` and `search_index.json`.
"""

from collections import Counter, defaultdict
import glob
import json
import os
from pathlib import Path
import pickle
import re

import numpy as np
import pyogrio
from pyproj import Transformer
from shapely import make_valid, points, transform as shp_transform
from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree

ROOT_DIR = str(Path(__file__).resolve().parents[2])
CACHE_DIR = os.path.join(ROOT_DIR, "pipeline", "cache")
DATA_DIR = os.path.join(ROOT_DIR, "app", "public", "data")

BRACEY_1922_URL = "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/21604"

# Verified primary-source platting dates for landmark Houston historic subdivisions.
# Tuple format: (year, is_exact, basis_note, record_series_override_or_empty)
ANCHORS_BY_VOL_PAGE: dict[str, tuple[int, bool, str, str]] = {
    "001-001": (
        1836,
        True,
        "Original August 1836 Borden & Gail Borden Survey of Houston (Allen Brothers Plat)",
        "Pre-1905 Harris County Deed Records (DR)",
    ),
    "003-069": (
        1839,
        False,
        "Early Houston North Side Buffalo Bayou (NSBB) Survey (c. 1839)",
        "Pre-1905 Harris County Deed Records (DR)",
    ),
    "005-001": (
        1882,
        False,
        "William R. Baker South Side Addition (Deed Records Vol. Y, Pg. 350, c. 1882)",
        "Pre-1905 Harris County Deed Records (DR)",
    ),
    "005-053": (
        1870,
        False,
        "William R. Baker North Side Buffalo Bayou Addition (Pre-1905 Deed Records, c. 1870)",
        "Pre-1905 Harris County Deed Records (DR)",
    ),
    "007-185": (
        1889,
        False,
        "Town of Brunner Addition (Deed Records Vol. 42, Pg. 26, c. 1889)",
        "Pre-1905 Harris County Deed Records (DR)",
    ),
    "021-044": (
        1892,
        True,
        "Omaha & South Texas Land Co. Plat of Houston Heights (1892; transcribed to Map Records Vol. 1A, Pg. 114)",
        "Pre-1905 Harris County Deed Records (DR) & 1905 Map Book Vol. 1A",
    ),
    "039-114": (
        1896,
        False,
        "Houston Heights Annex Addition (c. 1896)",
        "Pre-1905 Harris County Deed Records (DR)",
    ),
    "014-053": (
        1893,
        False,
        "Deed Records Vol. 69, Pg. 368 (Pre-1905 Harris County Deed Book, c. 1893)",
        "Pre-1905 Harris County Deed Records (DR)",
    ),
    "018-001": (
        1895,
        True,
        "Hyde Park Improvement Co. Plat along the Westheimer / Montrose streetcar line (1895, Pre-1905 Deed Records)",
        "Pre-1905 Harris County Deed Records (DR)",
    ),
    "018-034": (
        1906,
        False,
        "Hyde Park Annex Addition (c. 1906)",
        "Harris County Map Records (MR)",
    ),
    "018-047": (
        1907,
        True,
        "Map Book Vol. 2, Pg. 31 (Filing #32267, Platted 1907)",
        "Harris County Map Records (MR)",
    ),
    "037-029": (
        1902,
        True,
        "South End Land Co. (W. W. Baldwin) Plat of Westmoreland (1902, Pre-1905 Deed Records)",
        "Pre-1905 Harris County Deed Records (DR)",
    ),
    "010-249": (
        1906,
        True,
        "Courtlandt Improvement Co. Plat of Courtlandt Place (1906, Filing #29340)",
        "Harris County Map Records (MR)",
    ),
    "004-132": (
        1907,
        True,
        "Avondale Addition (Platted 1907, Instrument #32417)",
        "Harris County Map Records (MR)",
    ),
    "037-266": (
        1907,
        True,
        "Map Book Vol. 2, Pg. 33 (Platted October 1907 by William A. Wilson Realty Co., #32892)",
        "Harris County Map Records (MR)",
    ),
    "037-296": (
        1907,
        True,
        "Map Book Vol. 2, Pg. 41 (Highland Park / Germantown, Platted 1907)",
        "Harris County Map Records (MR)",
    ),
    "037-302": (
        1909,
        True,
        "Map Book Vol. 2, Pg. 73 (Woodland Terrace, Platted 1909)",
        "Harris County Map Records (MR)",
    ),
    "037-311": (
        1909,
        True,
        "Map Book Vol. 3, Pg. 38 (Woodland Heights Annex, Platted 1909, #68874)",
        "Harris County Map Records (MR)",
    ),
    "035-072": (
        1909,
        True,
        "Map Book Vol. 3, Pg. 43 (Sunset Heights, Platted 1909, #71619)",
        "Harris County Map Records (MR)",
    ),
    "025-088": (
        1909,
        True,
        "Magnolia Park Addition along Houston Ship Channel (1909; Map Book Vol. 4, Pg. 69)",
        "Harris County Map Records (MR)",
    ),
    "037-308": (
        1910,
        True,
        "Willborg Addition (Platted 1910)",
        "Harris County Map Records (MR)",
    ),
    "037-041": (
        1910,
        True,
        "Map Book Vol. 4, Pg. 21 (Westmoreland Farms, Platted 1910, #58985)",
        "Harris County Map Records (MR)",
    ),
    "013-012": (
        1911,
        True,
        "Map Book Vol. 4, Pg. 42 (William A. Wilson Co. Plat of Eastwood, 1911)",
        "Harris County Map Records (MR)",
    ),
    "026-127": (
        1911,
        True,
        "Houston Land Corporation (J. W. Link) Plat of Montrose (1911, Instrument #90214)",
        "Harris County Map Records (MR)",
    ),
    "050-226": (
        1916,
        True,
        "J. S. Cullinan Plat of Shadyside adjacent to Hermann Park (1916)",
        "Harris County Map Records (MR)",
    ),
    "038-236": (
        1920,
        False,
        "Cherryhurst Addition in Neartown / Montrose (c. 1920)",
        "Harris County Map Records (MR)",
    ),
    "062-075": (
        1920,
        True,
        "Map Book Vol. 6, Pg. 3 (Norhill Addition by William A. Wilson, 1920, #81062)",
        "Harris County Map Records (MR)",
    ),
    "052-221": (
        1920,
        True,
        "Map Book Vol. 6, Pg. 4 (Mandell Place Addition, 1920, #84772)",
        "Harris County Map Records (MR)",
    ),
    "056-113": (
        1921,
        True,
        "Map Book Vol. 6, Pg. 33 (Norhill Park Addition, 1921)",
        "Harris County Map Records (MR)",
    ),
    "054-217": (
        1922,
        True,
        "Winlow Place Addition (Platted 1922, Instrument #135271)",
        "Harris County Map Records (MR)",
    ),
    "053-041": (
        1922,
        True,
        "Deed Records Vol. 516, Pg. 429 (Ormond Place by W. T. Carter Co., 1922, #129128)",
        "Harris County Deed Records (DR)",
    ),
    "052-060": (
        1923,
        True,
        "Deed Records Vol. 516, Pg. 543 (Hyde Park Main Addition, 1923)",
        "Harris County Deed Records (DR)",
    ),
    "053-039": (
        1923,
        True,
        "Deed Records Vol. 526, Pg. 497 (Broadacres by E. H. Fleming, 1923, #108866)",
        "Harris County Deed Records (DR)",
    ),
    "054-067": (
        1923,
        True,
        "E. H. Fleming Plat of Southampton Place (1923, Instrument #141918)",
        "Harris County Map Records (MR)",
    ),
    "053-033": (
        1924,
        True,
        "Map Book Vol. 7, Pg. 19 / Vol. 534, Pg. 286 (Edgemont, Platted 1924)",
        "Harris County Map Records (MR)",
    ),
    "062-113": (
        1924,
        True,
        "Map Book Vol. 7, Pg. 17 (North Norhill, Platted 1924, #198966)",
        "Harris County Map Records (MR)",
    ),
    "056-121": (
        1924,
        True,
        "Map Book Vol. 7, Pg. 27 (Dearborn Place, Platted 1924, #208865)",
        "Harris County Map Records (MR)",
    ),
    "057-031": (
        1924,
        True,
        "Map Book Vol. 7, Pg. 35 (Alden Place, Platted 1924)",
        "Harris County Map Records (MR)",
    ),
    "060-152": (
        1924,
        True,
        "River Oaks Section 1 (Platted 1924 by Will & Mike Hogg and Hugh Potter, #251333)",
        "Harris County Map Records (MR)",
    ),
    "061-139": (
        1924,
        True,
        "Riverside Terrace Section 1 (Platted 1924 by Henry F. MacGregor, #152035)",
        "Harris County Map Records (MR)",
    ),
    "055-086": (
        1925,
        True,
        "Map Book Vol. 7, Pg. 57 (Cherokee Addition, Platted 1925)",
        "Harris County Map Records (MR)",
    ),
    "062-204": (
        1928,
        True,
        "Map Book Vol. 9, Pg. 8 (Idylwood Addition along Brays Bayou, 1928, #330080)",
        "Harris County Map Records (MR)",
    ),
    "066-045": (
        1937,
        True,
        "Map Book Vol. 14, Pg. 5 (E. L. Crain Plat of Garden Oaks, 1937)",
        "Harris County Map Records (MR)",
    ),
    "080-073": (
        1952,
        True,
        "Map Book Vol. 41, Pg. 17 (Denver Harbor Section 1, 1952, #1086409)",
        "Harris County Map Records (MR)",
    ),
    "085-031": (
        1953,
        True,
        "Map Book Vol. 49, Pg. 19 (Glenbrook Valley, Platted 1953, #1437339)",
        "Harris County Map Records (MR)",
    ),
    "083-381": (
        1955,
        True,
        "Frank Sharp Plat of Sharpstown Section 1 (1955, Instrument #1327800)",
        "Harris County Map Records (MR)",
    ),
    "095-379": (
        1955,
        True,
        "George Meyer Plat of Meyerland (1955, Map Book Vol. 36, Pg. 12)",
        "Harris County Map Records (MR)",
    ),
}

ANCHORS_BY_FEATURE_ID: dict[str, tuple[int, bool, str, str]] = {
    "plat_5512_ssbb": (
        1836,
        True,
        "Original August 1836 Borden Survey of Houston (South Side Buffalo Bayou)",
        "Pre-1905 Harris County Deed Records (DR)",
    ),
    "plat_0290_baker_w_r_nsbb": (
        1868,
        False,
        "William R. Baker North Side Buffalo Bayou Addition (Pre-1905 Deed Records, c. 1868)",
        "Pre-1905 Harris County Deed Records (DR)",
    ),
    "plat_1798_empire": (
        1893,
        False,
        "Empire Addition on Westheimer Road (Pre-1905 Harris County Deed Records, c. 1893)",
        "Pre-1905 Harris County Deed Records (DR)",
    ),
    "plat_4872_river_oaks_country_club_estate": (
        1924,
        True,
        "River Oaks Country Club Estates (Platted 1924 by Country Club Estates Co.)",
        "Harris County Map Records (MR)",
    ),
}

# Piecewise monotonic calibration curves for Harris County recording books
DR_VOL_CURVE = [
    (1, 1885),
    (42, 1889),
    (69, 1893),
    (100, 1897),
    (160, 1904),
    (220, 1908),
    (300, 1913),
    (357, 1916),
    (428, 1919),
    (516, 1922),
    (526, 1923),
    (650, 1926),
    (725, 1928),
    (855, 1931),
    (1100, 1938),
    (1350, 1944),
    (1559, 1947),
    (2006, 1949),
    (2270, 1951),
    (2612, 1953),
    (3082, 1956),
    (3600, 1959),
]

MR_VOL_CURVE = [
    (1, 1905),
    (2, 1907),
    (3, 1909),
    (4, 1910),
    (5, 1915),
    (6, 1920),
    (7, 1924),
    (8, 1926),
    (9, 1928),
    (10, 1930),
    (12, 1934),
    (14, 1937),
    (20, 1944),
    (27, 1948),
    (36, 1951),
    (41, 1952),
    (49, 1954),
    (60, 1957),
    (70, 1959),
    (75, 1961),
]

INSTRUMENT_CURVE = [
    (5000, 1900),
    (29340, 1906),
    (32892, 1907),
    (58985, 1909),
    (71619, 1910),
    (81062, 1919),
    (90214, 1920),
    (108866, 1922),
    (141918, 1923),
    (152035, 1924),
    (208865, 1924),
    (251333, 1925),
    (330080, 1928),
    (379388, 1947),
    (548544, 1948),
    (663414, 1949),
    (873665, 1950),
    (1086409, 1952),
    (1327800, 1954),
    (1521330, 1956),
    (2200000, 1961),
]

CLERK_LETTER_YEAR = {
    "B": 1964,
    "C": 1968,
    "D": 1972,
    "E": 1976,
    "F": 1979,
    "G": 1980,
    "H": 1982,
    "J": 1984,
    "K": 1986,
    "L": 1988,
    "M": 1990,
    "N": 1992,
    "P": 1994,
    "R": 1995,
    "S": 1997,
    "T": 1999,
    "U": 2000,
    "V": 2001,
    "W": 2003,
    "X": 2004,
    "Y": 2005,
    "Z": 2006,
}

REPLAT_TOKEN_RE = re.compile(
    r"\b(R/P|RP|REPLAT|AMEND|AMENDED|PAR\s+R/P|PARTIAL\s+REPLAT)\b",
    re.I,
)
INFILL_STYLE_NAME_RE = re.compile(
    r"\b(TOWNHOMES?|PATIO\s+HOMES?|CONDO(?:MINIUM)?S?|LOFTS?|VILLAS?|ENCLAVE|COLLECTIVE|MEWS|ESTATES\s+AT|MANORS?\s+AT)\b",
    re.I,
)


def interp_curve(val: float, curve: list[tuple[int, int]]) -> int:
    if val <= curve[0][0]:
        return curve[0][1]
    if val >= curve[-1][0]:
        return curve[-1][1]
    for (x0, y0), (x1, y1) in zip(curve, curve[1:]):
        if x0 <= val <= x1:
            if x1 == x0:
                return y0
            t = (val - x0) / float(x1 - x0)
            return int(round(y0 + t * (y1 - y0)))
    return curve[-1][1]


def clean_str(val) -> str:
    if val is None:
        return ""
    s = str(val).strip()
    return "" if s.lower() in ("none", "null", "nan") else s


def clean_title_name(raw_name: str) -> str:
    s = clean_str(raw_name)
    if not s:
        return ""
    s = re.sub(r"\s+", " ", s)
    words = []
    for w in s.title().split():
        up = w.upper().rstrip(",.")
        if up in ("II", "III", "IV", "VI", "LLC", "LP", "HOA", "R/P", "NSBB", "SSBB", "UR", "U/R"):
            words.append(up)
        elif up in ("OF", "AT", "ON", "THE", "IN", "TO", "AND") and words:
            words.append(up.lower())
        else:
            words.append(w)
    return " ".join(words)


def parse_vp_prefix(vol_page: str) -> int:
    vp = clean_str(vol_page)
    m = re.match(r"^(\d{3})-\d{3}$", vp)
    return int(m.group(1)) if m else 0


def format_plat_citation(recnum: str, vol_page: str) -> str:
    rec = clean_str(recnum)
    vp = clean_str(vol_page)
    parts = []
    if rec:
        m_dr = re.match(r"^0*(\d{1,4})[-]?(\d{3})DR$", rec, re.I)
        if m_dr:
            parts.append(f"Deed Records Vol. {int(m_dr.group(1))}, Pg. {int(m_dr.group(2))}")
        elif re.match(r"^\d{6}$", rec):
            vol = int(rec[:3])
            pg = int(rec[3:])
            if 1 <= vol <= 75 and 1 <= pg <= 150:
                parts.append(f"Map Book Vol. {vol}, Pg. {pg}")
            elif vol >= 500:
                parts.append(f"Plat Film #{rec[:3]}-{rec[3:]}")
            else:
                parts.append(f"Harris County Clerk Filing #{int(rec)}")
        elif re.match(r"^\d{3}-\d{3}$", rec):
            vol = int(rec[:3])
            pg = int(rec[4:])
            if 1 <= vol <= 75 and 1 <= pg <= 150:
                parts.append(f"Map Book Vol. {vol}, Pg. {pg}")
            else:
                parts.append(f"Plat Film #{rec}")
        else:
            parts.append(f"Harris County Clerk Filing #{rec}")
    if vp:
        parts.append(f"HCAD Subdiv. Code {vp}")
    return " · ".join(parts)


def format_deed_citation(deed_num: str) -> str:
    d = clean_str(deed_num)
    if not d:
        return ""
    if re.match(r"^\d{3}-\d{3}$", d):
        return f"Harris County Map Records Film Code #{d}"
    m_letter = re.match(r"^V([A-Z])P0*(\d+)(DR|MR)?$", d, re.I)
    if m_letter:
        vol_let = m_letter.group(1).upper()
        pg = int(m_letter.group(2))
        return f"Harris County Deed Records Vol. {vol_let}, Pg. {pg}"
    m_vm = re.match(r"^V0*(\d+[A-Z]?)P0*(\d+)(DR|MR)?$", d, re.I)
    if m_vm:
        vol_tok = m_vm.group(1).upper()
        pg = int(m_vm.group(2))
        kind = (m_vm.group(3) or "DR").upper()
        rec_type = "Map Records" if kind == "MR" else "Deed Records"
        return f"Harris County {rec_type} Vol. {vol_tok}, Pg. {pg}"
    m_dslash = re.match(r"^D0*(\d+)/0*(\d+)$", d, re.I)
    if m_dslash:
        return f"Harris County Deed Records Vol. {int(m_dslash.group(1))}, Pg. {int(m_dslash.group(2))}"
    if d.upper().startswith("VOL"):
        return f"Harris County Deed Records {d}"
    if "/" in d:
        return f"Recorded {d}"
    if d.upper().startswith("RP-") or re.match(r"^[A-Z]\d{6,8}$", d, re.I) or len(d) >= 8:
        return f"Harris County Clerk File #{d}"
    return f"Harris County Clerk Instrument #{d}"


def extract_modern_filing_year(deed_num: str, recnum: str) -> int:
    for tok in (clean_str(deed_num), clean_str(recnum)):
        if not tok:
            continue
        m_rp = re.match(r"^RP-(20\d{2})-\d+$", tok, re.I)
        if m_rp:
            return int(m_rp.group(1))
        m_20 = re.match(r"^(20\d{2})\d{7}$", tok)
        if m_20:
            yr = int(m_20.group(1))
            if 2000 <= yr <= 2027:
                return yr
        m_let = re.match(r"^([B-Z])\d{6}$", tok.upper())
        if m_let and m_let.group(1) in CLERK_LETTER_YEAR:
            return CLERK_LETTER_YEAR[m_let.group(1)]
    return 0


def parse_dr_mr_signal(deed_num: str, recnum: str) -> tuple[int, str, str]:
    """Returns (estimated_year, basis, record_series) from DR/MR book citations if present."""
    d = clean_str(deed_num)
    r = clean_str(recnum)

    # 1. Letter Deed Book Vol. A-Z (1838-1884)
    m_let = re.match(r"^V([A-Z])P0*(\d+)(DR)?$", d, re.I)
    if m_let:
        let = m_let.group(1).upper()
        pg = int(m_let.group(2))
        idx = ord(let) - ord("A")
        yr = int(round(1838 + (idx / 25.0) * (1884 - 1838)))
        return (
            yr,
            f"Harris County Deed Records Vol. {let}, Pg. {pg} (1838-1884 Letter Deed Book)",
            "Pre-1905 Harris County Deed Records (DR)",
        )

    # 2. Numbered Deed or Map Record V###P###DR / MR
    m_vm = re.match(r"^V0*(\d+)([A-Z]?)P0*(\d+)(DR|MR)?$", d, re.I)
    if m_vm:
        vol_num = int(m_vm.group(1))
        vol_suf = m_vm.group(2).upper()
        pg = int(m_vm.group(3))
        kind = (m_vm.group(4) or "DR").upper()
        if kind == "MR" and vol_num <= 85:
            yr = interp_curve(vol_num, MR_VOL_CURVE)
            return (
                yr,
                f"Harris County Map Records Vol. {vol_num}{vol_suf}, Pg. {pg}",
                "Harris County Map Records (MR)",
            )
        yr = interp_curve(vol_num, DR_VOL_CURVE)
        series = (
            "Pre-1905 Harris County Deed Records (DR)"
            if vol_num <= 160
            else "Harris County Deed Records (DR)"
        )
        return (
            yr,
            f"Harris County Deed Records Vol. {vol_num}{vol_suf}, Pg. {pg}",
            series,
        )

    # 3. D###/### Deed Record shorthand
    m_ds = re.match(r"^D0*(\d+)/0*(\d+)$", d, re.I)
    if m_ds:
        vol_num = int(m_ds.group(1))
        pg = int(m_ds.group(2))
        yr = interp_curve(vol_num, DR_VOL_CURVE)
        series = (
            "Pre-1905 Harris County Deed Records (DR)"
            if vol_num <= 160
            else "Harris County Deed Records (DR)"
        )
        return (yr, f"Harris County Deed Records Vol. {vol_num}, Pg. {pg}", series)

    # 4. RECNUM ### ###DR
    m_rdr = re.match(r"^0*(\d{1,4})[-]?(\d{3})DR$", r, re.I)
    if m_rdr:
        vol_num = int(m_rdr.group(1))
        pg = int(m_rdr.group(2))
        yr = interp_curve(vol_num, DR_VOL_CURVE)
        series = (
            "Pre-1905 Harris County Deed Records (DR)"
            if vol_num <= 160
            else "Harris County Deed Records (DR)"
        )
        return (yr, f"Harris County Deed Records Vol. {vol_num}, Pg. {pg}", series)

    # 5. RECNUM Map Book Vol. 1-75 (001001-075150 or 001-001-075-150)
    m_mr = re.match(r"^(\d{3})[-]?(\d{3})$", r)
    if m_mr:
        vol_num = int(m_mr.group(1))
        pg = int(m_mr.group(2))
        if 1 <= vol_num <= 75 and 1 <= pg <= 150:
            yr = interp_curve(vol_num, MR_VOL_CURVE)
            return (
                yr,
                f"Harris County Map Book Vol. {vol_num}, Pg. {pg}",
                "Harris County Map Records (MR)",
            )

    return (0, "", "")


def estimate_plat_metadata(
    props: dict,
    catalog_by_id: dict,
    catalog_by_name: dict,
    standing_years: list[int] | None = None,
) -> dict:
    """Computes calibrated platting year, exactness, basis, and Harris County record series."""
    fid = clean_str(props.get("id"))
    vp = clean_str(props.get("vol_page"))
    rec = clean_str(props.get("recnum"))
    deed = clean_str(props.get("deed_num"))
    nm = clean_str(props.get("name"))
    fn = clean_str(props.get("full_name") or nm)
    vp_prefix = parse_vp_prefix(vp)
    is_replat_named = bool(REPLAT_TOKEN_RE.search(fn) or REPLAT_TOKEN_RE.search(nm))
    mod_yr = extract_modern_filing_year(deed, rec)

    # 1. Check primary-source anchor tables first (only if not a modern partial replat sharing name)
    if not (is_replat_named and mod_yr >= 1975):
        if fid in ANCHORS_BY_FEATURE_ID:
            yr, ex, basis, ser = ANCHORS_BY_FEATURE_ID[fid]
            return {
                "estimated_plat_year": yr,
                "plat_year_is_exact": ex,
                "plat_year_label": str(yr) if ex else f"c. {yr}",
                "plat_year_basis": basis,
                "record_series": ser,
            }
        if vp and vp in ANCHORS_BY_VOL_PAGE:
            yr, ex, basis, ser = ANCHORS_BY_VOL_PAGE[vp]
            return {
                "estimated_plat_year": yr,
                "plat_year_is_exact": ex,
                "plat_year_label": str(yr) if ex else f"c. {yr}",
                "plat_year_basis": basis,
                "record_series": ser,
            }

    # 2. Check curated deed restriction catalog
    cat = catalog_by_id.get(fid) or catalog_by_name.get(nm.upper())
    if cat and cat.get("plat_year") and not (is_replat_named and mod_yr >= 1980):
        yr = int(cat["plat_year"])
        ser = (
            "Pre-1905 Harris County Deed Records (DR)"
            if yr <= 1904
            else "Harris County Map Records (MR)"
        )
        return {
            "estimated_plat_year": yr,
            "plat_year_is_exact": True,
            "plat_year_label": str(yr),
            "plat_year_basis": cat.get("plat_citation") or f"Verified Subdivision Dedication ({yr})",
            "record_series": ser,
        }

    # Compute structural floor year from verified standing buildings (2nd earliest or 10th pct)
    valid_yrs = sorted(y for y in (standing_years or []) if 1836 <= y <= 2026)
    pre1965_cnt = sum(1 for y in valid_yrs if y < 1965)
    pre1940_cnt = sum(1 for y in valid_yrs if y < 1940)
    structural_cap = 0
    if len(valid_yrs) >= 5 and pre1965_cnt >= 3:
        structural_cap = valid_yrs[min(2, len(valid_yrs) - 1)]
    elif len(valid_yrs) >= 2 and pre1940_cnt >= 2:
        structural_cap = valid_yrs[1]

    # 3. Explicit Deed Record (DR) or Map Book (MR) volume signal
    dr_mr_yr, dr_mr_basis, dr_mr_series = parse_dr_mr_signal(deed, rec)
    if dr_mr_yr > 0:
        yr = dr_mr_yr
        basis = f"{dr_mr_basis} (calibrated volume chronology)"
        if structural_cap and structural_cap < yr - 3 and not is_replat_named:
            yr = structural_cap
            basis = f"{dr_mr_basis} (bounded by {yr} standing structures)"
        ser = (
            "Pre-1905 Harris County Deed Records (DR)"
            if yr <= 1904 and "Map" not in dr_mr_series
            else dr_mr_series
        )
        return {
            "estimated_plat_year": yr,
            "plat_year_is_exact": False,
            "plat_year_label": f"c. {yr}",
            "plat_year_basis": basis,
            "record_series": ser,
        }

    # 4. Modern Clerk filing on modern Vol_Page (>= 095) or explicit R/P
    if mod_yr > 0 and (vp_prefix >= 95 or is_replat_named or pre1965_cnt <= 1):
        return {
            "estimated_plat_year": mod_yr,
            "plat_year_is_exact": mod_yr >= 2007,
            "plat_year_label": str(mod_yr) if mod_yr >= 2007 else f"c. {mod_yr}",
            "plat_year_basis": f"Harris County Clerk Real Property / Plat Filing ({deed or rec})",
            "record_series": "Harris County Clerk Film Code / Real Property (RP)",
        }

    # 5. Early pure-numeric Harris County Clerk Instrument # (1900-1961)
    if re.match(r"^\d{4,7}$", deed):
        num = int(deed)
        if 1000 <= num <= 2200000 and (vp_prefix == 0 or vp_prefix <= 94):
            yr = interp_curve(num, INSTRUMENT_CURVE)
            basis = f"Harris County Clerk Instrument #{num} (chronological filing series)"
            if structural_cap and structural_cap < yr - 3 and not is_replat_named:
                yr = structural_cap
                basis = f"Harris County Clerk Instrument #{num} (bounded by {yr} standing structures)"
            ser = (
                "Pre-1905 Harris County Deed Records (DR)"
                if yr <= 1904
                else "Harris County Map Records (MR)"
            )
            return {
                "estimated_plat_year": yr,
                "plat_year_is_exact": False,
                "plat_year_label": f"c. {yr}",
                "plat_year_basis": basis,
                "record_series": ser,
            }

    # 6. HCAD Block Book prefix calibration + standing historic building floor
    if 1 <= vp_prefix <= 38:
        # Alphabetical c. 1915 Harris County Tax Assessor Block Books (pre-1920 plats)
        yr = structural_cap if (1850 <= structural_cap <= 1918) else 1910
        ser = (
            "Pre-1905 Harris County Deed Records (DR)"
            if yr <= 1904
            else "Harris County Tax Assessor Block Books (Series CR001) & Map Records"
        )
        return {
            "estimated_plat_year": yr,
            "plat_year_is_exact": False,
            "plat_year_label": f"c. {yr}",
            "plat_year_basis": f"Pre-1920 Harris County Alphabetical Block Book ({vp}) & extant structure chronology",
            "record_series": ser,
        }
    if 39 <= vp_prefix <= 49:
        yr = min(1918, structural_cap) if structural_cap else 1917
        return {
            "estimated_plat_year": yr,
            "plat_year_is_exact": False,
            "plat_year_label": f"c. {yr}",
            "plat_year_basis": f"HCAD Block Book {vp} (c. 1915-1920 series)",
            "record_series": "Harris County Map Records (MR)",
        }
    if 50 <= vp_prefix <= 66:
        yr = int(round(1920 + ((vp_prefix - 50) / 16.0) * 19))
        if structural_cap and structural_cap < yr:
            yr = structural_cap
        return {
            "estimated_plat_year": yr,
            "plat_year_is_exact": False,
            "plat_year_label": f"c. {yr}",
            "plat_year_basis": f"HCAD Chronological Plat Book {vp} (1920-1939 series)",
            "record_series": "Harris County Map Records (MR)",
        }
    if 67 <= vp_prefix <= 94:
        yr = int(round(1940 + ((vp_prefix - 67) / 27.0) * 22))
        if structural_cap and structural_cap < yr:
            yr = structural_cap
        return {
            "estimated_plat_year": yr,
            "plat_year_is_exact": False,
            "plat_year_label": f"c. {yr}",
            "plat_year_basis": f"HCAD Chronological Plat Book {vp} (1940-1962 series)",
            "record_series": "Harris County Map Records (MR)",
        }
    if 95 <= vp_prefix <= 114:
        yr = int(round(1963 + ((vp_prefix - 95) / 19.0) * 20))
        return {
            "estimated_plat_year": yr,
            "plat_year_is_exact": False,
            "plat_year_label": f"c. {yr}",
            "plat_year_basis": f"HCAD Plat Book {vp} (1963-1983 Film Code series)",
            "record_series": "Harris County Map Records Film Code",
        }
    if 115 <= vp_prefix <= 129:
        yr = mod_yr or int(round(1984 + ((vp_prefix - 115) / 14.0) * 22))
        return {
            "estimated_plat_year": yr,
            "plat_year_is_exact": False,
            "plat_year_label": f"c. {yr}",
            "plat_year_basis": f"HCAD Plat Book {vp} (1984-2006 series)",
            "record_series": "Harris County Clerk Film Code",
        }
    if vp_prefix >= 130:
        yr = mod_yr or min(2026, int(round(2007 + ((vp_prefix - 130) / 22.0) * 19)))
        return {
            "estimated_plat_year": yr,
            "plat_year_is_exact": mod_yr >= 2007,
            "plat_year_label": str(yr) if mod_yr >= 2007 else f"c. {yr}",
            "plat_year_basis": f"HCAD Plat Book {vp} (Post-2006 series)",
            "record_series": "Harris County Clerk Real Property (RP)",
        }

    # Cadastral-dissolve plat without vol_page: infer from standing structures
    if structural_cap:
        ser = (
            "Pre-1905 Harris County Deed Records (DR)"
            if structural_cap <= 1904
            else "Harris County Map Records (MR)"
        )
        return {
            "estimated_plat_year": structural_cap,
            "plat_year_is_exact": False,
            "plat_year_label": f"c. {structural_cap}",
            "plat_year_basis": f"Inferred from earliest standing historic structures ({structural_cap})",
            "record_series": ser,
        }

    ey = int(props.get("earliest_year") or props.get("median_year") or 1950)
    if 1836 <= ey <= 2026:
        ser = (
            "Pre-1905 Harris County Deed Records (DR)"
            if ey <= 1904
            else "Harris County Map Records (MR)"
        )
        return {
            "estimated_plat_year": ey,
            "plat_year_is_exact": False,
            "plat_year_label": f"c. {ey}",
            "plat_year_basis": f"Bounded by earliest recorded structure ({ey})",
            "record_series": ser,
        }
    return {
        "estimated_plat_year": 0,
        "plat_year_is_exact": False,
        "plat_year_label": "",
        "plat_year_basis": "",
        "record_series": "Harris County Plat Records",
    }


def round_coords(coords, precision=5):
    if isinstance(coords, dict):
        out = dict(coords)
        if "coordinates" in out and out["coordinates"] is not None:
            out["coordinates"] = round_coords(out["coordinates"], precision)
        if "geometries" in out and out["geometries"] is not None:
            out["geometries"] = [round_coords(g, precision) for g in out["geometries"]]
        return out
    if isinstance(coords, (float, int, np.floating, np.integer)):
        return round(float(coords), precision)
    if isinstance(coords, (list, tuple)):
        return [round_coords(c, precision) for c in coords]
    return coords


def filter_polygonal(geom, min_area: float = 50.0):
    """Extracts clean Polygon/MultiPolygon components >= min_area."""
    if geom is None or geom.is_empty:
        return Polygon()
    geom = make_valid(geom)
    polys = []
    if geom.geom_type == "Polygon":
        polys.append(geom)
    elif geom.geom_type == "MultiPolygon":
        polys.extend(geom.geoms)
    elif geom.geom_type == "GeometryCollection":
        for sub in geom.geoms:
            if sub.geom_type == "Polygon":
                polys.append(sub)
            elif sub.geom_type == "MultiPolygon":
                polys.extend(sub.geoms)
    polys = [p for p in polys if not p.is_empty and p.area >= min_area]
    if not polys:
        return Polygon()
    if len(polys) == 1:
        return polys[0]
    return MultiPolygon(polys)


def fill_interior_holes_respecting_neighbors(
    geom_ft,
    max_hole_acres: float,
    other_historic_geoms: list,
    other_historic_tree: STRtree | None,
    self_hist_idx: int,
) -> tuple[object, int]:
    """Fills interior doughnut holes <= max_hole_acres while subtracting any other historic plat inside."""
    max_hole_sqft = max_hole_acres * 43560.0
    polys = [geom_ft] if geom_ft.geom_type == "Polygon" else list(geom_ft.geoms)
    rebuilt_parts = []
    holes_healed = 0

    for poly in polys:
        kept_holes = []
        fill_patches = []
        for ring in poly.interiors:
            hole_poly = make_valid(Polygon(ring))
            if hole_poly.is_empty or hole_poly.area > max_hole_sqft:
                kept_holes.append(ring)
                continue
            # Subtract any legitimate neighboring historic subdivision sitting inside this hole
            patch = hole_poly
            if other_historic_tree is not None:
                for hi in other_historic_tree.query(hole_poly):
                    if hi == self_hist_idx:
                        continue
                    og = other_historic_geoms[hi]
                    if og.intersects(patch):
                        patch = patch.difference(og)
                        if patch.is_empty:
                            break
            patch = filter_polygonal(patch)
            if not patch.is_empty and patch.area >= 400.0:
                fill_patches.append(patch)
                holes_healed += 1
            else:
                kept_holes.append(ring)
        base_poly = Polygon(poly.exterior, kept_holes)
        if fill_patches:
            rebuilt_parts.append(unary_union([base_poly] + fill_patches))
        else:
            rebuilt_parts.append(base_poly)

    merged = filter_polygonal(unary_union(rebuilt_parts))
    return merged, holes_healed


def is_modern_infill_replat_row(
    acres: float,
    vp: str,
    rec: str,
    deed: str,
    nm: str,
    fn: str,
) -> bool:
    if acres <= 0.005 or acres > 2.25:
        return False
    vp_prefix = parse_vp_prefix(vp)
    mod_yr = extract_modern_filing_year(deed, rec)
    combined_name = f"{nm} {fn}"
    is_rp = bool(REPLAT_TOKEN_RE.search(combined_name))
    is_infill_style = bool(INFILL_STYLE_NAME_RE.search(combined_name))

    if vp_prefix >= 115:
        return True
    if mod_yr >= 1980 and (is_rp or is_infill_style or acres <= 1.6):
        return True
    if is_rp and (vp_prefix >= 95 or mod_yr >= 1972):
        return True
    return False


def main() -> None:
    print("1. Loading overlays.json, deed_restrictions_catalog.json, and HCAD Sub_poly.zip...")
    overlays_path = os.path.join(DATA_DIR, "overlays.json")
    with open(overlays_path, "r", encoding="utf-8") as f:
        overlays = json.load(f)

    catalog_path = os.path.join(DATA_DIR, "deed_restrictions_catalog.json")
    catalog_by_id = {}
    catalog_by_name = {}
    if os.path.exists(catalog_path):
        with open(catalog_path, "r", encoding="utf-8") as f:
            cat_json = json.load(f)
        catalog_by_id = cat_json.get("subdivisions") or {}
        for cid, entry in catalog_by_id.items():
            sn = clean_str(entry.get("subdivision_name")).upper()
            if sn:
                catalog_by_name[sn] = entry

    tx_2278 = Transformer.from_crs("EPSG:4326", "EPSG:2278", always_xy=True)
    tx_4326 = Transformer.from_crs("EPSG:2278", "EPSG:4326", always_xy=True)

    def proj_to_2278(geom):
        return shp_transform(
            geom,
            lambda coords: np.column_stack(tx_2278.transform(coords[:, 0], coords[:, 1])),
        )

    def proj_to_4326(geom):
        return shp_transform(
            geom,
            lambda coords: np.column_stack(tx_4326.transform(coords[:, 0], coords[:, 1])),
        )

    ps_features = overlays.get("platted_subdivisions", {}).get("features", [])
    print(f"   Existing overlays.platted_subdivisions count: {len(ps_features):,}")

    # Load countywide building points from the 5 shards so we have exact standing-year distributions
    print("2. Loading 1.51M countywide building centroids & years for chronological bounds and healed stats...")
    hcad_sub_pkl = os.path.join(CACHE_DIR, "hcad_subdivisions_2026.pkl")
    hcad_to_sub = {}
    if os.path.exists(hcad_sub_pkl):
        with open(hcad_sub_pkl, "rb") as f:
            hcad_to_sub = pickle.load(f)

    coord_re = re.compile(r'"coordinates":\s*\[\s*\[\s*(?:\[\s*)?([-\d.]+)\s*,\s*([-\d.]+)')
    yr_re = re.compile(r'"year_built":\s*(\d+)')
    hcad_re = re.compile(r'"hcad_num":\s*"([^"]+)"')
    lm_re = re.compile(r'"is_landmark":\s*true')
    gb_re = re.compile(r'"good_brick":\s*true')

    lons_list, lats_list, yrs_list, hcads_list, lms_list, gbs_list = [], [], [], [], [], []
    shard_files = sorted(glob.glob(os.path.join(CACHE_DIR, "aligned_ndjson", "buildings_*.geojsonseq")))
    for sf in shard_files:
        with open(sf, "r", encoding="utf-8") as f:
            for line in f:
                mc = coord_re.search(line)
                if not mc:
                    continue
                lons_list.append(float(mc.group(1)))
                lats_list.append(float(mc.group(2)))
                my = yr_re.search(line)
                yrs_list.append(int(my.group(1)) if my else 0)
                mh = hcad_re.search(line)
                hcads_list.append(mh.group(1).split("#")[0] if mh else "")
                lms_list.append(bool(lm_re.search(line)))
                gbs_list.append(bool(gb_re.search(line)))

    pts_wgs84 = points(np.array(lons_list, dtype=np.float64), np.array(lats_list, dtype=np.float64))
    print(f"   Loaded {len(lons_list):,} building points across {len(shard_files)} countywide shards.")

    # Pre-collect initial standing years per existing platted_subdivision feature
    orig_wgs_geoms = [make_valid(shape(feat["geometry"])) for feat in ps_features]
    orig_wgs_tree = STRtree(orig_wgs_geoms)
    init_years_by_idx: dict[int, list[int]] = defaultdict(list)
    pt_i_arr, poly_i_arr = orig_wgs_tree.query(pts_wgs84, predicate="intersects")
    for pt_i, poly_i in zip(pt_i_arr.tolist(), poly_i_arr.tolist()):
        y = yrs_list[pt_i]
        if 1836 <= y <= 2026:
            init_years_by_idx[poly_i].append(y)

    # Project existing features to EPSG:2278 feet and classify historic parent plats vs modern cutouts
    print("3. Calibrating original platting years & classifying historic parent subdivisions...")
    feat_geoms_ft = []
    feat_acres_orig = []
    is_historic_parent = []
    is_standalone_modern_cutout = []

    for idx, feat in enumerate(ps_features):
        p = feat["properties"]
        g_ft = filter_polygonal(proj_to_2278(orig_wgs_geoms[idx]))
        acres = g_ft.area / 43560.0 if not g_ft.is_empty else 0.0
        feat_geoms_ft.append(g_ft)
        feat_acres_orig.append(acres)

        # Ensure clean citations if not curated
        if not p.get("has_deed_docs"):
            if p.get("recnum") or p.get("vol_page"):
                p["plat_citation"] = format_plat_citation(p.get("recnum", ""), p.get("vol_page", ""))
            if p.get("deed_num"):
                p["deed_citation"] = format_deed_citation(p.get("deed_num", ""))

        meta = estimate_plat_metadata(
            p,
            catalog_by_id,
            catalog_by_name,
            standing_years=init_years_by_idx.get(idx, []),
        )
        p.update(meta)

        vp = clean_str(p.get("vol_page"))
        vp_prefix = parse_vp_prefix(vp)
        rec = clean_str(p.get("recnum"))
        deed = clean_str(p.get("deed_num"))
        nm = clean_str(p.get("name"))
        fn = clean_str(p.get("full_name") or nm)
        mod_yr = extract_modern_filing_year(deed, rec)
        est_yr = int(meta["estimated_plat_year"] or 0)
        has_docs = bool(p.get("has_deed_docs"))

        cadastral_row_artifact = (not has_docs) and bool(
            re.search(
                r"\b(?:ABAND(?:ON(?:ED)?)?\s+ALLEY|DEDICATED\s+PER\s+PLAT|RIGHT\s+OF\s+WAY|UNPLATTED)\b",
                f"{nm} {fn}",
                re.I,
            )
        )
        modern_infill_flag = (not has_docs) and is_modern_infill_replat_row(
            acres, vp, rec, deed, nm, fn
        )
        # Standalone modern < 1.5-acre post-1985 townhome replat cutout check
        cutout_flag = (
            (not has_docs)
            and acres < 1.55
            and (
                vp_prefix >= 115
                or mod_yr >= 1985
                or (est_yr >= 1985 and p.get("pre_1965_count", 0) <= 3)
            )
            and p.get("building_count", 0) < 30
        )

        hist_flag = (
            (not cadastral_row_artifact)
            and (not modern_infill_flag)
            and (not cutout_flag)
            and acres >= 0.75
            and (
                (1836 <= est_yr <= 1965)
                or has_docs
                or (p.get("pre_1965_count", 0) >= 6 and vp_prefix < 115)
            )
        )
        is_historic_parent.append(hist_flag)
        is_standalone_modern_cutout.append(cutout_flag or modern_infill_flag or cadastral_row_artifact)

    hist_indices = [i for i, flag in enumerate(is_historic_parent) if flag and not feat_geoms_ft[i].is_empty]
    print(
        f"   Identified {len(hist_indices):,} Historic Parent Plats (<= 1965) and "
        f"{sum(is_standalone_modern_cutout):,} modern < 2.2-ac infill replat polygons in overlays."
    )

    # Load all 45,376 raw polygons from Sub_poly.zip in EPSG:2278 feet to capture every modern infill replat
    print("4. Reading all 45,376 polygons from Sub_poly.zip (EPSG:2278) for geometric de-replatting...")
    sub_zip_path = os.path.join(CACHE_DIR, "Sub_poly.zip")
    meta_sp, _fids, wkb_geoms, cols = pyogrio.raw.read(sub_zip_path, read_geometry=True)
    col_map = dict(zip(meta_sp["fields"], cols))
    from shapely import from_wkb

    sp_geoms = from_wkb(wkb_geoms)
    infill_replats = []
    for nm_raw, fn_raw, vp_raw, rec_raw, dn_raw, g_raw in zip(
        col_map["Name"],
        col_map["Sub_Name_F"],
        col_map["Vol_Page"],
        col_map["RECNUM"],
        col_map["Deed_Num"],
        sp_geoms,
    ):
        if g_raw is None or g_raw.is_empty:
            continue
        acres = g_raw.area / 43560.0
        if acres <= 0.005 or acres > 2.25:
            continue
        nm = clean_str(nm_raw)
        fn = clean_str(fn_raw) or nm
        vp = clean_str(vp_raw)
        rec = clean_str(rec_raw)
        dn = clean_str(dn_raw)
        if is_modern_infill_replat_row(acres, vp, rec, dn, nm, fn):
            g_clean = filter_polygonal(g_raw)
            if not g_clean.is_empty:
                infill_replats.append(
                    {
                        "name": clean_title_name(nm or fn),
                        "raw_name": (fn or nm).upper(),
                        "vol_page": vp,
                        "deed_num": dn,
                        "acres": acres,
                        "geom": g_clean,
                    }
                )
    print(f"   Extracted {len(infill_replats):,} modern < 2.25-acre infill replat polygons across Harris County.")

    # Pass 1: Heal interior doughnut holes on all Historic Parent Plats
    print("5. Pass 1: Healing interior replat doughnut holes on Historic Parent Plats...")
    hist_orig_geoms = [feat_geoms_ft[i] for i in hist_indices]
    hist_orig_tree = STRtree(hist_orig_geoms)

    hist_pass1_geoms = []
    hist_holes_healed = {}
    for h_pos, feat_idx in enumerate(hist_indices):
        filled_g, n_holes = fill_interior_holes_respecting_neighbors(
            hist_orig_geoms[h_pos],
            max_hole_acres=15.0,
            other_historic_geoms=hist_orig_geoms,
            other_historic_tree=hist_orig_tree,
            self_hist_idx=h_pos,
        )
        hist_pass1_geoms.append(filled_g)
        hist_holes_healed[feat_idx] = n_holes

    # Pass 2: Uniquely assign enclosed & edge-notched modern infill replats to their Historic Parent Plat
    print("6. Pass 2: Absorbing enclosed & edge-notched modern infill replats into Historic Parent Plats...")
    hist_pass1_buffered = [g.buffer(18.0) for g in hist_pass1_geoms]
    hist_pass1_hulls = [g.convex_hull for g in hist_pass1_geoms]
    hist_pass1_tree = STRtree(hist_pass1_buffered)

    assigned_replats: dict[int, list[dict]] = defaultdict(list)
    absorbed_replat_vps: set[str] = set()
    absorbed_replat_names_loc: list = []

    for rep in infill_replats:
        rg = rep["geom"]
        r_area = rg.area
        r_perim = rg.boundary.length
        if r_area <= 0 or r_perim <= 0:
            continue
        cand_h_positions = hist_pass1_tree.query(rg)
        if len(cand_h_positions) == 0:
            continue

        best_h_pos = -1
        best_score = -1.0
        rep_up = rep["raw_name"]

        for h_pos in cand_h_positions.tolist():
            feat_idx = hist_indices[h_pos]
            p_props = ps_features[feat_idx]["properties"]
            parent_name_up = clean_str(p_props.get("name")).upper()
            # Skip if replat IS the parent itself
            if rep["vol_page"] and rep["vol_page"] == clean_str(p_props.get("vol_page")) and not REPLAT_TOKEN_RE.search(rep_up):
                continue

            pg = hist_pass1_geoms[h_pos]
            inside_area = rg.intersection(pg).area if rg.intersects(pg) else 0.0
            inside_ratio = inside_area / r_area

            shared_len = rg.boundary.intersection(hist_pass1_buffered[h_pos]).length
            shared_perim_ratio = shared_len / r_perim

            hull_area = rg.intersection(hist_pass1_hulls[h_pos]).area
            hull_ratio = hull_area / r_area

            core_tok = re.sub(r"\b(ADDITION|SUBDIVISION|SECTION|SEC|PLACE|PARK|HEIGHTS)\b", "", parent_name_up).strip()
            name_affinity = bool(len(core_tok) >= 4 and core_tok in rep_up)

            qualifies = (
                inside_ratio >= 0.45
                or (shared_perim_ratio >= 0.46 and hull_ratio >= 0.62)
                or (name_affinity and shared_perim_ratio >= 0.30 and hull_ratio >= 0.40)
            )
            if not qualifies:
                continue

            score = inside_ratio * 2.2 + shared_perim_ratio + (0.45 if name_affinity else 0.0)
            if score > best_score:
                best_score = score
                best_h_pos = h_pos

        if best_h_pos >= 0:
            win_feat_idx = hist_indices[best_h_pos]
            assigned_replats[win_feat_idx].append(rep)
            if rep["vol_page"]:
                absorbed_replat_vps.add(rep["vol_page"])
            absorbed_replat_names_loc.append(rg)

    print(
        f"   Assigned {sum(len(v) for v in assigned_replats.values()):,} modern infill replats "
        f"across {len(assigned_replats):,} reconstructed Historic Parent Plats."
    )

    # Pass 3: Morphological weld (8 ft miter closing), hole cleanup, and strict neighbor disjointness
    print("7. Pass 3: Welding survey slivers & enforcing disjoint historic neighbor boundaries...")
    healed_hist_geoms: dict[int, object] = {}

    for h_pos, feat_idx in enumerate(hist_indices):
        orig_g = hist_orig_geoms[h_pos]
        p1_g = hist_pass1_geoms[h_pos]
        reps = assigned_replats.get(feat_idx, [])

        if not reps and hist_holes_healed.get(feat_idx, 0) == 0:
            healed_hist_geoms[feat_idx] = orig_g
            continue

        pieces = [p1_g] + [r["geom"] for r in reps]
        merged = filter_polygonal(unary_union(pieces))

        # Light 8-ft miter closing to weld 1-16 ft parcel/ROW survey slivers around absorbed replats
        closed = merged.buffer(8.0, join_style=2).buffer(-8.0, join_style=2)
        welded = filter_polygonal(unary_union([orig_g, merged, closed]))

        # Fill any small newly enclosed interior holes <= 12 acres (respecting neighbor historic plats)
        welded, extra_holes = fill_interior_holes_respecting_neighbors(
            welded,
            max_hole_acres=12.0,
            other_historic_geoms=hist_orig_geoms,
            other_historic_tree=hist_orig_tree,
            self_hist_idx=h_pos,
        )
        hist_holes_healed[feat_idx] = hist_holes_healed.get(feat_idx, 0) + extra_holes

        # Subtract any NEW encroachment onto neighboring historic parent originals (Q_orig - P_orig)
        for nh_pos in hist_orig_tree.query(welded).tolist():
            if nh_pos == h_pos:
                continue
            q_orig = hist_orig_geoms[nh_pos]
            if welded.intersects(q_orig):
                forbidden = q_orig.difference(orig_g)
                if not forbidden.is_empty and welded.intersects(forbidden):
                    welded = welded.difference(forbidden)

        healed_hist_geoms[feat_idx] = filter_polygonal(welded)

    # Second disjointness sweep so two adjacent healed historic additions never overlap in newly healed margins
    healed_pos_geoms = [healed_hist_geoms[i] for i in hist_indices]
    healed_pos_tree = STRtree(healed_pos_geoms)
    for h_pos, feat_idx in enumerate(hist_indices):
        g_cur = healed_hist_geoms[feat_idx]
        orig_cur = hist_orig_geoms[h_pos]
        for other_pos in healed_pos_tree.query(g_cur).tolist():
            if other_pos >= h_pos:
                continue
            other_idx = hist_indices[other_pos]
            g_prev = healed_hist_geoms[other_idx]
            if g_cur.intersects(g_prev):
                claim_by_prev = g_prev.difference(orig_cur)
                if not claim_by_prev.is_empty and g_cur.intersects(claim_by_prev):
                    g_cur = filter_polygonal(g_cur.difference(claim_by_prev))
        healed_hist_geoms[feat_idx] = g_cur

    # Filter & update final platted_subdivisions features
    print("8. Reprojecting reconstructed geometries to WGS84 & filtering absorbed townhome cutouts...")
    absorbed_tree = STRtree(absorbed_replat_names_loc) if absorbed_replat_names_loc else None

    kept_features = []
    dropped_absorbed = 0
    dropped_cutouts = 0

    for idx, feat in enumerate(ps_features):
        p = feat["properties"]
        vp = clean_str(p.get("vol_page"))
        orig_g_ft = feat_geoms_ft[idx]
        orig_acres = round(feat_acres_orig[idx], 2)

        if not is_historic_parent[idx]:
            # Check if this polygon was absorbed into a historic parent plat or is a tiny post-1985 cutout
            was_absorbed = bool(vp and vp in absorbed_replat_vps)
            if not was_absorbed and absorbed_tree is not None and not orig_g_ft.is_empty and orig_acres <= 2.3:
                for ai in absorbed_tree.query(orig_g_ft).tolist():
                    ag = absorbed_replat_names_loc[ai]
                    if orig_g_ft.intersects(ag) and (orig_g_ft.intersection(ag).area / orig_g_ft.area) >= 0.65:
                        was_absorbed = True
                        break
            if was_absorbed:
                dropped_absorbed += 1
                continue
            if is_standalone_modern_cutout[idx]:
                dropped_cutouts += 1
                continue

        final_g_ft = healed_hist_geoms.get(idx, orig_g_ft)
        if final_g_ft.is_empty:
            continue

        reconstructed_acres = round(final_g_ft.area / 43560.0, 2)
        p["surviving_acres"] = orig_acres
        p["reconstructed_acres"] = max(orig_acres, reconstructed_acres)
        p["is_historic_plat"] = bool(is_historic_parent[idx])

        reps_here = assigned_replats.get(idx, [])
        holes_cnt = int(hist_holes_healed.get(idx, 0))
        # Deduplicate healed replat names cleanly (excluding parent's own base name if identical)
        seen_rep_names = []
        seen_up = {clean_str(p.get("name")).upper()}
        for r in sorted(reps_here, key=lambda x: -x["acres"]):
            rn = r["name"]
            if not rn:
                continue
            ru = rn.upper()
            if ru in seen_up:
                continue
            seen_up.add(ru)
            seen_rep_names.append(rn)

        healed_count = max(len(reps_here), holes_cnt if p["reconstructed_acres"] > orig_acres + 0.05 else 0)
        p["replat_holes_healed"] = holes_cnt
        p["healed_replat_count"] = healed_count
        p["sample_healed_replats"] = seen_rep_names[:14]

        est_yr = int(p.get("estimated_plat_year") or 0)
        if is_historic_parent[idx] and 1836 <= est_yr <= 1965:
            if est_yr <= 1904:
                p["archival_lookup_note"] = (
                    "Harris County did not open dedicated Map Record (MR) books until 1905. "
                    "Pre-1905 subdivision plats like this one were recorded inside general Harris County "
                    "Deed Record (DR) volumes rather than the Clerk's modern Map index, and subsequent "
                    "townhome replats overwrite the parent addition name on modern HCAD parcels "
                    "(check the subtitle recital on any modern replat sheet for 'being a replat of "
                    "Lots X-Y, Block Z of the original Addition')."
                )
            else:
                p["archival_lookup_note"] = (
                    "Recorded in the Harris County Map Records (MR) / Deed Records series. "
                    "Where later townhome or commercial replats carved cutouts from this addition, "
                    "modern HCAD parcels list only the new replat name; Texas platting rules require "
                    "every replat title block to recite its parent Lots, Block, and original Addition."
                )
            lng = float(p.get("label_lng") or 0.0)
            lat = float(p.get("label_lat") or 0.0)
            if est_yr <= 1924 and -95.445 <= lng <= -95.285 and 29.695 <= lat <= 29.835:
                p["bracey_1922_url"] = BRACEY_1922_URL
            else:
                p.pop("bracey_1922_url", None)
        else:
            p.pop("archival_lookup_note", None)
            p.pop("bracey_1922_url", None)

        # If geometry changed (reconstructed historic plat), simplify 1.5 ft in EPSG:2278 and update WGS84 geometry
        if idx in healed_hist_geoms and (healed_count > 0 or holes_cnt > 0):
            simp_ft = filter_polygonal(final_g_ft.simplify(1.5, preserve_topology=True))
            if simp_ft.is_empty:
                simp_ft = final_g_ft
            wgs_g = filter_polygonal(proj_to_4326(simp_ft), min_area=0.0)
            if wgs_g.is_empty:
                wgs_g = orig_wgs_geoms[idx]
            rep_pt = wgs_g.representative_point()
            p["label_lng"] = round(rep_pt.x, 5)
            p["label_lat"] = round(rep_pt.y, 5)
            p["area_deg2"] = round(wgs_g.area, 7)
            feat["geometry"] = round_coords(mapping(wgs_g), 5)

        kept_features.append(feat)

    print(
        f"   Retained {len(kept_features):,} subdivisions "
        f"(dropped {dropped_absorbed:,} absorbed infill replats + {dropped_cutouts:,} tiny post-1985 cutouts)."
    )

    # Recompute exact building counts & Chapter 42 intersections across the reconstructed footprints
    print("9. Recomputing building statistics & Chapter 42 ordinance counts across reconstructed footprints...")
    final_wgs_geoms = [make_valid(shape(f["geometry"])) for f in kept_features]
    final_wgs_tree = STRtree(final_wgs_geoms)

    plat_stats = defaultdict(lambda: {"years": [], "landmarks": 0, "good_brick": 0, "subs": Counter()})
    pt_idxs, poly_idxs = final_wgs_tree.query(pts_wgs84, predicate="intersects")
    for pt_i, poly_i in zip(pt_idxs.tolist(), poly_idxs.tolist()):
        st = plat_stats[poly_i]
        yr = yrs_list[pt_i]
        if 1836 <= yr <= 2026:
            st["years"].append(yr)
        if lms_list[pt_i]:
            st["landmarks"] += 1
        if gbs_list[pt_i]:
            st["good_brick"] += 1
        sub = hcad_to_sub.get(hcads_list[pt_i])
        if sub:
            st["subs"][sub] += 1

    # Re-check Chapter 42 SMLS / SMBL intersections against reconstructed polygons
    prot_feats = overlays.get("land_use_protections", {}).get("features", [])
    smls_geoms, smls_props = [], []
    smbl_geoms, smbl_props = [], []
    for pf in prot_feats:
        pp = pf.get("properties") or {}
        pg = make_valid(shape(pf["geometry"]))
        if pg.is_empty:
            continue
        if pp.get("protection_type") == "smls":
            smls_geoms.append(pg)
            smls_props.append(pp)
        elif pp.get("protection_type") == "smbl":
            smbl_geoms.append(pg)
            smbl_props.append(pp)
    smls_tree = STRtree(smls_geoms) if smls_geoms else None
    smbl_tree = STRtree(smbl_geoms) if smbl_geoms else None

    for poly_i, feat in enumerate(kept_features):
        p = feat["properties"]
        st = plat_stats[poly_i]
        yrs = st["years"]
        if yrs:
            arr = np.array(yrs)
            p["building_count"] = len(yrs)
            p["earliest_year"] = int(arr.min())
            p["median_year"] = int(np.median(arr))
            p["pre_1940_count"] = int((arr < 1940).sum())
            p["pre_1965_count"] = int((arr < 1965).sum())
        p["landmark_count"] = max(int(p.get("landmark_count") or 0), st["landmarks"])
        p["good_brick_count"] = max(int(p.get("good_brick_count") or 0), st["good_brick"])
        top_subs = [s for s, _ in st["subs"].most_common(10)]
        p["top_subdivisions"] = top_subs

        # Ensure full_name (e.g. "Fairview Addition") is first in alt_names when distinct from short name
        fn_clean = clean_title_name(p.get("full_name") or "")
        alts = [a for a in (p.get("alt_names") or []) if a.upper() != p["name"].upper()]
        if fn_clean and fn_clean.upper() != p["name"].upper():
            alts = [fn_clean] + [a for a in alts if a.upper() != fn_clean.upper()]
        p["alt_names"] = alts[:12]

        sg = final_wgs_geoms[poly_i]
        if smls_tree is not None:
            ords, min_sqfts = [], []
            for si in smls_tree.query(sg).tolist():
                if smls_geoms[si].intersects(sg) and smls_geoms[si].intersection(sg).area > 1e-9:
                    sp = smls_props[si]
                    if sp.get("ordinance") and sp["ordinance"] not in ords:
                        ords.append(sp["ordinance"])
                    if int(sp.get("min_lot_sqft") or 0) > 0:
                        min_sqfts.append(int(sp["min_lot_sqft"]))
            if ords or min_sqfts:
                p["smls_count"] = len(ords) or len(min_sqfts)
                p["smls_ordinances"] = ", ".join(ords[:6])
                p["smls_min_sqft"] = min(min_sqfts) if min_sqfts else 0

        if smbl_tree is not None:
            b_ords, min_fts = [], []
            for bi in smbl_tree.query(sg).tolist():
                if smbl_geoms[bi].intersects(sg) and smbl_geoms[bi].intersection(sg).area > 1e-9:
                    bp = smbl_props[bi]
                    if bp.get("ordinance") and bp["ordinance"] not in b_ords:
                        b_ords.append(bp["ordinance"])
                    if int(bp.get("min_bldg_line_ft") or 0) > 0:
                        min_fts.append(int(bp["min_bldg_line_ft"]))
            if b_ords or min_fts:
                p["smbl_count"] = len(b_ords) or len(min_fts)
                p["smbl_ordinances"] = ", ".join(b_ords[:6])
                p["smbl_min_ft"] = min(min_fts) if min_fts else 0

    kept_features.sort(key=lambda f: float(f["properties"].get("area_deg2") or 0.0))
    overlays["platted_subdivisions"]["features"] = kept_features

    with open(overlays_path, "w", encoding="utf-8") as f:
        json.dump(overlays, f, separators=(",", ":"))
    print(f"10. Saved updated {overlays_path} ({os.path.getsize(overlays_path) / (1024 * 1024):.2f} MB)")

    # Update ONLY the plattedSubdivisions entries in search_index.json, preserving all other layers & buildings
    si_path = os.path.join(DATA_DIR, "search_index.json")
    with open(si_path, "r", encoding="utf-8") as f:
        si_list = json.load(f)

    non_plat_before = []
    non_plat_after = []
    seen_plat_block = False
    for item in si_list:
        if item.get("overlay_layer") in ("plattedSubdivisions", "platted_subdivisions"):
            seen_plat_block = True
            continue
        if not seen_plat_block:
            non_plat_before.append(item)
        else:
            non_plat_after.append(item)

    new_plat_si = []
    for feat in kept_features:
        p = feat["properties"]
        geom = final_wgs_geoms[0] if False else shape(feat["geometry"])
        is_hist = bool(p.get("is_historic_plat"))
        yr_lbl = clean_str(p.get("plat_year_label"))
        est_yr = int(p.get("estimated_plat_year") or 0)
        fn_clean = clean_title_name(p.get("full_name") or "")
        display_name = (
            fn_clean
            if (is_hist and fn_clean and fn_clean.upper().startswith(p["name"].upper()) and "ADDITION" in fn_clean.upper())
            else p["name"]
        )
        if is_hist and yr_lbl:
            bname = f"{display_name} ({yr_lbl} Historic Plat)"
            ptype = f"Historic Subdivision Plat ({yr_lbl})"
        else:
            bname = f"{p['name']} (Platted Subdivision)"
            ptype = "Platted Subdivision (HCAD)"

        nb_tag = (
            f" · {p['neighborhood']}"
            if p.get("neighborhood")
            else (f" · {p['super_neighborhood']}" if p.get("super_neighborhood") else "")
        )
        cit_short = clean_str(p.get("deed_citation") or p.get("plat_citation")).replace("Harris County ", "")
        cit_tag = f" · {cit_short}" if cit_short else (f" · Vol/Pg {p['vol_page']}" if p.get("vol_page") else "")
        healed_cnt = int(p.get("healed_replat_count") or 0)
        rec_ac = float(p.get("reconstructed_acres") or 0.0)
        healed_tag = (
            f" · {rec_ac:.1f} ac ({healed_cnt} replats healed)"
            if healed_cnt > 0
            else (f" · {p.get('building_count', 0):,} structures" if p.get("building_count") else "")
        )
        addr_line = (
            f"Platted {yr_lbl}{cit_tag}{nb_tag}{healed_tag}"
            if (is_hist and yr_lbl)
            else f"HCAD Platted Subdivision{nb_tag}{cit_tag} · {p.get('building_count', 0):,} structures"
        )

        new_plat_si.append(
            {
                "id": p["id"],
                "is_neighborhood_entry": True,
                "place_type": ptype,
                "overlay_layer": "plattedSubdivisions",
                "name": p["name"],
                "building_name": bname,
                "alt_names": p.get("alt_names", []),
                "neighborhood": p.get("neighborhood"),
                "super_neighborhood": p.get("super_neighborhood"),
                "historic_ward": p.get("historic_ward"),
                "address": addr_line,
                "category": "Platted Subdivision",
                "year_built": est_yr if (is_hist and est_yr >= 1836) else (p.get("median_year") or 1940),
                "estimated_plat_year": est_yr,
                "earliest_year": p.get("earliest_year") or 0,
                "building_count": p.get("building_count") or 0,
                "pre_1940_count": p.get("pre_1940_count") or 0,
                "healed_replat_count": healed_cnt,
                "lng": p["label_lng"],
                "lat": p["label_lat"],
                "bbox": [round(v, 5) for v in geom.bounds],
            }
        )

    updated_si = non_plat_before + new_plat_si + non_plat_after
    with open(si_path, "w", encoding="utf-8") as f:
        json.dump(updated_si, f, separators=(",", ":"))
    print(
        f"11. Updated {si_path}: {len(new_plat_si):,} plattedSubdivisions entries "
        f"(total search_index entries: {len(updated_si):,}, {os.path.getsize(si_path) / (1024 * 1024):.2f} MB)."
    )

    # Print verification scorecard for landmark inner-loop historic subdivisions
    print("\n=== HISTORIC SUBDIVISION DE-REPLATTING VERIFICATION ===")
    check_ids = [
        "plat_1908_fairview",
        "plat_3791_montrose",
        "plat_2751_hyde_park",
        "plat_2756_hyde_park_main",
        "plat_6366_westmoreland",
        "plat_0272_avondale",
        "plat_1316_courtlandt_place",
        "plat_6592_woodland_heights",
        "plat_3902_norhill",
        "plat_2683_houston_heights",
        "plat_1725_eastwood",
        "plat_0752_broadacres",
    ]
    by_id = {f["properties"]["id"]: f for f in kept_features}
    for cid in check_ids:
        feat = by_id.get(cid)
        if not feat:
            print(f"  MISSING: {cid}")
            continue
        p = feat["properties"]
        g = shape(feat["geometry"])
        holes_rem = (
            len(g.interiors)
            if g.geom_type == "Polygon"
            else sum(len(sub.interiors) for sub in g.geoms)
        )
        print(
            f"  {p['id']:30s} | {p.get('plat_year_label'):7s} | "
            f"{p.get('surviving_acres'):6.2f} -> {p.get('reconstructed_acres'):6.2f} ac | "
            f"healed={p.get('healed_replat_count'):3d} (holes_rem={holes_rem}) | "
            f"blds={p.get('building_count'):4d} | {p.get('deed_citation') or p.get('plat_citation')}"
        )
        if p.get("sample_healed_replats"):
            print(f"     sample healed replats: {', '.join(p['sample_healed_replats'][:8])}")


if __name__ == "__main__":
    main()
