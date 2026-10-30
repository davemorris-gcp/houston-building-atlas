"""Unit tests for the Lost_Houston_Catalog Google Sheets sync payload generator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from atlas_pipeline.sync_lost_houston_sheet import (
    ALL_HEADERS,
    COL_IDX,
    DEFAULT_OVERLAYS_PATH,
    DEFAULT_PHOTOS_PATH,
    SID_HUB,
    SID_LOST,
    _assert_catalog_invariants,
    build_create_raw_batch,
    build_lost_houston_catalog_rows,
    build_style_raw_batch,
    build_values_batch,
    col_letter,
    compute_grid_row_count,
    format_designation_id,
    main,
    sanitize_sheet_text,
)


@pytest.fixture(scope="module")
def live_catalog_snapshot() -> tuple[list[list[Any]], list[list[float]], dict[str, Any]]:
    overlays = json.loads(Path(DEFAULT_OVERLAYS_PATH).read_text(encoding="utf-8"))
    building_photos = json.loads(Path(DEFAULT_PHOTOS_PATH).read_text(encoding="utf-8"))
    return build_lost_houston_catalog_rows(
        overlays,
        building_photos,
        verify_milestone2_counts=True,
    )


def _box_geom(lon0: float, lat0: float, lon1: float, lat1: float) -> dict[str, Any]:
    return {
        "type": "Polygon",
        "coordinates": [[[lon0, lat0], [lon1, lat0], [lon1, lat1], [lon0, lat1], [lon0, lat0]]],
    }


def test_live_milestone2_catalog_integrity(
    live_catalog_snapshot: tuple[list[list[Any]], list[list[float]], dict[str, Any]],
) -> None:
    rows_a_ac, rows_ag_ah, metrics = live_catalog_snapshot

    assert len(rows_a_ac) == 623
    assert len(rows_ag_ah) == 623
    assert metrics["total_sites"] == 622
    assert metrics["cohort_counts"] == {1: 85, 2: 7, 3: 15, 4: 205, 5: 310}
    assert metrics["designated_landmark_count"] == 23
    assert metrics["holc_zoned_total"] == 393
    assert metrics["holc_counts"]["D"] == 168
    assert metrics["photo_site_count"] == 133

    capitol_row = next(r for r in rows_a_ac[1:] if r[COL_IDX["lost_id"]] == "lost_republic_of_texas_capitol")
    assert capitol_row[COL_IDX["site_hcad_nums"]] == "0010570000009"
    assert capitol_row[COL_IDX["cohort_tier"]] == "1. Curated Survey Landmark"

    # Verify Freedmen's Town Heritage District (COH Ch. 33) populates historic_district
    jd_row = next(r for r in rows_a_ac[1:] if r[COL_IDX["lost_id"]] == "lost_jefferson_davis_hospital_1937")
    assert "Freedmen's Town" in jd_row[COL_IDX["historic_district"]]
    assert jd_row[COL_IDX["designation_or_record_id"]] == "COH #98LD74"

    has_lte_lifespan = any(
        isinstance(r[COL_IDX["lifespan_label"]], str) and r[COL_IDX["lifespan_label"]].startswith("<=")
        for r in rows_a_ac[1:]
    )
    assert has_lte_lifespan is True


def test_helpers_sanitize_designation_and_col_letter() -> None:
    assert sanitize_sheet_text(None) == ""
    assert sanitize_sheet_text("   ") == ""
    assert sanitize_sheet_text("  Ward  \u2014  East \u2013 1905  ") == "Ward - East - 1905"

    assert col_letter(0) == "A"
    assert col_letter(25) == "Z"
    assert col_letter(27) == "AB"
    assert col_letter(len(ALL_HEADERS) - 1) == "AH"
    with pytest.raises(ValueError):
        col_letter(-1)

    assert format_designation_id({"loc_volume_id": "1907_v1", "loc_sheet_num": 3}) == "Sanborn 1907_v1 Sh. 3"
    combined = format_designation_id(
        {
            "coh_landmark_code": "98LD74",
            "nrhp_refnum": "84001761",
            "red_book_id": "rb1915_1",
            "demo_permit_date": "07/16/2025",
            "demo_permit_desc": "DEMO RES",
            "loc_volume_id": "1907_v1",
            "loc_sheet_num": 3,
        }
    )
    assert combined == "COH #98LD74; NRHP #84001761; 1915 Red Book: rb1915_1; Demo Permit 07/16/2025 (DEMO RES)"


def test_synthetic_catalog_enrichment_photo_fallback_and_validation() -> None:
    zone_geom = _box_geom(-95.39, 29.75, -95.37, 29.77)
    overlays = {
        "lost_houston": {
            "features": [
                {
                    "type": "Feature",
                    "geometry": _box_geom(-95.381, 29.756, -95.380, 29.757),
                    "properties": {
                        "id": "lost_synth_b",
                        "source_cohort": "red_book_1915_vanished",
                        "symbol_rank": 3,
                        "name": "Synth Church \u2014 Fourth Ward",
                        "address": "1200 Andrews St",
                        "historic_address_1915": "1200 Andrews Ave",
                        "lifespan_label": "c. 1905\u20131960",
                        "year_built": 1905,
                        "year_demolished": 1960,
                        "lifespan_years": 55,
                        "neighborhood": "Fourth Ward",
                        "architect": "A. Architect",
                        "builder": "B. Builder",
                        "stories": 2.5,
                        "site_hcad_nums": ["0010570000009"],
                        "hcad_num": "0010570000010",
                        "lon": -95.3805,
                        "lat": 29.7565,
                        "source": "1915 Red Book",
                    },
                },
                {
                    "type": "Feature",
                    "geometry": _box_geom(-95.50, 29.80, -95.49, 29.81),
                    "properties": {
                        "id": "lost_synth_a",
                        "source_cohort": "curated_landmark",
                        "symbol_rank": 1,
                        "name": "Alpha Landmark",
                        "address": "",
                        "historic_address_1915": "500 Main St",
                        "year_built": 1890,
                        "year_demolished": 1950,
                        "stories": 3.0,
                        "coh_landmark_code": "98LD74",
                        "lon": -95.495,
                        "lat": 29.805,
                    },
                },
            ]
        },
        "super_neighborhoods": {"features": [{"geometry": zone_geom, "properties": {"name": "Fourth Ward"}}]},
        "council_districts": {
            "features": [{"geometry": zone_geom, "properties": {"district_letter": "C", "council_member": "Member C"}}]
        },
        "holc_1937_zones": {
            "features": [
                {
                    "geometry": zone_geom,
                    "properties": {"holc_grade": "D", "zone_code": "D-5", "grade_short": "Hazardous", "area_title": "Fourth Ward"},
                }
            ]
        },
        "historic_districts": {"features": []},
        "heritage_districts": {
            "features": [{"geometry": zone_geom, "properties": {"name": "Freedmen's Town Heritage District"}}]
        },
        "nrhp_districts": {"features": [{"geometry": zone_geom, "properties": {"name": "FREEDMEN'S TOWN"}}]},
    }
    photos = {
        "photos": [
            {
                "building_id": "LOST_SYNTH_B",
                "is_lost_houston": True,
                "image_url": "https://upload.wikimedia.org/p1.jpg",
                "credit": "HMRC Archive",
            },
            {
                "building_id": "lost_synth_b",
                "is_lost_houston": True,
                "image_url": "https://upload.wikimedia.org/p2.jpg",
                "attribution": "Second Credit",
            },
        ]
    }

    rows_a_ac, rows_ag_ah, metrics = build_lost_houston_catalog_rows(overlays, photos)
    assert metrics["total_sites"] == 2
    assert metrics["designated_landmark_count"] == 1
    assert metrics["photo_site_count"] == 1
    assert metrics["total_curated_photos"] == 2

    # Cohort 1 sorts before Cohort 4
    r_alpha, r_beta = rows_a_ac[1], rows_a_ac[2]
    assert r_alpha[COL_IDX["lost_id"]] == "lost_synth_a"
    assert r_alpha[COL_IDX["address"]] == "1915: 500 Main St"
    assert r_alpha[COL_IDX["stories"]] == 3
    assert r_alpha[COL_IDX["council_district"]] == "Outside COH"

    assert r_beta[COL_IDX["lost_id"]] == "lost_synth_b"
    assert r_beta[COL_IDX["name"]] == "Synth Church - Fourth Ward"
    assert r_beta[COL_IDX["address"]] == "1200 Andrews St [1915: 1200 Andrews Ave]"
    assert r_beta[COL_IDX["lifespan_label"]] == "c. 1905-1960"
    assert r_beta[COL_IDX["architect_or_builder"]] == "A. Architect; Builder: B. Builder"
    assert r_beta[COL_IDX["stories"]] == 2.5
    assert r_beta[COL_IDX["site_hcad_nums"]] == "0010570000009; 0010570000010"
    assert r_beta[COL_IDX["historic_district"]] == "Freedmen's Town Heritage District; NRHP: FREEDMEN'S TOWN"
    assert r_beta[COL_IDX["archival_photo_url"]] == "https://upload.wikimedia.org/p1.jpg"
    assert r_beta[COL_IDX["archival_photo_credit"]] == "[2 curated photos] HMRC Archive"

    # Negative validation gates
    bad_dash_rows = [rows_a_ac[0], list(r_beta)]
    bad_dash_rows[1][COL_IDX["name"]] = "Bad \u2014 Dash"
    with pytest.raises(AssertionError, match="Typographic dash"):
        _assert_catalog_invariants(bad_dash_rows, rows_ag_ah[:2], {**metrics, "total_sites": 1, "cohort_counts": {4: 1}})

    bad_hcad_rows = [rows_a_ac[0], list(r_beta)]
    bad_hcad_rows[1][COL_IDX["site_hcad_nums"]] = "10570000009"
    with pytest.raises(AssertionError, match="Malformed 13-digit"):
        _assert_catalog_invariants(bad_hcad_rows, rows_ag_ah[:2], {**metrics, "total_sites": 1, "cohort_counts": {4: 1}})

    overlays["lost_houston"]["features"][0]["properties"]["source_cohort"] = "invalid_cohort"
    with pytest.raises(ValueError, match="Unknown source_cohort"):
        build_lost_houston_catalog_rows(overlays, photos)


def test_batch_builders_idempotency_scaling_and_cli(
    tmp_path: Path,
    live_catalog_snapshot: tuple[list[list[Any]], list[list[float]], dict[str, Any]],
) -> None:
    rows_a_ac, rows_ag_ah, metrics = live_catalog_snapshot

    # Default re-sync flags (False, False) never mutate Admin Hub or add duplicate sheets/rules
    assert build_create_raw_batch(False, False, total_rows=750) == {"requests": []}
    provision_reqs = build_create_raw_batch(True, True, total_rows=750)["requests"]
    assert provision_reqs[0]["addSheet"]["properties"]["sheetId"] == SID_LOST
    assert provision_reqs[0]["addSheet"]["properties"]["gridProperties"]["rowCount"] == compute_grid_row_count(750)
    assert provision_reqs[1]["insertDimension"]["range"]["sheetId"] == SID_HUB

    ops_resync = build_values_batch(rows_a_ac, rows_ag_ah, include_hub_row=False, metrics=metrics)
    assert [op["op"] for op in ops_resync] == ["clear", "write", "write", "write"]
    assert ops_resync[0]["range"] == "Lost_Houston_Catalog!A1:AH"
    assert ops_resync[1]["value_input"] == "RAW"
    assert ops_resync[2]["value_input"] == "RAW"
    assert ops_resync[3]["value_input"] == "USER_ENTERED"
    assert all("Admin_Hub" not in op["range"] for op in ops_resync)
    assert "&lost_houston=1&bnd=lost_houston:" in ops_resync[3]["data"][0][0]

    ops_hub = build_values_batch(rows_a_ac, rows_ag_ah, include_hub_row=True, metrics=metrics)
    assert len(ops_hub) == 5
    assert ops_hub[4]["range"] == "Admin_Hub_&_Quick_Filter!A18:I18"
    assert len(ops_hub[4]["data"]) == 1

    # Re-sync style uses updateFilterView + dynamic grid rows and zero duplicate add* requests
    resync_style = build_style_raw_batch(total_rows=750, metrics=metrics, initial_setup=False)["requests"]
    assert not any("addFilterView" in r for r in resync_style)
    assert not any("addConditionalFormatRule" in r for r in resync_style)
    assert not any("addProtectedRange" in r for r in resync_style)
    upd_fvs = [r["updateFilterView"]["filter"] for r in resync_style if "updateFilterView" in r]
    assert [fv["filterViewId"] for fv in upd_fvs] == [SID_LOST + i for i in range(1, 7)]
    assert "23 Sites" in upd_fvs[0]["title"]
    assert str(COL_IDX["designation_or_record_id"]) in upd_fvs[0]["criteria"]

    plain_text_cols = {
        r["repeatCell"]["range"]["startColumnIndex"]
        for r in resync_style
        if r.get("repeatCell", {}).get("cell", {}).get("userEnteredFormat", {}).get("numberFormat", {}).get("pattern")
        == "@"
    }
    assert plain_text_cols == {COL_IDX["lifespan_label"], COL_IDX["site_hcad_nums"]}
    for r in resync_style:
        if r.get("repeatCell", {}).get("cell", {}).get("userEnteredFormat", {}).get("numberFormat", {}).get("pattern") == "@":
            assert r["repeatCell"]["range"]["endRowIndex"] == 850

    # Initial provisioning style emits addFilterView + conditional rules + warning protection
    init_style = build_style_raw_batch(total_rows=623, metrics=metrics, initial_setup=True)["requests"]
    assert len([r for r in init_style if "addFilterView" in r]) == 6
    assert len([r for r in init_style if "addConditionalFormatRule" in r]) == 9
    prot = [r["addProtectedRange"]["protectedRange"] for r in init_style if "addProtectedRange" in r]
    assert len(prot) == 1 and prot[0]["warningOnly"] is True

    # End-to-end CLI smoke test into tmp_path
    main(["--output-dir", str(tmp_path), "--verify-milestone2-counts"])
    assert (tmp_path / "lost_houston_create_raw_batch.json").exists()
    assert (tmp_path / "lost_houston_values_batch.json").exists()
    assert (tmp_path / "lost_houston_style_raw_batch.json").exists()
