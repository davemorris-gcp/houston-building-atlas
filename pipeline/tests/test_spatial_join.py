"""Unit tests for Task 2 spatial join of building footprints to HCAD/COH parcels."""

from shapely.geometry import Polygon, mapping
from atlas_pipeline.spatial_join import (
    derive_building_footprint_from_parcel,
    join_footprints_to_parcels,
)


def test_derive_building_footprint_from_parcel_creates_valid_inset():
    parcel_poly = Polygon([
        (-95.3980, 29.7950),
        (-95.3976, 29.7950),
        (-95.3976, 29.7954),
        (-95.3980, 29.7954),
        (-95.3980, 29.7950),
    ])
    footprint = derive_building_footprint_from_parcel(parcel_poly, bld_area_sqft=2000, land_area_sqft=6000)
    assert footprint is not None
    assert footprint.is_valid
    assert not footprint.is_empty
    assert footprint.area < parcel_poly.area
    assert parcel_poly.buffer(1e-6).contains(footprint)


def test_join_footprints_to_parcels_matches_observed_and_derives_missing():
    # Parcel 1: has an observed OSM/Overture building footprint inside it
    p1_geom = Polygon([
        (-95.3980, 29.7950),
        (-95.3975, 29.7950),
        (-95.3975, 29.7955),
        (-95.3980, 29.7955),
        (-95.3980, 29.7950),
    ])
    # Parcel 2: has a built house (year_built=1924, bld_area=1850) but no external footprint
    p2_geom = Polygon([
        (-95.3970, 29.7950),
        (-95.3965, 29.7950),
        (-95.3965, 29.7955),
        (-95.3970, 29.7955),
        (-95.3970, 29.7950),
    ])

    parcels = [
        {
            "type": "Feature",
            "geometry": mapping(p1_geom),
            "properties": {
                "id": "pcl_001",
                "hcad_num": "0010010010001",
                "address": "1402 HEIGHTS BLVD",
                "year_built": 1910,
                "decade": 1910,
                "remodel_year": 0,
                "owner": "ALICE SMITH",
                "bld_area": 2600.0,
                "land_area": 6600.0,
                "stories": 2.0,
                "height_m": 7.0,
                "use_category": "Residential",
                "landuse_desc": "Single-family Residential",
                "bld_style": "Queen Anne Victorian",
                "subdivision": "HOUSTON HEIGHTS",
                "historic_district": "Houston Heights Historic District South",
                "contributing": "Contributing",
                "landmark_name": "Smith House",
                "landmark_type": "Protected Landmark",
                "architect": "G. E. Dickey",
            },
        },
        {
            "type": "Feature",
            "geometry": mapping(p2_geom),
            "properties": {
                "id": "pcl_002",
                "hcad_num": "0010010010002",
                "address": "1406 HEIGHTS BLVD",
                "year_built": 1924,
                "decade": 1920,
                "remodel_year": 1998,
                "owner": "BOB JONES",
                "bld_area": 1850.0,
                "land_area": 6600.0,
                "stories": 1.0,
                "height_m": 3.5,
                "use_category": "Residential",
                "landuse_desc": "Single-family Residential",
                "bld_style": "Craftsman Bungalow",
                "subdivision": "HOUSTON HEIGHTS",
                "historic_district": "Houston Heights Historic District South",
                "contributing": "Contributing",
                "landmark_name": "",
                "landmark_type": "",
                "architect": "",
            },
        },
    ]

    # Observed footprint inside Parcel 1
    fp1_geom = Polygon([
        (-95.3979, 29.7951),
        (-95.3976, 29.7951),
        (-95.3976, 29.7954),
        (-95.3979, 29.7954),
        (-95.3979, 29.7951),
    ])
    footprints = [
        {
            "type": "Feature",
            "geometry": mapping(fp1_geom),
            "properties": {
                "building": "house",
                "building:levels": "2",
            },
        }
    ]

    joined_buildings, enriched_parcels = join_footprints_to_parcels(parcels, footprints)
    assert len(enriched_parcels) == 2
    assert len(joined_buildings) == 2

    by_hcad = {b["properties"]["hcad_num"]: b for b in joined_buildings}
    assert "0010010010001" in by_hcad
    assert "0010010010002" in by_hcad

    b1 = by_hcad["0010010010001"]
    assert b1["properties"]["year_built"] == 1910
    assert b1["properties"]["landmark_name"] == "Smith House"
    assert b1["properties"]["footprint_source"] == "observed"

    b2 = by_hcad["0010010010002"]
    assert b2["properties"]["year_built"] == 1924
    assert b2["properties"]["address"] == "1406 HEIGHTS BLVD"
    assert b2["properties"]["footprint_source"] == "derived_parcel"


def test_deduplicate_geojsonseq_shard_resolves_overlaps_and_compound_polygons(tmp_path):
    import orjson
    from atlas_pipeline.full_build import deduplicate_geojsonseq_shard

    seq_file = tmp_path / "test_shard.geojsonseq"
    features = [
        # 1. Oversized compound polygon covering both houses below
        {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [-95.3765, 29.7645],
                    [-95.3755, 29.7645],
                    [-95.3755, 29.7655],
                    [-95.3765, 29.7655],
                    [-95.3765, 29.7645],
                ]],
            },
            "properties": {"id": "compound_block", "year_built": 1890},
        },
        # 2. House A (1890)
        {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [-95.3764, 29.7646],
                    [-95.3761, 29.7646],
                    [-95.3761, 29.7649],
                    [-95.3764, 29.7649],
                    [-95.3764, 29.7646],
                ]],
            },
            "properties": {"id": "house_a_1890", "year_built": 1890},
        },
        # 3. Duplicate overlapping polygon on House A with newer year (1950)
        {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [-95.37638, 29.76462],
                    [-95.37608, 29.76462],
                    [-95.37608, 29.76492],
                    [-95.37638, 29.76492],
                    [-95.37638, 29.76462],
                ]],
            },
            "properties": {"id": "house_a_dup_1950", "year_built": 1950},
        },
        # 4. House B (1920)
        {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [-95.3759, 29.7646],
                    [-95.3756, 29.7646],
                    [-95.3756, 29.7649],
                    [-95.3759, 29.7649],
                    [-95.3759, 29.7646],
                ]],
            },
            "properties": {"id": "house_b_1920", "year_built": 1920},
        },
    ]

    with open(seq_file, "wb") as f:
        for feat in features:
            f.write(b"\x1e" + orjson.dumps(feat) + b"\n")

    _, before_cnt, after_cnt = deduplicate_geojsonseq_shard(seq_file)
    assert before_cnt == 4
    assert after_cnt == 2

    kept_ids = [
        orjson.loads(line[1:])["properties"]["id"]
        for line in seq_file.read_bytes().splitlines()
        if line
    ]
    assert set(kept_ids) == {"house_a_1890", "house_b_1920"}


def test_identify_cross_parcel_aux_overrides_flags_only_neighbor_swallowing_aux_polygons():
    import numpy as np
    import shapely
    from pyproj import Transformer
    from atlas_pipeline.repair_cross_parcel_aux_overrides import (
        get_quadrant,
        identify_cross_parcel_aux_overrides,
        wgs84_geom_to_2278,
    )

    # Given two adjacent synthetic North Norhill parcels in EPSG:4326 projected to EPSG:2278:
    # Lot 14 (0621100000014 / 1127 KEY ST) on the west, Lot 15 (0621100000015 / 1121 KEY ST) on the east.
    tf_to_2278 = Transformer.from_crs("EPSG:4326", "EPSG:2278", always_xy=True)
    lot14_4326 = {
        "type": "Polygon",
        "coordinates": [[
            [-95.38730, 29.79165],
            [-95.38712, 29.79165],
            [-95.38712, 29.79185],
            [-95.38730, 29.79185],
            [-95.38730, 29.79165],
        ]],
    }
    lot15_4326 = {
        "type": "Polygon",
        "coordinates": [[
            [-95.38712, 29.79165],
            [-95.38694, 29.79165],
            [-95.38694, 29.79185],
            [-95.38712, 29.79185],
            [-95.38712, 29.79165],
        ]],
    }
    parcel_geoms = np.asarray(
        [
            wgs84_geom_to_2278(lot14_4326, tf_to_2278),
            wgs84_geom_to_2278(lot15_4326, tf_to_2278),
        ],
        dtype=object,
    )
    parcel_hcads = np.asarray(["0621100000014", "0621100000015"], dtype=object)
    parcel_tree = shapely.STRtree(parcel_geoms)

    overrides_map = {
        # Primary override on Lot 14 (must never be flagged even if eave crosses slightly)
        "0621100000014": {
            "hcad_num": "0621100000014",
            "address": "1127 KEY ST",
            "year_built": 1928,
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [-95.38727, 29.79169],
                    [-95.38714, 29.79169],
                    [-95.38714, 29.79179],
                    [-95.38727, 29.79179],
                    [-95.38727, 29.79169],
                ]],
            },
        },
        # Cross-parcel #aux_1 tagged to Lot 14 whose interior sits inside neighbor Lot 15 (1121 KEY ST)
        "0621100000014#aux_1": {
            "hcad_num": "0621100000014",
            "address": "1127 KEY ST",
            "year_built": 1928,
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [-95.38713, 29.79170],
                    [-95.38697, 29.79170],
                    [-95.38697, 29.79181],
                    [-95.38713, 29.79181],
                    [-95.38713, 29.79170],
                ]],
            },
        },
        # Legitimate detached garage #aux_2 whose interior sits inside Lot 14
        "0621100000014#aux_2": {
            "hcad_num": "0621100000014",
            "address": "1127 KEY ST",
            "year_built": 1928,
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [-95.38728, 29.79180],
                    [-95.38722, 29.79180],
                    [-95.38722, 29.79184],
                    [-95.38728, 29.79184],
                    [-95.38728, 29.79180],
                ]],
            },
        },
        # Unindexed / geometry-free #aux_3 entry (ignored safely)
        "0621100000014#aux_3": {"hcad_num": "0621100000014"},
    }

    # When scanning overrides_map for cross-parcel #aux_ entries.
    mismatches = identify_cross_parcel_aux_overrides(
        overrides_map, parcel_tree, parcel_hcads, tf_to_2278
    )

    # Then only the neighbor-swallowing #aux_1 polygon is flagged, mapped to 0621100000015 in nw_e.
    assert mismatches == [
        ("0621100000014#aux_1", "0621100000014", "0621100000015", 1)
    ]
    assert get_quadrant(-95.38704, 29.79176) == "nw_e"
    assert get_quadrant(-95.75, 29.95) == "nw_w"
    assert get_quadrant(-95.20, 29.95) == "ne"
    assert get_quadrant(-95.45, 29.65) == "sw"
    assert get_quadrant(-95.20, 29.65) == "se"


def test_reconcile_cross_parcel_overrides_and_quadrant_features():
    from pyproj import Transformer
    from atlas_pipeline.repair_cross_parcel_aux_overrides import (
        reconcile_cross_parcel_overrides,
        reconcile_quadrant_features,
    )

    tf_to_2278 = Transformer.from_crs("EPSG:4326", "EPSG:2278", always_xy=True)
    poly_neighbor_1121 = {
        "type": "Polygon",
        "coordinates": [[
            [-95.38710, 29.79170],
            [-95.38698, 29.79170],
            [-95.38698, 29.79180],
            [-95.38710, 29.79180],
            [-95.38710, 29.79170],
        ]],
    }
    poly_neighbor_retag = {
        "type": "Polygon",
        "coordinates": [[
            [-95.38685, 29.79170],
            [-95.38673, 29.79170],
            [-95.38673, 29.79180],
            [-95.38685, 29.79180],
            [-95.38685, 29.79170],
        ]],
    }
    poly_discovery_pavilion = {
        "type": "Polygon",
        "coordinates": [[
            [-95.35950, 29.75320],
            [-95.35935, 29.75320],
            [-95.35935, 29.75335],
            [-95.35950, 29.75335],
            [-95.35950, 29.75320],
        ]],
    }

    overrides_map = {
        "1319030010001": {
            "hcad_num": "1319030010001",
            "address": "1500 MCKINNEY ST",
            "building_name": "Discovery Green",
            "year_built": 2008,
            "use_category": "Civic / Institutional",
        },
        "1319030010003#aux_1": {
            "hcad_num": "1319030010003",
            "year_built": 2008,
            "stories": 1.0,
            "height_m": 4.0,
            "historic_district": "",
            "contributing": "Outside Historic District",
            "geometry": poly_discovery_pavilion,
        },
        "0621100000014#aux_1": {
            "hcad_num": "0621100000014",
            "address": "1127 KEY ST",
            "year_built": 1928,
            "geometry": poly_neighbor_1121,
        },
        "0621100000017#aux_1": {
            "hcad_num": "0621100000017",
            "address": "1115 KEY ST",
            "year_built": 1930,
            "geometry": poly_neighbor_retag,
        },
    }
    mismatches = [
        ("1319030010003#aux_1", "1319030010003", "1319030010001", 0),
        ("0621100000014#aux_1", "0621100000014", "0621100000015", 1),
        ("0621100000017#aux_1", "0621100000017", "0621100000016", 2),
    ]
    parcel_addrs = ["1500 MCKINNEY ST", "1121 KEY ST", "1119 KEY ST"]
    parcel_yrs = [2008, 1926, 1930]
    parcel_areas = [400000.0, 5000.0, 5000.0]
    cama_by_hcad = {
        "0621100000015": {
            "site_addr_1": "1121 KEY ST",
            "bldg_year": 1926,
            "bld_ar": 2042,
            "land_ar": 5000,
            "state_class": "A1",
            "remodel_year": 2022,
        },
        "0621100000016": {
            "site_addr_1": "1119 KEY ST",
            "bldg_year": 1930,
            "bld_ar": 1480,
            "land_ar": 5000,
            "state_class": "A1",
        },
    }
    bgeo_by_hcad = {
        "0621100000015": {
            "historic_district": "Norhill Historic District",
            "contributing": "Contributing",
        }
    }

    purged, rekeyed, pending_by_quad = reconcile_cross_parcel_overrides(
        overrides_map=overrides_map,
        mismatches=mismatches,
        parcel_addrs=parcel_addrs,
        parcel_yrs=parcel_yrs,
        parcel_areas=parcel_areas,
        cama_by_hcad=cama_by_hcad,
        bgeo_by_hcad=bgeo_by_hcad,
        tf_to_2278=tf_to_2278,
    )

    assert len(purged) == 3
    assert rekeyed == [("1319030010003#aux_1", "1319030010001#aux_1")]
    rekeyed_rec = overrides_map["1319030010001#aux_1"]
    assert rekeyed_rec["id"] == "1319030010001#aux_1"
    assert rekeyed_rec["building_id"] == "1319030010001#aux_1"
    assert rekeyed_rec["contributing"] == "Outside Historic District"
    assert rekeyed_rec["historic_district"] == ""

    shard_features = [
        {
            "type": "Feature",
            "geometry": poly_neighbor_retag,
            "properties": {"id": "0621100000017", "hcad_num": "0621100000017", "year_built": 1930},
        }
    ]
    retagged, restored = reconcile_quadrant_features(shard_features, pending_by_quad["nw_e"])
    assert retagged == 1
    assert restored == 1
    by_hcad = {f["properties"]["hcad_num"]: f["properties"] for f in shard_features}
    assert by_hcad["0621100000016"]["address"] == "1119 KEY ST"
    assert by_hcad["0621100000015"]["address"] == "1121 KEY ST"
    assert by_hcad["0621100000015"]["year_built"] == 1926
    assert by_hcad["0621100000015"]["contributing"] == "Contributing"



