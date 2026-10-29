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


