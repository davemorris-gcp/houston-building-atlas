"""Unit tests for Houston Building Atlas v2 schema normalization."""

from atlas_pipeline.schema import (
    classify_use_category,
    compute_decade,
    estimate_stories_and_height,
    normalize_parcel_record,
    normalize_year,
)


def test_normalize_year_valid_and_invalid():
    assert normalize_year("1912") == 1912
    assert normalize_year(1928.0) == 1928
    assert normalize_year(" 1895 ") == 1895
    assert normalize_year("0") == 0
    assert normalize_year(0) == 0
    assert normalize_year(None) == 0
    assert normalize_year("") == 0
    assert normalize_year("1750") == 0  # Prior to Houston era
    assert normalize_year("2035") == 0


def test_compute_decade():
    assert compute_decade(1836) == 1830
    assert compute_decade(1914) == 1910
    assert compute_decade(2026) == 2020
    assert compute_decade(0) == 0


def test_classify_use_category():
    assert classify_use_category("A1", "Single-family Residential", "") == "Residential"
    assert classify_use_category("B1", "Two-Family / Duplex", "") == "Multi-Family"
    assert classify_use_category("B2", "Multi-Family Apartment", "") == "Multi-Family"
    assert classify_use_category("F1", "Commercial Retail", "") == "Commercial"
    assert classify_use_category("X1", "Exempt Public / Church / School", "") == "Civic / Institutional"
    assert classify_use_category("F2", "Industrial Warehouse", "") == "Industrial"
    assert classify_use_category("C1", "Vacant Land", "") == "Vacant / Exempt"


def test_estimate_stories_and_height():
    stories, height_m = estimate_stories_and_height(
        bld_area=2400, footprint_area_sqft=1200, use_category="Residential", osm_levels=None, osm_height=None
    )
    assert stories == 2.0
    assert height_m == 7.0

    stories_osm, height_osm = estimate_stories_and_height(
        bld_area=50000, footprint_area_sqft=5000, use_category="Commercial", osm_levels="12", osm_height="45"
    )
    assert stories_osm == 12.0
    assert height_osm == 45.0


def test_normalize_parcel_record_merges_historic_and_landmark():
    raw_cadastral = {
        "TAX_ID": "0010020030004",
        "SITE_ADDR_1": "1506 HEIGHTS BLVD",
        "YR_IMPR": "1914",
        "OWNER_MAILTO": "JANE DOE",
        "TOTAL_BUILDING_AREA": 2450,
        "TOTAL_LAND_AREA": 6600,
        "STATE_CLASS": "A1",
        "LANDUSE_DSCR": "Single-family Residential",
        "ECON_BLD_CLASS": "1B",
        "LEGAL_DSCR_1": "LT 4 BLK 12 HOUSTON HEIGHTS",
    }
    historic_info = {
        "Historic_District_Name": "Houston Heights Historic District South",
        "Building_Classification": "Contributing",
    }
    landmark_info = {
        "USER_SITE_NAME": "Historic Heights Bungalow",
        "LandmarkDesignation": "Protected Landmark",
        "USER_YR_BUILT": 1912,
        "USER_ARCHITECT___BUILDER": "A. C. Finn",
        "USER_STYLE": "Craftsman Bungalow",
    }
    norm = normalize_parcel_record(raw_cadastral, historic_info, landmark_info)
    assert norm["hcad_num"] == "0010020030004"
    assert norm["address"] == "1506 HEIGHTS BLVD"
    assert norm["year_built"] == 1912
    assert norm["decade"] == 1910
    assert norm["contributing"] == "Contributing"
    assert norm["historic_district"] == "Houston Heights Historic District South"
    assert norm["landmark_name"] == "Historic Heights Bungalow"
    assert norm["landmark_type"] == "Protected Landmark"
    assert norm["architect"] == "A. C. Finn"
    assert norm["bld_style"] == "Craftsman Bungalow"
    assert norm["use_category"] == "Residential"
