"""Hermetic unit and stage-idempotency tests for Milestone 2 Lost Houston expansion.

Covers:
  - UT-1: ZeroExtantCollisionGate, historical street-alias normalization, and Cohort 2 COH Landmark synchronization
  - UT-2: Cohort 3 NRHP demolished year_built backfill, relocated 85002771 exclusion, and search_index sync
  - UT-3: Two-stage Cohort 5 2020-2026 main-structure demolition filter and non-demolition negative probes
  - UT-4: Qualifier-aware lifespan formatting, CCW <=6dp ring compaction, and -34.65 deg Borden parcel fitting
  - IT-1: 1:1 parity across overlays.json['lost_houston'] (622) and search_index.json
  - IT-2: HOLC 1937 rollups (393 across 47 zones) and scorecard lhc parity
  - IT-3: Stage-order idempotency for upsert_search_index_slice, NRHP enrichment, COH sanitization, and HOLC rollups
  - TST-1: Strictly hermetic in-memory fixtures without live network requests or raw multi-hundred-MB gdb reads
"""

from __future__ import annotations

import ast
import builtins
import contextlib
import copy
import io
import json
from collections import Counter
from pathlib import Path

from shapely.geometry import Point, Polygon, shape

from atlas_pipeline.audit_historical_accuracy import (
    VALID_SANBORN_MATERIALS,
    has_excess_ring_precision,
    run_audit,
)
from atlas_pipeline.lost_houston_expansion import (
    COH_DEMOLISHED_LANDMARKS_METADATA,
    COH_LANDMARK_ENRICHMENTS_ON_CURATED,
    NRHP_DEMOLISHED_METADATA,
    ZeroExtantCollisionGate,
    _lookup_neighborhood_namer,
    build_lost_houston_photo_record,
    build_lost_houston_search_item,
    classify_main_structure_demolition_permit,
    compact_lost_houston_feature,
    compute_symbol_rank,
    enrich_nrhp_demolished_overlay_and_search,
    ensure_valid_ccw_ring_6dp,
    evaluate_cohort5_teardown_candidate,
    fit_oriented_ghost_footprint,
    format_demolished_lifespan,
    format_teardown_address,
    get_all_coh_demolished_landmarks_meta,
    normalize_houston_address_keys,
    reconcile_holc_lost_houston_rollups,
    sanitize_atlas_text,
    sanitize_coh_landmarks_overlay,
)
from atlas_pipeline.schema import (
    LOST_HOUSTON_COHORT_LABELS,
    LOST_HOUSTON_VALID_COHORTS,
    is_main_structure_demolition_scope,
    upsert_search_index_slice,
    write_compact_json,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "app" / "public" / "data"


def test_ut1_normalize_houston_address_keys_and_historical_aliases() -> None:
    keys_dowling = normalize_houston_address_keys("2222 Dowling Street, Houston, TX 77003")
    keys_emanc = normalize_houston_address_keys("2222 EMANCIPATION AVE")
    assert set(keys_dowling) & set(keys_emanc), (
        f"Expected historical Dowling St <-> Emancipation Ave alias intersection: {keys_dowling} vs {keys_emanc}"
    )

    keys_sf = normalize_houston_address_keys("1015 San Felipe St")
    keys_wd = normalize_houston_address_keys("1015 West Dallas Street")
    assert set(keys_sf) & set(keys_wd), (
        f"Expected historical San Felipe <-> W Dallas alias intersection: {keys_sf} vs {keys_wd}"
    )

    keys_hath = normalize_houston_address_keys("410 Hathaway Street")
    keys_west = normalize_houston_address_keys("410 Westheimer Rd")
    assert set(keys_hath) & set(keys_west)

    no_alias = normalize_houston_address_keys("2222 Dowling St", include_historic_aliases=False)
    assert all("EMANCIPATION" not in k for k in no_alias)


def test_ut1_zero_extant_collision_gate_blocks_all_extant_and_relocated_hazards() -> None:
    extant_hcads = {
        "0020590000001": {"label": "Wesley Chapel AME Church", "impr_val": 0, "year_built": 1926},
        "0010530000013": {"label": "Paul Building", "year_built": 1906},
    }
    extant_addrs = {}
    for k in normalize_houston_address_keys("2209 Emancipation Ave", include_historic_aliases=True):
        extant_addrs[k] = {"label": "Wesley Chapel AME Church", "year_built": 1926}
    relocated_hcads = {"0011190000015"}
    relocated_addrs = set(normalize_houston_address_keys("1711 Rusk Ave", include_historic_aliases=True))
    protected_points = [(-95.3601, 29.7595, "1206 Nance Street Bakery Building", 1908)]
    standing_pre1945_hcads = {"0201520000004": 1903}
    standing_poly = Polygon(
        [
            [-95.39020, 29.79010],
            [-95.39000, 29.79010],
            [-95.39000, 29.79025],
            [-95.39020, 29.79025],
            [-95.39020, 29.79010],
        ]
    )

    gate = ZeroExtantCollisionGate(
        extant_hcads=extant_hcads,
        extant_addrs=extant_addrs,
        relocated_hcads=relocated_hcads,
        relocated_addrs=relocated_addrs,
        protected_points=protected_points,
        standing_pre1945_hcads=standing_pre1945_hcads,
        standing_pre1945_polys=[standing_poly],
    )

    # 1. Blocks tax-exempt surviving church (impr_val == 0) matched by HCAD
    ok, reason = gate.check_candidate(
        source_cohort="red_book_1915_vanished",
        year_built=1911,
        year_demolished=1960,
        hcad_nums=["0020590000001"],
        address="2209 Dowling St",
    )
    assert not ok and "extant_hcad" in reason

    # 2. Blocks historical alias collision ("2209 Dowling Street" == extant "2209 Emancipation Ave")
    ok, reason = gate.check_candidate(
        source_cohort="red_book_1915_vanished",
        year_built=1910,
        year_demolished=1955,
        hcad_nums=[],
        address="2209 Dowling Street",
    )
    assert not ok and "extant_address" in reason

    # 3. Blocks relocated structure by HCAD and by original address (Arthur B. Cohn House 1711 Rusk)
    ok_h, reason_h = gate.check_candidate(
        source_cohort="nrhp_demolished",
        year_built=1905,
        year_demolished=2003,
        hcad_nums=["0011190000015"],
        address="1711 Rusk Ave",
    )
    assert not ok_h and "relocated_hcad" in reason_h

    ok_a, reason_a = gate.check_candidate(
        source_cohort="red_book_1915_vanished",
        year_built=1905,
        year_demolished=1970,
        address="1711 Rusk Avenue",
    )
    assert not ok_a and "relocated_address" in reason_a

    # 4. Blocks standing pre-1945 HCAD parcel from Cohort 5 teardown entry
    ok_s, reason_s = gate.check_candidate(
        source_cohort="historic_teardown_2020_2026",
        year_built=1920,
        year_demolished=2023,
        hcad_nums=["0201520000004"],
        address="1435 Heights Blvd",
    )
    assert not ok_s and "standing_pre1945_hcad" in reason_s

    # 5. Blocks 14m point proximity to protected extant monument (1206 Nance St)
    ok_prox, reason_prox = gate.check_candidate(
        source_cohort="red_book_1915_vanished",
        year_built=1905,
        year_demolished=1960,
        address="1208 Nance St",
        lon=-95.36012,
        lat=29.75951,
    )
    assert not ok_prox and "extant_point_proximity_14m" in reason_prox

    # 6. Blocks polygon overlapping >20% of an extant pre-1945 building footprint
    overlap_poly = Polygon(
        [
            [-95.39019, 29.79011],
            [-95.39001, 29.79011],
            [-95.39001, 29.79024],
            [-95.39019, 29.79024],
            [-95.39019, 29.79011],
        ]
    )
    ok_p, reason_p = gate.check_candidate(
        source_cohort="historic_teardown_2020_2026",
        year_built=1925,
        year_demolished=2022,
        hcad_nums=["9999999999999"],
        address="999 Nowhere St",
        polygon=overlap_poly,
    )
    assert not ok_p and "standing_pre1945_polygon_overlap" in reason_p

    # 7. Admits genuinely cleared historical parcel with zero extant collisions
    clear_poly = Polygon(
        [
            [-95.37020, 29.74010],
            [-95.37005, 29.74010],
            [-95.37005, 29.74022],
            [-95.37020, 29.74022],
            [-95.37020, 29.74010],
        ]
    )
    ok_good, reason_good = gate.check_candidate(
        source_cohort="historic_teardown_2020_2026",
        year_built=1924,
        year_demolished=2024,
        hcad_nums=["1112223334445"],
        address="1410 Cleburne St",
        lon=-95.37012,
        lat=29.74016,
        polygon=clear_poly,
    )
    assert ok_good and reason_good == "ok"


def test_ut1_coh_landmarks_sanitization_and_reconciliation() -> None:
    fake_fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "id": "coh_lm_232",
                    "name": "Mount Carmel Missionary Baptist Church",
                    "hcad_num": "Demolished",
                    "demolished": False,
                    "alt_names": ["Jefferson Davis Hospital Boiler Room Building", "Historic Chapel"],
                },
                "geometry": {"type": "Point", "coordinates": [-95.36, 29.75]},
            },
            {
                "type": "Feature",
                "properties": {
                    "id": "coh_lm_089",
                    "name": "1206 Nance Street (Erroneous Demolished String)",
                    "hcad_num": "Demolished",
                    "demolished": True,
                    "alt_addresses": ["polluted address"],
                    "haif_threads": [{"title": "polluted thread"}],
                },
                "geometry": {"type": "Point", "coordinates": [-95.35, 29.76]},
            },
            {
                "type": "Feature",
                "properties": {
                    "id": "coh_lm_081",
                    "name": "The Jefferson Davis Hospital Buildings",
                    "hcad_num": "1244130010002",
                    "demolished": False,
                },
                "geometry": {"type": "Point", "coordinates": [-95.38, 29.76]},
            },
            {
                "type": "Feature",
                "properties": {
                    "id": "coh_lm_220",
                    "name": "William L. Edmundson House",
                    "hcad_num": "Demolished",
                    "demolished": False,
                },
                "geometry": {"type": "Point", "coordinates": [-95.39, 29.74]},
            },
        ],
    }
    fake_si = [
        {
            "type": "landmark",
            "id": "coh_lm_232",
            "hcad_num": "Demolished",
            "alt_names": ["Jefferson Davis Hospital Boiler Room Building", "Historic Chapel"],
        },
        {
            "type": "landmark",
            "id": "coh_lm_081",
            "hcad_num": "1244130010002",
            "alt_names": ["Jefferson Davis Hospital Boiler Room Building"],
        },
        {
            "type": "landmark",
            "id": "coh_lm_220",
            "hcad_num": "Demolished",
        },
    ]

    stats = sanitize_coh_landmarks_overlay(fake_fc, fake_si)
    assert stats["repaired_hcad"] >= 2
    assert stats["repaired_status"] >= 2
    assert stats["repaired_search"] >= 2
    p232 = fake_fc["features"][0]["properties"]
    p089 = fake_fc["features"][1]["properties"]
    p081 = fake_fc["features"][2]["properties"]
    p220 = fake_fc["features"][3]["properties"]

    assert p232["hcad_num"] == ""
    assert p232.get("site_hcad_num", "") == ""
    assert p232["demolished"] is True
    assert p232["city_tax_exemption_eligible"] is False
    assert p232["alt_names"] == ["Historic Chapel"]
    assert p089["hcad_num"] == ""
    assert p089["demolished"] is False, "Extant coh_lm_089 (1206 Nance St) must not be marked demolished"
    assert "alt_addresses" not in p089 and "haif_threads" not in p089
    assert p081["hcad_num"] == ""
    assert p081["site_hcad_num"] == "1244130010002"
    assert p081["demolished"] is True
    assert p081["year_demolished"] == 1999
    assert p081["city_tax_exemption_eligible"] is False
    assert p220["hcad_num"] == ""
    assert p220["site_hcad_num"] == "0601550450004"
    assert p220["demolished"] is True

    assert fake_si[0]["hcad_num"] == ""
    assert fake_si[0]["demolished"] is True
    assert "alt_names" not in fake_si[0]
    assert fake_si[1]["hcad_num"] == ""
    assert fake_si[1]["site_hcad_num"] == "1244130010002"
    assert fake_si[1]["demolished"] is True
    assert fake_si[1]["alt_names"] == ["Jefferson Davis Hospital Boiler Room Building"]
    assert fake_si[2]["hcad_num"] == ""
    assert fake_si[2]["site_hcad_num"] == "0601550450004"
    assert fake_si[2]["demolished"] is True

    assert len(COH_DEMOLISHED_LANDMARKS_METADATA) == 7
    assert len(get_all_coh_demolished_landmarks_meta()) == 8
    assert "coh_lm_089" not in COH_DEMOLISHED_LANDMARKS_METADATA
    assert COH_LANDMARK_ENRICHMENTS_ON_CURATED["lost_jefferson_davis_hospital_1937"]["coh_landmark_id"] == "coh_lm_081"


def test_ut2_nrhp_demolished_backfill_and_relocated_85002771_handling() -> None:
    assert len(NRHP_DEMOLISHED_METADATA) == 16
    assert NRHP_DEMOLISHED_METADATA["85002771"]["relocated_not_demolished"] is True
    assert NRHP_DEMOLISHED_METADATA["85002771"]["year_built"] == 1905

    nrhp_fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "id": f"nrhp_{ref}",
                    "refnum": ref,
                    "name": f"Test NRHP {ref}",
                    "is_demolished_nrhp": True,
                    "year_built": 0,
                },
                "geometry": {"type": "Point", "coordinates": [-95.36, 29.75]},
            }
            for ref in sorted(NRHP_DEMOLISHED_METADATA.keys())
        ],
    }
    search_items = [
        {"type": "nrhp_property", "id": f"nrhp_{ref}", "refnum": ref, "year_built": 0}
        for ref in sorted(NRHP_DEMOLISHED_METADATA.keys())
    ]

    updated = enrich_nrhp_demolished_overlay_and_search(nrhp_fc, search_items)
    assert updated == 16

    for feat in nrhp_fc["features"]:
        p = feat["properties"]
        assert isinstance(p["year_built"], int) and 1836 <= p["year_built"] <= 1975
        assert isinstance(p["year_demolished"], int) and p["year_demolished"] >= p["year_built"]
        if p["refnum"] == "85002771":
            assert p.get("was_relocated") is True
            assert p["year_built"] == 1905

    for si in search_items:
        assert isinstance(si["year_built"], int) and si["year_built"] >= 1836
        assert isinstance(si["year_demolished"], int) and si["year_demolished"] >= si["year_built"]

    # Idempotency check on second pass
    enrich_again = copy.deepcopy((nrhp_fc, search_items))
    enrich_nrhp_demolished_overlay_and_search(nrhp_fc, search_items)
    assert (nrhp_fc, search_items) == enrich_again


def test_ut3_two_stage_demolition_classifier_and_cohort5_evaluator() -> None:
    negative_permits = [
        ("DEMO DETACHED GARAGE ONLY", "13", "2022-05-10", ""),
        ("GARAGE APT DEMO IN REAR", "33", "2023-02-11", ""),
        ("DETACHED GARAGE BUILDING DEMO ONLY", "13", "2023-03-08", ""),
        ("DEMO BUILDING CANOPY AND CARPORT", "13", "2022-10-14", ""),
        ("DEMO 1/2 DUPLEX UNIT B", "13", "2021-08-19", "B"),
        ("CHECK FOR DEMO PRIOR TO REMODEL", "43", "2024-01-12", ""),
        ("INTERIOR NON-STRUCTURAL SELECTIVE DEMO", "SD", "2023-07-04", ""),
        ("SWIMMING POOL DEMO AND BACKFILL", "13", "2022-09-30", ""),
        ("CARPORT AND SHED REMOVAL", "13", "2021-03-15", ""),
        ("PARTIAL DEMO FOR KITCHEN ADDITION", "13", "2024-06-01", ""),
        ("DEMO HOUSE AND CLEAR LOT", "13", "2019-11-20", ""),
        ("DEMO HOUSE SENTINEL DATE", "13", "12/30/1899", ""),
    ]
    for desc, ptype, dt, apt in negative_permits:
        ok, _, reason = classify_main_structure_demolition_permit(
            desc, ptype, dt, site_apt=apt, min_year=2020, max_year=2026
        )
        assert not ok, f"Negative probe '{desc}' ({dt}) must be rejected, got ok=True ({reason})"

    # Direct shared screener oracles (scorecards call is_main_structure_demolition_scope directly)
    for denied_scope in (
        "DEMO DETACHED GARAGE ONLY",
        "SWIMMING POOL DEMO AND BACKFILL",
        "CARPORT AND SHED REMOVAL",
        "RESIDENTIAL SWIMMING POOL DEMO",
    ):
        assert is_main_structure_demolition_scope(denied_scope) is False, denied_scope
    assert is_main_structure_demolition_scope("DEMO HOUSE & GARAGE") is True
    assert is_main_structure_demolition_scope("DEMO SFR AND CLEAR LOT") is True

    # COH SD structural permit for a residential swimming pool must still be rejected by _RE_MAIN_DEMO_DENY
    ok_pool_sd, _, reason_pool_sd = classify_main_structure_demolition_permit(
        "RESIDENTIAL SWIMMING POOL DEMO",
        "SD",
        "2026-02-14",
        permit_desc="Demolition",
        min_year=2020,
        max_year=2026,
    )
    assert ok_pool_sd is False and reason_pool_sd == "denied_accessory_or_partial"

    ok_main, yr_main, _ = classify_main_structure_demolition_permit(
        "DEMO SFR AND CLEAR LOT", "13", "2023-04-18", min_year=2020, max_year=2026
    )
    assert ok_main is True and yr_main == 2023

    ok_combo, yr_combo, _ = classify_main_structure_demolition_permit(
        "DEMO HOUSE & GARAGE", "13", "2022-06-01", min_year=2020, max_year=2026
    )
    assert ok_combo is True and yr_combo == 2022

    ok_coh_sd, yr_coh_sd, _ = classify_main_structure_demolition_permit(
        "DEMOLITION OF SINGLE FAMILY RESIDENCE",
        "SD",
        "2026-02-14",
        permit_desc="Demolition",
        min_year=2020,
        max_year=2026,
    )
    assert ok_coh_sd is True and yr_coh_sd == 2026

    # Post-1940 cutoff must be strictly blocked (<= 1940 only)
    ok_1945, _ = evaluate_cohort5_teardown_candidate(
        "0201950000099",
        permit_description="DEMO HOUSE CLEAR LOT",
        permit_type="13",
        permit_issue_date="2023-05-10",
        cama_year_built=1945,
        cama_bld_area=0.0,
        cama_state_class="C1",
    )
    assert not ok_1945, "Post-1940 (1945) demolition must be rejected by <= 1940 Cohort 5 cutoff"

    # Non-cleared parcel where original 1925 house still stands (active bld_area > 0, no post-2000 rebuild)
    ok_standing, _ = evaluate_cohort5_teardown_candidate(
        "0201950000098",
        permit_description="DEMO HOUSE CLEAR LOT",
        permit_type="13",
        permit_issue_date="2022-08-01",
        cama_year_built=1925,
        cama_bld_area=1850.0,
        cama_state_class="A1",
    )
    assert not ok_standing, "Standing pre-1940 CAMA A1 house must be rejected"

    # Relocated structure flag must be rejected
    ok_reloc, _ = evaluate_cohort5_teardown_candidate(
        "0201950000097",
        permit_description="DEMO HOUSE CLEAR LOT",
        permit_type="13",
        permit_issue_date="2022-08-01",
        cama_year_built=1912,
        cama_bld_area=0.0,
        cama_state_class="C1",
        was_relocated=True,
    )
    assert not ok_reloc

    # Confirmed cleared pre-1940 teardown with rounded HCAD decade year -> circa qualifier + tier 3
    ok_valid, meta = evaluate_cohort5_teardown_candidate(
        "0201950000022",
        permit_description="DEMO HOUSE CLEAR LOT",
        permit_type="13",
        permit_issue_date="2024-03-12",
        cama_year_built=1920,
        cama_bld_area=0.0,
        cama_state_class="C1",
    )
    assert ok_valid is True
    assert meta["year_built"] == 1920
    assert meta["year_demolished"] == 2024
    assert meta["provenance_tier"] == 3
    assert meta["year_built_qualifier"] == "circa"


def test_ut4_lifespan_qualifiers_geometry_compaction_and_borden_fitting() -> None:
    exact = format_demolished_lifespan(1883, 1912, "exact", "exact")
    assert exact["year_built_display"] == "1883"
    assert exact["year_demolished_display"] == "1912"
    assert exact["lifespan_label"] == "1883-1912"

    circa = format_demolished_lifespan(1905, 1968, "circa", "before")
    assert circa["year_built_display"] == "c. 1905"
    assert circa["year_demolished_display"] == "before 1968"
    assert circa["lifespan_label"] == "c. 1905 - pre-1968"

    rb = format_demolished_lifespan(1915, 1950, "before", "after")
    assert rb["year_built_display"] == "On or before 1915"
    assert rb["year_demolished_display"] == "after 1950"
    assert rb["lifespan_label"] == "<=1915 - post-1950"
    for v in rb.values():
        assert "\u2014" not in v and "\u2013" not in v

    # Clockwise ring with 9 decimal places must be reversed to CCW and rounded to <=6 decimals
    cw_ring = [
        [-95.3626021234, 29.7603119876],
        [-95.3626021234, 29.7604519876],
        [-95.3624511234, 29.7604519876],
        [-95.3624511234, 29.7603119876],
        [-95.3626021234, 29.7603119876],
    ]
    ccw = ensure_valid_ccw_ring_6dp(cw_ring)
    poly = Polygon(ccw)
    assert poly.is_valid and poly.exterior.is_ccw
    assert ccw[0] == ccw[-1]
    for x, y in ccw:
        assert len(str(x).split(".")[1]) <= 6
        assert len(str(y).split(".")[1]) <= 6

    # Priority 1: pre_demo_ring wins with ms_osm_pre_demo_footprint
    ring1, lx1, ly1, prov1 = fit_oriented_ghost_footprint(
        -95.3625, 29.76038, 12.0, 18.0, pre_demo_ring=cw_ring
    )
    assert prov1 == "ms_osm_pre_demo_footprint"
    assert Polygon(ring1).exterior.is_ccw
    assert Polygon(ring1).covers(Point(lx1, ly1))

    # Priority 2: parcel_records + fit_rect_fn produces ghost_parcel_aligned with -34.65 deg Borden snap
    captured_angles: list[float] = []

    def fake_fit_rect(
        lon: float, lat: float, w: float, d: float, ang: float, rings: list, **kwargs: object
    ) -> tuple[float, float, float, float]:
        captured_angles.append(ang)
        return lon, lat, w, d

    parcel_ring = ensure_valid_ccw_ring_6dp(cw_ring)
    ring2, _, _, prov2 = fit_oriented_ghost_footprint(
        -95.36252,
        29.76038,
        10.0,
        16.0,
        parcel_records=[
            {"ring": parcel_ring, "lon": -95.36252, "lat": 29.76038, "angle": -34.2, "max_edge_m": 30.0}
        ],
        neighborhood="Downtown",
        address="800 Main St",
        fit_rect_fn=fake_fit_rect,
    )
    assert prov2 == "ghost_parcel_aligned"
    assert captured_angles == [-34.65], f"Expected Borden Survey -34.65 deg snap, got {captured_angles}"
    assert Polygon(ring2).exterior.is_ccw

    # Priority 3: street-aligned fallback
    ring3, lx3, ly3, prov3 = fit_oriented_ghost_footprint(
        -95.36252, 29.76038, 10.0, 16.0, neighborhood="Downtown", address="800 Main St"
    )
    assert prov3 == "ghost_street_aligned"
    assert Polygon(ring3).exterior.is_ccw

    # Compact feature guarantees pure integers on year_built/year_demolished & valid symbol_rank
    feat = compact_lost_houston_feature(
        {
            "id": "lost_test_church",
            "name": "Historic Test Chapel\u2014Freedmen's Town",
            "address": "1200 Andrews St",
            "neighborhood": "Fourth Ward",
            "category": "church",
            "year_built": 1905,
            "year_demolished": 1962,
            "year_built_qualifier": "circa",
            "year_demolished_qualifier": "before",
            "provenance_tier": 2,
            "symbol_rank": compute_symbol_rank(
                "red_book_1915_vanished", category="church", red_book_category="church"
            ),
            "replaced_by": "Vacant grassy lot\u2013cleared",
            "citations": [{"label": "1915 Red Book of Houston, p. 42"}],
            "lon": lx3,
            "lat": ly3,
        },
        ring3,
        source_cohort="red_book_1915_vanished",
        footprint_provenance=prov3,
    )
    fp = feat["properties"]
    assert isinstance(fp["year_built"], int) and fp["year_built"] == 1905
    assert isinstance(fp["year_demolished"], int) and fp["year_demolished"] == 1962
    assert fp["year_built_is_circa"] is True
    assert fp["year_demolished_is_circa"] is True
    assert fp["year_built_display"] == "c. 1905"
    assert fp["year_demolished_display"] == "before 1962"
    assert fp["lifespan_label"] == "c. 1905 - pre-1962"
    assert fp["symbol_rank"] == 2
    assert fp["source_cohort_label"] == LOST_HOUSTON_COHORT_LABELS["red_book_1915_vanished"]
    dumped = json.dumps(feat, ensure_ascii=False)
    assert "\u2014" not in dumped and "\u2013" not in dumped

    si = build_lost_houston_search_item(feat)
    assert si["overlay_layer"] == "lost_houston"
    assert isinstance(si["year_built"], int) and isinstance(si["year_demolished"], int)
    photo = build_lost_houston_photo_record(
        building_id="lost_test_church",
        landmark_name="Historic Test Chapel",
        image_url="https://quartex.rice.edu/iiif/test/full/max/0/default.jpg",
        caption="Plate\u20141915 Red Book",
    )
    assert photo["is_lost_houston"] is True
    assert "\u2014" not in photo["caption"]
    assert "\u2014" not in sanitize_atlas_text("1895\u20131960 \u2014 demolished")
    assert format_teardown_address("829 W  19TH ST") == "829 W 19th St"
    assert format_teardown_address("804 W  23RD ST") == "804 W 23rd St"
    assert format_teardown_address("248 W  22ND ST") == "248 W 22nd St"
    assert format_teardown_address("815 EMANCIPATION AV") == "815 Emancipation Ave"


def test_it1_it2_it3_live_artifacts_rollups_idempotency_and_audit(tmp_path: Path) -> None:
    overlays = json.loads((DATA_DIR / "overlays.json").read_text())
    search_index = json.loads((DATA_DIR / "search_index.json").read_text())
    scorecards_doc = json.loads((DATA_DIR / "neighborhood_scorecards.json").read_text())

    lh_feats = overlays["lost_houston"]["features"]
    assert len(lh_feats) == 622
    cohorts = Counter(f["properties"]["source_cohort"] for f in lh_feats)
    assert set(cohorts.keys()) == set(LOST_HOUSTON_VALID_COHORTS)
    assert cohorts == {
        "curated_landmark": 85,
        "coh_landmark_demolished": 7,
        "nrhp_demolished": 15,
        "red_book_1915_vanished": 205,
        "historic_teardown_2020_2026": 310,
    }

    # IT-1: 1:1 search_index parity
    lh_ids = [f["properties"]["id"] for f in lh_feats]
    si_lh = [r for r in search_index if r.get("overlay_layer") == "lost_houston" or r.get("type") == "lost_houston"]
    si_lh_ids = [r["id"] for r in si_lh]
    assert len(si_lh_ids) == 622
    assert len(set(si_lh_ids)) == 622
    assert set(si_lh_ids) == set(lh_ids)

    # IT-2: HOLC 1937 zones & grade_summary sync + scorecard lhc parity
    holc_feats = overlays["holc_1937_zones"]["features"]
    assert len(holc_feats) == 47
    zone_total = sum(int(z["properties"].get("lost_houston_count") or 0) for z in holc_feats)
    grade_summary = overlays["holc_1937_grade_summary"]
    grade_total = sum(int(g.get("lost_houston_count") or 0) for g in grade_summary)
    assert zone_total == grade_total == 393

    sc_rows = scorecards_doc["scorecards"]
    assert len(sc_rows) == 4242
    by_id = {r["id"]: r for r in sc_rows}
    assert by_id["council_dist_d"]["lhc"] == 73
    assert by_id["council_dist_c"]["lhc"] == 178
    assert len(by_id["council_dist_d"]["dm"]) == 7

    # IT-3: Re-running reconcile_holc_lost_houston_rollups on an isolated copy is byte-identical
    holc_copy = {
        "lost_houston": copy.deepcopy(overlays["lost_houston"]),
        "holc_1937_zones": copy.deepcopy(overlays["holc_1937_zones"]),
        "holc_1937_grade_summary": copy.deepcopy(overlays["holc_1937_grade_summary"]),
    }
    stats_rerun = reconcile_holc_lost_houston_rollups(holc_copy)
    assert stats_rerun == {
        "assigned_in_holc": 393,
        "grade_A": 44,
        "grade_B": 42,
        "grade_C": 139,
        "grade_D": 168,
    }
    assert holc_copy["holc_1937_grade_summary"] == overlays["holc_1937_grade_summary"]
    assert [
        z["properties"]["lost_houston_count"] for z in holc_copy["holc_1937_zones"]["features"]
    ] == [z["properties"]["lost_houston_count"] for z in holc_feats]

    # IT-3: upsert_search_index_slice stage-order idempotency & set commutativity
    base_si = [{"id": "bld_1", "type": "building"}, {"id": "nb_1", "is_neighborhood_entry": True}]
    lh_slice = [{"id": "lost_a", "overlay_layer": "lost_houston"}, {"id": "lost_b", "overlay_layer": "lost_houston"}]
    rb_slice = [{"id": "rb1915_1", "type": "red_book_1915"}]

    is_lh = lambda r: r.get("overlay_layer") == "lost_houston"
    is_rb = lambda r: r.get("type") == "red_book_1915"

    order_a = upsert_search_index_slice(base_si, slice_predicate=is_lh, new_entries=lh_slice)
    order_a = upsert_search_index_slice(order_a, slice_predicate=is_rb, new_entries=rb_slice)
    order_a_twice = upsert_search_index_slice(order_a, slice_predicate=is_lh, new_entries=lh_slice)
    assert [r["id"] for r in order_a_twice if is_lh(r)] == ["lost_a", "lost_b"]
    assert len(order_a_twice) == 5

    order_b = upsert_search_index_slice(base_si, slice_predicate=is_rb, new_entries=rb_slice)
    order_b = upsert_search_index_slice(order_b, slice_predicate=is_lh, new_entries=lh_slice)
    assert {r["id"] for r in order_a} == {r["id"] for r in order_b}

    # SIM-1: write_compact_json atomic single-line writer with dash sanitization
    out_file = tmp_path / "compact_test.json"
    write_compact_json(out_file, {"title": "1883\u20131912 \u2014 Capitol Hotel", "ok": True})
    raw_txt = out_file.read_text(encoding="utf-8")
    assert "\n" not in raw_txt
    assert "\u2014" not in raw_txt and "\u2013" not in raw_txt
    assert json.loads(raw_txt)["title"] == "1883-1912 - Capitol Hotel"

    # Full live geometry & zero-collision audit pass
    for f in lh_feats:
        g = shape(f["geometry"])
        assert g.is_valid and not g.is_empty and g.geom_type == "Polygon"
        assert f["properties"]["sanborn_material"] in VALID_SANBORN_MATERIALS, f["properties"]["id"]

    with contextlib.redirect_stdout(io.StringIO()):
        audit_res = run_audit(apply_fixes=False)
        audit_cache_free = run_audit(apply_fixes=False, cama_override={})
    for label, res in (("live", audit_res), ("cache_free", audit_cache_free)):
        rf = res["summary"]["reconciled_fixes"]
        assert rf["lost_houston_features_audited"] == 622, label
        assert rf["lost_houston_schema_errors"] == 0, label
        assert rf["lost_houston_extant_collision_errors"] == 0, label
        assert rf["landmarks_marked_demolished"] == 8, label


def _collect_scope_bindings(node: ast.AST) -> set[str]:
    """Collect bound variable names inside a module, class, function, or comprehension scope."""
    bound: set[str] = set()
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        args = node.args
        for a in (*args.posonlyargs, *args.args, *args.kwonlyargs):
            bound.add(a.arg)
        if args.vararg:
            bound.add(args.vararg.arg)
        if args.kwarg:
            bound.add(args.kwarg.arg)
    for child in ast.iter_child_nodes(node):
        if isinstance(
            child,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
                ast.ClassDef,
                ast.Lambda,
                ast.ListComp,
                ast.SetComp,
                ast.DictComp,
                ast.GeneratorExp,
            ),
        ):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bound.add(child.name)
            continue
        for sub in ast.walk(child):
            if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                bound.add(sub.id)
            elif isinstance(sub, (ast.Import, ast.ImportFrom)):
                for alias in sub.names:
                    bound.add((alias.asname or alias.name).split(".")[0])
            elif isinstance(sub, ast.ExceptHandler) and sub.name:
                bound.add(sub.name)
            elif isinstance(sub, ast.NamedExpr) and isinstance(sub.target, ast.Name):
                bound.add(sub.target.id)
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        for gen in node.generators:
            for sub in ast.walk(gen.target):
                if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                    bound.add(sub.id)
    return bound


def _find_unresolved_globals(py_path: Path) -> set[str]:
    tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
    builtin_names = set(dir(builtins)) | {"__file__", "__name__", "__package__", "__doc__"}
    unresolved: set[str] = set()

    def _walk(node: ast.AST, visible: set[str], in_annotation: bool = False) -> None:
        if in_annotation:
            return
        local_vis = visible
        if isinstance(
            node,
            (
                ast.Module,
                ast.FunctionDef,
                ast.AsyncFunctionDef,
                ast.ClassDef,
                ast.Lambda,
                ast.ListComp,
                ast.SetComp,
                ast.DictComp,
                ast.GeneratorExp,
            ),
        ):
            local_vis = visible | _collect_scope_bindings(node)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id not in local_vis:
                unresolved.add(node.id)
        annotation_fields: set[ast.AST] = set()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.returns:
                annotation_fields.add(node.returns)
            for a in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs, node.args.vararg, node.args.kwarg):
                if a is not None and a.annotation is not None:
                    annotation_fields.add(a.annotation)
        elif isinstance(node, ast.AnnAssign) and node.annotation is not None:
            annotation_fields.add(node.annotation)
        for child in ast.iter_child_nodes(node):
            _walk(child, local_vis, in_annotation=(child in annotation_fields))

    _walk(tree, builtin_names)
    return unresolved


def test_ut5_precision_predicate_neighborhood_namer_and_module_globals() -> None:
    # 1. Precision helper verifies both longitude AND latitude independently (High #5)
    assert has_excess_ring_precision([[-95.36327, 29.7603119876], [-95.363, 29.76]]) is True
    assert has_excess_ring_precision([[-95.36327098, 29.76031], [-95.363, 29.76]]) is True
    assert has_excess_ring_precision([[-95.363271, 29.760312], [-95.363, 29.76]]) is False

    # 2. _lookup_neighborhood_namer resolves canonical 'historic_districts' & 'super_neighborhoods.name' (Medium #6)
    fake_overlays = {
        "historic_districts": {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"name": "Houston Heights Historic District East"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [-95.396, 29.795],
                                [-95.390, 29.795],
                                [-95.390, 29.802],
                                [-95.396, 29.802],
                                [-95.396, 29.795],
                            ]
                        ],
                    },
                }
            ],
        },
        "super_neighborhoods": {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"name": "GREATER HEIGHTS"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [-95.410, 29.780],
                                [-95.380, 29.780],
                                [-95.380, 29.820],
                                [-95.410, 29.820],
                                [-95.410, 29.780],
                            ]
                        ],
                    },
                },
                {
                    "type": "Feature",
                    "properties": {"name": "IAH / AIRPORT AREA"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [-95.360, 29.970],
                                [-95.320, 29.970],
                                [-95.320, 30.000],
                                [-95.360, 30.000],
                                [-95.360, 29.970],
                            ]
                        ],
                    },
                },
            ],
        },
    }
    resolve_nh = _lookup_neighborhood_namer(fake_overlays)
    assert resolve_nh(-95.393, 29.798) == ("Greater Heights", "Houston Heights Historic District East")
    assert resolve_nh(-95.405, 29.790) == ("Greater Heights", "")
    assert resolve_nh(-95.340, 29.985) == ("IAH / Airport Area", "")
    assert resolve_nh(-95.700, 29.600) == ("Houston", "")

    # 3. AST unresolved-global gate across all Lost Houston pipeline entrypoints (High #4)
    pipeline_pkg = REPO_ROOT / "pipeline" / "atlas_pipeline"
    for mod_name in (
        "schema.py",
        "lost_houston_expansion.py",
        "build_lost_houston_and_sanborn.py",
        "build_preservation_layers.py",
        "build_social_history_layers.py",
        "build_neighborhood_scorecards.py",
        "build_permits_and_contractors.py",
        "audit_historical_accuracy.py",
    ):
        missing = _find_unresolved_globals(pipeline_pkg / mod_name)
        assert not missing, f"{mod_name} has unresolved global symbol(s): {sorted(missing)}"
