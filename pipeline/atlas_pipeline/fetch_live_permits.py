#!/usr/bin/env python3
"""
Automated Live Permit & Asbestos Notification Ingestion Pipeline
for The Houston Building Atlas.

Fetches:
1. City of Houston Sold Permits (WebFOCUS live API at cohtora.houstontx.gov
   using concurrent date-slice fetching + project drilldown via sold_permit_d.fex,
   supplemented by weekly Sold Permits .xlsx spreadsheets on houstonpermittingcenter.org).
2. Texas Department of State Health Services (DSHS) Asbestos Abatement &
   Demolition Notifications (vo.ras.dshs.state.tx.us/datamart) including full
   3-page notification PDF extraction via pdftotext.

Cross-references all filings against historic structures in
app/public/data/buildings.geojson and app/public/data/search_index.json,
classifies risk severity (CRITICAL vs WATCH), enriches affected permit shards
(app/public/data/permits/permits_<prefix3>.json), and writes
app/public/data/active_permit_alerts.json for the Atlas UI.
"""

import argparse
import ast
import concurrent.futures
import copy
import datetime
import http.cookiejar
import io
import json
import re
import ssl
import subprocess
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "pipeline") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "pipeline"))

from atlas_pipeline.lost_houston_expansion import extract_curated_demolished_hcads
from atlas_pipeline.schema import (
    read_json_or_gzip_shard,
    write_deterministic_gzip_json_shard,
)

KIRBY_MANSION_PERMIT_HEADLINE = (
    "Demolished October 30 - November 2, 2026 following City of Houston Project #26090093 "
    "(1/2 Plumbing Sewer Disconnect on 2026-10-14 + 2/2 Building Demolition Permit on 2026-10-30) "
    "and Texas DSHS Asbestos Abatement Notification #2026005825 (scheduled 10/27/2026 to 11/06/2026)."
)

KIRBY_MANSION_PRESERVATION_NOTE = (
    "Built in 1894 for businessman J. S. Price and purchased by lumber magnate John Henry Kirby in 1896, "
    "the 36-room Tudor Revival mansion at 2000 Smith Street achieved its landmark appearance in a 1926-1928 "
    "remodeling designed by architect James Ruskin Bailey (1979 Preservation Houston Good Brick Award). "
    "Sold September 30, 2026 to OB Texas Properties LLC (Omair Bashir / Smith St C Store LLC for a planned "
    "13,254 SF Brake Time convenience store & gas station, TDLR #TABS2026022839), the unprotected structure "
    "was demolished between Friday, October 30 and Monday, November 2, 2026 under COH Project #26090093 and "
    "TX DSHS Notification #2026005825, and has been graduated to the Lost Houston catalog (lost_kirby_mansion_1894)."
)


def format_demolished_permit_badge_and_severity(demolished_date_str: str) -> tuple[str, str]:
    """Format human-readable badge and severity label for confirmed-demolished permit alerts."""
    raw = str(demolished_date_str or "").strip()
    try:
        pretty_dt = datetime.date.fromisoformat(raw).strftime("%b %-d, %Y")
    except ValueError:
        pretty_dt = raw or "2026"
    return (
        f"⚫ Demolished ({pretty_dt})",
        f"Confirmed Demolished ({pretty_dt}) • Graduated to Lost Houston",
    )
BUILDINGS_GEOJSON = REPO_ROOT / "app" / "public" / "data" / "buildings.geojson"
SEARCH_INDEX_JSON = REPO_ROOT / "app" / "public" / "data" / "search_index.json"
PERMITS_DIR = REPO_ROOT / "app" / "public" / "data" / "permits"
ALERTS_OUTPUT_JSON = REPO_ROOT / "app" / "public" / "data" / "active_permit_alerts.json"
SCORECARDS_JSON = REPO_ROOT / "app" / "public" / "data" / "neighborhood_scorecards.json"

COH_WFSERVLET_URL = "http://cohtora.houstontx.gov/ibi_apps/WFServlet"
HPC_SOLD_PERMITS_PAGE = "https://www.houstonpermittingcenter.org/sold-permits-search"
DSHS_BASE_URL = "https://vo.ras.dshs.state.tx.us/datamart"

# BEGIN DEFAULT_POLICY_IMPACT_BRIEF
DEFAULT_POLICY_IMPACT_BRIEF: dict = {
    "generated_at": "2026-11-06",
    "data_tax_year": 2026,
    "vacant_lot_cutoff_year": 2024,
    "total_dated_footprints": 629101,
    "total_improved_cama_accounts": 566355,
    "standing_buildings_by_age": [
        {
            "id": "50_plus",
            "label": "50+ Years Old (<= 1976)",
            "cutoff_year": 1976,
            "standing_count": 354251,
            "share_of_dated_pct": 56.31,
            "share_of_all_cama_pct": 62.55,
            "coh_hd_count": 6651,
            "coh_lm_count": 478,
            "protected_count": 6969,
            "unprotected_count": 347282,
            "unprotected_pct": 98.0,
        },
        {
            "id": "75_plus",
            "label": "75+ Years Old (<= 1951)",
            "cutoff_year": 1951,
            "standing_count": 133593,
            "share_of_dated_pct": 21.24,
            "share_of_all_cama_pct": 23.59,
            "coh_hd_count": 4975,
            "coh_lm_count": 458,
            "protected_count": 5274,
            "unprotected_count": 128319,
            "unprotected_pct": 96.1,
        },
        {
            "id": "pre_1940",
            "label": "Pre-WWII (< 1940)",
            "cutoff_year": 1939,
            "standing_count": 46076,
            "share_of_dated_pct": 7.32,
            "share_of_all_cama_pct": 8.14,
            "coh_hd_count": 4683,
            "coh_lm_count": 393,
            "protected_count": 4920,
            "unprotected_count": 41156,
            "unprotected_pct": 89.3,
        },
        {
            "id": "100_plus",
            "label": "100+ Years Old (<= 1926)",
            "cutoff_year": 1926,
            "standing_count": 13686,
            "share_of_dated_pct": 2.18,
            "share_of_all_cama_pct": 2.42,
            "coh_hd_count": 3497,
            "coh_lm_count": 271,
            "protected_count": 3621,
            "unprotected_count": 10065,
            "unprotected_pct": 73.5,
        },
        {
            "id": "pre_1900",
            "label": "Pre-1900 (Victorian / 19th C.)",
            "cutoff_year": 1899,
            "standing_count": 319,
            "share_of_dated_pct": 0.05,
            "share_of_all_cama_pct": 0.06,
            "coh_hd_count": 206,
            "coh_lm_count": 52,
            "protected_count": 233,
            "unprotected_count": 86,
            "unprotected_pct": 27.0,
        },
    ],
    "annual_demolitions_by_age": {
        "years": [
            {
                "year": 2020,
                "is_ytd": False,
                "total_demos": 1472,
                "known_direct_age_count": 539,
                "known_best_age_count": 1336,
                "age_50plus_direct": 432,
                "age_50plus_best": 1192,
                "age_50plus_direct_pct": 29.3,
                "age_50plus_best_pct": 81.0,
                "age_75plus_direct": 255,
                "age_75plus_best": 659,
                "age_pre1940_direct": 117,
                "age_pre1940_best": 235,
                "age_100plus_direct": 40,
                "age_100plus_best": 94,
                "age_100plus_direct_pct": 2.7,
                "age_100plus_best_pct": 6.4,
                "age_pre1900_direct": 1,
                "age_pre1900_best": 1,
            },
            {
                "year": 2021,
                "is_ytd": False,
                "total_demos": 1730,
                "known_direct_age_count": 558,
                "known_best_age_count": 1571,
                "age_50plus_direct": 444,
                "age_50plus_best": 1420,
                "age_50plus_direct_pct": 25.7,
                "age_50plus_best_pct": 82.1,
                "age_75plus_direct": 290,
                "age_75plus_best": 790,
                "age_pre1940_direct": 123,
                "age_pre1940_best": 272,
                "age_100plus_direct": 46,
                "age_100plus_best": 106,
                "age_100plus_direct_pct": 2.7,
                "age_100plus_best_pct": 6.1,
                "age_pre1900_direct": 1,
                "age_pre1900_best": 1,
            },
            {
                "year": 2022,
                "is_ytd": False,
                "total_demos": 1790,
                "known_direct_age_count": 562,
                "known_best_age_count": 1659,
                "age_50plus_direct": 455,
                "age_50plus_best": 1520,
                "age_50plus_direct_pct": 25.4,
                "age_50plus_best_pct": 84.9,
                "age_75plus_direct": 294,
                "age_75plus_best": 864,
                "age_pre1940_direct": 119,
                "age_pre1940_best": 250,
                "age_100plus_direct": 33,
                "age_100plus_best": 88,
                "age_100plus_direct_pct": 1.8,
                "age_100plus_best_pct": 4.9,
                "age_pre1900_direct": 0,
                "age_pre1900_best": 0,
            },
            {
                "year": 2023,
                "is_ytd": False,
                "total_demos": 1751,
                "known_direct_age_count": 621,
                "known_best_age_count": 1622,
                "age_50plus_direct": 492,
                "age_50plus_best": 1459,
                "age_50plus_direct_pct": 28.1,
                "age_50plus_best_pct": 83.3,
                "age_75plus_direct": 342,
                "age_75plus_best": 898,
                "age_pre1940_direct": 128,
                "age_pre1940_best": 228,
                "age_100plus_direct": 22,
                "age_100plus_best": 59,
                "age_100plus_direct_pct": 1.3,
                "age_100plus_best_pct": 3.4,
                "age_pre1900_direct": 1,
                "age_pre1900_best": 1,
            },
            {
                "year": 2024,
                "is_ytd": False,
                "total_demos": 1980,
                "known_direct_age_count": 782,
                "known_best_age_count": 1832,
                "age_50plus_direct": 618,
                "age_50plus_best": 1635,
                "age_50plus_direct_pct": 31.2,
                "age_50plus_best_pct": 82.6,
                "age_75plus_direct": 421,
                "age_75plus_best": 961,
                "age_pre1940_direct": 170,
                "age_pre1940_best": 291,
                "age_100plus_direct": 45,
                "age_100plus_best": 95,
                "age_100plus_direct_pct": 2.3,
                "age_100plus_best_pct": 4.8,
                "age_pre1900_direct": 0,
                "age_pre1900_best": 0,
            },
            {
                "year": 2025,
                "is_ytd": False,
                "total_demos": 1824,
                "known_direct_age_count": 1130,
                "known_best_age_count": 1728,
                "age_50plus_direct": 958,
                "age_50plus_best": 1531,
                "age_50plus_direct_pct": 52.5,
                "age_50plus_best_pct": 83.9,
                "age_75plus_direct": 632,
                "age_75plus_best": 908,
                "age_pre1940_direct": 248,
                "age_pre1940_best": 346,
                "age_100plus_direct": 84,
                "age_100plus_best": 123,
                "age_100plus_direct_pct": 4.6,
                "age_100plus_best_pct": 6.7,
                "age_pre1900_direct": 1,
                "age_pre1900_best": 1,
            },
            {
                "year": 2026,
                "is_ytd": True,
                "total_demos": 1274,
                "known_direct_age_count": 1240,
                "known_best_age_count": 1264,
                "age_50plus_direct": 1140,
                "age_50plus_best": 1163,
                "age_50plus_direct_pct": 89.5,
                "age_50plus_best_pct": 91.3,
                "age_75plus_direct": 718,
                "age_75plus_best": 737,
                "age_pre1940_direct": 258,
                "age_pre1940_best": 264,
                "age_100plus_direct": 64,
                "age_100plus_best": 66,
                "age_100plus_direct_pct": 5.0,
                "age_100plus_best_pct": 5.2,
                "age_pre1900_direct": 1,
                "age_pre1900_best": 1,
            },
        ],
        "full_year_average": {
            "period_label": "2020-2025 Full-Yr Avg",
            "total_demos": 1758,
            "age_50plus_direct": 566,
            "age_50plus_best": 1460,
            "age_50plus_direct_pct": 32.2,
            "age_50plus_best_pct": 83.0,
            "age_50plus_weekly_direct": 10.9,
            "age_50plus_weekly_best": 28.1,
            "age_75plus_direct": 372,
            "age_75plus_best": 847,
            "age_pre1940_direct": 151,
            "age_pre1940_best": 270,
            "age_100plus_direct": 45,
            "age_100plus_best": 94,
            "age_100plus_direct_pct": 2.6,
            "age_100plus_best_pct": 5.3,
            "age_100plus_weekly_direct": 0.9,
            "age_100plus_weekly_best": 1.8,
            "age_pre1900_direct": 0.7,
            "age_pre1900_best": 0.7,
        },
        "cumulative": {
            "period_label": "2020-2026 Cumulative",
            "total_demos": 11821,
            "age_50plus_direct": 4539,
            "age_50plus_best": 9920,
            "age_75plus_direct": 2952,
            "age_75plus_best": 5817,
            "age_pre1940_direct": 1163,
            "age_pre1940_best": 1886,
            "age_100plus_direct": 334,
            "age_100plus_best": 631,
            "age_pre1900_direct": 5,
            "age_pre1900_best": 5,
        },
    },
    "local_designation_coverage": {
        "excludes_heritage_districts": True,
        "heritage_district_exclusion_note": "Excludes Heritage Districts (such as Freedmen's Town Heritage District), which regulate only public infrastructure on City-owned rights-of-way and do not protect private property or any buildings.",
        "coh_total_parcels": 686101,
        "coh_improved_parcels": 566355,
        "coh_parcel_land_acres": 521280.6,
        "coh_parcel_land_sq_mi": 814.5,
        "coh_municipal_boundary_acres": 429919.6,
        "coh_municipal_boundary_sq_mi": 671.75,
        "rows": [
            {
                "id": "protected_landmarks",
                "category": "Protected Landmarks (PLM)",
                "protection_strength": "Strongest (permanent covenant; no 90-day waiver)",
                "feature_count": 199,
                "unique_parcels": 99,
                "outside_hd_parcels": 62,
                "pct_all_parcels": 0.014,
                "pct_improved_parcels": 0.017,
                "parcel_acres": 95.4,
                "outside_hd_acres": 88.0,
                "pct_parcel_area": 0.018,
                "pct_city_boundary_area": 0.022,
            },
            {
                "id": "regular_landmarks",
                "category": "Regular Landmarks (LM, Non-Protected)",
                "protection_strength": "Weak outside HD (subject to 90-day demolition waiver)",
                "feature_count": 302,
                "unique_parcels": 241,
                "outside_hd_parcels": 158,
                "pct_all_parcels": 0.035,
                "pct_improved_parcels": 0.043,
                "parcel_acres": 114.2,
                "outside_hd_acres": 92.9,
                "pct_parcel_area": 0.022,
                "pct_city_boundary_area": 0.027,
            },
            {
                "id": "coh_historic_districts",
                "category": "COH Historic Districts (23 Active Districts)",
                "protection_strength": "COA review required (Ch. 33)",
                "district_count": 23,
                "unique_parcels": 6718,
                "pct_all_parcels": 0.98,
                "pct_improved_parcels": 1.19,
                "parcel_acres": 1104.8,
                "polygon_acres": 1537.8,
                "polygon_sq_mi": 2.4,
                "pct_parcel_area": 0.21,
                "pct_city_boundary_area": 0.36,
            },
            {
                "id": "combined_local_designation",
                "category": "Combined Local Historic Designation (Deduplicated: PLM + LM + 23 Historic Districts)",
                "protection_strength": "All City historic building designations combined",
                "unique_parcels": 6938,
                "pct_all_parcels": 1.01,
                "pct_improved_parcels": 1.23,
                "parcel_acres": 1285.6,
                "polygon_acres": 1718.7,
                "polygon_sq_mi": 2.69,
                "pct_parcel_area": 0.25,
                "pct_city_boundary_area": 0.4,
            },
            {
                "id": "strict_demolition_protection",
                "category": "Strict Demolition Protection Only (PLM + 23 COH Historic Districts, Excl. 90-Day Waiver LMs)",
                "protection_strength": "True non-waiver demolition protection",
                "unique_parcels": 6780,
                "pct_all_parcels": 0.99,
                "pct_improved_parcels": 1.2,
                "parcel_acres": 1192.7,
                "polygon_acres": 1625.8,
                "polygon_sq_mi": 2.54,
                "pct_parcel_area": 0.23,
                "pct_city_boundary_area": 0.38,
            },
        ],
    },
    "speculative_land_clearing": {
        "vacant_lot_cohort_label": "2020-2024 Teardowns Still Vacant ($0 Improvement Value) in 2026",
        "rows": [
            {
                "id": "all",
                "cohort": "All COH Demolitions (2020-2026)",
                "total_demos": 11821,
                "nb_before_or_same_day_count": 48,
                "nb_before_or_same_day_pct": 0.4,
                "demo_before_nb_count": 11773,
                "demo_before_nb_pct": 99.6,
                "nb_within_1_to_30d_count": 12,
                "nb_within_31_to_180d_count": 24,
                "nb_after_180d_count": 61,
                "no_nb_permit_recorded_count": 11676,
                "no_nb_permit_recorded_pct": 98.8,
                "demos_2020_2024_total": 8723,
                "still_vacant_2026_count": 2157,
                "still_vacant_2026_pct": 24.7,
            },
            {
                "id": "dir_50plus",
                "cohort": "50+ Year Old Buildings (Direct Year)",
                "total_demos": 4539,
                "nb_before_or_same_day_count": 31,
                "nb_before_or_same_day_pct": 0.7,
                "demo_before_nb_count": 4508,
                "demo_before_nb_pct": 99.3,
                "nb_within_1_to_30d_count": 6,
                "nb_within_31_to_180d_count": 12,
                "nb_after_180d_count": 21,
                "no_nb_permit_recorded_count": 4469,
                "no_nb_permit_recorded_pct": 98.5,
                "demos_2020_2024_total": 2441,
                "still_vacant_2026_count": 1797,
                "still_vacant_2026_pct": 73.6,
            },
            {
                "id": "best_50plus",
                "cohort": "50+ Year Old Buildings (Incl. Subdivision Age)",
                "total_demos": 9920,
                "nb_before_or_same_day_count": 39,
                "nb_before_or_same_day_pct": 0.4,
                "demo_before_nb_count": 9881,
                "demo_before_nb_pct": 99.6,
                "nb_within_1_to_30d_count": 11,
                "nb_within_31_to_180d_count": 20,
                "nb_after_180d_count": 46,
                "no_nb_permit_recorded_count": 9804,
                "no_nb_permit_recorded_pct": 98.8,
                "demos_2020_2024_total": 7226,
                "still_vacant_2026_count": 1877,
                "still_vacant_2026_pct": 26.0,
            },
            {
                "id": "dir_100plus",
                "cohort": "100+ Year Old Buildings (Direct Year)",
                "total_demos": 334,
                "nb_before_or_same_day_count": 1,
                "nb_before_or_same_day_pct": 0.3,
                "demo_before_nb_count": 333,
                "demo_before_nb_pct": 99.7,
                "nb_within_1_to_30d_count": 0,
                "nb_within_31_to_180d_count": 4,
                "nb_after_180d_count": 0,
                "no_nb_permit_recorded_count": 329,
                "no_nb_permit_recorded_pct": 98.5,
                "demos_2020_2024_total": 186,
                "still_vacant_2026_count": 114,
                "still_vacant_2026_pct": 61.3,
            },
            {
                "id": "best_100plus",
                "cohort": "100+ Year Old Buildings (Incl. Subdivision Age)",
                "total_demos": 631,
                "nb_before_or_same_day_count": 1,
                "nb_before_or_same_day_pct": 0.2,
                "demo_before_nb_count": 630,
                "demo_before_nb_pct": 99.8,
                "nb_within_1_to_30d_count": 0,
                "nb_within_31_to_180d_count": 5,
                "nb_after_180d_count": 2,
                "no_nb_permit_recorded_count": 623,
                "no_nb_permit_recorded_pct": 98.7,
                "demos_2020_2024_total": 442,
                "still_vacant_2026_count": 114,
                "still_vacant_2026_pct": 25.8,
            },
            {
                "id": "dir_pre1900",
                "cohort": "Pre-1900 Buildings (Direct Year)",
                "total_demos": 5,
                "nb_before_or_same_day_count": 0,
                "nb_before_or_same_day_pct": 0.0,
                "demo_before_nb_count": 5,
                "demo_before_nb_pct": 100.0,
                "nb_within_1_to_30d_count": 0,
                "nb_within_31_to_180d_count": 0,
                "nb_after_180d_count": 0,
                "no_nb_permit_recorded_count": 5,
                "no_nb_permit_recorded_pct": 100.0,
                "demos_2020_2024_total": 3,
                "still_vacant_2026_count": 2,
                "still_vacant_2026_pct": 66.7,
            },
        ],
    },
}
# END DEFAULT_POLICY_IMPACT_BRIEF


def _load_scorecards_policy_brief(scorecards_path: Path | None = None) -> dict | None:
    """Return parsed `citywide_policy_brief` from `neighborhood_scorecards.json`, or `None` if absent/invalid."""
    sc_path = scorecards_path if scorecards_path is not None else SCORECARDS_JSON
    if sc_path and Path(sc_path).exists():
        try:
            with open(sc_path, "r", encoding="utf-8") as f_sc:
                sc_payload = json.load(f_sc)
            sc_brief = sc_payload.get("citywide_policy_brief")
            if isinstance(sc_brief, dict) and sc_brief.get("standing_buildings_by_age"):
                return copy.deepcopy(sc_brief)
        except (OSError, json.JSONDecodeError):
            pass
    return None


def get_policy_impact_brief(scorecards_path: Path | None = None) -> dict:
    """Load canonical `citywide_policy_brief` from `neighborhood_scorecards.json` when available."""
    loaded = _load_scorecards_policy_brief(scorecards_path)
    return loaded if loaded is not None else copy.deepcopy(DEFAULT_POLICY_IMPACT_BRIEF)


DEFAULT_ANNUAL_DEMOLITION_BENCHMARKS: dict = {
    "updated_through": "2026-10-28",
    "coh_structural_demolitions": [
        {
            "period": "2026 YTD",
            "date_range": "Jan 1 - Oct 28, 2026",
            "total_permits": 1755,
            "unique_addresses": 1676,
            "net_main_bldg_teardowns": 1634,
            "standard_demo_sd": 1519,
            "dangerous_bldg_4d": 236,
            "residential_1_2_fam": 1349,
            "commercial_multifam": 288,
            "garage_accessory": 118,
            "pool_spa_only": 1,
            "canopy_porch_partial": 2,
            "excluded_type13_bldg_demo_permits": 289,
            "historic_index_matched": 42,
            "pre_1940_matched": 27,
            "sewer_disconnect_permits": 1829,
        },
        {
            "period": "2025",
            "date_range": "Jan 1 - Dec 31, 2025",
            "total_permits": 2187,
            "unique_addresses": 2066,
            "net_main_bldg_teardowns": 2021,
            "standard_demo_sd": 2021,
            "dangerous_bldg_4d": 166,
            "residential_1_2_fam": 1680,
            "commercial_multifam": 356,
            "garage_accessory": 151,
            "pool_spa_only": 5,
            "canopy_porch_partial": 10,
            "excluded_type13_bldg_demo_permits": 397,
            "historic_index_matched": 60,
            "pre_1940_matched": 33,
            "sewer_disconnect_permits": 2156,
        },
        {
            "period": "2024",
            "date_range": "Jan 1 - Dec 31, 2024",
            "total_permits": 2380,
            "unique_addresses": 2287,
            "net_main_bldg_teardowns": 2161,
            "standard_demo_sd": 2220,
            "dangerous_bldg_4d": 160,
            "residential_1_2_fam": 1848,
            "commercial_multifam": 322,
            "garage_accessory": 210,
            "pool_spa_only": 5,
            "canopy_porch_partial": 4,
            "excluded_type13_bldg_demo_permits": 353,
            "historic_index_matched": 47,
            "pre_1940_matched": 31,
            "sewer_disconnect_permits": 2150,
        },
    ],
    "hcad_countywide_demolitions": [
        {
            "year": 2025,
            "total_permits": 2791,
            "unique_parcels": 2464,
            "net_main_bldg_teardowns": 2542,
            "residential_type_13": 2287,
            "commercial_type_33": 479,
            "general_type_43": 25,
            "garage_accessory": 143,
            "pool_spa_only": 87,
            "partial_addition_interior": 19,
            "excluded_type11_31_remodel_demo": 463,
            "coh_agency_061": 2136,
        },
        {
            "year": 2024,
            "total_permits": 2869,
            "unique_parcels": 2596,
            "net_main_bldg_teardowns": 2561,
            "residential_type_13": 2452,
            "commercial_type_33": 405,
            "general_type_43": 12,
            "garage_accessory": 203,
            "pool_spa_only": 85,
            "partial_addition_interior": 20,
            "excluded_type11_31_remodel_demo": 482,
            "coh_agency_061": 2251,
        },
        {
            "year": 2023,
            "total_permits": 2592,
            "unique_parcels": 2393,
            "net_main_bldg_teardowns": 2325,
            "residential_type_13": 2218,
            "commercial_type_33": 365,
            "general_type_43": 9,
            "garage_accessory": 136,
            "pool_spa_only": 88,
            "partial_addition_interior": 43,
            "excluded_type11_31_remodel_demo": 503,
            "coh_agency_061": 1999,
        },
        {
            "year": 2022,
            "total_permits": 2566,
            "unique_parcels": 2323,
            "net_main_bldg_teardowns": 2341,
            "residential_type_13": 2208,
            "commercial_type_33": 269,
            "general_type_43": 89,
            "garage_accessory": 108,
            "pool_spa_only": 88,
            "partial_addition_interior": 29,
            "excluded_type11_31_remodel_demo": 490,
            "coh_agency_061": 2074,
        },
        {
            "year": 2021,
            "total_permits": 2416,
            "unique_parcels": 2249,
            "net_main_bldg_teardowns": 2180,
            "residential_type_13": 2159,
            "commercial_type_33": 232,
            "general_type_43": 25,
            "garage_accessory": 123,
            "pool_spa_only": 77,
            "partial_addition_interior": 36,
            "excluded_type11_31_remodel_demo": 531,
            "coh_agency_061": 1918,
        },
        {
            "year": 2020,
            "total_permits": 2096,
            "unique_parcels": 1975,
            "net_main_bldg_teardowns": 1923,
            "residential_type_13": 1897,
            "commercial_type_33": 185,
            "general_type_43": 14,
            "garage_accessory": 91,
            "pool_spa_only": 40,
            "partial_addition_interior": 42,
            "excluded_type11_31_remodel_demo": 397,
            "coh_agency_061": 1610,
        },
    ],
    "hahc_historic_district_demolitions": [
        {
            "period": "2026 YTD",
            "total_coa_filings": 155,
            "demo_reloc_filings": 8,
            "main_res_approved": 0,
            "main_res_denied": 2,
            "accessory_or_partial_approved": 5,
            "summary_note": "0 main residential structures approved for demolition (2 denied at 202 E 31st 1/2 St & 528 Columbia St; 4 garages/carports & 1 accessory relocation approved)",
        },
        {
            "period": "2025",
            "total_coa_filings": 136,
            "demo_reloc_filings": 7,
            "main_res_approved": 2,
            "main_res_denied": 3,
            "accessory_or_partial_approved": 2,
            "summary_note": "3 main residential demolitions/relocations denied (1138 Fugate St, 918 Columbia St, 1108 E 16th St); 2 residential demolitions approved (1207 Tulane St, 11 Montglen Ct); 1 garage & 1 partial rear addition approved",
        },
        {
            "period": "2024",
            "total_coa_filings": 112,
            "demo_reloc_filings": 2,
            "main_res_approved": 0,
            "main_res_denied": 1,
            "accessory_or_partial_approved": 1,
            "summary_note": "0 main residential structures approved for demolition (1 house demolition denied at 1120 E 14th St; 1 garage demolition approved at 1031 E 16th St)",
        },
    ],
    "policy_impact_brief": DEFAULT_POLICY_IMPACT_BRIEF,
}


def build_annual_demolition_benchmarks(
    existing_benchmarks: dict | None = None,
    scorecards_path: Path | None = None,
) -> dict:
    """Resolve annual demolition benchmarks with per-table and per-row fallback to defaults."""
    base = copy.deepcopy(DEFAULT_ANNUAL_DEMOLITION_BENCHMARKS)
    sc_brief = _load_scorecards_policy_brief(scorecards_path)
    loaded_sc_brief = sc_brief is not None
    if sc_brief is not None:
        base["policy_impact_brief"] = sc_brief
    if not isinstance(existing_benchmarks, dict):
        return base
    if existing_benchmarks.get("updated_through"):
        base["updated_through"] = str(existing_benchmarks["updated_through"])
    if not loaded_sc_brief and isinstance(existing_benchmarks.get("policy_impact_brief"), dict):
        existing_brief = existing_benchmarks["policy_impact_brief"]
        if existing_brief.get("standing_buildings_by_age"):
            base["policy_impact_brief"] = {
                **copy.deepcopy(DEFAULT_POLICY_IMPACT_BRIEF),
                **copy.deepcopy(existing_brief),
            }
    for key, id_field in (
        ("coh_structural_demolitions", "period"),
        ("hcad_countywide_demolitions", "year"),
        ("hahc_historic_district_demolitions", "period"),
    ):
        val = existing_benchmarks.get(key)
        if isinstance(val, list) and len(val) > 0:
            def_by_id = {
                str(r.get(id_field)): r
                for r in DEFAULT_ANNUAL_DEMOLITION_BENCHMARKS.get(key, [])
                if isinstance(r, dict)
            }
            merged_rows = []
            for r in val:
                if isinstance(r, dict):
                    def_row = def_by_id.get(str(r.get(id_field)), {})
                    clean_r = {k: v for k, v in r.items() if v is not None}
                    merged_rows.append({**copy.deepcopy(def_row), **copy.deepcopy(clean_r)})
            if merged_rows:
                base[key] = merged_rows
    return base


def make_ssl_context():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


SSL_CTX = make_ssl_context()


def normalize_address(addr: str) -> str:
    """Normalize a Houston street address for matching against HCAD situs addresses."""
    if not addr:
        return ""
    s = str(addr).upper().strip()
    s = re.sub(r"\bHOUSTON\s+HARRIS\s+TX.*$", "", s)
    s = re.sub(r"\bHOUSTON\s*,?\s*TX.*$", "", s)
    s = re.sub(r"\bHOUSTON\b.*$", "", s)
    s = re.sub(r"\s+\d{5}(?:-\d{4})?\s*$", "", s)
    s = re.sub(r"[.,#]", " ", s)
    s = re.sub(r"\b(?:STE|SUITE|APT|UNIT|FL|FLOOR|BLDG|BLD|RM|ROOM)\s+[A-Z0-9-]+\b.*$", "", s)
    replacements = [
        (r"\bSTREET\b", "ST"),
        (r"\bAVENUE\b", "AVE"),
        (r"\bBOULEVARD\b", "BLVD"),
        (r"\bDRIVE\b", "DR"),
        (r"\bROAD\b", "RD"),
        (r"\bLANE\b", "LN"),
        (r"\bCOURT\b", "CT"),
        (r"\bCIRCLE\b", "CIR"),
        (r"\bPLACE\b", "PL"),
        (r"\bPARKWAY\b", "PKWY"),
        (r"\bPKY\b", "PKWY"),
        (r"\bFREEWAY\b", "FWY"),
        (r"\bHIGHWAY\b", "HWY"),
        (r"\bSPEEDWAY\b", "SPWY"),
        (r"\bTERRACE\b", "TER"),
        (r"\bTRAIL\b", "TRL"),
        (r"\bSQUARE\b", "SQ"),
        (r"\bNORTH\b", "N"),
        (r"\bSOUTH\b", "S"),
        (r"\bEAST\b", "E"),
        (r"\bWEST\b", "W"),
    ]
    for pat, rep in replacements:
        s = re.sub(pat, rep, s)
    s = re.sub(r"\s+", " ", s).strip()
    m = re.match(
        r"^(\d+\s+(?:1/2\s+)?(?:[NSEW]\s+)?[A-Z0-9\s]+?\s+(?:ST|AVE|BLVD|DR|RD|LN|CT|CIR|PL|PKWY|FWY|HWY|WAY|TER|TRL|SQ|LOOP))\s+(?:[A-Z]|\d+|A\s*&\s*B|BRDGE|MAIN)$",
        s,
    )
    if m:
        s = m.group(1).strip()
    return s


def compute_feature_centroid(geom):
    """Compute [lng, lat] centroid from a GeoJSON Polygon or MultiPolygon."""
    if not geom:
        return [-95.3698, 29.7604]
    gtype = geom.get("type")
    coords = geom.get("coordinates") or []
    pts = []
    if gtype == "Point" and len(coords) >= 2:
        return [round(float(coords[0]), 6), round(float(coords[1]), 6)]
    elif gtype == "Polygon" and coords:
        pts = coords[0]
    elif gtype == "MultiPolygon" and coords and coords[0]:
        pts = coords[0][0]
    if not pts:
        return [-95.3698, 29.7604]
    xs = [p[0] for p in pts if len(p) >= 2]
    ys = [p[1] for p in pts if len(p) >= 2]
    if not xs or not ys:
        return [-95.3698, 29.7604]
    return [round(sum(xs) / len(xs), 6), round(sum(ys) / len(ys), 6)]


def enrich_kirby_mansion_metadata(gj_data: dict) -> bool:
    """
    Ensure the John Henry Kirby Mansion (2000 Smith St, HCAD 0152300000001)
    carries its complete architectural and historical provenance from
    Preservation Houston's archival records.
    """
    changed = False
    for feat in gj_data.get("features", []):
        p = feat.get("properties") or {}
        if str(p.get("hcad_num") or "") == "0152300000001":
            updates = {
                "landmark_name": "John Henry Kirby Mansion",
                "alt_names": "Kirby Mansion; J. S. Price House; 2000 Smith Street",
                "year_built": 1894,
                "eff_year_built": 1894,
                "yr_remodel": 1928,
                "architect": "James Ruskin Bailey",
                "style": "Tudor Revival",
                "owner": "OB Texas Properties LLC (Omair Bashir)",
                "year_provenance": (
                    "Original residence built in 1894 for businessman J. S. Price; "
                    "purchased in 1896 by lumber magnate John Henry Kirby and remodeled twice, "
                    "culminating in the 1926-1928 36-room Tudor Revival expansion designed by "
                    "architect James Ruskin Bailey (1979 Preservation Houston Good Brick Award)."
                ),
            }
            for k, v in updates.items():
                if p.get(k) != v:
                    p[k] = v
                    changed = True
    return changed


def update_search_index_for_kirby_mansion():
    """Keep search_index.json synchronized with the enriched Kirby Mansion record."""
    if not SEARCH_INDEX_JSON.exists():
        return
    with open(SEARCH_INDEX_JSON, "r", encoding="utf-8") as f:
        idx_data = json.load(f)
    items = idx_data if isinstance(idx_data, list) else idx_data.get("items", [])
    modified = False
    for item in items:
        if str(item.get("hcad_num") or item.get("h") or "") == "0152300000001":
            if item.get("year_built") != 1894:
                item["year_built"] = 1894
                modified = True
            if item.get("landmark_name") != "John Henry Kirby Mansion":
                item["landmark_name"] = "John Henry Kirby Mansion"
                modified = True
            if item.get("architect") != "James Ruskin Bailey":
                item["architect"] = "James Ruskin Bailey"
                modified = True
            if item.get("style") != "Tudor Revival":
                item["style"] = "Tudor Revival"
                modified = True
    if modified:
        with open(SEARCH_INDEX_JSON, "w", encoding="utf-8") as f:
            json.dump(idx_data, f, separators=(",", ":"))


def _query_coh_single_window_with_status(
    seltd: str, srh: str, bdt: datetime.date, edt: datetime.date, ptype: str = "ALL"
) -> tuple[list, bool, str]:
    """Query City of Houston WebFOCUS online_per_se.fex for a single small date window with explicit error state."""
    params = urllib.parse.urlencode(
        {
            "IBIF_ex": "online_per_se.fex",
            "IBIAPP_app": "soldpermits",
            "SELTD": seltd,
            "SRH": srh,
            "BDT": bdt.strftime("%Y/%m/%d"),
            "EDT": edt.strftime("%Y/%m/%d"),
            "PTYPE": ptype,
        }
    )
    url = f"{COH_WFSERVLET_URL}?{params}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=20) as r:
            res = r.read().decode("utf-8", errors="ignore")
    except Exception as e:
        err_msg = f"COH WFServlet query failed ({seltd}={srh}, {bdt}..{edt}): {e}"
        print(f"  [WARN] {err_msg}", flush=True)
        return [], True, err_msg

    m_ar = re.search(r"ARstrings=(\[\x27Search.*?\]);\s*T_cont", res, re.S)
    m_tc = re.search(r"T_cont\[NumOfTable\]\s*=\s*(\[\[\[.*?\]);", res, re.S)
    if not m_ar or not m_tc:
        return [], False, ""
    try:
        ar_strings = ast.literal_eval(m_ar.group(1))
        t_cont = ast.literal_eval(m_tc.group(1))
    except Exception as e:
        return [], True, f"COH WFServlet parse failed ({seltd}={srh}, {bdt}..{edt}): {e}"

    results = []
    for row in t_cont:
        if len(row) < 7:
            continue
        vals = [str(ar_strings[cell[0]]).strip() for cell in row[:7]]
        proj_no, p_desc, owner_occ, addr, proj_desc, val, p_type = vals
        results.append(
            {
                "project_no": proj_no,
                "permit_desc": p_desc,
                "owner_occupant": owner_occ,
                "raw_address": addr,
                "norm_address": normalize_address(addr),
                "use_desc": proj_desc,
                "valuation": val,
                "permit_type": p_type,
            }
        )
    return results, False, ""


def query_coh_single_window(seltd: str, srh: str, bdt: datetime.date, edt: datetime.date, ptype: str = "ALL") -> list:
    """Query City of Houston WebFOCUS online_per_se.fex for a single small date window."""
    rows, _failed, _err = _query_coh_single_window_with_status(seltd, srh, bdt, edt, ptype)
    return rows


def fetch_coh_permits_parallel(lookback_days: int = 45, *, return_status: bool = False):
    """
    Fetch City of Houston DEMO, SEWER DISC, and SD permits across `lookback_days`
    using parallel 5-day date slices so every slice stays below the 100-row WebFOCUS page cap.
    """
    today = datetime.date.today()
    start_dt = today - datetime.timedelta(days=lookback_days)
    slices = []
    cur = start_dt
    step = datetime.timedelta(days=5)
    while cur <= today:
        end_s = min(today, cur + step)
        slices.append((cur, end_s))
        cur = end_s + datetime.timedelta(days=1)

    tasks = []
    for bdt, edt in slices:
        tasks.append(("BU", "DEMO", bdt, edt, "ALL"))
        tasks.append(("PT", " ", bdt, edt, "SD"))

    combined = {}
    errors: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futs = [pool.submit(_query_coh_single_window_with_status, *t) for t in tasks]
        for fut in concurrent.futures.as_completed(futs):
            rows, failed, err_msg = fut.result()
            if failed and err_msg:
                errors.append(err_msg)
            for r in rows:
                key = (r["project_no"], r.get("permit_type", ""), r.get("permit_desc", ""))
                combined[key] = r

    if not return_status:
        return combined
    coh_failed = bool(errors) and (len(errors) >= max(1, len(tasks) // 2) or not combined)
    err_summary = (
        f"{len(errors)}/{len(tasks)} COH WebFOCUS window queries failed ({errors[0]})"
        if errors
        else ""
    )
    return combined, coh_failed, err_summary


def fetch_coh_permit_detail(project_no: str, permit_type: str) -> dict:
    """
    Fetch granular permit details (exact paid date, FCC group, buyer/contractor name,
    buyer address, phone) from City of Houston sold_permit_d.fex.
    """
    params = urllib.parse.urlencode(
        {
            "IBIF_ex": "sold_permit_d.fex",
            "IBIAPP_app": "soldpermits",
            "PN": project_no,
            "PT": permit_type,
        }
    )
    url = f"{COH_WFSERVLET_URL}?{params}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=15) as r:
            res = r.read().decode("utf-8", errors="ignore")
    except Exception:
        return {}

    no_style = re.sub(r"<(?:script|style)[^>]*>.*?</(?:script|style)>", " ", res, flags=re.S | re.I)
    clean = re.sub(r"<[^>]+>", "\n", no_style)
    lines = [re.sub(r"\s+", " ", ln.replace("&nbsp;", " ")).strip() for ln in clean.splitlines()]
    lines = [ln for ln in lines if ln]

    detail = {}
    label_map = {
        "Project No:": "project_no",
        "Date :": "raw_date",
        "USE :": "use_desc",
        "Owner/Occupant :": "owner_occupant",
        "Job Address :": "job_address",
        "Valuation :": "valuation_formatted",
        "Permit Type :": "permit_type",
        "FCC Group :": "fcc_group",
        "Buyer :": "buyer",
        "Address :": "buyer_address",
        "Phone :": "buyer_phone",
    }
    for i, ln in enumerate(lines):
        if ln in label_map and i + 1 < len(lines):
            val = lines[i + 1]
            if val not in label_map:
                detail[label_map[ln]] = val

    raw_dt = detail.get("raw_date", "")
    m_dt = re.search(r"(\d{4})/(\d{2})/(\d{2})", raw_dt)
    if m_dt:
        detail["date"] = f"{m_dt.group(1)}-{m_dt.group(2)}-{m_dt.group(3)}"
    return detail


def parse_xlsx_sold_permits(xlsx_bytes: bytes) -> list:
    """Parse a City of Houston weekly Sold Permits .xlsx spreadsheet in memory."""
    ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    try:
        z = zipfile.ZipFile(io.BytesIO(xlsx_bytes))
        ss = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root:
                ss.append("".join(t.text or "" for t in si.iter() if t.tag.endswith("}t")))
        sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    except Exception:
        return []

    records = []
    for row in sheet.findall(".//a:sheetData/a:row", ns):
        cells = {}
        for c in row.findall("a:c", ns):
            ref = c.attrib.get("r", "")
            col_letters = "".join(ch for ch in ref if ch.isalpha())
            t = c.attrib.get("t", "")
            v_el = c.find("a:v", ns)
            val = v_el.text if v_el is not None and v_el.text is not None else ""
            if t == "s" and val.isdigit() and int(val) < len(ss):
                val = ss[int(val)]
            cells[col_letters] = str(val).strip()
        proj_no = cells.get("D", "")
        addr = cells.get("E", "")
        dt_raw = cells.get("B", "")
        if not proj_no or not addr or proj_no == "Project No":
            continue
        m_dt = re.search(r"(\d{4})/(\d{2})/(\d{2})", dt_raw)
        iso_dt = f"{m_dt.group(1)}-{m_dt.group(2)}-{m_dt.group(3)}" if m_dt else dt_raw
        records.append(
            {
                "project_no": proj_no,
                "date": iso_dt,
                "permit_desc": cells.get("C", ""),
                "raw_address": f"{addr} {cells.get('A', '')}".strip(),
                "norm_address": normalize_address(addr),
                "use_desc": cells.get("F", ""),
                "permit_type": "",
                "source": "coh_weekly_xlsx",
            }
        )
    return records


def fetch_recent_weekly_xlsx_permits(max_files: int = 4, *, return_status: bool = False):
    """Download the most recent weekly Sold Permits .xlsx files from houstonpermittingcenter.org."""
    try:
        req = urllib.request.Request(HPC_SOLD_PERMITS_PAGE, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=15) as r:
            body = r.read().decode("utf-8", errors="ignore")
    except Exception as e:
        err_msg = f"Could not fetch {HPC_SOLD_PERMITS_PAGE}: {e}"
        print(f"  [WARN] {err_msg}", flush=True)
        return ([], True, err_msg) if return_status else []

    raw_links = re.findall(r'href=["\']([^"\']*xlsx?[^"\']*)["\']', body, re.I)
    clean_urls = []
    for lk in raw_links:
        lk = lk.replace("&amp;", "&")
        if "view.officeapps.live.com" in lk and "src=" in lk:
            parsed = urllib.parse.parse_qs(urllib.parse.urlparse(lk).query)
            if "src" in parsed and parsed["src"]:
                lk = urllib.parse.unquote(parsed["src"][0])
        if lk.startswith("/"):
            lk = "https://www.houstonpermittingcenter.org" + lk
        if lk.lower().endswith(".xlsx") and lk not in clean_urls:
            clean_urls.append(lk)

    recent_urls = clean_urls[-max_files:]
    all_rows = []
    dl_errors: list[str] = []
    for u in recent_urls:
        try:
            u_safe = urllib.parse.quote(urllib.parse.unquote(u), safe=":/?&=%")
            req = urllib.request.Request(u_safe, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, context=SSL_CTX, timeout=15) as r:
                rows = parse_xlsx_sold_permits(r.read())
                print(f"  Parsed {len(rows)} permits from weekly report: {u_safe.split('/')[-1]}", flush=True)
                all_rows.extend(rows)
        except Exception as e:
            err_msg = f"Weekly XLSX download failed for {u}: {e}"
            print(f"  [WARN] {err_msg}", flush=True)
            dl_errors.append(err_msg)
    if not return_status:
        return all_rows
    weekly_failed = bool(dl_errors) and not all_rows
    return all_rows, weekly_failed, dl_errors[0] if dl_errors else ""


def parse_dshs_pdf_text(pdf_bytes: bytes) -> dict:
    """Extract structured fields from a 3-page Texas DSHS Asbestos/Demolition Notification PDF."""
    try:
        p = subprocess.run(["pdftotext", "-", "-"], input=pdf_bytes, capture_output=True, timeout=10)
        txt = p.stdout.decode("utf-8", errors="ignore")
    except Exception:
        return {}

    info = {}
    lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]

    if len(lines) >= 2 and re.match(r"^\d{4}/\d{2}/\d{2}$", lines[1]):
        info["filing_date"] = lines[1].replace("/", "-")

    for idx, ln in enumerate(lines):
        if ln == "Section III - Type of Work/Schedule":
            block = lines[idx : idx + 25]
            for bln in block:
                if bln in ("Abatement", "Demolition", "Abatement/Demolition", "Renovation", "Ordered Demolition"):
                    info["work_type"] = bln
                if re.match(r"^[A-Z][a-z]{2}\s+\d{1,2},\s+\d{4}$", bln):
                    if "start_date_pretty" not in info:
                        info["start_date_pretty"] = bln
                    elif "end_date_pretty" not in info:
                        info["end_date_pretty"] = bln
                if "AM" in bln and "PM" in bln:
                    info["work_hours"] = bln
                if "Containment" in bln:
                    info["containment_method"] = bln

    for idx, ln in enumerate(lines):
        if "Date of Asbestos Survey/NESHAP Inspection" in ln:
            block = lines[idx : idx + 15]
            for bln in block:
                if re.match(r"^\d+\s+years$", bln, re.I):
                    info["reported_building_age"] = bln
                elif re.match(r"^[\d,]+\s+square\s+feet$", bln, re.I):
                    info["reported_sqft"] = bln
                elif re.match(r"^\d+$", bln) and "reported_floors" not in info:
                    info["reported_floors"] = int(bln)
                elif re.match(r"^[A-Z][a-z]{2}\s+\d{1,2},\s+\d{4}$", bln):
                    info["survey_date"] = bln

    for idx, ln in enumerate(lines):
        if "RACM to be removed" in ln:
            nums = [
                lines[j]
                for j in range(idx + 1, min(len(lines), idx + 22))
                if re.match(r"^\d+$", lines[j])
            ]
            if len(nums) >= 2:
                info["racm_linear_ft"] = int(nums[0])
                info["racm_sqft"] = int(nums[1])
            if len(nums) >= 4:
                info["non_friable_int_linear_ft"] = int(nums[2])
                info["non_friable_int_sqft"] = int(nums[3])
            if len(nums) >= 6:
                info["non_friable_ext_linear_ft"] = int(nums[4])
                info["non_friable_ext_sqft"] = int(nums[5])

    m_desc = re.search(
        r"Section V - Description of work practices\s+Description\s+(.*?)\s+Section VI - Project Personnel",
        txt,
        re.S,
    )
    if m_desc:
        desc_clean = re.sub(r"\s+", " ", m_desc.group(1)).strip()
        desc_clean = re.sub(
            r"^Immediately seal off the area,\s*wet materials,\s*clean using wet methods and/or HEPA vacuum\.\s*Notify local authority\.\s*",
            "",
            desc_clean,
            flags=re.I,
        )
        info["work_description"] = desc_clean

    m_contractor = re.search(
        r"Asbestos Abatement Contractor\s+(\d+)\s+DSHS License #\s+([^\n]+)\s+Name\s+([^\n]+)\s+Address\s+([^\n]+)",
        txt,
        re.S,
    )
    if m_contractor:
        info["contractor_license"] = m_contractor.group(1).strip()
        info["abatement_contractor"] = m_contractor.group(2).strip()
        info["contractor_address"] = f"{m_contractor.group(3).strip()}, {m_contractor.group(4).strip()}"

    m_owner = re.search(
        r"Facility Owner\s+([^\n]+)\s+Name\s+([^\n]+)\s+Address\s+([^\n]+)\s+([\d-]+)\s+Phone",
        txt,
        re.S,
    )
    if m_owner:
        info["facility_owner"] = m_owner.group(1).strip()
        info["owner_address"] = f"{m_owner.group(2).strip()}, {m_owner.group(3).strip()}"
        info["owner_phone"] = m_owner.group(4).strip()

    m_cons = re.search(
        r"Project Consultant\s+(\d+)\s+DSHS License #\s+([^\n]+)\s+Name\s+([^\n]+)\s+Attention",
        txt,
        re.S,
    )
    if m_cons:
        info["consultant_license"] = m_cons.group(1).strip()
        info["project_consultant"] = m_cons.group(2).strip()
        info["consultant_contact"] = m_cons.group(3).strip()

    m_disp = re.search(
        r"Waste Disposal Site\s+([A-Z0-9-]+)\s+TCEQ Permit #\s+([^\n]+)\s+Name",
        txt,
        re.S,
    )
    if m_disp:
        info["tceq_permit"] = m_disp.group(1).strip()
        info["disposal_site"] = m_disp.group(2).strip()

    return info


def fetch_dshs_houston_notifications(
    start_from: datetime.date,
    start_to: datetime.date,
    by_addr: dict,
    *,
    return_status: bool = False,
):
    """
    Query Texas DSHS Online Asbestos Notification System for Houston (Harris County = 101,
    Houston = 25185) and extract full PDF details for any notification matching an Atlas parcel.
    Includes retry logic for transient Texas DSHS portal latency.
    """
    first_html = None
    opener = None
    last_err = ""
    for attempt in range(1, 4):
        cj = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(cj),
            urllib.request.HTTPSHandler(context=SSL_CTX),
        )
        opener.addheaders = [("User-Agent", "Mozilla/5.0")]
        try:
            opener.open(f"{DSHS_BASE_URL}/login.do", timeout=25)
            cookie_data = urllib.parse.urlencode(
                {"param_rsdIndex": "null", "source": "public", "continue": "Next"}
            ).encode()
            opener.open(f"{DSHS_BASE_URL}/checkCookies.do", data=cookie_data, timeout=25)
            opener.open(f"{DSHS_BASE_URL}/notificationSearchAction.do?from=loginPage", timeout=25)

            search_data = urllib.parse.urlencode(
                {
                    "param_rsdIndex": "null",
                    "licNumber": "",
                    "persDetail_name": "",
                    "facility_name": "",
                    "facility_streetNo": "",
                    "facility_physicalAddress": "",
                    "facility_county": "101",
                    "persDetail_state": "",
                    "facility_city": "25185",
                    "facility_zip": "",
                    "work_abtStartDateStrFrom": start_from.strftime("%m/%d/%Y"),
                    "work_abtStartDateStrTo": start_to.strftime("%m/%d/%Y"),
                    "work_abtEndDateStrFrom": "",
                    "work_abtEndDateStrTo": "",
                    "search": "Search",
                }
            ).encode()
            r = opener.open(f"{DSHS_BASE_URL}/notificationSearchAction.do", data=search_data, timeout=45)
            first_html = r.read().decode("utf-8", errors="ignore")
            break
        except Exception as e:
            last_err = str(e)
            print(f"  [WARN] DSHS search attempt {attempt}/3 failed: {e}", flush=True)

    if not first_html or opener is None:
        fail_msg = f"DSHS asbestos portal unreachable after 3 attempts ({last_err or 'empty response'})"
        return ([], True, fail_msg) if return_status else []

    pages_html = [first_html]
    page_urls = []
    for m in re.finditer(r'href=["\']([^"\']*notificationSearchAction\.do\?[^"\']*page=\d+[^"\']*)["\']', first_html):
        pu = m.group(1).replace("&amp;", "&")
        if pu not in page_urls:
            page_urls.append(pu)
    for pu in page_urls[:10]:
        full_pu = pu if pu.startswith("http") else f"{DSHS_BASE_URL}/{pu.lstrip('/')}"
        try:
            rp = opener.open(full_pu, timeout=15)
            pages_html.append(rp.read().decode("utf-8", errors="ignore"))
        except Exception:
            pass

    matched_notifications = []
    seen_notif_nums = set()

    for html in pages_html:
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S | re.I)
        for r_html in rows:
            m_link = re.search(r"(notificationPrintAction\.do\?licId=\d+)", r_html)
            if not m_link:
                continue
            cols = [
                re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c).replace("&nbsp;", " ")).strip()
                for c in re.findall(r"<td[^>]*>(.*?)</td>", r_html, re.S | re.I)
            ]
            if len(cols) < 5:
                continue
            notif_no, fac_name, fac_addr, start_dt_str, end_dt_str = cols[:5]
            if notif_no in seen_notif_nums:
                continue
            norm_a = normalize_address(fac_addr)
            if norm_a not in by_addr:
                continue
            seen_notif_nums.add(notif_no)

            def to_iso(mmddyyyy: str) -> str:
                m_d = re.match(r"^(\d{2})/(\d{2})/(\d{4})$", mmddyyyy.strip())
                return f"{m_d.group(3)}-{m_d.group(1)}-{m_d.group(2)}" if m_d else mmddyyyy

            item = {
                "notification_number": notif_no,
                "facility_name": fac_name,
                "raw_address": fac_addr,
                "norm_address": norm_a,
                "start_date": to_iso(start_dt_str),
                "end_date": to_iso(end_dt_str),
                "print_action_url": f"{DSHS_BASE_URL}/{m_link.group(1)}",
            }

            try:
                r_sum = opener.open(item["print_action_url"], timeout=15)
                b_sum = r_sum.read().decode("utf-8", errors="ignore")
                note_links = re.findall(
                    r'(notificationSearchResultAction\.do\?noteId=\d+)', b_sum
                )
                if note_links:
                    latest_note_url = f"{DSHS_BASE_URL}/{note_links[-1]}"
                    item["pdf_url"] = latest_note_url
                    r_pdf = opener.open(latest_note_url, timeout=15)
                    pdf_info = parse_dshs_pdf_text(r_pdf.read())
                    item.update(pdf_info)
            except Exception as e:
                print(f"  [WARN] Could not parse DSHS PDF for {notif_no}: {e}", flush=True)

            matched_notifications.append(item)

    if return_status:
        return matched_notifications, False, ""
    return matched_notifications


def is_interior_only_demo(use_desc: str) -> bool:
    """Return True if a permit description is strictly a non-structural interior partition/buildout remodel."""
    u = (use_desc or "").upper()
    if "NON-LOAD BEARING" in u or "NON LOAD BEARING" in u or "NONFIRE RATED" in u or "NON-FIRE RATED" in u:
        return True
    if "INTERIOR DEMO" in u and "BLD DEMO" not in u and "SEWER" not in u:
        return True
    if "SWIMMING POOL DEMOLITION" in u:
        return True
    return False


def is_garage_or_accessory_only_demo(use_desc: str) -> bool:
    """Return True if a demolition permit is explicitly restricted to a detached garage or accessory structure."""
    u = (use_desc or "").upper()
    if "GARAGE ONLY" in u or "(GARAGE" in u or "- GA" in u or "DETACHED GARAGE" in u or "CARPORT ONLY" in u:
        return True
    return False


def is_structural_or_sewer_demo(permit_desc: str, use_desc: str, permit_type: str) -> bool:
    """Return True if a City of Houston permit is a building demolition or sewer disconnection for demolition."""
    if is_interior_only_demo(use_desc):
        return False
    u = (use_desc or "").upper()
    pd = (permit_desc or "").upper()
    pt = (permit_type or "").upper()
    if pt in ("SD", "4D") or "DEMOLITION" in pd or "DGR BLDG DEMO" in pd:
        return True
    if "SEWER DISC" in u or "BLD DEMO" in u or "DEMO RES" in u or "DEMO BLD" in u or "RES DEMO" in u:
        return True
    if "D.B./RESIENTIAL/DEMO" in u or "DEMO/RESIDENTIAL" in u:
        return True
    return False


def classify_coh_demo_permit_stage(permit_desc: str, use_desc: str, permit_type: str) -> dict:
    """Classify a single City of Houston permit row into Stage 1 (Plumbing Sewer Disc) vs Stage 2 (Demolition).

    In City of Houston Sold Permits, a main-structure demolition requires two permits under the
    same 8-digit project number that both carry `use_desc = "DEMO RES/SEWER DISC"` (or `"BLD DEMO/SEWER DISC"`),
    distinguished by `permit_type` and `permit_desc`:
      - Stage 1 (`1 of 2`): `permit_type in ("12", "4P")` or `"PLUMB"` in `permit_desc`
        -> `"Sewer Disconnect Plumbing Permit (1 of 2)"`
      - Stage 2 (`2 of 2`): `permit_type in ("SD", "4D")` or `"DEMOLITION"` / `"DGR BLDG DEMO"` in `permit_desc`
        -> `"Building Demolition Permit (2 of 2)"`
    """
    u = (use_desc or "").upper()
    pd = (permit_desc or "").upper()
    pt = (permit_type or "").strip().upper()
    is_interior = is_interior_only_demo(use_desc)
    is_garage = is_garage_or_accessory_only_demo(use_desc)
    is_struct = is_structural_or_sewer_demo(permit_desc, use_desc, permit_type)

    is_demo_code = (
        not is_interior
        and not is_garage
        and (pt in ("SD", "4D") or "DEMOLITION" in pd or "DGR BLDG DEMO" in pd)
    )
    is_plumbing_code = (
        not is_interior
        and not is_garage
        and not is_demo_code
        and (
            pt in ("12", "4P")
            or "PLUMB" in pd
            or "PLBG" in pd
            or "SEWER DISC" in u
        )
    )

    if is_garage:
        pt_label = "Garage / Accessory Demolition Permit"
        stage_code = "accessory_only"
        demo_stage_badge = ""
    elif is_demo_code:
        pt_label = "Building Demolition Permit (2 of 2)"
        stage_code = "stage_2_demolition"
        demo_stage_badge = "2 of 2 · Building Demo"
    elif is_plumbing_code:
        pt_label = "Sewer Disconnect Plumbing Permit (1 of 2)"
        stage_code = "stage_1_sewer_plumbing"
        demo_stage_badge = "1 of 2 · Sewer Disconnect"
    elif is_struct:
        pt_label = "Building Demolition Permit (2 of 2)"
        stage_code = "stage_2_demolition"
        demo_stage_badge = "2 of 2 · Building Demo"
    else:
        pt_label = (permit_desc or "City Permit").strip()
        stage_code = "other"
        demo_stage_badge = ""

    return {
        "permit_type_label": pt_label,
        "permit_stage": stage_code,
        "demo_stage_badge": demo_stage_badge,
        "is_sewer_disconnect": bool(is_plumbing_code),
        "is_demo_permit_issued": bool(is_demo_code or (is_struct and not is_plumbing_code and not is_garage)),
        "is_garage_only": bool(is_garage),
        "is_structural_demo": bool(is_struct),
        "is_interior_only": bool(is_interior),
    }


def summarize_coh_demo_permit_stage_for_parcel(main_bld_coh: list[dict]) -> dict:
    """Summarize whether a parcel has Stage 1 (Plumbing 1 of 2), Stage 2 (Demolition 2 of 2), or both."""
    has_sewer_plumbing = any(
        bool(c.get("is_sewer_disconnect"))
        or str(c.get("permit_type") or "").upper() in ("12", "4P")
        or "PLUMB" in str(c.get("permit_desc") or "").upper()
        or "PLBG" in str(c.get("permit_desc") or "").upper()
        for c in main_bld_coh
    )
    has_demo_permit = any(
        bool(c.get("is_demo_permit_issued"))
        or str(c.get("permit_type") or "").upper() in ("SD", "4D")
        or "DEMOLITION" in str(c.get("permit_desc") or "").upper()
        or "DGR BLDG DEMO" in str(c.get("permit_desc") or "").upper()
        for c in main_bld_coh
    )
    if has_demo_permit and has_sewer_plumbing:
        demo_permit_stage = "stage_2_both"
    elif has_demo_permit:
        demo_permit_stage = "stage_2_demo_issued"
    elif has_sewer_plumbing:
        demo_permit_stage = "stage_1_sewer_only"
    else:
        demo_permit_stage = "none"
    return {
        "demo_permit_stage": demo_permit_stage,
        "has_sewer_disc_plumbing": bool(has_sewer_plumbing),
        "has_demo_permit": bool(has_demo_permit),
    }


def main():
    parser = argparse.ArgumentParser(description="Fetch live City of Houston permits & Texas DSHS asbestos filings.")
    parser.add_argument("--lookback-days", type=int, default=45, help="Days to look back for COH DEMO/SEWER permits.")
    parser.add_argument("--weekly-xlsx-count", type=int, default=4, help="Number of recent weekly XLSX files to ingest.")
    args = parser.parse_args()

    print("1. Loading Atlas buildings from", BUILDINGS_GEOJSON, flush=True)
    with open(BUILDINGS_GEOJSON, "r", encoding="utf-8") as f:
        gj_data = json.load(f)

    if enrich_kirby_mansion_metadata(gj_data):
        print("   Enriched 2000 Smith St (John Henry Kirby Mansion, HCAD 0152300000001) metadata.", flush=True)
        with open(BUILDINGS_GEOJSON, "w", encoding="utf-8") as f:
            json.dump(gj_data, f, separators=(",", ":"))
        update_search_index_for_kirby_mansion()

    by_addr = {}
    by_hcad = {}
    for feat in gj_data.get("features", []):
        p = feat.get("properties") or {}
        centroid = compute_feature_centroid(feat.get("geometry"))
        p_with_geom = dict(p)
        p_with_geom["_centroid"] = centroid
        p_with_geom["_has_footprint"] = True
        na = normalize_address(p.get("address", ""))
        if na:
            by_addr.setdefault(na, []).append(p_with_geom)
        hcad = str(p.get("hcad_num") or "").strip()
        if hcad:
            by_hcad[hcad] = p_with_geom

    # Also index curated_overrides.json so campus/landmark overrides (including recently demolished landmarks) have exact polygon centroids
    overrides_json_path = REPO_ROOT / "app" / "public" / "data" / "curated_overrides.json"
    demolished_overrides_by_hcad: dict[str, dict] = {}
    if overrides_json_path.exists():
        with open(overrides_json_path, "r", encoding="utf-8") as f:
            ov_raw = json.load(f)
        ov_map = ov_raw.get("overrides", ov_raw) if isinstance(ov_raw, dict) else {}
        demolished_hcads_set = extract_curated_demolished_hcads(ov_raw)
        for ov_key, ov in ov_map.items():
            if not isinstance(ov, dict):
                continue
            hcad = str(ov.get("hcad_num") or str(ov_key).split("#")[0]).strip()
            if hcad in demolished_hcads_set:
                demolished_overrides_by_hcad[hcad] = ov
            if ov.get("suppress_only") and hcad not in demolished_hcads_set:
                continue
            bid = str(ov.get("building_id") or ov.get("id") or ov_key).strip()
            addr = ov.get("address") or ""
            na = normalize_address(addr)
            geom_obj = ov.get("geometry") or ov.get("_pre_suppress_geometry")
            c = compute_feature_centroid(geom_obj) if geom_obj else None
            if not c or (abs(c[0] - (-95.3698)) < 1e-4 and abs(c[1] - 29.7604) < 1e-4):
                continue
            rec = {
                "id": bid,
                "building_id": bid,
                "hcad_num": hcad,
                "address": addr,
                "year_built": ov.get("year_built") or ov.get("original_hcad_year") or 0,
                "landmark_name": ov.get("landmark_name") or ov.get("building_name") or "",
                "building_name": ov.get("building_name") or ov.get("landmark_name") or "",
                "historic_district": ov.get("historic_district") or "",
                "neighborhood": ov.get("neighborhood") or "",
                "good_brick_summary": ov.get("good_brick_summary") or "",
                "style": ov.get("style") or "",
                "architect": ov.get("architect") or "",
                "owner": ov.get("owner") or "",
                "_centroid": c,
                "_has_footprint": True,
            }
            if ov_key not in by_hcad:
                by_hcad[ov_key] = rec
            if hcad and hcad not in by_hcad:
                by_hcad[hcad] = rec
            if na:
                by_addr.setdefault(na, []).append(rec)

    # Also index search_index.json so all 26,419 countywide structures are covered
    if SEARCH_INDEX_JSON.exists():
        with open(SEARCH_INDEX_JSON, "r", encoding="utf-8") as f:
            s_idx = json.load(f)
        s_items = s_idx if isinstance(s_idx, list) else s_idx.get("items", [])
        for item in s_items:
            hcad = str(item.get("hcad_num") or item.get("h") or "").strip()
            if hcad and hcad not in by_hcad:
                addr = item.get("address") or item.get("a") or ""
                na = normalize_address(addr)
                lng = item.get("lng") or item.get("lon") or item.get("x") or -95.3698
                lat = item.get("lat") or item.get("y") or 29.7604
                rec = {
                    "id": item.get("id") or f"bld_{hcad}",
                    "hcad_num": hcad,
                    "address": addr,
                    "year_built": item.get("year_built") or item.get("y") or 0,
                    "landmark_name": item.get("landmark_name") or item.get("l") or "",
                    "historic_district": item.get("historic_district") or item.get("d") or "",
                    "neighborhood": item.get("neighborhood") or item.get("n") or "",
                    "good_brick_summary": item.get("good_brick_summary") or "",
                    "style": item.get("style") or "",
                    "architect": item.get("architect") or "",
                    "_centroid": [round(float(lng), 6), round(float(lat), 6)],
                    "_has_footprint": False,
                }
                by_hcad[hcad] = rec
                if na:
                    by_addr.setdefault(na, []).append(rec)

    print(f"   Indexed {len(by_addr):,} normalized street addresses across {len(by_hcad):,} structures.", flush=True)

    today = datetime.date.today()
    bdt = today - datetime.timedelta(days=args.lookback_days)
    print(f"2. Querying City of Houston WebFOCUS Sold Permits ({bdt} to {today})...", flush=True)
    combined_coh, coh_wf_failed, coh_wf_err = fetch_coh_permits_parallel(
        lookback_days=args.lookback_days, return_status=True
    )
    print(f"   Retrieved {len(combined_coh):,} unique City of Houston DEMO / SD permit records.", flush=True)

    # Filter to rows that match an Atlas building first, then fetch details in parallel
    candidate_coh = [r for r in combined_coh.values() if r["norm_address"] in by_addr]
    print(f"   Matched {len(candidate_coh)} COH permit records to Atlas addresses; fetching drilldown details...", flush=True)

    matched_coh_by_hcad = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        detail_futs = {
            pool.submit(fetch_coh_permit_detail, r["project_no"], r.get("permit_type", "")): r
            for r in candidate_coh
        }
        for fut in concurrent.futures.as_completed(detail_futs):
            r = detail_futs[fut]
            detail = fut.result()
            na = r["norm_address"]
            use_d = detail.get("use_desc") or r.get("use_desc", "")
            stage_meta = classify_coh_demo_permit_stage(
                r.get("permit_desc", ""),
                use_d,
                r.get("permit_type") or detail.get("permit_type", ""),
            )

            merged_permit = {
                "project_no": r["project_no"],
                "date": detail.get("date") or today.strftime("%Y-%m-%d"),
                "permit_type": r.get("permit_type") or detail.get("permit_type", ""),
                "permit_type_label": stage_meta["permit_type_label"],
                "permit_stage": stage_meta["permit_stage"],
                "permit_desc": r.get("permit_desc", ""),
                "use_desc": use_d,
                "fcc_group": detail.get("fcc_group", ""),
                "owner_occupant": detail.get("owner_occupant") or r.get("owner_occupant", ""),
                "buyer": detail.get("buyer", ""),
                "buyer_address": detail.get("buyer_address", ""),
                "valuation": detail.get("valuation_formatted") or r.get("valuation", ""),
                "job_address": detail.get("job_address") or r.get("raw_address", ""),
                "is_sewer_disconnect": stage_meta["is_sewer_disconnect"],
                "is_demo_permit_issued": stage_meta["is_demo_permit_issued"],
                "is_garage_only": stage_meta["is_garage_only"],
                "is_structural_demo": stage_meta["is_structural_demo"],
                "is_interior_only": stage_meta["is_interior_only"],
            }
            for bld in by_addr[na]:
                hcad = str(bld.get("hcad_num") or bld.get("id") or "")
                matched_coh_by_hcad.setdefault(hcad, []).append(merged_permit)

    print(f"3. Ingesting recent weekly Sold Permits .xlsx spreadsheets ({args.weekly_xlsx_count} weeks)...", flush=True)
    weekly_rows, weekly_failed, weekly_err = fetch_recent_weekly_xlsx_permits(
        max_files=args.weekly_xlsx_count, return_status=True
    )
    coh_fetch_failed = bool(coh_wf_failed or (coh_wf_err and weekly_failed))
    coh_fetch_error = "; ".join(x for x in (coh_wf_err, weekly_err if weekly_failed else "") if x)
    weekly_matched_by_hcad = {}
    for r in weekly_rows:
        na = r["norm_address"]
        if na not in by_addr:
            continue
        for bld in by_addr[na]:
            hcad = str(bld.get("hcad_num") or bld.get("id") or "")
            weekly_matched_by_hcad.setdefault(hcad, []).append(r)

    print("4. Querying Texas DSHS Asbestos Abatement & Demolition Notifications for Houston...", flush=True)
    dshs_start = today - datetime.timedelta(days=45)
    dshs_end = today + datetime.timedelta(days=90)
    dshs_matched, dshs_fetch_failed, dshs_fetch_error = fetch_dshs_houston_notifications(
        dshs_start, dshs_end, by_addr, return_status=True
    )
    print(f"   Matched {len(dshs_matched)} Texas DSHS notifications to Atlas structures.", flush=True)

    dshs_by_hcad = {}
    for item in dshs_matched:
        na = item["norm_address"]
        for bld in by_addr.get(na, []):
            hcad = str(bld.get("hcad_num") or bld.get("id") or "")
            dshs_by_hcad.setdefault(hcad, []).append(item)

    # Carry forward active COH permits from existing ALERTS_OUTPUT_JSON if upstream COH portal timed out
    if coh_fetch_failed and not matched_coh_by_hcad and ALERTS_OUTPUT_JSON.exists():
        try:
            with open(ALERTS_OUTPUT_JSON, "r", encoding="utf-8") as f:
                prev_alerts_coh = json.load(f).get("alerts", [])
            coh_cutoff_iso = bdt.strftime("%Y-%m-%d")
            carried_coh = 0
            for pa in prev_alerts_coh:
                ph = str(pa.get("hcad_num") or "")
                for cp in pa.get("coh_permits") or []:
                    if str(cp.get("date") or "9999") >= coh_cutoff_iso:
                        matched_coh_by_hcad.setdefault(ph, []).append(cp)
                        carried_coh += 1
            if carried_coh:
                print(
                    f"   Carried forward {carried_coh} active COH permits from previous snapshot (upstream degraded).",
                    flush=True,
                )
        except Exception:
            pass

    # Carry forward active DSHS notifications from existing ALERTS_OUTPUT_JSON if upstream DSHS portal timed out
    if not dshs_by_hcad and ALERTS_OUTPUT_JSON.exists():
        try:
            with open(ALERTS_OUTPUT_JSON, "r", encoding="utf-8") as f:
                prev_alerts = json.load(f).get("alerts", [])
            cutoff_iso = (today - datetime.timedelta(days=30)).strftime("%Y-%m-%d")
            carried = 0
            for pa in prev_alerts:
                ph = str(pa.get("hcad_num") or "")
                for dn in pa.get("dshs_notifications") or []:
                    if str(dn.get("end_date") or "9999") >= cutoff_iso:
                        dshs_by_hcad.setdefault(ph, []).append(dn)
                        carried += 1
            if carried:
                print(f"   Carried forward {carried} active DSHS notifications from previous snapshot.", flush=True)
        except Exception:
            pass

    all_flagged_hcads = sorted(set(matched_coh_by_hcad.keys()) | set(dshs_by_hcad.keys()))
    alerts_list = []

    for hcad in all_flagged_hcads:
        bld = by_hcad.get(hcad)
        if not bld:
            continue
        coh_list = matched_coh_by_hcad.get(hcad, [])
        dshs_list = dshs_by_hcad.get(hcad, [])

        dedup_coh = []
        seen_c = set()
        for c in sorted(coh_list, key=lambda x: x.get("date", ""), reverse=True):
            k = (c["project_no"], c["permit_desc"])
            if k not in seen_c:
                seen_c.add(k)
                dedup_coh.append(c)

        structural_coh = [c for c in dedup_coh if c["is_structural_demo"] and not c["is_interior_only"]]
        main_bld_coh = [c for c in structural_coh if not c.get("is_garage_only")]
        garage_only_coh = [c for c in structural_coh if c.get("is_garage_only")]
        has_structural_or_sewer = len(structural_coh) > 0
        has_main_bld_demo = len(main_bld_coh) > 0
        stage_summary = summarize_coh_demo_permit_stage_for_parcel(main_bld_coh)
        has_sewer_disc = stage_summary["has_sewer_disc_plumbing"]
        has_demo_permit = stage_summary["has_demo_permit"]
        demo_permit_stage = stage_summary["demo_permit_stage"]
        has_dshs_demo = any("DEMOLITION" in (d.get("work_type") or "").upper() for d in dshs_list)
        has_dshs = len(dshs_list) > 0

        if not has_structural_or_sewer and not has_dshs:
            continue

        yr = int(bld.get("year_built") or bld.get("eff_year_built") or 0)
        if has_main_bld_demo or has_dshs_demo or (has_sewer_disc and has_dshs):
            severity = "critical"
        else:
            severity = "watch"

        primary_coh = (
            main_bld_coh[0]
            if main_bld_coh
            else (garage_only_coh[0] if garage_only_coh else (dedup_coh[0] if dedup_coh else None))
        )

        if has_demo_permit and has_sewer_disc and has_dshs and primary_coh:
            severity_label = "Critical Alert: Both Demo Permits (1/2 Plumbing + 2/2 Demolition) + Asbestos Abatement"
            badge_short = "2/2 Demo + Asbestos"
            headline = (
                f"Both required City of Houston demolition permits pulled (#{primary_coh['project_no']}, "
                f"1/2 Plumbing Permit + 2/2 Demolition Permit, latest {primary_coh['date']}) "
                f"paired with Texas DSHS asbestos abatement filing (#{dshs_list[0]['notification_number']}, "
                f"scheduled {dshs_list[0]['start_date']} to {dshs_list[0]['end_date']})."
            )
        elif has_sewer_disc and has_dshs and primary_coh:
            severity_label = "Critical Alert: Stage 1 Sewer Disconnection (1 of 2) + Asbestos Abatement"
            badge_short = "1/2 Sewer Disc + Asbestos"
            headline = (
                f"City sewer disconnection plumbing permit (1 of 2, #{primary_coh['project_no']}, issued {primary_coh['date']}) "
                f"paired with Texas DSHS asbestos abatement filing (#{dshs_list[0]['notification_number']}, "
                f"scheduled {dshs_list[0]['start_date']} to {dshs_list[0]['end_date']})."
            )
        elif has_dshs_demo:
            severity_label = "Critical Alert: Texas DSHS Demolition Notification"
            badge_short = "DSHS Demolition Filing"
            headline = (
                f"Texas DSHS demolition notification (#{dshs_list[0]['notification_number']}) "
                f"scheduled {dshs_list[0]['start_date']} to {dshs_list[0]['end_date']}."
            )
        elif has_demo_permit and has_sewer_disc and primary_coh:
            severity_label = "Critical Alert: Both Permits Pulled (1/2 Plumbing Sewer Disc + 2/2 Building Demolition)"
            badge_short = "2 of 2 Permits: Sewer + Demo"
            headline = (
                f"Both required City of Houston demolition permits issued (#{primary_coh['project_no']}, "
                f"1/2 Plumbing Permit + 2/2 Demolition Permit, latest {primary_coh['date']}: {primary_coh['use_desc']})."
            )
        elif has_demo_permit and primary_coh:
            severity_label = "Critical Alert: Stage 2 Building Demolition Permit Issued (2 of 2)"
            badge_short = "Stage 2: Demo Permit Issued"
            headline = (
                f"City of Houston Stage 2 building demolition permit (#{primary_coh['project_no']}, "
                f"issued {primary_coh['date']}: {primary_coh['use_desc']})."
            )
        elif has_sewer_disc and primary_coh:
            severity_label = "Critical Alert: Stage 1 Prerequisite Sewer Disconnection Plumbing Permit (1 of 2)"
            badge_short = "1 of 2 Permits: Sewer Disc Only"
            headline = (
                f"City of Houston Stage 1 sewer disconnection plumbing permit (1 of 2, #{primary_coh['project_no']}, "
                f"issued {primary_coh['date']}: {primary_coh['use_desc']}); Stage 2 demolition permit not yet pulled."
            )
        elif garage_only_coh and primary_coh:
            severity_label = "Active Watch: Garage / Accessory Demolition Permit"
            badge_short = "Garage Demo Only"
            headline = (
                f"City of Houston accessory/garage demolition permit (#{primary_coh['project_no']}, "
                f"issued {primary_coh['date']}: {primary_coh['use_desc']})."
            )
        elif has_dshs:
            wt = dshs_list[0].get("work_type") or "Asbestos Abatement"
            wt_clean = wt.lower().replace("notification", "").strip()
            severity_label = f"Active Watch: Texas DSHS {wt} Filing"
            badge_short = f"DSHS {wt}"
            headline = (
                f"Texas DSHS {wt_clean} notification (#{dshs_list[0]['notification_number']}) "
                f"scheduled {dshs_list[0]['start_date']} to {dshs_list[0]['end_date']}."
            )
        else:
            severity_label = "Active Permit Watch"
            badge_short = "Active Permit Watch"
            headline = "Recent permit activity recorded on this historic parcel."

        latest_date = max(
            [c.get("date", "") for c in structural_coh]
            + [d.get("filing_date") or d.get("start_date", "") for d in dshs_list]
            + [""]
        )

        centroid = bld.get("_centroid") or [-95.3698, 29.7604]
        b_name = bld.get("landmark_name") or bld.get("building_name") or ""
        if hcad == "0152300000001":
            b_name = "John Henry Kirby Mansion"
        title = b_name or bld.get("address") or f"HCAD {hcad}"

        preservation_note = ""
        if hcad == "0152300000001":
            preservation_note = KIRBY_MANSION_PRESERVATION_NOTE

        demo_ov = demolished_overrides_by_hcad.get(hcad) or {}
        is_confirmed_demo = bool(
            hcad == "0152300000001"
            or demo_ov.get("is_demolished")
            or demo_ov.get("demolished_date")
            or demo_ov.get("lost_houston_id")
        )
        demolished_date_str = str(
            demo_ov.get("demolished_date") or ("2026-11-02" if hcad == "0152300000001" else "")
        ).strip()
        lost_id_str = str(
            demo_ov.get("lost_houston_id") or ("lost_kirby_mansion_1894" if hcad == "0152300000001" else "")
        ).strip()
        if is_confirmed_demo:
            badge_short, severity_label = format_demolished_permit_badge_and_severity(
                demolished_date_str
            )
            if hcad == "0152300000001":
                headline = KIRBY_MANSION_PERMIT_HEADLINE

        alert_obj = {
            "hcad_num": hcad,
            "building_id": bld.get("id") or bld.get("building_id") or "",
            "title": title,
            "building_name": b_name,
            "address": bld.get("address") or "",
            "year_built": yr,
            "yr_remodel": bld.get("yr_remodel") or 0,
            "style": bld.get("style") or "",
            "architect": bld.get("architect") or "",
            "neighborhood": bld.get("neighborhood") or bld.get("super_neighborhood") or "",
            "historic_district": bld.get("historic_district") or "",
            "landmark_name": bld.get("landmark_name") or b_name,
            "good_brick_summary": bld.get("good_brick_summary") or "",
            "owner": bld.get("owner") or "",
            "lng": centroid[0],
            "lon": centroid[0],
            "lat": centroid[1],
            "severity": severity,
            "severity_label": severity_label,
            "badge": badge_short,
            "headline": headline,
            "demo_permit_stage": demo_permit_stage,
            "has_sewer_disc_plumbing": has_sewer_disc,
            "has_demo_permit": has_demo_permit,
            "demolition_status": "demolished" if is_confirmed_demo else "active_watch",
            "is_demolished": is_confirmed_demo,
            "demolished_date": demolished_date_str,
            "year_demolished": int(demolished_date_str[:4]) if len(demolished_date_str) >= 4 and demolished_date_str[:4].isdigit() else (2026 if is_confirmed_demo else None),
            "lost_houston_id": lost_id_str,
            "preservation_note": preservation_note,
            "latest_filing_date": latest_date,
            "coh_permits": structural_coh if structural_coh else dedup_coh,
            "dshs_notifications": dshs_list,
            "_has_footprint": bool(bld.get("_has_footprint")),
        }
        alerts_list.append(alert_obj)

    alerts_list.sort(
        key=lambda a: (
            0 if a["severity"] == "critical" else 1,
            0 if (a["landmark_name"] or a["good_brick_summary"]) else 1,
            0 if a.get("_has_footprint") else 1,
            a["year_built"] if a["year_built"] >= 1836 else 9999,
        )
    )

    # Deduplicate multi-building campus/lot addresses into a single primary alert entry
    # while preserving all related_hcads so every footprint on the site gets the Inspector banner
    dedup_alerts = []
    by_norm_addr_alert = {}
    for a in alerts_list:
        a.pop("_has_footprint", None)
        na = normalize_address(a["address"])
        if na and na in by_norm_addr_alert:
            primary = by_norm_addr_alert[na]
            if a["hcad_num"] not in primary["related_hcads"]:
                primary["related_hcads"].append(a["hcad_num"])
            if a["building_id"] and a["building_id"] not in primary["related_building_ids"]:
                primary["related_building_ids"].append(a["building_id"])
        else:
            a["related_hcads"] = [a["hcad_num"]]
            a["related_building_ids"] = [a["building_id"]] if a["building_id"] else []
            dedup_alerts.append(a)
            if na:
                by_norm_addr_alert[na] = a
    alerts_list = dedup_alerts

    existing_benchmarks = None
    if ALERTS_OUTPUT_JSON.exists():
        try:
            with open(ALERTS_OUTPUT_JSON, "r", encoding="utf-8") as f:
                existing_benchmarks = json.load(f).get("annual_benchmarks")
        except Exception:
            existing_benchmarks = None

    annual_benchmarks = build_annual_demolition_benchmarks(existing_benchmarks)

    now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    alerts_payload = {
        "updated_at": now_iso,
        "generated_at": now_iso,
        "lookback_days": args.lookback_days,
        "coh_fetch_failed": bool(coh_fetch_failed),
        "dshs_fetch_failed": bool(dshs_fetch_failed),
        "coh_fetch_error": coh_fetch_error,
        "dshs_fetch_error": dshs_fetch_error,
        "summary": {
            "total_flagged_buildings": len(alerts_list),
            "critical_count": sum(1 for a in alerts_list if a["severity"] == "critical"),
            "watch_count": sum(1 for a in alerts_list if a["severity"] == "watch"),
            "coh_permits_matched": sum(len(a["coh_permits"]) for a in alerts_list),
            "dshs_notifications_matched": sum(len(a["dshs_notifications"]) for a in alerts_list),
        },
        "annual_benchmarks": annual_benchmarks,
        "alerts": alerts_list,
    }

    with open(ALERTS_OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(alerts_payload, f, indent=2)
    print(f"5. Wrote {len(alerts_list)} building alerts to {ALERTS_OUTPUT_JSON}", flush=True)

    all_hcads_to_update = set(matched_coh_by_hcad.keys()) | set(weekly_matched_by_hcad.keys()) | set(dshs_by_hcad.keys())
    by_prefix = {}
    for hcad in all_hcads_to_update:
        clean_h = re.sub(r"\D", "", str(hcad))
        if len(clean_h) >= 10:
            by_prefix.setdefault(clean_h[:3], []).append(clean_h)

    shards_updated = 0
    for prefix3, hcads in by_prefix.items():
        gz_path = PERMITS_DIR / f"permits_{prefix3}.json.gz"
        shard_data = read_json_or_gzip_shard(PERMITS_DIR, f"permits_{prefix3}")

        shard_modified = False
        for hcad in hcads:
            rec = shard_data.get(hcad) or {}
            p_list = list(rec.get("p") or [])
            existing_ids = {str(p.get("id") or "") for p in p_list}

            for cp in matched_coh_by_hcad.get(hcad, []):
                pid = str(cp["project_no"])
                if pid and pid not in existing_ids:
                    cat = "demo" if cp["is_structural_demo"] else "remodel"
                    tp_label = (
                        "Sewer Disconnect / Demo Prep"
                        if cp["is_sewer_disconnect"]
                        else ("Demolition Permit" if cp["is_structural_demo"] else (cp["permit_desc"] or "City Permit"))
                    )
                    desc_parts = [cp["use_desc"]]
                    if cp.get("fcc_group"):
                        desc_parts.append(f"FCC: {cp['fcc_group']}")
                    if cp.get("buyer"):
                        desc_parts.append(f"Contractor: {cp['buyer']}")
                    if cp.get("owner_occupant"):
                        desc_parts.append(f"Applicant/Occupant: {cp['owner_occupant']}")
                    p_list.insert(
                        0,
                        {
                            "id": pid,
                            "dt": cp["date"],
                            "cat": cat,
                            "tp": tp_label,
                            "d": " · ".join(x for x in desc_parts if x),
                            "src": "coh_live",
                        },
                    )
                    existing_ids.add(pid)
                    shard_modified = True

            for wp in weekly_matched_by_hcad.get(hcad, []):
                pid = str(wp["project_no"])
                if pid and pid not in existing_ids:
                    is_demo = is_structural_or_sewer_demo(wp["permit_desc"], wp["use_desc"], "")
                    cat = "demo" if is_demo else "remodel"
                    p_list.insert(
                        0,
                        {
                            "id": pid,
                            "dt": wp["date"],
                            "cat": cat,
                            "tp": wp["permit_desc"] or "City Permit",
                            "d": wp["use_desc"],
                            "src": "coh_weekly",
                        },
                    )
                    existing_ids.add(pid)
                    shard_modified = True

            for dn in dshs_by_hcad.get(hcad, []):
                nid = f"DSHS-{dn['notification_number']}"
                if nid not in existing_ids:
                    wt = dn.get("work_type") or "Asbestos Notification"
                    desc_parts = [
                        f"Scheduled {dn['start_date']} to {dn['end_date']}",
                    ]
                    if dn.get("work_description"):
                        desc_parts.append(dn["work_description"])
                    if dn.get("abatement_contractor"):
                        desc_parts.append(f"Abatement Contractor: {dn['abatement_contractor']}")
                    if dn.get("facility_owner"):
                        desc_parts.append(f"Facility Owner: {dn['facility_owner']}")
                    p_list.insert(
                        0,
                        {
                            "id": nid,
                            "dt": dn.get("filing_date") or dn["start_date"],
                            "cat": "demo" if "DEMOLITION" in wt.upper() else "asbestos",
                            "tp": f"Texas DSHS {wt}",
                            "d": " · ".join(desc_parts),
                            "src": "tx_dshs",
                        },
                    )
                    existing_ids.add(nid)
                    shard_modified = True

            if hcad == "0152300000001" and (rec.get("yr") != 1894 or rec.get("remod") != 1928):
                rec["yr"] = 1894
                rec["remod"] = 1928
                shard_modified = True

            if shard_modified:
                p_list.sort(key=lambda x: str(x.get("dt") or ""), reverse=True)
                rec["p"] = p_list
                shard_data[hcad] = rec

        if shard_modified:
            write_deterministic_gzip_json_shard(
                gz_path,
                shard_data,
                unlink_legacy_json=True,
                sanitize_dashes=True,
            )
            shards_updated += 1

    print(f"6. Updated {shards_updated} permit shard(s) in {PERMITS_DIR}", flush=True)
    for a in alerts_list:
        print(
            f"   [{a['severity'].upper()}] {a['address']} ({a['title']}, built {a['year_built']}, HCAD {a['hcad_num']}): {a['headline']}",
            flush=True,
        )


if __name__ == "__main__":
    main()
