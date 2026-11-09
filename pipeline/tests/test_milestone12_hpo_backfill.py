"""Milestone 12 hermetic unit and synthetic tmp_path integration tests for COH HPO
23 Historic District Survey Inventories and Post-2016 Landmark Designation Reports.
"""

from __future__ import annotations

import csv
import gzip
import json
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any

import pytest

from atlas_pipeline import cli
from atlas_pipeline.harvest_coh_hpo_surveys_and_landmarks import (
    DISTRICT_MANIFEST,
    HPO_DISTRICT_NAME_ALIASES,
    NON_ARCHITECTURAL_SURVEY_STYLES,
    STRIP_STREET_TAIL,
    HarvestBundlePaths,
    HarvestInputs,
    OverrideProvenance,
    PromotedAccountShift,
    SurveyParcelRow,
    SurveySecondaryRow,
    _architect_matches_year,
    _clean_architect_value,
    _compute_countywide_stat_transitions,
    _has_opposing_directional,
    _has_parcel_survey_conflict,
    _is_compatible_entity,
    _is_post_landmark_replacement,
    _is_post_survey_replacement,
    _promote_year_override,
    _reconcile_landmark_hcads_and_nrhp,
    _resolve_entity_hpo_survey_style,
    _resolve_generated_at_date,
    _scrub_personal_header_names,
    canonicalize_hpo_district_name,
    clean_hpo_text,
    encode_clean_http_url,
    format_hpo_harvest_cli_summary,
    is_usable_hpo_survey_style,
    match_post2016_landmark_pdf_candidates,
    normalize_hpo_lookup_address,
    normalize_hpo_street_address_keys,
    parse_district_inventory_text,
    resolve_override_own_tier_and_port,
    run_hpo_harvest,
)
from atlas_pipeline.schema import (
    ACTIVE_TAX_YEAR,
    CAMPUS_SPLIT_HCADS,
    EXCLUDED_AUTO_PROMOTION_HCADS,
    GENERIC_STYLE_PLACEHOLDERS,
    HCAD_STYLE_GRADE_CODES,
    STRUCTURAL_STYLE_CLASSES,
    is_replaceable_style_value,
    is_specific_architectural_style,
    is_structural_class_style,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_normalize_hpo_lookup_address_and_street_keys_variants() -> None:
    """Verify Houston directionals, apt/unit/rear suffixes, 1/2 addresses, and Saint/Mt/Ave variants."""
    # 1. Directionals and Boulevard Oaks 2-token street names
    assert normalize_hpo_lookup_address("1119 North Walling Street, Houston, TX 77009") == "1119 N WALLING"
    assert normalize_hpo_lookup_address("1119 N. Walling St.") == "1119 N WALLING"
    assert normalize_hpo_street_address_keys("1119 North Walling Street") == [
        "1119 N WALLING",
        "1119 WALLING",
    ]
    assert normalize_hpo_lookup_address("1511 North Boulevard") == "1511 NORTH"
    assert normalize_hpo_lookup_address("1511 N. Blvd.") == "1511 NORTH"
    assert normalize_hpo_lookup_address("1512 South Boulevard") == "1512 SOUTH"
    assert normalize_hpo_lookup_address("1512 S Blvd") == "1512 SOUTH"

    # 2. Apartment, unit, suite, building, floor, #unit, and rear suffixes
    assert normalize_hpo_lookup_address("3917 Main Street, #16") == "3917 MAIN"
    assert normalize_hpo_lookup_address("2700 N Albany St Ste 100, Houston TX 77006") == "2700 N ALBANY"
    assert normalize_hpo_lookup_address("804 Avondale St Apt B") == "804 AVONDALE"
    assert normalize_hpo_lookup_address("912 Heights Blvd Unit 4") == "912 HEIGHTS"
    assert normalize_hpo_lookup_address("512 Harvard St Rear") == "512 HARVARD"
    assert normalize_hpo_lookup_address("512 Harvard St (Rear)") == "512 HARVARD"

    # 3. Fractional 1/2 house numbers (spaced, fused, and trailing HCAD format) vs numbered 31 1/2 streets
    half_char = chr(0x00BD)
    assert normalize_hpo_lookup_address(f"3410 {half_char} Burlington St") == "3410 1/2 BURLINGTON"
    assert normalize_hpo_lookup_address("3410 1/2 Burlington Street") == "3410 1/2 BURLINGTON"
    assert normalize_hpo_lookup_address("18361/2 Columbia St") == "1836 1/2 COLUMBIA"
    assert normalize_hpo_lookup_address("2091/2 E WOODLAND ST") == "209 1/2 E WOODLAND"
    assert normalize_hpo_lookup_address("209 E WOODLAND ST 1/2") == "209 1/2 E WOODLAND"
    assert normalize_hpo_lookup_address("701 E 51/2 St") == "701 E 5 1/2"
    assert normalize_hpo_lookup_address("111 E 31st 1/2 St") == "111 E 31 1/2"
    assert normalize_hpo_lookup_address(f"111 E. 31 {half_char} Street") == "111 E 31 1/2"
    assert normalize_hpo_lookup_address(f"111 31 {half_char} ST") == "111 31 1/2"

    # 4. Saint/St, Mount/Mt, Avenue/Ave, Eleventh/11th, hyphenated letter suffixes, and ranges
    assert normalize_hpo_lookup_address("405 Saint Emanuel St") == "405 ST EMANUEL"
    assert normalize_hpo_lookup_address("405 St. Emanuel Street") == "405 ST EMANUEL"
    assert normalize_hpo_lookup_address("1200 Mount Vernon St") == "1200 MT VERNON"
    assert normalize_hpo_lookup_address("1200 Mt. Vernon Street") == "1200 MT VERNON"
    assert normalize_hpo_lookup_address("1015 Avenue A") == "1015 AVE A"
    assert normalize_hpo_lookup_address("1015 Ave. A") == "1015 AVE A"
    assert normalize_hpo_lookup_address("7106 Avenue E") == "7106 AVE E"
    assert normalize_hpo_lookup_address("7106 AVE E") == "7106 AVE E"
    assert normalize_hpo_lookup_address("7106 Avenue N") == "7106 AVE N"
    assert normalize_hpo_lookup_address("7106 Avenue S") == "7106 AVE S"
    assert normalize_hpo_lookup_address("7106 Avenue W") == "7106 AVE W"
    assert normalize_hpo_lookup_address("1000 Heights Blvd N") == "1000 N HEIGHTS"
    assert normalize_hpo_lookup_address("1000 Heights Blvd S") == "1000 S HEIGHTS"
    assert normalize_hpo_lookup_address("2200 Memorial Dr E") == "2200 E MEMORIAL"
    assert normalize_hpo_lookup_address("2200 Memorial Dr W") == "2200 W MEMORIAL"
    assert normalize_hpo_lookup_address("4-B West Eleventh Place") == "4B W 11TH"
    assert normalize_hpo_lookup_address("4B W 11th Pl") == "4B W 11TH"
    assert normalize_hpo_lookup_address(f"4{chr(0x2011)}B West 11th Pl") == "4B W 11TH"
    assert normalize_hpo_lookup_address("14-D West 11th Place") == "14D W 11TH"
    assert normalize_hpo_lookup_address("14D West 11th Place") == "14D W 11TH"

    assert normalize_hpo_street_address_keys("802/804 Commerce St") == [
        "802 COMMERCE",
        "804 COMMERCE",
    ]
    isabella_nrhp_keys = normalize_hpo_street_address_keys("3909--3917 S. Main St.")
    assert "3909 S MAIN" in isabella_nrhp_keys
    assert "3917 MAIN" in isabella_nrhp_keys

    # District alias canonicalization, provenance date resolution, and shared style helpers
    assert len(HPO_DISTRICT_NAME_ALIASES) == 21
    assert (
        canonicalize_hpo_district_name("Main Street Market Square Historic District")
        == "Main Street/Market Square Historic District"
    )
    assert (
        canonicalize_hpo_district_name("Houston Heights Historic District East")
        == "Houston Heights East Historic District"
    )
    assert canonicalize_hpo_district_name("W. 11th Place") == "West Eleventh Place Historic District"
    assert canonicalize_hpo_district_name("Outside Historic District") == "Outside Historic District"
    assert _resolve_generated_at_date(None, "20270415a") == "2027-04-15"
    assert _resolve_generated_at_date("2028-01-20", "20270415a") == "2028-01-20"
    assert _resolve_generated_at_date(None, None) == "2026-11-09"
    assert "c" in HCAD_STYLE_GRADE_CODES and "f" in HCAD_STYLE_GRADE_CODES
    assert is_replaceable_style_value("C") is True
    assert is_replaceable_style_value("B+") is True
    assert is_replaceable_style_value("Wood or Light Steel") is True
    assert is_replaceable_style_value("Tilt-Up Concrete") is True
    assert is_replaceable_style_value("CANOPY ONLY") is True
    assert is_replaceable_style_value("Plant Assets - Industrial") is True
    assert is_replaceable_style_value("Craftsman Bungalow") is False
    assert is_replaceable_style_value("Louisiana French Plantation style") is False
    assert is_structural_class_style("Tilt-Up Concrete") is True
    assert is_structural_class_style("Louisiana French Plantation style") is False
    assert is_structural_class_style("Miesian Steel & Rose-Brick International Style") is False
    assert is_specific_architectural_style("C") is False
    assert is_specific_architectural_style("Tilt-Up Concrete") is False
    assert is_specific_architectural_style("Craftsman Bungalow") is True
    assert is_specific_architectural_style("Louisiana French Plantation style") is True
    assert is_specific_architectural_style("Wood or Light Steel", allow_structural_class=True) is True
    assert is_specific_architectural_style("Tilt-Up Concrete", allow_structural_class=True) is True
    em_dash = chr(0x2014)
    en_dash = chr(0x2013)
    uni_hyphen = chr(0x2010)
    assert normalize_hpo_lookup_address(f"1414{en_dash}1416 East Austin St") == "1414 E AUSTIN"
    assert normalize_hpo_street_address_keys(f"1414{en_dash}1416 East Austin St") == [
        "1414 E AUSTIN",
        "1414 AUSTIN",
        "1416 E AUSTIN",
        "1416 AUSTIN",
    ]
    assert normalize_hpo_street_address_keys("1647-49 Cortlandt St") == [
        "1647 CORTLANDT",
        "1649 CORTLANDT",
    ]
    assert normalize_hpo_street_address_keys("120-122 E 11th St") == [
        "120 E 11TH",
        "120 11TH",
        "122 E 11TH",
        "122 11TH",
    ]
    assert _has_opposing_directional("120-122 E 11th St", "120 W 11th St") is True
    assert clean_hpo_text(f"Queen Anne L{uni_hyphen}Plan {em_dash} 1910") == "Queen Anne L-Plan - 1910"
    assert encode_clean_http_url("https://www.houstontx.gov/docs/My Report Final.pdf") == (
        "https://www.houstontx.gov/docs/My%20Report%20Final.pdf"
    )

    # 5. Unnumbered / empty address rejection + 0-address vacant-lot key preservation
    assert normalize_hpo_lookup_address("") == ""
    assert normalize_hpo_lookup_address(None) == ""
    assert normalize_hpo_lookup_address("Main Street") == ""
    assert normalize_hpo_street_address_keys("Avenida De Las Americas") == []
    assert normalize_hpo_lookup_address("0 North Blvd") == "0 NORTH"


_HPO_LAYOUT_TEST_CASES: list[tuple[str, str, str, str, int]] = [
    (
        "standard_audubon_wrapped_and_secondary",
        "Audubon Place Historic District",
        "STANDARD",
        """
        INVENTORY OF AUDUBON PLACE HISTORIC DISTRICT
        HOUSTON, TEXAS
        Building Status Legend
        C = Contributing Structure
        PC = Potentially Contributing
        NC = Noncontributing Structure
        V = Vacant
        Property Address      Subdivision       Block   Lot/Tract      Circa Year Built   Bldg Status   Style of Architecture
        3401   Audubon Pl.    Montrose          16      Trs 1 & 2A     1998               NC            Traditional
                                                                                                        Contemporary Apartment
        3403   Audubon Pl.    Carva T/H Condo   16                     1970               NC            Building
        100    Avondale       Avondale          7       1, Trs 2A, 13  1913               PC            Craftsman
                                                                                          PC            Garage
                                                                       c. 1920            C             Carriage house
        1628 BISSONNET ST     EDGEMONT          LT 14 BLK 6            1925               C             Colonial Revival
        1309 North Blvd       Lot 16                                   1923-24            C             Period, with Tudor influences
        524 Frasier           1                 13A                    1923               PC            Bungalow
        107 ALMA ST           GROTA HOME        TR 41A BLK 29          1915               C             Queen Anne
        111 31 1/2 ST         9, 10                                    1946-47            C             Church
        3410
        (3412) Burlington     Westmoreland      12      Lt 4           1912               C             American Foursquare
        2105 Bayland Ave      Woodland Hts      5       Lt 9           1914               C             Craftsman Bungalow
        """,
        10,
    ),
    (
        "standard_no_split_num_bleed",
        "Audubon Place Historic District",
        "STANDARD",
        "1920\n"
        "3401   Audubon Pl.   Montrose   16   Lt 1   1923   C   Bungalow\n"
        "(Rear) 3405 Audubon Pl   Montrose   16   Lt 2   1925   C   Cottage\n",
        1,
    ),
    (
        "standard_main_street_market_square",
        "Main Street/Market Square Historic District",
        "STANDARD",
        """
        INVENTORY OF MAIN STREET MARKET SQUARE HISTORIC DISTRICT
        Property Address      Block   Lot   Circa Year Built   Bldg Status   Style of Architecture
        801   Commerce        1             -                  N             Vacant
        800/802 Commerce      16      6     1882               C             Victorian Commercial
        112   Travis          24      7     1885               C             Italianate Commercial
        522-524   Hawthorne   8       3     1922               C             Duplex Bungalow
        201 (203)   Emerson   4       1     1914               C             American Foursquare
                                                               Year     Bldg
                                                               NC      1 story garage
                                                               1940               Added quarters in back
        705   Avondale        9       2     1916               C             Prairie w/Craftsman influence
        432 (A & B)   Hawthorne   1   23    1923               PC            Colonial Revival
        """,
        7,
    ),
    (
        "brunner_section_slice",
        "Brunner-Harmonium Historic District",
        "BRUNNER_SECTION",
        """
        DISTRICT NAME: Brunner-Harmonium
        OWNER: John Q. Citizen
        4000 Fake Pre-Section St   Brunner   1   1   1910   C   Queen Anne
        Building Inventory
        Property Address   Subdivision   Block   Lots   Circa Year Built   Bldg Status   Architectural Style
        4519 Floyd St      Brunner       28      7      1915               C             Pyramid Roof
                                                                                         Cottage
        4605 Blossom St    Brunner       16      10     n/a                V             n/a
        Bibliography
        9999 Fake Post-Section St  Brunner   2   2   1920   C   Bungalow
        """,
        2,
    ),
    (
        "glenbrook_fullword",
        "Glenbrook Valley Historic District",
        "GLENBROOK_FULLWORD",
        """
        PROPOSED GLENBROOK VALLEY HISTORIC DISTRICT
        8002 ARLETTA CT    5 R/P    7    14    1965    Contributing              Modern Ranch
        8005 ARLETTA CT    5 R/P    7    16    1958    Potentially Contributing  Traditional Ranch
        8010 ARLETTA CT    5 R/P    7    11    2004    Non-Contributing          New Construction
        8012 ARLETTA CT    5 R/P    7    12    n/a     Vacant
        """,
        4,
    ),
    (
        "norhill_status_last",
        "Norhill Historic District",
        "NORHILL_STATUS_LAST",
        """
        INVENTORY OF PROPERTIES FOR NORHILL HISTORIC DISTRICT
        1002 ALGREGG ST   LT 1 & TR 2A BLK 116 NORTH NORHILL  1925     Pediment Bungalow   PC
                                                              1930     Garage Apartment    C
        1008 ALGREGG ST   TR 2 BLK 116 NORTH NORHILL          c.1924   Bungalow            C
        """,
        2,
    ),
    (
        "high_first_ward_year_last",
        "High First Ward Historic District",
        "HIGH_FIRST_WARD_YEAR_LAST",
        """
        HIGH FIRST WARD HISTORIC DISTRICT
        1610 Crockett St   3   288   Baker NSBB   C    Queen Anne L-Plan   1905
        1619 Crockett St   12  289   Baker NSBB   V    Vacant              n/a
        1621 1/2 Weber St  4   270   Baker NSBB   PC   Shotgun Cottage     c. 1912
        """,
        3,
    ),
    (
        "old_sixth_ward_status_only",
        "Old Sixth Ward Historic District",
        "OLD_SIXTH_WARD_STATUS_ONLY",
        """
        OLD SIXTH WARD PROTECTED HISTORIC DISTRICT
        805 COLORADO ST     LT 12              HEISS GREGOR     C
        1802 DECATUR ST     TRS 8B & 9 BLK 444 BAKER W R NSBB   V
        1910 SABINE ST      LT 4 BLK 312       BAKER W R NSBB   NC
        """,
        3,
    ),
    (
        "shadow_lawn_aka_and_vacant",
        "Shadow Lawn Historic District",
        "SHADOW_LAWN",
        """
        INVENTORY OF SHADOW LAWN HISTORIC DISTRICT
        1 Shadow Lawn
        aka 1205 Bissonnet    Lt 1, Trs 1B, 1C    1928-29    C    Georgian/Colonial Revival
        9 Shadow Lawn         Lt 5                1926       C    Mediterranean Villa
        1402 Remington Lane   Tr 12A              n/a
        """,
        3,
    ),
    (
        "west_11th_interleaved",
        "West Eleventh Place Historic District",
        "WEST_11TH_INTERLEAVED",
        """
        W 11TH PLACE HISTORIC DISTRICT
        Cul-de-sac Plant Area
        East Gate Pier
                               Lt 6 & Trs 5       1925       C    French Provincial
        4 West 11th Place      Turner Place
                               NC    Garage
        8-A West 11th Place    Lt 8               1927       C    English Tudor
        """,
        2,
    ),
]


@pytest.mark.parametrize(
    ("case_id", "district_name", "layout", "sample_text", "expected_count"),
    _HPO_LAYOUT_TEST_CASES,
    ids=[c[0] for c in _HPO_LAYOUT_TEST_CASES],
)
def test_parse_district_inventory_layout_families(
    case_id: str,
    district_name: str,
    layout: str,
    sample_text: str,
    expected_count: int,
) -> None:
    """Verify each COH HPO district inventory layout family independently."""
    rows = parse_district_inventory_text(district_name, sample_text, layout)
    assert len(rows) == expected_count

    if case_id == "standard_audubon_wrapped_and_secondary":
        assert rows[0]["address"] == "3401 Audubon Pl."
        assert rows[0]["survey_status"] == "Non-Contributing"
        assert rows[0]["survey_style"] == "Traditional"
        assert rows[1]["address"] == "3403 Audubon Pl."
        assert rows[1]["survey_style"] == "Contemporary Apartment Building"
        assert rows[2]["address"] == "100 Avondale"
        assert rows[2]["survey_status"] == "Potentially Contributing"
        assert rows[2]["secondary"] == [
            {"status": "Potentially Contributing", "year": "", "type": "Garage"},
            {"status": "Contributing", "year": "c. 1920", "type": "Carriage house"},
        ]
        assert rows[4]["survey_year"] == 1923
        assert rows[4]["survey_year_raw"] == "1923-24"
        assert rows[7]["address"] == "111 31 1/2 ST"
        assert rows[8]["address"] == "3410 Burlington"
        assert rows[8]["alt_address"] == "3412 Burlington"
        assert rows[9]["address"] == "2105 Bayland Ave"
    elif case_id == "standard_no_split_num_bleed":
        assert rows[0]["address"] == "3401 Audubon Pl."
    elif case_id == "standard_main_street_market_square":
        assert rows[0]["survey_status"] == "Vacant"
        assert rows[0]["survey_year"] == 0
        assert rows[1]["survey_status"] == "Contributing"
        assert rows[1]["survey_year"] == 1882
        assert rows[2]["address"] == "112 Travis"
        assert rows[3]["address"] == "522-524 Hawthorne"
        assert rows[4]["address"] == "201 Emerson"
        assert rows[4]["alt_address"] == "203 Emerson"
        assert len(rows[4]["secondary"]) == 2
        assert rows[5]["address"] == "705 Avondale"
        assert rows[5]["survey_style"] == "Prairie w/Craftsman influence"
        assert rows[6]["address"] == "432 Hawthorne"
        assert rows[6]["alt_address"] == "432-A & B Hawthorne"
    elif case_id == "brunner_section_slice":
        assert rows[0]["address"] == "4519 Floyd St"
        assert rows[0]["survey_style"] == "Pyramid Roof Cottage"
        assert rows[1]["survey_status"] == "Vacant"
        assert rows[1]["survey_style"] == ""
    elif case_id == "glenbrook_fullword":
        assert [r["survey_status"] for r in rows] == [
            "Contributing",
            "Potentially Contributing",
            "Non-Contributing",
            "Vacant",
        ]
        assert rows[0]["survey_style"] == "Modern Ranch"
    elif case_id == "norhill_status_last":
        assert rows[0]["survey_status"] == "Potentially Contributing"
        assert rows[0]["secondary"] == [
            {"status": "Contributing", "year": "1930", "type": "Garage Apartment"}
        ]
        assert rows[1]["survey_year"] == 1924
    elif case_id == "high_first_ward_year_last":
        assert rows[0]["survey_year"] == 1905
        assert rows[1]["survey_status"] == "Vacant"
        assert rows[1]["survey_style"] == ""
        assert rows[2]["address"] == "1621 1/2 Weber St"
        assert rows[2]["survey_status"] == "Potentially Contributing"
    elif case_id == "old_sixth_ward_status_only":
        assert [r["survey_status"] for r in rows] == ["Contributing", "Vacant", "Non-Contributing"]
        assert rows[0]["survey_year"] == 0
    elif case_id == "shadow_lawn_aka_and_vacant":
        assert rows[0]["address"] == "1 Shadow Lawn"
        assert rows[0]["alt_address"] == "1205 Bissonnet"
        assert rows[2]["survey_status"] == "Vacant"
    elif case_id == "west_11th_interleaved":
        assert rows[0]["address"] == "4 West 11th Place"
        assert rows[0]["survey_year"] == 1925
        assert rows[0]["secondary"] == [{"status": "Non-Contributing", "year": "", "type": "Garage"}]
        assert rows[1]["address"] == "8-A West 11th Place"


def test_parse_district_inventory_invalid_layout_raises() -> None:
    """Verify unsupported layout names fail fast with ValueError."""
    with pytest.raises(ValueError, match="Unsupported HPO inventory layout class"):
        parse_district_inventory_text("Unknown District", "100 Main St", "BOGUS_LAYOUT")


def test_parse_standard_layout_owner_column_character_offset_alignment() -> None:
    """Verify tabular OWNER columns never leak PII when LEGAL DESCRIPTION is blank nor delete legal_desc when OWNER is blank."""
    tabular_hcad_owner_sample = """
    HISTORIC DISTRICT DESIGNATION INVENTORY
    OWNER: Confidential Taxpayer Name
    APPLICANT: Preservation Applicant Name
    HCAD_NUM         PROPERTY ADDRESS      PROPERTY OWNER       LEGAL DESCRIPTION   YEAR BUILT   BLDG STATUS   STYLE
    0202560000001    512 Harvard St        DOE JANE             Lt 1 Blk 10         1915         C             Craftsman Bungalow
    0202560000002    516 Harvard St        CONFIDENTIAL OWNER   Lt 2 Blk 10         1985         NC            Contemporary
    """
    tab_rows = parse_district_inventory_text(
        "Houston Heights South Historic District", tabular_hcad_owner_sample, "STANDARD"
    )
    assert len(tab_rows) == 2
    assert tab_rows[0]["hcad_num"] == "0202560000001"
    assert tab_rows[0]["address"] == "512 Harvard St"
    assert tab_rows[0]["legal_desc"] == "Lt 1 Blk 10"
    assert tab_rows[1]["hcad_num"] == "0202560000002"
    assert tab_rows[1]["legal_desc"] == "Lt 2 Blk 10"
    dumped_tab = json.dumps(tab_rows)
    assert "DOE JANE" not in dumped_tab
    assert "CONFIDENTIAL OWNER" not in dumped_tab
    assert "Taxpayer" not in dumped_tab

    # Empty LEGAL DESCRIPTION cell before OWNER must not shift owner PII into legal_desc
    empty_legal_sample = (
        "PROPERTY ADDRESS   LEGAL DESCRIPTION   OWNER            YEAR BUILT   STATUS   STYLE\n"
        "1204 Heights Blvd                      Jane Q. Public   1922         C        Bungalow\n"
    )
    empty_legal_rows = parse_district_inventory_text(
        "Audubon Place Historic District", empty_legal_sample, "STANDARD"
    )
    assert len(empty_legal_rows) == 1
    assert empty_legal_rows[0]["address"] == "1204 Heights Blvd"
    assert empty_legal_rows[0]["legal_desc"] == ""
    assert "Jane Q. Public" not in json.dumps(empty_legal_rows)

    # Empty OWNER cell before LEGAL DESCRIPTION must preserve Lt/Blk legal_desc
    empty_owner_sample = (
        "PROPERTY ADDRESS   OWNER               LEGAL DESCRIPTION   YEAR BUILT   STATUS   STYLE\n"
        "1204 Heights Blvd                      Lt 4 Blk 12         1922         C        Bungalow\n"
    )
    empty_owner_rows = parse_district_inventory_text(
        "Audubon Place Historic District", empty_owner_sample, "STANDARD"
    )
    assert len(empty_owner_rows) == 1
    assert empty_owner_rows[0]["address"] == "1204 Heights Blvd"
    assert empty_owner_rows[0]["legal_desc"] == "Lt 4 Blk 12"

    # Page form-feeds (\f), headers, and bare page numbers must reset carry state without bleeding into style
    page_break_sample = (
        "300 Bayland Ave   Woodland Hts   5   Lt 1   1914   C   Queen Anne\n"
        "                                                                     Amended Sheet\n"
        "                                                                     1\n"
        "\f"
        "INVENTORY OF WOODLAND HEIGHTS HISTORIC DISTRICT\n"
        "PROPERTY ADDRESS   SUBDIVISION    BLOCK   LOT    YEAR BUILT   STATUS   STYLE\n"
        "614 Bayland Ave    Woodland Hts   5       Lt 9   1918         C        Craftsman Bungalow\n"
    )
    pb_rows = parse_district_inventory_text(
        "Woodland Heights Historic District", page_break_sample, "STANDARD"
    )
    assert len(pb_rows) == 2
    assert pb_rows[1]["address"] == "614 Bayland Ave"
    assert pb_rows[1]["survey_style"] == "Craftsman Bungalow"


def test_scrub_personal_header_names_and_usable_survey_style() -> None:
    """Verify singular/plural OWNER(S)/APPLICANT(S) header scrubbing and HPO survey style hygiene."""
    assert _scrub_personal_header_names("OWNER: Jane Q. Smith\nBuilt in 1912 as a foursquare.") == (
        "Built in 1912 as a foursquare."
    )
    assert _scrub_personal_header_names("APPLICANT: John R. Citizen. Built in 1926.") == "Built in 1926."
    assert (
        _scrub_personal_header_names(
            "OWNERS: Oliver and Beverly Pennington HPO FILE NO: 11L253 APPLICANTS: Same\nBuilt in 1911."
        )
        == "HPO FILE NO: 11L253 Built in 1911."
    )
    scrubbed_plural = _scrub_personal_header_names(
        "OWNERS: Mr. John R. Citizen, Jr. HPO FILE NO.: 16PL131 APPLICANTS: Rachel Example. Constructed in 1952."
    )
    assert "Citizen" not in scrubbed_plural
    assert "Rachel Example" not in scrubbed_plural
    assert "OWNERS:" not in scrubbed_plural
    assert "APPLICANTS:" not in scrubbed_plural
    assert "Constructed in 1952." in scrubbed_plural

    assert is_usable_hpo_survey_style("YEAR          BLDG") is False
    assert is_usable_hpo_survey_style("CIRCA      BLDG Georgian") is False
    assert is_usable_hpo_survey_style("1940 Added quarters in back") is False
    assert is_usable_hpo_survey_style("Craftsman Bungalow") is True


_HPO_CACHE_PDF_PATH = REPO_ROOT / "pipeline" / "cache" / "hpo_district_pdf_texts.json"


@pytest.mark.skipif(
    not _HPO_CACHE_PDF_PATH.exists(),
    reason="Local hpo_district_pdf_texts.json cache not present",
)
def test_cached_23_district_inventories_determinism_and_style_hygiene() -> None:
    """Verify all 23 cached COH HPO inventory PDFs parse deterministically into 6,169 clean survey rows."""
    pdf_doc = json.loads(_HPO_CACHE_PDF_PATH.read_text(encoding="utf-8"))
    assert len(DISTRICT_MANIFEST) == 23
    total_rows = 0
    for dn, cfg in DISTRICT_MANIFEST.items():
        d_rows = parse_district_inventory_text(dn, pdf_doc[cfg["inventory_key"]]["text"], cfg["layout"])
        total_rows += len(d_rows)
        for dr in d_rows:
            sy = dr.get("survey_style") or ""
            assert "  " not in sy, f"Uncollapsed multi-column style in {dn}: {sy!r}"
            assert "year bldg" not in sy.lower() and "circa bldg" not in sy.lower()
            if dn == "Main Street/Market Square Historic District":
                assert " " in dr["address"], f"Streetless address in {dn}: {dr['address']!r}"
    assert total_rows == 6169


def test_reconcile_landmark_nrhp_slash_prefix_and_conflict_guards(tmp_path: Path) -> None:
    """Verify NRHP secondary_pdf_url reconciliation blocks short slash prefixes and HCAD/address conflicts while matching double-hyphen ranges."""
    addr_dir = tmp_path / "addr_shards"
    addr_dir.mkdir()
    inputs = HarvestInputs(
        cdir=tmp_path,
        odir=tmp_path,
        coh_landmarks=[
            {"properties": {"OBJECTID": 1, "USER_LM_NUM": "LM1", "USER_SITE_NAME": "Old / Historic Chapel", "USER_SITE_ST_NUM": "999", "USER_SITE_ST_NAME": "Pine St"}},
            {"properties": {"OBJECTID": 2, "USER_LM_NUM": "LM2", "USER_SITE_NAME": "John & Mary Smith House", "USER_SITE_ST_NUM": "100", "USER_SITE_ST_NAME": "Oak St"}},
            {"properties": {"OBJECTID": 3, "USER_LM_NUM": "19PL169", "USER_SITE_NAME": "Isabella Court", "USER_SITE_ST_NUM": "3917", "USER_SITE_ST_NAME": "Main Street, #16"}},
            {"properties": {"OBJECTID": 4, "USER_LM_NUM": "LM4", "USER_DEMOD": "Y", "USER_SITE_NAME": "Demolished Cottage", "USER_SITE_ST_NUM": "400", "USER_SITE_ST_NAME": "Market St"}},
        ],
        existing_enriched=[
            {"objectid": 1, "lm_num": "LM1", "name": "Old / Historic Chapel", "address": "999 Pine St", "hcad_num": "0111111111111", "report_pdf_url": "", "secondary_pdf_url": ""},
            {"objectid": 2, "lm_num": "LM2", "name": "John & Mary Smith House", "address": "100 Oak St", "hcad_num": "0111111111112", "report_pdf_url": "", "secondary_pdf_url": ""},
            {"objectid": 3, "lm_num": "19PL169", "name": "Isabella Court", "address": "3917 Main Street, #16", "hcad_num": "", "report_pdf_url": "", "secondary_pdf_url": ""},
            {"objectid": 4, "lm_num": "LM4", "name": "Demolished Cottage", "address": "400 Market St", "hcad_num": "0990010000001", "report_pdf_url": "", "secondary_pdf_url": ""},
        ],
        lm_pdf_cache={},
        hpo_pdf_cache={},
        overlays={
            "landmarks": {"features": []},
            "nrhp_properties": {
                "features": [
                    {
                        "properties": {
                            "name": "Old Sixth Ward House",
                            "address": "1800 Sabine St",
                            "hcad_num": "0222222222222",
                            "nomination_pdf_url": "https://atlas.thc.texas.gov/NR/pdfs/111/111.pdf",
                        }
                    },
                    {
                        "properties": {
                            "name": "Smith House",
                            "address": "200 Elm St",
                            "hcad_num": "0222222222223",
                            "nomination_pdf_url": "https://atlas.thc.texas.gov/NR/pdfs/222/222.pdf",
                        }
                    },
                    {
                        "properties": {
                            "name": "Isabella Court",
                            "address": "3909--3917 S. Main St.",
                            "hcad_num": "",
                            "nomination_pdf_url": "https://atlas.thc.texas.gov/NR/pdfs/94000626/94000626.pdf",
                        }
                    },
                ]
            },
        },
        overrides_doc={},
        overrides={},
        buildings_fc={},
        search_idx=[],
        paths=HarvestBundlePaths(
            overlays=tmp_path / "overlays.json",
            overrides=tmp_path / "curated_overrides.json",
            buildings=tmp_path / "buildings.geojson",
            search_idx=tmp_path / "search_index.json",
            surveys=tmp_path / "hpo_district_surveys.json",
        ),
        addr_shards_dir=addr_dir,
    )
    res = _reconcile_landmark_hcads_and_nrhp(inputs)
    by_oid = {r["objectid"]: r for r in res.enriched_landmarks}
    assert by_oid[1]["secondary_pdf_url"] == ""
    assert by_oid[2]["secondary_pdf_url"] == ""
    assert by_oid[3]["secondary_pdf_url"] == "https://atlas.thc.texas.gov/NR/pdfs/94000626/94000626.pdf"
    assert by_oid[4]["hcad_num"] == ""
    assert "0990010000001" not in res.lms_by_hcad


def test_match_post2016_landmark_pdf_candidates_headers_coa_olivewood_and_tpia() -> None:
    """Verify page-1 PDF header check, COA/wrong-number rejection, Olivewood SBR fallback, and TPIA preservation."""
    olivewood_sbr = (
        "https://www.houstontx.gov/planning/Commissions/hahc_042326/"
        "C_Houston-Olivewood-Cemetery-NR-SBR-Draft.pdf"
    )
    coh_landmarks = [
        {
            "properties": {
                "OBJECTID": 1,
                "USER_LM_NUM": "LM210",
                "USER_PLM_NUM": "PLM190",
                "USER_HPO_ID": "HP2020_0045",
                "USER_SITE_NAME": "310 Main Commercial Building",
                "USER_SITE_ST_NUM": "310",
                "USER_SITE_ST_NAME": "Main Street",
            }
        },
        {
            "properties": {
                "OBJECTID": 2,
                "USER_LM_NUM": "LM180",
                "USER_PLM_NUM": "",
                "USER_SITE_NAME": "Olivewood Cemetery",
                "USER_SITE_ST_NUM": "1300",
                "USER_SITE_ST_NAME": "Court Street",
            }
        },
        {
            "properties": {
                "OBJECTID": 3,
                "USER_LM_NUM": "LM299",
                "USER_PLM_NUM": "PLM215",
                "USER_ILMS_HOLD": "Y",
                "USER_SITE_NAME": "Unpublished Hold House",
                "USER_SITE_ST_NUM": "909",
                "USER_SITE_ST_NAME": "Lovett Boulevard",
            }
        },
        {
            "properties": {
                "OBJECTID": 4,
                "USER_LM_NUM": "LM245",
                "USER_SITE_NAME": "Hildalgo Park Quiosco",
                "USER_SITE_ST_NUM": "7000",
                "USER_SITE_ST_NAME": "Avenue Q",
            }
        },
    ]
    existing_enriched = [
        {
            "objectid": 1,
            "lm_num": "LM210",
            "plm_num": "PLM190",
            "name": "310 Main Commercial Building",
            "address": "310 Main Street",
            "report_pdf_url": "",
            "secondary_pdf_url": "",
            "pdf_architect": "Unknown",
            "final_architect": "TBD",
            "final_style": "",
            "pdf_summary": "",
        },
        {
            "objectid": 2,
            "lm_num": "LM180",
            "plm_num": "",
            "name": "Olivewood Cemetery",
            "address": "1300 Court Street",
            "report_pdf_url": "",
            "secondary_pdf_url": "",
            "final_architect": "",
            "final_style": "",
            "pdf_summary": "",
        },
        {
            "objectid": 3,
            "lm_num": "LM299",
            "plm_num": "PLM215",
            "name": "Unpublished Hold House",
            "address": "909 Lovett Boulevard",
            "report_pdf_url": "",
            "secondary_pdf_url": "",
            "final_architect": "None",
            "final_style": "",
            "pdf_summary": "OWNER: Private Citizen. Historic brick residence.",
        },
        {
            "objectid": 4,
            "lm_num": "LM245",
            "plm_num": "",
            "name": "Hildalgo Park Quiosco",
            "address": "7000 Avenue Q",
            "report_pdf_url": "",
            "secondary_pdf_url": "",
            "final_architect": "",
            "final_style": "",
            "pdf_summary": "",
        },
    ]
    pdf_cache: dict[str, dict[str, Any]] = {
        # Rejected: COA application at exact same 310 Main Street address
        "https://www.houstontx.gov/planning/hahc/310_Main_COA_Final.pdf": {
            "ok": True,
            "text": (
                "CITY OF HOUSTON CERTIFICATE OF APPROPRIATENESS\n"
                "LANDMARK NAME: 310 Main Commercial Building\n"
                "LOCATION: 310 Main Street, Houston, Texas\n"
            ),
        },
        # Rejected: Official designation report for wrong street number (308 Main vs 310 Main)
        "https://www.houstontx.gov/planning/hahc/308_Main_Designation_Final.pdf": {
            "ok": True,
            "text": (
                "PROTECTED LANDMARK DESIGNATION REPORT\n"
                "Landmark Name: 308 Main Building\n"
                "Location: 308 Main Street, Houston, Texas\n"
                "HPO File No: HP2020_0044\n"
            ),
        },
        # Rejected: Unverified / HTTP failed candidate for 909 Lovett Boulevard
        "https://www.houstontx.gov/planning/hahc/909_Lovett_Designation.pdf": {
            "ok": False,
            "text": "PROTECTED LANDMARK DESIGNATION REPORT\nLocation: 909 Lovett Boulevard\n",
        },
        # Accepted: Verified page-1 Protected Landmark Designation Report for 310 Main Street
        "https://www.houstontx.gov/planning/hahc/310 Main Designation Final.pdf": {
            "ok": True,
            "text": (
                "CITY OF HOUSTON\n"
                "PROTECTED LANDMARK DESIGNATION REPORT\n"
                "Landmark Name: 310 Main Commercial Building\n"
                "Owner: Confidential Holding Trust\n"
                "Location: 310 Main Street, Houston, Texas\n"
                "HPO File No: HP2020_0045\n"
                "HISTORY AND SIGNIFICANCE:\n"
                "OWNER: Confidential Holding Trust. The three-story Beaux-Arts masonry building "
                "at 310 Main Street was designed by noted Houston architect Alfred C. Finn and "
                "constructed in 1914.\n"
            ),
        },
        # Accepted: Olivewood primary landmark report + SBR NRHP draft secondary PDF
        "https://www.houstontx.gov/planning/hahc/Olivewood_Cemetery_LM_Report.pdf": {
            "ok": True,
            "text": (
                "L A N D M A R K D E S I G N A T I O N R E P O R T\n"
                "Landmark Name: Olivewood Cemetery\n"
                "Location: 1300 Court Street, Houston, Texas\n"
                "HPO File No: LM180\n"
                "HISTORY AND SIGNIFICANCE:\n"
                "Olivewood Cemetery at 1300 Court Street was established in 1875 as one of "
                "Houston's earliest historic African American cemeteries.\n"
            ),
        },
        olivewood_sbr: {
            "ok": True,
            "text": "National Register of Historic Places Registration Form - Olivewood Cemetery",
        },
        # Accepted: Hidalgo/Hildalgo Park Quiosco spelling variant
        "https://www.houstontx.gov/planning/hahc/Hidalgo_Park_Quiosco_Report.pdf": {
            "ok": True,
            "text": (
                "LANDMARK DESIGNATION REPORT\n"
                "Landmark Name: Hidalgo Park Quiosco\n"
                "Location: 7000 Avenue Q, Houston, Texas\n"
                "HPO File No: LM245\n"
            ),
        },
    }

    updated, stats = match_post2016_landmark_pdf_candidates(
        coh_landmarks, existing_enriched, pdf_cache
    )
    assert stats == {
        "total_landmarks": 4,
        "newly_matched_pdfs": 3,
        "total_with_report_pdf": 3,
        "remaining_missing_pdfs": 1,
    }
    by_oid = {r["objectid"]: r for r in updated}
    assert by_oid[1]["report_pdf_url"] == (
        "https://www.houstontx.gov/planning/hahc/310%20Main%20Designation%20Final.pdf"
    )
    assert "COA" not in by_oid[1]["report_pdf_url"]
    assert "308_Main" not in by_oid[1]["report_pdf_url"]
    assert by_oid[1]["final_architect"] == "Alfred C. Finn"
    assert by_oid[1]["final_year_built"] == 1914
    assert by_oid[1]["final_style"] == "Beaux-Arts"
    assert "Confidential Holding Trust" not in by_oid[1].get("pdf_summary", "")

    assert by_oid[2]["report_pdf_url"] == (
        "https://www.houstontx.gov/planning/hahc/Olivewood_Cemetery_LM_Report.pdf"
    )
    assert by_oid[2]["secondary_pdf_url"] == olivewood_sbr

    # Unpublished USER_ILMS_HOLD landmark preserved as pending TPIA with empty report_pdf_url
    assert by_oid[3]["report_pdf_url"] == ""
    assert by_oid[3]["final_architect"] == ""
    assert by_oid[3]["pdf_summary"] == "Historic brick residence."

    assert by_oid[4]["report_pdf_url"] == (
        "https://www.houstontx.gov/planning/hahc/Hidalgo_Park_Quiosco_Report.pdf"
    )


def test_clean_architect_value_and_career_window_guard() -> None:
    """Verify placeholder architect filtering and ARCHITECT_CAREER_WINDOWS anachronism blocking."""
    for placeholder in (
        "",
        "-",
        "Unknown",
        "UNKNOWN",
        "N/A",
        "na",
        "None",
        "null",
        "TBD",
        "Undetermined",
        "Not Applicable",
        "Architect Unknown",
        "Builder Unknown",
    ):
        assert _clean_architect_value(placeholder) == ""

    assert _clean_architect_value("Birdsall P. Briscoe") == "Birdsall P. Briscoe"
    assert _clean_architect_value("Alfred C. Finn") == "Alfred C. Finn"

    # Mid-century / modern remodeling & 19th-century architects blocked outside career windows
    assert _architect_matches_year("John S. Chase", 1900) is False
    assert _architect_matches_year("MacKie & Kamrath", 1902) is False
    assert _architect_matches_year("Kamrath", 1905) is False
    assert _architect_matches_year("Wilson, Morris, Crain & Anderson", 1905) is False
    assert _architect_matches_year("Caudill Rowlett Scott", 1910) is False
    assert _architect_matches_year("Howard Barnstone", 1908) is False
    assert _architect_matches_year("Eugene T Heiner", 1926) is False
    assert _architect_matches_year("Eugene T. Heiner", 1889) is True

    # Contemporaneous career-window matches allowed
    assert _architect_matches_year("John S. Chase", 1962) is True
    assert _architect_matches_year("MacKie & Kamrath", 1952) is True
    assert _architect_matches_year("Birdsall P. Briscoe", 1927) is True
    assert _architect_matches_year("Alfred C. Finn", 1929) is True
    assert _architect_matches_year("", 1900) is True
    assert _architect_matches_year("Birdsall P. Briscoe", 0) is True


def _bld_feat(
    hc: str,
    addr: str,
    yr: int,
    style: str = "wood or light steel",
    bld_id: str | None = None,
) -> dict[str, Any]:
    """Construct a minimal `buildings.geojson` Point feature for synthetic harvest tests."""
    return {
        "type": "Feature",
        "properties": {
            "id": bld_id or hc,
            "hcad_num": hc,
            "address": addr,
            "year_built": yr,
            "style": style,
            "use_category": "Single-Family Residential",
        },
        "geometry": {"type": "Point", "coordinates": [-95.39, 29.74]},
    }


def _make_minimal_hpo_workspace(
    tmp_path: Path,
    *,
    audubon_rows: str = "3402 Audubon Pl   Montrose   16   Lt 1   1923   C   Craftsman Bungalow\n",
    coh_landmarks: list[dict[str, Any]] | None = None,
    existing_enriched: list[dict[str, Any]] | None = None,
    hist_parcels: list[dict[str, Any]] | None = None,
    nrhp_features: list[dict[str, Any]] | None = None,
    overrides: dict[str, Any] | None = None,
    buildings: list[dict[str, Any]] | None = None,
    search_idx: list[dict[str, Any]] | None = None,
    permits_by_prefix: dict[str, dict[str, Any]] | None = None,
    slab_accts: tuple[str, ...] = (),
    rebuilt_accts: tuple[str, ...] = (),
    stats_doc: dict[str, Any] | None = None,
) -> tuple[Path, Path]:
    """Create a composable, minimal 23-district synthetic workspace inside `tmp_path`."""
    repo_root = tmp_path / "repo"
    cdir = repo_root / "pipeline" / "cache"
    odir = repo_root / "app" / "public" / "data"
    permits_dir = odir / "permits"
    addr_shards_dir = odir / "address_shards"
    (cdir / str(ACTIVE_TAX_YEAR)).mkdir(parents=True)
    permits_dir.mkdir(parents=True)
    addr_shards_dir.mkdir(parents=True)

    bld_zip = cdir / str(ACTIVE_TAX_YEAR) / "Real_building_land.zip"
    res_lines = ["acct\tbld_num\tdate_erected\teff\tnote"]
    for r_hc in rebuilt_accts:
        res_lines.append(f"{r_hc}\t1\t1920\t2018\tTOTAL REBUILD FROM STUDS")
    elem_lines = ["acct\tbld_num\tcode\tadj\ttype\ttype_dscr\tcat_dscr"]
    for s_hc in slab_accts:
        elem_lines.append(f"{s_hc}\t1\tFND\t1.0\tSL\tFoundation\tConcrete Slab")
    with zipfile.ZipFile(bld_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("building_res.txt", "\n".join(res_lines) + "\n")
        zf.writestr("structural_elem1.txt", "\n".join(elem_lines) + "\n")

    hpo_pdf_cache: dict[str, dict[str, Any]] = {}
    for dname, cfg in DISTRICT_MANIFEST.items():
        lay = cfg["layout"]
        if dname == "Audubon Place Historic District":
            text = f"INVENTORY OF AUDUBON PLACE HISTORIC DISTRICT\n{audubon_rows}"
        elif lay == "BRUNNER_SECTION":
            text = (
                "Building Inventory\n"
                "4519 Floyd St   Brunner   28   7   1914   C   Pyramid Cottage\n"
                "Bibliography\n"
            )
        elif lay == "GLENBROOK_FULLWORD":
            text = "8002 ARLETTA CT   5 R/P   7   14   1962   Contributing   Modern Ranch\n"
        elif lay == "NORHILL_STATUS_LAST":
            text = "1002 ALGREGG ST   LT 1 BLK 116 NORTH NORHILL   1924   Pediment Bungalow   C\n"
        elif lay == "HIGH_FIRST_WARD_YEAR_LAST":
            text = "1610 Crockett St   3   288   Baker NSBB   C   Queen Anne   1908\n"
        elif lay == "OLD_SIXTH_WARD_STATUS_ONLY":
            text = "805 COLORADO ST   LT 12   HEISS GREGOR   C\n"
        elif lay == "SHADOW_LAWN":
            text = "9 Shadow Lawn   Lt 5   1926   C   Mediterranean Villa\n"
        elif lay == "WEST_11TH_INTERLEAVED":
            text = "4 West 11th Place   Lt 6   1926   C   French Provincial\n"
        else:
            text = "500 Sample St   Blk 1   Lt 1   1922   C   Bungalow\n"
        hpo_pdf_cache[cfg["inventory_key"]] = {"ok": True, "url": cfg["inventory_key"], "text": text}
        for doc_k in ("designation_report_key", "guidelines_key", "map_key"):
            if cfg[doc_k]:
                hpo_pdf_cache[cfg[doc_k]] = {"ok": True, "url": cfg[doc_k], "text": "Official PDF"}

    (cdir / "hpo_district_pdf_texts.json").write_text(json.dumps(hpo_pdf_cache), encoding="utf-8")
    enr_list = list(existing_enriched or [])
    (cdir / "coh_landmarks.json").write_text(json.dumps(coh_landmarks or []), encoding="utf-8")
    (cdir / "coh_landmarks_enriched.json").write_text(json.dumps(enr_list), encoding="utf-8")
    (cdir / "landmark_pdf_texts.json").write_text(json.dumps({}), encoding="utf-8")
    (cdir / "coh_historic_parcels.json").write_text(
        json.dumps({"features": list(hist_parcels or [])}), encoding="utf-8"
    )

    for pfx, shard_data in (permits_by_prefix or {}).items():
        with gzip.open(permits_dir / f"permits_{pfx}.json.gz", "wt", encoding="utf-8") as gz:
            json.dump(shard_data, gz)

    overlays = {
        "landmarks": {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "name": r["name"],
                        "address": r["address"],
                        "hcad_num": r.get("hcad_num", ""),
                    },
                    "geometry": {"type": "Point", "coordinates": [-95.39, 29.74]},
                }
                for r in enr_list
            ],
        },
        "historic_districts": {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"name": "Audubon Place Historic District", "citations": []},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [-95.40, 29.74],
                                [-95.38, 29.74],
                                [-95.38, 29.75],
                                [-95.40, 29.75],
                                [-95.40, 29.74],
                            ]
                        ],
                    },
                },
                {
                    "type": "Feature",
                    "properties": {"name": "Main Street Market Square Historic District", "citations": []},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [-95.37, 29.75],
                                [-95.35, 29.75],
                                [-95.35, 29.77],
                                [-95.37, 29.77],
                                [-95.37, 29.75],
                            ]
                        ],
                    },
                },
            ],
        },
        "nrhp_properties": {"type": "FeatureCollection", "features": list(nrhp_features or [])},
    }
    (odir / "overlays.json").write_text(json.dumps(overlays), encoding="utf-8")
    (odir / "curated_overrides.json").write_text(
        json.dumps({"overrides": dict(overrides or {})}), encoding="utf-8"
    )
    (odir / "buildings.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": list(buildings or [])}), encoding="utf-8"
    )
    (odir / "search_index.json").write_text(json.dumps(list(search_idx or [])), encoding="utf-8")
    (odir / "stats_summary.json").write_text(
        json.dumps(
            stats_doc
            or {
                "decade_counts": {"1910": 10, "1920": 20},
                "year_source_counts": {"hcad_cama": 30, "curated_landmark": 0, "archival": 0},
                "audit_queue_total": 42,
            }
        ),
        encoding="utf-8",
    )
    (odir / "pmtiles_manifest.json").write_text(
        json.dumps({"version": 3, "cache_bust": "20261001a"}), encoding="utf-8"
    )
    return cdir, odir


def test_cli_dry_run_vs_apply_sanitization_and_manifest_sync(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify dry-run vs --apply CLI, single-line JSON/PII sanitization, NRHP/TPIA CSV, and relative stats_summary deltas."""
    promo_hc = "0400020000002"
    nrhp_hc = "0300010000008"
    cdir, odir = _make_minimal_hpo_workspace(
        tmp_path,
        coh_landmarks=[
            {
                "properties": {
                    "OBJECTID": 10,
                    "USER_LM_NUM": "LM301",
                    "USER_PLM_NUM": "PLM301",
                    "USER_SITE_NAME": "Montrose Masonry Hall",
                    "USER_SITE_ST_NUM": "710",
                    "USER_SITE_ST_NAME": "Lovett Boulevard",
                }
            },
            {
                "properties": {
                    "OBJECTID": 14,
                    "USER_LM_NUM": "LM305",
                    "USER_ILMS_HOLD": "Y",
                    "USER_SITE_NAME": "9 Shadow Lawn Residence",
                    "USER_SITE_ST_NUM": "9",
                    "USER_SITE_ST_NAME": "Shadow Lawn Street",
                }
            },
        ],
        existing_enriched=[
            {
                "objectid": 10,
                "lm_num": "LM301",
                "plm_num": "PLM301",
                "name": "Montrose Masonry Hall",
                "address": "710 Lovett Boulevard",
                "hcad_num": promo_hc,
                "gis_year_built": 1914,
                "pdf_year_built": 1914,
                "final_year_built": 1914,
                "final_architect": "",
                "final_style": "Prairie School",
                "report_pdf_url": "https://www.houstontx.gov/planning/hahc/710_Lovett_LM.pdf",
                "secondary_pdf_url": "",
                "pdf_summary": "OWNERS: Plural Secret Owners APPLICANTS: Plural Secret Applicants. Built in 1914.",
            },
            {
                "objectid": 14,
                "lm_num": "LM305",
                "plm_num": "",
                "name": "9 Shadow Lawn Residence",
                "address": "9 Shadow Lawn Street",
                "hcad_num": "",
                "gis_year_built": 1926,
                "pdf_year_built": 0,
                "final_year_built": 1926,
                "final_architect": "",
                "final_style": "Mediterranean Villa",
                "report_pdf_url": "",
                "secondary_pdf_url": "",
                "pdf_summary": "",
            },
        ],
        hist_parcels=[
            {
                "properties": {
                    "HCAD_NUM": nrhp_hc,
                    "Historic_District_Name": "Shadow Lawn Historic District",
                    "Building_Classification": "Contributing",
                    "Site_addr_1": "9 SHADOW LAWN ST",
                    "Owner_Mailto": "SECRET OWNER ONE",
                    "Mail_Addr_1": "PO BOX 99999",
                }
            }
        ],
        nrhp_features=[
            {
                "type": "Feature",
                "properties": {
                    "name": "9 Shadow Lawn Residence",
                    "address": "9 Shadow Lawn St",
                    "hcad_num": nrhp_hc,
                    "nomination_pdf_url": "https://atlas.thc.texas.gov/NR/pdfs/100001234/100001234.pdf",
                },
                "geometry": {"type": "Point", "coordinates": [-95.39, 29.72]},
            }
        ],
        overrides={
            "0300010000001": {
                "hcad_num": "0300010000001",
                "address": "3402 AUDUBON PL",
                "year_built": 1923,
                "decade": 1920,
                "own_tier": "homestead",
                "landmark_summary": "OWNERS: Stale Preexisting Owner APPLICANTS: Stale Preexisting Applicant. Clean summary.",
            }
        },
        buildings=[_bld_feat(promo_hc, "710 LOVETT BLVD", 1920)],
        search_idx=[{"id": promo_hc, "hcad_num": promo_hc, "year_built": 1920, "style": "wood or light steel"}],
    )
    stats_before = json.loads((odir / "stats_summary.json").read_text(encoding="utf-8"))

    dry_stats = run_hpo_harvest(cache_dir=cdir, output_dir=odir, apply=False)
    assert dry_stats["apply"] is False
    assert dry_stats["districts_harvested"] == 23
    assert not (odir / "hpo_district_surveys.json").exists()
    summary_txt = format_hpo_harvest_cli_summary(dry_stats, elapsed_s=0.4)
    assert "DRY-RUN" in summary_txt and "23 / 23" in summary_txt

    cli.main(
        [
            "--mode",
            "harvest-hpo",
            "--cache-dir",
            str(cdir),
            "--output-dir",
            str(odir),
            "--apply",
            "--cache-bust",
            "20261109z",
        ]
    )
    assert "APPLIED" in capsys.readouterr().out

    for fn in (
        "hpo_district_surveys.json",
        "overlays.json",
        "curated_overrides.json",
        "buildings.geojson",
        "search_index.json",
    ):
        raw_out = (odir / fn).read_text(encoding="utf-8")
        assert "\n" not in raw_out.rstrip("\n"), f"{fn} must be single-line compact JSON"
        assert chr(0x2014) not in raw_out and chr(0x2013) not in raw_out
        for leaked in (
            "SECRET OWNER ONE",
            "PO BOX 99999",
            "Plural Secret Owners",
            "Plural Secret Applicants",
            "Stale Preexisting Owner",
            "Stale Preexisting Applicant",
        ):
            assert leaked not in raw_out

    manifest_after = json.loads((odir / "pmtiles_manifest.json").read_text(encoding="utf-8"))
    assert manifest_after["cache_bust"] == "20261109z"

    surveys_after = json.loads((odir / "hpo_district_surveys.json").read_text(encoding="utf-8"))
    assert surveys_after["generated_at"] == "2026-11-09"
    assert surveys_after["district_aliases"] == HPO_DISTRICT_NAME_ALIASES

    stats_after = json.loads((odir / "stats_summary.json").read_text(encoding="utf-8"))
    assert stats_after["audit_queue_total"] == stats_before["audit_queue_total"]
    assert stats_after["decade_counts"]["1910"] - stats_before["decade_counts"]["1910"] == 1
    assert stats_after["decade_counts"]["1920"] - stats_before["decade_counts"]["1920"] == -1
    assert (
        stats_after["year_source_counts"]["curated_landmark"]
        - stats_before["year_source_counts"]["curated_landmark"]
        == 1
    )
    assert (
        stats_after["year_source_counts"]["hcad_cama"]
        - stats_before["year_source_counts"]["hcad_cama"]
        == -1
    )

    overlays_after = json.loads((odir / "overlays.json").read_text(encoding="utf-8"))
    shadow_lm = next(
        f["properties"]
        for f in overlays_after["landmarks"]["features"]
        if f["properties"]["name"] == "9 Shadow Lawn Residence"
    )
    assert shadow_lm["hcad_num"] == nrhp_hc
    assert shadow_lm["secondary_pdf_url"] == "https://atlas.thc.texas.gov/NR/pdfs/100001234/100001234.pdf"
    msms_hd = next(
        f["properties"]
        for f in overlays_after["historic_districts"]["features"]
        if f["properties"]["name"] == "Main Street Market Square Historic District"
    )
    assert msms_hd["inventory_pdf_url"].endswith("/Main_Street_Market_Square_Inventory.pdf")

    with (cdir / "coh_landmarks_missing_pdfs_tpia.csv").open("r", encoding="utf-8") as cf:
        rows = list(csv.DictReader(cf))
    assert len(rows) == 1 and rows[0]["HPO_LM_Number"] == "LM305"


def test_harvest_promotion_guards_excluded_campus_slab_and_five_year_multiples(tmp_path: Path) -> None:
    """Verify EXCLUDED_AUTO_PROMOTION_HCADS, CAMPUS_SPLIT_HCADS, slab_hcads, and 5-year survey multiples are never promoted."""
    assert set(EXCLUDED_AUTO_PROMOTION_HCADS) == {
        "0200670000010",
        "0102490000006",
        "0202160000012",
        "0090860000002",
        "0202010000014",
    }
    campus_hc = "1223580020001"
    assert campus_hc in CAMPUS_SPLIT_HCADS
    slab_hc = "0300010000008"
    five_yr_hc = "0300010000005"

    cdir, odir = _make_minimal_hpo_workspace(
        tmp_path,
        audubon_rows=(
            "3404 Audubon Pl   Montrose   16   Lt 2   1918   C   Prairie\n"
            "3406 Audubon Pl   Montrose   16   Lt 3   1918   C   Foursquare\n"
            "3410 Audubon Pl   Montrose   16   Lt 5   1925   C   Bungalow\n"
        ),
        coh_landmarks=[
            {
                "properties": {
                    "OBJECTID": 12,
                    "USER_LM_NUM": "LM303",
                    "USER_SITE_NAME": "Reserved Fixture Landmark",
                    "USER_SITE_ST_NUM": "3404",
                    "USER_SITE_ST_NAME": "Audubon Place",
                }
            },
            {
                "properties": {
                    "OBJECTID": 13,
                    "USER_LM_NUM": "LM304",
                    "USER_SITE_NAME": "Campus Chapel Building",
                    "USER_SITE_ST_NUM": "6100",
                    "USER_SITE_ST_NAME": "Main Street",
                }
            },
        ],
        existing_enriched=[
            {
                "objectid": 12,
                "lm_num": "LM303",
                "plm_num": "",
                "name": "Reserved Fixture Landmark",
                "address": "3404 Audubon Place",
                "hcad_num": "0200670000010",
                "gis_year_built": 1917,
                "pdf_year_built": 1917,
                "final_year_built": 1917,
                "final_architect": "Birdsall P. Briscoe",
                "final_style": "Prairie",
                "report_pdf_url": "https://www.houstontx.gov/planning/hahc/3404_Audubon_LM.pdf",
                "secondary_pdf_url": "",
                "pdf_summary": "Reserved fixture test.",
            },
            {
                "objectid": 13,
                "lm_num": "LM304",
                "plm_num": "",
                "name": "Campus Chapel Building",
                "address": "6100 Main Street",
                "hcad_num": campus_hc,
                "gis_year_built": 1929,
                "pdf_year_built": 1929,
                "final_year_built": 1929,
                "final_architect": "William Ward Watkin",
                "final_style": "Mediterranean",
                "report_pdf_url": "https://www.houstontx.gov/planning/hahc/6100_Main_LM.pdf",
                "secondary_pdf_url": "",
                "pdf_summary": "Campus structure.",
            },
        ],
        hist_parcels=[
            {"properties": {"HCAD_NUM": "0200670000010", "Historic_District_Name": "Audubon Place Historic District", "Building_Classification": "Contributing", "Site_addr_1": "3404 AUDUBON PL"}},
            {"properties": {"HCAD_NUM": "0102490000006", "Historic_District_Name": "Audubon Place Historic District", "Building_Classification": "Contributing", "Site_addr_1": "3406 AUDUBON PL"}},
            {"properties": {"HCAD_NUM": "0202160000012", "Historic_District_Name": "Norhill Historic District", "Building_Classification": "Contributing", "Site_addr_1": "1002 ALGREGG ST"}},
            {"properties": {"HCAD_NUM": "0090860000002", "Historic_District_Name": "High First Ward Historic District", "Building_Classification": "Contributing", "Site_addr_1": "1610 CROCKETT ST"}},
            {"properties": {"HCAD_NUM": slab_hc, "Historic_District_Name": "Shadow Lawn Historic District", "Building_Classification": "Contributing", "Site_addr_1": "9 SHADOW LAWN ST"}},
            {"properties": {"HCAD_NUM": five_yr_hc, "Historic_District_Name": "Audubon Place Historic District", "Building_Classification": "Contributing", "Site_addr_1": "3410 AUDUBON PL"}},
        ],
        overrides={
            f"{campus_hc}#chapel": {
                "hcad_num": campus_hc,
                "address": "6100 MAIN ST",
                "year_built": 1929,
                "decade": 1920,
                "is_building_override": True,
                "replace_parcel_shards": False,
                "keep_shard_footprints": True,
                "lon": -95.401,
                "lat": 29.717,
            }
        },
        buildings=[
            _bld_feat("0200670000010", "3404 AUDUBON PL", 1920),
            _bld_feat("0102490000006", "3406 AUDUBON PL", 1920),
            _bld_feat("0202160000012", "1002 ALGREGG ST", 1920),
            _bld_feat("0090860000002", "1610 CROCKETT ST", 1910),
            _bld_feat(slab_hc, "9 SHADOW LAWN ST", 1920),
            _bld_feat(five_yr_hc, "3410 AUDUBON PL", 1920),
            _bld_feat(campus_hc, "6100 MAIN ST", 1920),
        ],
        slab_accts=(slab_hc,),
    )

    run_hpo_harvest(cache_dir=cdir, output_dir=odir, apply=True)
    ov_after = json.loads((odir / "curated_overrides.json").read_text(encoding="utf-8"))["overrides"]
    blds_after = {
        f["properties"]["id"]: f["properties"]
        for f in json.loads((odir / "buildings.geojson").read_text(encoding="utf-8"))["features"]
    }
    for blocked_hc in (*EXCLUDED_AUTO_PROMOTION_HCADS, campus_hc, slab_hc, five_yr_hc):
        assert blocked_hc not in ov_after
    assert f"{campus_hc}#chapel" in ov_after
    assert blds_after["0200670000010"]["year_built"] == 1920
    assert blds_after["0200670000010"]["landmark_code"] == "LM303"
    assert blds_after["0200670000010"]["style"] == "Prairie"


def test_harvest_multi_building_shared_hcad_and_suppressed_shard_isolation(tmp_path: Path) -> None:
    """Verify ADU/multi-address/multi-survey conflicts, shared-HCAD mismatch, #<slug> sub-buildings, and suppress_shard_hcads redirection."""
    master_hc = "0300010000001"
    suppressed_lot_hc = "0300010000099"
    adu_hc = "0300010000007"
    multi_addr_hc = "0300010000011"
    multi_survey_hc = "0300010000012"

    cdir, odir = _make_minimal_hpo_workspace(
        tmp_path,
        audubon_rows=(
            "3402 Audubon Pl   Montrose   16   Lt 1   1923   C   Craftsman Bungalow\n"
            "3412 Audubon Pl   Montrose   16   Lt 6   1919   C   Tudor Revival\n"
            "3414 Audubon Pl   Montrose   16   Lt 7   1915   C   Craftsman\n"
            "3418 Audubon Pl   Montrose   16   Lt 8   1918   C   Foursquare\n"
            "3418 1/2 Audubon Pl   Montrose   16   Lt 8   1968   NC   Garage Apartment\n"
        ),
        coh_landmarks=[
            {
                "properties": {
                    "OBJECTID": 11,
                    "USER_LM_NUM": "LM302",
                    "USER_SITE_NAME": "Master Lot Landmark House",
                    "USER_SITE_ST_NUM": "3402",
                    "USER_SITE_ST_NAME": "Audubon Place",
                }
            },
            {
                "properties": {
                    "OBJECTID": 15,
                    "USER_LM_NUM": "08PL63",
                    "USER_SITE_NAME": "Sweeney & Coombs Building",
                    "USER_SITE_ST_NUM": "310",
                    "USER_SITE_ST_NAME": "Main Street",
                }
            },
        ],
        existing_enriched=[
            {
                "objectid": 11,
                "lm_num": "LM302",
                "plm_num": "",
                "name": "Master Lot Landmark House",
                "address": "3402 Audubon Place",
                "hcad_num": suppressed_lot_hc,
                "gis_year_built": 1923,
                "pdf_year_built": 1923,
                "final_year_built": 1923,
                "final_architect": "Unknown",
                "final_style": "Craftsman Bungalow",
                "report_pdf_url": "https://www.houstontx.gov/planning/hahc/3402_Audubon_LM.pdf",
                "secondary_pdf_url": "",
                "pdf_summary": "Built in 1923.",
            },
            {
                "objectid": 15,
                "lm_num": "08PL63",
                "plm_num": "08PL63",
                "name": "Sweeney & Coombs Building",
                "address": "310 Main Street",
                "hcad_num": "0010330000012",
                "gis_year_built": 1889,
                "pdf_year_built": 1889,
                "final_year_built": 1889,
                "final_architect": "Eugene T Heiner",
                "final_style": "Victorian Italianate",
                "report_pdf_url": "https://www.houstontx.gov/planning/hahc/08PL63_Sweeney_Coombs.pdf",
                "secondary_pdf_url": "",
                "pdf_summary": "Built in 1889 at 310 Main St.",
            },
        ],
        hist_parcels=[
            {"properties": {"HCAD_NUM": suppressed_lot_hc, "Historic_District_Name": "Audubon Place Historic District", "Building_Classification": "Contributing", "Site_addr_1": "3402 AUDUBON PL"}},
            {"properties": {"HCAD_NUM": adu_hc, "Historic_District_Name": "Audubon Place Historic District", "Building_Classification": "Contributing", "Site_addr_1": "3412 AUDUBON PL"}},
            {"properties": {"HCAD_NUM": multi_addr_hc, "Historic_District_Name": "Audubon Place Historic District", "Building_Classification": "Contributing", "Site_addr_1": "3414 AUDUBON PL"}},
            {"properties": {"HCAD_NUM": multi_survey_hc, "Historic_District_Name": "Audubon Place Historic District", "Building_Classification": "Contributing", "Site_addr_1": "3418 AUDUBON PL"}},
            {"properties": {"HCAD_NUM": multi_survey_hc, "Historic_District_Name": "Audubon Place Historic District", "Building_Classification": "Contributing", "Site_addr_1": "3418 1/2 AUDUBON PL"}},
        ],
        overrides={
            master_hc: {
                "hcad_num": master_hc,
                "address": "3402 AUDUBON PL",
                "year_built": 1923,
                "decade": 1920,
                "bld_style": "Tilt-Up Concrete",
                "own_tier": "homestead",
                "suppress_shard_hcads": [suppressed_lot_hc],
            },
            "0010330000012": {
                "hcad_num": "0010330000012",
                "address": "911 PRESTON AVE",
                "landmark_name": "Ritz Theater",
                "year_built": 1926,
                "decade": 1920,
                "architect": "William Ward Watkin",
                "bld_style": "Commercial Theater",
                "own_tier": "regional_portfolio",
            },
        },
        buildings=[
            _bld_feat(master_hc, "3402 AUDUBON PL", 1923, style="Tilt-Up Concrete"),
            _bld_feat(master_hc, "3402 AUDUBON PL", 1927, style="structure", bld_id=f"{master_hc}#modern_print_shop"),
            _bld_feat("0010330000012", "911 PRESTON AVE", 1926, style="Commercial Theater"),
            _bld_feat(adu_hc, "3412 AUDUBON PL", 1920, bld_id=f"{adu_hc}#fp_1"),
            _bld_feat(adu_hc, "3412 AUDUBON PL", 2019, bld_id=f"{adu_hc}#fp_2"),
            _bld_feat(multi_addr_hc, "3414 AUDUBON PL", 1920, style="C", bld_id=f"{multi_addr_hc}#fp_1"),
            _bld_feat(multi_addr_hc, "3416 OTHER ST", 2005, style="Contemporary", bld_id=f"{multi_addr_hc}#fp_2"),
            _bld_feat(multi_survey_hc, "3418 AUDUBON PL", 1920, bld_id=f"{multi_survey_hc}#fp_1"),
            _bld_feat(multi_survey_hc, "3418 AUDUBON PL", 1920, bld_id=f"{multi_survey_hc}#fp_2"),
        ],
        search_idx=[
            {"id": master_hc, "hcad_num": master_hc, "year_built": 1923, "style": "Tilt-Up Concrete"},
            {"id": f"{master_hc}#modern_print_shop", "hcad_num": master_hc, "address": "3402 AUDUBON PL", "year_built": 1927, "style": "structure"},
            {"id": "0010330000012", "hcad_num": "0010330000012", "address": "911 PRESTON AVE", "year_built": 1926, "style": "structure"},
            {"id": "0010330000012_other", "hcad_num": "0010330000012", "address": "915 PRESTON AVE", "year_built": 1926, "style": "structure"},
        ],
    )

    run_hpo_harvest(cache_dir=cdir, output_dir=odir, apply=True)
    ov_after = json.loads((odir / "curated_overrides.json").read_text(encoding="utf-8"))["overrides"]
    blds_after = {
        f["properties"]["id"]: f["properties"]
        for f in json.loads((odir / "buildings.geojson").read_text(encoding="utf-8"))["features"]
    }
    sidx_after = {
        e["id"]: e for e in json.loads((odir / "search_index.json").read_text(encoding="utf-8"))
    }
    surveys_after = json.loads((odir / "hpo_district_surveys.json").read_text(encoding="utf-8"))

    for multi_hc in (adu_hc, multi_addr_hc, multi_survey_hc, suppressed_lot_hc):
        assert multi_hc not in ov_after
    assert blds_after[f"{adu_hc}#fp_1"]["year_built"] == 1920
    assert blds_after[f"{adu_hc}#fp_2"]["year_built"] == 2019
    assert blds_after[f"{multi_addr_hc}#fp_1"]["style"] == "Craftsman"
    assert blds_after[f"{multi_addr_hc}#fp_2"]["style"] == "Contemporary"
    assert len(surveys_after["by_hcad"][multi_survey_hc]) == 2

    assert ov_after[master_hc]["landmark_code"] == "LM302"
    assert ov_after[master_hc]["bld_style"] == "Craftsman Bungalow"
    assert sidx_after[master_hc]["style"] == "Craftsman Bungalow"
    assert suppressed_lot_hc not in surveys_after["by_hcad"]
    assert master_hc in surveys_after["by_hcad"]
    assert blds_after[f"{master_hc}#modern_print_shop"]["style"] == "structure"
    assert sidx_after[f"{master_hc}#modern_print_shop"]["style"] == "structure"

    assert ov_after["0010330000012"]["architect"] == "William Ward Watkin"
    assert ov_after["0010330000012"].get("landmark_code") != "08PL63"
    assert blds_after["0010330000012"].get("landmark_code") != "08PL63"
    assert sidx_after["0010330000012"]["style"] == "Commercial Theater"
    assert sidx_after["0010330000012_other"]["style"] == "structure"


def test_harvest_sec_25025_confidential_shield_vs_investor_portfolio_stamping(tmp_path: Path) -> None:
    """Verify Sec. 25.025 confidential shield blocks stale port_id while non-confidential investor parcel keeps portfolio metadata."""
    conf_hc = "0609990000001"
    investor_hc = "0400020000002"
    cdir, odir = _make_minimal_hpo_workspace(
        tmp_path,
        audubon_rows="3408 Audubon Pl   Montrose   16   Lt 4   1916   C   Colonial Revival\n",
        coh_landmarks=[
            {
                "properties": {
                    "OBJECTID": 10,
                    "USER_LM_NUM": "LM301",
                    "USER_PLM_NUM": "PLM301",
                    "USER_SITE_NAME": "Montrose Masonry Hall",
                    "USER_SITE_ST_NUM": "710",
                    "USER_SITE_ST_NAME": "Lovett Boulevard",
                }
            }
        ],
        existing_enriched=[
            {
                "objectid": 10,
                "lm_num": "LM301",
                "plm_num": "PLM301",
                "name": "Montrose Masonry Hall",
                "address": "710 Lovett Boulevard",
                "hcad_num": investor_hc,
                "gis_year_built": 1914,
                "pdf_year_built": 1914,
                "final_year_built": 1914,
                "final_architect": "MacKie & Kamrath",
                "final_style": "Prairie School",
                "report_pdf_url": "https://www.houstontx.gov/planning/hahc/710_Lovett_LM.pdf",
                "secondary_pdf_url": "",
                "pdf_summary": "Built in 1914.",
            }
        ],
        hist_parcels=[
            {
                "properties": {
                    "HCAD_NUM": conf_hc,
                    "Historic_District_Name": "Audubon Place Historic District",
                    "Building_Classification": "Contributing",
                    "Site_addr_1": "3408 AUDUBON PL",
                    "Owner_Mailto": "CONFIDENTIAL OWNER",
                    "Mail_Addr_1": "PROTECTED INDIVIDUAL HOMESTEAD",
                }
            }
        ],
        permits_by_prefix={
            "040": {investor_hc: {"yr": 1920, "ot": "regional_portfolio", "pid": "p_lovett_llc"}},
            "060": {conf_hc: {"yr": 1920, "conf": 1, "ot": "regional_portfolio", "pid": "p_leaked_confidential_pid"}},
        },
        buildings=[
            _bld_feat(investor_hc, "710 LOVETT BLVD", 1920),
            _bld_feat(conf_hc, "3408 AUDUBON PL", 1920),
        ],
    )

    run_hpo_harvest(cache_dir=cdir, output_dir=odir, apply=True)
    ov_after = json.loads((odir / "curated_overrides.json").read_text(encoding="utf-8"))["overrides"]

    inv_ov = ov_after[investor_hc]
    assert inv_ov["year_built"] == 1914
    assert inv_ov["own_tier"] == "regional_portfolio"
    assert inv_ov["port_id"] == "p_lovett_llc"
    assert inv_ov["architect"] == ""

    conf_ov = ov_after[conf_hc]
    assert conf_ov["year_built"] == 1916
    assert conf_ov["own_tier"] == "homestead"
    assert "port_id" not in conf_ov
    assert "p_leaked_confidential_pid" not in json.dumps(ov_after)


def test_resolve_override_own_tier_fail_closed_invariants(tmp_path: Path) -> None:
    """Verify Sec. 25.025 fail-closed behavior when permits_dir, shard file, or account entry is missing."""
    pdir = tmp_path / "permits"
    pdir.mkdir()
    with gzip.open(pdir / "permits_040.json.gz", "wt", encoding="utf-8") as gz:
        gz.write(json.dumps({"0400020000002": {"ot": "regional_portfolio", "pid": "p_lovett_llc"}}))
    with gzip.open(pdir / "permits_060.json.gz", "wt", encoding="utf-8") as gz:
        gz.write(json.dumps({"0609990000001": {"conf": 1}}))

    ot_conf, pid_conf, _ = resolve_override_own_tier_and_port(
        "0609990000001", {}, "", "3408 AUDUBON PL", permits_dir=pdir, permit_shard_cache={}
    )
    assert ot_conf == "homestead" and pid_conf == ""

    ot_missing_dir, pid_missing_dir, _ = resolve_override_own_tier_and_port(
        "0041360000004",
        {"own_tier": "local_small", "port_id": "port_042"},
        "Landmark",
        "123 Main St",
        permits_dir=tmp_path / "nonexistent_permits",
        permit_shard_cache={},
    )
    assert ot_missing_dir == "homestead" and pid_missing_dir == ""

    ot_missing_shard, pid_missing_shard, _ = resolve_override_own_tier_and_port(
        "0041360000004",
        {"own_tier": "local_small", "port_id": "port_042"},
        "Landmark",
        "123 Main St",
        permits_dir=pdir,
        permit_shard_cache={},
    )
    assert ot_missing_shard == "homestead" and pid_missing_shard == ""

    ot_absent_acct, pid_absent_acct, _ = resolve_override_own_tier_and_port(
        "0409999999999",
        {"own_tier": "local_small", "port_id": "port_042"},
        "",
        "712 LOVETT BLVD",
        permits_dir=pdir,
        permit_shard_cache={},
    )
    assert ot_absent_acct == "homestead" and pid_absent_acct == ""


def test_promote_year_override_provenance_and_multi_footprint_guards(tmp_path: Path) -> None:
    """Verify OverrideProvenance stamping, canonical VALID_CONTRIBUTING_VALUES fallback, and distinct-year rejection."""
    pdir = tmp_path / "permits"
    pdir.mkdir()
    with gzip.open(pdir / "permits_037.json.gz", "wt", encoding="utf-8") as gz:
        gz.write(json.dumps({"0379990000001": {"ot": "homestead"}}))

    mixed_decade_blds = [
        {"properties": {"id": "0379990000001#fp_1", "year_built": 1910, "style": "wood or light steel"}},
        {"properties": {"id": "0379990000001#fp_2", "year_built": 1950, "style": "wood or light steel"}},
    ]
    prov = OverrideProvenance(
        verified_by="COH HPO Historic District Survey",
        source_type="COH HPO Historic District Survey",
        source_citation="Test",
        source_url="https://www.houstontx.gov/inv.pdf",
        year_source="archival",
    )
    assert (
        _promote_year_override(
            hc="0379990000001",
            target_blds=mixed_decade_blds,
            auth_yr=1913,
            allowed_curr_years=(1900, 1910, 1920, 1930, 1940, 1950),
            rebuilt_hcads=set(),
            slab_hcads=set(),
            provenance=prov,
            address="100 TEST ST",
            historic_district="Norhill Historic District",
            contributing="Contributing",
            style="Craftsman Bungalow",
            architect="",
            permits_dir=pdir,
            permit_shard_cache={},
        )
        is None
    )

    uniform_bld = [{"properties": {"id": "0379990000001", "year_built": 1920, "style": "C"}}]
    promoted = _promote_year_override(
        hc="0379990000001",
        target_blds=uniform_bld,
        auth_yr=1913,
        allowed_curr_years=(1910, 1920, 1930),
        rebuilt_hcads=set(),
        slab_hcads=set(),
        provenance=prov,
        address="100 TEST ST",
        historic_district="Norhill Historic District",
        contributing="Potentially Contributing",
        style="Craftsman Bungalow",
        architect="",
        permits_dir=pdir,
        permit_shard_cache={},
    )
    assert promoted is not None
    new_ov, shift = promoted
    assert new_ov["contributing"] == "Outside Historic District"
    assert uniform_bld[0]["properties"]["year_source"] == "archival"
    assert shift == PromotedAccountShift(
        hc="0379990000001",
        new_dec_key="1910",
        new_year_source="archival",
        fallback_transitions=(("1920", "1910", "hcad_cama", "archival"),),
    )


def test_post1965_undated_landmark_sibling_survey_style_and_countywide_stats(tmp_path: Path) -> None:
    """Verify post-1965 lm_yr==0 landmark preservation, sibling HPO survey style parity, and aligned_ndjson footprint transitions."""
    assert {"n/a", "na", "-"}.issubset(GENERIC_STYLE_PLACEHOLDERS)
    assert _has_opposing_directional("310 E 9th St", "310 W 9TH ST") is True
    assert _has_opposing_directional("310 E 9th St", "310 E 9TH ST") is False
    assert _is_compatible_entity("0300010000001#modern_print_shop", "3402 AUDUBON PL", {"3402 AUDUBON"}) is False
    assert _is_compatible_entity("0300010000001#fp_1", "3402 AUDUBON PL", {"3402 AUDUBON"}) is True
    assert (
        _is_compatible_entity(
            "1427070010001",
            "310 W 9TH ST",
            {"310 E 9TH", "310 9TH"},
            "310 E 9th St",
        )
        is False
    )

    # District survey replacement vs Individual Landmark replacement (lm_yr == 0 preserved)
    assert _is_post_survey_replacement(2008, 1925, "Potentially Contributing") is True
    assert _is_post_survey_replacement(1992, 0, "Contributing") is True
    assert _is_post_survey_replacement(1968, 1960, "Contributing") is False
    assert _is_post_landmark_replacement(1968, 0) is False
    assert _is_post_landmark_replacement(2014, 0) is False
    assert _is_post_landmark_replacement(2003, 1906) is True

    # Sibling survey row ('Queen Anne') resolves identically when primary row has 'Commercial'
    hc = "0620950000004"
    row_s1 = SurveyParcelRow(
        sid="s1",
        district="Norhill Historic District",
        district_idx=0,
        inventory_pdf_url="https://www.houstontx.gov/inv.pdf",
        address="1016 Algregg St",
        alt_address="",
        hcad_num=hc,
        survey_status="Contributing",
        survey_year=1925,
        survey_year_raw="1925",
        survey_style="Commercial",
        legal_desc="",
        secondary=(),
    )
    row_s2 = SurveyParcelRow(
        sid="s2",
        district="Norhill Historic District",
        district_idx=0,
        inventory_pdf_url="https://www.houstontx.gov/inv.pdf",
        address="1016 Algregg St",
        alt_address="",
        hcad_num=hc,
        survey_status="Contributing",
        survey_year=0,
        survey_year_raw="",
        survey_style="Queen Anne",
        legal_desc="",
        secondary=(SurveySecondaryRow(status="Non-Contributing", year="2015", type="Garage"),),
    )
    survey_rows_by_id = {"s1": row_s1, "s2": row_s2}
    resolved_style = _resolve_entity_hpo_survey_style(
        hc=hc,
        ent_id=hc,
        ent_addr="1016 ALGREGG ST",
        ent_year=1925,
        best_survey_for_hcad={hc: row_s1},
        survey_by_hcad={hc: ["s1", "s2"]},
        survey_rows_by_id=survey_rows_by_id,
    )
    assert resolved_style == "Queen Anne"
    assert (
        _has_parcel_survey_conflict(
            ["s1", "s2"],
            survey_rows_by_id,
            1925,
            has_multiple_primary_buildings=False,
        )
        is False
    )
    assert (
        _has_parcel_survey_conflict(
            ["s1", "s2"],
            survey_rows_by_id,
            1925,
            has_multiple_primary_buildings=True,
        )
        is True
    )

    # Countywide aligned_ndjson footprint transitions count multi-footprint + suppressed-redirect shards
    nd_dir = tmp_path / "aligned_ndjson"
    nd_dir.mkdir()
    (nd_dir / "buildings_sw.geojsonseq").write_text(
        "\n".join(
            [
                json.dumps({"properties": {"id": "0020320000001#fp_1", "hcad_num": "0020320000001", "year_built": 1920}}),
                json.dumps({"properties": {"id": "0020320000099#fp_2", "hcad_num": "0020320000099", "year_built": 1920, "year_source": "hcad_extra_feature"}}),
                json.dumps({"properties": {"id": "0020320000001#annex", "hcad_num": "0020320000001", "year_built": 2005}}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    inputs = HarvestInputs(
        cdir=tmp_path,
        odir=tmp_path,
        coh_landmarks=[],
        existing_enriched=[],
        lm_pdf_cache={},
        hpo_pdf_cache={},
        overlays={},
        overrides_doc={},
        overrides={},
        buildings_fc={},
        search_idx=[],
        paths=HarvestBundlePaths(
            overlays=tmp_path / "overlays.json",
            overrides=tmp_path / "curated_overrides.json",
            buildings=tmp_path / "buildings.geojson",
            search_idx=tmp_path / "search_index.json",
            surveys=tmp_path / "hpo_district_surveys.json",
        ),
        suppressed_redirect={"0020320000099": "0020320000001"},
    )
    transitions = _compute_countywide_stat_transitions(
        inputs,
        [
            PromotedAccountShift(
                hc="0020320000001",
                new_dec_key="1910",
                new_year_source="curated_landmark",
                fallback_transitions=(("1920", "1910", "hcad_cama", "curated_landmark"),),
            )
        ],
    )
    assert transitions == [
        ("1920", "1910", "hcad_cama", "curated_landmark"),
        ("1920", "1910", "hcad_extra_feature", "curated_landmark"),
    ]


def test_partial_override_without_year_built_respects_post_1965_standing_building_guard(
    tmp_path: Path,
) -> None:
    """Partial curated_overrides entries omitting year_built must inherit standing building year (>1965) and reject historic landmark/survey backfills."""
    repl_hc = "0620950000999"
    cdir, odir = _make_minimal_hpo_workspace(
        tmp_path,
        audubon_rows="3408 Audubon Pl   Montrose   16   Lt 4   1925   C   Craftsman Bungalow\n",
        coh_landmarks=[
            {
                "properties": {
                    "OBJECTID": 88,
                    "USER_LM_NUM": "LM888",
                    "USER_SITE_NAME": "Historic Predecessor House",
                    "USER_SITE_ST_NUM": "3408",
                    "USER_SITE_ST_NAME": "Audubon Place",
                }
            }
        ],
        existing_enriched=[
            {
                "objectid": 88,
                "lm_num": "LM888",
                "plm_num": "",
                "name": "Historic Predecessor House",
                "address": "3408 Audubon Place",
                "hcad_num": repl_hc,
                "gis_year_built": 1910,
                "pdf_year_built": 1910,
                "final_year_built": 1910,
                "final_architect": "Alfred C. Finn",
                "final_style": "Queen Anne",
                "report_pdf_url": "https://www.houstontx.gov/planning/HistoricPres/landmarks/LM888.pdf",
                "secondary_pdf_url": "",
                "pdf_summary": "Built in 1910.",
            }
        ],
        hist_parcels=[
            {
                "properties": {
                    "HCAD_NUM": repl_hc,
                    "Historic_District_Name": "Audubon Place Historic District",
                    "Building_Classification": "Contributing",
                    "Site_addr_1": "3408 AUDUBON PL",
                }
            }
        ],
        overrides={
            repl_hc: {
                "hcad_num": repl_hc,
                "address": "3408 AUDUBON PL",
                "building_name": "2019 Modern Townhome",
                "override_mode": "parcel_replacement",
            }
        },
        buildings=[_bld_feat(repl_hc, "3408 AUDUBON PL", 2019)],
        search_idx=[
            {
                "id": repl_hc,
                "hcad_num": repl_hc,
                "address": "3408 AUDUBON PL",
                "year_built": 2019,
                "style": "wood or light steel",
            }
        ],
    )
    run_hpo_harvest(cache_dir=cdir, output_dir=odir, apply=True)
    ov_after = json.loads((odir / "curated_overrides.json").read_text(encoding="utf-8"))["overrides"][repl_hc]
    assert not ov_after.get("landmark_report_url")
    assert not ov_after.get("landmark_code")
    assert not ov_after.get("architect")
    assert not ov_after.get("bld_style")

    sidx_after = json.loads((odir / "search_index.json").read_text(encoding="utf-8"))[0]
    assert not sidx_after.get("landmark_code")
    assert not sidx_after.get("architect")
    assert sidx_after.get("style") == "wood or light steel"


def test_hpo_survey_year_promotion_respects_earlier_city_directory_occupant(
    tmp_path: Path,
) -> None:
    """Circa HPO survey years newer than a verified 1866-1926 City Directory occupant must not overwrite year_built."""
    cd_hc = "0607770000014"
    cdir, odir = _make_minimal_hpo_workspace(
        tmp_path,
        audubon_rows="3420 Audubon Pl   Montrose   16   Lt 9   1928   PC   Hipped Bungalow\n",
        hist_parcels=[
            {
                "properties": {
                    "HCAD_NUM": cd_hc,
                    "Historic_District_Name": "Audubon Place Historic District",
                    "Building_Classification": "Contributing",
                    "Site_addr_1": "3420 AUDUBON PL",
                }
            }
        ],
        buildings=[_bld_feat(cd_hc, "3420 AUDUBON PL", 1920)],
        search_idx=[
            {
                "id": cd_hc,
                "hcad_num": cd_hc,
                "address": "3420 AUDUBON PL",
                "year_built": 1920,
                "style": "wood or light steel",
            }
        ],
    )
    (odir / "citydir_index.json").write_text(
        json.dumps({"by_hcad": {cd_hc: {"e": 1918, "y": [1918, 1920, 1922]}}}),
        encoding="utf-8",
    )
    run_hpo_harvest(cache_dir=cdir, output_dir=odir, apply=True)

    ov_after = json.loads((odir / "curated_overrides.json").read_text(encoding="utf-8"))["overrides"]
    assert cd_hc not in ov_after
    bld_after = json.loads((odir / "buildings.geojson").read_text(encoding="utf-8"))["features"][0]["properties"]
    assert bld_after["year_built"] == 1920
    assert bld_after["style"] == "Hipped Bungalow"
    surveys_after = json.loads((odir / "hpo_district_surveys.json").read_text(encoding="utf-8"))
    assert cd_hc in surveys_after["by_hcad"]


def test_js_python_hpo_normalization_and_style_parity() -> None:
    """Enforce live 1:1 Node.js <-> Python parity in Layer 1B CI where both Node 22 and pipeline/.venv exist."""
    node_bin = shutil.which("node")
    assert node_bin is not None, "node executable must be available for JS/Python HPO parity verification"

    half_frac = chr(0x00BD)
    nb_hyphen = chr(0x2011)
    en_dash = chr(0x2013)
    addr_vectors = [
        "1119 North Walling Street, Houston, TX 77009",
        "1119 N. Walling St.",
        "1511 North Boulevard",
        "1511 N. Blvd.",
        "1512 South Boulevard",
        "1512 S Blvd",
        "3917 Main Street, #16",
        "2700 N Albany St Ste 100, Houston TX 77006",
        "804 Avondale St Apt B",
        "512 Harvard St Rear",
        "512 Harvard St (Rear)",
        f"3410 {half_frac} Burlington St",
        "3410 1/2 Burlington Street",
        "18361/2 Columbia St",
        "2091/2 E WOODLAND ST",
        "209 E WOODLAND ST 1/2",
        "701 E 51/2 St",
        "111 E 31st 1/2 St",
        f"111 E. 31 {half_frac} Street",
        f"111 31 {half_frac} ST",
        "405 Saint Emanuel St",
        "405 St. Emanuel Street",
        "1200 Mount Vernon St",
        "1200 Mt. Vernon Street",
        "1015 Avenue A",
        "1015 Ave. A",
        "7106 Avenue E",
        "7106 AVE E",
        "7106 Avenue N",
        "7106 AVE N",
        "1000 Heights Blvd N",
        "1000 Heights Blvd S",
        "2200 Memorial Dr E",
        "2200 Memorial Dr W",
        "4-B West Eleventh Place",
        f"4{nb_hyphen}B West 11th Pl",
        "14-D West 11th Place",
        "14D West 11th Place",
        f"1414{en_dash}1416 East Austin St",
        "1647-49 Cortlandt St",
        "120-122 E 11th St",
        "802/804 Commerce St",
        "0 North Blvd",
        "Avenida De Las Americas",
        "Main Street",
        "",
    ]
    street_key_inputs = [
        "1119 North Walling Street",
        f"1414{en_dash}1416 East Austin St",
        "1647-49 Cortlandt St",
        "120-122 E 11th St",
        "18361/2 Columbia St",
        "209 E WOODLAND ST 1/2",
        "701 E 51/2 St",
        "802/804 Commerce St",
        "3909--3917 S. Main St.",
        "14-D West 11th Place",
        "111 E 31st 1/2 St",
        "Avenida De Las Americas",
    ]
    tail_probes = [f"100 OAK {tok}" for tok in sorted(STRIP_STREET_TAIL)]
    all_vocab = sorted(
        GENERIC_STYLE_PLACEHOLDERS
        | STRUCTURAL_STYLE_CLASSES
        | HCAD_STYLE_GRADE_CODES
        | NON_ARCHITECTURAL_SURVEY_STYLES
        | {
            "Craftsman Bungalow",
            "Queen Anne",
            "Modern Ranch",
            "Colonial Revival",
            "Louisiana French Plantation style",
            "Miesian Steel & Rose-Brick International Style",
            "Modified Foursquare Frame",
            "YEAR          BLDG",
            "CIRCA      BLDG Georgian/Colonial Revival",
            "1940 Added quarters in back",
        }
    )

    helper_url = (REPO_ROOT / "app" / "js" / "hpoSurveyHelpers.js").as_uri()
    hpo_json_path = str(REPO_ROOT / "app" / "public" / "data" / "hpo_district_surveys.json")
    js_probe = "\n".join(
        [
            "import fs from 'node:fs';",
            f"import * as H from {json.dumps(helper_url)};",
            "const payload = JSON.parse(process.argv[1]);",
            f"const hpo = JSON.parse(fs.readFileSync({json.dumps(hpo_json_path)}, 'utf8'));",
            "const stripped = { ...hpo, district_aliases: undefined };",
            "const aliasOut = {};",
            "for (const k of payload.alias_keys) {",
            "  aliasOut[k] = H.resolveHpoDistrictDocs(stripped, k)?.districtName || null;",
            "}",
            "console.log(JSON.stringify({",
            "  lookup: payload.addr_vectors.map((v) => H.normalizeHpoLookupAddress(v)),",
            "  keys: payload.street_key_inputs.map((v) => H.normalizeHpoStreetAddressKeys(v)),",
            "  tail_norm: payload.tail_probes.map((v) => H.normalizeHpoLookupAddress(v)),",
            "  usable: payload.all_vocab.map((v) => H.isUsableHpoSurveyStyle(v)),",
            "  replaceable: payload.all_vocab.map((v) => H.isReplaceableStyleValue(v)),",
            "  structural: payload.all_vocab.map((v) => H.isStructuralClassStyleValue(v)),",
            "  district_aliases: hpo.district_aliases,",
            "  alias_resolved: aliasOut,",
            "}));",
        ]
    )
    proc = subprocess.run(
        [
            node_bin,
            "--input-type=module",
            "-e",
            js_probe,
            "--",
            json.dumps(
                {
                    "addr_vectors": addr_vectors,
                    "street_key_inputs": street_key_inputs,
                    "tail_probes": tail_probes,
                    "all_vocab": all_vocab,
                    "alias_keys": sorted(HPO_DISTRICT_NAME_ALIASES.keys()),
                }
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    js_res = json.loads(proc.stdout.strip())

    assert js_res["lookup"] == [normalize_hpo_lookup_address(v) for v in addr_vectors]
    assert js_res["keys"] == [normalize_hpo_street_address_keys(v) for v in street_key_inputs]
    assert js_res["tail_norm"] == [normalize_hpo_lookup_address(v) for v in tail_probes]
    assert js_res["usable"] == [is_usable_hpo_survey_style(v) for v in all_vocab]
    assert js_res["replaceable"] == [is_replaceable_style_value(v) for v in all_vocab]
    assert js_res["structural"] == [is_structural_class_style(v) for v in all_vocab]
    assert js_res["district_aliases"] == HPO_DISTRICT_NAME_ALIASES
    assert js_res["alias_resolved"] == HPO_DISTRICT_NAME_ALIASES

