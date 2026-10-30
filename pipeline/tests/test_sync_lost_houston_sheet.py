"""Unit tests for the Lost_Houston_Catalog Google Sheets sync payload generator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from atlas_pipeline.sync_lost_houston_sheet import (
    ALL_HEADERS,
    BUILDING_PHOTOS_OPEN_IN_ATLAS_FORMULA,
    COL_IDX,
    DEFAULT_OVERLAYS_PATH,
    DEFAULT_PHOTOS_PATH,
    FILTER_VIEW_COUNT,
    SID_HUB,
    SID_INSTR,
    SID_LOST,
    SOP14_TARGET_ROW,
    _assert_catalog_invariants,
    build_create_raw_batch,
    build_lost_houston_catalog_rows,
    build_sop14_row,
    build_style_raw_batch,
    build_values_batch,
    col_letter,
    compute_grid_row_count,
    format_designation_id,
    ingest_lost_houston_sheet_photos,
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
        verify_milestone2_counts=False,
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

    assert len(rows_a_ac) == metrics["total_sites"] + 1
    assert len(rows_ag_ah) == metrics["total_sites"] + 1
    assert metrics["total_sites"] >= 622
    assert metrics["cohort_counts"][1] >= 85
    assert metrics["designated_landmark_count"] >= 23
    assert metrics["holc_zoned_total"] >= 393
    assert metrics["holc_counts"]["D"] >= 168
    assert metrics["photo_site_count"] >= 133
    assert metrics["total_curated_photos"] >= 184
    assert metrics["photo_site_count"] + metrics["unphotographed_site_count"] == metrics["total_sites"]

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
    assert metrics["unphotographed_site_count"] == 1
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
        _assert_catalog_invariants(
            bad_dash_rows,
            rows_ag_ah[:2],
            {**metrics, "total_sites": 1, "cohort_counts": {4: 1}, "photo_site_count": 1, "unphotographed_site_count": 0},
        )

    bad_hcad_rows = [rows_a_ac[0], list(r_beta)]
    bad_hcad_rows[1][COL_IDX["site_hcad_nums"]] = "10570000009"
    with pytest.raises(AssertionError, match="Malformed 13-digit"):
        _assert_catalog_invariants(
            bad_hcad_rows,
            rows_ag_ah[:2],
            {**metrics, "total_sites": 1, "cohort_counts": {4: 1}, "photo_site_count": 1, "unphotographed_site_count": 0},
        )

    overlays["lost_houston"]["features"][0]["properties"]["source_cohort"] = "invalid_cohort"
    with pytest.raises(ValueError, match="Unknown source_cohort"):
        build_lost_houston_catalog_rows(overlays, photos)


def test_ingest_building_photos_csv_df6_isolation(tmp_path: Path) -> None:
    sanborn_fallback_url = "https://tile.loc.gov/image-services/iiif/service:gmd:gmd403m:sheet/full/pct:25/0/default.jpg"
    overlays = {
        "lost_houston": {
            "features": [
                {
                    "type": "Feature",
                    "geometry": _box_geom(-95.37, 29.75, -95.36, 29.76),
                    "properties": {
                        "id": "lost_test_monument",
                        "name": "Test Monument",
                    },
                },
                {
                    "type": "Feature",
                    "geometry": _box_geom(-95.36, 29.75, -95.35, 29.76),
                    "properties": {
                        "id": "lost_sanborn_only",
                        "name": "Sanborn Fallback Monument",
                        "photo_url": sanborn_fallback_url,
                        "photo_credit": "Library of Congress Sanborn Sheet",
                        "sanborn_sheet_image_url": sanborn_fallback_url,
                    },
                },
            ]
        }
    }
    building_photos = {
        "photos": [
            {
                "hcad_num": "0010810000007",
                "building_id": "bld_standing",
                "landmark_name": "Standing Gulf Building",
                "address": "712 MAIN ST",
                "image_url": "https://upload.wikimedia.org/standing.jpg",
            }
        ]
    }
    csv_file = tmp_path / "Building_Photos.csv"
    csv_file.write_text(
        "hcad_num,building_id,landmark_name,address,photo_year,era_label,caption,image_url,thumb_url,credit,source_url\n"
        "0010810000007,bld_standing,Standing Gulf Building,712 MAIN ST,1929,Opening,Cap,https://upload.wikimedia.org/standing.jpg,,WM,\n"
        "0010570000009,LOST_TEST_MONUMENT,,909 TEXAS AVE,1912,c. 1912 Postcard,Historic view,https://upload.wikimedia.org/lost1.jpg,,HMRC Archive,https://cdm17006.contentdm.oclc.org/id/1\n"
        ",lost_test_monument,Test Monument,,1912,Dup,Dup,https://upload.wikimedia.org/lost1.jpg,,HMRC Archive,\n"
        ",lost_sanborn_only,Sanborn Fallback Monument,,1905,c. 1905 Photo,Real facade,https://upload.wikimedia.org/real_facade.jpg,,HABS Collection,\n",
        encoding="utf-8",
    )

    stats = ingest_lost_houston_sheet_photos(overlays, building_photos, csv_file)
    assert stats == {"scanned_lost_rows": 3, "added_photos": 2, "backfilled_features": 2}
    assert len(building_photos["photos"]) == 3
    assert building_photos["photos"][0]["building_id"] == "bld_standing"

    added = building_photos["photos"][1]
    assert added["building_id"] == "lost_test_monument"
    assert added["is_lost_houston"] is True
    assert added["curation_source"] == "building_photos_sheet"
    assert "hcad_num" not in added
    assert "address" not in added
    assert added["landmark_name"] == "Test Monument"
    assert added["attribution"] == "HMRC Archive"
    assert overlays["lost_houston"]["features"][0]["properties"]["photo_url"] == "https://upload.wikimedia.org/lost1.jpg"
    assert overlays["lost_houston"]["features"][0]["properties"]["photo_credit"] == "HMRC Archive"

    # Real archival photo replaces LOC Sanborn map plate fallback on feature properties
    assert (
        overlays["lost_houston"]["features"][1]["properties"]["photo_url"]
        == "https://upload.wikimedia.org/real_facade.jpg"
    )
    assert overlays["lost_houston"]["features"][1]["properties"]["photo_credit"] == "HABS Collection"

    # Missing required CSV columns (e.g., wrong sheet tab exported) fails fast
    wrong_tab_csv = tmp_path / "wrong_tab.csv"
    wrong_tab_csv.write_text("lost_id,cohort_tier,name\nlost_test_monument,1,Test\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing required column"):
        ingest_lost_houston_sheet_photos(overlays, building_photos, wrong_tab_csv)

    # Atomic failure rollback: valid Row 2 must NOT mutate inputs when Row 3 is invalid
    photos_before = json.loads(json.dumps(building_photos))
    overlays_before = json.loads(json.dumps(overlays))
    partial_bad_csv = tmp_path / "partial_bad.csv"
    partial_bad_csv.write_text(
        "hcad_num,building_id,image_url,credit\n"
        ",lost_test_monument,https://upload.wikimedia.org/should_not_commit.jpg,Test\n"
        ",lost_nonexistent_id,https://upload.wikimedia.org/x.jpg,Test\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown lost_id"):
        ingest_lost_houston_sheet_photos(overlays, building_photos, partial_bad_csv)
    assert building_photos == photos_before
    assert overlays == overlays_before


def test_batch_builders_sop14_idempotency_scaling_and_cli(
    tmp_path: Path,
    live_catalog_snapshot: tuple[list[list[Any]], list[list[float]], dict[str, Any]],
) -> None:
    rows_a_ac, rows_ag_ah, metrics = live_catalog_snapshot

    sop14 = build_sop14_row(metrics)
    assert len(sop14) == 3
    assert "SOP 14" in sop14[0]
    assert f"{metrics['unphotographed_site_count']} Sites" in sop14[1]
    assert "DF-6" in sop14[1]
    assert "--include-sop14 --update-hub-row" in sop14[1]
    assert "curation_source: building_photos_sheet" in sop14[1]
    assert all("\u2014" not in c and "\u2013" not in c for c in sop14)

    # Ensure Building_Photos!L1 trims leading whitespace and matches setup_admin_sheet.py
    assert "REGEXMATCH(TRIM(TO_TEXT(B2:B))" in BUILDING_PHOTOS_OPEN_IN_ATLAS_FORMULA
    setup_src = (Path(DEFAULT_OVERLAYS_PATH).parents[3] / "pipeline" / "atlas_pipeline" / "setup_admin_sheet.py").read_text(
        encoding="utf-8"
    )
    assert "REGEXMATCH(TRIM(TO_TEXT(B2:B))" in setup_src

    assert build_create_raw_batch(False, False, total_rows=750) == {"requests": []}
    provision_reqs = build_create_raw_batch(True, True, include_sop14=True, total_rows=750)["requests"]
    assert provision_reqs[0]["addSheet"]["properties"]["sheetId"] == SID_LOST
    assert provision_reqs[0]["addSheet"]["properties"]["gridProperties"]["rowCount"] == compute_grid_row_count(750)
    assert provision_reqs[1]["insertDimension"]["range"]["sheetId"] == SID_HUB
    assert provision_reqs[2]["repeatCell"]["range"]["sheetId"] == SID_INSTR
    assert provision_reqs[2]["repeatCell"]["range"]["startRowIndex"] == SOP14_TARGET_ROW - 1

    ops_resync = build_values_batch(rows_a_ac, rows_ag_ah, include_hub_row=False, metrics=metrics)
    assert [op["op"] for op in ops_resync] == ["clear", "write", "write", "write"]
    assert ops_resync[0]["range"] == "Lost_Houston_Catalog!A1:AH"
    assert all("Admin_Hub" not in op["range"] and "Instructions" not in op["range"] for op in ops_resync)
    assert "&lost_houston=1&bnd=lost_houston:" in ops_resync[3]["data"][0][0]

    ops_full = build_values_batch(
        rows_a_ac,
        rows_ag_ah,
        include_hub_row=True,
        include_sop14=True,
        metrics=metrics,
    )
    assert len(ops_full) == 7
    assert ops_full[4]["range"] == "Admin_Hub_&_Quick_Filter!A18:I18"
    assert ops_full[5]["range"] == f"Instructions_&_Apps_Script!A{SOP14_TARGET_ROW}:C{SOP14_TARGET_ROW}"
    assert ops_full[5]["value_input"] == "RAW"
    assert ops_full[6]["range"] == "Building_Photos!L1"
    assert ops_full[6]["data"] == [[BUILDING_PHOTOS_OPEN_IN_ATLAS_FORMULA]]

    resync_style = build_style_raw_batch(total_rows=750, metrics=metrics, initial_setup=False)["requests"]
    assert not any("addFilterView" in r for r in resync_style)
    assert not any("addConditionalFormatRule" in r for r in resync_style)
    assert not any("addProtectedRange" in r for r in resync_style)
    upd_fvs = [r["updateFilterView"]["filter"] for r in resync_style if "updateFilterView" in r]
    assert [fv["filterViewId"] for fv in upd_fvs] == [SID_LOST + i for i in range(1, FILTER_VIEW_COUNT + 1)]
    assert "23 Sites" in upd_fvs[0]["title"]
    assert f"{metrics['unphotographed_site_count']} Sites" in upd_fvs[6]["title"]
    assert upd_fvs[6]["criteria"][str(COL_IDX["archival_photo_url"])]["condition"]["type"] == "BLANK"

    # Incremental migration flags: create only Filter View 7 while updating 1..6 + styling SOP 14
    mig_style = build_style_raw_batch(
        total_rows=623,
        metrics=metrics,
        initial_setup=False,
        add_filter_view_offsets={7},
        include_sop14=True,
    )["requests"]
    assert [r["addFilterView"]["filter"]["filterViewId"] for r in mig_style if "addFilterView" in r] == [SID_LOST + 7]
    assert len([r for r in mig_style if "updateFilterView" in r]) == 6
    assert any(
        "repeatCell" in r and r["repeatCell"]["range"]["sheetId"] == SID_INSTR
        for r in mig_style
    )
    with pytest.raises(ValueError, match="add_filter_view_offsets"):
        build_style_raw_batch(total_rows=623, metrics=metrics, add_filter_view_offsets={0, 8})

    init_style = build_style_raw_batch(total_rows=623, metrics=metrics, initial_setup=True)["requests"]
    assert len([r for r in init_style if "addFilterView" in r]) == FILTER_VIEW_COUNT
    assert len([r for r in init_style if "addConditionalFormatRule" in r]) == 9
    prot = [r["addProtectedRange"]["protectedRange"] for r in init_style if "addProtectedRange" in r]
    assert len(prot) == 1 and prot[0]["warningOnly"] is True

    main(["--output-dir", str(tmp_path), "--include-sop14", "--verify-milestone2-counts"])
    assert (tmp_path / "lost_houston_create_raw_batch.json").exists()
    assert (tmp_path / "lost_houston_values_batch.json").exists()
    assert (tmp_path / "lost_houston_style_raw_batch.json").exists()

    # --write-site-data without --ingest-building-photos-csv must fail fast via parser.error
    with pytest.raises(SystemExit):
        main(["--output-dir", str(tmp_path), "--write-site-data"])


def test_cli_end_to_end_ingest_and_write_compact_site_data(tmp_path: Path) -> None:
    tmp_overlays = tmp_path / "overlays.json"
    tmp_photos = tmp_path / "building_photos.json"
    tmp_csv = tmp_path / "Building_Photos.csv"
    out_dir = tmp_path / "batches"

    sample_overlays = {
        "lost_houston": {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": _box_geom(-95.37, 29.75, -95.36, 29.76),
                    "properties": {
                        "id": "lost_cli_target",
                        "source_cohort": "historic_teardown_2020_2026",
                        "symbol_rank": 4,
                        "name": "CLI Target House",
                        "address": "100 Main St",
                        "year_built": 1920,
                        "year_demolished": 2024,
                        "lon": -95.365,
                        "lat": 29.755,
                    },
                }
            ],
        },
        "super_neighborhoods": {"features": []},
        "council_districts": {"features": []},
        "holc_1937_zones": {"features": []},
        "historic_districts": {"features": []},
        "heritage_districts": {"features": []},
        "nrhp_districts": {"features": []},
    }
    sample_photos = {
        "photos": [
            {
                "hcad_num": "0010810000007",
                "building_id": "bld_standing",
                "image_url": "https://upload.wikimedia.org/s.jpg",
            }
        ]
    }
    tmp_overlays.write_text(json.dumps(sample_overlays), encoding="utf-8")
    tmp_photos.write_text(json.dumps(sample_photos), encoding="utf-8")
    tmp_csv.write_text(
        "hcad_num,building_id,landmark_name,address,photo_year,image_url,credit\n"
        "0010570000009, lost_cli_target ,CLI Target House,100 MAIN ST,1928,https://upload.wikimedia.org/cli.jpg,HMRC\n",
        encoding="utf-8",
    )

    main(
        [
            "--overlays",
            str(tmp_overlays),
            "--photos",
            str(tmp_photos),
            "--ingest-building-photos-csv",
            str(tmp_csv),
            "--write-site-data",
            "--include-sop14",
            "--update-hub-row",
            "--add-filter-view-offsets",
            "7",
            "--output-dir",
            str(out_dir),
        ]
    )

    # Single-line compact JSON invariant required by tests/unit/data_integrity_and_bundle.test.mjs
    raw_photos = tmp_photos.read_text(encoding="utf-8")
    raw_overlays = tmp_overlays.read_text(encoding="utf-8")
    assert "\n" not in raw_photos.rstrip("\n")
    assert "\n" not in raw_overlays.rstrip("\n")

    persisted_photos = json.loads(raw_photos)["photos"]
    assert len(persisted_photos) == 2
    assert persisted_photos[1]["building_id"] == "lost_cli_target"
    assert persisted_photos[1]["is_lost_houston"] is True
    assert persisted_photos[1]["curation_source"] == "building_photos_sheet"
    assert "hcad_num" not in persisted_photos[1]
    assert "address" not in persisted_photos[1]

    create_reqs = json.loads((out_dir / "lost_houston_create_raw_batch.json").read_text(encoding="utf-8"))["requests"]
    assert not any("insertDimension" in r or "addSheet" in r for r in create_reqs)

    vals_ops = json.loads((out_dir / "lost_houston_values_batch.json").read_text(encoding="utf-8"))
    assert any(op["range"] == "Admin_Hub_&_Quick_Filter!A18:I18" for op in vals_ops)
    sop_op = next(op for op in vals_ops if op["range"].startswith("Instructions_&_Apps_Script!"))
    assert "1 sites carry 1 curated archival photographs" in sop_op["data"][0][1]

    style_reqs = json.loads((out_dir / "lost_houston_style_raw_batch.json").read_text(encoding="utf-8"))["requests"]
    assert [r["addFilterView"]["filter"]["filterViewId"] for r in style_reqs if "addFilterView" in r] == [SID_LOST + 7]
    assert len([r for r in style_reqs if "updateFilterView" in r]) == 6
