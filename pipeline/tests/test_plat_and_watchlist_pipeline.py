"""Offline unit tests for fetch_plat_and_watchlist.py and cross-language JS/Python parity.

Covers UT-1..UT-5, UT-10, UT-13, UT-14, SCH-2, SCH-4/5, and SA-4 (stdlib-only Python 3.12 import).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

from atlas_pipeline import fetch_plat_and_watchlist as fpw

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "pipeline" / "atlas_pipeline" / "fetch_plat_and_watchlist.py"


def test_ut1_excel_serial_to_iso_and_node_parity() -> None:
    """Verify floored Excel 1900 serial conversion eliminates +1 day drift and matches JS."""
    vectors = [
        (46296.60417, "2026-10-01"),
        (46310.60417, "2026-10-15"),
        ("46310.6041666667", "2026-10-15"),
        (46324.60417, "2026-10-29"),
        (46310.0, "2026-10-15"),
        (46310.99999, "2026-10-15"),
        ("10/15/2026", "2026-10-15"),
        ("2026-10-29", "2026-10-29"),
        ("TBD", ""),
        ("", ""),
        (None, ""),
    ]
    for raw_val, expected in vectors:
        assert fpw.excel_serial_to_iso(raw_val) == expected

    node_code = """
    import { excelSerialToIso } from './app/js/platMonitorHelpers.js';
    const inputs = [46296.60417, 46310.60417, '46310.6041666667', 46324.60417, 46310.0, 46310.99999, '10/15/2026', '2026-10-29', 'TBD', '', null];
    console.log(JSON.stringify(inputs.map((v) => excelSerialToIso(v))));
    """
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", node_code],
        cwd=str(REPO_ROOT),
        check=True,
        capture_output=True,
        text=True,
    )
    js_results = json.loads(proc.stdout.strip())
    assert js_results == [expected for _, expected in vectors]


def test_ut2_multi_parcel_building_tier_aggregation() -> None:
    """Verify min(year_built >= 1836), override precedence, and multi-HCAD aggregation."""
    parsed = fpw.parse_hcad_account_list("152300000001; 0660450130001, 0660450130016")
    assert parsed["hcadIds"] == ["0152300000001", "0660450130001", "0660450130016"]

    res_multi_bld = fpw.resolve_multi_parcel_building_tier(
        [{"hcad": "0410170020150", "year_built": 0, "all_years": [0, 1928, 2005]}]
    )
    assert res_multi_bld["oldestYearBuilt"] == 1928
    assert res_multi_bld["suggestedTier"] == "1"

    res_1938_variance = fpw.resolve_multi_parcel_building_tier(
        [{"hcad": "0410170020151", "year_built": 0, "all_years": [0, 1938, 2005]}],
        flags=["variance"],
    )
    assert res_1938_variance["oldestYearBuilt"] == 1938
    assert res_1938_variance["suggestedTier"] == "1"

    res_amerimex = fpw.resolve_multi_parcel_building_tier(
        [
            {"hcad": "0660450130001", "year_built": 1966, "all_years": [1966]},
            {"hcad": "0660450130016", "year_built": 1964, "all_years": [1800, 1964, 1982]},
        ]
    )
    assert res_amerimex["oldestYearBuilt"] == 1964
    assert res_amerimex["suggestedTier"] == "2"

    res_override = fpw.resolve_multi_parcel_building_tier(
        [{"hcad": "0152300000001", "year_built": 1955, "all_years": [1955]}],
        curated_overrides={"0152300000001": {"year_built": 1894}},
    )
    assert res_override["oldestYearBuilt"] == 1894
    assert res_override["suggestedTier"] == "1"


def test_ut3_classify_item_exact_parity_with_js() -> None:
    """Verify exact screening parity between Python classify_item() and JS classifyPlatItem()."""
    base = {
        "name": "Greenfield Reserve Sec 1",
        "app": "2026-9900",
        "type": "Class 2 subdivision plat (C2)",
        "special": "",
        "section": "A. Consent",
        "city": "City",
        "prevSub": "",
        "superN": "ELDORADO / OATES PRAIRIE",
        "hd": "",
        "landUse": "Commercial",
        "lots": "1",
        "atlas": {"earliestYearBuilt": 0, "designations": [], "hasActivePermit": False},
    }
    cases = [
        base,
        {**base, "city": "ETJ"},
        {**base, "section": "D. Variances", "special": "Variance"},
        {
            **base,
            "section": "IV. Establish Public Hearing Date (Nov 12, 2026)",
            "type": "Class 3 Replat with Public Hearing Notice (C3N)",
        },
        {
            **base,
            "app": "DPV-26073533",
            "section": "K. Development Plat Variances",
            "type": "Development Plat Variance (DPV)",
        },
        {**base, "section": "F. Reconsideration of Requirements"},
        {**base, "atlas": {"earliestYearBuilt": 1964, "designations": [], "hasActivePermit": False}},
        {**base, "atlas": {"earliestYearBuilt": 0, "designations": [], "hasActivePermit": True}},
        {
            **base,
            "atlas": {
                "earliestYearBuilt": 0,
                "designations": ["City Landmark"],
                "hasActivePermit": False,
            },
        },
        {**base, "hd": "Old Sixth Ward Protected Historic District"},
        {**base, "prevSub": "Houston Heights", "superN": "GREATER HEIGHTS", "lots": "4"},
        {**base, "name": "First Baptist Chapel Reserve", "prevSub": ""},
    ]

    py_outputs = [fpw.classify_item(c) for c in cases]
    assert py_outputs[0]["group"] == "out"
    assert py_outputs[1]["group"] == "etj"
    for idx_in in (2, 3, 4, 5, 6, 7, 8, 10, 11):
        assert py_outputs[idx_in]["group"] == "check"
    assert py_outputs[9]["group"] == "designated"

    node_code = """
    import fs from 'node:fs';
    import { classifyPlatItem } from './app/js/platMonitorHelpers.js';
    const cases = JSON.parse(process.argv[1]);
    console.log(JSON.stringify(cases.map((c) => classifyPlatItem(c))));
    """
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", node_code, json.dumps(cases)],
        cwd=str(REPO_ROOT),
        check=True,
        capture_output=True,
        text=True,
    )
    js_outputs = json.loads(proc.stdout.strip())
    for idx_c, (py_res, js_res) in enumerate(zip(py_outputs, js_outputs)):
        assert py_res["group"] == js_res["group"], f"Group mismatch on case {idx_c}"
        assert sorted(py_res["flags"]) == sorted(js_res["flags"]), f"Flags mismatch on case {idx_c}"


def test_ut4_outline_hearing_extraction_and_notice_classification_without_dpv_pollution() -> None:
    """Verify Section IV hearing extraction and ensure C3N Public Hearing Notices with 'development plat' boilerplate are not misclassified as DPVs or cross-attached to unrelated prior-cycle apps."""
    outline_sample = """
    PLANNING COMMISSION AGENDA OUTLINE
    Thursday, October 29, 2026
    I. Call to Order
    IV. Establish a public hearing date of November 12, 2026
    a. Denton Street Villas
    b. Montrose Residences
    c. Wheeler Residences
    K. Development Plats with Variance Requests
    101 1501 Wichman Street  DPV
    """
    parsed = fpw.parse_agenda_outline_text(outline_sample)
    assert parsed["sec4_hearing_date_iso"] == "2026-11-12"
    assert "Montrose Residences" in parsed["sec4_hearing_names"]
    assert any(d["name"] == "1501 Wichman Street" for d in parsed["dpv_items"])

    # Simulate fetch_multi_cycle_agendas_and_notices notice parsing when PDF boilerplate mentions
    # "subdivision or development plat" on a 2026-1585_Montrose-Residences_PH.pdf C3N notice.
    sample_html = (
        '<a href="2026/2026-1585_Montrose-Residences_PH.pdf">Montrose</a>'
        '<a href="2026/DPV-26075937-1501-Wichman-Street.pdf">Wichman</a>'
    ).encode("utf-8")
    montrose_pdf_text = (
        "CITY OF HOUSTON PLANNING COMMISSION PUBLIC HEARING NOTICE\n"
        "Pursuant to Chapter 42 governing any subdivision or development plat:\n"
        "REFERENCE NUMBER : 2026-1585\n"
        "PROJECT NAME : Montrose Residences C3N\n"
        "property located at north of West Clay Street, west of Waugh Drive.\n"
        "The applicant is requesting a replat into six single-family residential lots.\n"
        "Thursday, November 12, 2026 at 2:30 p.m.\n"
    )
    wichman_pdf_text = (
        "DEVELOPMENT PLAT VARIANCE NOTICE\n"
        "REFERENCE NUMBER : 26075937\n"
        "PROJECT NAME : 1501 Wichman Street DPV\n"
        "property located at 1501 Wichman Street.\n"
        "The applicant is requesting a development plat variance for a 5-foot garage building line.\n"
        "Thursday, October 29, 2026 at 2:30 p.m.\n"
    )

    def fake_fetch_bytes(url: str, timeout: int = 20, data=None, headers=None) -> bytes:
        if "DevelopRegs" in url:
            return b'<a href="docs_pdfs/Plat_report/2026/Plat-Current-Agenda-Spreadsheet-10-01-2026.xlsx">1</a><a href="docs_pdfs/Plat_report/2026/Plat-Current-Agenda-Spreadsheet-10-29-2026.xlsx">2</a>'
        if "Commissions" in url and url.endswith(".pdf"):
            return b"FAKE_OUTLINE_PDF" if "102926" in url else b""
        if "Commissions" in url:
            return b""
        if url == fpw.NOTICES_URL:
            return sample_html
        if "Montrose-Residences_PH.pdf" in url:
            return b"PDF_MONTROSE"
        if "DPV-26075937" in url:
            return b"PDF_WICHMAN"
        return b""

    def fake_pdf_to_text(pdf_bytes: bytes, layout: bool = True) -> str:
        if pdf_bytes == b"FAKE_OUTLINE_PDF":
            return outline_sample
        if pdf_bytes == b"PDF_MONTROSE":
            return montrose_pdf_text
        if pdf_bytes == b"PDF_WICHMAN":
            return wichman_pdf_text
        return ""

    with (
        mock.patch.object(fpw, "fetch_bytes", side_effect=fake_fetch_bytes),
        mock.patch.object(fpw, "pdf_to_text", side_effect=fake_pdf_to_text),
        mock.patch.object(
            fpw,
            "parse_xlsx_bytes",
            side_effect=[
                # 2026-10-01 spreadsheet has an unrelated item reusing app 2026-1585 ("Montrose Vista Replat No 1")
                [
                    {"A": "Plat Name", "B": "App No"},
                    {
                        "A": "Montrose Vista Replat No 1",
                        "B": "2026-1585",
                        "C": "46296.60417",
                        "D": "46286",
                        "E": "Class 3 subdivision replat Public Hearing Notification (C3N)",
                        "G": "D. Variances",
                        "I": "City",
                        "J": "Harris",
                        "AE": "https://www.houstontx.gov/planning/DevelopRegs/docs_pdfs/Plat_report/2026/MONTROSE_VISTA.pdf",
                    },
                ],
                # 2026-10-29 spreadsheet has regular items, while Montrose Residences comes from Section IV + Notice PDF
                [
                    {"A": "Plat Name", "B": "App No"},
                    {
                        "A": "Amerimex Motors",
                        "B": "2026-1659",
                        "C": "46324.60417",
                        "D": "46314",
                        "E": "Class 2 subdivision replat (C2R)",
                        "F": "Variance",
                        "G": "D. Variances",
                        "I": "City",
                        "J": "Harris",
                        "Z": "0660450130001; 0660450130016",
                    },
                ],
            ],
        ),
    ):
        scraped = fpw.fetch_multi_cycle_agendas_and_notices(max_cycles=2)
        montrose_notice = next(n for n in scraped["all_notices"] if n["name"] == "Montrose Residences")
        wichman_notice = next(n for n in scraped["all_notices"] if n["name"] == "1501 Wichman Street")
        assert montrose_notice["isDpv"] is False
        assert wichman_notice["isDpv"] is True

        empty_idx = {
            "hcad_map": {
                "0231440000010": {
                    "hcad": "0231440000010",
                    "address": "1226 W CLAY ST",
                    "addresses": ["1226 W CLAY ST"],
                    "lng": -95.39412,
                    "lat": 29.75498,
                    "year_built": 1930,
                    "all_years": [1930],
                    "label": "",
                }
            },
            "curated_overrides": {},
            "addr_exact": {},
            "addr_nodir": {},
            "street_centroids": {},
            "buildings_by_hcad": {},
            "subdiv_index": {},
            "snbr_index": {},
            "landmarks_by_hcad": {},
            "good_brick_by_hcad": {},
            "smls_by_subdiv": {},
            "active_alerts_by_hcad": {},
        }
        ds = fpw.build_plat_agenda_dataset(empty_idx, scraped)

        oct01_vista = ds["cycles"]["2026-10-01"]["items"][0]
        assert oct01_vista["name"] == "Montrose Vista Replat No 1"
        assert oct01_vista["noticePdf"] == "", "Montrose Residences Nov 12 notice must NOT attach to Montrose Vista Replat No 1"
        assert oct01_vista["hcad"] != "0231440000010", "Montrose Residences HCAD override must NOT pollute Montrose Vista Replat No 1"

        oct29_items = ds["cycles"]["2026-10-29"]["items"]
        montrose_item = next(i for i in oct29_items if i["name"] == "Montrose Residences")
        assert montrose_item["app"] == "2026-1585"
        assert montrose_item["pdf"] == ""
        assert montrose_item["noticePdf"].endswith("2026-1585_Montrose-Residences_PH.pdf")
        assert montrose_item["hearingDateIso"] == "2026-11-12"
        assert "Establish Public Hearing Date" in montrose_item["section"]
        assert montrose_item["hcad"] == "0231440000010"
        assert montrose_item["priorCycles"] == [], (
            "Colliding 2026-1585 Montrose Vista Replat No 1 must not link into Montrose Residences priorCycles"
        )

        denton_item = next(i for i in oct29_items if i["name"] == "Denton Street Villas")
        assert denton_item["hearingDateIso"] == "2026-11-12"
        assert denton_item["pdf"] == ""
        assert denton_item["noticePdf"] == ""
        assert "Establish Public Hearing Date" in denton_item["section"]


def test_ut5_simplify_search_address_parity_with_js() -> None:
    """Verify exact Python/JS parity on Houston street address simplification."""
    cases = [
        ("3827 Broadway St", "3827 Broadway"),
        ("2000 Smith St.", "2000 Smith"),
        ("530 Pierce St.", "530 Pierce"),
        ("910 Louisiana St Ste 400", "910 Louisiana"),
        ("909 TEXAS AVE 308", "909 TEXAS"),
        ("1 MAIN ST 562", "1 MAIN"),
        ("2100 St Emanuel St", "2100 St Emanuel"),
        ("1400 St Joseph Pkwy", "1400 St Joseph"),
        ("1200 Ave A", "1200 Ave A"),
        ("6500 Avenue J", "6500 Avenue J"),
        ("400 Park Way", "400 Park"),
    ]
    for raw_addr, expected in cases:
        assert fpw.simplify_search_address(raw_addr) == expected

    node_code = """
    import { simplifySearchAddress } from './app/js/watchlistSyncHelpers.js';
    const inputs = JSON.parse(process.argv[1]);
    console.log(JSON.stringify(inputs.map((v) => simplifySearchAddress(v))));
    """
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", node_code, json.dumps([c[0] for c in cases])],
        cwd=str(REPO_ROOT),
        check=True,
        capture_output=True,
        text=True,
    )
    js_results = json.loads(proc.stdout.strip())
    assert js_results == [c[1] for c in cases]


def test_ut10_soql_escaping_county_scoping_and_street_token_boundaries() -> None:
    """Verify SoQL single-quote escaping, Harris County filters, and house-number token boundaries."""
    assert fpw.escape_soql_literal("O'Connor Properties LLC") == "O''Connor Properties LLC"
    assert fpw.escape_soql_literal("A & B Retail") == "A & B Retail"
    assert any(v.lower() == "braketime" for v in fpw.expand_brand_variants(["Brake Time"]))

    # House-number and street-name contiguous token boundaries: '500 Gray' matches '500 GRAY ST'
    # and 'STE 100, 500 GRAY ST', NOT '1500 GRAY ST', '500 GRAYSTONE DR', or distinct '500 W GRAY ST'.
    assert fpw.matches_street_token_boundary("500 GRAY ST", "500 Gray") is True
    assert fpw.matches_street_token_boundary("STE 100, 500 GRAY ST", "500 Gray") is True
    assert fpw.matches_street_token_boundary("1500 GRAY ST", "500 Gray") is False
    assert fpw.matches_street_token_boundary("500 GRAYSTONE DR", "500 Gray") is False
    assert fpw.matches_street_token_boundary("500 W GRAY ST", "500 Gray") is False

    tabs_params = fpw.build_tdlr_tabs_search_params(
        address="530 Pierce St.",
        county_code="2101",
    )
    assert tabs_params["LocationCounty"] == "2101"
    assert tabs_params["LocationAddress"] == "530 Pierce"
    assert "FacilityName" not in tabs_params
    assert "OwnerName" not in tabs_params

    comp_sales_where = fpw.build_comptroller_sales_soql_where(
        addresses=["2000 Smith St."],
        entities=["O'Connor Properties LLC"],
        dbas=["A & B Retail"],
    )
    assert "HARRIS" in comp_sales_where.upper() or "101" in comp_sales_where
    assert "O''CONNOR" in comp_sales_where.upper()

    tabc_where = fpw.build_tabc_soql_where(
        addresses=["2000 Smith St."],
        entities=["Smith St C Store LLC"],
        dbas=["Brake Time"],
    )
    assert "HARRIS" in tabc_where.upper()


def test_ut13_offline_eight_agency_normalizers_and_cloud_watchlist_merge() -> None:
    """Verify all 8 agency normalizers, out-of-county exclusion, 8-digit ILMS harvesting, and non-destructive cloud Watchlist merge."""
    # #4 Comptroller (Harris County match kept; Angelina County / Lufkin Dollar General rejected)
    comp = fpw.normalize_comptroller_records(
        sales_rows=[
            {
                "taxpayer_number": "32102472084",
                "taxpayer_name": "SMITH ST C STORE LLC",
                "outlet_name": "BRAKE TIME",
                "outlet_address": "2000 SMITH ST",
                "outlet_city": "HOUSTON",
                "outlet_county_code": "101",
                "permit_issue_date": "2025-10-15T00:00:00.000",
            },
            {
                "taxpayer_number": "19999999999",
                "taxpayer_name": "DOLLAR GENERAL LUFKIN LLC",
                "outlet_name": "DOLLAR GENERAL",
                "outlet_address": "1500 GRAY ST",
                "outlet_city": "LUFKIN",
                "outlet_county_code": "003",
            },
        ],
        franchise_rows=[],
        prop_addresses=["2000 Smith St.", "500 Gray St."],
        prop_entities=["Smith St C Store LLC"],
        prop_dbas=["Brake Time"],
        sweep_date_iso="2026-10-27",
    )
    assert len(comp) == 1
    assert "SMITH ST C STORE LLC" in comp[0]["result"]
    assert all("LUFKIN" not in f["result"].upper() for f in comp)

    # #5 HCAD ArcGIS
    hcad_hits = fpw.normalize_hcad_arcgis_records(
        [
            {
                "attributes": {
                    "HCAD_NUM": "0152300000001",
                    "owner": "OB TEXAS PROPERTIES LLC",
                    "address": "2000 SMITH ST",
                    "appr_val": 4850000,
                }
            }
        ],
        sweep_date_iso="2026-10-27",
    )
    assert len(hcad_hits) == 1
    assert "OB TEXAS PROPERTIES LLC" in hcad_hits[0]["result"]

    # #6 TABC
    tabc_hits = fpw.normalize_tabc_records(
        pending_rows=[],
        active_rows=[
            {
                "license_id": "BG-2000111",
                "license_type": "BG",
                "trade_name": "BRAKE TIME",
                "owner": "SMITH ST C STORE LLC",
                "address": "2000 SMITH ST",
                "city": "HOUSTON",
                "county": "Harris",
                "original_issue_date": "2026-04-01T00:00:00",
            }
        ],
        prop_addresses=["2000 Smith St."],
        prop_entities=["Smith St C Store LLC"],
        prop_dbas=["Brake Time"],
        sweep_date_iso="2026-10-27",
    )
    assert len(tabc_hits) == 1
    assert "BRAKE TIME" in tabc_hits[0]["result"].upper()

    # #9 COH Plats
    plat_hits = fpw.normalize_plat_matches(
        plat_items=[
            {
                "app": "2026-1200",
                "name": "Smith Street Plaza",
                "type": "Class 2 subdivision replat (C2R)",
                "section": "A. Consent",
                "pcDateIso": "2026-10-15",
                "hcad": "0152300000001",
                "pdf": "https://www.houstontx.gov/planning/DevelopRegs/docs_pdfs/Plat_report/2026/SMITH_ST.pdf",
            }
        ],
        prop_hcad_ids=["0152300000001"],
        prop_addresses=["2000 Smith St."],
        prop_subdiv_keywords=["Smith Street Plaza"],
        sweep_date_iso="2026-10-27",
    )
    assert len(plat_hits) == 1
    assert "Smith Street Plaza" in plat_hits[0]["result"]

    # #10 TDLR TABS (form-urlencoded DataTables params + Harris County 2101 kept; out-of-county rejected)
    tabs_params = fpw.build_tdlr_tabs_search_params(address="530 Pierce St.", county_code="2101")
    assert tabs_params["LocationAddress"] == "530 Pierce"
    assert tabs_params["LocationCounty"] == "2101"
    assert "ProjectNumber" not in tabs_params
    assert "FacilityName" not in tabs_params
    tabs_hits = fpw.normalize_tdlr_tabs_records(
        tabs_rows=[
            {
                "ProjectNumber": "TABS2026022839",
                "ProjectName": "Braketime Smith Road",
                "FacilityName": "Braketime",
                "LocationAddress": "530 Pierce Street Houston, TX 77002",
                "County": 2101,
                "EstimatedCost": 2000000.0,
                "TypeOfWork": 9001,
                "ScopeOfWork": "New 13,254 S.F. one story building",
                "ProjectCreatedOn": "2026-06-15T10:59:58.863",
                "OwnerName": "Hi Tech Auto Reality, LLC",
                "DesignFirm": "Heights Engineering",
            },
            {
                "project_number": "TABS2026999999",
                "project_name": "Lufkin Dollar General",
                "facility_name": "Dollar General",
                "location_address": "530 PIERCE ST",
                "location_city": "LUFKIN",
                "location_county": "Angelina",
                "location_county_code": "003",
            },
        ],
        prop_addresses=["2000 Smith St.", "530 Pierce St."],
        prop_entities=["Hi Tech Auto Realty LLC"],
        prop_dbas=["Brake Time"],
        sweep_date_iso="2026-10-27",
    )
    assert len(tabs_hits) == 1
    assert tabs_hits[0]["date"] == "2026-06-15"
    assert "TABS2026022839" in tabs_hits[0]["result"]
    assert "New Construction" in tabs_hits[0]["result"]
    assert "Heights Engineering" in tabs_hits[0]["result"]
    assert tabs_hits[0]["links"][0]["url"] == "https://www.tdlr.texas.gov/TABS/Search/Project/TABS2026022839"

    # #13 Houston 311
    h311_hits = fpw.normalize_houston_311_records(
        features=[
            {
                "attributes": {
                    "CASE_NUMBER": "2600311999",
                    "CASE_TYPE": "Nuisance On Property",
                    "INCIDENT_ADDRESS": "2000 SMITH ST",
                    "STATUS": "Open",
                    "CREATED_DATE": "2026-10-21",
                }
            }
        ],
        prop_hcad_ids=["0152300000001"],
        prop_addresses=["2000 Smith St."],
        sweep_date_iso="2026-10-27",
    )
    assert len(h311_hits) == 1
    assert "2600311999" in h311_hits[0]["result"]

    # #14 COH Sold Permits + automatic 8-digit project number harvesting
    permits_res = fpw.normalize_coh_sold_permits(
        permit_rows=[
            {
                "project_no": "26090093",
                "use_desc": "BLD DEMO/SEWER DISC",
                "address": "2000 SMITH ST",
                "issue_date": "2026-10-14",
                "owner_occupant": "KIRBY MANSION",
            }
        ],
        prop_addresses=["2000 Smith St."],
        sweep_date_iso="2026-10-27",
    )
    permit_findings = permits_res["findings"]
    assert len(permit_findings) == 1
    assert "26090093" in permit_findings[0]["result"]
    assert "26090093" in permits_res["harvestedProjectNumbers"]

    # #15 DSHS Asbestos
    dshs_hits = fpw.normalize_dshs_asbestos_records(
        dshs_rows=[
            {
                "notification_number": "2026005825",
                "facility_name": "Kirby Mansion",
                "address": "2000 SMITH ST",
                "city": "HOUSTON",
                "start_date": "2026-10-27",
                "end_date": "2026-11-06",
                "lic_id": "2451374",
            }
        ],
        prop_addresses=["2000 Smith St."],
        prop_entities=["OB Texas Properties LLC"],
        sweep_date_iso="2026-10-27",
    )
    assert len(dshs_hits) == 1
    assert "2026005825" in dshs_hits[0]["result"]

    # Verify build_property_watchlist preserves seeded Kirby Mansion searchTerms when merging a legacy
    # cloud sync row that predates the searchTerms field, and sets tierConfirmed=True on staff cloud rows.
    import csv as _csv
    import io as _io

    buf = _io.StringIO()
    writer = _csv.writer(buf)
    writer.writerow(["timestamp", "hcad_num", "source_type", "source_citation", "verified_by", "status"])
    writer.writerow([
        "2026-10-23T18:00:00Z",
        "WATCH_PROP:prop-kirby-mansion",
        "PROPERTY_WATCHLIST_UPDATE",
        json.dumps({
            "id": "prop-kirby-mansion",
            "propertyName": "John Henry Kirby Mansion",
            "address": "2000 Smith St.",
            "notes": "Updated notes from cloud",
            "updatedAt": "2026-10-23T18:00:00Z",
        }),
        "Emily Ardoin",
        "approved",
    ])
    writer.writerow([
        "2026-10-24T12:00:00Z",
        "WATCH_PROP:prop-1792765948557",
        "PROPERTY_WATCHLIST_UPDATE",
        json.dumps({
            "id": "prop-1792765948557",
            "propertyName": "Synthetic Historic Building",
            "address": "1234 Hadley St",
            "tier": "1",
            "updatedAt": "2026-10-24T12:00:00Z",
        }),
        "Emily Ardoin",
        "approved",
    ])
    writer.writerow([
        "2026-10-22T09:00:00Z",
        "WATCH_PROP:plat-2026-9999",
        "PROPERTY_WATCHLIST_UPDATE",
        json.dumps({
            "id": "plat-2026-9999",
            "propertyName": "Unconfirmed Auto Dump",
            "tier": "2",
        }),
        "Bot",
        "approved",
    ])
    cloud_sync = fpw.pull_cloud_sheet_sync_rows(buf.getvalue())
    built_wl = fpw.build_property_watchlist(
        plat_data={"cycleKey": "2026-10-29", "cycles": {"2026-10-29": {"items": [], "reviews": {}}}},
        idx={},
        cloud_sync=cloud_sync,
        run_live_sweeps=False,
        sweep_timestamp_iso="2026-10-27T12:00:00Z",
    )
    ids = [p["id"] for p in built_wl["watchedProperties"]]
    assert "prop-kirby-mansion" in ids
    assert "prop-1792765948557" in ids
    assert "plat-2026-9999" not in ids, "Unconfirmed plat-* without human contributions must not resurrect"
    for p in built_wl["watchedProperties"]:
        assert p.get("tierConfirmed") is True

    kirby_out = next(p for p in built_wl["watchedProperties"] if p["id"] == "prop-kirby-mansion")
    assert "Smith St C Store LLC" in kirby_out["searchTerms"]["entities"]
    assert "Brake Time" in kirby_out["searchTerms"]["dbas"]
    assert kirby_out["notes"] == "Updated notes from cloud"
    assert kirby_out["lastAutoSweepAt"] == "2026-10-27T12:00:00Z"


def test_ut15_post_review_auto_sweep_escalation_chain_dba_scoping_tombstone_and_js_parity() -> None:
    """Verify backdated adverse auto-sweep escalation, off-site chain DBA exclusion, Tier 3 tombstones, and Python/JS hasHumanDossierContributions parity."""
    # 1. Off-site suburban Brake Time chain stores owned by unrelated LLCs must NOT match when site addresses/entities exist
    comp_chain = fpw.normalize_comptroller_records(
        sales_rows=[
            {
                "taxpayer_number": "32102472084",
                "taxpayer_name": "SMITH ST C STORE LLC",
                "outlet_name": "BRAKE TIME",
                "outlet_address": "2000 SMITH ST",
                "outlet_county_code": "101",
            },
            {
                "taxpayer_number": "32088888888",
                "taxpayer_name": "KATY SUBURBAN FUEL LLC",
                "outlet_name": "BRAKE TIME #14",
                "outlet_address": "14811 KATY FWY",
                "outlet_county_code": "101",
            },
        ],
        franchise_rows=[],
        prop_addresses=["2000 Smith St.", "530 Pierce St."],
        prop_entities=["Smith St C Store LLC"],
        prop_dbas=["Brake Time"],
        sweep_date_iso="2026-10-27",
    )
    assert len(comp_chain) == 1
    assert "2000 SMITH ST" in comp_chain[0]["result"]

    tabc_chain = fpw.normalize_tabc_records(
        pending_rows=[],
        active_rows=[
            {
                "license_id": "200183169",
                "license_type": "BQ",
                "owner": "SMITH ST C STORE LLC",
                "trade_name": "BRAKE TIME",
                "address": "2000 SMITH ST",
                "county": "HARRIS",
            },
            {
                "license_id": "200999999",
                "license_type": "BQ",
                "owner": "SUBURBAN HIGHWAY STORE LLC",
                "trade_name": "BRAKE TIME",
                "address": "6102 HIGHWAY 6 N",
                "county": "HARRIS",
            },
        ],
        prop_addresses=["2000 Smith St."],
        prop_entities=["Smith St C Store LLC"],
        prop_dbas=["Brake Time"],
        sweep_date_iso="2026-10-27",
    )
    assert len(tabc_chain) == 1
    assert "2000 SMITH ST" in tabc_chain[0]["result"]

    # 2. merge_auto_sweep_findings_into_property escalates backdated adverse hits to Flagged without pre-stamping lastChecked
    plat_prop = fpw._build_confirmed_plat_watch_property(
        it={
            "app": "DPV-26073533",
            "name": "801 McGowen Street",
            "type": "Development Plat Variance",
            "section": "K. Development Plat Variances",
            "superN": "MIDTOWN",
            "prevSub": "HADLEY",
            "pdf": "https://www.houstontx.gov/dpv.pdf",
            "atlas": {
                "primaryAddress": "801 MCGOWEN ST",
                "addresses": ["801 MCGOWEN ST"],
                "earliestYearBuilt": 1938,
            },
        },
        rev={"tier": "1", "tierConfirmed": True, "why": "Historic brick commercial building"},
        cycle_iso="2026-10-29",
        sweep_date="2026-10-27",
    )
    assert plat_prop["appNo"] == "DPV-26073533"
    chk11 = next(r for r in plat_prop["checklist"] if r["id"] == "chk-11")
    assert chk11["additionalResourceLabel"] == "", "Non-Kirby properties must not inherit Midtown TIRZ link"

    fpw.merge_auto_sweep_findings_into_property(
        plat_prop,
        {
            "findingsByCheck": {
                "chk-10": [
                    {
                        "date": "2026-08-21",
                        "source": "auto-sweep",
                        "adverse": True,
                        "agencyRecordId": "tabs:TABS2026028478",
                        "result": "[AUTO-SWEEP] TDLR TABS #TABS2026028478: $1,000,000 C Store With Gas Station",
                        "links": [{"label": "TDLR", "url": "https://www.tdlr.texas.gov/TABS/Search/Project/TABS2026028478"}],
                    }
                ],
                "chk-04": [
                    {
                        "date": "2026-05-01",
                        "source": "auto-sweep",
                        "adverse": False,
                        "agencyRecordId": "comptroller-sales:321:1",
                        "result": "[AUTO-SWEEP] TX Comptroller Active Sales Tax Permit #321",
                    }
                ],
            },
            "harvestedProjectNumbers": ["26052089"],
        },
        "2026-10-27T12:00:00Z",
    )
    chk10 = next(r for r in plat_prop["checklist"] if r["id"] == "chk-10")
    chk04 = next(r for r in plat_prop["checklist"] if r["id"] == "chk-04")
    assert chk10["status"] == "Flagged", "Backdated adverse TDLR TABS hit must escalate To Do -> Flagged"
    assert chk10["lastChecked"] == "", "Machine auto-sweep must not overwrite human lastChecked"
    assert chk10["checks"][0]["discoveredAt"] == "2026-10-27"
    assert chk04["status"] == "In Progress", "Non-adverse auto-sweep hit must move To Do -> In Progress"

    # Untouched pipeline-promoted plat-* dossier (even with auto-sweeps, harvested projectNumbers, and prevSub legalDescription) has zero human contributions in BOTH Python and JS!
    assert fpw.has_human_dossier_contributions(plat_prop) is False

    human_edited_prop = json.loads(json.dumps(plat_prop))
    human_chk10 = next(r for r in human_edited_prop["checklist"] if r["id"] == "chk-10")
    human_chk10["status"] = "Complete"
    human_chk10["lastChecked"] = "2026-10-27"
    human_chk10["humanStatusEdited"] = True
    assert fpw.has_human_dossier_contributions(human_edited_prop) is True

    # Same-day re-sweep must preserve manual Complete status on human_edited_prop
    fpw.merge_auto_sweep_findings_into_property(
        human_edited_prop,
        {"findingsByCheck": {"chk-10": []}, "harvestedProjectNumbers": []},
        "2026-10-27T18:00:00Z",
    )
    assert next(r for r in human_edited_prop["checklist"] if r["id"] == "chk-10")["status"] == "Complete"

    # Python <-> Node JS parity check on hasHumanDossierContributions
    node_script = (
        "import { hasHumanDossierContributions } from './app/js/platMonitorHelpers.js';"
        "import { normalizeWatchProperty } from './app/js/watchlistSyncHelpers.js';"
        "import fs from 'node:fs';"
        "const cases = JSON.parse(fs.readFileSync(0, 'utf8'));"
        "console.log(JSON.stringify(cases.map(c => hasHumanDossierContributions(normalizeWatchProperty(c)))));"
    )
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", node_script],
        input=json.dumps([plat_prop, human_edited_prop]),
        cwd=str(REPO_ROOT),
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(proc.stdout.strip()) == [False, True]

    # 3. Removed (Tier 3) cloud tombstone suppresses confirmed plat-* entry in build_property_watchlist
    tombstone_wl = fpw.build_property_watchlist(
        plat_data={
            "cycleKey": "2026-10-29",
            "cycles": {
                "2026-10-29": {
                    "items": [{"app": "DPV-26073533", "name": "801 McGowen Street", "atlas": {}}],
                    "reviews": {"DPV-26073533": {"tier": "1", "tierConfirmed": True}},
                }
            },
        },
        idx={},
        cloud_sync={
            "watchProperties": {
                "plat-DPV-26073533": {
                    "id": "plat-DPV-26073533",
                    "appNo": "DPV-26073533",
                    "tier": "3",
                    "trackerStatus": "Removed (Tier 3)",
                    "updatedAt": "2026-10-27T16:00:00Z",
                }
            }
        },
        run_live_sweeps=False,
        sweep_timestamp_iso="2026-10-27T17:00:00Z",
    )
    assert all(p["id"] != "plat-DPV-26073533" for p in tombstone_wl["watchedProperties"])


def test_ut14_and_sa4_ci_test_discovery_and_bare_stdlib_import() -> None:
    """Verify pyproject.toml & deploy-pages.yml run both test suites and fetch_plat_and_watchlist.py uses Python 3.12 stdlib only."""
    pyproject_txt = (REPO_ROOT / "pipeline" / "pyproject.toml").read_text(encoding="utf-8")
    assert 'testpaths = ["tests"' in pyproject_txt

    deploy_yml = (REPO_ROOT / ".github" / "workflows" / "deploy-pages.yml").read_text(encoding="utf-8")
    assert "node tests/unit/run_all.mjs" in deploy_yml
    assert "pytest tests" in deploy_yml

    # SA-4: python3 -S disables site-packages completely, proving stdlib-only runtime imports
    code = (
        "import importlib.util; "
        f"spec = importlib.util.spec_from_file_location('fpw', {str(SCRIPT_PATH)!r}); "
        "mod = importlib.util.module_from_spec(spec); "
        "spec.loader.exec_module(mod); "
        "assert hasattr(mod, 'build_property_watchlist')"
    )
    subprocess.run([sys.executable, "-S", "-c", code], check=True, cwd=str(REPO_ROOT))


def test_rp7_graceful_per_agency_timeout_degradation_and_alert_scripts(monkeypatch) -> None:
    """Verify per-agency timeout isolation in sweep_watched_property_agencies and stdlib cleanliness of daily alert scripts."""
    calls: list[str] = []

    def fake_fetch_bytes(
        url: str,
        timeout: int = 12,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> bytes | None:
        calls.append(url)
        # Simulate slow/unreachable timeout on TDLR TABS and Houston 311 while Comptroller & HCAD succeed
        if "tdlr.texas.gov" in url or "HOUSTON311" in url:
            return None
        if "jrea-zgmq.json" in url:
            return json.dumps([
                {
                    "taxpayer_number": "32099112233",
                    "taxpayer_name": "SMITH ST C STORE LLC",
                    "outlet_number": "00001",
                    "outlet_name": "BRAKE TIME",
                    "outlet_address": "2000 SMITH ST",
                    "outlet_city": "HOUSTON",
                    "outlet_county_code": "101",
                    "outlet_permit_issue_date": "20260315",
                }
            ]).encode("utf-8")
        if "arcweb.hcad.org" in url:
            return json.dumps({
                "features": [
                    {
                        "attributes": {
                            "HCAD_NUM": "0152300000001",
                            "owner": "OB TEXAS PROPERTIES LLC",
                            "address": "2000 SMITH ST",
                            "state_class": "F1",
                            "mkt_val": 4850000,
                        }
                    }
                ]
            }).encode("utf-8")
        return b"[]"

    monkeypatch.setattr(fpw, "fetch_bytes", fake_fetch_bytes)

    prop = {
        "id": "prop-kirby-mansion",
        "address": "2000 Smith St.",
        "secondaryAddresses": ["530 Pierce St."],
        "hcadAccount": "0152300000001",
        "searchTerms": {
            "entities": ["Smith St C Store LLC"],
            "dbas": ["Brake Time"],
            "altAddresses": ["530 Pierce St."],
        },
    }
    res = fpw.sweep_watched_property_agencies(
        prop=prop,
        plat_data={"cycles": {}},
        idx={},
        sweep_date_iso="2026-10-27",
    )
    findings_map = res.get("findingsByCheck") or res
    assert len(findings_map["chk-04"]) >= 1
    assert len(findings_map["chk-05"]) == 1
    assert findings_map["chk-10"] == []
    assert findings_map["chk-13"] == []
    assert any("tdlr.texas.gov" in u for u in calls)

    # Verify daily workflow sibling scripts (fetch_live_permits.py, fetch_hahc_tracker.py) compile cleanly under bare Python (-S)
    for rel_script in (
        "pipeline/atlas_pipeline/fetch_live_permits.py",
        "pipeline/atlas_pipeline/fetch_hahc_tracker.py",
    ):
        target = REPO_ROOT / rel_script
        assert target.exists(), f"Missing daily alert script {rel_script}"
        compile_cmd = f"import py_compile; py_compile.compile({str(target)!r}, doraise=True)"
        subprocess.run([sys.executable, "-S", "-c", compile_cmd], check=True, cwd=str(REPO_ROOT))


def test_annual_demolition_benchmarks_schema_and_js_parity() -> None:
    """Verify annual_benchmarks in fetch_live_permits.py, active_permit_alerts.json, and JS parity."""
    from atlas_pipeline import fetch_live_permits as flp

    assert hasattr(flp, "DEFAULT_ANNUAL_DEMOLITION_BENCHMARKS")
    assert hasattr(flp, "build_annual_demolition_benchmarks")

    default_bench = flp.DEFAULT_ANNUAL_DEMOLITION_BENCHMARKS
    built_bench = flp.build_annual_demolition_benchmarks(None)
    assert built_bench == default_bench
    assert built_bench is not default_bench

    # Partial existing benchmarks with sparse legacy row must preserve custom fields and backfill missing/None fields from DEFAULT_ANNUAL_DEMOLITION_BENCHMARKS
    custom_coh_row = {
        "period": "2026 YTD",
        "total_permits": 1999,
        "net_main_bldg_teardowns": None,
    }
    partial_built = flp.build_annual_demolition_benchmarks({
        "updated_through": "2026-11-01",
        "coh_structural_demolitions": [custom_coh_row],
        "hcad_countywide_demolitions": [],
    })
    assert partial_built["updated_through"] == "2026-11-01"
    assert len(partial_built["coh_structural_demolitions"]) == 1
    assert partial_built["coh_structural_demolitions"][0]["total_permits"] == 1999
    assert partial_built["coh_structural_demolitions"][0]["net_main_bldg_teardowns"] == 1634
    assert partial_built["coh_structural_demolitions"][0]["pool_spa_only"] == 1
    assert partial_built["coh_structural_demolitions"][0]["excluded_type13_bldg_demo_permits"] == 289
    assert len(partial_built["hcad_countywide_demolitions"]) == 6
    assert len(partial_built["hahc_historic_district_demolitions"]) == 3

    alerts_path = REPO_ROOT / "app" / "public" / "data" / "active_permit_alerts.json"
    alerts_raw = alerts_path.read_text(encoding="utf-8")
    assert "\u2014" not in alerts_raw, "active_permit_alerts.json must not contain U+2014 em-dashes"
    alerts_data = json.loads(alerts_raw)

    bench = alerts_data.get("annual_benchmarks")
    assert isinstance(bench, dict), "active_permit_alerts.json must include annual_benchmarks"

    coh = bench.get("coh_structural_demolitions")
    assert isinstance(coh, list) and len(coh) == 3
    by_period = {row["period"]: row for row in coh}
    assert by_period["2026 YTD"]["total_permits"] == 1755
    assert by_period["2026 YTD"]["unique_addresses"] == 1676
    assert by_period["2026 YTD"]["net_main_bldg_teardowns"] == 1634
    assert by_period["2026 YTD"]["pool_spa_only"] == 1
    assert by_period["2026 YTD"]["canopy_porch_partial"] == 2
    assert by_period["2026 YTD"]["excluded_type13_bldg_demo_permits"] == 289
    assert by_period["2025"]["total_permits"] == 2187
    assert by_period["2025"]["unique_addresses"] == 2066
    assert by_period["2025"]["net_main_bldg_teardowns"] == 2021
    assert by_period["2025"]["pool_spa_only"] == 5
    assert by_period["2025"]["canopy_porch_partial"] == 10
    assert by_period["2025"]["excluded_type13_bldg_demo_permits"] == 397
    assert by_period["2024"]["total_permits"] == 2380
    assert by_period["2024"]["unique_addresses"] == 2287
    assert by_period["2024"]["net_main_bldg_teardowns"] == 2161
    assert by_period["2024"]["pool_spa_only"] == 5
    assert by_period["2024"]["canopy_porch_partial"] == 4
    assert by_period["2024"]["excluded_type13_bldg_demo_permits"] == 353

    hcad = bench.get("hcad_countywide_demolitions")
    assert isinstance(hcad, list) and len(hcad) == 6
    assert [row["year"] for row in hcad] == [2025, 2024, 2023, 2022, 2021, 2020]
    assert hcad[0]["total_permits"] == 2791
    assert hcad[0]["unique_parcels"] == 2464
    assert hcad[0]["net_main_bldg_teardowns"] == 2542
    assert hcad[0]["garage_accessory"] == 143
    assert hcad[0]["pool_spa_only"] == 87
    assert hcad[0]["partial_addition_interior"] == 19
    assert hcad[0]["excluded_type11_31_remodel_demo"] == 463
    assert hcad[1]["net_main_bldg_teardowns"] == 2561
    assert hcad[-1]["year"] == 2020
    assert hcad[-1]["total_permits"] == 2096
    assert hcad[-1]["net_main_bldg_teardowns"] == 1923

    hahc = bench.get("hahc_historic_district_demolitions")
    assert isinstance(hahc, list) and len(hahc) == 3
    hahc_by_period = {row["period"]: row for row in hahc}
    assert set(hahc_by_period.keys()) == {"2026 YTD", "2025", "2024"}
    assert hahc_by_period["2026 YTD"]["main_res_approved"] == 0
    assert hahc_by_period["2026 YTD"]["main_res_denied"] == 2
    assert hahc_by_period["2024"]["main_res_approved"] == 0

    node_code = """
    import { DEFAULT_ANNUAL_DEMOLITION_BENCHMARKS } from './app/js/platMonitorHelpers.js';
    console.log(JSON.stringify(DEFAULT_ANNUAL_DEMOLITION_BENCHMARKS));
    """
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", node_code],
        cwd=str(REPO_ROOT),
        check=True,
        capture_output=True,
        text=True,
    )
    js_bench = json.loads(proc.stdout.strip())
    assert js_bench == default_bench
    assert js_bench == bench


def test_ut1_watchlist_sweep_health_fault_detection_and_rollup(
    monkeypatch, tmp_path: Path
) -> None:
    """UT-1: Verify honest 8-agency sweep health telemetry across timeouts, HTTP-200 errors, partial failures, skipped terms, and multi-property rollup."""
    import copy

    fake_data_dir = tmp_path / "data"
    (fake_data_dir / "permits").mkdir(parents=True)
    monkeypatch.setattr(fpw, "DATA_DIR", fake_data_dir)

    # Seed healthy baseline alerts index for scenarios 1 & 2
    (fake_data_dir / "active_permit_alerts.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-10-29T12:00:00Z",
                "alerts": [
                    {
                        "hcad_num": "0152300000001",
                        "address": "2000 Smith St",
                        "coh_permits": [
                            {
                                "project_no": "26099111",
                                "date": "2026-10-28",
                                "permit_type_label": "Demolition Permit",
                                "use_desc": "BLD DEMO/SEWER DISC",
                                "buyer": "ACME DEMO LLC",
                                "job_address": "2000 SMITH ST",
                                "is_structural_demo": True,
                            }
                        ],
                        "dshs_notifications": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    # Scenario A: Mixed multi-agency faults on a fully populated property:
    #   - chk-04: Sales Tax OK, Franchise Tax returns non-list Socrata error dict -> chk-04 failed
    #   - chk-05: HCAD ArcGIS returns HTTP-200 {"error": {"code": 499, ...}} -> chk-05 failed
    #   - chk-06: TABC Pending times out (None), TABC Active succeeds -> chk-06 failed
    #   - chk-09: plat_data flagged scrapeFailed -> chk-09 failed
    #   - chk-10: Addr #1 returns valid DataTables payload, Addr #2 times out (None) -> chk-10 failed
    #   - chk-13: Houston 311 ArcGIS returns HTTP-200 {"error": {"code": 500, ...}} -> chk-13 failed
    #   - chk-14 & chk-15: local active_permit_alerts.json valid -> ok
    tdlr_post_count = 0

    def fault_fetch_bytes(url: str, timeout: int = 12, data: bytes | None = None, headers=None):
        nonlocal tdlr_post_count
        if "jrea-zgmq.json" in url:  # Comptroller Sales Tax -> OK
            return json.dumps(
                [
                    {
                        "taxpayer_number": "32099112233",
                        "taxpayer_name": "SMITH ST C STORE LLC",
                        "outlet_number": "00001",
                        "outlet_name": "BRAKE TIME",
                        "outlet_address": "2000 SMITH ST",
                        "outlet_city": "HOUSTON",
                        "outlet_county_code": "101",
                        "outlet_permit_issue_date": "20260315",
                    }
                ]
            ).encode("utf-8")
        if "9cir-efmm.json" in url:  # Comptroller Franchise Tax -> non-list Socrata error dict
            return json.dumps({"error": True, "message": "SoQL rate limit or syntax fault"}).encode("utf-8")
        if "arcweb.hcad.org" in url:  # HCAD ArcGIS -> HTTP-200 error envelope
            return json.dumps({"error": {"code": 499, "message": "Token Required"}}).encode("utf-8")
        if "mxm5-tdpj.json" in url:  # TABC Pending -> network timeout
            return None
        if "7hf9-qc9f.json" in url:  # TABC Active -> OK list
            return b"[]"
        if "tdlr.texas.gov/TABS/Search/SearchProjects" in url:
            tdlr_post_count += 1
            if tdlr_post_count == 1:
                return json.dumps(
                    {
                        "data": [
                            {
                                "ProjectNumber": "TABS2026099999",
                                "ProjectName": "Smith Retail Buildout",
                                "LocationAddress": "2000 SMITH ST",
                                "County": "2101",
                                "EstimatedCost": "550000",
                                "WorkType": "New Construction",
                            }
                        ]
                    }
                ).encode("utf-8")
            return None  # Second perimeter address POST times out
        if "tdlr.texas.gov/TABS/Search/Project/" in url:
            return b"<html><body>Scope of Work: Convenience store Square Footage: 4000</body></html>"
        if "311" in url or "mycity" in url.lower() or "arcgis" in url.lower():  # Houston 311 ArcGIS
            return json.dumps({"error": {"code": 503, "message": "Service Unavailable"}}).encode("utf-8")
        return b"[]"

    monkeypatch.setattr(fpw, "fetch_bytes", fault_fetch_bytes)

    prop_faulty = fpw.normalize_watch_property_record(
        {
            "id": "prop-faulty",
            "propertyName": "Fault Test Building",
            "address": "2000 Smith St.",
            "secondaryAddresses": ["530 Pierce St."],
            "hcadAccount": "0152300000001",
            "searchTerms": {
                "entities": ["Smith St C Store LLC"],
                "dbas": ["Brake Time"],
                "altAddresses": ["530 Pierce St."],
            },
        }
    )
    res_faulty = fpw.sweep_watched_property_agencies(
        prop=prop_faulty,
        plat_data={"scrapeFailed": True, "error": "Upstream agenda download HTTP 502"},
        idx={},
        sweep_date_iso="2026-10-29",
    )
    ah = res_faulty["agencyHealth"]
    assert set(ah.keys()) == set(fpw.AUTOMATED_SWEEP_AGENCY_LABELS.keys())
    for failed_cid in ("chk-04", "chk-05", "chk-06", "chk-09", "chk-10", "chk-13"):
        assert ah[failed_cid]["status"] == "failed", f"{failed_cid} must be marked failed"
        assert ah[failed_cid]["ok"] is False
        assert ah[failed_cid]["error"], f"{failed_cid} must include diagnostic error text"
    assert "ArcGIS error 499: Token Required" in ah["chk-05"]["error"]
    assert "SoQL rate limit" in ah["chk-04"]["error"]
    assert "TABC Pending" in ah["chk-06"]["error"]
    assert "530 Pierce" in ah["chk-10"]["error"]
    assert ah["chk-14"]["status"] == "ok" and ah["chk-14"]["ok"] is True
    assert ah["chk-15"]["status"] == "ok" and ah["chk-15"]["ok"] is True
    # Valid partial data from chk-04 Sales Tax, chk-10 Addr #1, and chk-14 still surfaces
    assert len(res_faulty["findingsByCheck"]["chk-04"]) >= 1
    assert len(res_faulty["findingsByCheck"]["chk-10"]) == 1
    assert len(res_faulty["findingsByCheck"]["chk-14"]) == 1
    assert "26099111" in res_faulty["harvestedProjectNumbers"]

    # Verify merge_auto_sweep_findings_into_property does NOT mutate caller's sweep_bundle
    bundle_before = copy.deepcopy(res_faulty)
    fpw.merge_auto_sweep_findings_into_property(
        prop_faulty, res_faulty, "2026-10-29T12:05:00Z"
    )
    assert res_faulty == bundle_before, "merge_auto_sweep_findings_into_property must never mutate sweep_bundle"
    chk14_row = next(r for r in prop_faulty["checklist"] if r["id"] == "chk-14")
    assert chk14_row["status"] == "Flagged"
    assert chk14_row["lastAutoSweepAt"] == "2026-10-29T12:05:00Z"
    assert "26099111" in prop_faulty["searchTerms"]["projectNumbers"]
    # Re-merging identical sweep_bundle is strictly idempotent
    chk14_len = len(chk14_row["checks"])
    fpw.merge_auto_sweep_findings_into_property(
        prop_faulty, res_faulty, "2026-10-29T12:10:00Z"
    )
    assert len(chk14_row["checks"]) == chk14_len

    # Scenario B: Skipped agencies due to empty search terms report status == 'skipped' (ok=True, NOT failed)
    monkeypatch.setattr(fpw, "fetch_bytes", lambda *a, **kw: b"[]")
    prop_sparse = {"id": "prop-sparse", "address": "", "hcadAccount": "", "searchTerms": {}}
    res_sparse = fpw.sweep_watched_property_agencies(
        prop=prop_sparse,
        plat_data={"cycles": {"2026-10-29": {"items": []}}},
        idx={},
        sweep_date_iso="2026-10-29",
    )
    ah_sparse = res_sparse["agencyHealth"]
    for cid, meta in ah_sparse.items():
        assert meta["status"] == "skipped", f"Empty-term {cid} should be skipped, got {meta}"
        assert meta["ok"] is True
    rolled_sparse = fpw.aggregate_watchlist_sweep_health(
        [ah_sparse], "2026-10-29T12:00:00Z", run_live_sweeps=True
    )
    assert rolled_sparse["allOk"] is True
    assert rolled_sparse["failedAgencies"] == []
    assert rolled_sparse["succeededAgencies"] == 8
    assert len(rolled_sparse["skippedAgencies"]) == 8

    # Scenario C: Degraded local permit/DSHS indices flag chk-14 and chk-15 as failed
    (fake_data_dir / "active_permit_alerts.json").write_text(
        json.dumps({"alerts": [], "coh_fetch_failed": True, "dshs_fetch_failed": True}),
        encoding="utf-8",
    )
    res_deg_idx = fpw.sweep_watched_property_agencies(
        prop={"id": "prop-idx", "address": "500 Main St", "hcadAccount": "0010020030004"},
        plat_data={"cycles": {"2026-10-29": {"items": []}}},
        idx={},
        sweep_date_iso="2026-10-29",
    )
    assert res_deg_idx["agencyHealth"]["chk-14"]["ok"] is False
    assert res_deg_idx["agencyHealth"]["chk-15"]["ok"] is False

    # Restore healthy active_permit_alerts.json for Scenario D (multi-property worst-case rollup in build_property_watchlist)
    (fake_data_dir / "active_permit_alerts.json").write_text(
        json.dumps({"alerts": []}), encoding="utf-8"
    )

    def selective_prop_b_failure(url: str, timeout: int = 12, data: bytes | None = None, headers=None):
        if "tdlr.texas.gov/TABS/Search/SearchProjects" in url and data and b"910+BAGBY" in data.upper():
            return None  # Only Property B fails chk-10; Kirby Mansion succeeds on all 8
        if "tdlr.texas.gov/TABS/Search/SearchProjects" in url:
            return b'{"data": []}'
        if "arcweb.hcad.org" in url or "arcgis" in url.lower() or "311" in url or "mycity" in url.lower():
            return b'{"features": []}'
        return b"[]"

    monkeypatch.setattr(fpw, "fetch_bytes", selective_prop_b_failure)
    wl_built = fpw.build_property_watchlist(
        plat_data={
            "cycleKey": "2026-10-29",
            "cycles": {
                "2026-10-29": {
                    "items": [
                        {
                            "app": "2026-8888",
                            "name": "Bagby Block Replat",
                            "type": "C2",
                            "section": "A. Consent",
                            "hcad": "0410010010001",
                            "atlas": {"primaryAddress": "910 Bagby St", "earliestYearBuilt": 1925},
                        }
                    ],
                    "reviews": {
                        "2026-8888": {"tier": "1", "tierConfirmed": True, "why": "Historic block"}
                    },
                }
            },
        },
        idx={},
        cloud_sync={"watchProperties": {}},
        run_live_sweeps=True,
        sweep_timestamp_iso="2026-10-29T12:15:00Z",
    )
    sh = wl_built["lastSweepHealth"]
    assert sh["propertiesSwept"] == 2
    assert sh["totalAgencies"] == 8
    assert sh["succeededAgencies"] == 7
    assert sh["failedAgencies"] == ["chk-10"]
    assert sh["allOk"] is False
    assert sh["agencies"]["chk-10"]["failedPropertyCount"] == 1
    assert sh["agencies"]["chk-05"]["ok"] is True


def test_ut2_four_stream_snapshot_diff_events() -> None:
    """UT-2: Verify deterministic pre/post snapshot diffing across all 4 monitoring streams."""
    from atlas_pipeline import build_monitor_activity_log as bmal

    now_iso = "2026-10-29T12:15:00Z"

    before_bundle = {
        "property_watchlist.json": {
            "watchedProperties": [
                {
                    "id": "prop-kirby-mansion",
                    "propertyName": "John Henry Kirby Mansion",
                    "address": "2000 Smith St.",
                    "hcadAccount": "0152300000001",
                    "buildingId": "bld_004618",
                    "yearBuilt": 1894,
                    "designation": "1979 Good Brick Award",
                    "lat": 29.751432,
                    "lng": -95.374833,
                    "tier": "1",
                    "trackerStatus": "Active / High Priority",
                    "notes": "Baseline note",
                    "searchTerms": {"entities": ["OB Texas Properties LLC"]},
                    "driveLinks": [],
                    "checklist": [
                        {"id": "chk-02", "activity": "Site Visit", "status": "In Progress", "checks": []},
                        {"id": "chk-10", "activity": "Accessibility Registration (TDLR TABS)", "status": "Complete", "checks": []},
                        {"id": "chk-13", "activity": "Code Complaints & Dangerous Buildings", "status": "To Do", "checks": []},
                    ],
                },
                {
                    "id": "plat-2026-1111",
                    "propertyName": "Old Warehouse",
                    "address": "100 Commerce St",
                    "hcadAccount": "0020030040005",
                    "tier": "2",
                    "trackerStatus": "Monitoring",
                    "checklist": [],
                },
                {
                    "id": "plat-2026-2222",
                    "propertyName": "Removed Ephemeral Row",
                    "address": "200 Commerce St",
                    "tier": "2",
                    "trackerStatus": "Monitoring",
                    "checklist": [],
                },
            ]
        },
        "plat_agenda_monitor.json": {
            "cycles": {
                "2026-10-15": {
                    "items": [
                        {
                            "app": "2026-1585",
                            "name": "Montrose Residences",
                            "type": "C3N",
                            "section": "I. Public Hearings",
                            "hcad": "0610770240001",
                            "pdf": "",
                            "noticePdf": "",
                            "deferral": "",
                            "atlas": {
                                "primaryAddress": "4310 Montrose Blvd",
                                "earliestYearBuilt": 1930,
                                "lat": 29.734,
                                "lng": -95.391,
                            },
                        }
                    ],
                    "reviews": {},
                }
            }
        },
        "active_permit_alerts.json": {
            "alerts": [
                {
                    "hcad_num": "0152300000001",
                    "title": "John Henry Kirby Mansion",
                    "address": "2000 Smith St",
                    "year_built": 1894,
                    "coh_permits": [{"project_no": "26090093", "date": "2026-10-14", "is_structural_demo": True}],
                    "dshs_notifications": [],
                }
            ]
        },
        "hahc_coa_tracker.json": {
            "current_cycle": {
                "meeting_date": "2026-10-08",
                "active_items_count": 14,
                "guideline_updates": [],
            },
            "filings": [
                {
                    "filing_id": "HP2026_0301",
                    "meeting_date": "2026-10-08",
                    "address": "1115 Heights Blvd",
                    "hcad_num": "0201120000005",
                    "historic_district": "Houston Heights South",
                    "app_type": "Demolition / Relocation",
                    "app_category": "demolition_relocation",
                    "outcome": "pending_agenda",
                },
                {
                    "filing_id": "HP2026_0302",
                    "meeting_date": "2026-10-08",
                    "address": "712 Cortlandt St",
                    "historic_district": "Houston Heights South",
                    "app_type": "Alteration / Addition",
                    "app_category": "alteration_addition",
                    "outcome": "pending_agenda",
                },
                {
                    "filing_id": "HP2026_0303",
                    "meeting_date": "2026-10-08",
                    "address": "820 Columbia St",
                    "historic_district": "Houston Heights East",
                    "app_type": "Alteration / Addition",
                    "app_category": "alteration_addition",
                    "outcome": "pending_agenda",
                },
            ],
        },
    }

    after_bundle = {
        "property_watchlist.json": {
            "lastAutoSweepAt": now_iso,
            "lastSweepHealth": {
                "timestamp": now_iso,
                "sweepsExecuted": True,
                "propertiesSwept": 3,
                "totalAgencies": 8,
                "succeededAgencies": 7,
                "failedAgencies": ["chk-10"],
                "allOk": False,
                "agencies": {
                    "chk-10": {
                        "status": "failed",
                        "ok": False,
                        "label": "TDLR Architectural Barriers (TABS)",
                        "error": "TDLR TABS (530 Pierce St): HTTP timeout",
                    }
                },
            },
            "watchedProperties": [
                {
                    "id": "prop-kirby-mansion",
                    "propertyName": "John Henry Kirby Mansion",
                    "address": "2000 Smith St.",
                    "hcadAccount": "0152300000001",
                    "buildingId": "bld_004618",
                    "yearBuilt": 1894,
                    "designation": "1979 Good Brick Award",
                    "lat": 29.751432,
                    "lng": -95.374833,
                    "tier": "1",
                    "trackerStatus": "Active / High Priority",
                    "notes": "Updated note with architect plan contact",
                    "searchTerms": {"entities": ["OB Texas Properties LLC", "Brake Time Holding LLC"]},
                    "driveLinks": [{"label": "Deed PDF", "url": "https://drive.google.com/file/d/abc123/view"}],
                    "updatedAt": "2026-10-29T11:45:00Z",
                    "updatedBy": "Emily",
                    "checklist": [
                        {
                            "id": "chk-02",
                            "activity": "Site Visit",
                            "status": "Flagged",
                            "checks": [
                                {
                                    "date": "2026-10-29",
                                    "loggedAt": "2026-10-29T11:40:00Z",
                                    "result": "Observed orange abatement barrier fencing along Pierce St",
                                    "source": "manual",
                                }
                            ],
                        },
                        {
                            "id": "chk-10",
                            "activity": "Accessibility Registration (TDLR TABS)",
                            "status": "Flagged",
                            "checks": [
                                {
                                    "date": "2026-10-29",
                                    "discoveredAt": now_iso,
                                    "agencyRecordId": "TABS2026088777",
                                    "result": "[AUTO-SWEEP] TDLR #TABS2026088777 canopy registration at 530 Pierce St",
                                    "source": "auto-sweep",
                                    "adverse": True,
                                }
                            ],
                        },
                        {
                            "id": "chk-13",
                            "activity": "Code Complaints & Dangerous Buildings",
                            "status": "Flagged",
                            "checks": [],
                        },
                    ],
                },
                {
                    "id": "plat-2026-1111",
                    "propertyName": "Old Warehouse",
                    "address": "100 Commerce St",
                    "hcadAccount": "0020030040005",
                    "tier": "3",
                    "trackerStatus": "Archived (Tier 3)",
                    "archivedAt": "2026-10-29T11:50:00Z",
                    "checklist": [],
                },
                {
                    "id": "plat-2026-9901",
                    "propertyName": "Magnolia Brewery Annex",
                    "address": "715 Franklin St",
                    "hcadAccount": "0010120000001",
                    "tier": "1",
                    "trackerStatus": "Active / High Priority",
                    "keyDates": {"monitoringStarted": "2026-10-29"},
                    "checklist": [],
                },
            ],
        },
        "plat_agenda_monitor.json": {
            "cycles": {
                "2026-10-15": {
                    "items": [
                        {
                            "app": "2026-1585",
                            "name": "Montrose Residences",
                            "type": "C3N",
                            "section": "I. Public Hearings",
                            "hcad": "0610770240001",
                            "hearingDateIso": "2026-11-12",
                            "pdf": "https://edrc.houstontx.gov/plats/2026-1585.pdf",
                            "noticePdf": "https://houstontx.gov/planning/notices/2026-1585-notice.pdf",
                            "deferral": "1st Request (2 weeks)",
                            "atlas": {
                                "primaryAddress": "4310 Montrose Blvd",
                                "earliestYearBuilt": 1930,
                                "lat": 29.734,
                                "lng": -95.391,
                            },
                        },
                        {
                            "app": "2026-1659",
                            "name": "Amerimex Motors Reserve",
                            "type": "C2R",
                            "section": "D. Variances",
                            "hcad": "0660450130001",
                            "atlas": {"primaryAddress": "8002 North Fwy", "earliestYearBuilt": 1964},
                        },
                        {
                            "app": "DPV-26079999",
                            "name": "Westheimer Heritage Block DPV",
                            "type": "Development Plat Variance (DPV)",
                            "section": "K. Development Plat Variances",
                            "hcad": "0520010020003",
                            "varianceSummary": "Requesting 0-ft setback along Westheimer Rd",
                            "atlas": {
                                "primaryAddress": "1420 Westheimer Rd",
                                "earliestYearBuilt": 1924,
                                "designations": ["City Protected Landmark"],
                            },
                        },
                    ],
                    "reviews": {
                        "2026-1585": {
                            "tier": "1",
                            "tierConfirmed": True,
                            "reviewer": "Emily",
                            "threatCategory": "Significant Building Threatened",
                            "why": "1930 masonry courtyard apartments",
                            "updated": "2026-10-29T12:00:00Z",
                        }
                    },
                },
                "2026-10-29": {
                    "sourceSpreadsheetUrl": "https://houstontx.gov/planning/2026-10-29.xlsx",
                    "agendaOutlineUrl": "https://houstontx.gov/planning/2026-10-29-outline.pdf",
                    "summary": {
                        "totalAgendaItems": 88,
                        "toCheckCount": 11,
                        "dpvVarianceCount": 1,
                        "publicHearingNoticeCount": 2,
                    },
                    "items": [
                        {
                            "app": "DPV-26081111",
                            "name": "Heights Bungalow Variance",
                            "type": "Development Plat Variance (DPV)",
                            "section": "K. Development Plat Variances",
                            "atlas": {"primaryAddress": "512 Harvard St", "earliestYearBuilt": 1918},
                        }
                    ],
                    "reviews": {},
                },
            }
        },
        "active_permit_alerts.json": {
            "alerts": [
                {
                    "hcad_num": "0152300000001",
                    "watchlist_id": "prop-kirby-mansion",
                    "title": "John Henry Kirby Mansion",
                    "address": "2000 Smith St",
                    "year_built": 1894,
                    "coh_permits": [{"project_no": "26090093", "date": "2026-10-14", "is_structural_demo": True}],
                    "dshs_notifications": [
                        {
                            "notification_number": "2026009911",
                            "work_type": "Demolition",
                            "filing_date": "2026-10-29",
                            "start_date": "2026-11-12",
                            "end_date": "2026-11-20",
                            "abatement_contractor": "AAR Incorporated",
                            "print_action_url": "https://vo.ras.dshs.state.tx.us/datamart/notificationPrintAction.do?licId=999",
                        }
                    ],
                },
                {
                    "hcad_num": "410170020150",  # 12-digit unpadded HCAD tests 13-digit zero-padding
                    "title": "River Oaks Theater Annex",
                    "address": "2009 W Gray St",
                    "year_built": 1939,
                    "landmark_name": "River Oaks Theater",
                    "historic_district": "",
                    "lat": 29.7531,
                    "lon": -95.4082,
                    "coh_permits": [
                        {
                            "project_no": "26099555",
                            "date": "2026-10-29",
                            "permit_type_label": "Building Demolition",
                            "use_desc": "DEMO COMMERCIAL STRUCTURE",
                            "buyer": "DEMO CO",
                            "is_structural_demo": True,
                        }
                    ],
                    "dshs_notifications": [],
                },
            ]
        },
        "hahc_coa_tracker.json": {
            "source_url": "https://houstontx.gov/planning/Commissions/commiss_hahc.html",
            "current_cycle": {
                "meeting_date": "2026-11-05",
                "active_items_count": 19,
                "agenda_pdf_url": "https://houstontx.gov/planning/hahc/2026-11-05-agenda.pdf",
                "guideline_updates": [
                    {
                        "title": "Freedmen's Town Historic District Design Guidelines Redline",
                        "pdf_url": "https://houstontx.gov/planning/hahc/freedmens-redline.pdf",
                    }
                ],
            },
            "filings": [
                {
                    "filing_id": "HP2026_0301",
                    "meeting_date": "2026-10-08",
                    "address": "1115 Heights Blvd",
                    "hcad_num": "0201120000005",
                    "historic_district": "Houston Heights South",
                    "app_type": "Demolition / Relocation",
                    "app_category": "demolition_relocation",
                    "outcome": "approved",
                    "outcome_detail": "Approved final demolition COA per post-hearing action sheet",
                },
                {
                    "filing_id": "HP2026_0302",
                    "meeting_date": "2026-10-08",
                    "address": "712 Cortlandt St",
                    "historic_district": "Houston Heights South",
                    "app_type": "Alteration / Addition",
                    "app_category": "alteration_addition",
                    "outcome": "denied",
                },
                {
                    "filing_id": "HP2026_0303",
                    "meeting_date": "2026-10-08",
                    "address": "820 Columbia St",
                    "historic_district": "Houston Heights East",
                    "app_type": "Alteration / Addition",
                    "app_category": "alteration_addition",
                    "outcome": "deferred",
                },
            ],
        },
    }

    evs = bmal.compute_snapshot_diff_events(before_bundle, after_bundle, now_iso)
    by_id = {e["id"]: e for e in evs}

    # Stream 1 (watchlist)
    assert "wl:sweep:2026-10-29:degraded:chk-10" in by_id
    sw_ev = by_id["wl:sweep:2026-10-29:degraded:chk-10"]
    assert sw_ev["stream"] == "watchlist" and sw_ev["severity"] == "warning" and sw_ev["adverse"] is False
    assert "7/8 agencies OK" in sw_ev["title"]

    auto_ev = by_id["wl:auto:prop-kirby-mansion:chk-10:id:TABS2026088777"]
    assert auto_ev["stream"] == "watchlist" and auto_ev["severity"] == "critical" and auto_ev["adverse"] is True
    assert auto_ev["watchPropertyId"] == "prop-kirby-mansion" and auto_ev["checkId"] == "chk-10"

    # Both Complete -> Flagged (chk-10), To Do -> Flagged (chk-13), and In Progress -> Flagged (chk-02)
    assert by_id["wl:status:prop-kirby-mansion:chk-10:flagged:2026-10-29"]["kind"] == "watchlist_status_escalation"
    assert by_id["wl:status:prop-kirby-mansion:chk-10:flagged:2026-10-29"]["severity"] == "critical"
    assert by_id["wl:status:prop-kirby-mansion:chk-13:flagged:2026-10-29"]["adverse"] is True
    assert by_id["wl:status:prop-kirby-mansion:chk-02:flagged:2026-10-29"]["severity"] == "warning"

    # Unedited plat-confirmed watch property lands on watchlist stream
    plat_add_ev = by_id["staff:prop:plat-2026-9901:added"]
    assert plat_add_ev["stream"] == "watchlist" and plat_add_ev["watchPropertyId"] == "plat-2026-9901"

    # Stream 2 (agenda)
    assert by_id["agenda:cycle:2026-10-29"]["stream"] == "agenda"
    assert by_id["agenda:item:2026-10-29:DPV-26081111:dpv"]["kind"] == "agenda_item_dpv"
    assert by_id["agenda:item:2026-10-15:2026-1659:added"]["severity"] == "warning"
    dpv_crit = by_id["agenda:item:2026-10-15:DPV-26079999:dpv"]
    assert dpv_crit["severity"] == "critical" and dpv_crit["adverse"] is True
    assert by_id["agenda:item:2026-10-15:2026-1585:plat_pdf"]["kind"] == "agenda_item_plat_pdf"
    assert by_id["agenda:item:2026-10-15:2026-1585:notice_pdf"]["severity"] == "warning"
    assert by_id["agenda:item:2026-10-15:2026-1585:deferral:1st-request-2-weeks"]["kind"] == "agenda_item_deferral"

    # Stream 3 (staff)
    manual_matches = [k for k in by_id if k.startswith("wl:manual:prop-kirby-mansion:chk-02:2026-10-29:")]
    assert len(manual_matches) == 1
    assert by_id[manual_matches[0]]["stream"] == "staff"
    assert by_id[manual_matches[0]]["loggedAt"] == "2026-10-29T11:40:00Z"

    edit_ev = by_id["staff:prop:prop-kirby-mansion:edited:2026-10-29"]
    assert edit_ev["stream"] == "staff"
    assert "dossier notes" in edit_ev["summary"]
    assert "snowball search terms" in edit_ev["summary"]
    assert "Drive / document links" in edit_ev["summary"]

    assert by_id["staff:prop:plat-2026-1111:archived"]["watchPropertyId"] == "plat-2026-1111"
    assert by_id["staff:prop:plat-2026-2222:archived"]["watchPropertyId"] == ""
    rev_ev = by_id["staff:review:2026-10-15:2026-1585:t1"]
    assert rev_ev["stream"] == "staff" and rev_ev["occKey"] == "2026-10-15::2026-1585"

    # Stream 4 (permits & HAHC rulings)
    assert "permit:coh:0152300000001:26090093" not in by_id  # Unchanged baseline permit must NOT re-emit
    coh_new = by_id["permit:coh:0410170020150:26099555"]
    assert coh_new["stream"] == "permits" and coh_new["severity"] == "critical" and coh_new["adverse"] is True
    assert coh_new["hcad"] == "0410170020150"
    assert coh_new["permitMeta"]["lng"] == -95.4082

    dshs_new = by_id["permit:dshs:0152300000001:2026009911"]
    assert dshs_new["severity"] == "critical" and dshs_new["adverse"] is True
    assert dshs_new["watchPropertyId"] == "prop-kirby-mansion"

    assert by_id["hahc:cycle:2026-11-05"]["kind"] == "hahc_cycle_posted"
    guide_keys = [k for k in by_id if k.startswith("hahc:guideline:2026-11-05:")]
    assert len(guide_keys) == 1 and by_id[guide_keys[0]]["severity"] == "warning"

    ruling_approved = by_id["hahc:filing:HP2026_0301:approved"]
    assert ruling_approved["kind"] == "hahc_filing_outcome"
    assert ruling_approved["severity"] == "critical" and ruling_approved["adverse"] is True
    assert "Pending HAHC Agenda -> Approved" in ruling_approved["title"]

    ruling_denied = by_id["hahc:filing:HP2026_0302:denied"]
    assert ruling_denied["kind"] == "hahc_filing_outcome"
    assert ruling_denied["severity"] == "warning" and ruling_denied["adverse"] is False

    ruling_deferred = by_id["hahc:filing:HP2026_0303:deferred"]
    assert ruling_deferred["kind"] == "hahc_filing_outcome"
    assert ruling_deferred["severity"] == "warning" and ruling_deferred["adverse"] is False


def test_ut3_activity_log_cli_retention_timezone_and_intraday_idempotency(tmp_path: Path) -> None:
    """UT-3: Verify --save-before / --update-log / --seed-initial CLI, 90-day pruning, America/Chicago midnight bucketing, and same-day workflow_dispatch idempotency."""
    import shutil
    from atlas_pipeline import build_monitor_activity_log as bmal

    script_path = REPO_ROOT / "pipeline" / "atlas_pipeline" / "build_monitor_activity_log.py"

    # 1. Seed initial ledger from a copy of real app/public/data snapshots via bare stdlib Python (-S)
    seed_data_dir = tmp_path / "seed_data"
    seed_data_dir.mkdir()
    real_data_dir = REPO_ROOT / "app" / "public" / "data"
    for fname in bmal.SNAPSHOT_FILENAMES:
        shutil.copy2(real_data_dir / fname, seed_data_dir / fname)
    seed_log_path = tmp_path / "seeded_monitor_activity_log.json"

    subprocess.run(
        [
            sys.executable,
            "-S",
            str(script_path),
            "--seed-initial",
            "--data-dir",
            str(seed_data_dir),
            "--log-path",
            str(seed_log_path),
            "--now-iso",
            "2026-10-29T12:15:00Z",
        ],
        check=True,
        cwd=str(REPO_ROOT),
    )
    seeded_raw = seed_log_path.read_text(encoding="utf-8")
    assert "\u2014" not in seeded_raw
    seeded_doc = json.loads(seeded_raw)
    assert seeded_doc["version"] == 1
    assert seeded_doc["timezone"] == "America/Chicago"
    assert seeded_doc["retentionFloorDate"] == "2026-08-01"
    assert seeded_doc["summary"]["totalEvents"] > 10
    for s_key in ("watchlist", "agenda", "staff", "permits"):
        assert seeded_doc["summary"]["byStream"][s_key] >= 1, f"Seeded log missing {s_key} events"
    assert all(ev["localDate"] >= "2026-08-01" for ev in seeded_doc["events"])
    assert all(ev["localDate"] <= "2026-10-29" for ev in seeded_doc["events"])

    # 2. Missing optional snapshot files + >90-day pruning + America/Chicago UTC-midnight bucketing
    live_dir = tmp_path / "live_data"
    before_dir = tmp_path / "before"
    live_dir.mkdir()
    log_path = live_dir / "monitor_activity_log.json"

    # Write ONLY property_watchlist.json (leaving plat/permits/hahc optional snapshots absent)
    (live_dir / "property_watchlist.json").write_text(
        json.dumps(
            {
                "lastAutoSweepAt": "2026-10-28T12:00:00Z",
                "lastSweepHealth": {
                    "timestamp": "2026-10-28T12:00:00Z",
                    "sweepsExecuted": True,
                    "propertiesSwept": 1,
                    "totalAgencies": 8,
                    "succeededAgencies": 8,
                    "failedAgencies": [],
                    "allOk": True,
                },
                "watchedProperties": [
                    {
                        "id": "prop-kirby-mansion",
                        "propertyName": "John Henry Kirby Mansion",
                        "address": "2000 Smith St.",
                        "hcadAccount": "0152300000001",
                        "tier": "1",
                        "checklist": [
                            {"id": "chk-02", "activity": "Site Visit", "status": "In Progress", "checks": []}
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    # Seed an existing log containing a stale >90-day event (2026-07-15) that must be pruned
    log_path.write_text(
        json.dumps(
            {
                "version": 1,
                "events": [
                    {
                        "id": "staff:prop:old-legacy:added",
                        "stream": "staff",
                        "kind": "watchlist_property_added",
                        "severity": "info",
                        "adverse": False,
                        "date": "2026-07-15",
                        "localDate": "2026-07-15",
                        "timestamp": "2026-07-15T12:00:00Z",
                        "discoveredAt": "2026-07-15T12:00:00Z",
                        "title": "Stale >90d event",
                        "summary": "Should prune",
                    }
                ],
                "dailyRuns": [{"date": "2026-07-15", "eventCount": 1, "adverseEventCount": 0}],
            }
        ),
        encoding="utf-8",
    )

    assert bmal.main(["--save-before", str(before_dir), "--data-dir", str(live_dir)]) == 0

    # Evening edit at 2026-10-29T02:30:00Z == 2026-10-28 21:30 CDT (must bucket under 2026-10-28!)
    evening_wl = json.loads((live_dir / "property_watchlist.json").read_text(encoding="utf-8"))
    evening_wl["watchedProperties"][0]["checklist"][0]["checks"].append(
        {
            "date": "2026-10-28",
            "loggedAt": "2026-10-29T02:30:00Z",
            "result": "Evening drive-by check at 9:30 PM Central Time",
            "source": "manual",
        }
    )
    (live_dir / "property_watchlist.json").write_text(json.dumps(evening_wl), encoding="utf-8")

    assert (
        bmal.main(
            [
                "--update-log",
                "--data-dir",
                str(live_dir),
                "--before-dir",
                str(before_dir),
                "--log-path",
                str(log_path),
                "--now-iso",
                "2026-10-29T02:30:00Z",
            ]
        )
        == 0
    )
    after_evening = json.loads(log_path.read_text(encoding="utf-8"))
    assert all(e["id"] != "staff:prop:old-legacy:added" for e in after_evening["events"])
    assert all(r["date"] >= after_evening["retentionFloorDate"] for r in after_evening["dailyRuns"])
    assert len(after_evening["events"]) == 1
    assert after_evening["events"][0]["localDate"] == "2026-10-28"
    assert after_evening["dailyRuns"][0]["date"] == "2026-10-28"
    assert after_evening["dailyRuns"][0]["eventCount"] == 1

    # 3. Same-day repeated --update-log idempotency + degraded sweep telemetry isolation:
    #    Morning run (2026-10-29T12:15:00Z, 8/8 OK) discovers exactly 4 new substantive events (1 adverse).
    assert bmal.main(["--save-before", str(before_dir), "--data-dir", str(live_dir)]) == 0
    morning_wl = json.loads((live_dir / "property_watchlist.json").read_text(encoding="utf-8"))
    morning_wl["lastAutoSweepAt"] = "2026-10-29T12:15:00Z"
    morning_wl["lastSweepHealth"] = {
        "timestamp": "2026-10-29T12:15:00Z",
        "sweepsExecuted": True,
        "propertiesSwept": 1,
        "totalAgencies": 8,
        "succeededAgencies": 8,
        "failedAgencies": [],
        "allOk": True,
    }
    morning_wl["watchedProperties"][0]["notes"] = "Morning title update"
    morning_wl["watchedProperties"][0]["updatedAt"] = "2026-10-29T12:15:00Z"
    morning_wl["watchedProperties"][0]["checklist"].append(
        {
            "id": "chk-14",
            "activity": "Demolition & Building Permits",
            "status": "In Progress",  # Avoid extra status-escalation event so count is strictly 4
            "checks": [
                {
                    "date": "2026-10-29",
                    "discoveredAt": "2026-10-29T12:15:00Z",
                    "agencyRecordId": "26099888",
                    "result": "[AUTO-SWEEP] COH Demo Permit #26099888",
                    "source": "auto-sweep",
                    "adverse": True,
                }
            ],
        }
    )
    (live_dir / "property_watchlist.json").write_text(json.dumps(morning_wl), encoding="utf-8")
    (live_dir / "plat_agenda_monitor.json").write_text(
        json.dumps(
            {
                "cycles": {
                    "2026-10-29": {
                        "summary": {"totalAgendaItems": 1, "toCheckCount": 1},
                        "items": [
                            {
                                "app": "2026-7777",
                                "name": "Test Subdiv",
                                "type": "C2",
                                "section": "A. Consent",
                            }
                        ],
                        "reviews": {},
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    assert (
        bmal.main(
            [
                "--update-log",
                "--data-dir",
                str(live_dir),
                "--before-dir",
                str(before_dir),
                "--log-path",
                str(log_path),
                "--now-iso",
                "2026-10-29T12:15:00Z",
            ]
        )
        == 0
    )
    morning_log = json.loads(log_path.read_text(encoding="utf-8"))
    assert morning_log["dailyRuns"][0]["date"] == "2026-10-29"
    assert morning_log["dailyRuns"][0]["eventCount"] == 4
    assert morning_log["dailyRuns"][0]["adverseEventCount"] == 1
    assert morning_log["dailyRuns"][0]["allOk"] is True
    assert morning_log["dailyRuns"][0]["failedAgencies"] == []

    # Afternoon intraday workflow_dispatch re-run (2026-10-29T18:30:00Z) with 0 substantive delta
    # changes and degraded failedAgencies: ['chk-10']
    assert bmal.main(["--save-before", str(before_dir), "--data-dir", str(live_dir)]) == 0
    afternoon_wl = json.loads((live_dir / "property_watchlist.json").read_text(encoding="utf-8"))
    afternoon_wl["lastAutoSweepAt"] = "2026-10-29T18:30:00Z"
    afternoon_wl["lastSweepHealth"] = {
        "timestamp": "2026-10-29T18:30:00Z",
        "sweepsExecuted": True,
        "propertiesSwept": 1,
        "totalAgencies": 8,
        "succeededAgencies": 7,
        "failedAgencies": ["chk-10"],
        "allOk": False,
        "agencies": {
            "chk-10": {
                "status": "failed",
                "ok": False,
                "label": "TDLR Architectural Barriers (TABS)",
                "error": "HTTP request failed or timed out",
            }
        },
    }
    (live_dir / "property_watchlist.json").write_text(json.dumps(afternoon_wl), encoding="utf-8")

    assert (
        bmal.main(
            [
                "--update-log",
                "--data-dir",
                str(live_dir),
                "--before-dir",
                str(before_dir),
                "--log-path",
                str(log_path),
                "--now-iso",
                "2026-10-29T18:30:00Z",
            ]
        )
        == 0
    )
    afternoon_log = json.loads(log_path.read_text(encoding="utf-8"))
    today_rec = afternoon_log["dailyRuns"][0]
    assert today_rec["date"] == "2026-10-29"
    assert today_rec["runCount"] == 2
    assert today_rec["lastRunAt"] == "2026-10-29T18:30:00Z"
    assert today_rec["failedAgencies"] == ["chk-10"]
    assert today_rec["succeededAgencies"] == 7
    assert today_rec["allOk"] is False
    # Substantive morning counts (4 events / 1 adverse) MUST be preserved without inflating to 5 or dropping to 0
    assert today_rec["eventCount"] == 4
    assert today_rec["adverseEventCount"] == 1
    assert any(e["id"] == "wl:sweep:2026-10-29:degraded:chk-10" for e in afternoon_log["events"])

    # Evening clean recovery run (2026-10-29T21:00:00Z, 8/8 OK again) must clear stale same-day degraded notice
    assert bmal.main(["--save-before", str(before_dir), "--data-dir", str(live_dir)]) == 0
    evening_ok_wl = json.loads((live_dir / "property_watchlist.json").read_text(encoding="utf-8"))
    evening_ok_wl["lastAutoSweepAt"] = "2026-10-29T21:00:00Z"
    evening_ok_wl["lastSweepHealth"] = {
        "timestamp": "2026-10-29T21:00:00Z",
        "sweepsExecuted": True,
        "propertiesSwept": 1,
        "totalAgencies": 8,
        "succeededAgencies": 8,
        "failedAgencies": [],
        "allOk": True,
    }
    (live_dir / "property_watchlist.json").write_text(json.dumps(evening_ok_wl), encoding="utf-8")

    assert (
        bmal.main(
            [
                "--update-log",
                "--data-dir",
                str(live_dir),
                "--before-dir",
                str(before_dir),
                "--log-path",
                str(log_path),
                "--now-iso",
                "2026-10-29T21:00:00Z",
            ]
        )
        == 0
    )
    recovery_log = json.loads(log_path.read_text(encoding="utf-8"))
    rec_today = recovery_log["dailyRuns"][0]
    assert rec_today["runCount"] == 3
    assert rec_today["allOk"] is True
    assert rec_today["failedAgencies"] == []
    assert rec_today["eventCount"] == 4
    assert rec_today["adverseEventCount"] == 1
    assert all(e["kind"] != "watchlist_sweep_degraded" for e in recovery_log["events"])


def test_post_review_producer_failure_flags_and_timestamp_roundtrips(
    monkeypatch, tmp_path: Path
) -> None:
    """Verify F2 upstream scraper failure propagation (chk-09/chk-14/chk-15) and F3/F7 statusChangedAt + archivedAt CT round-tripping."""
    import datetime as dt
    import urllib.error

    from atlas_pipeline import build_monitor_activity_log as bmal
    from atlas_pipeline import fetch_live_permits as flp
    from atlas_pipeline import fetch_plat_and_watchlist as fpw

    # 1. Upstream Plat Agenda HTTP outage sets scrapeFailed=True and flips chk-09 to failed
    monkeypatch.setattr(fpw, "fetch_bytes", lambda *a, **kw: None)
    scraped_fail = fpw.fetch_multi_cycle_agendas_and_notices(max_cycles=1)
    assert scraped_fail["scrapeFailed"] is True
    assert "unreachable" in scraped_fail["error"] or "failed" in scraped_fail["error"]
    plat_ds_fail = fpw.build_plat_agenda_dataset({}, scraped_fail)
    assert plat_ds_fail["scrapeFailed"] is True
    assert plat_ds_fail["error"]

    # 2. Upstream COH WebFOCUS + TX DSHS network outages propagate explicit failure flags into chk-14 / chk-15
    def _raise_url_err(*_args, **_kwargs):
        raise urllib.error.URLError("simulated upstream portal timeout")

    monkeypatch.setattr(flp.urllib.request, "urlopen", _raise_url_err)
    monkeypatch.setattr(flp.urllib.request, "build_opener", lambda *a, **kw: type(
        "_DeadOpener",
        (),
        {"addheaders": [], "open": staticmethod(_raise_url_err)},
    )())

    coh_map, coh_failed, coh_err = flp.fetch_coh_permits_parallel(lookback_days=5, return_status=True)
    assert coh_map == {}
    assert coh_failed is True
    assert "COH WebFOCUS window queries failed" in coh_err

    dshs_rows, dshs_failed, dshs_err = flp.fetch_dshs_houston_notifications(
        dt.date(2026, 10, 1), dt.date(2026, 10, 29), {}, return_status=True
    )
    assert dshs_rows == []
    assert dshs_failed is True
    assert "DSHS asbestos portal unreachable" in dshs_err

    fake_data_dir = tmp_path / "data"
    (fake_data_dir / "permits").mkdir(parents=True)
    (fake_data_dir / "active_permit_alerts.json").write_text(
        json.dumps(
            {
                "alerts": [],
                "coh_fetch_failed": coh_failed,
                "dshs_fetch_failed": dshs_failed,
                "coh_fetch_error": coh_err,
                "dshs_fetch_error": dshs_err,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(fpw, "DATA_DIR", fake_data_dir)
    monkeypatch.setattr(fpw, "fetch_bytes", lambda *a, **kw: b"[]")

    res_e2e_fail = fpw.sweep_watched_property_agencies(
        prop={"id": "prop-kirby-mansion", "address": "2000 Smith St.", "hcadAccount": "0152300000001"},
        plat_data=plat_ds_fail,
        idx={},
        sweep_date_iso="2026-10-29",
    )
    assert res_e2e_fail["agencyHealth"]["chk-09"]["status"] == "failed"
    assert res_e2e_fail["agencyHealth"]["chk-14"]["status"] == "failed"
    assert res_e2e_fail["agencyHealth"]["chk-15"]["status"] == "failed"
    assert "COH WebFOCUS" in res_e2e_fail["agencyHealth"]["chk-14"]["error"]
    assert "DSHS asbestos portal" in res_e2e_fail["agencyHealth"]["chk-15"]["error"]

    # 3. normalize_watch_property_record & build_property_watchlist preserve statusChangedAt & archivedAt
    norm_with_ts = fpw.normalize_watch_property_record(
        {
            "id": "prop-ts-check",
            "propertyName": "Historic Bank",
            "address": "400 Main St.",
            "hcadAccount": "0010020030004",
            "trackerStatus": "Archived (Tier 3)",
            "archivedAt": "2026-10-30T01:30:00Z",
            "checklist": [
                {
                    "id": "chk-01",
                    "status": "Flagged",
                    "humanStatusEdited": True,
                    "statusChangedAt": "2026-10-30T01:15:00Z",
                    "updatedAt": "2026-10-30T01:15:00Z",
                    "lastChecked": "2026-10-29",
                    "checks": [],
                }
            ],
        }
    )
    assert norm_with_ts["archivedAt"] == "2026-10-30T01:30:00Z"
    chk01_norm = next(r for r in norm_with_ts["checklist"] if r["id"] == "chk-01")
    assert chk01_norm["statusChangedAt"] == "2026-10-30T01:15:00Z"

    merged_wl = fpw.build_property_watchlist(
        plat_data={"cycleKey": "2026-10-29", "cycles": {"2026-10-29": {"items": [], "reviews": {}}}},
        idx={},
        cloud_sync={
            "watchProperties": {
                "prop-kirby-mansion": {
                    "id": "prop-kirby-mansion",
                    "propertyName": "John Henry Kirby Mansion",
                    "address": "2000 Smith St.",
                    "hcadAccount": "0152300000001",
                    "tier": "3",
                    "trackerStatus": "Archived (Tier 3)",
                    "archivedAt": "2026-10-30T01:45:00Z",
                    "updatedAt": "2026-10-30T01:45:00Z",
                    "updatedBy": "Emily",
                    "_hasExplicitChecklist": True,
                    "checklist": [
                        {
                            "id": "chk-03",
                            "status": "Flagged",
                            "humanStatusEdited": True,
                            "statusChangedAt": "2026-10-30T01:20:00Z",
                            "updatedAt": "2026-10-30T01:20:00Z",
                            "lastChecked": "2026-10-29",
                            "checks": [],
                        }
                    ],
                }
            }
        },
        run_live_sweeps=False,
        sweep_timestamp_iso="2026-10-30T12:15:00Z",
    )
    kirby_merged = next(p for p in merged_wl["watchedProperties"] if p["id"] == "prop-kirby-mansion")
    assert kirby_merged["archivedAt"] == "2026-10-30T01:45:00Z"
    assert kirby_merged["updatedBy"] == "Emily"
    chk03_merged = next(r for r in kirby_merged["checklist"] if r["id"] == "chk-03")
    assert chk03_merged["statusChangedAt"] == "2026-10-30T01:20:00Z"

    # 4. Next-morning CI sweep (2026-10-30T12:15:00Z == Oct 30 CT) buckets evening staff escalation + archive onto Oct 29 CT
    before_wl = {
        "watchedProperties": [
            {
                "id": "prop-evening",
                "propertyName": "Evening Test House",
                "address": "800 Lovett Blvd",
                "hcadAccount": "0520520000001",
                "trackerStatus": "Active / High Priority",
                "checklist": [{"id": "chk-01", "activity": "County Clerk Deed", "status": "In Progress", "checks": []}],
            },
            {
                "id": "prop-archive-eve",
                "propertyName": "Archived Bungalow",
                "address": "900 Heights Blvd",
                "hcadAccount": "0610610000002",
                "trackerStatus": "Active / Watch",
                "checklist": [],
            },
        ]
    }
    after_wl = {
        "watchedProperties": [
            {
                "id": "prop-evening",
                "propertyName": "Evening Test House",
                "address": "800 Lovett Blvd",
                "hcadAccount": "0520520000001",
                "trackerStatus": "Active / High Priority",
                "checklist": [
                    {
                        "id": "chk-01",
                        "activity": "County Clerk Deed",
                        "status": "Flagged",
                        "humanStatusEdited": True,
                        "statusChangedAt": "2026-10-30T01:20:00Z",
                        "updatedAt": "2026-10-30T01:20:00Z",
                        "lastChecked": "2026-10-29",
                        "checks": [],
                    }
                ],
            },
            {
                "id": "prop-archive-eve",
                "propertyName": "Archived Bungalow",
                "address": "900 Heights Blvd",
                "hcadAccount": "0610610000002",
                "trackerStatus": "Archived (Tier 3)",
                "archivedAt": "2026-10-30T01:45:00Z",
                "updatedAt": "2026-10-30T01:45:00Z",
                "checklist": [],
            },
        ]
    }
    diff_evs = bmal._diff_property_watchlist(before_wl, after_wl, "2026-10-30T12:15:00Z")
    esc_ev = next(e for e in diff_evs if e["kind"] == "watchlist_status_escalation")
    assert esc_ev["id"] == "wl:status:prop-evening:chk-01:flagged:2026-10-29"
    assert esc_ev["stream"] == "staff"
    assert esc_ev["localDate"] == "2026-10-29"
    assert esc_ev["loggedAt"] == "2026-10-30T01:20:00Z"

    arch_ev = next(e for e in diff_evs if e["kind"] == "watchlist_property_archived")
    assert arch_ev["localDate"] == "2026-10-29"
    assert arch_ev["loggedAt"] == "2026-10-30T01:45:00Z"

