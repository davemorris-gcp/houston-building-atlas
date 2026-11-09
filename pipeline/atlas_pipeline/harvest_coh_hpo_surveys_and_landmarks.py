"""Harvest and cross-link all 23 City of Houston HPO Historic District parcel survey
inventories, design guidelines, designation reports, boundary maps, and post-2016
Landmark Designation Report PDFs into the Houston Building Atlas static bundle.
"""

from __future__ import annotations

import csv
import json
import re
import urllib.parse
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from atlas_pipeline.audit_historical_accuracy import is_architect_within_career_window
from atlas_pipeline.crawl_landmark_reports import normalize_lm_code, parse_report_metadata
from atlas_pipeline.schema import (
    ACTIVE_TAX_YEAR,
    CAMPUS_SPLIT_HCADS,
    EXCLUDED_AUTO_PROMOTION_HCADS,
    VALID_CONTRIBUTING_VALUES,
    infer_fallback_ownership_tier,
    is_replaceable_style_value,
    load_bld1_structural_flags,
    normalize_standing_hcad_token,
    read_json_or_gzip_shard,
    sanitize_atlas_text,
    sanitize_json_structure_dashes,
    sync_repo_tax_year_and_cache_bust,
    update_pmtiles_manifest_v3,
)

DEFAULT_GENERATED_AT_DATE = "2026-11-09"


def _resolve_generated_at_date(generated_at: str | None, cache_bust: str | None) -> str:
    """Resolve `YYYY-MM-DD` provenance date from explicit `generated_at`, `YYYYMMDD*` `cache_bust`, or default."""
    if generated_at:
        return str(generated_at).strip()
    if cache_bust:
        m = re.match(r"^(\d{4})(\d{2})(\d{2})", str(cache_bust).strip())
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return DEFAULT_GENERATED_AT_DATE

PLACEHOLDER_ARCHITECT_VALUES: frozenset[str] = frozenset(
    {
        "",
        "-",
        "n/a",
        "na",
        "none",
        "null",
        "tbd",
        "unknown",
        "undetermined",
        "not applicable",
        "architect unknown",
        "builder unknown",
    }
)


def _clean_architect_value(raw: Any) -> str:
    cleaned = clean_hpo_text(str(raw or ""))
    if cleaned.strip().lower() in PLACEHOLDER_ARCHITECT_VALUES:
        return ""
    return cleaned


def _architect_matches_year(architect: str, year_built: int) -> bool:
    if not architect or not year_built or year_built <= 0:
        return True
    return is_architect_within_career_window(architect, int(year_built))


NON_ARCHITECTURAL_SURVEY_STYLES: frozenset[str] = frozenset(
    {
        "",
        "n/a",
        "na",
        "-",
        "vacant",
        "lot",
        "parking lot",
        "new",
        "new construction",
        "building",
        "secondary structure",
        "commercial",
    }
)


def is_usable_hpo_survey_style(raw_st: str) -> bool:
    """Return True when `raw_st` names an architectural style (1:1 with `isUsableHpoSurveyStyle`)."""
    raw = str(raw_st or "").strip()
    low = raw.lower()
    if low in NON_ARCHITECTURAL_SURVEY_STYLES or len(low) < 4:
        return False
    if "  " in raw:
        return False
    if re.search(r"\b(?:year|circa)\s+bldg\b", low):
        return False
    if re.match(r"^(?:18|19|20)\d{2}\b", low):
        return False
    return not low.startswith(("new ", "vacant", "parking", "lot "))


def _is_post_survey_replacement(entity_year: int, survey_year: int, survey_status: str) -> bool:
    """Return True when `entity_year` indicates a post-1965 modern replacement structure."""
    if entity_year <= 1965:
        return False
    if survey_year > 0 and entity_year > survey_year + 25:
        return True
    return survey_year == 0 and survey_status in ("Contributing", "Potentially Contributing")


def _is_post_landmark_replacement(entity_year: int, landmark_year: int) -> bool:
    """Return True when `entity_year` is a post-1965 replacement >25 years newer than a dated landmark."""
    return bool(entity_year > 1965 and landmark_year > 0 and entity_year > landmark_year + 25)


def _is_sub_building_id(raw_id: Any) -> bool:
    """Return True when `raw_id` carries a bespoke `#<slug>` sub-building suffix (excluding `#fp_N` / `#aux_N`)."""
    cleaned = re.sub(r"#(?:fp|aux)_\d+$", "", str(raw_id or "").strip())
    return "#" in cleaned


def _write_sanitized_compact_json(path: Path, payload: Any) -> None:
    """Write single-line compact JSON with zero forbidden Unicode dashes."""
    clean = sanitize_json_structure_dashes(payload)
    path.write_text(
        sanitize_atlas_text(json.dumps(clean, ensure_ascii=False, separators=(",", ":"))),
        encoding="utf-8",
    )

DISTRICT_MANIFEST: dict[str, dict[str, str]] = {
    "Audubon Place Historic District": {
        "designation_date": "2009-04-08",
        "layout": "STANDARD",
        "inventory_key": "https://www.houstontx.gov/planning/HistoricPres/HistoricPreservationManual/docs_pdfs/Audubon_Inventory.pdf",
        "designation_report_key": "https://www.houstontx.gov/planning/HistoricPres/HistoricProperties_2009/II_Audubon_Place_Historic_District.pdf",
        "guidelines_key": "",
        "map_key": "https://www.houstontx.gov/planning/HistoricPres/HistoricPreservationManual/docs_pdfs/pj17025_Audubon_Place.pdf",
    },
    "Avondale East Historic District": {
        "designation_date": "1999-11-09",
        "layout": "STANDARD",
        "inventory_key": "https://www.houstontx.gov/planning/HistoricPres/HistoricPreservationManual/docs_pdfs/Avondale_East_Inventory.pdf",
        "designation_report_key": "",
        "guidelines_key": "",
        "map_key": "https://www.houstontx.gov/planning/HistoricPres/HistoricPreservationManual/docs_pdfs/pj17025_Avondale_East.pdf",
    },
    "Avondale West Historic District": {
        "designation_date": "2007-10-09",
        "layout": "STANDARD",
        "inventory_key": "https://www.houstontx.gov/planning/HistoricPres/HistoricPreservationManual/docs_pdfs/Avondale_West_Inventory.pdf",
        "designation_report_key": "",
        "guidelines_key": "",
        "map_key": "https://www.houstontx.gov/planning/HistoricPres/HistoricPreservationManual/docs_pdfs/pj17025_Avondale_West.pdf",
    },
    "Boulevard Oaks Historic District": {
        "designation_date": "2009-12-29",
        "layout": "STANDARD",
        "inventory_key": "https://www.houstontx.gov/planning/HistoricPres/HistoricPreservationManual/docs_pdfs/Boulevard_Oaks_Inventory.pdf",
        "designation_report_key": "",
        "guidelines_key": "",
        "map_key": "https://www.houstontx.gov/planning/HistoricPres/HistoricPreservationManual/docs_pdfs/pj17025_BoulevardOaks.pdf",
    },
    "Broadacres Historic District": {
        "designation_date": "2007-04-11",
        "layout": "STANDARD",
        "inventory_key": "https://www.houstontx.gov/planning/HistoricPres/HistoricPreservationManual/docs_pdfs/Broadacres_Inventory.pdf",
        "designation_report_key": "",
        "guidelines_key": "",
        "map_key": "https://www.houstontx.gov/planning/HistoricPres/HistoricPreservationManual/docs_pdfs/pj17025_Broadacres.pdf",
    },
    "Brunner-Harmonium Historic District": {
        "designation_date": "2021-08-25",
        "layout": "BRUNNER_SECTION",
        "inventory_key": "https://www.houstontx.gov/planning/HistoricPres/docs_pdfs/A_Brunner_Harmonium_Historic_District_Designation_2021_07_29.pdf",
        "designation_report_key": "https://www.houstontx.gov/planning/HistoricPres/docs_pdfs/A_Brunner_Harmonium_Historic_District_Designation_2021_07_29.pdf",
        "guidelines_key": "",
        "map_key": "",
    },
    "Courtlandt Place Historic District": {
        "designation_date": "1996-06-12",
        "layout": "STANDARD",
        "inventory_key": "https://www.houstontx.gov/planning/HistoricPres/HistoricPreservationManual/docs_pdfs/Courtlandt_Place_Inventory.pdf",
        "designation_report_key": "",
        "guidelines_key": "",
        "map_key": "https://www.houstontx.gov/planning/HistoricPres/HistoricPreservationManual/docs_pdfs/pj17025_Courtlandt_Place.pdf",
    },
    "First Montrose Commons Historic District": {
        "designation_date": "2010-06-09",
        "layout": "STANDARD",
        "inventory_key": "https://www.houstontx.gov/planning/HistoricPres/HistoricPreservationManual/docs_pdfs/First_Montrose_Commons_Inventory.pdf",
        "designation_report_key": "",
        "guidelines_key": "",
        "map_key": "https://www.houstontx.gov/planning/HistoricPres/HistoricPreservationManual/docs_pdfs/pj17025_First_Montrose_Commons.pdf",
    },
    "Freeland Historic District": {
        "designation_date": "2001-01-17",
        "layout": "STANDARD",
        "inventory_key": "https://www.houstontx.gov/planning/HistoricPres/HistoricPreservationManual/docs_pdfs/Freeland_Inventory.pdf",
        "designation_report_key": "",
        "guidelines_key": "",
        "map_key": "https://www.houstontx.gov/planning/HistoricPres/HistoricPreservationManual/docs_pdfs/pj17025_Freeland.pdf",
    },
    "Germantown Historic District": {
        "designation_date": "2012-12-05",
        "layout": "STANDARD",
        "inventory_key": "https://www.houstontx.gov/planning/HistoricPres/HistoricPreservationManual/docs_pdfs/Germantown_Inventory.pdf",
        "designation_report_key": "",
        "guidelines_key": "https://www.houstontx.gov/planning/HistoricPres/guidelines/Design-Guidelines-Germantown.pdf",
        "map_key": "",
    },
    "Glenbrook Valley Historic District": {
        "designation_date": "2011-06-29",
        "layout": "GLENBROOK_FULLWORD",
        "inventory_key": "https://www.houstontx.gov/planning/HistoricPres/HistoricPreservationManual/docs_pdfs/Glenbrook_Valley_Inventory.pdf",
        "designation_report_key": "https://www.houstontx.gov/planning/HistoricPres/HAHC_20101118/II_Glenbrook_Valley_Historic_District.pdf",
        "guidelines_key": "",
        "map_key": "",
    },
    "High First Ward Historic District": {
        "designation_date": "2014-05-28",
        "layout": "HIGH_FIRST_WARD_YEAR_LAST",
        "inventory_key": "https://www.houstontx.gov/planning/HistoricPres/HistoricPreservationManual/docs_pdfs/High_First_Ward_Inventory.pdf",
        "designation_report_key": "",
        "guidelines_key": "https://www.houstontx.gov/planning/HistoricPres/HistoricPreservationManual/docs_pdfs/High_First_Ward_Design_Guidelines_DRAFT.pdf",
        "map_key": "",
    },
    "Houston Heights East Historic District": {
        "designation_date": "2008-02-20",
        "layout": "STANDARD",
        "inventory_key": "https://www.houstontx.gov/planning/HistoricPres/HistoricPreservationManual/docs_pdfs/Houston_Heights_East_Inventory.pdf",
        "designation_report_key": "",
        "guidelines_key": "https://www.houstontx.gov/planning/HistoricPres/guidelines/Design-Guidelines-Heights.pdf",
        "map_key": "",
    },
    "Houston Heights South Historic District": {
        "designation_date": "2011-01-12",
        "layout": "STANDARD",
        "inventory_key": "https://www.houstontx.gov/planning/HistoricPres/HistoricPreservationManual/docs_pdfs/Houston_Heights_South_Inventory.pdf",
        "designation_report_key": "https://www.houstontx.gov/planning/HistoricPres/HistoricProperties_2010/pending/HHS_HD_report.pdf",
        "guidelines_key": "https://www.houstontx.gov/planning/HistoricPres/guidelines/Design-Guidelines-Heights.pdf",
        "map_key": "",
    },
    "Houston Heights West Historic District": {
        "designation_date": "2007-12-19",
        "layout": "STANDARD",
        "inventory_key": "https://www.houstontx.gov/planning/HistoricPres/HistoricPreservationManual/docs_pdfs/Houston_Heights_West_Inventory.pdf",
        "designation_report_key": "",
        "guidelines_key": "https://www.houstontx.gov/planning/HistoricPres/guidelines/Design-Guidelines-Heights.pdf",
        "map_key": "",
    },
    "Main Street/Market Square Historic District": {
        "designation_date": "1997-03-05",
        "layout": "STANDARD",
        "inventory_key": "https://www.houstontx.gov/planning/HistoricPres/HistoricPreservationManual/docs_pdfs/Main_Street_Market_Square_Inventory.pdf",
        "designation_report_key": "",
        "guidelines_key": "",
        "map_key": "https://www.houstontx.gov/planning/HistoricPres/HistoricPreservationManual/docs_pdfs/pj17025_Main_St_Market_Sq.pdf",
    },
    "Norhill Historic District": {
        "designation_date": "2000-06-14",
        "layout": "NORHILL_STATUS_LAST",
        "inventory_key": "https://www.houstontx.gov/planning/HistoricPres/HistoricPreservationManual/docs_pdfs/Norhill_Inventory.pdf",
        "designation_report_key": "",
        "guidelines_key": "https://www.houstontx.gov/planning/HistoricPres/norhill/Norhill-Design-Guidelines_Jan2026_CQ-FINAL_draft.pdf",
        "map_key": "https://www.houstontx.gov/planning/HistoricPres/HistoricPreservationManual/docs_pdfs/pj17025_Norhill.pdf",
    },
    "Old Sixth Ward Historic District": {
        "designation_date": "1998-05-27",
        "layout": "OLD_SIXTH_WARD_STATUS_ONLY",
        "inventory_key": "https://www.houstontx.gov/planning/HistoricPres/HistoricPreservationManual/docs_pdfs/Old_Sixth_Ward_Inventory.pdf",
        "designation_report_key": "",
        "guidelines_key": "https://www.houstontx.gov/planning/HistoricPres/guidelines/Design-Guidelines-Old-Sixth-Ward.pdf",
        "map_key": "",
    },
    "Shadow Lawn Historic District": {
        "designation_date": "2008-09-10",
        "layout": "SHADOW_LAWN",
        "inventory_key": "https://www.houstontx.gov/planning/HistoricPres/HistoricPreservationManual/docs_pdfs/Shadow_Lawn_Inventory.pdf",
        "designation_report_key": "",
        "guidelines_key": "",
        "map_key": "https://www.houstontx.gov/planning/HistoricPres/HistoricPreservationManual/docs_pdfs/pj17025_Shadow_Lawn.pdf",
    },
    "Starkweather Historic District": {
        "designation_date": "2014-02-26",
        "layout": "STANDARD",
        "inventory_key": "https://www.houstontx.gov/planning/HistoricPres/HistoricPreservationManual/docs_pdfs/Starkweather_Inventory.pdf",
        "designation_report_key": "",
        "guidelines_key": "https://www.houstontx.gov/planning/HistoricPres/guidelines/Design-Guidelines-Starkweather.pdf",
        "map_key": "",
    },
    "West Eleventh Place Historic District": {
        "designation_date": "1997-07-23",
        "layout": "WEST_11TH_INTERLEAVED",
        "inventory_key": "https://www.houstontx.gov/planning/HistoricPres/HistoricPreservationManual/docs_pdfs/West_11th_Place_Inventory.pdf",
        "designation_report_key": "",
        "guidelines_key": "",
        "map_key": "https://www.houstontx.gov/planning/HistoricPres/HistoricPreservationManual/docs_pdfs/pj17025_West_11th_Place.pdf",
    },
    "Westmoreland Historic District": {
        "designation_date": "1997-07-23",
        "layout": "STANDARD",
        "inventory_key": "https://www.houstontx.gov/planning/HistoricPres/HistoricPreservationManual/docs_pdfs/Westmoreland_Inventory.pdf",
        "designation_report_key": "",
        "guidelines_key": "",
        "map_key": "https://www.houstontx.gov/planning/HistoricPres/HistoricPreservationManual/docs_pdfs/pj17025_Westmoreland.pdf",
    },
    "Woodland Heights Historic District": {
        "designation_date": "2011-06-29",
        "layout": "STANDARD",
        "inventory_key": "https://www.houstontx.gov/planning/HistoricPres/HistoricPreservationManual/docs_pdfs/Woodland_Heights_Inventory.pdf",
        "designation_report_key": "https://www.houstontx.gov/planning/HistoricPres/HAHC_20101118/II_Woodland_Heights_Historic_District.pdf",
        "guidelines_key": "",
        "map_key": "https://www.houstontx.gov/planning/HistoricPres/HistoricPreservationManual/docs_pdfs/pj17480_Woodland_Heights.pdf",
    },
}

HPO_DISTRICT_NAME_ALIASES: dict[str, str] = {
    "Courtland Place": "Courtlandt Place Historic District",
    "Courtland Place Historic District": "Courtlandt Place Historic District",
    "Main Street Market Square": "Main Street/Market Square Historic District",
    "Main Street Market Square Historic District": "Main Street/Market Square Historic District",
    "Main St/Market Sq": "Main Street/Market Square Historic District",
    "Main St/Market Sq Historic District": "Main Street/Market Square Historic District",
    "Main St Market Sq": "Main Street/Market Square Historic District",
    "Main St Market Sq Historic District": "Main Street/Market Square Historic District",
    "W. 11th Place": "West Eleventh Place Historic District",
    "W. 11th Place Historic District": "West Eleventh Place Historic District",
    "W 11th Place": "West Eleventh Place Historic District",
    "W 11th Place Historic District": "West Eleventh Place Historic District",
    "W Eleventh Place": "West Eleventh Place Historic District",
    "W Eleventh Place Historic District": "West Eleventh Place Historic District",
    "West 11th Place": "West Eleventh Place Historic District",
    "West 11th Place Historic District": "West Eleventh Place Historic District",
    "Brunner Harmonium": "Brunner-Harmonium Historic District",
    "Brunner Harmonium Historic District": "Brunner-Harmonium Historic District",
    "OSW": "Old Sixth Ward Historic District",
    "Old Sixth Ward": "Old Sixth Ward Historic District",
    "Houston Heights Historic District East": "Houston Heights East Historic District",
}


def _canonicalize_district_slug(raw: str) -> str:
    s = str(raw or "").lower()
    s = re.sub(r"\bw\.?\s+(?:11th|eleventh)\b", "west eleventh", s)
    s = re.sub(r"\b11th\b", "eleventh", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


_DISTRICT_SLUG_TO_CANONICAL: dict[str, str] = {
    _canonicalize_district_slug(k): k for k in DISTRICT_MANIFEST
}


def canonicalize_hpo_district_name(raw_name: str | None) -> str:
    """Canonicalize COH GIS / overlay historic district spelling variants 1:1 with `resolveHpoDistrictDocs`."""
    raw = clean_hpo_text(str(raw_name or "")).strip()
    if not raw or raw.lower() == "none" or raw == "Outside Historic District":
        return raw
    aliased = HPO_DISTRICT_NAME_ALIASES.get(raw, raw)
    candidates = [aliased]
    if not re.search(r"Historic District$", aliased, re.I):
        with_hd = f"{aliased} Historic District"
        candidates.append(HPO_DISTRICT_NAME_ALIASES.get(with_hd, with_hd))
    for cand in candidates:
        if cand in DISTRICT_MANIFEST:
            return cand
    for cand in candidates:
        slug = _canonicalize_district_slug(cand)
        if slug in _DISTRICT_SLUG_TO_CANONICAL:
            return _DISTRICT_SLUG_TO_CANONICAL[slug]
    return aliased

STATUS_EXPAND: dict[str, str] = {
    "C": "Contributing",
    "PC": "Potentially Contributing",
    "NC": "Non-Contributing",
    "N": "Non-Contributing",
    "V": "Vacant",
    "CONTRIBUTING": "Contributing",
    "POTENTIALLY CONTRIBUTING": "Potentially Contributing",
    "NON-CONTRIBUTING": "Non-Contributing",
    "NONCONTRIBUTING": "Non-Contributing",
    "VACANT": "Vacant",
}

STATUS_SHORT_CODE: dict[str, str] = {
    "Contributing": "C",
    "Potentially Contributing": "PC",
    "Non-Contributing": "NC",
    "Vacant": "V",
}

_STRIP_STREET_TAIL: frozenset[str] = frozenset(
    {
        "HOUSTON",
        "TX",
        "TEXAS",
        "ST",
        "STREET",
        "AVE",
        "AVENUE",
        "BLVD",
        "BOULEVARD",
        "RD",
        "ROAD",
        "DR",
        "DRIVE",
        "LN",
        "LANE",
        "WAY",
        "PKWY",
        "PARKWAY",
        "FWY",
        "FREEWAY",
        "HWY",
        "HIGHWAY",
        "CT",
        "COURT",
        "PL",
        "PLACE",
        "CIR",
        "CIRCLE",
        "SQ",
        "SQUARE",
        "TER",
        "TERRACE",
        "TRL",
        "TRAIL",
        "LOOP",
        "N",
        "S",
        "E",
        "W",
        "NORTH",
        "SOUTH",
        "EAST",
        "WEST",
    }
)


STRIP_STREET_TAIL: frozenset[str] = _STRIP_STREET_TAIL
_CITY_STATE_TOKENS: frozenset[str] = frozenset({"HOUSTON", "TX", "TEXAS"})
_TRAILING_DIR_TOKENS: frozenset[str] = frozenset(
    {"N", "S", "E", "W", "NORTH", "SOUTH", "EAST", "WEST"}
)
_CARDINAL_DIRS: frozenset[str] = frozenset({"N", "S", "E", "W", "NE", "NW", "SE", "SW"})


def encode_clean_http_url(raw_url: str) -> str:
    """Percent-encode bare spaces while preserving valid query/archive delimiters."""
    s = str(raw_url or "").strip()
    if not s:
        return ""
    return s.replace(" ", "%20")


def clean_hpo_text(raw: str) -> str:
    """Normalize PDF Unicode hyphens/dashes/fractions/quotes into clean ASCII."""
    s = str(raw or "")
    s = s.replace("\f", " ")
    s = re.sub(r"[\u2010-\u2015\u2212\uFE58\uFE63\uFF0D]", "-", s)
    s = re.sub(r"(?<=\S)\u00bd(?=\S)", " 1/2 ", s)
    s = re.sub(r"(?<=\S)\u00bd", " 1/2", s)
    s = re.sub(r"\u00bd(?=\S)", "1/2 ", s)
    s = (
        s.replace("\u00bd", "1/2")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2018", "'")
        .replace("\u2019", "'")
    )
    return sanitize_atlas_text(s)


def normalize_hpo_lookup_address(raw_addr: str | None) -> str:
    """Canonical street lookup key matching `hpoSurveyHelpers.js:normalizeHpoLookupAddress`."""
    if not raw_addr:
        return ""
    s = clean_hpo_text(str(raw_addr)).upper()
    # Strip trailing apartment/suite/rear markers (preserving '#16' style unit numbers only after base)
    s = re.sub(r"\b(?:STE|SUITE|APT|UNIT|BLDG|FL|FLOOR|REAR)\b.*$", "", s)
    s = re.sub(r"#.*$", "", s)
    # Normalize ordinal words used in West Eleventh Place / Sixth Ward
    s = re.sub(r"\bELEVENTH\b", "11TH", s)
    # Split fused PDF fractions ('18361/2' -> '1836 1/2', '701 E 51/2 ST' -> '701 E 5 1/2 ST')
    s = re.sub(r"\b(\d{1,5})(?:\s*-+\s*)?1/2\b", r"\1 1/2", s)
    # Normalize 4-B -> 4B unit suffix before collapsing hyphenated street-number ranges
    s = re.sub(r"\b(\d+)-([A-D])\b", r"\1\2", s)
    s = re.sub(r"^\s*(\d{1,5})\s*-+\s*\d{1,5}\b(?!\s*/)", r"\1", s)
    s = re.sub(r"[^A-Z0-9/\s]", " ", s)
    parts = [p for p in s.split() if p]
    if not parts:
        return ""
    first_num = parts[0].split("/")[0]
    if not re.match(r"^\d+[A-Z]?$", first_num):
        return ""
    parts[0] = first_num
    while len(parts) > 3 and (
        parts[-1] in _CITY_STATE_TOKENS or (len(parts[-1]) == 5 and parts[-1].isdigit())
    ):
        parts.pop()
    # Merge '3410 1/2 BURLINGTON' or HCAD '209 E WOODLAND ST 1/2' so '1/2' stays attached to
    # street number unless '1/2' follows a numbered street like '111 31 1/2 ST' or '701 E 5 1/2'
    if len(parts) >= 3 and parts[1] == "1/2" and not re.match(r"^\d+(?:ST|ND|RD|TH)?$", parts[2]):
        parts = [f"{parts[0]} 1/2", *parts[2:]]
    elif (
        len(parts) >= 3
        and parts[-1] == "1/2"
        and not parts[0].endswith(" 1/2")
        and not re.match(r"^\d+(?:ST|ND|RD|TH)?$", parts[-2])
    ):
        parts = [f"{parts[0]} 1/2", *parts[1:-1]]
    dir_map = {"NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W"}
    captured_tail_dir = ""
    while len(parts) > 2 and (parts[-1].isdigit() or parts[-1] in _STRIP_STREET_TAIL):
        last = parts[-1]
        prev = parts[-2]
        # Protect Houston's single-letter street names ('7106 Avenue E', '6902 AVE N', '7310 Avenue S').
        if len(parts) == 3 and prev in ("AVE", "AVENUE") and len(last) == 1 and last.isalpha():
            break
        if (
            last in _TRAILING_DIR_TOKENS
            and prev in _STRIP_STREET_TAIL
            and prev not in _TRAILING_DIR_TOKENS
            and prev not in _CITY_STATE_TOKENS
        ):
            captured_tail_dir = dir_map.get(last, last)
        parts.pop()
    rev_dir_map = {"N": "NORTH", "S": "SOUTH", "E": "EAST", "W": "WEST"}
    if len(parts) >= 3 and parts[1] in dir_map:
        parts[1] = dir_map[parts[1]]
    elif len(parts) == 2 and parts[1] in rev_dir_map:
        parts[1] = rev_dir_map[parts[1]]
    if (
        captured_tail_dir
        and len(parts) >= 2
        and parts[1] not in _CARDINAL_DIRS
        and parts[1] not in dir_map
    ):
        parts = [parts[0], captured_tail_dir, *parts[1:]]
    word_alias = {"SAINT": "ST", "MOUNT": "MT", "AVENUE": "AVE"}
    # Normalize '31ST 1/2' -> '31 1/2' and non-trailing SAINT/MOUNT/AVENUE variants
    norm_tokens: list[str] = [parts[0]]
    for idx in range(1, len(parts)):
        tok = parts[idx]
        if idx + 1 < len(parts):
            if parts[idx + 1] == "1/2":
                m_ord = re.match(r"^(\d+)(?:ST|ND|RD|TH)$", tok)
                if m_ord:
                    tok = m_ord.group(1)
            elif tok in word_alias:
                tok = word_alias[tok]
        norm_tokens.append(tok)
    return " ".join(norm_tokens)


def normalize_hpo_street_address_keys(raw_addr: str | None) -> list[str]:
    """Generate primary and fallback normalized street keys (handling slash ranges & directionals)."""
    if not raw_addr:
        return []
    s = re.sub(
        r"\b(\d{1,5})(?:\s*-+\s*)?1/2\b",
        r"\1 1/2",
        clean_hpo_text(str(raw_addr)),
        flags=re.I,
    ).strip()
    keys: list[str] = []

    def _add(cand: str) -> None:
        k = normalize_hpo_lookup_address(cand)
        if k and k not in keys:
            keys.append(k)
        if k:
            pts = k.split(" ")
            if len(pts) >= 4 and pts[1] == "1/2" and pts[2] in _CARDINAL_DIRS:
                no_dir = f"{pts[0]} 1/2 {' '.join(pts[3:])}"
                if no_dir not in keys:
                    keys.append(no_dir)
            elif len(pts) >= 3 and pts[1] in _CARDINAL_DIRS:
                no_dir = f"{pts[0]} {' '.join(pts[2:])}"
                if no_dir not in keys:
                    keys.append(no_dir)

    _add(s)
    # Handle '802/804 Commerce', '1647-49 Cortlandt', '1414-1416 Austin St', or '3909--3917 S. Main St.'
    m_range = re.match(r"^\s*(\d{1,5})\s*[-/]+\s*(\d{1,5})\s+(.+)$", s)
    if m_range:
        n1, n2, rest = m_range.groups()
        if len(n2) < len(n1):
            n2 = n1[: len(n1) - len(n2)] + n2
        _add(f"{n1} {rest}")
        if int(n2) > int(n1):
            _add(f"{n2} {rest}")
    return keys


def _parse_year_token(raw_yr: str) -> tuple[str, int]:
    yr_s = clean_hpo_text(raw_yr or "").strip()
    if not yr_s or yr_s.lower() in ("-", "n/a", "na", "none", "unknown"):
        return "", 0
    m = re.search(r"(18[3-9]\d|19\d\d|20[0-2]\d)", yr_s)
    return yr_s, (int(m.group(1)) if m else 0)


_HEADER_SKIP_SUBSTRINGS = (
    "INVENTORY OF",
    "HISTORIC DISTRICT",
    "HOUSTON, TEXAS",
    "HOUSTON, TX",
    "DESIGNATION DATE",
    "BUILDING STATUS LEGEND",
    "CONTRIBUTING STRUCTURE",
    "POTENTIALLY CONTRIBUTING",
    "NONCONTRIBUTING STRUCTURE",
    "NON-CONTRIBUTING",
    "SUBDIVISION IS",
    "ALL LOTS",
    "WESTMORELAND ADDITION",
    "PROPERTY ADDRESS",
    "SITE ADDRESS",
    "CIRCA YEAR",
    "YEAR BUILT",
    "YR BLT",
    "YR BUIL",
    "STYLE OF",
    "ARCHITECTURAL",
    "CITY OF HOUSTON",
    "ARCHAEOLOGICAL",
    "EXHIBIT B",
    "LEGEND",
    "PAGE ",
    "AMENDED ",
)

_SECONDARY_RE = re.compile(
    r"^\s{20,}(?:(c\.?\s*\d{4}|\d{4})\s+(?:(C|PC|NC|N)\s+)?|(C|PC|NC|N)\s+)"
    r"((?:1(?:\s+1/2)?|2|one|two|three|four|4-plex)\s*(?:-\s*)?(?:story|car|plex|garage)\b.*|"
    r"Garage.*|Carriage.*|Original\s+carriage.*|Added\s+quarters.*|Historic\s+quarters.*|"
    r"Secondary.*|Rear.*|Quarters.*|Servants.*|Apartment.*|Back\s+quarters.*|"
    r"pair\s+of\s+hitching\s+posts.*)\s*$",
    re.I,
)
_SECONDARY_CONT_RE = re.compile(
    r"^\s{20,}(?:(c\.?\s*\d{4}|\d{4})\s+)?(C|PC|NC|N)\s{2,}(house|garage.*|quarters.*|stable.*|apartment.*)\s*$",
    re.I,
)
_NORHILL_SEC_RE = re.compile(
    r"^\s{20,}(c\.?\s*\d{4}|\d{4})\s+(.+?)\s{2,}(C|PC|NC)\s*$",
    re.I,
)


def _make_survey_row(
    *,
    address: str,
    alt_address: str = "",
    legal_desc: str = "",
    survey_year_raw: str = "",
    survey_year: int = 0,
    survey_status: str = "Non-Contributing",
    survey_style: str = "",
    hcad_num: str = "",
) -> dict[str, Any]:
    """Construct a normalized parcel survey row dictionary."""
    row_obj: dict[str, Any] = {
        "address": " ".join(address.split()),
        "alt_address": " ".join(alt_address.split()),
        "legal_desc": " ".join(legal_desc.split()),
        "survey_year_raw": survey_year_raw,
        "survey_year": int(survey_year or 0),
        "survey_status": survey_status,
        "survey_style": " ".join(survey_style.split()).strip('"'),
        "secondary": [],
    }
    if hcad_num:
        row_obj["hcad_num"] = hcad_num
    return row_obj


_SINGLE_LINE_LAYOUT_SPECS: dict[str, tuple[re.Pattern[str], tuple[str, ...]]] = {
    "GLENBROOK_FULLWORD": (
        re.compile(
            r"^\s*(\d{1,5}\s+[A-Z0-9\s./-]+?)\s{2,}(.+?)\s{2,}(\d{4}|n/a|-)\s{2,}(Contributing|Non-Contributing|Potentially Contributing|Vacant)\s*(.*?)\s*$",
            re.I,
        ),
        ("addr", "legal", "yr", "status", "style"),
    ),
    "NORHILL_STATUS_LAST": (
        re.compile(
            r"^\s*(\d{1,5}(?:\s*[-/]\s*\d{1,5})?\s+[A-Z0-9\s./-]+?)\s{2,}(.+?)\s+(c\.?\s*\d{4}|\d{4}|n/a|-)\s{2,}(.+?)\s{2,}(C|PC|NC|V)\s*$"
        ),
        ("addr", "legal", "yr", "style", "status"),
    ),
    "HIGH_FIRST_WARD_YEAR_LAST": (
        re.compile(
            r"^\s*(\d{1,5}(?:\s+1/2)?\s+[A-Za-z0-9\s./-]+?)\s{2,}(.+?)\s{2,}(C|PC|NC|V)\s{2,}(.+?)\s{2,}(c\.?\s*\d{4}|\d{4}|n/a|-)\s*$",
            re.I,
        ),
        ("addr", "legal", "status", "style", "yr"),
    ),
    "OLD_SIXTH_WARD_STATUS_ONLY": (
        re.compile(
            r"^\s*(\d{1,5}(?:\s*[-/]\s*\d{1,5})?(?:\s+1/2)?\s+[A-Za-z0-9\s./-]+?)\s{2,}(.+?)\s{2,}(C|PC|NC|V)\s*$"
        ),
        ("addr", "legal", "status"),
    ),
}


def _is_header_or_footer_line(line: str) -> bool:
    st = line.strip()
    if not st:
        return True
    up = st.upper()
    if up in (
        "INVENTORY",
        "V = VACANT",
        "C = CONTRIBUTING",
        "NC = NON-CONTRIBUTING",
        "ST",
        "BUILT",
        "STATUS",
        "ADDRESS",
        "CIRCA",
        "BLDG",
        "YEAR",
    ):
        return True
    if re.fullmatch(
        r"(?:CIRCA|YEAR|YR|BLDG|BLDGS|BUILDING|STATUS|BUILT|BLT|STYLE|OF|ARCHITECTURE|ARCHITECTURAL|\s|[./-])+",
        up,
    ):
        return True
    if re.match(r"^(?:C|PC|NC|V)\s*[-=]\s*", up):
        return True
    if re.match(r"^(?:OWNER|APPLICANT|PROPERTY\s+OWNER)S?\s*:", up):
        return True
    if re.match(r"^PAGE\s+\d+", up) or re.match(r"^\d+\s+OF\s+\d+$", up):
        return True
    if any(h in up for h in _HEADER_SKIP_SUBSTRINGS) or re.search(r"\bOWNER(?:\s+NAME)?\b", up):
        if not re.match(r"^(?:\d{13}|\d{1,5})\b", st):
            return True
    return False


@dataclass
class _StandardRowCarryState:
    """Mutable cross-line carry registers for `pdftotext -layout` multi-line HPO table rows."""

    style_prefix: str = ""
    year_prefix: str = ""
    split_num: str = ""
    alt_num: str = ""
    unclosed_addr: str = ""
    street_header: str = ""
    row_prefix: str = ""


_TAIL_HEADER_KEYWORDS: frozenset[str] = frozenset(
    {"YEAR", "CIRCA", "YR", "STATUS", "BLDG", "BUILDING", "STYLE", "ARCHITECTURAL", "BUILT"}
)
_PAGE_BREAK_SENTINEL = "\x0c"


def _infer_header_owner_columns(
    ln: str,
) -> tuple[int | None, list[tuple[str, int]] | None] | None:
    """Infer tabular `OWNER` column index and character-offset spans relative to `ADDRESS`."""
    up_raw = ln.upper()
    up_ln = up_raw.strip()
    if (
        re.match(r"^(?:\d{13}|\d{1,5})\b", up_ln)
        or "ADDRESS" not in up_ln
        or not re.search(r"\bOWNERS?\b", up_ln)
    ):
        return None
    raw_hdr_matches = [
        (m.group(0).strip(), m.start())
        for m in re.finditer(r"\S+(?:\s\S+)*", up_raw)
        if m.group(0).strip() and "HCAD" not in m.group(0) and "ACCOUNT" not in m.group(0)
    ]
    hdr_cols = [c for c, _ in raw_hdr_matches]
    addr_col_idx = next((i for i, c in enumerate(hdr_cols) if "ADDRESS" in c), None)
    own_col_idx = next(
        (i for i, c in enumerate(hdr_cols) if re.search(r"\bOWNERS?\b", c)), None
    )
    if addr_col_idx is None or own_col_idx is None or own_col_idx <= addr_col_idx:
        return None
    owner_col_after_addr = own_col_idx - addr_col_idx
    addr_raw_start = raw_hdr_matches[addr_col_idx][1]
    extracted_mid: list[tuple[str, int]] = []
    for c_txt, c_pos in raw_hdr_matches[addr_col_idx + 1 :]:
        first_word = c_txt.split()[0]
        if first_word in ("STREET", "ST") and c_txt in ("STREET", "ST", "STREET NAME"):
            continue
        if any(kw in c_txt.split() for kw in _TAIL_HEADER_KEYWORDS):
            break
        extracted_mid.append((c_txt, c_pos - addr_raw_start))
    return owner_col_after_addr, (extracted_mid or None)


def _strip_owner_pii_columns(
    rem_spans: list[tuple[str, int]],
    row_addr_pos: int,
    mid_hdr_cols: list[tuple[str, int]] | None,
    owner_col_after_addr: int | None,
) -> str:
    """Exclude tabular `OWNER` PII columns by nearest character offset relative to `ADDRESS`."""
    if mid_hdr_cols:
        rem_cols = [
            c_tok
            for c_tok, c_pos in rem_spans
            if not re.search(
                r"\bOWNERS?\b",
                min(mid_hdr_cols, key=lambda hc_col: abs((c_pos - row_addr_pos) - hc_col[1]))[0],
            )
        ]
    elif owner_col_after_addr is not None and 1 <= owner_col_after_addr <= len(rem_spans):
        rem_cols = [
            c_tok
            for idx_c, (c_tok, _) in enumerate(rem_spans, 1)
            if idx_c != owner_col_after_addr
        ]
    else:
        rem_cols = [c_tok for c_tok, _ in rem_spans]
    return " ".join(
        c
        for c in rem_cols
        if not re.match(r"^(?:OWNER|APPLICANT|PROPERTY\s+OWNER)S?\s*:", c, re.I)
    )


def _parse_standard_or_brunner_layout(
    district_name: str,
    text: str,
    lay: str,
) -> list[dict[str, Any]]:
    """Parse STANDARD and BRUNNER_SECTION HPO Historic District inventory table layouts."""
    raw_lines: list[str] = []
    for page_idx, page_chunk in enumerate(str(text or "").split("\f")):
        if page_idx > 0:
            raw_lines.append(_PAGE_BREAK_SENTINEL)
        for raw_ln in page_chunk.splitlines():
            raw_lines.append(clean_hpo_text(raw_ln).rstrip("\r\n"))

    if lay == "BRUNNER_SECTION":
        start_idx = next(
            (i for i, ln in enumerate(raw_lines) if ln.strip() == "Building Inventory"),
            0,
        )
        end_idx = next(
            (
                i
                for i, ln in enumerate(raw_lines[start_idx:], start_idx)
                if ln.strip() == "Bibliography"
            ),
            len(raw_lines),
        )
        raw_lines = raw_lines[start_idx:end_idx]

    main_st_market = "Main Street" in district_name
    rows: list[dict[str, Any]] = []
    carry = _StandardRowCarryState()
    owner_col_after_addr: int | None = None
    mid_hdr_cols: list[tuple[str, int]] | None = None
    tail_re = re.compile(
        r"^(.*?)\s{2,}(?:(c\.?\s*\d{4}(?:\s*[-/]\s*\d{2,4})?|\d{4}(?:\s*[-/]\s*\d{2,4})?|n/a|-)\s{2,})?(C|PC|NC|V|N)(?:\s{2,}(.+?))?\s*$"
    )

    for ln in raw_lines:
        if ln == _PAGE_BREAK_SENTINEL:
            carry = _StandardRowCarryState()
            continue
        inferred_hdr = _infer_header_owner_columns(ln)
        if inferred_hdr is not None:
            owner_col_after_addr, inferred_mid = inferred_hdr
            if inferred_mid:
                mid_hdr_cols = inferred_mid
        if _is_header_or_footer_line(ln):
            if ln.strip():
                carry = _StandardRowCarryState()
            continue

        st = ln.strip()
        leading_spaces = len(ln) - len(ln.lstrip(" "))

        if carry.unclosed_addr:
            ln = f"{carry.unclosed_addr} {st}"
            st = ln.strip()
            leading_spaces = 0
            carry.unclosed_addr = ""

        if carry.row_prefix:
            if leading_spaces >= 20 and re.match(
                r"^(?:c\.?\s*\d{4}|\d{4}|n/a|-)\s{2,}(?:C|PC|NC|V|N)\b",
                st,
                re.I,
            ):
                ln = f"{carry.row_prefix}   {st}"
                st = ln.strip()
                leading_spaces = 0
            carry.row_prefix = ""

        if leading_spaces < 15 and re.fullmatch(r"\d{3,5}", st):
            carry.split_num = st
            continue
        if leading_spaces < 15 and re.fullmatch(r"\d{1,5}\s*\([^)]*", st):
            carry.unclosed_addr = st
            continue
        if carry.split_num:
            if st.startswith("("):
                m_paren = re.match(r"^\(([^)]+)\)\s+(.+)$", st)
                if m_paren:
                    carry.alt_num = m_paren.group(1).strip()
                    ln = f"{carry.split_num} {m_paren.group(2)}"
                    st = ln.strip()
                    leading_spaces = 0
            carry.split_num = ""

        if 5 <= leading_spaces < 25 and re.fullmatch(r"[A-Za-z][A-Za-z0-9 .'-]{2,24}", st):
            carry.street_header = st
            continue

        m_sec_cont = _SECONDARY_CONT_RE.match(ln)
        if m_sec_cont and rows and carry.style_prefix:
            sec_yr_raw, sec_st, sec_tail = m_sec_cont.groups()
            combined_sec_type = " ".join(f"{carry.style_prefix} {sec_tail}".split()).strip(" .")
            carry.style_prefix = ""
            rows[-1]["secondary"].append(
                {
                    "status": STATUS_EXPAND.get(sec_st.upper(), sec_st),
                    "year": (sec_yr_raw or "").strip(),
                    "type": combined_sec_type,
                }
            )
            continue

        m_sec = _SECONDARY_RE.match(ln)
        if m_sec and rows:
            sec_yr_raw, sec_st1, sec_st2, sec_type = m_sec.groups()
            sec_st_tok = (sec_st1 or sec_st2 or "NC").upper()
            rows[-1]["secondary"].append(
                {
                    "status": STATUS_EXPAND.get(sec_st_tok, "Non-Contributing"),
                    "year": (sec_yr_raw or "").strip(),
                    "type": " ".join(sec_type.split()).strip(" ."),
                }
            )
            continue

        m_tail = tail_re.match(st) if leading_spaces < 20 else None
        if m_tail and re.match(r"^(?:\d{13}|\d{1,5})\b", st):
            left_part, yr_tok, st_tok, style_tok = m_tail.groups()
            raw_tok_spans = [
                (m.group(0).strip(), leading_spaces + m.start())
                for m in re.finditer(r"\S+(?:\s\S+)*", left_part)
                if m.group(0).strip()
            ]
            row_hcad = ""
            parts_2sp_spans: list[tuple[str, int]] = []
            for p_tok, p_pos in raw_tok_spans:
                clean_hc = re.sub(r"[\s-]", "", p_tok)
                if re.fullmatch(r"\d{13}", clean_hc) and not row_hcad:
                    row_hcad = clean_hc
                else:
                    parts_2sp_spans.append((p_tok, p_pos))
            parts_2sp = [t for t, _ in parts_2sp_spans]
            if (
                carry.street_header
                and len(parts_2sp) >= 2
                and parts_2sp[1].lower() == "(rear)"
            ):
                parts_2sp[1] = f"{carry.street_header} (rear)"
            carry.street_header = ""

            m_inline_alt = (
                re.match(r"^(\d{1,5}(?:\s+1/2)?)\s*\(([^)]+)\)$", parts_2sp[0])
                if parts_2sp
                else None
            )
            if m_inline_alt:
                parts_2sp[0] = m_inline_alt.group(1).strip()
                carry.alt_num = m_inline_alt.group(2).strip()

            row_addr_pos = parts_2sp_spans[0][1] if parts_2sp_spans else 0
            if (
                len(parts_2sp) >= 2
                and re.fullmatch(
                    r"\d{1,5}(?:\s*[-/,]\s*\d{1,5}|\s+1/2|\s*-?\s*[A-D](?:\s*&\s*[A-D])?)?",
                    parts_2sp[0],
                    re.I,
                )
                and not re.match(r"^(?:Lts?|Trs?|Blk)\b|^\d+$", parts_2sp[1], re.I)
            ):
                addr = f"{parts_2sp[0]} {parts_2sp[1]}"
                rem_spans = parts_2sp_spans[2:]
            elif parts_2sp and re.match(r"^\d{1,5}\b", parts_2sp[0]):
                addr = parts_2sp[0]
                rem_spans = parts_2sp_spans[1:]
            else:
                carry.alt_num = ""
                continue

            legal = _strip_owner_pii_columns(
                rem_spans, row_addr_pos, mid_hdr_cols, owner_col_after_addr
            )

            addr_clean = " ".join(addr.split())
            alt_addr_clean = ""
            if carry.alt_num:
                m_house_prefix = re.match(
                    r"^(\d{1,5})(?:\s*[-/,]\s*\d{1,5}|\s+1/2|\s*-\s*[A-D]\b(?:\s*&\s*[A-D]\b)?|[A-D]\b)?\s+(.+)$",
                    addr_clean,
                    flags=re.I,
                )
                if m_house_prefix:
                    base_house_num = m_house_prefix.group(1)
                    street_part = m_house_prefix.group(2).strip()
                    if re.fullmatch(r"[A-D](?:\s*&\s*[A-D])?", carry.alt_num, re.I):
                        alt_addr_clean = f"{base_house_num}-{carry.alt_num} {street_part}"
                    else:
                        alt_addr_clean = f"{carry.alt_num} {street_part}"
                carry.alt_num = ""

            eff_yr_tok = yr_tok or ""
            if carry.year_prefix:
                eff_yr_tok = (
                    f"{carry.year_prefix}{eff_yr_tok}"
                    if eff_yr_tok
                    else carry.year_prefix.rstrip("/-")
                )
                carry.year_prefix = ""
            yr_raw, yr_int = _parse_year_token(eff_yr_tok)

            style_clean = " ".join((style_tok or "").split()).strip()
            if style_clean.lower() == "n/a":
                style_clean = ""
            if carry.style_prefix:
                style_clean = f"{carry.style_prefix} {style_clean}".strip()
                carry.style_prefix = ""

            canon_status = STATUS_EXPAND.get(st_tok.upper(), "Non-Contributing")
            if main_st_market and st_tok.upper() == "N" and "vacant" in style_clean.lower():
                canon_status = "Vacant"

            rows.append(
                _make_survey_row(
                    address=addr_clean,
                    alt_address=alt_addr_clean,
                    legal_desc=legal,
                    survey_year_raw=yr_raw,
                    survey_year=yr_int,
                    survey_status=canon_status,
                    survey_style=style_clean,
                    hcad_num=row_hcad,
                )
            )
            continue

        carry.street_header = ""
        if leading_spaces < 15 and re.match(r"^\d{1,5}\s+\S+", st):
            carry.row_prefix = st
            continue

        if leading_spaces >= 45:
            m_yr_st = re.match(r"^((?:c\.?\s*)?\d{4}\s*[-/])\s{2,}(.+)$", st, re.I)
            if m_yr_st and "  " not in m_yr_st.group(2):
                carry.year_prefix = m_yr_st.group(1).strip()
                carry.style_prefix = m_yr_st.group(2).strip()
                continue

        if (
            leading_spaces >= 58
            and len(st) <= 45
            and "  " not in st
            and not st.isdigit()
            and not re.match(r"^(?:C|PC|NC|V|N/A|\d{4}|c\.?\s*\d{4})\b", st, re.I)
            and not re.search(r"\b(BLK|LT|LTS|TR|TRS)\b", st, re.I)
        ):
            # In COH `pdftotext -layout` tables, right-column style wraps appear ABOVE the next
            # row when the prior row already has a complete style (e.g. Audubon Place 'Contemporary
            # Apartment' above 'Building'), EXCEPT when the prior row ended on an incomplete stem
            # ('Pyramid Roof' + 'Cottage' in Brunner, or column-clipped 'Apartme' / 'Bungal').
            if st.lower().endswith(
                ("w/", "with", "federal", "queen anne with", "revival with", "craftsman", "stable")
            ) or (
                rows
                and rows[-1]["survey_style"]
                and not rows[-1]["survey_style"].endswith(("Apartme", "Roof", "Bungal"))
            ):
                carry.style_prefix = f"{carry.style_prefix} {st}".strip()
            elif rows:
                rows[-1]["survey_style"] = f"{rows[-1]['survey_style']} {st}".strip()
        else:
            carry.style_prefix = ""
    return rows


def _parse_single_line_layout(text: str, lay: str) -> list[dict[str, Any]]:
    """Parse single-line regex HPO inventory layouts (GLENBROOK, NORHILL, HIGH_FIRST_WARD, OLD_SIXTH_WARD)."""
    rx, group_names = _SINGLE_LINE_LAYOUT_SPECS[lay]
    rows: list[dict[str, Any]] = []
    for raw in text.splitlines():
        ln = clean_hpo_text(raw).rstrip("\r\n")
        if _is_header_or_footer_line(ln):
            continue
        if lay == "NORHILL_STATUS_LAST":
            m_sec = _NORHILL_SEC_RE.match(ln)
            if m_sec and rows:
                s_yr, s_tp, s_st = m_sec.groups()
                rows[-1]["secondary"].append(
                    {
                        "status": STATUS_EXPAND[s_st.upper()],
                        "year": s_yr.strip(),
                        "type": " ".join(s_tp.split()),
                    }
                )
                continue
        m = rx.match(ln)
        if not m:
            continue
        captured = dict(zip(group_names, m.groups(), strict=True))
        yr_raw, yr_int = _parse_year_token(captured.get("yr", ""))
        style_clean = " ".join((captured.get("style") or "").split())
        if lay == "HIGH_FIRST_WARD_YEAR_LAST" and style_clean.lower() in ("vacant", "n/a"):
            style_clean = ""
        rows.append(
            _make_survey_row(
                address=captured["addr"],
                legal_desc=captured["legal"],
                survey_year_raw=yr_raw,
                survey_year=yr_int,
                survey_status=STATUS_EXPAND[captured["status"].upper()],
                survey_style=style_clean,
            )
        )
    return rows


def _parse_shadow_lawn_layout(text: str) -> list[dict[str, Any]]:
    """Parse Shadow Lawn Historic District multi-line aka/vacant layout."""
    rows: list[dict[str, Any]] = []
    pending_addr = ""
    pending_style_prefix = ""
    for raw in text.splitlines():
        ln = clean_hpo_text(raw).rstrip("\r\n")
        if _is_header_or_footer_line(ln):
            continue
        st = ln.strip()
        m_only = re.match(r"^(\d+\s+Shadow\s+Lawn)(?:\s{10,}(.+))?$", st, re.I)
        if m_only and not re.search(r"\b(19\d\d|n/a)\b", st, re.I):
            pending_addr = " ".join(m_only.group(1).split())
            if m_only.group(2) and "  " not in m_only.group(2).strip():
                pending_style_prefix = m_only.group(2).strip()
            continue
        if st.lower().startswith("aka ") and pending_addr:
            m_aka = re.match(
                r"^aka\s+(.+?)\s{2,}(.+?)\s{2,}(\d{4}(?:-\d{2,4})?)\s{2,}(C|PC|NC|V)\s{2,}(.+)$",
                st,
                re.I,
            )
            if m_aka:
                alt_a, legal, yr_tok, st_tok, style_tok = m_aka.groups()
                yr_raw, yr_int = _parse_year_token(yr_tok)
                full_style = (
                    f"{pending_style_prefix} {style_tok}".strip()
                    if pending_style_prefix
                    else style_tok.strip()
                )
                rows.append(
                    _make_survey_row(
                        address=pending_addr,
                        alt_address=alt_a,
                        legal_desc=legal,
                        survey_year_raw=yr_raw,
                        survey_year=yr_int,
                        survey_status=STATUS_EXPAND[st_tok.upper()],
                        survey_style=full_style,
                    )
                )
                pending_addr = ""
                pending_style_prefix = ""
                continue
        m_vac = re.match(r"^(\d+\s+Remington\s+Lane)\s{2,}(.+?)\s{2,}n/a\s*$", st, re.I)
        if m_vac:
            rows.append(
                _make_survey_row(
                    address=m_vac.group(1),
                    legal_desc=m_vac.group(2),
                    survey_status="Vacant",
                )
            )
            continue
        m_row = re.match(
            r"^(\d+\s+Shadow\s+Lawn)\s{2,}(.+?)\s{2,}(\d{4}(?:-\d{2,4})?)\s{2,}(C|PC|NC|V)\s{2,}(.+)$",
            st,
            re.I,
        )
        if m_row:
            addr, legal, yr_tok, st_tok, style_tok = m_row.groups()
            yr_raw, yr_int = _parse_year_token(yr_tok)
            full_style = (
                f"{pending_style_prefix} {style_tok}".strip()
                if pending_style_prefix
                else style_tok.strip()
            )
            pending_style_prefix = ""
            rows.append(
                _make_survey_row(
                    address=addr,
                    legal_desc=legal,
                    survey_year_raw=yr_raw,
                    survey_year=yr_int,
                    survey_status=STATUS_EXPAND[st_tok.upper()],
                    survey_style=full_style,
                )
            )
        elif (
            len(ln) - len(ln.lstrip(" ")) >= 50
            and "  " not in st
            and not re.match(r"^(?:C|PC|NC|V|N/A|\d{4}|c\.?\s*\d{4})\b", st, re.I)
        ):
            pending_style_prefix = st
    return rows


def _parse_west_11th_layout(text: str) -> list[dict[str, Any]]:
    """Parse West Eleventh Place Historic District interleaved metadata layout."""
    raw_lines = [
        clean_hpo_text(ln).rstrip("\r\n")
        for ln in text.splitlines()
        if not _is_header_or_footer_line(clean_hpo_text(ln))
    ]
    rows: list[dict[str, Any]] = []
    pending_meta: tuple[str, str, int, str, str] | None = None
    meta_re = re.compile(r"^(.*?)\s{2,}(\d{4})\s{2,}(C|PC|NC|N|V)\s{2,}(.+?)\s*$")
    garage_re = re.compile(r"^(?:(.+?)\s{2,})?(C|PC|NC|N)\s{2,}(Garage)\s*$", re.I)
    addr_only_re = re.compile(
        r"^(\d+(?:-[A-Z])?\s+West\s+11th\s+Place)(?:\s{2,}(.+))?$", re.I
    )

    for ln in raw_lines:
        st = ln.strip()
        if re.match(
            r"^(West 11th Place Lane|Cul-de-sac Plant Area|East Gate Pier|West Gate Pier)\b",
            st,
            re.I,
        ):
            continue
        m_inline = re.match(
            r"^(\d+(?:-[A-Z])?\s+West\s+11th\s+Place)\s{2,}(.+?)\s{2,}(\d{4})\s{2,}(C|PC|NC|N|V)\s{2,}(.+)$",
            st,
            re.I,
        )
        if m_inline:
            addr, legal, yr_s, st_tok, style_s = m_inline.groups()
            rows.append(
                _make_survey_row(
                    address=addr,
                    legal_desc=legal,
                    survey_year_raw=yr_s,
                    survey_year=int(yr_s),
                    survey_status=STATUS_EXPAND[st_tok.upper()],
                    survey_style=style_s,
                )
            )
            pending_meta = None
            continue
        m_addr = addr_only_re.match(st)
        if m_addr:
            addr = " ".join(m_addr.group(1).split())
            extra_legal = (m_addr.group(2) or "").strip()
            if pending_meta:
                p_leg, yr_s, yr_i, st_c, style_s = pending_meta
                full_leg = " ".join(f"{p_leg} {extra_legal}".split())
                rows.append(
                    _make_survey_row(
                        address=addr,
                        legal_desc=full_leg,
                        survey_year_raw=yr_s,
                        survey_year=yr_i,
                        survey_status=st_c,
                        survey_style=style_s,
                    )
                )
                pending_meta = None
            else:
                rows.append(
                    _make_survey_row(
                        address=addr,
                        legal_desc=extra_legal,
                        survey_status="Contributing",
                    )
                )
            continue
        m_gar = garage_re.match(st)
        if m_gar and rows and not re.search(r"\d{4}", st):
            extra_l, g_st, g_tp = m_gar.groups()
            if extra_l and extra_l.strip():
                rows[-1]["legal_desc"] = " ".join(
                    f"{rows[-1]['legal_desc']} {extra_l.strip()}".split()
                )
            rows[-1]["secondary"].append(
                {
                    "status": STATUS_EXPAND[g_st.upper()],
                    "year": "",
                    "type": g_tp.strip(),
                }
            )
            continue
        m_meta = meta_re.match(ln if ln.startswith(" ") else f"   {st}")
        if m_meta:
            leg_part, yr_s, st_tok, style_s = m_meta.groups()
            if rows and rows[-1]["survey_year"] == 0:
                rows[-1]["legal_desc"] = " ".join(
                    f"{rows[-1]['legal_desc']} {leg_part.strip()}".split()
                )
                rows[-1]["survey_year_raw"] = yr_s
                rows[-1]["survey_year"] = int(yr_s)
                rows[-1]["survey_status"] = STATUS_EXPAND[st_tok.upper()]
                rows[-1]["survey_style"] = style_s.strip()
            else:
                pending_meta = (
                    leg_part.strip(),
                    yr_s,
                    int(yr_s),
                    STATUS_EXPAND[st_tok.upper()],
                    style_s.strip(),
                )
        elif rows and len(st) < 40 and any(
            k in st.lower() for k in ("turner", "place", "blk", "lt", "tr")
        ):
            rows[-1]["legal_desc"] = " ".join(f"{rows[-1]['legal_desc']} {st}".split())
    return rows


def parse_district_inventory_text(
    district_name: str,
    text: str,
    layout: str,
) -> list[dict[str, Any]]:
    """Parse a COH HPO Historic District Inventory PDF text into structured parcel survey rows."""
    lay = layout.strip().upper()
    if lay in ("STANDARD", "BRUNNER_SECTION"):
        return _parse_standard_or_brunner_layout(district_name, text, lay)
    if lay in _SINGLE_LINE_LAYOUT_SPECS:
        return _parse_single_line_layout(text, lay)
    if lay == "SHADOW_LAWN":
        return _parse_shadow_lawn_layout(text)
    if lay == "WEST_11TH_INTERLEAVED":
        return _parse_west_11th_layout(text)
    raise ValueError(f"Unsupported HPO inventory layout class: {layout!r}")


_PERSONAL_HEADER_SCRUB_RE = re.compile(
    r"\b(?:PROPERTY\s+OWNERS?|CURRENT\s+OWNERS?|OWNERS?|APPLICANTS?)\s*:\s*[^\n;]*?"
    r"(?=(?:\n|;|"
    r"\b(?:PROPERTY\s+OWNERS?|CURRENT\s+OWNERS?|OWNERS?|APPLICANTS?|"
    r"LOCATION|ADDRESS|AGENDA\s+ITEM|HPO\s+FILE(?:\s+NO\.?)?|FILE\s+NO\.?|"
    r"DATE(?:\s+ACCEPTED|\s+RECEIVED)?|HAHC\s+HEARING(?:\s+DATE)?|"
    r"30-DAY|90-DAY|SITE\s+INFORMATION|SIGNIFICANCE|SUMMARY|ARCHITECT|BUILDER|STYLE)\b"
    r"|(?<!\b[A-Z])(?<!\b(?:Mr|Ms|Dr|St|Jr|Sr|Co|vs))(?<!\b(?:Mrs|Rev|Inc|Ltd|Hon))\.\s+|$))\.?",
    re.I,
)


def _scrub_personal_header_names(summary: str) -> str:
    """Strip any singular/plural OWNER(S):/APPLICANT(S): header fragments and normalize dashes."""
    raw_s = str(summary or "").replace("\r\n", "\n").replace("\r", "\n")
    for _ in range(5):
        nxt = _PERSONAL_HEADER_SCRUB_RE.sub(" ", raw_s)
        if nxt == raw_s:
            break
        raw_s = nxt
    s = clean_hpo_text(raw_s)
    s = re.sub(r"^\s*[.;,-]+\s*", "", s)
    return " ".join(s.split()).strip()


def _is_designation_report_pdf_header(raw_text: str) -> bool:
    """Strictly verify that a candidate PDF is an official Landmark Designation Report (never a COA)."""
    head = clean_hpo_text(raw_text or "")[:1100].upper()
    if not head:
        return False
    if "CERTIFICATE OF APPROPRIATENESS" in head or "COA APPLICATION" in head:
        return False
    if re.search(r"(?:PROTECTED\s+)?LANDMARK\s+DESIGNATION\s+REPORT", head):
        return True
    if "P R O T E C T E D L A N D M A R K" in head or "L A N D M A R K D E S I G N A T I O N" in head:
        return True
    if "LANDMARK NAME:" in head and ("LOCATION:" in head or "HPO FILE" in head):
        return True
    return False


def _score_candidate_landmark_pdf(
    orig_url: str,
    info: dict[str, Any],
    meta: dict[str, Any],
    *,
    lm_codes: set[str],
    st_num: str,
    st_name: str,
    lm_name: str,
) -> int:
    raw_text = str(info.get("text") or "")
    if not _is_designation_report_pdf_header(raw_text):
        return 0

    fn = urllib.parse.unquote(orig_url.split("/")[-1]).lower()
    head_norm = clean_hpo_text(raw_text[:1100]).lower()
    hdr_loc = clean_hpo_text(meta.get("header_location") or "").lower()
    hdr_name = clean_hpo_text(meta.get("header_site_name") or "").lower()
    hdr_file = clean_hpo_text(meta.get("header_hpo_file") or "").lower()

    # Special verified spelling normalization for COH GIS typo 'Hildalgo Park Quiosco'
    if (
        "hildalgo" in lm_name.lower() or "hidalgo" in lm_name.lower()
    ) and "quiosco" in lm_name.lower():
        if "hidalgo" in (hdr_name + " " + fn) and "quiosco" in (hdr_name + " " + fn):
            return 140

    raw_st_toks = [
        t.lower()
        for t in re.split(r"[\s./-]+", clean_hpo_text(st_name))
        if len(t) >= 3
    ]
    suffix_words = {
        "street",
        "avenue",
        "boulevard",
        "drive",
        "lane",
        "road",
        "court",
        "place",
        "way",
        "north",
        "south",
        "east",
        "west",
    }
    st_toks = [t for t in raw_st_toks if t not in suffix_words]
    if not st_toks and raw_st_toks:
        st_toks = [raw_st_toks[0]]

    common_words = {
        "house",
        "building",
        "historic",
        "street",
        "avenue",
        "boulevard",
        "drive",
        "church",
        "park",
        "the",
        "and",
        "mrs",
        "residence",
        "home",
        "company",
        "complex",
        "houston",
        "texas",
        "john",
        "william",
        "james",
        "charles",
        "george",
        "joseph",
        "thomas",
        "henry",
        "robert",
        "edward",
        "david",
        "first",
        "second",
        "third",
        "saint",
        "missionary",
        "baptist",
        "united",
        "methodist",
    }
    name_toks = [
        w.lower()
        for w in re.split(r"[^A-Za-z0-9]+", clean_hpo_text(lm_name))
        if len(w) >= 5 and w.lower() not in common_words
    ]

    loc_hay = hdr_loc if hdr_loc else head_norm[:650]
    name_hay = hdr_name if hdr_name else head_norm[:650]

    st_num_base = st_num.split("-")[0].strip() if st_num else ""
    has_addr_hdr = bool(
        st_num_base
        and st_toks
        and any(
            re.search(
                rf"(^|[^0-9]){re.escape(st_num_base)}(?:\s*-\s*\d+)?\s+(?:[nsew]\.?\s+|south\s+|north\s+|east\s+|west\s+)?{re.escape(t)}\b",
                loc_hay,
            )
            for t in st_toks
        )
    )
    has_st_num_fn = bool(
        st_num_base and re.search(rf"(^|[^0-9]){re.escape(st_num_base)}([^0-9]|$)", fn)
    )
    has_st_tok_fn = bool(st_toks and any(t in fn for t in st_toks))
    has_name_hdr = bool(name_toks and any(w in name_hay for w in name_toks))
    has_code_hdr = bool(
        lm_codes and any(c.lower() == hdr_file or c.lower() in fn for c in lm_codes)
    )

    # Guard against wrong street number in LOCATION header (e.g. 308 Main vs 310 Main)
    if st_num_base and hdr_loc:
        loc_nums = re.findall(r"\b(\d{2,5})\b", hdr_loc)
        allowed_nums = set(re.findall(r"\d+", st_num))
        if loc_nums and not any(n in allowed_nums for n in loc_nums[:2]) and not has_addr_hdr:
            return -100

    if has_addr_hdr:
        return 120 + (20 if has_name_hdr else 0) + (15 if has_code_hdr else 0)
    if has_st_num_fn and has_st_tok_fn and (has_name_hdr or not hdr_loc):
        return 105
    if has_name_hdr and st_toks and any(t in loc_hay for t in st_toks):
        return 95
    return 0


_FALLBACK_PDF_ARCH_RE = re.compile(
    r"(?:designed\s+by\s+|architect(?:s)?\s+(?:was|were|is)\s+)"
    r"(?:the\s+(?:firm|architectural\s+firm)\s+of\s+|noted\s+(?:Houston\s+)?architect\s+|"
    r"prominent\s+(?:Houston\s+)?architect\s+|local\s+architect\s+|architect\s+)?"
    r"([A-Z][A-Za-z.\s,&'-]{3,52}?)"
    r"(?:(?<!\b[A-Z])\.|,|;|\s+and\s+(?:built|constructed)|\s+in\s+1[89]|\s+for\s+|\s+who\s+|\s+as\s+)"
)


def match_post2016_landmark_pdf_candidates(
    coh_landmarks: list[dict[str, Any]],
    existing_enriched: list[dict[str, Any]],
    pdf_cache: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Backfill newly recovered COH Landmark Designation Report PDFs onto previously missing rows."""
    parsed_meta: dict[str, dict[str, Any]] = {}
    for orig_u, info in pdf_cache.items():
        if isinstance(info, dict) and info.get("ok") and info.get("text"):
            raw_txt = str(info["text"])
            meta_entry = parse_report_metadata(raw_txt)
            cur_arch = str(meta_entry.get("pdf_architect") or "").strip()
            if not cur_arch or re.search(r"\b[A-Z]$", cur_arch):
                cleaned_ws = re.sub(r"\s+", " ", raw_txt)
                hist_m = re.search(r"HISTORY\s+AND\s+SIGNIFICANCE(?:\s+SUMMARY)?\s*:", cleaned_ws, re.I)
                narr_chunk = cleaned_ws[hist_m.start():hist_m.start() + 3800] if hist_m else cleaned_ws[:3800]
                fb_m = _FALLBACK_PDF_ARCH_RE.search(narr_chunk)
                if fb_m:
                    fb_cand = _clean_architect_value(fb_m.group(1).strip(" .,;-"))
                    # Reject institutional/corporate false positives matched by "designed by ..."
                    # in narrative prose (e.g. Woodmen of the World Omaha HQ or South Texas bank/college).
                    if 4 <= len(fb_cand) <= 52 and not any(
                        bad in fb_cand.lower()
                        for bad in (
                            "city of houston",
                            "national register",
                            "omaha",
                            "current owner",
                            "property owner",
                            "south texas",
                        )
                    ):
                        meta_entry["pdf_architect"] = fb_cand
            parsed_meta[orig_u] = meta_entry

    raw_by_objid: dict[Any, dict[str, Any]] = {}
    for feat in coh_landmarks:
        p = feat.get("properties") or feat.get("attributes") or {}
        if p.get("OBJECTID") is not None:
            raw_by_objid[p["OBJECTID"]] = p

    updated_rows: list[dict[str, Any]] = []
    newly_matched_count = 0
    olivewood_sbr_url = (
        "https://www.houstontx.gov/planning/Commissions/hahc_042326/"
        "C_Houston-Olivewood-Cemetery-NR-SBR-Draft.pdf"
    )
    has_olivewood_sbr = bool(
        olivewood_sbr_url in pdf_cache and pdf_cache[olivewood_sbr_url].get("ok")
    )

    for row in existing_enriched:
        r = dict(row)
        r["name"] = clean_hpo_text(r.get("name") or "")
        r["address"] = clean_hpo_text(r.get("address") or "")
        if "pdf_architect" in r:
            r["pdf_architect"] = _clean_architect_value(r.get("pdf_architect") or "")
        r["final_architect"] = _clean_architect_value(r.get("final_architect") or "")
        r["final_style"] = clean_hpo_text(r.get("final_style") or "")
        r["pdf_summary"] = _scrub_personal_header_names(r.get("pdf_summary") or "")
        r["report_pdf_url"] = encode_clean_http_url(r.get("report_pdf_url") or "")
        r["secondary_pdf_url"] = encode_clean_http_url(r.get("secondary_pdf_url") or "")

        if r["report_pdf_url"]:
            updated_rows.append(r)
            continue

        raw_p = raw_by_objid.get(r.get("objectid")) or {}
        lm = str(raw_p.get("USER_LM_NUM") or r.get("lm_num") or "").strip()
        plm = str(raw_p.get("USER_PLM_NUM") or r.get("plm_num") or "").strip()
        hpo_id = str(raw_p.get("USER_HPO_ID") or "").strip()
        st_num = str(raw_p.get("USER_SITE_ST_NUM") or "").strip()
        st_name = str(raw_p.get("USER_SITE_ST_NAME") or "").strip()
        if not st_num and r["address"]:
            m_addr = re.match(r"^(\d+(?:-\d+)?)\s+(.+)$", r["address"])
            if m_addr:
                st_num, st_name = m_addr.group(1), m_addr.group(2)

        lm_codes = set(normalize_lm_code(lm) + normalize_lm_code(plm))
        if hpo_id and hpo_id != "-":
            lm_codes.add(hpo_id.upper())

        scored: list[tuple[int, str, str]] = []
        for orig_u, info in pdf_cache.items():
            if not isinstance(info, dict) or not info.get("ok"):
                continue
            sc = _score_candidate_landmark_pdf(
                orig_u,
                info,
                parsed_meta.get(orig_u) or {},
                lm_codes=lm_codes,
                st_num=st_num,
                st_name=st_name,
                lm_name=r["name"],
            )
            if sc >= 95:
                final_u = encode_clean_http_url(str(info.get("url") or orig_u))
                is_final_bonus = 5 if "final" in orig_u.lower() or "action" in orig_u.lower() else 0
                scored.append((sc + is_final_bonus, orig_u, final_u))

        scored.sort(key=lambda item: (-item[0], len(item[2]), item[2]))
        dedup_hits: list[tuple[str, str]] = []
        seen_f: set[str] = set()
        for _, orig_u, final_u in scored:
            if final_u not in seen_f:
                seen_f.add(final_u)
                dedup_hits.append((orig_u, final_u))

        if dedup_hits:
            newly_matched_count += 1
            best_orig, best_final = dedup_hits[0]
            r["report_pdf_url"] = best_final
            if len(dedup_hits) > 1 and not r["secondary_pdf_url"]:
                r["secondary_pdf_url"] = dedup_hits[1][1]
            if "olivewood" in r["name"].lower() and has_olivewood_sbr:
                r["secondary_pdf_url"] = olivewood_sbr_url

            meta = parsed_meta.get(best_orig) or {}
            if meta.get("pdf_year_built") and not r.get("pdf_year_built"):
                r["pdf_year_built"] = int(meta["pdf_year_built"])
            if not r.get("final_year_built") and r.get("pdf_year_built"):
                r["final_year_built"] = int(r["pdf_year_built"])
            if meta.get("pdf_architect") and not r.get("pdf_architect"):
                cleaned_arch = _clean_architect_value(meta["pdf_architect"])
                if cleaned_arch:
                    r["pdf_architect"] = cleaned_arch
            if not r.get("final_architect") and r.get("pdf_architect"):
                r["final_architect"] = _clean_architect_value(r["pdf_architect"])
            if meta.get("pdf_style") and not r.get("pdf_style"):
                r["pdf_style"] = clean_hpo_text(meta["pdf_style"])
            if not r.get("final_style") and r.get("pdf_style"):
                r["final_style"] = r["pdf_style"]
            if meta.get("pdf_summary") and not r.get("pdf_summary"):
                r["pdf_summary"] = _scrub_personal_header_names(meta["pdf_summary"])

        updated_rows.append(r)

    total_with_pdf = sum(1 for r in updated_rows if r.get("report_pdf_url"))
    return updated_rows, {
        "total_landmarks": len(updated_rows),
        "newly_matched_pdfs": newly_matched_count,
        "total_with_report_pdf": total_with_pdf,
        "remaining_missing_pdfs": len(updated_rows) - total_with_pdf,
    }


def _geom_centroid_lnglat(geom: dict[str, Any] | None) -> tuple[float, float] | None:
    if not isinstance(geom, dict):
        return None
    gtype = geom.get("type")
    coords = geom.get("coordinates")
    if not coords:
        return None
    if gtype == "Point" and len(coords) >= 2:
        return float(coords[0]), float(coords[1])
    ring = (
        coords[0]
        if gtype == "Polygon" and coords
        else (coords[0][0] if gtype == "MultiPolygon" and coords and coords[0] else None)
    )
    if not ring:
        return None
    xs = [float(pt[0]) for pt in ring if isinstance(pt, (list, tuple)) and len(pt) >= 2]
    ys = [float(pt[1]) for pt in ring if isinstance(pt, (list, tuple)) and len(pt) >= 2]
    if not xs:
        return None
    return sum(xs) / len(xs), sum(ys) / len(ys)


def _geom_bbox(geom: dict[str, Any] | None, pad_deg: float = 0.004) -> tuple[float, float, float, float] | None:
    if not isinstance(geom, dict):
        return None
    gtype = geom.get("type")
    coords = geom.get("coordinates")
    if not coords:
        return None
    pts: list[tuple[float, float]] = []
    if gtype == "Polygon":
        for ring in coords:
            for pt in ring:
                if len(pt) >= 2:
                    pts.append((float(pt[0]), float(pt[1])))
    elif gtype == "MultiPolygon":
        for poly in coords:
            for ring in poly:
                for pt in ring:
                    if len(pt) >= 2:
                        pts.append((float(pt[0]), float(pt[1])))
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs) - pad_deg, min(ys) - pad_deg, max(xs) + pad_deg, max(ys) + pad_deg)


def _extract_leading_directional(raw_addr: Any) -> str:
    """Return canonical leading street directional (`N`/`S`/`E`/`W`/...) from `raw_addr` if present."""
    norm = normalize_hpo_lookup_address(str(raw_addr or ""))
    pts = norm.split(" ")
    if len(pts) >= 4 and pts[1] == "1/2" and pts[2] in _CARDINAL_DIRS:
        return pts[2]
    return pts[1] if len(pts) >= 3 and pts[1] in _CARDINAL_DIRS else ""


def _has_opposing_directional(addr_a: Any, addr_b: Any, alt_a: Any = "") -> bool:
    """Return True when `addr_a` and `addr_b` carry explicit, conflicting street directionals."""
    da = _extract_leading_directional(addr_a)
    db = _extract_leading_directional(addr_b)
    d_alt = _extract_leading_directional(alt_a) if alt_a else ""
    return bool(da and db and da != db and (not d_alt or d_alt != db))


def _hcad_has_opposing_directional(
    src_addr: Any,
    hc_cand: str,
    addrs_by_hcad: dict[str, list[str]],
    alt_addr: Any = "",
) -> bool:
    """Return True when known situs addresses on `hc_cand` carry directionals opposing `src_addr`."""
    cand_addrs = [a for a in (addrs_by_hcad.get(hc_cand) or []) if a]
    return bool(cand_addrs) and all(
        _has_opposing_directional(src_addr, ca, alt_addr) for ca in cand_addrs
    )


def _load_address_shard_candidates(
    addr_shards_dir: Path,
    shard_cache: dict[str, dict[str, Any]],
    raw_addr: str,
    alt_addr: str = "",
) -> list[tuple[str, float, float, int]]:
    """Query `address_shards/addr_<prefix>.json.gz` for `(hcad_num, lng, lat, yr)` matches."""
    if not addr_shards_dir.is_dir():
        return []
    keys = normalize_hpo_street_address_keys(raw_addr)
    if not keys:
        return []
    first_parts = keys[0].split(" ", 1)
    if len(first_parts) < 2:
        return []
    num_tok = re.sub(r"\D", "", first_parts[0])
    if not num_tok or num_tok.startswith("0"):
        return []
    prefix = num_tok[:2] if len(num_tok) >= 2 else f"{num_tok}_"
    if prefix not in shard_cache:
        shard_cache[prefix] = read_json_or_gzip_shard(addr_shards_dir, f"addr_{prefix}")
    shard_doc = shard_cache[prefix]
    if not shard_doc:
        return []

    target_street_norms = {k.split(" ", 1)[1] for k in keys if " " in k}
    hits: list[tuple[str, float, float, int]] = []
    for raw_st_name, entries in shard_doc.items():
        st_norm = normalize_hpo_lookup_address(f"100 {raw_st_name}")
        st_tail = st_norm.split(" ", 1)[1] if " " in st_norm else st_norm
        st_tail_nodir = re.sub(r"^(?:N|S|E|W)\s+", "", st_tail)
        if st_tail not in target_street_norms and st_tail_nodir not in target_street_norms:
            continue
        if _has_opposing_directional(raw_addr, f"100 {st_tail}", alt_addr):
            continue
        for ent in entries:
            if not isinstance(ent, list) or len(ent) < 5:
                continue
            ent_num = re.sub(r"\D", "", str(ent[0]))
            if ent_num == num_tok:
                hc = normalize_standing_hcad_token(ent[4])
                if hc:
                    hits.append((hc, float(ent[1]), float(ent[2]), int(ent[3] or 0)))
    return hits


def _is_compatible_entity(
    ent_id: Any,
    ent_addr: Any,
    source_addr_keys: set[str],
    source_raw_addr: Any = "",
    source_alt_addr: Any = "",
    *,
    require_addr_match: bool = False,
) -> bool:
    """Return True when `ent_id`/`ent_addr` is a primary building compatible with `source_addr_keys`."""
    if _is_sub_building_id(ent_id):
        return False
    if source_raw_addr and ent_addr and _has_opposing_directional(
        source_raw_addr, ent_addr, source_alt_addr
    ):
        return False
    ent_keys = set(normalize_hpo_street_address_keys(ent_addr))
    if require_addr_match:
        return bool(source_addr_keys and ent_keys and (source_addr_keys & ent_keys))
    if source_addr_keys and ent_keys and not (source_addr_keys & ent_keys):
        return False
    return True


def resolve_override_own_tier_and_port(
    hc_acct: str,
    bp0_props: dict[str, Any],
    lm_title: str,
    addr_str: str,
    *,
    permits_dir: Path,
    permit_shard_cache: dict[str, dict[str, Any]],
) -> tuple[str, str, str]:
    """Resolve Sec. 25.025-safe `(own_tier, port_id, use_category)` for a promoted override."""
    use_cat = str(bp0_props.get("use_category") or "").strip()
    raw_ot = str(bp0_props.get("own_tier") or "").strip()
    raw_pid = str(bp0_props.get("port_id") or "").strip()
    is_conf = bool(
        bp0_props.get("conf") in (1, True) or bp0_props.get("confidential") is True
    )
    if len(hc_acct) >= 3 and permits_dir.is_dir():
        pfx3 = hc_acct[:3]
        if pfx3 not in permit_shard_cache:
            loaded_sh = read_json_or_gzip_shard(permits_dir, f"permits_{pfx3}")
            permit_shard_cache[pfx3] = loaded_sh if isinstance(loaded_sh, dict) else {}
        if not permit_shard_cache[pfx3]:
            is_conf = True
        prec = permit_shard_cache[pfx3].get(hc_acct)
        if isinstance(prec, dict):
            if prec.get("conf") in (1, True):
                is_conf = True
            if not raw_ot and prec.get("ot"):
                raw_ot = str(prec["ot"]).strip()
            if not raw_pid and prec.get("pid") and not is_conf:
                raw_pid = str(prec["pid"]).strip()
        else:
            is_conf = True
    elif len(hc_acct) >= 3:
        is_conf = True
    if is_conf:
        raw_pid = ""
        if raw_ot not in ("homestead", "anonymity_shield", "civic_nonprofit"):
            raw_ot = "homestead"
    eff_ot = infer_fallback_ownership_tier(
        use_category=use_cat,
        landmark_name=lm_title,
        address=addr_str,
        current_tier=raw_ot,
    )
    return eff_ot, raw_pid, use_cat


@dataclass(frozen=True)
class OverrideProvenance:
    """Provenance, citation, and canonical `year_source` metadata for a promoted exact-year override."""

    verified_by: str
    source_type: str
    source_citation: str
    source_url: str
    year_source: str = "archival"
    verified_at: str = DEFAULT_GENERATED_AT_DATE
    landmark_name: str = ""
    landmark_report_url: str = ""
    landmark_code: str = ""
    landmark_summary: str = ""


@dataclass(frozen=True)
class PromotedAccountShift:
    """Countywide decade and `year_source` stat transitions triggered by promoting `hc`."""

    hc: str
    new_dec_key: str
    new_year_source: str
    fallback_transitions: tuple[tuple[str, str, str, str], ...]


@dataclass(frozen=True)
class HarvestBundlePaths:
    """Output JSON file paths written when `--apply` is set."""

    overlays: Path
    overrides: Path
    buildings: Path
    search_idx: Path
    surveys: Path


@dataclass
class HarvestInputs:
    """Typed workspace inputs and pre-indexed HCAD/address maps for the Milestone 12 harvester."""

    cdir: Path
    odir: Path
    coh_landmarks: list[dict[str, Any]]
    existing_enriched: list[dict[str, Any]]
    lm_pdf_cache: dict[str, dict[str, Any]]
    hpo_pdf_cache: dict[str, dict[str, Any]]
    overlays: dict[str, Any]
    overrides_doc: dict[str, Any]
    overrides: dict[str, Any]
    buildings_fc: dict[str, Any]
    search_idx: list[dict[str, Any]]
    paths: HarvestBundlePaths
    suppressed_redirect: dict[str, str] = field(default_factory=dict)
    effective_split_campus_hcads: set[str] = field(default_factory=set)
    gis_addr_by_dist: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    gis_addr_any: dict[str, set[str]] = field(default_factory=dict)
    gis_addrs_by_hcad: dict[str, list[str]] = field(default_factory=dict)
    known_addrs_by_hcad: dict[str, list[str]] = field(default_factory=dict)
    gis_class_by_hcad: dict[str, str] = field(default_factory=dict)
    gis_dist_by_hcad: dict[str, str] = field(default_factory=dict)
    blds_by_hcad: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    addr_to_bld_hcads: dict[str, set[str]] = field(default_factory=dict)
    addr_shards_dir: Path = field(default_factory=Path)
    shard_cache: dict[str, Any] = field(default_factory=dict)
    citydir_earliest_by_hcad: dict[str, int] = field(default_factory=dict)

    def canonical_hcad(self, raw_h: Any) -> str:
        hc = normalize_standing_hcad_token(raw_h)
        if not hc:
            return ""
        return self.suppressed_redirect.get(hc, hc)


@dataclass
class LandmarkReconcileResult:
    """Reconciled COH Landmark PDFs, 13-digit standing HCADs, and THC NRHP fallbacks."""

    enriched_landmarks: list[dict[str, Any]]
    lm_match_stats: dict[str, int]
    raw_demod_objids: set[Any]
    landmarks_hcad_resolved: int
    nrhp_fallbacks_attached: int
    lms_by_hcad: dict[str, list[dict[str, Any]]]


@dataclass(frozen=True)
class SurveySecondaryRow:
    """Canonical secondary structure entry parsed from an HPO district parcel inventory."""

    status: str
    year: str = ""
    type: str = ""

    def to_wire_dict(self) -> dict[str, Any]:
        wire: dict[str, Any] = {"s": STATUS_SHORT_CODE.get(self.status, "NC")}
        if self.year:
            wire["y"] = self.year
        wire["t"] = self.type
        return wire


@dataclass(frozen=True)
class SurveyParcelRow:
    """Canonical in-memory HPO Historic District parcel survey row (serialized via `to_wire_dict`)."""

    sid: str
    district: str
    district_idx: int
    inventory_pdf_url: str
    address: str
    alt_address: str = ""
    hcad_num: str = ""
    survey_status: str = "Non-Contributing"
    survey_year: int = 0
    survey_year_raw: str = ""
    survey_style: str = ""
    legal_desc: str = ""
    secondary: tuple[SurveySecondaryRow, ...] = ()

    @property
    def status_code(self) -> str:
        return STATUS_SHORT_CODE.get(self.survey_status, "NC")

    def to_wire_dict(self) -> dict[str, Any]:
        compact: dict[str, Any] = {
            "d": self.district_idx,
            "a": self.address,
            "st": self.status_code,
        }
        if self.alt_address:
            compact["alt"] = self.alt_address
        if self.hcad_num:
            compact["h"] = self.hcad_num
        if self.survey_year > 0:
            compact["y"] = self.survey_year
        if self.survey_year_raw and self.survey_year_raw != str(self.survey_year):
            compact["yr"] = self.survey_year_raw
        if self.survey_style:
            compact["sy"] = self.survey_style
        if self.legal_desc:
            compact["lg"] = self.legal_desc
        if self.secondary:
            compact["sec"] = [sec.to_wire_dict() for sec in self.secondary]
        return compact


@dataclass
class DistrictSurveyBundleResult:
    """Parsed 23-district HPO parcel survey inventory bundle and lookup indexes."""

    district_order: list[str]
    districts_summary: dict[str, dict[str, Any]]
    survey_rows_by_id: dict[str, SurveyParcelRow]
    survey_by_hcad: dict[str, list[str]]
    matched_survey_rows: int
    best_survey_for_hcad: dict[str, SurveyParcelRow]
    surveys_payload: dict[str, Any]


@dataclass
class BackfillStats:
    """Counters and promoted-account shifts produced by `_apply_landmark_and_survey_backfills`."""

    rebuilt_hcads: set[str]
    overlay_landmarks_updated: int
    overlay_districts_updated: int
    lm_pdf_links_attached: int
    lm_year_overrides_added: int
    survey_year_overrides_added: int
    survey_styles_backfilled: int
    promoted_shifts: list[PromotedAccountShift]


def _promote_year_override(
    *,
    hc: str,
    target_blds: list[dict[str, Any]],
    auth_yr: int,
    allowed_curr_years: tuple[int, ...],
    rebuilt_hcads: set[str],
    slab_hcads: set[str],
    provenance: OverrideProvenance,
    address: str,
    historic_district: str,
    contributing: str,
    style: str,
    architect: str,
    permits_dir: Path,
    permit_shard_cache: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], PromotedAccountShift] | None:
    """Promote an authoritative exact year onto `target_blds` and return `(new_ov, shift)` if eligible."""
    if not target_blds:
        return None
    bp0 = target_blds[0].get("properties") or {}
    curr_b_yr = int(bp0.get("year_built") or 0)
    distinct_curr_years = {
        int((b.get("properties") or {}).get("year_built") or 0) for b in target_blds
    }
    if (
        len(distinct_curr_years) > 1
        or any(
            int((b.get("properties") or {}).get("year_built") or 0) not in allowed_curr_years
            or (b.get("properties") or {}).get("is_curated_override")
            for b in target_blds
        )
        or abs(auth_yr - curr_b_yr) < 2
        or hc in rebuilt_hcads
        or (auth_yr < 1945 and hc in slab_hcads)
    ):
        return None

    new_dec = (auth_yr // 10) * 10
    new_dec_key = str(new_dec)
    new_yr_src = provenance.year_source
    fallback_transitions: list[tuple[str, str, str, str]] = []
    for b in target_blds:
        bp = b.get("properties") or {}
        old_b_yr = int(bp.get("year_built") or 0)
        old_dec_key = str((old_b_yr // 10) * 10) if old_b_yr > 0 else "unknown"
        old_yr_src = str(bp.get("year_source") or "hcad_cama").strip() or "hcad_cama"
        if old_b_yr > 0:
            bp["original_hcad_year"] = old_b_yr
        bp["year_built"] = auth_yr
        bp["decade"] = new_dec
        bp["is_curated_override"] = True
        bp["year_source"] = new_yr_src
        bp["verified_by"] = provenance.verified_by
        if style and is_replaceable_style_value(bp.get("style")):
            bp["style"] = style
        fallback_transitions.append((old_dec_key, new_dec_key, old_yr_src, new_yr_src))

    eff_ot, eff_pid, eff_use = resolve_override_own_tier_and_port(
        hc,
        bp0,
        provenance.landmark_name or str(bp0.get("building_name") or ""),
        address,
        permits_dir=permits_dir,
        permit_shard_cache=permit_shard_cache,
    )
    clean_arch = _clean_architect_value(architect)
    new_ov: dict[str, Any] = {
        "hcad_num": hc,
        "address": address,
        "historic_district": historic_district,
        "contributing": (
            contributing if contributing in VALID_CONTRIBUTING_VALUES else "Outside Historic District"
        ),
        "year_built": auth_yr,
        "decade": new_dec,
        "original_hcad_year": curr_b_yr or None,
        "bld_style": style or clean_hpo_text(str(bp0.get("bld_style") or "")),
        "architect": clean_arch if _architect_matches_year(clean_arch, auth_yr) else "",
        "own_tier": eff_ot,
        "source_type": provenance.source_type,
        "source_citation": _scrub_personal_header_names(provenance.source_citation)[:360],
        "source_url": provenance.source_url,
        "verified_by": provenance.verified_by,
        "verified_at": provenance.verified_at,
        "keep_shard_footprints": True,
    }
    if provenance.landmark_name:
        new_ov["landmark_name"] = provenance.landmark_name
    if provenance.landmark_report_url:
        new_ov["landmark_report_url"] = provenance.landmark_report_url
    if provenance.landmark_code:
        new_ov["landmark_code"] = provenance.landmark_code
    if provenance.landmark_summary:
        new_ov["landmark_summary"] = _scrub_personal_header_names(provenance.landmark_summary)
    if eff_pid:
        new_ov["port_id"] = eff_pid
    if eff_use:
        new_ov["use_category"] = eff_use
    return (
        new_ov,
        PromotedAccountShift(
            hc=hc,
            new_dec_key=new_dec_key,
            new_year_source=new_yr_src,
            fallback_transitions=tuple(fallback_transitions),
        ),
    )


def _has_parcel_survey_conflict(
    hc_survey_sids: list[str],
    survey_rows_by_id: dict[str, SurveyParcelRow],
    ref_year: int,
    *,
    has_multiple_primary_buildings: bool,
) -> bool:
    """Return True when sibling HPO survey rows or secondary structures conflict with `ref_year`."""
    rows = [survey_rows_by_id[sid] for sid in hc_survey_sids if sid in survey_rows_by_id]
    if len(rows) > 1 and any(
        r.status_code not in ("C", "PC") or r.survey_year not in (0, ref_year)
        for r in rows
    ):
        return True
    return has_multiple_primary_buildings and any(
        sec.status not in ("Contributing", "Potentially Contributing")
        or _parse_year_token(sec.year)[1] not in (0, ref_year)
        for r in rows
        for sec in r.secondary
    )


def _resolve_entity_hpo_survey_style(
    *,
    hc: str,
    ent_id: Any,
    ent_addr: Any,
    ent_year: int,
    best_survey_for_hcad: dict[str, SurveyParcelRow],
    survey_by_hcad: dict[str, list[str]],
    survey_rows_by_id: dict[str, SurveyParcelRow],
) -> str:
    """Resolve an architectural style from the primary HPO parcel row or address-matched sibling rows."""
    candidates: list[tuple[SurveyParcelRow, bool]] = []
    primary_row = best_survey_for_hcad.get(hc)
    if primary_row is not None:
        candidates.append((primary_row, False))
    sids = survey_by_hcad.get(hc) or []
    if len(sids) > 1:
        for sid in sids:
            sib = survey_rows_by_id.get(sid)
            if sib is not None and (primary_row is None or sib.sid != primary_row.sid):
                candidates.append((sib, True))
    for row, require_addr in candidates:
        u_st = row.survey_style.strip()
        if not is_usable_hpo_survey_style(u_st):
            continue
        if _is_post_survey_replacement(ent_year, row.survey_year, row.survey_status):
            continue
        row_keys = set(normalize_hpo_street_address_keys(row.address)) | set(
            normalize_hpo_street_address_keys(row.alt_address)
        )
        if _is_compatible_entity(
            ent_id,
            ent_addr,
            row_keys,
            row.address,
            row.alt_address,
            require_addr_match=require_addr,
        ):
            return u_st
    return ""


def _load_harvest_inputs(cdir: Path, odir: Path) -> HarvestInputs:
    """Load cache/bundle JSON documents and build canonical HCAD + GIS/building lookup indexes."""
    coh_landmarks = json.loads((cdir / "coh_landmarks.json").read_text(encoding="utf-8"))
    existing_enriched = json.loads(
        (cdir / "coh_landmarks_enriched.json").read_text(encoding="utf-8")
    )
    lm_pdf_cache = json.loads((cdir / "landmark_pdf_texts.json").read_text(encoding="utf-8"))
    hpo_pdf_cache = json.loads((cdir / "hpo_district_pdf_texts.json").read_text(encoding="utf-8"))
    coh_hist_parcels_raw = json.loads(
        (cdir / "coh_historic_parcels.json").read_text(encoding="utf-8")
    )
    coh_hist_parcels = (
        coh_hist_parcels_raw["features"]
        if isinstance(coh_hist_parcels_raw, dict) and "features" in coh_hist_parcels_raw
        else coh_hist_parcels_raw
    )

    paths = HarvestBundlePaths(
        overlays=odir / "overlays.json",
        overrides=odir / "curated_overrides.json",
        buildings=odir / "buildings.geojson",
        search_idx=odir / "search_index.json",
        surveys=odir / "hpo_district_surveys.json",
    )

    overlays = json.loads(paths.overlays.read_text(encoding="utf-8"))
    overrides_doc = json.loads(paths.overrides.read_text(encoding="utf-8"))
    overrides: dict[str, Any] = overrides_doc.get("overrides") or {}
    buildings_fc = json.loads(paths.buildings.read_text(encoding="utf-8"))
    search_idx: list[dict[str, Any]] = json.loads(paths.search_idx.read_text(encoding="utf-8"))

    # Build suppressed-shard -> master HCAD redirect map so we never revive suppressed lot accounts
    suppressed_redirect: dict[str, str] = {}
    for ov_k, ov_v in overrides.items():
        if not isinstance(ov_v, dict):
            continue
        if ov_v.get("landmark_summary"):
            ov_v["landmark_summary"] = _scrub_personal_header_names(str(ov_v["landmark_summary"]))
        if ov_v.get("source_citation"):
            ov_v["source_citation"] = _scrub_personal_header_names(str(ov_v["source_citation"]))
        master_hc = normalize_standing_hcad_token(
            str(ov_v.get("hcad_num") or str(ov_k).split("#", 1)[0])
        )
        if not master_hc:
            continue
        for sup_h in ov_v.get("suppress_shard_hcads") or []:
            sh = normalize_standing_hcad_token(sup_h)
            if sh and sh != master_hc:
                suppressed_redirect[sh] = master_hc

    inputs = HarvestInputs(
        cdir=cdir,
        odir=odir,
        coh_landmarks=coh_landmarks,
        existing_enriched=existing_enriched,
        lm_pdf_cache=lm_pdf_cache,
        hpo_pdf_cache=hpo_pdf_cache,
        overlays=overlays,
        overrides_doc=overrides_doc,
        overrides=overrides,
        buildings_fc=buildings_fc,
        search_idx=search_idx,
        paths=paths,
        suppressed_redirect=suppressed_redirect,
        addr_shards_dir=odir / "address_shards",
    )
    inputs.effective_split_campus_hcads = set(CAMPUS_SPLIT_HCADS) | {
        inputs.canonical_hcad(str(ov_k).split("#", 1)[0])
        for ov_k in overrides
        if _is_sub_building_id(ov_k) and inputs.canonical_hcad(str(ov_k).split("#", 1)[0])
    }
    citydir_path = odir / "citydir_index.json"
    if citydir_path.exists():
        citydir_doc = json.loads(citydir_path.read_text(encoding="utf-8"))
        for raw_hc, cd_rec in (citydir_doc.get("by_hcad") or {}).items():
            if not isinstance(cd_rec, dict):
                continue
            hc_c = inputs.canonical_hcad(raw_hc)
            cd_e = int(cd_rec.get("e") or 0)
            if hc_c and 1866 <= cd_e <= 1926:
                prev_e = inputs.citydir_earliest_by_hcad.get(hc_c)
                if prev_e is None or cd_e < prev_e:
                    inputs.citydir_earliest_by_hcad[hc_c] = cd_e

    # Index GIS historic parcels (strictly ignoring Owner_Mailto / Mail_Addr_* per Sec. 25.025)
    for feat in coh_hist_parcels:
        props = feat.get("properties") or feat.get("attributes") or {}
        hc = inputs.canonical_hcad(props.get("HCAD_NUM"))
        if not hc:
            continue
        canon_d = canonicalize_hpo_district_name(props.get("Historic_District_Name"))
        b_cls = str(props.get("Building_Classification") or "").strip()
        if b_cls and (hc not in inputs.gis_class_by_hcad or b_cls == "Contributing"):
            inputs.gis_class_by_hcad[hc] = b_cls
        if canon_d and canon_d != "Outside Historic District":
            inputs.gis_dist_by_hcad[hc] = canon_d
        site_addr = clean_hpo_text(str(props.get("Site_addr_1") or "")).strip()
        if site_addr:
            inputs.gis_addrs_by_hcad.setdefault(hc, []).append(site_addr)
            inputs.known_addrs_by_hcad.setdefault(hc, []).append(site_addr)
        for ak in normalize_hpo_street_address_keys(site_addr):
            inputs.gis_addr_any.setdefault(ak, set()).add(hc)
            if canon_d:
                dist_bucket = inputs.gis_addr_by_dist.setdefault(canon_d, {})
                lst = dist_bucket.setdefault(ak, [])
                if hc not in lst:
                    lst.append(hc)

    for bfeat in buildings_fc.get("features") or []:
        bp = bfeat.get("properties") or {}
        if bp.get("landmark_summary"):
            bp["landmark_summary"] = _scrub_personal_header_names(str(bp["landmark_summary"]))
        hc = inputs.canonical_hcad(bp.get("hcad_num"))
        if hc:
            inputs.blds_by_hcad.setdefault(hc, []).append(bfeat)
            b_addr = clean_hpo_text(str(bp.get("address") or "")).strip()
            if b_addr:
                inputs.known_addrs_by_hcad.setdefault(hc, []).append(b_addr)
            for ak in normalize_hpo_street_address_keys(b_addr):
                inputs.addr_to_bld_hcads.setdefault(ak, set()).add(hc)

    for ov_k, ov_v in overrides.items():
        if not isinstance(ov_v, dict) or ov_v.get("suppress_only"):
            continue
        hc = inputs.canonical_hcad(ov_v.get("hcad_num") or str(ov_k).split("#", 1)[0])
        if hc:
            ov_addr = clean_hpo_text(str(ov_v.get("address") or "")).strip()
            if ov_addr:
                inputs.known_addrs_by_hcad.setdefault(hc, []).append(ov_addr)
            for ak in normalize_hpo_street_address_keys(ov_addr):
                inputs.addr_to_bld_hcads.setdefault(ak, set()).add(hc)

    return inputs


def _reconcile_landmark_hcads_and_nrhp(inputs: HarvestInputs) -> LandmarkReconcileResult:
    """Match post-2016 COH Landmark PDFs, resolve standing 13-digit HCADs, and attach NRHP fallbacks."""
    coh_landmarks = inputs.coh_landmarks
    gis_addr_any = inputs.gis_addr_any
    addr_to_bld_hcads = inputs.addr_to_bld_hcads
    known_addrs_by_hcad = inputs.known_addrs_by_hcad
    addr_shards_dir = inputs.addr_shards_dir
    shard_cache = inputs.shard_cache
    overlays = inputs.overlays

    enriched_landmarks, lm_match_stats = match_post2016_landmark_pdf_candidates(
        coh_landmarks, inputs.existing_enriched, inputs.lm_pdf_cache
    )

    raw_lm_by_objid: dict[Any, dict[str, Any]] = {}
    raw_demod_objids: set[Any] = set()
    for feat in coh_landmarks:
        rp = feat.get("properties") or feat.get("attributes") or {}
        oid = rp.get("OBJECTID")
        if oid is not None:
            raw_lm_by_objid[oid] = feat
            if (
                str(rp.get("USER_DEMOD") or "").strip().upper() == "Y"
                or "DEMOLISHED" in str(rp.get("USER_SITE_NAME") or "").upper()
            ):
                raw_demod_objids.add(oid)

    landmarks_hcad_resolved = 0
    for r in enriched_landmarks:
        oid = r.get("objectid")
        is_demod = oid in raw_demod_objids or "DEMOLISHED" in str(r.get("name") or "").upper()
        if is_demod:
            r["hcad_num"] = ""
            continue
        curr_hc = inputs.canonical_hcad(r.get("hcad_num"))
        if curr_hc:
            r["hcad_num"] = curr_hc
            continue

        resolved_hc = ""
        lm_addr = str(r.get("address") or "")
        addr_keys = normalize_hpo_street_address_keys(lm_addr)
        for ak in addr_keys:
            raw_cands = gis_addr_any.get(ak) or addr_to_bld_hcads.get(ak) or set()
            cands = {
                hc_c
                for hc_c in raw_cands
                if not _hcad_has_opposing_directional(lm_addr, hc_c, known_addrs_by_hcad)
            }
            if len(cands) == 1:
                resolved_hc = next(iter(cands))
                break
        if not resolved_hc and lm_addr:
            shard_hits = [
                h
                for h in _load_address_shard_candidates(addr_shards_dir, shard_cache, lm_addr)
                if (hc_s := inputs.canonical_hcad(h[0]))
                and not _hcad_has_opposing_directional(lm_addr, hc_s, inputs.gis_addrs_by_hcad)
                and not _hcad_has_opposing_directional(lm_addr, hc_s, known_addrs_by_hcad)
            ]
            uniq_hcs = list(
                dict.fromkeys(
                    inputs.canonical_hcad(h[0]) for h in shard_hits if inputs.canonical_hcad(h[0])
                )
            )
            if len(uniq_hcs) == 1:
                resolved_hc = uniq_hcs[0]
            elif len(uniq_hcs) > 1 and oid in raw_lm_by_objid:
                lm_pt = _geom_centroid_lnglat(raw_lm_by_objid[oid].get("geometry"))
                if lm_pt:
                    best_hit = min(
                        shard_hits,
                        key=lambda item: (item[1] - lm_pt[0]) ** 2 + (item[2] - lm_pt[1]) ** 2,
                    )
                    if (best_hit[1] - lm_pt[0]) ** 2 + (best_hit[2] - lm_pt[1]) ** 2 < 0.002**2:
                        resolved_hc = inputs.canonical_hcad(best_hit[0])
        if resolved_hc:
            r["hcad_num"] = resolved_hc
            landmarks_hcad_resolved += 1
        else:
            r["hcad_num"] = ""

    nrhp_by_hcad: dict[str, str] = {}
    nrhp_by_addr: dict[str, str] = {}
    nrhp_addr_raw: dict[str, str] = {}
    nrhp_by_name: list[tuple[str, str, str, set[str]]] = []
    for nf in (overlays.get("nrhp_properties") or {}).get("features") or []:
        np = nf.get("properties") or {}
        pdf_u = encode_clean_http_url(str(np.get("nomination_pdf_url") or ""))
        if not pdf_u:
            continue
        nhc = inputs.canonical_hcad(np.get("hcad_num"))
        if nhc:
            nrhp_by_hcad[nhc] = pdf_u
        n_addr = str(np.get("address") or "")
        n_keys = set(normalize_hpo_street_address_keys(n_addr))
        for ak in n_keys:
            nrhp_by_addr[ak] = pdf_u
            nrhp_addr_raw[ak] = n_addr
        n_name = clean_hpo_text(str(np.get("name") or "")).lower().strip()
        if len(n_name) >= 10:
            nrhp_by_name.append((n_name, pdf_u, nhc, n_keys))

    nrhp_fallbacks_attached = 0
    for r in enriched_landmarks:
        if r.get("report_pdf_url") or r.get("secondary_pdf_url"):
            continue
        rhc = inputs.canonical_hcad(r.get("hcad_num"))
        fb_url = nrhp_by_hcad.get(rhc, "") if rhc else ""
        if not fb_url:
            for ak in normalize_hpo_street_address_keys(r.get("address")):
                if ak in nrhp_by_addr and not _has_opposing_directional(
                    r.get("address"), nrhp_addr_raw.get(ak, "")
                ):
                    fb_url = nrhp_by_addr[ak]
                    break
        if not fb_url:
            rn_low = clean_hpo_text(str(r.get("name") or "")).lower().strip()
            rn_prefix = rn_low.split("/")[0].strip()
            r_keys = set(normalize_hpo_street_address_keys(r.get("address")))
            for nn, nu, nhc, n_keys in nrhp_by_name:
                if rhc and nhc and rhc != nhc:
                    continue
                if r_keys and n_keys and not (r_keys & n_keys):
                    continue
                if nn in rn_low or (len(rn_prefix) >= 10 and rn_prefix in nn):
                    fb_url = nu
                    break
        if fb_url:
            r["secondary_pdf_url"] = fb_url
            nrhp_fallbacks_attached += 1

    lms_by_hcad: dict[str, list[dict[str, Any]]] = {}
    for enr in enriched_landmarks:
        oid = enr.get("objectid")
        if oid in raw_demod_objids or "DEMOLISHED" in str(enr.get("name") or "").upper():
            continue
        ehc_k = inputs.canonical_hcad(enr.get("hcad_num"))
        if ehc_k:
            lms_by_hcad.setdefault(ehc_k, []).append(enr)

    return LandmarkReconcileResult(
        enriched_landmarks=enriched_landmarks,
        lm_match_stats=lm_match_stats,
        raw_demod_objids=raw_demod_objids,
        landmarks_hcad_resolved=landmarks_hcad_resolved,
        nrhp_fallbacks_attached=nrhp_fallbacks_attached,
        lms_by_hcad=lms_by_hcad,
    )


def _build_hpo_district_surveys_bundle(
    inputs: HarvestInputs,
    *,
    generated_at: str = DEFAULT_GENERATED_AT_DATE,
) -> DistrictSurveyBundleResult:
    """Parse all 23 COH Historic District inventory PDFs and cross-link parcels with directional guards."""
    overlays = inputs.overlays
    hpo_pdf_cache = inputs.hpo_pdf_cache
    gis_addr_by_dist = inputs.gis_addr_by_dist
    gis_addrs_by_hcad = inputs.gis_addrs_by_hcad
    addr_shards_dir = inputs.addr_shards_dir
    shard_cache = inputs.shard_cache

    hd_features = (overlays.get("historic_districts") or {}).get("features") or []
    hd_bboxes: dict[str, tuple[float, float, float, float]] = {}
    for hf in hd_features:
        hp = hf.get("properties") or {}
        hname = canonicalize_hpo_district_name(hp.get("name"))
        bb = _geom_bbox(hf.get("geometry"))
        if hname and bb:
            hd_bboxes[hname] = bb

    district_order = sorted(DISTRICT_MANIFEST.keys())
    dist_name_to_idx = {d: idx for idx, d in enumerate(district_order)}
    districts_summary: dict[str, dict[str, Any]] = {}
    survey_rows_by_id: dict[str, SurveyParcelRow] = {}
    survey_by_hcad: dict[str, list[str]] = {}
    survey_by_addr: dict[str, list[str]] = {}
    best_survey_for_hcad: dict[str, SurveyParcelRow] = {}

    seq_num = 0
    matched_survey_rows = 0

    def resolve_district_doc_url(raw_key: str) -> str:
        if not raw_key:
            return ""
        info = hpo_pdf_cache.get(raw_key)
        if isinstance(info, dict) and info.get("ok") and info.get("url"):
            return encode_clean_http_url(str(info["url"]))
        return encode_clean_http_url(raw_key)

    for dist_name in district_order:
        cfg = DISTRICT_MANIFEST[dist_name]
        inv_key = cfg["inventory_key"]
        inv_entry = hpo_pdf_cache.get(inv_key)
        if not isinstance(inv_entry, dict) or not inv_entry.get("ok") or not inv_entry.get("text"):
            raise RuntimeError(
                f"Missing cached HPO inventory PDF text for {dist_name!r}: {inv_key}"
            )

        parsed_rows = parse_district_inventory_text(
            dist_name, str(inv_entry["text"]), cfg["layout"]
        )
        inv_url = resolve_district_doc_url(inv_key)
        desig_url = resolve_district_doc_url(cfg["designation_report_key"])
        guide_url = resolve_district_doc_url(cfg["guidelines_key"])
        map_url = resolve_district_doc_url(cfg["map_key"])

        status_counts: Counter[str] = Counter()
        dist_matched_hcads: set[str] = set()
        dist_idx = dist_name_to_idx[dist_name]
        dist_gis_map = gis_addr_by_dist.get(dist_name) or {}
        dist_bbox = hd_bboxes.get(dist_name)

        for prow in parsed_rows:
            seq_num += 1
            sid = f"s{seq_num}"
            st_full = prow["survey_status"]
            status_counts[st_full] += 1

            p_addr = prow["address"]
            p_alt = str(prow.get("alt_address") or "")
            p_dir = _extract_leading_directional(p_addr) or _extract_leading_directional(p_alt)
            cand_keys = normalize_hpo_street_address_keys(p_addr)
            for alt_k in normalize_hpo_street_address_keys(p_alt):
                if alt_k not in cand_keys:
                    cand_keys.append(alt_k)

            matched_hc = inputs.canonical_hcad(prow.get("hcad_num"))
            if not matched_hc:
                for ak in cand_keys:
                    raw_gis_hits = dist_gis_map.get(ak) or []
                    gis_hits = [
                        hc_c
                        for hc_c in raw_gis_hits
                        if not _hcad_has_opposing_directional(p_addr, hc_c, gis_addrs_by_hcad, p_alt)
                    ]
                    if not gis_hits:
                        continue
                    if not p_dir and len(gis_hits) > 1:
                        cand_dirs = {
                            d
                            for hc_c in gis_hits
                            for ga in (gis_addrs_by_hcad.get(hc_c) or [])
                            if (d := _extract_leading_directional(ga))
                        }
                        if len(cand_dirs) > 1:
                            continue
                    matched_hc = gis_hits[0]
                    break
            if not matched_hc and dist_bbox:
                shard_hits = _load_address_shard_candidates(
                    addr_shards_dir, shard_cache, p_addr, p_alt
                )
                in_box = [
                    hc_s
                    for h in shard_hits
                    if dist_bbox[0] <= h[1] <= dist_bbox[2]
                    and dist_bbox[1] <= h[2] <= dist_bbox[3]
                    and (hc_s := inputs.canonical_hcad(h[0]))
                    and not _hcad_has_opposing_directional(p_addr, hc_s, gis_addrs_by_hcad, p_alt)
                    and not _hcad_has_opposing_directional(
                        p_addr, hc_s, inputs.known_addrs_by_hcad, p_alt
                    )
                ]
                uniq_box = list(dict.fromkeys(in_box))
                if len(uniq_box) == 1:
                    matched_hc = uniq_box[0]

            canonical_row = SurveyParcelRow(
                sid=sid,
                district=dist_name,
                district_idx=dist_idx,
                inventory_pdf_url=inv_url,
                address=p_addr,
                alt_address=p_alt,
                hcad_num=matched_hc,
                survey_status=st_full,
                survey_year=int(prow["survey_year"] or 0),
                survey_year_raw=str(prow.get("survey_year_raw") or ""),
                survey_style=str(prow.get("survey_style") or ""),
                legal_desc=str(prow.get("legal_desc") or ""),
                secondary=tuple(
                    SurveySecondaryRow(
                        status=str(sec["status"]),
                        year=str(sec.get("year") or ""),
                        type=str(sec["type"]),
                    )
                    for sec in (prow.get("secondary") or [])
                ),
            )
            survey_rows_by_id[sid] = canonical_row

            if matched_hc:
                matched_survey_rows += 1
                dist_matched_hcads.add(matched_hc)
                hc_bucket = survey_by_hcad.setdefault(matched_hc, [])
                if sid not in hc_bucket:
                    hc_bucket.append(sid)
                prev = best_survey_for_hcad.get(matched_hc)
                rank_curr = (
                    (
                        2
                        if canonical_row.survey_status in ("Contributing", "Potentially Contributing")
                        else (1 if canonical_row.survey_status == "Non-Contributing" else 0)
                    ),
                    1 if canonical_row.survey_year > 0 else 0,
                    1 if is_usable_hpo_survey_style(canonical_row.survey_style) else 0,
                )
                rank_prev = (
                    (
                        (
                            2
                            if prev.survey_status in ("Contributing", "Potentially Contributing")
                            else (1 if prev.survey_status == "Non-Contributing" else 0)
                        ),
                        1 if prev.survey_year > 0 else 0,
                        1 if is_usable_hpo_survey_style(prev.survey_style) else 0,
                    )
                    if prev is not None
                    else (-1, -1, -1)
                )
                if rank_curr > rank_prev:
                    best_survey_for_hcad[matched_hc] = canonical_row

            for ak in cand_keys:
                addr_bucket = survey_by_addr.setdefault(ak, [])
                if sid not in addr_bucket:
                    addr_bucket.append(sid)

        districts_summary[dist_name] = {
            "designation_date": cfg["designation_date"],
            "inventory_pdf_url": inv_url,
            "designation_report_url": desig_url,
            "guidelines_pdf_url": guide_url,
            "map_pdf_url": map_url,
            "surveyed_parcel_count": len(parsed_rows),
            "matched_hcad_count": len(dist_matched_hcads),
            "status_counts": {
                "Contributing": int(status_counts.get("Contributing", 0)),
                "Potentially Contributing": int(status_counts.get("Potentially Contributing", 0)),
                "Non-Contributing": int(status_counts.get("Non-Contributing", 0)),
                "Vacant": int(status_counts.get("Vacant", 0)),
            },
        }

    wire_items = {sid: row.to_wire_dict() for sid, row in survey_rows_by_id.items()}
    surveys_payload = sanitize_json_structure_dashes(
        {
            "version": 1,
            "generated_at": generated_at,
            "district_count": len(district_order),
            "total_surveyed_rows": len(wire_items),
            "matched_hcad_parcels": len(survey_by_hcad),
            "matched_survey_rows": matched_survey_rows,
            "district_order": district_order,
            "district_aliases": dict(HPO_DISTRICT_NAME_ALIASES),
            "districts": districts_summary,
            "items": wire_items,
            "by_hcad": survey_by_hcad,
            "by_addr": survey_by_addr,
        }
    )

    return DistrictSurveyBundleResult(
        district_order=district_order,
        districts_summary=districts_summary,
        survey_rows_by_id=survey_rows_by_id,
        survey_by_hcad=survey_by_hcad,
        matched_survey_rows=matched_survey_rows,
        best_survey_for_hcad=best_survey_for_hcad,
        surveys_payload=surveys_payload,
    )


def _sync_overlay_landmarks_and_districts(
    inputs: HarvestInputs,
    lm_result: LandmarkReconcileResult,
    districts_summary: dict[str, dict[str, Any]],
) -> tuple[int, int]:
    """Synchronize `overlays.json` landmark and historic-district features with harvested metadata."""
    coh_landmarks = inputs.coh_landmarks
    overlays = inputs.overlays
    enriched_landmarks = lm_result.enriched_landmarks
    raw_demod_objids = lm_result.raw_demod_objids

    lm_by_objid = {r["objectid"]: r for r in enriched_landmarks if r.get("objectid") is not None}
    lm_by_name_addr = {(r["name"], r["address"]): r for r in enriched_landmarks}

    overlay_landmarks_updated = 0
    for idx, feat in enumerate((overlays.get("landmarks") or {}).get("features") or []):
        lp = feat.get("properties") or {}
        if lp.get("pdf_summary"):
            scrubbed_existing = _scrub_personal_header_names(str(lp["pdf_summary"]))
            if scrubbed_existing != lp["pdf_summary"]:
                lp["pdf_summary"] = scrubbed_existing
        raw_p = (
            (coh_landmarks[idx].get("properties") or coh_landmarks[idx].get("attributes") or {})
            if idx < len(coh_landmarks)
            else {}
        )
        raw_oid = raw_p.get("OBJECTID")
        enr = lm_by_objid.get(raw_oid) or lm_by_name_addr.get(
            (clean_hpo_text(str(lp.get("name") or "")), clean_hpo_text(str(lp.get("address") or "")))
        )
        if not enr:
            continue
        is_demod = (
            raw_oid in raw_demod_objids
            or bool(lp.get("demolished"))
            or "DEMOLISHED" in str(lp.get("name") or "").upper()
        )
        changed = False
        if is_demod and lp.get("hcad_num"):
            lp["hcad_num"] = ""
            changed = True
        elif not is_demod and enr.get("hcad_num") and not inputs.canonical_hcad(lp.get("hcad_num")):
            lp["hcad_num"] = enr["hcad_num"]
            changed = True
        if not lp.get("year_built") and enr.get("final_year_built"):
            lp["year_built"] = int(enr["final_year_built"])
            changed = True
        clean_lm_arch = _clean_architect_value(enr.get("final_architect"))
        if (
            not lp.get("architect")
            and clean_lm_arch
            and _architect_matches_year(clean_lm_arch, int(lp.get("year_built") or 0))
        ):
            lp["architect"] = clean_lm_arch
            changed = True
        if not lp.get("style") and enr.get("final_style"):
            lp["style"] = enr["final_style"]
            changed = True
        if enr.get("report_pdf_url") and lp.get("report_pdf_url") != enr["report_pdf_url"]:
            lp["report_pdf_url"] = enr["report_pdf_url"]
            changed = True
        if enr.get("secondary_pdf_url") and lp.get("secondary_pdf_url") != enr["secondary_pdf_url"]:
            lp["secondary_pdf_url"] = enr["secondary_pdf_url"]
            changed = True
        if enr.get("pdf_summary") and not lp.get("pdf_summary"):
            lp["pdf_summary"] = _scrub_personal_header_names(str(enr["pdf_summary"]))
            changed = True
        if changed:
            overlay_landmarks_updated += 1

    overlay_districts_updated = 0
    for hf in (overlays.get("historic_districts") or {}).get("features") or []:
        hp = hf.get("properties") or {}
        dname = canonicalize_hpo_district_name(hp.get("name"))
        dmeta = districts_summary.get(dname)
        if not dmeta:
            continue
        hp["designation_date"] = dmeta["designation_date"]
        hp["inventory_pdf_url"] = dmeta["inventory_pdf_url"]
        hp["designation_report_url"] = dmeta["designation_report_url"]
        hp["guidelines_pdf_url"] = dmeta["guidelines_pdf_url"]
        hp["map_pdf_url"] = dmeta["map_pdf_url"]
        hp["surveyed_parcel_count"] = dmeta["surveyed_parcel_count"]
        hp["survey_status_counts"] = dmeta["status_counts"]

        existing_cits = list(hp.get("citations") or [])
        seen_cit_urls = {str(c.get("url") or "").strip() for c in existing_cits if isinstance(c, dict)}
        for lbl, c_url in (
            (f"COH HPO Official Parcel Inventory ({dname})", dmeta["inventory_pdf_url"]),
            (f"COH HPO Design Guidelines ({dname})", dmeta["guidelines_pdf_url"]),
            (f"COH HAHC Designation Report ({dname})", dmeta["designation_report_url"]),
            (f"COH HPO District Lot Map ({dname})", dmeta["map_pdf_url"]),
        ):
            if c_url and c_url not in seen_cit_urls:
                existing_cits.append({"label": lbl, "url": c_url})
                seen_cit_urls.add(c_url)
        hp["citations"] = existing_cits
        overlay_districts_updated += 1

    return overlay_landmarks_updated, overlay_districts_updated


def _apply_landmark_and_survey_backfills(
    inputs: HarvestInputs,
    lm_result: LandmarkReconcileResult,
    survey_result: DistrictSurveyBundleResult,
    *,
    generated_at: str = DEFAULT_GENERATED_AT_DATE,
) -> BackfillStats:
    """Mutate overlays, curated_overrides, and buildings.geojson with guarded landmark/HPO metadata."""
    cdir = inputs.cdir
    odir = inputs.odir
    overrides = inputs.overrides
    blds_by_hcad = inputs.blds_by_hcad
    effective_split_campus_hcads = inputs.effective_split_campus_hcads
    gis_class_by_hcad = inputs.gis_class_by_hcad
    gis_dist_by_hcad = inputs.gis_dist_by_hcad

    enriched_landmarks = lm_result.enriched_landmarks
    raw_demod_objids = lm_result.raw_demod_objids
    lms_by_hcad = lm_result.lms_by_hcad
    districts_summary = survey_result.districts_summary
    survey_rows_by_id = survey_result.survey_rows_by_id
    survey_by_hcad = survey_result.survey_by_hcad
    best_survey_for_hcad = survey_result.best_survey_for_hcad

    bld_zip_candidates = [
        cdir / str(ACTIVE_TAX_YEAR) / "Real_building_land.zip",
        cdir / "Real_building_land.zip",
    ]
    bld_zip_path = next((p for p in bld_zip_candidates if p.exists()), bld_zip_candidates[0])
    candidate_year_hcads = set(best_survey_for_hcad.keys()) | {
        inputs.canonical_hcad(r.get("hcad_num"))
        for r in enriched_landmarks
        if inputs.canonical_hcad(r.get("hcad_num"))
    }
    struct_flags = load_bld1_structural_flags(bld_zip_path, candidate_year_hcads)
    slab_hcads: set[str] = struct_flags["slab_hcads"]
    rebuilt_hcads: set[str] = struct_flags["rebuilt_hcads"]

    overlay_landmarks_updated, overlay_districts_updated = _sync_overlay_landmarks_and_districts(
        inputs, lm_result, districts_summary
    )

    lm_pdf_links_attached = 0
    lm_year_overrides_added = 0
    permit_shard_cache: dict[str, dict[str, Any]] = {}
    permits_dir = odir / "permits"
    promoted_shifts: list[PromotedAccountShift] = []

    def _primary_buildings_for_hcad(hc_key: str) -> list[dict[str, Any]]:
        return [
            b
            for b in (blds_by_hcad.get(hc_key) or [])
            if not _is_sub_building_id(
                (b.get("properties") or {}).get("building_id")
                or (b.get("properties") or {}).get("id")
                or hc_key
            )
        ]

    for enr in enriched_landmarks:
        oid = enr.get("objectid")
        if oid in raw_demod_objids or "DEMOLISHED" in str(enr.get("name") or "").upper():
            continue
        hc = inputs.canonical_hcad(enr.get("hcad_num"))
        if not hc or hc in CAMPUS_SPLIT_HCADS:
            continue

        pdf_url = str(enr.get("report_pdf_url") or "")
        sec_url = str(enr.get("secondary_pdf_url") or "")
        lm_code = str(enr.get("plm_num") or enr.get("lm_num") or "").strip()
        gis_yr = int(enr["gis_year_built"]) if str(enr.get("gis_year_built") or "").isdigit() else 0
        pdf_yr = int(enr["pdf_year_built"]) if str(enr.get("pdf_year_built") or "").isdigit() else 0
        auth_yr = gis_yr or pdf_yr
        clean_enr_arch = _clean_architect_value(enr.get("final_architect"))
        clean_enr_summary = _scrub_personal_header_names(str(enr.get("pdf_summary") or ""))
        enr_raw_addr = str(enr.get("address") or "")
        enr_addr_keys = set(normalize_hpo_street_address_keys(enr_raw_addr))
        multi_lm_hcad = len(lms_by_hcad.get(hc) or []) > 1
        primary_blds_for_hc = _primary_buildings_for_hcad(hc)

        target_blds = [
            b
            for b in primary_blds_for_hc
            if _is_compatible_entity(
                (b.get("properties") or {}).get("building_id")
                or (b.get("properties") or {}).get("id")
                or hc,
                (b.get("properties") or {}).get("address"),
                enr_addr_keys,
                enr_raw_addr,
                require_addr_match=multi_lm_hcad,
            )
        ]

        for b in target_blds:
            bp = b.get("properties") or {}
            b_yr = int(bp.get("year_built") or 0)
            b_is_post_lm = _is_post_landmark_replacement(b_yr, auth_yr)
            if pdf_url and bp.get("landmark_report_url") != pdf_url and not b_is_post_lm:
                bp["landmark_report_url"] = pdf_url
                lm_pdf_links_attached += 1
            if sec_url and not bp.get("secondary_pdf_url") and not b_is_post_lm:
                bp["secondary_pdf_url"] = sec_url
            if lm_code and not bp.get("landmark_code") and not b_is_post_lm:
                bp["landmark_code"] = lm_code
            if clean_enr_summary and not bp.get("landmark_summary") and not b_is_post_lm:
                bp["landmark_summary"] = clean_enr_summary
            if (
                clean_enr_arch
                and not bp.get("architect")
                and not b_is_post_lm
                and _architect_matches_year(clean_enr_arch, int(b_yr or auth_yr or 0))
            ):
                bp["architect"] = clean_enr_arch
            if enr.get("final_style") and is_replaceable_style_value(bp.get("style")) and not b_is_post_lm:
                bp["style"] = enr["final_style"]

        hc_survey_sids = survey_by_hcad.get(hc) or []
        has_lm_conflict = _has_parcel_survey_conflict(
            hc_survey_sids,
            survey_rows_by_id,
            auth_yr,
            has_multiple_primary_buildings=len(primary_blds_for_hc) > 1,
        )

        ov = overrides.get(hc)
        primary_max_yr = max(
            (int((b.get("properties") or {}).get("year_built") or 0) for b in primary_blds_for_hc),
            default=0,
        )
        if (
            isinstance(ov, dict)
            and not ov.get("suppress_only")
            and _is_compatible_entity(
                ov.get("building_id") or ov.get("id") or hc,
                ov.get("address"),
                enr_addr_keys,
                enr_raw_addr,
                require_addr_match=multi_lm_hcad,
            )
        ):
            eff_ov_lm_yr = int(ov.get("year_built") or 0) or primary_max_yr
            ov_is_post_lm = _is_post_landmark_replacement(eff_ov_lm_yr, auth_yr)
            if pdf_url and not ov.get("landmark_report_url") and not ov_is_post_lm:
                ov["landmark_report_url"] = pdf_url
            if sec_url and not ov.get("secondary_pdf_url") and not ov_is_post_lm:
                ov["secondary_pdf_url"] = sec_url
            if lm_code and not ov.get("landmark_code") and not ov_is_post_lm:
                ov["landmark_code"] = lm_code
            if (
                clean_enr_arch
                and not ov.get("architect")
                and not ov_is_post_lm
                and _architect_matches_year(clean_enr_arch, int(eff_ov_lm_yr or auth_yr or 0))
            ):
                ov["architect"] = clean_enr_arch
            if enr.get("final_style") and is_replaceable_style_value(ov.get("bld_style")) and not ov_is_post_lm:
                ov["bld_style"] = enr["final_style"]
            if clean_enr_summary and not ov.get("landmark_summary") and not ov_is_post_lm:
                ov["landmark_summary"] = clean_enr_summary
        elif (
            ov is None
            and not multi_lm_hcad
            and hc not in effective_split_campus_hcads
            and hc not in EXCLUDED_AUTO_PROMOTION_HCADS
            and bool(target_blds)
            and len(target_blds) == len(primary_blds_for_hc)
            and not has_lm_conflict
            and pdf_url
            and auth_yr
            and 1836 <= auth_yr <= 1985
        ):
            cite_code = f"HPO #{lm_code}" if lm_code else "COH Landmark Report"
            cite_sum = clean_enr_summary or f"Designated City of Houston Landmark ({enr['name']})."
            dist_for_hc = gis_dist_by_hcad.get(hc, "")
            cnt_for_hc = (
                "Contributing"
                if gis_class_by_hcad.get(hc) == "Contributing"
                else ("Non-Contributing" if dist_for_hc else "Outside Historic District")
            )
            promoted_lm_res = _promote_year_override(
                hc=hc,
                target_blds=target_blds,
                auth_yr=auth_yr,
                allowed_curr_years=(0, 1900, 1910, 1920, 1930, 1940, 1950),
                rebuilt_hcads=rebuilt_hcads,
                slab_hcads=slab_hcads,
                provenance=OverrideProvenance(
                    verified_by="City of Houston HAHC Landmark Report",
                    source_type="COH Landmark Designation Report",
                    source_citation=f"{cite_code}: {cite_sum}",
                    source_url=pdf_url,
                    year_source="curated_landmark",
                    verified_at=generated_at,
                    landmark_name=enr["name"],
                    landmark_report_url=pdf_url,
                    landmark_code=lm_code,
                    landmark_summary=clean_enr_summary,
                ),
                address=enr["address"],
                historic_district=dist_for_hc,
                contributing=cnt_for_hc,
                style=str(enr.get("final_style") or ""),
                architect=clean_enr_arch,
                permits_dir=permits_dir,
                permit_shard_cache=permit_shard_cache,
            )
            if promoted_lm_res is not None:
                promoted_lm_ov, lm_shift = promoted_lm_res
                overrides[hc] = promoted_lm_ov
                promoted_shifts.append(lm_shift)
                lm_year_overrides_added += 1

    survey_year_overrides_added = 0
    survey_styles_backfilled = 0

    for hc, srow in sorted(best_survey_for_hcad.items()):
        if hc in CAMPUS_SPLIT_HCADS:
            continue
        s_style = srow.survey_style.strip()
        usable_style = s_style if is_usable_hpo_survey_style(s_style) else ""
        s_yr = srow.survey_year
        s_status = srow.survey_status
        dist_name = srow.district
        s_raw_addr = srow.address
        s_alt_addr = srow.alt_address
        s_addr_keys = set(normalize_hpo_street_address_keys(s_raw_addr)) | set(
            normalize_hpo_street_address_keys(s_alt_addr)
        )
        primary_blds_for_hc = _primary_buildings_for_hcad(hc)
        hc_survey_sids = survey_by_hcad.get(hc) or []

        ov = overrides.get(hc)
        primary_max_yr = max(
            (int((b.get("properties") or {}).get("year_built") or 0) for b in primary_blds_for_hc),
            default=0,
        )
        if (
            isinstance(ov, dict)
            and not ov.get("suppress_only")
            and hc not in rebuilt_hcads
            and is_replaceable_style_value(ov.get("bld_style"))
        ):
            resolved_ov_style = _resolve_entity_hpo_survey_style(
                hc=hc,
                ent_id=ov.get("building_id") or ov.get("id") or hc,
                ent_addr=ov.get("address"),
                ent_year=int(ov.get("year_built") or 0) or primary_max_yr,
                best_survey_for_hcad=best_survey_for_hcad,
                survey_by_hcad=survey_by_hcad,
                survey_rows_by_id=survey_rows_by_id,
            )
            if resolved_ov_style:
                ov["bld_style"] = resolved_ov_style
                survey_styles_backfilled += 1

        target_blds = [
            b
            for b in primary_blds_for_hc
            if _is_compatible_entity(
                (b.get("properties") or {}).get("building_id")
                or (b.get("properties") or {}).get("id")
                or hc,
                (b.get("properties") or {}).get("address"),
                s_addr_keys,
                s_raw_addr,
                s_alt_addr,
            )
        ]
        if hc not in rebuilt_hcads:
            for b in primary_blds_for_hc:
                bp = b.get("properties") or {}
                if not is_replaceable_style_value(bp.get("style")):
                    continue
                resolved_bld_style = _resolve_entity_hpo_survey_style(
                    hc=hc,
                    ent_id=bp.get("building_id") or bp.get("id") or hc,
                    ent_addr=bp.get("address"),
                    ent_year=int(bp.get("year_built") or 0),
                    best_survey_for_hcad=best_survey_for_hcad,
                    survey_by_hcad=survey_by_hcad,
                    survey_rows_by_id=survey_rows_by_id,
                )
                if resolved_bld_style:
                    bp["style"] = resolved_bld_style
                    survey_styles_backfilled += 1

        has_sv_conflict = _has_parcel_survey_conflict(
            hc_survey_sids,
            survey_rows_by_id,
            s_yr,
            has_multiple_primary_buildings=len(primary_blds_for_hc) > 1,
        )
        cd_earliest = int(inputs.citydir_earliest_by_hcad.get(hc) or 0)
        if (
            ov is None
            and hc not in effective_split_campus_hcads
            and hc not in EXCLUDED_AUTO_PROMOTION_HCADS
            and bool(target_blds)
            and len(target_blds) == len(primary_blds_for_hc)
            and not has_sv_conflict
            and gis_class_by_hcad.get(hc) == "Contributing"
            and s_status in ("Contributing", "Potentially Contributing")
            and 1850 <= s_yr <= 1965
            and (s_yr % 5) != 0
            and not (1866 <= cd_earliest < s_yr)
        ):
            bp0 = target_blds[0].get("properties") or {}
            yr_disp = srow.survey_year_raw or str(s_yr)
            addr_disp = clean_hpo_text(str(bp0.get("address") or s_raw_addr))
            promoted_sv_res = _promote_year_override(
                hc=hc,
                target_blds=target_blds,
                auth_yr=s_yr,
                allowed_curr_years=(1900, 1910, 1920, 1930, 1940, 1950),
                rebuilt_hcads=rebuilt_hcads,
                slab_hcads=slab_hcads,
                provenance=OverrideProvenance(
                    verified_by="COH HPO Historic District Survey",
                    source_type="COH HPO Historic District Survey",
                    source_citation=(
                        f"City of Houston Historic Preservation Office Official Inventory of {dist_name} "
                        f"(Circa Year Built: {yr_disp}; Status: {s_status}"
                        f"{f'; Style: {usable_style}' if usable_style else ''})."
                    ),
                    source_url=srow.inventory_pdf_url,
                    year_source="archival",
                    verified_at=generated_at,
                ),
                address=addr_disp,
                historic_district=dist_name,
                contributing="Contributing",
                style=usable_style,
                architect=str(bp0.get("architect") or ""),
                permits_dir=permits_dir,
                permit_shard_cache=permit_shard_cache,
            )
            if promoted_sv_res is not None:
                promoted_sv_ov, sv_shift = promoted_sv_res
                overrides[hc] = promoted_sv_ov
                promoted_shifts.append(sv_shift)
                survey_year_overrides_added += 1

    return BackfillStats(
        rebuilt_hcads=rebuilt_hcads,
        overlay_landmarks_updated=overlay_landmarks_updated,
        overlay_districts_updated=overlay_districts_updated,
        lm_pdf_links_attached=lm_pdf_links_attached,
        lm_year_overrides_added=lm_year_overrides_added,
        survey_year_overrides_added=survey_year_overrides_added,
        survey_styles_backfilled=survey_styles_backfilled,
        promoted_shifts=promoted_shifts,
    )


def _sync_search_index_with_hpo(
    inputs: HarvestInputs,
    lm_result: LandmarkReconcileResult,
    survey_result: DistrictSurveyBundleResult,
    backfill_stats: BackfillStats,
) -> int:
    """Synchronize `search_index.json` entries with updated overrides, landmarks, and HPO styles."""
    search_idx = inputs.search_idx
    overrides = inputs.overrides
    buildings_fc = inputs.buildings_fc
    lms_by_hcad = lm_result.lms_by_hcad
    survey_rows_by_id = survey_result.survey_rows_by_id
    survey_by_hcad = survey_result.survey_by_hcad
    best_survey_for_hcad = survey_result.best_survey_for_hcad
    rebuilt_hcads = backfill_stats.rebuilt_hcads
    bld_yr_by_id: dict[str, int] = {
        str((b.get("properties") or {}).get("id") or ""): int(
            (b.get("properties") or {}).get("year_built") or 0
        )
        for b in (buildings_fc.get("features") or [])
        if (b.get("properties") or {}).get("id")
    }

    sidx_updated = 0
    for entry in search_idx:
        if entry.get("is_neighborhood_entry") or entry.get("is_place_boundary"):
            continue
        ehc = inputs.canonical_hcad(entry.get("hcad_num"))
        eid = str(entry.get("id") or "")
        if not ehc or _is_sub_building_id(eid):
            continue
        eff_entry_yr = max(int(entry.get("year_built") or 0), bld_yr_by_id.get(eid, 0))
        changed_e = False
        ov_e = overrides.get(ehc)
        lm_ref_yr = max(
            (
                int(lm["gis_year_built"])
                if str(lm.get("gis_year_built") or "").isdigit() and int(lm["gis_year_built"]) > 0
                else (
                    int(lm["pdf_year_built"])
                    if str(lm.get("pdf_year_built") or "").isdigit()
                    else 0
                )
                for lm in (lms_by_hcad.get(ehc) or [])
            ),
            default=0,
        )
        if (
            isinstance(ov_e, dict)
            and not ov_e.get("suppress_only")
            and not _is_post_landmark_replacement(
                eff_entry_yr, int(ov_e.get("year_built") or 0) or lm_ref_yr
            )
            and _is_compatible_entity(
                eid or ehc,
                entry.get("address"),
                set(normalize_hpo_street_address_keys(ov_e.get("address"))),
                ov_e.get("address"),
            )
        ):
            if ov_e.get("year_built") and entry.get("year_built") != ov_e["year_built"]:
                entry["year_built"] = int(ov_e["year_built"])
                eff_entry_yr = max(eff_entry_yr, int(ov_e["year_built"]))
                changed_e = True
            ov_arch = _clean_architect_value(ov_e.get("architect"))
            if ov_arch and not entry.get("architect"):
                entry["architect"] = ov_arch
                changed_e = True
            if ov_e.get("landmark_code") and not entry.get("landmark_code"):
                entry["landmark_code"] = ov_e["landmark_code"]
                changed_e = True
            ov_st = str(ov_e.get("bld_style") or "").strip()
            if (
                ov_st
                and not is_replaceable_style_value(ov_st)
                and is_replaceable_style_value(entry.get("style"))
            ):
                entry["style"] = ov_st
                changed_e = True

        if ehc not in rebuilt_hcads and is_replaceable_style_value(entry.get("style")):
            resolved_entry_style = _resolve_entity_hpo_survey_style(
                hc=ehc,
                ent_id=eid or ehc,
                ent_addr=entry.get("address"),
                ent_year=eff_entry_yr,
                best_survey_for_hcad=best_survey_for_hcad,
                survey_by_hcad=survey_by_hcad,
                survey_rows_by_id=survey_rows_by_id,
            )
            if resolved_entry_style:
                entry["style"] = resolved_entry_style
                changed_e = True

        lms_for_ehc = lms_by_hcad.get(ehc) or []
        lm_hit = None
        if len(lms_for_ehc) == 1:
            cand_lm = lms_for_ehc[0]
            if _is_compatible_entity(
                eid or ehc,
                entry.get("address"),
                set(normalize_hpo_street_address_keys(cand_lm.get("address"))),
                cand_lm.get("address"),
            ):
                lm_hit = cand_lm
        elif len(lms_for_ehc) > 1:
            entry_addr_keys = set(normalize_hpo_street_address_keys(entry.get("address")))
            entry_title_low = clean_hpo_text(str(entry.get("name") or entry.get("label") or "")).lower()
            for cand_lm in lms_for_ehc:
                if entry.get("address") and _has_opposing_directional(
                    cand_lm.get("address"), entry.get("address")
                ):
                    continue
                cand_keys = set(normalize_hpo_street_address_keys(cand_lm.get("address")))
                cand_name_low = clean_hpo_text(str(cand_lm.get("name") or "")).lower()
                if (entry_addr_keys and cand_keys and (entry_addr_keys & cand_keys)) or (
                    entry_title_low
                    and cand_name_low
                    and (cand_name_low in entry_title_low or entry_title_low in cand_name_low)
                ):
                    lm_hit = cand_lm
                    break
        if lm_hit:
            lm_yr = int(
                lm_hit.get("final_year_built")
                or lm_hit.get("gis_year_built")
                or lm_hit.get("pdf_year_built")
                or 0
            )
            lm_is_post = _is_post_landmark_replacement(eff_entry_yr, lm_yr)
            lm_arch = _clean_architect_value(lm_hit.get("final_architect"))
            if (
                lm_arch
                and not entry.get("architect")
                and not lm_is_post
                and _architect_matches_year(lm_arch, int(eff_entry_yr or lm_yr or 0))
            ):
                entry["architect"] = lm_arch
                changed_e = True
            if (
                lm_hit.get("final_style")
                and is_replaceable_style_value(entry.get("style"))
                and not lm_is_post
            ):
                entry["style"] = lm_hit["final_style"]
                changed_e = True
        if changed_e:
            sidx_updated += 1

    return sidx_updated


def _compute_countywide_stat_transitions(
    inputs: HarvestInputs,
    promoted_shifts: list[PromotedAccountShift],
) -> list[tuple[str, str, str, str]]:
    """Compute `(old_dec, new_dec, old_src, new_src)` transitions across countywide shard footprints."""
    if not promoted_shifts:
        return []
    shift_by_hc: dict[str, PromotedAccountShift] = {s.hc: s for s in promoted_shifts}
    nd_files = sorted((inputs.cdir / "aligned_ndjson").glob("buildings_*.geojsonseq"))
    if not nd_files:
        return [tr for s in promoted_shifts for tr in s.fallback_transitions]

    # Build fast raw-token match set including suppressed shard HCADs redirecting to promoted masters
    raw_hc_tokens: set[str] = set(shift_by_hc.keys())
    for raw_sup, master_hc in inputs.suppressed_redirect.items():
        if master_hc in shift_by_hc:
            raw_hc_tokens.add(raw_sup)
    token_re = re.compile("|".join(re.escape(tok) for tok in sorted(raw_hc_tokens)))

    matched_by_hc: dict[str, list[tuple[str, str, str, str]]] = {}
    for nd_path in nd_files:
        with nd_path.open("r", encoding="utf-8") as nf:
            for raw_line in nf:
                if not token_re.search(raw_line):
                    continue
                feat = json.loads(raw_line.lstrip("\x1e"))
                fp = feat.get("properties") or {}
                hc_canon = inputs.canonical_hcad(fp.get("hcad_num"))
                shift = shift_by_hc.get(hc_canon)
                if shift is None:
                    continue
                if _is_sub_building_id(fp.get("building_id") or fp.get("id") or hc_canon):
                    continue
                old_yr = int(fp.get("year_built") or 0)
                old_dec = str((old_yr // 10) * 10) if old_yr > 0 else "unknown"
                old_src = str(fp.get("year_source") or "hcad_cama").strip() or "hcad_cama"
                matched_by_hc.setdefault(hc_canon, []).append(
                    (old_dec, shift.new_dec_key, old_src, shift.new_year_source)
                )

    transitions: list[tuple[str, str, str, str]] = []
    for s in promoted_shifts:
        transitions.extend(matched_by_hc.get(s.hc) or s.fallback_transitions)
    return transitions


def _persist_harvest_outputs(
    inputs: HarvestInputs,
    lm_result: LandmarkReconcileResult,
    survey_result: DistrictSurveyBundleResult,
    backfill_stats: BackfillStats,
    missing_landmark_rows: list[dict[str, str]],
    *,
    cache_bust: str | None = None,
) -> int | None:
    """Persist cache artifacts, compact bundle JSONs, historical audit queue, and cache-bust manifest."""
    cdir = inputs.cdir
    odir = inputs.odir
    paths = inputs.paths
    overrides_doc = inputs.overrides_doc
    buildings_fc = inputs.buildings_fc

    (cdir / "coh_landmarks_enriched.json").write_text(
        json.dumps(
            sanitize_json_structure_dashes(lm_result.enriched_landmarks),
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    missing_csv_path = cdir / "coh_landmarks_missing_pdfs_tpia.csv"
    with missing_csv_path.open("w", newline="", encoding="utf-8") as cf:
        writer = csv.writer(cf)
        writer.writerow(
            [
                "HPO_LM_Number",
                "HPO_PLM_Number",
                "Landmark_Name",
                "Street_Address",
                "HCAD_Account",
                "Year_Built",
            ]
        )
        for mr in missing_landmark_rows:
            writer.writerow(
                [
                    mr["lm_num"],
                    mr["plm_num"],
                    mr["name"],
                    mr["address"],
                    mr["hcad_num"],
                    mr["year_built"],
                ]
            )

    overrides_doc["overrides"] = inputs.overrides
    _write_sanitized_compact_json(paths.surveys, survey_result.surveys_payload)
    _write_sanitized_compact_json(paths.overlays, inputs.overlays)
    _write_sanitized_compact_json(paths.overrides, overrides_doc)
    _write_sanitized_compact_json(paths.buildings, buildings_fc)
    _write_sanitized_compact_json(paths.search_idx, inputs.search_idx)

    audit_queue_total: int | None = None
    canonical_repo_root = Path(__file__).resolve().parents[2]
    canonical_data_dir = canonical_repo_root / "app" / "public" / "data"
    is_canonical_data_dir = odir.resolve() == canonical_data_dir.resolve()
    if (
        is_canonical_data_dir
        and (odir / "historical_audit_queue.json").exists()
        and len(buildings_fc.get("features") or []) >= 5000
    ):
        from atlas_pipeline.audit_historical_accuracy import run_audit

        audit_res = run_audit(apply_fixes=True)
        audit_queue_total = int(audit_res["summary"]["total_queued_contradictions"])

    stats_path = odir / "stats_summary.json"
    if stats_path.exists():
        stats_doc = json.loads(stats_path.read_text(encoding="utf-8"))
        if audit_queue_total is not None:
            stats_doc["audit_queue_total"] = audit_queue_total
        transitions = _compute_countywide_stat_transitions(inputs, backfill_stats.promoted_shifts)
        dec_counts = stats_doc.get("decade_counts")
        src_counts = stats_doc.get("year_source_counts")
        for old_dec_k, new_dec_k, old_src_k, new_src_k in transitions:
            if (
                isinstance(dec_counts, dict)
                and old_dec_k != new_dec_k
                and old_dec_k in dec_counts
                and new_dec_k in dec_counts
                and int(dec_counts[old_dec_k]) > 0
            ):
                dec_counts[old_dec_k] = int(dec_counts[old_dec_k]) - 1
                dec_counts[new_dec_k] = int(dec_counts[new_dec_k]) + 1
            if (
                isinstance(src_counts, dict)
                and old_src_k != new_src_k
                and old_src_k in src_counts
                and int(src_counts[old_src_k]) > 0
            ):
                src_counts[old_src_k] = int(src_counts[old_src_k]) - 1
                src_counts[new_src_k] = int(src_counts.get(new_src_k, 0)) + 1
        _write_sanitized_compact_json(stats_path, stats_doc)

    if cache_bust and (odir / "pmtiles_manifest.json").exists():
        update_pmtiles_manifest_v3(odir / "pmtiles_manifest.json", cache_bust=cache_bust)
        if is_canonical_data_dir and (canonical_repo_root / "app" / "index.html").exists():
            sync_repo_tax_year_and_cache_bust(
                canonical_repo_root,
                tax_year=ACTIVE_TAX_YEAR,
                cache_bust=cache_bust,
            )

    return audit_queue_total


def run_hpo_harvest(
    *,
    cache_dir: Path,
    output_dir: Path,
    apply: bool = False,
    cache_bust: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Execute Milestone 12 HPO Historic District Survey & Post-2016 Landmark Report harvest."""
    generated_at_date = _resolve_generated_at_date(generated_at, cache_bust)
    inputs = _load_harvest_inputs(Path(cache_dir), Path(output_dir))
    lm_result = _reconcile_landmark_hcads_and_nrhp(inputs)
    survey_result = _build_hpo_district_surveys_bundle(inputs, generated_at=generated_at_date)
    backfill_stats = _apply_landmark_and_survey_backfills(
        inputs,
        lm_result,
        survey_result,
        generated_at=generated_at_date,
    )
    sidx_updated = _sync_search_index_with_hpo(inputs, lm_result, survey_result, backfill_stats)

    missing_landmark_rows = [
        {
            "lm_num": r.get("lm_num") or "",
            "plm_num": r.get("plm_num") or "",
            "name": r.get("name") or "",
            "address": r.get("address") or "",
            "hcad_num": r.get("hcad_num") or "",
            "year_built": str(r.get("final_year_built") or ""),
            "secondary_pdf_url": r.get("secondary_pdf_url") or "",
        }
        for r in lm_result.enriched_landmarks
        if not r.get("report_pdf_url")
    ]

    audit_queue_total = (
        _persist_harvest_outputs(
            inputs,
            lm_result,
            survey_result,
            backfill_stats,
            missing_landmark_rows,
            cache_bust=cache_bust,
        )
        if apply
        else None
    )

    lm_match_stats = lm_result.lm_match_stats
    return {
        "apply": bool(apply),
        "districts_harvested": len(survey_result.district_order),
        "total_surveyed_rows": len(survey_result.survey_rows_by_id),
        "matched_hcad_parcels": len(survey_result.survey_by_hcad),
        "matched_survey_rows": survey_result.matched_survey_rows,
        "total_landmarks": lm_match_stats["total_landmarks"],
        "newly_matched_landmark_pdfs": lm_match_stats["newly_matched_pdfs"],
        "total_landmarks_with_pdf": lm_match_stats["total_with_report_pdf"],
        "remaining_missing_landmark_pdfs": lm_match_stats["remaining_missing_pdfs"],
        "nrhp_fallbacks_attached": lm_result.nrhp_fallbacks_attached,
        "landmarks_hcad_resolved": lm_result.landmarks_hcad_resolved,
        "overlay_landmarks_updated": backfill_stats.overlay_landmarks_updated,
        "overlay_districts_updated": backfill_stats.overlay_districts_updated,
        "lm_pdf_links_attached": backfill_stats.lm_pdf_links_attached,
        "lm_year_overrides_added": backfill_stats.lm_year_overrides_added,
        "survey_year_overrides_added": backfill_stats.survey_year_overrides_added,
        "survey_styles_backfilled": backfill_stats.survey_styles_backfilled,
        "search_index_entries_updated": sidx_updated,
        "audit_queue_total": audit_queue_total,
        "missing_landmarks": missing_landmark_rows,
    }


def format_hpo_harvest_cli_summary(stats: dict[str, Any], *, elapsed_s: float) -> str:
    mode_lbl = "APPLIED" if stats.get("apply") else "DRY-RUN"
    aq_part = (
        f"\n   Historical audit queue synced  : {stats['audit_queue_total']:,}"
        if stats.get("audit_queue_total") is not None
        else ""
    )
    return (
        f"=== COH HPO District Surveys & Landmark Backfill ({mode_lbl}) Complete in {elapsed_s:.1f}s ===\n"
        f"   COH Historic Districts parsed  : {stats['districts_harvested']:,} / 23 "
        f"({stats['total_surveyed_rows']:,} survey rows; {stats['matched_hcad_parcels']:,} HCAD parcels)\n"
        f"   COH Landmark PDFs verified     : {stats['total_landmarks_with_pdf']:,} / {stats['total_landmarks']:,} "
        f"(+{stats['newly_matched_landmark_pdfs']:,} recovered; {stats['remaining_missing_landmark_pdfs']:,} pending TPIA; "
        f"+{stats['nrhp_fallbacks_attached']:,} NRHP fallbacks)\n"
        f"   Standing Landmark HCADs linked : +{stats['landmarks_hcad_resolved']:,}\n"
        f"   Year overrides added (LM/HPO)  : +{stats['lm_year_overrides_added']:,} / +{stats['survey_year_overrides_added']:,}\n"
        f"   HPO styles / search synced     : +{stats['survey_styles_backfilled']:,} / +{stats['search_index_entries_updated']:,}"
        f"{aq_part}"
    )
