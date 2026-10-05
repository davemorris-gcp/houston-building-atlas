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
