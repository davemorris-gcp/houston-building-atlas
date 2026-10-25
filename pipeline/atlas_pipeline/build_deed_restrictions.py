#!/usr/bin/env python3
"""
Builds the Houston Deed Restriction, Plat Record & Land-Use Protection Database:
1. Generates `app/public/data/deed_restrictions_catalog.json` with 64 mirrored local
   archival PDFs across 28 historic Houston subdivisions (Woodland Heights, Norhill,
   Boulevard Oaks / Broadacres, Oak Forest Sec 1-18, and Idylwood).
2. Enriches all 5,573 platted subdivisions in `app/public/data/overlays.json` with:
   - Human-readable `plat_citation` (Map Book Vol/Page or Plat Film Code + HCAD Code)
   - Human-readable `deed_citation` (Harris County Clerk File # or Deed Record Vol/Page)
   - Governing Registered Civic Association (`civic_club`, `civic_club_id`)
   - Intersecting COH Chapter 42 SMLS & SMBL ordinance summaries
   - `has_deed_docs` boolean flag & `deed_doc_count`
3. Adds `overlays.land_use_protections` (`910` polygons: 711 COH Special Minimum Lot
   Size ordinances, 192 Special Minimum Building Line ordinances, and 7 Conservation
   Districts) to `app/public/data/overlays.json`.
"""

import datetime
import json
import os
import re
from pathlib import Path
from shapely.geometry import shape, mapping
from shapely.strtree import STRtree

ROOT_DIR = str(Path(__file__).resolve().parents[2])
CACHE_DIR = os.path.join(ROOT_DIR, "pipeline", "cache")
DATA_DIR = os.path.join(ROOT_DIR, "app", "public", "data")

GIS_TYPO_FIXES = {
    "WOODKIND HEIGHTS CIVIC ASSOCIATION": "Woodland Heights Civic Association",
    "IDLYWOOD CIVIC CLUB": "Idylwood Civic Club",
    "BREABURN GLEN CIVIC CLUB": "Braeburn Glen Civic Club",
    "SANDLEWOOD CIVIC CLUB": "Sandalwood Civic Club",
    "SOUTH HAMPTON CIVIC CLUB, INC": "Southampton Civic Club",
}


def clean_civic_name(raw_name: str) -> str:
    if not raw_name:
        return ""
    up = raw_name.strip().upper()
    if up in GIS_TYPO_FIXES:
        return GIS_TYPO_FIXES[up]
    words = raw_name.strip().title().split()
    fixed = []
    for w in words:
        if w.upper() in ("HOA", "POA", "CIA", "II", "III", "IV", "SW", "NW", "NE", "SE"):
            fixed.append(w.upper())
        elif w.upper() == "INC" or w.upper() == "INC.":
            continue
        else:
            fixed.append(w.rstrip(","))
    return " ".join(fixed)


def format_plat_citation(recnum: str, vol_page: str) -> str:
    rec = (recnum or "").strip()
    vp = (vol_page or "").strip()
    parts = []
    if rec and rec.lower() != "none":
        if re.match(r"^\d{6}$", rec):
            vol = int(rec[:3])
            pg = int(rec[3:])
            if 1 <= vol <= 75 and 1 <= pg <= 150:
                parts.append(f"Map Book Vol. {vol}, Pg. {pg}")
            elif vol >= 500:
                parts.append(f"Plat Film #{rec[:3]}-{rec[3:]}")
            else:
                parts.append(f"Harris County Clerk Filing #{int(rec)}")
        elif re.match(r"^\d{3}-\d{3}$", rec):
            parts.append(f"Plat Film #{rec}")
        else:
            parts.append(f"Harris County Clerk Filing #{rec}")
    if vp and vp.lower() != "none":
        parts.append(f"HCAD Subdiv. Code {vp}")
    return " · ".join(parts)


def format_deed_citation(deed_num: str) -> str:
    d = (deed_num or "").strip()
    if not d or d.lower() == "none":
        return ""
    if re.match(r"^\d{3}-\d{3}$", d):
        return f"Harris County Map Records Film Code #{d}"
    m_dr = re.match(r"^V(\d+)P(\d+)(DR|MR)?$", d, re.I)
    if m_dr:
        vol, pg, kind = int(m_dr.group(1)), int(m_dr.group(2)), (m_dr.group(3) or "DR").upper()
        rec_type = "Map Records" if kind == "MR" else "Deed Records"
        return f"Harris County {rec_type} Vol. {vol}, Pg. {pg}"
    if d.upper().startswith("VOL"):
        return f"Harris County Deed Records {d}"
    if "/" in d:
        return f"Recorded {d}"
    if d.upper().startswith("RP-") or re.match(r"^[A-Z]\d{6,8}$", d, re.I) or len(d) >= 8:
        return f"Harris County Clerk File #{d}"
    return f"Harris County Clerk Instrument #{d}"


def ms_to_iso_date(ms_val) -> str:
    if not ms_val:
        return ""
    try:
        ts = float(ms_val) / 1000.0
        dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
        if dt.year < 1900 or dt.year > 2100:
            return ""
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return ""


def round_coords(coords, precision=5):
    if isinstance(coords, (float, int)):
        return round(float(coords), precision)
    return [round_coords(c, precision) for c in coords]


OAK_FOREST_SECTION_FILINGS = {
    1: "Instrument #379388 (Deed Vol. 1559, Pg. 387)",
    2: "Instrument #469872 (Deed Vol. 1711, Pg. 635)",
    3: "Instrument #548544 (Deed Vol. 1848, Pg. 696)",
    4: "Instrument #589314 (Deed Vol. 1906, Pg. 173)",
    5: "Instrument #663414 (Deed Vol. 2006, Pg. 37)",
    6: "Instrument #663415 (Deed Vol. 2006, Pg. 44)",
    7: "Instrument #692761 (Deed Vol. 2046, Pg. 440)",
    8: "Instrument #782464 (Deed Vol. 2166, Pg. 166)",
    9: "Instrument #757310 (Deed Vol. 2131, Pg. 412)",
    10: "Instrument #842900 (Deed Vol. 2231, Pg. 542)",
    11: "Instrument #850385 (Deed Vol. 2241, Pg. 377)",
    12: "Instrument #873665 (Deed Vol. 2270, Pg. 705)",
    13: "Instrument #971261 (Deed Vol. 2394, Pg. 142)",
    14: "Instrument #1097530 (Deed Vol. 2551, Pg. 47)",
    15: "Instrument #1145716 (Deed Vol. 2612, Pg. 53)",
    16: "Instrument #1143615 (Deed Vol. 2609, Pg. 425)",
    17: "Instrument #1521330 (Deed Vol. 3082, Pg. 128)",
    18: "Instrument #1412655 (Deed Vol. 2948, Pg. 379)",
}

CURATED_DEED_CATALOG = {
    "plat_6592_woodland_heights": {
        "subdivision_id": "plat_6592_woodland_heights",
        "subdivision_name": "Woodland Heights",
        "civic_association": "Woodland Heights Civic Association",
        "civic_association_url": "https://www.woodland-heights.org/land-use",
        "plat_year": 1907,
        "vol_page": "037-266",
        "plat_citation": "Map Book Vol. 2, Pg. 33 · HCAD Subdiv. Code 037-266",
        "deed_citation": "Harris County Film Code #199-30-1556 (Restated Restrictions) · Original Filing #32892",
        "covenant_summary": {
            "allowed_use": "Residential only (incidental non-public home office allowed; no commercial, retail, clinic, or hotel use)",
            "min_lot_size": "Max 1 single-family residence per 2,500 sq ft of lot area (no duplexes, apartments, or multi-family)",
            "front_setback": "Min 10 ft from street property line or 45 ft from street centerline (whichever is greater)",
            "side_rear_setback": "Min 3 ft from side and rear property lines for any structure exceeding 1 story",
            "max_height": "Max 3 stories and 40 ft above ground level",
            "plan_review": "WHCA plan review not required (COH HAHC Certificate of Appropriateness required inside Historic District)",
            "notes": "Platted in October 1907 by the William A. Wilson Realty Co. along the Houston Electric Co. streetcar line (certain perimeter/commercial lots in Blocks 1, 2, 10, 25, 31, 32, 43, 44, and 45 are excluded from the restated residential declaration).",
        },
        "citations": [
            {"label": "Harris County Film Code #199-30-1556 (PDF)", "url": "public/deeds/woodland-heights/Woodland-Heights-Official.pdf"},
            {"label": "COH Historic Districts", "url": "https://www.houstontx.gov/planning/HistoricPres/District_Reference.html"},
        ],
        "documents": [
            {
                "title": "Woodland Heights Deed Restrictions (Official Recorded Copy)",
                "doc_type": "official",
                "local_url": "public/deeds/woodland-heights/Woodland-Heights-Official.pdf",
                "external_url": "https://www.woodland-heights.org/s/Woodland-Heights-Official.pdf",
                "clerk_file": "Film Code #199-30-1556 (Map Vol. 2, Pg. 33)",
            },
            {
                "title": "Woodland Heights Deed Restrictions (Searchable Transcribed Text)",
                "doc_type": "searchable",
                "local_url": "public/deeds/woodland-heights/Woodland-Heights-Unofficial.pdf",
                "external_url": "https://www.woodland-heights.org/s/Woodland-Heights-Unofficial.pdf",
                "clerk_file": "Searchable Transcription",
            },
        ],
    },
    "plat_6593_woodland_heights_annex": {
        "subdivision_id": "plat_6593_woodland_heights_annex",
        "subdivision_name": "Woodland Heights Annex",
        "civic_association": "Woodland Heights Civic Association",
        "civic_association_url": "https://www.woodland-heights.org/land-use",
        "plat_year": 1909,
        "vol_page": "037-311",
        "plat_citation": "Map Book Vol. 3, Pg. 38 · HCAD Subdiv. Code 037-311",
        "deed_citation": "Harris County Clerk File #N545550 (Film Code 015-58-0155) · Joint Annex, Willborg & Hermosa Court Declaration",
        "covenant_summary": {
            "allowed_use": "Residential only (incidental non-public home office allowed; 'Residence' includes house, townhouse, condo, apartment, or garage apartment subject to 1 unit per 2,500 sq ft density cap; no commercial, clinic, or hotel use)",
            "min_lot_size": "Max 1 residential unit per 2,500 sq ft of lot area",
            "front_setback": "Min 15 ft from street property line or 45 ft from street centerline (whichever is greater)",
            "side_rear_setback": "Min 3 ft from side and rear property lines",
            "max_height": "Max 3 stories and 40 ft above ground level",
            "plan_review": "WHCA plan review not required (COH HAHC COA required if inside Historic District)",
            "notes": "Jointly restricted with Willborg and Hermosa Court subdivisions under Clerk File #N545550.",
        },
        "citations": [
            {"label": "Clerk File #N545550 (PDF)", "url": "public/deeds/woodland-heights/Woodland-Heights-Annex-Official.pdf"},
            {"label": "WHCA Land Use Archive", "url": "https://www.woodland-heights.org/land-use"},
        ],
        "documents": [
            {
                "title": "Woodland Heights Annex Deed Restrictions (Official Recorded Copy)",
                "doc_type": "official",
                "local_url": "public/deeds/woodland-heights/Woodland-Heights-Annex-Official.pdf",
                "external_url": "https://www.woodland-heights.org/s/Woodland-Heights-Annex-Official.pdf",
                "clerk_file": "Clerk File #N545550 / Film Code 015-58-0155",
            },
            {
                "title": "Woodland Heights Annex Deed Restrictions (Searchable Text)",
                "doc_type": "searchable",
                "local_url": "public/deeds/woodland-heights/Woodland-Heights-Annex-Unofficial.pdf",
                "external_url": "https://www.woodland-heights.org/s/Woodland-Heights-Annex-Unofficial.pdf",
                "clerk_file": "Searchable Transcription",
            },
        ],
    },
    "plat_6452_willborg": {
        "subdivision_id": "plat_6452_willborg",
        "subdivision_name": "Willborg",
        "civic_association": "Woodland Heights Civic Association",
        "civic_association_url": "https://www.woodland-heights.org/land-use",
        "plat_year": 1910,
        "vol_page": "037-308",
        "plat_citation": "HCAD Subdiv. Code 037-308",
        "deed_citation": "Harris County Clerk File #N545550 (Film Code 015-58-0155) · Joint Annex, Willborg & Hermosa Court Declaration",
        "covenant_summary": {
            "allowed_use": "Residential only (incidental non-public home office allowed; 'Residence' includes house, townhouse, condo, apartment, or garage apartment subject to 1 unit per 2,500 sq ft density cap; no commercial, clinic, or hotel use)",
            "min_lot_size": "Max 1 residential unit per 2,500 sq ft of lot area",
            "front_setback": "Min 15 ft from street property line or 45 ft from street centerline (whichever is greater)",
            "side_rear_setback": "Min 3 ft from side and rear property lines",
            "max_height": "Max 3 stories and 40 ft above ground level",
            "plan_review": "WHCA plan review not required",
            "notes": "Covered under the joint 1992 Declaration of Restrictions (#N545550) for Woodland Heights Annex, Willborg, and Hermosa Court.",
        },
        "citations": [
            {"label": "Clerk File #N545550 (PDF)", "url": "public/deeds/woodland-heights/Willborg-Official.pdf"},
            {"label": "WHCA Land Use Archive", "url": "https://www.woodland-heights.org/land-use"},
        ],
        "documents": [
            {
                "title": "Willborg Subdivision Deed Restrictions (Official Recorded Copy)",
                "doc_type": "official",
                "local_url": "public/deeds/woodland-heights/Willborg-Official.pdf",
                "external_url": "https://www.woodland-heights.org/s/Willborg-Official.pdf",
                "clerk_file": "Clerk File #N545550 / Film Code 015-58-0155",
            },
            {
                "title": "Willborg Subdivision Deed Restrictions (Searchable Text)",
                "doc_type": "searchable",
                "local_url": "public/deeds/woodland-heights/Willborg-Unofficial.pdf",
                "external_url": "https://www.woodland-heights.org/s/Willborg-Unofficial.pdf",
                "clerk_file": "Searchable Transcription",
            },
        ],
    },
    "plat_2522_hermosa_court": {
        "subdivision_id": "plat_2522_hermosa_court",
        "subdivision_name": "Hermosa Court",
        "civic_association": "Woodland Heights Civic Association",
        "civic_association_url": "https://www.woodland-heights.org/land-use",
        "plat_year": 1911,
        "vol_page": "037-105",
        "plat_citation": "HCAD Subdiv. Codes 037-105 & 058-005",
        "deed_citation": "Harris County Clerk File #N545550 (Film Code 015-58-0155) · Joint Annex, Willborg & Hermosa Court Declaration",
        "covenant_summary": {
            "allowed_use": "Residential only (incidental non-public home office allowed; 'Residence' includes house, townhouse, condo, apartment, or garage apartment subject to 1 unit per 2,500 sq ft density cap; no commercial, clinic, or hotel use)",
            "min_lot_size": "Max 1 residential unit per 2,500 sq ft of lot area",
            "front_setback": "Min 15 ft from street property line or 45 ft from street centerline (whichever is greater)",
            "side_rear_setback": "Min 3 ft from side and rear property lines",
            "max_height": "Max 3 stories and 40 ft above ground level",
            "plan_review": "WHCA plan review not required",
            "notes": "Covered under the joint 1992 Declaration of Restrictions (#N545550) for Hermosa Court, Woodland Heights Annex, and Willborg.",
        },
        "citations": [
            {"label": "Clerk File #N545550 (PDF)", "url": "public/deeds/woodland-heights/Hermosa-Court-Official.pdf"},
            {"label": "WHCA Land Use Archive", "url": "https://www.woodland-heights.org/land-use"},
        ],
        "documents": [
            {
                "title": "Hermosa Court Deed Restrictions (Official Recorded Copy)",
                "doc_type": "official",
                "local_url": "public/deeds/woodland-heights/Hermosa-Court-Official.pdf",
                "external_url": "https://www.woodland-heights.org/s/Hermosa-Court-Official.pdf",
                "clerk_file": "Clerk File #N545550 / Film Code 015-58-0155",
            },
            {
                "title": "Hermosa Court Deed Restrictions (Searchable Text)",
                "doc_type": "searchable",
                "local_url": "public/deeds/woodland-heights/Hermosa-Court-Unofficial.pdf",
                "external_url": "https://www.woodland-heights.org/s/Hermosa-Court-Unofficial.pdf",
                "clerk_file": "Searchable Transcription",
            },
        ],
    },
    "plat_2575_highland_park": {
        "subdivision_id": "plat_2575_highland_park",
        "subdivision_name": "Highland Park",
        "civic_association": "Woodland Heights Civic Association",
        "civic_association_url": "https://www.woodland-heights.org/land-use",
        "plat_year": 1903,
        "vol_page": "037-296",
        "plat_citation": "Map Book Vol. 1, Pg. 60 & Deed Vol. 195, Pg. 376 · HCAD Subdiv. Code 037-296",
        "deed_citation": "Harris County Clerk File #T056649 (Film Code 513-39-0760) · Joint Highland Park & Rodgers Declaration",
        "covenant_summary": {
            "allowed_use": "Residential only (incidental non-public home office allowed; 'Residence' includes house, townhouse, condo, apartment, or garage apartment subject to 1 unit per 2,500 sq ft density cap; no commercial use)",
            "min_lot_size": "Max 1 residential unit per 2,500 sq ft of lot area",
            "front_setback": "Min 10 ft from street property line; no front-yard parking off driveway",
            "side_rear_setback": "Min 3 ft from side and rear property lines",
            "max_height": "Max 3 stories and 40 ft above ground level",
            "plan_review": "WHCA plan review not required",
            "notes": "Adjoining 1903 subdivisions recorded in Map Book Vol. 1, Pg. 60 and jointly restricted under Clerk File #T056649.",
        },
        "citations": [
            {"label": "Clerk File #T056649 (PDF)", "url": "public/deeds/woodland-heights/Highland-Park-Official.pdf"},
            {"label": "WHCA Land Use Archive", "url": "https://www.woodland-heights.org/land-use"},
        ],
        "documents": [
            {
                "title": "Highland Park Deed Restrictions (Official Recorded Copy)",
                "doc_type": "official",
                "local_url": "public/deeds/woodland-heights/Highland-Park-Official.pdf",
                "external_url": "https://www.woodland-heights.org/s/Highland-Park-Official.pdf",
                "clerk_file": "Clerk File #T056649 / Film Code 513-39-0760",
            },
            {
                "title": "Highland Park Deed Restrictions (Searchable Text)",
                "doc_type": "searchable",
                "local_url": "public/deeds/woodland-heights/Highland-Park-Unofficial.pdf",
                "external_url": "https://www.woodland-heights.org/s/Highland-Park-Unofficial.pdf",
                "clerk_file": "Searchable Transcription",
            },
        ],
    },
    "plat_4925_rodgers": {
        "subdivision_id": "plat_4925_rodgers",
        "subdivision_name": "Rodgers",
        "civic_association": "Woodland Heights Civic Association",
        "civic_association_url": "https://www.woodland-heights.org/land-use",
        "plat_year": 1903,
        "vol_page": "030-139",
        "plat_citation": "Map Book Vol. 1, Pg. 60 & Deed Vol. 195, Pg. 376 · HCAD Subdiv. Code 030-139",
        "deed_citation": "Harris County Clerk File #T056649 (Film Code 513-39-0760) · Joint Highland Park & Rodgers Declaration",
        "covenant_summary": {
            "allowed_use": "Residential only (incidental non-public home office allowed; 'Residence' includes house, townhouse, condo, apartment, or garage apartment subject to 1 unit per 2,500 sq ft density cap; no commercial use)",
            "min_lot_size": "Max 1 residential unit per 2,500 sq ft of lot area",
            "front_setback": "Min 10 ft from street property line; no front-yard parking off driveway",
            "side_rear_setback": "Min 3 ft from side and rear property lines",
            "max_height": "Max 3 stories and 40 ft above ground level",
            "plan_review": "WHCA plan review not required",
            "notes": "Platted June 18, 1903 in Map Book Vol. 1, Pg. 60 alongside Highland Park and jointly restricted under Clerk File #T056649.",
        },
        "citations": [
            {"label": "Clerk File #T056649 (PDF)", "url": "public/deeds/woodland-heights/Rodgers-Official.pdf"},
            {"label": "WHCA Land Use Archive", "url": "https://www.woodland-heights.org/land-use"},
        ],
        "documents": [
            {
                "title": "Rodgers Addition Deed Restrictions (Official Recorded Copy)",
                "doc_type": "official",
                "local_url": "public/deeds/woodland-heights/Rodgers-Official.pdf",
                "external_url": "https://www.woodland-heights.org/s/Rodgers-Official.pdf",
                "clerk_file": "Clerk File #T056649 / Film Code 513-39-0760",
            },
            {
                "title": "Rodgers Addition Deed Restrictions (Searchable Text)",
                "doc_type": "searchable",
                "local_url": "public/deeds/woodland-heights/Rodgers-Unofficial.pdf",
                "external_url": "https://www.woodland-heights.org/s/Rodgers-Unofficial.pdf",
                "clerk_file": "Searchable Transcription",
            },
        ],
    },
    "plat_6618_woodson_place": {
        "subdivision_id": "plat_6618_woodson_place",
        "subdivision_name": "Woodson Place",
        "civic_association": "Woodland Heights Civic Association",
        "civic_association_url": "https://www.woodland-heights.org/land-use",
        "plat_year": 1922,
        "vol_page": "051-373",
        "plat_citation": "Deed Records Vol. 493, Pg. 66 · HCAD Subdiv. Code 051-373",
        "deed_citation": "Harris County Clerk File #T467865 (Film Code 521-61-1334) · Original Dedication Vol. 493, Pg. 66",
        "covenant_summary": {
            "allowed_use": "Residential only (1 primary single-family residence + optional 1 secondary garage apartment)",
            "min_lot_size": "Max 1 primary residence per 5,000 sq ft (with secondary unit, total density <= 1 unit per 2,500 sq ft)",
            "front_setback": "30 ft (Blocks 1, 2, 3, 9), 25 ft (Block 4), or 20 ft (Blocks 5, 6, 7, 8); open porches may project 8 ft",
            "side_rear_setback": "10 ft side setback along Julian and Michaux Streets (3 ft elsewhere); garages min 65 ft from front line",
            "max_height": "Max 3 stories and 40 ft above ground level",
            "plan_review": "WHCA plan review not required",
            "notes": "Residences may not face Julian Street or Michaux Street.",
        },
        "citations": [
            {"label": "Clerk File #T467865 (PDF)", "url": "public/deeds/woodland-heights/Woodson-Place-Official.pdf"},
            {"label": "WHCA Land Use Archive", "url": "https://www.woodland-heights.org/land-use"},
        ],
        "documents": [
            {
                "title": "Woodson Place Deed Restrictions (Official Recorded Copy)",
                "doc_type": "official",
                "local_url": "public/deeds/woodland-heights/Woodson-Place-Official.pdf",
                "external_url": "https://www.woodland-heights.org/s/Woodson-Place-Official.pdf",
                "clerk_file": "Clerk File #T467865 / Film Code 521-61-1334",
            },
            {
                "title": "Woodson Place Deed Restrictions (Searchable Text)",
                "doc_type": "searchable",
                "local_url": "public/deeds/woodland-heights/Woodson-Place-Unofficial.pdf",
                "external_url": "https://www.woodland-heights.org/s/Woodson-Place-Unofficial.pdf",
                "clerk_file": "Searchable Transcription",
            },
        ],
    },
    "plat_6601_woodland_terrace": {
        "subdivision_id": "plat_6601_woodland_terrace",
        "subdivision_name": "Woodland Terrace",
        "civic_association": "Woodland Heights Civic Association",
        "civic_association_url": "https://www.woodland-heights.org/land-use",
        "plat_year": 1923,
        "vol_page": "061-192",
        "plat_citation": "Map Book Vol. 7, Pgs. 22 & 70 · HCAD Subdiv. Code 061-192",
        "deed_citation": "Deed Records Vol. 524, Pg. 416 · Clerk Files #L812728, #20090471789 & #RP-2017-45903",
        "covenant_summary": {
            "allowed_use": "Single-family residential on interior blocks; restricted light commercial/professional on designated edge lots (Block 19) subject to Ch. 30 noise limits",
            "min_lot_size": "Single-family residential lot protections per 1989 Declaration and 2009/2017 Amendments",
            "front_setback": "Per recorded plat building lines (Map Book Vol. 7, Pgs. 22 & 70)",
            "side_rear_setback": "Min 3 ft side/rear residential setback; 6 ft screening fence required on commercial edge lots",
            "max_height": "Residential scale per 1989/2009 Declarations",
            "plan_review": "WHCA plan review not required (COH HAHC COA required inside Norhill Historic District)",
            "notes": "Includes 1989 Restated Restrictions, 2009 Modification (#20090471789), and 2017 First Amendment (#RP-2017-45903).",
        },
        "citations": [
            {"label": "Clerk File #20090471789 (PDF)", "url": "public/deeds/woodland-heights/Woodland-Terrace-2009-10-15-Modified.pdf"},
            {"label": "Clerk File #RP-2017-45903 (PDF)", "url": "public/deeds/woodland-heights/RP-2017-45903-Woodland-Terrace-First-Amendment.pdf"},
        ],
        "documents": [
            {
                "title": "Woodland Terrace 2009 Modified Deed Restrictions (#20090471789)",
                "doc_type": "official",
                "local_url": "public/deeds/woodland-heights/Woodland-Terrace-2009-10-15-Modified.pdf",
                "external_url": "https://www.woodland-heights.org/s/Woodland-Terrace-2009-10-15-Modified-Deed-Restrictions-sigs-redacted.pdf",
                "clerk_file": "Clerk File #20090471789",
            },
            {
                "title": "Woodland Terrace 1989 Recorded Deed Restrictions (#L812728 / #M279182)",
                "doc_type": "official",
                "local_url": "public/deeds/woodland-heights/Woodland-Terrace-1989-08-17.pdf",
                "external_url": "https://www.woodland-heights.org/s/Woodland-Terrace-1989-08-17-Deed-Restrictions-sigs-redacted.pdf",
                "clerk_file": "Clerk File #L812728",
            },
            {
                "title": "Woodland Terrace 2017 First Amendment (#RP-2017-45903)",
                "doc_type": "amendment",
                "local_url": "public/deeds/woodland-heights/RP-2017-45903-Woodland-Terrace-First-Amendment.pdf",
                "external_url": "https://www.woodland-heights.org/s/RP-2017-45903-First-Amendment-to-Declaration-of-Restrictions-for-Woodland-Terrace-Subdivision-no-sig.pdf",
                "clerk_file": "Clerk File #RP-2017-45903",
            },
            {
                "title": "Woodland Terrace Petition & Extension (Official Copy)",
                "doc_type": "petition",
                "local_url": "public/deeds/woodland-heights/Woodland-Terrace-Petition-Official.pdf",
                "external_url": "https://www.woodland-heights.org/s/Woodland-Terrace-Petition-to-Modify-Deed-Restrictions-Official.pdf",
                "clerk_file": "Petition Instrument",
            },
            {
                "title": "Woodland Terrace Petition & Extension (Searchable Text)",
                "doc_type": "searchable",
                "local_url": "public/deeds/woodland-heights/Woodland-Terrace-Petition-Unofficial.pdf",
                "external_url": "https://www.woodland-heights.org/s/Woodland-Terrace-Petition-to-Modify-Deed-Restrictions-Unofficial.pdf",
                "clerk_file": "Searchable Transcription",
            },
        ],
    },
    "plat_3902_norhill": {
        "subdivision_id": "plat_3902_norhill",
        "subdivision_name": "Norhill",
        "civic_association": "Woodland Heights Civic Association",
        "civic_association_url": "https://www.woodland-heights.org/land-use",
        "plat_year": 1920,
        "vol_page": "062-075",
        "plat_citation": "Map Book Vol. 6, Pg. 3 (Filing #81062) · HCAD Subdiv. Code 062-075",
        "deed_citation": "Harris County Clerk File #20150276464 (2015 Amended Restrictions) & #H009467 (1981 Renewal)",
        "covenant_summary": {
            "allowed_use": "Single-family residential on interior lots; retail or commercial business permitted on 12 designated corner lots (Lots 1-3 Blk 1, Lots 24-26 Blk 4, Lots 1-3 Blk 18, Lots 10-12 Blk 24)",
            "min_lot_size": "1 single-family residence per platted residential lot",
            "front_setback": "Min 20 ft front residential setback; garages and outbuildings min 60 ft from front street line on interior lots",
            "side_rear_setback": "Min 3 ft from interior side/rear lot lines; 20 ft side-street setback on west/east-facing corner lots",
            "max_height": "2-story historic bungalow / cottage scale (also regulated by COH Norhill Historic District)",
            "plan_review": "Architectural plans submitted to the Norhill Committee (or WHCA if no Norhill Committee is active), plus COH HAHC Certificate of Appropriateness",
            "notes": "Platted in October 1920 (Map Book Vol. 6, Pg. 3) by William C. 'Will' Hogg and the Varner Realty Company; restated June 25, 2015 under Clerk File #20150276464.",
        },
        "citations": [
            {"label": "Clerk File #20150276464 (2015 PDF)", "url": "public/deeds/woodland-heights/Norhill-Official.pdf"},
            {"label": "Clerk File #H009467 (1981 Renewal PDF)", "url": "public/deeds/woodland-heights/Norhill-Historical.pdf"},
            {"label": "COH Norhill Historic District", "url": "https://www.houstontx.gov/planning/HistoricPres/District_Reference.html"},
        ],
        "documents": [
            {
                "title": "Norhill Addition 2015 Amended & Extended Deed Restrictions (#20150276464)",
                "doc_type": "official",
                "local_url": "public/deeds/woodland-heights/Norhill-Official.pdf",
                "external_url": "https://www.woodland-heights.org/s/Norhill-Official.pdf",
                "clerk_file": "Clerk File #20150276464 (Filed 06/25/2015)",
            },
            {
                "title": "Norhill Addition 1981 Renewed Historical Deed Restrictions (#H009467)",
                "doc_type": "historical",
                "local_url": "public/deeds/woodland-heights/Norhill-Historical.pdf",
                "external_url": "https://www.woodland-heights.org/s/Norhill-Historical.pdf",
                "clerk_file": "Clerk File #H009467 / Film Code 187-83-2046 (1981)",
            },
        ],
    },
    "plat_1701_east_norhill": {
        "subdivision_id": "plat_1701_east_norhill",
        "subdivision_name": "East Norhill",
        "civic_association": "Woodland Heights Civic Association",
        "civic_association_url": "https://www.woodland-heights.org/land-use",
        "plat_year": 1924,
        "vol_page": "062-113",
        "plat_citation": "Map Book Vol. 6, Pg. 65 (Filing #154530) · HCAD Subdiv. Code 062-113",
        "deed_citation": "Harris County Clerk Filing #154530 (1924 Plat) · Adjacent Norhill Reference (#20150276464 / #H009467)",
        "covenant_summary": {
            "allowed_use": "Single-family residential",
            "min_lot_size": "1 single-family residence per platted residential lot",
            "front_setback": "Per recorded East Norhill plat building lines (Map Book Vol. 6, Pg. 65)",
            "side_rear_setback": "Standard residential side/rear setbacks and COH Norhill Historic District guidelines",
            "max_height": "Historic residential scale (also in COH Norhill Historic District)",
            "plan_review": "COH HAHC Certificate of Appropriateness required inside Norhill Historic District (check WHCA / Norhill Committee applicability)",
            "notes": "Eastern section of Norhill (292 lots, Map Book Vol. 6, Pg. 65, Filing #154530), opened in June 1924 by Varner Realty Company. Adjacent original Norhill Addition restrictions (#20150276464 / #H009467) linked for neighborhood reference.",
        },
        "citations": [
            {"label": "Adjacent Norhill #20150276464 (PDF)", "url": "public/deeds/woodland-heights/Norhill-Official.pdf"},
            {"label": "COH Norhill Historic District", "url": "https://www.houstontx.gov/planning/HistoricPres/District_Reference.html"},
        ],
        "documents": [
            {
                "title": "Adjacent Norhill Addition (Vol. 6, Pg. 3) Reference Restrictions (#20150276464)",
                "doc_type": "official",
                "local_url": "public/deeds/woodland-heights/Norhill-Official.pdf",
                "external_url": "https://www.woodland-heights.org/s/Norhill-Official.pdf",
                "clerk_file": "Reference: Clerk File #20150276464 (Norhill Vol. 6, Pg. 3)",
            },
            {
                "title": "Adjacent Norhill Addition 1981 Renewal Reference (#H009467)",
                "doc_type": "historical",
                "local_url": "public/deeds/woodland-heights/Norhill-Historical.pdf",
                "external_url": "https://www.woodland-heights.org/s/Norhill-Historical.pdf",
                "clerk_file": "Reference: Clerk File #H009467 (Norhill Vol. 6, Pg. 3)",
            },
        ],
    },
    "plat_3929_north_norhill": {
        "subdivision_id": "plat_3929_north_norhill",
        "subdivision_name": "North Norhill",
        "civic_association": "Proctor Plaza Neighborhood Association / WHCA",
        "civic_association_url": "https://www.woodland-heights.org/land-use",
        "plat_year": 1923,
        "vol_page": "062-112",
        "plat_citation": "Plat Film #649-238 · HCAD Subdiv. Code 062-112",
        "deed_citation": "North Norhill 1923 Varner Realty Co. Plat · Adjacent Norhill Reference (#H009467)",
        "covenant_summary": {
            "allowed_use": "Single-family residential",
            "min_lot_size": "Standard 5,000 sq ft residential lots (plus COH Norhill Historic District protections)",
            "front_setback": "Per recorded North Norhill building lines and COH Norhill Historic District design guidelines",
            "side_rear_setback": "Min 3 to 5 ft residential side/rear setbacks",
            "max_height": "Regulated by COH Norhill Historic District Certificate of Appropriateness",
            "plan_review": "COH HAHC Certificate of Appropriateness required inside Norhill Historic District",
            "notes": "Northern section of Norhill north of Pecore Street, opened in May 1923 by Varner Realty Company (555 lots). Adjacent original Norhill Addition restrictions (#H009467) linked for historical reference.",
        },
        "citations": [
            {"label": "Adjacent Norhill #H009467 (PDF)", "url": "public/deeds/woodland-heights/Norhill-Historical.pdf"},
            {"label": "COH Norhill Historic District", "url": "https://www.houstontx.gov/planning/HistoricPres/District_Reference.html"},
        ],
        "documents": [
            {
                "title": "Adjacent Norhill Addition 1981 Renewal Reference (#H009467)",
                "doc_type": "historical",
                "local_url": "public/deeds/woodland-heights/Norhill-Historical.pdf",
                "external_url": "https://www.woodland-heights.org/s/Norhill-Historical.pdf",
                "clerk_file": "Reference: Clerk File #H009467 (Norhill Vol. 6, Pg. 3)",
            },
        ],
    },
    "plat_3903_norhill_park": {
        "subdivision_id": "plat_3903_norhill_park",
        "subdivision_name": "Norhill Park",
        "civic_association": "Woodland Heights Civic Association",
        "civic_association_url": "https://www.woodland-heights.org/land-use",
        "plat_year": 1921,
        "vol_page": "056-113",
        "plat_citation": "HCAD Subdiv. Code 056-113",
        "deed_citation": "Norhill Park 1921 Subdivision · Adjacent Norhill Reference (#20150276464)",
        "covenant_summary": {
            "allowed_use": "Single-family residential",
            "min_lot_size": "1 single-family residence per platted lot",
            "front_setback": "Per recorded Norhill Park plat building lines",
            "side_rear_setback": "Standard residential side/rear setbacks",
            "max_height": "Historic residential scale",
            "plan_review": "Check parcel-specific covenants and COH HAHC COA applicability",
            "notes": "Adjacent 1921 Norhill Park subdivision; original Norhill Addition restrictions (#20150276464) linked for neighborhood reference.",
        },
        "citations": [
            {"label": "Adjacent Norhill #20150276464 (PDF)", "url": "public/deeds/woodland-heights/Norhill-Official.pdf"},
            {"label": "WHCA Land Use Archive", "url": "https://www.woodland-heights.org/land-use"},
        ],
        "documents": [
            {
                "title": "Adjacent Norhill Addition (Vol. 6, Pg. 3) Reference Restrictions (#20150276464)",
                "doc_type": "official",
                "local_url": "public/deeds/woodland-heights/Norhill-Official.pdf",
                "external_url": "https://www.woodland-heights.org/s/Norhill-Official.pdf",
                "clerk_file": "Reference: Clerk File #20150276464 (Norhill Vol. 6, Pg. 3)",
            },
        ],
    },
    # Boulevard Oaks / Broadacres Collection
    "plat_0752_broadacres": {
        "subdivision_id": "plat_0752_broadacres",
        "subdivision_name": "Broadacres",
        "civic_association": "Broadacres Homeowners Association / Boulevard Oaks Civic Association",
        "civic_association_url": "https://www.boulevardoaks.org/DeedLibrary/BOCADeedRestrictions.htm",
        "plat_year": 1923,
        "vol_page": "053-039",
        "plat_citation": "Deed Records Vol. 526, Pg. 497 (1923 Plat) · Replat Film #672-025 · HCAD Subdiv. Code 053-039",
        "deed_citation": "Harris County Clerk File #RP-2022-305552 (2022 Amended Restrictions) · Original Plat & Dedication Vol. 526, Pg. 497 (#108866)",
        "covenant_summary": {
            "allowed_use": "Strictly single-family residential estates only",
            "min_lot_size": "Large estate lots (1 single-family residence per platted estate lot; no subdivision of lots)",
            "front_setback": "Deep monumental front setbacks along North & South Boulevards",
            "side_rear_setback": "Generous estate side and rear setbacks per 1923/2022 Broadacres Covenants",
            "max_height": "Estate residential height & massing limits",
            "plan_review": "Broadacres Architectural Control Committee approval + COH HAHC COA (Broadacres Historic District)",
            "notes": "Organized in 1922 and platted in 1923 by Capt. James A. Baker and James A. Baker Jr. with master site planning and entry portals by William Ward Watkin and residences by Watkin, John F. Staub, and Birdsall P. Briscoe.",
        },
        "citations": [
            {"label": "Clerk File #RP-2022-305552 (PDF)", "url": "public/deeds/boulevard-oaks/Broadacres-2022-Amended-Restrictions.pdf"},
            {"label": "BHOA Summary Sheet (PDF)", "url": "public/deeds/boulevard-oaks/Broadacres-Original-Restrictions.pdf"},
            {"label": "NRHP Nomination #80004130 (Broadacres HD)", "url": "https://atlas.thc.texas.gov/NR/pdfs/80004130/80004130.pdf"},
        ],
        "documents": [
            {
                "title": "Broadacres 2022 Amended & Restated Deed Restrictions (#RP-2022-305552)",
                "doc_type": "official",
                "local_url": "public/deeds/boulevard-oaks/Broadacres-2022-Amended-Restrictions.pdf",
                "external_url": "https://www.boulevardoaks.org/DeedLibrary/BroadacresCurrent%20-%202022.06.13%20Amended%20and%20Restated%20Restriction%20for%20Broadacres%20Subdivision.pdf",
                "clerk_file": "Clerk File #RP-2022-305552 (Recorded 06/13/2022)",
            },
            {
                "title": "Broadacres Deed Restrictions & Architectural Summary Sheet",
                "doc_type": "searchable",
                "local_url": "public/deeds/boulevard-oaks/Broadacres-Original-Restrictions.pdf",
                "external_url": "https://www.boulevardoaks.org/DeedLibrary/Broadacres.pdf",
                "clerk_file": "BHOA Summary (Ref. #RP-2022-305552)",
            },
        ],
    },
    "plat_1735_edgemont": {
        "subdivision_id": "plat_1735_edgemont",
        "subdivision_name": "Edgemont",
        "civic_association": "Edgemont Civic Association / Boulevard Oaks Civic Association",
        "civic_association_url": "https://www.boulevardoaks.org/DeedLibrary/BOCADeedRestrictions.htm",
        "plat_year": 1923,
        "vol_page": "053-033",
        "plat_citation": "Deed Records Vol. 534, Pg. 286 & Vol. 572, Pgs. 491-492 · HCAD Subdiv. Code 053-033",
        "deed_citation": "Harris County Clerk Files #20080519363 (2008 Restated Restrictions) & #20110542736 (2011 Dedicatory Instruments)",
        "covenant_summary": {
            "allowed_use": "Single-family residential only",
            "min_lot_size": "1 single-family residence per platted lot along North & South Boulevards",
            "front_setback": "Per recorded Edgemont plat building lines along tree-lined esplanades",
            "side_rear_setback": "Per Edgemont 2008 Restated Restrictions (#20080519363)",
            "max_height": "2-to-3 story historic residential scale (COH Boulevard Oaks Historic District)",
            "plan_review": "Edgemont Civic Association architectural review + COH HAHC Certificate of Appropriateness",
            "notes": "Platted in 1923 by The Guardian Trust Company as the first subdivision of Boulevard Oaks along North and South Boulevards.",
        },
        "citations": [
            {"label": "Clerk File #20080519363 (PDF)", "url": "public/deeds/boulevard-oaks/Edgemont-2008-Restrictions.pdf"},
            {"label": "Clerk File #20110542736 (PDF)", "url": "public/deeds/boulevard-oaks/Edgemont-2012-Dedicatory-Instruments.pdf"},
        ],
        "documents": [
            {
                "title": "Edgemont Recorded Dedicatory Instruments (#20110542736)",
                "doc_type": "official",
                "local_url": "public/deeds/boulevard-oaks/Edgemont-2012-Dedicatory-Instruments.pdf",
                "external_url": "https://www.boulevardoaks.org/DeedLibrary/EDGEMONTDEDICATORYINSTRUMENTSasfiled12282012.pdf",
                "clerk_file": "Clerk File #20110542736 (Recorded 12/28/2011)",
            },
            {
                "title": "Edgemont 2008 Restated Deed Restrictions (#20080519363)",
                "doc_type": "official",
                "local_url": "public/deeds/boulevard-oaks/Edgemont-2008-Restrictions.pdf",
                "external_url": "https://www.boulevardoaks.org/DeedLibrary/Edgemont_08272008forWeb.pdf",
                "clerk_file": "Clerk File #20080519363 (Recorded 10/16/2008)",
            },
        ],
    },
    "plat_3910_north_edgemont": {
        "subdivision_id": "plat_3910_north_edgemont",
        "subdivision_name": "North Edgemont",
        "civic_association": "Boulevard Oaks Civic Association",
        "civic_association_url": "https://www.boulevardoaks.org/DeedLibrary/BOCADeedRestrictions.htm",
        "plat_year": 1924,
        "vol_page": "056-283",
        "plat_citation": "Deed Records Vol. 557, Pg. 77 · HCAD Subdiv. Code 056-283",
        "deed_citation": "Harris County Clerk File #E941368 (Film Code 152-07-1226) · North Edgemont Restrictions",
        "covenant_summary": {
            "allowed_use": "Single-family residential only",
            "min_lot_size": "1 single-family dwelling per platted lot",
            "front_setback": "Per recorded North Edgemont building lines and New Construction Guidelines",
            "side_rear_setback": "Per North Edgemont Deed Restrictions (#E941368)",
            "max_height": "Residential scale per North Edgemont Guidelines & Boulevard Oaks Historic District",
            "plan_review": "Architectural review + COH HAHC Certificate of Appropriateness",
            "notes": "Platted in Deed Records Vol. 557, Pg. 77 (c. 1924); includes dedicated Deed Restriction Guidelines for New Construction.",
        },
        "citations": [
            {"label": "Clerk File #E941368 (PDF)", "url": "public/deeds/boulevard-oaks/North-Edgemont-Restrictions.pdf"},
            {"label": "Design Guidelines (PDF)", "url": "public/deeds/boulevard-oaks/North-Edgemont-Design-Guidelines.pdf"},
        ],
        "documents": [
            {
                "title": "North Edgemont Recorded Deed Restrictions (#E941368)",
                "doc_type": "official",
                "local_url": "public/deeds/boulevard-oaks/North-Edgemont-Restrictions.pdf",
                "external_url": "https://www.boulevardoaks.org/DeedLibrary/NorthEdgmt.pdf",
                "clerk_file": "Clerk File #E941368 / Film Code 152-07-1226",
            },
            {
                "title": "North Edgemont Guidelines for New Construction",
                "doc_type": "searchable",
                "local_url": "public/deeds/boulevard-oaks/North-Edgemont-Design-Guidelines.pdf",
                "external_url": "https://www.boulevardoaks.org/DeedLibrary/North%20Edgemont%20Deed%20Restriction%20Guidelines%20for%20New%20Construction.pdf",
                "clerk_file": "Architectural Guidelines",
            },
        ],
    },
    "plat_6264_west_edgemont": {
        "subdivision_id": "plat_6264_west_edgemont",
        "subdivision_name": "West Edgemont",
        "civic_association": "Edgemont Civic Association / Boulevard Oaks Civic Association",
        "civic_association_url": "https://www.boulevardoaks.org/DeedLibrary/BOCADeedRestrictions.htm",
        "plat_year": 1925,
        "vol_page": "056-251",
        "plat_citation": "Deed Records Vol. 572, Pg. 2 & Pgs. 155-156 · Replat Film #671-167 · HCAD Subdiv. Code 056-251",
        "deed_citation": "Harris County Clerk File #20080519364 · West Edgemont 2008 Restated Restrictions",
        "covenant_summary": {
            "allowed_use": "Single-family residential only",
            "min_lot_size": "1 single-family residence per platted lot",
            "front_setback": "Per recorded West Edgemont plat building lines (Milford, Banks, Vassar)",
            "side_rear_setback": "Per 2008 West Edgemont Restrictions (#20080519364)",
            "max_height": "Historic residential scale",
            "plan_review": "Edgemont Civic Association architectural review",
            "notes": "Platted in Deed Records Vol. 572, Pg. 2 & Pgs. 155-156 (c. 1925) along Milford, Banks, and Vassar Streets.",
        },
        "citations": [
            {"label": "Clerk File #20080519364 (PDF)", "url": "public/deeds/boulevard-oaks/West-Edgemont-2008-Restrictions.pdf"},
        ],
        "documents": [
            {
                "title": "West Edgemont 2008 Restated Deed Restrictions (#20080519364)",
                "doc_type": "official",
                "local_url": "public/deeds/boulevard-oaks/West-Edgemont-2008-Restrictions.pdf",
                "external_url": "https://www.boulevardoaks.org/DeedLibrary/W_Edgemont_08282008_for_web.pdf",
                "clerk_file": "Clerk File #20080519364 (Recorded 10/16/2008)",
            },
        ],
    },
    "plat_4181_ormond_place": {
        "subdivision_id": "plat_4181_ormond_place",
        "subdivision_name": "Ormond Place",
        "civic_association": "Boulevard Oaks Civic Association",
        "civic_association_url": "https://www.boulevardoaks.org/DeedLibrary/BOCADeedRestrictions.htm",
        "plat_year": 1924,
        "vol_page": "053-041",
        "plat_citation": "Original Filing #129128 · Replat Film #674-842 · HCAD Subdiv. Code 053-041",
        "deed_citation": "Harris County Film Code #122-92-1286 (Renewed Ormond Place Restrictions) · Original Filing #129128",
        "covenant_summary": {
            "allowed_use": "Single-family residential only",
            "min_lot_size": "1 single-family residence per platted lot",
            "front_setback": "Per 1924 Recorded Plat and Ormond Place Covenants",
            "side_rear_setback": "Standard residential side/rear setbacks",
            "max_height": "2-to-3 story residential scale",
            "plan_review": "Ormond Place / BOCA Architectural Control Committee",
            "notes": "Platted in 1924 in Boulevard Oaks; restrictions renewed under Film Code #122-92-1286.",
        },
        "citations": [
            {"label": "Film Code #122-92-1286 (PDF)", "url": "public/deeds/boulevard-oaks/Ormond-Place-Restrictions.pdf"},
        ],
        "documents": [
            {
                "title": "Ormond Place Renewed Deed Restrictions (Film Code #122-92-1286)",
                "doc_type": "searchable",
                "local_url": "public/deeds/boulevard-oaks/Ormond-Place-Restrictions.pdf",
                "external_url": "https://www.boulevardoaks.org/DeedLibrary/Ormond-2clean.pdf",
                "clerk_file": "Film Code #122-92-1286",
            },
        ],
    },
    "plat_6292_west_ormond_place": {
        "subdivision_id": "plat_6292_west_ormond_place",
        "subdivision_name": "West Ormond Place",
        "civic_association": "Cresmere-West Ormond Civic Association / BOCA",
        "civic_association_url": "https://www.boulevardoaks.org/DeedLibrary/BOCADeedRestrictions.htm",
        "plat_year": 1931,
        "vol_page": "065-029",
        "plat_citation": "Deed Records Vol. 855, Pg. 725 · HCAD Subdiv. Code 065-029",
        "deed_citation": "Harris County Clerk Files #K948159 (1987) & #Y104895 (2004) · Original 1937 Restrictions Vol. 1041, Pg. 8",
        "covenant_summary": {
            "allowed_use": "Single-family residential only",
            "min_lot_size": "1 single-family residence per platted lot",
            "front_setback": "Per recorded West Ormond Place building lines (Deed Vol. 855, Pg. 725)",
            "side_rear_setback": "Per 1987/2004 Cresmere-West Ormond Covenants",
            "max_height": "Residential scale per CWOCA Architectural Guidelines",
            "plan_review": "Cresmere-West Ormond Civic Association (CWOCA) ARC Review",
            "notes": "Platted in Deed Records Vol. 855, Pg. 725 (c. 1931) and jointly restricted with Cresmere Place on Feb. 22, 1937 (Deed Vol. 1041, Pg. 8).",
        },
        "citations": [
            {"label": "Clerk File #K948159 (1987 PDF)", "url": "public/deeds/boulevard-oaks/West-Ormond-Place-1987-Restrictions.pdf"},
            {"label": "Clerk File #Y104895 (2004 PDF)", "url": "public/deeds/boulevard-oaks/Cresmere-West-Ormond-2004-Restrictions.pdf"},
        ],
        "documents": [
            {
                "title": "West Ormond Place 1987 Recorded Deed Restrictions (#K948159)",
                "doc_type": "official",
                "local_url": "public/deeds/boulevard-oaks/West-Ormond-Place-1987-Restrictions.pdf",
                "external_url": "https://www.boulevardoaks.org/DeedLibrary/WestOrmond-1987.pdf",
                "clerk_file": "Clerk File #K948159 / Film Code 071-74-1598 (01/30/1987)",
            },
            {
                "title": "Cresmere & West Ormond 2004 Modified Restrictions (#Y104895)",
                "doc_type": "amendment",
                "local_url": "public/deeds/boulevard-oaks/Cresmere-West-Ormond-2004-Restrictions.pdf",
                "external_url": "https://www.boulevardoaks.org/DeedLibrary/Cresmere-WestOrmond-2004nosigs.pdf",
                "clerk_file": "Clerk File #Y104895 / Film Code 596-62-0804 (12/03/2004)",
            },
        ],
    },
    "plat_1359_cresmere_place": {
        "subdivision_id": "plat_1359_cresmere_place",
        "subdivision_name": "Cresmere Place",
        "civic_association": "Cresmere-West Ormond Civic Association / BOCA",
        "civic_association_url": "https://www.boulevardoaks.org/DeedLibrary/BOCADeedRestrictions.htm",
        "plat_year": 1936,
        "vol_page": "066-039",
        "plat_citation": "Deed Records Vol. 998, Pg. 193 · Replat Film #635-111 · HCAD Subdiv. Code 066-039",
        "deed_citation": "Harris County Clerk File #Y104895 (2004 Modified Restrictions) · Original 1937 Restrictions Vol. 1041, Pg. 8",
        "covenant_summary": {
            "allowed_use": "Single-family residential only",
            "min_lot_size": "1 single-family residence per platted lot",
            "front_setback": "Per recorded Cresmere Place building lines (Deed Vol. 998, Pg. 193)",
            "side_rear_setback": "Per CWOCA Deed Restrictions (#Y104895)",
            "max_height": "Residential scale per CWOCA Architectural Guidelines",
            "plan_review": "Cresmere-West Ormond Civic Association (CWOCA) ARC Review",
            "notes": "Platted in 1936 (Deed Records Vol. 998, Pg. 193) and jointly administered with West Ormond Place by CWOCA.",
        },
        "citations": [
            {"label": "Clerk File #Y104895 (2004 PDF)", "url": "public/deeds/boulevard-oaks/Cresmere-West-Ormond-2004-Restrictions.pdf"},
        ],
        "documents": [
            {
                "title": "Cresmere & West Ormond 2004 Modified Restrictions (#Y104895)",
                "doc_type": "official",
                "local_url": "public/deeds/boulevard-oaks/Cresmere-West-Ormond-2004-Restrictions.pdf",
                "external_url": "https://www.boulevardoaks.org/DeedLibrary/Cresmere-WestOrmond-2004nosigs.pdf",
                "clerk_file": "Clerk File #Y104895 / Film Code 596-62-0804 (12/03/2004)",
            },
        ],
    },
    "plat_6037_vassar_place": {
        "subdivision_id": "plat_6037_vassar_place",
        "subdivision_name": "Vassar Place",
        "civic_association": "Boulevard Oaks Civic Association",
        "civic_association_url": "https://www.boulevardoaks.org/DeedLibrary/BOCADeedRestrictions.htm",
        "plat_year": 1936,
        "vol_page": "065-108",
        "plat_citation": "Deed Records Vol. 1031 (1936 Dedication) · HCAD Subdiv. Code 065-108",
        "deed_citation": "Harris County Deed Records Vol. 1031 (Executed Dec. 29, 1936; Recorded Jan. 6, 1937)",
        "covenant_summary": {
            "allowed_use": "Single-family residential only",
            "min_lot_size": "1 single-family residence per platted lot",
            "front_setback": "25 ft from Vassar Street property line; 10 ft side-street setback along Hazard and Wilton Streets",
            "side_rear_setback": "Standard residential side/rear setbacks",
            "max_height": "2-to-3 story residential scale",
            "plan_review": "Vassar Place / BOCA Architectural Control",
            "notes": "Restricted by Ben Taub on December 29, 1936 (recorded January 6, 1937 in Deed Records Vol. 1031; historical 1936 racial clause is void and unenforceable under federal and Texas law).",
        },
        "citations": [
            {"label": "Deed Records Vol. 1031 (1937 PDF)", "url": "public/deeds/boulevard-oaks/Vassar-Place-Restrictions.pdf"},
        ],
        "documents": [
            {
                "title": "Vassar Place 1936-1937 Recorded Deed Restrictions (Deed Vol. 1031)",
                "doc_type": "official",
                "local_url": "public/deeds/boulevard-oaks/Vassar-Place-Restrictions.pdf",
                "external_url": "https://www.boulevardoaks.org/DeedLibrary/VassarPl-1.pdf",
                "clerk_file": "Deed Records Vol. 1031 (Recorded 01/06/1937)",
            },
        ],
    },
    "plat_6036_vassar_court": {
        "subdivision_id": "plat_6036_vassar_court",
        "subdivision_name": "Vassar Court",
        "civic_association": "Boulevard Oaks Civic Association",
        "civic_association_url": "https://www.boulevardoaks.org/DeedLibrary/BOCADeedRestrictions.htm",
        "plat_year": 1938,
        "vol_page": "065-109",
        "plat_citation": "Map Book Vol. 15 (Clerk File #166457) · Prior Tract Vol. 998, Pg. 417 DR · HCAD Subdiv. Code 065-109",
        "deed_citation": "Harris County Clerk File #166457 (Recorded July 14, 1938, Map Records Vol. 15)",
        "covenant_summary": {
            "allowed_use": "Single-family residential only",
            "min_lot_size": "1 single-family residence per platted lot",
            "front_setback": "Per recorded Vassar Court building lines (Map Records Vol. 15)",
            "side_rear_setback": "Standard residential setbacks",
            "max_height": "Historic residential scale",
            "plan_review": "BOCA Architectural Review",
            "notes": "Dedicated and replatted as Block 1 of Vassar Courts by W. E. White on April 27, 1938 (recorded July 14, 1938 under Clerk File #166457).",
        },
        "citations": [
            {"label": "Clerk File #166457 (1938 PDF)", "url": "public/deeds/boulevard-oaks/Vassar-Court-Restrictions.pdf"},
        ],
        "documents": [
            {
                "title": "Vassar Court 1938 Dedication & Deed Restrictions (#166457)",
                "doc_type": "official",
                "local_url": "public/deeds/boulevard-oaks/Vassar-Court-Restrictions.pdf",
                "external_url": "https://www.boulevardoaks.org/DeedLibrary/VassarCt-1.pdf",
                "clerk_file": "Clerk File #166457 / Map Records Vol. 15 (07/14/1938)",
            },
        ],
    },
    "plat_1059_chevy_chase": {
        "subdivision_id": "plat_1059_chevy_chase",
        "subdivision_name": "Chevy Chase",
        "civic_association": "Boulevard Oaks Civic Association",
        "civic_association_url": "https://www.boulevardoaks.org/DeedLibrary/BOCADeedRestrictions.htm",
        "plat_year": 1928,
        "vol_page": "060-065",
        "plat_citation": "Map Book Vol. 9, Pg. 50 · Replat Film #521-278 · HCAD Subdiv. Code 060-065",
        "deed_citation": "Deed Records Vol. 777, Pg. 277 (Dec. 17, 1928) & Vol. 2398, Pg. 350 (May 17, 1951 Extension)",
        "covenant_summary": {
            "allowed_use": "Single-family residential only",
            "min_lot_size": "1 single-family residence per platted lot",
            "front_setback": "Per Chevy Chase recorded plat building lines (Map Book Vol. 9, Pg. 50)",
            "side_rear_setback": "Standard residential setbacks",
            "max_height": "Historic residential scale",
            "plan_review": "Chevy Chase / BOCA Architectural Control",
            "notes": "Replatted and restricted in 1928 (Map Book Vol. 9, Pg. 50; Deed Records Vol. 777, Pg. 277), with restrictions extended May 17, 1951 in Deed Records Vol. 2398, Pg. 350.",
        },
        "citations": [
            {"label": "Deed Vol. 2398, Pg. 350 (1951 PDF)", "url": "public/deeds/boulevard-oaks/Chevy-Chase-Restrictions.pdf"},
        ],
        "documents": [
            {
                "title": "Chevy Chase Addition 1951 Extended Deed Restrictions (Vol. 2398, Pg. 350)",
                "doc_type": "official",
                "local_url": "public/deeds/boulevard-oaks/Chevy-Chase-Restrictions.pdf",
                "external_url": "https://www.boulevardoaks.org/DeedLibrary/ChevyChase.pdf",
                "clerk_file": "Deed Records Vol. 2398, Pg. 350 (1951 Extension of 1928 Vol. 777, Pg. 277)",
            },
        ],
    },
    "plat_1049_cherokee": {
        "subdivision_id": "plat_1049_cherokee",
        "subdivision_name": "Cherokee",
        "civic_association": "Cherokee Civic Club / Boulevard Oaks Civic Association",
        "civic_association_url": "https://www.boulevardoaks.org/DeedLibrary/BOCADeedRestrictions.htm",
        "plat_year": 1924,
        "vol_page": "054-139",
        "plat_citation": "Deed Records Vol. 538, Pg. 442 (Original Plat) & Vol. 655, Pg. 298 (1926 Revised Plat) · HCAD Subdiv. Code 054-139",
        "deed_citation": "Harris County Clerk File #RP-2023-316842 (Recorded Aug. 18, 2023)",
        "covenant_summary": {
            "allowed_use": "Single-family residential only",
            "min_lot_size": "1 single-family residence per platted lot",
            "front_setback": "Per Cherokee Addition recorded building lines",
            "side_rear_setback": "Per 2023 Restated Cherokee Restrictions (#RP-2023-316842)",
            "max_height": "Residential scale per 2023 Cherokee Restrictions",
            "plan_review": "Cherokee Civic Club Architectural Control Committee",
            "notes": "Platted in Deed Records Vol. 538, Pg. 442 (revised 1926 in Vol. 655, Pg. 298); updated and recorded August 18, 2023 under Clerk File #RP-2023-316842.",
        },
        "citations": [
            {"label": "Clerk File #RP-2023-316842 (PDF)", "url": "public/deeds/boulevard-oaks/Cherokee-2023-Recorded-Restrictions.pdf"},
        ],
        "documents": [
            {
                "title": "Cherokee 2023 Recorded Deed Restrictions (#RP-2023-316842)",
                "doc_type": "official",
                "local_url": "public/deeds/boulevard-oaks/Cherokee-2023-Recorded-Restrictions.pdf",
                "external_url": "https://www.boulevardoaks.org/DeedLibrary/Cherokee%202023%20Restrictions%20(Recorded)-c.pdf",
                "clerk_file": "Clerk File #RP-2023-316842 (Recorded 08/18/2023)",
            },
        ],
    },
    "plat_4699_ranch_estates": {
        "subdivision_id": "plat_4699_ranch_estates",
        "subdivision_name": "Ranch Estates",
        "civic_association": "Boulevard Oaks Civic Association",
        "civic_association_url": "https://www.boulevardoaks.org/DeedLibrary/BOCADeedRestrictions.htm",
        "plat_year": 1946,
        "vol_page": "073-121",
        "plat_citation": "Map Book Vol. 22, Pg. 68 (Filing #325719) · HCAD Subdiv. Code 073-121",
        "deed_citation": "Harris County Clerk File #330584 · Deed Records Vol. 1446, Pgs. 350-351 (May 23, 1946)",
        "covenant_summary": {
            "allowed_use": "Single-family residential on interior lots (min 1,300 sq ft ground floor); duplexes with garage apartments permitted on Lots 35, 36, and 72",
            "min_lot_size": "1 single-family residence per platted interior lot (duplex permitted on Lots 35, 36, 72)",
            "front_setback": "Per recorded Ranch Estates building lines (Map Book Vol. 22, Pg. 68)",
            "side_rear_setback": "Per Ranch Estates 1946 Deed Restrictions (#330584)",
            "max_height": "Residential scale",
            "plan_review": "Ranch Estates / BOCA Architectural Control",
            "notes": "Platted and restricted in May 1946 by Richmond Lumber & Building Company (Map Book Vol. 22, Pg. 68; Deed Records Vol. 1446, Pgs. 350-351; historical 1946 racial clause is void under federal and Texas law).",
        },
        "citations": [
            {"label": "Clerk File #330584 (1946 PDF)", "url": "public/deeds/boulevard-oaks/Ranch-Estates-Restrictions.pdf"},
        ],
        "documents": [
            {
                "title": "Ranch Estates 1946 Recorded Deed Restrictions (#330584)",
                "doc_type": "official",
                "local_url": "public/deeds/boulevard-oaks/Ranch-Estates-Restrictions.pdf",
                "external_url": "https://www.boulevardoaks.org/DeedLibrary/RanchEst.pdf",
                "clerk_file": "Clerk File #330584 / Deed Vol. 1446, Pg. 350 (1946)",
            },
        ],
    },
    "plat_5655_sunset_place": {
        "subdivision_id": "plat_5655_sunset_place",
        "subdivision_name": "Sunset Place",
        "civic_association": "Boulevard Oaks Civic Association",
        "civic_association_url": "https://www.boulevardoaks.org/DeedLibrary/BOCADeedRestrictions.htm",
        "plat_year": 1927,
        "vol_page": "063-083",
        "plat_citation": "Deed Records Vol. 725, Pg. 370 · Replat Film #660-051 · HCAD Subdiv. Code 063-083",
        "deed_citation": "Harris County Clerk File #RP-2024-423389 (Nov. 13, 2024) · Original Restrictions Vol. 791, Pg. 205",
        "covenant_summary": {
            "allowed_use": "Single-family residential only",
            "min_lot_size": "1 single-family residence per platted lot",
            "front_setback": "Per Sunset Place recorded building lines (Deed Vol. 725, Pg. 370)",
            "side_rear_setback": "Per 2024 Sunset Place Restrictive Covenants (#RP-2024-423389)",
            "max_height": "Residential scale per 2024 Sunset Place Restrictions",
            "plan_review": "Sunset Place / BOCA Architectural Control",
            "notes": "Originally dedicated in Deed Records Vol. 725, Pg. 370 (c. 1927); restated November 13, 2024 under Clerk File #RP-2024-423389.",
        },
        "citations": [
            {"label": "Clerk File #RP-2024-423389 (PDF)", "url": "public/deeds/boulevard-oaks/Sunset-Place-2024-Restrictions.pdf"},
        ],
        "documents": [
            {
                "title": "Sunset Place 2024 Recorded Deed Restrictions (#RP-2024-423389)",
                "doc_type": "official",
                "local_url": "public/deeds/boulevard-oaks/Sunset-Place-2024-Restrictions.pdf",
                "external_url": "https://www.boulevardoaks.org/DeedLibrary/Sunset%20PlaceRestrictions_13Nov2024.pdf",
                "clerk_file": "Clerk File #RP-2024-423389 (Recorded 11/13/2024)",
            },
        ],
    },
    "plat_6646_wroxton_court": {
        "subdivision_id": "plat_6646_wroxton_court",
        "subdivision_name": "Wroxton Court",
        "civic_association": "Boulevard Oaks Civic Association",
        "civic_association_url": "https://www.boulevardoaks.org/DeedLibrary/BOCADeedRestrictions.htm",
        "plat_year": 1936,
        "vol_page": "066-103",
        "plat_citation": "Deed Records Vol. 998, Pg. 244 (Filing #119757) · HCAD Subdiv. Code 066-103",
        "deed_citation": "Harris County Clerk File #H123519 (Film Code 194-85-1860) · Original Plat Vol. 998, Pg. 244",
        "covenant_summary": {
            "allowed_use": "Residential (originally restricted primarily to two-family duplex residences and single-family dwellings)",
            "min_lot_size": "1 duplex or single-family residential building per platted lot",
            "front_setback": "Per Wroxton Court recorded plat building lines (Deed Vol. 998, Pg. 244)",
            "side_rear_setback": "Per Wroxton Court Deed Restrictions (#H123519)",
            "max_height": "Historic residential scale",
            "plan_review": "Wroxton Court / BOCA Architectural Control",
            "notes": "Platted in 1936 in Harris County Deed Records Vol. 998, Pg. 244 and renewed under Clerk File #H123519 as a historic duplex enclave.",
        },
        "citations": [
            {"label": "Clerk File #H123519 (PDF)", "url": "public/deeds/boulevard-oaks/Wroxton-Court-Restrictions.pdf"},
        ],
        "documents": [
            {
                "title": "Wroxton Court Renewed Deed Restrictions (#H123519)",
                "doc_type": "official",
                "local_url": "public/deeds/boulevard-oaks/Wroxton-Court-Restrictions.pdf",
                "external_url": "https://www.boulevardoaks.org/DeedLibrary/WroxtonCourt.pdf",
                "clerk_file": "Clerk File #H123519 / Film Code 194-85-1860",
            },
        ],
    },
    "plat_2283_greenbriar": {
        "subdivision_id": "plat_2283_greenbriar",
        "subdivision_name": "Greenbriar",
        "civic_association": "Boulevard Oaks Civic Association",
        "civic_association_url": "https://www.boulevardoaks.org/DeedLibrary/BOCADeedRestrictions.htm",
        "plat_year": 1936,
        "vol_page": "067-015",
        "plat_citation": "Deed Records Vol. 998, Pg. 411 · HCAD Subdiv. Code 067-015",
        "deed_citation": "Harris County Clerk File #RP-2018-530021 (Nov. 26, 2018) · Original Plat Vol. 998, Pg. 411",
        "covenant_summary": {
            "allowed_use": "Single-family residential only",
            "min_lot_size": "1 single-family residence per platted lot",
            "front_setback": "Per Greenbriar Addition recorded building lines (Deed Records Vol. 998, Pg. 411)",
            "side_rear_setback": "Per 2018 Recorded Greenbriar Addition Restrictions (#RP-2018-530021)",
            "max_height": "Residential scale",
            "plan_review": "Greenbriar Addition / BOCA Architectural Control",
            "notes": "Platted in 1936 (Deed Records Vol. 998, Pg. 411); restated and recorded November 26, 2018 under Clerk File #RP-2018-530021.",
        },
        "citations": [
            {"label": "Clerk File #RP-2018-530021 (PDF)", "url": "public/deeds/boulevard-oaks/Greenbriar-Addition-2018-Restrictions.pdf"},
        ],
        "documents": [
            {
                "title": "Greenbriar Addition 2018 Recorded Deed Restrictions (#RP-2018-530021)",
                "doc_type": "official",
                "local_url": "public/deeds/boulevard-oaks/Greenbriar-Addition-2018-Restrictions.pdf",
                "external_url": "https://www.boulevardoaks.org/DeedLibrary/GREENBRIAR%20ADDITION%20RECORDED%20DEED%20RESTRICTIONS%2011-26-2018%20TXT.pdf",
                "clerk_file": "Clerk File #RP-2018-530021 (Recorded 11/26/2018)",
            },
        ],
    },
    "plat_2517_herman_hospital_estate": {
        "subdivision_id": "plat_2517_herman_hospital_estate",
        "subdivision_name": "Herman Hospital Estate",
        "civic_association": "Boulevard Oaks Civic Association",
        "civic_association_url": "https://www.boulevardoaks.org/DeedLibrary/BOCADeedRestrictions.htm",
        "plat_year": 1938,
        "vol_page": "",
        "plat_citation": "14.94-Acre Tract at NW Corner of Bissonnet & Greenbriar Streets (Deed Records Pg. 477, Filing #164382)",
        "deed_citation": "Harris County Clerk File #164382 (June 1938 Warranty Deed & Covenants, Deed Records Pgs. 477-480)",
        "covenant_summary": {
            "allowed_use": "1938 deed permitted single-family, duplex, or up to four-family apartment dwellings on residential lots and retail/business uses along Bissonnet Street (initial 15-year covenant term expired 1953)",
            "min_lot_size": "Per subsequent replats of the 14.94-acre Bissonnet Corporation tract",
            "front_setback": "Per 1938 Hermann Hospital Estate deed covenants and subsequent section plats",
            "side_rear_setback": "Standard residential setbacks per subsequent section replats",
            "max_height": "Historic residential scale",
            "plan_review": "Check subsequent section replat covenants (outside COH Boulevard Oaks Historic District unless within boundary)",
            "notes": "Conveyed in June 1938 (Clerk File #164382) by Trustees of the Hermann Hospital Estate to Bissonnet Corporation at the northwest corner of Bissonnet and Greenbriar Streets.",
        },
        "citations": [
            {"label": "Clerk File #164382 (1938 PDF)", "url": "public/deeds/boulevard-oaks/Hermann-Hospital-Estates-Restrictions.pdf"},
        ],
        "documents": [
            {
                "title": "Hermann Hospital Estate 1938 Warranty Deed & Covenants (#164382)",
                "doc_type": "official",
                "local_url": "public/deeds/boulevard-oaks/Hermann-Hospital-Estates-Restrictions.pdf",
                "external_url": "https://www.boulevardoaks.org/DeedLibrary/HermannHEst-1.pdf",
                "clerk_file": "Clerk File #164382 / Deed Records Pgs. 477-480 (June 1938)",
            },
        ],
    },
    # Idylwood
    "plat_2762_idylwood": {
        "subdivision_id": "plat_2762_idylwood",
        "subdivision_name": "Idylwood",
        "civic_association": "Idylwood Civic Club",
        "civic_association_url": "https://www.idylwood.org/",
        "plat_year": 1928,
        "vol_page": "062-204",
        "plat_citation": "Map Book Vol. 9, Pg. 8 · HCAD Subdiv. Code 062-204",
        "deed_citation": "Harris County Clerk File #RP-2017-341800 (Recorded July 31, 2017) · Original Restrictions Deed Records Vol. 1081, Pg. 285",
        "covenant_summary": {
            "allowed_use": "Single-family residential on interior lots; neighborhood retail/commercial permitted on designated Community Store Lots (Lots 1-3, Block 1 at 6606 Lawndale St)",
            "min_lot_size": "1 single-family dwelling per platted lot across Idylwood Sections",
            "front_setback": "25 ft to 30 ft front building line per section plat",
            "side_rear_setback": "Residential side/rear setbacks per Idylwood Civic Club Covenants",
            "max_height": "2-story residential scale",
            "plan_review": "Idylwood Civic Club Architectural Control Committee",
            "notes": "Platted beginning in 1928 by developer John A. Embry with curvilinear landscape planning by Hare & Hare along Country Club Bayou, Villa de Matel, and Spurlock Park.",
        },
        "citations": [
            {"label": "Clerk File #RP-2017-341800 (PDF)", "url": "public/deeds/idylwood/Idylwood-Deed-Restrictions.pdf"},
            {"label": "Idylwood Civic Club", "url": "https://www.idylwood.org/"},
        ],
        "documents": [
            {
                "title": "Idylwood 2017 Amended & Restated Deed Restrictions (#RP-2017-341800)",
                "doc_type": "official",
                "local_url": "public/deeds/idylwood/Idylwood-Deed-Restrictions.pdf",
                "external_url": "https://www.idylwood.org/home/wp-content/uploads/2023/12/Idlywood-DeedRestrictions.pdf",
                "clerk_file": "Clerk File #RP-2017-341800 (Recorded 07/31/2017)",
            },
            {
                "title": "Idylwood Civic Club Bylaws",
                "doc_type": "searchable",
                "local_url": "public/deeds/idylwood/Idylwood-ByLaws.pdf",
                "external_url": "https://www.idylwood.org/home/wp-content/uploads/2023/12/Idlywood-ByLaws.pdf",
                "clerk_file": "Civic Club Bylaws",
            },
        ],
    },
    # Oak Forest (Sections 1-18)
    "plat_4019_oak_forest": {
        "subdivision_id": "plat_4019_oak_forest",
        "subdivision_name": "Oak Forest",
        "civic_association": "Oak Forest Homeowners Association (OFHA)",
        "civic_association_url": "https://www.ofha.org/Deed-Restriction-Documents",
        "plat_year": 1946,
        "vol_page": "073-099",
        "plat_citation": "Section 1 Plat Filing #365522 · Replat Film #683-612 · HCAD Subdiv. Code 073-099 (Sections 1-18)",
        "deed_citation": "Harris County Clerk Instruments #379388 (Sec. 1, Deed Vol. 1559, Pg. 387) through #1521330 (Sec. 17) & #1412655 (Sec. 18)",
        "covenant_summary": {
            "allowed_use": "Single-family residential across residential lots in Sections 1-18",
            "min_lot_size": "1 single-family residence per platted lot (typically 7,000 to 9,000+ sq ft)",
            "front_setback": "25 ft front building line (with section-specific corner lot side street setbacks)",
            "side_rear_setback": "Min 5 ft interior side setback; garage setback per section plat",
            "max_height": "2-story single-family residential height limit",
            "plan_review": "Oak Forest Homeowners Association (OFHA) Architectural / Deed Restriction Committee",
            "notes": "Developed beginning in August 1946 by Frank W. Sharp's Oak Forest Corporation (Sections 1-16), with Sections 17-18 platted in 1955-1956 by Cribbs/Busse/Draper and Oak Forest Center Corporation; all 18 recorded section instruments are archived below.",
        },
        "citations": [
            {"label": "Sec. 1 #379388 (PDF)", "url": "public/deeds/oak-forest/Oak-Forest-Sec-01-Restrictions.pdf"},
            {"label": "OFHA Deed Archive", "url": "https://www.ofha.org/Deed-Restriction-Documents"},
            {"label": "COH Super Neighborhood 13 (Oak Forest)", "url": "https://www.houstontx.gov/superneighborhoods/13.html"},
        ],
        "documents": [
            {
                "title": f"Oak Forest Section {sec:02d} Deed Restrictions (Searchable PDF)",
                "doc_type": "searchable",
                "local_url": f"public/deeds/oak-forest/Oak-Forest-Sec-{sec:02d}-Restrictions.pdf",
                "external_url": "https://www.ofha.org/Deed-Restriction-Documents",
                "clerk_file": OAK_FOREST_SECTION_FILINGS[sec],
            }
            for sec in range(1, 19)
        ],
    },
}


def main():
    print("1. Loading overlays.json, civic clubs, SMLS, SMBL, and conservation districts...")
    overlays_path = os.path.join(DATA_DIR, "overlays.json")
    with open(overlays_path, "r", encoding="utf-8") as f:
        overlays = json.load(f)

    # Load Civic Clubs
    civic_path = os.path.join(CACHE_DIR, "coh_civic_clubs.geojson")
    civic_geoms = []
    civic_names = []
    civic_ids = []
    if os.path.exists(civic_path):
        with open(civic_path, "r", encoding="utf-8") as f:
            civic_fc = json.load(f)
        for feat in civic_fc.get("features", []):
            g = feat.get("geometry")
            if not g:
                continue
            try:
                sg = shape(g)
                if not sg.is_valid:
                    sg = sg.buffer(0)
                if sg.is_empty:
                    continue
                p = feat.get("properties") or {}
                cname = clean_civic_name(p.get("CivicName") or p.get("NAME") or "")
                if not cname:
                    continue
                civic_geoms.append(sg)
                civic_names.append(cname)
                civic_ids.append(str(p.get("CivicID") or ""))
            except Exception:
                pass
    civic_tree = STRtree(civic_geoms) if civic_geoms else None

    # Load SMLS (Special Minimum Lot Size) & SMBL (Special Minimum Building Lines) & Conservation Districts
    smls_path = os.path.join(CACHE_DIR, "coh_smls.geojson")
    smbl_path = os.path.join(CACHE_DIR, "coh_smbl.geojson")
    cons_path = os.path.join(CACHE_DIR, "coh_conservation_districts.geojson")

    protection_features = []
    smls_geoms, smls_props = [], []
    smbl_geoms, smbl_props = [], []

    if os.path.exists(smls_path):
        with open(smls_path, "r", encoding="utf-8") as f:
            smls_fc = json.load(f)
        for idx, feat in enumerate(smls_fc.get("features", [])):
            g = feat.get("geometry")
            if not g:
                continue
            try:
                sg = shape(g)
                if not sg.is_valid:
                    sg = sg.buffer(0)
                if sg.is_empty:
                    continue
                p = feat.get("properties") or {}
                ord_no = str(p.get("ORDINANCE") or "").strip()
                lot_sqft = int(p.get("LOTSIZE") or 0)
                app_num = str(p.get("App_Num") or "").strip()
                no_lots = int(p.get("No_of_Lots") or 0)
                eff_date = ms_to_iso_date(p.get("Effective_Date") or p.get("STARTDATE"))
                exp_date = ms_to_iso_date(p.get("Expiration_Date"))
                status = str(p.get("Status") or "Approved").strip()
                rep = sg.representative_point()
                title = f"SMLS Ord. #{ord_no}" if ord_no else f"Special Minimum Lot Size #{app_num or idx+1}"
                if lot_sqft > 0:
                    title += f" ({lot_sqft:,} sq ft min)"
                clean_p = {
                    "id": f"smls_{ord_no or app_num or idx+1}_{idx}",
                    "name": title,
                    "protection_type": "smls",
                    "type_label": "Special Minimum Lot Size (COH Ch. 42)",
                    "ordinance": ord_no,
                    "min_lot_sqft": lot_sqft,
                    "min_bldg_line_ft": 0,
                    "no_of_lots": no_lots,
                    "app_num": app_num,
                    "effective_date": eff_date,
                    "expiration_date": exp_date,
                    "status": status,
                    "label_lng": round(rep.x, 5),
                    "label_lat": round(rep.y, 5),
                }
                smls_geoms.append(sg)
                smls_props.append(clean_p)
                protection_features.append({
                    "type": "Feature",
                    "geometry": {
                        "type": g["type"],
                        "coordinates": round_coords(g["coordinates"], 5),
                    },
                    "properties": clean_p,
                })
            except Exception:
                pass

    if os.path.exists(smbl_path):
        with open(smbl_path, "r", encoding="utf-8") as f:
            smbl_fc = json.load(f)
        for idx, feat in enumerate(smbl_fc.get("features", [])):
            g = feat.get("geometry")
            if not g:
                continue
            try:
                sg = shape(g)
                if not sg.is_valid:
                    sg = sg.buffer(0)
                if sg.is_empty:
                    continue
                p = feat.get("properties") or {}
                ord_no = str(p.get("ORDINANCE_") or p.get("ORDINANCE") or "").strip()
                bldg_ft = int(p.get("BLD__LINE") or 0)
                app_num = str(p.get("App_Num") or "").strip()
                no_lots = int(p.get("No_of_Lots") or 0)
                eff_date = ms_to_iso_date(p.get("START_DATE"))
                exp_date = ms_to_iso_date(p.get("Expiration_Date"))
                status = str(p.get("Status") or "Approved").strip()
                rep = sg.representative_point()
                title = f"SMBL Ord. #{ord_no}" if ord_no else f"Special Minimum Building Line #{app_num or idx+1}"
                if bldg_ft > 0:
                    title += f" ({bldg_ft} ft setback)"
                clean_p = {
                    "id": f"smbl_{ord_no or app_num or idx+1}_{idx}",
                    "name": title,
                    "protection_type": "smbl",
                    "type_label": "Special Minimum Building Line (COH Ch. 42)",
                    "ordinance": ord_no,
                    "min_lot_sqft": 0,
                    "min_bldg_line_ft": bldg_ft,
                    "no_of_lots": no_lots,
                    "app_num": app_num,
                    "effective_date": eff_date,
                    "expiration_date": exp_date,
                    "status": status,
                    "label_lng": round(rep.x, 5),
                    "label_lat": round(rep.y, 5),
                }
                smbl_geoms.append(sg)
                smbl_props.append(clean_p)
                protection_features.append({
                    "type": "Feature",
                    "geometry": {
                        "type": g["type"],
                        "coordinates": round_coords(g["coordinates"], 5),
                    },
                    "properties": clean_p,
                })
            except Exception:
                pass

    if os.path.exists(cons_path):
        with open(cons_path, "r", encoding="utf-8") as f:
            cons_fc = json.load(f)
        for idx, feat in enumerate(cons_fc.get("features", [])):
            g = feat.get("geometry")
            if not g:
                continue
            try:
                sg = shape(g)
                if not sg.is_valid:
                    sg = sg.buffer(0)
                if sg.is_empty:
                    continue
                p = feat.get("properties") or {}
                nm = str(p.get("NAME") or p.get("Name") or p.get("DISTRICT") or f"Conservation District #{idx+1}").strip().title()
                rep = sg.representative_point()
                clean_p = {
                    "id": f"cons_dist_{idx+1}",
                    "name": f"{nm} Conservation District",
                    "protection_type": "conservation",
                    "type_label": "COH Conservation District",
                    "ordinance": "COH Ch. 33 Conservation District",
                    "min_lot_sqft": 0,
                    "min_bldg_line_ft": 0,
                    "no_of_lots": 0,
                    "app_num": "",
                    "effective_date": "2023",
                    "expiration_date": "",
                    "status": "Active",
                    "label_lng": round(rep.x, 5),
                    "label_lat": round(rep.y, 5),
                }
                protection_features.append({
                    "type": "Feature",
                    "geometry": {
                        "type": g["type"],
                        "coordinates": round_coords(g["coordinates"], 5),
                    },
                    "properties": clean_p,
                })
            except Exception:
                pass

    smls_tree = STRtree(smls_geoms) if smls_geoms else None
    smbl_tree = STRtree(smbl_geoms) if smbl_geoms else None

    print(f"   Built land_use_protections with {len(protection_features)} features (SMLS: {len(smls_geoms)}, SMBL: {len(smbl_geoms)})")
    overlays["land_use_protections"] = {
        "type": "FeatureCollection",
        "features": protection_features,
    }

    # 2. Enrich all 5,573 platted subdivisions in overlays.json
    by_name_lookup = {}
    for sub_id, entry in CURATED_DEED_CATALOG.items():
        by_name_lookup[entry["subdivision_name"].upper()] = sub_id

    ps_features = overlays.get("platted_subdivisions", {}).get("features", [])
    doc_sub_count = 0
    smls_sub_count = 0
    civic_sub_count = 0

    for feat in ps_features:
        p = feat["properties"]
        sub_id = p.get("id", "")
        name_up = (p.get("name") or "").strip().upper()

        cat_entry = CURATED_DEED_CATALOG.get(sub_id)
        if not cat_entry and name_up in by_name_lookup:
            cat_entry = CURATED_DEED_CATALOG[by_name_lookup[name_up]]

        if cat_entry:
            if not p.get("vol_page") and cat_entry.get("vol_page"):
                p["vol_page"] = cat_entry["vol_page"]
            p["plat_year"] = cat_entry.get("plat_year") or p.get("earliest_year") or 0
            p["plat_citation"] = cat_entry.get("plat_citation") or format_plat_citation(p.get("recnum"), p.get("vol_page"))
            p["deed_citation"] = cat_entry.get("deed_citation") or format_deed_citation(p.get("deed_num"))
            p["civic_club"] = cat_entry.get("civic_association") or ""
            p["civic_club_url"] = cat_entry.get("civic_association_url") or ""
            p["has_deed_docs"] = True
            p["deed_doc_count"] = len(cat_entry.get("documents", []))
            p["catalog_id"] = cat_entry["subdivision_id"]
            doc_sub_count += 1
        else:
            if p.get("catalog_id") == "plat_2520_hermann_park":
                p["civic_club"] = ""
                p["civic_club_url"] = ""
                p.pop("plat_year", None)
            p.pop("catalog_id", None)
            p["plat_citation"] = format_plat_citation(p.get("recnum"), p.get("vol_page"))
            p["deed_citation"] = format_deed_citation(p.get("deed_num"))
            p["has_deed_docs"] = False
            p["deed_doc_count"] = 0

        # Spatial match against Civic Clubs, SMLS, and SMBL
        try:
            sg = shape(feat["geometry"])
            if not sg.is_valid:
                sg = sg.buffer(0)
            rep = sg.representative_point()

            if not p.get("civic_club") and civic_tree is not None:
                c_hits = civic_tree.query(rep)
                for ci in c_hits:
                    if civic_geoms[ci].contains(rep):
                        p["civic_club"] = civic_names[ci]
                        p["civic_club_id"] = civic_ids[ci]
                        civic_sub_count += 1
                        break

            if smls_tree is not None:
                s_hits = smls_tree.query(sg)
                ords = []
                min_sqfts = []
                for si in s_hits:
                    if smls_geoms[si].intersects(sg):
                        inter_area = smls_geoms[si].intersection(sg).area
                        if inter_area > 1e-9:
                            sp = smls_props[si]
                            if sp["ordinance"] and sp["ordinance"] not in ords:
                                ords.append(sp["ordinance"])
                            if sp["min_lot_sqft"] > 0:
                                min_sqfts.append(sp["min_lot_sqft"])
                if ords or min_sqfts:
                    p["smls_count"] = len(ords) or len(min_sqfts)
                    p["smls_ordinances"] = ", ".join(ords[:6])
                    p["smls_min_sqft"] = min(min_sqfts) if min_sqfts else 0
                    smls_sub_count += 1

            if smbl_tree is not None:
                b_hits = smbl_tree.query(sg)
                b_ords = []
                min_fts = []
                for bi in b_hits:
                    if smbl_geoms[bi].intersects(sg):
                        inter_area = smbl_geoms[bi].intersection(sg).area
                        if inter_area > 1e-9:
                            bp = smbl_props[bi]
                            if bp["ordinance"] and bp["ordinance"] not in b_ords:
                                b_ords.append(bp["ordinance"])
                            if bp["min_bldg_line_ft"] > 0:
                                min_fts.append(bp["min_bldg_line_ft"])
                if b_ords or min_fts:
                    p["smbl_count"] = len(b_ords) or len(min_fts)
                    p["smbl_ordinances"] = ", ".join(b_ords[:6])
                    p["smbl_min_ft"] = min(min_fts) if min_fts else 0
        except Exception:
            pass

    print(f"   Enriched {len(ps_features)} platted subdivisions: {doc_sub_count} with full PDFs, {civic_sub_count} matched to Civic Clubs, {smls_sub_count} with Ch. 42 SMLS ordinances.")

    # Save deed_restrictions_catalog.json
    catalog_out = {
        "generated_at": "2026-10-25",
        "total_subdivisions": len(CURATED_DEED_CATALOG),
        "total_mirrored_pdfs": sum(len(v["documents"]) for v in CURATED_DEED_CATALOG.values()),
        "subdivisions": CURATED_DEED_CATALOG,
        "by_name": by_name_lookup,
    }
    catalog_path = os.path.join(DATA_DIR, "deed_restrictions_catalog.json")
    with open(catalog_path, "w", encoding="utf-8") as f:
        json.dump(catalog_out, f, indent=2)
    print(f"2. Wrote {catalog_path} ({os.path.getsize(catalog_path) // 1024} KB)")

    # Save updated overlays.json
    with open(overlays_path, "w", encoding="utf-8") as f:
        json.dump(overlays, f, separators=(",", ":"))
    print(f"3. Updated {overlays_path} ({os.path.getsize(overlays_path) / (1024*1024):.2f} MB)")


if __name__ == "__main__":
    main()
