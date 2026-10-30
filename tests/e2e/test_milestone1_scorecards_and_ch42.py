from __future__ import annotations

import json
import math
import threading
import urllib.parse
from typing import Any

from conftest import (
    DEFAULT_E2E_VIEWPORT,
    attach_error_monitors,
    configure_fast_e2e_context,
    wait_for_atlas_ready as _wait_for_atlas_ready,
)

EXACT_FAMILY_COUNTS: dict[str, int] = {
    "council_districts": 11,
    "super_neighborhoods": 88,
    "historic_districts": 23,
    "potential_historic_districts": 60,
    "heritage_districts": 1,
    "holc_1937_zones": 47,
}

MIN_FAMILY_COUNTS: dict[str, int] = {
    "nrhp_districts": 12,
    "neighborhoods": 1380,
    "platted_subdivisions": 2500,
}

# Verified US Survey Feet conversion constants at Houston latitude 29.7917 N (North Norhill)
_REF_LNG = -95.38725
_REF_LAT = 29.79156
_US_SURVEY_FT_PER_M = 3937.0 / 1200.0
_LAT_RAD = math.radians(_REF_LAT)
_FT_PER_DEG_LAT = 110850.0 * _US_SURVEY_FT_PER_M
_FT_PER_DEG_LNG = 111320.0 * math.cos(_LAT_RAD) * _US_SURVEY_FT_PER_M

_ALLOWED_HCAD_OUTFIELDS = {
    "*",
    "HCAD_NUM",
    "address",
    "city",
    "zip",
    "owner",
    "subdivision",
    "state_class",
    "impr_val",
    "land_val",
    "appr_val",
    "mkt_val",
    "legal_lines",
    "Shape.STArea()",
}


def _rect_poly_ft(x_ft: float, y_ft: float, w_ft: float, d_ft: float) -> dict[str, Any]:
    def pt(px: float, py: float) -> list[float]:
        return [
            round(_REF_LNG + px / _FT_PER_DEG_LNG, 8),
            round(_REF_LAT + py / _FT_PER_DEG_LAT, 8),
        ]

    return {
        "type": "Polygon",
        "coordinates": [
            [
                pt(x_ft, y_ft),
                pt(x_ft + w_ft, y_ft),
                pt(x_ft + w_ft, y_ft + d_ft),
                pt(x_ft, y_ft + d_ft),
                pt(x_ft, y_ft),
            ]
        ],
    }


def _make_hcad_feature(
    hcad_num: str,
    address: str,
    owner: str,
    state_class: str,
    yr_impr: int,
    impr_val: int,
    land_val: int,
    area_sqft: float,
    x_ft: float,
    y_ft: float,
    w_ft: float,
    d_ft: float,
    legal_lines: str = "LT 1 BLK 131|NORTH NORHILL",
) -> dict[str, Any]:
    return {
        "type": "Feature",
        "properties": {
            "HCAD_NUM": hcad_num,
            "address": address,
            "owner": owner,
            "subdivision": "NORTH NORHILL",
            "state_class": state_class,
            "yr_impr": yr_impr,
            "impr_val": impr_val,
            "land_val": land_val,
            "appr_val": impr_val + land_val,
            "mkt_val": impr_val + land_val,
            "legal_lines": legal_lines,
            "Shape.STArea()": area_sqft,
        },
        "geometry": _rect_poly_ft(x_ft, y_ft, w_ft, d_ft),
    }


def _build_ch42_hcad_fixture_features() -> list[dict[str, Any]]:
    """Deterministic blockface around 1127 Key St (inside Norhill Historic District = 60% threshold)."""
    return [
        # Seed interior SFR lot (5,000 sq ft)
        _make_hcad_feature(
            "0621100000014",
            "1127 KEY ST",
            "MORRIS LAURA M & DAVID V",
            "A1",
            1928,
            245000,
            285000,
            5000.0,
            0.0,
            0.0,
            50.0,
            100.0,
            "LT 14 BLK 131|NORTH NORHILL",
        ),
        # Larger historic bungalow neighbor (6,000 sq ft)
        _make_hcad_feature(
            "0621100000015",
            "1121 KEY ST",
            "SPRECHER FAMILY LIVING TRUST",
            "A1",
            1926,
            260000,
            290000,
            6000.0,
            50.0,
            0.0,
            60.0,
            100.0,
            "LT 15 BLK 131|NORTH NORHILL",
        ),
        # Smaller SFR neighbor (4,000 sq ft)
        _make_hcad_feature(
            "0621100000016",
            "1119 KEY ST",
            "GOWER DE LOIS J",
            "A1",
            1930,
            210000,
            250000,
            4000.0,
            110.0,
            0.0,
            40.0,
            100.0,
            "LT 16 BLK 131|NORTH NORHILL",
        ),
        # Narrow townhome-sized SFR lot owned by corporate LLC (3,500 sq ft)
        _make_hcad_feature(
            "0621100000017",
            "1115 KEY ST",
            "HEIGHTS INFILL BUILDERS LLC",
            "A1",
            2018,
            340000,
            210000,
            3500.0,
            150.0,
            0.0,
            35.0,
            100.0,
            "TR 17A BLK 131|NORTH NORHILL",
        ),
        # Vacant residential lot eligible for Sec. 42-197(b)(4) prior-5-yr SFR toggle (5,000 sq ft)
        _make_hcad_feature(
            "0621100000098",
            "1109 KEY ST",
            "VACANT LOT HOLDINGS LLC",
            "C1",
            0,
            0,
            265000,
            5000.0,
            185.0,
            0.0,
            50.0,
            100.0,
            "LT 18 BLK 131|NORTH NORHILL",
        ),
        # Corner candidate addressed on perpendicular Michaux St (4,500 sq ft)
        _make_hcad_feature(
            "0621100000091",
            "1002 MICHAUX ST",
            "CORNER OWNER ALICE",
            "A1",
            1929,
            225000,
            255000,
            4500.0,
            235.0,
            0.0,
            45.0,
            100.0,
            "LT 20 BLK 131|NORTH NORHILL",
        ),
        # Common shared-driveway HOA reserve excluded from all denominators (800 sq ft)
        _make_hcad_feature(
            "0621100000092",
            "0 KEY ST",
            "KEY STREET TOWNHOMES HOA INC",
            "C1",
            0,
            0,
            100,
            800.0,
            -8.0,
            0.0,
            8.0,
            100.0,
            "RESERVE A SHARED DRIVEWAY PAE|NORTH NORHILL",
        ),
        # Opposing even-numbered blockface parcel across 60 ft Key St ROW (5,200 sq ft)
        _make_hcad_feature(
            "0621110000014",
            "1126 KEY ST",
            "OPPOSING NEIGHBOR BOB",
            "A1",
            1927,
            230000,
            275000,
            5200.0,
            0.0,
            -160.0,
            52.0,
            100.0,
            "LT 5 BLK 132|NORTH NORHILL",
        ),
    ]


def _install_hcad_blockface_route(page: Any, recorded_queries: list[dict[str, str]]) -> None:
    features = _build_ch42_hcad_fixture_features()
    by_hcad = {f["properties"]["HCAD_NUM"]: f for f in features}

    def _route_hcad(route: Any) -> None:
        url = route.request.url
        parsed = urllib.parse.urlsplit(url)
        params = dict(urllib.parse.parse_qsl(parsed.query))
        recorded_queries.append(params)

        where = params.get("where", "")
        if "HCAD_NUM=" in where:
            acct = where.split("HCAD_NUM='")[-1].split("'")[0]
            matched = [by_hcad[acct]] if acct in by_hcad else []
        else:
            matched = features

        route.fulfill(
            status=200,
            content_type="application/json; charset=utf-8",
            headers={"Access-Control-Allow-Origin": "*"},
            body=json.dumps({"type": "FeatureCollection", "features": matched}).encode("utf-8"),
        )

    page.route("**/public_query/MapServer/0/query*", _route_hcad)


def _wait_for_ch42_loaded(page: Any, seed_hcad: str, timeout_ms: int = 20000) -> None:
    page.wait_for_selector("#ch42-modal:not(.hidden)", timeout=timeout_ms)
    page.wait_for_function(
        """(expectedHcad) => {
            const body = document.getElementById('ch42-modal-body');
            if (!body) return false;
            const sheet = body.querySelector('.ch42-kit-sheet');
            return Boolean(
                sheet &&
                sheet.getAttribute('data-seed-hcad') === expectedHcad &&
                body.querySelectorAll('.ch42-inc-chk').length > 0
            );
        }""",
        arg=seed_hcad,
        timeout=timeout_ms,
    )


def test_it_e2e_1_scorecard_hub_families_sorts_search_and_flyto(
    guarded_page: Any,
    atlas_base_url: str,
    url_harvester: Any,
) -> None:
    page = guarded_page
    scorecard_fetches: list[str] = []
    page.on(
        "request",
        lambda req: scorecard_fetches.append(req.url)
        if "neighborhood_scorecards.json" in req.url
        else None,
    )

    page.goto(f"{atlas_base_url}/index.html", wait_until="domcontentloaded")
    _wait_for_atlas_ready(page)

    # Confirm neighborhood_scorecards.json is NOT eagerly downloaded on boot
    assert scorecard_fetches == [], f"Unexpected eager boot scorecard fetch: {scorecard_fetches}"

    page.click("#btn-open-scorecards-hub")
    page.wait_for_selector("#scorecard-modal:not(.hidden)", timeout=10000)
    page.wait_for_function(
        """() => {
            const badge = document.getElementById('scorecard-hub-count');
            return Boolean(badge && !badge.textContent.includes('Loading'));
        }""",
        timeout=15000,
    )
    assert len(scorecard_fetches) == 1

    # Verify all 9 boundary family tabs load exact JSON dataset counts without silent drops
    live_family_counts: dict[str, int] = page.evaluate(
        """async () => {
            const resp = await fetch('public/data/neighborhood_scorecards.json');
            const payload = await resp.json();
            return payload.family_counts || {};
        }"""
    )
    for fam_key, exact_n in EXACT_FAMILY_COUNTS.items():
        assert live_family_counts.get(fam_key) == exact_n
    for fam_key, min_n in MIN_FAMILY_COUNTS.items():
        assert int(live_family_counts.get(fam_key, 0)) >= min_n

    for fam_key in list(EXACT_FAMILY_COUNTS.keys()) + list(MIN_FAMILY_COUNTS.keys()):
        expected_total = int(live_family_counts[fam_key])
        page.click(f'#scorecard-family-tabs [data-sc-family="{fam_key}"]')
        count_txt = page.locator("#scorecard-hub-count").text_content() or ""
        assert f"{expected_total:,}" in count_txt, (
            f"Expected {expected_total:,} in #scorecard-hub-count for {fam_key}, got {count_txt!r}"
        )
        expected_dom_rows = min(expected_total, 400)
        assert page.locator("#scorecard-hub-tbody tr.scorecard-hub-row").count() == expected_dom_rows

    # Verify all 7 sort options execute cleanly and reorder rows deterministically
    page.click('#scorecard-family-tabs [data-sc-family="super_neighborhoods"]')
    seen_first_by_sort: dict[str, str] = {}
    for sort_val in [
        "pre1940",
        "unprotected_gap",
        "lowest_armor",
        "expiring",
        "llc_share",
        "demos_total",
        "name",
    ]:
        page.select_option("#scorecard-sort-select", value=sort_val)
        first_row_id = (
            page.locator("#scorecard-hub-tbody tr.scorecard-hub-row")
            .first.get_attribute("data-scorecard-id")
            or ""
        )
        assert first_row_id != ""
        seen_first_by_sort[sort_val] = first_row_id
    assert len(set(seen_first_by_sort.values())) >= 4, (
        f"Expected sort modes to produce distinct top boundaries: {seen_first_by_sort}"
    )

    # Search filtering across representative Houston neighborhoods and Council District C
    for fam_key, query, expected_sub in [
        ("historic_districts", "Norhill", "Norhill"),
        ("super_neighborhoods", "Montrose", "Montrose"),
        ("council_districts", "Panzarella", "City Council District C"),
        ("council_districts", "District C", "City Council District C"),
    ]:
        page.click(f'#scorecard-family-tabs [data-sc-family="{fam_key}"]')
        page.fill("#scorecard-search-input", query)
        tbody_txt = page.locator("#scorecard-hub-tbody").text_content() or ""
        assert expected_sub in tbody_txt, f"Search {query!r} missing {expected_sub!r}: {tbody_txt!r}"

    # Inspect Council District C scorecard detail sheet thoroughly
    page.locator('#scorecard-hub-tbody tr[data-scorecard-id="council_dist_c"]').click()
    page.wait_for_selector(
        '#scorecard-detail-pane [data-scorecard-key="council_districts:council_dist_c"]',
        timeout=10000,
    )
    detail_txt, detail_urls = url_harvester.harvest_text_and_urls(
        page.locator("#scorecard-detail-pane"), "scorecard:council_dist_c"
    )
    detail_low = detail_txt.lower()
    assert "Joe Panzarella" in detail_txt
    assert "Current term through January 2028" in detail_txt
    assert "832-393-3004" in detail_txt
    assert "districtc@houstontx.gov" in detail_txt
    assert "(832) 393-6637" in detail_txt
    assert "standing historic fabric" in detail_low
    assert "legal protection gap" in detail_low
    assert "ownership stability" in detail_low
    assert "2020-2026 teardown & infill velocity" in detail_low
    assert "action alert: chapter 42 ordinances reaching 20-year sunset" in detail_low
    assert "active preservation early-warning alerts inside boundary" in detail_low
    assert any("houstontx.gov/council/c" in u for u in detail_urls)
    assert page.locator("#scorecard-detail-pane .sc-bar-col").count() >= 10
    assert page.locator("#scorecard-detail-pane .sc-demo-col").count() == 7

    # Also harvest links across all 11 Council District detail sheets for IT-E2E-6 coverage
    page.fill("#scorecard-search-input", "")
    for row_idx in range(11):
        row_loc = page.locator("#scorecard-hub-tbody tr.scorecard-hub-row").nth(row_idx)
        sc_id = row_loc.get_attribute("data-scorecard-id") or f"idx_{row_idx}"
        row_loc.click()
        page.wait_for_selector(
            f'#scorecard-detail-pane [data-scorecard-key="council_districts:{sc_id}"]',
            timeout=10000,
        )
        url_harvester.harvest_from_locator(
            page.locator("#scorecard-detail-pane"), f"scorecard:{sc_id}"
        )

    # Re-select District C and click Focus on Map (.btn-scorecard-fly)
    page.locator('#scorecard-hub-tbody tr[data-scorecard-id="council_dist_c"]').click()
    page.wait_for_selector(
        '#scorecard-detail-pane [data-scorecard-key="council_districts:council_dist_c"]',
        timeout=10000,
    )
    page.click("#scorecard-detail-pane .btn-scorecard-fly")
    page.wait_for_selector("#scorecard-modal", state="hidden", timeout=5000)
    page.wait_for_selector("#inspector-drawer:not(.hidden)", timeout=10000)
    assert page.locator("#chk-layer-council-districts").is_checked()
    insp_txt = page.locator("#inspector-body").text_content() or ""
    assert "City Council District C" in insp_txt


def test_it_e2e_2_council_districts_layer_and_boundary_inspector_scorecard(
    guarded_page: Any,
    browser: Any,
    atlas_base_url: str,
    url_harvester: Any,
) -> None:
    page = guarded_page
    page.goto(f"{atlas_base_url}/index.html", wait_until="domcontentloaded")
    _wait_for_atlas_ready(page)

    # 1. Expand Category 3 accordion & toggle City Council Districts (11) in #overlay-layer-list
    landuse_details = page.locator('details[data-layer-group="landuse"]')
    if landuse_details.get_attribute("open") is None:
        landuse_details.locator("summary.layer-category-summary").click()
    chk = page.locator("#chk-layer-council-districts")
    assert not chk.is_checked()
    page.locator('.layer-item-row[data-layer-key="councilDistricts"] label.check-row').click()
    assert chk.is_checked()

    layer_state = page.evaluate(
        """() => {
            const map = window.atlasApp?.mapController?.map;
            return {
                hash: window.location.hash,
                fillVis: map?.getLayoutProperty?.('council-districts-fill', 'visibility') || null,
                lineVis: map?.getLayoutProperty?.('council-districts-line', 'visibility') || null,
                labelVis: map?.getLayoutProperty?.('council-districts-label', 'visibility') || null,
                srcCount: window.atlasApp?.mapController?.overlaysData?.council_districts?.features?.length || 0,
            };
        }"""
    )
    assert "council_districts" in layer_state["hash"]
    assert layer_state["srcCount"] == 11
    if layer_state["fillVis"] is not None:
        assert layer_state["fillVis"] == "visible"
        assert layer_state["lineVis"] == "visible"
        assert layer_state["labelVis"] == "visible"

    # 2. Verify inline Boundary Inspector scorecard card across Council Districts, Historic Districts,
    # Super Neighborhoods, and Potential Historic Districts
    sample_boundaries = page.evaluate(
        """() => {
            const ov = window.atlasApp.mapController.overlaysData;
            const pick = (layer, pred) => {
                const f = (ov[layer]?.features || []).find(pred) || ov[layer]?.features?.[0];
                return f ? { layer, id: String(f.properties.id || f.properties.name), name: String(f.properties.name || '') } : null;
            };
            return [
                { layer: 'council_districts', id: 'council_dist_c', name: 'City Council District C' },
                pick('historic_districts', f => /norhill/i.test(f.properties?.name || '')),
                pick('super_neighborhoods', f => /montrose/i.test(f.properties?.name || '')),
                pick('potential_historic_districts', f => /third ward/i.test(f.properties?.name || '')),
            ].filter(Boolean);
        }"""
    )
    assert len(sample_boundaries) == 4

    for bnd in sample_boundaries:
        page.evaluate(
            """async ({ layer, id }) => {
                window.atlasApp._lastHandledUrlKey = null;
                window.location.hash = `#bnd=${layer}:${encodeURIComponent(id)}&lat=29.76620&lng=-95.38050&z=15.20`;
                await window.atlasApp._handleUrlChange();
            }""",
            bnd,
        )
        page.wait_for_function(
            """(expectedLayer) => {
                const card = document.querySelector('#inspector-body #boundary-inline-scorecard-slot .boundary-scorecard-inline-card');
                const key = card?.getAttribute('data-scorecard-key') || '';
                return key.startsWith(`${expectedLayer}:`);
            }""",
            arg=bnd["layer"],
            timeout=20000,
        )
        slot_txt, _ = url_harvester.harvest_text_and_urls(
            page.locator("#inspector-body #boundary-inline-scorecard-slot"),
            f"inline_scorecard:{bnd['layer']}:{bnd['id']}",
        )
        slot_low = slot_txt.lower()
        assert "preservation scorecard & chapter 42 armor" in slot_low
        assert "pre-1940 buildings" in slot_low
        assert "ch. 42 protected res. lots" in slot_low

    # 3. Click Open Full Preservation Scorecard & verify transition + URL hash sync
    last_key = page.locator(
        "#inspector-body #boundary-inline-scorecard-slot .boundary-scorecard-inline-card"
    ).get_attribute("data-scorecard-key") or ""
    assert last_key.startswith("potential_historic_districts:")
    page.click("#boundary-inline-scorecard-slot .btn-open-full-scorecard")
    page.wait_for_selector("#scorecard-modal:not(.hidden)", timeout=10000)
    page.wait_for_selector(
        f'#scorecard-detail-pane [data-scorecard-key="{last_key}"]',
        timeout=15000,
    )
    assert f"scorecard={urllib.parse.quote(last_key, safe=':')}" in page.evaluate(
        "() => window.location.hash"
    ) or f"scorecard={urllib.parse.quote(last_key, safe='')}" in page.evaluate(
        "() => window.location.hash"
    )

    # 4. Verify graceful inline fallback when neighborhood_scorecards.json fails with HTTP 503 (DF-6)
    # Release primary page WebGL context first so xdist SwiftShader workers do not contend across 2 canvases
    page.goto("about:blank")
    ctx_fail = browser.new_context(viewport=DEFAULT_E2E_VIEWPORT)
    try:
        configure_fast_e2e_context(ctx_fail, atlas_base_url)
        page_fail = ctx_fail.new_page()
        p_errs: list[str] = []
        page_fail.on("pageerror", lambda exc: p_errs.append(str(exc)))
        page_fail.route(
            "**/public/data/neighborhood_scorecards.json*",
            lambda route: route.fulfill(status=503, content_type="text/plain", body=b"Offline"),
        )
        page_fail.goto(
            f"{atlas_base_url}/index.html#bnd=council_districts:council_dist_c",
            wait_until="domcontentloaded",
        )
        _wait_for_atlas_ready(page_fail, timeout_ms=45000)
        page_fail.wait_for_selector(
            "#boundary-inline-scorecard-slot .boundary-scorecard-inline-unavailable",
            timeout=10000,
        )
        assert "unavailable" in (
            page_fail.locator("#boundary-inline-scorecard-slot").text_content() or ""
        ).lower()
        assert not p_errs, f"Unhandled pageerror on simulated scorecard HTTP 503: {p_errs}"
    finally:
        ctx_fail.close()


def test_it_e2e_3_ch42_calculator_interactive_toggles_and_map_layers(
    guarded_page: Any,
    atlas_base_url: str,
    url_harvester: Any,
) -> None:
    page = guarded_page
    recorded_queries: list[dict[str, str]] = []
    _install_hcad_blockface_route(page, recorded_queries)

    page.goto(f"{atlas_base_url}/index.html#hcad=0621100000014", wait_until="domcontentloaded")
    _wait_for_atlas_ready(page)
    page.wait_for_selector("#inspector-drawer:not(.hidden)", timeout=10000)
    page.wait_for_selector("#btn-open-ch42-calculator", timeout=10000)

    # Launch Chapter 42 Block-Face Calculator directly from the Parcel Inspector CTA
    page.click("#btn-open-ch42-calculator")
    _wait_for_ch42_loaded(page, "0621100000014")

    # Verify outbound HCAD REST query parameter schema hygiene
    assert len(recorded_queries) >= 2
    for q in recorded_queries:
        assert q.get("f") == "geojson"
        assert q.get("outSR") == "4326"
        if "geometry" in q:
            assert q.get("inSR") == "4326"
            assert q.get("geometryType") == "esriGeometryEnvelope"
        out_fields = [tok.strip() for tok in q.get("outFields", "").split(",") if tok.strip()]
        assert out_fields
        for fld in out_fields:
            assert fld in _ALLOWED_HCAD_OUTFIELDS, f"Unverified HCAD outField token: {fld!r}"

    # Verify MapLibre ch42-blockface-src GeoJSON role styling and visibility
    map_info = page.evaluate(
        """() => {
            const map = window.atlasApp?.mapController?.map;
            const src = map?.getSource?.('ch42-blockface-src');
            const data = src?._data || src?.serialize?.()?.data || null;
            const roles = (data?.features || []).map(f => f.properties?.ch42_role);
            const allMatchRole = (data?.features || []).every(
                f => f.properties?.ch42_role && f.properties?.ch42_role === f.properties?.role
            );
            return {
                fillVis: map?.getLayoutProperty?.('ch42-blockface-fill', 'visibility') || null,
                lineVis: map?.getLayoutProperty?.('ch42-blockface-line', 'visibility') || null,
                fillPaint: JSON.stringify(map?.getPaintProperty?.('ch42-blockface-fill', 'fill-color') || null),
                featureCount: (data?.features || []).length,
                roles,
                allMatchRole,
            };
        }"""
    )
    assert map_info["featureCount"] == 8
    assert map_info["allMatchRole"] is True
    assert "seed" in map_info["roles"]
    assert "corner_candidate" in map_info["roles"]
    assert "common_reserve" in map_info["roles"]
    assert "opposing_candidate" in map_info["roles"]
    if map_info["fillVis"] is not None:
        assert map_info["fillVis"] == "visible"
        assert map_info["lineVis"] == "visible"
        assert "ch42_role" in map_info["fillPaint"]

    # Verify initial Norhill Historic District (60% threshold), statutory caveats, phone, & enacted ordinance banner
    kit_txt, kit_urls = url_harvester.harvest_text_and_urls(
        page.locator("#ch42-modal-body"), "modal:ch42_kit"
    )
    kit_low = kit_txt.lower()
    assert "coh historic district: 60% threshold" in kit_low
    assert "norhill historic district" in kit_low
    assert "SMLS Ord. 2024-0984" in kit_txt
    assert "Sec. 42-173(b)" in kit_txt
    assert "Sec. 42-171(a)(4)" in kit_txt
    assert "(832) 393-6637" in kit_txt
    assert "4 SFR lots of 5 qualifying denominator lots" in kit_txt
    assert "80%" in kit_txt
    assert any("houstontx.gov/planning/Min-Lot_Size-Min_Bldg_Line.html" in u for u in kit_urls)

    # 1. Toggle vacant lot prior-5-yr SFR override (.ch42-vac-sfr-chk on 0621100000098)
    page.locator('.ch42-vac-sfr-chk[data-hcad="0621100000098"]').check()
    txt_after_vac = page.locator("#ch42-modal-body").text_content() or ""
    assert "5 SFR lots of 5 qualifying denominator lots" in txt_after_vac
    assert "100%" in txt_after_vac
    assert "1 vacant/demolished excluded" in txt_after_vac

    # 2. Opt-in corner candidate lot (.ch42-inc-chk on 0621100000091) -> 6 lots totaling 28,000 sq ft
    page.locator('.ch42-inc-chk[data-hcad="0621100000091"]').check()
    txt_after_corner = page.locator("#ch42-modal-body").text_content() or ""
    assert "6 SFR lots of 6 qualifying denominator lots" in txt_after_corner
    assert "28,000 sq ft across 6 lots" in txt_after_corner

    # 3. Override building line setback manually (.ch42-sb-input on 0621100000016 -> 24.5 ft)
    sb_input = page.locator('.ch42-sb-input[data-hcad="0621100000016"]')
    sb_input.fill("24.5")
    sb_input.dispatch_event("change")
    assert page.locator('.ch42-sb-input[data-hcad="0621100000016"]').input_value() == "24.5"

    # 4. Toggle owner commitments (.ch42-sign-chk) across > 51% land area (> 14,280 sq ft of 28,000 sq ft)
    for acct in ["0621100000015", "0621100000014", "0621100000098"]:
        page.locator(f'.ch42-sign-chk[data-hcad="{acct}"]').check()
    txt_after_sign = page.locator("#ch42-modal-body").text_content() or ""
    assert "16,000 sq ft (57.1%)" in txt_after_sign
    assert "across 3 owners" in txt_after_sign
    assert page.locator("#ch42-modal-body .ch42-summary-grid .scorecard-stat-box").nth(3).evaluate(
        "el => el.classList.contains('is-pass')"
    )

    # 5. Toggle #chk-ch42-opposing -> syncs &ch42opp=1 into URL hash and adds opposing 5,200 sq ft lot
    page.locator("#chk-ch42-opposing").check()
    assert "ch42opp=1" in page.evaluate("() => window.location.hash")
    txt_after_opp = page.locator("#ch42-modal-body").text_content() or ""
    assert "33,200 sq ft across 7 lots" in txt_after_opp

    # 6. Close modal and confirm ch42-blockface-src clears cleanly and URL hash strips ch42 tokens
    page.click("#btn-close-ch42-modal")
    page.wait_for_selector("#ch42-modal", state="hidden", timeout=5000)
    cleared_state = page.evaluate(
        """() => {
            const map = window.atlasApp?.mapController?.map;
            const src = map?.getSource?.('ch42-blockface-src');
            const data = src?._data || src?.serialize?.()?.data || null;
            return {
                hash: window.location.hash,
                featureCount: (data?.features || []).length,
            };
        }"""
    )
    assert "ch42=" not in cleared_state["hash"]
    assert "ch42opp=" not in cleared_state["hash"]
    assert cleared_state["featureCount"] == 0


def test_it_e2e_4_mutual_css_print_exclusivity(
    guarded_page: Any,
    atlas_base_url: str,
) -> None:
    page = guarded_page
    recorded_queries: list[dict[str, str]] = []
    _install_hcad_blockface_route(page, recorded_queries)

    page.goto(f"{atlas_base_url}/index.html#hcad=0621100000014", wait_until="domcontentloaded")
    _wait_for_atlas_ready(page)
    page.wait_for_selector("#inspector-drawer:not(.hidden)", timeout=10000)

    def _measure_print_layout() -> dict[str, Any]:
        return page.evaluate(
            """() => {
                const probe = (id) => {
                    const el = document.getElementById(id);
                    if (!el) return { exists: false, display: 'none', width: 0, height: 0 };
                    const cs = window.getComputedStyle(el);
                    const r = el.getBoundingClientRect();
                    return {
                        exists: true,
                        hiddenClass: el.classList.contains('hidden'),
                        display: cs.display,
                        visibility: cs.visibility,
                        width: Math.round(r.width),
                        height: Math.round(r.height),
                    };
                };
                return {
                    map: probe('map'),
                    topbar: probe('atlas-topbar'),
                    sidebar: probe('sidebar'),
                    inspector: probe('inspector-drawer'),
                    dossier: probe('dossier-modal'),
                    scorecard: probe('scorecard-modal'),
                    ch42: probe('ch42-modal'),
                    ch42IncThDisplay: (() => {
                        const th = document.querySelector('.ch42-lot-table th.no-print');
                        return th ? window.getComputedStyle(th).display : 'none';
                    })(),
                };
            }"""
        )

    # Mode 1: Property Dossier modal open alone -> prints dossier only
    page.evaluate(
        """async () => {
            const feat = window.atlasApp.searchIndex.find(f => f.hcad_num === '0621100000014') || {
                hcad_num: '0621100000014',
                address: '1127 KEY ST',
                year_built: 1928,
            };
            await window.atlasApp.openPropertyDossierModal(feat);
        }"""
    )
    page.wait_for_selector("#dossier-modal:not(.hidden)", timeout=10000)

    page.emulate_media(media="print")
    m_dossier = _measure_print_layout()
    assert m_dossier["dossier"]["display"] != "none" and m_dossier["dossier"]["width"] > 200
    assert m_dossier["dossier"]["height"] > 200
    assert m_dossier["scorecard"]["display"] == "none"
    assert m_dossier["ch42"]["display"] == "none"
    assert m_dossier["map"]["display"] == "none", f"Map canvas leaked into dossier print: {m_dossier}"
    assert m_dossier["topbar"]["display"] == "none"
    assert m_dossier["inspector"]["display"] == "none"
    page.emulate_media(media="screen")

    # Mode 2: Open Scorecard modal WHILE dossier-modal is still mounted open behind it ->
    # strict mutual exclusivity MUST hide dossier-modal and print ONLY scorecard-modal!
    page.evaluate("() => window.atlasApp.openScorecardModal({ layer: 'council_districts', id: 'council_dist_c' })")
    page.wait_for_selector(
        '#scorecard-detail-pane [data-scorecard-key="council_districts:council_dist_c"]',
        timeout=15000,
    )
    assert page.evaluate(
        """() => Array.from(
            document.querySelectorAll('#scorecard-modal .print-only-inline, #scorecard-modal .print-only-block')
        ).every(el => window.getComputedStyle(el).display === 'none')"""
    ), "Print-only summary pill/block leaked onto interactive screen view"
    page.emulate_media(media="print")
    m_scorecard = _measure_print_layout()
    assert m_scorecard["scorecard"]["display"] != "none" and m_scorecard["scorecard"]["width"] > 200
    assert m_scorecard["scorecard"]["height"] > 200
    assert m_scorecard["dossier"]["display"] == "none", f"Sibling dossier leaked in print: {m_scorecard}"
    assert m_scorecard["ch42"]["display"] == "none"
    assert m_scorecard["map"]["display"] == "none", f"Map canvas leaked into scorecard print: {m_scorecard}"
    assert m_scorecard["topbar"]["display"] == "none"
    assert page.evaluate(
        """() => Array.from(document.querySelectorAll('#scorecard-modal .sc-print-overflow'))
            .every(el => window.getComputedStyle(el).display === 'none')"""
    ), ".sc-print-overflow failed to hide in print mode"
    assert page.evaluate(
        """() => Array.from(document.querySelectorAll('#scorecard-modal .print-only-inline, #scorecard-modal .print-only-block'))
            .every(el => window.getComputedStyle(el).display !== 'none')"""
    ), ".print-only-inline/.print-only-block failed to display in print mode"
    page.emulate_media(media="screen")

    # Mode 3: Open Chapter 42 modal WHILE both dossier-modal AND scorecard-modal remain open ->
    # highest-precedence ch42-modal prints alone while BOTH sibling modals hide!
    page.evaluate("() => window.atlasApp.openChapter42ModalForHcad('0621100000014')")
    _wait_for_ch42_loaded(page, "0621100000014")
    page.emulate_media(media="print")
    m_ch42 = _measure_print_layout()
    assert m_ch42["ch42"]["display"] != "none" and m_ch42["ch42"]["width"] > 200
    assert m_ch42["ch42"]["height"] > 200
    assert m_ch42["scorecard"]["display"] == "none", f"Sibling scorecard leaked in print: {m_ch42}"
    assert m_ch42["dossier"]["display"] == "none", f"Sibling dossier leaked in print: {m_ch42}"
    assert m_ch42["map"]["display"] == "none", f"Map canvas leaked into ch42 print: {m_ch42}"
    assert m_ch42["ch42IncThDisplay"] == "none", f"th.no-print leaked into ch42 print header: {m_ch42}"
    assert m_ch42["topbar"]["display"] == "none"
    ch42_print_state = page.evaluate(
        """() => {
            const omits = Array.from(document.querySelectorAll('#ch42-modal .ch42-print-omit'));
            const row = document.querySelector('#ch42-modal tr.ch42-lot-row:not(.ch42-print-omit):not(.is-cutoff-lot)');
            const parcelCell = row?.querySelector('td[data-label="Parcel"]');
            return {
                omitCount: omits.length,
                allOmitsHidden: omits.every(el => window.getComputedStyle(el).display === 'none'),
                rowDisplay: row ? window.getComputedStyle(row).display : '',
                parcelAlign: parcelCell ? window.getComputedStyle(parcelCell).textAlign : '',
            };
        }"""
    )
    assert ch42_print_state["omitCount"] >= 1 and ch42_print_state["allOmitsHidden"] is True
    assert ch42_print_state["rowDisplay"] == "table-row"
    assert ch42_print_state["parcelAlign"] == "left"
    page.emulate_media(media="screen")


def test_it_e2e_5_cold_boot_deeplinks_popstate_abort_and_escape_stack(
    browser: Any,
    atlas_base_url: str,
) -> None:
    ctx = browser.new_context(viewport=DEFAULT_E2E_VIEWPORT)
    try:
        configure_fast_e2e_context(ctx, atlas_base_url)
        page = ctx.new_page()
        page_errors, console_errors, local_http_errors = attach_error_monitors(page, atlas_base_url)
        recorded_queries: list[dict[str, str]] = []
        _install_hcad_blockface_route(page, recorded_queries)

        # 1. Cold-boot #scorecard=council_districts:council_dist_c
        page.goto(
            f"{atlas_base_url}/index.html#scorecard=council_districts:council_dist_c",
            wait_until="domcontentloaded",
        )
        _wait_for_atlas_ready(page)
        page.wait_for_selector(
            '#scorecard-detail-pane [data-scorecard-key="council_districts:council_dist_c"]',
            timeout=15000,
        )
        assert "Joe Panzarella" in (page.locator("#scorecard-detail-pane").text_content() or "")

        # Simulate map camera pan after load and verify #scorecard= persists in hash AND Share Modal URL
        page.evaluate(
            """() => {
                const map = window.atlasApp?.mapController?.map;
                if (map) {
                    map.jumpTo({ center: [-95.391, 29.744], zoom: 14.2 });
                    map.fire('moveend');
                }
            }"""
        )
        page.wait_for_function(
            """() => {
                const h = decodeURIComponent(window.location.hash || '');
                return h.includes('scorecard=council_districts:council_dist_c') &&
                       (!window.atlasApp?.mapController?.map || h.includes('29.744'));
            }""",
            timeout=5000,
        )
        copy_btn_url = (
            page.locator("#scorecard-detail-pane .btn-scorecard-copy-link").get_attribute(
                "data-share-url"
            )
            or ""
        )
        assert "scorecard=council_districts" in urllib.parse.unquote(copy_btn_url)

        # Verify Export Studio Council District entries resolve canonical council_dist_* AND legacy cd_* IDs,
        # and verify shipped COH Historic District export_cache loads 200 OK while unshipped boundaries emit zero HTTP 404s.
        export_check = page.evaluate(
            """async () => {
                window.atlasApp.openExportStudioModal();
                const opts = Array.from(
                    document.querySelectorAll('#select-export-target-area option')
                ).map(o => o.value).filter(v => v.startsWith('council_districts::'));
                const shippedDistrictLoaded = await window.atlasApp._ensureCachedExportFootprints({
                    layerKey: 'historic_districts',
                    id: 'Audubon Place Historic District',
                    name: 'Audubon Place Historic District',
                });
                const unshippedCouncilLoaded = await window.atlasApp._ensureCachedExportFootprints({
                    layerKey: 'council_districts',
                    id: 'council_dist_c',
                    name: 'District C (Abbie Kamin)',
                });
                const unshippedNeighborhoodLoaded = await window.atlasApp._ensureCachedExportFootprints({
                    layerKey: 'neighborhoods',
                    id: 'Montrose',
                    name: 'Montrose',
                });
                document.getElementById('export-studio-modal')?.classList.add('hidden');
                const byCanonical = window.atlasApp.mapController._findBoundaryEntry({
                    layerKey: 'council_districts',
                    id: 'council_dist_c',
                });
                const byLegacy = window.atlasApp.mapController._findBoundaryEntry({
                    layerKey: 'council_districts',
                    id: 'cd_c',
                });
                return {
                    opts,
                    canonicalId: byCanonical?.props?.id || '',
                    legacyId: byLegacy?.props?.id || '',
                    shippedSlugCount: window.atlasApp.constructor.SHIPPED_EXPORT_CACHE_SLUGS?.size || 0,
                    shippedDistrictLoaded,
                    unshippedCouncilLoaded,
                    unshippedNeighborhoodLoaded,
                };
            }"""
        )
        assert "council_districts::council_dist_c" in export_check["opts"]
        assert len(export_check["opts"]) >= 11
        assert export_check["canonicalId"] == "council_dist_c"
        assert export_check["legacyId"] == "council_dist_c"
        assert export_check["shippedSlugCount"] == 24
        assert export_check["shippedDistrictLoaded"] is True
        assert export_check["unshippedCouncilLoaded"] is False
        assert export_check["unshippedNeighborhoodLoaded"] is False

        page.evaluate("() => window.atlasApp.openShareModal?.() || document.getElementById('btn-open-share-modal')?.click()")
        page.wait_for_selector("#share-modal:not(.hidden)", timeout=5000)
        share_input_val = urllib.parse.unquote(page.locator("#share-url-input").input_value())
        assert "scorecard=council_districts:council_dist_c" in share_input_val
        page.click("#btn-close-share-modal")
        page.close()

        # 2. Cold-boot #ch42=0621100000014&ch42opp=1 in a fresh page
        page2 = ctx.new_page()
        p2_errs, c2_errs, h2_errs = attach_error_monitors(page2, atlas_base_url)
        _install_hcad_blockface_route(page2, recorded_queries)
        page2.goto(
            f"{atlas_base_url}/index.html#ch42=0621100000014&ch42opp=1",
            wait_until="domcontentloaded",
        )
        _wait_for_atlas_ready(page2, timeout_ms=45000)
        _wait_for_ch42_loaded(page2, "0621100000014")
        assert page2.locator("#chk-ch42-opposing").is_checked()
        ch42_share_url = urllib.parse.unquote(
            page2.locator("#btn-ch42-copy-link").get_attribute("data-share-url") or ""
        )
        assert "ch42=0621100000014" in ch42_share_url
        assert "ch42opp=1" in ch42_share_url

        # 3. History Back / Forward navigation across #bnd= -> #scorecard= -> #ch42=
        page2.click("#btn-close-ch42-modal")
        page2.wait_for_selector("#ch42-modal", state="hidden", timeout=5000)

        page2.goto(f"{atlas_base_url}/index.html#bnd=council_districts:council_dist_c")
        page2.wait_for_selector("#inspector-drawer:not(.hidden)", timeout=10000)
        page2.goto(f"{atlas_base_url}/index.html#scorecard=council_districts:council_dist_c")
        page2.wait_for_selector("#scorecard-modal:not(.hidden)", timeout=10000)
        page2.goto(f"{atlas_base_url}/index.html#ch42=0621100000014")
        _wait_for_ch42_loaded(page2, "0621100000014")

        page2.go_back()
        page2.wait_for_selector("#ch42-modal", state="hidden", timeout=10000)
        page2.wait_for_selector("#scorecard-modal:not(.hidden)", timeout=10000)

        page2.go_back()
        page2.wait_for_selector("#scorecard-modal", state="hidden", timeout=10000)
        page2.wait_for_selector("#inspector-drawer:not(.hidden)", timeout=10000)

        page2.go_forward()
        page2.wait_for_selector("#scorecard-modal:not(.hidden)", timeout=10000)

        # 4. AbortController race safety on rapid parcel switch:
        # Delay first seed parcel 0621100000015 response while immediately launching 0621100000014
        features = _build_ch42_hcad_fixture_features()
        by_hcad = {f["properties"]["HCAD_NUM"]: f for f in features}
        page2.unroute("**/public_query/MapServer/0/query*")

        release_stale_evt = threading.Event()
        stale_finished_evt = threading.Event()

        def _delayed_race_route(route: Any) -> None:
            parsed = urllib.parse.urlsplit(route.request.url)
            params = dict(urllib.parse.parse_qsl(parsed.query))
            where = params.get("where", "")
            is_stale_req = "0621100000015" in where
            if is_stale_req:
                release_stale_evt.wait(timeout=10.0)
                acct = "0621100000015"
                matched = [by_hcad[acct]]
            elif "HCAD_NUM=" in where:
                acct = where.split("HCAD_NUM='")[-1].split("'")[0]
                matched = [by_hcad[acct]] if acct in by_hcad else []
            else:
                matched = features
            try:
                route.fulfill(
                    status=200,
                    content_type="application/json; charset=utf-8",
                    headers={"Access-Control-Allow-Origin": "*"},
                    body=json.dumps({"type": "FeatureCollection", "features": matched}).encode("utf-8"),
                )
            except Exception:
                # Expected when Playwright/browser aborted the superseded request mid-flight
                pass
            finally:
                if is_stale_req:
                    stale_finished_evt.set()

        page2.route("**/public_query/MapServer/0/query*", _delayed_race_route)
        page2.evaluate(
            """() => {
                window.atlasApp.mapController.highlightBoundaryByIdOrName({
                    layerKey: 'council_districts',
                    id: 'council_dist_c',
                    name: 'City Council District C',
                    flyTo: false,
                    inspect: true,
                });
                window.atlasApp.openChapter42ModalForHcad('0621100000015');
                window.atlasApp.openChapter42ModalForHcad('0621100000014');
            }"""
        )
        _wait_for_ch42_loaded(page2, "0621100000014")
        release_stale_evt.set()
        stale_finished_evt.wait(timeout=5.0)
        page2.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        final_seed = page2.locator("#ch42-modal-body .ch42-kit-sheet").get_attribute("data-seed-hcad")
        assert final_seed == "0621100000014", f"Stale response overwrote active blockface: {final_seed}"

        # 5. Escape key modal stack priority: ch42-modal -> scorecard-modal -> inspector stays open
        assert not page2.locator("#ch42-modal").evaluate("el => el.classList.contains('hidden')")
        assert not page2.locator("#scorecard-modal").evaluate("el => el.classList.contains('hidden')")
        assert not page2.locator("#inspector-drawer").evaluate("el => el.classList.contains('hidden')")

        page2.keyboard.press("Escape")
        page2.wait_for_selector("#ch42-modal", state="hidden", timeout=5000)
        assert not page2.locator("#scorecard-modal").evaluate("el => el.classList.contains('hidden')")
        assert not page2.locator("#inspector-drawer").evaluate("el => el.classList.contains('hidden')")

        page2.keyboard.press("Escape")
        page2.wait_for_selector("#scorecard-modal", state="hidden", timeout=5000)
        assert not page2.locator("#inspector-drawer").evaluate("el => el.classList.contains('hidden')")

        assert not page_errors and not p2_errs, f"Page errors: {page_errors + p2_errs}"
        assert not local_http_errors and not h2_errs, f"HTTP errors: {local_http_errors + h2_errs}"
        assert not console_errors and not c2_errs, f"Console errors: {console_errors + c2_errs}"
    finally:
        ctx.close()


def test_it_e2e_7_responsive_modal_widths_mobile_tabs_and_ch42_cards(
    browser: Any,
    atlas_base_url: str,
) -> None:
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    try:
        configure_fast_e2e_context(ctx, atlas_base_url)
        page = ctx.new_page()
        page_errors, console_errors, local_http_errors = attach_error_monitors(page, atlas_base_url)
        recorded_queries: list[dict[str, str]] = []
        _install_hcad_blockface_route(page, recorded_queries)

        # 1. Wide Desktop (1440x900): modals expand across screen (never capped at 620px),
        #    both panes render side-by-side, and mobile-only controls remain hidden.
        page.goto(
            f"{atlas_base_url}/index.html#scorecard=council_districts:council_dist_c",
            wait_until="domcontentloaded",
        )
        _wait_for_atlas_ready(page)
        page.wait_for_selector(
            '#scorecard-detail-pane [data-scorecard-key="council_districts:council_dist_c"]',
            timeout=15000,
        )
        desktop_metrics = page.evaluate(
            """() => {
                const card = document.querySelector('.scorecard-modal-card');
                const tblWrap = document.querySelector('.scorecard-hub-table-wrap');
                const detail = document.getElementById('scorecard-detail-pane');
                const backBtn = document.querySelector('.btn-scorecard-back-table');
                const mobileTabs = document.getElementById('scorecard-mobile-pane-tabs');
                const statBoxes = Array.from(detail.querySelectorAll('.scorecard-stat-box'));
                return {
                    cardW: Math.round(card.getBoundingClientRect().width),
                    tblW: Math.round(tblWrap.getBoundingClientRect().width),
                    detailW: Math.round(detail.getBoundingClientRect().width),
                    backBtnDisplay: backBtn ? window.getComputedStyle(backBtn).display : 'none',
                    mobileTabsDisplay: mobileTabs ? window.getComputedStyle(mobileTabs).display : 'none',
                    minStatW: Math.min(...statBoxes.map(b => Math.round(b.getBoundingClientRect().width))),
                    statOverflow: statBoxes.some(b => b.scrollWidth > b.clientWidth + 2),
                };
            }"""
        )
        assert desktop_metrics["cardW"] >= 1300, f"Desktop scorecard modal crushed: {desktop_metrics}"
        assert desktop_metrics["tblW"] >= 450 and desktop_metrics["detailW"] >= 650
        assert desktop_metrics["backBtnDisplay"] == "none"
        assert desktop_metrics["mobileTabsDisplay"] == "none"
        assert desktop_metrics["minStatW"] >= 150
        assert desktop_metrics["statOverflow"] is False

        # Verify desktop Ch. 42 modal width and Property Inspector Ch. 42 CTA vertical layout
        page.evaluate("() => window.atlasApp.openChapter42ModalForHcad('0621100000014')")
        _wait_for_ch42_loaded(page, "0621100000014")
        ch42_card_w = page.locator(".ch42-modal-card").bounding_box()["width"]
        assert ch42_card_w >= 1250, f"Desktop Ch. 42 modal crushed to {ch42_card_w}px"
        page.evaluate("() => window.atlasApp.closeChapter42Modal()")
        page.evaluate("() => window.atlasApp.closeScorecardModal()")
        page.evaluate(
            """async () => {
                window.atlasApp._lastHandledUrlKey = null;
                window.location.hash = '#hcad=0621100000014';
                await window.atlasApp._handleUrlChange();
            }"""
        )
        page.wait_for_selector("#btn-open-ch42-calculator", timeout=10000)
        cta_metrics = page.evaluate(
            """() => {
                const btn = document.getElementById('btn-open-ch42-calculator');
                const cs = window.getComputedStyle(btn);
                return {
                    display: cs.display,
                    flexDirection: cs.flexDirection,
                    overflowPx: btn.scrollWidth - btn.clientWidth,
                };
            }"""
        )
        assert cta_metrics["display"] == "flex" and cta_metrics["flexDirection"] == "column"
        assert cta_metrics["overflowPx"] <= 2

        # 2. Split-Screen (885x1024) & Mobile (390x844): 2-tab pane switcher prevents crushed side-by-side layout
        for vw, vh in [(885, 1024), (390, 844)]:
            page.set_viewport_size({"width": vw, "height": vh})
            page.evaluate(
                "() => window.atlasApp.openScorecardModal({ layer: 'council_districts', id: 'council_dist_c' })"
            )
            page.wait_for_selector(
                '#scorecard-detail-pane [data-scorecard-key="council_districts:council_dist_c"]',
                timeout=10000,
            )
            detail_state = page.evaluate(
                """() => {
                    const modal = document.getElementById('scorecard-modal');
                    const tabs = document.getElementById('scorecard-mobile-pane-tabs');
                    const tblWrap = document.querySelector('.scorecard-hub-table-wrap');
                    const detail = document.getElementById('scorecard-detail-pane');
                    const brandDiv = modal.querySelector('.modal-brand-row > div:not(.ph-key-badge)');
                    const kicker = modal.querySelector('.modal-header .brand-kicker');
                    const closeBtn = document.getElementById('btn-close-scorecard-modal');
                    const statBoxes = Array.from(detail.querySelectorAll('.scorecard-stat-box'));
                    return {
                        pane: modal.dataset.mobilePane,
                        tabsDisplay: window.getComputedStyle(tabs).display,
                        tblDisplay: window.getComputedStyle(tblWrap).display,
                        detailW: Math.round(detail.getBoundingClientRect().width),
                        brandRight: Math.round(brandDiv.getBoundingClientRect().right),
                        closeLeft: Math.round(closeBtn.getBoundingClientRect().left),
                        kickerFontPx: parseFloat(window.getComputedStyle(kicker).fontSize),
                        statOverflow: statBoxes.some(b => b.scrollWidth > b.clientWidth + 2),
                    };
                }"""
            )
            assert detail_state["pane"] == "detail"
            assert detail_state["tabsDisplay"] == "flex"
            assert detail_state["tblDisplay"] == "none"
            assert detail_state["detailW"] >= vw - 80
            assert detail_state["brandRight"] <= detail_state["closeLeft"], (
                f"Header brand text overlapped close button at {vw}px: {detail_state}"
            )
            assert detail_state["kickerFontPx"] >= 9.0, f"Modal kicker shrunk below 9px at {vw}px: {detail_state}"
            assert detail_state["statOverflow"] is False

            # Switch to Ranking Table tab and verify full-width table, then click a row to auto-return to Detail
            page.click("#btn-sc-pane-table")
            assert page.locator("#scorecard-modal").get_attribute("data-mobile-pane") == "table"
            assert page.locator(".scorecard-hub-table-wrap").is_visible()
            assert not page.locator("#scorecard-detail-pane").is_visible()
            page.locator('#scorecard-hub-tbody tr[data-scorecard-id="council_dist_h"]').click()
            page.wait_for_selector(
                '#scorecard-detail-pane [data-scorecard-key="council_districts:council_dist_h"]',
                timeout=10000,
            )
            assert page.locator("#scorecard-modal").get_attribute("data-mobile-pane") == "detail"
            assert page.locator("#scorecard-detail-pane").is_visible()

        # 3. Mobile (390x844) Chapter 42 stacked field-walk parcel cards (2-column data-label + .ch42-cell-val)
        page.evaluate("() => window.atlasApp.closeScorecardModal()")
        page.evaluate("() => window.atlasApp.openChapter42ModalForHcad('0621100000014')")
        _wait_for_ch42_loaded(page, "0621100000014")
        ch42_mobile = page.evaluate(
            """() => {
                const cells = Array.from(document.querySelectorAll('#ch42-modal tr.ch42-lot-row:first-child td'));
                const parcelTd = cells.find(td => td.getAttribute('data-label') === 'Parcel');
                return {
                    theadDisplay: window.getComputedStyle(document.querySelector('#ch42-modal .ch42-lot-table thead')).display,
                    rowDisplay: window.getComputedStyle(document.querySelector('#ch42-modal tr.ch42-lot-row')).display,
                    labels: cells.map(td => td.getAttribute('data-label')),
                    allSingleValWrapper: cells.every(
                        td => td.children.length === 1 && td.firstElementChild.classList.contains('ch42-cell-val')
                    ),
                    parcelOverflowPx: parcelTd ? parcelTd.scrollWidth - parcelTd.clientWidth : 999,
                };
            }"""
        )
        assert ch42_mobile["theadDisplay"] == "none"
        assert ch42_mobile["rowDisplay"] == "block"
        assert "Parcel" in ch42_mobile["labels"] and "Lot Area (SMLS)" in ch42_mobile["labels"]
        assert ch42_mobile["allSingleValWrapper"] is True, f"Unwrapped td children broke mobile flex cards: {ch42_mobile}"
        assert ch42_mobile["parcelOverflowPx"] <= 2

        # Also verify narrow 390px print emulation NEVER leaks mobile block/flex cards into @media print
        page.emulate_media(media="print")
        ch42_narrow_print = page.evaluate(
            """() => {
                const row = document.querySelector('#ch42-modal tr.ch42-lot-row:not(.ch42-print-omit)');
                const td = row.querySelector('td[data-label="Parcel"]');
                return {
                    theadDisplay: window.getComputedStyle(document.querySelector('#ch42-modal .ch42-lot-table thead')).display,
                    rowDisplay: window.getComputedStyle(row).display,
                    tdDisplay: window.getComputedStyle(td).display,
                    tdAlign: window.getComputedStyle(td).textAlign,
                };
            }"""
        )
        page.emulate_media(media="screen")
        assert ch42_narrow_print == {
            "theadDisplay": "table-header-group",
            "rowDisplay": "table-row",
            "tdDisplay": "table-cell",
            "tdAlign": "left",
        }

        assert not page_errors, f"Page errors: {page_errors}"
        assert not local_http_errors, f"HTTP errors: {local_http_errors}"
        assert not console_errors, f"Console errors: {console_errors}"
    finally:
        ctx.close()
