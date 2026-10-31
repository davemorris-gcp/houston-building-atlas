"""Milestone 4 hermetic unit tests for Annual HCAD Appraisal Roll & Parcel Refresher.

Covers FAST-horizon unit test scenarios UT-1 through UT-8:
  - UT-1: Upstream roll discovery, download safety (.part staging, HTTP 200 vs HTML/error,
    ZIP magic/testzip, SHA-256 manifest), and multi-driver Stage 1 validation (GDB/GPKG/SHP/
    GeoJSON, header aliases, EPSG:2278 -> HARRIS_COUNTY_BBOX check, retention floor, certified ratio).
  - UT-2: Multi-building account normalization, explicit max_year runtime propagation (zero
    def-time year freezing across schema & sibling scripts), 1899/1900/1901 sentinel blocking,
    v2 cama_<YYYY>_compact.pkl read/write safety, school situs protection, and demolition permit gates.
  - UT-3: Provenance partitioning across curated/campus/tax-exempt invariants (FULL_CURATED_REPLACE,
    PARTIAL_CURATED_SUBBUILDINGS, UNCURATED_MULTI_FOOTPRINT, SINGLE_FOOTPRINT, suppress_only revival,
    Cohort 5 protected disagreement alert, and orphaned curated reference audit + tombstoned watchlist skip).
  - UT-4: EPSG:2278 spatial replat resolver & mass-demolition circuit breaker (1 -> 1 renumbering,
    M -> 1 consolidation with #slug disambiguation, 1 -> K townhome split overhanging parent drop vs
    carve-out retention, tieback assistance, and > 2.5% mass-demolition abort).
  - UT-5: New-construction oriented footprint synthesis (Borden -34.65 deg grid snap vs arbitrary
    parcel bearing, setback clipping, multi-building splitting, <= 6 decimals CCW) and deterministic
    Phases 2A-4 undated fallback achieving unknown == 0 without feedback contamination.
  - UT-6: Historical accuracy audit dynamic roll-year semantics (run_audit / parse_dos_year /
    extract_hcad_account_data accepting 2027..2030 dates with valid H+ deterministic finding_id hashes).
  - UT-7: Neighborhood scorecards & Ch. 42 renewal horizon dynamic computation across 2027 and 2030
    (auto-expanding demo_years, decade_bins ['2030'], sum(dm) == dmt, Ch. 42 renewal window).
  - UT-8: Manifest v3 metadata stamping, lockstep AnchorRule synchronization (2027 + 2030s decade
    injection, stale-anchor atomic abort), and multi-entry deed chain truncation determinism.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import pickle
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
import urllib.error
import zipfile

import numpy as np
import pyogrio.raw
import pytest
from pyproj import Transformer
import shapely
from shapely.geometry import Polygon, mapping, shape

from atlas_pipeline.audit_historical_accuracy import run_audit
from atlas_pipeline.build_neighborhood_scorecards import (
    main as rebuild_scorecards_main,
    parse_demolition_permit_row,
)
from atlas_pipeline.build_permits_and_contractors import build_hcad_permits_and_cama_index
from atlas_pipeline.enrich_deed_chains import (
    MAX_DEEDS_PER_PARCEL,
    normalize_hcad_clerk_instrument,
    parse_dos_yyyymmdd,
)
from atlas_pipeline.enrich_institutions import run_all_enrichments as enrich_institutions_main
from atlas_pipeline.lost_houston_expansion import (
    build_cohorts_2_to_5,
    evaluate_cohort5_teardown_candidate,
)
from atlas_pipeline.resolve_undated_countywide import (
    extract_plausible_text_year,
    resolve_undated_feature_batch,
)
from atlas_pipeline.schema import (
    ACTIVE_CACHE_BUST,
    ACTIVE_TAX_YEAR,
    HARRIS_COUNTY_BBOX,
    MIN_VALID_YEAR,
    QUAD_NAMES,
    RollContext,
    classify_main_structure_demolition_permit,
    compute_decade,
    compute_decade_bins,
    compute_demo_years,
    load_compact_cama_lookup,
    is_hcad_null_date_sentinel,
    normalize_hcad_date,
    normalize_polygon_rings_6dp_ccw,
    normalize_standing_override_year,
    normalize_year,
    parse_hcad_date_year,
    resolve_ch42_ordinance_lifecycle,
    save_compact_cama_lookup,
    sync_repo_tax_year_and_cache_bust,
    update_pmtiles_manifest_v3,
    write_compact_json,
)
from atlas_pipeline.tile_builder import compile_pmtiles_archive
from atlas_pipeline.update_annual_hcad_roll import (
    ArtifactSyncDelta,
    DeltaCategory,
    ParcelGovernance,
    RollSanityConfig,
    RollValidationError,
    _stream_targeted_enrichment_records,
    audit_curated_hcad_references,
    build_override_protection_index,
    classify_feature_delta,
    classify_parcel_footprint_governance,
    discover_latest_hcad_roll_year,
    load_parcel_geometries_2278,
    probe_hcad_archive_url,
    prune_optional_enrichment_for_soft_budget,
    reconcile_derived_building_indexes,
    resolve_parcels_vector_source,
    resolve_replat_lineage,
    run_annual_hcad_refresh,
    stage_hcad_roll_archives,
    stream_hcad_account_snapshots,
    synthesize_new_construction_features,
    validate_staged_app_bundle,
    validate_staged_hcad_roll,
)

TO_2278 = Transformer.from_crs("EPSG:4326", "EPSG:2278", always_xy=True)
FROM_2278 = Transformer.from_crs("EPSG:2278", "EPSG:4326", always_xy=True)


# ---------------------------------------------------------------------------
# Hermetic Mini-County Archive & Spatial Builders
# ---------------------------------------------------------------------------


def make_wgs84_box(center_lon: float, center_lat: float, half_w_deg: float = 0.00018, half_h_deg: float = 0.00018) -> dict[str, Any]:
    """Return a clean 6-decimal CCW GeoJSON Polygon around (center_lon, center_lat)."""
    ring = [
        [round(center_lon - half_w_deg, 6), round(center_lat - half_h_deg, 6)],
        [round(center_lon + half_w_deg, 6), round(center_lat - half_h_deg, 6)],
        [round(center_lon + half_w_deg, 6), round(center_lat + half_h_deg, 6)],
        [round(center_lon - half_w_deg, 6), round(center_lat + half_h_deg, 6)],
        [round(center_lon - half_w_deg, 6), round(center_lat - half_h_deg, 6)],
    ]
    return normalize_polygon_rings_6dp_ccw({"type": "Polygon", "coordinates": [ring]})


def wgs84_poly_to_2278(geom_dict: dict[str, Any]) -> Polygon:
    poly_wgs = shape(geom_dict)
    coords_2278 = [TO_2278.transform(x, y) for x, y in poly_wgs.exterior.coords]
    return Polygon(coords_2278)


def _tsv_bytes(headers: list[str], rows: list[dict[str, Any]]) -> bytes:
    lines = ["\t".join(headers)]
    for row in rows:
        lines.append("\t".join(str(row.get(h, "")) for h in headers))
    return ("\n".join(lines) + "\n").encode("latin-1", errors="replace")


def write_synthetic_roll_zips(
    target_dir: Path,
    *,
    tax_year: int = 2027,
    real_acct_rows: list[dict[str, Any]] | None = None,
    building_res_rows: list[dict[str, Any]] | None = None,
    building_other_rows: list[dict[str, Any]] | None = None,
    extra_features_rows: list[dict[str, Any]] | None = None,
    permits_rows: list[dict[str, Any]] | None = None,
    deeds_rows: list[dict[str, Any]] | None = None,
    tieback_rows: list[dict[str, Any]] | None = None,
    parcel_records: list[tuple[str, Polygon]] | None = None,
    parcel_driver: str = "GPKG",
    parcel_crs: str = "EPSG:2278",
    parcel_hcad_field: str = "HCAD_NUM",
    erect_col_name: str = "date_erected",
    area_col_name: str = "im_sq_ft",
) -> dict[str, Path]:
    """Build hermetic Real_building_land.zip, Real_acct_owner.zip, and Parcels.zip in target_dir."""
    target_dir.mkdir(parents=True, exist_ok=True)
    bldg_zip = target_dir / "Real_building_land.zip"
    acct_zip = target_dir / "Real_acct_owner.zip"
    parcels_zip = target_dir / "Parcels.zip"

    bldg_headers = [
        "acct",
        "bld_num",
        erect_col_name,
        "yr_remodel",
        "dscr",
        "structure_dscr",
        "property_use_cd",
        area_col_name,
        "notes",
    ]
    xf_headers = ["acct", "bld_num", "cd", "dscr", "l_dscr", "act_yr", "eff_yr", "uts"]
    with zipfile.ZipFile(bldg_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("building_res.txt", _tsv_bytes(bldg_headers, building_res_rows or []))
        zf.writestr("building_other.txt", _tsv_bytes(bldg_headers, building_other_rows or []))
        zf.writestr("extra_features_detail1.txt", _tsv_bytes(xf_headers, extra_features_rows or []))

    acct_headers = [
        "acct",
        "yr",
        "mailto",
        "site_addr_1",
        "site_addr_2",
        "site_addr_3",
        "str_num",
        "str_num_sfx",
        "str_pfx",
        "str",
        "str_sfx",
        "str_sfx_dir",
        "str_unit",
        "state_class",
        "yr_impr",
        "bld_ar",
        "land_ar",
        "acreage",
        "bld_val",
        "tot_appr_val",
        "tot_mkt_val",
        "splt_dt",
        "certified_date",
        "New_Own_Dt",
        "lgl_1",
        "lgl_2",
        "lgl_3",
        "lgl_4",
    ]
    perm_headers = [
        "acct",
        "id",
        "agency_id",
        "status",
        "dscr",
        "dor_cd",
        "permit_type",
        "permit_tp_descr",
        "property_tp",
        "issue_date",
        "yr",
        "site_num",
        "site_pfx",
        "site_str",
        "site_tp",
        "site_sfx",
        "site_apt",
    ]
    deed_headers = ["acct", "dos", "clerk_yr", "clerk_id", "deed_id"]
    tb_headers = ["acct", "tp", "dscr", "related_acct", "pct"]

    norm_accts: list[dict[str, Any]] = []
    for raw in real_acct_rows or []:
        rec = dict(raw)
        rec.setdefault("yr", str(tax_year))
        rec.setdefault("certified_date", f"08/15/{tax_year}")
        rec.setdefault("state_class", "A1")
        rec.setdefault("yr_impr", "")
        rec.setdefault("bld_ar", "1500")
        rec.setdefault("land_ar", "5000")
        rec.setdefault("bld_val", "200000")
        rec.setdefault("splt_dt", "")
        rec.setdefault("lgl_1", "LOT 1 BLK 1")
        rec.setdefault("lgl_2", "TEST ADDITION")
        norm_accts.append(rec)

    with zipfile.ZipFile(acct_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("real_acct.txt", _tsv_bytes(acct_headers, norm_accts))
        zf.writestr("permits.txt", _tsv_bytes(perm_headers, permits_rows or []))
        zf.writestr("deeds.txt", _tsv_bytes(deed_headers, deeds_rows or []))
        zf.writestr("parcel_tieback.txt", _tsv_bytes(tb_headers, tieback_rows or []))

    p_recs = parcel_records
    if p_recs is None:
        p_recs = []
        for idx, r in enumerate(norm_accts):
            geom_wgs = make_wgs84_box(-95.3800 + idx * 0.001, 29.7500)
            poly_out = shape(geom_wgs) if "4326" in parcel_crs else wgs84_poly_to_2278(geom_wgs)
            p_recs.append((str(r["acct"]), poly_out))

    scratch_vec = target_dir / "_build_vec"
    scratch_vec.mkdir(parents=True, exist_ok=True)
    geom_wkb = shapely.to_wkb(np.asarray([g for _, g in p_recs], dtype=object))
    field_arr = np.asarray([a for a, _ in p_recs], dtype=object)
    if parcel_driver == "GPKG":
        vec_file = scratch_vec / "Parcels.gpkg"
        pyogrio.raw.write(
            vec_file,
            geom_wkb,
            [field_arr],
            [parcel_hcad_field],
            layer="Parcels",
            driver="GPKG",
            geometry_type="Polygon",
            crs=parcel_crs,
        )
        members = [(vec_file, "Parcels.gpkg")]
    elif parcel_driver == "ESRI Shapefile":
        vec_file = scratch_vec / "Parcels.shp"
        pyogrio.raw.write(
            vec_file,
            geom_wkb,
            [field_arr],
            [parcel_hcad_field],
            driver="ESRI Shapefile",
            geometry_type="Polygon",
            crs=parcel_crs,
        )
        members = [(p, p.name) for p in sorted(scratch_vec.glob("Parcels.*"))]
    elif parcel_driver == "GeoJSON":
        vec_file = scratch_vec / "Parcels.geojson"
        feats_gj = [
            {
                "type": "Feature",
                "properties": {parcel_hcad_field: a},
                "geometry": mapping(g),
            }
            for a, g in p_recs
        ]
        vec_file.write_text(
            json.dumps(
                {
                    "type": "FeatureCollection",
                    "name": "Parcels",
                    "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
                    "features": feats_gj,
                }
            ),
            encoding="utf-8",
        )
        members = [(vec_file, "Parcels.geojson")]
    else:
        raise ValueError(f"Unsupported synthetic parcel_driver: {parcel_driver}")

    with zipfile.ZipFile(parcels_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for src_p, arc_name in members:
            zf.write(src_p, arcname=arc_name)

    for child in scratch_vec.glob("*"):
        child.unlink()
    scratch_vec.rmdir()
    return {
        "Real_building_land.zip": bldg_zip,
        "Real_acct_owner.zip": acct_zip,
        "Parcels.zip": parcels_zip,
    }


class _FakeHttpResponse:
    def __init__(self, payload: bytes, *, status: int = 200, content_type: str = "application/zip") -> None:
        self._buf = io.BytesIO(payload)
        self.status = status
        self.headers = {"Content-Type": content_type, "Content-Length": str(len(payload))}

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)

    def getcode(self) -> int:
        return self.status

    def __enter__(self) -> _FakeHttpResponse:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


def make_building_feature(
    hcad: str,
    lon: float,
    lat: float,
    *,
    building_id: str | None = None,
    year_built: int = 1925,
    year_source: str = "hcad_cama",
    address: str = "100 MAIN ST",
    owner: str = "SMITH JANE",
    use_category: str = "Residential",
    bld_style: str = "Bungalow",
    bld_area: float = 1600.0,
    land_area: float = 5000.0,
    extra_props: dict[str, Any] | None = None,
    half_w_deg: float = 0.00009,
    half_h_deg: float = 0.00009,
) -> dict[str, Any]:
    fid = building_id or hcad
    geom = make_wgs84_box(lon, lat, half_w_deg=half_w_deg, half_h_deg=half_h_deg)
    props: dict[str, Any] = {
        "id": fid,
        "building_id": fid,
        "hcad_num": hcad,
        "address": address,
        "owner": owner,
        "year_built": year_built,
        "decade": compute_decade(year_built) if year_built >= MIN_VALID_YEAR else 0,
        "year_source": year_source,
        "remodel_year": 0,
        "use_category": use_category,
        "bld_style": bld_style,
        "bld_area": bld_area,
        "footprint_area_sqft": 1400.0,
        "land_area": land_area,
        "stories": 1.0,
        "height_m": 4.2,
        "historic_district": "",
        "contributing_status": "Outside Historic District",
        "is_landmark": False,
        "landmark_name": "",
        "is_protected_landmark": False,
        "is_nrhp": False,
        "is_curated_override": False,
    }
    if extra_props:
        props.update(extra_props)
    return {"type": "Feature", "geometry": geom, "properties": props}


# ---------------------------------------------------------------------------
# UT-1: Upstream Discovery, Staging Safety & Multi-Driver Pre-Flight Gates
# ---------------------------------------------------------------------------


def test_ut1_discovery_download_safety_and_preflight_validation(tmp_path: Path) -> None:
    src_roll = tmp_path / "src_2027"
    write_synthetic_roll_zips(
        src_roll,
        tax_year=2027,
        real_acct_rows=[
            {
                "acct": f"00100000000{i:02d}",
                "yr": "2027",
                "mailto": "OWNER A",
                "site_addr_1": f"{100 + i} MAIN ST",
                "str_num": str(100 + i),
                "str": "MAIN",
                "str_sfx": "ST",
                "yr_impr": "1930",
                "bld_ar": "1500",
                "certified_date": "08/20/2027",
            }
            for i in range(1, 11)
        ],
        building_res_rows=[
            {
                "acct": f"00100000000{i:02d}",
                "bld_num": "1",
                "date_erect": "1930",
                "yr_remodel": "",
                "dscr": "Frame",
                "structure_dscr": "Single-Family",
                "property_use_cd": "A1",
                "act_ar": "1500",
            }
            for i in range(1, 11)
        ],
        erect_col_name="date_erect",
        area_col_name="act_ar",
        parcel_driver="GPKG",
    )

    zip_bytes_map = {
        name: (src_roll / name).read_bytes()
        for name in ("Real_building_land.zip", "Real_acct_owner.zip", "Parcels.zip")
    }

    def fake_opener(req: urllib.request.Request, timeout: float = 20.0) -> _FakeHttpResponse:
        url = req.full_url
        if "/2026/" in url or "/2027/" in url or url.endswith("/Parcels.zip"):
            for name, payload in zip_bytes_map.items():
                if url.endswith(f"/{name}"):
                    if req.get_method() == "GET" and req.headers.get("Range") == "bytes=0-3":
                        return _FakeHttpResponse(payload[:4], status=206)
                    return _FakeHttpResponse(payload, status=200)
        if "/2028/" in url:
            return _FakeHttpResponse(b"<html>404 Not Ready</html>", status=200, content_type="text/html")
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)  # type: ignore[arg-type]

    assert probe_hcad_archive_url(
        "https://download.hcad.org/data/CAMA/2028/Real_acct_owner.zip", opener=fake_opener
    ) is False
    discovered = discover_latest_hcad_roll_year(start_year=2026, max_probe_year=2029, opener=fake_opener)
    assert discovered == 2027

    cache_dir = tmp_path / "cache"
    out_dir = tmp_path / "data"
    out_dir.mkdir(parents=True)
    ctx = RollContext(tax_year=2027, cache_dir=cache_dir, output_dir=out_dir)
    assert ctx.roll_dir == cache_dir / "rolls" / "2027"
    assert ctx.staged_dir == cache_dir / "rolls" / "2027" / "staged"

    staged_dir, manifest = stage_hcad_roll_archives(ctx, opener=fake_opener)
    assert staged_dir == ctx.roll_dir
    assert (staged_dir / "roll_manifest.json").exists()
    for req_name in ("Real_building_land.zip", "Real_acct_owner.zip", "Parcels.zip"):
        expected_sha = hashlib.sha256(zip_bytes_map[req_name]).hexdigest()
        assert manifest["archives"][req_name]["sha256"] == expected_sha
        assert not (staged_dir / f"{req_name}.part").exists()

    # Validate stage passes with aliased columns date_erect / act_ar and >= 95% retention
    res = validate_staged_hcad_roll(
        staged_dir,
        ctx=ctx,
        baseline_acct_count=10,
        baseline_improved_count=10,
        baseline_parcel_count=10,
    )
    assert res["ok"] is True
    assert res["is_certified_roll"] is True
    assert res["certified_ratio"] == 1.0
    assert res["sample_in_bbox_ratio"] == 1.0

    # Verify Shapefile and GeoJSON drivers in Parcels.zip also resolve cleanly
    for drv in ("ESRI Shapefile", "GeoJSON"):
        alt_dir = tmp_path / f"drv_{drv.replace(' ', '_')}"
        write_synthetic_roll_zips(
            alt_dir,
            tax_year=2027,
            real_acct_rows=[{"acct": "0010000000099", "str_num": "99", "str": "ELM", "yr_impr": "1950"}],
            parcel_driver=drv,
            parcel_crs="EPSG:4326" if drv == "GeoJSON" else "EPSG:2278",
        )
        ds_p, lyr = resolve_parcels_vector_source(alt_dir / "Parcels.zip")
        assert ds_p and lyr
        assert validate_staged_hcad_roll(alt_dir, ctx=ctx)["ok"] is True

    # Fail-closed gates:
    # (a) HTML error page disguised as HTTP 200 cleans up .part file
    bad_ctx = RollContext(tax_year=2028, cache_dir=cache_dir, output_dir=out_dir)
    with pytest.raises(RollValidationError, match="Content-Type|HTML"):
        stage_hcad_roll_archives(bad_ctx, opener=fake_opener, force_redownload=True)
    assert list(bad_ctx.roll_dir.glob("*.part")) == []

    # (b) Truncated ZIP caught immediately
    trunc_dir = tmp_path / "trunc_roll"
    trunc_dir.mkdir()
    for name, bts in zip_bytes_map.items():
        (trunc_dir / name).write_bytes(bts[:20] if name == "Real_acct_owner.zip" else bts)
    with pytest.raises(RollValidationError, match="valid ZIP|corrupt|truncated|BadZipFile"):
        validate_staged_hcad_roll(trunc_dir, ctx=ctx)

    # (c) Retention drop below 95% rejected unless allow_partial_roll=True
    with pytest.raises(RollValidationError, match="Account retention ratio"):
        validate_staged_hcad_roll(staged_dir, ctx=ctx, baseline_acct_count=100)
    assert validate_staged_hcad_roll(
        staged_dir, ctx=ctx, baseline_acct_count=100, allow_partial_roll=True
    )["ok"] is True

    # (d) Modal roll year mismatch rejected
    wrong_yr_ctx = RollContext(tax_year=2028, cache_dir=cache_dir, output_dir=out_dir)
    with pytest.raises(RollValidationError, match="expected tax_year=2028"):
        validate_staged_hcad_roll(staged_dir, ctx=wrong_yr_ctx)

    # (e) Out-of-bounds parcel CRS coordinates rejected
    oob_dir = tmp_path / "oob_roll"
    oob_poly_2278 = Polygon([(0, 0), (100, 0), (100, 100), (0, 100), (0, 0)])
    write_synthetic_roll_zips(
        oob_dir,
        tax_year=2027,
        real_acct_rows=[{"acct": "0010000000077", "str_num": "77", "str": "OAK", "yr_impr": "1960"}],
        parcel_records=[("0010000000077", oob_poly_2278)],
    )
    with pytest.raises(RollValidationError, match="HARRIS_COUNTY_BBOX"):
        validate_staged_hcad_roll(oob_dir, ctx=ctx)

    # (f) Header alias missing raises RollValidationError naming missing column
    bad_hdr_dir = tmp_path / "bad_hdr"
    write_synthetic_roll_zips(
        bad_hdr_dir,
        tax_year=2027,
        real_acct_rows=[{"acct": "0010000000088", "str_num": "88", "str": "PINE", "yr_impr": "1960"}],
    )
    with zipfile.ZipFile(bad_hdr_dir / "Real_building_land.zip", "w") as zf:
        zf.writestr("building_res.txt", b"acct\tdscr\n0010000000088\tFrame\n")
        zf.writestr("building_other.txt", b"acct\tdscr\n")
        zf.writestr("extra_features_detail1.txt", b"acct\tcd\tdscr\tact_yr\teff_yr\n")
    with pytest.raises(RollValidationError, match="date_erected/date_erect"):
        validate_staged_hcad_roll(bad_hdr_dir, ctx=ctx)


# ---------------------------------------------------------------------------
# UT-2: Multi-Building Normalization, Explicit max_year & Sentinel Blocking
# ---------------------------------------------------------------------------


def test_ut2_runtime_max_year_propagation_multibuilding_and_cama_v2_pickle(tmp_path: Path) -> None:
    # 1. Explicit max_year runtime propagation across schema & sibling helpers (no def-time freeze)
    assert ACTIVE_TAX_YEAR == 2026
    assert normalize_year("2028", max_year=2028) == 2028
    assert normalize_year("2028") == 0  # Default bound remains 2027 under ACTIVE_TAX_YEAR=2026
    assert parse_hcad_date_year("07/14/2028", max_year=2028) == 2028
    assert is_hcad_null_date_sentinel(1899, 12, 30) is True
    assert is_hcad_null_date_sentinel(1900, 1, 1) is True
    assert is_hcad_null_date_sentinel(1900, 6, 15) is False
    assert normalize_hcad_date("05/12/2028", None, allow_fallback=False, max_year=2028) == (
        "2028-05-12",
        2028,
    )
    assert normalize_hcad_date("01/01/1900", "2027", allow_fallback=False, max_year=2027) == (None, 0)
    assert normalize_hcad_date("12/30/1899", "2027", allow_fallback=False, max_year=2027) == (None, 0)
    assert extract_plausible_text_year("ERECTED IN 2028 PER PERMIT", max_year=2028) == 2028
    assert normalize_standing_override_year(2027) == 2027

    # 2. Multi-building CAMA streaming + zero sentinel bleed + accessory permit immunity
    roll_dir = tmp_path / "roll_2027"
    write_synthetic_roll_zips(
        roll_dir,
        tax_year=2027,
        real_acct_rows=[
            {
                # Mixed apartment / duplex account: 1924 main + 2005 garage + 2027 build
                "acct": "0020000000001",
                "yr": "2027",
                "mailto": "HISTORIC HOLDINGS LLC",
                "site_addr_1": "0 MAIN ST",
                "str_num": "",
                "str": "MAIN",
                "str_sfx": "ST",
                "state_class": "B2",
                "yr_impr": "1900",  # Null-date placeholder must be suppressed!
                "bld_ar": "4200",
                "land_ar": "7500",
            },
            {
                # Account with undated + 1899/1900 sentinel permits carrying yr=2027
                "acct": "0020000000002",
                "yr": "2027",
                "mailto": "SENTINEL TEST OWNER",
                "site_addr_1": "412 TRAVIS ST",
                "str_num": "412",
                "str": "TRAVIS",
                "str_sfx": "ST",
                "state_class": "A1",
                "yr_impr": "0",
                "bld_ar": "1200",
                "splt_dt": "01/01/1900",
                "New_Own_Dt": "12/30/1899",
            },
            {
                # Main-structure demolition permit in 2027 vs accessory garage demo on #1
                "acct": "0020000000003",
                "yr": "2027",
                "mailto": "VACANT LOT LLC",
                "site_addr_1": "510 MILAM ST",
                "str_num": "510",
                "str": "MILAM",
                "str_sfx": "ST",
                "state_class": "C1",
                "yr_impr": "",
                "bld_ar": "0",
                "bld_val": "0",
            },
        ],
        building_res_rows=[
            {
                "acct": "0020000000001",
                "bld_num": "1",
                "date_erected": "1924",
                "yr_remodel": "2018",
                "dscr": "Average",
                "structure_dscr": "Duplex",
                "property_use_cd": "B2",
                "im_sq_ft": "2200",
            },
            {
                "acct": "0020000000001",
                "bld_num": "2",
                "date_erected": "2005",
                "yr_remodel": "",
                "dscr": "Detached Garage Apt",
                "structure_dscr": "Garage Apartment",
                "property_use_cd": "B2",
                "im_sq_ft": "600",
            },
            {
                "acct": "0020000000001",
                "bld_num": "3",
                "date_erected": "2027",
                "yr_remodel": "",
                "dscr": "Good",
                "structure_dscr": "Accessory Dwelling",
                "property_use_cd": "B2",
                "im_sq_ft": "1400",
            },
            {
                "acct": "0020000000002",
                "bld_num": "1",
                "date_erected": "0",
                "yr_remodel": "0",
                "dscr": "Average",
                "structure_dscr": "Single-Family",
                "property_use_cd": "A1",
                "im_sq_ft": "1200",
            },
        ],
        extra_features_rows=[
            {
                "acct": "0020000000002",
                "bld_num": "1",
                "cd": "RRP1",
                "dscr": "CANOPY",
                "act_yr": "0",
                "eff_yr": "2027",
                "uts": "1",
            }
        ],
        permits_rows=[
            # Accessory garage demo on 0020000000001 -> MUST NOT flag main demo!
            {
                "acct": "0020000000001",
                "permit_type": "09",
                "permit_tp_descr": "Demolition",
                "dscr": "DEMO DETACHED GARAGE ONLY",
                "issue_date": "03/15/2027",
                "yr": "2027",
                "site_num": "100",
                "site_str": "MAIN",
                "site_tp": "ST",
            },
            # Undated permit on 0020000000002 with blank issue_date and yr=2027 -> MUST NOT flag!
            {
                "acct": "0020000000002",
                "permit_type": "13",
                "permit_tp_descr": "Demolition",
                "dscr": "DEMO RESIDENCE",
                "issue_date": "",
                "yr": "2027",
                "site_num": "412",
                "site_str": "TRAVIS",
                "site_tp": "ST",
            },
            # 12/30/1899 and 01/01/1900 null-date sentinel permits -> MUST NOT fall back to yr=2027!
            {
                "acct": "0020000000002",
                "permit_type": "13",
                "permit_tp_descr": "Demolition",
                "dscr": "DEMO HOUSE",
                "issue_date": "12/30/1899",
                "yr": "2027",
                "site_num": "412",
                "site_str": "TRAVIS",
                "site_tp": "ST",
            },
            {
                "acct": "0020000000002",
                "permit_type": "13",
                "permit_tp_descr": "Demolition",
                "dscr": "DEMO HOUSE",
                "issue_date": "01/01/1900",
                "yr": "2027",
                "site_num": "412",
                "site_str": "TRAVIS",
                "site_tp": "ST",
            },
            # Real dated 2027 main-structure demo permit on 0020000000003 -> MUST flag demo_permit_year=2027!
            {
                "acct": "0020000000003",
                "permit_type": "13",
                "permit_tp_descr": "Demolition",
                "dscr": "DEMOLISH HOUSE SEWER DISCONNECT",
                "issue_date": "04/10/2027",
                "yr": "2027",
                "site_num": "510",
                "site_str": "MILAM",
                "site_tp": "ST",
            },
        ],
        deeds_rows=[
            {"acct": "0020000000002", "dos": "01/01/1900", "clerk_yr": "1900", "clerk_id": "1"},
            {"acct": "0020000000002", "dos": "06/15/2027", "clerk_yr": "2027", "clerk_id": "202712345"},
        ],
    )

    snaps = stream_hcad_account_snapshots(
        roll_dir / "Real_building_land.zip",
        roll_dir / "Real_acct_owner.zip",
        tax_year=2027,
        prior_tax_year=2026,
        cache_dir_for_cama_pkl=roll_dir,
    )
    s1 = snaps["0020000000001"]
    assert s1.earliest_year == 1924
    assert s1.latest_year == 2027
    assert s1.max_remodel_year == 2018
    assert s1.building_count == 3
    assert s1.bld_style == "Duplex"  # Largest structure wins representative style
    assert s1.numbered_address == ""  # "0 MAIN ST" correctly rejected by VALID_NUMBERED_SITUS_RE
    assert s1.has_main_demo_permit is False

    s2 = snaps["0020000000002"]
    assert s2.earliest_year == 0
    assert s2.yr_impr == 0
    assert normalize_hcad_date(s2.splt_dt, "2027", allow_fallback=False, max_year=2027) == (None, 0)
    assert normalize_hcad_date(s2.new_own_dt, "2027", allow_fallback=False, max_year=2027) == (None, 0)
    assert s2.has_main_demo_permit is False
    assert s2.demo_permit_year == 0

    s3 = snaps["0020000000003"]
    assert s3.has_main_demo_permit is True
    assert s3.demo_permit_year == 2027

    # 3. Stamped v2 cama_2027_compact.pkl round-trip + year-mismatch quarantine + unversioned dict backward compat
    loaded_2027 = load_compact_cama_lookup(roll_dir, tax_year=2027)
    assert loaded_2027 is not None
    assert loaded_2027["0020000000001"][0] == 1924
    assert load_compact_cama_lookup(roll_dir, tax_year=2028) is None

    legacy_dir = tmp_path / "legacy_cama"
    legacy_dir.mkdir()
    with open(legacy_dir / "cama_2026_compact.pkl", "wb") as fh:
        pickle.dump({"0020000000001": (1924, 2018, 4200, "B2", "Duplex", "HISTORIC", "100 MAIN ST", 7500.0)}, fh)
    assert load_compact_cama_lookup(legacy_dir, tax_year=2026) is not None
    assert load_compact_cama_lookup(legacy_dir, tax_year=2027) is None  # Stale legacy pkl safely ignored for 2027!

    # Also write wrong metadata tax_year inside stamped file and confirm rejected
    bad_meta_dir = tmp_path / "bad_meta"
    save_compact_cama_lookup(bad_meta_dir, {"0020000000001": s1.to_cama_tuple()}, tax_year=2026)
    (bad_meta_dir / "cama_2026_compact.pkl").rename(bad_meta_dir / "cama_2027_compact.pkl")
    assert load_compact_cama_lookup(bad_meta_dir, tax_year=2027) is None

    # 4. Sibling extractors honor explicit tax_year=2027 without discarding 2027 permits or teardowns
    is_main_d, yr_d, _r_d = classify_main_structure_demolition_permit(
        "DEMO SFR HOUSE", "09", "05/01/2027", max_year=2027
    )
    assert is_main_d is True and yr_d == 2027
    assert callable(build_hcad_permits_and_cama_index)
    assert callable(parse_demolition_permit_row)


# ---------------------------------------------------------------------------
# UT-3: Provenance Partitioning, Multi-Milestone Invariants & Orphan Audit
# ---------------------------------------------------------------------------


def test_ut3_provenance_partitioning_curation_invariants_and_orphan_audit(tmp_path: Path) -> None:
    curated_doc = {
        "schema_version": 2,
        "overrides": {
            # Whole-parcel custom polygons (#a1, #a2) with suppress_only base parcel
            "0030000000001": {"suppress_only": True, "year_built": 0, "source_type": "staff"},
            "0030000000001#main": {
                "year_built": 1912,
                "name": "Carter Mansion",
                "architect": "Alfred C. Finn",
                "style": "Classical Revival",
                "geometry": make_wgs84_box(-95.3900, 29.7450),
            },
            # Partial curated sub-buildings keeping shard footprints
            "0030000000002#chapel": {
                "year_built": 1928,
                "name": "Historic Chapel",
                "keep_shard_footprints": True,
                "geometry": make_wgs84_box(-95.3890, 29.7450),
            },
            # Pure vacant lot suppression where CAMA adds a brand-new 2027 structure
            "0030000000003": {
                "year_built": 0,
                "demolished": True,
                "note": "Confirmed vacant lot",
                "updated_at": "2026-05-01T12:00:00Z",
            },
            # Attribute override on standing pre-1940 structure where CAMA jumps to 2027
            "0030000000004": {
                "year_built": 1915,
                "name": "Freedmen Cottage",
                "architect": "Unknown Vernacular",
                "source_type": "archival",
            },
            # Orphaned override on retired HCAD account (will be checked by audit_curated_hcad_references)
            "0030000000099": {
                "year_built": 1908,
                "name": "Retired Parcel House",
                "address": "715 ANDREWS ST",
            },
        },
    }
    ov_idx = build_override_protection_index(curated_doc, default_updated_year=2026)

    f_replace = [make_building_feature("0030000000001", -95.3900, 29.7450, building_id="0030000000001#main", year_built=1912)]
    assert (
        classify_parcel_footprint_governance("0030000000001", f_replace, ov_idx)
        == ParcelGovernance.FULL_CURATED_REPLACE
    )

    f_partial = [
        make_building_feature("0030000000002", -95.3890, 29.7450, building_id="0030000000002#chapel", year_built=1928),
        make_building_feature("0030000000002", -95.3887, 29.7450, building_id="0030000000002#b2", year_built=1985),
    ]
    assert (
        classify_parcel_footprint_governance("0030000000002", f_partial, ov_idx)
        == ParcelGovernance.PARTIAL_CURATED_SUBBUILDINGS
    )

    f_multi = [
        make_building_feature("0030000000005", -95.3880, 29.7450, building_id="0030000000005#rec_0", year_built=1962),
        make_building_feature("0030000000005", -95.3877, 29.7450, building_id="0030000000005#rec_1", year_built=1974),
    ]
    assert (
        classify_parcel_footprint_governance("0030000000005", f_multi, ov_idx)
        == ParcelGovernance.UNCURATED_MULTI_FOOTPRINT
    )

    # Set up synthetic roll covering:
    # - 0030000000002: CAMA owner/remodel updates without flattening curated #chapel; uncurated #b2 keeps its footprint year unless demolished
    # - 0030000000003: suppressed vacant lot gains 2027 CAMA building -> logged in rebuilt_on_suppressed_lots ONLY (not auto-synthesized unless opted in)
    # - 0030000000004: CAMA jumps date_erected to 2027 -> curated 1915 year/name/architect PROTECTED + logged in protected_cama_disagreements
    # - 0030000000006: Tax-exempt XV church with bld_ar=0 and zero demo permit -> stays STANDING!
    # - 0030000000007: HISD campus with school_situs_hydrated=True and CAMA "0 MAIN ST" -> school address & year PROTECTED!
    # - 0030000000008: COH Landmark Designation Report year_source -> chronology PROTECTED!
    roll_dir = tmp_path / "roll_ut3"
    parcel_recs = [
        (f"003000000000{i}", wgs84_poly_to_2278(make_wgs84_box(-95.3910 + i * 0.001, 29.7450)))
        for i in range(1, 10)
    ]
    # Successor parcel at the location of retired 0030000000099
    succ_geom_wgs = make_wgs84_box(-95.3750, 29.7520)
    parcel_recs.append(("0030000000100", wgs84_poly_to_2278(succ_geom_wgs)))

    write_synthetic_roll_zips(
        roll_dir,
        tax_year=2027,
        real_acct_rows=[
            {"acct": "0030000000001", "mailto": "CARTER TRUST", "str_num": "10", "str": "BELL", "yr_impr": "1912", "bld_ar": "4000"},
            {"acct": "0030000000002", "mailto": "NEW PARISH BOARD", "str_num": "20", "str": "BELL", "yr_impr": "1985", "bld_ar": "3000"},
            {"acct": "0030000000003", "mailto": "INFILL BUILDER LLC", "str_num": "30", "str": "BELL", "yr_impr": "2027", "bld_ar": "2800", "bld_val": "450000"},
            {"acct": "0030000000004", "mailto": "MODERN REHAB LLC", "str_num": "40", "str": "BELL", "yr_impr": "2027", "bld_ar": "2900", "bld_val": "500000"},
            {"acct": "0030000000005", "mailto": "CAMPUS APT LLC", "str_num": "50", "str": "BELL", "yr_impr": "1962", "bld_ar": "5000"},
            {"acct": "0030000000006", "mailto": "FIRST HISTORIC CHURCH", "str_num": "60", "str": "BELL", "state_class": "XV", "yr_impr": "", "bld_ar": "0", "bld_val": "0"},
            {"acct": "0030000000007", "mailto": "HOUSTON ISD", "site_addr_1": "0 WESTHEIMER RD", "str_num": "0", "str": "WESTHEIMER", "state_class": "X1", "yr_impr": "2010", "bld_ar": "90000"},
            {"acct": "0030000000008", "mailto": "LANDMARK OWNER", "str_num": "80", "str": "BELL", "yr_impr": "1955", "bld_ar": "2200"},
            {"acct": "0030000000009", "mailto": "INLINE CURATED OWNER", "str_num": "90", "str": "BELL", "yr_impr": "2027", "bld_ar": "2600", "bld_val": "420000"},
            {"acct": "0030000000100", "mailto": "REPLAT BUYER", "site_addr_1": "715 ANDREWS ST", "str_num": "715", "str": "ANDREWS", "str_sfx": "ST", "yr_impr": "1908", "bld_ar": "1800"},
        ],
        building_res_rows=[
            {"acct": "0030000000003", "bld_num": "1", "date_erected": "2027", "dscr": "Good", "structure_dscr": "Single-Family", "property_use_cd": "A1", "im_sq_ft": "2800"},
            {"acct": "0030000000004", "bld_num": "1", "date_erected": "2027", "dscr": "Good", "structure_dscr": "Single-Family", "property_use_cd": "A1", "im_sq_ft": "2900"},
            {"acct": "0030000000008", "bld_num": "1", "date_erected": "1955", "dscr": "Avg", "structure_dscr": "Single-Family", "property_use_cd": "A1", "im_sq_ft": "2200"},
            {"acct": "0030000000009", "bld_num": "1", "date_erected": "2027", "dscr": "Good", "structure_dscr": "Single-Family", "property_use_cd": "A1", "im_sq_ft": "2600"},
            {"acct": "0030000000100", "bld_num": "1", "date_erected": "1908", "dscr": "Avg", "structure_dscr": "Single-Family", "property_use_cd": "A1", "im_sq_ft": "1800"},
        ],
        tieback_rows=[
            {"acct": "0030000000099", "tp": "S", "dscr": "SPLIT", "related_acct": "0030000000100", "pct": "100"},
        ],
        parcel_records=parcel_recs,
    )

    snaps = stream_hcad_account_snapshots(
        roll_dir / "Real_building_land.zip",
        roll_dir / "Real_acct_owner.zip",
        tax_year=2027,
        prior_tax_year=2026,
    )
    p_hcads, p_geoms, p_map, p_tree = load_parcel_geometries_2278(roll_dir / "Parcels.zip")

    baseline: dict[str, list[dict[str, Any]]] = {q: [] for q in QUAD_NAMES}
    baseline["sw"] = [
        *f_replace,
        *f_partial,
        make_building_feature(
            "0030000000004",
            -95.3910 + 4 * 0.001,
            29.7450,
            year_built=1915,
            year_source="archival",
            extra_props={"name": "Freedmen Cottage", "architect": "Unknown Vernacular", "is_curated_override": True},
        ),
        *f_multi,
        make_building_feature(
            "0030000000006",
            -95.3910 + 6 * 0.001,
            29.7450,
            year_built=1930,
            use_category="Religious",
            bld_area=0.0,
        ),
        make_building_feature(
            "0030000000007",
            -95.3910 + 7 * 0.001,
            29.7450,
            building_id="0030000000007#main_wing",
            year_built=1929,
            address="6529 BEVERLY HILL ST",
            extra_props={"school_situs_hydrated": True},
        ),
        make_building_feature(
            "0030000000008",
            -95.3910 + 8 * 0.001,
            29.7450,
            year_built=1910,
            year_source="COH Landmark Designation Report",
            extra_props={"is_landmark": True, "landmark_name": "Historic House"},
        ),
        # Standalone inline is_curated_override=True (with year_source='hcad_cama' and absent from curated_doc)
        make_building_feature(
            "0030000000009",
            -95.3910 + 9 * 0.001,
            29.7450,
            year_built=1918,
            year_source="hcad_cama",
            owner="",
            extra_props={"is_curated_override": True},
        ),
    ]

    delta_default = classify_feature_delta(
        baseline,
        snaps,
        parcel_hcads=p_hcads,
        parcel_geoms_2278=p_geoms,
        parcel_hcad_to_idx=p_map,
        parcel_strtree=p_tree,
        tieback_map={"0030000000099": ["0030000000100"]},
        curated_overrides=curated_doc,
        tax_year=2027,
        prior_tax_year=2026,
    )
    out_by_id = {
        f["properties"]["building_id"]: f["properties"]
        for fl in delta_default["patched_by_quad"].values()
        for f in fl
    }
    # Curated #chapel stayed 1928; uncurated sibling #b2 stayed 1985 (NOT flattened to parcel earliest!)
    assert out_by_id["0030000000002#chapel"]["year_built"] == 1928
    assert out_by_id["0030000000002#b2"]["year_built"] == 1985
    assert out_by_id["0030000000002#b2"]["owner"] == "NEW PARISH BOARD"
    assert out_by_id["0030000000005#rec_0"]["year_built"] == 1962
    assert out_by_id["0030000000005#rec_1"]["year_built"] == 1974
    # Curated 1915 cottage resisted CAMA jump to 2027 AND surfaced disagreement alert
    assert out_by_id["0030000000004"]["year_built"] == 1915
    assert out_by_id["0030000000004"]["name"] == "Freedmen Cottage"
    assert out_by_id["0030000000004"]["architect"] == "Unknown Vernacular"
    assert any(d["hcad_num"] == "0030000000004" for d in delta_default["protected_cama_disagreements"])
    # XV church with bld_ar=0 kept standing
    assert out_by_id["0030000000006"]["year_built"] == 1930
    # HISD school kept hydrated numbered address and 1929 date despite "0 WESTHEIMER RD" & yr_impr=2010
    assert out_by_id["0030000000007#main_wing"]["address"] == "6529 BEVERLY HILL ST"
    assert out_by_id["0030000000007#main_wing"]["year_built"] == 1929
    # COH Landmark Designation Report date preserved over HCAD 1955
    assert out_by_id["0030000000008"]["year_built"] == 1910
    # Standalone inline is_curated_override=True (absent from curated_doc, year_source='hcad_cama') protected
    assert out_by_id["0030000000009"]["year_built"] == 1918
    assert out_by_id["0030000000009"]["owner"] == "INLINE CURATED OWNER"
    assert any(d["hcad_num"] == "0030000000009" for d in delta_default["protected_cama_disagreements"])
    # Suppressed vacant lot logged alert without synthesizing unless auto_revive_rebuilt_suppressed=True
    assert any(r["hcad_num"] == "0030000000003" for r in delta_default["rebuilt_on_suppressed_lots"])
    assert "0030000000003" not in delta_default["artifact_delta"].new_construction_hcads

    delta_revived = classify_feature_delta(
        baseline,
        snaps,
        parcel_hcads=p_hcads,
        parcel_geoms_2278=p_geoms,
        parcel_hcad_to_idx=p_map,
        parcel_strtree=p_tree,
        tieback_map={},
        curated_overrides=curated_doc,
        tax_year=2027,
        prior_tax_year=2026,
        auto_revive_rebuilt_suppressed=True,
    )
    assert "0030000000003" in delta_revived["artifact_delta"].new_construction_hcads

    # Audit curated references: retired 0030000000099 found with candidate successor 0030000000100,
    # while tombstoned watchlist entry ("Removed from Watchlist" / "Removed (Tier 3)") is skipped!
    ddir = tmp_path / "data_ut3"
    ddir.mkdir()
    write_compact_json(ddir / "curated_overrides.json", curated_doc)
    write_compact_json(
        ddir / "property_watchlist.json",
        {
            "watchedProperties": [
                {
                    "id": "tombstoned_prop",
                    "name": "Old False Alarm",
                    "hcadAccount": "0039999999991",
                    "status": "Removed from Watchlist",
                },
                {
                    "id": "tier3_removed",
                    "name": "Tier 3 Archived False Alarm",
                    "hcadAccount": "0039999999992",
                    "trackerStatus": "Removed (Tier 3)",
                },
                {
                    "id": "active_watch_vanished",
                    "name": "Active Historic Watch Target",
                    "hcadAccount": "0030000000099",
                    "status": "Active",
                    "lng": -95.3750,
                    "lat": 29.7520,
                },
            ]
        },
    )
    orphan_rep = audit_curated_hcad_references(
        ddir,
        snaps,
        parcel_hcads=p_hcads,
        parcel_hcad_to_idx=p_map,
        parcel_strtree=p_tree,
        tieback_map={"0030000000099": ["0030000000100"]},
    )
    flagged_hcads = {r["hcad_num"] for r in orphan_rep["items"]}
    assert "0030000000099" in flagged_hcads
    assert "0039999999991" not in flagged_hcads
    assert "0039999999992" not in flagged_hcads
    for rec in orphan_rep["items"]:
        if rec["hcad_num"] == "0030000000099":
            assert "0030000000100" in rec["successor_hcads"]


# ---------------------------------------------------------------------------
# UT-4: Spatial EPSG:2278 Replat Resolver & Mass-Demolition Circuit Breaker
# ---------------------------------------------------------------------------


def test_ut4_spatial_replat_resolver_and_mass_demolition_circuit_breaker(tmp_path: Path) -> None:
    roll_dir = tmp_path / "roll_ut4"

    # Case A: 1 -> 1 simple account renumbering (same building intact)
    p_a_wgs = make_wgs84_box(-95.3800, 29.7550, half_w_deg=0.00020, half_h_deg=0.00020)
    # Case B: M -> 1 lot consolidation where two parcels carrying #rear merge into one parent parcel
    p_b_merged_wgs = make_wgs84_box(-95.3770, 29.7550, half_w_deg=0.00040, half_h_deg=0.00025)
    # Case C: 1 -> 4 townhome teardown split where old pre-1940 house spanned all 4 new narrow lots
    townhome_parcels = [
        (
            f"004000000030{k}",
            wgs84_poly_to_2278(
                make_wgs84_box(-95.3733 + k * 0.00012, 29.7550, half_w_deg=0.000055, half_h_deg=0.00020)
            ),
        )
        for k in range(1, 5)
    ]
    # Case D: Partial lot carve-out where historic house stays >= 85% inside Lot D1 while vacant Lot D2 splits off
    p_d1_wgs = make_wgs84_box(-95.3690, 29.7550, half_w_deg=0.00020, half_h_deg=0.00020)
    p_d2_wgs = make_wgs84_box(-95.3685, 29.7550, half_w_deg=0.00015, half_h_deg=0.00020)

    parcel_recs = [
        ("0040000000101", wgs84_poly_to_2278(p_a_wgs)),
        ("0040000000201", wgs84_poly_to_2278(p_b_merged_wgs)),
        *townhome_parcels,
        ("0040000000401", wgs84_poly_to_2278(p_d1_wgs)),
        ("0040000000402", wgs84_poly_to_2278(p_d2_wgs)),
    ]
    acct_rows = [
        {"acct": "0040000000101", "mailto": "JONES FAMILY", "site_addr_1": "101 OAK ST", "str_num": "101", "str": "OAK", "str_sfx": "ST", "yr_impr": "1926", "bld_ar": "1800"},
        {"acct": "0040000000201", "mailto": "ESTATE HOLDINGS", "site_addr_1": "201 OAK ST", "str_num": "201", "str": "OAK", "str_sfx": "ST", "yr_impr": "1935", "bld_ar": "3600", "land_ar": "12000"},
        *[
            {"acct": f"004000000030{k}", "mailto": "TOWNHOME DEV LLC", "site_addr_1": f"930{k} ASH ST", "str_num": f"930{k}", "str": "ASH", "str_sfx": "ST", "yr_impr": "2027", "bld_ar": "2400", "land_ar": "1600"}
            for k in range(1, 5)
        ],
        {"acct": "0040000000401", "mailto": "HISTORIC KEEPER", "site_addr_1": "401 OAK ST", "str_num": "401", "str": "OAK", "str_sfx": "ST", "yr_impr": "1918", "bld_ar": "2100", "land_ar": "5000"},
        {"acct": "0040000000402", "mailto": "SIDE LOT BUYER", "site_addr_1": "403 OAK ST", "str_num": "403", "str": "OAK", "str_sfx": "ST", "state_class": "C1", "yr_impr": "", "bld_ar": "0", "bld_val": "0", "land_ar": "3500"},
    ]
    bldg_rows = [
        {"acct": "0040000000101", "bld_num": "1", "date_erected": "1926", "dscr": "Avg", "structure_dscr": "SFR", "property_use_cd": "A1", "im_sq_ft": "1800"},
        {"acct": "0040000000201", "bld_num": "1", "date_erected": "1935", "dscr": "Avg", "structure_dscr": "SFR", "property_use_cd": "A1", "im_sq_ft": "3600"},
        *[
            {"acct": f"004000000030{k}", "bld_num": "1", "date_erected": "2027", "dscr": "Good", "structure_dscr": "Townhome", "property_use_cd": "A1", "im_sq_ft": "2400"}
            for k in range(1, 5)
        ],
        {"acct": "0040000000401", "bld_num": "1", "date_erected": "1918", "dscr": "Avg", "structure_dscr": "SFR", "property_use_cd": "A1", "im_sq_ft": "2100"},
    ]
    write_synthetic_roll_zips(
        roll_dir,
        tax_year=2027,
        real_acct_rows=acct_rows,
        building_res_rows=bldg_rows,
        parcel_records=parcel_recs,
    )
    snaps = stream_hcad_account_snapshots(
        roll_dir / "Real_building_land.zip", roll_dir / "Real_acct_owner.zip", tax_year=2027, prior_tax_year=2026
    )
    p_hcads, p_geoms, p_map, p_tree = load_parcel_geometries_2278(roll_dir / "Parcels.zip")
    ov_idx = build_override_protection_index({}, default_updated_year=2026)
    used_ids: set[str] = set()

    # A: 1 -> 1 renumber
    res_a = resolve_replat_lineage(
        "0040000000100",
        [make_building_feature("0040000000100", -95.3800, 29.7550, year_built=1926, address="101 OAK ST", owner="JONES FAMILY")],
        new_accounts=snaps,
        parcel_hcads=p_hcads,
        parcel_geoms_2278=p_geoms,
        parcel_hcad_to_idx=p_map,
        parcel_strtree=p_tree,
        tieback_map={"0040000000100": ["0040000000101"]},
        overrides_index=ov_idx,
        used_building_ids=used_ids,
        prior_tax_year=2026,
        tax_year=2027,
    )
    assert len(res_a["kept_features"]) == 1
    assert res_a["kept_features"][0]["properties"]["hcad_num"] == "0040000000101"

    # B: M -> 1 consolidation with colliding #rear suffix -> disambiguated without duplicate building_id
    res_b1 = resolve_replat_lineage(
        "0040000000298",
        [make_building_feature("0040000000298", -95.3772, 29.7550, building_id="0040000000298#rear", year_built=1935, address="201 OAK ST")],
        new_accounts=snaps,
        parcel_hcads=p_hcads,
        parcel_geoms_2278=p_geoms,
        parcel_hcad_to_idx=p_map,
        parcel_strtree=p_tree,
        tieback_map={},
        overrides_index=ov_idx,
        used_building_ids=used_ids,
        prior_tax_year=2026,
        tax_year=2027,
    )
    res_b2 = resolve_replat_lineage(
        "0040000000299",
        [make_building_feature("0040000000299", -95.3768, 29.7550, building_id="0040000000299#rear", year_built=1938, address="203 OAK ST")],
        new_accounts=snaps,
        parcel_hcads=p_hcads,
        parcel_geoms_2278=p_geoms,
        parcel_hcad_to_idx=p_map,
        parcel_strtree=p_tree,
        tieback_map={},
        overrides_index=ov_idx,
        used_building_ids=used_ids,
        prior_tax_year=2026,
        tax_year=2027,
    )
    id_b1 = res_b1["kept_features"][0]["properties"]["building_id"]
    id_b2 = res_b2["kept_features"][0]["properties"]["building_id"]
    assert id_b1 == "0040000000201#rear"
    assert id_b2 != id_b1
    assert id_b2.startswith("0040000000201#rear")

    # C: 1 -> 4 townhome split drops old wide parent footprint cleanly, frees all 4 successor lots for NEW_CONSTRUCTION, and stages Cohort 5 candidate
    wide_old_house = make_building_feature(
        "0040000000300",
        -95.3730,
        29.7550,
        year_built=1920,
        address="300 ASH ST",
        land_area=6400.0,
        half_w_deg=0.00022,
        half_h_deg=0.00012,
    )
    res_c = resolve_replat_lineage(
        "0040000000300",
        [wide_old_house],
        new_accounts=snaps,
        parcel_hcads=p_hcads,
        parcel_geoms_2278=p_geoms,
        parcel_hcad_to_idx=p_map,
        parcel_strtree=p_tree,
        tieback_map={"0040000000300": [f"004000000030{k}" for k in range(1, 5)]},
        overrides_index=ov_idx,
        used_building_ids=used_ids,
        prior_tax_year=2026,
        tax_year=2027,
    )
    assert res_c["kept_features"] == []
    assert res_c["dropped_townhome_splits"] == 1
    assert res_c["covered_new_hcads"] == set()  # All 4 child townhome lots remain uncovered -> NEW_CONSTRUCTION!
    assert len(res_c["cohort5_candidates"]) == 1
    assert res_c["cohort5_candidates"][0]["hcad_num"] == "0040000000300"

    # D: Partial lot carve-out preserves standing 1918 house inside Lot D1
    res_d = resolve_replat_lineage(
        "0040000000400",
        [make_building_feature("0040000000400", -95.3690, 29.7550, year_built=1918, address="401 OAK ST", land_area=8500.0)],
        new_accounts=snaps,
        parcel_hcads=p_hcads,
        parcel_geoms_2278=p_geoms,
        parcel_hcad_to_idx=p_map,
        parcel_strtree=p_tree,
        tieback_map={"0040000000400": ["0040000000401", "0040000000402"]},
        overrides_index=ov_idx,
        used_building_ids=used_ids,
        prior_tax_year=2026,
        tax_year=2027,
    )
    assert len(res_d["kept_features"]) == 1
    assert res_d["kept_features"][0]["properties"]["hcad_num"] == "0040000000401"
    assert res_d["kept_features"][0]["properties"]["year_built"] == 1918

    # E: > 2.5% mass-demolition circuit breaker triggers RollValidationError cleanly
    demo_baseline: dict[str, list[dict[str, Any]]] = {q: [] for q in QUAD_NAMES}
    demo_snaps = {}
    demo_parcels = []
    demo_accts = []
    for i in range(1, 21):
        hc = f"00490000000{i:02d}"
        g_wgs = make_wgs84_box(-95.4000 + i * 0.0005, 29.7600)
        demo_baseline["sw"].append(make_building_feature(hc, -95.4000 + i * 0.0005, 29.7600, year_built=1950))
        demo_parcels.append((hc, wgs84_poly_to_2278(g_wgs)))
        is_demo = i <= 5  # 5/20 = 25% demolished -> far above 2.5% threshold
        demo_accts.append(
            {
                "acct": hc,
                "str_num": str(i),
                "str": "DEMO",
                "state_class": "C1" if is_demo else "A1",
                "yr_impr": "" if is_demo else "1950",
                "bld_ar": "0" if is_demo else "1500",
                "bld_val": "0" if is_demo else "180000",
            }
        )
    cb_dir = tmp_path / "cb_roll"
    write_synthetic_roll_zips(cb_dir, tax_year=2027, real_acct_rows=demo_accts, parcel_records=demo_parcels)
    demo_snaps = stream_hcad_account_snapshots(
        cb_dir / "Real_building_land.zip", cb_dir / "Real_acct_owner.zip", tax_year=2027, prior_tax_year=2026
    )
    dp_hcads, dp_geoms, dp_map, dp_tree = load_parcel_geometries_2278(cb_dir / "Parcels.zip")
    with pytest.raises(RollValidationError, match="demolition circuit breaker|DEMOLISHED_TO_VACANT"):
        classify_feature_delta(
            demo_baseline,
            demo_snaps,
            parcel_hcads=dp_hcads,
            parcel_geoms_2278=dp_geoms,
            parcel_hcad_to_idx=dp_map,
            parcel_strtree=dp_tree,
            tieback_map={},
            curated_overrides={},
            tax_year=2027,
            prior_tax_year=2026,
            sanity_cfg=RollSanityConfig(max_annual_demolition_fraction=0.025),
        )


# ---------------------------------------------------------------------------
# UT-5: Oriented Synthetic Footprint Generation & 4-Phase Undated Fallback
# ---------------------------------------------------------------------------


def test_ut5_oriented_footprint_synthesis_and_shared_undated_fallback(tmp_path: Path) -> None:
    roll_dir = tmp_path / "roll_ut5"
    out_dir = tmp_path / "out_ut5"
    cache_dir = tmp_path / "cache_ut5"
    out_dir.mkdir()
    cache_dir.mkdir()

    # Build a downtown parcel rotated along Borden -34.65 deg grid + a non-Borden suburban parcel
    # + a multi-building new apartment parcel (2 observed footprints) + an undated new parcel sitting next to dated neighbors
    p1_wgs = make_wgs84_box(-95.3635, 29.7580, half_w_deg=0.00020, half_h_deg=0.00022)
    p2_wgs = make_wgs84_box(-95.4800, 29.8200, half_w_deg=0.00025, half_h_deg=0.00020)
    p3_wgs = make_wgs84_box(-95.4780, 29.8200, half_w_deg=0.00035, half_h_deg=0.00025)
    p4_wgs = make_wgs84_box(-95.4760, 29.8200, half_w_deg=0.00022, half_h_deg=0.00022)

    parcel_recs = [
        ("0050000000001", wgs84_poly_to_2278(p1_wgs)),
        ("0050000000002", wgs84_poly_to_2278(p2_wgs)),
        ("0050000000003", wgs84_poly_to_2278(p3_wgs)),
        ("0050000000004", wgs84_poly_to_2278(p4_wgs)),
    ]
    write_synthetic_roll_zips(
        roll_dir,
        tax_year=2027,
        real_acct_rows=[
            {"acct": "0050000000001", "mailto": "DOWNTOWN BUILDER", "site_addr_1": "810 TRAVIS ST", "str_num": "810", "str": "TRAVIS", "str_sfx": "ST", "yr_impr": "2027", "bld_ar": "3200", "land_ar": "5000"},
            {"acct": "0050000000002", "mailto": "OAK FOREST HOMES", "site_addr_1": "1402 ELLA BLVD", "str_num": "1402", "str": "ELLA", "str_sfx": "BLVD", "yr_impr": "2027", "bld_ar": "2400", "land_ar": "6500"},
            {"acct": "0050000000003", "mailto": "GARDEN APTS LLC", "site_addr_1": "1410 ELLA BLVD", "str_num": "1410", "str": "ELLA", "str_sfx": "BLVD", "state_class": "B1", "yr_impr": "2027", "bld_ar": "9000", "land_ar": "15000"},
            # Undated newly constructed structure (yr_impr=0) to exercise shared Phases 2A-4 fallback!
            {"acct": "0050000000004", "mailto": "OAK FOREST HOMES", "site_addr_1": "1420 ELLA BLVD", "str_num": "1420", "str": "ELLA", "str_sfx": "BLVD", "yr_impr": "0", "bld_ar": "2100", "land_ar": "6000", "lgl_2": "OAK FOREST SEC 2"},
        ],
        building_res_rows=[
            {"acct": "0050000000001", "bld_num": "1", "date_erected": "2027", "dscr": "Good", "structure_dscr": "Townhome", "property_use_cd": "A1", "im_sq_ft": "3200"},
            {"acct": "0050000000002", "bld_num": "1", "date_erected": "2027", "dscr": "Good", "structure_dscr": "SFR", "property_use_cd": "A1", "im_sq_ft": "2400"},
            {"acct": "0050000000003", "bld_num": "1", "date_erected": "2027", "dscr": "Good", "structure_dscr": "Apt Bldg A", "property_use_cd": "B1", "im_sq_ft": "4500"},
            {"acct": "0050000000003", "bld_num": "2", "date_erected": "2027", "dscr": "Good", "structure_dscr": "Apt Bldg B", "property_use_cd": "B1", "im_sq_ft": "4500"},
            {"acct": "0050000000004", "bld_num": "1", "date_erected": "0", "dscr": "Avg", "structure_dscr": "SFR", "property_use_cd": "A1", "im_sq_ft": "2100"},
        ],
        parcel_records=parcel_recs,
    )

    obs_ndjson = tmp_path / "observed_2027.geojsonseq"
    obs_feats = [
        {"type": "Feature", "properties": {"hcad_num": "0050000000003"}, "geometry": make_wgs84_box(-95.47815, 29.8200, half_w_deg=0.00008, half_h_deg=0.00008)},
        {"type": "Feature", "properties": {"hcad_num": "0050000000003"}, "geometry": make_wgs84_box(-95.47785, 29.8200, half_w_deg=0.00008, half_h_deg=0.00008)},
    ]
    obs_ndjson.write_text("\n".join(json.dumps(of) for of in obs_feats) + "\n", encoding="utf-8")

    snaps = stream_hcad_account_snapshots(
        roll_dir / "Real_building_land.zip", roll_dir / "Real_acct_owner.zip", tax_year=2027, prior_tax_year=2026
    )
    p_hcads, p_geoms, p_map, p_tree = load_parcel_geometries_2278(roll_dir / "Parcels.zip")

    patched: dict[str, list[dict[str, Any]]] = {q: [] for q in QUAD_NAMES}
    # Add an authoritative neighbor and one fallback-inferred neighbor to verify fallback donors
    # NEVER contaminate subsequent fallback inference!
    patched["nw_w"].append(
        make_building_feature(
            "0050000000090",
            -95.4758,
            29.8200,
            year_built=1954,
            year_source="hcad_cama",
            address="1418 ELLA BLVD",
            owner="LEGACY NEIGHBOR",
        )
    )
    patched["nw_w"].append(
        make_building_feature(
            "0050000000091",
            -95.4761,
            29.8200,
            year_built=1850,
            year_source="spatial_block_median",  # Non-authoritative source must be ignored as donor!
            address="1422 ELLA BLVD",
            owner="OAK FOREST HOMES",
        )
    )
    delta = ArtifactSyncDelta()
    delta.new_construction_hcads.update({"0050000000001", "0050000000002", "0050000000003", "0050000000004"})
    used_ids: set[str] = set()

    stats = synthesize_new_construction_features(
        patched,
        delta,
        snaps,
        parcel_hcads=p_hcads,
        parcel_geoms_2278=p_geoms,
        parcel_hcad_to_idx=p_map,
        parcel_strtree=p_tree,
        bldg_zip_path=roll_dir / "Real_building_land.zip",
        acct_zip_path=roll_dir / "Real_acct_owner.zip",
        cache_dir=cache_dir,
        tax_year=2027,
        observed_footprints_ndjson=obs_ndjson,
        used_building_ids=used_ids,
    )
    assert stats["synthesized_footprints"] == 5  # 1 + 1 + 2 (observed) + 1
    assert stats["observed_footprints_matched"] == 2
    assert stats["undated_parcels_resolved"] == 1
    all_new = [
        f
        for fl in patched.values()
        for f in fl
        if f["properties"]["hcad_num"] in delta.new_construction_hcads
    ]
    assert len(all_new) == 5
    min_lng, min_lat, max_lng, max_lat = HARRIS_COUNTY_BBOX
    for feat in all_new:
        pr = feat["properties"]
        assert pr["year_built"] >= MIN_VALID_YEAR  # unknown == 0 strictly guaranteed!
        assert isinstance(pr["decade"], int) and pr["decade"] == compute_decade(pr["year_built"])
        if pr["hcad_num"] == "0050000000003":
            assert pr["geometry_source"] == "observed_footprint"
            assert pr["is_footprint"] is True
        else:
            assert pr["geometry_source"] == "derived_parcel_oriented"
            assert pr["is_footprint"] is False
        ring = feat["geometry"]["coordinates"][0]
        assert ring[0] == ring[-1]
        poly = shape(feat["geometry"])
        assert poly.is_valid and poly.exterior.is_ccw
        for x, y in ring:
            assert min_lng <= x <= max_lng and min_lat <= y <= max_lat
            sx = f"{x:.10f}".rstrip("0").rstrip(".")
            sy = f"{y:.10f}".rstrip("0").rstrip(".")
            assert len(sx.split(".")[1]) <= 6 if "." in sx else True
            assert len(sy.split(".")[1]) <= 6 if "." in sy else True
        # Inside parent parcel boundary (setback respected within 6dp rounding epsilon)
        p_poly_2278 = p_geoms[p_map[pr["hcad_num"]]]
        fp_2278 = wgs84_poly_to_2278(feat["geometry"])
        assert p_poly_2278.buffer(0.5).covers(fp_2278)

    # Multi-building parcel 0050000000003 generated multiple distinct building_ids
    p3_ids = [f["properties"]["building_id"] for f in all_new if f["properties"]["hcad_num"] == "0050000000003"]
    assert p3_ids == ["0050000000003", "0050000000003#n2"]

    # Undated parcel 0050000000004 was resolved cleanly without inheriting 1850 from spatial_block_median donor
    p4_feat = next(f for f in all_new if f["properties"]["hcad_num"] == "0050000000004")
    assert p4_feat["properties"]["year_built"] != 1850
    assert p4_feat["properties"]["year_built"] >= 1950


# ---------------------------------------------------------------------------
# UT-6: Historical Accuracy Audit Dynamic Tax-Year Propagation
# ---------------------------------------------------------------------------


def test_ut6_historical_audit_dynamic_tax_year_semantics(tmp_path: Path) -> None:
    assert parse_dos_yyyymmdd("06/15/2028", "2028", max_year=2028) == "20280615"
    assert parse_dos_yyyymmdd("2030-03-01", "2030", max_year=2030) == "20300301"
    assert parse_dos_yyyymmdd("01/01/1900", "", max_year=2028) == "00000000"
    assert parse_dos_yyyymmdd("12/30/1899", "2028", max_year=2028) == "20280000"

    cache_dir = tmp_path / "audit_roll_2028"
    data_dir = tmp_path / "audit_data_2028"
    data_dir.mkdir()
    ctx_2028 = RollContext(tax_year=2028, cache_dir=cache_dir)
    ctx_2028.staged_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "aligned_ndjson").mkdir(parents=True, exist_ok=True)

    write_synthetic_roll_zips(
        cache_dir,
        tax_year=2028,
        real_acct_rows=[
            {
                "acct": "0060000000001",
                "yr": "2028",
                "mailto": "ROUNDED OWNER",
                "site_addr_1": "215 HEIGHTS BLVD",
                "str_num": "215",
                "str": "HEIGHTS",
                "str_sfx": "BLVD",
                "yr_impr": "1920",
                "bld_ar": "1900",
                "New_Own_Dt": "04/01/2028",
            }
        ],
        building_res_rows=[
            {
                "acct": "0060000000001",
                "bld_num": "1",
                "date_erected": "1920",
                "yr_remodel": "2028",
                "dscr": "Avg",
                "structure_dscr": "SFR",
                "property_use_cd": "A1",
                "im_sq_ft": "1900",
            }
        ],
        deeds_rows=[
            {"acct": "0060000000001", "dos": "03/10/1912", "clerk_yr": "1912", "clerk_id": "12345"},
            {"acct": "0060000000001", "dos": "04/01/2028", "clerk_yr": "2028", "clerk_id": "202899999"},
        ],
    )

    snaps_2028 = stream_hcad_account_snapshots(
        cache_dir / "Real_building_land.zip",
        cache_dir / "Real_acct_owner.zip",
        tax_year=2028,
        prior_tax_year=2027,
    )
    assert snaps_2028["0060000000001"].max_remodel_year == 2028

    # Seed minimal data files for run_audit, including a Lost Houston record demolished in 2028
    feat = make_building_feature(
        "0060000000001",
        -95.3980,
        29.7850,
        year_built=1920,
        address="215 HEIGHTS BLVD",
        extra_props={"historic_district": "Houston Heights East", "contributing": "Contributing"},
    )
    write_compact_json(data_dir / "buildings.geojson", {"type": "FeatureCollection", "features": [feat]})
    write_compact_json(
        data_dir / "overlays.json",
        {
            "landmarks": {"type": "FeatureCollection", "features": []},
            "good_brick_awards": {"type": "FeatureCollection", "features": []},
            "nrhp_properties": {"type": "FeatureCollection", "features": []},
            "thc_markers": {"type": "FeatureCollection", "features": []},
            "lost_houston": {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": make_wgs84_box(-95.3970, 29.7855, half_w_deg=0.0001, half_h_deg=0.0001),
                        "properties": {
                            "id": "lost_test_2028_house",
                            "name": "Historic 1908 Bungalow",
                            "address": "225 HEIGHTS BLVD",
                            "hcad_num": "0060000000099",
                            "year_built": 1908,
                            "year_demolished": 2028,
                            "source_cohort": "historic_teardown_2020_2026",
                            "symbol_rank": 3,
                            "sanborn_material": "wood_frame",
                            "replaced_by": "Vacant lot (2028 HCAD CAMA)",
                            "historic_significance": "Documented 2028 HCAD CAMA teardown.",
                            "source_url": "https://hcad.org/",
                        },
                    }
                ],
            },
        },
    )
    write_compact_json(
        data_dir / "citydir_index.json",
        {
            "by_hcad": {
                "0060000000001": {
                    "e": 1911,
                    "y": [1911, 1915],
                    "p": 10,
                    "a": "215 HEIGHTS BLVD",
                    "o": "J. P. SMITH",
                }
            }
        },
    )
    write_compact_json(data_dir / "haif_index.json", {"names_by_key": {}, "names_by_addr": {}})
    write_compact_json(data_dir / "curated_overrides.json", {"schema_version": 2, "overrides": {}})
    write_compact_json(data_dir / "search_index.json", [])

    res = run_audit(
        apply_fixes=False,
        tax_year=2028,
        cache_dir=cache_dir,
        data_dir=data_dir,
        generated_on="2028-09-01",
    )
    queue_doc = res["queue"] if "queue" in res else res
    assert queue_doc["generated_on"] == "2028-09-01"
    if "summary" in res:
        assert res["summary"]["reconciled_fixes"].get("lost_houston_schema_errors", 0) == 0
    fids = [it["fid"] for it in queue_doc["items"]]
    assert len(fids) == len(set(fids))
    assert all(isinstance(fid, str) and len(fid) >= 8 for fid in fids)


# ---------------------------------------------------------------------------
# UT-7: Neighborhood Scorecards & Ch. 42 Horizon Across 2027 & 2030
# ---------------------------------------------------------------------------


def test_ut7_scorecards_and_ch42_dynamic_horizon_2027_and_2030(tmp_path: Path) -> None:
    assert list(compute_demo_years(2027)) == [2020, 2021, 2022, 2023, 2024, 2025, 2026, 2027]
    assert list(compute_demo_years(2030))[-1] == 2030
    assert compute_decade_bins(2027)[-1] == 2020
    assert compute_decade_bins(2030)[-1] == 2030
    lc_2030 = resolve_ch42_ordinance_lifecycle(
        ordinance="Ord. 2010-333",
        effective_date="2010-05-01",
        protection_type="smls",
        ref_year=2027,
    )
    assert lc_2030["expiration_year"] == 2030
    assert lc_2030["sunset_status"] == "expiring_soon"

    for test_yr in (2027, 2030):
        sc_cache = tmp_path / f"sc_cache_{test_yr}"
        sc_data = tmp_path / f"sc_data_{test_yr}"
        (sc_cache / "aligned_ndjson").mkdir(parents=True)
        sc_data.mkdir(parents=True)

        p_poly_wgs = make_wgs84_box(-95.3900, 29.7800, half_w_deg=0.0005, half_h_deg=0.0005)
        dist_poly_wgs = make_wgs84_box(-95.3900, 29.7800, half_w_deg=0.0020, half_h_deg=0.0020)
        write_synthetic_roll_zips(
            sc_cache,
            tax_year=test_yr,
            real_acct_rows=[
                {
                    "acct": "0070000000001",
                    "yr": str(test_yr),
                    "mailto": "SMITH ROBERT",
                    "site_addr_1": "500 HEIGHTS BLVD",
                    "site_addr_2": "HOUSTON",
                    "str_num": "500",
                    "str": "HEIGHTS",
                    "str_sfx": "BLVD",
                    "state_class": "A1",
                    "yr_impr": "1922",
                    "bld_ar": "1800",
                    "land_ar": "6600",
                    "lgl_1": "LOT 1 BLK 2",
                    "lgl_2": "TEST HEIGHTS SEC 1",
                },
                {
                    "acct": "0070000000002",
                    "yr": str(test_yr),
                    "mailto": "INFILL BUYER LLC",
                    "site_addr_1": "504 HEIGHTS BLVD",
                    "str_num": "504",
                    "str": "HEIGHTS",
                    "str_sfx": "BLVD",
                    "state_class": "A1",
                    "yr_impr": str(test_yr),
                    "bld_ar": "3100",
                    "land_ar": "6600",
                    "lgl_1": "LOT 2 BLK 2",
                    "lgl_2": "TEST HEIGHTS SEC 1",
                },
            ],
            building_res_rows=[
                {"acct": "0070000000001", "bld_num": "1", "date_erected": "1922", "dscr": "Avg", "structure_dscr": "SFR", "property_use_cd": "A1", "im_sq_ft": "1800"},
                {"acct": "0070000000002", "bld_num": "1", "date_erected": str(test_yr), "dscr": "Good", "structure_dscr": "SFR", "property_use_cd": "A1", "im_sq_ft": "3100"},
            ],
            permits_rows=[
                {
                    "acct": "0070000000002",
                    "permit_type": "13",
                    "permit_tp_descr": "Demolition",
                    "dscr": "DEMOLISH SFR HOUSE",
                    "issue_date": f"02/14/{test_yr}",
                    "yr": str(test_yr),
                    "site_num": "504",
                    "site_str": "HEIGHTS",
                    "site_tp": "BLVD",
                }
            ],
            parcel_records=[
                ("0070000000001", wgs84_poly_to_2278(make_wgs84_box(-95.3903, 29.7800))),
                ("0070000000002", wgs84_poly_to_2278(make_wgs84_box(-95.3897, 29.7800))),
            ],
        )

        # Write buildings NDJSON shard (1922 standing + test_yr standing inside district)
        f1 = make_building_feature("0070000000001", -95.3903, 29.7800, year_built=1922, address="500 HEIGHTS BLVD")
        f2 = make_building_feature("0070000000002", -95.3897, 29.7800, year_built=test_yr, address="504 HEIGHTS BLVD")
        with open(sc_cache / "aligned_ndjson" / "buildings_nw_e.geojsonseq", "wb") as fh:
            for ft in (f1, f2):
                fh.write(b"\x1e" + json.dumps(ft).encode("utf-8") + b"\n")
        for q_empty in ("nw_w", "ne", "sw", "se"):
            (sc_cache / "aligned_ndjson" / f"buildings_{q_empty}.geojsonseq").write_bytes(b"")

        # Write all 11 council districts (A-K) GeoJSON + overlays + search_index + overrides
        cd_features = []
        for idx_cd, letter in enumerate("ABCDEFGHIJK"):
            geom_cd = (
                dist_poly_wgs
                if letter == "C"
                else make_wgs84_box(-95.55 + idx_cd * 0.01, 29.65, half_w_deg=0.0020, half_h_deg=0.0020)
            )
            cd_features.append(
                {
                    "type": "Feature",
                    "geometry": geom_cd,
                    "properties": {"DISTRICT": letter},
                }
            )
        write_compact_json(
            sc_cache / "coh_council_districts_2024.geojson",
            {"type": "FeatureCollection", "features": cd_features},
        )
        empty_fc = {"type": "FeatureCollection", "features": []}
        overlays = {
            k: dict(empty_fc)
            for k in (
                "landmarks",
                "historic_districts",
                "heritage_districts",
                "nrhp_districts",
                "nrhp_properties",
                "thc_markers",
                "annexations",
                "good_brick_awards",
                "neighborhoods",
                "super_neighborhoods",
                "council_districts",
                "historic_wards",
                "platted_subdivisions",
                "land_use_protections",
                "lost_houston",
            )
        }
        overlays["historic_districts"] = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": dist_poly_wgs,
                    "properties": {
                        "id": "hd_test_heights",
                        "name": "Test Heights District",
                        "designation_year": 2008,
                    },
                }
            ],
        }
        overlays["land_use_protections"] = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": p_poly_wgs,
                    "properties": {
                        "id": "smls_test_heights_1",
                        "name": "Test SMLS Block",
                        "protection_type": "smls",
                        "status": "Approved",
                        "effective_date": f"{test_yr - 18}-06-01",
                        "ordinance": f"Ord. {test_yr - 18}-400",
                    },
                }
            ],
        }
        write_compact_json(sc_data / "overlays.json", overlays)
        write_compact_json(sc_data / "search_index.json", [])
        write_compact_json(sc_data / "curated_overrides.json", {"schema_version": 2, "overrides": {}})

        payload = rebuild_scorecards_main(
            cache_dir=sc_cache,
            data_dir=sc_data,
            tax_year=test_yr,
            fetch_network=False,
        )
        assert payload["data_tax_year"] == test_yr
        assert payload["demo_years"][-1] == test_yr
        assert payload["demo_latest_year_is_ytd"] is True
        if test_yr == 2030:
            assert 2030 in payload["decade_bins"]
        hd_cards = [c for c in payload["scorecards"] if c["ly"] == "historic_districts"]
        assert len(hd_cards) == 1
        card = hd_cards[0]
        assert sum(card["dh"]) == card["bc"] == 2
        assert len(card["dm"]) == len(payload["demo_years"])
        assert sum(card["dm"]) == card["dmt"] == 1
        assert card["dm"][-1] == 1  # test_yr demo recorded cleanly without KeyError!
        # Ch. 42 protection enacted (test_yr - 18) expires in (test_yr + 2), inside [test_yr, test_yr + 5] watch window!
        assert card["chx"] >= 1
        if test_yr == 2030:
            idx_2030 = payload["decade_bins"].index(2030)
            assert card["dh"][idx_2030] == 1


# ---------------------------------------------------------------------------
# UT-8: Manifest v3, AnchorRule Lockstep Sync & Deed Chain Determinism
# ---------------------------------------------------------------------------


def test_ut8_manifest_v3_anchor_rule_sync_and_deed_truncation(tmp_path: Path) -> None:
    # 1. update_pmtiles_manifest_v3 preserves version 3 & core_pmtiles while updating year/count/cache_bust
    m_path = tmp_path / "pmtiles_manifest.json"
    m_path.write_text(
        json.dumps(
            {
                "version": 3,
                "cache_bust": "20261030e",
                "data_tax_year": 2026,
                "total_buildings": 1507897,
                "building_shards": ["houston_buildings_nw_w.pmtiles"],
                "core_pmtiles": "houston_atlas.pmtiles",
            }
        ),
        encoding="utf-8",
    )
    updated_m = update_pmtiles_manifest_v3(
        m_path,
        building_shards=["houston_buildings_nw_w.pmtiles", "houston_buildings_se.pmtiles"],
        total_buildings=1521000,
        data_tax_year=2027,
        cache_bust="20270901a",
    )
    assert updated_m["version"] == 3
    assert updated_m["data_tax_year"] == 2027
    assert updated_m["cache_bust"] == "20270901a"
    assert updated_m["total_buildings"] == 1521000
    assert updated_m["core_pmtiles"] == "houston_atlas.pmtiles"

    # 2. AnchorRule lockstep sync updates real repo file copies for both 2027 and 2030 (injecting 2030s Decade once)
    repo_root_real = Path(__file__).resolve().parents[2]
    fake_repo = tmp_path / "repo_copy"
    rel_files = [
        "pipeline/atlas_pipeline/schema.py",
        "app/js/filterStore.js",
        "app/js/curatedEdits.js",
        "app/js/preservationOrganizer.js",
        "app/js/mapController.js",
        "app/js/app.js",
        "app/js/palettes.js",
        "app/js/photoService.js",
        "app/js/staffWorkbench.js",
        "app/js/guidedChaptersAndPortfolios.js",
        "app/index.html",
        "app/plat-monitor.html",
        "tests/unit/data_integrity_and_bundle.test.mjs",
    ]
    for rel in rel_files:
        src = repo_root_real / rel
        dst = fake_repo / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    # Dry-run leaves all files untouched
    before_hashes = {
        rel: hashlib.sha256((fake_repo / rel).read_bytes()).hexdigest() for rel in rel_files
    }
    dry_res = sync_repo_tax_year_and_cache_bust(
        fake_repo, tax_year=2027, cache_bust="20270815a", dry_run=True
    )
    assert dry_res["dry_run"] is True
    for rel in rel_files:
        assert hashlib.sha256((fake_repo / rel).read_bytes()).hexdigest() == before_hashes[rel]

    # Apply 2027 + cache_bust
    sync_repo_tax_year_and_cache_bust(fake_repo, tax_year=2027, cache_bust="20270815a", dry_run=False)
    assert 'ACTIVE_TAX_YEAR = 2027' in (fake_repo / "pipeline/atlas_pipeline/schema.py").read_text(encoding="utf-8")
    assert 'ACTIVE_CACHE_BUST = "20270815a"' in (fake_repo / "pipeline/atlas_pipeline/schema.py").read_text(encoding="utf-8")
    assert "export const DEFAULT_ATLAS_MAX_YEAR = 2027;" in (fake_repo / "app/js/filterStore.js").read_text(encoding="utf-8")
    assert "export const MAX_VALID_YEAR = 2027;" in (fake_repo / "app/js/curatedEdits.js").read_text(encoding="utf-8")
    assert ACTIVE_CACHE_BUST not in (fake_repo / "app/js/app.js").read_text(encoding="utf-8")
    assert "?v=20270815a" in (fake_repo / "app/js/photoService.js").read_text(encoding="utf-8")
    html_2027 = (fake_repo / "app/index.html").read_text(encoding="utf-8")
    assert re.search(r'id="slider-min-year"[^>]*max="2027"', html_2027, re.DOTALL)
    assert re.search(r'id="slider-max-year"[^>]*max="2027"\s+value="2027"', html_2027, re.DOTALL)
    assert '(1836-2027, or <code>0</code> if vacant)' in html_2027
    assert '<option value="2030">' not in html_2027

    # Apply 2030 -> inserts <option value="2030">2030s Decade</option> before unknown, idempotently on rerun
    sync_repo_tax_year_and_cache_bust(fake_repo, tax_year=2030, cache_bust="20300815a", dry_run=False)
    sync_repo_tax_year_and_cache_bust(fake_repo, tax_year=2030, cache_bust="20300815a", dry_run=False)
    html_2030 = (fake_repo / "app/index.html").read_text(encoding="utf-8")
    assert html_2030.count('<option value="2030">2030s Decade</option>') == 1
    assert html_2030.index('<option value="2030">2030s Decade</option>') < html_2030.index(
        '<option value="unknown">'
    )

    # Stale anchor aborts atomically BEFORE writing ANY file
    snap_2030 = {rel: (fake_repo / rel).read_bytes() for rel in rel_files}
    (fake_repo / "app/js/filterStore.js").write_text("// broken anchor\n", encoding="utf-8")
    broken_snap = {rel: (fake_repo / rel).read_bytes() for rel in rel_files}
    with pytest.raises(ValueError, match="filterStore.js|Anchor"):
        sync_repo_tax_year_and_cache_bust(fake_repo, tax_year=2031, cache_bust="20310815a", dry_run=False)
    for rel in rel_files:
        assert (fake_repo / rel).read_bytes() == broken_snap[rel]
    assert snap_2030["app/index.html"] == broken_snap["app/index.html"]

    # 3. Deed instrument normalization, multi-entry truncation determinism, and frontend permit keys
    assert parse_dos_yyyymmdd("07/04/2027", "2027", max_year=2027) == "20270704"
    assert parse_dos_yyyymmdd("01/01/1900", "", max_year=2027) == "00000000"
    assert MAX_DEEDS_PER_PARCEL == 8
    inst = normalize_hcad_clerk_instrument("2027", "RP-2027-123456", max_year=2027)
    assert inst == "R2027-123456"

    deed_roll = tmp_path / "deed_roll"
    deeds_10 = [
        {"acct": "0088000000001", "dos": "01/01/1900", "clerk_yr": "2027", "clerk_id": "RP-2027-900001"},
        {"acct": "0088000000001", "dos": "07/04/2027", "clerk_yr": "2027", "clerk_id": "RP-2027-900001"},
    ]
    for idx_y, yr_d in enumerate((2026, 2024, 2022, 2020, 2019, 2018, 2017, 2015, 2012), start=2):
        cid_val = f"RP-{yr_d}-{900000 + idx_y}" if yr_d >= 2017 else f"{100000 + idx_y:07d}"
        deeds_10.append(
            {
                "acct": "0088000000001",
                "dos": f"05/{10 + idx_y:02d}/{yr_d}",
                "clerk_yr": str(yr_d),
                "clerk_id": cid_val,
            }
        )
    write_synthetic_roll_zips(
        deed_roll,
        real_acct_rows=[
            {
                "acct": "0088000000001",
                "yr": "2027",
                "mailto": "OWNER ONE",
                "site_addr_1": "500 MAIN ST",
                "str_num": "500",
                "str": "MAIN ST",
                "state_class": "A1",
                "yr_impr": "1925",
                "bld_ar": "2100",
                "land_ar": "5000",
                "certified_date": "08/01/2027",
            }
        ],
        building_res_rows=[
            {
                "acct": "0088000000001",
                "bld_num": "1",
                "date_erected": "1925",
                "yr_remodel": "2021",
                "im_sq_ft": "2100",
            }
        ],
        permits_rows=[
            {
                "acct": "0088000000001",
                "permit_type": "05",
                "permit_tp_descr": "RESIDENTIAL REMODEL",
                "dscr": "INTERIOR KITCHEN REMODEL",
                "issue_date": "06/15/2027",
                "yr": "2027",
                "id": "PRM2027001",
            }
        ],
        deeds_rows=deeds_10,
        parcel_records=[
            (
                "0088000000001",
                wgs84_poly_to_2278(make_wgs84_box(-95.365, 29.758)),
            )
        ],
    )
    snaps_d = stream_hcad_account_snapshots(
        deed_roll / "Real_building_land.zip",
        deed_roll / "Real_acct_owner.zip",
        tax_year=2027,
        prior_tax_year=2026,
    )
    enr_1 = _stream_targeted_enrichment_records(
        {"0088000000001"},
        snaps_d,
        deed_roll / "Real_building_land.zip",
        deed_roll / "Real_acct_owner.zip",
        tax_year=2027,
    )
    enr_2 = _stream_targeted_enrichment_records(
        {"0088000000001"},
        snaps_d,
        deed_roll / "Real_building_land.zip",
        deed_roll / "Real_acct_owner.zip",
        tax_year=2027,
    )
    assert enr_1 == enr_2
    rec_d = enr_1["0088000000001"]
    deed_tokens = rec_d["d"].split("|")
    assert len(deed_tokens) == MAX_DEEDS_PER_PARCEL == 8
    assert deed_tokens[0] == "20270704:R2027-900001"
    assert "00000000" not in rec_d["d"]
    assert rec_d["p"][0]["id"] == "PRM2027001"
    assert rec_d["p"][0]["cat"] == "remodel"
    assert rec_d["p"][0]["dt"] == "2027-06-15"


def test_ut6_sibling_scripts_tax_exact_year_propagation() -> None:
    # Verify evaluate_cohort5_teardown_candidate and build_cohorts_2_to_5 accept 2027 cleanly
    ok_2027, meta_2027 = evaluate_cohort5_teardown_candidate(
        "0080000000001",
        permit_description="DEMOLISH HOUSE",
        permit_type="13",
        permit_issue_date="03/15/2027",
        permit_address="912 HEIGHTS BLVD",
        cama_year_built=2027,
        cama_bld_area=3200.0,
        cama_state_class="A1",
        pre_demo_survey_year=1924,
        max_year=2027,
    )
    assert ok_2027 is True
    assert meta_2027["year_demolished"] == 2027
    # Without max_year=2027 when baseline max_year=2026, a 2027 permit is rejected as out-of-range
    ok_2026_cap, _ = evaluate_cohort5_teardown_candidate(
        "0080000000001",
        permit_description="DEMOLISH HOUSE",
        permit_type="13",
        permit_issue_date="03/15/2027",
        permit_address="912 HEIGHTS BLVD",
        cama_year_built=2027,
        cama_bld_area=3200.0,
        cama_state_class="A1",
        pre_demo_survey_year=1924,
        max_year=2026,
    )
    assert ok_2026_cap is False
    assert callable(build_cohorts_2_to_5)
    assert callable(enrich_institutions_main)
    assert callable(build_hcad_permits_and_cama_index)
    assert callable(resolve_undated_feature_batch)


# ---------------------------------------------------------------------------
# DEEP Horizon Integration Tests (IT-1 through IT-7)
# ---------------------------------------------------------------------------


def _snapshot_tree_sha256(root: Path, *, exclude_rel_prefixes: tuple[str, ...] = ()) -> dict[str, str]:
    """Return a deterministic relative-path -> SHA-256 map across every file under root."""
    out: dict[str, str] = {}
    if not root.exists():
        return out
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if any(rel == pref or rel.startswith(pref.rstrip("/") + "/") for pref in exclude_rel_prefixes):
            continue
        out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _copy_repo_sync_skeleton(fake_repo: Path) -> None:
    """Copy real repository AnchorRule target files into fake_repo so --apply lockstep sync runs safely."""
    repo_root_real = Path(__file__).resolve().parents[2]
    rel_files = [
        "pipeline/atlas_pipeline/schema.py",
        "app/js/filterStore.js",
        "app/js/curatedEdits.js",
        "app/js/preservationOrganizer.js",
        "app/js/mapController.js",
        "app/js/app.js",
        "app/js/palettes.js",
        "app/js/photoService.js",
        "app/js/staffWorkbench.js",
        "app/js/guidedChaptersAndPortfolios.js",
        "app/index.html",
        "app/plat-monitor.html",
        "tests/unit/data_integrity_and_bundle.test.mjs",
    ]
    for rel in rel_files:
        src = repo_root_real / rel
        dst = fake_repo / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")


def build_mini_county_workspace(
    base_dir: Path,
    *,
    baseline_year: int = 2026,
    next_year: int = 2027,
) -> dict[str, Any]:
    """Create a complete multi-quadrant + inner-core mini-county workspace covering all 5 DeltaCategories.

    Baseline accounts:
      - 0100000000001 (nw_e + core + HD): 1920 Craftsman Bungalow -> UNCHANGED in next_year
      - 0110000000002 (nw_e + core + HD): 1935 Frame Residence    -> UPDATED_STANDING in next_year
      - 0120000000003 (nw_e + core + HD): 1918 Pre-1940 Bungalow  -> DEMOLISHED_TO_VACANT in next_year (Cohort 5)
      - 0130000000004 (nw_e + core + HD): 1928 Landmark Duplex    -> REPLATTED_OR_RETIRED (1->1 to 0990000000004)
      - 0200000000007 (se quadrant):      1955 Ranch House        -> UNCHANGED (untouched shard proof)
      - 0210000000008 (nw_w quadrant):    1962 Brick Ranch        -> UNCHANGED
      - 0220000000009 (ne quadrant):      1968 Split Level        -> UNCHANGED
      - 0230000000010 (sw quadrant):      1974 Townhouse          -> UNCHANGED

    Added in next_year:
      - 0990000000004 (nw_e + core + HD): renumbered replacement account for 0130000000004 at 904 HEIGHTS BLVD
      - 0140000000005 (nw_e + core + HD): NEW_CONSTRUCTION explicitly dated next_year (landmark named)
      - 0140000000006 (nw_e + core + HD): NEW_CONSTRUCTION undated in CAMA, resolved via structural extra feature
    """
    fake_repo = base_dir / "repo"
    _copy_repo_sync_skeleton(fake_repo)
    cache_dir = fake_repo / "pipeline" / "cache"
    ndjson_dir = cache_dir / "aligned_ndjson"
    data_dir = fake_repo / "app" / "public" / "data"
    addr_dir = data_dir / "address_shards"
    permits_dir = data_dir / "permits"
    export_dir = data_dir / "export_cache"
    roll_src_dir = base_dir / f"src_roll_{next_year}"
    for d in (ndjson_dir, addr_dir, permits_dir, export_dir, roll_src_dir):
        d.mkdir(parents=True, exist_ok=True)

    if baseline_year != 2026:
        sync_repo_tax_year_and_cache_bust(
            fake_repo,
            tax_year=baseline_year,
            cache_bust=f"{baseline_year}0101a",
            dry_run=False,
        )

    hd_name = "Houston Heights Historic District South"
    hd_slug = "historic_districts__houston_heights_historic_district_south"
    hd_poly = make_wgs84_box(-95.3975, 29.7900, half_w_deg=0.0040, half_h_deg=0.0040)

    b_unch = make_building_feature(
        "0100000000001",
        -95.3990,
        29.7900,
        year_built=1920,
        address="101 HEIGHTS BLVD",
        owner="ALPHA OWNER",
        extra_props={
            "historic_district": hd_name,
            "contributing": "Contributing",
            "contributing_status": "Contributing",
            "subdivision": "HOUSTON HEIGHTS",
            "footprint_source": "observed",
        },
    )
    b_upd = make_building_feature(
        "0110000000002",
        -95.3982,
        29.7900,
        year_built=1935,
        address="202 HEIGHTS BLVD",
        owner="OLD BETA OWNER",
        extra_props={
            "historic_district": hd_name,
            "contributing": "Contributing",
            "contributing_status": "Contributing",
            "subdivision": "HOUSTON HEIGHTS",
            "footprint_source": "observed",
        },
    )
    b_demo = make_building_feature(
        "0120000000003",
        -95.3974,
        29.7900,
        year_built=1918,
        address="303 HEIGHTS BLVD",
        owner="GAMMA OWNER",
        extra_props={
            "historic_district": hd_name,
            "contributing": "Contributing",
            "contributing_status": "Contributing",
            "subdivision": "HOUSTON HEIGHTS",
            "footprint_source": "observed",
        },
    )
    b_replat = make_building_feature(
        "0130000000004",
        -95.3966,
        29.7900,
        year_built=1928,
        address="404 HEIGHTS BLVD",
        owner="DELTA OWNER",
        bld_style="Duplex",
        extra_props={
            "historic_district": hd_name,
            "contributing": "Contributing",
            "contributing_status": "Contributing",
            "is_landmark": True,
            "landmark_name": "Delta Historic Duplex",
            "subdivision": "HOUSTON HEIGHTS",
            "footprint_source": "observed",
        },
    )
    b_se = make_building_feature(
        "0200000000007",
        -95.3000,
        29.7000,
        year_built=1955,
        address="707 GULF FWY",
        owner="SE OWNER",
    )
    b_nww = make_building_feature(
        "0210000000008",
        -95.6000,
        29.8200,
        year_built=1962,
        address="808 HEMPSTEAD RD",
        owner="NWW OWNER",
    )
    b_ne = make_building_feature(
        "0220000000009",
        -95.3000,
        29.8200,
        year_built=1968,
        address="909 EASTEX FWY",
        owner="NE OWNER",
    )
    b_sw = make_building_feature(
        "0230000000010",
        -95.6000,
        29.7000,
        year_built=1974,
        address="606 BELLAIRE BLVD",
        owner="SW OWNER",
    )

    quad_features: dict[str, list[dict[str, Any]]] = {
        "nw_e": [b_unch, b_upd, b_demo, b_replat],
        "se": [b_se],
        "nw_w": [b_nww],
        "ne": [b_ne],
        "sw": [b_sw],
    }
    all_base_blds = [f for q in QUAD_NAMES for f in quad_features[q]]

    shard_names: list[str] = []
    for q in QUAD_NAMES:
        seq_p = ndjson_dir / f"buildings_{q}.geojsonseq"
        with open(seq_p, "wb") as fh:
            for ft in quad_features[q]:
                fh.write(b"\x1e" + json.dumps(ft, separators=(",", ":")).encode("utf-8") + b"\n")
        pm_p = data_dir / f"houston_buildings_{q}.pmtiles"
        compile_pmtiles_archive(pm_p, quad_features[q], [], min_zoom=12, max_zoom=14)
        shard_names.append(pm_p.name)

    core_blds = list(quad_features["nw_e"])
    core_parcels = []
    for bf in core_blds:
        bp = bf["properties"]
        p_geom = make_wgs84_box(
            shape(bf["geometry"]).centroid.x,
            shape(bf["geometry"]).centroid.y,
            half_w_deg=0.00018,
            half_h_deg=0.00018,
        )
        core_parcels.append(
            {
                "type": "Feature",
                "geometry": p_geom,
                "properties": {
                    "id": f"pcl_{bp['hcad_num']}",
                    "hcad_num": bp["hcad_num"],
                    "address": bp["address"],
                    "owner": bp["owner"],
                    "year_built": bp["year_built"],
                    "decade": bp["decade"],
                    "use_category": bp["use_category"],
                    "land_area": bp["land_area"],
                    "bld_area": bp["bld_area"],
                },
            }
        )

    write_compact_json(
        data_dir / "buildings.geojson",
        {"type": "FeatureCollection", "features": core_blds},
    )
    write_compact_json(
        data_dir / "parcels.geojson",
        {"type": "FeatureCollection", "features": core_parcels},
    )
    compile_pmtiles_archive(
        data_dir / "houston_atlas.pmtiles", core_blds, core_parcels, min_zoom=12, max_zoom=14
    )

    overlays: dict[str, Any] = {
        k: {"type": "FeatureCollection", "features": []}
        for k in (
            "landmarks",
            "historic_districts",
            "heritage_districts",
            "nrhp_districts",
            "nrhp_properties",
            "thc_markers",
            "annexations",
            "good_brick_awards",
            "neighborhoods",
            "super_neighborhoods",
            "council_districts",
            "historic_wards",
            "platted_subdivisions",
            "land_use_protections",
            "lost_houston",
        )
    }
    courtlandt_poly = make_wgs84_box(-95.3850, 29.7450, half_w_deg=0.0010, half_h_deg=0.0010)
    overlays["historic_districts"]["features"] = [
        {
            "type": "Feature",
            "geometry": hd_poly,
            "properties": {
                "id": "hd_heights_south",
                "name": hd_name,
                "slug": "houston_heights_historic_district_south",
                "designation_year": 2011,
            },
        },
        {
            "type": "Feature",
            "geometry": courtlandt_poly,
            "properties": {
                "id": "hd_courtlandt",
                "name": "Courtlandt Place Historic District",
                "slug": "courtlandt_place_historic_district",
                "designation_year": 1996,
            },
        },
    ]
    overlays["lost_houston"]["features"] = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [-95.3632, 29.7589]},
            "properties": {
                "id": "lost_old_capitol",
                "name": "Historic Lost Test Building",
                "year_built": 1903,
                "year_demolished": 1968,
                "cohort": "cohort_1_curated",
            },
        }
    ]
    write_compact_json(data_dir / "overlays.json", overlays)

    cd_features = []
    for idx_cd, letter in enumerate("ABCDEFGHIJK"):
        geom_cd = (
            hd_poly
            if letter == "C"
            else make_wgs84_box(-95.58 + idx_cd * 0.01, 29.65, half_w_deg=0.0020, half_h_deg=0.0020)
        )
        cd_features.append(
            {"type": "Feature", "geometry": geom_cd, "properties": {"DISTRICT": letter}}
        )
    write_compact_json(
        cache_dir / "coh_council_districts_2024.geojson",
        {"type": "FeatureCollection", "features": cd_features},
    )

    # Seed baseline address_shards across touched/deleted/migrated shard keys
    addr_payloads: dict[str, dict[str, list[list[Any]]]] = {
        "10": {"HEIGHTS BLVD": [["101", -95.399, 29.79, 1920, "0100000000001"]]},
        "20": {"HEIGHTS BLVD": [["202", -95.3982, 29.79, 1935, "0110000000002"]]},
        "30": {"HEIGHTS BLVD": [["303", -95.3974, 29.79, 1918, "0120000000003"]]},
        "40": {"HEIGHTS BLVD": [["404", -95.3966, 29.79, 1928, "0130000000004"]]},
        "70": {"GULF FWY": [["707", -95.3, 29.7, 1955, "0200000000007"]]},
        "80": {"HEMPSTEAD RD": [["808", -95.6, 29.82, 1962, "0210000000008"]]},
        "90": {"EASTEX FWY": [["909", -95.3, 29.82, 1968, "0220000000009"]]},
        "60": {"BELLAIRE BLVD": [["606", -95.6, 29.7, 1974, "0230000000010"]]},
    }
    for sk, sp in addr_payloads.items():
        write_compact_json(addr_dir / f"addr_{sk}.json", sp)

    # Seed search_index.json with boundary, lost_*, school_*, deleted, replatted, and updated entries
    search_items = [
        {
            "type": "district",
            "category": "Historic District",
            "label": hd_name,
            "sublabel": "Historic District",
            "lon": -95.3975,
            "lat": 29.79,
            "zoom": 15.2,
            "hcad_num": "",
            "year_built": 0,
            "id": "dist_heights_south",
        },
        {
            "type": "building",
            "category": "Lost Houston",
            "label": "Historic Lost Test Building",
            "sublabel": "Built 1903 - Demolished 1968",
            "lon": -95.3632,
            "lat": 29.7589,
            "zoom": 17.5,
            "hcad_num": "",
            "year_built": 1903,
            "id": "lost_old_capitol",
        },
        {
            "type": "building",
            "category": "School",
            "label": "Heights Annex School",
            "address": "404 HEIGHTS BLVD",
            "sublabel": "School - Built 1928 - HCAD #0130000000004",
            "lon": -95.3966,
            "lat": 29.79,
            "zoom": 17.5,
            "hcad_num": "0130000000004",
            "year_built": 1928,
            "id": "school_heights_annex",
        },
        {
            "type": "building",
            "category": "Contributing",
            "label": "303 HEIGHTS BLVD",
            "address": "303 HEIGHTS BLVD",
            "sublabel": "Built 1918 - HCAD #0120000000003",
            "lon": -95.3974,
            "lat": 29.79,
            "zoom": 17.5,
            "hcad_num": "0120000000003",
            "year_built": 1918,
            "id": "0120000000003",
        },
        {
            "type": "building",
            "category": "Contributing",
            "label": "Delta Historic Duplex - 404 HEIGHTS BLVD",
            "address": "404 HEIGHTS BLVD",
            "sublabel": "Built 1928 - HCAD #0130000000004",
            "lon": -95.3966,
            "lat": 29.79,
            "zoom": 17.5,
            "hcad_num": "0130000000004",
            "year_built": 1928,
            "id": "0130000000004",
        },
        {
            "type": "building",
            "category": "Contributing",
            "label": "202 HEIGHTS BLVD",
            "address": "202 HEIGHTS BLVD",
            "sublabel": "Built 1935 - HCAD #0110000000002",
            "lon": -95.3982,
            "lat": 29.79,
            "zoom": 17.5,
            "hcad_num": "0110000000002",
            "year_built": 1935,
            "id": "0110000000002",
        },
    ]
    write_compact_json(data_dir / "search_index.json", search_items)

    # Seed permits/permits_<prefix3>.json shards including old prefixes 012 and 013
    write_compact_json(
        permits_dir / "permits_010.json",
        {"0100000000001": {"y": 1920, "s": "Bungalow", "d": "19950101:P100001"}},
    )
    write_compact_json(
        permits_dir / "permits_011.json",
        {"0110000000002": {"y": 1935, "s": "Frame", "d": "20100505:201000001"}},
    )
    write_compact_json(
        permits_dir / "permits_012.json",
        {"0120000000003": {"y": 1918, "s": "Bungalow", "d": "20010303:U100003"}},
    )
    write_compact_json(
        permits_dir / "permits_013.json",
        {"0130000000004": {"y": 1928, "s": "Duplex", "d": "20080808:200800004"}},
    )

    # Seed 24 export_cache/*.json district bundles (main HD + Courtlandt alias + 22 empty district shells)
    write_compact_json(
        export_dir / f"{hd_slug}.json",
        {
            "buildings": core_blds,
            "stats": {
                "buildingCount": len(core_blds),
                "earliestYear": 1918,
                "medianYear": 1924,
                "landmarkCount": 1,
                "goodBrickCount": 0,
            },
        },
    )
    write_compact_json(
        export_dir / "historic_districts__courtland_place_historic_district.json",
        {
            "buildings": [],
            "stats": {
                "buildingCount": 0,
                "earliestYear": None,
                "medianYear": None,
                "landmarkCount": 0,
                "goodBrickCount": 0,
            },
        },
    )
    for idx_ex in range(1, 23):
        slug_extra = f"historic_districts__extra_district_{idx_ex:02d}"
        overlays["historic_districts"]["features"].append(
            {
                "type": "Feature",
                "geometry": make_wgs84_box(-95.4200 + idx_ex * 0.0012, 29.7420, half_w_deg=0.0004, half_h_deg=0.0004),
                "properties": {
                    "id": f"hd_extra_{idx_ex:02d}",
                    "name": f"Extra District {idx_ex:02d}",
                    "slug": f"extra_district_{idx_ex:02d}",
                    "designation_year": 2010,
                },
            }
        )
        write_compact_json(
            export_dir / f"{slug_extra}.json",
            {
                "buildings": [],
                "stats": {
                    "buildingCount": 0,
                    "earliestYear": None,
                    "medianYear": None,
                    "landmarkCount": 0,
                    "goodBrickCount": 0,
                },
            },
        )
    # Re-persist overlays.json after adding the 22 extra district polygons
    write_compact_json(data_dir / "overlays.json", overlays)

    # Curated & support data files
    write_compact_json(data_dir / "curated_overrides.json", {"schema_version": 2, "overrides": {}})
    write_compact_json(data_dir / "building_photos.json", {})
    write_compact_json(data_dir / "haif_index.json", {})
    write_compact_json(data_dir / "citydir_index.json", {"0120000000003": {"e": 1918, "a": "303 HEIGHTS BLVD"}})
    watchlist_doc = {
        "schema_version": 1,
        "properties": [
            {
                "id": "wl_heights_101",
                "hcad": "0100000000001",
                "address": "101 HEIGHTS BLVD",
                "status": "Active / High Priority",
                "tier": "Tier 1",
            }
        ],
    }
    write_compact_json(data_dir / "property_watchlist.json", watchlist_doc)

    # Baseline stats_summary.json + pmtiles_manifest.json + cama_<baseline_year>_compact.pkl
    dec_counts = {str(d): 0 for d in compute_decade_bins(baseline_year)}
    for bf in all_base_blds:
        dec_counts[str(bf["properties"]["decade"])] += 1
    dec_counts["unknown"] = 0
    stats_base = {
        "data_tax_year": baseline_year,
        "total_buildings": len(all_base_blds),
        "dated_buildings": len(all_base_blds),
        "observed_footprints": 4,
        "earliest_year": 1918,
        "latest_year": 1974,
        "total_landmarks": 0,
        "total_historic_districts": 24,
        "total_heritage_districts": 0,
        "total_nrhp_districts": 0,
        "total_thc_markers": 0,
        "decade_counts": dec_counts,
        "contributing_counts": {"Contributing": 4, "Outside Historic District": 4},
        "use_category_counts": {"Residential": 8},
        "top_historic_districts": {hd_name: 4},
    }
    (data_dir / "stats_summary.json").write_text(
        json.dumps(stats_base, indent=2) + "\n", encoding="utf-8"
    )
    update_pmtiles_manifest_v3(
        data_dir / "pmtiles_manifest.json",
        total_buildings=len(all_base_blds),
        building_shards=shard_names,
        data_tax_year=baseline_year,
        cache_bust=f"{baseline_year}1031a" if baseline_year != 2026 else ACTIVE_CACHE_BUST,
    )
    base_cama = {
        bf["properties"]["hcad_num"]: (
            int(bf["properties"]["year_built"]),
            0,
            int(bf["properties"]["bld_area"]),
            "A1",
            str(bf["properties"]["bld_style"]),
            str(bf["properties"]["owner"]),
            str(bf["properties"]["address"]),
            float(bf["properties"]["land_area"]),
        )
        for bf in all_base_blds
    }
    save_compact_cama_lookup(cache_dir, base_cama, tax_year=baseline_year)

    # Next-year HCAD roll archives (9 parcels vs 8 baseline -> retention > 100%)
    acct_rows = [
        # 1. UNCHANGED
        {
            "acct": "0100000000001",
            "yr": str(next_year),
            "mailto": "ALPHA OWNER",
            "site_addr_1": "101 HEIGHTS BLVD",
            "str_num": "101",
            "str": "HEIGHTS",
            "str_sfx": "BLVD",
            "state_class": "A1",
            "yr_impr": "1920",
            "bld_ar": "1600",
            "land_ar": "5000",
            "lgl_2": "HOUSTON HEIGHTS",
        },
        # 2. UPDATED_STANDING
        {
            "acct": "0110000000002",
            "yr": str(next_year),
            "mailto": "REVITALIZED HOMES LLC",
            "site_addr_1": "202 HEIGHTS BLVD",
            "str_num": "202",
            "str": "HEIGHTS",
            "str_sfx": "BLVD",
            "state_class": "A1",
            "yr_impr": "1935",
            "bld_ar": "1950",
            "land_ar": "5000",
            "lgl_2": "HOUSTON HEIGHTS",
        },
        # 3. DEMOLISHED_TO_VACANT (pre-1940 teardown -> Cohort 5)
        {
            "acct": "0120000000003",
            "yr": str(next_year),
            "mailto": "GAMMA VACANT LLC",
            "site_addr_1": "303 HEIGHTS BLVD",
            "str_num": "303",
            "str": "HEIGHTS",
            "str_sfx": "BLVD",
            "state_class": "C1",
            "yr_impr": "",
            "bld_ar": "0",
            "bld_val": "0",
            "land_ar": "5000",
            "lgl_2": "HOUSTON HEIGHTS",
        },
        # 4. REPLATTED_OR_RETIRED successor account (0130000000004 -> 0990000000004)
        {
            "acct": "0990000000004",
            "yr": str(next_year),
            "mailto": "DELTA SUCCESSOR TRUST",
            "site_addr_1": "904 HEIGHTS BLVD",
            "str_num": "904",
            "str": "HEIGHTS",
            "str_sfx": "BLVD",
            "state_class": "B2",
            "yr_impr": "1928",
            "bld_ar": "1600",
            "land_ar": "5000",
            "lgl_2": "HOUSTON HEIGHTS",
        },
        # 5a. NEW_CONSTRUCTION dated next_year
        {
            "acct": "0140000000005",
            "yr": str(next_year),
            "mailto": "EPSILON BUILDERS INC",
            "site_addr_1": "505 HEIGHTS BLVD",
            "str_num": "505",
            "str": "HEIGHTS",
            "str_sfx": "BLVD",
            "state_class": "A1",
            "yr_impr": str(next_year),
            "bld_ar": "2400",
            "land_ar": "5000",
            "lgl_2": "HOUSTON HEIGHTS",
        },
        # 5b. NEW_CONSTRUCTION undated in CAMA -> resolved via structural extra feature (unknown == 0)
        {
            "acct": "0140000000006",
            "yr": str(next_year),
            "mailto": "EPSILON INFILL TWO LLC",
            "site_addr_1": "507 HEIGHTS BLVD",
            "str_num": "507",
            "str": "HEIGHTS",
            "str_sfx": "BLVD",
            "state_class": "A1",
            "yr_impr": "0",
            "bld_ar": "2100",
            "land_ar": "5000",
            "lgl_2": "HOUSTON HEIGHTS",
        },
        # Untouched non-core quadrants
        {
            "acct": "0200000000007",
            "yr": str(next_year),
            "mailto": "SE OWNER",
            "site_addr_1": "707 GULF FWY",
            "str_num": "707",
            "str": "GULF",
            "str_sfx": "FWY",
            "state_class": "A1",
            "yr_impr": "1955",
            "bld_ar": "1600",
            "land_ar": "5000",
        },
        {
            "acct": "0210000000008",
            "yr": str(next_year),
            "mailto": "NWW OWNER",
            "site_addr_1": "808 HEMPSTEAD RD",
            "str_num": "808",
            "str": "HEMPSTEAD",
            "str_sfx": "RD",
            "state_class": "A1",
            "yr_impr": "1962",
            "bld_ar": "1600",
            "land_ar": "5000",
        },
        {
            "acct": "0220000000009",
            "yr": str(next_year),
            "mailto": "NE OWNER",
            "site_addr_1": "909 EASTEX FWY",
            "str_num": "909",
            "str": "EASTEX",
            "str_sfx": "FWY",
            "state_class": "A1",
            "yr_impr": "1968",
            "bld_ar": "1600",
            "land_ar": "5000",
        },
        {
            "acct": "0230000000010",
            "yr": str(next_year),
            "mailto": "SW OWNER",
            "site_addr_1": "606 BELLAIRE BLVD",
            "str_num": "606",
            "str": "BELLAIRE",
            "str_sfx": "BLVD",
            "state_class": "A1",
            "yr_impr": "1974",
            "bld_ar": "1600",
            "land_ar": "5000",
        },
    ]

    bldg_rows = [
        {"acct": "0100000000001", "bld_num": "1", "date_erected": "1920", "yr_remodel": "", "dscr": "Good", "structure_dscr": "Bungalow", "property_use_cd": "A1", "im_sq_ft": "1600"},
        {"acct": "0110000000002", "bld_num": "1", "date_erected": "1935", "yr_remodel": str(next_year), "dscr": "Renovated", "structure_dscr": "Bungalow", "property_use_cd": "A1", "im_sq_ft": "1950"},
        {"acct": "0990000000004", "bld_num": "1", "date_erected": "1928", "yr_remodel": "", "dscr": "Good", "structure_dscr": "Duplex", "property_use_cd": "B2", "im_sq_ft": "1600"},
        {"acct": "0140000000005", "bld_num": "1", "date_erected": str(next_year), "yr_remodel": "", "dscr": "Excellent", "structure_dscr": "Single-Family", "property_use_cd": "A1", "im_sq_ft": "2400"},
        {"acct": "0140000000006", "bld_num": "1", "date_erected": "0", "yr_remodel": "", "dscr": "Excellent", "structure_dscr": "Single-Family", "property_use_cd": "A1", "im_sq_ft": "2100"},
        {"acct": "0200000000007", "bld_num": "1", "date_erected": "1955", "yr_remodel": "", "dscr": "Average", "structure_dscr": "Bungalow", "property_use_cd": "A1", "im_sq_ft": "1600"},
        {"acct": "0210000000008", "bld_num": "1", "date_erected": "1962", "yr_remodel": "", "dscr": "Average", "structure_dscr": "Bungalow", "property_use_cd": "A1", "im_sq_ft": "1600"},
        {"acct": "0220000000009", "bld_num": "1", "date_erected": "1968", "yr_remodel": "", "dscr": "Average", "structure_dscr": "Bungalow", "property_use_cd": "A1", "im_sq_ft": "1600"},
        {"acct": "0230000000010", "bld_num": "1", "date_erected": "1974", "yr_remodel": "", "dscr": "Average", "structure_dscr": "Bungalow", "property_use_cd": "A1", "im_sq_ft": "1600"},
    ]

    xf_rows = [
        {
            "acct": "0140000000006",
            "bld_num": "1",
            "cd": "RRP1",
            "dscr": "FRAME PORCH FOUNDATION",
            "act_yr": str(next_year),
            "eff_yr": str(next_year),
            "uts": "1",
        }
    ]
    permits_rows = [
        {
            "acct": "0120000000003",
            "id": f"PRMDEM{next_year}",
            "permit_type": "13",
            "permit_tp_descr": "Demolition",
            "dscr": "DEMOLISH HOUSE SEWER DISCONNECT",
            "issue_date": f"03/10/{next_year}",
            "yr": str(next_year),
            "site_num": "303",
            "site_str": "HEIGHTS",
            "site_tp": "BLVD",
        },
        {
            "acct": "0110000000002",
            "id": f"PRMUPD{next_year}",
            "permit_type": "05",
            "permit_tp_descr": "Residential Remodel",
            "dscr": "REAR ADDITION AND KITCHEN REMODEL",
            "issue_date": f"04/12/{next_year}",
            "yr": str(next_year),
            "site_num": "202",
            "site_str": "HEIGHTS",
            "site_tp": "BLVD",
        },
        {
            "acct": "0140000000005",
            "id": f"PRMNEW{next_year}",
            "permit_type": "01",
            "permit_tp_descr": "New Single Family",
            "dscr": "NEW 2 STORY RESIDENCE",
            "issue_date": f"02/05/{next_year}",
            "yr": str(next_year),
            "site_num": "505",
            "site_str": "HEIGHTS",
            "site_tp": "BLVD",
        },
    ]
    deeds_rows = [
        {"acct": "0110000000002", "dos": f"05/20/{next_year}", "clerk_yr": str(next_year), "clerk_id": f"RP-{next_year}-555002"},
        {"acct": "0990000000004", "dos": f"06/01/{next_year}", "clerk_yr": str(next_year), "clerk_id": f"RP-{next_year}-990004"},
        {"acct": "0140000000005", "dos": f"01/15/{next_year}", "clerk_yr": str(next_year), "clerk_id": f"RP-{next_year}-140005"},
    ]
    tieback_rows = [
        {"acct": "0990000000004", "tp": "REPLAT", "dscr": "REPLATTED FROM PRIOR ACCT", "related_acct": "0130000000004", "pct": "100"}
    ]

    parcel_coords = {
        "0100000000001": (-95.3990, 29.7900),
        "0110000000002": (-95.3982, 29.7900),
        "0120000000003": (-95.3974, 29.7900),
        "0990000000004": (-95.3966, 29.7900),
        "0140000000005": (-95.3958, 29.7900),
        "0140000000006": (-95.3950, 29.7900),
        "0200000000007": (-95.3000, 29.7000),
        "0210000000008": (-95.6000, 29.8200),
        "0220000000009": (-95.3000, 29.8200),
        "0230000000010": (-95.6000, 29.7000),
    }
    parcel_records = [
        (acct, wgs84_poly_to_2278(make_wgs84_box(lon, lat, half_w_deg=0.00018, half_h_deg=0.00018)))
        for acct, (lon, lat) in parcel_coords.items()
    ]
    write_synthetic_roll_zips(
        roll_src_dir,
        tax_year=next_year,
        real_acct_rows=acct_rows,
        building_res_rows=bldg_rows,
        extra_features_rows=xf_rows,
        permits_rows=permits_rows,
        deeds_rows=deeds_rows,
        tieback_rows=tieback_rows,
        parcel_records=parcel_records,
    )

    return {
        "repo_root": fake_repo,
        "cache_dir": cache_dir,
        "data_dir": data_dir,
        "roll_src_dir": roll_src_dir,
        "hd_name": hd_name,
        "hd_slug": hd_slug,
    }


def _relaxed_sanity_for_mini_county() -> RollSanityConfig:
    # 1 demo out of 8 baseline features is 12.5%; permit demo tests in mini-county while testing 2.5% breaker in UT-4
    return RollSanityConfig(max_annual_demolition_fraction=0.25)


def test_it1_end_to_end_cli_apply_all_categories_and_dirty_only_pmtiles(tmp_path: Path) -> None:
    """IT-1: End-to-end 2026 -> 2027 apply across multi-quadrant + inner-core covering all 5 categories."""
    ws = build_mini_county_workspace(tmp_path, baseline_year=2026, next_year=2027)
    data_dir: Path = ws["data_dir"]
    cache_dir: Path = ws["cache_dir"]

    # Record untouched quadrant PMTiles checksums BEFORE apply
    untouched_quads = ("se", "nw_w", "ne", "sw")
    pre_pmtiles_sha = {
        q: hashlib.sha256((data_dir / f"houston_buildings_{q}.pmtiles").read_bytes()).hexdigest()
        for q in untouched_quads
    }

    # Verify CLI subprocess works end-to-end cleanly on dry-run first (checking CLI wiring & stdout formatting)
    pipeline_pkg_dir = Path(__file__).resolve().parents[1]
    cli_proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "atlas_pipeline.cli",
            "--mode",
            "annual-refresh",
            "--tax-year",
            "2027",
            "--roll-dir",
            str(ws["roll_src_dir"]),
            "--cache-dir",
            str(cache_dir),
            "--output-dir",
            str(data_dir),
            "--allow-partial-roll",
            "--dry-run",
        ],
        cwd=str(pipeline_pkg_dir),
        capture_output=True,
        text=True,
        check=False,
    )
    # Note: mini-county has 1/8 = 12.5% demo rate so default 2.5% circuit breaker triggers nicely via CLI unless relaxed;
    # confirm CLI reaches Stage 3 cleanly without import/argparse errors.
    assert "Houston Building Atlas v2 Pipeline (mode=annual-refresh)" in cli_proc.stdout

    # Now execute full --apply via run_annual_hcad_refresh (relaxing only the 8-parcel demo fraction threshold)
    res = run_annual_hcad_refresh(
        cache_dir=cache_dir,
        output_dir=data_dir,
        tax_year=2027,
        roll_dir=ws["roll_src_dir"],
        apply=True,
        cache_bust="20270901a",
        sanity_cfg=_relaxed_sanity_for_mini_county(),
        repo_root=ws["repo_root"],
    )

    assert res["mode"] == "apply"
    assert res["prior_tax_year"] == 2026
    assert res["tax_year"] == 2027
    pc = res["parcel_counts"]
    assert pc[DeltaCategory.UNCHANGED.value] == 5
    assert pc[DeltaCategory.UPDATED_STANDING.value] == 1
    assert pc[DeltaCategory.DEMOLISHED_TO_VACANT.value] == 1
    assert pc[DeltaCategory.REPLATTED_OR_RETIRED.value] == 1
    assert pc[DeltaCategory.NEW_CONSTRUCTION.value] == 2

    # Dirty-only PMTiles rebuild: ONLY nw_e is rebuilt; se/nw_w/ne/sw PMTiles remain byte-identical!
    assert res["dirty_quadrants"] == ["nw_e"]
    assert res["rebuilt_pmtiles_shards"] == ["houston_buildings_nw_e.pmtiles"]
    assert res["scorecards"]["skipped"] is False
    assert res["scorecards"]["feature_count"] == 35
    delta_rpt_doc = json.loads(Path(res["reports"]["delta_report"]).read_text(encoding="utf-8"))
    assert delta_rpt_doc["scorecards"]["feature_count"] == 35
    for q in untouched_quads:
        after_sha = hashlib.sha256((data_dir / f"houston_buildings_{q}.pmtiles").read_bytes()).hexdigest()
        assert after_sha == pre_pmtiles_sha[q], f"Untouched shard {q} was unnecessarily recompiled!"
    assert (data_dir / "houston_buildings_nw_e.pmtiles").stat().st_size > 100

    # Manifest v3 & stats_summary invariants: unknown == 0, total == dated == sum(decade_counts)
    manifest = json.loads((data_dir / "pmtiles_manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == 3
    assert manifest["data_tax_year"] == 2027
    assert manifest["cache_bust"] == "20270901a"
    assert len(manifest["building_shards"]) == 5

    stats = json.loads((data_dir / "stats_summary.json").read_text(encoding="utf-8"))
    assert stats["data_tax_year"] == 2027
    assert stats["decade_counts"]["unknown"] == 0
    assert stats["total_buildings"] == 9  # 8 baseline - 1 demolished + 2 new construction = 9
    assert stats["dated_buildings"] == stats["total_buildings"]
    assert sum(stats["decade_counts"].values()) == stats["total_buildings"]
    assert stats["latest_year"] == 2027

    # Also prove CLI subprocess --apply succeeds cleanly when demolition fraction <= 2.5% (same-year rerun = 0 demos!)
    cli_apply = subprocess.run(
        [
            sys.executable,
            "-m",
            "atlas_pipeline.cli",
            "--mode",
            "annual-refresh",
            "--tax-year",
            "2027",
            "--roll-dir",
            str(ws["roll_src_dir"]),
            "--cache-dir",
            str(cache_dir),
            "--output-dir",
            str(data_dir),
            "--cache-bust",
            "20270901b",
            "--apply",
        ],
        cwd=str(pipeline_pkg_dir),
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Annual HCAD Roll Refresh Complete (APPLY)" in cli_apply.stdout


def test_it2_dry_run_sha256_tree_invariance(tmp_path: Path) -> None:
    """IT-2: --dry-run produces zero mutations across tracked repo files, app/public/data, and aligned_ndjson."""
    ws = build_mini_county_workspace(tmp_path, baseline_year=2026, next_year=2027)
    repo_root: Path = ws["repo_root"]

    pre_hashes = _snapshot_tree_sha256(
        repo_root, exclude_rel_prefixes=("pipeline/cache/rolls",)
    )
    assert len(pre_hashes) >= 45

    dry_res = run_annual_hcad_refresh(
        cache_dir=ws["cache_dir"],
        output_dir=ws["data_dir"],
        tax_year=2027,
        roll_dir=ws["roll_src_dir"],
        apply=False,
        cache_bust="20270901a",
        sanity_cfg=_relaxed_sanity_for_mini_county(),
        repo_root=repo_root,
    )
    assert dry_res["mode"] == "dry_run"
    for rpt_key in ("delta_report", "orphaned_curated_entities", "lost_houston_candidates"):
        assert Path(dry_res["reports"][rpt_key]).is_file()

    post_hashes = _snapshot_tree_sha256(
        repo_root, exclude_rel_prefixes=("pipeline/cache/rolls",)
    )
    assert post_hashes == pre_hashes

    # Also verify direct module invocation (`python -m atlas_pipeline.update_annual_hcad_roll --dry-run`
    # WITHOUT explicit `--mode annual-refresh`) routes to annual-refresh dry-run rather than `--mode core`.
    proc_direct = subprocess.run(
        [
            sys.executable,
            "-m",
            "atlas_pipeline.update_annual_hcad_roll",
            "--cache-dir",
            str(ws["cache_dir"]),
            "--output-dir",
            str(ws["data_dir"]),
            "--tax-year",
            "2027",
            "--roll-dir",
            str(ws["roll_src_dir"]),
            "--allow-partial-roll",
            "--dry-run",
        ],
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc_direct.returncode == 0, f"direct module dry-run failed:\n{proc_direct.stdout}\n{proc_direct.stderr}"
    assert "Annual HCAD Roll Refresh Complete (DRY_RUN)" in proc_direct.stdout
    post_direct_hashes = _snapshot_tree_sha256(
        repo_root, exclude_rel_prefixes=("pipeline/cache/rolls",)
    )
    assert post_direct_hashes == pre_hashes


def test_it3_artifact_sync_delta_pruning_across_indexes_and_export_cache(tmp_path: Path) -> None:
    """IT-3: Verify ArtifactSyncDelta prunes demolished/retired IDs and migrates renumbered keys everywhere."""
    ws = build_mini_county_workspace(tmp_path, baseline_year=2026, next_year=2027)
    data_dir: Path = ws["data_dir"]

    run_annual_hcad_refresh(
        cache_dir=ws["cache_dir"],
        output_dir=data_dir,
        tax_year=2027,
        roll_dir=ws["roll_src_dir"],
        apply=True,
        cache_bust="20270901a",
        sanity_cfg=_relaxed_sanity_for_mini_county(),
        repo_root=ws["repo_root"],
    )

    # 1. buildings.geojson: 0120000000003 deleted, 0130000000004 replaced by 0990000000004, 0140000000005 & 6 added
    core_blds = json.loads((data_dir / "buildings.geojson").read_text(encoding="utf-8"))["features"]
    core_hcads = {f["properties"]["hcad_num"] for f in core_blds}
    assert "0120000000003" not in core_hcads
    assert "0130000000004" not in core_hcads
    assert {"0100000000001", "0110000000002", "0990000000004", "0140000000005", "0140000000006"}.issubset(core_hcads)
    upd_feat = next(f for f in core_blds if f["properties"]["hcad_num"] == "0110000000002")
    assert upd_feat["properties"]["owner"] == "REVITALIZED HOMES LLC"
    assert upd_feat["properties"]["remodel_year"] == 2027

    # 2. parcels.geojson: demolished 0120000000003 & retired 0130000000004 pruned; 0990000000004 & 0140000000005 inserted
    core_pcls = json.loads((data_dir / "parcels.geojson").read_text(encoding="utf-8"))["features"]
    pcl_by_hcad = {f["properties"]["hcad_num"]: f["properties"] for f in core_pcls}
    assert "0120000000003" not in pcl_by_hcad
    assert "0130000000004" not in pcl_by_hcad
    assert "0990000000004" in pcl_by_hcad
    assert pcl_by_hcad["0140000000005"]["year_built"] == 2027

    # 3. address_shards: 303 removed from addr_30, 404 removed from addr_40, 904 added to addr_90, 505/507 added to addr_50
    addr_30 = json.loads((data_dir / "address_shards" / "addr_30.json").read_text(encoding="utf-8"))
    assert all(r[4] != "0120000000003" for rows in addr_30.values() for r in rows)
    addr_40 = json.loads((data_dir / "address_shards" / "addr_40.json").read_text(encoding="utf-8"))
    assert all(r[4] != "0130000000004" for rows in addr_40.values() for r in rows)
    addr_90 = json.loads((data_dir / "address_shards" / "addr_90.json").read_text(encoding="utf-8"))
    assert any(r[0] == "904" and r[4] == "0990000000004" for r in addr_90.get("HEIGHTS BLVD", []))
    addr_50 = json.loads((data_dir / "address_shards" / "addr_50.json").read_text(encoding="utf-8"))
    h50_ids = {r[4] for r in addr_50.get("HEIGHTS BLVD", [])}
    assert {"0140000000005", "0140000000006"}.issubset(h50_ids)

    # 4. search_index.json: boundary + lost_* + school_* preserved; 0120000000003 deleted; 013->099 migrated
    s_items = json.loads((data_dir / "search_index.json").read_text(encoding="utf-8"))
    s_ids = {it.get("id") for it in s_items}
    s_hcads = {it.get("hcad_num") for it in s_items}
    assert "dist_heights_south" in s_ids
    assert "lost_old_capitol" in s_ids
    assert "school_heights_annex" in s_ids
    assert "0120000000003" not in s_ids and "0120000000003" not in s_hcads
    assert "0130000000004" not in s_hcads
    school_it = next(it for it in s_items if it.get("id") == "school_heights_annex")
    assert school_it["hcad_num"] == "0990000000004"
    replat_it = next(it for it in s_items if it.get("id") == "0990000000004")
    assert replat_it["address"] == "904 HEIGHTS BLVD"

    # 5. permits/permits_<prefix3>.json: old 0130000000004 pruned; 099/014/011 enriched with 2027 deeds & frontend permit keys
    p013 = json.loads((data_dir / "permits" / "permits_013.json").read_text(encoding="utf-8"))
    assert "0130000000004" not in p013
    p099 = json.loads((data_dir / "permits" / "permits_099.json").read_text(encoding="utf-8"))
    assert "20270601:R2027-990004" in p099["0990000000004"]["d"]
    p011 = json.loads((data_dir / "permits" / "permits_011.json").read_text(encoding="utf-8"))
    assert p011["0110000000002"]["p"][0]["id"] == "PRMUPD2027"
    assert p011["0110000000002"]["p"][0]["cat"] == "remodel"
    assert "20270520:R2027-555002" in p011["0110000000002"]["d"]

    # 6. All 24 export_cache/*.json district bundles intact and reconciled (alias file preserved)
    export_files = sorted((data_dir / "export_cache").glob("*.json"))
    assert len(export_files) == 24
    assert (data_dir / "export_cache" / "historic_districts__courtland_place_historic_district.json").is_file()
    hd_exp = json.loads((data_dir / "export_cache" / f"{ws['hd_slug']}.json").read_text(encoding="utf-8"))
    exp_hcads = {f["properties"]["hcad_num"] for f in hd_exp["buildings"]}
    assert exp_hcads == {"0100000000001", "0110000000002", "0990000000004", "0140000000005", "0140000000006"}
    assert hd_exp["stats"]["buildingCount"] == 5
    assert hd_exp["stats"]["earliestYear"] == 1920


def test_it4_same_year_idempotence_rollback_and_budget_gates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """IT-4: Consecutive same-year idempotence, downstream rollback invariance, soft budget trim, and hard caps."""
    ws = build_mini_county_workspace(tmp_path, baseline_year=2026, next_year=2027)
    cache_dir: Path = ws["cache_dir"]
    data_dir: Path = ws["data_dir"]
    repo_root: Path = ws["repo_root"]

    # First apply 2026 -> 2027
    first = run_annual_hcad_refresh(
        cache_dir=cache_dir,
        output_dir=data_dir,
        tax_year=2027,
        roll_dir=ws["roll_src_dir"],
        apply=True,
        cache_bust="20270901a",
        sanity_cfg=_relaxed_sanity_for_mini_county(),
        repo_root=repo_root,
    )
    assert first["rebuilt_pmtiles_shards"] == ["houston_buildings_nw_e.pmtiles"]
    count_after_first = first["total_buildings_after"]
    pm_hashes_after_first = {
        q: hashlib.sha256((data_dir / f"houston_buildings_{q}.pmtiles").read_bytes()).hexdigest()
        for q in QUAD_NAMES
    }

    # Consecutive same-year rerun: 0 dirty quadrants, 0 rebuilt shards, 0 duplicate features!
    second = run_annual_hcad_refresh(
        cache_dir=cache_dir,
        output_dir=data_dir,
        tax_year=2027,
        roll_dir=ws["roll_src_dir"],
        apply=True,
        cache_bust="20270901a",
        repo_root=repo_root,
    )
    assert second["rebuilt_pmtiles_shards"] == []
    assert second["dirty_quadrants"] == []
    assert second["total_buildings_after"] == count_after_first
    for q in QUAD_NAMES:
        assert (
            hashlib.sha256((data_dir / f"houston_buildings_{q}.pmtiles").read_bytes()).hexdigest()
            == pm_hashes_after_first[q]
        )

    # Soft budget trim gate: prunes non-standing permit records first, then caps deeds <= 2 and permits <= 5
    staged_trim = tmp_path / "staged_trim"
    (staged_trim / "permits").mkdir(parents=True)
    live_dummy = tmp_path / "live_dummy"
    live_dummy.mkdir()
    write_compact_json(
        staged_trim / "permits" / "permits_011.json",
        {
            "0110000000002": {
                "y": 1935,
                "d": "|".join(f"202{i}0101:R202{i}-00000{i}" for i in range(6)),
                "p": [{"id": f"P{i}", "dt": f"202{i}-01-01", "cat": "remodel", "tp": "05", "d": "WORK"} for i in range(8)],
            },
            "0119999999999": {
                "y": 0,
                "d": "20200101:R2020-999999",
                "p": [{"id": "PX", "dt": "2020-01-01", "cat": "demolition", "tp": "13", "d": "DEMO"}],
            },
        },
    )
    trim_res = prune_optional_enrichment_for_soft_budget(
        staged_trim, live_dummy, {"0110000000002"}, soft_max_bytes=1
    )
    assert trim_res["trimmed_accounts"] >= 2
    assert trim_res["bytes_saved"] > 0
    trimmed_doc = json.loads((staged_trim / "permits" / "permits_011.json").read_text(encoding="utf-8"))
    assert "0119999999999" not in trimmed_doc
    assert len(trimmed_doc["0110000000002"]["d"].split("|")) <= 2
    assert len(trimmed_doc["0110000000002"]["p"]) <= 5

    # Hard bundle cap & single-file cap rejection checks on valid baseline copy
    staged_gate = tmp_path / "staged_gate"
    staged_gate.mkdir()
    for fname in ("pmtiles_manifest.json", "stats_summary.json", "curated_overrides.json"):
        (staged_gate / fname).write_bytes((data_dir / fname).read_bytes())
    assert validate_staged_app_bundle(staged_gate, data_dir)["projected_app_bytes"] > 0

    with pytest.raises(RollValidationError, match="max_single_file|95|Single file|exceeds"):
        validate_staged_app_bundle(staged_gate, data_dir, max_single_file_bytes=100)
    with pytest.raises(RollValidationError, match="hard_max_app|980|bundle|ceiling|exceeds"):
        validate_staged_app_bundle(staged_gate, data_dir, hard_max_app_bytes=100)

    # Atomic rollback test: inject exception inside rebuild_neighborhood_scorecards during 2027 -> 2028 apply
    ws_rb = build_mini_county_workspace(tmp_path / "rb", baseline_year=2026, next_year=2027)
    pre_rb = _snapshot_tree_sha256(
        ws_rb["repo_root"], exclude_rel_prefixes=("pipeline/cache/rolls",)
    )
    import atlas_pipeline.update_annual_hcad_roll as upd_mod

    def _boom(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("Injected scorecard crash after staging")

    monkeypatch.setattr(upd_mod, "rebuild_neighborhood_scorecards", _boom)
    with pytest.raises(RuntimeError, match="Injected scorecard crash"):
        run_annual_hcad_refresh(
            cache_dir=ws_rb["cache_dir"],
            output_dir=ws_rb["data_dir"],
            tax_year=2027,
            roll_dir=ws_rb["roll_src_dir"],
            apply=True,
            cache_bust="20270901z",
            sanity_cfg=_relaxed_sanity_for_mini_county(),
            repo_root=ws_rb["repo_root"],
        )
    post_rb = _snapshot_tree_sha256(
        ws_rb["repo_root"], exclude_rel_prefixes=("pipeline/cache/rolls",)
    )
    assert post_rb == pre_rb


def test_it5_decade_2030_rollover_end_to_end(tmp_path: Path) -> None:
    """IT-5: Simulated 2029 -> 2030 refresh emits integer decade 2030, '2030' bins, and <option value='2030'>."""
    ws = build_mini_county_workspace(tmp_path, baseline_year=2029, next_year=2030)
    data_dir: Path = ws["data_dir"]
    repo_root: Path = ws["repo_root"]

    res = run_annual_hcad_refresh(
        cache_dir=ws["cache_dir"],
        output_dir=data_dir,
        tax_year=2030,
        roll_dir=ws["roll_src_dir"],
        apply=True,
        cache_bust="20300901a",
        sanity_cfg=_relaxed_sanity_for_mini_county(),
        repo_root=repo_root,
    )
    assert res["tax_year"] == 2030
    assert res["prior_tax_year"] == 2029

    # Building shards & core GeoJSON encode integer decade == 2030
    core_blds = json.loads((data_dir / "buildings.geojson").read_text(encoding="utf-8"))["features"]
    new_2030 = [f for f in core_blds if f["properties"]["hcad_num"] in ("0140000000005", "0140000000006")]
    assert len(new_2030) == 2
    for ft in new_2030:
        assert ft["properties"]["year_built"] == 2030
        assert ft["properties"]["decade"] == 2030
        assert isinstance(ft["properties"]["decade"], int)

    stats = json.loads((data_dir / "stats_summary.json").read_text(encoding="utf-8"))
    assert stats["data_tax_year"] == 2030
    assert stats["decade_counts"]["2030"] == 2
    assert stats["decade_counts"]["unknown"] == 0

    sc = json.loads((data_dir / "neighborhood_scorecards.json").read_text(encoding="utf-8"))
    assert sc["data_tax_year"] == 2030
    assert 2030 in sc["decade_bins"]
    assert sc["demo_years"][-1] == 2030
    hd_card = next(
        c for c in sc["scorecards"] if c.get("ly") == "historic_districts" and c.get("nm") == ws["hd_name"]
    )
    assert "dm" in hd_card
    assert hd_card["dm"][-1] == 1
    idx_2030 = sc["decade_bins"].index(2030)
    assert hd_card["dh"][idx_2030] == 2
    for card in sc["scorecards"]:
        assert sum(card.get("dm") or ()) == card.get("dmt", 0)
        if "dh" in card:
            assert len(card["dh"]) == len(sc["decade_bins"])

    html_2030 = (repo_root / "app" / "index.html").read_text(encoding="utf-8")
    assert html_2030.count('<option value="2030">2030s Decade</option>') == 1


def test_it6_cohort5_read_only_isolation(tmp_path: Path) -> None:
    """IT-6: Cohort 5 lost_houston_candidates_2027.json captures pre-1940 teardown while keeping live overlays untouched."""
    ws = build_mini_county_workspace(tmp_path, baseline_year=2026, next_year=2027)
    data_dir: Path = ws["data_dir"]

    before_lost = json.loads((data_dir / "overlays.json").read_text(encoding="utf-8"))["lost_houston"]
    before_wl_bytes = (data_dir / "property_watchlist.json").read_bytes()

    res = run_annual_hcad_refresh(
        cache_dir=ws["cache_dir"],
        output_dir=data_dir,
        tax_year=2027,
        roll_dir=ws["roll_src_dir"],
        apply=True,
        cache_bust="20270901a",
        sanity_cfg=_relaxed_sanity_for_mini_county(),
        repo_root=ws["repo_root"],
    )

    c5_path = Path(res["reports"]["lost_houston_candidates"])
    c5_doc = json.loads(c5_path.read_text(encoding="utf-8"))
    assert c5_doc["read_only_review_queue"] is True
    assert c5_doc["candidate_count"] >= 1
    cands_by_hcad = {c["hcad_num"]: c for c in c5_doc["candidates"]}
    assert "0120000000003" in cands_by_hcad
    cand = cands_by_hcad["0120000000003"]
    assert cand["year_built"] == 1918
    assert cand["year_demolished"] == 2027
    assert cand["post_demo_status"] == "vacant_lot"
    assert "303 HEIGHTS BLVD" in cand["address"]
    lon_c, lat_c = cand["lon"], cand["lat"]
    assert HARRIS_COUNTY_BBOX[0] <= lon_c <= HARRIS_COUNTY_BBOX[2]
    assert HARRIS_COUNTY_BBOX[1] <= lat_c <= HARRIS_COUNTY_BBOX[3]

    # Live overlays.json["lost_houston"] and property_watchlist.json remain 100% untouched!
    after_lost = json.loads((data_dir / "overlays.json").read_text(encoding="utf-8"))["lost_houston"]
    assert after_lost == before_lost
    assert (data_dir / "property_watchlist.json").read_bytes() == before_wl_bytes


def test_it7_observed_footprints_dedup_and_6dp_ccw_normalization(tmp_path: Path) -> None:
    """IT-7: Observed footprint matching + deduplicate_geojsonseq_shard overlap cleanup + <= 6dp CCW rings."""
    ws = build_mini_county_workspace(tmp_path, baseline_year=2026, next_year=2027)
    data_dir: Path = ws["data_dir"]
    cache_dir: Path = ws["cache_dir"]

    # Provide a high-precision clockwise observed footprint for 0140000000005 AND inject a provisional
    # duplicate stack into baseline nw_e to verify deduplicate_geojsonseq_shard drops overlap cleanly
    obs_ndjson = tmp_path / "observed_ms_osm.geojsonseq"
    cw_high_prec_ring = [
        [-95.39589123456, 29.78991123456],
        [-95.39589123456, 29.79008987654],
        [-95.39570876543, 29.79008987654],
        [-95.39570876543, 29.78991123456],
        [-95.39589123456, 29.78991123456],
    ]
    obs_feat = {
        "type": "Feature",
        "properties": {"source": "microsoft"},
        "geometry": {"type": "Polygon", "coordinates": [cw_high_prec_ring]},
    }
    obs_ndjson.write_bytes(b"\x1e" + json.dumps(obs_feat).encode("utf-8") + b"\n")

    # Inject a synthetic overlapping rectangle right on top of baseline 0100000000001 across BOTH
    # the quadrant shard AND derived inner-core / address_shards / search_index artifacts to verify
    # post-dedup ArtifactSyncDelta purge removes ghost duplicate records everywhere.
    dup_syn = make_building_feature(
        "0100000000001",
        -95.3990,
        29.7900,
        building_id="0100000000001#dup_provisional",
        year_built=2025,
        address="101 HEIGHTS BLVD",
    )
    seq_nwe = cache_dir / "aligned_ndjson" / "buildings_nw_e.geojsonseq"
    with open(seq_nwe, "ab") as fh:
        fh.write(b"\x1e" + json.dumps(dup_syn, separators=(",", ":")).encode("utf-8") + b"\n")
    core_fc = json.loads((data_dir / "buildings.geojson").read_text(encoding="utf-8"))
    core_fc["features"].append(dup_syn)
    write_compact_json(data_dir / "buildings.geojson", core_fc)
    addr10_doc = json.loads((data_dir / "address_shards" / "addr_10.json").read_text(encoding="utf-8"))
    addr10_doc["HEIGHTS BLVD"].append(
        ["101", -95.399, 29.79, 2025, "0100000000001", "", "0100000000001#dup_provisional"]
    )
    write_compact_json(data_dir / "address_shards" / "addr_10.json", addr10_doc)
    sidx_doc = json.loads((data_dir / "search_index.json").read_text(encoding="utf-8"))
    sidx_doc.append(
        {
            "type": "building",
            "id": "0100000000001#dup_provisional",
            "building_id": "0100000000001#dup_provisional",
            "hcad_num": "0100000000001",
            "label": "Provisional Dup",
            "address": "101 HEIGHTS BLVD",
            "lon": -95.399,
            "lat": 29.79,
            "zoom": 17.5,
            "year_built": 2025,
        }
    )
    write_compact_json(data_dir / "search_index.json", sidx_doc)

    res = run_annual_hcad_refresh(
        cache_dir=cache_dir,
        output_dir=data_dir,
        tax_year=2027,
        roll_dir=ws["roll_src_dir"],
        apply=True,
        cache_bust="20270901a",
        observed_footprints_ndjson=obs_ndjson,
        sanity_cfg=_relaxed_sanity_for_mini_county(),
        repo_root=ws["repo_root"],
    )
    assert res["synthesis"]["observed_footprints_matched"] >= 1
    assert res["dedup_dropped_by_quad"].get("nw_e", 0) >= 1

    # Verify every output geometry in buildings.geojson and buildings_nw_e.geojsonseq:
    # - closed ring, RFC 7946 CCW exterior, <= 6 fractional digits, inside HARRIS_COUNTY_BBOX
    core_blds = json.loads((data_dir / "buildings.geojson").read_text(encoding="utf-8"))["features"]
    ids_out = {f["properties"]["id"] for f in core_blds}
    assert "0100000000001#dup_provisional" not in ids_out  # Older observed 1920 footprint won dedup!
    assert "0100000000001" in ids_out
    addr10_after = json.loads((data_dir / "address_shards" / "addr_10.json").read_text(encoding="utf-8"))[
        "HEIGHTS BLVD"
    ]
    addr10_ents = {(r[6] if len(r) > 6 else r[4]) for r in addr10_after}
    assert "0100000000001#dup_provisional" not in addr10_ents
    assert "0100000000001" in addr10_ents
    sidx_after_ids = {
        it["id"] for it in json.loads((data_dir / "search_index.json").read_text(encoding="utf-8"))
    }
    assert "0100000000001#dup_provisional" not in sidx_after_ids

    f_obs = next(f for f in core_blds if f["properties"]["hcad_num"] == "0140000000005")
    assert f_obs["properties"]["geometry_source"] == "observed_footprint"
    assert f_obs["properties"]["is_footprint"] is True

    coord_text = (data_dir / "buildings.geojson").read_text(encoding="utf-8")
    assert not re.search(r"-?\d+\.\d{7,}", coord_text), "Found coordinate exceeding 6 decimal places!"

    for feat in core_blds:
        poly = shape(feat["geometry"])
        assert poly.is_valid and not poly.is_empty
        ring = feat["geometry"]["coordinates"][0]
        assert ring[0] == ring[-1]
        assert Polygon(ring).exterior.is_ccw is True
        minx, miny, maxx, maxy = poly.bounds
        assert minx >= HARRIS_COUNTY_BBOX[0] and maxx <= HARRIS_COUNTY_BBOX[2]
        assert miny >= HARRIS_COUNTY_BBOX[1] and maxy <= HARRIS_COUNTY_BBOX[3]


# ---------------------------------------------------------------------------
# Post-Review Pass 1 Regressions: Stacked Condos, Multi-Building Sync,
# 5-Tuple Curated Chronologies, CAMA Note Normalization & Mid-Commit Rollback
# ---------------------------------------------------------------------------


def test_reg_stacked_condo_parcel_retention_and_shared_parcel_extraction(tmp_path: Path) -> None:
    """Issue #1, #7, #12: stacked-condo parcel retention, zip-slip/mtime extraction, and SOP 9 parity."""
    from atlas_pipeline.schema import extract_parcels_zip_dataset
    from atlas_pipeline.setup_admin_sheet import build_instructions_rows

    ws = build_mini_county_workspace(tmp_path, baseline_year=2026, next_year=2027)
    cache_dir: Path = ws["cache_dir"]
    data_dir: Path = ws["data_dir"]
    roll_src_dir: Path = ws["roll_src_dir"]

    # Seed baseline cache/Parcels.zip with 8 unique baseline polygons while expanding
    # baseline cama_2026_compact.pkl with 20 stacked-condo tax accounts sharing one parcel.
    # That makes new_parcel_count (10) / base_acct_cnt (28) == 0.357 (< 0.95), whereas
    # new_parcel_count (10) / base_pcl_cnt (8) == 1.25 (>= 0.95).
    base_coords = [
        ("0100000000001", (-95.3990, 29.7900)),
        ("0110000000002", (-95.3982, 29.7900)),
        ("0120000000003", (-95.3974, 29.7900)),
        ("0130000000004", (-95.3966, 29.7900)),
        ("0200000000007", (-95.3000, 29.7000)),
        ("0210000000008", (-95.6000, 29.8200)),
        ("0220000000009", (-95.3000, 29.8200)),
        ("0230000000010", (-95.6000, 29.7000)),
    ]
    base_zip_dir = tmp_path / "base_pcl_src"
    write_synthetic_roll_zips(
        base_zip_dir,
        tax_year=2026,
        real_acct_rows=[{"acct": a, "str_num": "1", "str": "MAIN", "yr_impr": "1950"} for a, _ in base_coords],
        parcel_records=[
            (a, wgs84_poly_to_2278(make_wgs84_box(lon, lat, half_w_deg=0.00018, half_h_deg=0.00018)))
            for a, (lon, lat) in base_coords
        ],
    )
    (cache_dir / "Parcels.zip").write_bytes((base_zip_dir / "Parcels.zip").read_bytes())

    base_cama = load_compact_cama_lookup(cache_dir, tax_year=2026) or {}
    for k in range(1, 21):
        base_cama[f"08800000000{k:02d}"] = (2005, 0, 1100, "A1", "Condo", f"CONDO {k}", f"101 HEIGHTS #{k}", 0.0)
    save_compact_cama_lookup(cache_dir, base_cama, tax_year=2026)

    # Also add those 20 condo accounts to the 2027 CAMA zips so account retention stays >= 95%
    # while parcel count remains strictly 10 polygons (< 28 tax accounts).
    with zipfile.ZipFile(roll_src_dir / "Real_acct_owner.zip", "r") as zf:
        acct_txt = zf.read("real_acct.txt").decode("latin-1")
        perm_bytes = zf.read("permits.txt")
        deed_bytes = zf.read("deeds.txt")
        tb_bytes = zf.read("parcel_tieback.txt")
    hdr_cols = acct_txt.splitlines()[0].split("\t")
    extra_lines: list[str] = []
    for k in range(1, 21):
        row_d = {
            "acct": f"08800000000{k:02d}",
            "yr": "2027",
            "mailto": f"CONDO {k}",
            "site_addr_1": f"101 HEIGHTS BLVD #{k}",
            "str_num": "101",
            "str": "HEIGHTS",
            "str_sfx": "BLVD",
            "str_unit": str(k),
            "state_class": "A1",
            "yr_impr": "2005",
            "bld_ar": "1100",
            "land_ar": "0",
            "bld_val": "180000",
            "certified_date": "08/15/2027",
        }
        extra_lines.append("\t".join(row_d.get(c, "") for c in hdr_cols))
    with zipfile.ZipFile(roll_src_dir / "Real_acct_owner.zip", "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("real_acct.txt", (acct_txt.rstrip("\n") + "\n" + "\n".join(extra_lines) + "\n").encode("latin-1"))
        zf.writestr("permits.txt", perm_bytes)
        zf.writestr("deeds.txt", deed_bytes)
        zf.writestr("parcel_tieback.txt", tb_bytes)

    # Without --allow-partial-roll, run_annual_hcad_refresh passes Stage 1 cleanly!
    res = run_annual_hcad_refresh(
        cache_dir=cache_dir,
        output_dir=data_dir,
        tax_year=2027,
        roll_dir=roll_src_dir,
        apply=True,
        cache_bust="20270901c",
        allow_partial_roll=False,
        sanity_cfg=RollSanityConfig(max_annual_demolition_fraction=0.25),
        repo_root=ws["repo_root"],
    )
    assert res["preflight"]["ok"] is True
    # Confirm single standardized _parcels_extracted scratch tree (never legacy _parcels_extract)
    assert (cache_dir / "rolls" / "2027" / "_parcels_extracted").is_dir()
    assert not (cache_dir / "rolls" / "2027" / "_parcels_extract").exists()
    staged_view_ext = cache_dir / "rolls" / "2027" / "staged" / "cache_view" / "_parcels_extracted"
    assert staged_view_ext.is_symlink()

    # Zip-slip guard & size+mtime invalidation in shared extract_parcels_zip_dataset
    evil_zip = tmp_path / "evil_parcels.zip"
    with zipfile.ZipFile(evil_zip, "w") as zf:
        zf.writestr("../escaped_payload.txt", b"nope")
    with pytest.raises(ValueError, match="unsafe path entry"):
        extract_parcels_zip_dataset(evil_zip, scratch_extract_dir=tmp_path / "evil_out")

    same_size_zip = tmp_path / "mtime_test.zip"
    ext_dir = tmp_path / "mtime_ext"
    with zipfile.ZipFile(same_size_zip, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("a.geojson", b'{"a":1}')
    extract_parcels_zip_dataset(same_size_zip, scratch_extract_dir=ext_dir)
    stale_sentinel = ext_dir / "stale_leftover.shp"
    stale_sentinel.write_bytes(b"stale")
    orig_st = same_size_zip.stat()
    with zipfile.ZipFile(same_size_zip, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("a.geojson", b'{"b":2}')
    import os as _os

    _os.utime(same_size_zip, ns=(orig_st.st_atime_ns, orig_st.st_mtime_ns + 1_000_000))
    assert same_size_zip.stat().st_size == orig_st.st_size
    extract_parcels_zip_dataset(same_size_zip, scratch_extract_dir=ext_dir)
    assert (ext_dir / "a.geojson").read_bytes() == b'{"b":2}'
    assert not stale_sentinel.exists()

    # Configurable RollSanityConfig.min_in_bbox_ratio enforced in validate_staged_hcad_roll
    mixed_bbox_dir = tmp_path / "mixed_bbox"
    p_9_in_1_out = [
        (f"09100000000{i:02d}", wgs84_poly_to_2278(make_wgs84_box(-95.38 + i * 0.001, 29.76)))
        for i in range(1, 10)
    ] + [("0910000000010", Polygon([(0, 0), (100, 0), (100, 100), (0, 100), (0, 0)]))]
    write_synthetic_roll_zips(
        mixed_bbox_dir,
        tax_year=2027,
        real_acct_rows=[
            {"acct": f"09100000000{i:02d}", "str_num": str(i), "str": "OAK", "yr_impr": "1950"}
            for i in range(1, 11)
        ],
        parcel_records=p_9_in_1_out,
    )
    m_ctx = RollContext(tax_year=2027, cache_dir=tmp_path / "mc", output_dir=tmp_path / "mo")
    assert validate_staged_hcad_roll(mixed_bbox_dir, ctx=m_ctx, sanity=RollSanityConfig(min_in_bbox_ratio=0.90))[
        "ok"
    ] is True
    with pytest.raises(RollValidationError, match="HARRIS_COUNTY_BBOX"):
        validate_staged_hcad_roll(mixed_bbox_dir, ctx=m_ctx, sanity=RollSanityConfig(min_in_bbox_ratio=0.95))

    # SOP 9 operator runbook matches Code_description_real.zip, pre-1940 Cohort 5 scope, and >= 90% bbox floor
    sop9_row = build_instructions_rows()[17]
    assert "SOP 9" in sop9_row[0]
    assert "Code_description_real.zip" in sop9_row[1]
    assert "Hearing_files.zip" not in sop9_row[1]
    assert "pre-1940" in sop9_row[1]
    assert "pre-1960" not in sop9_row[1]
    assert ">= 90% county bbox floor" in sop9_row[1]


def test_reg_multibuilding_reconciliation_addr_7col_and_partial_split(tmp_path: Path) -> None:
    """Issue #2 & #3: multi-building entity parity across buildings.geojson, search_index, 6/7-col addr, & export_cache."""
    roll_dir = tmp_path / "roll_mb"
    staged_data = tmp_path / "staged_mb"
    (staged_data / "address_shards").mkdir(parents=True)
    (staged_data / "permits").mkdir(parents=True)
    (staged_data / "export_cache").mkdir(parents=True)

    hd_name = "Test Multi Historic District"
    hd_slug = "historic_districts__test_multi_historic_district"
    hd_geom = make_wgs84_box(-95.3890, 29.7600, half_w_deg=0.020, half_h_deg=0.020)

    # Parcels in new 2027 roll:
    # - 0300000000001: PARTIAL_CURATED_SUBBUILDINGS campus (#chapel 1928 protected, #gym 1985 remodeled, #shed demolished)
    # - 0300000000002: UNCURATED_MULTI_FOOTPRINT (#front 1908, #rear 1930) owner updated
    # - 0300000000301 & 0300000000302: 1 -> 2 split children of retired 0300000000003 (#west 1922 -> 301, #east 1936 -> 302)
    # - 0300000000004: Multi-building NEW_CONSTRUCTION (#n1 & #n2) inside HD
    # - 0300000000005: Single-building named landmark updating owner (6-column addr row preservation)
    p_coords = {
        "0300000000001": (-95.3940, 29.7600, 0.00050),
        "0300000000002": (-95.3920, 29.7600, 0.00040),
        "0300000000301": (-95.3902, 29.7600, 0.00018),
        "0300000000302": (-95.3896, 29.7600, 0.00018),
        "0300000000004": (-95.3880, 29.7600, 0.00035),
        "0300000000005": (-95.3860, 29.7600, 0.00020),
    }
    parcel_records = [
        (acct, wgs84_poly_to_2278(make_wgs84_box(lon, lat, half_w_deg=hw, half_h_deg=0.00025)))
        for acct, (lon, lat, hw) in p_coords.items()
    ]
    write_synthetic_roll_zips(
        roll_dir,
        tax_year=2027,
        real_acct_rows=[
            {"acct": "0300000000001", "mailto": "NEW PARISH TRUSTEES", "site_addr_1": "200 LEE DR", "str_num": "200", "str": "LEE", "str_sfx": "DR", "yr_impr": "1985", "bld_ar": "6200"},
            {"acct": "0300000000002", "mailto": "DUPLEX STEWARD LLC", "site_addr_1": "210 LEE DR", "str_num": "210", "str": "LEE", "str_sfx": "DR", "yr_impr": "1908", "bld_ar": "3000"},
            {"acct": "0300000000301", "mailto": "WEST SPLIT OWNER", "site_addr_1": "301 LEE DR", "str_num": "301", "str": "LEE", "str_sfx": "DR", "yr_impr": "1922", "bld_ar": "1500"},
            {"acct": "0300000000302", "mailto": "EAST SPLIT OWNER", "site_addr_1": "303 LEE DR", "str_num": "303", "str": "LEE", "str_sfx": "DR", "yr_impr": "1936", "bld_ar": "1400"},
            {"acct": "0300000000004", "mailto": "COURTYARD HOMES LLC", "site_addr_1": "400 LEE DR", "str_num": "400", "str": "LEE", "str_sfx": "DR", "state_class": "B1", "yr_impr": "2027", "bld_ar": "4800"},
            {"acct": "0300000000005", "mailto": "LANDMARK BUYER", "site_addr_1": "500 LEE DR", "str_num": "500", "str": "LEE", "str_sfx": "DR", "yr_impr": "1924", "bld_ar": "2200"},
        ],
        building_res_rows=[
            {"acct": "0300000000001", "bld_num": "1", "date_erected": "1985", "yr_remodel": "2027", "dscr": "Good", "structure_dscr": "Gymnasium", "property_use_cd": "A1", "im_sq_ft": "4200"},
            {"acct": "0300000000002", "bld_num": "1", "date_erected": "1908", "dscr": "Avg", "structure_dscr": "Main House", "property_use_cd": "A1", "im_sq_ft": "1800"},
            {"acct": "0300000000002", "bld_num": "2", "date_erected": "1930", "dscr": "Avg", "structure_dscr": "Garage Apt", "property_use_cd": "A1", "im_sq_ft": "1200"},
            {"acct": "0300000000301", "bld_num": "1", "date_erected": "1922", "dscr": "Avg", "structure_dscr": "Bungalow", "property_use_cd": "A1", "im_sq_ft": "1500"},
            {"acct": "0300000000302", "bld_num": "1", "date_erected": "1936", "dscr": "Avg", "structure_dscr": "Cottage", "property_use_cd": "A1", "im_sq_ft": "1400"},
            {"acct": "0300000000004", "bld_num": "1", "date_erected": "2027", "dscr": "Good", "structure_dscr": "Bldg A", "property_use_cd": "B1", "im_sq_ft": "2400"},
            {"acct": "0300000000004", "bld_num": "2", "date_erected": "2027", "dscr": "Good", "structure_dscr": "Bldg B", "property_use_cd": "B1", "im_sq_ft": "2400"},
            {"acct": "0300000000005", "bld_num": "1", "date_erected": "1924", "yr_remodel": "2027", "dscr": "Good", "structure_dscr": "Villa", "property_use_cd": "A1", "im_sq_ft": "2200"},
        ],
        tieback_rows=[
            {"acct": "0300000000301", "tp": "S", "dscr": "SPLIT", "related_acct": "0300000000003", "pct": "50"},
            {"acct": "0300000000302", "tp": "S", "dscr": "SPLIT", "related_acct": "0300000000003", "pct": "50"},
        ],
        parcel_records=parcel_records,
    )

    snaps = stream_hcad_account_snapshots(
        roll_dir / "Real_building_land.zip", roll_dir / "Real_acct_owner.zip", tax_year=2027, prior_tax_year=2026
    )
    p_hcads, p_geoms, p_map, p_tree = load_parcel_geometries_2278(roll_dir / "Parcels.zip")

    curated_doc = {
        "schema_version": 2,
        "overrides": {
            "0300000000001#chapel": {
                "year_built": 1928,
                "name": "Historic Chapel",
                "keep_shard_footprints": True,
                "geometry": make_wgs84_box(-95.3942, 29.7600, half_w_deg=0.00008, half_h_deg=0.00008),
            }
        },
    }
    write_compact_json(staged_data / "curated_overrides.json", curated_doc)
    write_compact_json(
        staged_data / "overlays.json",
        {
            "historic_districts": {
                "type": "FeatureCollection",
                "features": [{"type": "Feature", "properties": {"name": hd_name}, "geometry": hd_geom}],
            },
            "lost_houston": {"type": "FeatureCollection", "features": []},
        },
    )

    f_chapel = make_building_feature(
        "0300000000001",
        -95.3942,
        29.7600,
        building_id="0300000000001#chapel",
        year_built=1928,
        year_source="archival",
        address="200 LEE DR",
        extra_props={"building_name": "Historic Chapel", "historic_district": hd_name},
    )
    f_gym = make_building_feature(
        "0300000000001",
        -95.3939,
        29.7600,
        building_id="0300000000001#gym",
        year_built=1985,
        address="200 LEE DR",
        extra_props={"building_name": "Parish Gym", "historic_district": hd_name},
    )
    f_shed = make_building_feature(
        "0300000000001",
        -95.3936,
        29.7600,
        building_id="0300000000001#shed",
        year_built=1992,
        address="200 LEE DR",
        extra_props={"building_name": "Old Shed", "historic_district": hd_name},
    )
    f_front = make_building_feature(
        "0300000000002",
        -95.3921,
        29.7600,
        building_id="0300000000002#front",
        year_built=1908,
        address="210 LEE DR",
        extra_props={"historic_district": hd_name},
    )
    f_rear = make_building_feature(
        "0300000000002",
        -95.3918,
        29.7600,
        building_id="0300000000002#rear",
        year_built=1930,
        address="210 LEE DR",
        extra_props={"historic_district": hd_name},
    )
    f_split_w = make_building_feature(
        "0300000000003",
        -95.3902,
        29.7600,
        building_id="0300000000003#west",
        year_built=1922,
        address="300 LEE DR",
        extra_props={"historic_district": hd_name},
    )
    f_split_e = make_building_feature(
        "0300000000003",
        -95.3896,
        29.7600,
        building_id="0300000000003#east",
        year_built=1936,
        address="300 LEE DR",
        extra_props={"historic_district": hd_name},
    )
    f_lm5 = make_building_feature(
        "0300000000005",
        -95.3860,
        29.7600,
        year_built=1924,
        year_source="COH Landmark Designation Report",
        address="500 LEE DR",
        extra_props={"is_landmark": True, "landmark_name": "Lee Villa", "historic_district": hd_name},
    )
    base_feats = [f_chapel, f_gym, f_shed, f_front, f_rear, f_split_w, f_split_e, f_lm5]
    write_compact_json(staged_data / "buildings.geojson", {"type": "FeatureCollection", "features": base_feats})
    write_compact_json(staged_data / "parcels.geojson", {"type": "FeatureCollection", "features": []})
    write_compact_json(staged_data / "pmtiles_manifest.json", {"version": 3, "data_tax_year": 2026})
    write_compact_json(staged_data / "stats_summary.json", {"data_tax_year": 2026})
    write_compact_json(
        staged_data / "search_index.json",
        [
            {
                "id": "0300000000001#chapel",
                "building_id": "0300000000001#chapel",
                "hcad_num": "0300000000001",
                "label": "Historic Chapel",
                "sublabel": "200 LEE DR · Built 1928",
                "year_built": 1928,
                "year_source": "archival",
                "address": "200 LEE DR",
                "coords": [-95.3942, 29.7600],
            },
            {
                "id": "0300000000001#gym",
                "building_id": "0300000000001#gym",
                "hcad_num": "0300000000001",
                "label": "Parish Gym",
                "sublabel": "200 LEE DR · Built 1985",
                "year_built": 1985,
                "address": "200 LEE DR",
                "coords": [-95.3939, 29.7600],
            },
            {
                "id": "0300000000001#shed",
                "building_id": "0300000000001#shed",
                "hcad_num": "0300000000001",
                "label": "Old Shed",
                "sublabel": "200 LEE DR · Built 1992",
                "year_built": 1992,
                "address": "200 LEE DR",
                "coords": [-95.3936, 29.7600],
            },
            {
                "id": "0300000000003#west",
                "building_id": "0300000000003#west",
                "hcad_num": "0300000000003",
                "label": "West Bungalow",
                "sublabel": "300 LEE DR · Built 1922",
                "year_built": 1922,
                "address": "300 LEE DR",
                "coords": [-95.3902, 29.7600],
            },
            {
                "id": "0300000000003#east",
                "building_id": "0300000000003#east",
                "hcad_num": "0300000000003",
                "label": "East Cottage",
                "sublabel": "300 LEE DR · Built 1936",
                "year_built": 1936,
                "address": "300 LEE DR",
                "coords": [-95.3896, 29.7600],
            },
        ],
    )
    write_compact_json(
        staged_data / "address_shards" / "addr_20.json",
        {
            "LEE DR": [
                ["200", -95.3942, 29.7600, 1928, "0300000000001", "Historic Chapel", "0300000000001#chapel"],
                ["200", -95.3939, 29.7600, 1985, "0300000000001", "Parish Gym", "0300000000001#gym"],
                ["200", -95.3936, 29.7600, 1992, "0300000000001", "Old Shed", "0300000000001#shed"],
                ["210", -95.3921, 29.7600, 1908, "0300000000002", "", "0300000000002#front"],
                ["210", -95.3918, 29.7600, 1930, "0300000000002", "", "0300000000002#rear"],
            ]
        },
    )
    write_compact_json(
        staged_data / "address_shards" / "addr_30.json",
        {
            "LEE DR": [
                ["300", -95.3902, 29.7600, 1922, "0300000000003", "West", "0300000000003#west"],
                ["300", -95.3896, 29.7600, 1936, "0300000000003", "East", "0300000000003#east"],
            ]
        },
    )
    write_compact_json(
        staged_data / "address_shards" / "addr_50.json",
        {"LEE DR": [["500", -95.3860, 29.7600, 1924, "0300000000005", "Lee Villa"]]},
    )
    write_compact_json(
        staged_data / "export_cache" / f"{hd_slug}.json",
        {
            "id": hd_slug,
            "layerKey": "historic_districts",
            "name": hd_name,
            "stats": {"buildingCount": len(base_feats), "earliestYear": 1908, "medianYear": 1928},
            "boundary": {"type": "Feature", "properties": {"name": hd_name}, "geometry": hd_geom},
            "buildings": base_feats,
        },
    )

    baseline_by_q: dict[str, list[dict[str, Any]]] = {q: [] for q in QUAD_NAMES}
    # Pass surviving campus siblings (#chapel, #gym) + #front/#rear + split #west/#east + #lm5 through Stage 3,
    # and simulate partial demolition of #shed on 0300000000001 plus 2-footprint new construction on 0300000000004.
    baseline_by_q["nw_e"] = [f_chapel, f_gym, f_front, f_rear, f_split_w, f_split_e, f_lm5]
    delta_out = classify_feature_delta(
        baseline_by_q,
        snaps,
        parcel_hcads=p_hcads,
        parcel_geoms_2278=p_geoms,
        parcel_hcad_to_idx=p_map,
        parcel_strtree=p_tree,
        tieback_map={"0300000000003": ["0300000000301", "0300000000302"]},
        curated_overrides=curated_doc,
        tax_year=2027,
        prior_tax_year=2026,
    )
    art_delta: ArtifactSyncDelta = delta_out["artifact_delta"]
    patched_by_q: dict[str, list[dict[str, Any]]] = delta_out["patched_by_quad"]
    # Record partial demolition of #shed sharing campus situs "200 LEE DR" while #chapel & #gym survive
    art_delta.deleted_building_ids.add("0300000000001#shed")

    obs_ndjson = tmp_path / "obs_mb.geojsonseq"
    obs_ndjson.write_text(
        "\n".join(
            json.dumps({"type": "Feature", "properties": {"hcad_num": "0300000000004"}, "geometry": make_wgs84_box(lon, 29.7600, half_w_deg=0.00008, half_h_deg=0.00008)})
            for lon in (-95.38815, -95.38785)
        )
        + "\n",
        encoding="utf-8",
    )
    used_ids = {f["properties"]["building_id"] for fl in patched_by_q.values() for f in fl}
    synthesize_new_construction_features(
        patched_by_q,
        art_delta,
        snaps,
        parcel_hcads=p_hcads,
        parcel_geoms_2278=p_geoms,
        parcel_hcad_to_idx=p_map,
        parcel_strtree=p_tree,
        bldg_zip_path=roll_dir / "Real_building_land.zip",
        acct_zip_path=roll_dir / "Real_acct_owner.zip",
        cache_dir=tmp_path,
        tax_year=2027,
        observed_footprints_ndjson=obs_ndjson,
        used_building_ids=used_ids,
    )

    assert art_delta.old_to_all_new_hcads["0300000000003"] == ["0300000000301", "0300000000302"]
    assert art_delta.old_fid_to_new_hcad["0300000000003#west"] == "0300000000301"
    assert art_delta.old_fid_to_new_hcad["0300000000003#east"] == "0300000000302"

    reconcile_derived_building_indexes(
        staged_data,
        patched_by_q,
        art_delta,
        snaps,
        parcel_geoms_2278=p_geoms,
        parcel_hcad_to_idx=p_map,
        bldg_zip_path=roll_dir / "Real_building_land.zip",
        acct_zip_path=roll_dir / "Real_acct_owner.zip",
        tax_year=2027,
        cache_bust="20270901m",
        shard_filenames=["houston_buildings_nw_e.pmtiles"],
        total_pmtiles_bytes=1024,
        compile_core_pmtiles=False,
    )

    # 1. buildings.geojson: #chapel stayed 1928, #gym remodeled 2027/1985, #shed gone, #front=1908 & #rear=1930 distinct,
    #    1->2 split mapped #west->301 and #east->302, AND both #n1/#n2 on 0300000000004 inserted!
    blds_out = json.loads((staged_data / "buildings.geojson").read_text(encoding="utf-8"))["features"]
    by_bid = {f["properties"]["building_id"]: f["properties"] for f in blds_out}
    assert "0300000000001#shed" not in by_bid
    assert by_bid["0300000000001#chapel"]["year_built"] == 1928
    assert by_bid["0300000000001#gym"]["year_built"] == 1985
    assert by_bid["0300000000001#gym"]["remodel_year"] == 2027
    assert by_bid["0300000000001#gym"]["owner"] == "NEW PARISH TRUSTEES"
    assert by_bid["0300000000002#front"]["year_built"] == 1908
    assert by_bid["0300000000002#rear"]["year_built"] == 1930
    assert by_bid["0300000000002#rear"]["owner"] == "DUPLEX STEWARD LLC"
    split_hcads = {p["hcad_num"]: p["year_built"] for p in by_bid.values() if p["hcad_num"] in ("0300000000301", "0300000000302")}
    assert split_hcads == {"0300000000301": 1922, "0300000000302": 1936}
    assert "0300000000004" in by_bid and "0300000000004#n2" in by_bid

    # 2. search_index.json: #chapel NOT clobbered by #gym; #shed deleted; 1->2 split items mapped to distinct HCADs
    s_out = {it["id"]: it for it in json.loads((staged_data / "search_index.json").read_text(encoding="utf-8"))}
    assert "0300000000001#shed" not in s_out
    assert s_out["0300000000001#chapel"]["year_built"] == 1928
    assert "Built 1928" in s_out["0300000000001#chapel"]["sublabel"]
    assert s_out["0300000000001#gym"]["year_built"] == 1985
    s_split_hcads = {it["hcad_num"]: it["year_built"] for it in s_out.values() if it["hcad_num"] in ("0300000000301", "0300000000302")}
    assert s_split_hcads == {"0300000000301": 1922, "0300000000302": 1936}

    # 3. export_cache/<hd>.json: #chapel stayed 1928, #front/#rear distinct, 1->2 split distinct, AND #n2 included!
    exp_out = json.loads((staged_data / "export_cache" / f"{hd_slug}.json").read_text(encoding="utf-8"))
    exp_by_bid = {f["properties"]["building_id"]: f["properties"] for f in exp_out["buildings"]}
    assert "0300000000001#shed" not in exp_by_bid
    assert exp_by_bid["0300000000001#chapel"]["year_built"] == 1928
    assert exp_by_bid["0300000000002#front"]["year_built"] == 1908
    assert exp_by_bid["0300000000002#rear"]["year_built"] == 1930
    exp_split = {p["hcad_num"]: p["year_built"] for p in exp_by_bid.values() if p["hcad_num"] in ("0300000000301", "0300000000302")}
    assert exp_split == {"0300000000301": 1922, "0300000000302": 1936}
    assert "0300000000004" in exp_by_bid and "0300000000004#n2" in exp_by_bid

    # 4. address_shards: 7-col rows preserved for #chapel & #gym while #shed pruned; 6-col Lee Villa row preserved!
    a20 = json.loads((staged_data / "address_shards" / "addr_20.json").read_text(encoding="utf-8"))["LEE DR"]
    a20_by_ent = {(r[6] if len(r) > 6 else r[4]): r for r in a20}
    assert "0300000000001#shed" not in a20_by_ent
    assert len(a20_by_ent["0300000000001#chapel"]) == 7
    assert a20_by_ent["0300000000001#chapel"][5] == "Historic Chapel"
    assert len(a20_by_ent["0300000000001#gym"]) == 7
    assert a20_by_ent["0300000000001#gym"][5] == "Parish Gym"
    assert "0300000000002#front" not in a20_by_ent and "0300000000002#rear" not in a20_by_ent

    a21 = json.loads((staged_data / "address_shards" / "addr_21.json").read_text(encoding="utf-8"))["LEE DR"]
    a21_by_ent = {(r[6] if len(r) > 6 else r[4]): r for r in a21}
    assert "0300000000002#front" in a21_by_ent and "0300000000002#rear" in a21_by_ent

    a50 = json.loads((staged_data / "address_shards" / "addr_50.json").read_text(encoding="utf-8"))["LEE DR"]
    assert len(a50) == 1 and len(a50[0]) == 6 and a50[0][5] == "Lee Villa"

    a40 = json.loads((staged_data / "address_shards" / "addr_40.json").read_text(encoding="utf-8"))["LEE DR"]
    a40_ents = {(r[6] if len(r) > 6 else r[4]) for r in a40}
    assert {"0300000000004", "0300000000004#n2"}.issubset(a40_ents)


def test_reg_5tuple_additional_curated_chronologies_and_cama_note_normalization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #4, #6, #9, #15: 5-tuple chronologies, appraiser-note relocation/XF normalization, & dynamic defaults."""
    import datetime
    import inspect
    import atlas_pipeline.audit_historical_accuracy as aha
    import atlas_pipeline.build_neighborhood_scorecards as bns
    import atlas_pipeline.schema as schema_mod

    # 1. Issue #4: 5-tuple ADDITIONAL_CURATED_PARCEL_CHRONOLOGIES ('0402330000134', Deady MS) unpacks cleanly
    roll_c = tmp_path / "roll_5tup"
    deady_hcad = "0402330000134"
    p_deady = wgs84_poly_to_2278(make_wgs84_box(-95.2800, 29.7050, half_w_deg=0.00025, half_h_deg=0.00025))
    write_synthetic_roll_zips(
        roll_c,
        tax_year=2027,
        real_acct_rows=[
            {"acct": deady_hcad, "mailto": "HOUSTON ISD", "site_addr_1": "2500 BROADWAY ST", "str_num": "2500", "str": "BROADWAY", "str_sfx": "ST", "state_class": "X1", "yr_impr": "0", "bld_ar": "12000"}
        ],
        building_other_rows=[
            {"acct": deady_hcad, "bld_num": "1", "date_erected": "0", "dscr": "School", "structure_dscr": "School Annex", "property_use_cd": "X1", "im_sq_ft": "12000"}
        ],
        parcel_records=[(deady_hcad, p_deady)],
    )
    snaps_c = stream_hcad_account_snapshots(
        roll_c / "Real_building_land.zip", roll_c / "Real_acct_owner.zip", tax_year=2027, prior_tax_year=2026
    )
    ph_c, pg_c, pm_c, pt_c = load_parcel_geometries_2278(roll_c / "Parcels.zip")
    patched_c: dict[str, list[dict[str, Any]]] = {q: [] for q in QUAD_NAMES}
    delta_c = ArtifactSyncDelta()
    delta_c.new_construction_hcads.add(deady_hcad)
    stats_c = synthesize_new_construction_features(
        patched_c,
        delta_c,
        snaps_c,
        parcel_hcads=ph_c,
        parcel_geoms_2278=pg_c,
        parcel_hcad_to_idx=pm_c,
        parcel_strtree=pt_c,
        bldg_zip_path=roll_c / "Real_building_land.zip",
        acct_zip_path=roll_c / "Real_acct_owner.zip",
        cache_dir=tmp_path,
        tax_year=2027,
        used_building_ids=set(),
    )
    assert stats_c["fallback_phase_counts"]["p1_curated_landmark"] == 1
    deady_feat = next(f for fl in patched_c.values() for f in fl if f["properties"]["hcad_num"] == deady_hcad)
    assert deady_feat["properties"]["year_built"] == 1926
    assert deady_feat["properties"]["landmark_name"] == "James S. Deady Middle School"
    assert deady_feat["properties"]["architect"] == "Harry D. Payne"

    # 2. Issue #6: Appraiser notes ('MOVED FROM ...', bare numbers, >240 truncation) + XF filtering & cap=6
    roll_n = tmp_path / "roll_notes"
    long_note = "HISTORIC BRICK VENEER ADDITION " * 12  # > 360 chars
    xf_rows = [
        {"acct": "0600000000002", "bld_num": "1", "cd": "RG1", "dscr": "RESIDENTIAL GAS LINE", "act_yr": "2020", "eff_yr": "2020", "uts": "1"},
        {"acct": "0600000000002", "bld_num": "1", "cd": "CRK", "dscr": "CRACKED FOUNDATION SLAB", "act_yr": "2019", "eff_yr": "2019", "uts": "1"},
        {"acct": "0600000000002", "bld_num": "1", "cd": "GAR", "dscr": "DETACHED GARAGE", "act_yr": "", "eff_yr": "2022", "uts": "1"},
        *[
            {"acct": "0600000000002", "bld_num": "1", "cd": f"XF{i}", "dscr": f"CANOPY DECK {i}", "act_yr": str(2015 + i), "eff_yr": "", "uts": "1"}
            for i in range(1, 9)
        ],
    ]
    write_synthetic_roll_zips(
        roll_n,
        tax_year=2027,
        real_acct_rows=[
            # Relocated pre-1940 structure (noted only in appraiser notes) that now has bld_ar=0 + demo cleanup permit
            {"acct": "0600000000001", "mailto": "DONOR LOT LLC", "str_num": "10", "str": "MAIN", "state_class": "C1", "yr_impr": "", "bld_ar": "0", "bld_val": "0"},
            {"acct": "0600000000002", "mailto": "PROSE NOTE OWNER", "str_num": "20", "str": "MAIN", "yr_impr": "1932", "bld_ar": "1800"},
            {"acct": "0600000000003", "mailto": "NUMERIC NOTE OWNER", "str_num": "30", "str": "MAIN", "yr_impr": "1940", "bld_ar": "1600"},
        ],
        building_res_rows=[
            {"acct": "0600000000001", "bld_num": "1", "date_erected": "", "im_sq_ft": "0", "notes": "HOUSE MOVED FROM LOT TO FORT BEND IN 2026"},
            {"acct": "0600000000002", "bld_num": "1", "date_erected": "1932", "im_sq_ft": "1800", "notes": long_note},
            {"acct": "0600000000003", "bld_num": "1", "date_erected": "1940", "im_sq_ft": "1600", "notes": "12345, 67890 / #44"},
        ],
        extra_features_rows=xf_rows,
        permits_rows=[
            {"acct": "0600000000001", "id": "P1", "permit_type": "13", "permit_tp_descr": "Demo", "dscr": "DEMO HOUSE UTILITY DISCONNECT", "issue_date": "03/01/2027", "yr": "2027"}
        ],
    )
    snaps_n = stream_hcad_account_snapshots(
        roll_n / "Real_building_land.zip", roll_n / "Real_acct_owner.zip", tax_year=2027, prior_tax_year=2026
    )
    assert snaps_n["0600000000001"].was_relocated is True
    ph_n, pg_n, pm_n, pt_n = load_parcel_geometries_2278(roll_n / "Parcels.zip")
    base_n: dict[str, list[dict[str, Any]]] = {q: [] for q in QUAD_NAMES}
    base_n["sw"] = [make_building_feature("0600000000001", -95.3800, 29.7500, year_built=1912, address="10 MAIN ST")]
    delta_n = classify_feature_delta(
        base_n,
        snaps_n,
        parcel_hcads=ph_n,
        parcel_geoms_2278=pg_n,
        parcel_hcad_to_idx=pm_n,
        parcel_strtree=pt_n,
        tieback_map={},
        curated_overrides={},
        tax_year=2027,
        prior_tax_year=2026,
        enforce_circuit_breaker=False,
    )
    assert delta_n["lost_houston_candidates"] == []  # Relocated house blocked from Cohort 5!

    enr = _stream_targeted_enrichment_records(
        {"0600000000001", "0600000000002", "0600000000003"},
        snaps_n,
        roll_n / "Real_building_land.zip",
        roll_n / "Real_acct_owner.zip",
        tax_year=2027,
    )
    assert "MOVED FROM" in enr["0600000000001"]["reloc"]
    assert len(enr["0600000000002"]["note"]) == 240
    assert "note" not in enr.get("0600000000003", {})  # Bare numeric permit list omitted
    xf_out = enr["0600000000002"]["xf"]
    assert len(xf_out) == schema_mod.EXTRA_FEATURES_CAP_PER_ACCOUNT == 6
    assert all("GAS" not in x["d"] and "CRACKED" not in x["d"] for x in xf_out)
    gar_row = next(x for x in xf_out if x["d"] == "DETACHED GARAGE")
    assert gar_row["y"] == 0  # eff_yr NOT used when act_yr is blank
    assert [x["y"] for x in xf_out if x["d"].startswith("CANOPY DECK ")] == [2016, 2017, 2018, 2019, 2020]

    # 3. Issue #9 & #15: Dynamic runtime default parameters + strict load_compact_cama_lookup bare-dict guard
    assert inspect.signature(bns.parse_demolition_permit_row).parameters["max_year"].default is None
    assert inspect.signature(bns.hydrate_land_use_protections_lifecycle).parameters["ref_year"].default is None
    assert inspect.signature(bns.fetch_or_load_coh_ytd_demo_permits).parameters["tax_year"].default is None
    assert inspect.signature(build_override_protection_index).parameters["default_updated_year"].default is None
    assert inspect.signature(aha.build_historical_audit_queue_payload).parameters["generated_on"].default is None

    q_payload = aha.build_historical_audit_queue_payload([])
    assert q_payload["generated_on"] == datetime.date.today().isoformat()

    monkeypatch.setattr(schema_mod, "ACTIVE_TAX_YEAR", 2030)
    monkeypatch.setattr(schema_mod, "MAX_VALID_CAMA_DATE_YEAR", 2031)
    assert 2030 in bns.DECADE_BINS
    assert bns.DEMO_YEARS[-1] == 2030
    parsed_pm = bns.parse_demolition_permit_row(
        acct="0600000000099", permit_type="13", dscr="DEMO SFR HOUSE", issue_date="06/15/2030", roll_yr="2030"
    )
    assert parsed_pm == ("0600000000099", 2030)
    ov_dyn = build_override_protection_index(
        {"overrides": {"0600000000099": {"year_built": 0, "suppress_only": True}}}
    )
    assert ov_dyn["suppressed_vacant_by_hcad"]["0600000000099"]["updated_year"] == 2030

    # Bare legacy dict placed at cama_2030_compact.pkl MUST be rejected when ACTIVE_TAX_YEAR == 2030 & tax_year=None
    bare_dir = tmp_path / "bare_2030"
    bare_dir.mkdir()
    with open(bare_dir / "cama_2030_compact.pkl", "wb") as fh:
        pickle.dump({"0600000000099": (1950, 0, 1200, "A1", "SFR", "OWN", "1 MAIN", 5000.0)}, fh)
    assert load_compact_cama_lookup(bare_dir) is None


def test_reg_mid_commit_fault_rolls_back_all_live_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #5: Mid-StagedRollPromotion.commit() OSError or AnchorRule write failure restores all live paths."""
    import os as _os
    import atlas_pipeline.schema as schema_mod
    import atlas_pipeline.update_annual_hcad_roll as upd_mod

    # Scenario A: crash mid-way through Phase B os.replace swaps AFTER live shards & .pmtiles are overwritten
    ws_a = build_mini_county_workspace(tmp_path / "mc_a", baseline_year=2026, next_year=2027)
    pre_a = _snapshot_tree_sha256(
        ws_a["repo_root"], exclude_rel_prefixes=("pipeline/cache/rolls",)
    )
    real_replace = _os.replace
    swap_counter = {"n": 0}

    def _flaky_replace(src: Any, dst: Any) -> None:
        src_s = str(src)
        if ".promote.tmp" in src_s:
            swap_counter["n"] += 1
            if swap_counter["n"] == 4:
                raise OSError("Simulated ENOSPC on 4th live os.replace")
        real_replace(src, dst)

    with monkeypatch.context() as mp:
        mp.setattr(upd_mod.os, "replace", _flaky_replace)
        with pytest.raises(OSError, match="Simulated ENOSPC on 4th live os.replace"):
            run_annual_hcad_refresh(
                cache_dir=ws_a["cache_dir"],
                output_dir=ws_a["data_dir"],
                tax_year=2027,
                roll_dir=ws_a["roll_src_dir"],
                apply=True,
                cache_bust="20270901x",
                sanity_cfg=_relaxed_sanity_for_mini_county(),
                repo_root=ws_a["repo_root"],
            )
    assert swap_counter["n"] == 4  # Confirmed 3 live targets were already replaced before crash!
    post_a = _snapshot_tree_sha256(
        ws_a["repo_root"], exclude_rel_prefixes=("pipeline/cache/rolls",)
    )
    assert post_a == pre_a
    assert list(ws_a["repo_root"].rglob("*.promote.tmp")) == []

    # Scenario B: crash mid-loop during Step 4 sync_repo_tax_year_and_cache_bust(..., dry_run=False)
    # AFTER ALL binary/JSON targets swapped AND after 2 tracked repo files were already rewritten!
    ws_b = build_mini_county_workspace(tmp_path / "mc_b", baseline_year=2026, next_year=2027)
    pre_b = _snapshot_tree_sha256(
        ws_b["repo_root"], exclude_rel_prefixes=("pipeline/cache/rolls",)
    )
    real_path_replace = Path.replace
    sync_swap_counter = {"n": 0}

    def _fail_on_third_sync_swap(self: Path, target: Path) -> Path:
        if self.name.endswith(".sync.tmp"):
            sync_swap_counter["n"] += 1
            if sync_swap_counter["n"] == 3:
                raise PermissionError("Simulated AnchorRule write failure on 3rd .sync.tmp swap")
        return real_path_replace(self, target)

    with monkeypatch.context() as mp:
        mp.setattr(Path, "replace", _fail_on_third_sync_swap)
        with pytest.raises(PermissionError, match="Simulated AnchorRule write failure on 3rd"):
            run_annual_hcad_refresh(
                cache_dir=ws_b["cache_dir"],
                output_dir=ws_b["data_dir"],
                tax_year=2027,
                roll_dir=ws_b["roll_src_dir"],
                apply=True,
                cache_bust="20270901y",
                sanity_cfg=_relaxed_sanity_for_mini_county(),
                repo_root=ws_b["repo_root"],
            )
    assert sync_swap_counter["n"] == 3  # Confirmed 2 tracked repo files were rewritten before fault!
    post_b = _snapshot_tree_sha256(
        ws_b["repo_root"], exclude_rel_prefixes=("pipeline/cache/rolls",)
    )
    assert post_b == pre_b
    assert list(ws_b["repo_root"].rglob("*.promote.tmp")) == []
    assert list(ws_b["repo_root"].rglob("*.sync.tmp")) == []
    assert list(ws_b["repo_root"].rglob("*.rollback.tmp")) == []


def test_reg_pass2_review_fixes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pass 2 regressions: M->1 order-independent IDs, clean deed/permit parity, school_*/lng splits, dynamic defaults, and normalize_ring_6dp."""
    import datetime as _dt
    import inspect

    import atlas_pipeline.schema as schema_mod
    from atlas_pipeline.fix_polygon_orientations import batch_transform_polys_2278_to_rings_4326

    # 1. Pass 2 Issue #2: M -> 1 consolidation from lower retired HCAD ('010...') onto higher active HCAD ('090...')
    # MUST NOT collide with '0900000000001#b1' even though '010...' sorts before '090...'!
    roll_m1 = tmp_path / "roll_m1_order"
    west_box = make_wgs84_box(-95.3820, 29.7520, half_w_deg=0.00018, half_h_deg=0.00018)
    east_box = make_wgs84_box(-95.3814, 29.7520, half_w_deg=0.00018, half_h_deg=0.00018)
    merged_parcel = wgs84_poly_to_2278(
        {
            "type": "Polygon",
            "coordinates": [[
                [-95.3824, 29.7516],
                [-95.3810, 29.7516],
                [-95.3810, 29.7524],
                [-95.3824, 29.7524],
                [-95.3824, 29.7516],
            ]],
        }
    )
    write_synthetic_roll_zips(
        roll_m1,
        tax_year=2027,
        real_acct_rows=[
            {
                "acct": "0900000000001",
                "mailto": "MERGED OWNER LLC",
                "str_num": "500",
                "str": "WESTHEIMER",
                "str_sfx": "RD",
                "yr_impr": "1935",
                "bld_ar": "3600",
                "land_ar": "12000",
            }
        ],
        building_res_rows=[
            {"acct": "0900000000001", "bld_num": "1", "date_erected": "1935", "im_sq_ft": "1800"},
            {"acct": "0900000000001", "bld_num": "2", "date_erected": "1948", "im_sq_ft": "1800"},
        ],
        parcel_records=[("0900000000001", merged_parcel)],
    )
    snaps_m1 = stream_hcad_account_snapshots(
        roll_m1 / "Real_building_land.zip",
        roll_m1 / "Real_acct_owner.zip",
        tax_year=2027,
        prior_tax_year=2026,
    )
    ph_m1, pg_m1, pm_m1, pt_m1 = load_parcel_geometries_2278(roll_m1 / "Parcels.zip")
    base_m1: dict[str, list[dict[str, Any]]] = {q: [] for q in QUAD_NAMES}
    f_retired = make_building_feature(
        "0100000000001",
        -95.3820,
        29.7520,
        building_id="0100000000001#b1",
        year_built=1935,
        address="500 WESTHEIMER RD",
    )
    f_retired["geometry"] = west_box
    f_surviving = make_building_feature(
        "0900000000001",
        -95.3814,
        29.7520,
        building_id="0900000000001#b1",
        year_built=1948,
        address="502 WESTHEIMER RD",
    )
    f_surviving["geometry"] = east_box
    base_m1["sw"] = [f_retired, f_surviving]
    delta_m1 = classify_feature_delta(
        base_m1,
        snaps_m1,
        parcel_hcads=ph_m1,
        parcel_geoms_2278=pg_m1,
        parcel_hcad_to_idx=pm_m1,
        parcel_strtree=pt_m1,
        tieback_map={"0100000000001": ["0900000000001"]},
        curated_overrides={},
        tax_year=2027,
        prior_tax_year=2026,
        enforce_circuit_breaker=False,
    )
    sw_feats = delta_m1["patched_by_quad"]["sw"]
    assert len(sw_feats) == 2
    out_bids = [f["properties"]["building_id"] for f in sw_feats]
    assert len(set(out_bids)) == 2, f"Duplicate building_id on M->1 merge: {out_bids}"
    assert set(out_bids) == {"0900000000001#b1", "0900000000001#b1_0001"}

    # 2. Pass 2 Issue #3: No bogus D<deed_id> fallback tokens + byte-exact permit parity across >25 & undated rows
    roll_dp = tmp_path / "roll_deed_permit_parity"
    long_pdesc = "EXTERIOR MASONRY RESTORATION AND WINDOW SASH REHABILITATION PERMIT " * 3
    p_rows = [
        {
            "acct": "0610000000001",
            "id": "P_UNDATED",
            "permit_type": "05",
            "permit_tp_descr": "Remodel",
            "dscr": long_pdesc,
            "issue_date": "",
            "yr": "2025",
        },
        {
            "acct": "0610000000001",
            "id": "P_RELOC",
            "permit_type": "15",
            "permit_tp_descr": "Move Permit",
            "dscr": "MOVE HOUSE ONTO NEW PIER FOUNDATION",
            "issue_date": "04/10/2027",
            "yr": "2027",
        },
        *[
            {
                "acct": "0610000000001",
                "id": f"P_{i:02d}",
                "permit_type": "07",
                "permit_tp_descr": "Re-Roof",
                "dscr": f"REROOF SECTION {i}",
                "issue_date": f"05/{(i % 27) + 1:02d}/{1998 + i}",
                "yr": str(1998 + i),
            }
            for i in range(1, 29)
        ],
    ]
    write_synthetic_roll_zips(
        roll_dp,
        tax_year=2027,
        real_acct_rows=[
            {"acct": "0610000000001", "mailto": "PARITY OWNER", "str_num": "101", "str": "MAIN", "yr_impr": "1925", "bld_ar": "2000"},
            {"acct": "0610000000002", "mailto": "BLANK CLERK OWNER", "str_num": "103", "str": "MAIN", "yr_impr": "1930", "bld_ar": "1800"},
        ],
        building_res_rows=[
            {"acct": "0610000000001", "bld_num": "1", "date_erected": "1925", "im_sq_ft": "2000"},
            {"acct": "0610000000002", "bld_num": "1", "date_erected": "1930", "im_sq_ft": "1800"},
        ],
        permits_rows=p_rows,
        deeds_rows=[
            # Blank clerk_id with internal deed_id MUST be skipped (never emit bogus 'D145892')!
            {"acct": "0610000000002", "dos": "05/10/2001", "clerk_yr": "2001", "clerk_id": "", "deed_id": "145892"},
            {"acct": "0610000000001", "dos": "06/01/2020", "clerk_yr": "2020", "clerk_id": "", "deed_id": "999111"},
            {"acct": "0610000000001", "dos": "07/15/2027", "clerk_yr": "2027", "clerk_id": "RP-2027-445566", "deed_id": "999222"},
        ],
    )
    snaps_dp = stream_hcad_account_snapshots(
        roll_dp / "Real_building_land.zip",
        roll_dp / "Real_acct_owner.zip",
        tax_year=2027,
        prior_tax_year=2026,
    )
    targeted_dp = _stream_targeted_enrichment_records(
        {"0610000000001", "0610000000002"},
        snaps_dp,
        roll_dp / "Real_building_land.zip",
        roll_dp / "Real_acct_owner.zip",
        tax_year=2027,
    )
    assert "d" not in targeted_dp["0610000000002"]
    assert targeted_dp["0610000000001"]["d"] == "20270715:R2027-445566"
    assert "D999111" not in targeted_dp["0610000000001"]["d"]

    _cama_f, _xf_f, full_permits, full_reloc, _addr_f = build_hcad_permits_and_cama_index(
        {"0610000000001"}, tax_year=2027, cache_dir=roll_dp
    )
    assert len(targeted_dp["0610000000001"]["p"]) == 25
    assert targeted_dp["0610000000001"]["p"] == full_permits["0610000000001"]
    assert targeted_dp["0610000000001"]["p"][0]["dt"] == "2027-04-10"
    assert targeted_dp["0610000000001"]["reloc"] == full_reloc["0610000000001"]["note"]

    # Also verify undated permit formatting does NOT fabricate roll_yr or truncate descriptions at 120 chars
    p_undated_norm, _ = schema_mod.normalize_hcad_permit_row(
        "P_UNDATED", long_pdesc, "05", "Remodel", "", max_year=2027
    )
    assert p_undated_norm["dt"] == ""
    assert len(p_undated_norm["d"]) > 120

    # 3. Pass 2 Issue #4: school_* 1 -> K split + building_id remap + contractor 'lng' spatial containment
    ddir_sc = tmp_path / "data_school_split"
    ddir_sc.mkdir()
    write_compact_json(ddir_sc / "buildings.geojson", {"type": "FeatureCollection", "features": []})
    write_compact_json(ddir_sc / "overlays.json", {"landmarks": {"type": "FeatureCollection", "features": []}, "good_brick_awards": {"type": "FeatureCollection", "features": []}})
    write_compact_json(
        ddir_sc / "search_index.json",
        [
            {
                "type": "building",
                "id": "school_west",
                "building_id": "0870000000001#b1",
                "hcad_num": "0870000000001",
                "address": "1400 CAMPUS WAY",
                "school_situs_hydrated": True,
                "lat": 29.7520,
                "lon": -95.3820,
            },
            {
                "type": "building",
                "id": "school_east",
                "building_id": "0870000000001#b2",
                "hcad_num": "0870000000001",
                "address": "1410 CAMPUS WAY",
                "school_situs_hydrated": True,
                "lat": 29.7520,
                "lon": -95.3810,
            },
            {
                "type": "building",
                "id": "bld_contractor_east",
                "building_id": "bld_contractor_east",
                "hcad_num": "0870000000001",
                "address": "1412 CAMPUS WAY",
                "lat": 29.7520,
                "lng": -95.3810,  # 'lng' without 'lon' from build_permits_and_contractors.py!
            },
        ],
    )
    west_child_2278 = wgs84_poly_to_2278(
        make_wgs84_box(-95.3820, 29.7520, half_w_deg=0.00025, half_h_deg=0.00025)
    )
    east_child_2278 = wgs84_poly_to_2278(
        make_wgs84_box(-95.3810, 29.7520, half_w_deg=0.00025, half_h_deg=0.00025)
    )
    delta_sc = ArtifactSyncDelta()
    delta_sc.old_to_new_hcads["0870000000001"] = "0870000000011"
    delta_sc.old_to_all_new_hcads["0870000000001"] = ["0870000000011", "0870000000012"]
    delta_sc.old_to_new_building_ids["0870000000001#b1"] = "0870000000011#b1"
    delta_sc.old_to_new_building_ids["0870000000001#b2"] = "0870000000012#b2"
    reconcile_derived_building_indexes(
        ddir_sc,
        {},
        delta_sc,
        {},
        parcel_geoms_2278=np.array([west_child_2278, east_child_2278], dtype=object),
        parcel_hcad_to_idx={"0870000000011": 0, "0870000000012": 1},
        bldg_zip_path=roll_dp / "Real_building_land.zip",
        acct_zip_path=roll_dp / "Real_acct_owner.zip",
        tax_year=2027,
        cache_bust="20270815s",
        shard_filenames=[],
        total_pmtiles_bytes=0,
        compile_core_pmtiles=False,
    )
    sidx_out = {
        row["id"]: row
        for row in json.loads((ddir_sc / "search_index.json").read_text(encoding="utf-8"))
    }
    assert sidx_out["school_west"]["hcad_num"] == "0870000000011"
    assert sidx_out["school_west"]["building_id"] == "0870000000011#b1"
    assert sidx_out["school_west"]["address"] == "1400 CAMPUS WAY"
    assert sidx_out["school_east"]["hcad_num"] == "0870000000012"
    assert sidx_out["school_east"]["building_id"] == "0870000000012#b2"
    assert sidx_out["school_east"]["address"] == "1410 CAMPUS WAY"
    assert sidx_out["bld_contractor_east"]["hcad_num"] == "0870000000012"

    # 4. Pass 2 Issue #6: Dynamic run_audit generated_on default & runtime RollContext factory defaults
    assert inspect.signature(run_audit).parameters["generated_on"].default is None
    with monkeypatch.context() as mp:
        mp.setattr(schema_mod, "ACTIVE_TAX_YEAR", 2031)
        mp.setattr(schema_mod, "ACTIVE_CACHE_BUST", "20310901z")
        dyn_ctx = RollContext()
        assert dyn_ctx.tax_year == 2031
        assert dyn_ctx.max_cama_date_year == 2031
        assert dyn_ctx.max_valid_year == 2032
        assert dyn_ctx.cache_bust == "20310901z"
        assert len(_dt.date.today().isoformat()) == 10

    # 5. Pass 2 Issue #7: batch_transform_polys_2278_to_rings_4326 collapses sub-foot duplicate 6dp vertices
    to_4326 = Transformer.from_crs("EPSG:2278", "EPSG:4326", always_xy=True)
    sliver_poly = Polygon([
        (3120000.00, 13840000.00),
        (3120040.00, 13840000.00),
        (3120040.02, 13840000.02),  # < 0.05 ft jitter -> rounds to identical 6dp WGS84 vertex!
        (3120040.00, 13840030.00),
        (3120000.00, 13840030.00),
        (3120000.00, 13840000.00),
    ])
    rings_out = batch_transform_polys_2278_to_rings_4326([sliver_poly], to_4326)
    assert len(rings_out) == 1
    ring_0 = rings_out[0]
    assert len(ring_0) == 5  # 4 unique corners + closing point (duplicate 6dp vertex collapsed!)
    for idx_v in range(len(ring_0) - 1):
        assert ring_0[idx_v] != ring_0[idx_v + 1]

    # 6. Pass 2 Issue #8: Scorecard AnchorRules stay zero-diff at 2026 baseline and rewrite cleanly for 2027
    repo_real = Path(__file__).resolve().parents[2]
    dry_base = sync_repo_tax_year_and_cache_bust(
        repo_real, tax_year=ACTIVE_TAX_YEAR, cache_bust=ACTIVE_CACHE_BUST, dry_run=True
    )
    assert dry_base["modified_files"] == []
    fake_sc_repo = tmp_path / "fake_sc_repo"
    for rel_p in {rule.rel_path for rule in schema_mod.REPO_SYNC_ANCHOR_RULES}:
        src_p = repo_real / rel_p
        if src_p.exists():
            dst_p = fake_sc_repo / rel_p
            dst_p.parent.mkdir(parents=True, exist_ok=True)
            dst_p.write_bytes(src_p.read_bytes())
    sync_repo_tax_year_and_cache_bust(fake_sc_repo, tax_year=2027, cache_bust="20270815b", dry_run=False)
    synced_html = (fake_sc_repo / "app/index.html").read_text(encoding="utf-8")
    assert 'Sort: Expiring Ch. 42 Ordinances (2027-2032)' in synced_html
    assert 'Sort: Highest 2020-2027 Demolitions' in synced_html
    assert 'id="sc-th-sunset" class="sc-col-num" title="Pre-July 24, 2015 Chapter 42 ordinances reaching 20-year sunset in 2027-2032">Sunset 27-32</th>' in synced_html
    assert 'id="sc-th-demos" class="sc-col-num" title="Main-structure teardown parcels permitted 2020-2027">Demos</th>' in synced_html


def test_reg_pass3_review_fixes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pass 3 regressions: Stage 3b healed undated sync, demolished school_* pruning, decade option reversibility, .rollback.tmp safety, & core PMTiles byte sum."""
    import atlas_pipeline.schema as schema_mod

    # 1. Pass 3 High #2: Stage 3b healed undated building on an UNCHANGED parcel propagates across all derived indexes
    roll_h = tmp_path / "roll_heal"
    ddir_h = tmp_path / "data_heal"
    for sub in ("address_shards", "permits", "export_cache"):
        (ddir_h / sub).mkdir(parents=True)
    hd_name = "Test Heal Historic District"
    hd_slug = "historic_districts__test_heal_historic_district"
    hd_geom = make_wgs84_box(-95.3985, 29.7900, half_w_deg=0.0040, half_h_deg=0.0040)
    p1 = wgs84_poly_to_2278(make_wgs84_box(-95.3990, 29.7900, half_w_deg=0.00018, half_h_deg=0.00018))
    p2 = wgs84_poly_to_2278(make_wgs84_box(-95.3982, 29.7900, half_w_deg=0.00018, half_h_deg=0.00018))
    write_synthetic_roll_zips(
        roll_h,
        tax_year=2027,
        real_acct_rows=[
            {
                "acct": "0100000000091",
                "mailto": "HEALED OWNER",
                "site_addr_1": "109 HEIGHTS BLVD",
                "str_num": "109",
                "str": "HEIGHTS",
                "str_sfx": "BLVD",
                "state_class": "A1",
                "yr_impr": "",
                "bld_ar": "1500",
                "lgl_2": "HOUSTON HEIGHTS",
            },
            {
                "acct": "0100000000092",
                "mailto": "NEIGHBOR OWNER",
                "site_addr_1": "111 HEIGHTS BLVD",
                "str_num": "111",
                "str": "HEIGHTS",
                "str_sfx": "BLVD",
                "state_class": "A1",
                "yr_impr": "1924",
                "bld_ar": "1500",
                "lgl_2": "HOUSTON HEIGHTS",
            },
        ],
        building_res_rows=[
            {"acct": "0100000000091", "bld_num": "1", "date_erected": "", "im_sq_ft": "1500"},
            {"acct": "0100000000092", "bld_num": "1", "date_erected": "1924", "im_sq_ft": "1500"},
        ],
        extra_features_rows=[
            {"acct": "0100000000091", "bld_num": "1", "cd": "GAR", "dscr": "DETACHED GARAGE", "act_yr": "1926", "uts": "1"}
        ],
        parcel_records=[("0100000000091", p1), ("0100000000092", p2)],
    )
    snaps_h = stream_hcad_account_snapshots(
        roll_h / "Real_building_land.zip",
        roll_h / "Real_acct_owner.zip",
        tax_year=2027,
        prior_tax_year=2026,
    )
    ph_h, pg_h, pm_h, pt_h = load_parcel_geometries_2278(roll_h / "Parcels.zip")
    f_undated = make_building_feature(
        "0100000000091",
        -95.3990,
        29.7900,
        year_built=0,
        address="109 HEIGHTS BLVD",
        owner="HEALED OWNER",
        extra_props={"historic_district": hd_name, "subdivision": "HOUSTON HEIGHTS"},
    )
    f_neighbor = make_building_feature(
        "0100000000092",
        -95.3982,
        29.7900,
        year_built=1924,
        address="111 HEIGHTS BLVD",
        owner="NEIGHBOR OWNER",
        extra_props={"historic_district": hd_name, "subdivision": "HOUSTON HEIGHTS"},
    )
    patched_h: dict[str, list[dict[str, Any]]] = {q: [] for q in QUAD_NAMES}
    patched_h["nw_e"] = [copy.deepcopy(f_undated), copy.deepcopy(f_neighbor)]
    write_compact_json(
        ddir_h / "buildings.geojson",
        {"type": "FeatureCollection", "features": [copy.deepcopy(f_undated), copy.deepcopy(f_neighbor)]},
    )
    write_compact_json(ddir_h / "parcels.geojson", {"type": "FeatureCollection", "features": []})
    write_compact_json(
        ddir_h / "address_shards" / "addr_10.json",
        {"HEIGHTS BLVD": [["109", -95.399, 29.79, 0, "0100000000091"]]},
    )
    write_compact_json(
        ddir_h / "search_index.json",
        [
            {
                "type": "building",
                "id": "0100000000091",
                "hcad_num": "0100000000091",
                "label": "109 HEIGHTS BLVD",
                "address": "109 HEIGHTS BLVD",
                "sublabel": "Built 0000 - HCAD #0100000000091",
                "lon": -95.399,
                "lat": 29.79,
                "zoom": 17.5,
                "year_built": 0,
            }
        ],
    )
    write_compact_json(ddir_h / "permits" / "permits_010.json", {})
    write_compact_json(
        ddir_h / "overlays.json",
        {
            "historic_districts": {
                "type": "FeatureCollection",
                "features": [{"type": "Feature", "properties": {"name": hd_name}, "geometry": hd_geom}],
            },
            "landmarks": {"type": "FeatureCollection", "features": []},
            "heritage_districts": {"type": "FeatureCollection", "features": []},
            "nrhp_districts": {"type": "FeatureCollection", "features": []},
            "thc_markers": {"type": "FeatureCollection", "features": []},
        },
    )
    write_compact_json(
        ddir_h / "export_cache" / f"{hd_slug}.json",
        {
            "buildings": [copy.deepcopy(f_undated), copy.deepcopy(f_neighbor)],
            "stats": {"buildingCount": 2, "earliestYear": 1924, "medianYear": 1924, "landmarkCount": 0, "goodBrickCount": 0},
        },
    )
    write_compact_json(ddir_h / "curated_overrides.json", {"schema_version": 2, "overrides": {}})
    write_compact_json(ddir_h / "stats_summary.json", {"data_tax_year": 2026})
    update_pmtiles_manifest_v3(
        ddir_h / "pmtiles_manifest.json",
        total_buildings=2,
        building_shards=["houston_buildings_nw_e.pmtiles"],
        data_tax_year=2026,
        cache_bust=ACTIVE_CACHE_BUST,
    )

    delta_h = ArtifactSyncDelta()  # Empty initial delta simulating an UNCHANGED standing parcel!
    syn_h = synthesize_new_construction_features(
        patched_h,
        delta_h,
        snaps_h,
        parcel_hcads=ph_h,
        parcel_geoms_2278=pg_h,
        parcel_hcad_to_idx=pm_h,
        parcel_strtree=pt_h,
        bldg_zip_path=roll_h / "Real_building_land.zip",
        acct_zip_path=roll_h / "Real_acct_owner.zip",
        cache_dir=tmp_path,
        tax_year=2027,
        used_building_ids={"0100000000091", "0100000000092"},
    )
    assert syn_h["undated_parcels_resolved"] == 1
    assert delta_h.inner_core_touched is True
    assert "010" in delta_h.touched_permit_prefixes
    assert len(delta_h.upserted_features_by_hcad["0100000000091"]) == 1
    healed_yr = int(delta_h.upserted_features_by_hcad["0100000000091"][0]["properties"]["year_built"])
    assert healed_yr == 1926

    reconcile_derived_building_indexes(
        ddir_h,
        patched_h,
        delta_h,
        snaps_h,
        parcel_geoms_2278=pg_h,
        parcel_hcad_to_idx=pm_h,
        bldg_zip_path=roll_h / "Real_building_land.zip",
        acct_zip_path=roll_h / "Real_acct_owner.zip",
        tax_year=2027,
        cache_bust="20270901h",
        shard_filenames=["houston_buildings_nw_e.pmtiles"],
        total_pmtiles_bytes=2048,
        compile_core_pmtiles=False,
    )
    b_healed = {
        f["properties"]["hcad_num"]: f["properties"]["year_built"]
        for f in json.loads((ddir_h / "buildings.geojson").read_text(encoding="utf-8"))["features"]
    }
    assert b_healed["0100000000091"] == 1926
    a_healed = json.loads((ddir_h / "address_shards" / "addr_10.json").read_text(encoding="utf-8"))["HEIGHTS BLVD"]
    assert a_healed[0][3] == 1926
    s_healed = json.loads((ddir_h / "search_index.json").read_text(encoding="utf-8"))[0]
    assert s_healed["year_built"] == 1926
    assert "Built 1926" in s_healed["sublabel"]
    p_healed = json.loads((ddir_h / "permits" / "permits_010.json").read_text(encoding="utf-8"))
    assert p_healed["0100000000091"]["xf"][0]["y"] == 1926
    e_healed = {
        f["properties"]["hcad_num"]: f["properties"]["year_built"]
        for f in json.loads((ddir_h / "export_cache" / f"{hd_slug}.json").read_text(encoding="utf-8"))["buildings"]
    }
    assert e_healed["0100000000091"] == 1926

    # 2. Pass 3 Medium #6: Demolished school_* row pruned on 1 -> 2 split while curated school_* row survives
    ddir_sp = tmp_path / "data_school_prune"
    ddir_sp.mkdir()
    write_compact_json(ddir_sp / "buildings.geojson", {"type": "FeatureCollection", "features": []})
    write_compact_json(ddir_sp / "parcels.geojson", {"type": "FeatureCollection", "features": []})
    write_compact_json(ddir_sp / "stats_summary.json", {"data_tax_year": 2026})
    write_compact_json(ddir_sp / "pmtiles_manifest.json", {"version": 3})
    write_compact_json(
        ddir_sp / "curated_overrides.json",
        {
            "schema_version": 2,
            "overrides": {"0870000000001#b4": {"year_built": 1915, "name": "Protected Schoolhouse"}},
        },
    )
    write_compact_json(
        ddir_sp / "search_index.json",
        [
            {
                "type": "building",
                "id": "school_keep",
                "building_id": "0870000000001#b1",
                "hcad_num": "0870000000001",
                "lat": 29.7520,
                "lon": -95.3820,
            },
            {
                "type": "building",
                "id": "school_portable_demo",
                "building_id": "0870000000001#b3",
                "hcad_num": "0870000000001",
                "lat": 29.7520,
                "lon": -95.3810,
            },
            {
                "type": "building",
                "id": "school_curated_keep",
                "building_id": "0870000000001#b4",
                "hcad_num": "0870000000001",
                "lat": 29.7520,
                "lon": -95.3810,
            },
        ],
    )
    west_2278 = wgs84_poly_to_2278(make_wgs84_box(-95.3820, 29.7520, half_w_deg=0.00025, half_h_deg=0.00025))
    east_2278 = wgs84_poly_to_2278(make_wgs84_box(-95.3810, 29.7520, half_w_deg=0.00025, half_h_deg=0.00025))
    delta_sp = ArtifactSyncDelta()
    delta_sp.old_to_new_hcads["0870000000001"] = "0870000000011"
    delta_sp.old_to_all_new_hcads["0870000000001"] = ["0870000000011", "0870000000012"]
    delta_sp.old_to_new_building_ids["0870000000001#b1"] = "0870000000011#b1"
    delta_sp.deleted_building_ids.add("0870000000001#b3")
    delta_sp.deleted_building_ids.add("0870000000001#b4")  # Curated override MUST block pruning!
    reconcile_derived_building_indexes(
        ddir_sp,
        {},
        delta_sp,
        {},
        parcel_geoms_2278=np.array([west_2278, east_2278], dtype=object),
        parcel_hcad_to_idx={"0870000000011": 0, "0870000000012": 1},
        bldg_zip_path=roll_h / "Real_building_land.zip",
        acct_zip_path=roll_h / "Real_acct_owner.zip",
        tax_year=2027,
        cache_bust="20270901p",
        shard_filenames=[],
        total_pmtiles_bytes=0,
        compile_core_pmtiles=False,
    )
    sp_out = {
        row["id"]: row
        for row in json.loads((ddir_sp / "search_index.json").read_text(encoding="utf-8"))
    }
    assert "school_portable_demo" not in sp_out
    assert sp_out["school_keep"]["hcad_num"] == "0870000000011"
    assert sp_out["school_keep"]["building_id"] == "0870000000011#b1"
    assert "school_curated_keep" in sp_out

    # 3. Pass 3 Medium #7: Bidirectional decade option pruning (2030 -> 2027) & clean .rollback.tmp lifecycle
    repo_real = Path(__file__).resolve().parents[2]
    fake_rev = tmp_path / "fake_reversible_repo"
    for rel_p in {rule.rel_path for rule in schema_mod.REPO_SYNC_ANCHOR_RULES}:
        src_p = repo_real / rel_p
        if src_p.exists():
            dst_p = fake_rev / rel_p
            dst_p.parent.mkdir(parents=True, exist_ok=True)
            dst_p.write_bytes(src_p.read_bytes())

    sync_repo_tax_year_and_cache_bust(fake_rev, tax_year=2030, cache_bust="20300901a", dry_run=False)
    html_2030 = (fake_rev / "app/index.html").read_text(encoding="utf-8")
    assert '<option value="2030">2030s Decade</option>' in html_2030
    assert list(fake_rev.rglob("*.rollback.tmp")) == []
    assert list(fake_rev.rglob("*.sync.tmp")) == []

    sync_repo_tax_year_and_cache_bust(fake_rev, tax_year=2027, cache_bust="20270901a", dry_run=False)
    html_2027 = (fake_rev / "app/index.html").read_text(encoding="utf-8")
    assert '<option value="2030">2030s Decade</option>' not in html_2027
    assert '<option value="2020">2020s Decade</option>' in html_2027
    assert list(fake_rev.rglob("*.rollback.tmp")) == []

    snap_2027 = _snapshot_tree_sha256(fake_rev)
    orig_replace = Path.replace
    swap_calls = 0

    def _boom_on_third_swap(self_p: Path, target_p: Path) -> Path:
        nonlocal swap_calls
        if str(self_p).endswith(".sync.tmp"):
            swap_calls += 1
            if swap_calls == 3:
                raise OSError("Simulated mid-swap disk error")
        return orig_replace(self_p, target_p)

    with monkeypatch.context() as mp:
        mp.setattr(Path, "replace", _boom_on_third_swap)
        with pytest.raises(OSError, match="Simulated mid-swap disk error"):
            sync_repo_tax_year_and_cache_bust(
                fake_rev, tax_year=2031, cache_bust="20310901a", dry_run=False
            )
    assert _snapshot_tree_sha256(fake_rev) == snap_2027
    assert list(fake_rev.rglob("*.rollback.tmp")) == []
    assert list(fake_rev.rglob("*.sync.tmp")) == []

    # 4. Pass 3 Low #11a: stats_summary.json["pmtiles_size_bytes"] equals exact sum of shards + rebuilt houston_atlas.pmtiles
    ws_pm = build_mini_county_workspace(tmp_path / "ws_pm_bytes", baseline_year=2026, next_year=2027)
    run_annual_hcad_refresh(
        cache_dir=ws_pm["cache_dir"],
        output_dir=ws_pm["data_dir"],
        tax_year=2027,
        roll_dir=ws_pm["roll_src_dir"],
        apply=True,
        cache_bust="20270901b",
        sanity_cfg=_relaxed_sanity_for_mini_county(),
        repo_root=ws_pm["repo_root"],
    )
    expected_pm_bytes = sum(
        (ws_pm["data_dir"] / f"houston_buildings_{q}.pmtiles").stat().st_size for q in QUAD_NAMES
    ) + (ws_pm["data_dir"] / "houston_atlas.pmtiles").stat().st_size
    stats_pm = json.loads((ws_pm["data_dir"] / "stats_summary.json").read_text(encoding="utf-8"))
    assert stats_pm["pmtiles_size_bytes"] == expected_pm_bytes


def test_reg_pass4_review_fixes(tmp_path: Path) -> None:
    """Pass 4 regressions: exhausted-parcel post-dedup delta sync, curated dedup shield, clerk RP year cap, & obsolete PMTiles skip."""
    from atlas_pipeline.enrich_deed_chains import normalize_hcad_clerk_instrument
    from atlas_pipeline.full_build import deduplicate_geojsonseq_shard
    from atlas_pipeline.schema import RollContext
    from atlas_pipeline.update_annual_hcad_roll import StagedRollPromotion, _compute_projected_app_size_bytes

    # 1. Pass 4 Medium #3: Clerk RP regex loop enforces y_val <= eff_max upper bound
    assert normalize_hcad_clerk_instrument("2027", "RP-2062-123456", max_year=2027) is None
    assert normalize_hcad_clerk_instrument("2027", "2092-54321", max_year=2027) is None
    assert normalize_hcad_clerk_instrument("2027", "2045-RP-999999", max_year=2027) is None
    assert normalize_hcad_clerk_instrument("2032", "RP-2032-123456", max_year=2032) == "R2032-123456"

    # 2. Pass 4 Medium #2: deduplicate_geojsonseq_shard protects is_curated_override=True footprints
    # against overlapping older uncurated footprints AND >=2 smaller overlapping footprints
    seq_cur = tmp_path / "buildings_curated_shield.geojsonseq"
    f_cur = make_building_feature("0700000000001", -95.3900, 29.7600, year_built=1928)
    f_cur["properties"]["is_curated_override"] = True
    f_older_overlap = make_building_feature("0700000000002", -95.39005, 29.7600, year_built=1910)
    with open(seq_cur, "wb") as fh:
        for ft in (f_cur, f_older_overlap):
            fh.write(b"\x1e" + json.dumps(ft, separators=(",", ":")).encode("utf-8") + b"\n")
    _, init_c, kept_c = deduplicate_geojsonseq_shard(seq_cur)
    assert init_c == 2 and kept_c == 1
    kept_lines = [
        json.loads(ln[1:] if ln.startswith(b"\x1e") else ln)
        for ln in seq_cur.read_bytes().splitlines()
        if ln.strip()
    ]
    assert kept_lines[0]["properties"]["hcad_num"] == "0700000000001"

    # 3. Pass 4 High #1: Exhausted parcel dropped in Stage 4 overlap dedup purges ghost 'bld_*' core/search/export
    # rows AND drops synthesized NEW_CONSTRUCTION parcels from permits shards & new_construction_hcads
    roll_ex = tmp_path / "roll_exhausted"
    ddir_ex = tmp_path / "data_exhausted"
    cdir_ex = tmp_path / "cache_exhausted"
    for sub in ("address_shards", "permits", "export_cache"):
        (ddir_ex / sub).mkdir(parents=True)
    cdir_ex.mkdir(parents=True)
    hd_name = "Test Exhausted HD"
    hd_slug = "historic_districts__test_exhausted_hd"
    hd_geom = make_wgs84_box(-95.3900, 29.7600, half_w_deg=0.0040, half_h_deg=0.0040)
    pcl_dom = wgs84_poly_to_2278(make_wgs84_box(-95.3900, 29.7600, half_w_deg=0.00030, half_h_deg=0.00030))
    write_synthetic_roll_zips(
        roll_ex,
        tax_year=2027,
        real_acct_rows=[
            {"acct": "0710000000001", "mailto": "DOM OWNER", "site_addr_1": "10 DOM ST", "str_num": "10", "str": "DOM", "str_sfx": "ST", "yr_impr": "1920", "bld_ar": "2400"},
            {"acct": "0720000000002", "mailto": "GHOST CORE OWNER", "site_addr_1": "20 DOM ST", "str_num": "20", "str": "DOM", "str_sfx": "ST", "yr_impr": "1960", "bld_ar": "1200"},
            {"acct": "0730000000003", "mailto": "SYN NEW OWNER", "site_addr_1": "30 DOM ST", "str_num": "30", "str": "DOM", "str_sfx": "ST", "yr_impr": "2027", "bld_ar": "1100"},
        ],
        building_res_rows=[
            {"acct": "0710000000001", "bld_num": "1", "date_erected": "1920", "im_sq_ft": "2400"},
            {"acct": "0720000000002", "bld_num": "1", "date_erected": "1960", "im_sq_ft": "1200"},
            {"acct": "0730000000003", "bld_num": "1", "date_erected": "2027", "im_sq_ft": "1100"},
        ],
        parcel_records=[("0710000000001", pcl_dom), ("0720000000002", pcl_dom), ("0730000000003", pcl_dom)],
    )
    snaps_ex = stream_hcad_account_snapshots(
        roll_ex / "Real_building_land.zip", roll_ex / "Real_acct_owner.zip", tax_year=2027, prior_tax_year=2026
    )
    _ph_e, pg_e, pm_e, _pt_e = load_parcel_geometries_2278(roll_ex / "Parcels.zip")

    # Dominant 1920 non-synthetic octagon overlapping two synthetic 5-point axis-aligned boxes on 072.. & 073..
    dom_feat = make_building_feature("0710000000001", -95.3900, 29.7600, year_built=1920, address="10 DOM ST")
    ghost_shard = make_building_feature(
        "0720000000002", -95.3900, 29.7600, building_id="0720000000002#b1", year_built=1960, address="20 DOM ST"
    )
    syn_new = make_building_feature("0730000000003", -95.3900, 29.7600, year_built=2027, address="30 DOM ST")
    patched_ex: dict[str, list[dict[str, Any]]] = {q: [] for q in QUAD_NAMES}
    patched_ex["nw_e"] = [dom_feat, ghost_shard, syn_new]

    ctx_ex = RollContext(tax_year=2027, cache_bust="20270901x", cache_dir=cdir_ex, output_dir=ddir_ex)
    prom_ex = StagedRollPromotion(ctx_ex)
    prom_ex.reset_staging_dirs()
    sd_ex = prom_ex.staged_data
    for sub in ("address_shards", "permits", "export_cache"):
        (sd_ex / sub).mkdir(parents=True, exist_ok=True)

    # Seed legacy 'bld_legacy_072' (building_id absent) in core buildings.geojson, search_index, and export_cache
    legacy_core_feat = copy.deepcopy(ghost_shard)
    legacy_core_feat["properties"]["id"] = "bld_legacy_072"
    legacy_core_feat["properties"].pop("building_id", None)
    write_compact_json(
        sd_ex / "buildings.geojson",
        {"type": "FeatureCollection", "features": [copy.deepcopy(dom_feat), legacy_core_feat]},
    )
    write_compact_json(
        sd_ex / "parcels.geojson",
        {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": dom_feat["geometry"], "properties": {"id": "pcl_071", "hcad_num": "0710000000001"}},
                {"type": "Feature", "geometry": ghost_shard["geometry"], "properties": {"id": "pcl_072", "hcad_num": "0720000000002"}},
            ],
        },
    )
    write_compact_json(
        sd_ex / "address_shards" / "addr_20.json",
        {"DOM ST": [["20", -95.39, 29.76, 1960, "0720000000002"]]},
    )
    write_compact_json(
        sd_ex / "search_index.json",
        [
            {"type": "building", "id": "bld_legacy_072", "hcad_num": "0720000000002", "label": "20 DOM ST", "lat": 29.76, "lon": -95.39},
            {"type": "building", "id": "school_legacy_072", "hcad_num": "0720000000002", "label": "Old Annex", "lat": 29.76, "lon": -95.39},
        ],
    )
    write_compact_json(sd_ex / "permits" / "permits_072.json", {"0720000000002": {"y": 1960}})
    write_compact_json(
        sd_ex / "overlays.json",
        {
            "historic_districts": {
                "type": "FeatureCollection",
                "features": [{"type": "Feature", "properties": {"name": hd_name}, "geometry": hd_geom}],
            }
        },
    )
    write_compact_json(
        sd_ex / "export_cache" / f"{hd_slug}.json",
        {
            "buildings": [copy.deepcopy(dom_feat), copy.deepcopy(legacy_core_feat)],
            "stats": {"buildingCount": 2, "earliestYear": 1920, "medianYear": 1920, "landmarkCount": 0, "goodBrickCount": 0},
        },
    )
    write_compact_json(sd_ex / "curated_overrides.json", {"schema_version": 2, "overrides": {}})
    write_compact_json(sd_ex / "stats_summary.json", {"data_tax_year": 2026})
    write_compact_json(sd_ex / "pmtiles_manifest.json", {"version": 3})

    art_ex = ArtifactSyncDelta()
    art_ex.dirty_quadrants.add("nw_e")
    art_ex.new_construction_hcads.add("0730000000003")
    art_ex.upserted_features_by_hcad["0730000000003"] = [syn_new]

    dropped_ex = prom_ex.write_and_deduplicate_dirty_shards(patched_ex, {"nw_e"}, art_delta=art_ex)
    assert dropped_ex["nw_e"] == 2
    assert "0720000000002" in art_ex.deleted_hcads
    assert "0730000000003" in art_ex.deleted_hcads
    assert "0730000000003" not in art_ex.new_construction_hcads
    assert "0730000000003" not in art_ex.upserted_features_by_hcad

    reconcile_derived_building_indexes(
        sd_ex,
        patched_ex,
        art_ex,
        snaps_ex,
        parcel_geoms_2278=pg_e,
        parcel_hcad_to_idx=pm_e,
        bldg_zip_path=roll_ex / "Real_building_land.zip",
        acct_zip_path=roll_ex / "Real_acct_owner.zip",
        tax_year=2027,
        cache_bust="20270901x",
        shard_filenames=["houston_buildings_nw_e.pmtiles"],
        total_pmtiles_bytes=512,
        compile_core_pmtiles=False,
    )
    b_ex_ids = {f["properties"]["id"] for f in json.loads((sd_ex / "buildings.geojson").read_text(encoding="utf-8"))["features"]}
    assert b_ex_ids == {"0710000000001"}
    p_ex_hcads = {f["properties"]["hcad_num"] for f in json.loads((sd_ex / "parcels.geojson").read_text(encoding="utf-8"))["features"]}
    assert p_ex_hcads == {"0710000000001"}
    assert json.loads((sd_ex / "address_shards" / "addr_20.json").read_text(encoding="utf-8")) == {}
    assert json.loads((sd_ex / "search_index.json").read_text(encoding="utf-8")) == []
    assert json.loads((sd_ex / "permits" / "permits_072.json").read_text(encoding="utf-8")) == {}
    assert not (sd_ex / "permits" / "permits_073.json").exists()
    exp_ex_ids = {f["properties"]["id"] for f in json.loads((sd_ex / "export_cache" / f"{hd_slug}.json").read_text(encoding="utf-8"))["buildings"]}
    assert exp_ex_ids == {"0710000000001"}

    # 4. Pass 4 Low #8: obsolete_live_rels skips slated-for-deletion empty dirty quadrant PMTiles
    (ddir_ex / "houston_buildings_se.pmtiles").write_bytes(b"X" * 4096)
    sz_with, _ = _compute_projected_app_size_bytes(ddir_ex, sd_ex)
    sz_without, _ = _compute_projected_app_size_bytes(
        ddir_ex, sd_ex, obsolete_live_rels={"houston_buildings_se.pmtiles"}
    )
    assert sz_with - sz_without == 4096


