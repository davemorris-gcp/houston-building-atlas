"""Playwright E2E tests for Milestone 4 Annual HCAD Appraisal Roll & Parcel Refresher (E2E-1..E2E-4)."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from conftest import REPO_ROOT, wait_for_atlas_ready

DATA_DIR = REPO_ROOT / "app" / "public" / "data"
MOCK_WEBHOOK_URL = "https://script.google.com/macros/s/MOCK_PH_M4_E2E/exec"


def _load_real_json(filename: str) -> dict[str, Any]:
    path = Path(DATA_DIR) / filename
    return json.loads(path.read_text(encoding="utf-8"))


def _install_roll_year_fixture_routes(
    page: Any,
    *,
    tax_year: int,
    extra_overrides: dict[str, dict[str, Any]] | None = None,
) -> None:
    """Intercepts stats_summary.json, pmtiles_manifest.json, optional curated_overrides.json, and live HCAD ArcGIS queries."""
    base_stats = _load_real_json("stats_summary.json")
    base_manifest = _load_real_json("pmtiles_manifest.json")

    def _mutate_payload(raw: dict[str, Any]) -> dict[str, Any]:
        payload = deepcopy(raw)
        payload["data_tax_year"] = int(tax_year)
        payload["latest_year"] = int(tax_year)
        dec_counts = dict(payload.get("decade_counts") or {})
        dec_counts["2020"] = int(dec_counts.get("2020", 0)) + 15
        if tax_year >= 2030:
            dec_counts["2030"] = 42
        payload["decade_counts"] = dec_counts
        if "total_buildings" in payload:
            payload["total_buildings"] = int(payload["total_buildings"]) + (57 if tax_year >= 2030 else 15)
        if "dated_buildings" in payload:
            payload["dated_buildings"] = payload.get("total_buildings", payload["dated_buildings"])
        return payload

    stats_body = json.dumps(_mutate_payload(base_stats), separators=(",", ":"))
    manifest_body = json.dumps(_mutate_payload(base_manifest), separators=(",", ":"))

    def _fulfill_json(route: Any, body: str) -> None:
        route.fulfill(
            status=200,
            content_type="application/json; charset=utf-8",
            headers={
                "Access-Control-Allow-Origin": "*",
                "Cache-Control": "no-store",
            },
            body=body,
        )

    page.route("**/public/data/stats_summary.json*", lambda route: _fulfill_json(route, stats_body))
    page.route("**/public/data/pmtiles_manifest.json*", lambda route: _fulfill_json(route, manifest_body))

    if extra_overrides:
        base_overrides = _load_real_json("curated_overrides.json")
        ov_bucket = base_overrides.setdefault("overrides", {})
        for key, val in extra_overrides.items():
            ov_bucket[key] = deepcopy(val)
        overrides_body = json.dumps(base_overrides, separators=(",", ":"))
        page.route("**/public/data/curated_overrides.json*", lambda route: _fulfill_json(route, overrides_body))

    def _handle_hcad_live(route: Any) -> None:
        url = route.request.url
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        where_raw = (qs.get("where") or [""])[0]
        acct = "0621100000014"
        if "HCAD_NUM='" in where_raw:
            acct = where_raw.split("HCAD_NUM='", 1)[1].split("'", 1)[0] or acct
        fmt = (qs.get("f") or ["json"])[0].lower()
        is_m4_synth = acct == "0621100000999"
        addr = "1129 KEY ST" if is_m4_synth else "1127 KEY ST"
        owner = "MILESTONE 4 TEST OWNER" if is_m4_synth else "KEY STREET ARCHIVAL TRUST"
        appr_val = 725000 if is_m4_synth else 615000
        yr_impr = tax_year if is_m4_synth else 1928
        if fmt == "geojson":
            payload: dict[str, Any] = {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {
                            "HCAD_NUM": acct,
                            "owner": owner,
                            "address": addr,
                            "city": "HOUSTON",
                            "zip": "77009",
                            "state_class": "A1",
                            "appr_val": appr_val,
                            "mkt_val": appr_val,
                            "impr_val": appr_val - 225000,
                            "land_val": 225000,
                            "Shape.STArea()": 5000,
                            "subdivision": "NORHILL",
                            "legal_lines": "LT 14 BLK 42\\nNORHILL",
                        },
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [
                                    [-95.38275, 29.79145],
                                    [-95.38255, 29.79145],
                                    [-95.38255, 29.79160],
                                    [-95.38275, 29.79160],
                                    [-95.38275, 29.79145],
                                ]
                            ],
                        },
                    }
                ],
            }
        else:
            payload = {
                "features": [
                    {
                        "attributes": {
                            "HCAD_NUM": acct,
                            "CurrOwner": owner,
                            "LocAddr": addr,
                            "city": "HOUSTON",
                            "zip": "77009",
                            "StClsCode": "A1",
                            "landuse": "1001",
                            "Grade": "B+",
                            "year_impr": yr_impr,
                            "bldg_sqft": 2480 if is_m4_synth else 1740,
                            "acreage": 0.1148,
                            "dscr": "NORHILL",
                        }
                    }
                ]
            }
        _fulfill_json(route, json.dumps(payload, separators=(",", ":")))

    page.route("**/*hcad.org/**", _handle_hcad_live)


def _install_mock_apps_script_webhook(page: Any) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []

    def _handle_route(route: Any) -> None:
        if route.request.method == "POST":
            body = json.loads(route.request.post_data or "{}")
            captured.append(body)
            route.fulfill(
                status=200,
                content_type="application/json; charset=utf-8",
                headers={"Access-Control-Allow-Origin": "*"},
                body=json.dumps({"ok": True, "status": "Pending", "authorized": False}),
            )
        else:
            route.fulfill(
                status=200,
                content_type="text/csv; charset=utf-8",
                headers={"Access-Control-Allow-Origin": "*"},
                body="hcad_num,status\n",
            )

    page.route(f"{MOCK_WEBHOOK_URL}*", _handle_route)
    return captured


def _select_1127_key_st_and_wait_for_live_badge(page: Any, expected_year: int) -> None:
    page.fill("#search-input", "1127 KEY ST")
    page.wait_for_function(
        """() => {
            const box = document.getElementById('search-results');
            return Boolean(
                box &&
                !box.classList.contains('hidden') &&
                (box.innerText || '').toUpperCase().includes('1127 KEY ST')
            );
        }""",
        timeout=10000,
    )
    page.evaluate(
        """() => {
            const items = Array.from(document.querySelectorAll('#search-results .search-result-item'));
            const hit = items.find((el) => (el.innerText || '').toUpperCase().includes('1127 KEY ST'));
            if (!hit) throw new Error('Missing 1127 KEY ST search result');
            hit.click();
            const map = window.atlasApp.mapController.map;
            map.stop();
            map.jumpTo({ center: [-95.3827, 29.7915], zoom: 17.2, pitch: 0, bearing: 0 });
        }"""
    )
    expected_badge = f"{expected_year} Roll"
    page.wait_for_function(
        """(needle) => {
            const badge = document.getElementById('hcad-live-status-badge');
            return Boolean(badge && (badge.textContent || '').includes(needle) && (badge.textContent || '').includes('Live'));
        }""",
        arg=expected_badge,
        timeout=10000,
    )


def test_e2e_1_dynamic_roll_year_2027_and_2030_boot_hydration_and_live_hcad_badge(
    guarded_page: Any,
    browser: Any,
    atlas_base_url: str,
) -> None:
    """E2E-1: Dynamic hydration of sliders, readouts, correction max/hint, decade options, and live HCAD badge for 2027 and 2030."""
    # Pass A: Simulated 2027 appraisal roll
    page = guarded_page
    _install_roll_year_fixture_routes(page, tax_year=2027)
    page.goto(f"{atlas_base_url}/index.html", wait_until="domcontentloaded")
    wait_for_atlas_ready(page)

    ui_2027 = page.evaluate(
        """() => ({
            minSliderMax: document.getElementById('slider-min-year')?.getAttribute('max'),
            maxSliderMax: document.getElementById('slider-max-year')?.getAttribute('max'),
            maxSliderVal: document.getElementById('slider-max-year')?.value,
            readout: (document.getElementById('year-range-readout')?.textContent || '').trim(),
            decadeValues: Array.from(document.querySelectorAll('#select-decade option')).map((o) => o.value),
            corrMax: document.getElementById('corr-suggested-year')?.getAttribute('max'),
            corrLabelTxt: (document.getElementById('lbl-corr-suggested-year')?.innerText || '').trim(),
            histDecades: Array.from(document.querySelectorAll('#decade-histogram button[data-decade]')).map((b) => b.getAttribute('data-decade')),
            stateMaxYear: window.atlasApp.filterStore.getState().maxYear,
        })"""
    )
    assert ui_2027["minSliderMax"] == "2027"
    assert ui_2027["maxSliderMax"] == "2027"
    assert ui_2027["maxSliderVal"] == "2027"
    assert ui_2027["stateMaxYear"] == 2027
    assert "1836" in ui_2027["readout"] and "2027" in ui_2027["readout"]
    assert "2020" in ui_2027["decadeValues"]
    assert "2030" not in ui_2027["decadeValues"]
    assert ui_2027["corrMax"] == "2027"
    assert "1836-2027" in ui_2027["corrLabelTxt"]
    assert "1836-2026" not in ui_2027["corrLabelTxt"]
    assert "2030" not in ui_2027["histDecades"]

    _select_1127_key_st_and_wait_for_live_badge(page, expected_year=2027)
    badge_2027 = (page.locator("#hcad-live-status-badge").text_content() or "").strip()
    assert "2027 Roll" in badge_2027 and "Live" in badge_2027

    # Pass B: Simulated 2030 appraisal roll (new decade boundary) after unloading Pass A WebGL context
    page.goto("about:blank")
    _install_roll_year_fixture_routes(page, tax_year=2030)
    page.goto(f"{atlas_base_url}/index.html", wait_until="domcontentloaded")
    wait_for_atlas_ready(page, timeout_ms=45000)

    ui_2030 = page.evaluate(
        """() => ({
            minSliderMax: document.getElementById('slider-min-year')?.getAttribute('max'),
            maxSliderMax: document.getElementById('slider-max-year')?.getAttribute('max'),
            maxSliderVal: document.getElementById('slider-max-year')?.value,
            readout: (document.getElementById('year-range-readout')?.textContent || '').trim(),
            decadeOptions: Array.from(document.querySelectorAll('#select-decade option')).map((o) => ({
                value: o.value,
                text: (o.textContent || '').trim(),
            })),
            corrMax: document.getElementById('corr-suggested-year')?.getAttribute('max'),
            corrLabelTxt: (document.getElementById('lbl-corr-suggested-year')?.innerText || '').trim(),
            histDecades: Array.from(document.querySelectorAll('#decade-histogram button[data-decade]')).map((b) => b.getAttribute('data-decade')),
        })"""
    )
    assert ui_2030["minSliderMax"] == "2030"
    assert ui_2030["maxSliderMax"] == "2030"
    assert ui_2030["maxSliderVal"] == "2030"
    assert "1836" in ui_2030["readout"] and "2030" in ui_2030["readout"]
    assert ui_2030["corrMax"] == "2030"
    assert "1836-2030" in ui_2030["corrLabelTxt"]
    assert "2030" in ui_2030["histDecades"]

    dec_vals = [o["value"] for o in ui_2030["decadeOptions"]]
    assert "2030" in dec_vals
    if "unknown" in dec_vals:
        assert dec_vals.index("2030") < dec_vals.index("unknown")
    opt_2030 = next(o for o in ui_2030["decadeOptions"] if o["value"] == "2030")
    assert "2030s" in opt_2030["text"]

    # Selecting 2030s filters minYear=2030..maxYear=2030 cleanly
    page.select_option("#select-decade", "2030")
    state_after_dec = page.evaluate("() => window.atlasApp.filterStore.getState()")
    assert state_after_dec["selectedDecade"] == "2030"
    assert state_after_dec["minYear"] == 2030
    assert state_after_dec["maxYear"] == 2030

    page.select_option("#select-decade", "all")
    _select_1127_key_st_and_wait_for_live_badge(page, expected_year=2030)
    badge_2030 = (page.locator("#hcad-live-status-badge").text_content() or "").strip()
    assert "2030 Roll" in badge_2030 and "Live" in badge_2030


def test_e2e_2_cold_boot_deeplinks_presets_stepper_timelapse_and_reset_2027(
    guarded_page: Any,
    atlas_base_url: str,
) -> None:
    """E2E-2: Cold-boot #maxY=2027 & #minY=2020&maxY=2027 retention, Share View Presets, stepper, time-lapse completion, and reset."""
    page = guarded_page
    _install_roll_year_fixture_routes(page, tax_year=2027)

    # 1. Cold boot with #maxY=2027 before async manifest resolves
    page.goto(f"{atlas_base_url}/index.html#maxY=2027", wait_until="domcontentloaded")
    wait_for_atlas_ready(page)
    cold_a = page.evaluate(
        """() => ({
            state: window.atlasApp.filterStore.getState(),
            sliderMaxVal: document.getElementById('slider-max-year')?.value,
            readout: (document.getElementById('year-range-readout')?.textContent || '').trim(),
        })"""
    )
    assert cold_a["state"]["minYear"] == 1836
    assert cold_a["state"]["maxYear"] == 2027
    assert cold_a["sliderMaxVal"] == "2027"
    assert "2027" in cold_a["readout"]

    # 2. Full cold reload into #minY=2020&maxY=2027
    page.goto("about:blank")
    page.goto(f"{atlas_base_url}/index.html#minY=2020&maxY=2027", wait_until="domcontentloaded")
    wait_for_atlas_ready(page)
    cold_b = page.evaluate(
        """() => ({
            state: window.atlasApp.filterStore.getState(),
            sliderMinVal: document.getElementById('slider-min-year')?.value,
            sliderMaxVal: document.getElementById('slider-max-year')?.value,
            readout: (document.getElementById('year-range-readout')?.textContent || '').trim(),
            hash: window.location.hash,
        })"""
    )
    assert cold_b["state"]["minYear"] == 2020
    assert cold_b["state"]["maxYear"] == 2027
    assert cold_b["sliderMinVal"] == "2020"
    assert cold_b["sliderMaxVal"] == "2027"
    assert "2020" in cold_b["readout"] and "2027" in cold_b["readout"]
    assert "minY=2020" in cold_b["hash"]

    # 3. Share View Presets seamlessly resolve top year 2027 via dynamic Proxy + UI preset buttons
    preset_audit = page.evaluate(
        """() => {
            window.atlasApp._renderSharePresetsGrid();
            const preBtn = document.querySelector('#share-presets-grid [data-preset-id="pre1940_buildings_only"]');
            const fullBtn = document.querySelector('#share-presets-grid [data-preset-id="full_atlas_default"]');
            const gbBtn = document.querySelector('#share-presets-grid [data-preset-id="good_brick_light"]');
            if (!preBtn || !fullBtn || !gbBtn) {
                throw new Error('Missing expected Share View Preset buttons in #share-presets-grid');
            }
            preBtn.click();
            const afterNarrow = window.atlasApp.filterStore.getState().maxYear;
            gbBtn.click();
            const afterGoodBrick = window.atlasApp.filterStore.getState().maxYear;
            fullBtn.click();
            const afterFull = window.atlasApp.filterStore.getState().maxYear;
            return { afterNarrow, afterGoodBrick, afterFull };
        }"""
    )
    assert preset_audit["afterNarrow"] == 1939
    assert preset_audit["afterGoodBrick"] == 2027
    assert preset_audit["afterFull"] == 2027

    # 4. #btn-step-next clamps smoothly through 2027
    page.evaluate(
        """() => {
            window.atlasApp.filterStore.setState({ minYear: 1836, maxYear: 2024, stepYears: 5, isPlaying: false });
        }"""
    )
    page.click("#btn-step-next")
    step_state = page.evaluate(
        """() => ({
            maxYear: window.atlasApp.filterStore.getState().maxYear,
            sliderMaxVal: document.getElementById('slider-max-year')?.value,
        })"""
    )
    assert step_state["maxYear"] == 2027
    assert step_state["sliderMaxVal"] == "2027"

    # 5. #btn-timelapse-play advances cleanly to 2027 and halts automatically
    page.evaluate(
        """() => {
            window.atlasApp.filterStore.setState({ minYear: 1836, maxYear: 2022, playSpeed: 5, isPlaying: false });
        }"""
    )
    page.click("#btn-timelapse-play")
    page.wait_for_function(
        """() => {
            const st = window.atlasApp.filterStore.getState();
            return st.isPlaying === false && st.maxYear === 2027;
        }""",
        timeout=5000,
    )
    assert page.input_value("#slider-max-year") == "2027"

    # 6. Narrow range, verify hash carries minY/maxY, then #btn-reset-filters restores 1836..2027 cleanly
    page.evaluate(
        """() => {
            window.atlasApp.filterStore.setState({ minYear: 1920, maxYear: 1985, selectedDecade: 'all', isPlaying: false });
        }"""
    )
    page.wait_for_function(
        "() => window.location.hash.includes('minY=1920') && window.location.hash.includes('maxY=1985')",
        timeout=5000,
    )
    page.click("#btn-reset-filters")
    reset_audit = page.evaluate(
        """() => ({
            minYear: window.atlasApp.filterStore.getState().minYear,
            maxYear: window.atlasApp.filterStore.getState().maxYear,
            sliderMaxVal: document.getElementById('slider-max-year')?.value,
            hash: window.location.hash,
        })"""
    )
    assert reset_audit["minYear"] == 1836
    assert reset_audit["maxYear"] == 2027
    assert reset_audit["sliderMaxVal"] == "2027"
    assert "maxY=1985" not in reset_audit["hash"]
    assert "maxY=2026" not in reset_audit["hash"]


def test_e2e_3_inspector_dossier_correction_validation_and_isolated_polygon_2027_building(
    guarded_page: Any,
    atlas_base_url: str,
) -> None:
    """E2E-3: Inspector & Archival Dossier (0 yrs old + 2027 Appraised Value), Correction modal (2027 valid), and custom isolated polygon stats on a 2027 building."""
    page = guarded_page
    synth_override = {
        "0621100000999": {
            "hcad_num": "0621100000999",
            "address": "1129 KEY ST",
            "landmark_name": "Key Street 2027 Infill Test House",
            "year_built": 2027,
            "decade": 2020,
            "year_source": "HCAD Real Property CAMA (2027 Roll)",
            "confidence": "High",
            "use_category": "Single-Family Residential",
            "bld_style": "Contemporary",
            "bld_area": 2480,
            "stories": 2,
            "height_m": 7.6,
            "owner": "MILESTONE 4 TEST OWNER",
            "neighborhood": "Norhill",
            "historic_district": "Norhill",
            "contributing": "Non-Contributing",
            "lon": -95.38258,
            "lat": 29.79152,
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [-95.38263, 29.79148],
                        [-95.38253, 29.79148],
                        [-95.38253, 29.79156],
                        [-95.38263, 29.79156],
                        [-95.38263, 29.79148],
                    ]
                ],
            },
        }
    }
    _install_roll_year_fixture_routes(page, tax_year=2027, extra_overrides=synth_override)
    webhook_posts = _install_mock_apps_script_webhook(page)

    page.goto(f"{atlas_base_url}/index.html", wait_until="domcontentloaded")
    wait_for_atlas_ready(page)

    # 1. Select the synthetic 2027 building and verify Property Inspector non-negative age + 2027 roll caption
    page.evaluate(
        """(wh) => {
            const app = window.atlasApp;
            const mc = app.mapController;
            if (mc.sheetSyncStatus) {
                mc.sheetSyncStatus.webhookUrl = wh;
            }
            mc.map.stop();
            mc.map.jumpTo({ center: [-95.38258, 29.79152], zoom: 17.5, pitch: 0, bearing: 0 });
            const hit = mc.selectFeatureByIdOrHcad({ hcadNum: '0621100000999' });
            if (!hit) {
                throw new Error('Synthetic 2027 curated override 0621100000999 was not indexed');
            }
        }""",
        MOCK_WEBHOOK_URL,
    )
    page.wait_for_function(
        """() => {
            const body = document.getElementById('inspector-body');
            const badge = document.getElementById('hcad-live-status-badge');
            const appr = document.getElementById('hcad-live-appr');
            return Boolean(
                body &&
                (body.innerText || '').includes('2027') &&
                badge &&
                (badge.textContent || '').includes('2027 Roll') &&
                appr &&
                (appr.textContent || '').includes('$725,000')
            );
        }""",
        timeout=10000,
    )
    inspector_txt = page.inner_text("#inspector-body")
    assert "Built 2027" in inspector_txt
    assert "0 yrs old" in inspector_txt
    assert "-1 yrs old" not in inspector_txt

    page.click('#inspector-body [data-inspector-tab="permits"]')
    permits_txt = page.inner_text("#inspector-body")
    assert "2004-2027" in permits_txt

    page.click('#inspector-body [data-inspector-tab="hcad"]')
    hcad_txt = page.inner_text("#inspector-body")
    assert "2027 Roll" in hcad_txt and "Live" in hcad_txt

    # 2. Open Printable Archival Dossier (#dossier-modal) and verify 2027 year seal + dynamic 2027 Appraised Value caption
    page.evaluate(
        """async () => {
            await window.atlasApp.openPropertyDossierModal(window.atlasApp.mapController.selectedFeatureProps);
        }"""
    )
    page.wait_for_selector("#dossier-modal:not(.hidden)", timeout=8000)
    dossier_txt = page.inner_text("#printable-dossier-sheet")
    assert "2027" in dossier_txt
    assert "21st Century" in dossier_txt
    assert "2027 Appraised Value" in dossier_txt
    assert "2026 Appraised Value" not in dossier_txt
    assert "$725,000" in dossier_txt
    assert "-1 yrs" not in dossier_txt
    page.evaluate("() => document.getElementById('dossier-modal')?.classList.add('hidden')")

    # 3. Open Suggest Correction modal and verify HTML5 + JS validation accepts 2027 and rejects 2028
    page.evaluate(
        """() => {
            window.atlasApp.openCorrectionModal(window.atlasApp.mapController.selectedFeatureProps);
        }"""
    )
    page.wait_for_selector("#correction-modal:not(.hidden)", timeout=5000)
    validity_audit = page.evaluate(
        """() => {
            const input = document.getElementById('corr-suggested-year');
            const maxAttr = input.getAttribute('max');
            input.value = '2027';
            const valid2027 = input.checkValidity();
            input.value = '2028';
            const valid2028 = input.checkValidity();
            return { maxAttr, valid2027, valid2028 };
        }"""
    )
    assert validity_audit == {"maxAttr": "2027", "valid2027": True, "valid2028": False}

    page.fill("#corr-suggested-year", "2028")
    page.fill("#corr-citation", "Attempted future year beyond roll.")
    page.click("#btn-submit-correction")
    fb_err = page.inner_text("#corr-submit-feedback")
    assert "1836" in fb_err and "2027" in fb_err
    assert len(webhook_posts) == 0

    page.fill("#corr-suggested-year", "2027")
    page.fill("#corr-citation", "Certificate of occupancy issued Feb 2027.")
    page.click("#btn-submit-correction")
    page.wait_for_function(
        "() => (document.getElementById('corr-submit-feedback')?.innerText || '').includes('Pending')",
        timeout=5000,
    )
    assert len(webhook_posts) == 1
    assert webhook_posts[-1]["suggested_year_built"] == 2027
    page.click("#btn-close-correction-modal")

    # 4. Evaluate Custom Area Polygon isolated stats & decade histogram over the 2027 footprint
    poly_stats = page.evaluate(
        """async () => {
            const app = window.atlasApp;
            const mc = app.mapController;
            const waitPaint = async () => {
                if (mc.map && typeof mc.map.triggerRepaint === 'function') mc.map.triggerRepaint();
                await new Promise((r) => setTimeout(r, 120));
                await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
            };
            const customRing = [
                [-95.38266, 29.79146],
                [-95.38250, 29.79146],
                [-95.38250, 29.79158],
                [-95.38266, 29.79158],
                [-95.38266, 29.79146],
            ];
            const customIso = {
                feature: {
                    type: 'Feature',
                    properties: { id: 'm4_custom_poly', name: 'Milestone 4 Custom Study Polygon' },
                    geometry: { type: 'Polygon', coordinates: [customRing] },
                },
                bbox: [-95.38266, 29.79146, -95.38250, 29.79158],
                centroid: [-95.38258, 29.79152],
                layerKey: 'custom_polygon',
                id: 'm4_custom_poly',
                name: 'Milestone 4 Custom Study Polygon',
                mode: 'contents',
            };
            mc.map.jumpTo({ center: [-95.38258, 29.79152], zoom: 17.6, pitch: 0, bearing: 0 });
            app.filterStore.setState({ minYear: 1836, maxYear: 2027, selectedDecade: 'all', isPlaying: false });
            mc.isolatedBoundary = customIso;
            await waitPaint();
            mc.computeViewportHistogram();
            const matchingFull2027 = app.lastViewportStats?.matchingFilterCount ?? 0;

            app.filterStore.setState({ minYear: 2027, maxYear: 2027, selectedDecade: 'all', isPlaying: false });
            mc.isolatedBoundary = customIso;
            await waitPaint();
            mc.computeViewportHistogram();
            const statsAt2027 = {
                matching: app.lastViewportStats?.matchingFilterCount ?? 0,
                inPolyTotal: app.lastViewportStats?.inViewportTotal ?? 0,
                dec2020: app.lastViewportStats?.decadeCounts?.['2020'] ?? 0,
                domCount: (document.getElementById('stat-viewport-count')?.textContent || '').trim(),
                hist2020Title: document.querySelector('#decade-histogram button[data-decade="2020"]')?.getAttribute('title') || '',
            };

            app.filterStore.setState({ minYear: 1836, maxYear: 2026, selectedDecade: 'all', isPlaying: false });
            mc.isolatedBoundary = customIso;
            await waitPaint();
            mc.computeViewportHistogram();
            const matchingCappedAt2026 = app.lastViewportStats?.matchingFilterCount ?? 0;

            app.filterStore.setState({ minYear: 1836, maxYear: 2027, selectedDecade: 'all', isPlaying: false });
            mc.clearIsolatedBoundary();
            return { matchingFull2027, statsAt2027, matchingCappedAt2026 };
        }"""
    )
    assert poly_stats["statsAt2027"]["inPolyTotal"] >= 1
    assert poly_stats["statsAt2027"]["matching"] == 1
    assert poly_stats["statsAt2027"]["dec2020"] >= 1
    assert poly_stats["statsAt2027"]["domCount"] == "1"
    assert "2020s:" in poly_stats["statsAt2027"]["hist2020Title"]
    assert poly_stats["matchingFull2027"] == poly_stats["matchingCappedAt2026"] + 1


def test_e2e_4_index_and_plat_monitor_network_cache_bust_parity_and_zero_errors(
    guarded_page: Any,
    atlas_base_url: str,
) -> None:
    """E2E-4: Capture local asset & PMTiles HTTP 206 requests across index.html and plat-monitor.html for ?v= parity and zero 404/500 errors."""
    page = guarded_page
    manifest = _load_real_json("pmtiles_manifest.json")
    active_cache_bust = str(manifest["cache_bust"])
    assert active_cache_bust

    tracked_exts = (".js", ".css", ".json", ".geojson", ".pmtiles")
    index_responses: list[dict[str, Any]] = []
    plat_responses: list[dict[str, Any]] = []
    current_bucket = index_responses

    def _on_response(resp: Any) -> None:
        url = resp.url
        if not url.startswith(atlas_base_url):
            return
        parsed = urlparse(url)
        if any(parsed.path.endswith(ext) for ext in tracked_exts) or parsed.path.endswith(".html"):
            qs = parse_qs(parsed.query)
            current_bucket.append(
                {
                    "url": url,
                    "path": parsed.path,
                    "status": resp.status,
                    "v": (qs.get("v") or [None])[0],
                }
            )

    page.on("response", _on_response)

    # 1. Load index.html and trigger lazy scorecard + address shard fetches + viewport pan for PMTiles 206 slices
    page.goto(f"{atlas_base_url}/index.html", wait_until="domcontentloaded")
    wait_for_atlas_ready(page)
    page.click("#btn-open-scorecards-hub")
    page.wait_for_selector("#scorecard-modal:not(.hidden)", timeout=10000)
    page.wait_for_function(
        """() => {
            const badge = document.getElementById('scorecard-hub-count');
            return Boolean(badge && (badge.textContent || '').includes('11'));
        }""",
        timeout=10000,
    )
    page.evaluate("() => document.getElementById('scorecard-modal')?.classList.add('hidden')")
    page.fill("#search-input", "1127 KEY ST")
    page.wait_for_function(
        """() => {
            const box = document.getElementById('search-results');
            return Boolean(box && !box.classList.contains('hidden') && (box.innerText || '').includes('1127'));
        }""",
        timeout=10000,
    )

    bad_index = [r for r in index_responses if r["status"] >= 400]
    assert bad_index == [], f"Unexpected 4xx/5xx responses on index.html: {bad_index}"

    pmtiles_resps = [r for r in index_responses if r["path"].endswith(".pmtiles")]
    assert len(pmtiles_resps) >= 1, "Expected at least one .pmtiles asset request on index.html"
    assert any(r["status"] == 206 for r in pmtiles_resps), f"Expected HTTP 206 range requests for .pmtiles, got {pmtiles_resps}"
    mismatched_pmtiles = [r for r in pmtiles_resps if r["v"] != active_cache_bust]
    assert mismatched_pmtiles == [], f"PMTiles cache-bust mismatch (expected {active_cache_bust}): {mismatched_pmtiles}"

    versioned_index = [r for r in index_responses if r["v"] is not None]
    assert len(versioned_index) >= 10
    drift_index = [r for r in versioned_index if r["v"] != active_cache_bust]
    assert drift_index == [], f"Cache-bust drift on index.html (expected {active_cache_bust}): {drift_index}"

    # 2. Load plat-monitor.html and verify identical ?v=<ACTIVE_CACHE_BUST> parity and zero 404/500 errors
    current_bucket = plat_responses
    page.goto(f"{atlas_base_url}/plat-monitor.html", wait_until="networkidle")
    page.wait_for_timeout(400)

    bad_plat = [r for r in plat_responses if r["status"] >= 400]
    assert bad_plat == [], f"Unexpected 4xx/5xx responses on plat-monitor.html: {bad_plat}"

    versioned_plat = [r for r in plat_responses if r["v"] is not None]
    assert len(versioned_plat) >= 6, f"Expected versioned JS/JSON requests on plat-monitor.html, got {plat_responses}"
    drift_plat = [r for r in versioned_plat if r["v"] != active_cache_bust]
    assert drift_plat == [], f"Cache-bust drift on plat-monitor.html (expected {active_cache_bust}): {drift_plat}"
