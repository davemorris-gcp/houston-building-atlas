"""Unit tests for Task 3 tile, search index, and stats summary builder."""

import json
from pathlib import Path
from shapely.geometry import Polygon, mapping
from atlas_pipeline.tile_builder import (
    build_search_index,
    build_stats_summary,
    write_atlas_bundle,
)


def _sample_buildings_and_parcels():
    poly = Polygon([
        (-95.3980, 29.7950),
        (-95.3976, 29.7950),
        (-95.3976, 29.7954),
        (-95.3980, 29.7954),
        (-95.3980, 29.7950),
    ])
    props = {
        "id": "bld_000001",
        "hcad_num": "0010010010001",
        "address": "1506 HEIGHTS BLVD",
        "year_built": 1912,
        "decade": 1910,
        "remodel_year": 0,
        "owner": "JANE DOE",
        "bld_area": 2450.0,
        "land_area": 6600.0,
        "stories": 2.0,
        "height_m": 7.0,
        "use_category": "Residential",
        "landuse_desc": "Single-family Residential",
        "bld_style": "Craftsman Bungalow",
        "subdivision": "HOUSTON HEIGHTS",
        "historic_district": "Houston Heights Historic District South",
        "contributing": "Contributing",
        "landmark_name": "Historic Heights Bungalow",
        "landmark_type": "Protected Landmark",
        "architect": "A. C. Finn",
        "footprint_source": "observed",
    }
    buildings = [{"type": "Feature", "geometry": mapping(poly), "properties": props}]
    parcels = [{"type": "Feature", "geometry": mapping(poly), "properties": {**props, "id": "pcl_0010010010001"}}]
    overlays = {
        "landmarks": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-95.3978, 29.7952]},
                "properties": {
                    "id": "lm_1",
                    "name": "Historic Heights Bungalow",
                    "address": "1506 HEIGHTS BLVD",
                    "designation": "Protected Landmark",
                    "year_built": 1912,
                    "architect": "A. C. Finn",
                    "style": "Craftsman Bungalow",
                    "historic_district": "Houston Heights Historic District South",
                    "hcad_num": "0010010010001",
                },
            }
        ],
        "historic_districts": [],
        "heritage_districts": [],
        "nrhp_districts": [],
        "annexations": [],
        "thc_markers": [],
    }
    return buildings, parcels, overlays


def test_build_search_index_and_stats():
    buildings, _, overlays = _sample_buildings_and_parcels()
    search_items = build_search_index(buildings, overlays)
    assert len(search_items) >= 1
    assert any("1506 HEIGHTS BLVD" in item["label"] for item in search_items)

    stats = build_stats_summary(buildings, overlays)
    assert stats["total_buildings"] == 1
    assert stats["dated_buildings"] == 1
    assert stats["decade_counts"]["1910"] == 1
    assert stats["contributing_counts"]["Contributing"] == 1


def test_write_atlas_bundle_creates_pmtiles_and_json_assets(tmp_path: Path):
    buildings, parcels, overlays = _sample_buildings_and_parcels()
    manifest = write_atlas_bundle(tmp_path, buildings, parcels, overlays)

    assert (tmp_path / "houston_atlas.pmtiles").exists()
    assert (tmp_path / "houston_atlas.pmtiles").stat().st_size > 100
    assert (tmp_path / "buildings.geojson").exists()
    assert (tmp_path / "parcels.geojson").exists()
    assert (tmp_path / "overlays.json").exists()
    assert (tmp_path / "search_index.json").exists()
    assert (tmp_path / "stats_summary.json").exists()

    loaded_stats = json.loads((tmp_path / "stats_summary.json").read_text())
    assert loaded_stats["total_buildings"] == 1
    assert manifest["pmtiles_size_bytes"] > 100
