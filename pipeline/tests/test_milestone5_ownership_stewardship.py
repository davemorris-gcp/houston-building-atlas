"""Milestone 5 unit & regression test suite: Ownership Unmasking, Historical Chain of Stewardship,
2-Stage COH Demolition Permit Classifier, and Two-Key Auto-Graduation Rule.

Covers Quality Plan scenarios UT-1, UT-2, UT-3, UT-4, RP-1, RP-2, plus 2-stage COH
demolition permit classification (1 of 2 Sewer Disconnect Plumbing vs 2 of 2 Building
Demolition) and Two-Key Auto-Graduation in update_annual_hcad_roll.py.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
import zipfile

from atlas_pipeline.build_ownership_and_history import (
    build_ownership_graph,
    fetch_or_load_tx_comptroller_entities,
)
from atlas_pipeline.fetch_live_permits import (
    classify_coh_demo_permit_stage,
    summarize_coh_demo_permit_stage_for_parcel,
)
from atlas_pipeline.schema import (
    ACTIVE_CACHE_BUST,
    ACTIVE_TAX_YEAR,
    CONFIDENTIAL_OWNER_TOKENS,
    ENRICHED_PERMIT_SHARD_KEYS,
    OWNERSHIP_TIER_KEYS,
    PLACEHOLDER_NON_GRAPH_OWNER_TOKENS,
    classify_account_ownership,
    classify_main_structure_demolition_permit,
    compute_pre_demolition_flip_days,
    extract_care_of_and_clean_mail,
    is_corporate_or_trust_entity,
    is_hoa_or_civic_steward,
    is_protected_homestead,
    is_registered_agent_billing_cluster,
    is_tax_protest_agent_care_of,
    normalize_postal_billing_key,
    sync_repo_tax_year_and_cache_bust,
)
from atlas_pipeline.update_annual_hcad_roll import (
    HcadAccountSnapshot,
    _maybe_build_cohort5_candidate,
    classify_feature_delta,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "app" / "public" / "data"


# ===========================================================================
# UT-1: Ownership Taxonomy & TX Tax Code Sec. 25.025 Privacy Redaction
# ===========================================================================


def test_ut1_ownership_tier_keys_and_sec_25_025_privacy_redaction() -> None:
    """Verify deterministic 6-tier ownership classification and Sec. 25.025 privacy redaction."""
    assert OWNERSHIP_TIER_KEYS == (
        "homestead",
        "local_small",
        "regional_portfolio",
        "institutional_pe",
        "anonymity_shield",
        "civic_nonprofit",
    )

    # 1. Confidential / redacted residential A1 homestead (e.g. 0202570000033 / 731 HEIGHTS BLVD B)
    assert "CURRENT OWNER" in CONFIDENTIAL_OWNER_TOKENS
    assert "CONFIDENTIAL OWNER" in CONFIDENTIAL_OWNER_TOKENS
    assert "1017 RUTLAND LLC" not in CONFIDENTIAL_OWNER_TOKENS

    is_hs_res, is_conf_res = is_protected_homestead(
        "A1",
        "CURRENT OWNER",
        "123 PRIVATE LN",
        "731 HEIGHTS BLVD B",
        mail_city="HOUSTON",
        mail_state="TX",
        owner_parcel_count=1,
        cluster_non_situs_commercial_size=0,
    )
    assert is_hs_res is True
    assert is_conf_res is True

    homestead_info = classify_account_ownership(
        state_class="A1",
        owner_name="CURRENT OWNER",
        situs_addr="731 HEIGHTS BLVD B",
        mail_addr_1="C/O JANE DOE",
        mail_addr_2="731 HEIGHTS BLVD B",
        mail_city="HOUSTON",
        mail_state="TX",
        mail_zip="77007",
        owner_parcel_count=1,
        cluster_parcel_count=1,
    )
    assert homestead_info["own_tier"] == "homestead"
    assert homestead_info["is_protected_homestead"] is True
    assert homestead_info["is_sec_25_025_confidential"] is True
    assert homestead_info["display_mail"] == ""
    assert homestead_info["care_of"] == ""
    assert homestead_info["port_id"] == ""

    # 2. Commercial F1 parcel with CURRENT OWNER (e.g. 1091420000006) must NOT become homestead
    is_hs_comm, is_conf_comm = is_protected_homestead(
        "F1",
        "CURRENT OWNER",
        "500 MAIN ST",
        "500 MAIN ST",
        mail_city="HOUSTON",
        mail_state="TX",
        owner_parcel_count=1,
        cluster_non_situs_commercial_size=5,
    )
    assert is_hs_comm is False
    assert is_conf_comm is False

    comm_placeholder = classify_account_ownership(
        state_class="F1",
        owner_name="CURRENT OWNER",
        situs_addr="500 MAIN ST",
        mail_addr_1="500 MAIN ST",
        mail_city="HOUSTON",
        mail_state="TX",
        mail_zip="77002",
        owner_parcel_count=1,
        cluster_parcel_count=1,
    )
    assert comm_placeholder["own_tier"] == "anonymity_shield"
    assert comm_placeholder["is_protected_homestead"] is False
    assert "CURRENT OWNER" in PLACEHOLDER_NON_GRAPH_OWNER_TOKENS

    # 3. Single-entity & multi-LLC thresholds (5+ -> regional_portfolio, 100+ or out-of-state -> institutional_pe)
    reg_info = classify_account_ownership(
        state_class="A1",
        owner_name="HEIGHTS BUNGALOW HOLDINGS LLC",
        situs_addr="1017 RUTLAND ST",
        mail_addr_1="7155 OLD KATY RD STE N260",
        mail_city="HOUSTON",
        mail_state="TX",
        mail_zip="77024",
        owner_parcel_count=5,
        cluster_parcel_count=5,
        cluster_distinct_corps=2,
        port_id="port_7155_old_katy_n260",
    )
    assert reg_info["own_tier"] == "regional_portfolio"
    assert reg_info["port_id"] == "port_7155_old_katy_n260"

    single_ent_5_info = classify_account_ownership(
        state_class="A1",
        owner_name="BAYOU RENTAL HOMES LLC",
        situs_addr="1200 HARVARD ST",
        mail_addr_1="900 HARVARD ST",
        mail_city="HOUSTON",
        mail_state="TX",
        mail_zip="77008",
        owner_parcel_count=5,
        cluster_parcel_count=1,
        cluster_distinct_corps=1,
        port_id="port_bayou_rental",
    )
    assert single_ent_5_info["own_tier"] == "regional_portfolio"

    single_ent_4_info = classify_account_ownership(
        state_class="A1",
        owner_name="BAYOU RENTAL HOMES LLC",
        situs_addr="1200 HARVARD ST",
        mail_addr_1="900 HARVARD ST",
        mail_city="HOUSTON",
        mail_state="TX",
        mail_zip="77008",
        owner_parcel_count=4,
        cluster_parcel_count=1,
        cluster_distinct_corps=1,
    )
    assert single_ent_4_info["own_tier"] == "local_small"

    pe_info = classify_account_ownership(
        state_class="A1",
        owner_name="INVITATION HOMES LP",
        situs_addr="100 MAIN ST",
        mail_addr_1="1717 MAIN ST STE 2000",
        mail_city="DALLAS",
        mail_state="TX",
        mail_zip="75201",
        owner_parcel_count=120,
        cluster_parcel_count=120,
        cluster_distinct_corps=4,
        port_id="port_test_pe",
    )
    assert pe_info["own_tier"] == "institutional_pe"
    assert pe_info["port_id"] == "port_test_pe"

    # 4. Registered-agent anonymity shield (5900 Balcones Dr Ste 100, Austin TX; 3225 McLeod Dr Ste 777, Las Vegas NV)
    bk_lv, _, _ = normalize_postal_billing_key(
        "3225 MCLEOD DR STE 777", "", "LAS VEGAS", "NV", "89121"
    )
    assert is_registered_agent_billing_cluster(bk_lv, owner_name="COLE EMMANUELLA I") is True

    bk_aus, _, _ = normalize_postal_billing_key(
        "5900 BALCONES DR STE 100", "", "AUSTIN", "TX", "78731"
    )
    assert (
        is_registered_agent_billing_cluster(
            bk_aus, owner_name="OPAQUE HOLDINGS LLC", care_of="REGISTERED AGENTS INC"
        )
        is True
    )

    shield_info = classify_account_ownership(
        state_class="A1",
        owner_name="COLE EMMANUELLA I",
        situs_addr="1107 TULANE ST",
        mail_addr_1="3225 MCLEOD DR STE 777",
        mail_city="LAS VEGAS",
        mail_state="NV",
        mail_zip="89121",
        owner_parcel_count=1,
        cluster_parcel_count=2,
    )
    assert shield_info["own_tier"] == "anonymity_shield"

    # 5. Civic / Nonprofit / Public Trust
    civic_info = classify_account_ownership(
        state_class="X1",
        owner_name="CITY OF HOUSTON",
        situs_addr="901 BAGBY ST",
        mail_addr_1="PO BOX 1562",
        mail_city="HOUSTON",
        mail_state="TX",
        mail_zip="77251",
        owner_parcel_count=120,
        cluster_parcel_count=120,
    )
    assert civic_info["own_tier"] == "civic_nonprofit"


# ===========================================================================
# UT-2: Bipartite Entity Resolution & Property Tax Agent Isolation
# ===========================================================================


def test_ut2_bipartite_entity_resolution_and_tax_agent_isolation(tmp_path: Path) -> None:
    """Verify tax agents never merge unrelated owners while shared developer hubs resolve cleanly."""
    for agent_str in (
        "O'CONNOR & ASSOCIATES",
        "BETTENCOURT TAX ADVISORS LLC",
        "RYAN LLC PROPERTY TAX",
        "POPP HUTCHESON PLLC",
        "CANTRELL MCCULLOCH INC",
    ):
        assert is_tax_protest_agent_care_of(agent_str) is True

    assert is_tax_protest_agent_care_of("ROBERTS DEVELOPMENT MGMT") is False

    # Build a synthetic Real_acct_owner.zip archive with:
    # - 6 unrelated single-asset LLCs using C/O O'CONNOR & ASSOCIATES (must NOT cluster)
    # - 6 single-asset LLCs sharing 7155 OLD KATY RD STE N260, HOUSTON TX 77024 (MUST cluster into port_7155_old_katy_n260)
    # - 10 commercial F1 parcels with CURRENT OWNER placeholder (must NOT cluster into a portfolio)
    header = "\t".join(
        [
            "acct",
            "yr",
            "mailto",
            "mail_addr_1",
            "mail_addr_2",
            "mail_city",
            "mail_state",
            "mail_zip",
            "site_addr_1",
            "state_class",
            "lgl_1",
            "tot_mkt_val",
            "yr_impr",
        ]
    )
    rows = [header]

    for i in range(1, 7):
        acct = f"010000000000{i}"
        rows.append(
            "\t".join(
                [
                    acct,
                    "2026",
                    f"UNRELATED OWNER {i} LLC",
                    "C/O O'CONNOR & ASSOCIATES",
                    "2200 NORTH LOOP W STE 200",
                    "HOUSTON",
                    "TX",
                    "77018",
                    f"{100 + i} OAK ST",
                    "A1",
                    "OAK ADDITION",
                    "450000",
                    "1930",
                ]
            )
        )

    for i in range(1, 7):
        acct = f"020209000001{i}"
        rows.append(
            "\t".join(
                [
                    acct,
                    "2026",
                    f"101{i} RUTLAND LLC",
                    "7155 OLD KATY RD STE N260",
                    "",
                    "HOUSTON",
                    "TX",
                    "77024",
                    f"101{i} RUTLAND ST",
                    "A1",
                    "HOUSTON HEIGHTS",
                    "750000",
                    "1925",
                ]
            )
        )

    for i in range(1, 11):
        acct = f"10914200000{i:02d}"
        rows.append(
            "\t".join(
                [
                    acct,
                    "2026",
                    "CURRENT OWNER",
                    "500 MAIN ST",
                    "",
                    "HOUSTON",
                    "TX",
                    "77002",
                    f"{500 + i} MAIN ST",
                    "F1",
                    "DOWNTOWN",
                    "1200000",
                    "1950",
                ]
            )
        )

    synth_zip = tmp_path / "Real_acct_owner.zip"
    with zipfile.ZipFile(synth_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("real_acct.txt", "\n".join(rows) + "\n")

    comptroller_cache = tmp_path / "tx_comptroller_entities.json"
    graph = build_ownership_graph(
        synth_zip,
        tax_year=2026,
        comptroller_cache_path=comptroller_cache,
        include_owners_table=False,
        include_permits_table=False,
    )
    records = graph["records"]
    portfolios = graph["portfolios"]

    # Tax-agent accounts must NOT be merged into a portfolio
    for i in range(1, 7):
        acct = f"010000000000{i}"
        rec = records[acct]
        assert rec.port_id == ""
        assert rec.own_tier == "local_small"
        assert rec.is_tax_agent_care_of is True

    # Commercial CURRENT OWNER accounts must NOT be merged into a mega-portfolio
    for i in range(1, 11):
        acct = f"10914200000{i:02d}"
        rec = records[acct]
        assert rec.port_id == ""
        assert rec.own_tier == "anonymity_shield"
        assert rec.is_protected_homestead is False

    # Genuine developer hub accounts MUST resolve to canonical port_7155_old_katy_n260
    for i in range(1, 7):
        acct = f"020209000001{i}"
        rec = records[acct]
        assert rec.port_id == "port_7155_old_katy_n260"
        assert rec.own_tier == "regional_portfolio"

    assert "port_7155_old_katy_n260" in portfolios
    assert len(portfolios["port_7155_old_katy_n260"]["accts"]) == 6
    assert len(portfolios["port_7155_old_katy_n260"]["distinct_corps"]) == 6


# ===========================================================================
# UT-3: Pre-Demolition Rapid-Flip Window & Sentinel Date Filtering
# ===========================================================================


def test_ut3_pre_demolition_flip_days_and_sentinel_filtering() -> None:
    """Verify exact rapid-flip day delta math and rejection of partial/sentinel dates."""
    # Benchmark 1107 TULANE ST (0202010000014): 20251117 deed -> 2025-12-15 demo permit = 28 days
    assert compute_pre_demolition_flip_days("20251117", "2025-12-15") == 28
    assert compute_pre_demolition_flip_days("20251117", "12/15/2025") == 28

    # Exact boundaries: 0 days (same day) and 365 days
    assert compute_pre_demolition_flip_days("20250601", "2025-06-01") == 0
    assert compute_pre_demolition_flip_days("20240601", "2025-06-01") == 365

    # > 365 days returns None
    assert compute_pre_demolition_flip_days("20240531", "2025-06-01") is None

    # Deed after demolition permit (negative delta) returns None
    assert compute_pre_demolition_flip_days("20250701", "2025-06-01") is None

    # Partial year-only deed dates (YYYY0000) and month-00/day-00 must return None
    assert compute_pre_demolition_flip_days("20250000", "2025-06-01") is None
    assert compute_pre_demolition_flip_days("20250600", "2025-06-15") is None

    # HCAD SQL/OLE null-date sentinels must return None
    assert compute_pre_demolition_flip_days("00000000", "2025-06-01") is None
    assert compute_pre_demolition_flip_days("18991230", "2025-06-01") is None
    assert compute_pre_demolition_flip_days("19000101", "2025-06-01") is None
    assert compute_pre_demolition_flip_days("19000102", "2025-06-01") is None
    assert compute_pre_demolition_flip_days("20250501", "12/30/1899") is None
    assert compute_pre_demolition_flip_days("20250501", "01/01/1900") is None


# ===========================================================================
# UT-4: Additive Permit Shard Enrichment & Benchmark Parcel Verification
# ===========================================================================


def test_ut4_additive_permit_shard_enrichment_and_live_benchmarks() -> None:
    """Verify ENRICHED_PERMIT_SHARD_KEYS and live benchmark parcels in permits_020.json."""
    assert ENRICHED_PERMIT_SHARD_KEYS == ("d", "ot", "pid", "m", "co", "flip")

    shard_020_path = DATA_DIR / "permits" / "permits_020.json"
    assert shard_020_path.is_file(), "Expected app/public/data/permits/permits_020.json to exist"
    shard_020 = json.loads(shard_020_path.read_text(encoding="utf-8"))

    # Benchmark 1: 1017 RUTLAND ST (0202090000013) -> regional_portfolio, port_7155_old_katy_n260
    rec_rutland = shard_020.get("0202090000013")
    assert isinstance(rec_rutland, dict)
    assert rec_rutland.get("ot") == "regional_portfolio"
    assert rec_rutland.get("pid") == "port_7155_old_katy_n260"
    assert "7155 OLD KATY RD" in str(rec_rutland.get("m") or "")

    # Benchmark 2: 1107 TULANE ST (0202010000014) -> anonymity_shield, flip=28, deed chain present
    rec_tulane = shard_020.get("0202010000014")
    assert isinstance(rec_tulane, dict)
    assert rec_tulane.get("ot") == "anonymity_shield"
    assert rec_tulane.get("flip") == 28
    assert "20251117:R2025-495199" in str(rec_tulane.get("d") or "")
    assert "3225 MCLEOD DR" in str(rec_tulane.get("m") or "")

    # Benchmark 3: 731 HEIGHTS BLVD B (0202570000033) -> homestead with zero m/co leak
    rec_homestead = shard_020.get("0202570000033")
    if isinstance(rec_homestead, dict):
        assert rec_homestead.get("ot") in (None, "homestead")
        assert "m" not in rec_homestead
        assert "co" not in rec_homestead
        assert "pid" not in rec_homestead


# ===========================================================================
# 2-Stage COH Demolition Permit & Two-Key Auto-Graduation Tests
# ===========================================================================


def test_two_stage_coh_demolition_permit_classification() -> None:
    """Verify Stage 1 (1 of 2 Sewer Disconnect Plumbing) vs Stage 2 (2 of 2 Building Demolition)."""
    # Stage 1: Plumbing Sewer Disconnect (permit_type 12 or 4P, or PLUMBING in permit_desc)
    s1_12 = classify_coh_demo_permit_stage(
        permit_desc="Plumbing Permit",
        use_desc="DEMO RES/SEWER DISC",
        permit_type="12",
    )
    assert s1_12["permit_stage"] == "stage_1_sewer_plumbing"
    assert s1_12["permit_type_label"] == "Sewer Disconnect Plumbing Permit (1 of 2)"
    assert s1_12["is_sewer_disconnect"] is True
    assert s1_12["is_demo_permit_issued"] is False

    s1_4p = classify_coh_demo_permit_stage(
        permit_desc="Plbg Demo Sewer Disc",
        use_desc="BLD DEMO/SEWER DISC",
        permit_type="4P",
    )
    assert s1_4p["permit_stage"] == "stage_1_sewer_plumbing"
    assert s1_4p["is_sewer_disconnect"] is True
    assert s1_4p["is_demo_permit_issued"] is False

    # Stage 2: Main-Structure Building Demolition Permit (permit_type SD or 4D, or DEMOLITION in permit_desc)
    s2_sd = classify_coh_demo_permit_stage(
        permit_desc="Demolition",
        use_desc="DEMO RES/SEWER DISC",
        permit_type="SD",
    )
    assert s2_sd["permit_stage"] == "stage_2_demolition"
    assert s2_sd["permit_type_label"] == "Building Demolition Permit (2 of 2)"
    assert s2_sd["is_demo_permit_issued"] is True
    assert s2_sd["is_sewer_disconnect"] is False

    s2_4d = classify_coh_demo_permit_stage(
        permit_desc="Dgr Bldg Demo",
        use_desc="BLD DEMO/SEWER DISC",
        permit_type="4D",
    )
    assert s2_4d["permit_stage"] == "stage_2_demolition"
    assert s2_4d["is_demo_permit_issued"] is True

    # Detached garage / accessory-only demolition must never trigger Stage 1 or Stage 2 main building demo
    acc = classify_coh_demo_permit_stage(
        permit_desc="Demolition",
        use_desc="DEMO DETACHED GARAGE ONLY",
        permit_type="SD",
    )
    assert acc["permit_stage"] == "accessory_only"
    assert acc["is_garage_only"] is True
    assert acc["is_demo_permit_issued"] is False

    # Parcel-level summary across Stage 1 only vs Stage 1 + Stage 2
    sum_s1_only = summarize_coh_demo_permit_stage_for_parcel([s1_12])
    assert sum_s1_only == {
        "demo_permit_stage": "stage_1_sewer_only",
        "has_sewer_disc_plumbing": True,
        "has_demo_permit": False,
    }

    sum_both = summarize_coh_demo_permit_stage_for_parcel([s1_12, s2_sd])
    assert sum_both == {
        "demo_permit_stage": "stage_2_both",
        "has_sewer_disc_plumbing": True,
        "has_demo_permit": True,
    }


def test_two_key_auto_graduation_rule_in_annual_roll_update() -> None:
    """Verify Two-Key Auto-Graduation requires BOTH $0 improvement flip AND Stage 2 main-structure demo permit."""
    prev_feat = {
        "type": "Feature",
        "properties": {
            "id": "pcl_0202010000014",
            "hcad_num": "0202010000014",
            "address": "1107 TULANE ST",
            "year_built": 1920,
            "decade": 1920,
            "owner": "COLE EMMANUELLA I",
            "bld_area": 1840.0,
            "land_area": 5000.0,
            "use_category": "Residential",
            "bld_style": "Bungalow",
            "subdivision": "HOUSTON HEIGHTS",
            "historic_district": "Houston Heights Historic District South",
            "contributing": "Contributing",
            "own_tier": "anonymity_shield",
        },
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [-95.4014, 29.7893],
                    [-95.4012, 29.7893],
                    [-95.4012, 29.7895],
                    [-95.4014, 29.7895],
                    [-95.4014, 29.7893],
                ]
            ],
        },
    }

    # Case A: HCAD improvement value drops to $0 / year_built=0 WITHOUT a Stage 2 main demo permit
    # (e.g. clerical CAMA revaluation or Stage 1 sewer disconnect plumbing permit only).
    is_main_plumb, _, _ = classify_main_structure_demolition_permit(
        "SEWER DISCONNECT FOR DEMO",
        permit_type="12",
        issue_date="2025-11-20",
        raw_address="1107 TULANE ST",
        permit_desc="Plumbing",
    )
    assert is_main_plumb is False

    snap_no_demo = HcadAccountSnapshot(
        acct="0202010000014",
        earliest_year=0,
        latest_year=0,
        max_remodel_year=0,
        total_bld_area=0.0,
        building_count=0,
        state_class="C1",
        use_category="Vacant / Exempt",
        numbered_address="1107 TULANE ST",
        owner="COLE EMMANUELLA I",
        subdivision="HOUSTON HEIGHTS",
        land_area_sqft=5000.0,
        bld_val=0.0,
        yr_impr=0,
        has_main_demo_permit=False,
        demo_permit_year=0,
        demo_permit_desc="",
    )

    cand_hold = _maybe_build_cohort5_candidate(
        "0202010000014",
        prev_feat["properties"],
        prev_feat["geometry"],
        snap_no_demo,
        detection_basis="impr_zeroed_no_demo_permit",
        tax_year=2026,
        prior_tax_year=2025,
    )
    assert cand_hold is not None
    assert cand_hold["auto_graduate"] is False
    assert cand_hold["graduation_status"] == "hold_for_review_no_demo_permit"
    assert cand_hold["demo_permit_date"] == ""

    delta_hold = classify_feature_delta(
        {"nw_w": [], "nw_e": [prev_feat], "ne": [], "sw": [], "se": []},
        {"0202010000014": snap_no_demo},
        parcel_hcads=["0202010000014"],
        parcel_geoms_2278=[],
        parcel_hcad_to_idx={},
        parcel_strtree=None,
        tieback_map={},
        curated_overrides={},
        tax_year=2026,
        prior_tax_year=2025,
        enforce_circuit_breaker=False,
    )
    assert len(delta_hold["bare_zero_value_flips_hold_for_review"]) == 1
    assert len(delta_hold["auto_graduated_lost_houston_candidates"]) == 0
    assert (
        delta_hold["bare_zero_value_flips_hold_for_review"][0]["graduation_status"]
        == "hold_for_review_no_demo_permit"
    )

    # Case B: HCAD improvement value drops to $0 WITH a verified Stage 2 main-structure demo permit
    is_main_s2, s2_yr, _ = classify_main_structure_demolition_permit(
        "DEMO RES/SEWER DISC - SINGLE FAMILY RESIDENCE",
        permit_type="SD",
        issue_date="2025-12-15",
        raw_address="1107 TULANE ST",
        permit_desc="Demolition",
    )
    assert is_main_s2 is True
    assert s2_yr == 2025

    snap_with_demo = HcadAccountSnapshot(
        acct="0202010000014",
        earliest_year=0,
        latest_year=0,
        max_remodel_year=0,
        total_bld_area=0.0,
        building_count=0,
        state_class="C1",
        use_category="Vacant / Exempt",
        numbered_address="1107 TULANE ST",
        owner="COLE EMMANUELLA I",
        subdivision="HOUSTON HEIGHTS",
        land_area_sqft=5000.0,
        bld_val=0.0,
        yr_impr=0,
        has_main_demo_permit=True,
        demo_permit_year=2025,
        demo_permit_type="SD",
        demo_permit_date="2025-12-15",
        demo_permit_desc="DEMO RES/SEWER DISC - SINGLE FAMILY RESIDENCE",
        demo_permit_addr="1107 TULANE ST",
    )

    cand_grad = _maybe_build_cohort5_candidate(
        "0202010000014",
        prev_feat["properties"],
        prev_feat["geometry"],
        snap_with_demo,
        detection_basis="demo_permit_verified",
        tax_year=2026,
        prior_tax_year=2025,
    )
    assert cand_grad is not None
    assert cand_grad["auto_graduate"] is True
    assert cand_grad["graduation_status"] == "auto_graduated_demo_permit_paired"
    assert cand_grad["year_demolished"] == 2025

    delta_grad = classify_feature_delta(
        {"nw_w": [], "nw_e": [prev_feat], "ne": [], "sw": [], "se": []},
        {"0202010000014": snap_with_demo},
        parcel_hcads=["0202010000014"],
        parcel_geoms_2278=[],
        parcel_hcad_to_idx={},
        parcel_strtree=None,
        tieback_map={},
        curated_overrides={},
        tax_year=2026,
        prior_tax_year=2025,
        enforce_circuit_breaker=False,
    )
    assert len(delta_grad["auto_graduated_lost_houston_candidates"]) == 1
    assert len(delta_grad["bare_zero_value_flips_hold_for_review"]) == 0
    assert (
        delta_grad["auto_graduated_lost_houston_candidates"][0]["graduation_status"]
        == "auto_graduated_demo_permit_paired"
    )


# ===========================================================================
# RP-1 & RP-2: Annual Rollover Compatibility & Live Data Bundle Integrity
# ===========================================================================


def test_rp1_and_rp2_annual_rollover_sync_and_live_ownership_bundle_integrity() -> None:
    """Verify dry-run repo sync and live ownership_portfolios.json / neighborhood_scorecards.json contracts."""
    sync_res = sync_repo_tax_year_and_cache_bust(
        REPO_ROOT,
        tax_year=ACTIVE_TAX_YEAR,
        cache_bust=ACTIVE_CACHE_BUST,
        dry_run=True,
    )
    assert sync_res["rules_validated"] >= 18
    assert sync_res["modified_files"] == []

    # Check ownership_portfolios.json schema and benchmark portfolio port_7155_old_katy_n260
    port_path = DATA_DIR / "ownership_portfolios.json"
    assert port_path.is_file()
    raw_port_text = port_path.read_text(encoding="utf-8")
    assert "\u2014" not in raw_port_text
    assert "\u2013" not in raw_port_text
    port_data = json.loads(raw_port_text)
    assert "portfolios" in port_data
    assert "tier_counts" in port_data
    assert "port_7155_old_katy_n260" in port_data["portfolios"]
    rutland_port = port_data["portfolios"]["port_7155_old_katy_n260"]
    assert rutland_port["parcel_count"] >= 5
    assert rutland_port["distinct_entity_count"] >= 2
    assert any(w[0] == "0202090000013" for w in rutland_port.get("works", []))

    # Check neighborhood_scorecards.json includes ownership tier breakdown ('ot') and top portfolios ('tps')
    sc_path = DATA_DIR / "neighborhood_scorecards.json"
    assert sc_path.is_file()
    sc_data = json.loads(sc_path.read_text(encoding="utf-8"))
    scorecards = sc_data.get("scorecards") or []
    assert len(scorecards) >= 50
    assert isinstance(scorecards[0].get("ot"), list)
    assert len(scorecards[0]["ot"]) == len(OWNERSHIP_TIER_KEYS)
    assert "tps" in scorecards[0]

    # Check stats_summary.json includes ownership_tier_counts
    stats_path = DATA_DIR / "stats_summary.json"
    assert stats_path.is_file()
    stats_data = json.loads(stats_path.read_text(encoding="utf-8"))
    assert isinstance(stats_data.get("ownership_tier_counts"), dict)
    for tier_k in OWNERSHIP_TIER_KEYS:
        assert tier_k in stats_data["ownership_tier_counts"]

    # Check curated_overrides.json and buildings.geojson non-residential vs owner-occupied plex tiers
    from atlas_pipeline.schema import infer_fallback_ownership_tier

    assert (
        infer_fallback_ownership_tier(
            use_category="Civic / Institutional",
            owner_name="HARRIS COUNTY HOUSTON SPORTS AUTHORITY",
            landmark_name="Toyota Center",
        )
        == "civic_nonprofit"
    )
    assert (
        infer_fallback_ownership_tier(
            use_category="Commercial",
            owner_name="",
            landmark_name="Fulbright Tower",
            current_tier="homestead",
        )
        == "local_small"
    )
    assert (
        infer_fallback_ownership_tier(
            use_category="Multi-Family",
            owner_name="MORGAN MICHELLE",
            address="413 COLUMBIA ST",
            current_tier="homestead",
        )
        == "homestead"
    )
    assert (
        infer_fallback_ownership_tier(
            use_category="Multi-Family",
            owner_name="TSG LEASING LLC",
            address="1628 HARVARD ST",
            current_tier="homestead",
        )
        == "local_small"
    )

    ov_doc = json.loads((DATA_DIR / "curated_overrides.json").read_text(encoding="utf-8"))
    ov_map = ov_doc.get("overrides") if isinstance(ov_doc.get("overrides"), dict) else ov_doc
    assert ov_map["osm_city_25312761#bldg_toyota_center"]["own_tier"] == "civic_nonprofit"
    assert ov_map["1420820010001#aux_1"]["own_tier"] == "civic_nonprofit"
    assert ov_map["1319030010001#aux_1"]["own_tier"] == "civic_nonprofit"
