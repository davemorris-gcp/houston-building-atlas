"""Playwright E2E tests for Option 1 Demolished / Vacant Lot Footprint Suppression & Year=0 Workflow (IT-2)."""

from __future__ import annotations

import json
from typing import Any

from conftest import wait_for_atlas_ready

MOCK_WEBHOOK_URL = "https://script.google.com/macros/s/MOCK_PH_VACANT_E2E/exec"
DEMOLISHED_VACANT_ISSUE = "Building demolished - lot currently vacant"
DEMOLISHED_REPLACED_ISSUE = "Building demolished & replaced by newer structure"


def _install_mock_apps_script_webhook(page: Any) -> list[dict[str, Any]]:
    """Intercepts mock Google Apps Script webhook POSTs and returns captured JSON bodies."""
    captured: list[dict[str, Any]] = []

    def _handle_route(route: Any) -> None:
        if route.request.method == "POST":
            body = json.loads(route.request.post_data or "{}")
            captured.append(body)
            action = body.get("action")
            if action == "verify_admin":
                route.fulfill(
                    status=200,
                    content_type="application/json; charset=utf-8",
                    headers={"Access-Control-Allow-Origin": "*"},
                    body=json.dumps(
                        {
                            "ok": True,
                            "authorized": True,
                            "email": body.get("admin_email") or "curator@preservationhouston.org",
                        }
                    ),
                )
            else:
                is_admin = action == "admin_approve"
                route.fulfill(
                    status=200,
                    content_type="application/json; charset=utf-8",
                    headers={"Access-Control-Allow-Origin": "*"},
                    body=json.dumps(
                        {
                            "ok": True,
                            "status": "Approved" if is_admin else "Pending",
                            "authorized": is_admin,
                        }
                    ),
                )
        else:
            route.fulfill(
                status=200,
                content_type="text/csv; charset=utf-8",
                headers={"Access-Control-Allow-Origin": "*"},
                body="hcad_num,status\n",
            )

    page.context.route(f"{MOCK_WEBHOOK_URL}*", _handle_route)
    return captured


def _select_1127_key_st_and_open_correction_modal(page: Any) -> None:
    """Searches and selects curated 1127 KEY ST (0621100000014, Built 1928) and opens #correction-modal."""
    page.evaluate(
        """(wh) => {
            if (window.atlasApp?.mapController?.sheetSyncStatus) {
                window.atlasApp.mapController.sheetSyncStatus.webhookUrl = wh;
            }
        }""",
        MOCK_WEBHOOK_URL,
    )
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
    page.wait_for_function(
        """() => {
            const b = document.getElementById('inspector-body');
            return Boolean(
                b &&
                (b.innerText || '').toUpperCase().includes('1127 KEY ST') &&
                (b.innerText || '').includes('1928')
            );
        }""",
        timeout=12000,
    )
    page.evaluate(
        """() => {
            window.atlasApp.openCorrectionModal(window.atlasApp.mapController.selectedFeatureProps);
        }"""
    )
    page.wait_for_selector("#correction-modal:not(.hidden)", timeout=5000)


def _unlock_staff_admin_in_modal(page: Any) -> None:
    """Unlocks Staff Admin session inside #correction-modal using MOCK_WEBHOOK_URL."""
    page.click("#btn-toggle-admin-auth")
    page.fill("#admin-auth-email", "curator@preservationhouston.org")
    page.fill("#admin-auth-passkey", "ph-e2e-passkey")
    page.click("#btn-verify-admin-unlock")
    page.wait_for_function(
        """() => {
            const btn = document.getElementById('btn-admin-approve-direct');
            const details = document.getElementById('admin-sheet-details');
            return Boolean(
                btn &&
                !btn.classList.contains('hidden') &&
                details &&
                !details.classList.contains('hidden')
            );
        }""",
        timeout=5000,
    )
    page.fill("#admin-webhook-url", MOCK_WEBHOOK_URL)


def test_correction_modal_demolished_vacant_bidirectional_sync_default_notes_and_year_validation(
    guarded_page: Any,
    atlas_base_url: str,
) -> None:
    """IT-2 (1): Split dropdown options, bidirectional Year=0 sync, default notes, and year validation."""
    page = guarded_page
    webhook_posts = _install_mock_apps_script_webhook(page)
    page.goto(f"{atlas_base_url}/index.html", wait_until="domcontentloaded")
    wait_for_atlas_ready(page)

    _select_1127_key_st_and_open_correction_modal(page)

    # 1. Pre-filled curated year is "1928" and split dropdown options exist
    assert page.input_value("#corr-suggested-year") == "1928"
    dropdown_values = page.evaluate(
        """() => Array.from(document.querySelectorAll('#corr-footprint-issue option')).map((o) => o.value)"""
    )
    assert DEMOLISHED_VACANT_ISSUE in dropdown_values
    assert DEMOLISHED_REPLACED_ISSUE in dropdown_values

    # 2. Selecting vacant issue overwrites pre-filled "1928" with "0" and makes notes optional
    page.select_option("#corr-footprint-issue", DEMOLISHED_VACANT_ISSUE)
    assert page.input_value("#corr-suggested-year") == "0"
    assert page.evaluate("() => document.getElementById('corr-footprint-notes').required === false")

    # 3. Switching to replaced clears "0" to "" and makes notes required
    page.select_option("#corr-footprint-issue", DEMOLISHED_REPLACED_ISSUE)
    page.wait_for_timeout(120)
    assert page.input_value("#corr-suggested-year") == ""
    assert page.evaluate("() => document.getElementById('corr-footprint-notes').required === true")

    # 4. Typing "0" auto-selects vacant issue; typing a valid replacement year "2022" switches to replaced
    page.fill("#corr-suggested-year", "0")
    assert page.input_value("#corr-footprint-issue") == DEMOLISHED_VACANT_ISSUE

    page.fill("#corr-suggested-year", "2022")
    assert page.input_value("#corr-footprint-issue") == DEMOLISHED_REPLACED_ISSUE

    # 5. Validation rejects 1..1835 (1 and 1835) and >2026 (2027) without posting to webhook
    page.fill("#corr-footprint-notes", "Replaced by modern infill structure.")
    for invalid_year in ("1", "1835", "2027"):
        page.fill("#corr-suggested-year", invalid_year)
        page.click("#btn-submit-correction")
        fb_txt = page.inner_text("#corr-submit-feedback")
        assert "1836" in fb_txt and "2026" in fb_txt and "0" in fb_txt
    assert len(webhook_posts) == 0

    # 6. Submitting Year=0 with blank footprint notes succeeds and injects default note + vacant tag
    page.fill("#corr-footprint-notes", "")
    page.fill("#corr-citation", "")
    page.select_option("#corr-footprint-issue", DEMOLISHED_VACANT_ISSUE)
    assert page.input_value("#corr-suggested-year") == "0"
    page.click("#btn-submit-correction")
    page.wait_for_function("() => document.getElementById('corr-submit-feedback')?.innerText.includes('Pending')")

    assert len(webhook_posts) == 1
    vacant_post = webhook_posts[-1]
    assert vacant_post["status"] == "Pending"
    assert vacant_post["suppress_only"] is True
    assert vacant_post["suggested_year_built"] == 0
    assert "Building demolished; lot currently vacant." in vacant_post["source_citation"]
    assert "[DEMOLISHED / VACANT LOT - HIDE FOOTPRINT]" in vacant_post["source_citation"]

    # 7. Submitting valid replacement year (2022) with "Building demolished & replaced by newer structure"
    # produces suppress_only: false and suggested_year_built: 2022
    page.select_option("#corr-footprint-issue", DEMOLISHED_REPLACED_ISSUE)
    page.wait_for_timeout(120)
    page.fill("#corr-suggested-year", "2022")
    page.fill("#corr-footprint-notes", "Original bungalow demolished; 2-story infill built 2022.")
    page.click("#btn-submit-correction")
    page.wait_for_function(
        "() => document.getElementById('corr-submit-feedback')?.innerText.includes('2022')"
    )

    assert len(webhook_posts) == 2
    replaced_post = webhook_posts[-1]
    assert replaced_post["suppress_only"] is False
    assert replaced_post["suggested_year_built"] == 2022


def test_staff_admin_direct_approve_whole_parcel_vacant_suppression_and_revival(
    guarded_page: Any,
    atlas_base_url: str,
) -> None:
    """IT-2 (2): 1-click Staff Admin live whole-parcel footprint suppression, vacant parcel enrichment, and revival."""
    page = guarded_page
    webhook_posts = _install_mock_apps_script_webhook(page)
    page.goto(f"{atlas_base_url}/index.html", wait_until="domcontentloaded")
    wait_for_atlas_ready(page)

    _select_1127_key_st_and_open_correction_modal(page)
    _unlock_staff_admin_in_modal(page)

    page.select_option("#corr-footprint-issue", DEMOLISHED_VACANT_ISSUE)
    page.fill("#corr-footprint-notes", "")
    assert page.input_value("#corr-suggested-year") == "0"
    page.click("#btn-admin-approve-direct")

    page.wait_for_function(
        """() => {
            const ov = window.atlasApp?.mapController?.curatedOverrides?.['0621100000014'];
            return Boolean(ov && ov.suppress_only === true && ov.year_built === 0);
        }""",
        timeout=8000,
    )

    admin_post = [p for p in webhook_posts if p.get("action") == "admin_approve"][-1]
    assert admin_post["status"] == "Approved"
    assert admin_post["suppress_only"] is True
    assert admin_post["suggested_year_built"] == 0
    assert "Building demolished; lot currently vacant." in admin_post["source_citation"]

    page.click("#btn-close-correction-modal")

    # Verify whole-parcel suppression across map pools, selection, search (including address_shards), and parcels
    audit = page.evaluate(
        """async () => {
            const app = window.atlasApp;
            const mc = app.mapController;
            await app._runSearchQuery('1127 KEY ST');
            const searchTxt = (document.getElementById('search-results')?.innerText || '').toUpperCase();
            const stillInSearch = searchTxt.includes('0621100000014') || searchTxt.includes('1127 KEY ST');

            const bldCount = (mc.buildingsData || []).filter(
                (f) => String(f.properties?.hcad_num || '') === '0621100000014'
            ).length;
            const ovCount = (mc.overridesFC?.features || []).filter(
                (f) => String(f.properties?.hcad_num || '') === '0621100000014'
            ).length;
            const selectResult = mc.selectFeatureByIdOrHcad({ hcadNum: '0621100000014' });

            const parcelFeat = (mc.parcelsData || mc.pilotParcelsData || []).find(
                (f) => String(f.properties?.hcad_num || '') === '0621100000014'
            );
            if (parcelFeat) {
                app.renderInspectorDrawer(parcelFeat.properties);
                document.getElementById('inspector-drawer')?.classList.remove('hidden');
            }
            return {
                stillInSearch,
                bldCount,
                ovCount,
                selectResult,
                parcelProps: parcelFeat ? parcelFeat.properties : null,
                inspectorText: document.getElementById('inspector-body')?.innerText || '',
            };
        }"""
    )
    assert audit["stillInSearch"] is False
    assert audit["bldCount"] == 0
    assert audit["ovCount"] == 0
    assert audit["selectResult"] is False
    assert audit["parcelProps"] is not None
    assert audit["parcelProps"]["year_built"] == 0
    assert audit["parcelProps"]["decade"] == 0
    assert audit["parcelProps"]["use_category"] == "Vacant / Exempt"
    assert audit["parcelProps"]["is_demolished_vacant"] is True
    assert audit["parcelProps"]["suppress_only"] is False
    assert "Vacant Lot (Demolished)" in audit["inspectorText"]

    # Now select the vacant parcel on the map (setting selectedFeatureGeometry to the tax lot boundary),
    # reopen #correction-modal, and approve a replacement structure (Built 2024)
    page.evaluate(
        """() => {
            const mc = window.atlasApp.mapController;
            const parcelFeat = (mc.parcelsData || mc.pilotParcelsData || []).find(
                (f) => String(f.properties?.hcad_num || '') === '0621100000014'
            );
            mc.highlightAndInspectFeature(parcelFeat.properties, parcelFeat.geometry);
            window.atlasApp.openCorrectionModal(parcelFeat.properties);
        }"""
    )
    page.wait_for_selector("#correction-modal:not(.hidden)", timeout=5000)
    assert page.input_value("#corr-suggested-year") == "0"
    assert page.input_value("#corr-footprint-issue") == DEMOLISHED_VACANT_ISSUE

    page.select_option("#corr-footprint-issue", DEMOLISHED_REPLACED_ISSUE)
    page.wait_for_timeout(120)
    assert page.input_value("#corr-suggested-year") == ""
    page.fill("#corr-suggested-year", "2024")
    page.fill("#corr-footprint-notes", "New residence constructed on formerly vacant lot in 2024.")
    page.click("#btn-admin-approve-direct")

    page.wait_for_function(
        """() => {
            const ov = window.atlasApp?.mapController?.curatedOverrides?.['0621100000014'];
            return Boolean(ov && ov.suppress_only === false && ov.year_built === 2024);
        }""",
        timeout=8000,
    )
    revived_audit = page.evaluate(
        """async () => {
            const app = window.atlasApp;
            const mc = app.mapController;
            await app._runSearchQuery('1127 KEY ST');
            const searchTxt = (document.getElementById('search-results')?.innerText || '').toUpperCase();
            const parcelFeat = (mc.parcelsData || mc.pilotParcelsData || []).find(
                (f) => String(f.properties?.hcad_num || '') === '0621100000014'
            );
            const bldFeat = [...(mc.overridesFC?.features || []), ...(mc.buildingsData || [])].find(
                (f) => String(f.properties?.hcad_num || '') === '0621100000014'
            );
            return {
                revivedInSearch: searchTxt.includes('1127 KEY ST'),
                parcelProps: parcelFeat ? parcelFeat.properties : null,
                hasRevivedBuildingGeom: Boolean(bldFeat?.geometry),
                geomDiffersFromParcelLot:
                    JSON.stringify(bldFeat?.geometry) !== JSON.stringify(parcelFeat?.geometry),
            };
        }"""
    )
    assert revived_audit["revivedInSearch"] is True
    assert revived_audit["hasRevivedBuildingGeom"] is True
    assert revived_audit["geomDiffersFromParcelLot"] is True
    assert revived_audit["parcelProps"]["is_demolished_vacant"] is False
    assert revived_audit["parcelProps"]["year_built"] == 2024
    assert revived_audit["parcelProps"]["use_category"] != "Vacant / Exempt"


def test_staff_admin_direct_approve_sub_building_campus_isolation(
    guarded_page: Any,
    atlas_base_url: str,
) -> None:
    """IT-2 (3): #<slug> sub-building vacant suppression hides ONLY that sub-building and preserves campus siblings."""
    page = guarded_page
    _install_mock_apps_script_webhook(page)
    page.goto(f"{atlas_base_url}/index.html", wait_until="domcontentloaded")
    wait_for_atlas_ready(page)

    page.evaluate(
        """(wh) => {
            if (window.atlasApp?.mapController?.sheetSyncStatus) {
                window.atlasApp.mapController.sheetSyncStatus.webhookUrl = wh;
            }
            const map = window.atlasApp.mapController.map;
            map.stop();
            map.jumpTo({ center: [-95.60448, 29.88972], zoom: 15.3, pitch: 0, bearing: 0 });
        }""",
        MOCK_WEBHOOK_URL,
    )
    page.wait_for_function(
        """() => {
            const map = window.atlasApp?.mapController?.map;
            if (!map || !map.areTilesLoaded()) return false;
            const ids = new Set();
            for (const f of map.queryRenderedFeatures()) {
                if (String(f.properties?.hcad_num || '') === '1223580020001') {
                    ids.add(String(f.properties?.building_id || f.properties?.id || ''));
                }
            }
            return ids.size === 19;
        }""",
        timeout=20000,
    )

    campus_ids_before: list[str] = page.evaluate(
        """() => {
            const ids = new Set();
            for (const f of window.atlasApp.mapController.map.queryRenderedFeatures()) {
                if (String(f.properties?.hcad_num || '') === '1223580020001') {
                    ids.add(String(f.properties?.building_id || f.properties?.id || ''));
                }
            }
            return Array.from(ids).sort();
        }"""
    )
    target_sub_id = next(
        bid for bid in campus_ids_before if "#" in bid and bid != "1223580020001#cy_ridge_hs_main"
    )

    page.evaluate(
        """(subId) => {
            window.atlasApp.openCorrectionModal({
                id: subId,
                building_id: subId,
                hcad_num: '1223580020001',
                is_building_override: true,
                address: '7900 N ELDRIDGE PKWY',
                building_name: 'Demolished Campus Auxiliary Building',
                year_built: 2002,
            });
        }""",
        target_sub_id,
    )
    page.wait_for_selector("#correction-modal:not(.hidden)", timeout=5000)
    assert page.input_value("#corr-hcad-num") == target_sub_id

    _unlock_staff_admin_in_modal(page)
    page.select_option("#corr-footprint-issue", DEMOLISHED_VACANT_ISSUE)
    assert page.input_value("#corr-suggested-year") == "0"
    page.click("#btn-admin-approve-direct")

    page.wait_for_function(
        """(subId) => Boolean(window.atlasApp?.mapController?.curatedOverrides?.[subId]?.suppress_only)""",
        arg=target_sub_id,
        timeout=8000,
    )
    page.click("#btn-close-correction-modal")

    # Verify MapLibre filter suppresses ONLY target_sub_id while keeping all 18 sibling footprints rendered
    page.wait_for_function(
        """({ subId, expectedCount }) => {
            const ids = new Set();
            for (const f of window.atlasApp.mapController.map.queryRenderedFeatures()) {
                if (String(f.properties?.hcad_num || '') === '1223580020001') {
                    ids.add(String(f.properties?.building_id || f.properties?.id || ''));
                }
            }
            return !ids.has(subId) && ids.has('1223580020001#cy_ridge_hs_main') && ids.size === expectedCount;
        }""",
        arg={"subId": target_sub_id, "expectedCount": len(campus_ids_before) - 1},
        timeout=10000,
    )

    sibling_check = page.evaluate(
        """async (subId) => {
            const app = window.atlasApp;
            const mc = app.mapController;
            const suppressedSelect = mc.selectFeatureByIdOrHcad({
                featureId: subId,
                hcadNum: '1223580020001',
            });
            await app._runSearchQuery('Cypress Ridge High School');
            const searchTxt = (document.getElementById('search-results')?.innerText || '').toUpperCase();
            const siblingSelect = mc.selectFeatureByIdOrHcad({
                featureId: '1223580020001#cy_ridge_hs_main',
                hcadNum: '1223580020001',
            });
            return {
                suppressedSelect,
                siblingSearchable: searchTxt.includes('CYPRESS RIDGE'),
                siblingSelect,
            };
        }""",
        target_sub_id,
    )
    assert sibling_check["suppressedSelect"] is False
    assert sibling_check["siblingSearchable"] is True
    assert sibling_check["siblingSelect"] is True
