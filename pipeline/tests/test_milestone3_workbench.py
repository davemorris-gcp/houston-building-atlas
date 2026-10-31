"""Milestone 3 unit & cross-language integration tests for Staff & Field Workbench.

Covers:
  - UT-1 / UT-2: Local tangent-plane metric rotation/nudge, post-rounding polygon topology
    validation (`validate_and_normalize_override_polygon`), CW->CCW winding, bowtie/spike
    rejection, county bounding box, and closing-vertex parity.
  - UT-3 / UT-4: `[GEOMETRY_GEOJSON:{...}]` RFC 8259 round-trip, sequential CSV fold
    (`fold_approved_csv_overrides` / `sync_curated_overrides`), unknown-year standing polygon
    preservation, Tier 1 source_type preservation, and 4-mode `classify_override_footprint_mode`.
  - UT-6: `build_historical_audit_queue_payload` invariants, duplicate `finding_id` rejection,
    and on-disk `app/public/data/historical_audit_queue.json` category/byte/URL verification.
  - UT-9: `[FIELD_SURVEY_JSON:{...}]` envelope round-trip, `recover_field_survey_rows_from_pending_csv`
    18-column losslessness, deterministic `survey_id` dedup, formula neutralization, and override exclusion.
  - UT-11 / IT-5: 12-tab Google Sheet builders, Admin Hub coordinates (Row 18 Lost Houston,
    Row 19 Field Surveys, Row 22 Quick Filter), `SOP 14` Row 25 & `SOP 15` Row 26, `B6` passkey
    & `S:V` preservation, `verify_live_sheet_anchors` positive + negative drift checks, enum parity,
    and `APPS_SCRIPT_CODE` Col W / `field_survey` / upsert curator-status / `/` safeguards.
  - IT-1 / IT-2 / IT-3: Live cross-language JS <-> Python subprocess parity checks.
"""

from __future__ import annotations

import csv
import io
import json
import math
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from atlas_pipeline.audit_historical_accuracy import build_historical_audit_queue_payload
from atlas_pipeline.schema import (
    FIELD_SURVEY_CONDITION_RATINGS,
    FIELD_SURVEY_INTEGRITY_GRADES,
    FIELD_SURVEY_SOURCE_TYPE,
    GOOGLE_SHEETS_CELL_MAX_CHARS,
    HARRIS_COUNTY_BBOX,
    MAX_STANDING_OVERRIDE_YEAR,
    METERS_PER_DEGREE_LAT,
    MIN_FOOTPRINT_AREA_SQ_M,
    OVERRIDE_MODE_ADDITIVE_POLYGON,
    OVERRIDE_MODE_PARCEL_REPLACEMENT,
    OVERRIDE_MODE_SHARD_PRIMARY,
    OVERRIDE_MODE_SUPPRESS_VACANT,
    POLYGON_EDITOR_MAX_VERTICES,
    SQ_FT_PER_SQ_M,
    classify_override_footprint_mode,
    compute_ring_planar_area_sq_m,
    encode_citation_with_field_survey,
    encode_citation_with_geometry,
    extract_embedded_field_survey_json,
    extract_embedded_geometry_json,
    normalize_standing_override_year,
    nudge_polygon_tangent_plane,
    rotate_polygon_tangent_plane,
    strip_embedded_field_survey_tag,
    strip_embedded_geometry_tag,
    validate_and_normalize_override_polygon,
)
from atlas_pipeline.setup_admin_sheet import (
    APPROVED_EDITS_FILTER_FORMULA,
    APPROVED_EDITS_HEADERS,
    APPROVED_TOTAL_COLS,
    APPS_SCRIPT_CODE,
    HUB_FILTER_INPUT_ROW,
    HUB_RESULTS_FORMULA_ROW,
    HUB_RESULTS_HEADER_ROW,
    HUB_TRIAGE_DROPDOWN_VALUES,
    PENDING_TOTAL_COLS,
    PENDING_TRIAGE_FORMULA,
    SID_APPROVED,
    SID_HUB,
    SID_INSTR,
    SID_LOST,
    SID_PENDING,
    SID_SURVEYS,
    build_admin_hub_rows,
    build_instructions_rows,
)
from atlas_pipeline.sync_curated_edits import (
    fold_approved_csv_overrides,
    merge_override_record,
    parse_override_target,
    sync_curated_overrides,
)
from atlas_pipeline.sync_field_surveys_sheet import (
    ALL_SURVEY_HEADERS,
    HEADERS_A_TO_R,
    HUB_SURVEYS_ROW,
    SHEET_TITLE_SURVEYS,
    SOP15_TARGET_ROW,
    SURVEY_CONTRIBUTING_VALUES,
    SURVEY_FILTER_VIEW_COUNT,
    SURVEY_REVIEW_STATUSES,
    build_milestone3_sheet_batches,
    build_sop15_row,
    recover_field_survey_rows_from_pending_csv,
    sanitize_sheet_cell,
    verify_live_sheet_anchors,
)
from atlas_pipeline.sync_lost_houston_sheet import (
    SHEET_TITLE_LOST,
    SID_LOST as LOST_SHEET_ID,
    SOP14_TARGET_ROW,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "app" / "public" / "data"


def _make_rect_ring(center_lng: float, center_lat: float, width_m: float, height_m: float) -> list[list[float]]:
    cos_lat = math.cos(math.radians(center_lat))
    half_dx = (width_m / 2.0) / (METERS_PER_DEGREE_LAT * cos_lat)
    half_dy = (height_m / 2.0) / METERS_PER_DEGREE_LAT
    return [
        [center_lng - half_dx, center_lat - half_dy],
        [center_lng + half_dx, center_lat - half_dy],
        [center_lng + half_dx, center_lat + half_dy],
        [center_lng - half_dx, center_lat + half_dy],
        [center_lng - half_dx, center_lat - half_dy],
    ]


def _make_regular_ngon(center_lng: float, center_lat: float, radius_m: float, n: int) -> list[list[float]]:
    cos_lat = math.cos(math.radians(center_lat))
    pts: list[list[float]] = []
    for i in range(n):
        theta = (2.0 * math.pi * i) / n
        dx = (radius_m * math.cos(theta)) / (METERS_PER_DEGREE_LAT * cos_lat)
        dy = (radius_m * math.sin(theta)) / METERS_PER_DEGREE_LAT
        pts.append([center_lng + dx, center_lat + dy])
    pts.append([pts[0][0], pts[0][1]])
    return pts


def test_ut1_metric_tangent_plane_rotation_nudge_and_snap_rounding() -> None:
    """UT-1: Rotate & nudge at ~29.76N preserve area (< 0.5% drift) and right angles; snap guarded."""
    ring = _make_rect_ring(-95.3635, 29.7604, 20.0, 30.0)
    base_res = validate_and_normalize_override_polygon(ring)
    assert base_res["ok"] is True
    assert abs(base_res["area_sq_m"] - 600.0) < 4.0

    for angle in (30.0, 45.0, 90.0, -34.65):
        rot_ring = rotate_polygon_tangent_plane(base_res["geometry"], angle)
        rot_res = validate_and_normalize_override_polygon(rot_ring)
        assert rot_res["ok"] is True, f"Rotation {angle} failed: {rot_res['errors']}"
        drift_pct = abs(rot_res["area_sq_m"] - base_res["area_sq_m"]) / base_res["area_sq_m"] * 100.0
        assert drift_pct < 0.5, f"Area drift {drift_pct:.3f}% exceeded 0.5% at {angle} deg"

        coords = rot_res["geometry"]["coordinates"][0]
        assert coords[0] == coords[-1]
        cos_lat = math.cos(math.radians(29.7604))
        xy = [(c[0] * METERS_PER_DEGREE_LAT * cos_lat, c[1] * METERS_PER_DEGREE_LAT) for c in coords[:4]]
        for i in range(4):
            prev_pt = xy[(i - 1) % 4]
            curr_pt = xy[i]
            next_pt = xy[(i + 1) % 4]
            vx1, vy1 = prev_pt[0] - curr_pt[0], prev_pt[1] - curr_pt[1]
            vx2, vy2 = next_pt[0] - curr_pt[0], next_pt[1] - curr_pt[1]
            cos_theta = abs(vx1 * vx2 + vy1 * vy2) / (math.hypot(vx1, vy1) * math.hypot(vx2, vy2))
            assert cos_theta < 0.01, f"WGS84 shear detected at corner {i} for angle {angle}: {cos_theta}"

    nudged = nudge_polygon_tangent_plane(base_res["geometry"], 3.0, -2.0)
    nudged_res = validate_and_normalize_override_polygon(nudged)
    assert nudged_res["ok"] is True
    orig_c = base_res["centroid"]
    new_c = nudged_res["centroid"]
    moved_east_m = (new_c[0] - orig_c[0]) * METERS_PER_DEGREE_LAT * math.cos(math.radians(29.7604))
    moved_north_m = (new_c[1] - orig_c[1]) * METERS_PER_DEGREE_LAT
    assert abs(moved_east_m - 3.0) < 0.25
    assert abs(moved_north_m - (-2.0)) < 0.25

    # Sub-ulp micro-sliver collapses under 6-decimal rounding and is rejected
    near_collapse = [
        [-95.3800001, 29.7800001],
        [-95.3800004, 29.7800004],
        [-95.3799000, 29.7800001],
        [-95.3800001, 29.7800001],
    ]
    assert validate_and_normalize_override_polygon(near_collapse)["ok"] is False

    # ~590 sq m C-shaped building whose East jaws sit 8e-7 deg (~9 cm) apart before rounding
    # (strictly valid simple CCW polygon unrounded), but snap onto the SAME 6-decimal vertex
    # [-95.38, 29.7801], creating an invalid pinched self-touching ring (kills MT-2).
    jaw_pinch_after_rounding = [
        [-95.380300, 29.780000],
        [-95.380000, 29.780000],
        [-95.380000, 29.7800996],
        [-95.380200, 29.780080],
        [-95.380200, 29.780120],
        [-95.380000, 29.7801004],
        [-95.380000, 29.780200],
        [-95.380300, 29.780200],
        [-95.380300, 29.780000],
    ]
    pinch_res = validate_and_normalize_override_polygon(jaw_pinch_after_rounding)
    assert pinch_res["ok"] is False
    assert any("pinched" in e or "self-touching" in e for e in pinch_res["errors"])


def test_ut2_polygon_topology_validator_boundaries() -> None:
    """UT-2: Vertex counts (2,3,64,65), CW->CCW, bowties, 180-deg spike, county bbox, and min area."""
    assert validate_and_normalize_override_polygon([[-95.38, 29.78], [-95.3799, 29.78]])["ok"] is False

    tri = _make_regular_ngon(-95.38, 29.78, 8.0, 3)
    res_tri = validate_and_normalize_override_polygon(tri)
    assert res_tri["ok"] is True
    assert res_tri["vertex_count"] == 3
    assert abs(res_tri["area_sq_ft"] - round(res_tri["area_sq_m"] * SQ_FT_PER_SQ_M, 1)) < 0.2

    poly64 = _make_regular_ngon(-95.38, 29.78, 15.0, POLYGON_EDITOR_MAX_VERTICES)
    res64 = validate_and_normalize_override_polygon(poly64)
    assert res64["ok"] is True
    assert res64["vertex_count"] == 64

    poly65 = _make_regular_ngon(-95.38, 29.78, 15.0, POLYGON_EDITOR_MAX_VERTICES + 1)
    res65 = validate_and_normalize_override_polygon(poly65)
    assert res65["ok"] is False
    assert any("exceeding maximum" in e for e in res65["errors"])

    cw_ring = list(reversed(_make_rect_ring(-95.39, 29.79, 12.0, 8.0)))
    cw_res = validate_and_normalize_override_polygon(cw_ring)
    assert cw_res["ok"] is True
    assert cw_res["was_reversed"] is True
    assert compute_ring_planar_area_sq_m(cw_res["geometry"]) > 90.0

    sym_bowtie = [
        [-95.3902, 29.7900],
        [-95.3900, 29.7902],
        [-95.3902, 29.7902],
        [-95.3900, 29.7900],
        [-95.3902, 29.7900],
    ]
    assert validate_and_normalize_override_polygon(sym_bowtie)["ok"] is False

    asym_bowtie = [
        [-95.3910, 29.7900],
        [-95.3900, 29.7910],
        [-95.3908, 29.7910],
        [-95.3900, 29.7900],
        [-95.3910, 29.7900],
    ]
    asym_res = validate_and_normalize_override_polygon(asym_bowtie)
    assert asym_res["ok"] is False
    assert any("self-intersects" in e for e in asym_res["errors"])

    spike = [
        [-95.3903, 29.7900],
        [-95.3900, 29.7900],
        [-95.39015, 29.7900],
        [-95.3900, 29.7902],
        [-95.3903, 29.7902],
        [-95.3903, 29.7900],
    ]
    spike_res = validate_and_normalize_override_polygon(spike)
    assert spike_res["ok"] is False
    assert any("collinear" in e or "intersects" in e for e in spike_res["errors"])

    austin = _make_rect_ring(-97.7431, 30.2672, 12.0, 12.0)
    austin_res = validate_and_normalize_override_polygon(austin)
    assert austin_res["ok"] is False
    assert any("Harris County bounds" in e for e in austin_res["errors"])

    tiny = _make_rect_ring(-95.39, 29.79, 1.0, 1.0)
    tiny_res = validate_and_normalize_override_polygon(tiny)
    assert tiny_res["ok"] is False
    assert MIN_FOOTPRINT_AREA_SQ_M == 4.0
    assert any("minimum threshold" in e for e in tiny_res["errors"])

    holed = {
        "type": "Polygon",
        "coordinates": [
            _make_rect_ring(-95.39, 29.79, 20.0, 20.0),
            _make_rect_ring(-95.39, 29.79, 5.0, 5.0),
        ],
    }
    assert validate_and_normalize_override_polygon(holed)["ok"] is False


def test_ut3_geometry_tag_encoding_decoding_and_cell_limit() -> None:
    """UT-3: Balanced `[GEOMETRY_GEOJSON:{...}]` parser handles nested `]]]`, re-saves, and corrupt tags."""
    geom64 = validate_and_normalize_override_polygon(
        _make_regular_ngon(-95.391, 29.792, 14.0, POLYGON_EDITOR_MAX_VERTICES)
    )["geometry"]
    base_cit = "Sanborn Fire Insurance Map Vol. 3 Sheet 312 [1924 ed.]"
    encoded1 = encode_citation_with_geometry(base_cit, geom64)
    assert len(encoded1) < 3500
    assert len(encoded1) < GOOGLE_SHEETS_CELL_MAX_CHARS
    assert extract_embedded_geometry_json(encoded1) == geom64
    assert strip_embedded_geometry_tag(encoded1) == base_cit

    encoded2 = encode_citation_with_geometry(encoded1, geom64)
    assert encoded2.count("[GEOMETRY_GEOJSON:") == 1
    assert strip_embedded_geometry_tag(encoded2) == base_cit

    corrupted = f"{base_cit} [GEOMETRY_GEOJSON:{{\"type\":\"Polygon\",\"coordinates\":[[[-95.39,29.79"
    assert extract_embedded_geometry_json(corrupted) is None
    assert strip_embedded_geometry_tag(corrupted) == corrupted

    with pytest.raises(ValueError, match="limit"):
        encode_citation_with_geometry("X" * GOOGLE_SHEETS_CELL_MAX_CHARS, geom64)


def test_ut4_csv_sequential_fold_modes_and_sync_curated_overrides(tmp_path: Path) -> None:
    """UT-4 & IT-3: Left-to-right fold, unknown-year footprints, Tier 1 sources, #aux_1 modes, and sync CLI."""
    assert normalize_standing_override_year(2035) == MAX_STANDING_OVERRIDE_YEAR
    assert normalize_standing_override_year(2036) == 0
    assert parse_override_target({"hcad_num": "621100000014#aux_1"}) == (
        "0621100000014#aux_1",
        "0621100000014",
        True,
    )

    rect_primary = validate_and_normalize_override_polygon(_make_rect_ring(-95.392, 29.791, 12.0, 10.0))["geometry"]
    rect_aux = validate_and_normalize_override_polygon(_make_rect_ring(-95.3922, 29.7912, 6.0, 6.0))["geometry"]
    rect_unknown = validate_and_normalize_override_polygon(_make_rect_ring(-95.395, 29.795, 11.0, 9.0))["geometry"]

    csv_buf = io.StringIO()
    writer = csv.DictWriter(csv_buf, fieldnames=APPROVED_EDITS_HEADERS)
    writer.writeheader()
    writer.writerows(
        [
            # Row 1: Primary parcel geometry trace with Tier 1 deed date
            {
                "status": "Approved",
                "hcad_num": "0621100000014",
                "address": "1127 KEY ST",
                "year_built": "1924",
                "original_hcad_year": "1930",
                "historic_district": "Norhill",
                "contributing": "Contributing",
                "source_type": "Harris County Clerk Historic Deed",
                "source_citation": encode_citation_with_geometry("Deed Vol 512 p 44", rect_primary),
                "source_url": "https://www.cclerk.hctx.net/Applications/WebSearch/RP.aspx",
                "verified_by": "Staff Historian",
                "building_name": "",
                "alt_names": "",
                "bld_style": "Craftsman Bungalow",
                "architect": "",
                "geometry_geojson": json.dumps(rect_primary, separators=(",", ":")),
            },
            # Row 2: Subsequent metadata-only edit -> must preserve geometry
            {
                "status": "Approved",
                "hcad_num": "0621100000014",
                "address": "1127 KEY ST",
                "year_built": "1925",
                "original_hcad_year": "1930",
                "historic_district": "Norhill",
                "contributing": "Contributing",
                "source_type": "Houston City Directory",
                "source_citation": "1925 Houston City Directory p. 812",
                "source_url": "",
                "verified_by": "Staff Historian",
                "building_name": "",
                "alt_names": "",
                "bld_style": "",
                "architect": "",
                "geometry_geojson": "",
            },
            # Row 3: Subsequent geometry-only polish with fallback Satellite source -> keeps 1925 + City Dir
            {
                "status": "Approved",
                "hcad_num": "0621100000014",
                "address": "1127 KEY ST",
                "year_built": "",
                "original_hcad_year": "1930",
                "historic_district": "Norhill",
                "contributing": "Contributing",
                "source_type": "Satellite / Aerial Imagery Verification",
                "source_citation": encode_citation_with_geometry("Roofline polish", rect_primary),
                "source_url": "",
                "verified_by": "GIS Specialist",
                "building_name": "",
                "alt_names": "",
                "bld_style": "",
                "architect": "",
                "geometry_geojson": json.dumps(rect_primary, separators=(",", ":")),
            },
            # Row 4: Additive secondary building #aux_1 via embedded citation fallback only (empty Col P)
            {
                "status": "Approved",
                "hcad_num": "0621100000014#aux_1",
                "address": "1127 KEY ST REAR GARAGE Apt",
                "year_built": "1928",
                "original_hcad_year": "1930",
                "historic_district": "Norhill",
                "contributing": "Contributing",
                "source_type": "Sanborn Fire Insurance Map",
                "source_citation": encode_citation_with_geometry("1924-1950 Sanborn Vol 7", rect_aux),
                "source_url": "",
                "verified_by": "Staff Historian",
                "building_name": "",
                "alt_names": "",
                "bld_style": "",
                "architect": "",
                "geometry_geojson": "",
            },
            # Row 5: Unknown-year standing footprint edit (blank year_built, non-vacant)
            {
                "status": "Approved",
                "hcad_num": "0451230000099",
                "address": "500 MAIN ST",
                "year_built": "",
                "original_hcad_year": "0",
                "historic_district": "Outside Historic District",
                "contributing": "Outside Historic District",
                "source_type": "Satellite / Aerial Imagery",
                "source_citation": encode_citation_with_geometry("Standing commercial Annex", rect_unknown),
                "source_url": "",
                "verified_by": "GIS Specialist",
                "building_name": "",
                "alt_names": "",
                "bld_style": "",
                "architect": "",
                "geometry_geojson": json.dumps(rect_unknown, separators=(",", ":")),
            },
            # Row 6: Field survey row accidentally copied into Approved_Edits -> MUST be skipped
            {
                "status": "Approved",
                "hcad_num": "0621100000015",
                "address": "1129 KEY ST",
                "year_built": "",
                "original_hcad_year": "1926",
                "historic_district": "Norhill",
                "contributing": "Contributing",
                "source_type": FIELD_SURVEY_SOURCE_TYPE,
                "source_citation": encode_citation_with_field_survey(
                    "Walk survey", {"survey_id": "surv_leak", "integrity_grade": "Intact"}
                ),
                "source_url": "",
                "verified_by": "Volunteer",
                "building_name": "",
                "alt_names": "",
                "bld_style": "",
                "architect": "",
                "geometry_geojson": "",
            },
        ]
    )
    csv_text = csv_buf.getvalue()
    folded = fold_approved_csv_overrides(csv_text)

    primary = folded["0621100000014"]
    assert primary["year_built"] == 1925
    assert primary["decade"] == 1920
    assert primary["source_type"] == "Houston City Directory"
    assert primary["geometry"] == rect_primary
    assert "[GEOMETRY_GEOJSON:" not in primary["source_citation"]
    assert classify_override_footprint_mode("0621100000014", primary) == OVERRIDE_MODE_PARCEL_REPLACEMENT

    aux = folded["0621100000014#aux_1"]
    assert aux["is_building_override"] is True
    assert aux["keep_shard_footprints"] is False
    assert aux["replace_parcel_shards"] is False
    assert aux["geometry"] == rect_aux
    assert classify_override_footprint_mode("0621100000014#aux_1", aux) == OVERRIDE_MODE_ADDITIVE_POLYGON

    unk = folded["0451230000099"]
    assert unk["suppress_only"] is False
    assert unk.get("year_built") in (None, 0) or "year_built" not in unk
    assert unk["geometry"] == rect_unknown
    assert classify_override_footprint_mode("0451230000099", unk) == OVERRIDE_MODE_PARCEL_REPLACEMENT

    assert "0621100000015" not in folded

    # Verify shard-primary campus override mode and vacant-suppression mode
    campus_ov = merge_override_record(
        None,
        {
            "override_key": "0410540010001#lovett_hall",
            "hcad_num": "0410540010001",
            "is_building_override": True,
            "year_built": 1912,
            "suppress_only": False,
        },
        "0410540010001#lovett_hall",
    )
    assert campus_ov["keep_shard_footprints"] is True
    assert classify_override_footprint_mode("0410540010001#lovett_hall", campus_ov) == OVERRIDE_MODE_SHARD_PRIMARY

    vac_ov = merge_override_record(
        primary,
        {"override_key": "0621100000014", "hcad_num": "0621100000014", "suppress_only": True},
        "0621100000014",
    )
    assert vac_ov["suppress_only"] is True
    assert "geometry" not in vac_ov
    assert classify_override_footprint_mode("0621100000014", vac_ov) == OVERRIDE_MODE_SUPPRESS_VACANT

    # Verify sync_curated_overrides end-to-end against temp data_dir
    csv_file = tmp_path / "approved.csv"
    csv_file.write_text(csv_text, encoding="utf-8")
    (tmp_path / "curated_overrides.json").write_text(
        json.dumps({"version": 1, "overrides": {}}), encoding="utf-8"
    )
    (tmp_path / "buildings.geojson").write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {
                            "id": "b_1127",
                            "hcad_num": "0621100000014",
                            "address": "1127 KEY ST",
                            "year_built": 1930,
                            "historic_district": "Norhill",
                        },
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [_make_rect_ring(-95.392, 29.791, 10.0, 10.0)],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    sync_curated_overrides(tmp_path, local_csv_path=csv_file)
    written_doc = json.loads((tmp_path / "curated_overrides.json").read_text(encoding="utf-8"))
    assert "0621100000014#aux_1" in written_doc["overrides"]
    assert written_doc["overrides"]["0621100000014"]["geometry"] == rect_primary
    assert "0621100000015" not in written_doc["overrides"]


def test_ut6_audit_queue_builder_and_ondisk_artifact() -> None:
    """UT-6: Verify `build_historical_audit_queue_payload` and `historical_audit_queue.json`."""
    queue_path = DATA_DIR / "historical_audit_queue.json"
    summary_path = DATA_DIR / "historical_audit_summary.json"
    raw = queue_path.read_text(encoding="utf-8")
    assert len(raw.encode("utf-8")) < 1024 * 1024
    assert "\n" not in raw.strip()
    assert "\u2014" not in raw
    assert "\u2013" not in raw

    q_doc = json.loads(raw)
    s_doc = json.loads(summary_path.read_text(encoding="utf-8"))
    assert q_doc["total"] == 2600
    assert len(q_doc["items"]) == 2600
    assert q_doc["categories"] == s_doc["contradictions_by_category"]
    assert q_doc["categories"] == {
        "CITY_DIRECTORY_EARLIER_THAN_HCAD": 1722,
        "CITY_DIRECTORY_ABSENT_THROUGH_1926": 782,
        "LANDMARK_VS_ATLAS_YEAR_MISMATCH": 51,
        "ADDRESS_STREET_MISMATCH": 40,
        "SUSPECTED_UNMARKED_LANDMARK_DEMOLITION": 3,
        "ARCHITECT_CAREER_WINDOW_CHECK": 2,
    }

    min_lng, min_lat, max_lng, max_lat = HARRIS_COUNTY_BBOX
    seen_fids: set[str] = set()
    for item in q_doc["items"]:
        fid = item["fid"]
        assert fid and fid not in seen_fids
        seen_fids.add(fid)
        ll = item["ll"]
        assert isinstance(ll, list) and len(ll) == 2
        assert min_lng <= ll[0] <= max_lng
        assert min_lat <= ll[1] <= max_lat
        if "cdm17006.contentdm.oclc.org/digital/collection/citydir/id/" in item.get("u", ""):
            page_id = item["u"].rsplit("/id/", 1)[-1].split("/", 1)[0]
            assert page_id.isdigit(), f"Non-integer ContentDM page id in {item['u']}"

    from atlas_pipeline.audit_historical_accuracy import resolve_citydir_cdm_url

    ptr_ok, cdm_ok = resolve_citydir_cdm_url(
        {"e": 1912, "p": 4412, "c": "1506 Heights Blvd - J. Smith"},
        "https://cdm17006.contentdm.oclc.org/digital/collection/citydir/search/searchterm/1506%20HEIGHTS",
    )
    assert ptr_ok == 4412
    assert cdm_ok == "https://cdm17006.contentdm.oclc.org/digital/collection/citydir/id/4412/rec/1"
    ptr_none, cdm_fb = resolve_citydir_cdm_url(
        {"e": 1912, "p": "OCR text not integer", "c": "1506 Heights Blvd"},
        "https://cdm17006.contentdm.oclc.org/digital/collection/citydir/search/searchterm/1506%20HEIGHTS",
    )
    assert ptr_none == 0
    assert "/search/searchterm/" in cdm_fb

    dup_finding = {
        "finding_id": "DUP_FID_1",
        "category": "CITY_DIRECTORY_EARLIER_THAN_HCAD",
        "priority_score": 85,
        "hcad_num": "0621100000014",
        "building_id": "0621100000014",
        "address": "1127 KEY ST",
        "district_or_context": "Norhill",
        "contributing": "Contributing",
        "atlas_year": 1930,
        "hcad_year": 1930,
        "suggested_year": 1924,
        "lng": -95.392,
        "lat": 29.791,
        "suggested_source_type": "Houston City Directory",
        "conflicting_evidence": "Directory lists resident in 1924 vs HCAD 1930",
        "recommended_action": "Accept 1924",
    }
    with pytest.raises(ValueError, match="Duplicate"):
        build_historical_audit_queue_payload([dup_finding, dict(dup_finding)])

    bad_cdm_url_finding = dict(dup_finding)
    bad_cdm_url_finding["finding_id"] = "BAD_CDM_URL_1"
    bad_cdm_url_finding["primary_source_url"] = (
        "https://cdm17006.contentdm.oclc.org/digital/collection/citydir/id/1127 KEY ST OCR/rec/1"
    )
    with pytest.raises(ValueError, match="ContentDM"):
        build_historical_audit_queue_payload([bad_cdm_url_finding])


def test_ut9_field_survey_recovery_dedup_and_cell_sanitization() -> None:
    """UT-9: Recover 18-col field surveys from Pending_Submissions CSV with retry dedup & formula guards."""
    env1 = {
        "survey_id": "surv_0621100000014_20261030",
        "surveyed_at": "2026-10-30 14:00:00",
        "review_status": "Unreviewed",
        "hcad_num": "621100000014",  # unpadded 12-digit simulating Sheets numeric strip
        "address": "1127 KEY ST",
        "district_or_corridor": "Norhill",
        "integrity_grade": "Intact",
        "condition_rating": "Good",
        "suggested_contributing": "Contributing",
        "windows_status": "Original Wood",
        "cladding_material": "Wood Siding",
        "porch_integrity": "Original Open Porch",
        "atlas_year_built": 1930,
        "observed_year_or_demo_note": "c. 1924",
        "photo_url": "0621100000014_1127_KEY_ST_20261030.jpg",
        "field_notes": "=SUM(1,2) Original Craftsman porch intact",
        "surveyor_name": "Jane Blockwalker",
        "gps_lat_lng_acc": "29.791200, -95.392100 (+-6m)",
    }
    assert extract_embedded_field_survey_json(encode_citation_with_field_survey("Walk", env1))["survey_id"] == env1["survey_id"]
    assert strip_embedded_field_survey_tag(encode_citation_with_field_survey("Walk note", env1)) == "Walk note"

    env1_retry = dict(env1)
    env1_retry["field_notes"] = "Updated note after retry: Original Craftsman porch intact"

    env_gps = {
        "survey_id": "surv_GPS_297915_953925_20261030",
        "surveyed_at": "2026-10-30 14:05:00",
        "review_status": "Unreviewed",
        "hcad_num": "GPS_297915_-953925",
        "address": "Unmapped Rear Alley Garage",
        "district_or_corridor": "Norhill",
        "integrity_grade": "Minor Alterations",
        "condition_rating": "Fair",
        "suggested_contributing": "Non-Contributing",
        "windows_status": "Vinyl Replacement",
        "cladding_material": "Wood Siding",
        "porch_integrity": "N/A",
        "atlas_year_built": "",
        "observed_year_or_demo_note": "1935",
        "photo_url": "",
        "field_notes": "=CMD() Rear garage apartment visible from alley",
        "surveyor_name": "Jane Blockwalker",
        "gps_lat_lng_acc": "29.791500, -95.392500 (+-8m)",
    }

    pending_headers = [
        "status",
        "hcad_num",
        "address",
        "historic_district",
        "contributing",
        "source_type",
        "source_citation",
        "source_url",
        "submitted_by",
        "submitted_at",
    ]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=pending_headers)
    w.writeheader()
    w.writerows(
        [
            {
                "status": "Pending",
                "hcad_num": "621100000014",
                "address": "1127 KEY ST",
                "source_type": FIELD_SURVEY_SOURCE_TYPE,
                "source_citation": encode_citation_with_field_survey("Field survey", env1),
                "submitted_by": "Jane Blockwalker",
                "submitted_at": "2026-10-30 14:00:01",
            },
            # Duplicate retry row for same survey_id -> updates prior entry cleanly without double row
            {
                "status": "Pending",
                "hcad_num": "0621100000014",
                "address": "1127 KEY ST",
                "source_type": FIELD_SURVEY_SOURCE_TYPE,
                "source_citation": encode_citation_with_field_survey("Field survey", env1_retry),
                "submitted_by": "Jane Blockwalker",
                "submitted_at": "2026-10-30 14:00:15",
            },
            # Unmatched GPS fallback building
            {
                "status": "Pending",
                "hcad_num": "GPS_297915_-953925",
                "address": "Unmapped Rear Alley Garage",
                "source_type": FIELD_SURVEY_SOURCE_TYPE,
                "source_citation": encode_citation_with_field_survey("Field survey", env_gps),
                "submitted_by": "Jane Blockwalker",
                "submitted_at": "2026-10-30 14:05:02",
            },
            # Normal archival date submission -> ignored by field survey recovery
            {
                "status": "Pending",
                "hcad_num": "0621100000099",
                "address": "1200 KEY ST",
                "source_type": "Houston City Directory",
                "source_citation": "1926 Directory p. 412",
                "submitted_by": "Historian",
                "submitted_at": "2026-10-30 14:10:00",
            },
        ]
    )
    recovered = recover_field_survey_rows_from_pending_csv(buf.getvalue())
    assert len(recovered) == 2
    assert len(recovered[0]) == 18
    assert recovered[0][0] == "surv_0621100000014_20261030"
    assert recovered[0][3] == "0621100000014"  # Col D zero-padded to 13 digits!
    assert recovered[0][4] == "1127 KEY ST"
    assert "Updated note after retry" in recovered[0][15]
    assert "[FIELD_SURVEY_JSON:" not in recovered[0][15]
    assert recovered[1][3] == "GPS_297915_-953925"
    assert recovered[1][15].startswith("'=CMD()")

    # Spreadsheet formula injection sanitization vs negative decimal coordinates
    for prefix in ("=CMD()", "+SUM(A1)", "-1+2", "@HYPERLINK", "\t=1", "\r=1"):
        sanitized = sanitize_sheet_cell(prefix)
        assert sanitized.startswith("'"), f"Failed to neutralize {prefix!r}: got {sanitized!r}"
    assert sanitize_sheet_cell("-29.760400, -95.363500") == "-29.760400, -95.363500"


def test_ut11_and_it5_twelve_tab_sheets_contract_and_apps_script_guards() -> None:
    """UT-11 & IT-5: Cross-check all 12-tab coordinates, formulas, enums, pre-flight guards, and Apps Script."""
    assert SID_SURVEYS == 913405001
    assert SID_LOST == LOST_SHEET_ID == 824622001
    assert SHEET_TITLE_SURVEYS == "Field_Surveys"
    assert SHEET_TITLE_LOST == "Lost_Houston_Catalog"
    assert HUB_SURVEYS_ROW == 19
    assert HUB_FILTER_INPUT_ROW == 22
    assert HUB_RESULTS_HEADER_ROW == 24
    assert HUB_RESULTS_FORMULA_ROW == 25
    assert SOP14_TARGET_ROW == 25
    assert SOP15_TARGET_ROW == 26
    assert PENDING_TOTAL_COLS == 23
    assert APPROVED_TOTAL_COLS == 16
    assert APPROVED_EDITS_HEADERS[-1] == "geometry_geojson"
    assert len(HEADERS_A_TO_R) == 18
    assert len(ALL_SURVEY_HEADERS) == 20
    assert ALL_SURVEY_HEADERS[18:] == ["open_in_atlas", "inspect_hcad"]
    assert SURVEY_FILTER_VIEW_COUNT == 6

    # Verify Admin Hub builder coordinates match Hub constants across Lost Houston + Field Surveys
    hub_rows = build_admin_hub_rows()
    assert str(SID_LOST) in hub_rows[HUB_SURVEYS_ROW - 2][4]  # Row 18 Col E: Lost_Houston_Catalog link
    assert "Lost Houston" in hub_rows[HUB_SURVEYS_ROW - 2][0]
    assert str(SID_SURVEYS) in hub_rows[HUB_SURVEYS_ROW - 1][4]  # Row 19 Col E: Field_Surveys link
    assert "Walk-the-Block Field Surveys" in hub_rows[HUB_SURVEYS_ROW - 1][0]
    assert hub_rows[HUB_FILTER_INPUT_ROW - 3][0].startswith("HOW TO USE:")  # Row 20
    assert "Filter by Triage Category" in hub_rows[HUB_FILTER_INPUT_ROW - 2][0]  # Row 21 labels
    assert hub_rows[HUB_RESULTS_HEADER_ROW - 1][0] == "Jump to Edit Row in Sheet"  # Row 24
    assert len(hub_rows[HUB_RESULTS_HEADER_ROW - 1]) == 12
    assert "Pending_Submissions!A2:A" in hub_rows[HUB_RESULTS_FORMULA_ROW - 1][0]  # Row 25

    # Verify baseline Instructions rows safeguard Row 5 (Apps Script) and Row 6 (B6 Passkey)
    instr_rows = build_instructions_rows()
    assert "Google Apps Script Code" in instr_rows[4][0]
    assert instr_rows[4][1] == APPS_SCRIPT_CODE
    assert "Passkey" in instr_rows[5][0] and "B6" in instr_rows[5][0]

    # Verify Approved_Edits filter formula mirrors Col W into Col P cleanly (16 cols)
    assert "Pending_Submissions!W2:W" in APPROVED_EDITS_FILTER_FORMULA
    assert "📋 Field Survey Observation" in PENDING_TRIAGE_FORMULA
    assert "📋 Field Survey Observation" in HUB_TRIAGE_DROPDOWN_VALUES

    # Verify canonical enum parity across Python schema + Field_Surveys sheet builders
    sop15 = build_sop15_row()
    for grade in FIELD_SURVEY_INTEGRITY_GRADES:
        assert grade in sop15[1], f"Missing integrity grade {grade!r} in SOP 15 instructions"
    assert tuple(SURVEY_CONTRIBUTING_VALUES) == (
        "Contributing",
        "Non-Contributing",
        "Potentially Contributing",
        "Vacant / Demolished",
        "Outside Historic District",
    )
    assert "Promoted to Override" in SURVEY_REVIEW_STATUSES

    # Verify Milestone 3 surgical batch builder NEVER overwrites Cell B6 or Pending_Submissions S:V
    batches = build_milestone3_sheet_batches(
        [
            [
                "surv_1",
                "2026-10-30 15:00:00",
                "Unreviewed",
                "0621100000014",
                "1127 KEY ST",
                "Norhill",
                "Intact",
                "Good",
                "Contributing",
                "Original Wood",
                "Wood Siding",
                "Original Open Porch",
                1930,
                "1924",
                "",
                "Clean porch",
                "Jane",
                "29.791200, -95.392100 (+-6m)",
            ]
        ],
        include_add_sheet=True,
        include_hub_insert=True,
        add_filter_views=True,
    )
    touched_ranges = [
        entry["range"] for entry in (batches["raw_values_batch"] + batches["formula_values_batch"])
    ]
    for rng in touched_ranges:
        assert "B6" not in rng, f"Danger: batch touches Admin Passkey cell B6 via {rng}"
        for sv_col in ("!S2", "!T2", "!U2", "!V2"):
            assert not (rng.startswith("Pending_Submissions") and sv_col in rng)
    style_reqs = batches["style_raw_batch"]["requests"]
    assert sum(1 for r in style_reqs if "addFilterView" in r) == SURVEY_FILTER_VIEW_COUNT
    create_reqs = batches["create_raw_batch"]["requests"]
    assert any("addSheet" in r for r in create_reqs)
    assert any("insertDimension" in r for r in create_reqs)

    # Positive pre-flight check (both pre-upgrade 11-tab and post-upgrade 12-tab layouts)
    tab_map_11 = {
        SID_HUB: "Admin_Hub_&_Quick_Filter",
        SID_PENDING: "Pending_Submissions",
        SID_APPROVED: "Approved_Edits",
        SID_INSTR: "Instructions_&_Apps_Script",
        SID_LOST: "Lost_Houston_Catalog",
    }
    sop14_col_a = "23. SOP 14 - Lost Houston Demolished Buildings Catalog & Archival Photo Staging"
    base_instr_a = [r[0] for r in instr_rows]
    while len(base_instr_a) < 24:
        base_instr_a.append("")
    pre_instr_a = base_instr_a[:24] + [sop14_col_a]
    post_instr_a = pre_instr_a + [sop15[0]]

    # Pre-upgrade Hub has Lost Houston at Row 18 and HOW TO USE at Row 19 (before Field_Surveys insert)
    pre_hub_a = [r[0] for r in hub_rows[:18]] + [r[0] for r in hub_rows[19:]]
    state_pre = verify_live_sheet_anchors(
        tab_id_to_title=tab_map_11, hub_col_a=pre_hub_a, instr_col_a=pre_instr_a
    )
    assert state_pre == {"need_add_sheet": True, "need_hub_insert": True}

    tab_map_12 = dict(tab_map_11)
    tab_map_12[SID_SURVEYS] = SHEET_TITLE_SURVEYS
    post_hub_a = [r[0] for r in hub_rows]
    state_post = verify_live_sheet_anchors(
        tab_id_to_title=tab_map_12, hub_col_a=post_hub_a, instr_col_a=post_instr_a
    )
    assert state_post == {"need_add_sheet": False, "need_hub_insert": False}

    # Negative pre-flight checks: abort on drifted rows before writing
    bad_hub = list(post_hub_a)
    bad_hub[17] = "Unexpected Row 18 Content"
    with pytest.raises(ValueError, match="Row 18"):
        verify_live_sheet_anchors(tab_id_to_title=tab_map_12, hub_col_a=bad_hub, instr_col_a=post_instr_a)

    bad_instr = list(post_instr_a)
    bad_instr[24] = "Wrong Row 25"
    with pytest.raises(ValueError, match="Row 25"):
        verify_live_sheet_anchors(tab_id_to_title=tab_map_12, hub_col_a=post_hub_a, instr_col_a=bad_instr)

    bad_b6 = list(post_instr_a)
    bad_b6[5] = "Something else"
    with pytest.raises(ValueError, match="Row 6"):
        verify_live_sheet_anchors(tab_id_to_title=tab_map_12, hub_col_a=post_hub_a, instr_col_a=bad_b6)

    # Verify APPS_SCRIPT_CODE safeguards (DF-4, DF-5, Defect #12 upsert protection, `/` preservation)
    assert "getSheetByName('Field_Surveys')" in APPS_SCRIPT_CODE
    assert "payload.action === 'field_survey'" in APPS_SCRIPT_CODE
    assert "payload.geometry_geojson" in APPS_SCRIPT_CODE
    assert "existingReview !== 'Unreviewed'" in APPS_SCRIPT_CODE
    assert "rawBldName.split(/[;|]/)" in APPS_SCRIPT_CODE
    assert "split(/\\s*[;|\\/]\\s*/)" not in APPS_SCRIPT_CODE


@pytest.mark.skipif(shutil.which("node") is None, reason="node binary required for JS<->Python parity")
def test_it1_it2_it3_cross_language_js_python_parity() -> None:
    """IT-1, IT-2, IT-3: Live Node subprocess parity against Python geometry, CSV fold, and Field Survey codecs."""
    rect_primary = validate_and_normalize_override_polygon(_make_rect_ring(-95.392, 29.791, 12.0, 10.0))["geometry"]
    rect_aux = validate_and_normalize_override_polygon(_make_rect_ring(-95.3922, 29.7912, 6.0, 6.0))["geometry"]
    cw_ring = list(reversed(_make_rect_ring(-95.391, 29.790, 9.0, 7.0)))
    asym_bowtie = [
        [-95.3910, 29.7900],
        [-95.3900, 29.7910],
        [-95.3908, 29.7910],
        [-95.3900, 29.7900],
        [-95.3910, 29.7900],
    ]
    austin = _make_rect_ring(-97.7431, 30.2672, 12.0, 12.0)

    csv_buf = io.StringIO()
    writer = csv.DictWriter(csv_buf, fieldnames=APPROVED_EDITS_HEADERS)
    writer.writeheader()
    writer.writerows(
        [
            {
                "status": "Approved",
                "hcad_num": "0621100000014",
                "address": "1127 KEY ST",
                "year_built": "1924",
                "original_hcad_year": "1930",
                "historic_district": "Norhill",
                "contributing": "Contributing",
                "source_type": "Harris County Clerk Historic Deed",
                "source_citation": encode_citation_with_geometry("Vol 512 p 44", rect_primary),
                "source_url": "",
                "verified_by": "Historian",
                "building_name": "",
                "alt_names": "",
                "bld_style": "",
                "architect": "",
                "geometry_geojson": json.dumps(rect_primary, separators=(",", ":")),
            },
            {
                "status": "Approved",
                "hcad_num": "0621100000014",
                "address": "1127 KEY ST",
                "year_built": "",
                "original_hcad_year": "1930",
                "historic_district": "Norhill",
                "contributing": "Contributing",
                "source_type": "Satellite / Aerial Imagery Verification",
                "source_citation": encode_citation_with_geometry("Roof corner adjustment", rect_primary),
                "source_url": "",
                "verified_by": "GIS Staff",
                "building_name": "",
                "alt_names": "",
                "bld_style": "",
                "architect": "",
                "geometry_geojson": json.dumps(rect_primary, separators=(",", ":")),
            },
            {
                "status": "Approved",
                "hcad_num": "0621100000014#aux_1",
                "address": "1127 KEY ST GARAGE",
                "year_built": "1928",
                "original_hcad_year": "1930",
                "historic_district": "Norhill",
                "contributing": "Contributing",
                "source_type": "Sanborn Fire Insurance Map",
                "source_citation": encode_citation_with_geometry("Sanborn Vol 7", rect_aux),
                "source_url": "",
                "verified_by": "Historian",
                "building_name": "",
                "alt_names": "",
                "bld_style": "",
                "architect": "",
                "geometry_geojson": "",
            },
        ]
    )
    shared_csv = csv_buf.getvalue()

    fixtures = {
        "polygons": [rect_primary, cw_ring, asym_bowtie, austin],
        "rotBase": rect_primary,
        "rotAngle": -34.65,
        "csvText": shared_csv,
    }

    js_script = """
import fs from "node:fs";
import {
  validateOverridePolygonGeometry,
  parseCsvToObjects,
  parseOverridesFromSheetRows,
  classifyOverrideFootprintMode,
  buildFieldSurveyWebhookRecord,
  MAX_STANDING_OVERRIDE_YEAR,
  FIELD_SURVEY_INTEGRITY_GRADES,
  FIELD_SURVEY_CONDITION_RATINGS,
  FIELD_SURVEY_CSV_COLUMNS,
} from "./app/js/curatedEdits.js";
import { rotatePolygonTangentPlane } from "./app/js/staffWorkbench.js";

const input = JSON.parse(fs.readFileSync(0, "utf8"));
const polyResults = input.polygons.map((g) => {
  const r = validateOverridePolygonGeometry(g);
  return { ok: r.ok, geometry: r.geometry, area_sq_m: r.area_sq_m, vertex_count: r.vertex_count };
});
const rotGeom = rotatePolygonTangentPlane(input.rotBase, input.rotAngle);
const folded = parseOverridesFromSheetRows(parseCsvToObjects(input.csvText));
const modes = {};
for (const [k, v] of Object.entries(folded)) {
  modes[k] = classifyOverrideFootprintMode(k, v);
}
const { webhookPayload } = buildFieldSurveyWebhookRecord({
  survey_id: "surv_parity_0621100000014",
  surveyed_at: "2026-10-30 16:30:00",
  surveyor_name: "Parity Tester",
  hcad_num: "0621100000014",
  address: "1127 KEY ST",
  district_or_corridor: "Norhill",
  integrity_grade: "Intact",
  condition_rating: "Good",
  suggested_contributing: "Contributing",
  windows_status: "Original Wood",
  cladding_material: "Wood Siding",
  porch_integrity: "Original Open Porch",
  atlas_year_built: 1930,
  observed_year_or_demo_note: "1924",
  photo_url: "0621100000014_1127_KEY_ST_20261030.jpg",
  gps_lat_lng_acc: "29.791234, -95.392345 (+-5m)",
  field_notes: "Cross-language parity test note",
});
process.stdout.write(JSON.stringify({
  polyResults,
  rotRing: rotGeom.coordinates[0],
  folded,
  modes,
  webhookPayload,
  maxStandingYear: MAX_STANDING_OVERRIDE_YEAR,
  integrityGrades: FIELD_SURVEY_INTEGRITY_GRADES,
  conditionRatings: FIELD_SURVEY_CONDITION_RATINGS,
  surveyCols: FIELD_SURVEY_CSV_COLUMNS,
}));
"""
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", js_script],
        input=json.dumps(fixtures),
        text=True,
        capture_output=True,
        cwd=str(REPO_ROOT),
        check=True,
    )
    js_out: dict[str, Any] = json.loads(proc.stdout)

    # IT-1: Constants, enums, 18-col schema, polygon validator, and metric rotation parity
    assert js_out["maxStandingYear"] == MAX_STANDING_OVERRIDE_YEAR == 2035
    assert tuple(js_out["integrityGrades"]) == FIELD_SURVEY_INTEGRITY_GRADES
    assert tuple(js_out["conditionRatings"]) == FIELD_SURVEY_CONDITION_RATINGS
    assert js_out["surveyCols"] == HEADERS_A_TO_R

    for idx, poly_in in enumerate(fixtures["polygons"]):
        py_r = validate_and_normalize_override_polygon(poly_in)
        js_r = js_out["polyResults"][idx]
        assert js_r["ok"] == py_r["ok"], f"Fixture {idx} ok mismatch: JS={js_r} vs PY={py_r}"
        if py_r["ok"]:
            assert js_r["vertex_count"] == py_r["vertex_count"]
            assert abs(js_r["area_sq_m"] - py_r["area_sq_m"]) < 0.05
            assert js_r["geometry"] == py_r["geometry"]

    py_rot = rotate_polygon_tangent_plane(rect_primary, -34.65)
    assert len(js_out["rotRing"]) == len(py_rot)
    for j_pt, p_pt in zip(js_out["rotRing"], py_rot, strict=True):
        assert abs(j_pt[0] - p_pt[0]) <= 1e-6
        assert abs(j_pt[1] - p_pt[1]) <= 1e-6

    # IT-2: Multi-row Approved_Edits fold convergence between browser JS and Python CLI
    py_folded = fold_approved_csv_overrides(shared_csv)
    assert set(js_out["folded"].keys()) == set(py_folded.keys()) == {"0621100000014", "0621100000014#aux_1"}
    for key in py_folded:
        js_ov = js_out["folded"][key]
        py_ov = py_folded[key]
        assert js_ov["year_built"] == py_ov["year_built"]
        assert js_ov["decade"] == py_ov["decade"]
        assert js_ov["source_type"] == py_ov["source_type"]
        assert js_ov["geometry"] == py_ov["geometry"]
        assert bool(js_ov.get("keep_shard_footprints")) == bool(py_ov.get("keep_shard_footprints"))
        assert js_out["modes"][key] == classify_override_footprint_mode(key, py_ov)

    # IT-3: JS buildFieldSurveyWebhookRecord -> Python recover_field_survey_rows_from_pending_csv
    wh = js_out["webhookPayload"]
    pending_buf = io.StringIO()
    pw = csv.DictWriter(
        pending_buf,
        fieldnames=["status", "hcad_num", "address", "source_type", "source_citation", "submitted_by", "submitted_at"],
    )
    pw.writeheader()
    pw.writerow(
        {
            "status": wh["status"],
            "hcad_num": wh["hcad_num"],
            "address": wh["address"],
            "source_type": wh["source_type"],
            "source_citation": wh["source_citation"],
            "submitted_by": wh["submitter_name"],
            "submitted_at": wh["submitted_at"],
        }
    )
    recovered_rows = recover_field_survey_rows_from_pending_csv(pending_buf.getvalue())
    assert len(recovered_rows) == 1
    row18 = recovered_rows[0]
    assert row18 == [
        "surv_parity_0621100000014",
        "2026-10-30 16:30:00",
        "Unreviewed",
        "0621100000014",
        "1127 KEY ST",
        "Norhill",
        "Intact",
        "Good",
        "Contributing",
        "Original Wood",
        "Wood Siding",
        "Original Open Porch",
        1930,
        "1924",
        "0621100000014_1127_KEY_ST_20261030.jpg",
        "Cross-language parity test note",
        "Parity Tester",
        "29.791234, -95.392345 (+-5m)",
    ]


def test_rp1_rp2_legacy_sheet_compat_and_campus_coexistence() -> None:
    """RP-1 & RP-2: Legacy 15-col Approved_Edits / 22-col Pending_Submissions parse cleanly and coexist with campus/vacant rules."""
    rect_aux = validate_and_normalize_override_polygon(_make_rect_ring(-95.6042, 29.8895, 8.0, 10.0))["geometry"]
    legacy15_headers = APPROVED_EDITS_HEADERS[:15]
    assert "geometry_geojson" not in legacy15_headers

    buf15 = io.StringIO()
    w15 = csv.DictWriter(buf15, fieldnames=legacy15_headers)
    w15.writeheader()
    w15.writerows(
        [
            {
                "status": "Approved",
                "hcad_num": "1223580020001#cy_ridge_hs_main",
                "address": "7900 N ELDRIDGE PKWY",
                "year_built": "2001",
                "original_hcad_year": "2002",
                "historic_district": "Outside Historic District",
                "contributing": "Outside Historic District",
                "source_type": "County Permit",
                "source_citation": "Campus main building completion year",
                "source_url": "",
                "verified_by": "Staff",
                "building_name": "Cypress Ridge High School Main Building",
                "alt_names": "",
                "bld_style": "",
                "architect": "",
            },
            {
                "status": "Approved",
                "hcad_num": "1223580020001#aux_1",
                "address": "7900 N ELDRIDGE PKWY FIELDHOUSE",
                "year_built": "2014",
                "original_hcad_year": "",
                "historic_district": "Outside Historic District",
                "contributing": "Outside Historic District",
                "source_type": "Satellite / Aerial Imagery Verification",
                "source_citation": encode_citation_with_geometry("Legacy embedded geometry", rect_aux),
                "source_url": "",
                "verified_by": "Staff",
                "building_name": "Athletic Fieldhouse",
                "alt_names": "",
                "bld_style": "",
                "architect": "",
            },
            {
                "status": "Approved",
                "hcad_num": "0621100000014",
                "address": "1127 KEY ST",
                "year_built": "0",
                "original_hcad_year": "1928",
                "historic_district": "Norhill",
                "contributing": "Non-Contributing",
                "source_type": "City of Houston Demolition Permit",
                "source_citation": "Building demolished - lot currently vacant",
                "source_url": "",
                "verified_by": "Staff",
                "building_name": "",
                "alt_names": "",
                "bld_style": "",
                "architect": "",
            },
        ]
    )
    folded = fold_approved_csv_overrides(buf15.getvalue())
    campus_main = folded["1223580020001#cy_ridge_hs_main"]
    assert campus_main["keep_shard_footprints"] is True
    assert classify_override_footprint_mode("1223580020001#cy_ridge_hs_main", campus_main) == OVERRIDE_MODE_SHARD_PRIMARY

    campus_aux = folded["1223580020001#aux_1"]
    assert campus_aux["geometry"] == rect_aux
    assert campus_aux["keep_shard_footprints"] is False
    assert campus_aux["replace_parcel_shards"] is False
    assert classify_override_footprint_mode("1223580020001#aux_1", campus_aux) == OVERRIDE_MODE_ADDITIVE_POLYGON

    vacant = folded["0621100000014"]
    assert vacant["suppress_only"] is True
    assert classify_override_footprint_mode("0621100000014", vacant) == OVERRIDE_MODE_SUPPRESS_VACANT

    # Legacy 22-column Pending_Submissions CSV (no Col W) parses cleanly in recover_field_survey_rows_from_pending_csv
    buf22 = io.StringIO()
    w22 = csv.writer(buf22)
    w22.writerow(["status", "hcad_num", "address", "source_type", "source_citation", "submitted_by", "submitted_at"])
    w22.writerow(
        [
            "Pending Review",
            "0610870390001",
            "1506 HEIGHTS BLVD",
            "Houston City Directories (1866-1926)",
            "Normal pending archival tip without survey tag",
            "Neighbor",
            "2026-10-30",
        ]
    )
    assert recover_field_survey_rows_from_pending_csv(buf22.getvalue()) == []


def test_post_review_apps_script_formula_hardening_and_survey_merge_guards(tmp_path):
    """Regression coverage for QA Review findings:
      - Critical #1: APPS_SCRIPT_CODE blocks formula injection via review_status, survey_id,
        surveyed_at, hcad_num, Pending_Submissions rows, and Column W geometry_geojson.
      - High #3: sync_field_surveys_sheet.py merges --existing-surveys-csv safely, blocks
        accidental truncation of Field_Surveys!A2:R, and supports --update-hub-row without
        writing survey body rows.
      - High #4: sync_curated_edits.py parses alt_names & bld_style, preserves Tier 1
        source_type on 'Footprint Issue: ...' geometry edits, and respects explicit
        replace_parcel_shards on primary bare-HCAD keys.
    """
    from atlas_pipeline.schema import FIELD_SURVEY_CONTRIBUTING_VALUES
    from atlas_pipeline.setup_admin_sheet import APPS_SCRIPT_CODE, build_admin_hub_rows
    from atlas_pipeline.sync_curated_edits import merge_override_record, parse_approved_csv_rows
    from atlas_pipeline.sync_field_surveys_sheet import (
        load_existing_field_surveys_csv,
        main as sync_surveys_main,
        merge_field_survey_rows,
    )

    # Critical #1: APPS_SCRIPT_CODE hardening invariants + zero literal double quotes (so Google Sheets
    # single-click cell copy never doubles inner `"` -> `""` and causes V8 SyntaxError or CSV bugs)
    assert '"' not in APPS_SCRIPT_CODE
    assert "surveyRow = [\n        safeSurveyId,\n        sanitizeSheetFormulaPrefix_(surveyAt),\n        'Unreviewed'," in APPS_SCRIPT_CODE
    assert "revCell.getFormula()" in APPS_SCRIPT_CODE
    assert "allowedReviewStatuses[existingReview] === true" in APPS_SCRIPT_CODE
    assert "setValues([[sanitizeSheetFormulaPrefix_(geomGeoJsonStr)]])" in APPS_SCRIPT_CODE
    assert "sanitizeSheetFormulaPrefix_(citationStr)" in APPS_SCRIPT_CODE
    assert "payload.review_status" not in APPS_SCRIPT_CODE

    apps_js_file = tmp_path / "Code.gs.js"
    apps_js_file.write_text(APPS_SCRIPT_CODE, encoding="utf-8")
    node_bin = shutil.which("node") or "node"
    syntax_res = subprocess.run([node_bin, "--check", str(apps_js_file)], capture_output=True, text=True, check=False)
    assert syntax_res.returncode == 0, syntax_res.stderr
    runtime_probe = subprocess.run(
        [
            node_bin,
            "-e",
            APPS_SCRIPT_CODE
            + "\nconst assert = require('assert');"
            + "\nassert.strictEqual(sanitizeSheetFormulaPrefix_('=B6'), \"'=B6\");"
            + "\nassert.strictEqual(sanitizeSheetFormulaPrefix_(\"'''=B6\"), \"'=B6\");"
            + "\nconst obj = extractBalancedTagJson_('note [FIELD_SURVEY_JSON:{\"survey_id\":\"S1\",\"address\":\"100 \\\\\"A\\\\\" St\"}]', '[FIELD_SURVEY_JSON:');"
            + "\nassert.strictEqual(obj.survey_id, 'S1');"
            + "\nassert.strictEqual(obj.address, '100 \"A\" St');",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert runtime_probe.returncode == 0, runtime_probe.stderr

    # Medium #11: Hub rows contain no truncated docs.google.com/document/d/1 link
    hub_dump = json.dumps(build_admin_hub_rows())
    assert "document/d/1\"" not in hub_dump
    assert "docs/10-sources-integrations-and-curation-workflows.md#62-" in hub_dump

    # Medium #12: Hoisted FIELD_SURVEY_CONTRIBUTING_VALUES
    assert "Potentially Contributing" in FIELD_SURVEY_CONTRIBUTING_VALUES
    assert len(FIELD_SURVEY_CONTRIBUTING_VALUES) == 5

    # High #3: --existing-surveys-csv merge + curator review_status preservation + --update-hub-row guard
    existing_csv = tmp_path / "Field_Surveys.csv"
    existing_csv.write_text(
        "survey_id,surveyed_at,review_status,hcad_num,address,district_or_corridor,"
        "integrity_grade,condition_rating,suggested_contributing,windows_status,"
        "cladding_material,porch_integrity,atlas_year_built,observed_year_or_demo_note,"
        "photo_url,field_notes,surveyor_name,gps_lat_lng_acc\n"
        "SURV_LIVE_1,2026-10-30 10:00:00,Verified,0610870380001,1502 HEIGHTS BLVD,"
        "Houston Heights East,Intact,Good,Contributing,,,,1912,,,Live webhook row,DM,\"29.798100, -95.397600\"\n"
        "SURV_LIVE_2,2026-10-30 10:05:00,Unreviewed,0610870390001,1506 HEIGHTS BLVD,"
        "Houston Heights East,Minor Alterations,Fair,Contributing,,,,1920,,,Second live row,DM,\"29.798300, -95.397600\"\n",
        encoding="utf-8",
    )
    pending_csv = tmp_path / "Pending_Submissions.csv"
    env_retry = json.dumps(
        {
            "survey_id": "SURV_LIVE_1",
            "surveyed_at": "2026-10-30 10:00:00",
            "review_status": "Unreviewed",
            "hcad_num": "0610870380001",
            "address": "1502 HEIGHTS BLVD",
            "integrity_grade": "Intact",
            "surveyor_name": "DM",
        },
        separators=(",", ":"),
    )
    env_new = json.dumps(
        {
            "survey_id": "SURV_BACKFILL_3",
            "surveyed_at": "2026-10-30 10:12:00",
            "review_status": "=Instructions_&_Apps_Script!B6",
            "hcad_num": "0621100000014",
            "address": "1127 KEY ST",
            "integrity_grade": "Intact",
            "surveyor_name": "DM",
        },
        separators=(",", ":"),
    )
    buf_p = io.StringIO()
    wp = csv.writer(buf_p)
    wp.writerow(["status", "hcad_num", "address", "source_type", "source_citation", "submitted_by", "submitted_at"])
    wp.writerow(["Pending", "0610870380001", "1502 HEIGHTS BLVD", "FIELD_SURVEY_OBSERVATION", f"[FIELD_SURVEY_JSON:{env_retry}]", "DM", "2026-10-30"])
    wp.writerow(["Pending", "0621100000014", "1127 KEY ST", "FIELD_SURVEY_OBSERVATION", f"[FIELD_SURVEY_JSON:{env_new}]", "DM", "2026-10-30"])
    pending_csv.write_text(buf_p.getvalue(), encoding="utf-8")

    # Rejects unmerged --ingest-pending-csv without --existing-surveys-csv
    with pytest.raises(SystemExit):
        sync_surveys_main(["--ingest-pending-csv", str(pending_csv), "--output-dir", str(tmp_path / "bad")])

    # Merged run keeps SURV_LIVE_1 (preserving 'Verified'!), SURV_LIVE_2, and appends formula-neutralized SURV_BACKFILL_3
    out_merged = tmp_path / "merged"
    sync_surveys_main(
        [
            "--existing-surveys-csv",
            str(existing_csv),
            "--ingest-pending-csv",
            str(pending_csv),
            "--update-hub-row",
            "--output-dir",
            str(out_merged),
        ]
    )
    raw_ops = json.loads((out_merged / "field_surveys_raw_values_batch.json").read_text(encoding="utf-8"))
    survey_write = next(op for op in raw_ops if op["range"].startswith("Field_Surveys!"))
    assert survey_write["range"] == "Field_Surveys!A1:R4"
    body_rows = survey_write["data"][1:]
    assert [r[0] for r in body_rows] == ["SURV_LIVE_1", "SURV_LIVE_2", "SURV_BACKFILL_3"]
    assert body_rows[0][2] == "Verified"
    assert body_rows[2][2].startswith("'=")

    # Hub-only update touches only header row A1:R1 (never overwrites A2:R)
    out_hub = tmp_path / "hub_only"
    sync_surveys_main(["--update-hub-row", "--output-dir", str(out_hub)])
    hub_raw_ops = json.loads((out_hub / "field_surveys_raw_values_batch.json").read_text(encoding="utf-8"))
    hub_survey_write = next(op for op in hub_raw_ops if op["range"].startswith("Field_Surveys!"))
    assert hub_survey_write["range"] == "Field_Surveys!A1:R1"
    assert len(hub_survey_write["data"]) == 1

    # Also exercise load_existing_field_surveys_csv + merge_field_survey_rows directly
    assert len(merge_field_survey_rows(load_existing_field_surveys_csv(existing_csv.read_text()), [])) == 2

    # High #4: sync_curated_edits cross-language parity with curatedEdits.js
    rect = validate_and_normalize_override_polygon(
        _make_rect_ring(-95.383400, 29.791800, 12.0, 10.0)
    )["geometry"]
    csv_buf = io.StringIO()
    cw = csv.DictWriter(
        csv_buf,
        fieldnames=[
            "status",
            "hcad_num",
            "address",
            "building_name",
            "alt_names",
            "historic_district",
            "contributing",
            "original_hcad_year",
            "year_built",
            "bld_style",
            "architect",
            "source_type",
            "source_citation",
            "source_url",
            "verified_by",
            "geometry_geojson",
            "replace_parcel_shards",
        ],
    )
    cw.writeheader()
    cw.writerow(
        {
            "status": "Approved",
            "hcad_num": "0621100000014",
            "address": "1127 KEY ST",
            "building_name": "Key House",
            "alt_names": "Old Key Bungalow; Morris Cottage",
            "historic_district": "Norhill Historic District",
            "contributing": "Contributing",
            "original_hcad_year": "1920",
            "year_built": "",
            "bld_style": "1920s Craftsman Bungalow",
            "architect": "Russell Brown Co.",
            "source_type": "Footprint Issue: Reshaped building footprint",
            "source_citation": "Satellite roof verification",
            "source_url": "",
            "verified_by": "Staff",
            "geometry_geojson": json.dumps(rect, separators=(",", ":")),
            "replace_parcel_shards": "false",
        }
    )
    parsed_rows = parse_approved_csv_rows(csv_buf.getvalue())
    assert len(parsed_rows) == 1
    assert parsed_rows[0]["alt_names"] == ["Old Key Bungalow", "Morris Cottage"]
    assert parsed_rows[0]["bld_style"] == "1920s Craftsman Bungalow"
    assert parsed_rows[0]["replace_parcel_shards"] is False

    merged_rec = merge_override_record(
        {"hcad_num": "0621100000014", "year_built": 1928, "source_type": "Houston City Directory"},
        parsed_rows[0],
        "0621100000014",
    )
    assert merged_rec["source_type"] == "Houston City Directory"
    assert merged_rec["year_built"] == 1928
    assert merged_rec["alt_names"] == ["Old Key Bungalow", "Morris Cottage"]
    assert merged_rec["bld_style"] == "1920s Craftsman Bungalow"
    assert merged_rec["replace_parcel_shards"] is False


