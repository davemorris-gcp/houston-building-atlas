"""Unit tests for HCAD SQL/OLE null-date sentinels, idempotent campus contiguity, and guarded school situs hydration."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from atlas_pipeline.enrich_deed_chains import parse_dos_yyyymmdd
from atlas_pipeline.fix_campus_anomalies_and_schools import is_eligible_zero_street_hydration
from atlas_pipeline.resolve_undated_countywide import (
    is_institutional_owner,
    is_valid_authoritative_donor,
    re_resolve_synthetic_shard_features,
    resolve_contiguity_candidate,
    seed_authoritative_dated_parcels,
)
from atlas_pipeline.schema import (
    INFERRED_FALLBACK_YEAR_SOURCES,
    VALID_CONTRIBUTING_VALUES,
    is_hcad_null_date_sentinel,
    normalize_hcad_date,
    normalize_override_record,
    parse_hcad_date_year,
    write_compact_json,
)


@pytest.mark.parametrize(
    ("year", "month", "day", "expected"),
    [
        (1899, 12, 30, True),
        (1899, 12, 31, True),
        (1899, 1, 1, True),
        (1900, 1, 1, True),
        (1900, 1, 2, True),
        (1900, 0, 15, True),
        (1900, 6, 0, True),
        (1900, 1, 3, False),
        (1900, 6, 15, False),
        (1901, 1, 1, False),
        (1901, 11, 3, False),
        (1928, 9, 10, False),
    ],
)
def test_is_hcad_null_date_sentinel_matrix(year: int, month: int, day: int, expected: bool) -> None:
    """UT-1: Verify SQL/COM/OLE epoch sentinels are rejected while genuine 1900+ dates survive."""
    assert is_hcad_null_date_sentinel(year, month, day) is expected


@pytest.mark.parametrize(
    "raw_date",
    [
        "12/30/1899",
        "12/31/1899",
        "1899-12-30",
        "1899-12-30 00:00:00",
        "01/01/1900",
        "1/1/1900",
        "01/02/1900",
        "1900-01-01T00:00:00",
        "1900-01-02 00:00:00",
        "00/00/0000",
        "0000-00-00",
        "0",
        "",
        "   ",
        None,
        "N/A",
        "UNKN",
        "02/30/1950",
        "13/01/1950",
        "05/12/1835",
        "08/01/2027",
    ],
)
def test_parse_hcad_date_year_rejects_all_sentinels_and_out_of_bounds(raw_date: str | None) -> None:
    """UT-1: Verify sentinel, malformed, and out-of-range dates return (None, 0) without fallback."""
    iso_dt, yr = normalize_hcad_date(raw_date, allow_fallback=False)
    assert iso_dt is None
    assert yr == 0
    assert parse_hcad_date_year(raw_date, allow_fallback=False) == 0


@pytest.mark.parametrize(
    ("raw_date", "expected_iso", "expected_yr"),
    [
        ("01/03/1900", "1900-01-03", 1900),
        ("06/15/1900", "1900-06-15", 1900),
        ("11/03/1901", "1901-11-03", 1901),
        ("1928-09-10 00:00:00", "1928-09-10", 1928),
        ("04/21/1836", "1836-04-21", 1836),
        ("10/27/2026", "2026-10-27", 2026),
    ],
)
def test_normalize_hcad_date_preserves_genuine_historic_dates(
    raw_date: str, expected_iso: str, expected_yr: int
) -> None:
    """UT-1: Verify genuine non-sentinel calendar dates in [1836, 2026] normalize cleanly."""
    iso_dt, yr = normalize_hcad_date(raw_date, allow_fallback=False)
    assert iso_dt == expected_iso
    assert yr == expected_yr
    assert parse_hcad_date_year(raw_date, allow_fallback=False) == expected_yr


def test_deed_clerk_yr_fallback_vs_permit_tax_roll_yr_guard() -> None:
    """UT-1: Verify deeds.txt falls back to clerk_yr while permits.txt blocks tax-roll yr fallback."""
    # deeds.txt: 12/30/1899 null dos must fall back to valid County Clerk filing year (1998)
    iso_deed, deed_yr = normalize_hcad_date("12/30/1899", "1998", allow_fallback=True)
    assert iso_deed is None
    assert deed_yr == 1998
    assert parse_hcad_date_year("01/01/1900", "1968", allow_fallback=True) == 1968
    assert parse_dos_yyyymmdd("12/30/1899", "1998") == "19980000"
    assert parse_dos_yyyymmdd("01/01/1900", "1899") == "00000000"
    assert parse_dos_yyyymmdd("06/15/1900", "1998") == "19000615"

    # Invalid clerk_yr fallbacks are rejected even when allow_fallback=True
    assert parse_hcad_date_year("12/30/1899", "1899", allow_fallback=True) == 0
    assert parse_hcad_date_year("12/30/1899", "1820", allow_fallback=True) == 0
    assert parse_hcad_date_year("12/30/1899", "2030", allow_fallback=True) == 0

    # permits.txt: yr column stores HCAD appraisal tax-roll cycle year (e.g. 2008 or 2025) and MUST NOT leak
    assert parse_hcad_date_year("12/30/1899", "2025", allow_fallback=False) == 0
    assert parse_hcad_date_year("01/01/1900", "2008", allow_fallback=False) == 0
    assert normalize_hcad_date("12/31/1899", "2025", allow_fallback=False) == (None, 0)


def test_write_compact_json_and_normalize_override_record(tmp_path: Path) -> None:
    """UT-1 & UT-4: Verify single-line compact JSON serialization and override schema normalization."""
    out_file = tmp_path / "compact_test.json"
    payload = {
        "version": 2,
        "overrides": {
            "1223580020001#cy_ridge_hs_main": normalize_override_record(
                "1223580020001#cy_ridge_hs_main",
                {
                    "year_built": "2002",
                    "decade": "2000s",
                    "contributing": "NonContributing",
                    "building_name": "Cypress Ridge High School",
                },
            ),
            "1468640010001#lee_hs_main_1928": normalize_override_record(
                "1468640010001#lee_hs_main_1928",
                {
                    "year_built": 1928,
                    "contributing": "Unrecognized Flag",
                    "building_name": "Robert E. Lee High School",
                },
            ),
        },
    }
    write_compact_json(out_file, payload)
    raw_text = out_file.read_text(encoding="utf-8")
    assert "\n" not in raw_text
    assert ", " not in raw_text

    parsed = json.loads(raw_text)
    cy = parsed["overrides"]["1223580020001#cy_ridge_hs_main"]
    assert cy["hcad_num"] == "1223580020001"
    assert cy["id"] == "1223580020001#cy_ridge_hs_main"
    assert cy["building_id"] == "1223580020001#cy_ridge_hs_main"
    assert cy["year_built"] == 2002
    assert cy["decade"] == 2000 and isinstance(cy["decade"], int)
    assert cy["contributing"] == "Non-Contributing"
    assert cy["contributing"] in VALID_CONTRIBUTING_VALUES

    lee = parsed["overrides"]["1468640010001#lee_hs_main_1928"]
    assert lee["decade"] == 1920 and isinstance(lee["decade"], int)
    assert lee["contributing"] == "Outside Historic District"


def test_is_valid_authoritative_donor_excludes_inferred_sources_and_zero_bld_area() -> None:
    """UT-2: Verify inferred fallback sources and demolished bld_ar == 0 lots cannot donate years."""
    # Every inferred fallback tier must be rejected
    for src in INFERRED_FALLBACK_YEAR_SOURCES:
        assert is_valid_authoritative_donor(1965, src, 42000.0, has_curated_override=False) is False

    # Demolished pre-war house lot (bld_ar == 0) acquired by college/ISD must be rejected
    assert is_valid_authoritative_donor(1927, "hcad_cama", 0.0, has_curated_override=False) is False
    assert is_valid_authoritative_donor(1927, "", 0.0, has_curated_override=False) is False

    # Curated open-air landmark / historic cemetery override is permitted even with bld_ar == 0
    assert is_valid_authoritative_donor(1836, "curated_landmark", 0.0, has_curated_override=True) is True

    # Authoritative HCAD CAMA improvement with positive bld_area is accepted
    assert is_valid_authoritative_donor(1968, "hcad_cama", 18500.0, has_curated_override=False) is True
    assert is_valid_authoritative_donor(1835, "hcad_cama", 18500.0, has_curated_override=False) is False
    assert is_valid_authoritative_donor(2027, "hcad_cama", 18500.0, has_curated_override=False) is False


def test_institutional_contiguity_guards_and_idempotent_synthetic_re_resolution() -> None:
    """UT-2: Verify < 1945 & 350m campus_contiguity guards, completeness, and consecutive-run idempotency."""
    assert is_institutional_owner("LEE COLLEGE DISTRICT") is True
    assert is_institutional_owner("CYPRESS FAIRBANKS ISD") is True

    # Direct contiguity check: institutional owner rejects 1931 pre-war donor and >350m donor, picks 1968 donor at ~120m
    owner_to_dated = {
        "LEE COLLEGE DISTRICT": [
            (1931, -94.9781, 29.7321, "0451440030991"),  # 25m away, but < 1945 pre-war bungalow
            (1960, -94.9835, 29.7320, "0451440030992"),  # ~510m away (> 350m radius limit)
            (1968, -94.9769, 29.7315, "0500610000001"),  # ~145m away, post-1945 authoritative donor
        ]
    }
    cand = resolve_contiguity_candidate(
        ulon=-94.9780,
        ulat=29.7320,
        norm_own="LEE COLLEGE DISTRICT",
        st_num="",
        st_name="",
        addr_to_dated={},
        owner_to_dated=owner_to_dated,
        exclude_acct="0451440030163",
    )
    assert cand is not None
    assert cand["year_built"] == 1968
    assert cand["year_source"] == "campus_contiguity"

    # Synthetic multi-parcel fixture testing stale inferred shard seeding, demolished bld_ar == 0 lot,
    # 1899/1900 sentinel permits/deeds, and byte-identical consecutive-run idempotency.
    def _poly(lon: float, lat: float) -> dict:
        d = 0.0001
        return {
            "type": "Polygon",
            "coordinates": [[[lon, lat], [lon + d, lat], [lon + d, lat + d], [lon, lat + d], [lon, lat]]],
        }

    stale_shard_features = [
        # 1. Previously poisoned campus parcel carrying stale campus_contiguity=1927 from demolished lot
        {
            "type": "Feature",
            "geometry": _poly(-94.9780, 29.7320),
            "properties": {
                "hcad_num": "0451440030163",
                "year_built": 1927,
                "decade": 1920,
                "year_source": "campus_contiguity",
                "bld_area": 55000,
            },
        },
        # 2. Demolished pre-war residential reserve lot: yr_impr=1927 in CAMA, but bld_ar=0!
        {
            "type": "Feature",
            "geometry": _poly(-94.9781, 29.7321),
            "properties": {
                "hcad_num": "0451440030111",
                "year_built": 1927,
                "decade": 1920,
                "year_source": "hcad_cama",
                "bld_area": 0,
            },
        },
        # 3. Active post-1945 institutional donor parcel within 150m (yr_impr=1968, bld_ar=24000)
        {
            "type": "Feature",
            "geometry": _poly(-94.9769, 29.7315),
            "properties": {
                "hcad_num": "0500610000001",
                "year_built": 1968,
                "decade": 1960,
                "year_source": "hcad_cama",
                "bld_area": 24000,
            },
        },
        # 4. Previously sentinel-poisoned hcad_permit=1900 parcel that actually has a 12/30/1899 permit
        #    (tax-roll yr=2025) and a 12/30/1899 deed with clerk_yr=1974
        {
            "type": "Feature",
            "geometry": _poly(-95.6050, 29.8929),
            "properties": {
                "hcad_num": "1223580029999",
                "year_built": 1900,
                "decade": 1900,
                "year_source": "hcad_permit",
                "bld_area": 12000,
            },
        },
    ]

    cama_lookup = {
        "0451440030163": (0, 0, 55000, "X3", "Exempt College", "0 LEE DR"),
        "0451440030111": (1927, 1927, 0, "X3", "Exempt College", "210 LEE DR"),
        "0500610000001": (1968, 1968, 24000, "X3", "Exempt College", "501 S WHITING ST"),
        "1223580029999": (0, 0, 12000, "F1", "Commercial", "7910 N ELDRIDGE PKWY"),
    }
    acct_meta = {
        "0451440030163": {"owner": "LEE COLLEGE DISTRICT", "address": "0 LEE DR", "lgl2": "BAYTOWN COLLEGE"},
        "0451440030111": {"owner": "LEE COLLEGE DISTRICT", "address": "210 LEE DR", "lgl2": "BAYTOWN COLLEGE"},
        "0500610000001": {"owner": "LEE COLLEGE DISTRICT", "address": "501 S WHITING ST", "lgl2": "BAYTOWN COLLEGE"},
        "1223580029999": {
            "owner": "ELDRIDGE RETAIL PROPERTIES LLC",
            "address": "7910 N ELDRIDGE PKWY",
            "lgl2": "ELDRIDGE RESERVE",
            "splt_dt": "12/30/1899",
            "new_own_dt": "01/01/1900",
        },
    }
    permits_by_acct = {
        "1223580029999": [("12/30/1899", "2025", "LEGACY CAMA NULL PERMIT")],
    }
    deeds_by_acct = {
        "1223580029999": [("12/30/1899", "1974")],
    }

    seeded_years, _ = seed_authoritative_dated_parcels(stale_shard_features, cama_lookup)
    assert "0451440030163" not in seeded_years  # campus_contiguity excluded
    assert "0451440030111" not in seeded_years  # bld_ar == 0 demolished lot excluded
    assert "1223580029999" not in seeded_years  # hcad_permit excluded
    assert seeded_years == {"0500610000001": 1968}

    run1 = re_resolve_synthetic_shard_features(
        stale_shard_features,
        cama_lookup,
        acct_meta,
        permits_by_acct=permits_by_acct,
        deeds_by_acct=deeds_by_acct,
    )
    by_hcad_1 = {f["properties"]["hcad_num"]: f["properties"] for f in run1}
    assert by_hcad_1["0451440030163"]["year_built"] == 1968
    assert by_hcad_1["0451440030163"]["year_source"] == "campus_contiguity"
    assert by_hcad_1["1223580029999"]["year_built"] == 1974
    assert by_hcad_1["1223580029999"]["year_source"] == "hcad_deed"
    for feat in run1:
        assert 1836 <= feat["properties"]["year_built"] <= 2026

    # Consecutive-run idempotency check
    run2 = re_resolve_synthetic_shard_features(
        run1,
        cama_lookup,
        acct_meta,
        permits_by_acct=permits_by_acct,
        deeds_by_acct=deeds_by_acct,
    )
    assert json.dumps(run1, sort_keys=True) == json.dumps(run2, sort_keys=True)


def test_guarded_zero_street_institutional_situs_hydration() -> None:
    """UT-2 & IT-3: Verify '0 <STREET>' hydration accepts tax-exempt campuses and rejects strip malls / unnumbered strings."""
    # Valid tax-exempt ISD campus with '0 N ELDRIDGE PKWY' matching '7900 N ELDRIDGE PKWY'
    assert (
        is_eligible_zero_street_hydration(
            current_addr="0 N ELDRIDGE PKWY",
            school_addr="7900 N ELDRIDGE PKWY",
            parcel_owner="CYPRESS FAIRBANKS ISD",
            parcel_state_class="X1",
            parcel_use_category="Civic / Institutional",
            dist_m=155.0,
        )
        is True
    )

    # Rejects commercial strip-center parcel (state_class F1/F2) even when close to charter school pin
    assert (
        is_eligible_zero_street_hydration(
            current_addr="0 WESTHEIMER RD",
            school_addr="9800 WESTHEIMER RD",
            parcel_owner="WESTHEIMER RETAIL PLAZA LP",
            parcel_state_class="F1",
            parcel_use_category="Commercial",
            dist_m=25.0,
        )
        is False
    )

    # Rejects unnumbered or empty school addresses ('TUCKERTON RD', '', '0 MARKET ST')
    for bad_addr in ("TUCKERTON RD", "", "0 MARKET ST", "P.O. BOX 420"):
        assert (
            is_eligible_zero_street_hydration(
                current_addr="0 TUCKERTON RD",
                school_addr=bad_addr,
                parcel_owner="CYPRESS FAIRBANKS ISD",
                parcel_state_class="X1",
                parcel_use_category="Civic / Institutional",
                dist_m=15.0,
            )
            is False
        )

    # Rejects parcels that already carry a valid numbered street address
    assert (
        is_eligible_zero_street_hydration(
            current_addr="7600 N ELDRIDGE PKWY",
            school_addr="7900 N ELDRIDGE PKWY",
            parcel_owner="CYPRESS FAIRBANKS ISD",
            parcel_state_class="X1",
            parcel_use_category="Civic / Institutional",
            dist_m=10.0,
        )
        is False
    )


def test_modularity_single_source_of_truth_and_cli_registration() -> None:
    """MOD-1 & MOD-2: Confirm shared schema.py constants/helpers, stage signature contracts, and authentic 1900-1901 fallback preservation."""
    import inspect

    from atlas_pipeline.fix_campus_anomalies_and_schools import (
        CAMPUS_SPLIT_HCADS,
        run_campus_and_school_fixes,
    )
    from atlas_pipeline.resolve_undated_countywide import resolve_permit_deed_spatial_fallback
    from atlas_pipeline.schema import ACTIVE_CACHE_BUST, sanitize_atlas_text

    pipeline_pkg = Path(__file__).resolve().parents[1] / "atlas_pipeline"

    # MOD-1: Consumer modules must import centralized date-sentinel, cache-bust, and PMTiles compiler helpers from atlas_pipeline.schema
    for mod_name, expected_symbol in [
        ("resolve_undated_countywide.py", "parse_hcad_date_year"),
        ("resolve_undated_countywide.py", "compile_quadrant_pmtiles"),
        ("fix_campus_anomalies_and_schools.py", "parse_hcad_date_year"),
        ("fix_campus_anomalies_and_schools.py", "ACTIVE_CACHE_BUST"),
        ("fix_campus_anomalies_and_schools.py", "compile_quadrant_pmtiles"),
        ("full_build.py", "ACTIVE_CACHE_BUST"),
        ("full_build.py", "compile_quadrant_pmtiles"),
        ("enrich_deed_chains.py", "normalize_hcad_date"),
        ("build_permits_and_contractors.py", "normalize_hcad_date"),
    ]:
        src = (pipeline_pkg / mod_name).read_text(encoding="utf-8")
        tree = ast.parse(src, filename=mod_name)
        imported_from_schema = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "atlas_pipeline.schema":
                imported_from_schema.update(alias.name for alias in node.names)
        assert expected_symbol in imported_from_schema, (
            f"{mod_name} must import {expected_symbol} from atlas_pipeline.schema"
        )

    assert ACTIVE_CACHE_BUST == "20261028a"
    assert len(CAMPUS_SPLIT_HCADS) == 10
    assert "1223580020001" in CAMPUS_SPLIT_HCADS
    assert "0451440030163" in CAMPUS_SPLIT_HCADS
    assert "1468640010001" in CAMPUS_SPLIT_HCADS

    # MOD-2: Verify run_campus_and_school_fixes accepts explicit shard_dir and full_build passes shard_dir=ndjson_dir before Tippecanoe
    sig = inspect.signature(run_campus_and_school_fixes)
    assert "shard_dir" in sig.parameters
    assert sig.parameters["shard_dir"].default is None
    assert "compile_pmtiles" in sig.parameters

    fb_tree = ast.parse((pipeline_pkg / "full_build.py").read_text(encoding="utf-8"), filename="full_build.py")
    campus_calls = [
        node
        for node in ast.walk(fb_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "run_campus_and_school_fixes"
    ]
    assert len(campus_calls) == 1
    kw_names = {kw.arg for kw in campus_calls[0].keywords}
    assert {"compile_pmtiles", "shard_dir"} <= kw_names

    # Authentic non-sentinel 1900 and 1901 permits and deeds are preserved uniformly by resolve_permit_deed_spatial_fallback
    p_1900 = resolve_permit_deed_spatial_fallback(
        permit_candidates=[(1900, "ORIGINAL BRICK WAREHOUSE")],
        earliest_deed_year=1905,
        local_era=1910,
        nn_median=1910,
    )
    assert p_1900["year_built"] == 1900
    assert p_1900["year_source"] == "hcad_permit"

    d_1901 = resolve_permit_deed_spatial_fallback(
        permit_candidates=[],
        earliest_deed_year=1901,
        local_era=1908,
        nn_median=1908,
    )
    assert d_1901["year_built"] == 1901
    assert d_1901["year_source"] == "hcad_deed"

    assert sanitize_atlas_text("Lone Star College \u2014 CyFair") == "Lone Star College - CyFair"


def test_sync_curated_edits_preserves_subbuilding_keys_and_suggested_year(tmp_path: Path) -> None:
    """Verify sync_curated_edits preserves <hcad>#<slug> keys, suggested_year_built, geometry-free sub-buildings, and typography hygiene."""
    from atlas_pipeline.sync_curated_edits import parse_approved_csv_rows, sync_curated_overrides

    csv_text = (
        "status,hcad_num,building_id,address,suggested_year_built,building_name,notes\n"
        "Approved,1223580020001#cy_ridge_hs_main,,7900 N ELDRIDGE PKWY,2002,Cypress Ridge High School,Main campus\n"
        "Verified,1468640010001,1468640010001#lee_hs_1954_shops,1809 MARKET ST,1954,Robert E. Lee HS \u2014 Vocational Shops,Lowell Lammers\n"
        "Approved,0010330000018,,301 MAIN ST,1889,Sweeney Coombs & Fredericks,Single parcel\n"
    )
    rows = parse_approved_csv_rows(csv_text)
    assert len(rows) == 3
    assert rows[0]["override_key"] == "1223580020001#cy_ridge_hs_main"
    assert rows[0]["hcad_num"] == "1223580020001"
    assert rows[0]["year_built"] == 2002
    assert rows[0]["is_building_override"] is True
    assert rows[1]["override_key"] == "1468640010001#lee_hs_1954_shops"
    assert rows[1]["hcad_num"] == "1468640010001"
    assert rows[1]["year_built"] == 1954
    assert rows[2]["override_key"] == "0010330000018"
    assert rows[2].get("is_building_override", False) is False

    csv_file = tmp_path / "approved.csv"
    csv_file.write_text(csv_text, encoding="utf-8")
    (tmp_path / "buildings.geojson").write_text(
        json.dumps({
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "id": "1468640010001#lee_hs_main_1928",
                        "building_id": "1468640010001#lee_hs_main_1928",
                        "hcad_num": "1468640010001",
                        "year_built": 1928,
                    },
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[-94.97, 29.73], [-94.969, 29.73], [-94.969, 29.731], [-94.97, 29.73]]],
                    },
                },
                {
                    "type": "Feature",
                    "properties": {
                        "id": "pcl_0010330000018",
                        "hcad_num": "0010330000018",
                        "year_built": 1890,
                    },
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[-95.36, 29.76], [-95.359, 29.76], [-95.359, 29.761], [-95.36, 29.76]]],
                    },
                },
            ],
        }),
        encoding="utf-8",
    )

    stats = sync_curated_overrides(data_dir=tmp_path, local_csv_path=csv_file)
    assert stats["total_overrides"] == 3
    assert stats["updated_buildings_geojson"] == 1

    ov_doc = json.loads((tmp_path / "curated_overrides.json").read_text(encoding="utf-8"))
    ov_map = ov_doc["overrides"]
    assert "1223580020001#cy_ridge_hs_main" in ov_map
    assert "1468640010001#lee_hs_1954_shops" in ov_map
    assert "1468640010001" not in ov_map
    assert "\u2014" not in ov_map["1468640010001#lee_hs_1954_shops"]["building_name"]
    assert "geometry" not in ov_map["1468640010001#lee_hs_1954_shops"]

    b_fc = json.loads((tmp_path / "buildings.geojson").read_text(encoding="utf-8"))
    assert b_fc["features"][0]["properties"]["year_built"] == 1928
    assert b_fc["features"][1]["properties"]["year_built"] == 1889


def test_cli_campuses_handles_fresh_output_dir_without_existing_stats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify cli.main --mode campuses succeeds without KeyError when stats_summary.json did not previously exist."""
    import pickle
    import zipfile

    from atlas_pipeline import cli
    from atlas_pipeline.schema import QUAD_NAMES

    cache_dir = tmp_path / "cache"
    quad_dir = cache_dir / "quadrant_ndjson"
    out_dir = tmp_path / "out"
    quad_dir.mkdir(parents=True)
    out_dir.mkdir(parents=True)

    with open(cache_dir / "cama_2026_compact.pkl", "wb") as f:
        pickle.dump({"0010330000018": (1889, 1889, 12000, "F1", "Commercial", "SWEENEY")}, f)

    with zipfile.ZipFile(cache_dir / "Real_acct_owner.zip", "w") as zf:
        zf.writestr(
            "real_acct.txt",
            "acct\tmailto\tsite_addr_1\tsite_addr_3\tstate_class\tNeighborhood_Code\tMarket_Area_1\tsplt_dt\tnew_own_dt\tlgl_2\n"
            "0010330000018\tSWEENEY COOMBS\t301 MAIN ST\t77002\tF1\t1000\t100\t\t\tSSBB\n",
        )

    feat = {
        "type": "Feature",
        "properties": {
            "id": "0010330000018#b1",
            "building_id": "0010330000018#b1",
            "hcad_num": "0010330000018",
            "address": "301 MAIN ST",
            "year_built": 1889,
            "decade": 1880,
            "year_source": "hcad_cama",
            "bld_area": 12000,
            "use_category": "Commercial",
            "contributing": "Contributing",
        },
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[-95.362, 29.763], [-95.361, 29.763], [-95.361, 29.764], [-95.362, 29.764], [-95.362, 29.763]]],
        },
    }
    for idx, q in enumerate(QUAD_NAMES):
        payload = (b"\x1e" + json.dumps(feat).encode("utf-8") + b"\n") if idx == 0 else b""
        (quad_dir / f"buildings_{q}.geojsonseq").write_bytes(payload)

    (out_dir / "curated_overrides.json").write_text(json.dumps({"version": 1, "overrides": {}}), encoding="utf-8")
    (out_dir / "search_index.json").write_text(json.dumps([]), encoding="utf-8")

    monkeypatch.setattr(
        "atlas_pipeline.fix_campus_anomalies_and_schools.compile_quadrant_pmtiles",
        lambda shard_dir, output_dir, quad_names=QUAD_NAMES: (
            [f"houston_buildings_{q}.pmtiles" for q in quad_names],
            1024,
        ),
    )
    monkeypatch.setattr(
        "sys.argv",
        ["atlas-pipeline", "--mode", "campuses", "--cache-dir", str(cache_dir), "--output-dir", str(out_dir)],
    )

    cli.main()
    saved_stats = json.loads((out_dir / "stats_summary.json").read_text(encoding="utf-8"))
    assert saved_stats["total_buildings"] == 1
    assert saved_stats["dated_buildings"] == 1
    assert saved_stats["observed_footprints"] == 1
    assert saved_stats["total_landmarks"] == 0


def test_enrich_styles_and_search_index_overwrites_zero_street_item_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify enrich_styles_and_search_index writes numbered situs back to item['address'] over '0 <STREET>' placeholders."""
    from atlas_pipeline import enrich_styles_and_search_index as ess

    si_path = tmp_path / "search_index.json"
    bld_path = tmp_path / "buildings.geojson"
    ov_path = tmp_path / "curated_overrides.json"
    ovl_path = tmp_path / "overlays.json"

    si_path.write_text(
        json.dumps([
            {
                "type": "building",
                "id": "school_test",
                "building_id": "1223580020001#cy_ridge_hs_main",
                "hcad_num": "1223580020001",
                "label": "CYPRESS RIDGE High School",
                "address": "0 N ELDRIDGE PKWY",
                "category": "CYPRESS-FAIRBANKS ISD",
                "year_built": 1899,
            }
        ]),
        encoding="utf-8",
    )
    bld_path.write_text(json.dumps({"type": "FeatureCollection", "features": []}), encoding="utf-8")
    ov_path.write_text(
        json.dumps({
            "version": 1,
            "overrides": {
                "1223580020001#cy_ridge_hs_main": {
                    "id": "1223580020001#cy_ridge_hs_main",
                    "building_id": "1223580020001#cy_ridge_hs_main",
                    "hcad_num": "1223580020001",
                    "address": "7900 N ELDRIDGE PKWY",
                    "year_built": 2002,
                    "decade": 2000,
                    "architect": "PBK Architects",
                    "bld_style": "Contemporary Educational Complex",
                    "use_category": "Civic / Institutional",
                    "contributing": "Outside Historic District",
                }
            },
        }),
        encoding="utf-8",
    )
    ovl_path.write_text(
        json.dumps({
            "landmarks": {"type": "FeatureCollection", "features": []},
            "good_brick_awards": {"type": "FeatureCollection", "features": []},
        }),
        encoding="utf-8",
    )

    monkeypatch.setattr(ess, "SEARCH_INDEX_PATH", str(si_path))
    monkeypatch.setattr(ess, "BUILDINGS_PATH", str(bld_path))
    monkeypatch.setattr(ess, "OVERRIDES_PATH", str(ov_path))
    monkeypatch.setattr(ess, "OVERLAYS_PATH", str(ovl_path))
    monkeypatch.setattr(ess, "SHARD_GLOB", str(tmp_path / "*.geojsonseq"))

    ess.main()
    updated_si = json.loads(si_path.read_text(encoding="utf-8"))
    assert updated_si[0]["address"] == "7900 N ELDRIDGE PKWY"
    assert updated_si[0]["year_built"] == 2002
    assert "7900 N ELDRIDGE PKWY" in updated_si[0]["sublabel"]
