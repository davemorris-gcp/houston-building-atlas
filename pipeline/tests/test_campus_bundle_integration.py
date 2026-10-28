"""Cross-artifact integration and regression tests for countywide campus dating and school situs linkage (IT-1..IT-3, REG-2, REG-4)."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

import orjson
import pytest

from atlas_pipeline.fix_campus_anomalies_and_schools import (
    ACTIVE_CACHE_BUST,
    LEE_COLLEGE_MAIN_BUILDINGS,
    QUAD_NAMES,
    VALID_NUMBERED_SITUS_RE,
)
from atlas_pipeline.schema import MAX_VALID_CAMA_DATE_YEAR, MIN_VALID_YEAR

REPO_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_DIR = REPO_ROOT / "pipeline"
ALIGNED_NDJSON_DIR = PIPELINE_DIR / "cache" / "aligned_ndjson"
DATA_DIR = REPO_ROOT / "app" / "public" / "data"
ADDRESS_SHARDS_DIR = DATA_DIR / "address_shards"

TRACKED_HCADS: frozenset[str] = frozenset(
    {
        "1223580020001",  # Cypress Ridge HS & Eldridge Transportation Center
        "1468640010001",  # Robert E. Lee HS (1928 main + 1954 shops)
        "0451440030163",  # IMPACT ECHS + Lee College Main Campus
        "0500610000001",  # Lee College Tech-Voc 5,6,7 & 9
        "0500620000001",  # Lee College Physical Plant / Transportation / Shipping
        "0500580000001",  # Lee College Tech-Voc 10
        "0500500000006",  # Lee College Campus Security Facility
        "1378920010001",  # Lee College PAC (2008) & McNulty-Haddick (1952)
        "0451440050245",  # George Washington Carver Elementary (1928)
        "0410210010510",  # Ashbel Smith Elementary (1996)
        "0402330000134",  # James S. Deady Middle School (1926 Harry D. Payne)
        "0010330000018",  # Sweeney, Coombs & Fredericks Building (1889)
        "0011020000001",  # Union Station / Minute Maid Park (1911)
        "0421790000001",  # Rice University campus
        "0421790000004",  # Hermann Park / Houston Zoo
    }
)


@pytest.fixture(scope="module")
def shard_scan_summary() -> dict[str, Any]:
    """Single-pass orjson scan across all 5 aligned .geojsonseq regional shards."""
    total_count = 0
    dated_in_range_count = 0
    out_of_range_samples: list[tuple[str, Any]] = []
    sentinel_permit_deed_le_1901: list[tuple[str, int, str]] = []
    pre_1945_campus_contiguity: list[tuple[str, int, str]] = []
    commercial_school_clobbers: list[tuple[str, str, str]] = []
    genuine_1900_count = 0
    genuine_1901_count = 0
    pre_1945_total_count = 0
    all_building_ids: set[str] = set()
    tracked_by_id: dict[str, dict[str, Any]] = {}
    tracked_by_hcad: dict[str, list[dict[str, Any]]] = {h: [] for h in TRACKED_HCADS}
    per_quad_counts: dict[str, int] = {}

    for quad in QUAD_NAMES:
        shard_path = ALIGNED_NDJSON_DIR / f"buildings_{quad}.geojsonseq"
        assert shard_path.is_file(), f"Missing aligned shard {shard_path}"
        quad_count = 0
        with shard_path.open("rb") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue
                if line.startswith(b"\x1e"):
                    line = line[1:]
                feat = orjson.loads(line)
                props = feat.get("properties") or {}
                total_count += 1
                quad_count += 1

                bid = str(props.get("building_id") or props.get("id") or feat.get("id") or "")
                hcad = str(props.get("hcad_num") or "")
                if bid:
                    all_building_ids.add(bid)

                yr = props.get("year_built")
                ysrc = str(props.get("year_source") or "")
                if isinstance(yr, int) and MIN_VALID_YEAR <= yr <= MAX_VALID_CAMA_DATE_YEAR:
                    dated_in_range_count += 1
                    if yr < 1945:
                        pre_1945_total_count += 1
                    if yr == 1900:
                        genuine_1900_count += 1
                    elif yr == 1901:
                        genuine_1901_count += 1
                    if yr <= 1901 and ysrc in ("hcad_permit", "hcad_deed"):
                        if len(sentinel_permit_deed_le_1901) < 25:
                            sentinel_permit_deed_le_1901.append((bid or hcad, yr, ysrc))
                    if yr < 1945 and ysrc == "campus_contiguity":
                        if len(pre_1945_campus_contiguity) < 25:
                            pre_1945_campus_contiguity.append((bid or hcad, yr, ysrc))
                else:
                    if len(out_of_range_samples) < 25:
                        out_of_range_samples.append((bid or hcad, yr))

                st_cls = str(props.get("state_class") or "").strip().upper()
                if st_cls.startswith(("F1", "F2")) and props.get("school_situs_hydrated"):
                    commercial_school_clobbers.append((hcad, st_cls, str(props.get("address") or "")))

                if hcad in TRACKED_HCADS:
                    snap = dict(props)
                    snap["_quad"] = quad
                    tracked_by_hcad[hcad].append(snap)
                    if bid:
                        tracked_by_id[bid] = snap
        per_quad_counts[quad] = quad_count

    return {
        "total_count": total_count,
        "per_quad_counts": per_quad_counts,
        "dated_in_range_count": dated_in_range_count,
        "out_of_range_samples": out_of_range_samples,
        "sentinel_permit_deed_le_1901": sentinel_permit_deed_le_1901,
        "pre_1945_campus_contiguity": pre_1945_campus_contiguity,
        "commercial_school_clobbers": commercial_school_clobbers,
        "genuine_1900_count": genuine_1900_count,
        "genuine_1901_count": genuine_1901_count,
        "pre_1945_total_count": pre_1945_total_count,
        "all_building_ids": all_building_ids,
        "tracked_by_id": tracked_by_id,
        "tracked_by_hcad": tracked_by_hcad,
    }


@pytest.fixture(scope="module")
def curated_overrides() -> dict[str, dict[str, Any]]:
    raw = json.loads((DATA_DIR / "curated_overrides.json").read_text(encoding="utf-8"))
    if isinstance(raw, dict) and isinstance(raw.get("overrides"), dict):
        return raw["overrides"]
    return raw


@pytest.fixture(scope="module")
def search_index() -> list[dict[str, Any]]:
    return json.loads((DATA_DIR / "search_index.json").read_text(encoding="utf-8"))


def test_it1_countywide_shards_pmtiles_stats_and_manifest_invariants(
    shard_scan_summary: dict[str, Any],
) -> None:
    """IT-1: Verify 100% countywide dating, zero sentinel/pre-1945 contiguity leaks, and PMTiles/stats parity."""
    total = shard_scan_summary["total_count"]
    assert total == 1_507_897, f"Expected 1,507,897 countywide footprints, got {total}"
    assert shard_scan_summary["dated_in_range_count"] == total, (
        f"Out-of-range or undated footprints found: {shard_scan_summary['out_of_range_samples']}"
    )
    assert not shard_scan_summary["sentinel_permit_deed_le_1901"], (
        f"Found <= 1901 permit/deed sentinel leaks: {shard_scan_summary['sentinel_permit_deed_le_1901']}"
    )
    assert not shard_scan_summary["pre_1945_campus_contiguity"], (
        f"Found < 1945 campus_contiguity leaks: {shard_scan_summary['pre_1945_campus_contiguity']}"
    )

    # Verify all 5 regional PMTiles v3 archives exist, carry valid v3 magic headers, and match manifest metadata
    manifest = json.loads((DATA_DIR / "pmtiles_manifest.json").read_text(encoding="utf-8"))
    assert manifest.get("cache_bust") == ACTIVE_CACHE_BUST
    assert manifest.get("total_buildings") == total
    assert manifest.get("mode") == "full_county"
    shards_manifest = manifest.get("building_shards") or []
    assert len(shards_manifest) == 5, f"Expected 5 shards in pmtiles_manifest.json, got {len(shards_manifest)}"
    seen_pmtiles_files: set[str] = set()
    for entry in shards_manifest:
        fname = str(entry).split("?", 1)[0].rsplit("/", 1)[-1]
        seen_pmtiles_files.add(fname)
        pm_path = DATA_DIR / fname
        assert pm_path.is_file(), f"Missing PMTiles archive {pm_path}"
        assert pm_path.stat().st_size > 1_000_000, f"Unexpectedly small PMTiles archive {pm_path}"
        with pm_path.open("rb") as pfh:
            magic = pfh.read(8)
        assert magic == b"PMTiles\x03", f"Invalid PMTiles v3 magic bytes in {fname}: {magic!r}"

    expected_pmtiles = {f"houston_buildings_{q}.pmtiles" for q in QUAD_NAMES}
    assert seen_pmtiles_files == expected_pmtiles

    # Verify stats_summary.json matches countywide shard totals with zero unknown buildings
    stats = json.loads((DATA_DIR / "stats_summary.json").read_text(encoding="utf-8"))
    assert stats["total_buildings"] == total
    assert stats["dated_buildings"] == total
    decade_counts = stats.get("decade_counts") or {}
    assert decade_counts.get("unknown", 0) == 0
    numeric_decade_sum = sum(v for k, v in decade_counts.items() if k != "unknown")
    assert numeric_decade_sum == total


def test_it2_multi_building_campuses_cross_artifact_parity(
    shard_scan_summary: dict[str, Any],
    curated_overrides: dict[str, dict[str, Any]],
    search_index: list[dict[str, Any]],
) -> None:
    """IT-2: Verify primary-source sub-building chronologies across shards, overrides, and search_index."""
    tracked_by_id = shard_scan_summary["tracked_by_id"]
    tracked_by_hcad = shard_scan_summary["tracked_by_hcad"]
    search_by_id = {str(item.get("id") or ""): item for item in search_index}

    # 1. Cy-Fair reserve 1223580020001: 19 footprints split between Cypress Ridge HS & Eldridge Bus Barn
    cy_feats = tracked_by_hcad["1223580020001"]
    assert len(cy_feats) == 19, f"Expected 19 footprints on 1223580020001, got {len(cy_feats)}"
    cy_ids = {f["building_id"] for f in cy_feats}
    assert len(cy_ids) == 19, "Every footprint on 1223580020001 must have a unique #<slug> building_id"
    assert all(f["year_built"] == 2002 for f in cy_feats)
    assert all(f["year_source"] == "curated_landmark" for f in cy_feats)

    # 2. Robert E. Lee HS 1468640010001: 1928 Harry D. Payne main + three 1954 Lowell Lammers shops
    lee_hs_feats = tracked_by_hcad["1468640010001"]
    assert len(lee_hs_feats) == 4
    assert tracked_by_id["1468640010001#lee_hs_main_1928"]["year_built"] == 1928
    assert tracked_by_id["1468640010001#lee_hs_main_1928"]["architect"] == "Harry D. Payne"
    for shop_idx in (1, 2, 3):
        shop_id = f"1468640010001#lee_hs_shop_{shop_idx}"
        assert tracked_by_id[shop_id]["year_built"] == 1954
        assert tracked_by_id[shop_id]["architect"] == "Lowell Lammers"

    # 3. Baytown Lee College & IMPACT ECHS tract 0451440030163: zero spurious 1927 demolished-lot dates remain
    lc_tract_feats = tracked_by_hcad["0451440030163"]
    assert len(lc_tract_feats) >= 20
    assert all(f["year_built"] >= 1951 for f in lc_tract_feats), (
        f"Spurious pre-1951 demolished-lot contiguity dates survived on 0451440030163: "
        f"{[(f['building_id'], f['year_built']) for f in lc_tract_feats if f['year_built'] < 1951]}"
    )

    expected_primary_buildings: list[tuple[str, int, str, str]] = [
        ("1223580020001#cy_ridge_hs_main", 2002, "7900 N ELDRIDGE PKWY", ""),
        ("1223580020001#eldridge_trans_main", 2002, "7600 N ELDRIDGE PKWY", ""),
        ("1468640010001#lee_hs_main_1928", 1928, "1809 MARKET ST", "Harry D. Payne"),
        ("1468640010001#lee_hs_shop_1", 1954, "1809 MARKET ST", "Lowell Lammers"),
        ("0451440030163#impact_echs_main", 1981, "1415 MARKET ST", ""),
        ("0500610000001#lee_tech_voc_567", 1968, "501 S WHITING ST", ""),
        ("0500610000001#lee_tech_voc_9", 1968, "511 S WHITING ST", ""),
        ("0500620000001#lee_physical_plant", 1972, "520 S WHITING ST", ""),
        ("0500580000001#lee_tech_voc_10", 1972, "500 S PRUETT ST", ""),
        ("0500500000006#lee_security_facility", 1970, "600 W REPUBLIC ST", ""),
        ("1378920010001#lee_pac_2008", 2008, "805 W TEXAS AVE", ""),
        ("1378920010001#lee_mcnulty_haddick", 1952, "711 W TEXAS AVE", ""),
        ("0451440050245#carver_elem_main", 1928, "610 S PRUETT ST", ""),
        ("0410210010510#ashbel_smith_elem", 1996, "403 E JAMES ST", ""),
    ]
    for spec in LEE_COLLEGE_MAIN_BUILDINGS:
        expected_primary_buildings.append(
            (f"0451440030163#{spec['slug']}", int(spec["year_built"]), str(spec["address"]), "")
        )

    for bid, exp_yr, exp_addr, exp_arch in expected_primary_buildings:
        assert bid in tracked_by_id, f"Missing {bid} in .geojsonseq shards"
        shard_rec = tracked_by_id[bid]
        assert shard_rec["year_built"] == exp_yr, f"{bid} shard year {shard_rec['year_built']} != {exp_yr}"
        assert shard_rec["address"] == exp_addr, f"{bid} shard address {shard_rec['address']} != {exp_addr}"
        if exp_arch:
            assert shard_rec.get("architect") == exp_arch

        assert bid in curated_overrides, f"Missing {bid} in curated_overrides.json"
        ov = curated_overrides[bid]
        assert ov["year_built"] == exp_yr
        assert ov["address"] == exp_addr
        assert ov.get("keep_shard_footprints") is True
        assert ov.get("replace_parcel_shards") is False
        assert "geometry" not in ov, f"Shard-primary override {bid} must not embed duplicate geometry"
        assert isinstance(ov.get("lon"), (int, float)) and -96.1 <= float(ov["lon"]) <= -94.9
        assert isinstance(ov.get("lat"), (int, float)) and 29.4 <= float(ov["lat"]) <= 30.2

        assert bid in search_by_id, f"Primary campus building {bid} missing from search_index.json"
        si = search_by_id[bid]
        assert si["year_built"] == exp_yr
        assert si["address"] == exp_addr

    # Auxiliary support sheds must carry skip_search_index=True in curated_overrides and stay out of search_index
    for aux_id in (
        "1223580020001#cy_ridge_hs_aux_1",
        "1223580020001#eldridge_trans_aux_1",
        "1468640010001#lee_hs_shop_2",
        "0451440030163#lee_campus_aux_1",
    ):
        assert curated_overrides[aux_id].get("skip_search_index") is True
        assert aux_id not in search_by_id, f"Auxiliary shed {aux_id} leaked into search_index.json"


def test_it3_school_linkage_address_shards_and_inner_core_consistency(
    shard_scan_summary: dict[str, Any],
    search_index: list[dict[str, Any]],
) -> None:
    """IT-3: Audit all 688 school_* items, address_shards lookup rows, zero commercial clobbers, and inner-core GeoJSON."""
    all_building_ids = shard_scan_summary["all_building_ids"]
    assert not shard_scan_summary["commercial_school_clobbers"], (
        f"Commercial F1/F2 parcels mutated by school hydration: {shard_scan_summary['commercial_school_clobbers']}"
    )

    school_items = [it for it in search_index if str(it.get("id") or "").startswith("school_")]
    assert len(school_items) == 688, f"Expected 688 school_* records, got {len(school_items)}"

    fully_linked = 0
    for item in school_items:
        hcad = str(item.get("hcad_num") or "").strip()
        bid = str(item.get("building_id") or "").strip()
        yr = int(item.get("year_built") or 0)
        addr = str(item.get("address") or "").strip()
        if addr:
            assert not addr.startswith("0 "), f"School {item['id']} carries malformed '0 <STREET>' address: {addr!r}"
        if re.fullmatch(r"\d{13}", hcad) and bid in all_building_ids and MIN_VALID_YEAR <= yr <= MAX_VALID_CAMA_DATE_YEAR:
            fully_linked += 1

    linkage_ratio = fully_linked / len(school_items)
    assert linkage_ratio >= 0.90, (
        f"Expected >= 90% school_* HCAD/footprint/year linkage, got {fully_linked}/{len(school_items)} ({linkage_ratio:.1%})"
    )

    school_by_id = {str(it["id"]): it for it in school_items}
    assert school_by_id["school_612"]["building_id"] == "1223580020001#cy_ridge_hs_main"
    assert school_by_id["school_612"]["year_built"] == 2002
    assert school_by_id["school_612"]["address"] == "7900 N ELDRIDGE PKWY"

    assert school_by_id["school_705"]["building_id"] == "1468640010001#lee_hs_main_1928"
    assert school_by_id["school_705"]["year_built"] == 1928
    assert school_by_id["school_705"]["address"] == "1809 MARKET ST"

    assert school_by_id["school_706"]["building_id"] == "0451440030163#impact_echs_main"
    assert school_by_id["school_706"]["year_built"] == 1981
    assert school_by_id["school_706"]["address"] == "1415 MARKET ST"

    # Co-located schools sharing one HCAD all remain addressable
    for coloc_id in ("school_565", "school_566", "school_567"):
        assert coloc_id in school_by_id

    # Verify numeric campus street addresses resolve to linked [num, lon, lat, yr, hcad, name, building_id] rows
    expected_addr_rows = [
        ("79", "N ELDRIDGE PKWY", "7900", 2002, "1223580020001", "1223580020001#cy_ridge_hs_main"),
        ("76", "N ELDRIDGE PKWY", "7600", 2002, "1223580020001", "1223580020001#eldridge_trans_main"),
        ("18", "MARKET ST", "1809", 1928, "1468640010001", "1468640010001#lee_hs_main_1928"),
        ("14", "MARKET ST", "1415", 1981, "0451440030163", "0451440030163#impact_echs_main"),
        ("61", "S PRUETT ST", "610", 1928, "0451440050245", "0451440050245#carver_elem_main"),
        ("20", "LEE DR", "200", 1951, "0451440030163", "0451440030163#lee_rundell_hall"),
        ("40", "E JAMES ST", "403", 1996, "0410210010510", "0410210010510#ashbel_smith_elem"),
        ("50", "S WHITING ST", "501", 1968, "0500610000001", "0500610000001#lee_tech_voc_567"),
        ("52", "S WHITING ST", "520", 1972, "0500620000001", "0500620000001#lee_physical_plant"),
        ("80", "W TEXAS AVE", "805", 2008, "1378920010001", "1378920010001#lee_pac_2008"),
    ]
    for shard_prefix, street_key, house_num, exp_yr, exp_hcad, exp_bid in expected_addr_rows:
        shard_file = ADDRESS_SHARDS_DIR / f"addr_{shard_prefix}.json"
        assert shard_file.is_file(), f"Missing address shard {shard_file}"
        shard_obj = json.loads(shard_file.read_text(encoding="utf-8"))
        rows = shard_obj.get(street_key) or []
        matching = [
            r
            for r in rows
            if str(r[0]) == house_num
            and int(r[3] or 0) == exp_yr
            and str(r[4] or "") == exp_hcad
            and len(r) >= 7
            and str(r[6] or "") == exp_bid
        ]
        assert matching, f"Missing linked row for {house_num} {street_key} ({exp_bid}) in addr_{shard_prefix}.json"
        assert VALID_NUMBERED_SITUS_RE.match(f"{house_num} {street_key}")

    # Verify inner-core Pilot Core buildings.geojson is 100% dated and free of sentinel/pre-1945 contiguity leaks
    core_fc = orjson.loads((DATA_DIR / "buildings.geojson").read_bytes())
    core_feats = core_fc.get("features") or []
    assert len(core_feats) >= 18_000
    for feat in core_feats:
        props = feat.get("properties") or {}
        yr = props.get("year_built")
        ysrc = str(props.get("year_source") or "")
        assert isinstance(yr, int) and MIN_VALID_YEAR <= yr <= MAX_VALID_CAMA_DATE_YEAR, (
            f"Undated or out-of-range Pilot Core feature {props.get('building_id')}: {yr!r}"
        )
        assert not (yr <= 1901 and ysrc in ("hcad_permit", "hcad_deed"))
        assert not (yr < 1945 and ysrc == "campus_contiguity")


def test_reg2_and_reg4_existing_university_park_overrides_and_pre1945_historic_chronologies(
    shard_scan_summary: dict[str, Any],
    curated_overrides: dict[str, dict[str, Any]],
    search_index: list[dict[str, Any]],
) -> None:
    """REG-2 & REG-4: Existing university/medical/park # overrides and authentic pre-1945 chronologies remain intact."""
    tracked_by_hcad = shard_scan_summary["tracked_by_hcad"]
    search_by_id = {str(it.get("id") or ""): it for it in search_index}

    # REG-2: Geometry-bearing replace_parcel_shards=True institutional campuses (Rice, Hermann Park / Zoo, #aux_*)
    rice_overrides = [k for k in curated_overrides if k.startswith("0421790000001#")]
    zoo_overrides = [k for k in curated_overrides if k.startswith("0421790000004#")]
    aux_overrides = [k for k in curated_overrides if "#aux_" in k]
    assert len(rice_overrides) >= 15, f"Expected >= 15 Rice University # overrides, got {len(rice_overrides)}"
    assert len(zoo_overrides) >= 8, f"Expected >= 8 Hermann Park / Zoo # overrides, got {len(zoo_overrides)}"
    assert len(aux_overrides) >= 1, "Expected cross-parcel #aux_* overrides to remain intact"

    for sample_key in (rice_overrides[0], zoo_overrides[0]):
        ov = curated_overrides[sample_key]
        assert ov.get("replace_parcel_shards") is True
        geom = ov.get("geometry") or {}
        assert geom.get("type") in ("Polygon", "MultiPolygon")
        assert MIN_VALID_YEAR <= int(ov["year_built"]) <= MAX_VALID_CAMA_DATE_YEAR
        assert sample_key in search_by_id, f"Expected {sample_key} to remain searchable in search_index.json"

    # REG-4: Authentic pre-1945 historic landmarks and genuine 1900/1901 structures survive untouched
    assert shard_scan_summary["pre_1945_total_count"] >= 80_000, (
        f"Expected >= 80,000 authentic pre-1945 structures countywide, got {shard_scan_summary['pre_1945_total_count']}"
    )
    assert shard_scan_summary["genuine_1900_count"] >= 300, (
        f"Authentic 1900 buildings were over-filtered: {shard_scan_summary['genuine_1900_count']}"
    )
    assert shard_scan_summary["genuine_1901_count"] >= 15, (
        f"Authentic 1901 buildings were over-filtered: {shard_scan_summary['genuine_1901_count']}"
    )

    key_st_ov = curated_overrides["0621100000014"]
    assert key_st_ov["year_built"] == 1928

    sweeney_feats = tracked_by_hcad["0010330000018"]
    assert sweeney_feats and all(f["year_built"] == 1900 for f in sweeney_feats)

    union_feats = tracked_by_hcad["0011020000001"]
    assert any(f["year_built"] == 1911 for f in union_feats)

    deady_feats = tracked_by_hcad["0402330000134"]
    assert deady_feats and all(f["year_built"] == 1926 for f in deady_feats)
