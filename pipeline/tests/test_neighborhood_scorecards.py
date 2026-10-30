"""Unit and artifact tests for Milestone 1 Neighborhood & Council District Scorecards (UT-PY-1..5)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from shapely import STRtree
from shapely.geometry import MultiPolygon, Point, Polygon, shape

from atlas_pipeline.build_neighborhood_scorecards import (
    COH_COUNCIL_ROSTER_2024_2028,
    COUNCIL_TERM_EXPIRES,
    DECADE_BINS,
    DEMO_YEARS,
    MIN_SUBDIVISION_STANDING_STRUCTURES,
    RIBBON_SMBL_BUFFER_FT,
    RIBBON_SMBL_RATIO_THRESHOLD_FT,
    build_council_search_entries,
    classify_parcel_ownership,
    fetch_and_cache_council_districts,
    hydrate_land_use_protections_lifecycle,
    is_main_structure_demolition_desc,
    match_ch42_protected_parcels,
    normalize_hcad13,
    normalize_situs_compare_key,
    parse_demolition_permit_row,
    reconcile_standing_buildings,
    upsert_council_districts_in_overlays_and_search,
)
from atlas_pipeline.schema import (
    ORD_2015_639_CUTOFF_ISO,
    ORD_2015_639_CUTOFF_ORD_NUM,
    is_boundary_search_entry,
    resolve_ch42_ordinance_lifecycle,
    sanitize_atlas_text,
    upsert_search_index_slice,
    write_compact_json,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "app" / "public" / "data"
SCORECARDS_JSON = DATA_DIR / "neighborhood_scorecards.json"
OVERLAYS_JSON = DATA_DIR / "overlays.json"
SEARCH_INDEX_JSON = DATA_DIR / "search_index.json"


# ==============================================================================
# UT-PY-1: Two-Grain StandingBuildingIndex vs UniqueParcelIndex & Exclusions
# ==============================================================================


def test_ut_py_1_two_grain_reconcile_buildings_and_parcel_grain_separation() -> None:
    """Verify standing building reconciliation honors suppress_only, #slug overrides, MultiPolygon expansion, and separates footprint vs parcel grain (DF-4)."""
    square = {
        "type": "Polygon",
        "coordinates": [
            [
                [-95.388, 29.790],
                [-95.3879, 29.790],
                [-95.3879, 29.7901],
                [-95.388, 29.7901],
                [-95.388, 29.790],
            ]
        ],
    }

    # 1. Parcel A has 5 apartment buildings on ONE HCAD tax parcel (tests building grain = 5 vs parcel grain = 1!)
    apt_shards = [
        {
            "type": "Feature",
            "properties": {
                "id": f"0660010010001_{i}",
                "hcad_num": "0660010010001",
                "year_built": 1938,
                "historic_district": "Norhill Historic District",
            },
            "geometry": square,
        }
        for i in range(5)
    ]
    # 2. Parcel B is suppressed as demolished/vacant (suppress_only: True) -> MUST produce 0 standing buildings!
    demolished_shard = {
        "type": "Feature",
        "properties": {
            "id": "0660010010002",
            "hcad_num": "0660010010002",
            "year_built": 1920,
        },
        "geometry": square,
    }
    # 3. Parcel C has undated structure (year_built=0) -> MUST be excluded from dated standing count!
    undated_shard = {
        "type": "Feature",
        "properties": {
            "id": "0660010010003",
            "hcad_num": "0660010010003",
            "year_built": 0,
        },
        "geometry": square,
    }
    # 4. Parcel D receives attribute-only curator year/landmark override + independent #rear_cottage sub-building override!
    curated_shard = {
        "type": "Feature",
        "properties": {
            "id": "0660010010004",
            "hcad_num": "0660010010004",
            "year_built": 1950,
            "historic_district": "Outside Historic District",
        },
        "geometry": square,
    }

    overrides: dict[str, dict[str, Any]] = {
        "0660010010002": {
            "hcad_num": "0660010010002",
            "suppress_only": True,
        },
        "0660010010004": {
            "hcad_num": "0660010010004",
            "year_built": 1914,
            "landmark_type": "PLM",
            "landmark_name": "Historic 1914 House",
        },
        "0660010010004#rear_cottage": {
            "id": "0660010010004#rear_cottage",
            "building_id": "0660010010004#rear_cottage",
            "hcad_num": "0660010010004",
            "is_building_override": True,
            "year_built": 1926,
            "geometry": square,
        },
        "0660010010005": {
            "id": "0660010010005",
            "hcad_num": "0660010010005",
            "year_built": 1929,
            "geometry": {
                "type": "MultiPolygon",
                "coordinates": [square["coordinates"], square["coordinates"]],
            },
        },
    }

    # 5. Parcel F carries suppress_only: True directly on the shard feature -> MUST also be filtered out!
    suppressed_in_shard = {
        "type": "Feature",
        "properties": {
            "id": "0660010010099",
            "hcad_num": "0660010010099",
            "year_built": 1922,
            "suppress_only": True,
        },
        "geometry": square,
    }

    standing = reconcile_standing_buildings(
        [*apt_shards, demolished_shard, undated_shard, curated_shard, suppressed_in_shard],
        overrides,
    )
    # 5 apartment bldgs + 1 main curated house + 1 #rear_cottage + 2 expanded MultiPolygon wings = 9 standing buildings!
    assert len(standing) == 9
    hcad_counts: dict[str, int] = {}
    for b in standing:
        hcad_counts[b["hcad_num"]] = hcad_counts.get(b["hcad_num"], 0) + 1

    # Demolished (0002), undated (0003), and shard-suppressed (0099) are strictly absent
    assert "0660010010002" not in hcad_counts
    assert "0660010010003" not in hcad_counts
    assert "0660010010099" not in hcad_counts
    # Grain contrast: 9 standing footprints reside across only 3 distinct 13-digit HCAD parcels!
    assert hcad_counts == {
        "0660010010001": 5,
        "0660010010004": 2,
        "0660010010005": 2,
    }
    assert len(set(hcad_counts.keys())) == 3

    curated_main = next(b for b in standing if b["id"] == "0660010010004")
    assert curated_main["year_built"] == 1914
    assert curated_main["landmark_type"] == "PLM"
    assert curated_main["has_landmark"] is True
    assert curated_main["historic_district"] == ""


# ==============================================================================
# UT-PY-2: Ownership Classification, Family Trusts & Demolition Permit Hygiene
# ==============================================================================


def test_ut_py_2_ownership_classification_and_demolition_permit_filtering() -> None:
    """Verify LLC vs Family Trust vs Situs Resident precedence and HCAD null-date / accessory-permit rejection (DF-4)."""
    # 1. Corporate LLC mailing to situs address (vacant teardown lot pattern) MUST be corporate, NEVER resident!
    bucket, situs_ok = classify_parcel_ownership(
        "BAYOU TEARDOWN INVESTMENTS LLC",
        "1420 PEDEN ST",
        "1420 PEDEN STREET HOUSTON TX 77006",
    )
    assert bucket == "corporate"
    assert situs_ok is False

    # 2. Spaced "L L C" and LP/INC/BUILDERS variants
    assert classify_parcel_ownership("HEIGHTS MODERN HOMES L P", "712 W 18TH ST", "712 W 18TH ST")[0] == "corporate"
    assert classify_parcel_ownership("MONTROSE CAPITAL L L C", "1502 WESTHEIMER RD", "PO BOX 123")[0] == "corporate"

    # 3. Family living trust mailing to situs home -> family_trust WITH situs_matched=True (counts toward resident occ!)
    ft_res, ft_situs = classify_parcel_ownership(
        "GARCIA MARIA LIVING TRUST",
        "1122 KEY ST",
        "1122 KEY STREET #A",
    )
    assert ft_res == "family_trust"
    assert ft_situs is True

    # 4. Estate / family trust mailing out-of-state -> family_trust WITH situs_matched=False
    ft_abs, ft_abs_situs = classify_parcel_ownership(
        "ESTATE OF JOHN SMITH",
        "1122 KEY ST",
        "900 CONGRESS AVE AUSTIN TX 78701",
    )
    assert ft_abs == "family_trust"
    assert ft_abs_situs is False

    # 5. Individual homeowner with STREET vs ST abbreviation + unit suffix -> resident
    res_b, res_situs = classify_parcel_ownership(
        "NGUYEN LINH & TRAN MINH",
        "714 1/2 E 14TH ST",
        "714 1/2 EAST 14TH STREET APT B HOUSTON TX 77008",
    )
    assert res_b == "resident"
    assert res_situs is True

    # 6. Individual mailing to P.O. Box or different street -> absentee
    assert classify_parcel_ownership("DOE JANE", "1122 KEY ST", "PO BOX 54001")[0] == "absentee"
    assert classify_parcel_ownership("DOE JANE", "1122 KEY ST", "4400 MEMORIAL DR")[0] == "absentee"

    # 7. Governmental / public exempt
    assert classify_parcel_ownership("CITY OF HOUSTON", "500 MCKINNEY ST", "500 MCKINNEY ST")[0] == "public_exempt"
    assert classify_situs_edges() is None


def classify_situs_edges() -> None:
    assert normalize_situs_compare_key("0 FAIRVIEW ST") == ""
    assert normalize_situs_compare_key("P.O. BOX 12345") == ""
    assert normalize_situs_compare_key("1127 KEY STREET HOUSTON TX 77009-1234") == "1127 KEY ST"
    assert normalize_hcad13("0621100000014#aux_1") == "0621100000014"
    assert normalize_hcad13("0000000000000") == ""


def test_ut_py_2b_demolition_permit_null_date_and_scope_hygiene() -> None:
    """Reject 1899/1900 SQL/OLE null sentinels (even with roll_yr=2025) and pool/garage/interior scopes."""
    # Valid main-structure demo permits (types 13, 33, 43)
    assert parse_demolition_permit_row(
        acct="0610550020012",
        permit_type="33",
        dscr="DEMO RESIDENCE SEWER DISCONNECT",
        issue_date="06/15/2023",
        roll_yr="2025",
    ) == ("0610550020012", 2023)

    assert parse_demolition_permit_row(
        acct="0610550020012",
        permit_type="13",
        dscr="DEMO HOUSE & GARAGE",
        issue_date="2024-11-02",
        roll_yr="2025",
    ) == ("0610550020012", 2024)

    # CRITICAL DF-4 guard: Microsoft OLE/SQL null sentinel dates MUST return None, NEVER fall back to roll_yr=2025!
    for sentinel in ("12/30/1899", "12/31/1899", "01/01/1900", "01/02/1900", "00/00/0000", ""):
        assert (
            parse_demolition_permit_row(
                acct="0610550020012",
                permit_type="33",
                dscr="DEMO RESIDENCE",
                issue_date=sentinel,
                roll_yr="2025",
            )
            is None
        ), f"Sentinel {sentinel!r} leaked roll_yr fallback!"

    # Pre-2020 permit rejected
    assert (
        parse_demolition_permit_row(
            acct="0610550020012",
            permit_type="33",
            dscr="DEMO HOUSE",
            issue_date="08/10/2019",
            roll_yr="2025",
        )
        is None
    )

    # Non-demolition permit code rejected
    assert (
        parse_demolition_permit_row(
            acct="0610550020012",
            permit_type="01",
            dscr="NEW RESIDENCE",
            issue_date="08/10/2023",
        )
        is None
    )

    # Accessory / interior scopes rejected
    for non_main in (
        "SWIMMING POOL DEMO ONLY",
        "DEMO DETACHED GARAGE AT RESIDENCE",
        "INTERIOR NON-LOAD BEARING REMODEL",
        "CANOPY DEMO",
        "SHED DEMO",
        "CARPORT ONLY",
    ):
        assert is_main_structure_demolition_desc(non_main) is False, f"Failed to reject {non_main!r}"
        assert (
            parse_demolition_permit_row(
                acct="0610550020012",
                permit_type="43",
                dscr=non_main,
                issue_date="05/20/2024",
            )
            is None
        )


# ==============================================================================
# UT-PY-3: Polygon Hole Respect, MultiPolygons & Dual-Mode SMLS / Ribbon SMBL
# ==============================================================================


def test_ut_py_3_spatial_holes_multipolygons_and_dual_mode_ch42_matching() -> None:
    """Verify hole exclusion on Swiss-cheese boundaries/ordinances and 26-ft ribbon SMBL buffering in EPSG:2278 feet (DF-3)."""
    assert RIBBON_SMBL_RATIO_THRESHOLD_FT == 20.0
    assert RIBBON_SMBL_BUFFER_FT == 26.0

    # Construct 5 parcels in EPSG:2278 US Survey Feet coordinates:
    # P0: Inside outer ring of SMLS polygon (x=50..100, y=50..150) -> matched by SMLS
    # P1: Strictly INSIDE an interior cutout hole (micro-replat hole x=200..260, y=50..150) -> MUST NOT match SMLS!
    # P2: Fronting north side of street ribbon SMBL (ribbon y=-12..-10, P2 at y=0..100, 10 ft gap <= 26 ft) -> matched by SMBL
    # P3: Opposite side across 60-ft street ROW (P3 at y=-160..-60, 48 ft from ribbon > 26 ft buffer) -> MUST NOT match SMBL!
    # P4: Commercial parcel (is_residential=False) sitting directly inside SMLS -> MUST NOT count toward residential masks!
    p0 = Polygon([(50, 50), (100, 50), (100, 150), (50, 150)])
    p1_in_hole = Polygon([(205, 55), (255, 55), (255, 145), (205, 145)])
    p2_ribbon_front = Polygon([(500, 0), (550, 0), (550, 100), (500, 100)])
    p3_across_row = Polygon([(500, -160), (550, -160), (550, -60), (500, -60)])
    p4_commercial = Polygon([(110, 50), (160, 50), (160, 150), (110, 150)])

    # P5: Parcel inside second disjoint island of a MultiPolygon boundary / ordinance (x=900..950, y=50..150)
    p5_island = Polygon([(900, 50), (950, 50), (950, 150), (900, 150)])

    geoms = np.array([p0, p1_in_hole, p2_ribbon_front, p3_across_row, p4_commercial, p5_island], dtype=object)
    pts = np.array([g.representative_point() for g in geoms], dtype=object)
    areas = np.array([g.area for g in geoms], dtype=np.float64)
    is_res = np.array([True, True, True, True, False, True], dtype=bool)

    smls_with_hole_and_island = MultiPolygon(
        [
            Polygon(
                shell=[(0, 0), (300, 0), (300, 200), (0, 200)],
                holes=[[(200, 50), (260, 50), (260, 150), (200, 150)]],
            ),
            Polygon([(880, 0), (980, 0), (980, 200), (880, 200)]),
        ]
    )
    # Verify basic STRtree hole check directly too (used for Swiss-cheese neighborhoods)
    tree = STRtree(pts)
    hole_hits = sorted(int(i) for i in tree.query(smls_with_hole_and_island, predicate="intersects"))
    assert hole_hits == [0, 4, 5]  # P1 in cutout hole is strictly excluded!

    # Thin 2-ft wide front-setback SMBL strip running x=490..560, y=-12..-10 (area=140, perim=144 -> ratio=0.97 < 20 ft)
    thin_smbl_ribbon = Polygon([(490, -12), (560, -12), (560, -10), (490, -10)])

    # Expired and Pending ordinances MUST be ignored by match_ch42_protected_parcels
    expired_smls = Polygon([(480, -200), (580, -200), (580, -50), (480, -50)])

    protections = [
        {
            "protection_type": "smls",
            "ordinance": "2018-0100",
            "sunset_status": "active",
            "geom_2278": smls_with_hole_and_island,
        },
        {
            "protection_type": "smbl",
            "ordinance": "2008-0333",
            "sunset_status": "expiring_soon",
            "geom_2278": thin_smbl_ribbon,
        },
        {
            "protection_type": "smls",
            "ordinance": "2003-0011",
            "sunset_status": "expired",
            "geom_2278": expired_smls,
        },
        {
            "protection_type": "smls",
            "ordinance": "2026-PEND",
            "sunset_status": "pending",
            "geom_2278": expired_smls,
        },
    ]

    res = match_ch42_protected_parcels(geoms, pts, areas, is_res, protections)
    assert res["smls_mask"].tolist() == [True, False, False, False, False, True]
    assert res["smbl_mask"].tolist() == [False, False, True, False, False, False]
    assert res["any_mask"].tolist() == [True, False, True, False, False, True]
    assert res["expiring_mask"].tolist() == [False, False, True, False, False, False]
    assert int(res["any_mask"].sum()) <= int(is_res.sum())


def test_ut_py_3b_python_lifecycle_mirrors_js_ord_2015_639_cutoff() -> None:
    """Verify Python schema.resolve_ch42_ordinance_lifecycle enforces identical July 24, 2015 cutoff (DF-1)."""
    assert ORD_2015_639_CUTOFF_ISO == "2015-07-24"
    assert ORD_2015_639_CUTOFF_ORD_NUM == 639

    day_before = resolve_ch42_ordinance_lifecycle(
        ordinance="2015-0638",
        effective_date="2015-07-23",
        expiration_date="",
        protection_type="smbl",
        ref_year=2026,
    )
    assert day_before["statutory_term_years"] == 20
    assert day_before["expiration_date"] == "2035-07-23"
    assert day_before["expiration_year"] == 2035
    assert day_before["is_estimated_sunset"] is True

    day_of = resolve_ch42_ordinance_lifecycle(
        ordinance="2015-0639",
        effective_date="2015-07-24",
        expiration_date="",
        protection_type="smls",
        ref_year=2026,
    )
    assert day_of["statutory_term_years"] == 40
    assert day_of["expiration_date"] == "2055-07-24"
    assert day_of["expiration_year"] == 2055

    # Idempotence on overlays['land_use_protections'] across repeated runs
    sample_overlays = {
        "land_use_protections": {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "protection_type": "smbl",
                        "ordinance": "2007-0249",
                        "status": "Approved",
                        "effective_date": "",
                        "expiration_date": "",
                    },
                    "geometry": {"type": "Polygon", "coordinates": []},
                }
            ],
        }
    }
    c1 = hydrate_land_use_protections_lifecycle(sample_overlays, ref_year=2026)
    p_after_1 = dict(sample_overlays["land_use_protections"]["features"][0]["properties"])
    c2 = hydrate_land_use_protections_lifecycle(sample_overlays, ref_year=2026)
    p_after_2 = dict(sample_overlays["land_use_protections"]["features"][0]["properties"])
    assert c1 == c2
    assert p_after_1 == p_after_2
    assert p_after_2["is_estimated_sunset"] is True
    assert p_after_2["expiration_year"] == 2027
    assert p_after_2["expiration_date"] == "2027-12-31"
    assert p_after_2["sunset_status"] == "expiring_soon"
    assert p_after_2["gis_expiration_date"] == ""

    # Pending, rejected/withdrawn, and permanent Chapter 33 conservation parity with JS
    for pend_status in ("Pending City Council Review", "Application Applied", "Draft Under Review"):
        pend = resolve_ch42_ordinance_lifecycle(
            ordinance="2026-0001",
            effective_date="2026-03-01",
            status=pend_status,
            ref_year=2026,
        )
        assert pend["sunset_status"] == "pending"
        assert pend["effective_date"] == ""
        assert pend["effective_year"] == 0
        assert pend["expiration_date"] == ""
        assert pend["expiration_year"] == 0
        assert pend["statutory_term_years"] == 40

    for rej_status in ("Application Denied", "Withdrawn by Applicant", "Ordinance Rescinded", "Rejected"):
        rej = resolve_ch42_ordinance_lifecycle(
            ordinance="2019-0100",
            effective_date="2019-05-01",
            status=rej_status,
            ref_year=2026,
        )
        assert rej["sunset_status"] == "inactive"
        assert rej["effective_year"] == 0
        assert rej["expiration_year"] == 0
        assert rej["statutory_term_years"] == 0

    cons = resolve_ch42_ordinance_lifecycle(
        effective_date="2023",
        status="Active",
        protection_type="conservation",
        ref_year=2026,
    )
    assert cons["sunset_status"] == "permanent"
    assert cons["statutory_term_years"] == 0
    assert cons["expiration_date"] == ""


# ==============================================================================
# UT-PY-4: City Council Districts A-K & Non-Clobbering Search Index Upsert
# ==============================================================================


def test_ut_py_4_council_districts_a_to_k_and_search_index_slice_upsert(tmp_path: Path) -> None:
    """Verify 11 Districts A-K, valid WGS84 interiors, and non-destructive upsert_search_index_slice (DF-7)."""
    # 1. Verify synthetic cached FeatureCollection validation + roster attachment
    synth_features = []
    for idx, letter in enumerate("ABCDEFGHIJK"):
        lon0 = -95.50 + idx * 0.02
        lat0 = 29.70
        synth_features.append(
            {
                "type": "Feature",
                "properties": {"DISTRICT": letter, "MEMBER": "STALE GIS NAME"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [lon0, lat0],
                            [lon0 + 0.015, lat0],
                            [lon0 + 0.015, lat0 + 0.02],
                            [lon0, lat0 + 0.02],
                            [lon0, lat0],
                        ]
                    ],
                },
            }
        )
    cache_file = tmp_path / "coh_council_districts_2024.geojson"
    cache_file.write_text(
        json.dumps({"type": "FeatureCollection", "features": synth_features}),
        encoding="utf-8",
    )
    loaded = fetch_and_cache_council_districts(cache_file)
    assert len(loaded) == 11
    assert [f["properties"]["district_letter"] for f in loaded] == list("ABCDEFGHIJK")
    dist_c = next(f for f in loaded if f["properties"]["district_letter"] == "C")
    assert dist_c["properties"]["id"] == "council_dist_c"
    assert dist_c["properties"]["district_code"] == "C"
    assert dist_c["properties"]["council_member"] == COH_COUNCIL_ROSTER_2024_2028["C"]["member"] == "Joe Panzarella"
    assert dist_c["properties"]["council_term_expires"] == COUNCIL_TERM_EXPIRES == "2028-01"

    # Reject incomplete council datasets (< 11 districts)
    bad_cache = tmp_path / "bad_council.geojson"
    bad_cache.write_text(
        json.dumps({"type": "FeatureCollection", "features": synth_features[:9]}),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="Expected 11"):
        fetch_and_cache_council_districts(bad_cache)

    # 2. Verify idempotent, non-clobbering upsert_search_index_slice across arbitrary script order (DF-7)
    seed_index = [
        {"id": "hd_norhill", "type": "district", "overlay_layer": "historicDistricts", "name": "Norhill HD"},
        {"id": "holc_c12", "is_neighborhood_entry": True, "overlay_layer": "holc1937", "name": "HOLC C12"},
        {"id": "phd_eastwood", "is_neighborhood_entry": True, "overlay_layer": "potentialHistoricDistricts", "name": "Eastwood"},
        {"id": "sn_22", "is_neighborhood_entry": True, "overlay_layer": "superNeighborhoods", "name": "Washington Ave"},
        {"id": "0621100000014", "name": "1127 Key St", "hcad_num": "0621100000014"},
    ]
    overlays_obj: dict[str, Any] = {}
    after_run_1 = upsert_council_districts_in_overlays_and_search(overlays_obj, seed_index, loaded)
    after_run_2 = upsert_council_districts_in_overlays_and_search(overlays_obj, after_run_1, loaded)
    assert after_run_1 == after_run_2
    assert len(after_run_2) == 11 + len(seed_index)
    # Every pre-existing boundary slice and building entry MUST remain intact!
    retained_ids = {row["id"] for row in after_run_2}
    assert {"hd_norhill", "holc_c12", "phd_eastwood", "sn_22", "0621100000014"}.issubset(retained_ids)
    assert sum(1 for r in after_run_2 if r.get("overlay_layer") == "councilDistricts") == 11

    # Also verify updating another slice (e.g. potentialHistoricDistricts) leaves all 11 council districts intact!
    after_phd_refresh = upsert_search_index_slice(
        after_run_2,
        slice_predicate=lambda it: is_boundary_search_entry(it) and it.get("overlay_layer") == "potentialHistoricDistricts",
        new_entries=[
            {
                "id": "phd_eastwood",
                "is_neighborhood_entry": True,
                "overlay_layer": "potentialHistoricDistricts",
                "name": "Eastwood Updated",
            }
        ],
    )
    assert sum(1 for r in after_phd_refresh if r.get("overlay_layer") == "councilDistricts") == 11
    assert sum(1 for r in after_phd_refresh if r.get("overlay_layer") == "historicDistricts") == 1
    assert sum(1 for r in after_phd_refresh if r.get("overlay_layer") == "holc1937") == 1


# ==============================================================================
# UT-PY-5: Compact Payload Hygiene, Zero Em-Dashes & Live Production Invariants
# ==============================================================================


def test_ut_py_5_production_scorecards_overlays_and_compact_json_invariants(tmp_path: Path) -> None:
    """Verify production neighborhood_scorecards.json, overlays.json, and search_index.json invariants."""
    # 1. write_compact_json + sanitize_atlas_text invariants
    cleaned = sanitize_atlas_text("Montrose \u2014 District C\u2014West")
    assert "\u2014" not in cleaned
    assert cleaned == "Montrose - District C-West"

    out_tmp = tmp_path / "compact.json"
    write_compact_json(out_tmp, {"a": 1, "b": [2, 3], "note": sanitize_atlas_text("A \u2014 B")})
    raw_tmp = out_tmp.read_text(encoding="utf-8")
    assert "\n" not in raw_tmp
    assert "\u2014" not in raw_tmp
    assert raw_tmp == '{"a":1,"b":[2,3],"note":"A - B"}'

    # 2. Production neighborhood_scorecards.json size, single-line format, zero em-dashes, and bounds
    assert SCORECARDS_JSON.exists()
    raw_sc_text = SCORECARDS_JSON.read_text(encoding="utf-8")
    assert len(raw_sc_text.encode("utf-8")) < 2_500_000, "neighborhood_scorecards.json must stay < 2.5 MB"
    assert "\n" not in raw_sc_text.rstrip("\n"), "neighborhood_scorecards.json must be compact single-line JSON"
    assert "\u2014" not in raw_sc_text, "neighborhood_scorecards.json must contain zero U+2014 em-dashes"

    payload = json.loads(raw_sc_text)
    assert list(payload["decade_bins"]) == list(DECADE_BINS)
    assert list(payload["demo_years"]) == list(DEMO_YEARS)
    assert payload["min_subdivision_standing_structures"] == MIN_SUBDIVISION_STANDING_STRUCTURES == 10
    assert payload["council_term_expires"] == "2028-01"

    records = payload["scorecards"]
    assert len(records) >= 4200

    for rec in records:
        bc = int(rec.get("bc", 0))
        pc = int(rec.get("pc", 0))
        rpc = int(rec.get("rpc", 0))
        lm = int(rec.get("lm", 0))
        plm = int(rec.get("plm", 0))
        vlm = int(rec.get("vlm", 0))
        assert rpc <= pc, f"{rec['id']} rpc ({rpc}) > pc ({pc})"
        assert int(rec.get("sfc", 0)) <= rpc
        assert int(rec.get("occ", 0)) <= rpc
        assert int(rec.get("llc", 0)) <= rpc
        assert int(rec.get("chp", 0)) <= rpc
        assert int(rec.get("hdp", 0)) <= bc
        assert plm + vlm <= lm, f"{rec['id']} plm ({plm}) + vlm ({vlm}) > lm ({lm})"
        cx, cy = float(rec["cx"]), float(rec["cy"])
        assert -96.2 <= cx <= -94.8 and 29.4 <= cy <= 30.3
        if rec["ly"] == "platted_subdivisions":
            assert bc >= MIN_SUBDIVISION_STANDING_STRUCTURES

    # 3. Verify real overlays.json council_districts geometry containment, landmark conservation & search_index sync
    overlays = json.loads(OVERLAYS_JSON.read_text(encoding="utf-8"))
    cd_feats = overlays["council_districts"]["features"]
    assert len(cd_feats) == 11
    assert [f["properties"]["district_letter"] for f in cd_feats] == list("ABCDEFGHIJK")
    assert [f["properties"]["district_code"] for f in cd_feats] == list("ABCDEFGHIJK")
    for feat in cd_feats:
        p = feat["properties"]
        sg = shape(feat["geometry"])
        assert sg.is_valid and not sg.is_empty
        assert float(p["area_sq_mi"]) > 0
        assert "ch42_protected_count" in p and "ch42_protected_pct" in p
        pt = Point(float(p["label_lng"]), float(p["label_lat"]))
        assert sg.covers(pt), f"{p['id']} label point lies outside geometry!"

    # Every boundary polygon in the 8 non-subdivision families must have an exact 1:1 scorecard
    sc_ids_by_layer: dict[str, set[str]] = {}
    for rec in records:
        sc_ids_by_layer.setdefault(rec["ly"], set()).add(rec["id"])
    for fam_layer in (
        "council_districts",
        "historic_districts",
        "heritage_districts",
        "nrhp_districts",
        "potential_historic_districts",
        "super_neighborhoods",
        "holc_1937_zones",
        "neighborhoods",
    ):
        ov_ids = {str(f["properties"]["id"]) for f in overlays[fam_layer]["features"]}
        assert sc_ids_by_layer.get(fam_layer, set()) == ov_ids, f"ID drift in {fam_layer}"

    # Standing Protected Landmarks (is_protected_landmark / legal_tier == 'plm', excluding demolished) must conserve cleanly
    standing_plms = [
        f
        for f in overlays["landmarks"]["features"]
        if not f["properties"].get("demolished")
        and (
            f["properties"].get("is_protected_landmark")
            or str(f["properties"].get("legal_tier") or "").lower() == "plm"
        )
    ]
    cd_plm_sum = sum(int(r.get("plm", 0)) for r in records if r["ly"] == "council_districts")
    dist_c_rec = next(r for r in records if r["ly"] == "council_districts" and r["id"] == "council_dist_c")
    assert len(standing_plms) > 150
    assert cd_plm_sum == len(standing_plms)
    assert int(dist_c_rec.get("plm", 0)) > 50

    sidx = json.loads(SEARCH_INDEX_JSON.read_text(encoding="utf-8"))
    idx_list = sidx["items"] if isinstance(sidx, dict) and "items" in sidx else sidx
    cd_sidx = [r for r in idx_list if isinstance(r, dict) and r.get("overlay_layer") == "councilDistricts"]
    assert len(cd_sidx) == 11
