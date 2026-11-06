"""Milestone 7 unit, cross-language parity, Google Sheet layout, and data-integrity tests.

Covers:
  - UT-1 & DF-5: `evaluate_pre1927_auto_promotion` positive double-vetted promotions and 100%
    exclusion of Non-Contributing replacements (`0102490000006`, `0202160000012`), Building 1
    Concrete Slab, New / Rebuilt remodel codes, reserved fixture `0200670000010`, demolished
    landmarks, `lost_houston`, post-1927 demo permits, CAMA vacant (`C1`/`C2`/`C3`, `bld_ar <= 0`),
    heterogeneous post-1960 multi-building parcels, invalid ContentDM pointer `p <= 0`, partial
    house-number OCR substrings (`10` in `1010`), and uncorroborated pre-1911 single-volume hits
    outside Old Sixth Ward / Downtown Borden grid; plus `--dry-run` side-effect isolation.
  - UT-2 & RP-2: `build_promoted_override_record` `SOP 4 Case D` invariants (omitted `geometry` and
    `replace_parcel_shards`, accurate `own_tier` across commercial/industrial/multi-family/civic/
    portfolio/homestead accounts, explicit `contributing`, clean ASCII `source_citation`, canonical
    ContentDM `/id/<p>/rec/1` `source_url`) and multi-footprint sibling preservation.
  - UT-3: `classify_1927_triage_buckets` across Buckets A-F, `jurs == '061'` filtering, 13-digit
    `hcad_num` deduplication, `select_sheet_triage_subset` contiguous blockface re-sorting, and
    formula-injection sanitization (`=`, `+`, `-`, `@`).
  - UT-5 & DF-2: 1:1 cross-language street-walk sort key parity between Python `parse_street_walk_key`
    and Node.js `parseAuditStreetWalkKey` / `sortAuditQueueItems` across 26 Houston address fixtures.
  - IT-1, IT-2, IT-3, IT-4, RP-1 & RP-4: Reconciled JSON bundle & census CSV invariants, zero
    `LOST_HOUSTON_EXTANT_COLLISION` findings, `< 995,000` B `historical_audit_queue.json` cap,
    4-way `DEFAULT_POLICY_IMPACT_BRIEF` parity, 13-tab Google Sheet batch generation & Row 20/21
    non-interference across `setup_admin_sheet.py`, `sync_field_surveys_sheet.py`, and
    `build_1927_cutoff_triage.py`, and strict `Instructions_&_Apps_Script!B6` passkey isolation.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
import subprocess
from typing import Any
import zipfile

import pytest

from atlas_pipeline.audit_historical_accuracy import build_historical_audit_queue_payload
from atlas_pipeline.build_1927_cutoff_triage import (
    ALL_TRIAGE_HEADERS,
    BUCKET_LABELS,
    RESERVED_E2E_FIXTURE_HCADS,
    STREET_WALK_DIR_TOKENS,
    STREET_WALK_TYPE_SUFFIXES,
    STREET_WALK_UNIT_CUT_TOKENS,
    STREET_WALK_UNNUMBERED_SENTINEL,
    TRIAGE_HEADERS_A_TO_T,
    UNDESIGNATED_DISTRICT_SENTINELS,
    _extract_centroid_xy,
    build_1927_triage_sheet_batches,
    build_promoted_override_record,
    classify_1927_triage_buckets,
    deduplicate_and_sort_triage_rows,
    evaluate_pre1927_auto_promotion,
    load_existing_triage_csv_statuses,
    main as triage_1927_main,
    merge_existing_triage_statuses,
    parse_street_walk_key,
    run_1927_cutoff_pipeline,
    select_sheet_triage_subset,
    sync_policy_impact_brief_constants,
    triage_record_to_sheet_row,
    verify_1927_triage_sheet_anchors,
)
from atlas_pipeline.build_neighborhood_scorecards import iter_reconciled_standing_buildings
from atlas_pipeline.fetch_live_permits import (
    DEFAULT_POLICY_IMPACT_BRIEF,
    build_annual_demolition_benchmarks,
    get_policy_impact_brief,
)
from atlas_pipeline.schema import (
    ACTIVE_CACHE_BUST,
    ALLOWED_PREWAR_FOUNDATIONS,
    collect_overridden_hcad_accounts,
    load_bld1_structural_flags,
    match_earliest_platted_subdivisions,
)
from atlas_pipeline.setup_admin_sheet import (
    HUB_FILTER_INPUT_ROW,
    HUB_HOW_TO_USE_ROW,
    HUB_RESULTS_FORMULA_ROW,
    HUB_SURVEYS_ROW,
    HUB_TRIAGE_1927_ROW,
    SHEET_TITLE_TRIAGE_1927,
    SID_LOST,
    SID_SURVEYS,
    SID_TRIAGE_1927,
    SOP15_TARGET_ROW,
    SOP16_TARGET_ROW,
    TRIAGE_1927_FILTER_VIEW_COUNT,
    assert_safe_workbook_write_ranges,
    build_admin_hub_rows,
    build_instructions_rows,
    build_sop16_row,
    inspect_workbook_layout_anchors,
    main as setup_admin_sheet_main,
)
from atlas_pipeline.sync_field_surveys_sheet import (
    SHEET_TITLE_SURVEYS,
    build_milestone3_sheet_batches,
    build_sop15_row,
    main as sync_field_surveys_main,
    resolve_hub_filter_input_row,
    verify_live_sheet_anchors,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "app" / "public" / "data"
CACHE_DIR = REPO_ROOT / "pipeline" / "cache"


def _base_valid_promotion_candidate(**overrides: Any) -> dict[str, Any]:
    cand: dict[str, Any] = {
        "hcad_num": "0112233445566",
        "address": "1517 Heights Blvd",
        "historic_district": "Houston Heights Historic District South",
        "neighborhood": "Houston Heights",
        "in_coh_historic_district": True,
        "contributing": "Contributing",
        "earliest_dir_year": 1915,
        "atlas_year": 1930,
        "standing_building_years": [1930],
        "cama_max_bld_year": 1930,
        "state_class": "A1",
        "cama_bld_ar": 2150.0,
        "bld1_foundations": ["Crawl Space"],
        "has_bld1_slab": False,
        "has_bld1_new_rebuilt": False,
        "is_demolished_landmark": False,
        "in_lost_houston": False,
        "has_post1927_demo_permit": False,
        "already_overridden": False,
        "dir_rec": {
            "e": 1915,
            "p": 32415,
            "c": "1517 Heights Blvd - Craftsman residence",
            "y": [1915, 1919, 1922],
        },
    }
    cand.update(overrides)
    return cand


def test_ut1_di1_pre1927_auto_promotion_positive_and_negative_gates() -> None:
    """UT-1 & DF-5: Verify double-vetted positive paths and every negative exclusion gate."""
    # Path A: Contributing inside COH Historic District (1928..1960) promotes even up to 1960
    res_hd = evaluate_pre1927_auto_promotion(
        _base_valid_promotion_candidate(atlas_year=1955, standing_building_years=[1955], cama_max_bld_year=1955)
    )
    assert res_hd["eligible"] is True
    assert res_hd["promoted_year"] == 1915
    assert res_hd["reasons"] == []
    assert res_hd["source_url"] == "https://cdm17006.contentdm.oclc.org/digital/collection/citydir/id/32415/rec/1"
    assert "\u2014" not in res_hd["source_citation"]
    assert "\u2013" not in res_hd["source_citation"]

    # Path B: Outside HD with rounded placeholder (1930/1935/1940/1945/1950) or early annexation (1928..1932) + pre-war Bldg 1 foundation
    for valid_yr in (1929, 1930, 1935, 1940, 1945, 1950):
        res_typ = evaluate_pre1927_auto_promotion(
            _base_valid_promotion_candidate(
                in_coh_historic_district=False,
                historic_district="Outside Historic District",
                contributing="Outside Historic District",
                atlas_year=valid_yr,
                standing_building_years=[valid_yr],
                cama_max_bld_year=valid_yr,
                bld1_foundations=["Masonry Bearing"],
            )
        )
        assert res_typ["eligible"] is True, f"Expected {valid_yr} with Masonry Bearing to promote: {res_typ}"

    # Non-placeholder year 1954 outside HD MUST NOT promote
    res_non_placeholder = evaluate_pre1927_auto_promotion(
        _base_valid_promotion_candidate(
            in_coh_historic_district=False,
            historic_district="Outside Historic District",
            contributing="Outside Historic District",
            atlas_year=1954,
            standing_building_years=[1954],
            cama_max_bld_year=1954,
            bld1_foundations=["Crawl Space"],
        )
    )
    assert res_non_placeholder["eligible"] is False
    assert "lacks_hd_contributing_or_prewar_foundation_corroboration" in res_non_placeholder["reasons"]

    # Negative Gate 1: Non-Contributing replacement buildings (explicitly testing 0102490000006 & 0202160000012)
    for nc_hcad, nc_addr in (
        ("0102490000006", "10 Courtlandt Pl"),
        ("0202160000012", "1001 Columbia St"),
    ):
        res_nc = evaluate_pre1927_auto_promotion(
            _base_valid_promotion_candidate(
                hcad_num=nc_hcad,
                address=nc_addr,
                contributing="Non-Contributing",
                dir_rec={"e": 1912, "p": 21100, "c": f"{nc_addr} residence", "y": [1912, 1915]},
            )
        )
        assert res_nc["eligible"] is False
        assert "non_contributing_replacement_risk" in res_nc["reasons"]

    # Negative Gate 2: Building 1 Concrete Slab row
    res_slab = evaluate_pre1927_auto_promotion(
        _base_valid_promotion_candidate(has_bld1_slab=True)
    )
    assert res_slab["eligible"] is False
    assert "bld1_concrete_slab" in res_slab["reasons"]

    # Negative Gate 3: Building 1 New / Rebuilt remodel code
    res_rebuilt = evaluate_pre1927_auto_promotion(
        _base_valid_promotion_candidate(has_bld1_new_rebuilt=True)
    )
    assert res_rebuilt["eligible"] is False
    assert "bld1_new_or_rebuilt_code" in res_rebuilt["reasons"]

    # Negative Gate 4: Reserved multi-category E2E fixture 0200670000010 (2009 Harvard St)
    assert "0200670000010" in RESERVED_E2E_FIXTURE_HCADS
    res_reserved = evaluate_pre1927_auto_promotion(
        _base_valid_promotion_candidate(
            hcad_num="0200670000010",
            address="2009 Harvard St",
            dir_rec={"e": 1915, "p": 32400, "c": "2009 Harvard St", "y": [1915, 1919]},
        )
    )
    assert res_reserved["eligible"] is False
    assert "reserved_e2e_fixture" in res_reserved["reasons"]

    # Negative Gate 5: Demolished landmark, Lost Houston, post-1927 demo permit, CAMA vacant/zero area
    for flag_kw, expected_code in (
        ({"is_demolished_landmark": True}, "demolished_landmark"),
        ({"in_lost_houston": True}, "in_lost_houston"),
        ({"has_post1927_demo_permit": True}, "post1927_demo_permit"),
        ({"state_class": "C1"}, "cama_vacant_state_class"),
        ({"cama_bld_ar": 0.0}, "cama_zero_building_area"),
    ):
        res_bad = evaluate_pre1927_auto_promotion(_base_valid_promotion_candidate(**flag_kw))
        assert res_bad["eligible"] is False
        assert expected_code in res_bad["reasons"]

    # Negative Gate 6: Heterogeneous multi-building parcel with post-1960 sibling (`DF-5`)
    res_hetero = evaluate_pre1927_auto_promotion(
        _base_valid_promotion_candidate(standing_building_years=[1930, 2016], cama_max_bld_year=2016)
    )
    assert res_hetero["eligible"] is False
    assert "heterogeneous_post1960_sibling" in res_hetero["reasons"]

    # Negative Gate 7: Invalid ContentDM pointer p <= 0
    res_noptr = evaluate_pre1927_auto_promotion(
        _base_valid_promotion_candidate(
            dir_rec={"e": 1915, "p": 0, "c": "1517 Heights Blvd", "y": [1915, 1919]}
        )
    )
    assert res_noptr["eligible"] is False
    assert "missing_valid_cdm_pointer" in res_noptr["reasons"]

    # Negative Gate 8: Partial house-number OCR substring ("10" inside "1010") MUST reject
    res_partial_ocr = evaluate_pre1927_auto_promotion(
        _base_valid_promotion_candidate(
            address="10 Courtlandt Pl",
            dir_rec={"e": 1915, "p": 32415, "c": "1010 Courtlandt Pl", "y": [1915, 1919]},
        )
    )
    assert res_partial_ocr["eligible"] is False
    assert "ocr_missing_exact_house_number" in res_partial_ocr["reasons"]

    # Negative Gate 9: Uncorroborated single-volume pre-1911 hit outside Old Sixth Ward / Downtown Borden grid
    res_pre1911_single = evaluate_pre1927_auto_promotion(
        _base_valid_promotion_candidate(
            earliest_dir_year=1905,
            historic_district="Houston Heights Historic District South",
            neighborhood="Houston Heights",
            dir_rec={"e": 1905, "p": 12001, "c": "1517 Heights Blvd", "y": [1905]},
        )
    )
    assert res_pre1911_single["eligible"] is False
    assert "uncorroborated_pre1911_renumbering_risk" in res_pre1911_single["reasons"]

    # Corroborated pre-1911 hit (2+ directory volumes) OR inside Old Sixth Ward promotes cleanly
    res_pre1911_multi = evaluate_pre1927_auto_promotion(
        _base_valid_promotion_candidate(
            earliest_dir_year=1905,
            dir_rec={"e": 1905, "p": 12001, "c": "1517 Heights Blvd", "y": [1905, 1915]},
        )
    )
    assert res_pre1911_multi["eligible"] is True

    res_pre1911_osw = evaluate_pre1927_auto_promotion(
        _base_valid_promotion_candidate(
            earliest_dir_year=1905,
            historic_district="Old Sixth Ward Protected Historic District",
            neighborhood="Sixth Ward",
            dir_rec={"e": 1905, "p": 12001, "c": "1517 Heights Blvd", "y": [1905]},
        )
    )
    assert res_pre1911_osw["eligible"] is True


def test_ut1_dry_run_side_effect_isolation(tmp_path: Path) -> None:
    """UT-1: Verify run_1927_cutoff_pipeline(apply_changes=False) performs zero writes to data_dir or cache_dir."""
    tmp_data = tmp_path / "data"
    tmp_cache = tmp_path / "cache"
    tmp_data.mkdir()
    tmp_cache.mkdir()

    initial_files: dict[str, str] = {
        "curated_overrides.json": json.dumps({"overrides": {}}),
        "buildings.geojson": json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {
                            "id": "0112233445566",
                            "hcad_num": "0112233445566",
                            "address": "1517 Heights Blvd",
                            "year_built": 1930,
                            "historic_district": "Houston Heights Historic District South",
                            "contributing": "Contributing",
                            "neighborhood": "Houston Heights",
                        },
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [
                                    [-95.3980, 29.7980],
                                    [-95.3979, 29.7980],
                                    [-95.3979, 29.7981],
                                    [-95.3980, 29.7981],
                                    [-95.3980, 29.7980],
                                ]
                            ],
                        },
                    }
                ],
            }
        ),
        "search_index.json": json.dumps(
            [
                {
                    "id": "0112233445566",
                    "hcad_num": "0112233445566",
                    "address": "1517 Heights Blvd",
                    "year_built": 1930,
                }
            ]
        ),
        "overlays.json": json.dumps(
            {
                "landmarks": {"type": "FeatureCollection", "features": []},
                "lost_houston": {"type": "FeatureCollection", "features": []},
                "platted_subdivisions": {"type": "FeatureCollection", "features": []},
                "historic_districts": {"type": "FeatureCollection", "features": []},
                "neighborhoods": {"type": "FeatureCollection", "features": []},
                "super_neighborhoods": {"type": "FeatureCollection", "features": []},
                "annexations": {"type": "FeatureCollection", "features": []},
            }
        ),
        "citydir_index.json": json.dumps(
            {
                "by_hcad": {
                    "0112233445566": {
                        "e": 1915,
                        "p": 32415,
                        "c": "1517 Heights Blvd",
                        "y": [1915, 1919],
                    }
                },
                "absent_through_1926": [],
            }
        ),
    }
    for fname, content in initial_files.items():
        (tmp_data / fname).write_text(content, encoding="utf-8")

    res = run_1927_cutoff_pipeline(
        apply_changes=False,
        data_dir=tmp_data,
        cache_dir=tmp_cache,
    )
    assert res["mode"] == "DRY_RUN"
    assert not (tmp_cache / "triage_1927_cutoff_coh.csv").exists()
    for fname, expected_content in initial_files.items():
        assert (tmp_data / fname).read_text(encoding="utf-8") == expected_content


def test_ut2_di2_build_promoted_override_record_sop4_and_ownership_tiers() -> None:
    """UT-2 & RP-2: Verify omitted geometry/replace_parcel_shards and accurate non-homestead own_tier."""
    # Commercial F1 account must never default to homestead
    comm_rec = build_promoted_override_record(
        hcad_num="0011223344556",
        promoted_year=1913,
        original_hcad_year=1948,
        source_citation="Appears in 1913 Directory\u2014verified\u2013storefront",
        source_url="https://cdm17006.contentdm.oclc.org/digital/collection/citydir/id/25100/rec/1",
        standing_props={
            "address": "308 Main St",
            "use_category": "Commercial",
            "own_tier": "local_small",
            "historic_district": "Main Street Market Square Historic District",
            "contributing": "Contributing",
        },
        cama_acct={
            "state_class": "F1",
            "owner_name": "308 MAIN HOLDINGS LP",
            "situs_addr": "308 MAIN ST",
            "mail_addr_1": "PO BOX 100",
        },
    )
    assert "geometry" not in comm_rec
    assert "replace_parcel_shards" not in comm_rec
    assert comm_rec["own_tier"] != "homestead"
    assert comm_rec["own_tier"] in {"local_small", "anonymity_shield", "regional_portfolio"}
    assert comm_rec["contributing"] == "Contributing"
    assert comm_rec["year_built"] == 1913
    assert comm_rec["decade"] == 1910
    assert comm_rec["original_hcad_year"] == 1948
    assert comm_rec["source_type"] == "Houston City Directory"
    assert "\u2014" not in comm_rec["source_citation"]
    assert "\u2013" not in comm_rec["source_citation"]

    # RP-2 & MT-2: Attribute-only override preserves ALL sibling roof footprints on a multi-footprint parcel
    sibling_shards = [
        {
            "type": "Feature",
            "properties": {
                "id": "0011223344556_main",
                "hcad_num": "0011223344556",
                "year_built": 1948,
                "historic_district": "Main Street Market Square Historic District",
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [-95.3630, 29.7600],
                        [-95.3629, 29.7600],
                        [-95.3629, 29.7601],
                        [-95.3630, 29.7601],
                        [-95.3630, 29.7600],
                    ]
                ],
            },
        },
        {
            "type": "Feature",
            "properties": {
                "id": "0011223344556_carriage",
                "hcad_num": "0011223344556",
                "year_built": 1948,
                "historic_district": "Main Street Market Square Historic District",
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [-95.3628, 29.7600],
                        [-95.3627, 29.7600],
                        [-95.3627, 29.7601],
                        [-95.3628, 29.7601],
                        [-95.3628, 29.7600],
                    ]
                ],
            },
        },
    ]
    reconciled_siblings = list(
        iter_reconciled_standing_buildings(sibling_shards, {"0011223344556": comm_rec})
    )
    assert len(reconciled_siblings) == 2
    assert {r["id"] for r in reconciled_siblings} == {"0011223344556_main", "0011223344556_carriage"}
    assert all(r["year_built"] == 1913 for r in reconciled_siblings)

    # Civic / Church X1 account preserves civic_nonprofit
    civic_rec = build_promoted_override_record(
        hcad_num="0011223344557",
        promoted_year=1908,
        original_hcad_year=1935,
        source_citation="Appears in 1908 Directory",
        source_url="https://cdm17006.contentdm.oclc.org/digital/collection/citydir/id/19000/rec/1",
        standing_props={
            "address": "1101 Bastrop St",
            "use_category": "Civic / Institutional",
            "building_name": "Historic Chapel",
            "own_tier": "civic_nonprofit",
        },
        cama_acct={
            "state_class": "X1",
            "owner_name": "FIRST BAPTIST CHURCH TRUSTEES",
            "situs_addr": "1101 BASTROP ST",
        },
    )
    assert civic_rec["own_tier"] == "civic_nonprofit"
    assert civic_rec["contributing"] == "Outside Historic District"
    assert "geometry" not in civic_rec

    # Portfolio-owned multi-family B1 account retains regional_portfolio / institutional_pe standing tier
    mf_rec = build_promoted_override_record(
        hcad_num="0011223344558",
        promoted_year=1924,
        original_hcad_year=1940,
        source_citation="Appears in 1924 Directory",
        source_url="https://cdm17006.contentdm.oclc.org/digital/collection/citydir/id/41000/rec/1",
        standing_props={
            "address": "412 Westheimer Rd",
            "use_category": "Multi-Family",
            "own_tier": "regional_portfolio",
            "port_id": "port_sample_12",
        },
        cama_acct={
            "state_class": "B1",
            "owner_name": "MONTROSE COURTS OWNER",
            "situs_addr": "412 WESTHEIMER RD",
            "mail_addr_1": "900 LOUISIANA ST",
        },
    )
    assert mf_rec["own_tier"] == "regional_portfolio"
    assert mf_rec["port_id"] == "port_sample_12"
    assert "geometry" not in mf_rec


def test_ut3_classify_buckets_deduplication_subset_sort_and_formula_sanitization() -> None:
    """UT-3: Validate Buckets A-F, jurs=='061' filter, deduplication, subset re-sort, and formula safety."""
    # Outside COH (`jurs` missing 061) -> excluded
    assert (
        classify_1927_triage_buckets(
            {
                "hcad_num": "0123456789012",
                "jurs": "001 040 041",
                "atlas_year": 1930,
                "earliest_dir_year": 1915,
                "state_class": "A1",
                "cama_bld_ar": 1500,
                "address": "101 Main St",
            }
        )
        is None
    )

    # Multi-bucket parcel matching Bucket A + Bucket B + formula-injection address/district strings
    rec_ab_1 = classify_1927_triage_buckets(
        {
            "hcad_num": "0023456789012",
            "jurs": "001 061",
            "atlas_year": 1930,
            "earliest_dir_year": 1919,
            "holdback_reasons": ["bld1_concrete_slab"],
            "in_coh_historic_district": True,
            "historic_district": "=Norhill Historic District",
            "contributing": "Contributing",
            "state_class": "A1",
            "cama_bld_ar": 1620,
            "bld1_foundations": ["Crawl Space"],
            "has_bld1_slab": False,
            "address": "815 Norhill Blvd",
        }
    )
    assert rec_ab_1 is not None
    assert rec_ab_1["primary_bucket"] == BUCKET_LABELS["A"]
    assert BUCKET_LABELS["B"] in rec_ab_1["all_buckets"]
    assert rec_ab_1["district_or_neighborhood"] == "=Norhill Historic District"
    assert triage_record_to_sheet_row(rec_ab_1)[5].startswith("'")

    # Bucket C (postwar annexation pier-and-beam) & Bucket D (Inner-Loop pier-and-beam overwrite)
    rec_c = classify_1927_triage_buckets(
        {
            "hcad_num": "0023456789013",
            "jurs": "061",
            "atlas_year": 1950,
            "annex_decade": 1940,
            "state_class": "A1",
            "cama_bld_ar": 1400,
            "bld1_foundations": ["Crawl Space"],
            "neighborhood": "Oak Forest",
            "address": "1202 W 43rd St",
        }
    )
    assert rec_c is not None
    assert BUCKET_LABELS["C"] in rec_c["all_buckets"]

    rec_d = classify_1927_triage_buckets(
        {
            "hcad_num": "0023456789014",
            "jurs": "061",
            "atlas_year": 1962,
            "in_inner_loop": True,
            "plat_year": 1912,
            "plat_name": "Montrose",
            "state_class": "A1",
            "cama_bld_ar": 1900,
            "bld1_foundations": ["Piers 8 ft or Greater"],
            "neighborhood": "Montrose",
            "address": "1411 Harold St",
        }
    )
    assert rec_d is not None
    assert rec_d["primary_bucket"] == BUCKET_LABELS["D"]

    # Bucket E: Historic streetcar corridor Masonry Bearing commercial (1945-1990)
    rec_e = classify_1927_triage_buckets(
        {
            "hcad_num": "0023456789015",
            "jurs": "061",
            "atlas_year": 1958,
            "in_inner_loop": True,
            "state_class": "F1",
            "cama_bld_ar": 4800,
            "structure_dscr": "Masonry Bearing",
            "neighborhood": "Midtown",
            "address": "2410 Main St",
        }
    )
    assert rec_e is not None
    assert rec_e["primary_bucket"] == BUCKET_LABELS["E"]

    # Bucket F: 1920 false-positive audit (1921-1929 exact plat OR absent through 1926 directory)
    rec_f = classify_1927_triage_buckets(
        {
            "hcad_num": "0023456789016",
            "jurs": "061",
            "atlas_year": 1920,
            "in_absent_through_1926": True,
            "plat_name": "River Oaks Sec 2",
            "plat_year": 1925,
            "plat_year_is_exact": True,
            "state_class": "A1",
            "cama_bld_ar": 3400,
            "neighborhood": "River Oaks",
            "address": "2115 River Oaks Blvd",
        }
    )
    assert rec_f is not None
    assert rec_f["primary_bucket"] == BUCKET_LABELS["F"]
    assert rec_f["suggested_max_year"] == 1927

    # Duplicate HCAD merge preserves highest-priority primary_bucket, unions all_buckets, and preserves leading zeros
    dup_extra = dict(rec_d)
    dup_extra["hcad_num"] = "0023456789012"
    dup_extra["all_buckets"] = BUCKET_LABELS["D"]
    dup_extra["primary_bucket"] = BUCKET_LABELS["D"]
    deduped = deduplicate_and_sort_triage_rows([rec_f, rec_e, rec_d, rec_c, rec_ab_1, dup_extra])
    assert len(deduped) == 5
    merged_012 = next(r for r in deduped if r["hcad_num"] == "0023456789012")
    assert merged_012["primary_bucket"] == BUCKET_LABELS["A"]
    assert BUCKET_LABELS["B"] in merged_012["all_buckets"]
    assert BUCKET_LABELS["D"] in merged_012["all_buckets"]

    sheet_row = triage_record_to_sheet_row(merged_012)
    assert len(sheet_row) == len(TRIAGE_HEADERS_A_TO_T) == 20
    assert sheet_row[4] == "0023456789012"
    assert str(sheet_row[5]).startswith("'")

    # Subset selection keeps Bucket A/E/F and re-sorts contiguously by sort_key
    subset = select_sheet_triage_subset(deduped, max_rows=4)
    assert len(subset) == 4
    assert [r["sort_key"] for r in subset] == sorted(r["sort_key"] for r in subset)


def test_ut5_cross_language_python_vs_node_street_walk_sort_parity() -> None:
    """UT-5 & DF-2: Assert 1:1 street-walk tuple & sorted order parity between Python and Node.js."""
    fixtures = [
        {"fid": "f01", "district": "Houston Heights Historic District South", "address": "1412 1/2 Columbia St"},
        {"fid": "f02", "district": "Houston Heights Historic District South", "address": "1411 Columbia St"},
        {"fid": "f03", "district": "Houston Heights Historic District South", "address": "1412A Columbia St"},
        {"fid": "f04", "district": "Houston Heights Historic District South", "address": "1415-1417 Columbia St"},
        {"fid": "f05", "district": "Houston Heights Historic District South", "address": "0 Columbia St"},
        {"fid": "f06", "district": "Freedmen's Town Historic District", "address": "1216-1218 Wilson St"},
        {"fid": "f07", "district": "Freedmen's Town Historic District", "address": "1215 Wilson St Rear"},
        {"fid": "f08", "district": "Outside Historic District", "address": "520 W 19th St"},
        {"fid": "f09", "district": "Undesignated / Outside HD", "address": "519 E 19th St, Houston, TX 77008"},
        {"fid": "f10", "district": "", "address": "711 75th St"},
        {"fid": "f11", "district": "Historic Core", "address": "712 75th St Apt 4"},
        {"fid": "f12", "district": "Main Street Market Square Historic District", "address": "1001 McKinney St / 921 Main St"},
        {"fid": "f13", "district": "Main Street Market Square Historic District", "address": "1002 McKinney Ave Ste 200"},
        {"fid": "f14", "district": "Norhill Historic District", "address": "815 NW Norhill Blvd Garage"},
        {"fid": "f15", "district": "Norhill Historic District", "address": "816 SE Norhill Blvd #B"},
        {"fid": "f16", "district": "Germantown Historic District", "address": "100 North St"},
        {"fid": "f17", "district": "Germantown Historic District", "address": "101 North St"},
        {"fid": "f18", "district": "Old Sixth Ward Protected Historic District", "address": "1802 Washington Ave / 502 Silver St"},
        {"fid": "f19", "district": "Old Sixth Ward Protected Historic District", "address": "1801 Washington Ave"},
        {"fid": "f20", "district": "", "address": ""},
        {"fid": "f21", "district": "", "address": "Bellfort Ave"},
        {"fid": "f22", "district": "Avondale East Historic District", "address": "402 Avondale St Unit 1"},
        {"fid": "f23", "district": "Avondale East Historic District", "address": "401 Avondale St"},
        {"fid": "f24", "district": "First Montrose Commons Historic District", "address": "611 Sul Ross St Bldg B"},
        {"fid": "f25", "district": "First Montrose Commons Historic District", "address": "610 Sul Ross St"},
        {"fid": "f26", "district": "Houston Heights Historic District South", "address": "1412 Columbia St"},
        {"fid": "f27", "district": "Main Street Market Square Historic District", "address": "1001 Main St Building B"},
        {"fid": "f28", "district": "Main Street Market Square Historic District", "address": "1005 Main St"},
        {"fid": "f29", "district": "Undesignated COH", "address": "500 Main St"},
    ]

    py_tuples = [
        list(parse_street_walk_key(f["address"], f["district"], f["fid"]))
        for f in fixtures
    ]
    py_sorted_fids = [
        f["fid"]
        for f in sorted(
            fixtures,
            key=lambda x: parse_street_walk_key(x["address"], x["district"], x["fid"]),
        )
    ]

    node_script = """
const ver = process.env.ACTIVE_CACHE_BUST;
const mod = await import(`./app/js/staffWorkbench.js?v=${ver}`);
const fixtures = JSON.parse(process.env.FIXTURES_JSON);
const tuples = fixtures.map((f) => mod.parseAuditStreetWalkKey(f).tuple);
const sortedFids = mod.sortAuditQueueItems(fixtures, "street_block").map((f) => f.fid);
console.log(JSON.stringify({
  tuples,
  sortedFids,
  dirTokens: [...mod.STREET_WALK_DIR_TOKENS].sort(),
  typeSuffixes: [...mod.STREET_WALK_TYPE_SUFFIXES].sort(),
  unitCutTokens: [...mod.STREET_WALK_UNIT_CUT_TOKENS].sort(),
  undesignatedSentinels: [...mod.UNDESIGNATED_DISTRICT_SENTINELS].sort(),
}));
"""
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", node_script],
        cwd=str(REPO_ROOT),
        env={
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "FIXTURES_JSON": json.dumps(fixtures),
            "ACTIVE_CACHE_BUST": ACTIVE_CACHE_BUST,
        },
        capture_output=True,
        text=True,
        check=True,
    )
    js_out = json.loads(proc.stdout.strip())
    assert js_out["tuples"] == py_tuples
    assert js_out["sortedFids"] == py_sorted_fids
    assert js_out["dirTokens"] == sorted(STREET_WALK_DIR_TOKENS)
    assert js_out["typeSuffixes"] == sorted(STREET_WALK_TYPE_SUFFIXES)
    assert js_out["unitCutTokens"] == sorted(STREET_WALK_UNIT_CUT_TOKENS)
    assert js_out["undesignatedSentinels"] == sorted(UNDESIGNATED_DISTRICT_SENTINELS)
    assert py_tuples[-3][1] == "MAIN"
    assert py_tuples[-1] == ["ZZZ_UNDESIGNATED", "MAIN", 1, 500, "f29"]
    assert py_tuples[4][2:] == [2, STREET_WALK_UNNUMBERED_SENTINEL, "f05"]


def test_it1_it2_it4_rp1_rp2_reconciled_bundles_policy_brief_and_multifootprint_safety() -> None:
    """IT-1, IT-2, IT-4, RP-1 & RP-2: Check reconciled bundles, 4-way policy brief parity, and shard retention."""
    raw_ov = json.loads((DATA_DIR / "curated_overrides.json").read_text(encoding="utf-8"))
    overrides = raw_ov.get("overrides", raw_ov)
    summary = json.loads((DATA_DIR / "historical_audit_summary.json").read_text(encoding="utf-8"))
    stats_summary = json.loads((DATA_DIR / "stats_summary.json").read_text(encoding="utf-8"))
    queue_bytes = (DATA_DIR / "historical_audit_queue.json").stat().st_size
    queue_doc = json.loads((DATA_DIR / "historical_audit_queue.json").read_text(encoding="utf-8"))
    scorecards = json.loads((DATA_DIR / "neighborhood_scorecards.json").read_text(encoding="utf-8"))
    active_permits = json.loads((DATA_DIR / "active_permit_alerts.json").read_text(encoding="utf-8"))

    # IT-1: Zero LOST_HOUSTON_EXTANT_COLLISION findings & < 995 KB queue target
    cat_counts = summary.get("contradictions_by_category") or {}
    assert int(cat_counts.get("LOST_HOUSTON_EXTANT_COLLISION", 0)) == 0
    assert queue_bytes < 995_000
    assert queue_doc["total"] >= 2600
    assert queue_doc["total"] == len(queue_doc["items"]) == summary["total_queued_contradictions"]
    assert stats_summary.get("audit_queue_total") == queue_doc["total"]
    assert "1930_PLACEHOLDER_PRE1927_PLAT" in queue_doc["defaults"]["actions"]
    assert "1930_PLACEHOLDER_PRE1927_PLAT" in queue_doc["defaults"]["sources"]
    assert queue_doc["defaults"].get("plat_1930_claim")

    # Byte-cap enforcement test on synthetic oversized payload (>= 995,000 B)
    with pytest.raises(ValueError, match="exceeded 995,000-byte target cap"):
        build_historical_audit_queue_payload(
            [
                {
                    "finding_id": f"plat_1930:{i:013d}",
                    "priority_score": 90,
                    "category": "1930_PLACEHOLDER_PRE1927_PLAT",
                    "hcad_num": f"{i:013d}",
                    "address": f"{100 + i} Heights Blvd",
                    "lng": -95.39,
                    "lat": 29.79,
                    "atlas_year": 1930,
                    "hcad_year": 1930,
                    "suggested_year": 0,
                    "displayed_claim": "Custom claim text " + ("y" * 120),
                    "conflicting_evidence": "x" * 1200,
                }
                for i in range(1, 900)
            ]
        )

    # RP-1 & UT-1: Reserved fixture, Non-Contributing replacements, and uncorroborated pre-1911 hits absent from curated_overrides.json
    for must_exclude_hcad in ("0200670000010", "0102490000006", "0202160000012", "0090860000002"):
        assert must_exclude_hcad not in overrides
    q_020067 = [it for it in queue_doc["items"] if it.get("h") == "0200670000010"]
    assert any(it.get("cat") == "CITY_DIRECTORY_EARLIER_THAN_HCAD" for it in q_020067)

    # UT-2 & RP-2: Every Milestone 7 auto-promoted override omits geometry/replace_parcel_shards & has clean ASCII
    promoted = [
        (k, v)
        for k, v in overrides.items()
        if isinstance(v, dict)
        and v.get("verified_by") == "Archival City Directory Cross-Check (Double-Vetted)"
    ]
    assert len(promoted) >= 80
    promoted_map = dict(promoted)
    for k, v in promoted:
        assert len(k) == 13 and k.isdigit()
        assert "geometry" not in v
        assert "replace_parcel_shards" not in v
        assert 1836 <= int(v["year_built"]) <= 1926
        assert int(v["original_hcad_year"]) >= 1928
        assert v["own_tier"] in {
            "homestead",
            "local_small",
            "regional_portfolio",
            "institutional_pe",
            "anonymity_shield",
            "civic_nonprofit",
        }
        if v.get("use_category") in ("Commercial", "Industrial", "Civic / Institutional"):
            assert v["own_tier"] != "homestead", f"Non-residential parcel {k} defaulted to homestead: {v}"
        assert "\u2014" not in v.get("source_citation", "")
        assert "\u2013" not in v.get("source_citation", "")
        assert "/digital/collection/citydir/id/" in v.get("source_url", "")

    # IT-1: buildings.geojson and search_index.json reflect promoted pre-1927 overrides consistently
    bgeo = json.loads((DATA_DIR / "buildings.geojson").read_text(encoding="utf-8"))
    assert len(bgeo.get("features", [])) > 18000
    matched_bgeo = 0
    for feat in bgeo["features"]:
        fp = feat.get("properties") or {}
        hc = str(fp.get("hcad_num") or "").strip()
        if hc in promoted_map and not fp.get("suppress_only"):
            matched_bgeo += 1
            assert int(fp["year_built"]) == int(promoted_map[hc]["year_built"])
            assert int(fp["decade"]) == int(promoted_map[hc]["decade"])
            assert fp.get("is_curated_override") is True
    assert matched_bgeo >= 40

    sidx = json.loads((DATA_DIR / "search_index.json").read_text(encoding="utf-8"))
    matched_sidx = 0
    for srow in sidx:
        if not isinstance(srow, dict) or srow.get("overlay_layer"):
            continue
        hc = str(srow.get("hcad_num") or "").strip()
        if hc in promoted_map:
            matched_sidx += 1
            exp_yr = int(promoted_map[hc]["year_built"])
            assert int(srow["year_built"]) == exp_yr
            assert int(srow["decade"]) == int(promoted_map[hc]["decade"])
            if str(srow.get("category") or "").startswith("Built "):
                assert srow["category"] == f"Built {exp_yr}"
            if "Built " in str(srow.get("sublabel") or ""):
                assert f"Built {exp_yr}" in str(srow["sublabel"])
    assert matched_sidx >= 80

    # IT-2: 4-way policy brief parity (scorecards == fetch_live_permits == active_permit_alerts == platMonitorHelpers.js)
    sc_brief = scorecards["citywide_policy_brief"]
    assert DEFAULT_POLICY_IMPACT_BRIEF == sc_brief
    assert get_policy_impact_brief() == sc_brief
    assert active_permits["annual_benchmarks"]["policy_impact_brief"] == sc_brief
    assert sc_brief["local_designation_coverage"]["excludes_heritage_districts"] is True
    c100_bucket = next(r for r in sc_brief["standing_buildings_by_age"] if r["id"] == "100_plus")
    assert c100_bucket["standing_count"] >= 13686

    # Local cache CSV check (runs whenever gitignored pipeline/cache/triage_1927_cutoff_coh.csv is present on disk)
    csv_path = CACHE_DIR / "triage_1927_cutoff_coh.csv"
    if csv_path.exists():
        with csv_path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.reader(fh)
            header = next(reader)
            assert header == TRIAGE_HEADERS_A_TO_T
            seen_hcads: set[str] = set()
            bucket_hits: set[str] = set()
            in_district_unverified_hits = 0
            for row in reader:
                assert len(row) == 20
                hcad = row[4]
                assert len(hcad) == 13 and hcad.isdigit()
                assert hcad not in seen_hcads
                seen_hcads.add(hcad)
                if "(In District)" in row[16]:
                    in_district_unverified_hits += 1
                for lbl in BUCKET_LABELS.values():
                    if lbl in row[2]:
                        bucket_hits.add(lbl)
            assert len(seen_hcads) >= 31800
            assert in_district_unverified_hits >= 10
            assert bucket_hits == set(BUCKET_LABELS.values())


def test_it3_rp4_df1_13tab_google_sheet_batches_sibling_sync_and_b6_isolation(tmp_path: Path) -> None:
    """IT-3, DF-1 & RP-4: Verify centralized 13-tab layout, Row 20/21 non-interference, SOP 16, and B6 guard."""
    assert SID_TRIAGE_1927 == 927192701
    assert HUB_SURVEYS_ROW == 19
    assert HUB_TRIAGE_1927_ROW == 20
    assert HUB_HOW_TO_USE_ROW == 21
    assert HUB_FILTER_INPUT_ROW == 23
    assert HUB_RESULTS_FORMULA_ROW == 26
    assert SOP15_TARGET_ROW == 26
    assert SOP16_TARGET_ROW == 27
    assert len(ALL_TRIAGE_HEADERS) == 22

    # 1. Baseline 13-tab setup_admin_sheet hub/instructions rows & CLI batch generation check
    hub_rows = build_admin_hub_rows()
    instr_rows = build_instructions_rows()
    assert "Walk-the-Block Field Surveys" in hub_rows[HUB_SURVEYS_ROW - 1][0]
    assert "1927 (100-Yr) Cutoff" in hub_rows[HUB_TRIAGE_1927_ROW - 1][0]
    assert hub_rows[HUB_HOW_TO_USE_ROW - 1][0].startswith("HOW TO USE:")
    assert "Passkey" in instr_rows[5][0] and "B6" in instr_rows[5][0]

    tmp_admin = tmp_path / "admin_out"
    setup_admin_sheet_main(["--output-dir", str(tmp_admin)])
    assert (tmp_admin / "admin_values_batch.json").exists()
    assert (tmp_admin / "admin_raw_batch.json").exists()

    # 2. Sibling sync (`sync_field_surveys_sheet.py`) writes HOW TO USE to Row 21 (NOT Row 20!)
    m3_batches = build_milestone3_sheet_batches([], include_add_sheet=False, include_hub_insert=False)
    m3_ranges = [
        op["range"]
        for op in (m3_batches["raw_values_batch"] + m3_batches["formula_values_batch"])
    ]
    assert f"Admin_Hub_&_Quick_Filter!A{HUB_HOW_TO_USE_ROW}" in m3_ranges
    assert not any(
        r.startswith(f"Admin_Hub_&_Quick_Filter!A{HUB_TRIAGE_1927_ROW}")
        for r in m3_ranges
    ), f"sync_field_surveys_sheet.py clobbered Row 20: {m3_ranges}"

    # 3. Milestone 7 `build_1927_triage_sheet_batches` DDL, RAW, USER_ENTERED, and 7 Filter Views
    sample_rec = classify_1927_triage_buckets(
        {
            "hcad_num": "0012345678901",
            "jurs": "061",
            "atlas_year": 1930,
            "earliest_dir_year": 1915,
            "holdback_reasons": ["bld1_concrete_slab"],
            "state_class": "A1",
            "cama_bld_ar": 1800,
            "address": "803 Columbia St",
            "neighborhood": "Houston Heights",
        }
    )
    assert sample_rec is not None
    sample_row = triage_record_to_sheet_row(sample_rec)
    m7_batches = build_1927_triage_sheet_batches(
        [sample_row],
        include_add_sheet=True,
        include_hub_insert=True,
        expand_instr_rows=True,
        replace_filter_views=True,
    )
    ddl_reqs = m7_batches["create_raw_batch"]["requests"]
    assert any(
        r.get("addSheet", {}).get("properties", {}).get("sheetId") == SID_TRIAGE_1927
        for r in ddl_reqs
    )
    assert any(
        r.get("insertDimension", {}).get("range", {}).get("startIndex") == HUB_TRIAGE_1927_ROW - 1
        for r in ddl_reqs
    )
    assert any("appendDimension" in r for r in ddl_reqs)

    fvs = [
        r["addFilterView"]["filter"]["filterViewId"]
        for r in m7_batches["style_raw_batch"]["requests"]
        if "addFilterView" in r
    ]
    assert fvs == [927192702, 927192703, 927192704, 927192705, 927192706, 927192707, 927192708]
    assert len(fvs) == TRIAGE_1927_FILTER_VIEW_COUNT == 7

    sop16 = build_sop16_row()
    assert len(sop16) == 3
    assert "SOP 16" in sop16[0]
    assert all("\u2014" not in cell and "\u2013" not in cell for cell in sop16)

    # 4. Anchor verification across BOTH 12-tab pre-upgrade and 13-tab upgraded states (`IT-3`)
    base_tabs_12 = {SID_LOST: "Lost_Houston_Catalog", SID_SURVEYS: SHEET_TITLE_SURVEYS}
    hub_12 = [""] * 25
    hub_12[17] = "Lost Houston Demolished Buildings Catalog"
    hub_12[18] = "Walk-the-Block Field Surveys Logged"
    hub_12[19] = "HOW TO USE: Select a Triage Category..."
    instr_col_a = [""] * 27
    instr_col_a[4] = "Google Apps Script Code (Cell B5)"
    instr_col_a[5] = "Sheet Editor Admin Passkey (Cell B6)"
    instr_col_a[24] = "SOP 14: Lost Houston Catalog"
    instr_col_a[25] = "SOP 15: Preservation Staff & Field Workbench"

    anchors_12 = verify_1927_triage_sheet_anchors(
        tab_id_to_title=base_tabs_12,
        hub_col_a=hub_12,
        instr_col_a=instr_col_a,
        instr_grid_rows=27,
    )
    assert anchors_12 == {
        "need_add_sheet": True,
        "need_hub_insert": True,
        "need_instr_expand": True,
    }

    tabs_13 = {**base_tabs_12, SID_TRIAGE_1927: SHEET_TITLE_TRIAGE_1927}
    hub_13 = list(hub_12)
    hub_13[19] = "1927 (100-Yr) Cutoff Undercount Triage"
    hub_13[20] = "HOW TO USE: Select a Triage Category..."
    instr_13 = list(instr_col_a)
    instr_13[26] = "SOP 16: 1927 (100-Year Cutoff) Triage"
    anchors_13 = verify_1927_triage_sheet_anchors(
        tab_id_to_title=tabs_13,
        hub_col_a=hub_13,
        instr_col_a=instr_13,
        instr_grid_rows=60,
    )
    assert anchors_13 == {
        "need_add_sheet": False,
        "need_hub_insert": False,
        "need_instr_expand": False,
    }
    assert verify_live_sheet_anchors(
        tab_id_to_title=tabs_13,
        hub_col_a=hub_13,
        instr_col_a=instr_13,
    ) == {"need_add_sheet": False, "need_hub_insert": False}

    # 5. `assert_safe_workbook_write_ranges` strictly blocks Cell B6, Pending_Submissions S:V, and dashes
    with pytest.raises(ValueError, match="Instructions_&_Apps_Script"):
        assert_safe_workbook_write_ranges(
            [{"op": "write", "range": "Instructions_&_Apps_Script!A6:B6", "data": [["x", "secret"]]}],
            allow_baseline_b6=False,
        )
    with pytest.raises(ValueError, match="Pending_Submissions"):
        assert_safe_workbook_write_ranges(
            [{"op": "write", "range": "Pending_Submissions!S2:V5", "data": [["x"]]}],
            allow_baseline_b6=False,
        )
    with pytest.raises(ValueError, match="Typographic dash"):
        assert_safe_workbook_write_ranges(
            [{"op": "write", "range": "1927_Cutoff_Triage!A1:A1", "data": [["bad\u2014dash"]]}],
            allow_baseline_b6=False,
        )

    # 6. `sync_policy_impact_brief_constants` idempotency & hermetic `active_permit_alerts.json` isolation
    tmp_sc = tmp_path / "scorecards.json"
    tmp_py = tmp_path / "fetch_live_permits.py"
    tmp_js = tmp_path / "platMonitorHelpers.js"
    tmp_alerts = tmp_path / "active_permit_alerts.json"
    prod_alerts_path = DATA_DIR / "active_permit_alerts.json"
    prod_alerts_before_bytes = prod_alerts_path.read_bytes()
    prod_alerts_before_mtime = prod_alerts_path.stat().st_mtime_ns

    sc_copy = json.loads((DATA_DIR / "neighborhood_scorecards.json").read_text(encoding="utf-8"))
    tmp_sc.write_text(json.dumps(sc_copy), encoding="utf-8")
    tmp_alerts.write_text(
        json.dumps({"alerts": [], "annual_benchmarks": {"policy_impact_brief": {"stale": True}}}),
        encoding="utf-8",
    )
    tmp_py.write_text(
        (REPO_ROOT / "pipeline" / "atlas_pipeline" / "fetch_live_permits.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    tmp_js.write_text((REPO_ROOT / "app" / "js" / "platMonitorHelpers.js").read_text(encoding="utf-8"), encoding="utf-8")

    synced = sync_policy_impact_brief_constants(
        scorecards_path=tmp_sc,
        fetch_live_permits_path=tmp_py,
        plat_helpers_path=tmp_js,
    )
    assert prod_alerts_path.stat().st_mtime_ns == prod_alerts_before_mtime
    assert prod_alerts_path.read_bytes() == prod_alerts_before_bytes
    updated_tmp_alerts = json.loads(tmp_alerts.read_text(encoding="utf-8"))
    assert updated_tmp_alerts["annual_benchmarks"]["policy_impact_brief"] == synced

    c100_synced = next(r for r in synced["standing_buildings_by_age"] if r["id"] == "100_plus")
    assert c100_synced["standing_count"] == 13686
    assert "\u2014" not in tmp_py.read_text(encoding="utf-8")
    assert "\u2014" not in tmp_js.read_text(encoding="utf-8")
    assert "# BEGIN DEFAULT_POLICY_IMPACT_BRIEF" in tmp_py.read_text(encoding="utf-8")
    assert "// BEGIN DEFAULT_POLICY_IMPACT_BRIEF" in tmp_js.read_text(encoding="utf-8")


def test_review_pass1_regressions_own_tier_bucket_e_idempotent_sheet_and_shared_fnd(tmp_path: Path) -> None:
    """Verify fixes for countywide own_tier preservation, Bucket E multi-bucket retention, idempotent Sheet sync, Row 20 guard, and shared CAMA FND parser."""
    # 1. Countywide `standing_tier` (`local_small`, `anonymity_shield`) preserved over single-row `cama_tier = homestead`
    ov_ls = build_promoted_override_record(
        hcad_num="0051440000008",
        promoted_year=1915,
        original_hcad_year=1930,
        source_citation="Appears in 1915 Morrison & Fourmy Houston City Directory",
        source_url="https://cdm17006.contentdm.oclc.org/digital/collection/citydir/id/15000/rec/1",
        standing_props={
            "address": "2005 SHEARN ST",
            "own_tier": "local_small",
            "port_id": "",
            "use_category": "Residential",
        },
        cama_acct={
            "state_class": "A1",
            "owner_name": "BRIONES IRENE",
            "situs_addr": "2005 SHEARN ST",
            "mail_addr_1": "3721 MOORE ST",
            "mail_state": "TX",
        },
        updated_at="2026-11-06",
    )
    assert ov_ls["own_tier"] == "local_small"

    ov_anon = build_promoted_override_record(
        hcad_num="0373010000002",
        promoted_year=1912,
        original_hcad_year=1930,
        source_citation="Appears in 1912 Morrison & Fourmy Houston City Directory",
        source_url="https://cdm17006.contentdm.oclc.org/digital/collection/citydir/id/13658/rec/1",
        standing_props={
            "address": "710 WOODLAND ST",
            "own_tier": "anonymity_shield",
            "port_id": "p_anon",
            "use_category": "Residential",
        },
        cama_acct={
            "state_class": "A1",
            "owner_name": "CURRENT OWNER",
            "situs_addr": "710 WOODLAND ST",
            "mail_addr_1": "PO BOX 7135",
            "mail_state": "TX",
        },
        updated_at="2026-11-06",
    )
    assert ov_anon["own_tier"] == "anonymity_shield"
    assert ov_anon["port_id"] == "p_anon"

    # Verify live `curated_overrides.json` and `buildings.geojson` on the 3 repaired parcels
    live_ov = json.loads((DATA_DIR / "curated_overrides.json").read_text(encoding="utf-8"))["overrides"]
    assert live_ov["0051440000008"]["own_tier"] == "local_small"
    assert live_ov["0201570000009"]["own_tier"] == "local_small"
    assert live_ov["0373010000002"]["own_tier"] == "anonymity_shield"

    live_bgeo = json.loads((DATA_DIR / "buildings.geojson").read_text(encoding="utf-8"))["features"]
    bgeo_by_hcad: dict[str, list[dict[str, Any]]] = {}
    for feat in live_bgeo:
        props = feat.get("properties") or {}
        hc = str(props.get("hcad_num") or "").strip()
        if hc in ("0051440000008", "0201570000009", "0373010000002"):
            bgeo_by_hcad.setdefault(hc, []).append(props)
    assert all(p["own_tier"] == "local_small" for p in bgeo_by_hcad.get("0051440000008", []))
    assert all(p["own_tier"] == "local_small" for p in bgeo_by_hcad.get("0201570000009", []))
    assert all(p["own_tier"] == "anonymity_shield" for p in bgeo_by_hcad.get("0373010000002", []))

    # 2. Multi-bucket Bucket E & moderator-reviewed rows are 100% retained under `max_rows` truncation
    shadowed_bucket_e = {
        "hcad_num": "0010000000001",
        "primary_bucket": BUCKET_LABELS["D"],
        "all_buckets": f"{BUCKET_LABELS['D']} | {BUCKET_LABELS['E']}",
        "priority_score": 72,
        "in_inner_loop": True,
        "bucket_count": 2,
        "review_status": "Unreviewed",
        "reviewer_notes": "",
        "sort_key": ("ZZZ_UNDESIGNATED", "MAIN", 0, 1001, "0010000000001"),
    }
    reviewed_bucket_b = {
        "hcad_num": "0010000000002",
        "primary_bucket": BUCKET_LABELS["B"],
        "all_buckets": BUCKET_LABELS["B"],
        "priority_score": 60,
        "in_inner_loop": False,
        "bucket_count": 1,
        "review_status": "Needs Field Visit",
        "reviewer_notes": "Check porch framing",
        "sort_key": ("ZZZ_UNDESIGNATED", "MAIN", 0, 1003, "0010000000002"),
    }
    high_score_pool = [
        {
            "hcad_num": f"001000000010{idx}",
            "primary_bucket": BUCKET_LABELS["B"],
            "all_buckets": BUCKET_LABELS["B"],
            "priority_score": 95,
            "in_inner_loop": True,
            "bucket_count": 1,
            "review_status": "Unreviewed",
            "reviewer_notes": "",
            "sort_key": ("ZZZ_UNDESIGNATED", "MAIN", 1, 1000 + idx * 2, f"001000000010{idx}"),
        }
        for idx in range(5)
    ]
    subset = select_sheet_triage_subset(
        [shadowed_bucket_e, reviewed_bucket_b, *high_score_pool],
        max_rows=3,
    )
    kept_hcads = {r["hcad_num"] for r in subset}
    assert "0010000000001" in kept_hcads
    assert "0010000000002" in kept_hcads
    assert len(subset) == 3

    # 3. Moderator CSV merge + idempotent re-sync Sheet batch defaults (`clear` + `deleteFilterView` + zero `addSheet`)
    sample_csv = (
        "review_status,primary_bucket,hcad_num,reviewer_notes\n"
        "Confirmed Pre-1927,1. Pre-1927 City Directory Hold-Back,'0010000000001,Sanborn Vol 2 Sheet 208 match\n"
        "Unreviewed,2. Pre-1925 Plat Placeholder,0010000000002,\n"
    )
    parsed_statuses = load_existing_triage_csv_statuses(sample_csv)
    assert parsed_statuses == {
        "0010000000001": {
            "review_status": "Confirmed Pre-1927",
            "reviewer_notes": "Sanborn Vol 2 Sheet 208 match",
        }
    }
    merged_rows = merge_existing_triage_statuses([dict(shadowed_bucket_e)], parsed_statuses)
    assert merged_rows[0]["review_status"] == "Confirmed Pre-1927"
    assert merged_rows[0]["reviewer_notes"] == "Sanborn Vol 2 Sheet 208 match"

    idempotent_batches = build_1927_triage_sheet_batches(
        [],
        include_add_sheet=False,
        include_hub_insert=False,
        expand_instr_rows=False,
        replace_filter_views=True,
    )
    assert not any("addSheet" in r for r in idempotent_batches["create_raw_batch"]["requests"])
    assert not any("insertDimension" in r for r in idempotent_batches["create_raw_batch"]["requests"])
    assert idempotent_batches["raw_values_batch"][0] == {
        "op": "clear",
        "range": f"{SHEET_TITLE_TRIAGE_1927}!A2:T",
    }
    style_reqs = idempotent_batches["style_raw_batch"]["requests"]
    del_fvs = [r["deleteFilterView"]["filterId"] for r in style_reqs if "deleteFilterView" in r]
    add_fvs = [r["addFilterView"]["filter"]["filterViewId"] for r in style_reqs if "addFilterView" in r]
    assert del_fvs == add_fvs == [927192702, 927192703, 927192704, 927192705, 927192706, 927192707, 927192708]

    # 4. `resolve_hub_filter_input_row` & `build_milestone3_sheet_batches` Row 20 clobber guard
    hub_13 = [r[0] for r in build_admin_hub_rows()]
    hub_12 = hub_13[:19] + hub_13[20:]
    assert resolve_hub_filter_input_row(hub_13) == 23
    assert resolve_hub_filter_input_row(hub_12) == 22
    with pytest.raises(ValueError, match="Refusing to clobber Row 20"):
        build_milestone3_sheet_batches([], hub_filter_input_row=22)
    legacy_batches = build_milestone3_sheet_batches([], hub_filter_input_row=22, allow_legacy_12tab=True)
    assert any(
        op["range"] == "Admin_Hub_&_Quick_Filter!A20"
        for op in legacy_batches["formula_values_batch"]
    )

    # 5. Shared `load_bld1_structural_flags` parses commercial `building_other.txt` + `structural_elem1.txt` and raises on corrupt zip
    valid_zip = tmp_path / "Real_building_land.zip"
    with zipfile.ZipFile(valid_zip, "w") as zf:
        zf.writestr(
            "building_other.txt",
            "\t".join([f"c{i}" for i in range(19)]) + "\n"
            + "\t".join(
                [
                    "0011223344556",
                    "C",
                    "1",
                    "4300",
                    "4300",
                    "MB",
                    "Masonry Bearing",
                    "C",
                    "100",
                    "1.0",
                    "Avg",
                    "1",
                    "1",
                    "1",
                    "1930",
                    "1930",
                    "1985",
                    "2026",
                    "4200",
                ]
            )
            + "\n",
        )
        zf.writestr(
            "structural_elem1.txt",
            "acct\tbld_num\tcode\tadj\ttype\ttype_dscr\tcategory_dscr\n"
            "0099887766554\t1\tFND\t1\tFND\tFoundation\tPier and Beam\n"
            "0099887766553\t1\tSL\t1\tSL\tFoundation\tConcrete Slab\n"
            "0099887766553\t1\tCAD\t1\tCAD\tRemodel\tNew / Rebuilt\n",
        )
    parsed_flags = load_bld1_structural_flags(
        valid_zip,
        {"0011223344556", "0099887766554", "0099887766553"},
    )
    assert "Masonry Bearing" in ALLOWED_PREWAR_FOUNDATIONS
    assert "Masonry Bearing" in parsed_flags["fnds_by_hcad"]["0011223344556"]
    assert parsed_flags["meta_by_hcad"]["0011223344556"] == (1930, 1985, "Masonry Bearing", True)
    assert "Pier and Beam" in parsed_flags["fnds_by_hcad"]["0099887766554"]
    assert "0099887766553" in parsed_flags["slab_hcads"]
    assert "0099887766553" in parsed_flags["rebuilt_hcads"]

    corrupt_zip = tmp_path / "corrupt.zip"
    corrupt_zip.write_bytes(b"not-a-valid-zip-archive")
    with pytest.raises(zipfile.BadZipFile):
        load_bld1_structural_flags(corrupt_zip, {"0011223344556"})


def test_review_pass2_regressions(tmp_path: Path) -> None:
    """Verify all Pass-2 Quality Agent review fixes across Python pipeline, schema, and Sheet CLIs."""
    # 1. COH Historic District unverified/blank contributing status -> '(In District)' & score 84 (vs 88 for Contributing)
    unverified_hd_rec = classify_1927_triage_buckets(
        {
            "hcad_num": "0200670000881",
            "jurs": "061",
            "already_overridden": False,
            "in_lost_houston": False,
            "is_demolished_landmark": False,
            "state_class": "A1",
            "cama_bld_ar": 1950.0,
            "atlas_year": 1930,
            "eff_year": 1930,
            "yr_remodel": 0,
            "structure_dscr": "One-Story Residential",
            "is_commercial_or_multifamily": False,
            "contributing": "",
            "in_coh_historic_district": True,
            "historic_district": "Houston Heights Historic District East",
            "neighborhood": "Houston Heights",
            "super_neighborhood": "GREATER HEIGHTS",
            "address": "1612 Harvard St",
            "bld1_foundations": ["Crawl Space"],
            "has_bld1_slab": False,
            "has_bld1_new_rebuilt": False,
            "plat_name": "HOUSTON HEIGHTS",
            "plat_year": 1892,
            "plat_year_is_exact": True,
            "plat_year_basis": "explicit_name_year",
            "annex_decade": 1910,
            "annex_label": "1918 Annexation",
            "in_inner_loop": True,
            "earliest_dir_year": 0,
            "in_absent_through_1926": False,
            "holdback_reasons": [],
            "has_pre1927_secondary_bldg": False,
            "landmark_status": "",
            "archival_url": "",
        }
    )
    assert unverified_hd_rec is not None
    assert unverified_hd_rec["coh_historic_status"] == "Houston Heights Historic District East (In District)"
    assert unverified_hd_rec["priority_score"] == 84

    verified_hd_rec = classify_1927_triage_buckets(
        {
            "hcad_num": "0200670000882",
            "jurs": "061",
            "already_overridden": False,
            "in_lost_houston": False,
            "is_demolished_landmark": False,
            "state_class": "A1",
            "cama_bld_ar": 1950.0,
            "atlas_year": 1930,
            "eff_year": 1930,
            "yr_remodel": 0,
            "structure_dscr": "One-Story Residential",
            "is_commercial_or_multifamily": False,
            "contributing": "Contributing",
            "in_coh_historic_district": True,
            "historic_district": "Houston Heights Historic District East",
            "neighborhood": "Houston Heights",
            "super_neighborhood": "GREATER HEIGHTS",
            "address": "1614 Harvard St",
            "bld1_foundations": ["Crawl Space"],
            "has_bld1_slab": False,
            "has_bld1_new_rebuilt": False,
            "plat_name": "HOUSTON HEIGHTS",
            "plat_year": 1892,
            "plat_year_is_exact": True,
            "plat_year_basis": "explicit_name_year",
            "annex_decade": 1910,
            "annex_label": "1918 Annexation",
            "in_inner_loop": True,
            "earliest_dir_year": 0,
            "in_absent_through_1926": False,
            "holdback_reasons": [],
            "has_pre1927_secondary_bldg": False,
            "landmark_status": "",
            "archival_url": "",
        }
    )
    assert verified_hd_rec is not None
    assert verified_hd_rec["coh_historic_status"] == "Houston Heights Historic District East (Contributing)"
    assert verified_hd_rec["priority_score"] == 88

    # 2. Shared schema helpers: collect_overridden_hcad_accounts & match_earliest_platted_subdivisions
    extracted_hcads = collect_overridden_hcad_accounts(
        {
            "0610870390001#b1": {"year_built": 1912},
            "custom_campus_key": {"hcad_num": "0200670000099"},
            "non_hcad_entry": {"hcad_num": "INVALID"},
        }
    )
    assert extracted_hcads == {"0610870390001", "0200670000099"}

    # Self-intersecting bowtie polygon (repaired via make_valid) + boundary-collinear point (intersects)
    bowtie_subdivisions = [
        {
            "type": "Feature",
            "properties": {
                "name": "BOWTIE ADDITION",
                "estimated_plat_year": 1908,
                "plat_year_is_exact": True,
                "plat_year_basis": "explicit_name_year",
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [-95.40, 29.75],
                        [-95.38, 29.77],
                        [-95.40, 29.77],
                        [-95.38, 29.75],
                        [-95.40, 29.75],
                    ]
                ],
            },
        },
        {
            "type": "Feature",
            "properties": {
                "name": "LATER OVERLAP ADDITION",
                "estimated_plat_year": 1923,
                "plat_year_is_exact": False,
                "plat_year_basis": "pre1926_building_p15",
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [-95.41, 29.74],
                        [-95.37, 29.74],
                        [-95.37, 29.78],
                        [-95.41, 29.78],
                        [-95.41, 29.74],
                    ]
                ],
            },
        },
    ]
    matched_subs = match_earliest_platted_subdivisions(
        [
            (-95.39, 29.75),  # directly on southern boundary edge of BOWTIE ADDITION
            (-95.375, 29.745),  # strictly inside LATER OVERLAP ADDITION only
            (-95.50, 29.85),  # outside all subdivisions
        ],
        bowtie_subdivisions,
    )
    assert matched_subs[0] == ("BOWTIE ADDITION", 1908, True, "explicit_name_year")
    assert matched_subs[1] == ("LATER OVERLAP ADDITION", 1923, False, "pre1926_building_p15")
    assert matched_subs[2] == ("", 0, False, "")

    # 3. Out-of-district 1930_PLACEHOLDER_PRE1927_PLAT finding never leaks plat name into `d`
    q_payload = build_historical_audit_queue_payload(
        [
            {
                "finding_id": "1930_PLACEHOLDER_PRE1927_PLAT:0610990000012",
                "priority_score": 82,
                "category": "1930_PLACEHOLDER_PRE1927_PLAT",
                "hcad_num": "0610990000012",
                "building_id": "0610990000012",
                "address": "1411 Harold St",
                "lng": -95.3965,
                "lat": 29.7418,
                "atlas_year": 1930,
                "hcad_year": 1930,
                "suggested_year": 1926,
                "district_or_context": "Outside Historic District",
                "contributing": "Outside Historic District",
                "displayed_claim": "HCAD Year Built: 1930 (Rounded Decade Placeholder)",
                "conflicting_evidence": "Sits inside MANDEL PLACE (platted 1912); Crawl Space foundation.",
                "primary_source_url": "",
                "cdm_pointer": 0,
            }
        ],
        generated_on="2026-11-06",
    )
    assert len(q_payload["items"]) == 1
    q_item = q_payload["items"][0]
    assert "d" not in q_item
    assert "cnt" not in q_item
    assert "cl" not in q_item  # deduplicated via defaults.plat1930Claim

    # 4. Runtime get_policy_impact_brief reads scorecards JSON dynamically and falls back cleanly
    custom_sc = tmp_path / "custom_scorecards.json"
    custom_sc.write_text(
        json.dumps(
            {
                "citywide_policy_brief": {
                    "generated_on": "2026-11-06",
                    "standing_buildings_by_age": [{"id": "100_plus", "standing_count": 13687}],
                    "custom_probe": 13687,
                }
            }
        ),
        encoding="utf-8",
    )
    loaded_brief = get_policy_impact_brief(custom_sc)
    assert loaded_brief["custom_probe"] == 13687
    incomplete_sc = tmp_path / "incomplete_scorecards.json"
    incomplete_sc.write_text(json.dumps({"citywide_policy_brief": {}}), encoding="utf-8")
    assert (
        get_policy_impact_brief(incomplete_sc)["local_designation_coverage"]["excludes_heritage_districts"]
        is True
    )
    fallback_brief = get_policy_impact_brief(tmp_path / "missing_scorecards.json")
    assert fallback_brief["local_designation_coverage"]["excludes_heritage_districts"] is True

    # 5. CLI anchor wiring in sync_field_surveys_sheet.py (`--hub-anchors-json` & `--allow-legacy-12tab`)
    hub_rows_13 = build_admin_hub_rows()
    hub_col_a_13 = [str(r[0] or "") if r else "" for r in hub_rows_13]
    anchors_13_path = tmp_path / "hub_anchors_13.json"
    anchors_13_path.write_text(json.dumps({"hub_col_a": hub_col_a_13}), encoding="utf-8")
    out_13_dir = tmp_path / "fs_batches_13"
    sync_field_surveys_main(
        [
            "--output-dir",
            str(out_13_dir),
            "--hub-anchors-json",
            str(anchors_13_path),
        ]
    )
    fs_formula_13 = json.loads((out_13_dir / "field_surveys_formula_values_batch.json").read_text(encoding="utf-8"))
    assert any(op["range"] == f"Admin_Hub_&_Quick_Filter!A{HUB_HOW_TO_USE_ROW}" for op in fs_formula_13)

    # Legacy 12-tab anchor auto-resolves hub_filter_input_row=22 without clobbering error
    hub_col_a_12 = [v for idx, v in enumerate(hub_col_a_13, start=1) if idx != HUB_TRIAGE_1927_ROW]
    anchors_12_path = tmp_path / "hub_anchors_12.json"
    anchors_12_path.write_text(json.dumps({"hub_col_a": hub_col_a_12}), encoding="utf-8")
    out_12_dir = tmp_path / "fs_batches_12"
    sync_field_surveys_main(
        [
            "--output-dir",
            str(out_12_dir),
            "--hub-anchors-json",
            str(anchors_12_path),
        ]
    )
    fs_formula_12 = json.loads((out_12_dir / "field_surveys_formula_values_batch.json").read_text(encoding="utf-8"))
    assert any(op["range"] == "Admin_Hub_&_Quick_Filter!A20" for op in fs_formula_12)

    # 6. verify_1927_triage_sheet_anchors + expand_instr_rows wiring in build_1927_triage_sheet_batches
    instr_col_a = [str(r[0] or "") if r else "" for r in build_instructions_rows()]
    instr_col_a_12 = (instr_col_a + [""] * 26)[:24] + ["SOP 14: Lost Houston", "SOP 15: Field Surveys"]
    anchor_flags = verify_1927_triage_sheet_anchors(
        tab_id_to_title={SID_LOST: "Lost_Houston_Catalog", SID_SURVEYS: "Field_Surveys"},
        hub_col_a=hub_col_a_12,
        instr_col_a=instr_col_a_12,
        instr_grid_rows=26,
    )
    assert anchor_flags == {
        "need_add_sheet": True,
        "need_hub_insert": True,
        "need_instr_expand": True,
    }
    expanded_batches = build_1927_triage_sheet_batches(
        [],
        include_add_sheet=True,
        include_hub_insert=True,
        expand_instr_rows=True,
        replace_filter_views=False,
    )
    assert any(
        "appendDimension" in req
        and req["appendDimension"].get("dimension") == "ROWS"
        for req in expanded_batches["create_raw_batch"]["requests"]
    )


def test_review_pass3_regressions(tmp_path: Path) -> None:
    """Pass-3 QA regressions: Undesignated COH sentinel, multi-line CSV notes, scorecards fallback, COH & SL gates, and Sheet SOPs."""
    # 1. Undesignated COH sentinel sorts last (`ZZZ_UNDESIGNATED`) at the end of `classify_1927_triage_buckets`
    undesig_rec = classify_1927_triage_buckets(
        {
            "hcad_num": "0610990000088",
            "jurs": "061",
            "atlas_year": 1930,
            "earliest_dir_year": 1918,
            "holdback_reasons": ["bld1_concrete_slab"],
            "historic_district": "Outside Historic District",
            "neighborhood": "",
            "super_neighborhood": "",
            "state_class": "A1",
            "cama_bld_ar": 1400,
            "address": "500 Main St",
        }
    )
    assert undesig_rec is not None
    assert undesig_rec["district_or_neighborhood"] == "Undesignated COH"
    assert undesig_rec["sort_key"][0] == "ZZZ_UNDESIGNATED"

    # 2. Multi-line quoted `reviewer_notes` in `1927_Cutoff_Triage.csv` preserves spaces across newlines
    multiline_csv = tmp_path / "multiline_triage.csv"
    with multiline_csv.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["review_status", "verified_year", "reviewer_notes", "hcad_num"])
        writer.writerow(
            [
                "Confirmed Pre-1927",
                "1918",
                "1924 Sanborn Vol 2\nBuilt c. 1918\r\nBrick veneer intact",
                "0610990000088",
            ]
        )
    loaded_statuses = load_existing_triage_csv_statuses(multiline_csv.read_text(encoding="utf-8"))
    assert loaded_statuses["0610990000088"]["reviewer_notes"] == (
        "1924 Sanborn Vol 2 Built c. 1918 Brick veneer intact"
    )

    # 3. `build_annual_demolition_benchmarks` preserves `existing_benchmarks["policy_impact_brief"]` when scorecards file is `{}` or corrupt
    custom_brief = {
        "generated_on": "2026-11-06",
        "standing_buildings_by_age": [{"id": "100_plus", "standing_count": 13999}],
        "custom_sentinel": 13999,
    }
    empty_sc = tmp_path / "empty_sc.json"
    empty_sc.write_text("{}", encoding="utf-8")
    corrupt_sc = tmp_path / "corrupt_sc.json"
    corrupt_sc.write_text("{not valid json", encoding="utf-8")
    for bad_sc_path in (empty_sc, corrupt_sc):
        merged_bm = build_annual_demolition_benchmarks(
            existing_benchmarks={"policy_impact_brief": custom_brief},
            scorecards_path=bad_sc_path,
        )
        assert merged_bm["policy_impact_brief"]["custom_sentinel"] == 13999

    # 4a. Hermetic `run_1927_cutoff_pipeline` WITH `Real_acct_owner.zip`: non-COH (`jurs != '061'`) parcel in `buildings.geojson` is strictly excluded from Step 1 promotion
    case_a_data = tmp_path / "case_a_data"
    case_a_cache = tmp_path / "case_a_cache"
    case_a_data.mkdir()
    case_a_cache.mkdir()

    def _write_hermetic_base_data(ddir: Path, target_hcad: str) -> None:
        (ddir / "curated_overrides.json").write_text(json.dumps({"overrides": {}}), encoding="utf-8")
        (ddir / "buildings.geojson").write_text(
            json.dumps(
                {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "properties": {
                                "id": target_hcad,
                                "hcad_num": target_hcad,
                                "address": "410 Preston Ave",
                                "year_built": 1930,
                                "historic_district": "Houston Heights Historic District South",
                                "contributing": "Contributing",
                                "neighborhood": "Houston Heights",
                            },
                            "geometry": {
                                "type": "Polygon",
                                "coordinates": [
                                    [
                                        [-95.3980, 29.7980],
                                        [-95.3979, 29.7980],
                                        [-95.3979, 29.7981],
                                        [-95.3980, 29.7981],
                                        [-95.3980, 29.7980],
                                    ]
                                ],
                            },
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (ddir / "search_index.json").write_text("[]", encoding="utf-8")
        (ddir / "overlays.json").write_text(
            json.dumps(
                {
                    "landmarks": {"type": "FeatureCollection", "features": []},
                    "lost_houston": {"type": "FeatureCollection", "features": []},
                    "platted_subdivisions": {"type": "FeatureCollection", "features": []},
                    "historic_districts": {"type": "FeatureCollection", "features": []},
                    "neighborhoods": {"type": "FeatureCollection", "features": []},
                    "super_neighborhoods": {"type": "FeatureCollection", "features": []},
                    "annexations": {"type": "FeatureCollection", "features": []},
                }
            ),
            encoding="utf-8",
        )
        (ddir / "citydir_index.json").write_text(
            json.dumps(
                {
                    "by_hcad": {
                        target_hcad: {
                            "e": 1915,
                            "p": 32415,
                            "c": "410 Preston Ave",
                            "y": [1915, 1919],
                        }
                    },
                    "absent_through_1926": [],
                }
            ),
            encoding="utf-8",
        )

    non_coh_hcad = "0998877665544"
    coh_dummy_hcad = "0112233445566"
    _write_hermetic_base_data(case_a_data, non_coh_hcad)
    with zipfile.ZipFile(case_a_cache / "Real_acct_owner.zip", "w") as zf:
        hdr = ["acct", "yr", "mailto", "mail_addr_1", "mail_addr_2", "mail_city", "mail_state", "mail_zip"]
        hdr += [f"c{i}" for i in range(8, 17)] + ["site_addr_1", "c18", "c19", "state_class"]
        hdr += [f"c{i}" for i in range(21, 33)] + ["yr_impr"] + [f"c{i}" for i in range(34, 38)]
        hdr += ["bld_ar"] + [f"c{i}" for i in range(39, 70)] + ["jurs"]
        row_coh = [""] * 71
        row_coh[0] = coh_dummy_hcad
        row_coh[17] = "1517 HEIGHTS BLVD"
        row_coh[20] = "A1"
        row_coh[33] = "1930"
        row_coh[38] = "1800"
        row_coh[70] = "001 061"
        row_non_coh = list(row_coh)
        row_non_coh[0] = non_coh_hcad
        row_non_coh[17] = "410 PRESTON AVE"
        row_non_coh[70] = "001 040 070"  # Pasadena / non-COH
        zf.writestr(
            "real_acct.txt",
            "\t".join(hdr) + "\n" + "\t".join(row_coh) + "\n" + "\t".join(row_non_coh) + "\n",
        )
    with zipfile.ZipFile(case_a_cache / "Real_building_land.zip", "w") as zf:
        zf.writestr(
            "structural_elem1.txt",
            "acct\tbld_num\tcode\tadj\ttype\ttype_dscr\tcategory_dscr\n"
            f"{non_coh_hcad}\t1\tSL\t1.0\tSL\tFoundation\tConcrete Slab\n",
        )

    res_case_a = run_1927_cutoff_pipeline(
        apply_changes=False,
        data_dir=case_a_data,
        cache_dir=case_a_cache,
    )
    assert res_case_a["promoted_count"] == 0

    # 4b. Hermetic `run_1927_cutoff_pipeline` WITHOUT `Real_acct_owner.zip` still scans `buildings_doc` HCADs in `Real_building_land.zip` and blocks `SL` Concrete Slab
    case_b_data = tmp_path / "case_b_data"
    case_b_cache = tmp_path / "case_b_cache"
    case_b_data.mkdir()
    case_b_cache.mkdir()
    slab_hcad = "0112233445599"
    _write_hermetic_base_data(case_b_data, slab_hcad)
    with zipfile.ZipFile(case_b_cache / "Real_building_land.zip", "w") as zf:
        zf.writestr(
            "structural_elem1.txt",
            "acct\tbld_num\tcode\tadj\ttype\ttype_dscr\tcategory_dscr\n"
            f"{slab_hcad}\t1\tSL\t1.0\tSL\tFoundation\tConcrete Slab\n",
        )
    res_case_b = run_1927_cutoff_pipeline(
        apply_changes=False,
        data_dir=case_b_data,
        cache_dir=case_b_cache,
    )
    assert res_case_b["promoted_count"] == 0
    assert res_case_b["holdback_reason_counts"].get("bld1_concrete_slab") == 1

    # 5. `--verify-anchors-json` CLI alias on `sync_field_surveys_sheet.py` + count-agnostic Hub Row 17 / SOP 15 + SOP 16 `--sync-policy-brief`
    hub_rows = build_admin_hub_rows()
    row17 = hub_rows[16]
    assert "2,828" not in row17[3] and "2,600" not in row17[3]
    assert "2,828" not in row17[8] and "2,600" not in row17[8]
    sop15 = build_sop15_row()
    assert "2,828" not in sop15[1] and "2,600" not in sop15[1]
    sop16 = build_sop16_row()
    assert "--sync-policy-brief" in sop16[1]

    anchors_alias_json = tmp_path / "anchors_alias.json"
    anchors_alias_json.write_text(
        json.dumps({"hub_col_a": [str(r[0] or "") if r else "" for r in hub_rows]}),
        encoding="utf-8",
    )
    out_alias_dir = tmp_path / "fs_alias_out"
    sync_field_surveys_main(
        [
            "--output-dir",
            str(out_alias_dir),
            "--verify-anchors-json",
            str(anchors_alias_json),
        ]
    )
    assert (out_alias_dir / "field_surveys_formula_values_batch.json").exists()

    # 6. Pass-4: Percent-encoded ContentDM fallback search URLs (`/` and `#` encoded) + lazy Layer-1 imports
    from atlas_pipeline.schema import build_citydir_search_url

    encoded_slash = build_citydir_search_url("1412 1/2 COLUMBIA ST", "0011223344556")
    assert encoded_slash == (
        "https://cdm17006.contentdm.oclc.org/digital/collection/citydir/search/searchterm/1412%201%2F2%20COLUMBIA%20ST"
    )
    encoded_hash = build_citydir_search_url("816 SE NORHILL BLVD #B", "0011223344556")
    assert "%23B" in encoded_hash and "#" not in encoded_hash
    frac_rec = classify_1927_triage_buckets(
        {
            "hcad_num": "0012345678999",
            "jurs": "061",
            "atlas_year": 1930,
            "earliest_dir_year": 1915,
            "holdback_reasons": ["bld1_concrete_slab"],
            "state_class": "A1",
            "cama_bld_ar": 1200,
            "address": "1412 1/2 COLUMBIA ST #B",
            "neighborhood": "Houston Heights",
        }
    )
    assert frac_rec is not None
    assert "%2F" in frac_rec["citydir_or_sanborn_url"] and "%23B" in frac_rec["citydir_or_sanborn_url"]

    import ast

    mod_ast = ast.parse(
        (Path(__file__).resolve().parents[1] / "atlas_pipeline" / "build_1927_cutoff_triage.py").read_text(
            encoding="utf-8"
        )
    )
    top_level_imports = {
        alias.name.split(".")[0]
        for node in mod_ast.body
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in mod_ast.body
        if isinstance(node, ast.ImportFrom)
    }
    assert "numpy" not in top_level_imports
    assert "shapely" not in top_level_imports


def test_review_pass5_regressions_retraction_rollback_pre1911_corroboration_escaping_and_anchors(
    tmp_path: Path,
) -> None:
    """Verify Pass-5 fixes: 2-pass demoted override rollback, post-1911 corroboration, single-pass Sheet escaping, closed-ring centroids, partial Filter View deletion, and shared workbook anchor helper."""
    from atlas_pipeline.sync_field_surveys_sheet import resolve_hub_filter_input_row

    # 1. Pre-1911 multi-volume corroboration requires >= 1 post-1911 directory outside stable-grid districts
    base_pre1911 = {
        "hcad_num": "0201230000001",
        "jurs": "061",
        "earliest_dir_year": 1905,
        "atlas_year": 1930,
        "in_coh_historic_district": True,
        "historic_district": "Houston Heights Historic District South",
        "contributing": "Contributing",
        "standing_building_years": [1930],
        "cama_max_bld_year": 1930,
        "address": "412 Heights Blvd",
        "cdm_pointer": 1200,
        "ocr_citation": "1905: 412 Heights Blvd",
    }
    res_two_pre1911 = evaluate_pre1927_auto_promotion({**base_pre1911, "dir_years": [1905, 1908]})
    assert res_two_pre1911["eligible"] is False
    assert "uncorroborated_pre1911_renumbering_risk" in res_two_pre1911["reasons"]

    res_post1911_corrob = evaluate_pre1927_auto_promotion({**base_pre1911, "dir_years": [1905, 1912]})
    assert res_post1911_corrob["eligible"] is True

    res_sixth_ward = evaluate_pre1927_auto_promotion(
        {
            **base_pre1911,
            "historic_district": "Old Sixth Ward Protected Historic District",
            "dir_years": [1905],
        }
    )
    assert res_sixth_ward["eligible"] is True

    # 2. 2-pass `demoted_auto_overrides` rollback restores `buildings.geojson` & `search_index.json` cleanly
    roll_data = tmp_path / "roll_data"
    roll_cache = tmp_path / "roll_cache"
    roll_data.mkdir()
    roll_cache.mkdir()
    roll_hcad = "0201230000099"
    (roll_data / "curated_overrides.json").write_text(json.dumps({"overrides": {}}), encoding="utf-8")
    (roll_data / "buildings.geojson").write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {
                            "id": roll_hcad,
                            "hcad_num": roll_hcad,
                            "address": "812 HEIGHTS BLVD",
                            "year_built": 1930,
                            "decade": 1930,
                            "year_source": "HCAD",
                            "historic_district": "Houston Heights Historic District South",
                            "contributing": "Contributing",
                            "neighborhood": "Houston Heights",
                        },
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [
                                    [-95.3980, 29.7980],
                                    [-95.3970, 29.7980],
                                    [-95.3970, 29.7990],
                                    [-95.3980, 29.7990],
                                    [-95.3980, 29.7980],
                                ]
                            ],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (roll_data / "search_index.json").write_text(
        json.dumps(
            [
                {
                    "hcad_num": roll_hcad,
                    "label": "812 Heights Blvd",
                    "year_built": 1930,
                    "decade": 1930,
                    "category": "Built 1930",
                    "sublabel": "812 Heights Blvd · Built 1930",
                }
            ]
        ),
        encoding="utf-8",
    )
    base_overlays = {
        "landmarks": {"type": "FeatureCollection", "features": []},
        "lost_houston": {"type": "FeatureCollection", "features": []},
        "platted_subdivisions": {"type": "FeatureCollection", "features": []},
        "historic_districts": {"type": "FeatureCollection", "features": []},
        "neighborhoods": {"type": "FeatureCollection", "features": []},
        "super_neighborhoods": {"type": "FeatureCollection", "features": []},
        "annexations": {"type": "FeatureCollection", "features": []},
    }
    (roll_data / "overlays.json").write_text(json.dumps(base_overlays), encoding="utf-8")
    (roll_data / "citydir_index.json").write_text(
        json.dumps(
            {
                "by_hcad": {
                    roll_hcad: {
                        "e": 1915,
                        "p": 32415,
                        "c": "812 Heights Blvd",
                        "y": [1915, 1919],
                    }
                },
                "absent_through_1926": [],
            }
        ),
        encoding="utf-8",
    )
    with zipfile.ZipFile(roll_cache / "Real_acct_owner.zip", "w") as zf:
        hdr = ["acct", "yr", "mailto", "mail_addr_1", "mail_addr_2", "mail_city", "mail_state", "mail_zip"]
        hdr += [f"c{i}" for i in range(8, 17)] + ["site_addr_1", "c18", "c19", "state_class"]
        hdr += [f"c{i}" for i in range(21, 33)] + ["yr_impr"] + [f"c{i}" for i in range(34, 38)]
        hdr += ["bld_ar"] + [f"c{i}" for i in range(39, 70)] + ["jurs"]
        row_coh = [""] * 71
        row_coh[0] = roll_hcad
        row_coh[17] = "812 HEIGHTS BLVD"
        row_coh[20] = "A1"
        row_coh[33] = "1930"
        row_coh[38] = "1800"
        row_coh[70] = "001 061"
        zf.writestr("real_acct.txt", "\t".join(hdr) + "\n" + "\t".join(row_coh) + "\n")

    # Pass 1: promotes `roll_hcad` -> 1915
    pass1 = run_1927_cutoff_pipeline(apply_changes=True, data_dir=roll_data, cache_dir=roll_cache)
    assert pass1["promoted_count"] == 1
    ov_after_p1 = json.loads((roll_data / "curated_overrides.json").read_text(encoding="utf-8"))["overrides"]
    assert roll_hcad in ov_after_p1
    bgeo_after_p1 = json.loads((roll_data / "buildings.geojson").read_text(encoding="utf-8"))["features"][0]["properties"]
    assert bgeo_after_p1["year_built"] == 1915
    assert bgeo_after_p1["is_curated_override"] is True
    sidx_after_p1 = json.loads((roll_data / "search_index.json").read_text(encoding="utf-8"))[0]
    assert sidx_after_p1["year_built"] == 1915
    assert sidx_after_p1["category"] == "Built 1915"

    # Pass 2: parcel enters `lost_houston` -> retracted from overrides AND reverted in buildings.geojson & search_index.json
    base_overlays["lost_houston"]["features"] = [
        {"type": "Feature", "properties": {"site_hcad_nums": [roll_hcad]}, "geometry": None}
    ]
    (roll_data / "overlays.json").write_text(json.dumps(base_overlays), encoding="utf-8")
    pass2 = run_1927_cutoff_pipeline(apply_changes=True, data_dir=roll_data, cache_dir=roll_cache)
    assert pass2["promoted_count"] == 0
    assert pass2["holdback_reason_counts"].get("in_lost_houston") == 1
    ov_after_p2 = json.loads((roll_data / "curated_overrides.json").read_text(encoding="utf-8"))["overrides"]
    assert roll_hcad not in ov_after_p2
    bgeo_after_p2 = json.loads((roll_data / "buildings.geojson").read_text(encoding="utf-8"))["features"][0]["properties"]
    assert bgeo_after_p2["year_built"] == 1930
    assert bgeo_after_p2["decade"] == 1930
    assert bgeo_after_p2["year_source"] == "HCAD"
    assert "is_curated_override" not in bgeo_after_p2
    assert "original_hcad_year" not in bgeo_after_p2
    sidx_after_p2 = json.loads((roll_data / "search_index.json").read_text(encoding="utf-8"))[0]
    assert sidx_after_p2["year_built"] == 1930
    assert sidx_after_p2["decade"] == 1930
    assert sidx_after_p2["category"] == "Built 1930"
    assert "Built 1930" in sidx_after_p2["sublabel"]

    # Pass 3: subsequent run still sees `in_lost_houston` holdback rather than misclassifying as human override
    pass3 = run_1927_cutoff_pipeline(apply_changes=False, data_dir=roll_data, cache_dir=roll_cache)
    assert pass3["holdback_reason_counts"].get("in_lost_houston") == 1

    # 3. Deferred Sheet sanitization & quote/plus-resilient `parse_street_walk_key`
    esc_rec = classify_1927_triage_buckets(
        {
            "hcad_num": "0023456789012",
            "jurs": "061",
            "atlas_year": 1930,
            "earliest_dir_year": 1915,
            "holdback_reasons": ["bld1_concrete_slab"],
            "state_class": "A1",
            "cama_bld_ar": 1600,
            "address": "+1412 Columbia St",
            "neighborhood": "=Norhill",
        }
    )
    assert esc_rec is not None
    assert esc_rec["address"] == "+1412 Columbia St"
    assert esc_rec["district_or_neighborhood"] == "=Norhill"
    esc_row = triage_record_to_sheet_row(esc_rec)
    assert esc_row[5] == "'=Norhill"
    assert esc_row[9] == "'+1412 Columbia St", f"Expected single leading apostrophe, got {esc_row[9]!r}"

    assert parse_street_walk_key("'+1412 Columbia St", "'=Norhill Historic District", "0023456789012") == (
        "=NORHILL HISTORIC DISTRICT",
        "COLUMBIA",
        1,
        1412,
        "0023456789012",
    )
    assert parse_street_walk_key("'815 Columbia St", "'Undesignated / Outside HD", "001")[0] == "ZZZ_UNDESIGNATED"
    dedup_from_escaped = deduplicate_and_sort_triage_rows(
        [
            {
                "hcad_num": "0023456789012",
                "address": "'+1412 Columbia St",
                "district_or_neighborhood": "'Norhill Historic District",
                "all_buckets": "1. Pre-1927 City Directory Hold-Back",
                "priority_score": 90,
            },
            {
                "hcad_num": "0023456789013",
                "address": "'815 Columbia St",
                "district_or_neighborhood": "'Norhill Historic District",
                "all_buckets": "1. Pre-1927 City Directory Hold-Back",
                "priority_score": 80,
            },
        ]
    )
    assert [r["hcad_num"] for r in dedup_from_escaped] == ["0023456789013", "0023456789012"]

    # 4. Closed-ring `_extract_centroid_xy` matches unbiased 4-corner midpoint
    closed_poly = {
        "type": "Polygon",
        "coordinates": [
            [
                [-95.3980, 29.7980],
                [-95.3970, 29.7980],
                [-95.3970, 29.7990],
                [-95.3980, 29.7990],
                [-95.3980, 29.7980],
            ]
        ],
    }
    cx, cy = _extract_centroid_xy(closed_poly)
    assert cx == pytest.approx(-95.3975)
    assert cy == pytest.approx(29.7985)
    mcx, mcy = _extract_centroid_xy({"type": "MultiPolygon", "coordinates": [closed_poly["coordinates"]]})
    assert mcx == pytest.approx(-95.3975)
    assert mcy == pytest.approx(29.7985)

    # 5. `existing_filter_view_ids` emits `deleteFilterView` strictly for present IDs
    partial_fvs = build_1927_triage_sheet_batches(
        [esc_row],
        replace_filter_views=True,
        existing_filter_view_ids={927192702, 927192705},
    )
    del_ids = [
        r["deleteFilterView"]["filterId"]
        for r in partial_fvs["style_raw_batch"]["requests"]
        if "deleteFilterView" in r
    ]
    add_ids = [
        r["addFilterView"]["filter"]["filterViewId"]
        for r in partial_fvs["style_raw_batch"]["requests"]
        if "addFilterView" in r
    ]
    assert del_ids == [927192702, 927192705]
    assert len(add_ids) == 7

    # 6. Canonical `inspect_workbook_layout_anchors` & `resolve_hub_filter_input_row` delegation
    hub_13 = [str(r[0] or "") if r else "" for r in build_admin_hub_rows()]
    instr_13 = [str(r[0] or "") if r else "" for r in build_instructions_rows()]
    lay_13 = inspect_workbook_layout_anchors(hub_col_a=hub_13, instr_col_a=instr_13, require_surveys_row=True)
    assert lay_13["has_surveys_row"] is True
    assert lay_13["has_triage_1927_row"] is True
    assert lay_13["filter_input_row"] == HUB_FILTER_INPUT_ROW
    assert resolve_hub_filter_input_row(hub_13) == HUB_FILTER_INPUT_ROW

    hub_12 = list(hub_13)
    hub_12.pop(HUB_TRIAGE_1927_ROW - 1)
    lay_12 = inspect_workbook_layout_anchors(hub_col_a=hub_12, instr_col_a=instr_13, require_surveys_row=True)
    assert lay_12["has_surveys_row"] is True
    assert lay_12["has_triage_1927_row"] is False
    assert lay_12["filter_input_row"] == HUB_FILTER_INPUT_ROW - 1
    assert resolve_hub_filter_input_row(hub_12) == HUB_FILTER_INPUT_ROW - 1





