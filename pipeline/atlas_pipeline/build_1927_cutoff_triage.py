#!/usr/bin/env python3
"""1927 (100-Year Demolition Review Cutoff) HCAD Undercount Triage, Safe Promotion & Sheet Sync.

Implements Milestone 7 across three strictly separated layers (DF-1..DF-7):
  1. Pure Domain Evaluators (`evaluate_pre1927_auto_promotion`, `build_promoted_override_record`,
     `parse_street_walk_key`, `classify_1927_triage_buckets`, `deduplicate_and_sort_triage_rows`)
     with zero file or network side effects for fast hermetic testing.
  2. Double-Vetted Pre-1927 City Directory Auto-Promotion (`--apply`) into
     `app/public/data/curated_overrides.json`, `buildings.geojson`, and `search_index.json`
     preserving multi-footprint `.pmtiles` shards (`SOP 4 Case D`: omitting `geometry` and
     `replace_parcel_shards`) and accurate non-homestead `own_tier` classifications (`DI-1`, `DI-2`).
  3. City of Houston (`jurs == "061"`) 6-Bucket Street-Sorted Census export
     (`pipeline/cache/triage_1927_cutoff_coh.csv`), 13th Google Sheet tab batch generator
     (`1927_Cutoff_Triage`, `gid = 927192701`, 22 columns `A:V`, 7 Filter Views), and 3-way
     `DEFAULT_POLICY_IMPACT_BRIEF` constant synchronizer (`--sync-policy-brief`).
"""

from __future__ import annotations

import argparse
from collections import Counter
import csv
import datetime
import io
import json
from pathlib import Path
import re
from typing import Any
import zipfile

from atlas_pipeline.schema import (
    ACTIVE_TAX_YEAR,
    ALLOWED_PREWAR_FOUNDATIONS,
    MIN_VALID_YEAR,
    OWNERSHIP_TIER_KEYS,
    build_citydir_search_url,
    classify_account_ownership,
    classify_main_structure_demolition_permit,
    collect_overridden_hcad_accounts,
    infer_fallback_ownership_tier,
    load_bld1_structural_flags,
    match_earliest_platted_subdivisions,
    normalize_hcad_date,
    resolve_citydir_cdm_url,
    sanitize_atlas_text,
    write_compact_json,
)
from atlas_pipeline.setup_admin_sheet import (
    ATLAS_BASE_URL,
    HUB_FILTER_INPUT_ROW,
    HUB_HOW_TO_USE_ROW,
    HUB_RESULTS_FORMULA_ROW,
    HUB_TRIAGE_1927_ROW,
    SHEET_TITLE_SURVEYS,
    SHEET_TITLE_TRIAGE_1927,
    SID_HUB,
    SID_INSTR,
    SID_LOST,
    SID_SURVEYS,
    SID_TRIAGE_1927,
    SOP15_TARGET_ROW,
    SOP16_TARGET_ROW,
    TRIAGE_1927_FILTER_VIEW_COUNT,
    assert_safe_workbook_write_ranges,
    build_hub_filter_count_formula,
    build_hub_results_formula,
    build_sop16_row,
    inspect_workbook_layout_anchors,
    rgb,
)
from atlas_pipeline.sync_field_surveys_sheet import sanitize_sheet_cell

REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = REPO_ROOT / "pipeline" / "cache"
DATA_DIR = REPO_ROOT / "app" / "public" / "data"

AUTO_PROMOTED_VERIFIED_BY = "Archival City Directory Cross-Check (Double-Vetted)"

# Reserved multi-category E2E fixture (2009 Harvard St) excluded from auto-promotion (RP-1)
# so Playwright E2E tests continue exercising independent per-category resolution semantics.
RESERVED_E2E_FIXTURE_HCADS: frozenset[str] = frozenset({"0200670000010"})

ROUNDED_PLACEHOLDER_YEARS: frozenset[int] = frozenset({1930, 1935, 1940, 1945, 1950})
BUCKET_B_PLACEHOLDER_YEARS: frozenset[int] = frozenset({1930, 1935, 1940, 1950})
EARLY_ANNEXATION_CLUSTER_YEARS: frozenset[int] = frozenset({1928, 1929, 1930, 1931, 1932})
BUCKET_C_EARLY_ANNEX_YEARS: frozenset[int] = frozenset({1927, 1928, 1929, 1930, 1931, 1932})
BUCKET_C_POSTWAR_ANNEX_YEARS: frozenset[int] = frozenset({1949, 1950, 1956})

PIER_AND_BEAM_FOUNDATIONS: frozenset[str] = frozenset(
    {
        "Crawl Space",
        "Piers 8 ft or Greater",
        "Piers 8 ft or Greater with Enclosure",
        "Full Basement",
        "Partial Basement",
        "Basement",
    }
)

# Houston enacted its major citywide house-renumbering ordinance in 1911; single-volume
# pre-1911 directory hits outside Downtown's original Borden grid and Old Sixth Ward are
# held back for human Sanborn verification unless corroborated by a post-1911 volume (DI-1).
PRE1911_STABLE_NUMBERING_DISTRICTS: tuple[str, ...] = (
    "OLD SIXTH WARD",
    "SIXTH WARD",
    "MAIN STREET MARKET SQUARE",
    "DOWNTOWN",
)

HISTORIC_STREETCAR_CORRIDORS: frozenset[str] = frozenset(
    {
        "MAIN",
        "WASHINGTON",
        "NAVIGATION",
        "DOWLING",
        "EMANCIPATION",
        "LYONS",
        "PRESTON",
        "CONGRESS",
        "MILAM",
        "TRAVIS",
        "FANNIN",
        "SAN JACINTO",
        "CAROLINE",
        "AUSTIN",
        "LA BRANCH",
        "PRAIRIE",
        "TEXAS",
        "CAPITOL",
        "RUSK",
        "MCKINNEY",
        "WALKER",
        "POLK",
        "DALLAS",
        "WESTHEIMER",
        "MONTROSE",
        "WAUGH",
        "YALE",
        "19TH",
        "TELEPHONE",
        "HARRISBURG",
        "LAWNDALE",
        "BROADWAY",
        "CANAL",
        "SAMPSON",
        "JENSEN",
        "HOLMAN",
        "ELGIN",
        "ALABAMA",
        "MCGOWEN",
    }
)

EARLY_ANNEXATION_NEIGHBORHOOD_KEYWORDS: tuple[str, ...] = (
    "MAGNOLIA PARK",
    "EAST END",
    "SECOND WARD",
    "LAWNDALE",
    "IDYLWOOD",
    "EASTWOOD",
    "INDEPENDENCE HEIGHTS",
)

BUCKET_LABELS: dict[str, str] = {
    "A": "1. Pre-1927 City Directory Hold-Back",
    "B": "2. Rounded Placeholder in Pre-1925 Plat / HD",
    "C": "3. Annexation-Ring Cluster (1927-32 / 1949-56)",
    "D": "4. Inner-Loop Pier-and-Beam Overwrite (1945-85)",
    "E": "5. Historic Corridor Masonry Commercial (1945-90)",
    "F": "6. 1920 False-Positive Audit (1921-29 Plat / Absent <=1926)",
}

# Canonical shared street-walk normalization token sets (synchronized 1:1 with app/js/staffWorkbench.js; DF-2).
STREET_WALK_UNIT_CUT_TOKENS: frozenset[str] = frozenset(
    {
        "APT",
        "UNIT",
        "STE",
        "SUITE",
        "BLDG",
        "BUILDING",
        "BLD",
        "FL",
        "FLOOR",
        "RM",
        "ROOM",
        "LOT",
        "TRLR",
        "REAR",
        "FRONT",
        "GARAGE",
    }
)

STREET_WALK_DIR_TOKENS: frozenset[str] = frozenset(
    {
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
        "NORTHEAST",
        "NORTHWEST",
        "SOUTHEAST",
        "SOUTHWEST",
    }
)

STREET_WALK_TYPE_SUFFIXES: frozenset[str] = frozenset(
    {
        "ST",
        "STREET",
        "AVE",
        "AV",
        "AVENUE",
        "BLVD",
        "BOULEVARD",
        "DR",
        "DRIVE",
        "RD",
        "ROAD",
        "LN",
        "LANE",
        "CT",
        "COURT",
        "PL",
        "PLACE",
        "CIR",
        "CIRCLE",
        "PKWY",
        "PKY",
        "PARKWAY",
        "FWY",
        "FREEWAY",
        "HWY",
        "HIGHWAY",
        "WAY",
        "TER",
        "TERRACE",
        "TRL",
        "TRAIL",
        "SQ",
        "SQUARE",
        "LOOP",
        "WALK",
        "ALY",
        "ALLEY",
        "ESPLANADE",
    }
)

STREET_WALK_UNNUMBERED_SENTINEL = 999_999_999

# Google Sheet 13th tab (`1927_Cutoff_Triage`, gid=927192701) constants
MAX_SHEET_TRIAGE_ROWS = 5000
MIN_TRIAGE_GRID_ROWS = 1000
TRIAGE_ROW_HEADROOM = 200

TRIAGE_HEADERS_A_TO_T: list[str] = [
    "review_status",
    "primary_bucket",
    "all_buckets",
    "priority_score",
    "hcad_num",
    "district_or_neighborhood",
    "street_stem",
    "block_side",
    "house_num",
    "address",
    "hcad_year_built",
    "eff_or_remodel_year",
    "suggested_max_year",
    "foundation_or_structure",
    "plat_name_and_year",
    "annexation_era",
    "coh_historic_status",
    "triage_evidence",
    "citydir_or_sanborn_url",
    "reviewer_notes",
]
TRIAGE_HEADERS_U_TO_V: list[str] = ["open_in_atlas", "inspect_hcad"]
ALL_TRIAGE_HEADERS: list[str] = TRIAGE_HEADERS_A_TO_T + TRIAGE_HEADERS_U_TO_V
TRIAGE_COL_IDX: dict[str, int] = {name: idx for idx, name in enumerate(ALL_TRIAGE_HEADERS)}

TRIAGE_REVIEW_STATUSES: list[str] = [
    "Unreviewed",
    "Confirmed Pre-1927 (Promote)",
    "Confirmed Post-1926 (Keep/Correct)",
    "Replacement Building (Reject)",
    "Needs Field / Sanborn Check",
    "Promoted to Approved_Edits",
]

_RE_HOUSE_PREFIX = re.compile(
    r"^(\d+)(?:[A-Z])?(?:\s*-\s*\d+[A-Z]?)?(?:\s+\d+/\d+)?(?:\s+|$)"
)
_RE_CITY_STATE_TAIL = re.compile(
    r"\s*,?\s*HOUSTON(?:\s+HARRIS)?(?:\s*,?\s*TX)?(?:\s+\d{5}(?:-\d{4})?)?\s*$"
)


UNDESIGNATED_DISTRICT_SENTINELS: frozenset[str] = frozenset({
    "OUTSIDE HISTORIC DISTRICT",
    "UNDESIGNATED / OUTSIDE HD",
    "UNDESIGNATED COH",
    "HISTORIC CORE",
})


def parse_street_walk_key(
    address: Any,
    district: Any = "",
    tie_breaker: Any = "",
) -> tuple[str, str, int, int, str]:
    """Build the canonical 5-tuple Street-Block Walk sort key shared 1:1 with JS (`DF-2`, `UT-5`).

    Returns `(norm_district, norm_street_stem, parity_rank, house_num_or_sentinel, tie_breaker)`:
      - `parity_rank == 0`: Odd blockface (`house_num > 0` and `house_num % 2 == 1`)
      - `parity_rank == 1`: Even blockface (`house_num > 0` and `house_num % 2 == 0`)
      - `parity_rank == 2`: Unnumbered / zero-address parcel (`house_num_or_sentinel = 999999999`)
    """
    raw_dist = re.sub(r"\s+", " ", str(district or "").strip().lstrip("'").strip()).upper()
    norm_dist = (
        "ZZZ_UNDESIGNATED"
        if not raw_dist or raw_dist in UNDESIGNATED_DISTRICT_SENTINELS
        else raw_dist
    )

    raw_addr = str(address or "").strip().lstrip("'").lstrip("+").upper().strip()
    primary = raw_addr.split(" / ", 1)[0].strip()
    primary = _RE_CITY_STATE_TAIL.sub("", primary).strip()

    house_num = 0
    rem = primary
    m_num = _RE_HOUSE_PREFIX.match(primary)
    if m_num:
        try:
            house_num = int(m_num.group(1))
        except ValueError:
            house_num = 0
        rem = primary[m_num.end() :].strip()

    rem = re.sub(r"\s*#.*$", "", rem)
    rem = re.sub(r"[.,'\"\\()/+-]", " ", rem)
    tokens = [tok for tok in rem.split() if tok]

    # Cut trailing unit designator (APT, STE, REAR, etc.) once at least one street token precedes it.
    cut_idx = len(tokens)
    for idx in range(1, len(tokens)):
        if tokens[idx] in STREET_WALK_UNIT_CUT_TOKENS:
            cut_idx = idx
            break
    tokens = tokens[:cut_idx]

    while len(tokens) > 1 and (
        tokens[-1] in STREET_WALK_TYPE_SUFFIXES or tokens[-1] in STREET_WALK_DIR_TOKENS
    ):
        tokens.pop()
    while len(tokens) > 1 and tokens[0] in STREET_WALK_DIR_TOKENS:
        tokens.pop(0)

    street_stem = " ".join(tokens) if tokens else "ZZZ_UNNAMED"

    if house_num > 0:
        parity_rank = 0 if (house_num % 2 == 1) else 1
        sort_house = house_num
    else:
        parity_rank = 2
        sort_house = STREET_WALK_UNNUMBERED_SENTINEL

    return (norm_dist, street_stem, parity_rank, sort_house, str(tie_breaker or "").strip())


def evaluate_pre1927_auto_promotion(candidate: dict[str, Any]) -> dict[str, Any]:
    """Pure multi-gate validator for double-vetted pre-1927 City Directory auto-promotions (`DI-1`, `DF-5`).

    Returns a dictionary containing `eligible` (`bool`), `promoted_year` (`int | None`),
    `reasons` (`list[str]` hold-back codes when ineligible, or qualification tag when eligible),
    `cdm_pointer`, `source_url`, and clean ASCII `source_citation`.
    """
    reasons: list[str] = []
    hcad_num = str(candidate.get("hcad_num") or "").strip()
    if not re.match(r"^\d{13}$", hcad_num) or hcad_num == "0000000000000":
        reasons.append("invalid_hcad13")
    if hcad_num in RESERVED_E2E_FIXTURE_HCADS:
        reasons.append("reserved_e2e_fixture")
    if candidate.get("already_overridden"):
        reasons.append("already_overridden")

    earliest_dir = int(candidate.get("earliest_dir_year") or 0)
    atlas_year = int(candidate.get("atlas_year") or 0)
    if not (1836 <= earliest_dir <= 1926):
        reasons.append("dir_year_not_pre1927")
    elif atlas_year > 0 and earliest_dir > atlas_year - 5:
        reasons.append("dir_gap_under_5_years")

    if candidate.get("is_demolished_landmark"):
        reasons.append("demolished_landmark")
    if candidate.get("in_lost_houston"):
        reasons.append("in_lost_houston")
    if candidate.get("has_post1927_demo_permit"):
        reasons.append("post1927_demo_permit")

    state_class = str(candidate.get("state_class") or "").strip().upper()
    if state_class[:2] in {"C1", "C2", "C3"}:
        reasons.append("cama_vacant_state_class")
    cama_bld_ar = candidate.get("cama_bld_ar")
    if cama_bld_ar is not None and float(cama_bld_ar) <= 0:
        reasons.append("cama_zero_building_area")

    contributing = str(candidate.get("contributing") or "").strip()
    if contributing == "Non-Contributing":
        reasons.append("non_contributing_replacement_risk")

    standing_years = [int(y) for y in (candidate.get("standing_building_years") or []) if int(y or 0) > 0]
    cama_max_bld_year = int(candidate.get("cama_max_bld_year") or 0)
    if not standing_years:
        reasons.append("no_standing_building_years")
    elif any(y < 1836 or y > 1960 for y in standing_years) or cama_max_bld_year > 1960:
        reasons.append("heterogeneous_post1960_sibling")

    has_bld1_slab = bool(candidate.get("has_bld1_slab"))
    has_bld1_new_rebuilt = bool(candidate.get("has_bld1_new_rebuilt"))
    if has_bld1_slab:
        reasons.append("bld1_concrete_slab")
    if has_bld1_new_rebuilt:
        reasons.append("bld1_new_or_rebuilt_code")

    if not (1928 <= atlas_year <= 1960):
        reasons.append("atlas_year_outside_1928_1960")

    in_coh_hd = bool(candidate.get("in_coh_historic_district"))
    bld1_fnds = {str(x).strip() for x in (candidate.get("bld1_foundations") or []) if str(x).strip()}
    has_prewar_fnd = bool(bld1_fnds & ALLOWED_PREWAR_FOUNDATIONS)
    is_double_vetted_hd = in_coh_hd and contributing == "Contributing"
    is_double_vetted_typology = (
        atlas_year in ROUNDED_PLACEHOLDER_YEARS or atlas_year in EARLY_ANNEXATION_CLUSTER_YEARS
    ) and has_prewar_fnd

    if not (is_double_vetted_hd or is_double_vetted_typology):
        reasons.append("lacks_hd_contributing_or_prewar_foundation_corroboration")

    dir_rec = candidate.get("dir_rec") if isinstance(candidate.get("dir_rec"), dict) else {
        "e": earliest_dir,
        "p": candidate.get("cdm_pointer"),
        "c": candidate.get("ocr_citation", ""),
        "y": candidate.get("dir_years", []),
    }
    ptr_int, cdm_url = resolve_citydir_cdm_url(dir_rec, "")
    if ptr_int <= 0:
        reasons.append("missing_valid_cdm_pointer")

    address = str(candidate.get("address") or "").strip()
    _, _, parity_rank, house_num_or_sentinel, _ = parse_street_walk_key(address)
    house_num = 0 if parity_rank == 2 else house_num_or_sentinel
    ocr_citation = sanitize_atlas_text(
        str(dir_rec.get("c") or candidate.get("ocr_citation") or "").replace("\u2014", " - ").replace("\u2013", "-")
    )
    if house_num <= 0:
        reasons.append("unnumbered_situs_address")
    elif not re.search(rf"(?<!\d){house_num}(?!\d)", ocr_citation):
        reasons.append("ocr_missing_exact_house_number")

    dir_years = sorted(
        {
            int(y)
            for y in (dir_rec.get("y") or candidate.get("dir_years") or [])
            if isinstance(y, (int, float)) and int(y) >= 1836
        }
    )
    ctx_upper = f"{candidate.get('historic_district') or ''} {candidate.get('neighborhood') or ''}".upper()
    in_pre1911_stable_grid = any(tok in ctx_upper for tok in PRE1911_STABLE_NUMBERING_DISTRICTS)
    has_post1911_corroboration = len(dir_years) >= 2 and any(y >= 1911 for y in dir_years)
    if earliest_dir < 1911 and not has_post1911_corroboration and not in_pre1911_stable_grid:
        reasons.append("uncorroborated_pre1911_renumbering_risk")

    eligible = len(reasons) == 0
    vet_basis = (
        "COH Historic District Contributing inventory"
        if is_double_vetted_hd
        else f"pre-war {sorted(bld1_fnds & ALLOWED_PREWAR_FOUNDATIONS)[0]} typology"
        if has_prewar_fnd
        else "archival survey"
    )
    snip = ocr_citation
    if len(snip) > 160:
        snip = snip[:157].rstrip() + "..."
    clean_citation = sanitize_atlas_text(
        f"Appears in {earliest_dir} Morrison & Fourmy Houston City Directory "
        f"(ContentDM #{ptr_int}; corroborated by {vet_basis}; HCAD listed {atlas_year})"
        + (f": {snip}" if snip else "")
    ).replace("\u2014", " - ").replace("\u2013", "-")

    return {
        "eligible": eligible,
        "promoted_year": earliest_dir if eligible else None,
        "qualification_basis": vet_basis if eligible else "",
        "reasons": reasons,
        "cdm_pointer": ptr_int,
        "source_url": cdm_url,
        "source_citation": clean_citation,
    }


def build_promoted_override_record(
    *,
    hcad_num: str,
    promoted_year: int,
    original_hcad_year: int,
    source_citation: str,
    source_url: str,
    standing_props: dict[str, Any] | None = None,
    cama_acct: dict[str, Any] | None = None,
    updated_at: str = "2026-11-06",
) -> dict[str, Any]:
    """Construct a `SOP 4 Case D` attribute-only override preserving multi-footprint shards & `own_tier` (`DI-2`)."""
    sp = standing_props or {}
    ca = cama_acct or {}

    # Resolve accurate ownership tier without ever blindly defaulting commercial/civic/MFH parcels to homestead.
    standing_tier = str(sp.get("own_tier") or "").strip().lower()
    if ca and (ca.get("state_class") or ca.get("owner_name")):
        acct_res = classify_account_ownership(
            state_class=str(ca.get("state_class") or sp.get("state_class") or ""),
            owner_name=str(ca.get("owner_name") or sp.get("owner") or ""),
            situs_addr=str(ca.get("situs_addr") or sp.get("address") or ""),
            mail_addr_1=str(ca.get("mail_addr_1") or ""),
            mail_addr_2=str(ca.get("mail_addr_2") or ""),
            mail_city=str(ca.get("mail_city") or ""),
            mail_state=str(ca.get("mail_state") or ""),
            mail_zip=str(ca.get("mail_zip") or ""),
            subdivision=str(sp.get("subdivision") or ""),
            port_id=str(sp.get("port_id") or ""),
        )
        cama_tier = str(acct_res.get("own_tier") or "local_small")
        if (
            cama_tier in ("homestead", "local_small")
            and standing_tier
            in (
                "local_small",
                "regional_portfolio",
                "institutional_pe",
                "anonymity_shield",
                "civic_nonprofit",
            )
        ):
            own_tier = standing_tier
        else:
            own_tier = cama_tier
    elif standing_tier in OWNERSHIP_TIER_KEYS:
        own_tier = standing_tier
    else:
        own_tier = infer_fallback_ownership_tier(
            use_category=str(sp.get("use_category") or ""),
            owner_name=str(sp.get("owner") or ""),
            landmark_name=str(sp.get("building_name") or sp.get("landmark_name") or ""),
            address=str(sp.get("address") or ca.get("situs_addr") or ""),
            current_tier=standing_tier,
        )

    hd = sanitize_atlas_text(str(sp.get("historic_district") or "")).strip()
    in_hd = bool(hd and hd != "Outside Historic District")
    raw_cnt = sanitize_atlas_text(str(sp.get("contributing") or "")).strip()
    if in_hd and raw_cnt in ("Contributing", "Potentially Contributing"):
        contributing = raw_cnt
    else:
        contributing = "Outside Historic District"

    clean_cit = sanitize_atlas_text(source_citation).replace("\u2014", " - ").replace("\u2013", "-")
    rec: dict[str, Any] = {
        "hcad_num": hcad_num,
        "address": sanitize_atlas_text(str(sp.get("address") or ca.get("situs_addr") or "")),
        "historic_district": hd if in_hd else "Outside Historic District",
        "contributing": contributing,
        "year_built": int(promoted_year),
        "decade": (int(promoted_year) // 10) * 10,
        "original_hcad_year": int(original_hcad_year),
        "source_type": "Houston City Directory",
        "source_citation": clean_cit,
        "source_url": str(source_url),
        "verified_by": AUTO_PROMOTED_VERIFIED_BY,
        "updated_at": str(updated_at),
        "own_tier": own_tier,
    }
    for opt_key in (
        "bld_style",
        "architect",
        "building_name",
        "landmark_name",
        "landmark_type",
        "use_category",
        "preservation_status",
        "landuse_desc",
        "subdivision",
        "neighborhood",
        "super_neighborhood",
        "port_id",
    ):
        val = sp.get(opt_key)
        if isinstance(val, str) and val.strip():
            rec[opt_key] = sanitize_atlas_text(val.strip()).replace("\u2014", " - ").replace("\u2013", "-")

    # Strictly enforce SOP 4 Case D: zero inline geometry or shard-replacement flags on bare parcel promotions!
    assert "geometry" not in rec
    assert "replace_parcel_shards" not in rec
    return rec


def classify_1927_triage_buckets(parcel: dict[str, Any]) -> dict[str, Any] | None:
    """Classify a single City of Houston (`jurs` token `'061'`) parcel across Buckets A-F (`UT-3`).

    Returns `None` if the parcel is outside COH (`061`), already curated-overridden, demolished/vacant,
    or matches zero 1927-cutoff suspect / `1920` false-positive buckets.
    """
    jurs_raw = parcel.get("jurs")
    jurs_tokens = (
        set(jurs_raw.split())
        if isinstance(jurs_raw, str)
        else {str(x).strip() for x in (jurs_raw or [])}
    )
    if "061" not in jurs_tokens:
        return None

    hcad_num = str(parcel.get("hcad_num") or "").strip()
    if not re.match(r"^\d{13}$", hcad_num) or hcad_num == "0000000000000":
        return None
    if parcel.get("already_overridden") or parcel.get("in_lost_houston") or parcel.get("is_demolished_landmark"):
        return None

    state_class = str(parcel.get("state_class") or "").strip().upper()
    if state_class[:2] in {"C1", "C2", "C3"}:
        return None
    bld_ar = parcel.get("cama_bld_ar")
    if bld_ar is not None and float(bld_ar) <= 0:
        return None

    atlas_year = int(parcel.get("atlas_year") or 0)
    if atlas_year < MIN_VALID_YEAR and not parcel.get("earliest_dir_year"):
        return None

    contributing = str(parcel.get("contributing") or "").strip()
    is_non_contrib = contributing == "Non-Contributing"
    in_coh_hd = bool(parcel.get("in_coh_historic_district"))
    hd_name = str(parcel.get("historic_district") or "").strip()
    neighborhood = str(parcel.get("neighborhood") or "").strip()
    super_nbhd = str(parcel.get("super_neighborhood") or "").strip()
    district_display = (
        hd_name
        if (in_coh_hd and hd_name and hd_name != "Outside Historic District")
        else (neighborhood or super_nbhd or "Undesignated COH")
    )

    address = str(parcel.get("address") or "").strip()
    norm_dist, street_stem, parity_rank, sort_house, _ = parse_street_walk_key(
        address, district_display, hcad_num
    )
    house_num = 0 if parity_rank == 2 else sort_house
    block_side = "Odd" if parity_rank == 0 else ("Even" if parity_rank == 1 else "Unnumbered")

    bld1_fnds = {str(x).strip() for x in (parcel.get("bld1_foundations") or []) if str(x).strip()}
    structure_dscr = str(parcel.get("structure_dscr") or "").strip()
    has_slab = bool(parcel.get("has_bld1_slab"))
    has_new_rebuilt = bool(parcel.get("has_bld1_new_rebuilt"))
    has_pier_beam = bool(bld1_fnds & PIER_AND_BEAM_FOUNDATIONS)
    has_prewar_fnd = bool(bld1_fnds & ALLOWED_PREWAR_FOUNDATIONS)

    plat_name = str(parcel.get("plat_name") or "").strip()
    plat_year = int(parcel.get("plat_year") or 0)
    plat_exact = bool(parcel.get("plat_year_is_exact"))
    plat_basis = str(parcel.get("plat_year_basis") or "").strip()
    plat_basis_upper = plat_basis.upper()
    plat_is_vetted_record = plat_exact or (
        ("CLERK" in plat_basis_upper or "DEED" in plat_basis_upper or "MAP RECORD" in plat_basis_upper or "FILING" in plat_basis_upper)
        and "EARLIEST STANDING" not in plat_basis_upper
        and "EARLIEST RECORDED" not in plat_basis_upper
        and "INFERRED" not in plat_basis_upper
        and "BOUNDED BY" not in plat_basis_upper
    )

    annex_decade = int(parcel.get("annex_decade") or 0)
    annex_label = str(parcel.get("annex_label") or (f"{annex_decade}s Ring" if annex_decade else "")).strip()
    in_inner_loop = bool(parcel.get("in_inner_loop")) or (1836 <= annex_decade <= 1930)

    earliest_dir = int(parcel.get("earliest_dir_year") or 0)
    in_absent_1926 = bool(parcel.get("in_absent_through_1926"))
    holdback_reasons = [str(r) for r in (parcel.get("holdback_reasons") or []) if str(r)]
    has_pre1927_secondary = bool(parcel.get("has_pre1927_secondary_bldg"))
    is_comm_or_mf = bool(parcel.get("is_commercial_or_multifamily")) or state_class[:1] in {"B", "F", "X"}

    buckets: list[str] = []
    evidence_notes: list[str] = []
    suggested_max_year: int | str = ""
    score = 0

    # Bucket A: Pre-1927 City Directory Hold-Back
    if 1836 <= earliest_dir <= 1926 and (atlas_year == 0 or atlas_year >= 1927):
        buckets.append(BUCKET_LABELS["A"])
        suggested_max_year = earliest_dir
        reason_txt = ", ".join(holdback_reasons[:3]) if holdback_reasons else "requires visual confirmation"
        evidence_notes.append(f"1866-1926 City Dir hit ({earliest_dir}); held back ({reason_txt})")
        score = max(score, 94 if not is_non_contrib else 78)

    # Bucket B: Rounded Placeholder (1930, 1935, 1940, 1950) inside COH HD or pre-1925 Plat
    if (
        atlas_year in BUCKET_B_PLACEHOLDER_YEARS
        and not is_non_contrib
        and not has_slab
        and not has_new_rebuilt
        and (in_coh_hd or (1836 <= plat_year <= 1925 and (has_prewar_fnd or not bld1_fnds)))
    ):
        buckets.append(BUCKET_LABELS["B"])
        if not suggested_max_year:
            suggested_max_year = 1926
        ctx_b = f"COH HD ({hd_name})" if in_coh_hd else f"{plat_name or 'Subdivision'} (platted {plat_year})"
        evidence_notes.append(f"Rounded {atlas_year} placeholder inside {ctx_b}")
        is_verified_hd_contrib = in_coh_hd and contributing == "Contributing"
        score = max(
            score,
            88 if (is_verified_hd_contrib and atlas_year == 1930) else (84 if atlas_year == 1930 else 76),
        )

    # Bucket C: Annexation-Ring Cluster (1927-32 in 1926-29 rings; 1949/1950/1956 pier-and-beam in 1949-56 rings)
    nbhd_upper = f"{neighborhood} {super_nbhd} {plat_name}".upper()
    in_early_annex_zone = annex_decade in {1920, 1930} or any(
        kw in nbhd_upper for kw in EARLY_ANNEXATION_NEIGHBORHOOD_KEYWORDS
    )
    if (
        not is_non_contrib
        and not has_slab
        and not has_new_rebuilt
        and (
            (atlas_year in BUCKET_C_EARLY_ANNEX_YEARS and in_early_annex_zone and (has_prewar_fnd or not bld1_fnds))
            or (atlas_year in BUCKET_C_POSTWAR_ANNEX_YEARS and annex_decade in {1940, 1950} and has_pier_beam)
        )
    ):
        buckets.append(BUCKET_LABELS["C"])
        if not suggested_max_year:
            suggested_max_year = 1926
        evidence_notes.append(
            f"Annexation-cluster build year {atlas_year} in {annex_label or 'annexation ring'}"
        )
        score = max(score, 83 if atlas_year <= 1932 else 74)

    # Bucket D: Inner-Loop Pier-and-Beam Overwrite (1945-1985) or Multi-Building Pre-1927 Secondary Swap
    if (
        not is_non_contrib
        and not has_slab
        and not has_new_rebuilt
        and (
            (in_inner_loop and 1945 <= atlas_year <= 1985 and has_pier_beam and (in_coh_hd or 1836 <= plat_year <= 1926))
            or (has_pre1927_secondary and atlas_year >= 1928)
        )
    ):
        buckets.append(BUCKET_LABELS["D"])
        if not suggested_max_year:
            suggested_max_year = 1926
        if has_pre1927_secondary and atlas_year >= 1928:
            evidence_notes.append(
                f"Multi-building swap: secondary building <= 1926 while Bldg 1 coded {atlas_year}"
            )
            score = max(score, 85)
        else:
            evidence_notes.append(
                f"Inner-Loop {atlas_year} remodel/move-on overwrite with pier-and-beam foundation"
            )
            score = max(score, 79 if in_coh_hd else 72)

    # Bucket E: Historic Streetcar Corridor Unreinforced / Bearing Masonry Commercial (1945-1990)
    is_masonry_bearing = (
        "MASONRY BEARING" in structure_dscr.upper()
        or "Masonry Bearing" in bld1_fnds
    ) and "FIREPROOFED STEEL" not in structure_dscr.upper()
    if (
        is_comm_or_mf
        and is_masonry_bearing
        and not has_slab
        and not has_new_rebuilt
        and 1945 <= atlas_year <= 1990
        and street_stem in HISTORIC_STREETCAR_CORRIDORS
        and (in_inner_loop or annex_decade <= 1940)
    ):
        buckets.append(BUCKET_LABELS["E"])
        if not suggested_max_year:
            suggested_max_year = 1926
        evidence_notes.append(
            f"Historic streetcar corridor ({street_stem}) Masonry Bearing commercial coded {atlas_year}"
        )
        score = max(score, 81)

    # Bucket F: 1920 False-Positive Audit (1921-1929 exact plat OR absent through 1926 directory)
    if atlas_year == 1920 and not (1836 <= earliest_dir <= 1920):
        in_post1920_exact_plat = (1921 <= plat_year <= 1929) and plat_is_vetted_record
        if in_absent_1926 or in_post1920_exact_plat:
            buckets.append(BUCKET_LABELS["F"])
            suggested_max_year = max(1927, plat_year) if in_absent_1926 else plat_year
            if in_absent_1926 and in_post1920_exact_plat:
                evidence_notes.append(
                    f"Coded 1920 but absent through 1926 City Dir AND inside {plat_name} (platted {plat_year})"
                )
                score = max(score, 86)
            elif in_absent_1926:
                evidence_notes.append("Coded 1920 placeholder but absent through 1926 City Directory")
                score = max(score, 80)
            else:
                evidence_notes.append(
                    f"Coded 1920 placeholder inside post-1920 subdivision {plat_name} (platted {plat_year})"
                )
                score = max(score, 77)

    if not buckets:
        return None

    fnd_summary_parts = sorted(bld1_fnds)
    if structure_dscr and structure_dscr not in fnd_summary_parts:
        fnd_summary_parts.append(structure_dscr)
    fnd_summary = "; ".join(fnd_summary_parts) if fnd_summary_parts else "Unspecified"

    plat_summary = (
        f"{plat_name} ({plat_year}{', exact' if plat_is_vetted_record else ', est.'})"
        if (plat_name and plat_year > 0)
        else (plat_name or "")
    )
    eff_remodel_parts: list[str] = []
    eff_yr = int(parcel.get("eff_year") or 0)
    rem_yr = int(parcel.get("yr_remodel") or 0)
    if eff_yr > 0 and eff_yr != atlas_year:
        eff_remodel_parts.append(f"eff {eff_yr}")
    if rem_yr > 0:
        eff_remodel_parts.append(f"rem {rem_yr}")
    eff_remodel_str = " / ".join(eff_remodel_parts)

    coh_status = (
        f"{hd_name} ({contributing or 'In District'})"
        if in_coh_hd
        else (str(parcel.get("landmark_status") or "").strip() or "Undesignated")
    )

    archival_url = str(parcel.get("archival_url") or "").strip()
    if not archival_url:
        archival_url = build_citydir_search_url(address, hcad_num)

    sort_key = (norm_dist, street_stem, parity_rank, sort_house, hcad_num)
    return {
        "hcad_num": hcad_num,
        "review_status": "Unreviewed",
        "primary_bucket": buckets[0],
        "all_buckets": " | ".join(buckets),
        "bucket_count": len(buckets),
        "priority_score": int(score),
        "district_or_neighborhood": sanitize_atlas_text(district_display),
        "street_stem": sanitize_atlas_text(street_stem if street_stem != "ZZZ_UNNAMED" else ""),
        "block_side": block_side,
        "house_num": house_num if house_num > 0 else "",
        "address": sanitize_atlas_text(address),
        "hcad_year_built": atlas_year if atlas_year > 0 else "",
        "eff_or_remodel_year": sanitize_atlas_text(eff_remodel_str),
        "suggested_max_year": suggested_max_year,
        "foundation_or_structure": sanitize_atlas_text(fnd_summary),
        "plat_name_and_year": sanitize_atlas_text(plat_summary),
        "annexation_era": sanitize_atlas_text(annex_label),
        "coh_historic_status": sanitize_atlas_text(coh_status),
        "triage_evidence": sanitize_atlas_text("; ".join(evidence_notes)),
        "citydir_or_sanborn_url": sanitize_atlas_text(archival_url),
        "reviewer_notes": "",
        "in_inner_loop": in_inner_loop,
        "sort_key": sort_key,
    }


def deduplicate_and_sort_triage_rows(
    raw_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Deduplicate triage candidates by 13-digit `hcad_num` and sort in canonical street-walk order (`UT-3`)."""
    by_hcad: dict[str, dict[str, Any]] = {}
    bucket_order = {lbl: idx for idx, lbl in enumerate(BUCKET_LABELS.values())}

    for rec in raw_records:
        hcad = str(rec.get("hcad_num") or "").strip()
        if not re.match(r"^\d{13}$", hcad):
            continue
        if hcad not in by_hcad:
            by_hcad[hcad] = dict(rec)
            continue
        existing = by_hcad[hcad]
        merged_buckets_set = set(str(existing["all_buckets"]).split(" | ")) | set(
            str(rec["all_buckets"]).split(" | ")
        )
        merged_buckets = sorted(
            (b for b in merged_buckets_set if b),
            key=lambda b: bucket_order.get(b, 99),
        )
        existing["primary_bucket"] = merged_buckets[0]
        existing["all_buckets"] = " | ".join(merged_buckets)
        existing["bucket_count"] = len(merged_buckets)
        existing["priority_score"] = max(int(existing["priority_score"]), int(rec["priority_score"]))
        if rec.get("triage_evidence") and rec["triage_evidence"] not in existing["triage_evidence"]:
            existing["triage_evidence"] = sanitize_atlas_text(
                f"{existing['triage_evidence']}; {rec['triage_evidence']}"
            )

    deduped = list(by_hcad.values())
    for item in deduped:
        if "sort_key" not in item:
            item["sort_key"] = parse_street_walk_key(
                item.get("address", ""),
                item.get("district_or_neighborhood", ""),
                item.get("hcad_num", ""),
            )
    deduped.sort(key=lambda r: tuple(r["sort_key"]))
    return deduped


def triage_record_to_sheet_row(rec: dict[str, Any]) -> list[Any]:
    """Serialize a deduplicated triage dict to its 20-column `A:T` RAW Google Sheet row (`hcad_num` preserved)."""
    hcad = str(rec["hcad_num"]).strip()
    assert re.match(r"^\d{13}$", hcad), f"Expected 13-digit HCAD string, got {hcad!r}"
    return [
        sanitize_sheet_cell(rec.get("review_status") or "Unreviewed"),
        sanitize_sheet_cell(rec.get("primary_bucket") or ""),
        sanitize_sheet_cell(rec.get("all_buckets") or ""),
        int(rec.get("priority_score") or 0),
        hcad,
        sanitize_sheet_cell(rec.get("district_or_neighborhood") or ""),
        sanitize_sheet_cell(rec.get("street_stem") or ""),
        sanitize_sheet_cell(rec.get("block_side") or ""),
        rec.get("house_num") if rec.get("house_num") != "" else "",
        sanitize_sheet_cell(rec.get("address") or ""),
        rec.get("hcad_year_built") if rec.get("hcad_year_built") != "" else "",
        sanitize_sheet_cell(rec.get("eff_or_remodel_year") or ""),
        rec.get("suggested_max_year") if rec.get("suggested_max_year") != "" else "",
        sanitize_sheet_cell(rec.get("foundation_or_structure") or ""),
        sanitize_sheet_cell(rec.get("plat_name_and_year") or ""),
        sanitize_sheet_cell(rec.get("annexation_era") or ""),
        sanitize_sheet_cell(rec.get("coh_historic_status") or ""),
        sanitize_sheet_cell(rec.get("triage_evidence") or ""),
        sanitize_sheet_cell(rec.get("citydir_or_sanborn_url") or ""),
        sanitize_sheet_cell(rec.get("reviewer_notes") or ""),
    ]


def load_existing_triage_csv_statuses(csv_text: str) -> dict[str, dict[str, str]]:
    """Parse an exported `1927_Cutoff_Triage` CSV to preserve moderator `review_status` and `reviewer_notes`."""
    if not csv_text or not csv_text.strip():
        return {}
    clean_csv = csv_text.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(clean_csv))
    out: dict[str, dict[str, str]] = {}
    for raw_row in reader:
        if not isinstance(raw_row, dict):
            continue
        raw_hcad = str(raw_row.get("hcad_num") or "").strip().lstrip("'")
        digits = "".join(ch for ch in raw_hcad if ch.isdigit())
        if not (digits and raw_hcad.replace("-", "").replace(" ", "").isdigit() and 10 <= len(digits) <= 13):
            continue
        hcad = digits.zfill(13)
        status = sanitize_sheet_cell(raw_row.get("review_status") or "").strip()
        notes = sanitize_sheet_cell(raw_row.get("reviewer_notes") or "").strip()
        if (status and status != "Unreviewed") or notes:
            out[hcad] = {
                "review_status": status or "Unreviewed",
                "reviewer_notes": notes,
            }
    return out


def merge_existing_triage_statuses(
    records: list[dict[str, Any]],
    existing_by_hcad: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    """Overlay prior moderator `review_status` (Col A) and `reviewer_notes` (Col T) onto refreshed census rows."""
    if not existing_by_hcad:
        return records
    merged: list[dict[str, Any]] = []
    for rec in records:
        hcad = str(rec.get("hcad_num") or "").strip()
        prior = existing_by_hcad.get(hcad)
        if not prior:
            merged.append(rec)
            continue
        updated = dict(rec)
        if prior.get("review_status") and prior["review_status"] != "Unreviewed":
            updated["review_status"] = prior["review_status"]
        if prior.get("reviewer_notes"):
            updated["reviewer_notes"] = prior["reviewer_notes"]
        merged.append(updated)
    return merged


def select_sheet_triage_subset(
    sorted_records: list[dict[str, Any]],
    *,
    max_rows: int = MAX_SHEET_TRIAGE_ROWS,
) -> list[dict[str, Any]]:
    """Select up to `max_rows` highest-priority suspects for the Google Sheet tab while preserving street-walk order.

    Guarantees 100% inclusion of Bucket A (City Directory Hold-Backs), Bucket F (`1920` False-Positive
    Audits), Bucket E (Historic Corridor Masonry Commercial across `all_buckets`), and any row carrying
    non-default moderator `review_status` or `reviewer_notes`, fills remaining capacity by descending
    `(priority_score, in_inner_loop, bucket_count)`, and re-sorts the selected slice by `sort_key` so
    every blockface on the sheet remains contiguous!
    """
    if len(sorted_records) <= max_rows:
        return list(sorted_records)

    must_keep: list[dict[str, Any]] = []
    pool: list[dict[str, Any]] = []
    must_labels = (BUCKET_LABELS["A"], BUCKET_LABELS["E"], BUCKET_LABELS["F"])
    for rec in sorted_records:
        rec_buckets = {
            b.strip()
            for b in str(rec.get("all_buckets") or rec.get("primary_bucket") or "").split("|")
            if b.strip()
        }
        has_moderator_state = (
            str(rec.get("review_status") or "Unreviewed").strip() not in ("", "Unreviewed")
            or bool(str(rec.get("reviewer_notes") or "").strip())
        )
        if has_moderator_state or any(lbl in rec_buckets for lbl in must_labels):
            must_keep.append(rec)
        else:
            pool.append(rec)

    if len(must_keep) >= max_rows:
        ranked = sorted(
            must_keep,
            key=lambda r: (-int(r["priority_score"]), 0 if r.get("in_inner_loop") else 1, r["hcad_num"]),
        )[:max_rows]
    else:
        rem = max_rows - len(must_keep)
        pool_ranked = sorted(
            pool,
            key=lambda r: (
                -int(r["priority_score"]),
                0 if r.get("in_inner_loop") else 1,
                -int(r.get("bucket_count") or 1),
                r["hcad_num"],
            ),
        )[:rem]
        ranked = must_keep + pool_ranked

    ranked.sort(key=lambda r: tuple(r["sort_key"]))
    return ranked


def verify_1927_triage_sheet_anchors(
    *,
    tab_id_to_title: dict[int, str],
    hub_col_a: list[str],
    instr_col_a: list[str],
    instr_grid_rows: int = 60,
) -> dict[str, bool]:
    """Validate live 12-tab or 13-tab workbook anchors before building Milestone 7 Sheet batches (`DF-1`, `IT-3`)."""
    if tab_id_to_title.get(SID_LOST) != "Lost_Houston_Catalog":
        raise ValueError(
            f"Expected Lost_Houston_Catalog at gid={SID_LOST}, got {tab_id_to_title.get(SID_LOST)!r}"
        )
    if tab_id_to_title.get(SID_SURVEYS) != SHEET_TITLE_SURVEYS:
        raise ValueError(
            f"Expected {SHEET_TITLE_SURVEYS} at gid={SID_SURVEYS}, got {tab_id_to_title.get(SID_SURVEYS)!r}"
        )
    existing_triage = tab_id_to_title.get(SID_TRIAGE_1927)
    if existing_triage is not None and existing_triage != SHEET_TITLE_TRIAGE_1927:
        raise ValueError(
            f"Sheet ID {SID_TRIAGE_1927} collided with unexpected tab {existing_triage!r}"
        )
    need_add_sheet = existing_triage is None

    layout = inspect_workbook_layout_anchors(
        hub_col_a=hub_col_a,
        instr_col_a=instr_col_a,
        require_surveys_row=True,
    )
    need_hub_insert = not layout["has_triage_1927_row"]

    row26_a = instr_col_a[SOP15_TARGET_ROW - 1].strip() if len(instr_col_a) >= SOP15_TARGET_ROW else ""
    if "SOP 15" not in row26_a:
        raise ValueError(
            f"Instructions_&_Apps_Script Row {SOP15_TARGET_ROW} anchor mismatch: expected SOP 15, got {row26_a!r}"
        )
    row27_a = instr_col_a[SOP16_TARGET_ROW - 1].strip() if len(instr_col_a) >= SOP16_TARGET_ROW else ""
    if row27_a and "SOP 16" not in row27_a:
        raise ValueError(
            f"Instructions_&_Apps_Script Row {SOP16_TARGET_ROW} already occupied by non-SOP-16 content: {row27_a!r}"
        )

    need_instr_expand = int(instr_grid_rows) < SOP16_TARGET_ROW + 2
    return {
        "need_add_sheet": need_add_sheet,
        "need_hub_insert": need_hub_insert,
        "need_instr_expand": need_instr_expand,
    }


def build_1927_triage_sheet_batches(
    triage_rows_a_t: list[list[Any]],
    *,
    include_add_sheet: bool = False,
    include_hub_insert: bool = False,
    expand_instr_rows: bool = False,
    replace_filter_views: bool = False,
    existing_filter_view_ids: set[int] | None = None,
) -> dict[str, Any]:
    """Generate conflict-free Google Sheet DDL, RAW, USER_ENTERED, and Style batches (`DF-1`, `IT-3`)."""
    rows_a_t = [TRIAGE_HEADERS_A_TO_T] + list(triage_rows_a_t)
    total_rows = len(rows_a_t)
    grid_rows = max(MIN_TRIAGE_GRID_ROWS, total_rows + TRIAGE_ROW_HEADROOM)
    total_cols = len(ALL_TRIAGE_HEADERS)

    for r_idx, row in enumerate(rows_a_t, start=1):
        if len(row) != len(TRIAGE_HEADERS_A_TO_T):
            raise AssertionError(
                f"Triage row {r_idx} has {len(row)} cols, expected {len(TRIAGE_HEADERS_A_TO_T)}"
            )
        for cell in row:
            if isinstance(cell, str) and ("\u2014" in cell or "\u2013" in cell):
                raise AssertionError(f"Typographic dash leaked into triage row {r_idx}: {cell!r}")

    create_reqs: list[dict[str, Any]] = []
    if include_add_sheet:
        create_reqs.append(
            {
                "addSheet": {
                    "properties": {
                        "sheetId": SID_TRIAGE_1927,
                        "title": SHEET_TITLE_TRIAGE_1927,
                        "index": 12,
                        "gridProperties": {
                            "rowCount": grid_rows,
                            "columnCount": total_cols,
                            "frozenRowCount": 1,
                            "frozenColumnCount": 5,
                        },
                        "tabColor": rgb("#d97706"),
                    }
                }
            }
        )
    if include_hub_insert:
        create_reqs.append(
            {
                "insertDimension": {
                    "range": {
                        "sheetId": SID_HUB,
                        "dimension": "ROWS",
                        "startIndex": HUB_TRIAGE_1927_ROW - 1,
                        "endIndex": HUB_TRIAGE_1927_ROW,
                    },
                    "inheritFromBefore": True,
                }
            }
        )
    if expand_instr_rows:
        create_reqs.append(
            {
                "appendDimension": {
                    "sheetId": SID_INSTR,
                    "dimension": "ROWS",
                    "length": 10,
                }
            }
        )

    sop16_row = build_sop16_row()
    raw_values_ops: list[dict[str, Any]] = [
        {
            "op": "clear",
            "range": f"{SHEET_TITLE_TRIAGE_1927}!A2:T",
        },
        {
            "op": "write",
            "range": f"{SHEET_TITLE_TRIAGE_1927}!A1:T{total_rows}",
            "data": rows_a_t,
        },
        {
            "op": "write",
            "range": f"Instructions_&_Apps_Script!A{SOP16_TARGET_ROW}:C{SOP16_TARGET_ROW}",
            "data": [sop16_row],
        },
    ]

    r_in = HUB_FILTER_INPUT_ROW
    triage_formula_headers = [
        [
            '={"open_in_atlas"; ARRAYFORMULA(IF(LEN(E2:E)=0, , '
            f'HYPERLINK("{ATLAS_BASE_URL}#hcad=" & E2:E, "🗺️ Open in Atlas ↗")))}}',
            '={"inspect_hcad"; ARRAYFORMULA(IF(LEN(E2:E)=0, , '
            'HYPERLINK("https://arcweb.hcad.org/parcel-viewer-v2.0/?hcad_num=" & LEFT(E2:E, 13), '
            '"🏛️ HCAD Parcel ↗")))}',
        ]
    ]
    formula_values_ops: list[dict[str, Any]] = [
        {
            "op": "write",
            "range": f"{SHEET_TITLE_TRIAGE_1927}!U1:V1",
            "data": triage_formula_headers,
        },
        {
            "op": "write",
            "range": f"Admin_Hub_&_Quick_Filter!A{HUB_TRIAGE_1927_ROW}:I{HUB_TRIAGE_1927_ROW}",
            "data": [
                [
                    "🎯 1927 (100-Yr) Cutoff Undercount Triage (COH Census)",
                    f"=COUNTA(IFERROR({SHEET_TITLE_TRIAGE_1927}!A2:A))",
                    "Street-block triage / policy prep",
                    f"In {SHEET_TITLE_TRIAGE_1927} -> Data -> Filter views ({TRIAGE_1927_FILTER_VIEW_COUNT} pre-built Bucket A-F & Score >= 84 views)",
                    f'=HYPERLINK("#gid={SID_TRIAGE_1927}", "Open {SHEET_TITLE_TRIAGE_1927} Tab ↗")',
                    "",
                    "1927 Cutoff Street-Block Walk (Audit Mode)",
                    f'=HYPERLINK("{ATLAS_BASE_URL}#audit=1&auditCat=1927_CUTOFF_TRIAGE&auditSort=street_block", "🎯 Open 1927 Cutoff Street-Block Walk ↗")',
                    "Pre-filtered 100-yr cutoff suspects sorted along odd/even street blockfaces",
                ]
            ],
        },
        {
            "op": "write",
            "range": f"Admin_Hub_&_Quick_Filter!A{HUB_HOW_TO_USE_ROW}",
            "data": [
                [
                    f"HOW TO USE: Select a Triage Category (A{r_in}), Status (B{r_in}), or Historic District (C{r_in}) "
                    f"from the dropdowns below, or type any keyword in D{r_in} (e.g. street name, HCAD account, "
                    "building name, or submitter). Click '✏️ Edit Row #...' in Column A of the results to jump "
                    "straight to that exact cell in Pending_Submissions!"
                ]
            ],
        },
        {
            "op": "write",
            "range": f"Admin_Hub_&_Quick_Filter!F{r_in}",
            "data": [[build_hub_filter_count_formula(r_in)]],
        },
        {
            "op": "write",
            "range": f"Admin_Hub_&_Quick_Filter!A{HUB_RESULTS_FORMULA_ROW}",
            "data": [[build_hub_results_formula(r_in)]],
        },
    ]

    assert_safe_workbook_write_ranges(raw_values_ops + formula_values_ops, allow_baseline_b6=False)

    c_charcoal = rgb("#272727")
    c_sage = rgb("#95c959")
    c_offwhite = rgb("#f8faf5")

    def col_width_req(sheet_id: int, start_col: int, end_col: int, px: int) -> dict[str, Any]:
        return {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": start_col,
                    "endIndex": end_col,
                },
                "properties": {"pixelSize": px},
                "fields": "pixelSize",
            }
        }

    filter_view_specs = [
        (
            SID_TRIAGE_1927 + 1,
            "1. Bucket A - Pre-1927 City Directory Hold-Backs",
            {TRIAGE_COL_IDX["all_buckets"]: {"condition": {"type": "TEXT_CONTAINS", "values": [{"userEnteredValue": BUCKET_LABELS["A"]}]}}},
        ),
        (
            SID_TRIAGE_1927 + 2,
            "2. Bucket B - Rounded Placeholders (1930/35/40/50) in Pre-1925 Plats & HDs",
            {TRIAGE_COL_IDX["all_buckets"]: {"condition": {"type": "TEXT_CONTAINS", "values": [{"userEnteredValue": BUCKET_LABELS["B"]}]}}},
        ),
        (
            SID_TRIAGE_1927 + 3,
            "3. Bucket C - Annexation-Ring Clusters (1927-32 & 1949-56)",
            {TRIAGE_COL_IDX["all_buckets"]: {"condition": {"type": "TEXT_CONTAINS", "values": [{"userEnteredValue": BUCKET_LABELS["C"]}]}}},
        ),
        (
            SID_TRIAGE_1927 + 4,
            "4. Bucket D - Inner-Loop Pier-and-Beam Overwrites (1945-85)",
            {TRIAGE_COL_IDX["all_buckets"]: {"condition": {"type": "TEXT_CONTAINS", "values": [{"userEnteredValue": BUCKET_LABELS["D"]}]}}},
        ),
        (
            SID_TRIAGE_1927 + 5,
            "5. Bucket E - Historic Streetcar Corridor Masonry Commercial",
            {TRIAGE_COL_IDX["all_buckets"]: {"condition": {"type": "TEXT_CONTAINS", "values": [{"userEnteredValue": BUCKET_LABELS["E"]}]}}},
        ),
        (
            SID_TRIAGE_1927 + 6,
            "6. Bucket F - 1920 False-Positive Audit (1921-29 Plats & Absent <=1926)",
            {TRIAGE_COL_IDX["all_buckets"]: {"condition": {"type": "TEXT_CONTAINS", "values": [{"userEnteredValue": BUCKET_LABELS["F"]}]}}},
        ),
        (
            SID_TRIAGE_1927 + 7,
            "7. High-Priority Unreviewed Queue (Score >= 84)",
            {
                TRIAGE_COL_IDX["review_status"]: {"condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": "Unreviewed"}]}},
                TRIAGE_COL_IDX["priority_score"]: {"condition": {"type": "NUMBER_GREATER_THAN_EQ", "values": [{"userEnteredValue": "84"}]}},
            },
        ),
    ]
    assert len(filter_view_specs) == TRIAGE_1927_FILTER_VIEW_COUNT

    style_reqs: list[dict[str, Any]] = [
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": SID_TRIAGE_1927,
                    "gridProperties": {
                        "rowCount": grid_rows,
                        "columnCount": total_cols,
                        "frozenRowCount": 1,
                        "frozenColumnCount": 5,
                    },
                },
                "fields": "gridProperties(rowCount,columnCount,frozenRowCount,frozenColumnCount)",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": SID_TRIAGE_1927,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": total_cols,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": c_charcoal,
                        "textFormat": {"bold": True, "foregroundColor": c_sage, "fontSize": 10},
                        "verticalAlignment": "MIDDLE",
                        "wrapStrategy": "WRAP",
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment,wrapStrategy)",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": SID_TRIAGE_1927,
                    "startRowIndex": 1,
                    "endRowIndex": grid_rows,
                    "startColumnIndex": 0,
                    "endColumnIndex": total_cols,
                },
                "cell": {
                    "userEnteredFormat": {
                        "verticalAlignment": "TOP",
                        "wrapStrategy": "CLIP",
                        "textFormat": {"fontSize": 10},
                    }
                },
                "fields": "userEnteredFormat(verticalAlignment,wrapStrategy,textFormat.fontSize)",
            }
        },
        # Plain Text (@) formatting on hcad_num and review_status so leading zeros never strip
        *[
            {
                "repeatCell": {
                    "range": {
                        "sheetId": SID_TRIAGE_1927,
                        "startRowIndex": 1,
                        "endRowIndex": grid_rows,
                        "startColumnIndex": TRIAGE_COL_IDX[col_name],
                        "endColumnIndex": TRIAGE_COL_IDX[col_name] + 1,
                    },
                    "cell": {"userEnteredFormat": {"numberFormat": {"type": "TEXT", "pattern": "@"}}},
                    "fields": "userEnteredFormat.numberFormat",
                }
            }
            for col_name in ("review_status", "hcad_num")
        ],
        # Dropdown validation on review_status
        {
            "setDataValidation": {
                "range": {
                    "sheetId": SID_TRIAGE_1927,
                    "startRowIndex": 1,
                    "endRowIndex": grid_rows,
                    "startColumnIndex": 0,
                    "endColumnIndex": 1,
                },
                "rule": {
                    "condition": {
                        "type": "ONE_OF_LIST",
                        "values": [{"userEnteredValue": v} for v in TRIAGE_REVIEW_STATUSES],
                    },
                    "showCustomUi": True,
                    "strict": False,
                },
            }
        },
        # Wrap triage_evidence and reviewer_notes
        *[
            {
                "repeatCell": {
                    "range": {
                        "sheetId": SID_TRIAGE_1927,
                        "startRowIndex": 1,
                        "endRowIndex": grid_rows,
                        "startColumnIndex": TRIAGE_COL_IDX[col_name],
                        "endColumnIndex": TRIAGE_COL_IDX[col_name] + 1,
                    },
                    "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP"}},
                    "fields": "userEnteredFormat.wrapStrategy",
                }
            }
            for col_name in ("triage_evidence", "reviewer_notes")
        ],
        # Style SOP 16 row (Row 27 = startRowIndex 26..27) without touching Row 6 (B6)!
        {
            "repeatCell": {
                "range": {
                    "sheetId": SID_INSTR,
                    "startRowIndex": SOP16_TARGET_ROW - 1,
                    "endRowIndex": SOP16_TARGET_ROW,
                    "startColumnIndex": 0,
                    "endColumnIndex": 3,
                },
                "cell": {
                    "userEnteredFormat": {
                        "verticalAlignment": "TOP",
                        "wrapStrategy": "WRAP",
                        "textFormat": {"fontSize": 10},
                    }
                },
                "fields": "userEnteredFormat(verticalAlignment,wrapStrategy,textFormat.fontSize)",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": SID_INSTR,
                    "startRowIndex": SOP16_TARGET_ROW - 1,
                    "endRowIndex": SOP16_TARGET_ROW,
                    "startColumnIndex": 0,
                    "endColumnIndex": 1,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": c_offwhite,
                        "textFormat": {"bold": True, "fontSize": 10, "foregroundColor": c_charcoal},
                        "verticalAlignment": "TOP",
                        "wrapStrategy": "WRAP",
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment,wrapStrategy)",
            }
        },
        col_width_req(SID_TRIAGE_1927, 0, 1, 185),
        col_width_req(SID_TRIAGE_1927, 1, 2, 260),
        col_width_req(SID_TRIAGE_1927, 2, 3, 240),
        col_width_req(SID_TRIAGE_1927, 3, 4, 90),
        col_width_req(SID_TRIAGE_1927, 4, 5, 130),
        col_width_req(SID_TRIAGE_1927, 5, 6, 195),
        col_width_req(SID_TRIAGE_1927, 6, 7, 145),
        col_width_req(SID_TRIAGE_1927, 7, 9, 85),
        col_width_req(SID_TRIAGE_1927, 9, 10, 200),
        col_width_req(SID_TRIAGE_1927, 10, 13, 105),
        col_width_req(SID_TRIAGE_1927, 13, 16, 175),
        col_width_req(SID_TRIAGE_1927, 16, 17, 190),
        col_width_req(SID_TRIAGE_1927, 17, 18, 340),
        col_width_req(SID_TRIAGE_1927, 18, 20, 210),
        col_width_req(SID_TRIAGE_1927, 20, 22, 145),
        {
            "setBasicFilter": {
                "filter": {
                    "range": {
                        "sheetId": SID_TRIAGE_1927,
                        "startRowIndex": 0,
                        "endRowIndex": total_rows,
                        "startColumnIndex": 0,
                        "endColumnIndex": total_cols,
                    }
                }
            }
        },
    ]

    if replace_filter_views:
        for fv_id, _, _ in filter_view_specs:
            if existing_filter_view_ids is None or fv_id in existing_filter_view_ids:
                style_reqs.append({"deleteFilterView": {"filterId": fv_id}})
    for fv_id, fv_title, fv_criteria in filter_view_specs:
        style_reqs.append(
            {
                "addFilterView": {
                    "filter": {
                        "filterViewId": fv_id,
                        "title": fv_title,
                        "range": {
                            "sheetId": SID_TRIAGE_1927,
                            "startRowIndex": 0,
                            "endRowIndex": total_rows,
                            "startColumnIndex": 0,
                            "endColumnIndex": total_cols,
                        },
                        "criteria": {str(k): v for k, v in fv_criteria.items()},
                    }
                }
            }
        )

    return {
        "create_raw_batch": {"requests": create_reqs},
        "raw_values_batch": raw_values_ops,
        "formula_values_batch": formula_values_ops,
        "style_raw_batch": {"requests": style_reqs},
    }


def _format_python_literal(value: Any, level: int = 0) -> str:
    """Format JSON-compatible Python data structures with clean PEP-8 4-space indentation."""
    pad = "    " * level
    inner_pad = "    " * (level + 1)
    if value is None:
        return "None"
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        if not value:
            return "[]"
        items = [f"{inner_pad}{_format_python_literal(item, level + 1)}," for item in value]
        return "[\n" + "\n".join(items) + f"\n{pad}]"
    if isinstance(value, dict):
        if not value:
            return "{}"
        entries = [
            f"{inner_pad}{json.dumps(str(k), ensure_ascii=False)}: {_format_python_literal(v, level + 1)},"
            for k, v in value.items()
        ]
        return "{\n" + "\n".join(entries) + f"\n{pad}}}"
    raise TypeError(f"Unsupported literal type for DEFAULT_POLICY_IMPACT_BRIEF: {type(value)!r}")


def sync_policy_impact_brief_constants(
    *,
    scorecards_path: Path | None = None,
    fetch_live_permits_path: Path | None = None,
    plat_helpers_path: Path | None = None,
    active_permit_alerts_path: Path | None = None,
) -> dict[str, Any]:
    """Deterministically synchronize `DEFAULT_POLICY_IMPACT_BRIEF` across Python & JS from `neighborhood_scorecards.json` (`DF-6`, `IT-2`)."""
    sc_path = scorecards_path or (DATA_DIR / "neighborhood_scorecards.json")
    py_path = fetch_live_permits_path or (REPO_ROOT / "pipeline" / "atlas_pipeline" / "fetch_live_permits.py")
    js_path = plat_helpers_path or (REPO_ROOT / "app" / "js" / "platMonitorHelpers.js")

    sc_doc = json.loads(sc_path.read_text(encoding="utf-8"))
    brief = sc_doc.get("citywide_policy_brief")
    if not isinstance(brief, dict) or "standing_buildings_by_age" not in brief:
        raise ValueError(f"Missing citywide_policy_brief in {sc_path}")

    # Enforce strict Heritage District exclusion guardrail before syncing constants!
    desig = brief.get("local_designation_coverage") or {}
    if not desig.get("excludes_heritage_districts"):
        raise AssertionError("citywide_policy_brief must keep excludes_heritage_districts=True")

    py_formatted = _format_python_literal(brief, level=0)
    py_block = (
        "# BEGIN DEFAULT_POLICY_IMPACT_BRIEF\n"
        f"DEFAULT_POLICY_IMPACT_BRIEF: dict = {py_formatted}\n"
        "# END DEFAULT_POLICY_IMPACT_BRIEF"
    )
    py_text = py_path.read_text(encoding="utf-8")
    py_marker_re = re.compile(
        r"# BEGIN DEFAULT_POLICY_IMPACT_BRIEF\n.*?# END DEFAULT_POLICY_IMPACT_BRIEF",
        re.S,
    )
    py_legacy_re = re.compile(
        r"DEFAULT_POLICY_IMPACT_BRIEF:\s*dict\s*=\s*\{.*?\}\s*(?=\n+DEFAULT_ANNUAL_DEMOLITION_BENCHMARKS:\s*dict\s*=\s*\{)",
        re.S,
    )
    if py_marker_re.search(py_text):
        new_py_text = py_marker_re.sub(lambda _: py_block, py_text, count=1)
    elif py_legacy_re.search(py_text):
        new_py_text = py_legacy_re.sub(lambda _: py_block, py_text, count=1)
    else:
        raise RuntimeError(f"Could not locate DEFAULT_POLICY_IMPACT_BRIEF anchor in {py_path}")
    if "\u2014" in new_py_text or "\u2013" in new_py_text:
        raise AssertionError(f"Typographic dash detected when syncing {py_path}")
    py_path.write_text(new_py_text, encoding="utf-8")

    js_formatted = json.dumps(brief, indent=2, ensure_ascii=False)
    js_block = (
        "// BEGIN DEFAULT_POLICY_IMPACT_BRIEF\n"
        f"export const DEFAULT_POLICY_IMPACT_BRIEF = {js_formatted};\n\n"
        "DEFAULT_ANNUAL_DEMOLITION_BENCHMARKS.policy_impact_brief = DEFAULT_POLICY_IMPACT_BRIEF;\n"
        "// END DEFAULT_POLICY_IMPACT_BRIEF"
    )
    js_text = js_path.read_text(encoding="utf-8")
    js_marker_re = re.compile(
        r"// BEGIN DEFAULT_POLICY_IMPACT_BRIEF\n.*?// END DEFAULT_POLICY_IMPACT_BRIEF",
        re.S,
    )
    js_legacy_re = re.compile(
        r"export const DEFAULT_POLICY_IMPACT_BRIEF\s*=\s*\{.*?\};\s*\n+DEFAULT_ANNUAL_DEMOLITION_BENCHMARKS\.policy_impact_brief\s*=\s*DEFAULT_POLICY_IMPACT_BRIEF;",
        re.S,
    )
    if js_marker_re.search(js_text):
        new_js_text = js_marker_re.sub(lambda _: js_block, js_text, count=1)
    elif js_legacy_re.search(js_text):
        new_js_text = js_legacy_re.sub(lambda _: js_block, js_text, count=1)
    else:
        raise RuntimeError(f"Could not locate DEFAULT_POLICY_IMPACT_BRIEF anchor in {js_path}")
    if "\u2014" in new_js_text or "\u2013" in new_js_text:
        raise AssertionError(f"Typographic dash detected when syncing {js_path}")
    js_path.write_text(new_js_text, encoding="utf-8")

    # Also update active_permit_alerts.json if present in the target data dir so live web fallback matches 1:1
    alerts_path = active_permit_alerts_path or (
        (scorecards_path.parent / "active_permit_alerts.json")
        if scorecards_path is not None
        else (DATA_DIR / "active_permit_alerts.json")
    )
    if alerts_path.exists():
        alerts_doc = json.loads(alerts_path.read_text(encoding="utf-8"))
        if isinstance(alerts_doc, dict):
            alerts_doc.pop("policy_impact_brief", None)
            if isinstance(alerts_doc.get("annual_benchmarks"), dict):
                alerts_doc["annual_benchmarks"]["policy_impact_brief"] = brief
            if isinstance(alerts_doc.get("annual_demolition_benchmarks"), dict):
                alerts_doc["annual_demolition_benchmarks"]["policy_impact_brief"] = brief
            write_compact_json(alerts_path, alerts_doc)

    return brief


def _extract_centroid_xy(geom: dict[str, Any] | None) -> tuple[float, float] | None:
    if not isinstance(geom, dict):
        return None
    gtype = geom.get("type")
    coords = geom.get("coordinates") or []
    ring = (
        coords[0]
        if (gtype == "Polygon" and coords and coords[0])
        else coords[0][0]
        if (gtype == "MultiPolygon" and coords and coords[0] and coords[0][0])
        else None
    )
    if ring:
        pts = ring[:-1] if len(ring) > 1 and ring[0][:2] == ring[-1][:2] else ring
        return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
    if gtype == "Point" and len(coords) >= 2:
        return (float(coords[0]), float(coords[1]))
    return None


def run_1927_cutoff_pipeline(
    *,
    apply_changes: bool = False,
    data_dir: Path | None = None,
    cache_dir: Path | None = None,
    sheet_batches_dir: Path | None = None,
    bootstrap_13th_tab: bool = False,
    verify_anchors_json: Path | None = None,
    existing_triage_csv: Path | None = None,
    updated_at: str | None = None,
) -> dict[str, Any]:
    """Execute Step 1 (`DI-1`/`DI-2` promotion) and Step 2 (6-bucket COH census + CSV/Sheet batches)."""
    import numpy as np
    import shapely
    from shapely.geometry import shape
    from shapely.strtree import STRtree
    from shapely.validation import make_valid

    from atlas_pipeline.build_neighborhood_scorecards import iter_reconciled_standing_buildings

    ddir = data_dir or DATA_DIR
    cdir = cache_dir or CACHE_DIR
    eff_date = updated_at or datetime.date.today().isoformat()

    overrides_path = ddir / "curated_overrides.json"
    buildings_path = ddir / "buildings.geojson"
    search_index_path = ddir / "search_index.json"
    overlays_path = ddir / "overlays.json"
    citydir_path = ddir / "citydir_index.json"

    overrides_doc = json.loads(overrides_path.read_text(encoding="utf-8"))
    overrides: dict[str, Any] = dict(overrides_doc.get("overrides", overrides_doc))
    buildings_doc = json.loads(buildings_path.read_text(encoding="utf-8"))
    search_index: list[dict[str, Any]] = json.loads(search_index_path.read_text(encoding="utf-8"))
    overlays_doc = json.loads(overlays_path.read_text(encoding="utf-8"))
    citydir_doc = json.loads(citydir_path.read_text(encoding="utf-8"))

    by_hcad_dir: dict[str, dict[str, Any]] = citydir_doc.get("by_hcad") or {}
    absent_1926_set: set[str] = set(citydir_doc.get("absent_through_1926") or [])

    # 1. Demolished landmarks & Lost Houston HCAD sets
    demolished_lm_hcads: set[str] = set()
    standing_lm_status_by_hcad: dict[str, str] = {}
    for feat in overlays_doc.get("landmarks", {}).get("features", []):
        lp = feat.get("properties") or {}
        lm_hcads: list[str] = []
        for k_h in ("hcad_num", "site_hcad_num"):
            hc = str(lp.get(k_h) or "").strip()
            if re.match(r"^\d{13}$", hc):
                lm_hcads.append(hc)
        for raw_item in lp.get("site_hcad_nums") or []:
            hc = str(raw_item or "").strip()
            if re.match(r"^\d{13}$", hc):
                lm_hcads.append(hc)
        if not lm_hcads:
            continue
        is_demo_lm = bool(
            lp.get("demolished") or lp.get("is_demolished") or str(lp.get("protection_tier") or "") == "lm_demolished"
        )
        desig = str(lp.get("designation") or lp.get("landmark_type") or "COH Landmark").strip()
        for hc in lm_hcads:
            if is_demo_lm:
                demolished_lm_hcads.add(hc)
            else:
                standing_lm_status_by_hcad[hc] = desig

    lost_hcads: set[str] = set()
    for feat in overlays_doc.get("lost_houston", {}).get("features", []):
        lp = feat.get("properties") or {}
        for k_h in ("hcad_num", "site_hcad_num"):
            hc = str(lp.get(k_h) or "").strip()
            if re.match(r"^\d{13}$", hc):
                lost_hcads.add(hc)
        for raw_item in lp.get("site_hcad_nums") or []:
            hc = str(raw_item or "").strip()
            if re.match(r"^\d{13}$", hc):
                lost_hcads.add(hc)

    # 2. Stream CAMA Real_acct_owner.zip (COH jurs == '061' accounts + post-1927 main-structure demo permits)
    coh_accts: dict[str, dict[str, Any]] = {}
    demo_permit_hcads: set[str] = set()
    acct_zip = cdir / "Real_acct_owner.zip"
    if acct_zip.exists():
        with zipfile.ZipFile(acct_zip, "r") as zf:
            with zf.open("real_acct.txt") as fh:
                header_cols = fh.readline().decode("latin1", errors="ignore").rstrip("\r\n").split("\t")
                col_map = {c.strip(): i for i, c in enumerate(header_cols)}
                idx_acct = col_map.get("acct", 0)
                idx_mailto = col_map.get("mailto", 2)
                idx_m1 = col_map.get("mail_addr_1", 3)
                idx_m2 = col_map.get("mail_addr_2", 4)
                idx_mcity = col_map.get("mail_city", 5)
                idx_mst = col_map.get("mail_state", 6)
                idx_mzip = col_map.get("mail_zip", 7)
                idx_site = col_map.get("site_addr_1", 17)
                idx_sclass = col_map.get("state_class", 20)
                idx_yrimpr = col_map.get("yr_impr", 33)
                idx_bldar = col_map.get("bld_ar", 38)
                idx_jurs = col_map.get("jurs", 70)

                for raw_b in fh:
                    line = raw_b.decode("latin1", errors="ignore").rstrip("\r\n")
                    parts = line.split("\t")
                    if len(parts) <= idx_jurs:
                        continue
                    jurs_s = parts[idx_jurs]
                    if "061" not in jurs_s.split():
                        continue
                    acct = parts[idx_acct].strip()
                    if len(acct) != 13 or not acct.isdigit():
                        continue
                    try:
                        yr_i = int(parts[idx_yrimpr].strip() or "0")
                    except ValueError:
                        yr_i = 0
                    try:
                        bld_ar_f = float(parts[idx_bldar].strip() or "0")
                    except ValueError:
                        bld_ar_f = 0.0
                    coh_accts[acct] = {
                        "state_class": parts[idx_sclass].strip(),
                        "yr_impr": yr_i,
                        "bld_ar": bld_ar_f,
                        "situs_addr": parts[idx_site].strip(),
                        "owner_name": parts[idx_mailto].strip(),
                        "mail_addr_1": parts[idx_m1].strip(),
                        "mail_addr_2": parts[idx_m2].strip(),
                        "mail_city": parts[idx_mcity].strip(),
                        "mail_state": parts[idx_mst].strip(),
                        "mail_zip": parts[idx_mzip].strip(),
                        "jurs": "061",
                    }

            if "permits.txt" in zf.namelist():
                with zf.open("permits.txt") as pfh:
                    p_hdr = pfh.readline().decode("latin1", errors="ignore").rstrip("\r\n").split("\t")
                    pmap = {c.strip(): i for i, c in enumerate(p_hdr)}
                    i_pa = pmap.get("acct", 0)
                    i_ptp = pmap.get("permit_type", 3)
                    i_pdscr = pmap.get("dscr", 5)
                    i_pdt = pmap.get("issue_date", 8)
                    for raw_b in pfh:
                        acct_p = raw_b[:16].decode("latin1", errors="ignore").split("\t", 1)[0].strip()
                        if acct_p not in coh_accts:
                            continue
                        pp = raw_b.decode("latin1", errors="ignore").rstrip("\r\n").split("\t")
                        if len(pp) <= max(i_ptp, i_pdscr, i_pdt):
                            continue
                        dt_iso, p_yr = normalize_hcad_date(pp[i_pdt].strip())
                        if not p_yr or p_yr <= 1927:
                            continue
                        if classify_main_structure_demolition_permit(pp[i_ptp].strip(), pp[i_pdscr].strip()):
                            demo_permit_hcads.add(acct_p)

    # 3. Stream Real_building_land.zip for COH accounts via shared schema helper (`load_bld1_structural_flags`)
    scan_target_hcads = (
        set(coh_accts.keys())
        if coh_accts
        else {
            str((f.get("properties") or {}).get("hcad_num") or "").strip()
            for f in buildings_doc.get("features", [])
        }
    )
    bld1_scan = load_bld1_structural_flags(cdir / "Real_building_land.zip", scan_target_hcads)
    bld1_fnds_by_hcad: dict[str, set[str]] = bld1_scan["fnds_by_hcad"]
    bld1_slab_hcads: set[str] = bld1_scan["slab_hcads"]
    bld1_rebuilt_hcads: set[str] = bld1_scan["rebuilt_hcads"]
    bldg_years_by_hcad: dict[str, list[int]] = bld1_scan["bldg_years_by_hcad"]
    bld1_meta_by_hcad: dict[str, tuple[int, int, str, bool]] = bld1_scan["meta_by_hcad"]

    # 4. Collect standing footprints & coordinates across buildings.geojson + 5 countywide NDJSON shards
    blds_by_hcad_core: dict[str, list[dict[str, Any]]] = {}
    for feat in buildings_doc.get("features", []):
        bp = feat.get("properties") or {}
        hc = str(bp.get("hcad_num") or "").strip()
        if re.match(r"^\d{13}$", hc):
            blds_by_hcad_core.setdefault(hc, []).append(feat)

    # Separate pre-existing human/external overrides from prior auto-promoted Milestone 7 entries
    # so re-running `--apply` is 100% idempotent and preserves countywide `own_tier` from shards!
    human_overrides: dict[str, Any] = {
        k: ov
        for k, ov in overrides.items()
        if isinstance(ov, dict) and ov.get("verified_by") != AUTO_PROMOTED_VERIFIED_BY
    }
    human_overridden_hcads: set[str] = collect_overridden_hcad_accounts(human_overrides)
    overridden_parcel_hcads: set[str] = set(human_overridden_hcads)

    standing_years_by_hcad: dict[str, list[int]] = {}
    coord_by_hcad: dict[str, tuple[float, float]] = {}
    shard_props_by_hcad: dict[str, dict[str, Any]] = {}

    shard_dir = cdir / "aligned_ndjson"
    shard_paths = sorted(shard_dir.glob("buildings_*.geojsonseq")) if shard_dir.exists() else []
    if shard_paths:
        def _iter_shards():
            for sp in shard_paths:
                with sp.open("r", encoding="utf-8") as fh:
                    for ln in fh:
                        s_ln = ln.strip().lstrip("\x1e")
                        if not s_ln:
                            continue
                        feat_obj = json.loads(s_ln)
                        fp_s = feat_obj.get("properties")
                        if isinstance(fp_s, dict):
                            hc_s = str(fp_s.get("hcad_num") or "").strip()
                            if hc_s and hc_s not in shard_props_by_hcad and (not coh_accts or hc_s in coh_accts):
                                shard_props_by_hcad[hc_s] = fp_s
                        yield feat_obj

        for rec in iter_reconciled_standing_buildings(_iter_shards(), human_overrides, max_year=ACTIVE_TAX_YEAR + 1):
            hc = str(rec.get("hcad_num") or "").strip()
            if not hc or (coh_accts and hc not in coh_accts):
                continue
            yr_s = int(rec.get("year_built") or 0)
            if yr_s >= MIN_VALID_YEAR:
                standing_years_by_hcad.setdefault(hc, []).append(yr_s)
            if hc not in coord_by_hcad:
                cxy = _extract_centroid_xy(rec.get("geometry"))
                if cxy:
                    coord_by_hcad[hc] = cxy
    else:
        for hc, flist in blds_by_hcad_core.items():
            for feat in flist:
                bp = feat.get("properties") or {}
                if bp.get("suppress_only"):
                    continue
                if str(bp.get("id") or "") == hc and len(flist) > 1:
                    continue
                is_prior_auto = (
                    bp.get("is_curated_override")
                    and hc not in human_overridden_hcads
                    and int(bp.get("original_hcad_year") or 0) >= MIN_VALID_YEAR
                )
                yr_s = int(bp["original_hcad_year"]) if is_prior_auto else int(bp.get("year_built") or 0)
                if yr_s >= MIN_VALID_YEAR:
                    standing_years_by_hcad.setdefault(hc, []).append(yr_s)
                if hc not in coord_by_hcad:
                    cxy = _extract_centroid_xy(feat.get("geometry"))
                    if cxy:
                        coord_by_hcad[hc] = cxy

    # Ensure core features supply rich properties & fallback coordinates
    primary_core_props: dict[str, dict[str, Any]] = {}
    for hc, flist in blds_by_hcad_core.items():
        non_parcel = [f for f in flist if str((f.get("properties") or {}).get("id") or "") != hc]
        chosen = (non_parcel or flist)[0]
        primary_core_props[hc] = chosen.get("properties") or {}
        if hc not in coord_by_hcad:
            cxy = _extract_centroid_xy(chosen.get("geometry"))
            if cxy:
                coord_by_hcad[hc] = cxy

    # 5. Evaluate Step 1 (`DI-1` / `DI-2`) Double-Vetted Pre-1927 City Directory Auto-Promotions
    promoted_overrides: dict[str, dict[str, Any]] = {}
    demoted_auto_overrides: dict[str, dict[str, Any]] = {}
    holdback_reasons_by_hcad: dict[str, list[str]] = {}
    holdback_counter: Counter[str] = Counter()

    for hcad, dir_rec in sorted(by_hcad_dir.items()):
        if not isinstance(dir_rec, dict) or not re.match(r"^\d{13}$", hcad):
            continue
        earliest_dir = int(dir_rec.get("e") or 0)
        if not (1836 <= earliest_dir <= 1926):
            continue
        if coh_accts and hcad not in coh_accts:
            continue

        prior_auto_ov = (
            overrides[hcad]
            if isinstance(overrides.get(hcad), dict)
            and overrides[hcad].get("verified_by") == AUTO_PROMOTED_VERIFIED_BY
            else None
        )
        raw_core = primary_core_props.get(hcad) or {}
        raw_shard = shard_props_by_hcad.get(hcad) or {}
        if prior_auto_ov is not None:
            bp = dict(raw_core) if raw_core else dict(raw_shard)
            for k_sh in ("own_tier", "port_id", "year_built", "decade"):
                if k_sh in raw_shard and raw_shard[k_sh] not in ("", None, 0):
                    bp[k_sh] = raw_shard[k_sh]
            if not raw_shard and int(prior_auto_ov.get("original_hcad_year") or 0) >= MIN_VALID_YEAR:
                bp["year_built"] = int(prior_auto_ov["original_hcad_year"])
        else:
            bp = raw_core or raw_shard or {}

        ca = coh_accts.get(hcad) or {}
        already_ov = bool(
            (bp.get("is_curated_override") and prior_auto_ov is None) or hcad in human_overridden_hcads
        )
        s_years = standing_years_by_hcad.get(hcad) or bldg_years_by_hcad.get(hcad) or []
        atlas_yr = int(
            (raw_shard.get("year_built") if prior_auto_ov is not None else 0)
            or (prior_auto_ov.get("original_hcad_year") if prior_auto_ov is not None else 0)
            or bp.get("year_built")
            or (min(s_years) if s_years else ca.get("yr_impr") or 0)
        )
        cama_b_years = bldg_years_by_hcad.get(hcad) or []
        cama_max_yr = max(cama_b_years) if cama_b_years else int(ca.get("yr_impr") or 0)

        hd = str(bp.get("historic_district") or "").strip()
        in_hd = bool(hd and hd != "Outside Historic District")
        cnt = str(bp.get("contributing") or "").strip()
        addr = str(bp.get("address") or ca.get("situs_addr") or "").strip()

        eval_res = evaluate_pre1927_auto_promotion(
            {
                "hcad_num": hcad,
                "already_overridden": already_ov,
                "earliest_dir_year": earliest_dir,
                "atlas_year": atlas_yr,
                "is_demolished_landmark": hcad in demolished_lm_hcads,
                "in_lost_houston": hcad in lost_hcads,
                "has_post1927_demo_permit": hcad in demo_permit_hcads,
                "state_class": ca.get("state_class", ""),
                "cama_bld_ar": ca.get("bld_ar"),
                "contributing": cnt,
                "standing_building_years": s_years or ([atlas_yr] if atlas_yr > 0 else []),
                "cama_max_bld_year": cama_max_yr,
                "has_bld1_slab": hcad in bld1_slab_hcads,
                "has_bld1_new_rebuilt": hcad in bld1_rebuilt_hcads,
                "in_coh_historic_district": in_hd,
                "historic_district": hd,
                "neighborhood": str(bp.get("neighborhood") or ""),
                "bld1_foundations": sorted(bld1_fnds_by_hcad.get(hcad, set())),
                "address": addr,
                "dir_rec": dir_rec,
            }
        )

        if eval_res["eligible"]:
            ov_rec = build_promoted_override_record(
                hcad_num=hcad,
                promoted_year=int(eval_res["promoted_year"]),
                original_hcad_year=atlas_yr,
                source_citation=eval_res["source_citation"],
                source_url=eval_res["source_url"],
                standing_props=bp,
                cama_acct=ca,
                updated_at=eff_date,
            )
            promoted_overrides[hcad] = ov_rec
        else:
            if prior_auto_ov is not None:
                overrides.pop(hcad, None)
                demoted_auto_overrides[hcad] = prior_auto_ov
            if not already_ov:
                holdback_reasons_by_hcad[hcad] = list(eval_res["reasons"])
                for r_code in eval_res["reasons"]:
                    holdback_counter[r_code] += 1

    # Revert any retracted prior auto-promotions across buildings_doc and search_index before applying current promotions
    if demoted_auto_overrides:
        for hcad, old_ov in demoted_auto_overrides.items():
            orig_yr = int(old_ov.get("original_hcad_year") or 0)
            for feat in blds_by_hcad_core.get(hcad, []):
                fp = feat.get("properties") or {}
                if fp.get("suppress_only"):
                    continue
                if orig_yr >= MIN_VALID_YEAR:
                    fp["year_built"] = orig_yr
                    fp["decade"] = (orig_yr // 10) * 10
                fp["year_source"] = "HCAD"
                for k_rm in (
                    "is_curated_override",
                    "original_hcad_year",
                    "source_type",
                    "source_citation",
                    "source_url",
                ):
                    fp.pop(k_rm, None)
        for srow in search_index:
            if not isinstance(srow, dict) or srow.get("overlay_layer"):
                continue
            shc = str(srow.get("hcad_num") or "").strip()
            if shc in demoted_auto_overrides:
                orig_yr = int(demoted_auto_overrides[shc].get("original_hcad_year") or 0)
                if orig_yr >= MIN_VALID_YEAR:
                    srow["year_built"] = orig_yr
                    srow["decade"] = (orig_yr // 10) * 10
                    if str(srow.get("category") or "").startswith("Built "):
                        srow["category"] = f"Built {orig_yr}"
                    if "Built " in str(srow.get("sublabel") or ""):
                        srow["sublabel"] = re.sub(
                            r"\bBuilt\s+\d{4}\b",
                            f"Built {orig_yr}",
                            str(srow["sublabel"]),
                        )

    # Apply Step 1 promotions in memory (and persist if apply_changes=True)
    if promoted_overrides:
        for hcad, ov_rec in promoted_overrides.items():
            overrides[hcad] = ov_rec
            overridden_parcel_hcads.add(hcad)
            for feat in blds_by_hcad_core.get(hcad, []):
                fp = feat.setdefault("properties", {})
                if fp.get("suppress_only"):
                    continue
                fp["year_built"] = ov_rec["year_built"]
                fp["decade"] = ov_rec["decade"]
                fp["original_hcad_year"] = ov_rec["original_hcad_year"]
                fp["is_curated_override"] = True
                fp["year_source"] = ov_rec["source_type"]
                fp["source_type"] = ov_rec["source_type"]
                fp["source_citation"] = ov_rec["source_citation"]
                fp["source_url"] = ov_rec["source_url"]
                fp["own_tier"] = ov_rec["own_tier"]

        promoted_hcads_set = set(promoted_overrides.keys())
        for srow in search_index:
            if not isinstance(srow, dict) or srow.get("overlay_layer"):
                continue
            shc = str(srow.get("hcad_num") or "").strip()
            if shc in promoted_hcads_set:
                ov_r = promoted_overrides[shc]
                yr_new = int(ov_r["year_built"])
                srow["year_built"] = yr_new
                srow["decade"] = ov_r["decade"]
                cat_s = str(srow.get("category") or "")
                if cat_s.startswith("Built "):
                    srow["category"] = f"Built {yr_new}"
                sub_s = str(srow.get("sublabel") or "")
                if "Built " in sub_s:
                    srow["sublabel"] = re.sub(r"\bBuilt\s+\d{4}\b", f"Built {yr_new}", sub_s)

    # 6. Vectorized spatial overlay tagging across all COH (`061`) accounts with coordinates
    candidate_coh_hcads: list[str] = []
    candidate_xs: list[float] = []
    candidate_ys: list[float] = []
    for hc in sorted(coh_accts.keys() or primary_core_props.keys()):
        if hc in overridden_parcel_hcads or hc in lost_hcads or hc in demolished_lm_hcads:
            continue
        cxy = coord_by_hcad.get(hc)
        if cxy is None:
            continue
        candidate_coh_hcads.append(hc)
        candidate_xs.append(cxy[0])
        candidate_ys.append(cxy[1])

    pts_arr = shapely.points(np.asarray(candidate_xs, dtype=np.float64), np.asarray(candidate_ys, dtype=np.float64))
    pts_tree = STRtree(pts_arr) if len(pts_arr) else None
    n_cands = len(candidate_coh_hcads)

    plat_matches = match_earliest_platted_subdivisions(
        list(zip(candidate_xs, candidate_ys)),
        overlays_doc.get("platted_subdivisions", {}).get("features", []),
        min_year=MIN_VALID_YEAR,
    )
    plat_name_arr = [m[0] for m in plat_matches]
    plat_yr_arr = [m[1] for m in plat_matches]
    plat_exact_arr = [m[2] for m in plat_matches]
    plat_basis_arr = [m[3] for m in plat_matches]

    hd_name_arr = [""] * n_cands
    if pts_tree is not None:
        for hfeat in overlays_doc.get("historic_districts", {}).get("features", []):
            hp = hfeat.get("properties") or {}
            hg = hfeat.get("geometry")
            if not hg:
                continue
            sg = shape(hg)
            if not sg.is_valid:
                sg = make_valid(sg)
            hname = str(hp.get("name") or "").strip()
            for idx_h in pts_tree.query(sg, predicate="intersects"):
                hd_name_arr[int(idx_h)] = hname

    nbhd_arr = [""] * n_cands
    snbhd_arr = [""] * n_cands
    if pts_tree is not None:
        for layer_key, target_arr in (("neighborhoods", nbhd_arr), ("super_neighborhoods", snbhd_arr)):
            for nfeat in overlays_doc.get(layer_key, {}).get("features", []):
                np_p = nfeat.get("properties") or {}
                ng = nfeat.get("geometry")
                if not ng:
                    continue
                sg = shape(ng)
                if not sg.is_valid:
                    sg = make_valid(sg)
                nname = str(np_p.get("name") or "").strip()
                for idx_h in pts_tree.query(sg, predicate="intersects"):
                    ii = int(idx_h)
                    if not target_arr[ii]:
                        target_arr[ii] = nname

    annex_dec_arr = [0] * n_cands
    annex_lbl_arr = [""] * n_cands
    if pts_tree is not None:
        annex_feats = []
        for afeat in overlays_doc.get("annexations", {}).get("features", []):
            ap = afeat.get("properties") or {}
            if ap.get("annex_subtype") != "full_purpose":
                continue
            ag = afeat.get("geometry")
            if not ag:
                continue
            sg = shape(ag)
            if not sg.is_valid:
                sg = make_valid(sg)
            annex_feats.append((int(ap.get("decade") or 0), str(ap.get("era_label") or "").strip(), sg))
        # Sort descending so smallest/earliest containment overwrites later cumulative rings!
        annex_feats.sort(key=lambda x: -x[0])
        for adec, albl, asg in annex_feats:
            for idx_h in pts_tree.query(asg, predicate="intersects"):
                ii = int(idx_h)
                annex_dec_arr[ii] = adec
                annex_lbl_arr[ii] = albl

    # 7. Classify all COH (`061`) candidate parcels across Buckets A-F
    raw_triage_records: list[dict[str, Any]] = []
    for ii, hc in enumerate(candidate_coh_hcads):
        ca = coh_accts.get(hc) or {}
        bp = primary_core_props.get(hc) or shard_props_by_hcad.get(hc) or {}
        s_years = standing_years_by_hcad.get(hc) or []
        cama_b_years = bldg_years_by_hcad.get(hc) or []
        atlas_yr = int(bp.get("year_built") or (min(s_years) if s_years else ca.get("yr_impr") or 0))
        eff_y, rem_y, sdscr, is_comm_tbl = bld1_meta_by_hcad.get(hc, (0, 0, "", False))

        # Check multi-building swap: secondary CAMA building <= 1926 while primary yr_impr >= 1928
        has_pre1927_sec = len(cama_b_years) >= 2 and min(cama_b_years) <= 1926 and max(cama_b_years) >= 1928

        dir_rec = by_hcad_dir.get(hc) if isinstance(by_hcad_dir.get(hc), dict) else None
        earliest_dir = int(dir_rec["e"]) if (dir_rec and dir_rec.get("e")) else 0
        _, cdm_url = resolve_citydir_cdm_url(dir_rec, "")

        hd_val = str(bp.get("historic_district") or hd_name_arr[ii] or "").strip()
        in_hd_val = bool(hd_val and hd_val != "Outside Historic District")
        cnt_val = str(bp.get("contributing") or ("" if in_hd_val else "Outside Historic District")).strip()

        triage_rec = classify_1927_triage_buckets(
            {
                "hcad_num": hc,
                "jurs": ca.get("jurs", "061"),
                "already_overridden": False,
                "in_lost_houston": hc in lost_hcads,
                "is_demolished_landmark": hc in demolished_lm_hcads,
                "state_class": ca.get("state_class", ""),
                "cama_bld_ar": ca.get("bld_ar"),
                "atlas_year": atlas_yr,
                "eff_year": eff_y,
                "yr_remodel": rem_y,
                "structure_dscr": sdscr,
                "is_commercial_or_multifamily": is_comm_tbl,
                "contributing": cnt_val,
                "in_coh_historic_district": in_hd_val,
                "historic_district": hd_val,
                "neighborhood": str(bp.get("neighborhood") or nbhd_arr[ii] or ""),
                "super_neighborhood": str(bp.get("super_neighborhood") or snbhd_arr[ii] or ""),
                "address": str(bp.get("address") or ca.get("situs_addr") or ""),
                "bld1_foundations": sorted(bld1_fnds_by_hcad.get(hc, set())),
                "has_bld1_slab": hc in bld1_slab_hcads,
                "has_bld1_new_rebuilt": hc in bld1_rebuilt_hcads,
                "plat_name": plat_name_arr[ii],
                "plat_year": plat_yr_arr[ii],
                "plat_year_is_exact": plat_exact_arr[ii],
                "plat_year_basis": plat_basis_arr[ii],
                "annex_decade": annex_dec_arr[ii],
                "annex_label": annex_lbl_arr[ii],
                "in_inner_loop": 1836 <= annex_dec_arr[ii] <= 1930,
                "earliest_dir_year": earliest_dir,
                "in_absent_through_1926": hc in absent_1926_set,
                "holdback_reasons": holdback_reasons_by_hcad.get(hc, []),
                "has_pre1927_secondary_bldg": has_pre1927_sec,
                "landmark_status": standing_lm_status_by_hcad.get(hc, ""),
                "archival_url": cdm_url,
            }
        )
        if triage_rec is not None:
            raw_triage_records.append(triage_rec)

    sorted_triage = deduplicate_and_sort_triage_rows(raw_triage_records)
    if existing_triage_csv is not None and existing_triage_csv.exists():
        existing_statuses = load_existing_triage_csv_statuses(
            existing_triage_csv.read_text(encoding="utf-8-sig")
        )
        sorted_triage = merge_existing_triage_statuses(sorted_triage, existing_statuses)

    primary_bucket_counts = Counter(r["primary_bucket"] for r in sorted_triage)
    any_bucket_counts: Counter[str] = Counter()
    for r in sorted_triage:
        for b_lbl in str(r["all_buckets"]).split(" | "):
            if b_lbl:
                any_bucket_counts[b_lbl] += 1

    sheet_subset = select_sheet_triage_subset(sorted_triage, max_rows=MAX_SHEET_TRIAGE_ROWS)
    sheet_rows_a_t = [triage_record_to_sheet_row(r) for r in sheet_subset]

    csv_path = cdir / "triage_1927_cutoff_coh.csv"
    if apply_changes:
        overrides_doc["overrides"] = overrides
        write_compact_json(overrides_path, overrides_doc)
        write_compact_json(buildings_path, buildings_doc)
        write_compact_json(search_index_path, search_index)

        cdir.mkdir(parents=True, exist_ok=True)
        with csv_path.open("w", newline="", encoding="utf-8") as cf:
            writer = csv.writer(cf)
            writer.writerow(TRIAGE_HEADERS_A_TO_T)
            for r in sorted_triage:
                writer.writerow(triage_record_to_sheet_row(r))

    if sheet_batches_dir is not None:
        sheet_batches_dir.mkdir(parents=True, exist_ok=True)
        inc_add = bootstrap_13th_tab
        inc_hub = bootstrap_13th_tab
        exp_instr = bootstrap_13th_tab
        existing_fv_ids: set[int] | None = None
        if verify_anchors_json is not None:
            anchor_doc = json.loads(verify_anchors_json.read_text(encoding="utf-8"))
            raw_tabs = anchor_doc.get("tab_id_to_title") or {}
            tab_map = {int(k): str(v) for k, v in raw_tabs.items()}
            anchor_flags = verify_1927_triage_sheet_anchors(
                tab_id_to_title=tab_map,
                hub_col_a=[str(v or "") for v in (anchor_doc.get("hub_col_a") or [])],
                instr_col_a=[str(v or "") for v in (anchor_doc.get("instr_col_a") or [])],
                instr_grid_rows=int(anchor_doc.get("instr_grid_rows") or 60),
            )
            inc_add = anchor_flags["need_add_sheet"]
            inc_hub = anchor_flags["need_hub_insert"]
            exp_instr = anchor_flags["need_instr_expand"]
            if "filter_view_ids" in anchor_doc and anchor_doc["filter_view_ids"] is not None:
                existing_fv_ids = {int(x) for x in anchor_doc["filter_view_ids"]}
        batches = build_1927_triage_sheet_batches(
            sheet_rows_a_t,
            include_add_sheet=inc_add,
            include_hub_insert=inc_hub,
            expand_instr_rows=exp_instr,
            replace_filter_views=not inc_add,
            existing_filter_view_ids=existing_fv_ids,
        )
        for name, payload in batches.items():
            (sheet_batches_dir / f"triage_1927_{name}.json").write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

    return {
        "mode": "APPLY" if apply_changes else "DRY_RUN",
        "promoted_count": len(promoted_overrides),
        "promoted_hcads": sorted(promoted_overrides.keys()),
        "promoted_sample": [promoted_overrides[k] for k in sorted(promoted_overrides.keys())[:10]],
        "holdback_count": len(holdback_reasons_by_hcad),
        "holdback_reason_counts": dict(holdback_counter),
        "total_triage_coh_parcels": len(sorted_triage),
        "sheet_triage_rows": len(sheet_rows_a_t),
        "primary_bucket_counts": dict(primary_bucket_counts),
        "any_bucket_counts": dict(any_bucket_counts),
        "csv_path": str(csv_path),
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Build 1927 (100-Yr Cutoff) HCAD undercount promotions, COH census CSV, and Google Sheet batches."
    )
    parser.add_argument("--apply", action="store_true", help="Write double-vetted promotions and COH census CSV.")
    parser.add_argument("--dry-run", action="store_true", help="Preview counts with zero writes to app/public/data/.")
    parser.add_argument(
        "--sync-policy-brief",
        action="store_true",
        help="Synchronize DEFAULT_POLICY_IMPACT_BRIEF in fetch_live_permits.py & platMonitorHelpers.js from scorecards.",
    )
    parser.add_argument(
        "--sheet-batches-dir",
        type=Path,
        default=None,
        help="Optional directory to emit 13th-tab Google Sheet JSON batch files.",
    )
    parser.add_argument(
        "--bootstrap-13th-tab",
        action="store_true",
        help="ONE-TIME ONLY: emit addSheet(gid=927192701) and Hub Row 20 insertDimension DDL.",
    )
    parser.add_argument(
        "--verify-anchors-json",
        type=Path,
        default=None,
        help="Optional JSON workbook anchor snapshot validated via verify_1927_triage_sheet_anchors before batch output.",
    )
    parser.add_argument(
        "--existing-triage-csv",
        type=Path,
        default=None,
        help="Optional exported 1927_Cutoff_Triage CSV to preserve moderator review_status and reviewer_notes.",
    )
    args = parser.parse_args(argv)

    if args.sync_policy_brief:
        brief = sync_policy_impact_brief_constants()
        c100 = next(r for r in brief["standing_buildings_by_age"] if r["id"] == "100_plus")
        print(
            "Synchronized DEFAULT_POLICY_IMPACT_BRIEF 1:1 across Python, JS, and active_permit_alerts.json -> "
            f"100+ yr standing={c100['standing_count']:,} ({c100['share_of_dated_pct']}% dated, "
            f"unprotected={c100['unprotected_count']:,} [{c100['unprotected_pct']}%])"
        )
        if not args.apply and not args.dry_run and args.sheet_batches_dir is None:
            return

    apply_mode = bool(args.apply and not args.dry_run)
    res = run_1927_cutoff_pipeline(
        apply_changes=apply_mode,
        sheet_batches_dir=args.sheet_batches_dir,
        bootstrap_13th_tab=args.bootstrap_13th_tab,
        verify_anchors_json=args.verify_anchors_json,
        existing_triage_csv=args.existing_triage_csv,
    )
    print(json.dumps({k: v for k, v in res.items() if k != "promoted_hcads"}, indent=2))


if __name__ == "__main__":
    main()
