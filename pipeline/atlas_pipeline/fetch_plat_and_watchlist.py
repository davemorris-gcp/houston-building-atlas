#!/usr/bin/env python3
"""Automated City of Houston Planning Commission Plat, Variance & Multi-Agency Watchlist Pipeline.

Integrates Preservation Houston's two preservation monitoring workflows into the Houston Building Atlas:
1. Biweekly Planning Commission Plat & Variance Review (Multi-Cycle):
   - Discovers and ingests up to the 3 most recent 'Plat-Current-Agenda-Spreadsheet-MM-DD-YYYY.xlsx'
     agendas from houstontx.gov alongside their matching 'PC-Draft-Agenda-Outline-MMDDYY.pdf' outlines
   - Extracts Section K Development Plat Variances (DPV), dynamic Section III/IV future Public Hearing
     dates, agenda item numbers, and deferral badges (DEF1/DEF2)
   - Separates Public Hearing Notice PDFs (noticePdf) from submittal plat drawings (pdf) so notice-only
     items never mislabel a 2-page hearing notice as a Plat PDF
   - Links deferred and withdrawn/resubmitted applications across cycles via strict continuity keys
   - Aggregates multi-building and multi-HCAD construction years using minimum valid nonzero
     year_built >= 1836 with curated_overrides.json precedence
2. Endangered Property Multi-Agency Watchlist & 15-Point Dossier:
   - Pulls staff-confirmed watched properties and snowball search terms from Plat_&_Watchlist_Sync
   - Sweeps 8 public Open Data endpoints automatically using parameterized, Harris-County-scoped queries:
     * Check #4: TX Comptroller Active Sales Tax (Socrata jrea-zgmq) & Franchise Tax (9cir-efmm)
     * Check #5: HCAD Ownership & Classification (HCAD ArcGIS REST public_query/MapServer)
     * Check #6: TABC Pending Applications (Socrata mxm5-tdpj) & Active Licenses (7hf9-qc9f)
     * Check #9: COH Planning Commission Plats, Section K Variances & Public Hearing Notices
     * Check #10: TDLR Architectural Barriers TABS (Harris County LocationCounty=2101 / HARRIS)
     * Check #13: Houston 311 Code Complaints & Dangerous Buildings (ArcGIS FeatureServer)
     * Check #14: COH Sold Permits (harvesting 8-digit ILMS plan review project numbers)
     * Check #15: TX DSHS Asbestos & Demolition Notifications
   - Strictly uses Python 3.12 standard-library modules so unattended GitHub Actions cron runs
     (.github/workflows/update_live_permits.yml) execute without third-party dependencies.
"""

from __future__ import annotations

import csv
import datetime as dt
import glob
import html
import io
import json
import math
import re
import subprocess
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "app" / "public" / "data"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) HoustonBuildingAtlas/1.0"

DEV_REPORTS_URL = "https://www.houstontx.gov/planning/DevelopRegs/dev_reports.html"
COMMISS_PLAN_URL = "https://www.houstontx.gov/planning/Commissions/commiss_plan.html"
NOTICES_URL = "https://www.houstontx.gov/planning/notices/"

SHEET_PUBLISHED_CSV_URL = (
    "https://docs.google.com/spreadsheets/d/e/"
    "2PACX-1vS6oiB-T1JH-oy8coeDD1Gqey_l9rP7Its3NWe7KerNQyBF_Mqrx1NHPLWV8yKTtgS0CTXoJySi3jcO/"
    "pub?gid=1876369840&single=true&output=csv"
)
AUTO_SWEEP_SCHEDULE = "Daily at 7:00 AM CT"
PLAT_CACHE_DIR = REPO_ROOT / "pipeline" / "cache" / "plat_spreadsheets_2026"

REMOVED_TIER3_TOMBSTONE = "Removed (Tier 3)"
REMOVED_WATCHLIST_TOMBSTONE = "Removed from Watchlist"
WATCHLIST_TOMBSTONE_STATUSES = {
    REMOVED_TIER3_TOMBSTONE,
    REMOVED_WATCHLIST_TOMBSTONE,
}
WATCHLIST_TRACKER_STATUSES = {
    "Active / High Priority",
    "Monitoring",
    "Dormant / Medium Priority",
    "For Sale",
    "Saved",
    "Lost",
    "Archived (Tier 3)",
}
WATCHLIST_ALL_VALID_STATUSES = WATCHLIST_TRACKER_STATUSES | WATCHLIST_TOMBSTONE_STATUSES
CARRY_FORWARD_REVIEW_FIELDS = (
    "hcad",
    "address",
    "yearBuilt",
    "designation",
    "tier",
    "threatCategory",
    "why",
    "sources",
    "followup",
)
RETRACTED_BASELINE_PLAT_SEEDS = {
    "DPV-26073533": {"2603 MILAM ST 15", "2603 MILAM"},
    "2026-1585": {"1226 W CLAY ST", "MONTROSE RESIDENCES"},
}

UTILITY_RE = re.compile(
    r"lift station|water treatment|water plant|wastewater|ground water|utility|detention only",
    re.I,
)
INST_RE = re.compile(
    r"\b(church|chapel|school|temple|mosque|synagogue|theat(?:er|re)|lodge|hospital|clinic|cemetery)\b",
    re.I,
)

OLDER_RING_SUPER_N = {
    "ACRES HOME",
    "CENTRAL NORTHWEST",
    "DENVER HARBOR / PORT HOUSTON",
    "DOWNTOWN",
    "EAST END",
    "EAST HOUSTON",
    "EAST LITTLE YORK / HOMESTEAD",
    "EASTEX - JENSEN AREA",
    "FOURTH WARD",
    "GREATER EASTWOOD",
    "GREATER FIFTH WARD",
    "GREATER HEIGHTS",
    "GREATER OST / SOUTH UNION",
    "GREATER THIRD WARD",
    "GREATER UPTOWN",
    "GULFGATE RIVERVIEW / PINE VALLEY",
    "INDEPENDENCE HEIGHTS",
    "KASHMERE GARDENS",
    "LAWNDALE / WAYSIDE",
    "MACGREGOR",
    "MAGNOLIA PARK",
    "MEMORIAL",
    "MIDTOWN",
    "MUSEUM PARK",
    "NEAR NORTHSIDE",
    "NEARTOWN - MONTROSE",
    "NORTHSIDE/NORTHLINE",
    "NORTHSIDE VILLAGE",
    "PARK PLACE",
    "PECAN PARK",
    "SECOND WARD",
    "SETTEGAST",
    "SOUTH ACRES / CRESTMONT PARK",
    "SOUTH PARK",
    "SPRING BRANCH CENTRAL",
    "SPRING BRANCH EAST",
    "SPRING BRANCH WEST",
    "SUNNYSIDE",
    "TRINITY / HOUSTON GARDENS",
    "UNIVERSITY PLACE",
    "WASHINGTON AVENUE COALITION / MEMORIAL PARK",
}

GENERIC_STANDALONE_PLAT_NAMES = {
    "RESERVE",
    "RESERVE A",
    "RESERVE B",
    "COMMERCIAL RESERVE",
    "UNRESTRICTED RESERVE",
    "TRACT",
    "TRACT 1",
    "TRACT A",
    "REPLAT",
    "PARTIAL REPLAT",
    "AMENDING PLAT",
    "DEVELOPMENT PLAT",
    "ESTATES",
    "PARK",
    "PLACE",
    "GARDENS",
    "ACRES",
    "VILLAGE",
    "HEIGHTS",
    "SQUARE",
    "COURT",
    "MANOR",
    "TERRACE",
    "OAKS",
    "WOODS",
    "HILLS",
    "VILLAS",
    "CENTER",
    "PLAZA",
    "CROSSING",
    "COMMONS",
    "GROVE",
}

STREET_TYPE_SUFFIXES = {
    "ST",
    "STREET",
    "AVE",
    "AVENUE",
    "RD",
    "ROAD",
    "DR",
    "DRIVE",
    "BLVD",
    "BOULEVARD",
    "LN",
    "LANE",
    "CT",
    "COURT",
    "PL",
    "PLACE",
    "WAY",
    "PKWY",
    "PARKWAY",
    "CIR",
    "CIRCLE",
    "TRL",
    "TRAIL",
    "TER",
    "TERRACE",
    "FWY",
    "FREEWAY",
    "EXPY",
    "EXPRESSWAY",
    "SQ",
    "SQUARE",
    "PLZ",
    "PLAZA",
    "ALY",
    "ALLEY",
    "PATH",
    "ROW",
    "WALK",
    "CV",
    "COVE",
    "PT",
    "POINT",
}

DIRECTIONAL_TOKENS = {
    "N",
    "S",
    "E",
    "W",
    "NE",
    "NW",
    "SE",
    "SW",
    "NORTH",
    "SOUTH",
    "EAST",
    "WEST",
}

MONTH_NAME_MAP = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}


def fetch_bytes(
    url: str,
    timeout: int = 20,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> bytes | None:
    req_headers = {"User-Agent": USER_AGENT}
    if headers:
        req_headers.update(headers)
    try:
        req = urllib.request.Request(url, data=data, headers=req_headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception as exc:
        print(f"[warn] Could not fetch {url}: {exc}")
        return None


def pdf_to_text(pdf_bytes: bytes, layout: bool = True) -> str:
    cmd = ["pdftotext"]
    if layout:
        cmd.append("-layout")
    cmd.extend(["-", "-"])
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        out, _ = proc.communicate(pdf_bytes, timeout=20)
        return out.decode("utf-8", errors="ignore")
    except Exception as exc:
        print(f"[warn] pdftotext failed: {exc}")
        return ""


def excel_serial_to_iso(val: str | int | float | None) -> str:
    """Convert an Excel 1900-system serial or calendar string into YYYY-MM-DD.

    Why math.floor(n): City of Houston Planning Commission spreadsheets encode 2:30 PM
    afternoon meeting times in the fractional serial (e.g. 46310.6041666667 = Oct 15, 2026
    at 14:30). Rounding shifts afternoon serials (>= 0.5) forward to Friday (+1 day).
    Flooring the day integer relative to 1899-12-30 matches JS Math.floor(n) identically.
    """
    if val is None or val == "" or isinstance(val, bool):
        return ""
    s = str(val).strip()
    if not s:
        return ""

    if re.match(r"^[+-]?\d+(?:\.\d+)?$", s):
        try:
            n = float(s)
            if math.isfinite(n) and 20000 < n < 80000:
                base = dt.date(1899, 12, 30)
                return (base + dt.timedelta(days=int(math.floor(n)))).isoformat()
        except ValueError:
            pass

    iso_m = re.match(r"^(\d{4})-(\d{2})-(\d{2})(?:$|T|\s)", s)
    if iso_m:
        try:
            return dt.date(int(iso_m.group(1)), int(iso_m.group(2)), int(iso_m.group(3))).isoformat()
        except ValueError:
            return ""

    us_m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})(?:$|\s)", s)
    if us_m:
        try:
            return dt.date(int(us_m.group(3)), int(us_m.group(1)), int(us_m.group(2))).isoformat()
        except ValueError:
            return ""

    human_m = re.search(
        r"(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+)?"
        r"([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})",
        s,
        re.I,
    )
    if human_m:
        m_num = MONTH_NAME_MAP.get(human_m.group(1).lower())
        if m_num:
            try:
                return dt.date(int(human_m.group(3)), m_num, int(human_m.group(2))).isoformat()
            except ValueError:
                return ""

    return ""


def extract_hearing_date_iso(text: str) -> str:
    """Dynamically extract a future public hearing date from Agenda Outline or Notice text."""
    s = str(text or "").strip()
    if not s:
        return ""
    sec_m = re.search(
        r"Establish\s+a?\s*public\s+hearing\s+date\s+(?:of|for)\s+([A-Za-z]+\s+\d{1,2},?\s+\d{4})",
        s,
        re.I,
    )
    if sec_m:
        return excel_serial_to_iso(sec_m.group(1))
    notice_m = re.search(
        r"(?:Thursday|Wednesday|Monday|Tuesday|Friday),?\s+([A-Za-z]+\s+\d{1,2},?\s+\d{4})",
        s,
        re.I,
    )
    if notice_m:
        return excel_serial_to_iso(notice_m.group(1))
    return excel_serial_to_iso(s)


def format_human_us_date(iso_str: str, short_month: bool = True) -> str:
    iso = excel_serial_to_iso(iso_str)
    if not iso:
        return ""
    try:
        d = dt.date.fromisoformat(iso)
        fmt = "%b %-d, %Y" if short_month else "%B %-d, %Y"
        return d.strftime(fmt)
    except ValueError:
        return iso


def simplify_search_address(raw_addr: str) -> str:
    """Simplify a Houston street address for municipal/state portal searches.

    Why positional tokenization: Portals inconsistently abbreviate trailing street types
    ('3827 Broadway St' vs '3827 Broadway'), while HCAD appends bare condo unit numbers after
    street suffixes ('909 TEXAS AVE 308'). Positional checks preserve Saint-prefixed streets
    ('2100 St Emanuel St' -> '2100 St Emanuel') and East End lettered avenues ('1200 Ave A').
    """
    s = str(raw_addr or "").strip()
    if not s:
        return ""
    s = s.split(";")[0].strip()
    s = re.sub(r",\s*Houston\b.*$", "", s, flags=re.I)
    s = re.sub(r",\s*TX\b(?:\s+\d{5}(?:-\d{4})?)?$", "", s, flags=re.I)
    s = re.sub(r"\s+\bHouston\s*,?\s*TX\b(?:\s+\d{5}(?:-\d{4})?)?$", "", s, flags=re.I)
    s = re.sub(r"\s+#\s*[A-Za-z0-9-]+.*$", "", s)
    s = re.sub(
        r"\s*,?\s*\b(?:STE|SUITE|APT|APARTMENT|UNIT|BLDG|BUILDING|FL|FLOOR|RM|ROOM|DEPT|SPC|SPACE)\b\.?(?:\s+[A-Za-z0-9#-]+.*)?$",
        "",
        s,
        flags=re.I,
    )

    tokens = [tok.strip(".,") for tok in s.split() if tok.strip(".,")]
    if not tokens:
        return ""

    has_leading_num = bool(re.match(r"^\d+[A-Za-z]?(?:-\d+[A-Za-z]?)?$", tokens[0]))
    street_start = 1 if has_leading_num else 0
    if has_leading_num and len(tokens) > 1 and tokens[1] == "1/2":
        street_start = 2

    street_slice = tokens[street_start:]
    if (
        len(street_slice) >= 2
        and re.match(r"^[A-Za-z]$", street_slice[-1])
        and street_slice[-2].upper() in ("AVE", "AVENUE")
    ):
        pre_ave = street_slice[:-2]
        if not pre_ave or all(t.upper() in DIRECTIONAL_TOKENS for t in pre_ave):
            return " ".join(tokens)

    if len(tokens) - street_start >= 3:
        penult_upper = tokens[-2].upper()
        last_tok = tokens[-1]
        core_before_penult = [
            t for t in tokens[street_start:-2] if t.upper() not in DIRECTIONAL_TOKENS
        ]
        if (
            penult_upper in STREET_TYPE_SUFFIXES
            and len(core_before_penult) >= 1
            and re.match(r"^(?:\d+[A-Za-z]?|[A-Za-z]\d+)$", last_tok)
        ):
            tokens.pop()

    if len(tokens) - street_start >= 2:
        last_upper = tokens[-1].upper()
        if last_upper in STREET_TYPE_SUFFIXES:
            core_before_suffix = [
                t for t in tokens[street_start:-1] if t.upper() not in DIRECTIONAL_TOKENS
            ]
            if len(core_before_suffix) >= 1:
                tokens.pop()

    return " ".join(tokens)


def parse_hcad_account_list(raw: str) -> dict[str, list[str]]:
    """Parse comma/semicolon/whitespace-separated 10-13 digit HCAD IDs into 13-digit accounts."""
    text = str(raw or "").strip()
    if not text:
        return {"hcadIds": [], "nonHcadIds": [], "warnings": []}

    raw_tokens = [t.strip() for t in re.split(r"[,;\s|]+", text) if t.strip()]
    seen: set[str] = set()
    hcad_ids: list[str] = []
    non_hcad_ids: list[str] = []
    warnings: list[str] = []

    for raw_tok in raw_tokens:
        tok = raw_tok.lstrip("#").split("#")[0].strip()
        if not tok:
            continue
        if re.match(r"^[+-]?\d+(?:\.\d+)?E[+-]?\d+$", tok, re.I):
            non_hcad_ids.append(raw_tok)
            warnings.append(f"Scientific notation '{raw_tok}' lost 13-digit HCAD precision.")
            continue
        digits = re.sub(r"[-\s]+", "", tok)
        if digits.isdigit():
            if 10 <= len(digits) <= 13 and int(digits) != 0:
                padded = digits.zfill(13)
                if padded not in seen:
                    seen.add(padded)
                    hcad_ids.append(padded)
            elif len(digits) > 13:
                non_hcad_ids.append(digits)
                warnings.append(f"Parcel ID '{digits}' has {len(digits)} digits (non-HCAD).")

    return {"hcadIds": hcad_ids, "nonHcadIds": non_hcad_ids, "warnings": warnings}


def _collect_valid_year(val: object) -> int | None:
    if val is None or val == "" or isinstance(val, bool):
        return None
    try:
        n = int(float(str(val).strip()))
        if 1836 <= n <= 2035:
            return n
    except ValueError:
        pass
    return None


def resolve_multi_parcel_building_tier(
    parcels: list[dict],
    flags: list[str] | tuple[str, ...] = (),
    super_n: str = "",
    curated_overrides: dict | None = None,
    confirmed_vacant: bool = False,
    override_suggested_tier: str = "",
) -> dict:
    """Resolve oldest nonzero standing construction year (>= 1836) and suggested tier.

    Why >= 1836 filter: HCAD stores year_built = 0 on vacant lots, parking reserves, and
    unimproved sub-parcels; unfiltered min() would return 0 instead of the oldest standing
    building on multi-structure parcels (such as Amerimex Motors with c. 1940 and 2013 buildings).
    When a notice packet or parcel audit explicitly verifies a site is vacant (confirmed_vacant=True)
    with zero standing historic structures or designations, Tier 3 supersedes generic older-ring
    neighborhood heuristics (DF-6).
    """
    overrides_map = {}
    if isinstance(curated_overrides, dict):
        overrides_map = curated_overrides.get("overrides", curated_overrides)

    valid_all_years: list[int] = []
    parcel_summaries: list[dict] = []
    designations: list[str] = []
    valid_parcels = [raw_p for raw_p in (parcels or []) if isinstance(raw_p, dict)]

    for raw_p in valid_parcels:
        raw_hcad = str(raw_p.get("hcad") or raw_p.get("hcad_num") or "").strip()
        parsed_hcads = parse_hcad_account_list(raw_hcad)["hcadIds"]
        hcad = parsed_hcads[0] if parsed_hcads else raw_hcad.split("#")[0].strip()
        raw_ov = overrides_map.get(hcad) if (hcad and isinstance(overrides_map, dict)) else None
        is_demolished_override = bool(isinstance(raw_ov, dict) and raw_ov.get("suppress_only"))
        ov = raw_ov if (isinstance(raw_ov, dict) and not is_demolished_override) else None

        hd = str((ov or {}).get("historic_district") or raw_p.get("historic_district") or "").strip()
        lm = str((ov or {}).get("landmark_name") or raw_p.get("landmark_name") or "").strip()
        gb = str((ov or {}).get("good_brick_summary") or raw_p.get("good_brick_summary") or "").strip()
        raw_desigs = [str(d) for d in (raw_p.get("designations") or []) if d]
        has_designation = bool(hd or lm or gb or raw_desigs)

        cand_years: list[int] = []
        ov_yr = (
            None
            if is_demolished_override
            else _collect_valid_year(
                raw_p.get("overrideYearBuilt") if raw_p.get("overrideYearBuilt") is not None else (ov or {}).get("year_built")
            )
        )
        if ov_yr is not None:
            cand_years.append(ov_yr)
        elif not is_demolished_override and not (confirmed_vacant and not has_designation):
            many = raw_p.get("allYears") or raw_p.get("all_years") or []
            if isinstance(many, list):
                for y in many:
                    cy = _collect_valid_year(y)
                    if cy is not None:
                        cand_years.append(cy)
            for single_key in ("yearBuilt", "year_built", "earliestYearBuilt"):
                cy = _collect_valid_year(raw_p.get(single_key))
                if cy is not None:
                    cand_years.append(cy)

        dedup_years = sorted(set(cand_years))
        parcel_oldest = dedup_years[0] if dedup_years else 0
        valid_all_years.extend(dedup_years)

        addr = " ".join(str((ov or {}).get("address") or raw_p.get("address") or "").split())
        bname = str(
            (ov or {}).get("landmark_name")
            or (ov or {}).get("building_name")
            or raw_p.get("landmark_name")
            or raw_p.get("building_name")
            or ""
        ).strip()

        if hd:
            designations.append(f"City historic district ({hd})")
        if lm:
            designations.append("City Landmark")
        if gb:
            designations.append("Good Brick Award")
        designations.extend(raw_desigs)

        is_parcel_vacant = bool(
            raw_p.get("isVacant")
            or raw_p.get("confirmedVacant") is True
            or raw_p.get("confirmed_vacant") is True
            or is_demolished_override
            or (confirmed_vacant and not dedup_years)
        )
        parcel_summaries.append(
            {
                "hcad": hcad,
                "address": addr,
                "buildingName": bname,
                "oldestYearBuilt": parcel_oldest,
                "allYearsBuilt": dedup_years,
                "usedCuratedOverride": ov_yr is not None,
                "isVacant": is_parcel_vacant,
            }
        )

    sorted_all_years = sorted(set(valid_all_years))
    oldest_yr = sorted_all_years[0] if sorted_all_years else 0
    newest_yr = sorted_all_years[-1] if sorted_all_years else 0
    unique_desigs = list(dict.fromkeys(designations))
    flag_set = set(flags or ())
    norm_sn = str(super_n or "").strip().upper()

    norm_override_tier = str(override_suggested_tier or "").strip()
    all_parcels_confirmed_vacant = bool(confirmed_vacant) or (
        bool(parcel_summaries) and all(p.get("isVacant") for p in parcel_summaries)
    )
    is_confirmed_vacant = bool(all_parcels_confirmed_vacant and oldest_yr == 0 and not unique_desigs)

    suggested_tier = ""
    if norm_override_tier in ("1", "2", "3"):
        suggested_tier = norm_override_tier
    elif is_confirmed_vacant:
        suggested_tier = "3"
    elif unique_desigs:
        suggested_tier = "1"
    elif "dpv" in flag_set:
        suggested_tier = (
            "1"
            if ((1836 <= oldest_yr <= 1976) or (norm_sn in OLDER_RING_SUPER_N))
            else "2"
        )
    elif 1836 <= oldest_yr <= 1965 and (
        "hearing" in flag_set
        or "variance" in flag_set
        or "split" in flag_set
        or "use" in flag_set
        or oldest_yr <= 1935
    ):
        suggested_tier = (
            "1"
            if ("hearing" in flag_set or "variance" in flag_set or oldest_yr <= 1935)
            else "2"
        )
    elif 1836 <= oldest_yr <= 1976:
        suggested_tier = "2"

    return {
        "oldestYearBuilt": oldest_yr,
        "newestYearBuilt": newest_yr,
        "allYearsBuilt": sorted_all_years,
        "parcels": parcel_summaries,
        "designations": unique_desigs,
        "suggestedTier": suggested_tier,
        "confirmedVacant": is_confirmed_vacant,
    }


def _parse_finite_float(val: object) -> float | None:
    if val is None or val == "" or isinstance(val, bool):
        return None
    s = str(val).replace(",", "").strip()
    if not s:
        return None
    try:
        n = float(s)
        return n if math.isfinite(n) else None
    except ValueError:
        return None


def classify_item(it: dict, review_override: dict | None = None) -> dict:
    """Single canonical Pass 1 plat classifier shared 1:1 with JS classifyPlatItem().

    Why blank prevSub checks has_preservation_signal: Surveyors frequently omit Previous
    Subdivision on urban commercial replats and variance applications (e.g. Amerimex Motors
    2026-1659 in Section D. Variances). Blank prevSub screens an item out only when zero
    variance, hearing, DPV, reconsideration, pre-1976, active-permit, or designation signals exist.
    """
    t_str = str(it.get("type") or "")
    m_code = re.search(r"\(([A-Z0-9]+)\)\s*$", t_str.strip())
    code = m_code.group(1) if m_code else ""
    lu = str(it.get("landUse") or "")
    ac = _parse_finite_float(it.get("acreage"))
    lots = _parse_finite_float(it.get("lots"))

    city_val = str(it.get("city") or "")
    in_city = ("city" in city_val.lower()) and (city_val.strip().lower() != "etj")
    sec = str(it.get("section") or "").strip()
    special = str(it.get("special") or "")
    name = str(it.get("name") or "")
    prev_sub = str(it.get("prevSub") or "").strip()
    atlas = it.get("atlas") if isinstance(it.get("atlas"), dict) else {}

    flags: list[str] = []
    if (
        code in ("C3N", "PH")
        or re.match(r"^c\.", sec, re.I)
        or re.match(r"^[ivx]+\.\s*establish\b", sec, re.I)
    ):
        flags.append("hearing")
    if code == "DPV" or "dpv" in t_str.lower() or re.match(r"^k\.", sec, re.I):
        flags.append("dpv")
    if "variance" in special.lower() or code == "DPV" or re.match(r"^(?:d|k)\.", sec, re.I):
        flags.append("variance")
    if "reconsideration" in special.lower() or re.match(r"^f\.", sec, re.I):
        flags.append("reconsider")
    if it.get("deferral"):
        flags.append("deferred")

    if prev_sub:
        if ac is not None and ac <= 0.35 and lots is not None and 2 <= lots <= 4:
            flags.append("split")
        if re.search(r"unrestricted|commercial|multifamily|multi-unit", lu, re.I):
            flags.append("use")
        if lots == 0 and not UTILITY_RE.search(lu) and not UTILITY_RE.search(name):
            flags.append("reserve")

    if re.search(r"shared driveway|pae", lu, re.I):
        flags.append("shared")

    combined_name = re.sub(r"school lands?", "", f"{name} {prev_sub}", flags=re.I)
    if re.search(r"church|school|institutional", lu, re.I) or INST_RE.search(combined_name):
        flags.append("inst")

    if not in_city and ac is not None and ac >= 20:
        flags.append("tract")

    rev_yr = _collect_valid_year((review_override or {}).get("yearBuilt"))
    at_yr = _collect_valid_year(atlas.get("earliestYearBuilt"))
    valid_yrs = [y for y in (rev_yr, at_yr) if y is not None]
    parcel_yr = min(valid_yrs) if valid_yrs else 0

    if 1836 <= parcel_yr <= 1976:
        flags.append("pre1976")
    if atlas.get("hasActivePermit"):
        flags.append("permit")
    if isinstance(atlas.get("smlsProtections"), list) and len(atlas["smlsProtections"]) > 0:
        flags.append("smls")

    atlas_desigs = [d for d in (atlas.get("designations") or []) if d]
    hd_from_desig = next(
        (str(d) for d in atlas_desigs if re.search(r"historic district", str(d), re.I)),
        "",
    )
    hd_name = str(it.get("hd") or atlas.get("historicDistrict") or hd_from_desig or "").strip()
    has_any_designation = bool(hd_name or atlas_desigs)
    has_risk_flag = any(
        f in ("dpv", "hearing", "variance", "reconsider", "pre1976", "permit", "inst") for f in flags
    )
    has_preservation_signal = has_any_designation or has_risk_flag

    group = "check"
    reason = ""
    if hd_name:
        group = "designated"
        clean_hd = re.sub(r"^City historic district\s*\((.*)\)$", r"\1", hd_name, flags=re.I)
        reason = f"In a city historic district ({clean_hd})."
    elif not in_city:
        group = "etj"
        reason = "Outside city limits (ETJ). Skim for cemeteries, farmsteads or rural churches."
    elif code == "SP" and not has_preservation_signal:
        group = "out"
        reason = "Street dedication plat."
    elif code == "GP" and not has_preservation_signal:
        group = "out"
        reason = "General plan."
    elif not prev_sub and not has_preservation_signal:
        group = "out"
        reason = "First-time plat: no previous subdivision."
    elif (
        UTILITY_RE.search(lu)
        and not re.search(r"residential|commercial|unrestricted|multi", lu, re.I)
        and not has_preservation_signal
    ):
        group = "out"
        reason = "Utility site."

    overridden_from_out = False
    original_reason = reason
    if review_override and review_override.get("overrideScreenOut") and group == "out":
        group = "check"
        overridden_from_out = True

    weights = {
        "dpv": 4,
        "pre1976": 4,
        "permit": 5,
        "hearing": 2,
        "variance": 2,
        "reconsider": 1,
        "deferred": 1,
        "split": 3,
        "use": 2,
        "reserve": 1,
        "shared": 1,
        "inst": 3,
        "tract": 1,
        "smls": 1,
    }
    score = sum(weights.get(f, 0) for f in flags)
    return {
        "group": group,
        "reason": reason,
        "originalReason": original_reason,
        "overriddenFromOut": overridden_from_out,
        "flags": flags,
        "score": score,
        "code": code,
        "inCity": in_city,
    }


def build_occurrence_key(cycle_key: str, app_no: str) -> str:
    c = str(cycle_key or "unknown-cycle").strip()
    a = str(app_no or "").strip()
    return f"{c}::{a}"


def normalize_continuity_plat_name(raw_name: str) -> str:
    """Normalize a plat name for cross-cycle continuity while preserving Section/Phase numbers."""
    s = str(raw_name or "").upper().strip()
    if not s:
        return ""
    s = re.sub(r"\([^)]*\)", " ", s)
    s = re.sub(r"\b(?:SECTION|SECT)\b", "SEC", s)
    s = re.sub(r"\bPHASE\b", "PH", s)
    s = re.sub(
        r"\b(?:PARTIAL\s+REPLAT\s+NO\s*\d+|REPLAT\s+NO\s*\d+|AMENDING\s+PLAT\s+NO\s*\d+)\b",
        " ",
        s,
    )
    s = re.sub(r"\b(?:PARTIAL\s+REPLAT|REPLAT|AMENDING\s+PLAT|SUBDIVISION|ADDITION)\b", " ", s)
    s = re.sub(r"[^A-Z0-9\s]", " ", s)
    s = " ".join(s.split())
    if not s or len(s) < 5:
        return ""
    if s in GENERIC_STANDALONE_PLAT_NAMES:
        return ""
    if re.match(r"^(?:RESERVE|TRACT|LOT|BLOCK)\s+[A-Z0-9]+$", s):
        return ""
    return s


def get_canonical_plat_app_ids(entity: dict | None) -> list[str]:
    """Return normalized non-placeholder plat application IDs (app / appNo / sourcePlatApp / linkedAppNo).

    Positional per-meeting outline IDs (PH-135, DPV-21) vary across cycles and are excluded here,
    whereas stable COH subdivision app numbers (2026-1522) and 6+ digit ILMS DPV references
    (DPV-26073533) are preserved for cross-cycle continuity and Watchlist <-> Biweekly sync (DF-5).
    """
    if not isinstance(entity, dict):
        return []
    out: list[str] = []
    seen: set[str] = set()
    raw_id = str(entity.get("id") or "").strip()
    id_app = raw_id[5:] if raw_id.lower().startswith("plat-") else ""
    for cand in (
        entity.get("app"),
        entity.get("appNo"),
        entity.get("sourcePlatApp"),
        entity.get("linkedAppNo"),
        id_app,
    ):
        val = str(cand or "").strip().upper()
        if not val or re.match(r"^(?:PH-\d+|DPV-\d{1,5})$", val):
            continue
        if val not in seen:
            seen.add(val)
            out.append(val)
    return out


def build_continuity_keys(it: dict, rev: dict | None = None) -> list[str]:
    rev = rev or {}
    keys: list[str] = []
    for app_id in get_canonical_plat_app_ids(it):
        keys.append(f"app:{app_id}")

    atlas = it.get("atlas") if isinstance(it.get("atlas"), dict) else {}
    raw_hcads = f"{rev.get('hcad', '')} {it.get('hcad', '')} {atlas.get('hcad_num', '')}"
    hcad_ids = parse_hcad_account_list(raw_hcads)["hcadIds"]
    if hcad_ids:
        keys.append(f"hcad:{','.join(sorted(hcad_ids))}")

    norm_name = normalize_continuity_plat_name(it.get("name", ""))
    geo_or_party = re.sub(
        r"[^A-Z0-9]+",
        " ",
        str(
            it.get("superN")
            or atlas.get("superNeighborhoodMatch")
            or it.get("developer")
            or it.get("applicant")
            or ""
        ).upper(),
    ).strip()
    if norm_name and geo_or_party:
        keys.append(f"name:{norm_name}|ctx:{geo_or_party}")
    return keys


def is_boilerplate_plat_why(text: object) -> bool:
    """Return True when a review why/notes string is blank or pure machine-generated boilerplate."""
    s = str(text or "").strip()
    if not s:
        return True
    if re.search(r"\b(?:ea\s*note|ea\s*:|staff\s*note|ph\s*note)\b", s, re.I):
        return False
    s_inner = re.sub(r"^[\s(\[]+|[\s)\]]+$", "", s)
    if not s_inner:
        return True
    return bool(
        re.match(
            r"^(?:Atlas matched HCAD parcel at |Atlas matched designated historic resource |"
            r"Section K Development Plat Variance |Flagged on Planning Commission agenda |"
            r"Confirmed from \d{4}-\d{2}-\d{2} Planning Commission Agenda|"
            r"Proposed vacant-parcel development at )",
            s_inner,
            re.I,
        )
    )


def merge_plat_review_overlay(
    seed_rev: dict | None,
    incoming_rev: dict | None,
    item: dict | None = None,
) -> dict:
    """Merge a cloud or browser review overlay onto a fresh pipeline review seed safely (DF-1).

    When an incoming overlay is an unconfirmed machine auto-enrichment with no human edits,
    stale machine-geocoded fields (address, yearBuilt, designation, hcad, why, sources, followup)
    yield to the freshly computed pipeline seed so notice/HCAD fixes propagate automatically.
    Also replaces retracted pre-Pass-2 baseline mis-geocodes (DPV-26073533 -> 2603 MILAM ST 15
    and 2026-1585 -> 1226 W CLAY ST) when their notes remain untouched machine boilerplate.
    """
    base = dict(seed_rev or {})
    if not isinstance(incoming_rev, dict) or not incoming_rev:
        return base
    inc = dict(incoming_rev)
    app_id = str((item or {}).get("app") or (item or {}).get("appNo") or "").strip().upper()
    inc_addr_upper = " ".join(re.sub(r"[^A-Z0-9\s]", " ", str(inc.get("address") or "").upper()).split())
    human_fields = (
        [str(f) for f in inc.get("humanEditedFields") if str(f).strip()]
        if isinstance(inc.get("humanEditedFields"), list)
        else []
    )
    has_human_edits = bool(inc.get("humanEdited") is True or human_fields)
    boilerplate_why = is_boilerplate_plat_why(inc.get("why"))

    is_retracted_seed = bool(
        app_id in RETRACTED_BASELINE_PLAT_SEEDS
        and any(seed in inc_addr_upper for seed in RETRACTED_BASELINE_PLAT_SEEDS[app_id])
        and not has_human_edits
        and boilerplate_why
    )
    if is_retracted_seed:
        return base

    if inc.get("autoEnriched") is True and not has_human_edits and not inc.get("tierConfirmed") and boilerplate_why:
        merged = dict(base)
        # Preserve an explicit human Tier 3 dismissal made against the correct parcel while
        # refreshing machine-enriched parcel metadata from base.
        if str(inc.get("tier") or "") == "3" and str(inc.get("reviewer") or "").strip():
            merged["tier"] = "3"
            merged["tierConfirmed"] = False
            if inc.get("threatCategory"):
                merged["threatCategory"] = inc["threatCategory"]
        for keep_key in ("overrideScreenOut", "reviewer", "updated", "carriedFromCycle", "carriedFields"):
            if keep_key in inc and inc[keep_key] not in (None, "", []):
                merged[keep_key] = inc[keep_key]
        return merged

    merged = dict(base)
    merged.update(inc)
    return merged


def _is_human_reviewed_plat_entry(rev: dict | None) -> bool:
    if not isinstance(rev, dict) or not rev:
        return False
    if rev.get("humanEdited") is True:
        return True
    if isinstance(rev.get("humanEditedFields"), list) and len(rev["humanEditedFields"]) > 0:
        return True
    if rev.get("tierConfirmed") is True:
        return True
    if not is_boilerplate_plat_why(rev.get("why")):
        return True
    if not rev.get("autoEnriched"):
        return any(str(rev.get(k) or "").strip() for k in CARRY_FORWARD_REVIEW_FIELDS)
    if str(rev.get("tier") or "") == "3" and str(rev.get("reviewer") or "").strip():
        return True
    return False


def _is_truncated_carry_forward_value(field: str, cur_val: object, prior_val: object) -> bool:
    """Detect partial-keystroke prefixes or multi-HCAD subsets captured by pre-Pass-2 carry-forward."""
    cur_s = str(cur_val or "").strip()
    prior_s = str(prior_val or "").strip()
    if not cur_s or not prior_s or cur_s == prior_s or field == "tier":
        return False
    if field == "hcad":
        cur_hcads = parse_hcad_account_list(cur_s)["hcadIds"]
        prior_hcads = parse_hcad_account_list(prior_s)["hcadIds"]
        if len(prior_hcads) > len(cur_hcads) >= 1 and set(cur_hcads).issubset(set(prior_hcads)):
            return True
    return len(prior_s) > len(cur_s) and prior_s.upper().startswith(cur_s.upper())


def _are_continuity_names_compatible(norm_a: str, norm_b: str) -> bool:
    if not norm_a or not norm_b:
        return True
    sec_a_m = re.search(r"\b(?:SEC|PH)\s+([A-Z0-9]+)\b", norm_a)
    sec_b_m = re.search(r"\b(?:SEC|PH)\s+([A-Z0-9]+)\b", norm_b)
    sec_a = sec_a_m.group(1) if sec_a_m else ""
    sec_b = sec_b_m.group(1) if sec_b_m else ""
    if sec_a and sec_b and sec_a != sec_b:
        return False
    if norm_a == norm_b or norm_a in norm_b or norm_b in norm_a:
        return True
    ta = [t for t in norm_a.split() if len(t) >= 3]
    tb = [t for t in norm_b.split() if len(t) >= 3]
    if not ta or not tb:
        return False
    shared = len(set(ta) & set(tb))
    min_needed = 1 if min(len(ta), len(tb)) == 1 else 2
    return shared >= min_needed


def _are_continuity_snapshots_compatible(
    cur_hcads: list[str],
    cur_norm_name: str,
    prev_hcads: list[str],
    prev_norm_name: str,
) -> bool:
    if cur_hcads and prev_hcads:
        return bool(set(cur_hcads) & set(prev_hcads))
    return _are_continuity_names_compatible(cur_norm_name, prev_norm_name)


def link_cross_cycle_history(cycles_map: dict[str, dict], cycle_order: list[str] | None = None) -> dict[str, dict]:
    """Link deferred or resubmitted items chronologically across agenda cycles."""
    ordered = sorted(cycle_order) if cycle_order else sorted(cycles_map.keys())
    seen_by_key: dict[str, list[dict]] = {}

    for cycle_iso in ordered:
        cyc = cycles_map.get(cycle_iso)
        if not cyc or not isinstance(cyc.get("items"), list):
            continue
        reviews = cyc.setdefault("reviews", {})

        for it in cyc["items"]:
            r = reviews.get(it["app"], {})
            ckeys = build_continuity_keys(it, r)
            it["continuityKey"] = ckeys[0] if ckeys else ""
            atlas = it.get("atlas") if isinstance(it.get("atlas"), dict) else {}
            cur_hcads = parse_hcad_account_list(
                f"{r.get('hcad', '')} {it.get('hcad', '')} {atlas.get('hcad_num', '')}"
            )["hcadIds"]
            cur_norm_name = normalize_continuity_plat_name(it.get("name", ""))

            prior_map: dict[str, dict] = {}
            for ck in ckeys:
                for prev in seen_by_key.get(ck, []):
                    occ = f"{prev['cycleKey']}::{prev['app']}"
                    if prev["cycleKey"] == cycle_iso or occ in prior_map:
                        continue
                    if ck.startswith("app:"):
                        prev_hcads = parse_hcad_account_list(prev.get("hcad", ""))["hcadIds"]
                        prev_norm_name = normalize_continuity_plat_name(prev.get("name", ""))
                        if not _are_continuity_snapshots_compatible(
                            cur_hcads, cur_norm_name, prev_hcads, prev_norm_name
                        ):
                            continue
                    prior_map[occ] = prev

            prior_cycles = sorted(prior_map.values(), key=lambda x: x["cycleKey"])
            it["priorCycles"] = prior_cycles

            if prior_cycles:
                latest_prior = prior_cycles[-1]
                changed = False
                human_fields = (
                    {str(f) for f in r.get("humanEditedFields") if str(f).strip()}
                    if isinstance(r.get("humanEditedFields"), list)
                    else set()
                )
                carried_fields: list[str] = (
                    [str(f) for f in r.get("carriedFields") if str(f).strip()]
                    if isinstance(r.get("carriedFields"), list)
                    else []
                )
                is_carried_from_prior = bool(
                    r.get("carriedFromCycle") == latest_prior["cycleKey"] or carried_fields
                )
                prior_is_human = bool(latest_prior.get("isHumanReviewed"))
                cur_is_untouched_auto = bool(
                    r.get("autoEnriched") is True
                    and not r.get("humanEdited")
                    and not human_fields
                    and not r.get("tierConfirmed")
                    and is_boilerplate_plat_why(r.get("why"))
                )

                for k in CARRY_FORWARD_REVIEW_FIELDS:
                    if k in human_fields:
                        continue
                    prior_val = str(latest_prior.get(k) or "").strip()
                    if not prior_val:
                        continue
                    cur_val = str(r.get(k) or "").strip()
                    should_copy = False
                    if not cur_val or (k == "designation" and cur_val == "Not checked yet" and prior_val != "Not checked yet"):
                        if k == "hcad":
                            it_hcads = set(parse_hcad_account_list(it.get("hcad", ""))["hcadIds"])
                            pr_hcads = set(parse_hcad_account_list(prior_val)["hcadIds"])
                            should_copy = (not it.get("hcad")) or (prior_is_human and pr_hcads and pr_hcads != it_hcads)
                        else:
                            should_copy = True
                    elif k in carried_fields:
                        should_copy = cur_val != prior_val
                    elif is_carried_from_prior and _is_truncated_carry_forward_value(k, cur_val, prior_val):
                        should_copy = True
                    elif prior_is_human and cur_is_untouched_auto and cur_val != prior_val:
                        should_copy = True

                    if should_copy:
                        r[k] = prior_val
                        if k not in carried_fields:
                            carried_fields.append(k)
                        changed = True

                if changed:
                    r["carriedFromCycle"] = latest_prior["cycleKey"]
                    r["carriedFields"] = carried_fields
                    reviews[it["app"]] = r
                    if "_c" in it:
                        it["_c"] = classify_item(it, r)

            atlas = it.get("atlas") if isinstance(it.get("atlas"), dict) else {}
            snapshot = {
                "cycleKey": cycle_iso,
                "app": it["app"],
                "linkedAppNo": it.get("linkedAppNo", ""),
                "name": it.get("name", ""),
                "deferral": it.get("deferral", ""),
                "section": it.get("section", ""),
                "tier": r.get("tier", ""),
                "tierConfirmed": bool(r.get("tierConfirmed")),
                "isHumanReviewed": _is_human_reviewed_plat_entry(r),
                "hcad": r.get("hcad") or it.get("hcad") or "",
                "address": r.get("address") or atlas.get("primaryAddress") or "",
                "yearBuilt": r.get("yearBuilt")
                or (str(atlas["earliestYearBuilt"]) if atlas.get("earliestYearBuilt") else ""),
                "designation": r.get("designation", ""),
                "threatCategory": r.get("threatCategory", ""),
                "why": r.get("why", ""),
                "sources": r.get("sources", ""),
                "followup": r.get("followup", ""),
            }
            # Refresh continuity keys if hcad was carried forward onto r
            final_ckeys = build_continuity_keys(it, r)
            it["continuityKey"] = final_ckeys[0] if final_ckeys else ""
            for ck in final_ckeys:
                seen_by_key.setdefault(ck, []).append(snapshot)

    return cycles_map


def parse_agenda_outline_text(outline_text: str) -> dict:
    """Parse a COH Planning Commission Agenda Outline PDF text.

    Supports both Section III and Section IV Roman numerals for 'Establish a public hearing
    date of <Month DD, YYYY>' (e.g. Section IV on Oct 15, 2026 vs Section III on Oct 29, 2026).
    """
    outline_by_name: dict[str, dict] = {}
    dpv_items: list[dict] = []
    sec_hearing_names: list[str] = []
    sec_hearing_date_iso = ""
    sec_hearing_numeral = "IV"
    in_sec_establish = False
    in_sec_k = False

    for line in (outline_text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        m_est = re.match(
            r"^([IVX]+)\.\s+Establish\s+a?\s*public\s+hearing\s+date(?:\s+(?:of|for)\s+([A-Za-z]+\s+\d{1,2},?\s+\d{4}))?",
            s,
            re.I,
        )
        if m_est:
            in_sec_establish = True
            in_sec_k = False
            sec_hearing_numeral = m_est.group(1).upper()
            if m_est.group(2):
                sec_hearing_date_iso = excel_serial_to_iso(m_est.group(2))
            continue

        if in_sec_establish and (
            re.match(r"^(?:[IVX]+|[A-K])\.\s+", s) or s.startswith("Platting Summary")
        ):
            in_sec_establish = False

        if in_sec_establish:
            m_item_letter = re.match(r"^[a-z]\.\s+(.+)$", s, re.I)
            if m_item_letter:
                sec_hearing_names.append(m_item_letter.group(1).strip())
            continue

        if re.match(r"^K\.\s+Development Plats with Variance Requests", s, re.I):
            in_sec_k = True
            continue
        if in_sec_k and re.match(r"^City of Houston", s, re.I):
            in_sec_k = False
            continue

        m_item = re.match(
            r"^(\d{1,3})\s+(.+?)\s{2,}(C2R|C2|C3P|C3F|C3R|C3N|GP|SP|DPV)(?:\s+(DEF\s*\d+))?\s*$",
            s,
        )
        if m_item:
            item_no = m_item.group(1)
            plat_name = m_item.group(2).strip()
            app_type = m_item.group(3).strip()
            deferral = (m_item.group(4) or "").replace(" ", "")
            outline_by_name[plat_name.lower()] = {
                "agendaItemNo": item_no,
                "appType": app_type,
                "deferral": deferral,
            }
            if app_type == "DPV" or in_sec_k:
                dpv_items.append(
                    {
                        "agendaItemNo": item_no,
                        "name": plat_name,
                        "appType": "Development Plat Variance (DPV)",
                        "deferral": deferral,
                    }
                )

    return {
        "outline_by_name": outline_by_name,
        "dpv_items": dpv_items,
        "sec4_hearing_names": sec_hearing_names,
        "sec4_hearing_date_iso": sec_hearing_date_iso,
        "sec4_numeral": sec_hearing_numeral,
    }


def norm_street(st: str) -> str:
    s = re.sub(r"[^A-Z0-9\s]", " ", st.upper())
    s = " ".join(s.split())
    rep = {
        "STREET": "ST",
        "AVENUE": "AVE",
        "DRIVE": "DR",
        "ROAD": "RD",
        "BOULEVARD": "BLVD",
        "LANE": "LN",
        "COURT": "CT",
        "PLACE": "PL",
        "CIRCLE": "CIR",
        "PARKWAY": "PKWY",
        "FREEWAY": "FWY",
        "HIGHWAY": "HWY",
        "NORTH": "N",
        "SOUTH": "S",
        "EAST": "E",
        "WEST": "W",
    }
    tokens = [rep.get(tok, tok) for tok in s.split()]
    return " ".join(tokens)


def normalize_historical_subdivision_key(raw_name: str) -> str:
    """Normalize a subdivision name for historical .xlsx matching while preserving Sec/Replat ordinals (DF-7)."""
    s = str(raw_name or "").upper().strip()
    if not s:
        return ""
    s = re.sub(r"\([^)]*\)", " ", s)
    s = re.sub(r"\b(?:SECTION|SECT)\b", "SEC", s)
    s = re.sub(r"\b(?:NUMBER|NUM)\b", "NO", s)
    s = re.sub(r"[^A-Z0-9\s]", " ", s)
    return " ".join(s.split())


def norm_subdivision(name: str) -> str:
    s = (name or "").upper()
    s = re.sub(
        r"\b(PARTIAL\s+REPLAT|REPLAT|AMENDING\s+PLAT|SEC(?:TION)?|NO|ADDITION|SUBDIVISION|ANNEX|EXT(?:ENSION)?|LOT\s+\d+|BLOCK\s+\d+|OF)\b",
        " ",
        s,
    )
    s = re.sub(r"[^A-Z0-9\s]", " ", s)
    s = re.sub(r"\b\d+\b", " ", s)
    return " ".join(s.split())


def load_atlas_indices():
    print("[1/6] Loading Houston Building Atlas address shards, curated overrides, buildings, and overlays...")
    curated_overrides: dict[str, dict] = {}
    cov_path = DATA_DIR / "curated_overrides.json"
    if cov_path.exists():
        with open(cov_path, "r", encoding="utf-8") as f:
            cov_raw = json.load(f)
        raw_ov_map = cov_raw.get("overrides", cov_raw) if isinstance(cov_raw, dict) else {}
        if isinstance(raw_ov_map, dict):
            for k, v in raw_ov_map.items():
                if isinstance(v, dict) and str(k).strip().isdigit():
                    curated_overrides[str(k).strip().zfill(13)] = v

    hcad_map: dict[str, dict] = {}
    addr_exact: dict[tuple[str, str], dict] = {}
    addr_nodir: dict[tuple[str, str], dict] = {}
    street_centroids: dict[str, tuple[float, float]] = {}

    shard_paths = sorted(glob.glob(str(DATA_DIR / "address_shards" / "addr_*.json")))
    for sp in shard_paths:
        with open(sp, "r", encoding="utf-8") as f:
            shard = json.load(f)
        for raw_st, recs in shard.items():
            st = " ".join(raw_st.upper().split())
            st_nodir = re.sub(r"^[NSEW]\s+", "", st)
            if recs and st not in street_centroids:
                street_centroids[st] = (float(recs[0][1]), float(recs[0][2]))
                if st_nodir not in street_centroids:
                    street_centroids[st_nodir] = (float(recs[0][1]), float(recs[0][2]))
            for r in recs:
                hnum = str(r[0]).strip()
                lng = float(r[1])
                lat = float(r[2])
                raw_yr = int(r[3]) if r[3] else 0
                valid_yr = raw_yr if 1836 <= raw_yr <= 2035 else 0
                hcad = str(r[4]).strip().zfill(13) if r[4] else ""
                label = r[5] if len(r) > 5 else ""
                full_addr = f"{hnum} {st}"

                if hcad:
                    if hcad not in hcad_map:
                        hcad_map[hcad] = {
                            "hcad": hcad,
                            "address": full_addr,
                            "addresses": [full_addr],
                            "lng": lng,
                            "lat": lat,
                            "year_built": valid_yr,
                            "all_years": [valid_yr] if valid_yr else [],
                            "label": label,
                        }
                    else:
                        entry = hcad_map[hcad]
                        if full_addr not in entry["addresses"]:
                            entry["addresses"].append(full_addr)
                        if valid_yr and valid_yr not in entry["all_years"]:
                            entry["all_years"].append(valid_yr)
                            entry["all_years"].sort()
                            entry["year_built"] = entry["all_years"][0]

                info = {
                    "hcad": hcad,
                    "address": full_addr,
                    "lng": lng,
                    "lat": lat,
                    "year_built": hcad_map[hcad]["year_built"] if hcad in hcad_map else valid_yr,
                    "all_years": list(hcad_map[hcad]["all_years"]) if hcad in hcad_map else ([valid_yr] if valid_yr else []),
                    "label": label,
                }
                if (hnum, st) not in addr_exact or (
                    valid_yr and (not addr_exact[(hnum, st)]["year_built"] or valid_yr < addr_exact[(hnum, st)]["year_built"])
                ):
                    addr_exact[(hnum, st)] = info
                if (hnum, st_nodir) not in addr_nodir or (
                    valid_yr and (not addr_nodir[(hnum, st_nodir)]["year_built"] or valid_yr < addr_nodir[(hnum, st_nodir)]["year_built"])
                ):
                    addr_nodir[(hnum, st_nodir)] = info

    buildings_by_hcad: dict[str, dict] = {}
    bpath = DATA_DIR / "buildings.geojson"
    if bpath.exists():
        with open(bpath, "r", encoding="utf-8") as f:
            bgeo = json.load(f)
        for feat in bgeo.get("features", []):
            props = feat.get("properties", {})
            geom = feat.get("geometry", {})
            coords = geom.get("coordinates", [])
            lng, lat = (coords[0], coords[1]) if len(coords) >= 2 else (None, None)
            byr = _collect_valid_year(props.get("year_built")) or 0
            for h in [props.get("hcad_num")] + (props.get("related_hcads") or []):
                if not h:
                    continue
                h_str = str(h).strip().zfill(13)
                if h_str not in buildings_by_hcad:
                    shard_yrs = hcad_map.get(h_str, {}).get("all_years", [])
                    all_yrs = sorted(set(([byr] if byr else []) + list(shard_yrs)))
                    buildings_by_hcad[h_str] = {
                        "building_id": props.get("building_id") or props.get("id") or "",
                        "building_name": props.get("building_name") or props.get("title") or "",
                        "address": props.get("address") or hcad_map.get(h_str, {}).get("address", ""),
                        "year_built": all_yrs[0] if all_yrs else 0,
                        "all_years": all_yrs,
                        "style": props.get("style") or "",
                        "architect": props.get("architect") or "",
                        "historic_district": props.get("historic_district") or "",
                        "landmark_name": props.get("landmark_name") or "",
                        "good_brick_summary": props.get("good_brick_summary") or "",
                        "owner": props.get("owner") or "",
                        "lng": lng,
                        "lat": lat,
                    }
                else:
                    ex_b = buildings_by_hcad[h_str]
                    if byr and byr not in ex_b["all_years"]:
                        ex_b["all_years"].append(byr)
                        ex_b["all_years"].sort()
                        ex_b["year_built"] = ex_b["all_years"][0]
                    for str_key in (
                        "building_name",
                        "address",
                        "style",
                        "architect",
                        "historic_district",
                        "landmark_name",
                        "good_brick_summary",
                        "owner",
                    ):
                        if not ex_b.get(str_key) and props.get(str_key):
                            ex_b[str_key] = props[str_key]

    # Apply curated_overrides.json precedence across hcad_map and buildings_by_hcad
    for hid, ov in curated_overrides.items():
        ov_yr = _collect_valid_year(ov.get("year_built"))
        if hid in hcad_map and ov_yr is not None:
            hcad_map[hid]["year_built"] = ov_yr
            if ov_yr not in hcad_map[hid]["all_years"]:
                hcad_map[hid]["all_years"] = sorted(set([ov_yr] + hcad_map[hid]["all_years"]))
        if hid in buildings_by_hcad:
            b_rec = buildings_by_hcad[hid]
            if ov_yr is not None:
                b_rec["year_built"] = ov_yr
                if ov_yr not in b_rec["all_years"]:
                    b_rec["all_years"] = sorted(set([ov_yr] + b_rec["all_years"]))
            for ov_key in ("landmark_name", "historic_district", "good_brick_summary", "address", "architect", "style"):
                if ov.get(ov_key):
                    b_rec[ov_key] = ov[ov_key]
        elif ov_yr is not None or ov.get("landmark_name") or ov.get("historic_district"):
            h_rec = hcad_map.get(hid, {})
            buildings_by_hcad[hid] = {
                "building_id": ov.get("building_id") or "",
                "building_name": ov.get("landmark_name") or ov.get("building_name") or "",
                "address": ov.get("address") or h_rec.get("address", ""),
                "year_built": ov_yr or h_rec.get("year_built", 0),
                "all_years": sorted(set(([ov_yr] if ov_yr else []) + list(h_rec.get("all_years", [])))),
                "style": ov.get("style") or "",
                "architect": ov.get("architect") or "",
                "historic_district": ov.get("historic_district") or "",
                "landmark_name": ov.get("landmark_name") or "",
                "good_brick_summary": ov.get("good_brick_summary") or "",
                "owner": ov.get("owner") or "",
                "lng": h_rec.get("lng"),
                "lat": h_rec.get("lat"),
            }

    overlays_path = DATA_DIR / "overlays.json"
    overlays = json.load(open(overlays_path, "r", encoding="utf-8")) if overlays_path.exists() else {}

    def feat_props(layer_key: str) -> list[dict]:
        raw = overlays.get(layer_key, {})
        if isinstance(raw, dict):
            return [f.get("properties", {}) for f in raw.get("features", []) if isinstance(f, dict)]
        if isinstance(raw, list):
            return [f for f in raw if isinstance(f, dict)]
        return []

    generic_subdiv_keys = {
        "N", "S", "E", "W", "NE", "NW", "SE", "SW", "NORTH", "SOUTH", "EAST", "WEST",
        "ESTATES", "PARK", "PLACE", "GARDENS", "ACRES", "VILLAGE", "HEIGHTS", "SQUARE",
        "COURT", "MANOR", "TERRACE", "OAKS", "WOODS", "HILLS", "VILLAS", "CENTER",
        "PLAZA", "CROSSING", "COMMONS", "LANDING", "MEADOWS", "GROVE", "POINT", "POINTE",
        "RIDGE", "VALLEY", "CREEK", "BAYOU", "RESERVE", "TRACT", "TRACTS", "BLOCK", "LOTS",
    }
    subdiv_index: dict[str, dict] = {}
    for sub in feat_props("platted_subdivisions"):
        for key_candidate in [sub.get("name", ""), sub.get("full_name", "")]:
            nk = norm_subdivision(key_candidate)
            if nk and nk not in generic_subdiv_keys and len(nk) >= 3:
                if nk not in subdiv_index or (
                    sub.get("pre_1940_count", 0) > subdiv_index[nk].get("pre_1940_count", 0)
                ):
                    subdiv_index[nk] = sub

    snbr_index: dict[str, dict] = {}
    for sn in feat_props("super_neighborhoods"):
        for key_cand in [sn.get("raw_name", ""), sn.get("name", "")]:
            nk = " ".join(re.sub(r"[^A-Z0-9\s]", " ", (key_cand or "").upper()).split())
            if nk:
                snbr_index[nk] = sn

    landmarks_by_hcad: dict[str, dict] = {}
    for lm in feat_props("landmarks"):
        hid_raw = lm.get("hcad_id") or lm.get("hcad_num")
        if hid_raw and str(hid_raw).strip().isdigit():
            landmarks_by_hcad[str(hid_raw).strip().zfill(13)] = lm

    good_brick_by_hcad: dict[str, dict] = {}
    for gb in feat_props("good_brick_awards"):
        hid_raw = gb.get("hcad_id") or gb.get("hcad_num")
        if hid_raw and str(hid_raw).strip().isdigit():
            good_brick_by_hcad[str(hid_raw).strip().zfill(13)] = gb

    smls_by_subdiv: dict[str, list[dict]] = {}
    for lp in feat_props("land_use_protections"):
        sn = norm_subdivision(lp.get("subdivision") or lp.get("name") or "")
        if sn:
            smls_by_subdiv.setdefault(sn, []).append(lp)

    active_alerts_by_hcad: dict[str, dict] = {}
    alerts_path = DATA_DIR / "active_permit_alerts.json"
    if alerts_path.exists():
        alerts_data = json.load(open(alerts_path, "r", encoding="utf-8"))
        for al in alerts_data.get("alerts", []):
            for h in [al.get("hcad_num")] + (al.get("related_hcads") or []):
                if h and str(h).strip().isdigit():
                    active_alerts_by_hcad[str(h).strip().zfill(13)] = al

    print(
        f"      Loaded {len(hcad_map):,} HCAD parcels, {len(buildings_by_hcad):,} historic buildings, "
        f"{len(curated_overrides):,} curated overrides, {len(subdiv_index):,} platted subdivisions."
    )
    return {
        "hcad_map": hcad_map,
        "curated_overrides": curated_overrides,
        "addr_exact": addr_exact,
        "addr_nodir": addr_nodir,
        "street_centroids": street_centroids,
        "buildings_by_hcad": buildings_by_hcad,
        "subdiv_index": subdiv_index,
        "snbr_index": snbr_index,
        "landmarks_by_hcad": landmarks_by_hcad,
        "good_brick_by_hcad": good_brick_by_hcad,
        "smls_by_subdiv": smls_by_subdiv,
        "active_alerts_by_hcad": active_alerts_by_hcad,
    }


_STREET_LOOKUP_RE = re.compile(
    r"\b(\d{1,5})(?:\s*(?:-|&|to)\s*(\d{1,5}))?\s+([A-Za-z0-9\s.\-]+?)\s+"
    r"(Street|St|Avenue|Ave|Drive|Dr|Road|Rd|Boulevard|Blvd|Lane|Ln|Court|Ct|Place|Pl|Circle|Cir|Parkway|Pkwy|Freeway|Fwy|Highway|Hwy|Way)\b",
    re.I,
)


def expand_compound_address_candidates(raw_addr: str) -> list[str]:
    """Expand compound range or ampersand street strings (e.g. '1226 W. Pierce St. A-D & 1230 W. Pierce St. A-D')."""
    s = str(raw_addr or "").strip()
    if not s:
        return []
    out: list[str] = []
    seen: set[str] = set()

    def _push(cand: str) -> None:
        clean = " ".join(cand.split()).strip(" ,;.")
        if clean and clean.upper() not in seen:
            seen.add(clean.upper())
            out.append(clean)

    segments = re.split(r"\s*(?:&|;|\band\b(?=\s+\d))\s*", s, flags=re.I)
    for seg in segments:
        m = _STREET_LOOKUP_RE.search(seg)
        if m:
            st_tail = f"{m.group(3)} {m.group(4)}"
            _push(f"{m.group(1)} {st_tail}")
            if m.group(2):
                _push(f"{m.group(2)} {st_tail}")
    m_full = _STREET_LOOKUP_RE.search(s)
    if m_full:
        st_tail = f"{m_full.group(3)} {m_full.group(4)}"
        _push(f"{m_full.group(1)} {st_tail}")
        if m_full.group(2):
            _push(f"{m_full.group(2)} {st_tail}")
    _push(s)
    return out


def _lookup_single_address_candidate(raw_addr: str, idx: dict) -> dict | None:
    if not raw_addr:
        return None
    m = re.search(
        r"\b(\d{1,5})\s+(?:&\s*\d{1,5}\s+)?([A-Za-z0-9\s.\-]+?)\s+"
        r"(Street|St|Avenue|Ave|Drive|Dr|Road|Rd|Boulevard|Blvd|Lane|Ln|Court|Ct|Place|Pl|Circle|Cir|Parkway|Pkwy|Freeway|Fwy|Highway|Hwy|Way)\b",
        raw_addr,
        re.I,
    )
    if not m:
        return None
    hnum = m.group(1).strip()
    st_full = norm_street(f"{m.group(2)} {m.group(3)}")
    st_nodir = re.sub(r"^[NSEW]\s+", "", st_full)
    hit = idx["addr_exact"].get((hnum, st_full)) or idx["addr_nodir"].get((hnum, st_nodir))
    if hit:
        return dict(hit)
    for delta in (2, -2, 4, -4, 1, -1, 6, -6, 8, -8, 10, -10):
        try:
            cand_num = str(int(hnum) + delta)
        except ValueError:
            break
        near = idx["addr_exact"].get((cand_num, st_full)) or idx["addr_nodir"].get((cand_num, st_nodir))
        if near:
            return {
                "hcad": "",
                "address": f"{hnum} {st_full}",
                "lng": near["lng"],
                "lat": near["lat"],
                "year_built": 0,
                "approximate": True,
            }
    if st_full in idx["street_centroids"]:
        lng, lat = idx["street_centroids"][st_full]
        return {
            "hcad": "",
            "address": f"{hnum} {st_full}",
            "lng": lng,
            "lat": lat,
            "year_built": 0,
            "approximate": True,
        }
    return None


def lookup_address_string_all(raw_addr: str, idx: dict) -> list[dict]:
    """Resolve all exact parcels referenced by a single or compound notice address, or best fallback."""
    if not raw_addr:
        return []
    exact_hits: list[dict] = []
    seen_keys: set[str] = set()
    first_approx: dict | None = None
    for cand in expand_compound_address_candidates(raw_addr):
        res = _lookup_single_address_candidate(cand, idx)
        if not res:
            continue
        if not res.get("approximate"):
            dedup_k = res.get("hcad") or res.get("address") or cand.upper()
            if dedup_k not in seen_keys:
                seen_keys.add(dedup_k)
                exact_hits.append(res)
        elif first_approx is None:
            first_approx = res
    if exact_hits:
        return exact_hits
    return [first_approx] if first_approx else []


def lookup_address_string(raw_addr: str, idx: dict) -> dict | None:
    hits = lookup_address_string_all(raw_addr, idx)
    return hits[0] if hits else None


def parse_xlsx_bytes(xlsx_bytes: bytes) -> list[dict[str, str]]:
    if not xlsx_bytes or not xlsx_bytes.startswith(b"PK\x03\x04"):
        return []
    try:
        z_ctx = zipfile.ZipFile(io.BytesIO(xlsx_bytes))
    except (zipfile.BadZipFile, OSError, ValueError):
        return []
    with z_ctx as z:
        try:
            ns = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            shared: list[str] = []
            if "xl/sharedStrings.xml" in z.namelist():
                root = ET.fromstring(z.read("xl/sharedStrings.xml"))
                for si in root.findall("main:si", ns):
                    shared.append("".join(t.text or "" for t in si.findall(".//main:t", ns)))
            sheet_names = sorted(
                n for n in z.namelist() if n.startswith("xl/worksheets/") and n.endswith(".xml")
            )
            if not sheet_names:
                return []
            sheet_name = sheet_names[0]
            rels_name = sheet_name.replace("worksheets/", "worksheets/_rels/") + ".rels"
            hlinks: dict[str, str] = {}
            if rels_name in z.namelist():
                srels = {
                    r.attrib["Id"]: r.attrib["Target"]
                    for r in ET.fromstring(z.read(rels_name))
                    if "Id" in r.attrib and "Target" in r.attrib
                }
                sroot = ET.fromstring(z.read(sheet_name))
                for hl in sroot.findall(".//main:hyperlink", ns):
                    hrid = hl.attrib.get(
                        "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
                    )
                    if hrid and hrid in srels:
                        hlinks[hl.attrib.get("ref", "")] = srels[hrid]
            sroot = ET.fromstring(z.read(sheet_name))
            rows: list[dict[str, str]] = []
            for row in sroot.findall(".//main:row", ns):
                rdict: dict[str, str] = {}
                for c in row.findall("main:c", ns):
                    cref = c.attrib.get("r", "")
                    col = "".join(ch for ch in cref if ch.isalpha())
                    ctype = c.attrib.get("t")
                    v = c.find("main:v", ns)
                    val = ""
                    if ctype == "s" and v is not None and v.text:
                        idx_s = int(v.text)
                        val = shared[idx_s] if idx_s < len(shared) else ""
                    elif ctype == "inlineStr":
                        val = "".join(t.text or "" for t in c.findall(".//main:t", ns))
                    elif v is not None and v.text:
                        val = v.text
                    if cref in hlinks and not val.lower().startswith("http"):
                        val = hlinks[cref]
                    rdict[col] = val.strip()
                if rdict:
                    rows.append(rdict)
            return rows
        except (ET.ParseError, KeyError, ValueError, zipfile.BadZipFile):
            return []


_DEFAULT_SPREADSHEET_COL_MAP = {
    "name": "A",
    "app": "B",
    "pcDate": "C",
    "submitted": "D",
    "type": "E",
    "special": "F",
    "section": "G",
    "council": "H",
    "city": "I",
    "county": "J",
    "precinct": "K",
    "keymap": "L",
    "tract": "M",
    "zip": "N",
    "isd": "O",
    "prevSub": "P",
    "tirz": "Q",
    "mgmt": "R",
    "hd": "S",
    "superN": "T",
    "park": "U",
    "thoroughfare": "V",
    "landUse": "W",
    "acreage": "X",
    "lots": "Y",
    "hcad": "Z",
    "developer": "AA",
    "org": "AB",
    "applicant": "AC",
    "phone": "AD",
    "pdf": "AE",
}


def _build_col_map_from_header_row(header_row: dict[str, str] | None) -> dict[str, str]:
    """Detect spreadsheet columns by header text across all 2026 COH .xlsx export layouts."""
    if not isinstance(header_row, dict) or not header_row:
        return dict(_DEFAULT_SPREADSHEET_COL_MAP)
    detected: dict[str, str] = {}
    sorted_cols = sorted(
        header_row.items(),
        key=lambda kv: (len(str(kv[0])), str(kv[0])),
    )
    for col, raw_label in sorted_cols:
        lbl = " ".join(re.sub(r"[^a-z0-9\s]", " ", str(raw_label or "").lower()).split())
        if not lbl or lbl.startswith("do not modify"):
            continue
        if lbl in ("subdivision name", "plat name", "project name", "name") or (
            "subdivision" in lbl and "name" in lbl and "prev" not in lbl and "original" not in lbl
        ):
            detected.setdefault("name", col)
        elif lbl in ("app no", "app num", "app number", "application no", "application number", "app"):
            detected.setdefault("app", col)
        elif "pdf" in lbl or "plat link" in lbl or "download subdivision plat" in lbl:
            detected.setdefault("pdf", col)
        elif ("pc" in lbl or "agenda" in lbl or "commission" in lbl) and "date" in lbl:
            detected.setdefault("pcDate", col)
        elif ("record" in lbl or "submit" in lbl or "file" in lbl) and "date" in lbl:
            detected.setdefault("submitted", col)
        elif lbl == "application type":
            # Prefer full descriptive 'Application Type' over short code 'App Type' in 33/44-col COH exports.
            detected["type"] = col
        elif "app" in lbl and "type" in lbl:
            detected.setdefault("type", col)
        elif "special" in lbl or "exception" in lbl or "variance" in lbl:
            detected.setdefault("special", col)
        elif lbl in ("app location", "agenda location", "agenda section", "section") or "section" in lbl:
            detected.setdefault("section", col)
        elif "council" in lbl:
            detected.setdefault("council", col)
        elif lbl in ("in city limits", "city limits", "city etj", "city", "jurisdiction") or (
            "city" in lbl and ("etj" in lbl or "limit" in lbl)
        ):
            detected.setdefault("city", col)
        elif "precinct" in lbl:
            detected.setdefault("precinct", col)
        elif "hcad" in lbl or "appraisal district" in lbl or ("tax" in lbl and "id" in lbl):
            detected.setdefault("hcad", col)
        elif lbl == "county":
            detected.setdefault("county", col)
        elif "key" in lbl and "map" in lbl:
            detected.setdefault("keymap", col)
        elif "census" in lbl or lbl == "tract":
            detected.setdefault("tract", col)
        elif "zip" in lbl:
            detected.setdefault("zip", col)
        elif "school" in lbl or lbl == "isd":
            detected.setdefault("isd", col)
        elif ("prev" in lbl or "original" in lbl) and "sub" in lbl:
            detected.setdefault("prevSub", col)
        elif "tirz" in lbl:
            detected.setdefault("tirz", col)
        elif "management" in lbl or "mgmt" in lbl:
            detected.setdefault("mgmt", col)
        elif "historic" in lbl:
            detected.setdefault("hd", col)
        elif "super" in lbl and "neigh" in lbl:
            detected.setdefault("superN", col)
        elif "park" in lbl and "parking" not in lbl:
            detected.setdefault("park", col)
        elif "thoroughfare" in lbl or "throughfare" in lbl or "major street" in lbl:
            detected.setdefault("thoroughfare", col)
        elif "land" in lbl and "use" in lbl:
            detected.setdefault("landUse", col)
        elif lbl in ("total acreage", "acreage"):
            detected["acreage"] = col
        elif "acre" in lbl and "reserve" not in lbl and "open space" not in lbl:
            detected.setdefault("acreage", col)
        elif "lot" in lbl:
            detected.setdefault("lots", col)
        elif "phone" in lbl:
            detected.setdefault("phone", col)
        elif "developer" in lbl or lbl in ("owner", "property owner"):
            detected.setdefault("developer", col)
        elif (
            lbl in ("organization", "company", "firm", "applicant company", "engineering firm")
            or "org" in lbl
        ):
            detected.setdefault("org", col)
        elif "applicant" in lbl:
            detected.setdefault("applicant", col)
        elif "link" in lbl or "document" in lbl:
            detected.setdefault("pdf", col)
    if "name" in detected and "app" in detected:
        if len(detected) >= 10:
            return {k: detected.get(k, "") for k in _DEFAULT_SPREADSHEET_COL_MAP}
        return {**_DEFAULT_SPREADSHEET_COL_MAP, **detected}
    return dict(_DEFAULT_SPREADSHEET_COL_MAP)


def parse_notice_pdf_text(ntxt: str, rel_path: str = "", nurl: str = "") -> dict:
    """Parse a COH Public Hearing or Section K DPV Notice PDF layout text (F2-04, F2-05, DF-6)."""
    ref_m = re.search(r"REFERENCE NUMBER\s*:\s*([A-Z0-9-]+)", ntxt, re.I)
    proj_m = re.search(r"(?:PROJECT|PLAT) NAME\s*:\s*([^\n]+)", ntxt, re.I)

    loc_str = ""
    loc_start = re.search(r"property located\s+(?:at\s+)?", ntxt, re.I)
    if loc_start:
        tail = ntxt[loc_start.end() : loc_start.end() + 450]
        tail = re.split(r"\n\s*\n", tail, maxsplit=1)[0]
        tail_clean = " ".join(tail.replace("\u037e", ";").split())
        abbrev_re = re.compile(
            r"\b(?:N|S|E|W|NE|NW|SE|SW|St|Ave|Dr|Blvd|Rd|Ln|Ct|Pl|Cir|Pkwy|Fwy|Hwy|Expy|Sq|Ter|Trl|No|Sec|Ste|Apt|Bldg|Mt|Ft)\.$",
            re.I,
        )
        end_idx = len(tail_clean)
        for dot_m in re.finditer(r"\.(?=\s+[A-Z]|\s*$)", tail_clean):
            prefix = tail_clean[: dot_m.end()]
            if abbrev_re.search(prefix) or re.search(r"\b[A-Z]\.$", prefix):
                continue
            end_idx = dot_m.start()
            break
        loc_str = tail_clean[:end_idx].strip(" ,;.")

    req_m = re.search(
        r"(The applicant is requesting[^.]+\.)",
        ntxt.replace("\n", " "),
        re.I,
    )
    meeting_m = re.search(
        r"((?:Thursday|Wednesday),\s+[A-Za-z]+\s+\d{1,2},\s+\d{4})",
        ntxt,
        re.I,
    )

    def _clean_form_cell(raw_cell: str) -> str:
        s = str(raw_cell or "").strip()
        s = re.split(
            r"\s{2,}(?:HCAD|ACREAGE|KEY\s*MAP|COUNCIL|SUPER|ZIP|CENSUS|LOT|BLOCK|SCHOOL|CITY|COUNTY)\b",
            s,
            maxsplit=1,
            flags=re.I,
        )[0]
        s = re.split(
            r"\b(?:ACREAGE|HCAD ACCOUNT NUMBER(?:\(S\))?)\s*:",
            s,
            maxsplit=1,
            flags=re.I,
        )[0]
        out_cell = " ".join(s.split()).strip(" ,;:")
        if re.search(
            r"^(?:the\s+planning\s+and\s+development|planning\s+and\s+development\s+department)\b",
            out_cell,
            re.I,
        ):
            return ""
        return out_cell

    prop_addr_m = re.search(r"PROPERTY ADDRESS[ \t]*:[ \t]*([^\n]+)", ntxt, re.I)
    hcad_line_m = re.search(r"HCAD ACCOUNT NUMBER(?:\(S\))?[ \t]*:[ \t]*([^\n]+)", ntxt, re.I)
    acr_m = re.search(r"\bACREAGE[ \t]*:[ \t]*([0-9]+(?:\.[0-9]+)?)", ntxt, re.I)
    owner_m = re.search(r"PROPERTY OWNER(?: OF RECORD)?[ \t]*:[ \t]*([^\n]+)", ntxt, re.I)
    exist_m = re.search(r"EXISTING STRUCTURE(?:\(S\))?[ \t]*:[ \t]*([^\n]+)", ntxt, re.I)

    prop_addr = _clean_form_cell(prop_addr_m.group(1)) if prop_addr_m else ""
    raw_hcad_cell = _clean_form_cell(hcad_line_m.group(1)) if hcad_line_m else ""
    parsed_hcads = parse_hcad_account_list(raw_hcad_cell)["hcadIds"]
    acreage_str = acr_m.group(1).strip() if acr_m else ""
    owner_str = _clean_form_cell(owner_m.group(1)) if owner_m else ""
    existing_struct_str = _clean_form_cell(exist_m.group(1)) if exist_m else ""
    confirmed_vacant = bool(
        existing_struct_str
        and re.search(
            r"^(?:none|vacant|n/?a|no\s+existing(?:\s+structures?)?|undeveloped|unimproved|empty|cleared|0)$",
            existing_struct_str.strip(),
            re.I,
        )
    )

    if prop_addr and (not loc_str or not re.search(r"^\d{1,5}\b", loc_str)):
        effective_location = prop_addr
    else:
        effective_location = loc_str or prop_addr

    ref_id = ref_m.group(1).strip() if ref_m else ""
    raw_proj_name = proj_m.group(1).strip() if proj_m else ""
    proj_name = re.sub(r"\s+(DPV|VAR/SE|C3N)\s*$", "", raw_proj_name, flags=re.I).strip()
    req_str = " ".join((req_m.group(1) if req_m else "").split())
    meeting_str = meeting_m.group(1).strip() if meeting_m else ""
    meeting_iso = extract_hearing_date_iso(meeting_str)

    # Why strict DPV predicate: Houston Planning Commission Public Hearing Notices carry
    # Chapter 42 boilerplate mentioning 'subdivision or development plat'; true DPV notices
    # carry a DPV filename/reference/suffix or request a 'development plat variance' without
    # a YYYY-NNNN subdivision plat reference number.
    is_subdiv_ref = bool(re.match(r"^\d{4}-\d+$", ref_id))
    is_dpv = bool(
        "DPV" in rel_path.upper()
        or "DPV" in ref_id.upper()
        or re.search(r"\bDPV\s*$", raw_proj_name, re.I)
        or (not is_subdiv_ref and "development plat variance" in ntxt.lower())
    )
    is_ph = (
        "_PH" in rel_path.upper()
        or "-PH" in rel_path.upper()
        or "PUBLIC HEARING" in ntxt.upper()
    )
    return {
        "ref": ref_id,
        "name": proj_name,
        "location": effective_location,
        "noticeProseLocation": loc_str,
        "propertyAddress": prop_addr,
        "hcad": ", ".join(parsed_hcads),
        "owner": owner_str,
        "acreage": acreage_str,
        "existingStructures": existing_struct_str,
        "confirmedVacant": confirmed_vacant,
        "requestSummary": req_str,
        "meetingDate": meeting_str,
        "meetingDateIso": meeting_iso,
        "noticePdf": nurl,
        "isDpv": is_dpv,
        "isPublicHearing": is_ph,
        "fileName": rel_path.split("/")[-1] if rel_path else "",
    }


def _index_historical_spreadsheet_rows(
    cycle_iso: str,
    raw_rows: list[dict[str, str]],
    by_app: dict[str, dict],
    by_subdiv: dict[str, dict],
) -> None:
    """Index spreadsheet rows chronologically by app number and ordinal-preserving Subdivision Name."""
    if not raw_rows or len(raw_rows) <= 1:
        return
    col_map = _build_col_map_from_header_row(raw_rows[0])
    for r in raw_rows[1:]:
        rec = {k: (r.get(col, "").strip() if col else "") for k, col in col_map.items()}
        if not rec["app"] and not rec["name"]:
            continue
        rec["sourceCycleIso"] = cycle_iso

        def _merge_preferring_nonempty(prev: dict | None, cur: dict) -> dict:
            if not prev:
                return dict(cur)
            merged = dict(prev)
            for fk, fv in cur.items():
                if fv or fk not in merged:
                    merged[fk] = fv
            return merged

        app_key = rec["app"].upper()
        if app_key:
            by_app[app_key] = _merge_preferring_nonempty(by_app.get(app_key), rec)
        sub_key = normalize_historical_subdivision_key(rec["name"])
        if sub_key:
            by_subdiv[sub_key] = _merge_preferring_nonempty(by_subdiv.get(sub_key), rec)


def _extract_xlsx_cycle_iso_from_url(url: str) -> str:
    m = re.search(r"(\d{1,2})-(\d{1,2})-(\d{4})\.xlsx$", url, re.I)
    if m:
        return excel_serial_to_iso(f"{m.group(1)}/{m.group(2)}/{m.group(3)}")
    return ""


def _extract_outline_cycle_iso_from_url(url: str) -> str:
    m = re.search(r"(\d{2})(\d{2})(\d{2})\.pdf$", url, re.I)
    if m:
        mm, dd, yy = int(m.group(1)), int(m.group(2)), 2000 + int(m.group(3))
        try:
            return dt.date(yy, mm, dd).isoformat()
        except ValueError:
            return ""
    return ""


def fetch_multi_cycle_agendas_and_notices(max_cycles: int = 3) -> dict:
    """Fetch up to the 3 most recent Planning Commission .xlsx spreadsheets, matching Outline PDFs, and Notice PDFs."""
    print("[2/6] Fetching recent COH Plat Agenda .xlsx cycles, Agenda Outline PDFs, and Notice PDFs...")
    scrape_errors: list[str] = []
    dev_html_b = fetch_bytes(DEV_REPORTS_URL)
    xlsx_by_iso: dict[str, str] = {}
    if dev_html_b:
        dev_html = dev_html_b.decode("utf-8", errors="ignore")
        links = re.findall(
            r'href=["\']([^"\']*Plat-Current-Agenda-Spreadsheet[^"\']*\.xlsx)["\']',
            dev_html,
            re.I,
        )
        for rel in links:
            full = (
                rel
                if rel.lower().startswith("http")
                else "https://www.houstontx.gov/planning/DevelopRegs/" + rel.lstrip("/")
            )
            c_iso = _extract_xlsx_cycle_iso_from_url(full)
            if c_iso:
                xlsx_by_iso[c_iso] = full
        if not xlsx_by_iso:
            scrape_errors.append("No Plat Agenda .xlsx links found on DevelopRegs page")
    else:
        scrape_errors.append("COH Planning DevelopRegs index unreachable")

    if not xlsx_by_iso:
        xlsx_by_iso["2026-10-15"] = (
            "https://www.houstontx.gov/planning/DevelopRegs/docs_pdfs/Plat_report/2026/"
            "Plat-Current-Agenda-Spreadsheet-10-15-2026.xlsx"
        )

    selected_isos = sorted(xlsx_by_iso.keys())[-max_cycles:]

    comm_html_b = fetch_bytes(COMMISS_PLAN_URL)
    outline_url_by_iso: dict[str, str] = {}
    if comm_html_b:
        comm_html = comm_html_b.decode("utf-8", errors="ignore")
        olinks = re.findall(
            r'href=["\']([^"\']*PC-[^"\']*Agenda[^"\']*\.pdf)["\']',
            comm_html,
            re.I,
        )
        for rel in olinks:
            full = (
                rel
                if rel.lower().startswith("http")
                else "https://www.houstontx.gov/planning/Commissions/" + rel.lstrip("/")
            )
            o_iso = _extract_outline_cycle_iso_from_url(full)
            if o_iso and o_iso not in outline_url_by_iso:
                outline_url_by_iso[o_iso] = full
    else:
        scrape_errors.append("COH Planning Commission outline index unreachable")

    notices_html_b = fetch_bytes(NOTICES_URL)
    if not notices_html_b:
        scrape_errors.append("COH Public Hearing Notices page unreachable")
    notices_by_ref: dict[str, dict] = {}
    notices_by_name: dict[str, dict] = {}
    all_notices: list[dict] = []

    if notices_html_b:
        notices_html = notices_html_b.decode("utf-8", errors="ignore")
        nlinks = sorted(
            set(re.findall(r'href=["\']([^"\']*2026/[^"\']*\.pdf)["\']', notices_html, re.I))
        )
        for rel_path in nlinks:
            nurl = (
                rel_path
                if rel_path.lower().startswith("http")
                else NOTICES_URL + rel_path.lstrip("/")
            )
            nb = fetch_bytes(nurl, timeout=15)
            if not nb:
                continue
            ntxt = pdf_to_text(nb)
            ninfo = parse_notice_pdf_text(ntxt, rel_path=rel_path, nurl=nurl)
            ref_id = ninfo["ref"]
            proj_name = ninfo["name"]
            all_notices.append(ninfo)
            if ref_id:
                notices_by_ref[ref_id] = ninfo
            if proj_name:
                notices_by_name[proj_name.lower()] = ninfo

    PLAT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    historical_by_app: dict[str, dict] = {}
    historical_by_subdiv: dict[str, dict] = {}

    cycles_scraped: dict[str, dict] = {}
    all_chronological_xlsx_isos = sorted(xlsx_by_iso.keys())
    selected_set = set(selected_isos)

    for c_iso in all_chronological_xlsx_isos:
        x_url = xlsx_by_iso[c_iso]
        fname = x_url.split("/")[-1]
        cache_path = PLAT_CACHE_DIR / fname
        x_bytes: bytes | None = None
        if c_iso not in selected_set and cache_path.exists() and cache_path.stat().st_size > 0:
            try:
                cand_bytes = cache_path.read_bytes()
                if cand_bytes.startswith(b"PK\x03\x04"):
                    x_bytes = cand_bytes
            except Exception:
                x_bytes = None
        if x_bytes is None:
            x_bytes = fetch_bytes(x_url)
            if x_bytes and x_bytes.startswith(b"PK\x03\x04") and fname.lower().endswith(".xlsx"):
                try:
                    cache_path.write_bytes(x_bytes)
                except Exception:
                    pass
        raw_rows = parse_xlsx_bytes(x_bytes) if x_bytes else []
        _index_historical_spreadsheet_rows(c_iso, raw_rows, historical_by_app, historical_by_subdiv)

        if c_iso not in selected_set:
            continue

        if not x_bytes:
            scrape_errors.append(f"Cycle {c_iso} agenda spreadsheet download failed")
        elif len(raw_rows) <= 1:
            scrape_errors.append(f"Cycle {c_iso} agenda spreadsheet empty or unparseable")

        d_obj = dt.date.fromisoformat(c_iso)
        mmddyy = d_obj.strftime("%m%d%y")
        fallback_outline_url = (
            f"https://www.houstontx.gov/planning/Commissions/docs_pdfs/{d_obj.year}/"
            f"PC-Draft-Agenda-Outline-{mmddyy}.pdf"
        )
        o_url = outline_url_by_iso.get(c_iso) or fallback_outline_url
        o_bytes = fetch_bytes(o_url)
        if not o_bytes:
            if c_iso in outline_url_by_iso:
                scrape_errors.append(f"Cycle {c_iso} agenda outline PDF download failed")
            o_url = ""
            parsed_outline = parse_agenda_outline_text("")
        else:
            parsed_outline = parse_agenda_outline_text(pdf_to_text(o_bytes))

        cycles_scraped[c_iso] = {
            "cycle_iso": c_iso,
            "xlsx_url": x_url,
            "file_name": x_url.split("/")[-1],
            "outline_url": o_url,
            "raw_rows": raw_rows,
            **parsed_outline,
        }
        print(
            f"      Cycle {c_iso}: {max(0, len(raw_rows) - 1)} spreadsheet rows, "
            f"{len(parsed_outline['dpv_items'])} Section K DPVs, "
            f"{len(parsed_outline['sec4_hearing_names'])} Establish-Hearing items "
            f"(target hearing: {parsed_outline['sec4_hearing_date_iso'] or 'N/A'})."
        )

    print(
        f"      Parsed {len(all_notices)} active Public Hearing & Variance Notice PDFs; "
        f"indexed {len(historical_by_subdiv):,} historical 2026 subdivision filings across {len(all_chronological_xlsx_isos)} spreadsheets."
    )
    return {
        "cycles_scraped": cycles_scraped,
        "notices_by_ref": notices_by_ref,
        "notices_by_name": notices_by_name,
        "all_notices": all_notices,
        "historical_by_app": historical_by_app,
        "historical_by_subdiv": historical_by_subdiv,
        "scrapeFailed": bool(scrape_errors),
        "error": "; ".join(scrape_errors),
    }


def _notice_name_compatible(item_name: str, notice_name: str) -> bool:
    """Return True when a spreadsheet item name and Notice PDF project name refer to the same plat."""
    a = norm_subdivision(item_name or "")
    b = norm_subdivision(notice_name or "")
    if not a or not b:
        return True
    if a == b or a in b or b in a:
        return True
    ta = [t for t in a.split() if len(t) >= 3]
    tb = [t for t in b.split() if len(t) >= 3]
    if not ta or not tb:
        return False
    # Require >= 2 shared distinctive tokens or exact single-token match so 'MONTROSE VISTA'
    # never collides with 'MONTROSE RESIDENCES' when COH reuses/mistypes a reference number.
    shared = set(ta) & set(tb)
    min_needed = 1 if min(len(ta), len(tb)) == 1 else 2
    return len(shared) >= min_needed


def _suggest_threat_category(flags: list[str], earliest_yr: int, tier: str) -> str:
    if tier == "3":
        return "No Historic Impact"
    fset = set(flags)
    if "dpv" in fset:
        return "Setback / Walkable Realm Variance"
    if "inst" in fset:
        return "Institutional / Cultural Site"
    if "split" in fset and (not earliest_yr or earliest_yr > 1945):
        return "Lot Split / Subdividing Historic Lot"
    if earliest_yr and 1836 <= earliest_yr <= 1945:
        return "Significant Building Threatened"
    if tier in ("1", "2"):
        return "Neighborhood / District Context Impact"
    return ""


def _recompute_cycle_summary_counts(cyc_data: dict) -> None:
    items = cyc_data.get("items") or []
    reviews = cyc_data.get("reviews") or {}
    summary = cyc_data.setdefault("summary", {})
    summary["totalAgendaItems"] = len(items)
    summary["onMeetingAgendaCount"] = sum(1 for i in items if i.get("onMeetingAgenda"))
    summary["cycleFilingOnlyCount"] = sum(1 for i in items if not i.get("onMeetingAgenda"))
    summary["toCheckCount"] = sum(1 for i in items if (i.get("_c") or {}).get("group") == "check")
    summary["preEnrichedCount"] = len(reviews)
    summary["tier1Count"] = sum(1 for r in reviews.values() if str(r.get("tier") or "") == "1")
    summary["tier2Count"] = sum(1 for r in reviews.values() if str(r.get("tier") or "") == "2")
    summary["tier3Count"] = sum(1 for r in reviews.values() if str(r.get("tier") or "") == "3")


def build_single_cycle_dataset(
    idx: dict,
    cycle_scraped: dict,
    notices_bundle: dict,
    is_latest_cycle: bool = False,
    confirmed_baseline_apps: set[str] | None = None,
) -> dict:
    confirmed_baseline_apps = set(confirmed_baseline_apps or ())
    raw_rows = cycle_scraped["raw_rows"]
    cycle_iso = cycle_scraped["cycle_iso"]
    col_map = _build_col_map_from_header_row(raw_rows[0] if raw_rows else None)

    items: list[dict] = []
    seen_apps: set[str] = set()
    seen_names: set[str] = set()

    for r in raw_rows[1:]:
        it = {k: (r.get(col, "").strip() if col else "") for k, col in col_map.items()}
        if not it["app"] and not it["name"]:
            continue
        it["pcDateIso"] = excel_serial_to_iso(it["pcDate"]) or cycle_iso
        it["submittedIso"] = excel_serial_to_iso(it["submitted"])

        outline_hit = cycle_scraped["outline_by_name"].get(it["name"].lower(), {})
        it["agendaItemNo"] = outline_hit.get("agendaItemNo", "")
        it["deferral"] = outline_hit.get("deferral", "")

        ref_cand = notices_bundle["notices_by_ref"].get(it["app"])
        if ref_cand and not _notice_name_compatible(it["name"], ref_cand.get("name", "")):
            ref_cand = None
        notice_hit = (
            ref_cand
            or notices_bundle["notices_by_name"].get(it["name"].lower())
            or {}
        )
        it["noticePdf"] = notice_hit.get("noticePdf", "")
        it["noticeLocation"] = notice_hit.get("location", "")
        it["varianceSummary"] = notice_hit.get("requestSummary", "")
        if notice_hit.get("hcad") and not it.get("hcad"):
            it["hcad"] = notice_hit["hcad"]
        if notice_hit.get("confirmedVacant"):
            it["_confirmedVacant"] = True
        is_sec4_spreadsheet_match = any(
            it["name"].lower() == s4.lower() for s4 in cycle_scraped.get("sec4_hearing_names") or []
        )
        it["hearingDateIso"] = notice_hit.get("meetingDateIso", "") or (
            (cycle_scraped.get("sec4_hearing_date_iso") or "") if is_sec4_spreadsheet_match else ""
        )
        # Never confuse a notice PDF with a plat drawing PDF
        if it["pdf"] and it["noticePdf"] and it["pdf"] == it["noticePdf"]:
            it["pdf"] = ""

        items.append(it)
        if it["app"]:
            seen_apps.add(it["app"])
        if it["name"]:
            seen_names.add(it["name"].lower())

    default_pc_serial = items[0]["pcDate"] if items else ""
    default_pc_iso = cycle_iso

    # Section K DPV items from this cycle's Agenda Outline PDF (+ matching DPV notices for this meeting date)
    dpv_by_name = {d["name"].lower(): d for d in cycle_scraped["dpv_items"]}
    for ninfo in notices_bundle["all_notices"]:
        if ninfo["isDpv"] and (
            ninfo.get("meetingDateIso") == default_pc_iso
            or (is_latest_cycle and (ninfo.get("meetingDateIso") or "") >= default_pc_iso)
        ):
            key = ninfo["name"].lower()
            if key not in dpv_by_name:
                dpv_by_name[key] = {
                    "agendaItemNo": "",
                    "name": ninfo["name"],
                    "appType": "Development Plat Variance (DPV)",
                    "deferral": "",
                }

    dpv_known_fallbacks = {
        "801 mcgowen street": {
            "app": "DPV-26073533",
            "hcad": "0132680000011",
            "address": "801 MCGOWEN ST",
            "owner": "801 Business LLC",
            "acreage": "0.4408",
            "lng": -95.375082,
            "lat": 29.746475,
            "confirmed_vacant": True,
            "suggested_tier": "3",
            "council": "C",
            "superN": "MIDTOWN",
            "prevSub": "South Side Buffalo Bayou (Midtown)",
            "landUse": "Commercial (Convenience Store, Fuel Canopy & Retail Shell)",
            "applicant": "Jessica Rubio",
            "phone": "713-541-3195",
            "varianceSummary": (
                "Requesting a Development Plat Variance (DPV) to allow auto-related uses in the "
                "pedestrian realm along McGowen Street for a new convenience store, fuel canopy, "
                "and retail shell building at the intersection of McGowen, Milam, and Travis Streets."
            ),
        },
        "4801 n main street": {
            "app": "DPV-4801-NMAIN",
            "hcad": "0331890010001",
            "address": "4801 N MAIN ST",
            "lng": -95.38401,
            "lat": 29.79874,
            "council": "H",
            "superN": "GREATER HEIGHTS",
            "prevSub": "Houston Heights / Sunset Heights",
            "landUse": "Commercial",
            "applicant": "Development Plat Variance Applicant",
            "varianceSummary": "Section K Development Plat with Variance Request (DPV) at 4801 N Main Street in the Greater Heights.",
        },
        "323 wycliffe drive": {
            "app": "DPV-323-WYCLIFFE",
            "hcad": "0915710000011",
            "address": "323 WYCLIFFE DR",
            "lng": -95.57476,
            "lat": 29.76715,
            "council": "G",
            "superN": "MEMORIAL",
            "prevSub": "Memorial Plaza / Rustling Oaks",
            "landUse": "Single Family Residential",
            "applicant": "Development Plat Variance Applicant",
            "varianceSummary": "Section K Development Plat with Variance Request (DPV, Deferred 1) at 323 Wycliffe Drive (1960 residence).",
        },
        "1501 wichman street": {
            "app": "DPV-26075937",
            "hcad": "0352750130042",
            "address": "1501 WICHMAN ST",
            "lng": -95.38835,
            "lat": 29.77512,
            "council": "C",
            "superN": "WASHINGTON AVENUE COALITION / MEMORIAL PARK",
            "prevSub": "Lockwood Addition",
            "landUse": "Single Family Residential",
            "applicant": "Eric Hughes",
            "varianceSummary": "Requesting a 5-foot building line in lieu of the 10-foot garage building line along East 2nd Street for a new single-family residence.",
        },
    }

    for key, dpv in dpv_by_name.items():
        if key in seen_names:
            continue
        ninfo = notices_bundle["notices_by_name"].get(key, {})
        fb = dpv_known_fallbacks.get(key, {})
        app_id = (
            (
                f"DPV-{ninfo['ref']}"
                if ninfo.get("ref") and not ninfo["ref"].startswith("DPV")
                else ninfo.get("ref")
            )
            or fb.get("app")
            or f"DPV-{dpv.get('agendaItemNo') or len(items) + 1}"
        )
        it = {
            "name": dpv["name"],
            "app": app_id,
            "pcDate": default_pc_serial,
            "pcDateIso": default_pc_iso,
            "submitted": default_pc_serial,
            "submittedIso": default_pc_iso,
            "hearingDateIso": ninfo.get("meetingDateIso") or default_pc_iso,
            "type": "Development Plat Variance (DPV)",
            "special": "Development Plat Variance (Section K)",
            "section": "K. Development Plat Variances",
            "council": fb.get("council", ""),
            "city": "City",
            "county": "Harris",
            "precinct": "",
            "keymap": "",
            "tract": "",
            "zip": "",
            "isd": "HISD",
            "prevSub": fb.get("prevSub", "Existing Urban Parcel (Development Plat Variance)"),
            "tirz": "",
            "mgmt": "",
            "hd": "",
            "superN": fb.get("superN", ""),
            "park": "",
            "thoroughfare": "",
            "landUse": fb.get("landUse", "Commercial / Redevelopment"),
            "acreage": str(ninfo.get("acreage") or fb.get("acreage") or ""),
            "lots": "1",
            "hcad": str(ninfo.get("hcad") or fb.get("hcad") or ""),
            "developer": str(ninfo.get("owner") or fb.get("owner") or ""),
            "org": "",
            "applicant": fb.get("applicant", ""),
            "phone": fb.get("phone", ""),
            "pdf": "",
            "noticePdf": ninfo.get("noticePdf", ""),
            "noticeLocation": ninfo.get("location") or dpv["name"],
            "varianceSummary": ninfo.get("requestSummary") or fb.get("varianceSummary", ""),
            "agendaItemNo": dpv.get("agendaItemNo", ""),
            "deferral": dpv.get("deferral", ""),
            "_fallbackCoords": (fb.get("lng"), fb.get("lat")) if fb.get("lng") else None,
            "_fallbackAddr": str(ninfo.get("propertyAddress") or fb.get("address") or ""),
            "_confirmedVacant": bool(ninfo.get("confirmedVacant") or fb.get("confirmed_vacant")),
            "_overrideTier": str(fb.get("suggested_tier") or ""),
        }
        items.append(it)
        seen_apps.add(app_id)
        seen_names.add(key)

    # Add Section III/IV (Establish a Public Hearing Date) & active upcoming Public Hearing Notices
    sec_num = cycle_scraped.get("sec4_numeral") or "IV"
    sec_target_iso = cycle_scraped.get("sec4_hearing_date_iso") or ""

    for ninfo in notices_bundle["all_notices"]:
        if ninfo["isDpv"]:
            continue
        ref = ninfo.get("ref") or ""
        name = ninfo.get("name") or ""
        if not name or (ref and ref in seen_apps) or name.lower() in seen_names:
            continue

        is_in_establish_sec = any(
            name.lower() == s4.lower() for s4 in cycle_scraped["sec4_hearing_names"]
        )
        notice_hearing_iso = ninfo.get("meetingDateIso") or ""
        belongs_to_cycle = is_in_establish_sec or (notice_hearing_iso == default_pc_iso) or (
            is_latest_cycle and notice_hearing_iso and notice_hearing_iso >= default_pc_iso
        )
        if not belongs_to_cycle:
            continue

        resolved_hearing_iso = (
            (sec_target_iso if is_in_establish_sec and sec_target_iso else "")
            or notice_hearing_iso
        )
        human_hearing = format_human_us_date(resolved_hearing_iso, short_month=True)
        if is_in_establish_sec:
            section_label = (
                f"{sec_num}. Establish Public Hearing Date ({human_hearing})"
                if human_hearing
                else f"{sec_num}. Establish Public Hearing Date"
            )
            special_label = (
                f"Public Hearing Notification (Hearing Date: {human_hearing})"
                if human_hearing
                else "Public Hearing Notification"
            )
        else:
            section_label = (
                f"C. Public Hearing Notice Posted (Hearing: {human_hearing})"
                if human_hearing
                else "C. Public Hearing Notice Posted"
            )
            special_label = (
                f"Public Hearing Notification (Hearing Date: {human_hearing})"
                if human_hearing
                else "Public Hearing Notification"
            )

        it = {
            "name": name,
            "app": ref or f"PH-{len(items) + 1}",
            "pcDate": default_pc_serial,
            "pcDateIso": default_pc_iso,
            "submitted": default_pc_serial,
            "submittedIso": default_pc_iso,
            "hearingDateIso": resolved_hearing_iso,
            "type": "Class 3 Replat with Public Hearing Notice (C3N)",
            "special": special_label,
            "section": section_label,
            "council": "",
            "city": "City",
            "county": "Harris",
            "precinct": "",
            "keymap": "",
            "tract": "",
            "zip": "",
            "isd": "",
            "prevSub": name,
            "tirz": "",
            "mgmt": "",
            "hd": "",
            "superN": "",
            "park": "",
            "thoroughfare": "",
            "landUse": "Single Family Residential Replat",
            "acreage": "",
            "lots": "",
            "hcad": "",
            "developer": "",
            "org": "",
            "applicant": "",
            "phone": "",
            "pdf": "",
            "noticePdf": ninfo.get("noticePdf", ""),
            "noticeLocation": ninfo.get("location", ""),
            "varianceSummary": ninfo.get("requestSummary", ""),
            "agendaItemNo": f"Sec. {sec_num}" if is_in_establish_sec else "",
            "deferral": "",
        }
        items.append(it)
        if ref:
            seen_apps.add(ref)
        seen_names.add(name.lower())

    # Emit Agenda Outline Section III/IV Establish-Hearing items whose Notice PDFs are not yet posted
    human_sec4_hearing = format_human_us_date(sec_target_iso, short_month=True)
    sec4_section_label = (
        f"{sec_num}. Establish Public Hearing Date ({human_sec4_hearing})"
        if human_sec4_hearing
        else f"{sec_num}. Establish Public Hearing Date"
    )
    sec4_special_label = (
        f"Establish Public Hearing Date ({human_sec4_hearing})"
        if human_sec4_hearing
        else "Establish Public Hearing Date"
    )
    for s4_raw in cycle_scraped.get("sec4_hearing_names") or []:
        s4_name = str(s4_raw or "").strip()
        if not s4_name or s4_name.lower() in seen_names:
            continue
        items.append(
            {
                "name": s4_name,
                "app": f"PH-{len(items) + 1}",
                "pcDate": default_pc_serial,
                "pcDateIso": default_pc_iso,
                "submitted": default_pc_serial,
                "submittedIso": default_pc_iso,
                "hearingDateIso": sec_target_iso,
                "type": "Class 3 Replat - Establish Public Hearing Date (C3N)",
                "special": sec4_special_label,
                "section": sec4_section_label,
                "council": "",
                "city": "City",
                "county": "Harris",
                "precinct": "",
                "keymap": "",
                "tract": "",
                "zip": "",
                "isd": "",
                "prevSub": s4_name,
                "tirz": "",
                "mgmt": "",
                "hd": "",
                "superN": "",
                "park": "",
                "thoroughfare": "",
                "landUse": "Single Family Residential Replat",
                "acreage": "",
                "lots": "",
                "hcad": "",
                "developer": "",
                "org": "",
                "applicant": "",
                "phone": "",
                "pdf": "",
                "noticePdf": "",
                "noticeLocation": "",
                "varianceSummary": "",
                "agendaItemNo": f"Sec. {sec_num}",
                "deferral": "",
            }
        )
        seen_names.add(s4_name.lower())

    # Backfill notice-only C3N and Sec. III/IV PH-* items from historical 2026 Plat Tracker spreadsheets (F2-07, DF-7)
    hist_by_app = notices_bundle.get("historical_by_app") or {}
    hist_by_subdiv = notices_bundle.get("historical_by_subdiv") or {}
    for it in items:
        app_upper = str(it.get("app") or "").strip().upper()
        is_ph_placeholder = app_upper.startswith("PH-")
        needs_hist = is_ph_placeholder or (
            it.get("noticePdf") and not it.get("pdf") and not it.get("developer") and not it.get("hcad")
        )
        if not needs_hist:
            continue
        hist: dict | None = None
        if not is_ph_placeholder and app_upper in hist_by_app:
            cand_h = hist_by_app[app_upper]
            if _notice_name_compatible(it.get("name", ""), cand_h.get("name", "")):
                hist = cand_h
        if hist is None:
            sub_key = normalize_historical_subdivision_key(it.get("name", ""))
            if sub_key and sub_key in hist_by_subdiv:
                hist = hist_by_subdiv[sub_key]
        if not hist:
            continue
        if is_ph_placeholder and hist.get("app"):
            it["linkedAppNo"] = hist["app"]
        if hist.get("sourceCycleIso"):
            it["linkedSourceCycle"] = hist["sourceCycleIso"]
        for fk in (
            "council",
            "city",
            "county",
            "precinct",
            "keymap",
            "tract",
            "zip",
            "isd",
            "tirz",
            "mgmt",
            "hd",
            "superN",
            "park",
            "thoroughfare",
            "acreage",
            "lots",
            "hcad",
            "developer",
            "org",
            "applicant",
            "phone",
            "pdf",
        ):
            if hist.get(fk) and (not it.get(fk) or (fk == "city" and it.get("city") == "City")):
                it[fk] = hist[fk]
        if hist.get("prevSub") and (not it.get("prevSub") or it["prevSub"] == it.get("name")):
            it["prevSub"] = hist["prevSub"]
        if hist.get("landUse") and it.get("landUse") in ("", "Single Family Residential Replat"):
            it["landUse"] = hist["landUse"]
        if hist.get("submitted") and it.get("submittedIso") == default_pc_iso:
            it["submitted"] = hist["submitted"]
            it["submittedIso"] = excel_serial_to_iso(hist["submitted"]) or it["submittedIso"]
        if it.get("pdf") and it.get("noticePdf") and it["pdf"] == it["noticePdf"]:
            it["pdf"] = ""

    notice_overrides = {
        "2026-1585": {
            "name": "Montrose Residences",
            "hcad": (
                "1490700010001, 1490700010002, 1490700010003, 1490700010004, "
                "1490700020001, 1490700020002, 1490700020003, 1490700020004"
            ),
            "address": "1226-1230 W PIERCE ST",
            "prefer_override_address": True,
            "lng": -95.39498,
            "lat": 29.75421,
            "year_built": 2025,
            "suggested_tier": "3",
            "superN": "NEARTOWN - MONTROSE",
            "prevSub": "Montrose Vista",
        },
        "2026-1641": {
            "name": "Wheeler Residences",
            "hcad": "0611650000001",
            "address": "2802 WHEELER ST",
            "lng": -95.36754,
            "lat": 29.72457,
            "year_built": 1940,
            "superN": "GREATER THIRD WARD",
            "prevSub": "Riverside / Third Ward",
        },
        "2026-1569": {
            "name": "Kafi Commons",
            "hcad": "0410170020150",
            "address": "4710 W ALABAMA ST",
            "lng": -95.45692,
            "lat": 29.73812,
            "year_built": 1955,
            "superN": "AFTON OAKS / RIVER OAKS AREA",
            "prevSub": "Weslayan Plaza / Afton Oaks",
        },
        "2026-1520": {
            "name": "Sandalwood",
            "hcad": "0912960000011",
            "address": "54 PATTI LYNN LN",
            "lng": -95.53440,
            "lat": 29.75224,
            "year_built": 1958,
            "superN": "MEMORIAL",
            "prevSub": "Sandalwood Sec 2 and 3",
        },
        "2026-1570": {
            "name": "Bacher Manor",
            "hcad": "0381770000024",
            "address": "5603 BACHER ST",
            "lng": -95.27768,
            "lat": 29.81229,
            "year_built": 1933,
            "superN": "SETTEGAST",
            "prevSub": "Holleman's Addition",
        },
    }

    for it in items:
        raw_ov = notice_overrides.get(it["app"]) or notice_overrides.get(it.get("linkedAppNo", "")) or {}
        ov = (
            raw_ov
            if raw_ov and (not raw_ov.get("name") or _notice_name_compatible(it.get("name", ""), raw_ov["name"]))
            else {}
        )
        if ov:
            if not it.get("hcad") and ov.get("hcad"):
                it["hcad"] = ov["hcad"]
            if not it.get("superN") and ov.get("superN"):
                it["superN"] = ov["superN"]
            if it.get("prevSub") == it.get("name") and ov.get("prevSub"):
                it["prevSub"] = ov["prevSub"]
            if ov.get("suggested_tier"):
                it["_overrideTier"] = str(ov["suggested_tier"])
        it["onMeetingAgenda"] = bool(str(it.get("agendaItemNo") or "").strip())

        hcad_ids = parse_hcad_account_list(it.get("hcad", ""))["hcadIds"]
        matched_parcels: list[dict] = []
        for hid in hcad_ids:
            if hid in idx["buildings_by_hcad"]:
                b = idx["buildings_by_hcad"][hid]
                h_rec = idx["hcad_map"].get(hid, {})
                all_yrs = sorted(
                    set(list(b.get("all_years") or []) + list(h_rec.get("all_years") or []))
                )
                matched_parcels.append(
                    {
                        "hcad": hid,
                        "address": b["address"] or h_rec.get("address", ""),
                        "year_built": b["year_built"] or h_rec.get("year_built", 0),
                        "all_years": all_yrs,
                        "lng": b["lng"] or h_rec.get("lng"),
                        "lat": b["lat"] or h_rec.get("lat"),
                        "building_id": b["building_id"],
                        "building_name": b["building_name"],
                        "style": b["style"],
                        "architect": b["architect"],
                        "historic_district": b["historic_district"],
                        "landmark_name": b["landmark_name"],
                        "good_brick_summary": b["good_brick_summary"],
                    }
                )
            elif hid in idx["hcad_map"]:
                p = idx["hcad_map"][hid]
                matched_parcels.append(
                    {
                        "hcad": hid,
                        "address": p["address"],
                        "year_built": p["year_built"],
                        "all_years": list(p.get("all_years") or []),
                        "lng": p["lng"],
                        "lat": p["lat"],
                    }
                )

        if not matched_parcels:
            if ov:
                matched_parcels.append(
                    {
                        "hcad": ov["hcad"],
                        "address": ov["address"],
                        "year_built": ov["year_built"],
                        "all_years": [ov["year_built"]] if ov.get("year_built") else [],
                        "lng": ov["lng"],
                        "lat": ov["lat"],
                    }
                )
            else:
                for cand_text in [
                    it.get("noticeLocation", ""),
                    it.get("_fallbackAddr", ""),
                    it.get("name", ""),
                ]:
                    hits = lookup_address_string_all(cand_text, idx)
                    if hits:
                        matched_parcels.extend(hits)
                        hit_hcads = [h["hcad"] for h in hits if h.get("hcad")]
                        if hit_hcads and not it.get("hcad"):
                            it["hcad"] = ", ".join(dict.fromkeys(hit_hcads))
                        break
                if not matched_parcels and it.get("_fallbackCoords"):
                    lng_fb, lat_fb = it["_fallbackCoords"]
                    matched_parcels.append(
                        {
                            "hcad": it.get("hcad", ""),
                            "address": it.get("_fallbackAddr") or it["name"],
                            "year_built": 0,
                            "all_years": [],
                            "lng": lng_fb,
                            "lat": lat_fb,
                            "confirmedVacant": bool(it.get("_confirmedVacant")),
                        }
                    )

        if ov and matched_parcels:
            if ov.get("year_built") and not any(p.get("year_built") for p in matched_parcels):
                matched_parcels[0]["year_built"] = ov["year_built"]
                matched_parcels[0]["all_years"] = [ov["year_built"]]
            if ov.get("lng") and ov.get("lat") and not any(p.get("lng") and p.get("lat") for p in matched_parcels):
                matched_parcels[0]["lng"] = ov["lng"]
                matched_parcels[0]["lat"] = ov["lat"]

        sub_norm = norm_subdivision(it.get("prevSub", ""))
        sub_hit = idx["subdiv_index"].get(sub_norm)
        if not sub_hit and sub_norm and len(sub_norm) >= 6:
            for k_sub, v_sub in idx["subdiv_index"].items():
                if len(k_sub) >= 6 and (
                    k_sub.startswith(sub_norm + " ")
                    or sub_norm.startswith(k_sub + " ")
                    or (len(k_sub.split()) >= 2 and (sub_norm in k_sub or k_sub in sub_norm))
                ):
                    sub_hit = v_sub
                    break

        sn_norm = " ".join(re.sub(r"[^A-Z0-9\s]", " ", (it.get("superN") or "").upper()).split())
        sn_hit = idx.get("snbr_index", {}).get(sn_norm)
        if not sn_hit and sn_norm:
            for k_sn, v_sn in idx.get("snbr_index", {}).items():
                if sn_norm in k_sn or k_sn in sn_norm:
                    sn_hit = v_sn
                    break

        smls_hits = idx["smls_by_subdiv"].get(sub_norm, [])

        tier_res = resolve_multi_parcel_building_tier(
            matched_parcels,
            curated_overrides=idx.get("curated_overrides"),
            confirmed_vacant=bool(it.get("_confirmedVacant")),
            override_suggested_tier=str(it.get("_overrideTier") or ""),
        )
        earliest_yr = tier_res["oldestYearBuilt"]
        years = tier_res["allYearsBuilt"]
        raw_addresses = [
            " ".join(str(p.get("address") or "").split())
            for p in matched_parcels
            if str(p.get("address") or "").strip()
        ]
        non_zero_addresses = [a for a in raw_addresses if not re.match(r"^0(?:\s+|$)", a)]
        addresses = non_zero_addresses if non_zero_addresses else raw_addresses
        if ov.get("prefer_override_address") and ov.get("address"):
            primary_addr = " ".join(str(ov["address"]).split())
            if primary_addr not in addresses:
                addresses.insert(0, primary_addr)
        else:
            primary_addr = "; ".join(dict.fromkeys(addresses))

        lng, lat = (None, None)
        for p in matched_parcels:
            if p.get("lng") and p.get("lat"):
                lng, lat = p["lng"], p["lat"]
                break
        coord_source = "parcel" if (lng and lat) else ""
        if (not lng or not lat) and sub_hit:
            lng = sub_hit.get("label_lng")
            lat = sub_hit.get("label_lat")
            coord_source = "subdivision"
        if (not lng or not lat) and sn_hit:
            lng = sn_hit.get("label_lng")
            lat = sn_hit.get("label_lat")
            coord_source = "super_neighborhood"

        designations: list[str] = list(tier_res["designations"])
        if it.get("hd"):
            designations.insert(0, f"City historic district ({it['hd']})")
        for p in matched_parcels:
            hid = p.get("hcad", "")
            if hid in idx["landmarks_by_hcad"]:
                lm = idx["landmarks_by_hcad"][hid]
                lm_type = "Protected Landmark" if lm.get("protected") else "City Landmark"
                designations.append(lm_type)
            if hid in idx["good_brick_by_hcad"]:
                designations.append("Good Brick Award")

        active_alerts = [
            idx["active_alerts_by_hcad"][p["hcad"]]
            for p in matched_parcels
            if p.get("hcad") in idx["active_alerts_by_hcad"]
        ]

        it["occurrenceKey"] = build_occurrence_key(default_pc_iso, it["app"])
        it["atlas"] = {
            "addresses": addresses,
            "primaryAddress": primary_addr,
            "earliestYearBuilt": earliest_yr,
            "allYearsBuilt": years,
            "parcelBreakdown": tier_res["parcels"],
            "lng": round(float(lng), 6) if lng else None,
            "lat": round(float(lat), 6) if lat else None,
            "coordSource": coord_source,
            "buildingId": next((p.get("building_id") for p in matched_parcels if p.get("building_id")), ""),
            "buildingName": next((p.get("building_name") for p in matched_parcels if p.get("building_name")), ""),
            "subdivisionMatch": sub_hit.get("full_name") or sub_hit.get("name") if sub_hit else "",
            "subdivisionId": sub_hit.get("id") if sub_hit else "",
            "subdivisionEarliestYear": sub_hit.get("earliest_year") if sub_hit else None,
            "subdivisionMedianYear": sub_hit.get("median_year") if sub_hit else None,
            "subdivisionPre1940Count": sub_hit.get("pre_1940_count") if sub_hit else 0,
            "subdivisionPre1965Count": sub_hit.get("pre_1965_count") if sub_hit else 0,
            "superNeighborhoodMatch": sn_hit.get("name") if sn_hit else "",
            "superNeighborhoodId": sn_hit.get("id") if sn_hit else "",
            "superNeighborhoodEarliestYear": sn_hit.get("earliest_year") if sn_hit else None,
            "superNeighborhoodMedianYear": sn_hit.get("median_year") if sn_hit else None,
            "designations": list(dict.fromkeys(designations)),
            "smlsProtections": [lp.get("type", "SMLS/SMBL") for lp in smls_hits[:3]],
            "hasActivePermit": bool(active_alerts),
            "activePermitHeadline": active_alerts[0].get("headline", "") if active_alerts else "",
        }
        it.pop("_fallbackCoords", None)
        it.pop("_fallbackAddr", None)
        it["_c"] = classify_item(it)

    preseeded_reviews: dict[str, dict] = {}
    for it in items:
        at = it["atlas"]
        c = it["_c"]
        if c["group"] != "check":
            continue
        yr = at.get("earliestYearBuilt") or 0
        sub_yr = at.get("subdivisionEarliestYear") or 0
        addr = at.get("primaryAddress") or it.get("noticeLocation") or ""
        des = (
            "City historic district"
            if it.get("hd")
            else (
                at["designations"][0]
                if at.get("designations")
                else ("None found" if (yr or addr) else "Not checked yet")
            )
        )

        tier_eval = resolve_multi_parcel_building_tier(
            [
                {
                    "allYears": at.get("allYearsBuilt") or ([yr] if yr else []),
                    "designations": at.get("designations") or [],
                }
            ],
            flags=c["flags"],
            super_n=it.get("superN", ""),
            confirmed_vacant=bool(it.get("_confirmedVacant")),
            override_suggested_tier=str(it.get("_overrideTier") or ""),
        )
        tier = tier_eval["suggestedTier"]
        is_confirmed_vacant = bool(tier_eval.get("confirmedVacant"))
        why_parts: list[str] = []
        sources_list: list[str] = []
        followup = ""

        if it.get("pdf"):
            sources_list.append(it["pdf"])
        if it.get("noticePdf") and it.get("noticePdf") != it.get("pdf"):
            sources_list.append(it["noticePdf"])

        if "dpv" in c["flags"]:
            why_parts.append(
                f"Section K Development Plat Variance ({it['app']}) in {it['superN'] or 'Houston'}."
            )
            if is_confirmed_vacant:
                why_parts.append("Notice application verifies no existing structures on the parcel (vacant site).")
            if it.get("varianceSummary"):
                why_parts.append(it["varianceSummary"])
            followup = (
                "No existing building threatened on the parcel; monitor only if adjacent historic district context applies."
                if tier == "3"
                else "Review site plan in DPV packet before Wednesday 2:30 p.m. speaker comment deadline."
            )
        elif at.get("designations"):
            why_parts.append(
                f"Atlas matched designated historic resource ({', '.join(at['designations'])}) at {addr or it['name']}."
            )
            if it.get("varianceSummary"):
                why_parts.append(it["varianceSummary"])
            followup = "Evaluate impact on designated historic resource and check HAHC / Planning Commission deadlines."
        elif yr and yr <= 1965 and (
            "hearing" in c["flags"]
            or "variance" in c["flags"]
            or "split" in c["flags"]
            or "use" in c["flags"]
            or yr <= 1935
        ):
            why_parts.append(
                f"Atlas matched HCAD parcel at {addr} with a standing structure built in {yr} "
                f"in {it.get('prevSub') or it.get('superN')}."
            )
            if it.get("varianceSummary"):
                why_parts.append(it["varianceSummary"])
            elif "split" in c["flags"]:
                why_parts.append(
                    f"Proposed replat divides {it.get('acreage')} ac into {it.get('lots')} lots ({it.get('landUse')})."
                )
            elif "use" in c["flags"]:
                why_parts.append(f"Proposed land use is {it.get('landUse')}.")
            followup = (
                "Confirm structure condition on Street View and check COH Sold Permits."
                if tier == "2"
                else "Public hearing / variance opportunity - evaluate for comment to speakercomments.pc@houstontx.gov."
            )
        elif yr and yr <= 1976:
            why_parts.append(
                f"Atlas matched HCAD parcel at {addr} (built {yr}) in {it.get('prevSub') or it.get('superN')}."
            )
            followup = "Monitor for demolition or building permit filings."
        elif tier == "3" and yr and yr > 1976:
            why_parts.append(
                f"Atlas matched HCAD parcel at {addr} (built {yr}) in {it.get('prevSub') or it.get('superN')}; no pre-1976 structure threatened."
            )

        if addr or yr or tier or why_parts:
            is_confirmed = it["app"] in confirmed_baseline_apps
            preseeded_reviews[it["app"]] = {
                "address": addr,
                "yearBuilt": str(yr) if yr else (
                    "Vacant parcel" if is_confirmed_vacant else (f"ca. {sub_yr} (subdiv.)" if sub_yr and sub_yr <= 1955 else "")
                ),
                "designation": des,
                "tier": tier,
                "tierConfirmed": is_confirmed,
                "threatCategory": _suggest_threat_category(c["flags"], yr, tier),
                "why": " ".join(why_parts),
                "sources": "\n".join(sources_list),
                "followup": followup,
                "autoEnriched": True,
                "updated": dt.datetime.now(dt.timezone.utc).isoformat(),
            }

    for it in items:
        it.pop("_confirmedVacant", None)
        it.pop("_overrideTier", None)

    cyc_payload = {
        "cycleKey": default_pc_iso,
        "meetingDate": default_pc_iso,
        "pcDate": f"{default_pc_iso}T00:00:00.000Z",
        "fileName": cycle_scraped["file_name"],
        "sourceSpreadsheetUrl": cycle_scraped["xlsx_url"],
        "agendaOutlineUrl": cycle_scraped["outline_url"],
        "summary": {
            "totalAgendaItems": len(items),
            "spreadsheetPlatCount": max(0, len(raw_rows) - 1),
            "dpvVarianceCount": len(dpv_by_name),
            "publicHearingNoticeCount": len(notices_bundle["all_notices"]),
        },
        "items": items,
        "reviews": preseeded_reviews,
    }
    _recompute_cycle_summary_counts(cyc_payload)
    return cyc_payload


def _canonical_situs_match_key(raw_addr: object) -> str:
    """Build a strict positive house-number + street-root match key (rejecting house <= 0)."""
    simp = simplify_search_address(str(raw_addr or "")).upper().strip()
    if not simp:
        return ""
    m = re.match(r"^(\d+)[A-Z]?\s+(?:1/2\s+)?(.+)$", simp)
    if not m:
        return ""
    num_int = int(m.group(1))
    if num_int <= 0:
        return ""
    street = re.sub(r"[^A-Z0-9]+", " ", m.group(2)).strip()
    if len(street) < 2:
        return ""
    return f"{num_int}|{street}"


def _reconcile_watchlist_tiers_onto_cycles(
    cycles_out: dict[str, dict],
    chronological_isos: list[str],
    cloud_watch_properties: dict[str, dict] | None,
) -> None:
    """Propagate staff-edited Watchlist tiers onto untouched or older matching plat reviews (F2-01)."""
    if not cloud_watch_properties:
        return
    for wp in cloud_watch_properties.values():
        if not isinstance(wp, dict):
            continue
        if is_removed_watchlist_tombstone(wp) or is_legacy_unconfirmed_plat_seed(wp):
            continue
        if wp.get("humanEdited") is not True:
            continue
        wp_tier = str(wp.get("tier") or "").strip()
        if wp_tier not in ("1", "2", "3"):
            continue
        wp_updated = str(wp.get("updatedAt") or "").strip()
        wp_src_cycle = str(wp.get("sourceCycleKey") or "").strip()
        wp_app_no = str(wp.get("appNo") or "").strip()
        wp_app_ids = set(get_canonical_plat_app_ids(wp))
        wp_hcads = set(parse_hcad_account_list(wp.get("hcadAccount") or "")["hcadIds"])
        wp_norm_name = normalize_continuity_plat_name(
            str(wp.get("platProjectName") or wp.get("propertyName") or "")
        )
        wp_addr_key = _canonical_situs_match_key(wp.get("address"))

        for c_iso in chronological_isos:
            cyc = cycles_out.get(c_iso)
            if not cyc:
                continue
            reviews = cyc.setdefault("reviews", {})
            for it in cyc.get("items") or []:
                cur_rev = dict(reviews.get(it["app"]) or {})
                it_hcads = parse_hcad_account_list(
                    " ".join(
                        [
                            str(cur_rev.get("hcad") or ""),
                            str(it.get("hcad") or ""),
                            str((it.get("atlas") or {}).get("hcad_num") or ""),
                        ]
                    )
                )["hcadIds"]
                it_hcad_set = set(it_hcads)
                it_norm_name = normalize_continuity_plat_name(str(it.get("name") or ""))
                it_addr_key = _canonical_situs_match_key(
                    cur_rev.get("address")
                    or (it.get("atlas") or {}).get("primaryAddress")
                    or it.get("noticeLocation")
                )

                exact_source = bool(wp_src_cycle == c_iso and wp_app_no and it["app"] == wp_app_no)
                app_match = False
                if not exact_source and wp_app_ids and (wp_app_ids & set(get_canonical_plat_app_ids(it))):
                    app_match = _are_continuity_snapshots_compatible(
                        it_hcads, it_norm_name, sorted(wp_hcads), wp_norm_name
                    )
                hcad_match = bool(wp_hcads and it_hcad_set and (wp_hcads & it_hcad_set))
                addr_match = bool(
                    wp_addr_key
                    and it_addr_key == wp_addr_key
                    and not (wp_hcads and it_hcad_set and not (wp_hcads & it_hcad_set))
                )
                if not (exact_source or app_match or hcad_match or addr_match):
                    continue

                rev_updated = str(cur_rev.get("updated") or "").strip()
                if rev_updated and (not wp_updated or wp_updated <= rev_updated):
                    continue

                cur_rev["tier"] = wp_tier
                cur_rev["tierConfirmed"] = wp_tier in ("1", "2")
                if wp_updated:
                    cur_rev["updated"] = wp_updated
                if wp.get("updatedBy") and not cur_rev.get("reviewer"):
                    cur_rev["reviewer"] = str(wp["updatedBy"])
                prev_hf = (
                    list(cur_rev.get("humanEditedFields"))
                    if isinstance(cur_rev.get("humanEditedFields"), list)
                    else []
                )
                if "tier" not in prev_hf:
                    prev_hf.append("tier")
                cur_rev["humanEditedFields"] = prev_hf
                reviews[it["app"]] = cur_rev
                it["_c"] = classify_item(it, cur_rev)


def build_plat_agenda_dataset(
    idx: dict,
    scraped_multi: dict,
    cloud_plat_reviews: dict[tuple[str, str], dict] | None = None,
    confirmed_baseline_apps: set[str] | None = None,
    cloud_watch_properties: dict[str, dict] | None = None,
) -> dict:
    print("[3/6] Cross-referencing multi-cycle Plat Agendas, Section K DPVs, and Hearing Notices with Atlas...")
    confirmed_baseline_apps = set(confirmed_baseline_apps) if confirmed_baseline_apps is not None else set()
    cycles_scraped = scraped_multi["cycles_scraped"]
    chronological_isos = sorted(cycles_scraped.keys())
    latest_iso = chronological_isos[-1]

    cycles_out: dict[str, dict] = {}
    for c_iso in chronological_isos:
        cyc_data = build_single_cycle_dataset(
            idx,
            cycles_scraped[c_iso],
            scraped_multi,
            is_latest_cycle=(c_iso == latest_iso),
            confirmed_baseline_apps=confirmed_baseline_apps,
        )
        items_by_app = {it["app"]: it for it in cyc_data["items"]}
        # Apply any cloud-synced staff reviews for this cycle using safe machine-vs-human overlay rules (DF-1)
        if cloud_plat_reviews:
            for (rev_cycle, app_no), cloud_rev in cloud_plat_reviews.items():
                if rev_cycle == c_iso or not rev_cycle:
                    target_it = items_by_app.get(app_no)
                    merged = merge_plat_review_overlay(
                        cyc_data["reviews"].get(app_no),
                        cloud_rev,
                        target_it,
                    )
                    cyc_data["reviews"][app_no] = merged
                    if target_it is not None:
                        target_it["_c"] = classify_item(target_it, merged)
        cycles_out[c_iso] = cyc_data

    _reconcile_watchlist_tiers_onto_cycles(cycles_out, chronological_isos, cloud_watch_properties)
    link_cross_cycle_history(cycles_out, chronological_isos)
    for cyc_data in cycles_out.values():
        _recompute_cycle_summary_counts(cyc_data)

    cycle_order_desc = list(reversed(chronological_isos))
    active = cycles_out[latest_iso]
    generated_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        "app": "Plat Agenda Monitor",
        "version": 3,
        "generatedAt": generated_at,
        "autoSweepSchedule": AUTO_SWEEP_SCHEDULE,
        "scrapeFailed": bool(scraped_multi.get("scrapeFailed")),
        "error": str(scraped_multi.get("error") or ""),
        "cycleKey": active["cycleKey"],
        "meetingDate": active["meetingDate"],
        "pcDate": active["pcDate"],
        "fileName": active["fileName"],
        "sourceSpreadsheetUrl": active["sourceSpreadsheetUrl"],
        "agendaOutlineUrl": active["agendaOutlineUrl"],
        "noticesPageUrl": NOTICES_URL,
        "summary": active["summary"],
        "items": active["items"],
        "reviews": active["reviews"],
        "cycleOrder": cycle_order_desc,
        "cycles": cycles_out,
    }


# ---------------------------------------------------------------------------
# Parameterized SoQL & Multi-Agency Query Builders (Offline-Testable, Zero I/O)
# ---------------------------------------------------------------------------

SOCRATA_SALES_TAX_URL = "https://data.texas.gov/resource/jrea-zgmq.json"
SOCRATA_FRANCHISE_TAX_URL = "https://data.texas.gov/resource/9cir-efmm.json"
SOCRATA_TABC_PENDING_URL = "https://data.texas.gov/resource/mxm5-tdpj.json"
SOCRATA_TABC_ACTIVE_URL = "https://data.texas.gov/resource/7hf9-qc9f.json"
TDLR_TABS_SEARCH_API_URL = "https://www.tdlr.texas.gov/TABS/Search/SearchProjects"
TDLR_TABS_PORTAL_URL = "https://www.tdlr.texas.gov/tabs/search"
HCAD_ARCGIS_QUERY_URL = (
    "https://arcweb.hcad.org/server/rest/services/public/public_query/MapServer/0/query"
)
HOUSTON_311_QUERY_URL = (
    "https://mycity2.houstontx.gov/gisweb01/rest/services/311/"
    "HOUSTON311_RECENT_SR_SNOW/FeatureServer/0/query"
)
NOVUS_AGENDA_URL = "https://houston.novusagenda.com/agendapublic/"
COH_SOLD_PERMITS_PORTAL_URL = "https://cohtora.houstontx.gov/approot/soldpermits/online_permit.htm"


def escape_soql_literal(val: str) -> str:
    """Escape a string literal for Socrata SoQL single-quoted predicates.

    Why single-quote doubling: User-entered snowball terms routinely contain apostrophes
    (e.g. "O'Connor Properties LLC") and ampersands ("A & B Retail"). Unescaped single
    quotes trigger Socrata HTTP 400 malformed SoQL errors.
    """
    cleaned = re.sub(r"[\x00-\x1f\x7f]+", " ", str(val or "")).strip()
    cleaned = " ".join(cleaned.split())
    return cleaned.replace("'", "''")


def expand_brand_variants(terms: list[str] | tuple[str, ...]) -> list[str]:
    """Expand compound trade names into spaced and unspaced variants (e.g. 'Brake Time' / 'Braketime')."""
    out: list[str] = []
    seen: set[str] = set()

    def _add(candidate: str) -> None:
        c = " ".join(str(candidate or "").split()).strip()
        if len(c) < 3:
            return
        cu = c.upper()
        if cu not in seen:
            seen.add(cu)
            out.append(c)

    for raw in terms or ():
        base = " ".join(str(raw or "").split()).strip()
        if not base:
            continue
        _add(base)
        dehyphen = base.replace("-", " ")
        _add(dehyphen)
        if " " in dehyphen and len(dehyphen.split()) <= 3:
            collapsed = "".join(dehyphen.split())
            if len(collapsed) >= 5:
                _add(collapsed)
        elif re.match(r"^[A-Za-z]{6,}$", base):
            # Known convenience/fuel compound brand split for BrakeTime <-> Brake Time
            if base.upper() == "BRAKETIME":
                _add("Brake Time")

    return out


def _normalize_addr_tokens_for_match(raw_addr: str) -> list[str]:
    simp = simplify_search_address(raw_addr)
    if not simp:
        return []
    cleaned = re.sub(r"[^A-Z0-9\s/-]", " ", simp.upper())
    return [t for t in cleaned.split() if t]


def matches_street_token_boundary(candidate_addr: str, query_addr: str) -> bool:
    """Verify token-bounded street-number and street-name prefix matching.

    Why token boundaries: Substring matching on short inner-loop addresses like '500 Gray'
    false-matches '1500 Gray St' (house-number suffix collision) and '500 Graystone Dr'
    (street-root prefix collision). Every query token must match an exact contiguous token
    sequence in the candidate address.
    """
    q_tokens = _normalize_addr_tokens_for_match(query_addr)
    if len(q_tokens) < 2:
        return False
    raw_cand = re.sub(r"[^A-Z0-9\s/-]", " ", str(candidate_addr or "").upper())
    c_tokens = [t for t in raw_cand.split() if t]
    if len(c_tokens) < len(q_tokens):
        return False
    q_len = len(q_tokens)
    for idx_start in range(0, len(c_tokens) - q_len + 1):
        if c_tokens[idx_start : idx_start + q_len] == q_tokens:
            return True
    return False


def build_street_token_soql_clause(field_expr: str, raw_addr: str) -> str:
    """Build a token-bounded SoQL WHERE clause for a simplified street address."""
    q_tokens = _normalize_addr_tokens_for_match(raw_addr)
    if len(q_tokens) < 2:
        return ""
    phrase = escape_soql_literal(" ".join(q_tokens))
    col = f"upper({field_expr})"
    return (
        f"({col} = '{phrase}' "
        f"OR {col} LIKE '{phrase} %' "
        f"OR {col} LIKE '% {phrase}' "
        f"OR {col} LIKE '% {phrase} %')"
    )


def _build_substring_soql_clause(field_expr: str, raw_term: str, min_len: int = 3) -> str:
    cleaned = " ".join(str(raw_term or "").split()).strip()
    if len(cleaned) < min_len:
        return ""
    esc = escape_soql_literal(cleaned.upper())
    return f"upper({field_expr}) LIKE '%{esc}%'"


def build_tdlr_tabs_search_params(
    address: str = "",
    facility_name: str = "",
    owner_name: str = "",
    county_code: str = "2101",
) -> dict[str, str]:
    """Build x-www-form-urlencoded parameters for TDLR /TABS/Search/SearchProjects (2101 = Harris).

    Why omit empty keys + form-urlencoded: TDLR's DataTables endpoint uses lodash omitBy() on empty
    inputs and jQuery form-urlencoded POST binding. Sending JSON or empty keys leaves server-side
    filters unbound, causing unfiltered statewide table scans or timeouts.
    """
    simp_addr = simplify_search_address(address) if address else ""
    raw = {
        "FacilityName": str(facility_name or "").strip(),
        "LocationAddress": simp_addr,
        "LocationCounty": str(county_code or "2101").strip(),
        "OwnerName": str(owner_name or "").strip(),
        "DataVersionId": "900001",
        "start": "0",
        "length": "15",
    }
    return {k: v for k, v in raw.items() if v != ""}


def build_comptroller_sales_soql_where(
    addresses: list[str] | tuple[str, ...] = (),
    entities: list[str] | tuple[str, ...] = (),
    dbas: list[str] | tuple[str, ...] = (),
    county_code: str = "101",
) -> str:
    """Build SoQL WHERE for TX Comptroller Active Sales Tax Permits (jrea-zgmq, county 101 = Harris).

    Why DBAs are excluded when site addresses or legal entities exist: multi-store retail chains
    (such as 'Brake Time') have dozens of unrelated suburban outlets across Harris County that
    would saturate Socrata's unordered $limit=20 and crowd out actual on-site permits.
    """
    disjuncts: list[str] = []
    for addr in addresses or ():
        c = build_street_token_soql_clause("outlet_address", addr)
        if c:
            disjuncts.append(c)
    for ent in entities or ():
        c = _build_substring_soql_clause("taxpayer_name", ent, min_len=4)
        if c:
            disjuncts.append(c)
    if not disjuncts:
        for dba in expand_brand_variants(list(dbas or ())):
            c = _build_substring_soql_clause("outlet_name", dba, min_len=4)
            if c:
                disjuncts.append(c)
    if not disjuncts:
        return ""
    cc_esc = escape_soql_literal(str(county_code or "101").strip())
    dedup = list(dict.fromkeys(disjuncts))
    return f"outlet_county_code='{cc_esc}' AND ({' OR '.join(dedup)})"


def build_comptroller_franchise_soql_where(
    entities: list[str] | tuple[str, ...] = (),
    taxpayer_ids: list[str] | tuple[str, ...] = (),
    county_code: str = "",
) -> str:
    """Build SoQL WHERE for TX Comptroller Active Franchise Tax Holders (9cir-efmm)."""
    disjuncts: list[str] = []
    for ent in entities or ():
        cleaned = " ".join(str(ent or "").split()).strip()
        if len(cleaned) >= 4:
            esc = escape_soql_literal(cleaned.upper())
            disjuncts.append(f"upper(taxpayer_name) = '{esc}'")
            disjuncts.append(f"upper(taxpayer_name) LIKE '{esc}%'")
    for tid in taxpayer_ids or ():
        digits = re.sub(r"\D+", "", str(tid or ""))
        if len(digits) >= 11:
            disjuncts.append(f"taxpayer_number='{escape_soql_literal(digits)}'")
    if not disjuncts:
        return ""
    dedup = list(dict.fromkeys(disjuncts))
    where = f"({' OR '.join(dedup)})"
    if county_code:
        where = f"taxpayer_county_code='{escape_soql_literal(county_code)}' AND {where}"
    return where


def build_tabc_soql_where(
    addresses: list[str] | tuple[str, ...] = (),
    entities: list[str] | tuple[str, ...] = (),
    dbas: list[str] | tuple[str, ...] = (),
    county: str = "HARRIS",
    include_trade_name: bool = True,
) -> str:
    """Build Harris-County-scoped SoQL WHERE for TABC Pending (mxm5-tdpj) & Active (7hf9-qc9f).

    Note: mxm5-tdpj omits the trade_name column, so callers pass include_trade_name=False for
    pending applications to prevent Socrata HTTP 400 unknown-column failures.
    """
    disjuncts: list[str] = []
    for addr in addresses or ():
        c = build_street_token_soql_clause("address", addr)
        if c:
            disjuncts.append(c)
    for ent in entities or ():
        c = _build_substring_soql_clause("owner", ent, min_len=4)
        if c:
            disjuncts.append(c)
    if include_trade_name and not disjuncts:
        for dba in expand_brand_variants(list(dbas or ())):
            c = _build_substring_soql_clause("trade_name", dba, min_len=4)
            if c:
                disjuncts.append(c)
    if not disjuncts:
        return ""
    county_esc = escape_soql_literal((county or "HARRIS").upper())
    dedup = list(dict.fromkeys(disjuncts))
    return f"upper(county)='{county_esc}' AND ({' OR '.join(dedup)})"


def build_socrata_query_url(resource_url: str, where_clause: str, limit: int = 25) -> str:
    """Encode a SoQL $where + $limit URL safely using percent-encoding (preserving '&' inside literals)."""
    if not where_clause:
        return ""
    qs = urllib.parse.urlencode({"$where": where_clause, "$limit": str(int(limit))})
    return f"{resource_url}?{qs}"


def build_houston_311_where(
    addresses: list[str] | tuple[str, ...] = (),
    hcad_ids: list[str] | tuple[str, ...] = (),
) -> str:
    """Build an ArcGIS SQL WHERE clause for Houston 311 Code Enforcement / Dangerous Buildings."""
    clauses: list[str] = []
    for hid in hcad_ids or ():
        parsed = parse_hcad_account_list(hid)["hcadIds"]
        for p_id in parsed:
            clauses.append(f"TAXID = '{escape_soql_literal(p_id)}'")
    for addr in addresses or ():
        q_tokens = _normalize_addr_tokens_for_match(addr)
        if len(q_tokens) >= 2:
            phrase = escape_soql_literal(" ".join(q_tokens))
            clauses.append(f"UPPER(ADDRESS) LIKE '{phrase}%'")
    if not clauses:
        return ""
    code_filter = (
        "(UPPER(DEPARTMENT) LIKE '%NEIGHBORHOOD%' "
        "OR UPPER(CASE_TYPE) LIKE '%DANGEROUS%' "
        "OR UPPER(CASE_TYPE) LIKE '%BUILDING%' "
        "OR UPPER(CASE_TYPE) LIKE '%NUISANCE%' "
        "OR UPPER(TITLE) LIKE '%DANGEROUS%' "
        "OR UPPER(TITLE) LIKE '%STRUCTUR%')"
    )
    return f"{code_filter} AND ({' OR '.join(dict.fromkeys(clauses))})"


# ---------------------------------------------------------------------------
# Pure Offline-Testable 8-Agency Response Normalizers (UT-13)
# ---------------------------------------------------------------------------

def _make_auto_sweep_finding(
    check_id: str,
    agency_record_id: str,
    date_iso: str,
    summary_body: str,
    adverse: bool = False,
    links: list[dict[str, str]] | None = None,
) -> dict:
    clean_date = excel_serial_to_iso(date_iso) or dt.date.today().isoformat()
    clean_summary = " ".join(str(summary_body or "").split()).strip()
    if not clean_summary.startswith("[AUTO-SWEEP]"):
        clean_summary = f"[AUTO-SWEEP] {clean_summary}"
    out: dict = {
        "date": clean_date,
        "result": clean_summary,
        "source": "auto-sweep",
        "automated": True,
        "checkId": check_id,
        "agencyRecordId": str(agency_record_id or "").strip(),
        "adverse": bool(adverse),
    }
    if links:
        valid_links = [
            {"label": str(lk.get("label") or "Source").strip(), "url": str(lk["url"]).strip()}
            for lk in links
            if isinstance(lk, dict)
            and str(lk.get("url") or "").strip().lower().startswith(("http://", "https://"))
        ]
        if valid_links:
            out["links"] = valid_links
    return out


def _matches_any_property_context(
    candidate_addr: str,
    candidate_names: list[str],
    prop_addresses: list[str],
    prop_terms: list[str],
) -> bool:
    for pa in prop_addresses:
        if matches_street_token_boundary(candidate_addr, pa):
            return True
    cand_blob = " | ".join(str(x or "").upper() for x in candidate_names if x)
    if cand_blob:
        for term in expand_brand_variants(prop_terms):
            tu = term.upper().strip()
            if len(tu) >= 4 and tu in cand_blob:
                return True
    return False


def normalize_comptroller_records(
    sales_rows: list[dict] | None,
    franchise_rows: list[dict] | None,
    prop_addresses: list[str] | None = None,
    prop_entities: list[str] | None = None,
    prop_dbas: list[str] | None = None,
    sweep_date_iso: str = "",
) -> list[dict]:
    """Normalize Check #4 TX Comptroller Sales Tax (jrea-zgmq) & Franchise Tax (9cir-efmm) rows."""
    prop_addresses = list(prop_addresses or [])
    prop_entities_list = list(prop_entities or [])
    prop_dbas_list = list(prop_dbas or [])
    prop_terms = prop_entities_list + prop_dbas_list
    has_site_or_entity_scope = bool(prop_addresses or prop_entities_list)
    findings: list[dict] = []
    seen_ids: set[str] = set()

    for row in sales_rows or []:
        if not isinstance(row, dict):
            continue
        county_code = str(row.get("outlet_county_code") or "").strip()
        if county_code and county_code not in ("101", "HARRIS"):
            continue
        out_addr = str(row.get("outlet_address") or "").strip()
        tp_name = str(row.get("taxpayer_name") or "").strip()
        out_name = str(row.get("outlet_name") or "").strip()
        if has_site_or_entity_scope:
            if not _matches_any_property_context(out_addr, [tp_name], prop_addresses, prop_entities_list):
                continue
        elif prop_dbas_list and not _matches_any_property_context(
            out_addr, [tp_name, out_name], [], prop_dbas_list
        ):
            continue
        tp_num = str(row.get("taxpayer_number") or "").strip()
        out_num = str(row.get("outlet_number") or "1").strip()
        rec_id = f"comptroller-sales:{tp_num}:{out_num}" if tp_num else f"comptroller-sales:{tp_name}:{out_addr}"
        if rec_id in seen_ids:
            continue
        seen_ids.add(rec_id)
        raw_dt = str(row.get("outlet_permit_issue_date") or row.get("outlet_first_sales_date") or "")
        iso_dt = (
            f"{raw_dt[:4]}-{raw_dt[4:6]}-{raw_dt[6:8]}"
            if re.match(r"^\d{8}$", raw_dt)
            else (excel_serial_to_iso(raw_dt) or sweep_date_iso)
        )
        links = (
            [
                {
                    "label": f"Comptroller #{tp_num}",
                    "url": f"https://comptroller.texas.gov/taxes/franchise/account-status/search/{tp_num}",
                }
            ]
            if tp_num.isdigit()
            else []
        )
        summary = (
            f"TX Comptroller Active Sales Tax Permit #{tp_num or 'N/A'}: "
            f"{tp_name or 'Unknown Taxpayer'}"
            f"{f' (DBA {out_name})' if out_name and out_name.upper() != tp_name.upper() else ''} "
            f"at {out_addr or 'Houston'}."
        )
        findings.append(
            _make_auto_sweep_finding("chk-04", rec_id, iso_dt, summary, adverse=False, links=links)
        )

    for row in franchise_rows or []:
        if not isinstance(row, dict):
            continue
        tp_name = str(row.get("taxpayer_name") or "").strip()
        tp_num = str(row.get("taxpayer_number") or "").strip()
        if not tp_name and not tp_num:
            continue
        if prop_terms and not _matches_any_property_context("", [tp_name], [], prop_terms):
            continue
        rec_id = f"comptroller-franchise:{tp_num or tp_name}"
        if rec_id in seen_ids:
            continue
        seen_ids.add(rec_id)
        sos_num = str(row.get("secretary_of_state_sos_or_coa_file_number") or "").strip()
        rtb = str(row.get("right_to_transact_business_code") or "").strip()
        tp_addr = ", ".join(
            p
            for p in [
                str(row.get("taxpayer_address") or "").strip(),
                str(row.get("taxpayer_city") or "").strip(),
                str(row.get("taxpayer_state") or "").strip(),
            ]
            if p
        )
        raw_dt = str(row.get("sos_charter_date") or row.get("responsibility_beginning_date") or "")
        iso_dt = (
            f"{raw_dt[:4]}-{raw_dt[4:6]}-{raw_dt[6:8]}"
            if re.match(r"^\d{8}$", raw_dt)
            else (excel_serial_to_iso(raw_dt) or sweep_date_iso)
        )
        links = (
            [
                {
                    "label": f"Franchise #{tp_num}",
                    "url": f"https://comptroller.texas.gov/taxes/franchise/account-status/search/{tp_num}",
                }
            ]
            if tp_num.isdigit()
            else []
        )
        summary = (
            f"TX Comptroller Franchise Tax Account #{tp_num or 'N/A'}: {tp_name}"
            f"{f' (SOS #{sos_num})' if sos_num else ''}"
            f"{f', status {rtb}' if rtb else ''}"
            f"{f', mailing {tp_addr}' if tp_addr else ''}."
        )
        findings.append(
            _make_auto_sweep_finding("chk-04", rec_id, iso_dt, summary, adverse=False, links=links)
        )

    return findings


def normalize_hcad_arcgis_records(
    arcgis_features: list[dict] | None,
    sweep_date_iso: str = "",
) -> list[dict]:
    """Normalize Check #5 HCAD ArcGIS public_query/MapServer/0 features."""
    findings: list[dict] = []
    seen_ids: set[str] = set()
    for feat in arcgis_features or []:
        if not isinstance(feat, dict):
            continue
        attrs = feat.get("attributes") if isinstance(feat.get("attributes"), dict) else feat
        raw_hcad = str(attrs.get("HCAD_NUM") or attrs.get("hcad_num") or "").strip()
        parsed_ids = parse_hcad_account_list(raw_hcad)["hcadIds"]
        hcad = parsed_ids[0] if parsed_ids else raw_hcad
        if not hcad:
            continue
        owner = " ".join(str(attrs.get("owner") or attrs.get("CurrOwner") or "").split())
        state_cls = str(attrs.get("state_class") or "").strip()
        mkt_val = attrs.get("mkt_val")
        impr_val = attrs.get("impr_val")
        land_val = attrs.get("land_val")
        addr = " ".join(str(attrs.get("address") or attrs.get("LocAddr") or "").split())
        legal = " ".join(str(attrs.get("legal_lines") or attrs.get("subdivision") or "").split())
        rec_id = f"hcad:{hcad}:{owner}:{state_cls}:{impr_val}"
        if rec_id in seen_ids:
            continue
        seen_ids.add(rec_id)
        val_bits: list[str] = []
        if mkt_val is not None and str(mkt_val) != "":
            val_bits.append(f"Market ${mkt_val}")
        if impr_val is not None and str(impr_val) != "":
            val_bits.append(f"Impr ${impr_val}")
        if land_val is not None and str(land_val) != "":
            val_bits.append(f"Land ${land_val}")
        summary = (
            f"HCAD Account {hcad}{f' ({addr})' if addr else ''}: "
            f"Owner {owner or 'Unknown'}"
            f"{f', Class {state_cls}' if state_cls else ''}"
            f"{f' ({', '.join(val_bits)})' if val_bits else ''}"
            f"{f', Legal: {legal}' if legal else ''}."
        )
        links = [
            {
                "label": f"HCAD Map ({hcad})",
                "url": f"https://arcweb.hcad.org/parcel-viewer-v2.0/?hcad_num={hcad}",
            }
        ]
        findings.append(
            _make_auto_sweep_finding(
                "chk-05",
                rec_id,
                sweep_date_iso,
                summary,
                adverse=False,
                links=links,
            )
        )
    return findings


def normalize_tabc_records(
    pending_rows: list[dict] | None,
    active_rows: list[dict] | None,
    prop_addresses: list[str] | None = None,
    prop_entities: list[str] | None = None,
    prop_dbas: list[str] | None = None,
    sweep_date_iso: str = "",
) -> list[dict]:
    """Normalize Check #6 TABC Pending Applications (mxm5-tdpj) & Active Licenses (7hf9-qc9f)."""
    prop_addresses = list(prop_addresses or [])
    prop_entities_list = list(prop_entities or [])
    prop_dbas_list = list(prop_dbas or [])
    has_site_or_entity_scope = bool(prop_addresses or prop_entities_list)
    findings: list[dict] = []
    seen_ids: set[str] = set()

    for row in pending_rows or []:
        if not isinstance(row, dict):
            continue
        county = str(row.get("county") or "").strip().upper()
        if county and county != "HARRIS":
            continue
        addr = " ".join(str(row.get("address") or "").split())
        owner = " ".join(str(row.get("owner") or "").split())
        trade = " ".join(str(row.get("trade_name") or "").split())
        if has_site_or_entity_scope:
            if not _matches_any_property_context(addr, [owner], prop_addresses, prop_entities_list):
                continue
        elif prop_dbas_list and not _matches_any_property_context(addr, [owner, trade], [], prop_dbas_list):
            continue
        app_id = str(row.get("application_id") or row.get("primary_license_id") or "").strip()
        lic_type = str(row.get("license_type") or "").strip()
        status = str(row.get("applicationstatus") or "Pending").strip()
        rec_id = f"tabc-pending:{app_id or (owner + ':' + addr)}"
        if rec_id in seen_ids:
            continue
        seen_ids.add(rec_id)
        iso_dt = (
            excel_serial_to_iso(row.get("submission_date") or row.get("application_submission_date"))
            or sweep_date_iso
        )
        summary = (
            f"TABC Pending Application #{app_id or 'N/A'} ({lic_type or 'Permit'}, {status}): "
            f"{owner or 'Applicant'}{f' (DBA {trade})' if trade else ''} at {addr or 'Houston'}."
        )
        findings.append(
            _make_auto_sweep_finding(
                "chk-06",
                rec_id,
                iso_dt,
                summary,
                adverse=True,
                links=[{"label": "TABC Public Inquiry", "url": "https://apps.tabc.texas.gov/publicinquiry/"}],
            )
        )

    for row in active_rows or []:
        if not isinstance(row, dict):
            continue
        county = str(row.get("county") or "").strip().upper()
        if county and county != "HARRIS":
            continue
        addr = " ".join(str(row.get("address") or "").split())
        owner = " ".join(str(row.get("owner") or "").split())
        trade = " ".join(str(row.get("trade_name") or "").split())
        if has_site_or_entity_scope:
            if not _matches_any_property_context(addr, [owner], prop_addresses, prop_entities_list):
                continue
        elif prop_dbas_list and not _matches_any_property_context(addr, [owner, trade], [], prop_dbas_list):
            continue
        lic_id = str(row.get("license_id") or row.get("legacy_clp") or "").strip()
        lic_type = str(row.get("license_type") or "").strip()
        status = str(row.get("license_status") or row.get("primary_status") or "Active").strip()
        rec_id = f"tabc-active:{lic_id or (owner + ':' + addr)}"
        if rec_id in seen_ids:
            continue
        seen_ids.add(rec_id)
        raw_dt = str(row.get("original_issue_date") or row.get("current_issued_date") or "")
        iso_dt = (
            f"{raw_dt[:4]}-{raw_dt[4:6]}-{raw_dt[6:8]}"
            if re.match(r"^\d{8}$", raw_dt)
            else (excel_serial_to_iso(raw_dt) or sweep_date_iso)
        )
        summary = (
            f"TABC License #{lic_id or 'N/A'} ({lic_type or 'Permit'}, {status}): "
            f"{owner or 'Licensee'}{f' (DBA {trade})' if trade else ''} at {addr or 'Houston'}."
        )
        findings.append(
            _make_auto_sweep_finding(
                "chk-06",
                rec_id,
                iso_dt,
                summary,
                adverse=False,
                links=[{"label": "TABC Public Inquiry", "url": "https://apps.tabc.texas.gov/publicinquiry/"}],
            )
        )

    return findings


def normalize_plat_matches(
    plat_items: list[dict] | None,
    prop_hcad_ids: list[str] | None = None,
    prop_addresses: list[str] | None = None,
    prop_subdiv_keywords: list[str] | None = None,
    sweep_date_iso: str = "",
) -> list[dict]:
    """Normalize Check #9 COH Planning Commission Plat, Variance & Public Hearing matches."""
    target_hcads = set(
        hid for raw in (prop_hcad_ids or []) for hid in parse_hcad_account_list(raw)["hcadIds"]
    )
    prop_addresses = list(prop_addresses or [])
    subdiv_kw = [k.upper().strip() for k in (prop_subdiv_keywords or []) if len(str(k or "").strip()) >= 5]
    findings: list[dict] = []
    seen_ids: set[str] = set()

    for it in plat_items or []:
        if not isinstance(it, dict):
            continue
        item_hcads = set(parse_hcad_account_list(it.get("hcad", ""))["hcadIds"])
        atlas = it.get("atlas") if isinstance(it.get("atlas"), dict) else {}
        item_addrs = [
            it.get("noticeLocation", ""),
            atlas.get("primaryAddress", ""),
            *(atlas.get("addresses") or []),
        ]
        matched = False
        if target_hcads and (target_hcads & item_hcads):
            matched = True
        if not matched and prop_addresses:
            for cand_a in item_addrs:
                if any(matches_street_token_boundary(cand_a, pa) for pa in prop_addresses):
                    matched = True
                    break
        if not matched and subdiv_kw:
            name_blob = f"{it.get('name', '')} {it.get('prevSub', '')}".upper()
            if any(kw in name_blob for kw in subdiv_kw):
                matched = True
        if not matched:
            continue

        cycle_iso = str(it.get("pcDateIso") or sweep_date_iso).strip()
        app_no = str(it.get("app") or "").strip()
        rec_id = f"coh-plat:{cycle_iso}:{app_no or it.get('name', '')}"
        if rec_id in seen_ids:
            continue
        seen_ids.add(rec_id)

        hearing_iso = str(it.get("hearingDateIso") or "").strip()
        links: list[dict[str, str]] = []
        if it.get("pdf"):
            links.append({"label": "Plat PDF", "url": str(it["pdf"])})
        if it.get("noticePdf"):
            links.append({"label": "Public Hearing Notice PDF", "url": str(it["noticePdf"])})
        summary = (
            f"COH Planning Commission Agenda ({cycle_iso}) Item {app_no or 'N/A'}: "
            f"'{it.get('name', '')}' ({it.get('type', 'Plat')}, {it.get('section', '')})"
            f"{f', Public Hearing {hearing_iso}' if hearing_iso else ''}."
        )
        is_adverse = bool(
            it.get("noticePdf")
            or "variance" in str(it.get("special", "")).lower()
            or "dpv" in str(it.get("type", "")).lower()
        )
        findings.append(
            _make_auto_sweep_finding("chk-09", rec_id, cycle_iso, summary, adverse=is_adverse, links=links)
        )
    return findings


def normalize_tdlr_tabs_records(
    tabs_rows: list[dict] | None,
    prop_addresses: list[str] | None = None,
    prop_entities: list[str] | None = None,
    prop_dbas: list[str] | None = None,
    sweep_date_iso: str = "",
) -> list[dict]:
    """Normalize Check #10 TDLR Architectural Barriers (TABS) records (Socrata & live TDLR JSON).

    Strictly enforces Harris County membership (LocationCounty/County == 2101 or 'HARRIS') so
    statewide street-name collisions (such as Lufkin / Angelina County or Mesquite / 2057)
    are deterministically rejected.
    """
    prop_addresses = list(prop_addresses or [])
    prop_terms = list(prop_entities or []) + list(prop_dbas or [])
    findings: list[dict] = []
    seen_ids: set[str] = set()

    for row in tabs_rows or []:
        if not isinstance(row, dict):
            continue
        raw_county = str(
            row.get("facility_county")
            or row.get("location_county_code")
            or row.get("location_county")
            or row.get("county_name")
            or row.get("CountyName")
            or row.get("LocationCounty")
            or row.get("County")
            or ""
        ).strip().upper()
        if raw_county not in ("HARRIS", "HARRIS COUNTY", "2101", "101"):
            continue

        proj_no = str(
            row.get("project_number") or row.get("ProjectNumber") or row.get("tabs_number") or ""
        ).strip()
        fac_name = " ".join(
            str(
                row.get("facility_name")
                or row.get("FacilityName")
                or row.get("project_name")
                or row.get("ProjectName")
                or ""
            ).split()
        )
        loc_addr = " ".join(
            str(
                row.get("location_address")
                or row.get("LocationAddress")
                or row.get("facility_address")
                or row.get("Address")
                or ""
            ).split()
        )
        owner = " ".join(
            str(row.get("owner_name") or row.get("OwnerName") or row.get("tenant_name") or "").split()
        )
        design_firm = " ".join(
            str(row.get("design_firm_name") or row.get("DesignFirm") or row.get("design_firm") or "").split()
        )
        tabs_work_types = {
            "9001": "New Construction",
            "9002": "Renovation/Alteration",
            "9003": "Additions to Existing Building",
            "9004": "Historic Preservation",
            "9005": "Public Right of Way",
        }
        raw_work_type = str(
            row.get("work_type")
            or row.get("WorkType")
            or row.get("TypeOfWork")
            or row.get("scope_of_work")
            or ""
        ).strip()
        scope = " ".join(tabs_work_types.get(raw_work_type, raw_work_type).split())
        scope_detail = " ".join(str(row.get("ScopeOfWork") or "").split())
        if scope_detail and scope_detail.lower() not in scope.lower():
            scope = f"{scope} ({scope_detail})" if scope else scope_detail
        cost_raw = row.get("estimated_cost") if row.get("estimated_cost") is not None else row.get("EstimatedCost")

        if (prop_addresses or prop_terms) and not _matches_any_property_context(
            loc_addr, [fac_name, owner], prop_addresses, prop_terms
        ):
            continue

        rec_id = f"tdlr-tabs:{proj_no or (fac_name + ':' + loc_addr)}"
        if rec_id in seen_ids:
            continue
        seen_ids.add(rec_id)

        iso_dt = (
            excel_serial_to_iso(
                row.get("registration_date")
                or row.get("RegistrationDate")
                or row.get("ProjectCreatedOn")
                or row.get("created_date")
                or row.get("StartDate")
                or row.get("EstimatedStartDate")
            )
            or sweep_date_iso
        )
        cost_str = ""
        if cost_raw is not None and str(cost_raw).strip() != "":
            c_num = _parse_finite_float(cost_raw)
            cost_str = f"${int(round(c_num)):,}" if c_num is not None else str(cost_raw).strip()

        proj_url = (
            f"https://www.tdlr.texas.gov/TABS/Search/Project/{proj_no}"
            if re.match(r"^(?:TABS|EABPRJ)[A-Z0-9-]+$", proj_no, re.I)
            else TDLR_TABS_PORTAL_URL
        )
        summary = (
            f"TDLR TABS #{proj_no or 'N/A'}"
            f"{f' ({fac_name})' if fac_name else ''} at {loc_addr or 'Houston, Harris County'}"
            f"{f': {cost_str}' if cost_str else ''}"
            f"{f' {scope}' if scope else ''}"
            f"{f', Owner {owner}' if owner else ''}"
            f"{f', Design Firm {design_firm}' if design_firm else ''}."
        )
        findings.append(
            _make_auto_sweep_finding(
                "chk-10",
                rec_id,
                iso_dt,
                summary,
                adverse=True,
                links=[{"label": f"TDLR TABS ({proj_no or 'Search'})", "url": proj_url}],
            )
        )
    return findings


def normalize_houston_311_records(
    features: list[dict] | None,
    prop_hcad_ids: list[str] | None = None,
    prop_addresses: list[str] | None = None,
    sweep_date_iso: str = "",
) -> list[dict]:
    """Normalize Check #13 Houston 311 Code Enforcement & Dangerous Buildings cases."""
    target_hcads = set(
        hid for raw in (prop_hcad_ids or []) for hid in parse_hcad_account_list(raw)["hcadIds"]
    )
    prop_addresses = list(prop_addresses or [])
    findings: list[dict] = []
    seen_ids: set[str] = set()

    for feat in features or []:
        if not isinstance(feat, dict):
            continue
        attrs = feat.get("attributes") if isinstance(feat.get("attributes"), dict) else feat
        case_no = str(attrs.get("CASE_NUMBER") or attrs.get("case_number") or "").strip()
        case_type = " ".join(str(attrs.get("CASE_TYPE") or attrs.get("TITLE") or "").split())
        status = " ".join(str(attrs.get("STATUS") or "").split())
        addr = " ".join(
            str(attrs.get("ADDRESS") or attrs.get("INCIDENT_ADDRESS") or "").split()
        )
        tax_ids = set(parse_hcad_account_list(str(attrs.get("TAXID") or ""))["hcadIds"])

        if (target_hcads or prop_addresses) and not (
            (target_hcads and (target_hcads & tax_ids))
            or any(matches_street_token_boundary(addr, pa) for pa in prop_addresses)
        ):
            continue

        rec_id = f"coh-311:{case_no or (case_type + ':' + addr)}"
        if rec_id in seen_ids:
            continue
        seen_ids.add(rec_id)

        created_raw = attrs.get("CREATED_ON") or attrs.get("CREATED_DATE")
        if isinstance(created_raw, (int, float)) and created_raw > 1_000_000_000_000:
            iso_dt = dt.datetime.fromtimestamp(created_raw / 1000.0, tz=dt.timezone.utc).date().isoformat()
        else:
            iso_dt = excel_serial_to_iso(created_raw) or sweep_date_iso

        is_adverse = bool(re.search(r"dangerous|demolition|structural|open\s+structure|hazard", case_type, re.I))
        summary = (
            f"Houston 311 Case #{case_no or 'N/A'}: {case_type or 'Code Complaint'} "
            f"({status or 'Open'}) at {addr or 'Houston'}."
        )
        findings.append(
            _make_auto_sweep_finding(
                "chk-13",
                rec_id,
                iso_dt,
                summary,
                adverse=is_adverse,
                links=[
                    {
                        "label": "311 Service Request Viewer",
                        "url": "https://experience.arcgis.com/experience/cf006ed734814cee9f5b9bcf7abc839f",
                    }
                ],
            )
        )
    return findings


def normalize_coh_sold_permits(
    permit_rows: list[dict] | None,
    prop_addresses: list[str] | None = None,
    sweep_date_iso: str = "",
) -> dict[str, list]:
    """Normalize Check #14 COH Sold Permits and harvest 8-digit ILMS project numbers for Check #8."""
    prop_addresses = list(prop_addresses or [])
    findings: list[dict] = []
    harvested_projects: list[str] = []
    seen_ids: set[str] = set()
    seen_proj: set[str] = set()

    for row in permit_rows or []:
        if not isinstance(row, dict):
            continue
        addr = " ".join(
            str(row.get("job_address") or row.get("address") or row.get("situs_address") or "").split()
        )
        if prop_addresses and not any(matches_street_token_boundary(addr, pa) for pa in prop_addresses):
            continue

        proj_raw = str(
            row.get("project_no") or row.get("project_number") or row.get("permit_number") or ""
        ).strip()
        m_proj = re.search(r"\b(\d{8})\b", proj_raw)
        proj_8 = m_proj.group(1) if m_proj else ""
        if proj_8 and proj_8 not in seen_proj:
            seen_proj.add(proj_8)
            harvested_projects.append(proj_8)

        use_desc = " ".join(
            str(row.get("use_desc") or row.get("permit_type_label") or row.get("permit_desc") or "Permit").split()
        )
        buyer = " ".join(str(row.get("buyer") or row.get("contractor") or "").split())
        occupant = " ".join(str(row.get("owner_occupant") or "").split())
        iso_dt = excel_serial_to_iso(row.get("date") or row.get("issue_date")) or sweep_date_iso

        rec_id = f"coh-permit:{proj_8 or proj_raw or (use_desc + ':' + iso_dt)}"
        if rec_id in seen_ids:
            continue
        seen_ids.add(rec_id)

        is_demo = bool(
            row.get("is_structural_demo")
            or row.get("is_sewer_disconnect")
            or re.search(r"\bdemo|sewer\s*disc|wreck|raz", use_desc, re.I)
        )
        summary = (
            f"COH Sold Permit #{proj_8 or proj_raw or 'N/A'}: {use_desc}"
            f"{f' ({occupant})' if occupant else ''}"
            f"{f' issued to {buyer}' if buyer else ''}"
            f" at {addr or 'Houston'}."
        )
        findings.append(
            _make_auto_sweep_finding(
                "chk-14",
                rec_id,
                iso_dt,
                summary,
                adverse=is_demo,
                links=[{"label": "Houston Sold Permits", "url": COH_SOLD_PERMITS_PORTAL_URL}],
            )
        )

    return {"findings": findings, "harvestedProjectNumbers": harvested_projects}


def normalize_dshs_asbestos_records(
    dshs_rows: list[dict] | None,
    prop_addresses: list[str] | None = None,
    prop_entities: list[str] | None = None,
    sweep_date_iso: str = "",
) -> list[dict]:
    """Normalize Check #15 TX DSHS Asbestos & Demolition Notification records."""
    prop_addresses = list(prop_addresses or [])
    prop_entities = list(prop_entities or [])
    findings: list[dict] = []
    seen_ids: set[str] = set()

    for row in dshs_rows or []:
        if not isinstance(row, dict):
            continue
        addr = " ".join(str(row.get("norm_address") or row.get("raw_address") or row.get("address") or "").split())
        owner = " ".join(str(row.get("facility_owner") or row.get("owner") or "").split())
        fac = " ".join(str(row.get("facility_name") or "").split())
        if (prop_addresses or prop_entities) and not _matches_any_property_context(
            addr, [owner, fac], prop_addresses, prop_entities
        ):
            continue

        notif_no = str(row.get("notification_number") or row.get("notif_no") or "").strip()
        work_type = " ".join(str(row.get("work_type") or "Notification").split())
        start_dt = excel_serial_to_iso(row.get("start_date")) or str(row.get("start_date") or "").strip()
        end_dt = excel_serial_to_iso(row.get("end_date")) or str(row.get("end_date") or "").strip()
        iso_dt = excel_serial_to_iso(row.get("filing_date")) or start_dt or sweep_date_iso
        contractor = " ".join(str(row.get("abatement_contractor") or "").split())

        rec_id = f"dshs-asbestos:{notif_no or (addr + ':' + iso_dt)}"
        if rec_id in seen_ids:
            continue
        seen_ids.add(rec_id)

        links: list[dict[str, str]] = []
        for url_key, lbl in (("print_action_url", f"DSHS #{notif_no}"), ("pdf_url", "DSHS PDF")):
            u = str(row.get(url_key) or "").strip()
            if u.lower().startswith(("http://", "https://")):
                links.append({"label": lbl, "url": u})
        if not links:
            links.append(
                {"label": "DSHS Notifications", "url": "https://vo.ras.dshs.state.tx.us/datamart/mainMenu.do"}
            )

        window = f" scheduled {start_dt} to {end_dt}" if (start_dt and end_dt) else (f" starting {start_dt}" if start_dt else "")
        summary = (
            f"TX DSHS {work_type} Notification #{notif_no or 'N/A'} for {addr or 'Houston'}"
            f"{window}"
            f"{f' by {contractor}' if contractor else ''}"
            f"{f' (Owner {owner})' if owner else ''}."
        )
        findings.append(
            _make_auto_sweep_finding("chk-15", rec_id, iso_dt, summary, adverse=True, links=links)
        )
    return findings


# ---------------------------------------------------------------------------
# Thin Live Agency Transport Wrappers & Watchlist Dossier Builder
# ---------------------------------------------------------------------------

AUTOMATED_SWEEP_AGENCIES: tuple[tuple[str, str], ...] = (
    ("chk-04", "TX Comptroller (Sales & Franchise Tax)"),
    ("chk-05", "HCAD Ownership & Classification"),
    ("chk-06", "TABC Liquor Licensing"),
    ("chk-09", "COH Planning Commission Plats & Variances"),
    ("chk-10", "TDLR Architectural Barriers (TABS)"),
    ("chk-13", "Houston 311 & Dangerous Buildings"),
    ("chk-14", "Houston Sold Permits"),
    ("chk-15", "TX DSHS Asbestos & Demolition"),
)
AUTOMATED_SWEEP_AGENCY_LABELS: dict[str, str] = dict(AUTOMATED_SWEEP_AGENCIES)


def _fetch_json_with_status(
    url: str,
    timeout: int = 12,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[object, str, str]:
    """Fetch and parse JSON via fetch_bytes while distinguishing skipped vs ok vs failed.

    Built directly atop fetch_bytes so offline unit tests that monkeypatch fetch_bytes retain
    full deterministic control over transport timeouts, malformed JSON, and HTTP-200 error bodies.
    """
    if not url:
        return None, "skipped", ""
    raw = fetch_bytes(url, timeout=timeout, data=data, headers=headers)
    if not raw:
        return None, "failed", "HTTP request failed or timed out"
    try:
        return json.loads(raw.decode("utf-8", errors="ignore")), "ok", ""
    except Exception as exc:
        print(f"[warn] JSON decode failed for {url}: {exc}")
        return None, "failed", f"Invalid JSON response: {exc}"


def _fetch_json_safe(
    url: str,
    timeout: int = 12,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> object:
    payload, _, _ = _fetch_json_with_status(url, timeout=timeout, data=data, headers=headers)
    return payload


def _validate_socrata_rows(
    payload: object,
    status: str,
    err: str,
    label: str,
) -> tuple[list[dict], str, str]:
    if status != "ok":
        return [], status, (f"{label}: {err}" if err else "")
    if isinstance(payload, list):
        return payload, "ok", ""
    detail = ""
    if isinstance(payload, dict):
        detail = str(payload.get("message") or payload.get("error") or "").strip()
    return [], "failed", f"{label}: {detail or 'Expected Socrata JSON array'}"


def _validate_arcgis_features(
    payload: object,
    status: str,
    err: str,
    label: str,
) -> tuple[list[dict], str, str]:
    if status != "ok":
        return [], status, (f"{label}: {err}" if err else "")
    if isinstance(payload, dict) and "error" not in payload and isinstance(payload.get("features"), list):
        return payload["features"], "ok", ""
    detail = "Expected ArcGIS feature set without error key"
    if isinstance(payload, dict) and "error" in payload:
        err_obj = payload.get("error")
        if isinstance(err_obj, dict):
            code = err_obj.get("code")
            msg = str(err_obj.get("message") or "ArcGIS service error").strip()
            detail = f"ArcGIS error {code}: {msg}" if code is not None else msg
        else:
            detail = str(err_obj or detail).strip()
    return [], "failed", f"{label}: {detail}"


def _validate_tdlr_tabs_rows(
    payload: object,
    status: str,
    err: str,
    addr: str,
) -> tuple[list[dict], str, str]:
    if status != "ok":
        return [], status, (f"TDLR TABS ({addr}): {err}" if err else "")
    if isinstance(payload, dict) and "error" not in payload and isinstance(payload.get("data"), list):
        return payload["data"], "ok", ""
    detail = ""
    if isinstance(payload, dict) and payload.get("error"):
        detail = str(payload.get("error")).strip()
    return [], "failed", f"TDLR TABS ({addr}): {detail or 'Expected DataTables object with data list'}"


def _combine_subquery_health(results: list[tuple[str, str]], label: str) -> dict:
    errors = [err for st, err in results if st == "failed" and err]
    if any(st == "failed" for st, _ in results):
        return {
            "status": "failed",
            "ok": False,
            "label": label,
            "error": "; ".join(dict.fromkeys(errors)) or f"{label} query failed",
        }
    if any(st == "ok" for st, _ in results):
        return {"status": "ok", "ok": True, "label": label, "error": ""}
    return {"status": "skipped", "ok": True, "label": label, "error": ""}


def aggregate_watchlist_sweep_health(
    per_property_healths: list[dict],
    sweep_timestamp_iso: str,
    run_live_sweeps: bool = True,
) -> dict:
    """Aggregate per-property 8-agency health via worst-case logical AND across all active properties.

    Why worst-case rollup: reporting '8/8 agencies OK' when even a single watched property suffered
    an HTTP-200 ArcGIS fault, Socrata query rejection, or TDLR timeout gives staff false confidence
    that zero regulatory filings occurred overnight.
    """
    agencies: dict[str, dict] = {}
    for chk_id, label in AUTOMATED_SWEEP_AGENCIES:
        entries = [
            h[chk_id]
            for h in per_property_healths
            if isinstance(h, dict) and isinstance(h.get(chk_id), dict)
        ]
        failed_entries = [
            e for e in entries if e.get("status") == "failed" or e.get("ok") is False
        ]
        if failed_entries:
            uniq_errs: list[str] = []
            for fe in failed_entries:
                em = str(fe.get("error") or "").strip()
                if em and em not in uniq_errs:
                    uniq_errs.append(em)
            agencies[chk_id] = {
                "status": "failed",
                "ok": False,
                "label": label,
                "failedPropertyCount": len(failed_entries),
                "error": "; ".join(uniq_errs[:3]) or f"{label} failed",
            }
        elif any(e.get("status") == "ok" for e in entries):
            agencies[chk_id] = {
                "status": "ok",
                "ok": True,
                "label": label,
                "failedPropertyCount": 0,
                "error": "",
            }
        else:
            agencies[chk_id] = {
                "status": "skipped",
                "ok": True,
                "label": label,
                "failedPropertyCount": 0,
                "error": "",
            }

    failed_ids = sorted(cid for cid, meta in agencies.items() if not meta["ok"])
    skipped_ids = sorted(cid for cid, meta in agencies.items() if meta["status"] == "skipped")
    total = len(AUTOMATED_SWEEP_AGENCIES)
    return {
        "timestamp": sweep_timestamp_iso,
        "sweepsExecuted": bool(run_live_sweeps and per_property_healths),
        "propertiesSwept": len(per_property_healths),
        "totalAgencies": total,
        "succeededAgencies": total - len(failed_ids),
        "failedAgencies": failed_ids,
        "skippedAgencies": skipped_ids,
        "allOk": len(failed_ids) == 0,
        "agencies": agencies,
    }


def _extract_property_sweep_context(prop: dict) -> dict[str, list[str]]:
    raw_addrs = [prop.get("address", "")]
    raw_addrs.extend(prop.get("secondaryAddresses") or [])
    st = prop.get("searchTerms") if isinstance(prop.get("searchTerms"), dict) else {}
    raw_addrs.extend(st.get("altAddresses") or [])
    addresses: list[str] = []
    for a in raw_addrs:
        simp = simplify_search_address(a)
        if simp and simp.upper() not in {x.upper() for x in addresses}:
            addresses.append(simp)

    entities: list[str] = []
    for e in list(st.get("entities") or []):
        ec = " ".join(str(e or "").split()).strip()
        if len(ec) >= 4 and ec.upper() not in {x.upper() for x in entities}:
            entities.append(ec)
    for blob_field in ("ownerOfRecord", "buyerRelatedEntities"):
        blob = str(prop.get(blob_field) or "")
        for part in re.split(r"[/;,\n]+", blob):
            cleaned = re.sub(r"\([^)]*\)", " ", part)
            cleaned = " ".join(cleaned.split()).strip()
            if (
                len(cleaned) >= 4
                and re.search(r"\b(?:LLC|INC|CORP|LP|LTD|TRUST|PROPERTIES|REALTY|HOLDINGS|OPERATIONS|COMPANY)\b", cleaned, re.I)
                and cleaned.upper() not in {x.upper() for x in entities}
            ):
                entities.append(cleaned)

    dbas: list[str] = []
    for d in list(st.get("dbas") or []):
        dc = " ".join(str(d or "").split()).strip()
        if len(dc) >= 3 and dc.upper() not in {x.upper() for x in dbas}:
            dbas.append(dc)

    subdiv_kw: list[str] = [
        str(k).strip() for k in (st.get("legalKeywords") or []) if len(str(k or "").strip()) >= 5
    ]
    hcad_ids = parse_hcad_account_list(
        f"{prop.get('hcadAccount', '')} {prop.get('hcad', '')}"
    )["hcadIds"]
    return {
        "addresses": addresses[:6],
        "entities": entities[:8],
        "dbas": dbas[:6],
        "subdivKeywords": subdiv_kw[:6],
        "hcadIds": hcad_ids[:6],
    }


def sweep_watched_property_agencies(
    prop: dict,
    plat_data: dict,
    idx: dict,
    sweep_date_iso: str,
) -> dict:
    """Execute 8-agency public open-data sweeps for a single watched property with error isolation."""
    ctx = _extract_property_sweep_context(prop)
    addrs = ctx["addresses"]
    entities = ctx["entities"]
    dbas = ctx["dbas"]
    hcad_ids = ctx["hcadIds"]

    findings_by_check: dict[str, list[dict]] = {chk_id: [] for chk_id, _ in AUTOMATED_SWEEP_AGENCIES}
    agency_health: dict[str, dict] = {}
    harvested_projects: list[str] = []

    # Check #4: TX Comptroller Sales Tax (jrea-zgmq) & Franchise Tax (9cir-efmm)
    sales_where = build_comptroller_sales_soql_where(addrs, entities, dbas)
    sales_url = build_socrata_query_url(SOCRATA_SALES_TAX_URL, sales_where, limit=20)
    sales_raw, sales_st, sales_err = _fetch_json_with_status(sales_url)
    sales_rows, sales_st, sales_err = _validate_socrata_rows(
        sales_raw, sales_st, sales_err, "TX Comptroller Sales Tax"
    )

    fran_where = build_comptroller_franchise_soql_where(entities)
    fran_url = build_socrata_query_url(SOCRATA_FRANCHISE_TAX_URL, fran_where, limit=15)
    fran_raw, fran_st, fran_err = _fetch_json_with_status(fran_url)
    fran_rows, fran_st, fran_err = _validate_socrata_rows(
        fran_raw, fran_st, fran_err, "TX Comptroller Franchise Tax"
    )

    findings_by_check["chk-04"] = normalize_comptroller_records(
        sales_rows,
        fran_rows,
        addrs,
        entities,
        dbas,
        sweep_date_iso,
    )
    agency_health["chk-04"] = _combine_subquery_health(
        [(sales_st, sales_err), (fran_st, fran_err)],
        AUTOMATED_SWEEP_AGENCY_LABELS["chk-04"],
    )

    # Check #5: HCAD ArcGIS REST MapServer/0/query
    if hcad_ids:
        quoted = ",".join(f"'{escape_soql_literal(h)}'" for h in hcad_ids)
        hcad_qs = urllib.parse.urlencode(
            {
                "where": f"HCAD_NUM IN ({quoted})",
                "outFields": "HCAD_NUM,owner,subdivision,address,state_class,appr_val,mkt_val,impr_val,land_val,legal_lines",
                "returnGeometry": "false",
                "f": "json",
            }
        )
        hcad_raw, hcad_st, hcad_err = _fetch_json_with_status(f"{HCAD_ARCGIS_QUERY_URL}?{hcad_qs}")
        hcad_feats, hcad_st, hcad_err = _validate_arcgis_features(
            hcad_raw, hcad_st, hcad_err, "HCAD ArcGIS"
        )
        findings_by_check["chk-05"] = normalize_hcad_arcgis_records(hcad_feats, sweep_date_iso)
        agency_health["chk-05"] = _combine_subquery_health(
            [(hcad_st, hcad_err)], AUTOMATED_SWEEP_AGENCY_LABELS["chk-05"]
        )
    else:
        agency_health["chk-05"] = _combine_subquery_health(
            [("skipped", "")], AUTOMATED_SWEEP_AGENCY_LABELS["chk-05"]
        )

    # Check #6: TABC Pending (mxm5-tdpj, no trade_name column) & Active (7hf9-qc9f)
    tabc_pend_w = build_tabc_soql_where(addrs, entities, dbas, include_trade_name=False)
    tabc_act_w = build_tabc_soql_where(addrs, entities, dbas, include_trade_name=True)
    tp_raw, tp_st, tp_err = _fetch_json_with_status(
        build_socrata_query_url(SOCRATA_TABC_PENDING_URL, tabc_pend_w, limit=15)
    )
    tp_rows, tp_st, tp_err = _validate_socrata_rows(tp_raw, tp_st, tp_err, "TABC Pending")
    ta_raw, ta_st, ta_err = _fetch_json_with_status(
        build_socrata_query_url(SOCRATA_TABC_ACTIVE_URL, tabc_act_w, limit=15)
    )
    ta_rows, ta_st, ta_err = _validate_socrata_rows(ta_raw, ta_st, ta_err, "TABC Active")
    findings_by_check["chk-06"] = normalize_tabc_records(
        tp_rows,
        ta_rows,
        addrs,
        entities,
        dbas,
        sweep_date_iso,
    )
    agency_health["chk-06"] = _combine_subquery_health(
        [(tp_st, tp_err), (ta_st, ta_err)],
        AUTOMATED_SWEEP_AGENCY_LABELS["chk-06"],
    )

    # Check #9: Multi-cycle COH Planning Commission Plats, Section K Variances & Notices
    all_plat_items: list[dict] = []
    chk09_st = "ok"
    chk09_err = ""
    if not isinstance(plat_data, dict):
        chk09_st = "failed"
        chk09_err = "COH Plat Agenda dataset missing or invalid"
    elif plat_data.get("scrapeFailed") is True or plat_data.get("error"):
        chk09_st = "failed"
        chk09_err = str(plat_data.get("error") or "COH Plat Agenda upstream scrape failed")
    elif "cycles" in plat_data:
        if not isinstance(plat_data.get("cycles"), dict):
            chk09_st = "failed"
            chk09_err = "COH Plat Agenda cycles index malformed"
        else:
            for cyc in plat_data["cycles"].values():
                if isinstance(cyc, dict) and isinstance(cyc.get("items"), list):
                    all_plat_items.extend(cyc["items"])
                else:
                    chk09_st = "failed"
                    chk09_err = "COH Plat Agenda cycle entry malformed"
    elif "items" in plat_data:
        if isinstance(plat_data.get("items"), list):
            all_plat_items.extend(plat_data["items"])
        else:
            chk09_st = "failed"
            chk09_err = "COH Plat Agenda items list malformed"
    else:
        chk09_st = "failed"
        chk09_err = "COH Plat Agenda dataset contains neither cycles nor items"

    if chk09_st == "ok" and not (hcad_ids or addrs or ctx["subdivKeywords"]):
        chk09_st = "skipped"
    findings_by_check["chk-09"] = normalize_plat_matches(
        all_plat_items,
        hcad_ids,
        addrs,
        ctx["subdivKeywords"],
        sweep_date_iso,
    )
    agency_health["chk-09"] = _combine_subquery_health(
        [(chk09_st, chk09_err)], AUTOMATED_SWEEP_AGENCY_LABELS["chk-09"]
    )

    # Check #10: TDLR Architectural Barriers (TABS) scoped strictly to Harris County (2101)
    tabs_collected: list[dict] = []
    seen_tabs_proj: set[str] = set()
    tabs_statuses: list[tuple[str, str]] = []
    for addr in addrs[:6]:
        params = build_tdlr_tabs_search_params(address=addr, county_code="2101")
        body = urllib.parse.urlencode(params).encode("utf-8")
        t_raw, t_st, t_err = _fetch_json_with_status(
            TDLR_TABS_SEARCH_API_URL,
            timeout=12,
            data=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        raw_rows, t_st, t_err = _validate_tdlr_tabs_rows(t_raw, t_st, t_err, addr)
        tabs_statuses.append((t_st, t_err))
        for raw_row in raw_rows:
            if not isinstance(raw_row, dict):
                continue
            row_copy = dict(raw_row)
            if not row_copy.get("LocationAddress") and not row_copy.get("location_address"):
                row_copy["LocationAddress"] = addr
            pno = str(row_copy.get("ProjectNumber") or row_copy.get("project_number") or "").strip()
            rc = str(row_copy.get("County") or row_copy.get("LocationCounty") or "").strip()
            if (
                pno
                and pno not in seen_tabs_proj
                and len(seen_tabs_proj) < 3
                and rc in ("2101", "101", "HARRIS", "Harris")
                and re.match(r"^(?:TABS|EABPRJ)[A-Z0-9-]+$", pno, re.I)
            ):
                seen_tabs_proj.add(pno)
                detail_raw = fetch_bytes(
                    f"https://www.tdlr.texas.gov/TABS/Search/Project/{pno}",
                    timeout=10,
                )
                if detail_raw:
                    detail_txt = " ".join(
                        re.sub(r"<[^>]+>", " ", detail_raw.decode("utf-8", errors="ignore")).split()
                    )
                    for key_out, pat in (
                        ("LocationAddress", r"Location Address:\s*(.+?)\s+Location County:"),
                        ("ScopeOfWork", r"Scope of Work:\s*(.+?)\s+(?:Square Footage:|Current Status:)"),
                        ("OwnerName", r"Owner Name:\s*(.+?)\s+Owner Address:"),
                        ("DesignFirm", r"Design Firm Name:\s*(.+?)\s+Design Firm Address:"),
                    ):
                        m_det = re.search(pat, detail_txt)
                        if m_det and m_det.group(1).strip():
                            row_copy[key_out] = m_det.group(1).strip()
            tabs_collected.append(row_copy)
    findings_by_check["chk-10"] = normalize_tdlr_tabs_records(
        tabs_collected,
        addrs,
        entities,
        dbas,
        sweep_date_iso,
    )
    agency_health["chk-10"] = _combine_subquery_health(
        tabs_statuses or [("skipped", "")],
        AUTOMATED_SWEEP_AGENCY_LABELS["chk-10"],
    )

    # Check #13: Houston 311 Code Enforcement & Dangerous Buildings
    w_311 = build_houston_311_where(addrs, hcad_ids)
    if w_311:
        qs_311 = urllib.parse.urlencode(
            {
                "where": w_311,
                "outFields": "CASE_NUMBER,TITLE,CASE_TYPE,DEPARTMENT,STATUS,CREATED_ON,ADDRESS,TAXID",
                "returnGeometry": "false",
                "resultRecordCount": "15",
                "f": "json",
            }
        )
        r311_raw, r311_st, r311_err = _fetch_json_with_status(f"{HOUSTON_311_QUERY_URL}?{qs_311}")
        feats_311, r311_st, r311_err = _validate_arcgis_features(
            r311_raw, r311_st, r311_err, "Houston 311 ArcGIS"
        )
        findings_by_check["chk-13"] = normalize_houston_311_records(
            feats_311,
            hcad_ids,
            addrs,
            sweep_date_iso,
        )
        agency_health["chk-13"] = _combine_subquery_health(
            [(r311_st, r311_err)], AUTOMATED_SWEEP_AGENCY_LABELS["chk-13"]
        )
    else:
        agency_health["chk-13"] = _combine_subquery_health(
            [("skipped", "")], AUTOMATED_SWEEP_AGENCY_LABELS["chk-13"]
        )

    # Check #14 & #15: Cross-check fresh active_permit_alerts.json & permit shards harvested by fetch_live_permits.py
    permit_candidates: list[dict] = []
    dshs_candidates: list[dict] = []
    chk14_statuses: list[tuple[str, str]] = []
    chk15_statuses: list[tuple[str, str]] = []
    alerts_path = DATA_DIR / "active_permit_alerts.json"
    if not alerts_path.exists():
        chk14_statuses.append(("failed", f"Missing {alerts_path.name} index"))
        chk15_statuses.append(("failed", f"Missing {alerts_path.name} index"))
    else:
        try:
            alerts_doc = json.loads(alerts_path.read_text(encoding="utf-8"))
            if not isinstance(alerts_doc, dict) or not isinstance(alerts_doc.get("alerts"), list):
                raise ValueError("alerts index missing alerts array")
            if alerts_doc.get("degraded") is True or alerts_doc.get("coh_fetch_failed") is True:
                chk14_statuses.append(
                    (
                        "failed",
                        str(alerts_doc.get("coh_fetch_error") or "COH permit alerts index flagged degraded"),
                    )
                )
            else:
                chk14_statuses.append(("ok" if (hcad_ids or addrs) else "skipped", ""))
            if alerts_doc.get("degraded") is True or alerts_doc.get("dshs_fetch_failed") is True:
                chk15_statuses.append(
                    (
                        "failed",
                        str(alerts_doc.get("dshs_fetch_error") or "DSHS asbestos index flagged degraded"),
                    )
                )
            else:
                chk15_statuses.append(("ok" if (hcad_ids or addrs or entities) else "skipped", ""))

            for al in alerts_doc.get("alerts", []):
                if not isinstance(al, dict):
                    continue
                al_hcads = set(
                    parse_hcad_account_list(
                        " ".join([str(al.get("hcad_num") or "")] + [str(x) for x in (al.get("related_hcads") or [])])
                    )["hcadIds"]
                )
                al_addrs = [al.get("address", "")] + list(al.get("secondary_addresses") or [])
                if (set(hcad_ids) & al_hcads) or any(
                    matches_street_token_boundary(aa, pa) for aa in al_addrs for pa in addrs
                ):
                    permit_candidates.extend(al.get("coh_permits") or [])
                    dshs_candidates.extend(al.get("dshs_notifications") or [])
        except Exception as exc:
            print(f"[warn] Could not inspect active_permit_alerts.json during sweep: {exc}")
            chk14_statuses.append(("failed", f"Corrupt active_permit_alerts.json: {exc}"))
            chk15_statuses.append(("failed", f"Corrupt active_permit_alerts.json: {exc}"))

    for hid in hcad_ids:
        shard_file = DATA_DIR / "permits" / f"permits_{hid[:3]}.json"
        if shard_file.exists():
            try:
                shard_doc = json.loads(shard_file.read_text(encoding="utf-8"))
                if not isinstance(shard_doc, dict):
                    raise ValueError("Shard payload is not a JSON object")
                rec = shard_doc.get(hid)
                if isinstance(rec, dict):
                    for p_item in rec.get("permits") or rec.get("coh_permits") or []:
                        if isinstance(p_item, dict):
                            p_copy = dict(p_item)
                            if not p_copy.get("job_address") and addrs:
                                p_copy["job_address"] = addrs[0]
                            permit_candidates.append(p_copy)
            except Exception as exc:
                chk14_statuses.append(("failed", f"Corrupt permit shard {shard_file.name}: {exc}"))

    norm_permits = normalize_coh_sold_permits(permit_candidates, addrs, sweep_date_iso)
    findings_by_check["chk-14"] = norm_permits["findings"]
    harvested_projects.extend(norm_permits["harvestedProjectNumbers"])
    agency_health["chk-14"] = _combine_subquery_health(
        chk14_statuses, AUTOMATED_SWEEP_AGENCY_LABELS["chk-14"]
    )

    findings_by_check["chk-15"] = normalize_dshs_asbestos_records(
        dshs_candidates,
        addrs,
        entities,
        sweep_date_iso,
    )
    agency_health["chk-15"] = _combine_subquery_health(
        chk15_statuses, AUTOMATED_SWEEP_AGENCY_LABELS["chk-15"]
    )

    return {
        "findingsByCheck": findings_by_check,
        "harvestedProjectNumbers": harvested_projects,
        "agencyHealth": agency_health,
    }


def _find_checklist_row_by_id(checklist: list[dict], check_id: str) -> dict | None:
    target = str(check_id or "").strip().lower()
    for row in checklist or []:
        if isinstance(row, dict) and str(row.get("id") or "").strip().lower() == target:
            return row
    return None


def _rebuild_property_findings_log(checklist: list[dict]) -> list[dict]:
    entries: list[dict] = []
    for row in checklist or []:
        if not isinstance(row, dict):
            continue
        for c in row.get("checks") or []:
            if not isinstance(c, dict) or not str(c.get("result") or "").strip():
                continue
            item: dict = {
                "date": str(c.get("date") or ""),
                "stage": str(row.get("stage") or ""),
                "activity": str(row.get("activity") or ""),
                "checkId": str(row.get("id") or ""),
                "result": str(c.get("result") or ""),
                "automated": bool(c.get("automated") or c.get("source") == "auto-sweep" or row.get("automated")),
            }
            if c.get("source"):
                item["source"] = c["source"]
            if c.get("discoveredAt"):
                item["discoveredAt"] = str(c["discoveredAt"])
            if c.get("loggedAt"):
                item["loggedAt"] = str(c["loggedAt"])
            if isinstance(c.get("adverse"), bool):
                item["adverse"] = c["adverse"]
            if c.get("agencyRecordId"):
                item["agencyRecordId"] = c["agencyRecordId"]
            if isinstance(c.get("links"), list) and c["links"]:
                item["links"] = c["links"]
            entries.append(item)
    entries.sort(key=lambda x: x.get("date") or "")
    for idx_e, ent in enumerate(entries, start=1):
        ent["entryNo"] = idx_e
    return entries


def collect_new_auto_sweep_findings(
    prop: dict,
    sweep_bundle: dict,
    sweep_date_iso: str = "",
) -> dict[str, list[dict]]:
    """Purely compute net-new deduplicated [AUTO-SWEEP] findings by row['id'] without mutating inputs."""
    checklist = prop.get("checklist") if isinstance(prop.get("checklist"), list) else []
    findings_by_check = (sweep_bundle or {}).get("findingsByCheck") or {}
    net_new: dict[str, list[dict]] = {}

    for check_id, new_findings in findings_by_check.items():
        row = _find_checklist_row_by_id(checklist, check_id)
        if not row:
            continue
        existing_keys: set[str] = set()
        existing_texts: set[str] = set()
        for ex in row.get("checks") or []:
            if not isinstance(ex, dict):
                continue
            if ex.get("agencyRecordId"):
                existing_keys.add(f"{check_id}::{ex['agencyRecordId']}")
            res_norm = " ".join(str(ex.get("result") or "").upper().split())
            if res_norm:
                existing_texts.add(res_norm)
                # Avoid duplicating human-logged project/permit numbers already documented in the row
                for ref_tok in re.findall(r"\b(?:TABS\d{8,12}|\d{8,11})\b", res_norm):
                    existing_texts.add(f"REF::{ref_tok}")

        accepted: list[dict] = []
        for nf in new_findings or []:
            if not isinstance(nf, dict):
                continue
            rec_key = f"{check_id}::{nf.get('agencyRecordId')}" if nf.get("agencyRecordId") else ""
            res_norm = " ".join(str(nf.get("result") or "").upper().split())
            ref_tokens = [f"REF::{t}" for t in re.findall(r"\b(?:TABS\d{8,12}|\d{8,11})\b", res_norm)]
            if (rec_key and rec_key in existing_keys) or (res_norm in existing_texts) or any(
                rt in existing_texts for rt in ref_tokens
            ):
                continue
            nf_copy = dict(nf)
            nf_copy["discoveredAt"] = str(nf_copy.get("discoveredAt") or sweep_date_iso)
            if isinstance(nf.get("links"), list):
                nf_copy["links"] = [dict(lk) for lk in nf["links"] if isinstance(lk, dict)]
            accepted.append(nf_copy)
            if rec_key:
                existing_keys.add(rec_key)
            existing_texts.add(res_norm)
            for rt in ref_tokens:
                existing_texts.add(rt)
        if accepted:
            net_new[check_id] = accepted
    return net_new


def merge_auto_sweep_findings_into_property(
    prop: dict,
    sweep_bundle: dict,
    sweep_timestamp_iso: str,
) -> dict:
    """Idempotently merge [AUTO-SWEEP] findings by row['id'] ('chk-01'..'chk-15') without mutating sweep_bundle."""
    sweep_date = sweep_timestamp_iso[:10]
    checklist = prop.get("checklist") if isinstance(prop.get("checklist"), list) else []
    findings_by_check = sweep_bundle.get("findingsByCheck") or {}
    net_new_by_check = collect_new_auto_sweep_findings(prop, sweep_bundle, sweep_date)

    for check_id in findings_by_check.keys():
        row = _find_checklist_row_by_id(checklist, check_id)
        if not row:
            continue
        row["automated"] = True
        row["lastAutoSweepAt"] = sweep_timestamp_iso
        checks = row.setdefault("checks", [])
        for nf_copy in net_new_by_check.get(check_id, []):
            checks.append(dict(nf_copy))

        # Keep row['lastChecked'] strictly for human check/acknowledgment dates (relying on
        # row['lastAutoSweepAt'] and chk['discoveredAt'] for machine sweeps) so backdated adverse
        # agency filings (e.g. TDLR TABS or COH permits registered weeks earlier) escalate to 'Flagged'.
        is_human_ack = bool(
            row.get("humanStatusEdited") is True or row.get("status") in ("Complete", "N/A")
        )
        human_ack_date = (
            str(row.get("lastChecked") or "") or str(row.get("updatedAt") or "")[:10]
            if is_human_ack
            else str(row.get("lastChecked") or "")
        )
        auto_checks = [
            ch
            for ch in checks
            if isinstance(ch, dict)
            and (
                ch.get("source") == "auto-sweep"
                or bool(ch.get("automated"))
                or str(ch.get("result") or "").strip().startswith("[AUTO-SWEEP]")
            )
        ]
        has_unseen_adverse = any(
            bool(ch.get("adverse"))
            and (
                not human_ack_date
                or max(str(ch.get("discoveredAt") or ""), str(ch.get("date") or "")) > human_ack_date
            )
            for ch in auto_checks
        )
        if has_unseen_adverse and row.get("status") != "N/A":
            row["status"] = "Flagged"
        elif row.get("status") == "To Do" and len(auto_checks) > 0 and not has_unseen_adverse:
            row["status"] = "In Progress"

    # Harvest 8-digit COH Sold Permit project numbers into Check #8 snowball projectNumbers
    harvested = sweep_bundle.get("harvestedProjectNumbers") or []
    if harvested:
        st = prop.setdefault("searchTerms", {})
        proj_list = list(st.get("projectNumbers") or [])
        for pno in harvested:
            if pno not in proj_list:
                proj_list.append(pno)
        st["projectNumbers"] = proj_list

    prop["lastAutoSweepAt"] = sweep_timestamp_iso
    prop["autoSweepSchedule"] = AUTO_SWEEP_SCHEDULE
    prop["findingsLog"] = _rebuild_property_findings_log(checklist)
    return prop


def is_removed_watchlist_tombstone(prop: object) -> bool:
    """Return True when a Watchlist record represents a soft-delete / removal tombstone (F2-02, DF-2)."""
    if not isinstance(prop, dict):
        return False
    if prop.get("removedFromWatchlist") is True:
        return True
    return str(prop.get("trackerStatus") or "").strip() in WATCHLIST_TOMBSTONE_STATUSES


def has_human_dossier_contributions(prop: dict | None) -> bool:
    """Return True if a Watchlist dossier contains human research that must never be hard-deleted.

    Maintains 1:1 predicate parity with hasHumanDossierContributions in app/js/platMonitorHelpers.js.
    """
    if not isinstance(prop, dict):
        return False
    pid = str(prop.get("id") or "")
    app_ref = str(
        prop.get("appNo")
        or prop.get("sourcePlatApp")
        or (pid[5:] if pid.startswith("plat-") else "")
    ).strip()
    is_plat_originated = pid.startswith("plat-") or bool(app_ref)
    if not is_plat_originated:
        return True
    if prop.get("humanEdited") is True:
        return True
    if (
        str(prop.get("phPastActions") or "").strip()
        or str(prop.get("reportedBy") or "").strip()
        or str(prop.get("broker") or "").strip()
    ):
        return True
    if str(prop.get("trackerStatus") or "").strip() in ("Saved", "Lost", "For Sale"):
        return True
    if str(prop.get("archivedAt") or "").strip():
        return True
    if isinstance(prop.get("driveLinks"), list) and len(prop["driveLinks"]) > 0:
        return True
    if isinstance(prop.get("documents"), list) and len(prop["documents"]) > 0:
        return True
    st = prop.get("searchTerms")
    if isinstance(st, dict):
        has_explicit_terms_ts = bool(prop.get("searchTermsUpdatedAt"))
        for cat_key, arr in st.items():
            if not isinstance(arr, list) or len(arr) == 0:
                continue
            if cat_key == "projectNumbers" and not has_explicit_terms_ts:
                continue
            return True
    for row in prop.get("checklist") or []:
        if not isinstance(row, dict):
            continue
        if row.get("humanStatusEdited") is True:
            return True
        if row.get("status") in ("Complete", "N/A"):
            return True
        if isinstance(row.get("links"), list) and len(row["links"]) > 0:
            return True
        checks = row.get("checks") if isinstance(row.get("checks"), list) else []
        for chk in checks:
            if not isinstance(chk, dict):
                continue
            src = str(chk.get("source") or "").strip()
            res = str(chk.get("result") or "").strip()
            is_auto = (
                bool(chk.get("automated"))
                or src in ("auto-sweep", "plat-seed")
                or res.startswith("[AUTO-SWEEP]")
                or res.startswith("[PLAT-SEED]")
            )
            is_plat_seed = row.get("id") == "chk-09" and (
                res.startswith("Confirmed from ")
                or (bool(app_ref) and res.startswith(f"{app_ref} ("))
            )
            has_links = isinstance(chk.get("links"), list) and len(chk["links"]) > 0
            if not is_auto and not is_plat_seed and (bool(res) or has_links):
                return True
        if row.get("id") != "chk-09" and row.get("status") == "In Progress" and len(checks) == 0:
            return True
    return False


def is_legacy_unconfirmed_plat_seed(raw: dict | None) -> bool:
    """Return True when a raw Watchlist record is an unconfirmed auto-seed or retracted baseline seed (F2-01, DF-1).

    Applied BEFORE normalization across all Python/JS hydration paths so legacy plat-* seeds never leak
    back into Watchlist while every record with human dossier contributions or explicit confirmation stays safe.
    """
    if not isinstance(raw, dict):
        return False
    if is_removed_watchlist_tombstone(raw):
        return False
    pid = str(raw.get("id") or "").strip()
    app_ref = str(
        raw.get("appNo")
        or raw.get("sourcePlatApp")
        or (pid[5:] if pid.startswith("plat-") else "")
    ).strip()
    if not (pid.startswith("plat-") or bool(app_ref)):
        return False
    if has_human_dossier_contributions(raw):
        return False
    if raw.get("tierConfirmed") is not True:
        return True
    updated_by_lower = str(raw.get("updatedBy") or "").strip().lower()
    if "houston building atlas" in updated_by_lower and raw.get("humanEdited") is not True:
        return True
    app_upper = app_ref.upper()
    addr_upper = " ".join(str(raw.get("address") or "").upper().split())
    if app_upper == "DPV-26073533" and (not raw.get("updatedAt") or "2603 MILAM" in addr_upper):
        return True
    if app_upper == "2026-1585" and (not raw.get("updatedAt") or "1226 W CLAY" in addr_upper):
        return True
    return False


def build_default_checklist_for_property(
    address: str,
    hcad: str = "",
    last_checked_auto: str = "",
) -> list[dict]:
    """Build the 15-point checklist template with Emily's verified portal URLs (P1-09..P1-14)."""
    q_addr = urllib.parse.quote_plus(f"{address or 'Houston'}, Houston, TX")
    simp_q = urllib.parse.quote(simplify_search_address(address).upper() or "HOUSTON")
    hcad_map_url = (
        f"https://arcweb.hcad.org/parcel-viewer-v2.0/?hcad_num={hcad}"
        if hcad
        else "https://arcweb.hcad.org/parcel-viewer-v2.0/"
    )
    return [
        {
            "id": "chk-01",
            "stage": "Every Check",
            "activity": "Public Notices",
            "whatToLookFor": "Search simplified street address, owner, and buyer names for published legal notices.",
            "typicalTiming": "Any stage.",
            "resourceLabel": "Texas Public Notices",
            "resourceUrl": "https://www.texaspublicnotices.com/",
            "additionalResourceLabel": "Houston Planning Notices",
            "additionalResourceUrl": NOTICES_URL,
            "status": "To Do",
            "lastChecked": "",
            "automated": False,
            "checks": [],
        },
        {
            "id": "chk-02",
            "stage": "Every Check",
            "activity": "Site Visit",
            "whatToLookFor": "Drive by and photograph the site. Look for TABC 60-day signs, plat or variance signs, fencing, dumpsters, and survey stakes.",
            "typicalTiming": "Any stage. Posted signs can appear before records show up online.",
            "resourceLabel": "Google Street View",
            "resourceUrl": f"https://www.google.com/maps/search/?api=1&query={q_addr}",
            "additionalResourceLabel": "",
            "additionalResourceUrl": "",
            "status": "To Do",
            "lastChecked": "",
            "automated": False,
            "checks": [],
        },
        {
            "id": "chk-03",
            "stage": "Sale & Ownership",
            "activity": "Deed Transfers & Liens",
            "whatToLookFor": "Search Block & Subdivision keywords or Grantor/Grantee names for deeds, deeds of trust (naming the lender), and purchase options.",
            "typicalTiming": "Deeds usually record within days of closing.",
            "resourceLabel": "HC Clerk Real Property",
            "resourceUrl": "https://www.cclerk.hctx.net/applications/websearch/RP.aspx",
            "additionalResourceLabel": "",
            "additionalResourceUrl": "",
            "status": "To Do",
            "lastChecked": "",
            "automated": False,
            "checks": [],
        },
        {
            "id": "chk-04",
            "stage": "Sale & Ownership",
            "activity": "Corporate Entity Verification",
            "whatToLookFor": "Look up each LLC across TX Comptroller, Bizapedia, Google Search, and TX SOSDirect to find officers, registered agents, and related entities.",
            "typicalTiming": "As soon as a new entity name surfaces.",
            "resourceLabel": "Comptroller Entity Search",
            "resourceUrl": "https://comptroller.texas.gov/taxes/franchise/account-status/search",
            "additionalResourceLabel": "Bizapedia Entity Lookup",
            "additionalResourceUrl": "https://www.bizapedia.com/",
            "status": "To Do",
            "lastChecked": last_checked_auto,
            "automated": True,
            "checks": [],
        },
        {
            "id": "chk-05",
            "stage": "Sale & Ownership",
            "activity": "HCAD Ownership & Classification",
            "whatToLookFor": "Search the 13-digit HCAD account number to confirm the owner of record, legal description, and land/improvement value shifts.",
            "typicalTiming": "Owner updates lag the deed by weeks to months.",
            "resourceLabel": "HCAD Property Search",
            "resourceUrl": "https://search.hcad.org/",
            "additionalResourceLabel": "HCAD GIS Parcel Map",
            "additionalResourceUrl": hcad_map_url,
            "status": "To Do",
            "lastChecked": last_checked_auto,
            "automated": True,
            "checks": [],
        },
        {
            "id": "chk-06",
            "stage": "Plans & Use",
            "activity": "Liquor Licensing",
            "whatToLookFor": "Search simplified primary and perimeter addresses for pending or active TABC permits revealing operator LLCs and trade names.",
            "typicalTiming": "Often filed months before construction or demolition permits.",
            "resourceLabel": "TABC Public Inquiry",
            "resourceUrl": "https://apps.tabc.texas.gov/publicinquiry/",
            "additionalResourceLabel": "",
            "additionalResourceUrl": "",
            "status": "To Do",
            "lastChecked": last_checked_auto,
            "automated": True,
            "checks": [],
        },
        {
            "id": "chk-07",
            "stage": "Plans & Use",
            "activity": "Hemp Licensing",
            "whatToLookFor": "Search DSHS Consumable Hemp registrations by entity name, DBA/trade name, or officer name (portal does not search by street address).",
            "typicalTiming": "After closing or lease execution for convenience-store operators.",
            "resourceLabel": "DSHS License Search",
            "resourceUrl": "https://vo.ras.dshs.state.tx.us/datamart/login.do",
            "additionalResourceLabel": "",
            "additionalResourceUrl": "",
            "status": "To Do",
            "lastChecked": "",
            "automated": False,
            "checks": [],
        },
        {
            "id": "chk-08",
            "stage": "Plans & Use",
            "activity": "Pending Plans (Open Records & ILMS)",
            "whatToLookFor": "Look up 8-digit project numbers (found via Sold Permits) in COH ILMS, or submit a GovQA open records request covering all perimeter block addresses.",
            "typicalTiming": "Design and plan-review phase after closing.",
            "resourceLabel": "Public Records (GovQA)",
            "resourceUrl": "https://houstontx.govqa.us/WEBAPP/_rs/supporthome.aspx",
            "additionalResourceLabel": "ILMS Project Status",
            "additionalResourceUrl": "https://www.pdinet.pd.houstontx.gov/cohilms/webs/plan_lookup.asp",
            "status": "To Do",
            "lastChecked": "",
            "automated": False,
            "checks": [],
        },
        {
            "id": "chk-09",
            "stage": "Plans & Use",
            "activity": "Replats & Variances",
            "whatToLookFor": "Check Biweekly Planning Commission spreadsheets, Agenda Outlines (Section K DPV), and Public Hearing Notices for replats or variances.",
            "typicalTiming": "Can be filed before closing with seller consent or during design.",
            "resourceLabel": "COH Biweekly Plat Reports",
            "resourceUrl": DEV_REPORTS_URL,
            "additionalResourceLabel": "COH Planning Notices",
            "additionalResourceUrl": NOTICES_URL,
            "status": "To Do",
            "lastChecked": last_checked_auto,
            "automated": True,
            "checks": [],
        },
        {
            "id": "chk-10",
            "stage": "Plans & Use",
            "activity": "Accessibility Registration (TDLR TABS)",
            "whatToLookFor": "Search simplified primary and perimeter addresses, DBAs, and owner LLCs in TDLR TABS (required for commercial projects >= $50,000).",
            "typicalTiming": "Design phase, often months before building or demolition permits.",
            "resourceLabel": "TDLR TABS Search",
            "resourceUrl": TDLR_TABS_PORTAL_URL,
            "additionalResourceLabel": "",
            "additionalResourceUrl": "",
            "status": "To Do",
            "lastChecked": last_checked_auto,
            "automated": True,
            "checks": [],
        },
        {
            "id": "chk-11",
            "stage": "Plans & Use",
            "activity": "Easements, Abandonments & TIRZ",
            "whatToLookFor": "Check City Council NovusAgenda packets for street, alley, or easement abandonments and check local TIRZ board agendas.",
            "typicalTiming": "Design phase, usually after closing.",
            "resourceLabel": "City Council Agendas (NovusAgenda)",
            "resourceUrl": NOVUS_AGENDA_URL,
            "additionalResourceLabel": "",
            "additionalResourceUrl": "",
            "status": "To Do",
            "lastChecked": "",
            "automated": False,
            "checks": [],
        },
        {
            "id": "chk-12",
            "stage": "Plans & Use",
            "activity": "Environmental Registrations",
            "whatToLookFor": "Search TCEQ Central Registry for underground petroleum storage tanks (PST) or construction stormwater registrations.",
            "typicalTiming": "Shortly before construction, especially for fuel canopies and larger commercial sites.",
            "resourceLabel": "TCEQ Central Registry",
            "resourceUrl": "https://www.tceq.texas.gov/permitting/central_registry",
            "additionalResourceLabel": "",
            "additionalResourceUrl": "",
            "status": "To Do",
            "lastChecked": "",
            "automated": False,
            "checks": [],
        },
        {
            "id": "chk-13",
            "stage": "Demolition Signals",
            "activity": "Code Complaints & Dangerous Buildings",
            "whatToLookFor": "Check Houston 311 complaints and cross-check periodic City Dangerous Buildings (DONDB) CSV lists so historic buildings can be flagged.",
            "typicalTiming": "Any stage, especially while a structure is vacant.",
            "resourceLabel": "311 Service Request Viewer",
            "resourceUrl": "https://experience.arcgis.com/experience/cf006ed734814cee9f5b9bcf7abc839f",
            "additionalResourceLabel": "Community Code Enforcement",
            "additionalResourceUrl": "https://www.houstonpermittingcenter.org/community-code-enforcement",
            "status": "To Do",
            "lastChecked": last_checked_auto,
            "automated": True,
            "checks": [],
        },
        {
            "id": "chk-14",
            "stage": "Demolition Signals",
            "activity": "Demolition & Building Permits",
            "whatToLookFor": "Search Houston Sold Permits by simplified primary and perimeter addresses for demolition, sewer disconnect, and commercial building permits.",
            "typicalTiming": "Final weeks before site work starts (also reveals 8-digit project numbers for Check #8 ILMS).",
            "resourceLabel": "Houston Sold Permits",
            "resourceUrl": COH_SOLD_PERMITS_PORTAL_URL,
            "additionalResourceLabel": "",
            "additionalResourceUrl": "",
            "status": "To Do",
            "lastChecked": last_checked_auto,
            "automated": True,
            "checks": [],
        },
        {
            "id": "chk-15",
            "stage": "Demolition Signals",
            "activity": "Asbestos & Demolition Notification (DSHS)",
            "whatToLookFor": "DSHS must be notified at least 10 working days before any commercial or public building is abated or demolished.",
            "typicalTiming": "At least 10 working days before abatement or demolition starts.",
            "resourceLabel": "DSHS Notifications",
            "resourceUrl": "https://vo.ras.dshs.state.tx.us/datamart/mainMenu.do",
            "additionalResourceLabel": "",
            "additionalResourceUrl": "",
            "status": "To Do",
            "lastChecked": last_checked_auto,
            "automated": True,
            "checks": [],
        },
    ]


def normalize_watch_property_record(raw: dict) -> dict:
    """Normalize Watchlist schema aliases (architectEngineer <-> designFirm), tracker fields, and 15-point checklist URLs."""
    p = dict(raw or {})
    pid = str(p.get("id") or "").strip()
    resolved_app = str(
        p.get("appNo")
        or p.get("sourcePlatApp")
        or (pid[5:] if pid.startswith("plat-") else "")
    ).strip()
    p["appNo"] = resolved_app
    p["sourcePlatApp"] = resolved_app
    p["linkedAppNo"] = str(p.get("linkedAppNo") or "").strip()
    design_firm = str(p.get("designFirm") or p.get("architectEngineer") or "").strip()
    p["designFirm"] = design_firm
    p["architectEngineer"] = design_firm
    p["broker"] = str(p.get("broker") or "").strip()
    p["developer"] = str(p.get("developer") or "").strip()
    p.setdefault("legalDescription", "")
    is_tombstone = is_removed_watchlist_tombstone(p)
    tier_val = str(p.get("tier") or "1").strip()
    if tier_val not in ("1", "2", "3"):
        tier_val = "1"
    p["tier"] = tier_val
    is_plat_orig = pid.startswith("plat-") or bool(resolved_app)
    if is_tombstone:
        p["removedFromWatchlist"] = (
            bool(p.get("removedFromWatchlist"))
            or str(p.get("trackerStatus") or "").strip() == REMOVED_WATCHLIST_TOMBSTONE
        )
        raw_status = str(p.get("trackerStatus") or "").strip()
        p["trackerStatus"] = (
            raw_status
            if raw_status in WATCHLIST_TOMBSTONE_STATUSES
            else REMOVED_WATCHLIST_TOMBSTONE
        )
        p["tierConfirmed"] = False
    else:
        p["removedFromWatchlist"] = False
        raw_status = str(p.get("trackerStatus") or "").strip()
        default_status = (
            "Archived (Tier 3)"
            if tier_val == "3"
            else ("Monitoring" if tier_val == "2" else "Active / High Priority")
        )
        if not raw_status or raw_status not in WATCHLIST_TRACKER_STATUSES:
            raw_status = default_status
        elif tier_val == "3" and raw_status in (
            "Active / High Priority",
            "Monitoring",
            "Dormant / Medium Priority",
        ):
            raw_status = "Archived (Tier 3)"
        p["trackerStatus"] = raw_status
        p["tierConfirmed"] = (
            bool(p.get("tierConfirmed"))
            if is_plat_orig
            else (p.get("tierConfirmed") is not False)
        )
    p.setdefault("threatCategory", "")
    p.setdefault("neighborhood", "")
    p.setdefault("reportedBy", "")
    p.setdefault("phPastActions", "")
    p.setdefault("platProjectName", "")
    p.setdefault("pcMeetingDate", "")
    p.setdefault("sourceCycleKey", "")
    p.setdefault("hearingDateIso", "")
    st = p.get("searchTerms") if isinstance(p.get("searchTerms"), dict) else {}
    p["searchTerms"] = {
        "entities": list(st.get("entities") or []),
        "dbas": list(st.get("dbas") or []),
        "officers": list(st.get("officers") or []),
        "legalKeywords": list(st.get("legalKeywords") or []),
        "altAddresses": list(st.get("altAddresses") or []),
        "projectNumbers": list(st.get("projectNumbers") or []),
    }
    raw_docs = p.get("documents") if isinstance(p.get("documents"), list) else (p.get("driveLinks") or [])
    clean_docs = [
        {"label": str(d.get("label") or d.get("url") or "Document").strip(), "url": str(d["url"]).strip()}
        for d in raw_docs
        if isinstance(d, dict)
        and str(d.get("url") or "").strip().lower().startswith(("http://", "https://"))
    ]
    p["documents"] = clean_docs
    p["driveLinks"] = list(clean_docs)

    # Upgrade all 15 checklist rows by canonical row['id'] ('chk-01'..'chk-15') onto canonical template
    address = str(p.get("address") or p.get("propertyName") or "").strip()
    hcad = str(p.get("hcadAccount") or "").split(",")[0].strip()
    template_rows = build_default_checklist_for_property(address, hcad if re.fullmatch(r"\d{13}", hcad) else "", "")
    incoming_rows = p.get("checklist") if isinstance(p.get("checklist"), list) else []
    upgraded_checklist: list[dict] = []
    for idx_t, tpl in enumerate(template_rows):
        matched = _find_checklist_row_by_id(incoming_rows, tpl["id"])
        if matched is None and idx_t < len(incoming_rows) and isinstance(incoming_rows[idx_t], dict) and not incoming_rows[idx_t].get("id"):
            matched = incoming_rows[idx_t]
        if matched is None:
            upgraded_checklist.append(tpl)
            continue
        out_row = dict(tpl)
        if matched.get("status") in ("To Do", "In Progress", "Flagged", "Complete", "N/A"):
            out_row["status"] = matched["status"]
        out_row["lastChecked"] = str(matched.get("lastChecked") or "")
        out_row["checks"] = list(matched.get("checks") or [])
        if matched.get("updatedAt"):
            out_row["updatedAt"] = str(matched["updatedAt"])
        if matched.get("lastAutoSweepAt"):
            out_row["lastAutoSweepAt"] = str(matched["lastAutoSweepAt"])
        if matched.get("humanStatusEdited") is True:
            out_row["humanStatusEdited"] = True
        if isinstance(matched.get("statusChangedAt"), str) and matched["statusChangedAt"].strip():
            out_row["statusChangedAt"] = matched["statusChangedAt"].strip()
        # Preserve specific TIRZ link on chk-11 or DSHS notification print link on chk-15
        if tpl["id"] == "chk-11" and matched.get("additionalResourceUrl"):
            out_row["additionalResourceLabel"] = str(matched.get("additionalResourceLabel") or "TIRZ Board")
            out_row["additionalResourceUrl"] = str(matched["additionalResourceUrl"])
        if tpl["id"] == "chk-15" and "notificationPrintAction.do" in str(matched.get("additionalResourceUrl") or ""):
            out_row["additionalResourceLabel"] = str(matched.get("additionalResourceLabel") or "DSHS Notification")
            out_row["additionalResourceUrl"] = str(matched["additionalResourceUrl"])
        upgraded_checklist.append(out_row)
    p["checklist"] = upgraded_checklist
    p["findingsLog"] = _rebuild_property_findings_log(upgraded_checklist)
    return p


def pull_cloud_sheet_sync_rows(csv_text: str | None = None) -> dict:
    """Pull approved PLAT_MONITOR_REVIEW and PROPERTY_WATCHLIST_UPDATE rows from Plat_&_Watchlist_Sync."""
    if csv_text is None:
        raw_b = fetch_bytes(f"{SHEET_PUBLISHED_CSV_URL}&_cb={int(dt.datetime.now().timestamp())}", timeout=15)
        if not raw_b:
            return {"platReviews": {}, "watchProperties": {}}
        csv_text = raw_b.decode("utf-8", errors="ignore")

    plat_reviews: dict[tuple[str, str], dict] = {}
    watch_props: dict[str, dict] = {}

    reader = csv.DictReader(io.StringIO(csv_text))
    for row in reader:
        norm = {str(k or "").strip().lower(): str(v or "").strip() for k, v in row.items()}
        if norm.get("status", "").lower() != "approved":
            continue
        key = norm.get("hcad_num", "")
        src_type = norm.get("source_type", "").upper()
        citation = norm.get("source_citation", "")
        if not citation.startswith("{"):
            continue
        try:
            parsed = json.loads(citation)
        except Exception:
            continue
        if not isinstance(parsed, dict):
            continue

        if src_type == "PLAT_MONITOR_REVIEW" or key.startswith("PLAT_REV:"):
            parts = key.split(":")
            cycle_key = str(parsed.get("cycleKey") or (parts[1] if len(parts) >= 3 else "")).strip()
            app_no = str(
                parsed.get("appNo")
                or parsed.get("app")
                or (":".join(parts[2:]) if len(parts) >= 3 else "")
            ).strip()
            if app_no:
                rev_obj = dict(parsed.get("review") if isinstance(parsed.get("review"), dict) else parsed)
                if norm.get("verified_by") and not rev_obj.get("reviewer"):
                    rev_obj["reviewer"] = norm["verified_by"]
                plat_reviews[(cycle_key, app_no)] = rev_obj
        elif src_type == "PROPERTY_WATCHLIST_UPDATE" or key.startswith("WATCH_PROP:"):
            prop_obj = dict(parsed.get("property") if isinstance(parsed.get("property"), dict) else parsed)
            pid = str(prop_obj.get("id") or key.replace("WATCH_PROP:", "", 1)).strip()
            if pid:
                prop_obj["id"] = pid
                if is_legacy_unconfirmed_plat_seed(prop_obj):
                    continue
                has_explicit_terms = isinstance(prop_obj.get("searchTerms"), dict) or bool(
                    prop_obj.get("searchTermsUpdatedAt")
                )
                has_explicit_links = (
                    isinstance(prop_obj.get("driveLinks"), list)
                    or isinstance(prop_obj.get("documents"), list)
                    or bool(prop_obj.get("driveLinksUpdatedAt"))
                )
                has_explicit_checklist = isinstance(prop_obj.get("checklist"), list) and len(
                    prop_obj.get("checklist")
                ) > 0
                prev = watch_props.get(pid)
                if not prev or str(prop_obj.get("updatedAt") or "") >= str(prev.get("updatedAt") or ""):
                    norm_rec = normalize_watch_property_record(prop_obj)
                    norm_rec["_hasExplicitSearchTerms"] = has_explicit_terms
                    norm_rec["_hasExplicitDriveLinks"] = has_explicit_links
                    norm_rec["_hasExplicitChecklist"] = has_explicit_checklist
                    watch_props[pid] = norm_rec

    return {"platReviews": plat_reviews, "watchProperties": watch_props}


def _build_kirby_mansion_seed(sweep_date: str) -> dict:
    kirby_checklist = build_default_checklist_for_property("2000 Smith St.", "0152300000001", "")
    chk11 = _find_checklist_row_by_id(kirby_checklist, "chk-11")
    if chk11:
        chk11["additionalResourceLabel"] = "Midtown Redevelopment Authority"
        chk11["additionalResourceUrl"] = "https://midtownhouston.com/affiliated-organizations/mra/"
    seed_rows = {
        "chk-01": (
            "In Progress",
            "2026-10-20",
            [
                {"date": "2026-09-22", "result": "3 notices from February - March 2026 related to TABC permit"},
                {"date": "2026-10-05", "result": "no new results"},
                {"date": "2026-10-09", "result": "no new results"},
                {"date": "2026-10-20", "result": "no results"},
            ],
        ),
        "chk-02": (
            "In Progress",
            "2026-10-14",
            [
                {"date": "2026-10-07", "result": "Reported by Paul Homeyer: new 'For Information' banner from Greenberg & Company"},
                {"date": "2026-10-14", "result": "Called Greenberg & Company. Took message; have not called back"},
            ],
        ),
        "chk-03": (
            "Complete",
            "2026-10-05",
            [
                {
                    "date": "2026-10-05",
                    "result": "Special Warranty Deed (#RP-2026-399473) & Deed of Trust ($7M loan from Greenberg Finance Co.) dated 9/30/2026 and recorded 10/1/2026 to OB Texas Properties LLC (Omair Bashir)",
                }
            ],
        ),
        "chk-04": (
            "Complete",
            "2026-10-23",
            [
                {
                    "date": "2026-09-22",
                    "result": "Smith St C Store LLC (formed Oct 2025, Active Sales Tax Permit #32102472084 for 'Brake Time' at 2000 Smith St); BTCM National Retail Operations LLC",
                },
                {
                    "date": "2026-10-05",
                    "result": "OB Texas Properties LLC (Taxpayer #32062633212, Manager Omair Bashir, 6300 Richmond Ave Ste 300) https://comptroller.texas.gov/taxes/franchise/account-status/search/32062633212",
                },
            ],
        ),
        "chk-05": (
            "In Progress",
            "2026-10-23",
            [
                {
                    "date": "2026-10-23",
                    "result": "HCAD Account 0152300000001 (Tracts 1-16, Block 417 SSBB, 62,500 SF full city block bounded by Smith, Pierce, Brazos, and Gray); deed transfer to OB Texas Properties LLC closed 9/30/2026",
                }
            ],
        ),
        "chk-06": (
            "Complete",
            "2026-10-07",
            [
                {"date": "2026-09-22", "result": "TABC permit for 2000 Smith under Smith St C Store LLC issued 4/1/26"},
                {"date": "2026-10-07", "result": "Submitted public records request for application materials, including site plan; denied due to confidentiality"},
            ],
        ),
        "chk-07": (
            "Complete",
            "2026-09-22",
            [{"date": "2026-09-22", "result": "'Retail Hemp Registration' for Smith St C Store LLC at 2000 Smith (applied Feb-Mar 2026)"}],
        ),
        "chk-08": (
            "Flagged",
            "2026-10-22",
            [
                {
                    "date": "2026-10-22",
                    "result": "COH Plan Review Project #26052089 filed under perimeter block address 530 Pierce St on 6/24/2026 for a proposed gas station; plans submitted 8 times and returned for revisions 10/2/2026",
                }
            ],
        ),
        "chk-09": (
            "Complete",
            "2026-10-23",
            [
                {
                    "date": "2026-09-22",
                    "result": "Planning Commission approved 'Smith Street Plaza' replat consolidating block into 1 unrestricted reserve on 5/28/2026 (not yet recorded in County Clerk plat records)",
                }
            ],
        ),
        "chk-10": (
            "Flagged",
            "2026-10-23",
            [
                {
                    "date": "2026-10-22",
                    "result": "TDLR Project #TABS2026022839 ('Braketime Smith Road', Harris County 2101) registered 6/17/2026 at perimeter address 530 Pierce St: $2,000,000 new construction of 13,254 SF convenience store (scheduled 9/1/2026 - 9/1/2027), design firm Heights Engineering (Munir Ishaq, P.E.), owner Hi Tech Auto Realty LLC / tenant Brake Time",
                }
            ],
        ),
        "chk-12": (
            "In Progress",
            "2026-10-20",
            [
                {"date": "2026-09-22", "result": "old/irrelevant results. RN for location: RN101872331"},
                {"date": "2026-10-05", "result": "no new results"},
                {"date": "2026-10-09", "result": "OB Texas Properties is registered as a customer since 10/9/2020, but no affiliated regulated entities. CN605820570"},
                {"date": "2026-10-20", "result": "no new results"},
            ],
        ),
        "chk-13": ("N/A", "2026-10-23", []),
        "chk-14": (
            "Flagged",
            "2026-10-23",
            [
                {"date": "2026-09-22", "result": "no demo permit"},
                {"date": "2026-10-05", "result": "no demo permit"},
                {
                    "date": "2026-10-14",
                    "result": "COH Permit #26090093 ('BLD DEMO/SEWER DISC', occupant '*KIRBY MANSION') issued 10/14/2026 to Robert Camacho at 2000 Smith St",
                },
            ],
        ),
        "chk-15": (
            "Flagged",
            "2026-10-23",
            [
                {"date": "2026-09-22", "result": "no results"},
                {"date": "2026-10-05", "result": "no results"},
                {
                    "date": "2026-10-13",
                    "result": "DSHS Notification #2026005825 filed 10/13/2026 for 2000 Smith St: abatement of 5,010 SF RACM scheduled 10/27/2026 - 11/6/2026 by AAR Incorporated for owner Omair Bashir (OB Texas Properties LLC)",
                },
            ],
        ),
    }
    for cid, (st_val, lc_val, chk_list) in seed_rows.items():
        row = _find_checklist_row_by_id(kirby_checklist, cid)
        if row:
            row["status"] = st_val
            row["lastChecked"] = lc_val
            row["checks"] = chk_list
            if cid == "chk-15":
                row["additionalResourceLabel"] = "DSHS Notification #2026005825"
                row["additionalResourceUrl"] = (
                    "https://vo.ras.dshs.state.tx.us/datamart/notificationPrintAction.do?licId=2451374"
                )

    return normalize_watch_property_record(
        {
            "id": "prop-kirby-mansion",
            "propertyName": "John Henry Kirby Mansion",
            "platProjectName": "Smith Street Plaza",
            "address": "2000 Smith St.",
            "secondaryAddresses": ["530 Pierce St.", "2000 Brazos St.", "500 Gray St."],
            "hcadAccount": "0152300000001",
            "legalDescription": "TRS 1-16 BLK 417 SSBB",
            "buildingId": "bld_004618",
            "yearBuilt": 1894,
            "yearRemodeled": 1928,
            "architect": "James Ruskin Bailey (1928 Tudor Revival remodel)",
            "designation": "1979 Good Brick Award (No City Protected Landmark designation)",
            "neighborhood": "Midtown",
            "trackerStatus": "Active / High Priority",
            "threatCategory": "Significant Building Threatened",
            "reportedBy": "Paul Homeyer",
            "phPastActions": "Contacted listing broker and new owner Omair Bashir; monitoring October 27 - November 6, 2026 asbestos abatement window.",
            "ownerOfRecord": "OB Texas Properties LLC (acquired 9/30/2026 from Hi Tech Auto Realty LLC)",
            "buyerRelatedEntities": "Omair Bashir / OB Texas Properties LLC / Smith St C Store LLC / BTCM National Retail Operations LLC (Brake Time)",
            "lender": "Greenberg Finance Co. ($7,000,000 loan recorded 10/1/2026)",
            "designFirm": "Heights Engineering (Munir Ishaq, P.E.)",
            "keyDates": {
                "monitoringStarted": "2026-09-22",
                "targetClosing": "2026-09-30",
                "planningCommission": "2026-05-28",
                "asbestosAbatementWindow": "2026-10-27 to 2026-11-06",
                "expectedDemolition": "Imminent risk following Nov 6, 2026 abatement window",
                "tdlrScheduledConstruction": "2026-09-01 to 2027-09-01",
            },
            "searchTerms": {
                "entities": [
                    "Smith St C Store LLC",
                    "OB Texas Properties LLC",
                    "BTCM National Retail Operations LLC",
                    "Hi Tech Auto Realty LLC",
                    "Greenberg Finance Co.",
                ],
                "dbas": ["Brake Time", "Braketime Smith Road"],
                "officers": ["Omair Bashir", "Munir Ishaq"],
                "legalKeywords": ["417", "SSBB", "HADLEY", "Smith Street Plaza"],
                "altAddresses": ["530 Pierce St.", "2000 Brazos St.", "500 Gray St."],
                "projectNumbers": ["26052089", "26090093"],
            },
            "notes": (
                "Sale closed effective 9/30/2026 (recorded 10/1/2026, Special Warranty Deed #RP-2026-399473). "
                "Multi-agency filings across 2000 Smith St and perimeter block address 530 Pierce St reveal plans "
                "for a $2M, 13,254 SF 'Brake Time' convenience store and gas station (TDLR #TABS2026022839, "
                "COH Plan Review #26052089). Emailed Omair Bashir, who responded positively about meeting to discuss plans."
            ),
            "lng": -95.374833,
            "lat": 29.751432,
            "tier": "1",
            "tierConfirmed": True,
            "checklist": kirby_checklist,
            "findingsLog": _rebuild_property_findings_log(kirby_checklist),
        }
    )


def _build_confirmed_plat_watch_property(it: dict, rev: dict, cycle_iso: str, sweep_date: str) -> dict:
    at = it.get("atlas") if isinstance(it.get("atlas"), dict) else {}
    addr = str(rev.get("address") or at.get("primaryAddress") or it.get("noticeLocation") or it.get("name") or "").strip()
    hcad = str(rev.get("hcad") or it.get("hcad") or "").strip()
    yr = _collect_valid_year(rev.get("yearBuilt")) or _collect_valid_year(at.get("earliestYearBuilt"))
    checklist = build_default_checklist_for_property(addr, hcad, "")
    chk9 = _find_checklist_row_by_id(checklist, "chk-09")
    if chk9:
        chk9["status"] = "Flagged"
        chk9["lastChecked"] = cycle_iso
        links: list[dict[str, str]] = []
        if it.get("pdf"):
            links.append({"label": "Plat PDF", "url": str(it["pdf"])})
        if it.get("noticePdf"):
            links.append({"label": "Public Hearing Notice PDF", "url": str(it["noticePdf"])})
        chk9["checks"] = [
            {
                "date": cycle_iso,
                "result": (
                    f"Confirmed from {cycle_iso} Planning Commission Agenda ({it['app']} - {it.get('type', 'Plat')}): "
                    f"{rev.get('why') or it.get('varianceSummary') or it.get('special') or it.get('section')}"
                ),
                "links": links,
                "source": "plat-seed",
            }
        ]
    if at.get("hasActivePermit") and at.get("activePermitHeadline"):
        chk14 = _find_checklist_row_by_id(checklist, "chk-14")
        if chk14:
            chk14["status"] = "Flagged"
            chk14["checks"] = [
                {
                    "date": sweep_date,
                    "discoveredAt": sweep_date,
                    "result": str(at["activePermitHeadline"]),
                    "source": "auto-sweep",
                    "adverse": True,
                }
            ]

    historic_title = str(at.get("buildingName") or addr or it.get("name") or "").strip()
    return normalize_watch_property_record(
        {
            "id": f"plat-{it['app']}",
            "appNo": it["app"],
            "sourcePlatApp": it["app"],
            "linkedAppNo": str(it.get("linkedAppNo") or ""),
            "sourceCycleKey": cycle_iso,
            "propertyName": historic_title,
            "platProjectName": it.get("name", ""),
            "address": addr,
            "secondaryAddresses": [a for a in (at.get("addresses") or []) if a and a != addr][:3],
            "hcadAccount": hcad,
            "legalDescription": str(rev.get("legalDescription") or it.get("prevSub") or "").strip(),
            "buildingId": at.get("buildingId", ""),
            "yearBuilt": yr,
            "yearRemodeled": None,
            "architect": "",
            "designation": rev.get("designation") or (", ".join(at.get("designations") or []) or "Undesignated"),
            "neighborhood": it.get("superN") or at.get("superNeighborhoodMatch") or "",
            "trackerStatus": "Active / High Priority" if str(rev.get("tier") or "1") == "1" else "Monitoring",
            "threatCategory": str(rev.get("threatCategory") or ""),
            "ownerOfRecord": it.get("developer") or "See HCAD Property Search",
            "developer": it.get("developer") or "",
            "buyerRelatedEntities": it.get("developer") or "",
            "broker": "",
            "lender": "",
            "designFirm": it.get("org") or it.get("applicant") or "",
            "pcMeetingDate": cycle_iso,
            "hearingDateIso": str(it.get("hearingDateIso") or ""),
            "keyDates": {
                "monitoringStarted": sweep_date,
                "targetClosing": "",
                "planningCommission": cycle_iso,
                "publicHearingDate": str(it.get("hearingDateIso") or ""),
                "asbestosAbatementWindow": "",
                "expectedDemolition": "",
                "tdlrScheduledConstruction": "",
            },
            "notes": f"{rev.get('why', '')} {rev.get('followup', '')}".strip(),
            "lng": at.get("lng"),
            "lat": at.get("lat"),
            "tier": str(rev.get("tier") or "1"),
            "tierConfirmed": True,
            "checklist": checklist,
            "findingsLog": _rebuild_property_findings_log(checklist),
        }
    )


def _strip_internal_cloud_flags(prop: dict) -> dict:
    for k in ("_hasExplicitSearchTerms", "_hasExplicitDriveLinks", "_hasExplicitChecklist"):
        prop.pop(k, None)
    return prop


def build_property_watchlist(
    plat_data: dict,
    idx: dict,
    cloud_sync: dict | None = None,
    run_live_sweeps: bool = True,
    sweep_timestamp_iso: str = "",
) -> dict:
    """Build property_watchlist.json containing ONLY confirmed watched properties (P2-07, DF-2, DF-4)."""
    print("[4/6] Building confirmed 15-Point Property Watchlist & running 8-agency automated sweeps...")
    now_iso = sweep_timestamp_iso or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sweep_date = now_iso[:10]
    cloud_watch = (cloud_sync or {}).get("watchProperties") or {}

    watched_by_id: dict[str, dict] = {}
    kirby = _build_kirby_mansion_seed(sweep_date)
    watched_by_id[kirby["id"]] = kirby

    # Include ONLY explicitly confirmed Tier 1 / Tier 2 plat applications across loaded cycles
    cycles_map = plat_data.get("cycles") or {plat_data.get("cycleKey", sweep_date): plat_data}
    latest_plat_tier_by_app: dict[str, tuple[str, str]] = {}
    for c_iso in sorted(cycles_map.keys()):
        cyc = cycles_map[c_iso]
        reviews = cyc.get("reviews") or {}
        for it in cyc.get("items") or []:
            rev = reviews.get(it["app"]) or {}
            pid = f"plat-{it['app']}"
            tier_val = str(rev.get("tier") or "")
            rev_ts = str(rev.get("updated") or c_iso or "")
            if tier_val:
                latest_plat_tier_by_app[it["app"]] = (tier_val, rev_ts)
                for cid in get_canonical_plat_app_ids(it):
                    latest_plat_tier_by_app[cid] = (tier_val, rev_ts)
            is_confirmed = bool(rev.get("tierConfirmed"))
            cloud_for_pid = cloud_watch.get(pid)
            if (
                cloud_for_pid
                and is_removed_watchlist_tombstone(cloud_for_pid)
                and str(cloud_for_pid.get("updatedAt") or "9999") >= rev_ts
            ):
                watched_by_id.pop(pid, None)
                continue
            if is_confirmed and tier_val in ("1", "2"):
                seed_p = _build_confirmed_plat_watch_property(it, rev, c_iso, sweep_date)
                if rev.get("updated"):
                    seed_p["updatedAt"] = str(rev["updated"])
                watched_by_id[pid] = seed_p
            elif tier_val == "3" and pid in watched_by_id:
                existing_p = watched_by_id[pid]
                if has_human_dossier_contributions(existing_p):
                    existing_p["tier"] = "3"
                    existing_p["tierConfirmed"] = True
                    existing_p["trackerStatus"] = "Archived (Tier 3)"
                else:
                    watched_by_id.pop(pid, None)

    # Merge cloud-synced staff-created or staff-edited Watchlist dossiers
    for pid, cloud_prop in cloud_watch.items():
        if is_legacy_unconfirmed_plat_seed(cloud_prop):
            continue
        has_explicit_terms = (
            bool(cloud_prop.get("_hasExplicitSearchTerms"))
            if "_hasExplicitSearchTerms" in cloud_prop
            else (
                bool(cloud_prop.get("searchTermsUpdatedAt"))
                or any(
                    bool(v)
                    for v in (
                        (cloud_prop.get("searchTerms") if isinstance(cloud_prop.get("searchTerms"), dict) else {}).values()
                    )
                )
            )
        )
        has_explicit_links = (
            bool(cloud_prop.get("_hasExplicitDriveLinks"))
            if "_hasExplicitDriveLinks" in cloud_prop
            else (
                bool(cloud_prop.get("driveLinksUpdatedAt"))
                or bool(cloud_prop.get("driveLinks"))
                or bool(cloud_prop.get("documents"))
            )
        )
        has_explicit_checklist = (
            bool(cloud_prop.get("_hasExplicitChecklist"))
            if "_hasExplicitChecklist" in cloud_prop
            else bool(cloud_prop.get("checklist"))
        )
        norm_cp = _strip_internal_cloud_flags(normalize_watch_property_record(cloud_prop))
        if is_removed_watchlist_tombstone(norm_cp):
            existing_base = watched_by_id.get(pid)
            tomb_ts = str(norm_cp.get("updatedAt") or "9999")
            base_ts = str((existing_base or {}).get("updatedAt") or "")
            if not existing_base or not base_ts or tomb_ts >= base_ts:
                watched_by_id.pop(pid, None)
            continue
        app_lookup_keys = [str(norm_cp.get("appNo") or "")] + get_canonical_plat_app_ids(norm_cp)
        latest_app_tier = ""
        latest_app_ts = ""
        for ak in app_lookup_keys:
            if ak and ak in latest_plat_tier_by_app:
                t_val, t_ts = latest_plat_tier_by_app[ak]
                if t_ts >= latest_app_ts:
                    latest_app_tier = t_val
                    latest_app_ts = t_ts
        cp_ts = str(norm_cp.get("updatedAt") or "")
        plat_downgraded_newer = bool(
            latest_app_tier == "3" and (not cp_ts or not latest_app_ts or latest_app_ts >= cp_ts)
        )
        if (norm_cp.get("tier") == "3" or plat_downgraded_newer) and pid.startswith("plat-"):
            if has_human_dossier_contributions(norm_cp):
                norm_cp["tier"] = "3"
                norm_cp["tierConfirmed"] = True
                if norm_cp.get("trackerStatus") not in ("Saved", "Lost", "For Sale"):
                    norm_cp["trackerStatus"] = "Archived (Tier 3)"
            else:
                watched_by_id.pop(pid, None)
                continue
        if pid in watched_by_id:
            base_p = watched_by_id[pid]
            # Preserve human edits from cloud while protecting seeded searchTerms/driveLinks from legacy rows
            merged_p = dict(base_p)
            for k_meta in (
                "propertyName",
                "platProjectName",
                "address",
                "secondaryAddresses",
                "hcadAccount",
                "legalDescription",
                "linkedAppNo",
                "yearBuilt",
                "yearRemodeled",
                "architect",
                "designation",
                "neighborhood",
                "trackerStatus",
                "threatCategory",
                "reportedBy",
                "phPastActions",
                "ownerOfRecord",
                "developer",
                "buyerRelatedEntities",
                "broker",
                "lender",
                "designFirm",
                "architectEngineer",
                "keyDates",
                "notes",
                "tier",
                "humanEdited",
                "updatedAt",
                "updatedBy",
                "archivedAt",
            ):
                if norm_cp.get(k_meta) not in (None, "", [], {}, False):
                    merged_p[k_meta] = norm_cp[k_meta]
            if merged_p.get("tier") == "3" and merged_p.get("trackerStatus") in (
                "Active / High Priority",
                "Monitoring",
                "Dormant / Medium Priority",
            ):
                merged_p["trackerStatus"] = "Archived (Tier 3)"
            if merged_p.get("trackerStatus") != "Archived (Tier 3)":
                merged_p["archivedAt"] = ""
            if has_explicit_terms:
                merged_p["searchTerms"] = norm_cp["searchTerms"]
                if norm_cp.get("searchTermsUpdatedAt"):
                    merged_p["searchTermsUpdatedAt"] = norm_cp["searchTermsUpdatedAt"]
            if has_explicit_links:
                merged_p["documents"] = norm_cp["documents"]
                merged_p["driveLinks"] = norm_cp["driveLinks"]
                if norm_cp.get("driveLinksUpdatedAt"):
                    merged_p["driveLinksUpdatedAt"] = norm_cp["driveLinksUpdatedAt"]
            if has_explicit_checklist and isinstance(norm_cp.get("checklist"), list):
                merged_rows = [dict(r) for r in (base_p.get("checklist") or [])]
                for c_row in norm_cp["checklist"]:
                    b_row = _find_checklist_row_by_id(merged_rows, c_row.get("id", ""))
                    if not b_row:
                        continue
                    c_status = c_row.get("status")
                    if c_status and c_status != "To Do":
                        b_row["status"] = c_status
                    if str(c_row.get("lastChecked") or "") > str(b_row.get("lastChecked") or ""):
                        b_row["lastChecked"] = c_row["lastChecked"]
                    if c_row.get("updatedAt"):
                        b_row["updatedAt"] = c_row["updatedAt"]
                    if c_row.get("humanStatusEdited") is True:
                        b_row["humanStatusEdited"] = True
                    if isinstance(c_row.get("statusChangedAt"), str) and c_row["statusChangedAt"].strip():
                        b_row["statusChangedAt"] = c_row["statusChangedAt"].strip()
                    seen_chk = {
                        (str(ch.get("date") or ""), str(ch.get("result") or "").strip())
                        for ch in (b_row.get("checks") or [])
                    }
                    for ch in c_row.get("checks") or []:
                        k_ch = (str(ch.get("date") or ""), str(ch.get("result") or "").strip())
                        if k_ch[1] and k_ch not in seen_chk:
                            seen_chk.add(k_ch)
                            b_row.setdefault("checks", []).append(dict(ch))
                merged_p["checklist"] = merged_rows
            merged_p["tierConfirmed"] = True
            watched_by_id[pid] = _strip_internal_cloud_flags(normalize_watch_property_record(merged_p))
        else:
            # Do not resurrect unconfirmed auto-dumped plat-* rows from legacy snapshots unless confirmed or contributed
            if pid.startswith("plat-") and cloud_prop.get("tierConfirmed") is not True and not has_human_dossier_contributions(norm_cp):
                continue
            if not norm_cp.get("checklist"):
                norm_cp["checklist"] = build_default_checklist_for_property(
                    norm_cp.get("address", ""),
                    norm_cp.get("hcadAccount", ""),
                    "",
                )
            norm_cp["tierConfirmed"] = True
            watched_by_id[pid] = _strip_internal_cloud_flags(norm_cp)

    # Execute 8-agency sweeps for every active watched property and roll up worst-case sweep health
    per_property_healths: list[dict] = []
    for pid, prop in watched_by_id.items():
        if prop.get("trackerStatus") == "Archived (Tier 3)":
            prop["lastAutoSweepAt"] = now_iso
            prop["autoSweepSchedule"] = AUTO_SWEEP_SCHEDULE
            continue
        if run_live_sweeps:
            sweep_res = sweep_watched_property_agencies(prop, plat_data, idx, sweep_date)
            if isinstance(sweep_res.get("agencyHealth"), dict):
                per_property_healths.append(sweep_res["agencyHealth"])
            merge_auto_sweep_findings_into_property(prop, sweep_res, now_iso)
        else:
            prop["lastAutoSweepAt"] = now_iso
            prop["autoSweepSchedule"] = AUTO_SWEEP_SCHEDULE

    last_sweep_health = aggregate_watchlist_sweep_health(
        per_property_healths, now_iso, run_live_sweeps=run_live_sweeps
    )

    tier_rank = {"1": 1, "2": 2, "3": 3}
    ordered_props = sorted(
        watched_by_id.values(),
        key=lambda p: (
            1 if p.get("trackerStatus") == "Archived (Tier 3)" else 0,
            tier_rank.get(str(p.get("tier") or "2"), 9),
            0 if p.get("id") == "prop-kirby-mansion" else 1,
            str(p.get("address") or p.get("propertyName") or ""),
        ),
    )
    primary = watched_by_id.get("prop-kirby-mansion") or (ordered_props[0] if ordered_props else {})

    return {
        "updatedAt": now_iso,
        "lastAutoSweepAt": now_iso,
        "lastSweepHealth": last_sweep_health,
        "autoSweepSchedule": AUTO_SWEEP_SCHEDULE,
        "automatedAgencyCount": len(AUTOMATED_SWEEP_AGENCIES),
        "primaryProperty": primary,
        "watchedProperties": ordered_props,
    }


def enrich_kirby_mansion_permit_alert() -> None:
    """Ensure active_permit_alerts.json links Kirby Mansion to TDLR /tabs/search and verified Harris County filings."""
    print("[5/6] Syncing Kirby Mansion multi-agency timeline in active_permit_alerts.json...")
    alerts_path = DATA_DIR / "active_permit_alerts.json"
    if not alerts_path.exists():
        return
    with open(alerts_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    changed = False
    for al in data.get("alerts", []):
        if al.get("hcad_num") == "0152300000001":
            al["watchlist_id"] = "prop-kirby-mansion"
            for mf in al.get("multi_agency_filings") or []:
                if "tdlr.texas.gov" in str(mf.get("url") or "").lower():
                    if mf["url"] != TDLR_TABS_PORTAL_URL:
                        mf["url"] = TDLR_TABS_PORTAL_URL
                        changed = True
            break

    if changed:
        with open(alerts_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)


def main() -> None:
    idx = load_atlas_indices()
    cloud_sync = pull_cloud_sheet_sync_rows()
    scraped_multi = fetch_multi_cycle_agendas_and_notices(max_cycles=3)
    plat_dataset = build_plat_agenda_dataset(
        idx,
        scraped_multi,
        cloud_plat_reviews=cloud_sync.get("platReviews"),
        cloud_watch_properties=cloud_sync.get("watchProperties"),
    )

    plat_out = DATA_DIR / "plat_agenda_monitor.json"
    sweep_plat_dataset = plat_dataset
    if (
        plat_dataset.get("scrapeFailed")
        and int(plat_dataset.get("summary", {}).get("totalAgendaItems") or 0) == 0
        and plat_out.exists()
    ):
        try:
            prior_plat = json.loads(plat_out.read_text(encoding="utf-8"))
            if isinstance(prior_plat, dict) and prior_plat.get("cycles"):
                sweep_plat_dataset = {
                    **prior_plat,
                    "scrapeFailed": True,
                    "error": str(plat_dataset.get("error") or "COH Plat Agenda upstream scrape failed"),
                }
                print(
                    f"[6/6] Preserved prior {plat_out.name} because upstream scrape returned 0 items "
                    f"({sweep_plat_dataset['error']})."
                )
        except Exception as exc:
            print(f"[warn] Could not read prior {plat_out.name}: {exc}")

    if sweep_plat_dataset is plat_dataset:
        with open(plat_out, "w", encoding="utf-8") as f:
            json.dump(plat_dataset, f, indent=2)
        print(
            f"[6/6] Wrote {plat_out} (active cycle {plat_dataset['cycleKey']}, "
            f"cycles={plat_dataset['cycleOrder']}, "
            f"{plat_dataset['summary']['totalAgendaItems']} active items, "
            f"{plat_dataset['summary']['toCheckCount']} to check)."
        )

    watchlist = build_property_watchlist(
        sweep_plat_dataset, idx, cloud_sync=cloud_sync, run_live_sweeps=True
    )
    wl_out = DATA_DIR / "property_watchlist.json"
    with open(wl_out, "w", encoding="utf-8") as f:
        json.dump(watchlist, f, indent=2)
    print(
        f"      Wrote {wl_out} ({len(watchlist['watchedProperties'])} confirmed watched properties, "
        f"lastAutoSweepAt={watchlist['lastAutoSweepAt']})."
    )

    enrich_kirby_mansion_permit_alert()


if __name__ == "__main__":
    main()
