from __future__ import annotations

import csv
import io
import json
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

from conftest import wait_for_atlas_ready as _wait_for_atlas_ready


SYNTHETIC_OUTER_CORE_PARCELS_GEOJSON: dict[str, Any] = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {
                "HCAD_NUM": "0770010010001",
                "LocAddr": "1510 GARDENIA DR",
                "CurrOwner": "GARDEN OAKS HISTORIC TRUST",
                "ImprSqFt": 2180,
                "yr_impr": 1938,
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [-95.4331, 29.8262],
                        [-95.4326, 29.8262],
                        [-95.4326, 29.8266],
                        [-95.4331, 29.8266],
                        [-95.4331, 29.8262],
                    ]
                ],
            },
        },
        {
            "type": "Feature",
            "properties": {
                "HCAD_NUM": "0770010010002",
                "LocAddr": "1514 GARDENIA DR",
                "CurrOwner": "OAK FOREST VACANT LOT LLC",
                "ImprSqFt": 0,
                "yr_impr": 0,
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [-95.4325, 29.8262],
                        [-95.4320, 29.8262],
                        [-95.4320, 29.8266],
                        [-95.4325, 29.8266],
                        [-95.4325, 29.8262],
                    ]
                ],
            },
        },
        # Pilot Core overlap parcel (1127 Key St, 0621100000014) to verify deduplication
        {
            "type": "Feature",
            "properties": {
                "HCAD_NUM": "0621100000014",
                "LocAddr": "1127 KEY ST",
                "CurrOwner": "PILOT CORE DUPLICATE CHECK",
                "ImprSqFt": 1650,
                "yr_impr": 1928,
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [-95.3835, 29.7912],
                        [-95.3831, 29.7912],
                        [-95.3831, 29.7916],
                        [-95.3835, 29.7916],
                        [-95.3835, 29.7912],
                    ]
                ],
            },
        },
    ],
}


def _seed_staff_admin_session(page: Any) -> None:
    page.context.add_init_script(
        """
        sessionStorage.setItem('ph_atlas_admin_session', JSON.stringify({
            authorized: true,
            email: 'eardoin@preservationhouston.org'
        }));
        """
    )


def _build_synthetic_dondb_csv() -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(
        [
            "CaseNumber",
            "Parcel.ParcelID",
            "Parcel.ParcelName",
            "Parcel.Section",
            "Parcel.Area.Code",
            "ProcessState.Code",
        ]
    )
    # 1. Leading-zero-stripped 11-digit Landmark/Historic District parcel (112 Travis St / 0010160000011)
    writer.writerow(["DONDB900001", "10160000011", "112 TRAVIS ST, SUITE A", "SEC 1", "61", "LEGAL_REVIEW"])
    # 2. Leading-zero-stripped 12-digit North Norhill contributing parcel (1127 Key St / 0621100000014)
    writer.writerow(["DONDB900002", "621100000014", "1127 KEY ST, HOUSTON", "BLK 31", "22", "HEARING_PENDING"])
    # 3. Mid-century Tier 2 parcel (4710 W Alabama St / 0410170020150)
    writer.writerow(["DONDB900003", "410170020150", "4710 W ALABAMA ST", "RES A", "44", "OPEN"])
    # 4. Excel scientific-notation corrupted parcel ID -> must surface in Parcel ID Warnings tab
    writer.writerow(["DONDB900004", "1.523E+11", "2000 SMITH ST", "BLK 417", "61", "OPEN"])
    # 5. Non-historic / unmatched modern row
    writer.writerow(["DONDB900005", "9999999999999", "19999 OUTER SUBURB PKWY", "SEC 9", "99", "CLOSED"])
    return buf.getvalue().encode("utf-8")


def test_it1_and_it2_outer_core_hcad_parcels_and_vector_street_labels(
    guarded_page: Any,
    atlas_base_url: str,
) -> None:
    """Verify outer-core HCAD viewport parcel rendering, Pilot Core deduplication, and vector street labels across themes."""
    page = guarded_page
    hcad_parcel_queries: list[str] = []

    def _stub_hcad_parcels(route: Any) -> None:
        hcad_parcel_queries.append(route.request.url)
        route.fulfill(
            status=200,
            content_type="application/geo+json; charset=utf-8",
            headers={"Access-Control-Allow-Origin": "*"},
            body=json.dumps(SYNTHETIC_OUTER_CORE_PARCELS_GEOJSON),
        )

    page.route("**/public_query/MapServer/0/query**", _stub_hcad_parcels)

    page.goto(f"{atlas_base_url}/index.html#layers=permit_watch", wait_until="domcontentloaded")
    _wait_for_atlas_ready(page)

    # 1. Verify Buildings-only mode does not fetch dynamic outer-core parcels
    page.evaluate(
        """() => {
            window.atlasApp.filterStore.setState({ renderMode: 'buildings' });
            window.atlasApp.mapController.map.jumpTo({ center: [-95.4325, 29.8264], zoom: 16.0 });
        }"""
    )
    page.wait_for_timeout(350)
    assert len(hcad_parcel_queries) == 0

    # 2. Switch to Both mode at z >= 15.2 outside Pilot Core -> fetches and unifies dynamic parcels
    page.evaluate(
        """() => {
            window.atlasApp.filterStore.setState({ renderMode: 'both' });
            window.atlasApp.mapController._scheduleDynamicViewportParcels({ fetchNetwork: true, immediate: true });
        }"""
    )
    page.wait_for_function(
        """() => (window.atlasApp.mapController.parcelsData || []).some(
            (f) => f?.properties?.hcad_num === '0770010010001'
        )""",
        timeout=15000,
    )

    snap = page.evaluate(
        """() => {
            const mc = window.atlasApp.mapController;
            const allParcels = mc.parcelsData || [];
            const key1127 = allParcels.filter((f) => f?.properties?.hcad_num === '0621100000014');
            const builtOuter = allParcels.find((f) => f?.properties?.hcad_num === '0770010010001');
            const vacantOuter = allParcels.find((f) => f?.properties?.hcad_num === '0770010010002');
            return {
                key1127Count: key1127.length,
                key1127IsDynamic: Boolean(key1127[0]?.properties?.is_dynamic_hcad_parcel),
                builtYear: builtOuter?.properties?.year_built,
                builtDecade: builtOuter?.properties?.decade,
                vacantYear: vacantOuter?.properties?.year_built,
            };
        }"""
    )
    # Pilot Core 0621100000014 must appear at most once and keep its Pilot Core record (not duplicated)
    assert snap["key1127Count"] == 1
    assert snap["key1127IsDynamic"] is False
    assert snap["builtYear"] == 1938
    assert snap["builtDecade"] == 1930
    assert snap["vacantYear"] == 0

    # Switch to Parcels mode and verify decade/showUndated filter compilation + Inspector selection
    queries_before_parcels = len(hcad_parcel_queries)
    page.evaluate(
        """() => {
            const app = window.atlasApp;
            app.filterStore.setState({ renderMode: 'parcels', showUndated: false });
            const target = (app.mapController.parcelsData || []).find(
                (f) => f?.properties?.hcad_num === '0770010010001'
            );
            if (target && typeof app.openInspector === 'function') {
                app.openInspector(target.properties);
            } else if (target && typeof app.mapController._openInspectorForFeature === 'function') {
                app.mapController._openInspectorForFeature(target);
            }
        }"""
    )
    page.wait_for_timeout(300)
    # Cached viewport reuse: toggling renderMode/showUndated at identical bbox must not trigger unbounded fetches
    assert len(hcad_parcel_queries) <= queries_before_parcels + 1

    # 3. IT-2 & RP-3: Verify vector-street-labels z-ordering and theme-aware halo/visibility across basemaps
    theme_checks = page.evaluate(
        """() => {
            const app = window.atlasApp;
            const map = app.mapController.map;
            const styleLayers = map.getStyle().layers;
            const layerIds = styleLayers.map((l) => l.id);
            const labelLayer = map.getLayer('vector-street-labels');
            const labelIdx = layerIds.indexOf('vector-street-labels');
            const fillIndices = styleLayers
                .map((l, idx) => ((l.id.includes('parcels-fill') || l.id === 'buildings-2d') ? idx : -1))
                .filter((idx) => idx >= 0);
            const extrusionIndices = styleLayers
                .map((l, idx) => (l.type === 'fill-extrusion' ? idx : -1))
                .filter((idx) => idx >= 0);

            const byTheme = {};
            for (const bm of ['dark_archival', 'warm_parchment', 'satellite', 'solid_dark', 'solid_light']) {
                app.filterStore.setState({ basemap: bm });
                byTheme[bm] = {
                    visibility: map.getLayoutProperty('vector-street-labels', 'visibility'),
                    textColor: map.getPaintProperty('vector-street-labels', 'text-color'),
                    haloColor: map.getPaintProperty('vector-street-labels', 'text-halo-color'),
                };
            }
            app.filterStore.setState({ basemap: 'dark_archival' });
            return {
                exists: Boolean(labelLayer),
                sourceLayer: labelLayer?.sourceLayer || map.getStyle().layers[labelIdx]?.['source-layer'] || '',
                labelIdx,
                maxFillIdx: fillIndices.length ? Math.max(...fillIndices) : -1,
                minExtrusionIdx: extrusionIndices.length ? Math.min(...extrusionIndices) : 999999,
                byTheme,
            };
        }"""
    )
    assert theme_checks["exists"] is True
    assert theme_checks["sourceLayer"] == "transportation_name"
    assert theme_checks["labelIdx"] > theme_checks["maxFillIdx"]
    assert theme_checks["labelIdx"] < theme_checks["minExtrusionIdx"]
    assert theme_checks["byTheme"]["dark_archival"]["visibility"] == "visible"
    assert theme_checks["byTheme"]["warm_parchment"]["visibility"] == "visible"
    assert theme_checks["byTheme"]["satellite"]["visibility"] == "visible"
    assert (
        theme_checks["byTheme"]["dark_archival"]["textColor"]
        != theme_checks["byTheme"]["warm_parchment"]["textColor"]
    )
    assert theme_checks["byTheme"]["solid_dark"]["visibility"] == "none"
    assert theme_checks["byTheme"]["solid_light"]["visibility"] == "none"


def test_it3_and_it4_multicycle_plat_monitor_screenout_override_and_tier_sync(
    guarded_page: Any,
    atlas_base_url: str,
) -> None:
    """Verify multi-cycle agenda selector, Montrose Residences Notice PDF, Amerimex continuity, screen-out override, and explicit Tier confirmation."""
    page = guarded_page
    _seed_staff_admin_session(page)

    page.goto(f"{atlas_base_url}/plat-monitor.html", wait_until="domcontentloaded")
    page.wait_for_selector("#app:not(.hidden)", timeout=15000)
    page.wait_for_selector("#list .row", timeout=15000)

    # IT-5 header decoupling on Agenda mode: #agendaHeaderControls visible, #watchlistHeaderControls hidden
    assert page.locator("#agendaHeaderControls").is_visible()
    assert not page.locator("#watchlistHeaderControls").is_visible()

    # IT-3: Verify cycle selector options (2026-10-29, 2026-10-15, 2026-10-01, all)
    cycle_values = page.evaluate(
        "() => Array.from(document.querySelectorAll('#cycleSelect option')).map(o => o.value)"
    )
    for expected_cyc in ("2026-10-29", "2026-10-15", "2026-10-01", "all"):
        assert expected_cyc in cycle_values

    # Verify Tier 3 tab exists in #tabs and threat filter default selected option is non-blank
    tabs_text = page.locator("#tabs").inner_text()
    assert "Tier 3" in tabs_text
    f_threat_selected = page.evaluate(
        "() => document.querySelector('#fThreat')?.selectedOptions?.[0]?.textContent?.trim() || ''"
    )
    assert f_threat_selected == "All threat categories"

    # Select All Recent Cycles and verify Montrose Residences (2026-1585, fixed to Tier 3 / 1226-1230 W Pierce St)
    page.select_option("#cycleSelect", "all")
    page.locator("#tabs [data-g='all']").click()
    page.fill("#q", "Montrose Residences")
    page.wait_for_selector("#list .row:has-text('1226-1230 W Pierce')", timeout=5000)
    assert page.locator("#list .row").count() >= 1
    page.locator("#list .row").first.click()
    montrose_detail = page.locator("#detail").inner_text()
    assert "Nov 12, 2026" in montrose_detail or "11/12/2026" in montrose_detail
    assert page.locator("#detail a", has_text="Public Hearing Notice PDF").count() >= 1
    assert page.locator("#detail .links a", has_text="Plat PDF").count() == 0
    assert page.locator("#detail a", has_text="Open Submittal Plat PDF").count() == 0
    assert "Plat PDF not posted (Notice-only item)" in montrose_detail
    r_threat_selected = page.evaluate(
        "() => document.querySelector('#r-threat')?.selectedOptions?.[0]?.textContent?.trim() || ''"
    )
    assert r_threat_selected != ""

    # Verify Amerimex Motors (2026-1659) multi-HCAD & cross-cycle history
    page.fill("#q", "Amerimex Motors")
    page.wait_for_selector("#list .row:has-text('2026-1659')", timeout=5000)
    assert page.locator("#list .row").count() >= 1
    page.locator("#list .row").first.click()
    amerimex_detail = page.locator("#detail").inner_text()
    assert "2026-1659" in amerimex_detail
    assert "10/15/2026" in amerimex_detail or "2026-10-15" in amerimex_detail

    # Verify Screened out tab displays screen-out reason banner + manual Override button
    page.select_option("#cycleSelect", "2026-10-29")
    page.fill("#q", "")
    page.locator("#tabs button, #tabs .tab", has_text="Screened out").first.click()
    page.wait_for_selector("#list .row", timeout=10000)
    page.locator("#list .row").first.click()
    assert page.locator("#overrideScreenOutBtn").count() == 1
    page.click("#overrideScreenOutBtn")
    detail_after_override = page.locator("#detail").inner_text()
    assert (
        "Screen-Out Overridden" in detail_after_override
        or "Restore Pass 1 Screen-Out" in detail_after_override
    )

    # IT-4: Explicit Confirm Tier & Add to Watchlist workflow + two-way Tier 3 downgrade soft-archive vs hard-remove
    page.select_option("#cycleSelect", "2026-10-15")
    page.locator("#tabs button, #tabs .tab", has_text="To check").first.click()
    page.wait_for_selector("#list .row", timeout=10000)

    # Pick an unconfirmed item not currently on the Watchlist
    target_occ = page.evaluate(
        """() => {
            const rows = Array.from(document.querySelectorAll('#list .row'));
            for (const row of rows) {
                if (!row.innerText.includes('Montrose Residences') && !row.innerText.includes('801 McGowen')) {
                    row.click();
                    const btn = document.getElementById('confirmTierWatchBtn');
                    if (btn && btn.innerText.includes('Confirm Tier')) {
                        return btn.dataset.occ || '';
                    }
                    const t2Radio = document.querySelector('input[name="tier"][value="2"]');
                    if (t2Radio) {
                        t2Radio.click();
                        const btnAfter = document.getElementById('confirmTierWatchBtn');
                        if (btnAfter && btnAfter.innerText.includes('Confirm Tier')) {
                            return btnAfter.dataset.occ || '';
                        }
                    }
                }
            }
            return '';
        }"""
    )
    assert target_occ != ""
    initial_wl_count = int(page.locator("#modeWatchlistCount").inner_text().strip())

    # Clicking Tier 2 radio alone must NOT auto-add to Watchlist before clicking #confirmTierWatchBtn
    assert int(page.locator("#modeWatchlistCount").inner_text().strip()) == initial_wl_count

    # Click explicit Confirm Tier & Add to Watchlist -> Watchlist count increases by 1
    page.click("#confirmTierWatchBtn")
    assert int(page.locator("#modeWatchlistCount").inner_text().strip()) == initial_wl_count + 1

    # Untouched plat-* dossier downgraded to Tier 3 -> moves out of To check into Tier 3 tab, is removed cleanly from active Watchlist, and does NOT resurrect on cloud sync / reload
    target_app = target_occ.split("::", 1)[1] if "::" in target_occ else target_occ
    stale_cloud_csv = (
        "timestamp,hcad_num,source_type,source_citation,verified_by,status\n"
        f'2026-10-20T10:00:00Z,WATCH_PROP:plat-{target_app},PROPERTY_WATCHLIST_UPDATE,'
        f'"{{\\"id\\":\\"plat-{target_app}\\",\\"appNo\\":\\"{target_app}\\",\\"tier\\":\\"2\\",\\"tierConfirmed\\":true,\\"updatedAt\\":\\"2026-10-20T10:00:00Z\\"}}",PH Staff,approved\n'
    )
    page.route(
        "**/spreadsheets/d/**/pub*",
        lambda route: route.fulfill(status=200, content_type="text/csv", body=stale_cloud_csv),
    )
    page.locator('input[name="tier"][value="3"]').click()
    if page.locator("#confirmTier3Btn").count() > 0:
        page.click("#confirmTier3Btn")
    assert int(page.locator("#modeWatchlistCount").inner_text().strip()) == initial_wl_count
    assert page.locator(f'#list .row[data-occ="{target_occ}"]').count() == 0

    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("#app:not(.hidden)", timeout=15000)
    page.wait_for_selector("#list .row", timeout=15000)
    assert int(page.locator("#modeWatchlistCount").inner_text().strip()) == initial_wl_count
    page.unroute("**/spreadsheets/d/**/pub*")

    # Switch to Tier 3 tab, re-select target_occ, promote again, add human research, then downgrade to Tier 3 -> soft-archived ("Archived (Tier 3)")
    page.select_option("#cycleSelect", "2026-10-15")
    page.locator('#tabs [data-g="t3"]').click()
    target_row_t3 = page.locator(f'#list .row[data-occ="{target_occ}"]')
    assert target_row_t3.count() == 1
    target_row_t3.click()
    page.locator('input[name="tier"][value="1"]').click()
    # In Tier 3 tab, changing tier to 1 moves target_occ out of Tier 3; switch to Tier 1 tab and re-select target_occ to click #confirmTierWatchBtn
    page.locator('#tabs [data-g="t1"]').click()
    page.locator(f'#list .row[data-occ="{target_occ}"]').click()
    page.click("#confirmTierWatchBtn")
    assert int(page.locator("#modeWatchlistCount").inner_text().strip()) == initial_wl_count + 1

    page.click("#jumpWatchDossierBtn")
    page.fill("#snowIn-entities", "Preservation Test Holdings LLC")
    page.click('[data-add-term-cat="entities"]')
    page.click("#jumpBackToPlatItemBtn")

    page.locator('#tabs [data-g="t1"]').click()
    page.locator(f'#list .row[data-occ="{target_occ}"]').click()
    page.locator('input[name="tier"][value="3"]').click()
    page.locator('#tabs [data-g="t3"]').click()
    page.locator(f'#list .row[data-occ="{target_occ}"]').click()
    if page.locator("#confirmTier3Btn").count() > 0:
        page.click("#confirmTier3Btn")

    # Active Watchlist count returns to initial_wl_count, while Archived (Tier 3) preserves the researched dossier
    assert int(page.locator("#modeWatchlistCount").inner_text().strip()) == initial_wl_count
    page.click("#modeWatchlistBtn")
    page.select_option("#wlStatusFilter", "Archived (Tier 3)")
    assert page.locator("#list .row").count() >= 1
    page.locator(f'#list .row[data-watch-id="plat-{target_app}"]').click()
    assert "Preservation Test Holdings LLC" in page.locator("#detail").inner_text()


def test_it5_it6_and_it7_watchlist_header_editor_snowball_checklist_and_exports(
    guarded_page: Any,
    atlas_base_url: str,
) -> None:
    """Verify Watchlist header decoupling, inline property editor persistence, snowball term pills/deletion, checklist UX, and HTML/CSV/DOCX exports."""
    page = guarded_page
    _seed_staff_admin_session(page)

    page.goto(f"{atlas_base_url}/plat-monitor.html", wait_until="domcontentloaded")
    page.wait_for_selector("#modeWatchlistBtn", state="visible", timeout=15000)
    page.click("#modeWatchlistBtn")
    page.wait_for_selector("#list .row", timeout=15000)

    # IT-5: Header decoupling, non-blank threat dropdown default, & Street-Address-first Tier-1-sorted sidebar
    assert not page.locator("#agendaHeaderControls").is_visible()
    assert page.locator("#watchlistHeaderControls").is_visible()
    wl_threat_selected = page.evaluate(
        "() => document.querySelector('#wlThreatFilter')?.selectedOptions?.[0]?.textContent?.trim() || ''"
    )
    assert wl_threat_selected == "All Threat Categories"
    first_card_txt = page.locator("#list .row").first.inner_text()
    assert "2000 Smith" in first_card_txt

    # Select Kirby Mansion (2000 Smith St.)
    page.locator("#list .row", has_text="2000 Smith").first.click()
    assert "2000 Smith" in (page.locator("#detail h2").text_content() or "")

    # Inline Edit Property Details persistence across reload
    page.click("#toggleEditWatchPropBtn")
    page.fill("#we-legal", "TRS 1-16 BLK 417 SSBB")
    page.fill("#we-design", "Heights Engineering PLLC")
    page.select_option("#we-threat", "Significant Building Threatened")
    page.click("#saveEditWatchPropBtn")
    assert "TRS 1-16 BLK 417 SSBB" in page.locator("#detail").inner_text()
    assert "Heights Engineering PLLC" in page.locator("#detail").inner_text()

    # IT-6: Snowball Search-Term Discovery Panel add, portal pill propagation, zero address fallthrough, and non-resurrection
    snowball_txt = page.locator("#snowballCard").inner_text()
    for seeded_term in ("Smith St C Store LLC", "Brake Time", "Omair Bashir", "530 Pierce"):
        assert seeded_term in snowball_txt

    page.fill("#snowIn-entities", "Kirby Block Holdings LLC")
    page.click('[data-add-term-cat="entities"]')
    assert "Kirby Block Holdings LLC" in page.locator("#snowballCard").inner_text()

    # Pass #2 F2-11: Property Watchlist must omit extra copy buttons while preserving direct portal links (especially chk-05) and workflow modals
    pill_audit = page.evaluate(
        """() => {
            return {
                totalCopyBtns: document.querySelectorAll('#detail [data-copy-text]').length,
                chk05Links: Array.from(document.querySelectorAll('tr[data-row-id="chk-05"] a[href]')).map(a => a.getAttribute('href') || ''),
                hasGovQaAction: document.querySelectorAll('tr[data-row-id="chk-08"] [data-pill-action="open-govqa-modal"]').length,
                hasDondbAction: document.querySelectorAll('tr[data-row-id="chk-13"] [data-pill-action="open-dondb-modal"]').length,
            };
        }"""
    )
    assert pill_audit["totalCopyBtns"] == 0, f"Expected 0 Watchlist copy buttons after F2-11 cleanup, got {pill_audit['totalCopyBtns']}"
    assert any("search.hcad.org" in h for h in pill_audit["chk05Links"])
    assert any("arcweb.hcad.org/parcel-viewer-v2.0" in h for h in pill_audit["chk05Links"])
    assert pill_audit["hasGovQaAction"] == 1
    assert pill_audit["hasDondbAction"] == 1

    # Delete a snowball chip and reload -> verify neither deleted chip nor reverted editor fields resurrect
    page.locator(
        '#snowballCard button.rm[data-rm-term-cat="entities"][data-rm-term-val="Kirby Block Holdings LLC"]'
    ).click()
    assert "Kirby Block Holdings LLC" not in page.locator("#snowballCard").inner_text()

    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("#modeWatchlistBtn", state="visible", timeout=15000)
    page.click("#modeWatchlistBtn")
    page.locator("#list .row", has_text="2000 Smith").first.click()
    assert "TRS 1-16 BLK 417 SSBB" in page.locator("#detail").inner_text()
    assert "Heights Engineering PLLC" in page.locator("#detail").inner_text()
    assert "Kirby Block Holdings LLC" not in page.locator("#snowballCard").inner_text()

    # IT-7: 15-point checklist UX - single Mark Checked button per row, To Do -> In Progress, muted Complete/N/A rows, URL sanitization
    assert page.locator('tr[data-row-id="chk-02"] [data-quick-check-id="chk-02"]').count() == 1
    page.select_option('select[data-wl-row-id="chk-02"]', "To Do")
    page.click('[data-quick-check-id="chk-02"]')
    assert page.locator('select[data-wl-row-id="chk-02"]').input_value() == "In Progress"

    page.select_option('select[data-wl-row-id="chk-12"]', "N/A")
    assert "wl-row-done" in (page.locator('tr[data-row-id="chk-12"]').get_attribute("class") or "")
    page.select_option('select[data-wl-row-id="chk-11"]', "Complete")
    assert "wl-row-done" in (page.locator('tr[data-row-id="chk-11"]').get_attribute("class") or "")

    # Also resolve a Flagged automated row (chk-10) to Complete and verify persistence across reload
    page.select_option('select[data-wl-row-id="chk-10"]', "Complete")
    assert "wl-row-done" in (page.locator('tr[data-row-id="chk-10"]').get_attribute("class") or "")
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("#modeWatchlistBtn", state="visible", timeout=15000)
    page.click("#modeWatchlistBtn")
    page.locator("#list .row", has_text="2000 Smith").first.click()
    assert page.locator('select[data-wl-row-id="chk-10"]').input_value() == "Complete"
    assert page.locator('select[data-wl-row-id="chk-11"]').input_value() == "Complete"
    assert page.locator('select[data-wl-row-id="chk-12"]').input_value() == "N/A"

    # Reject javascript: scheme on checklist result link, accept https:// link with & query parameters
    page.fill("#chkText-chk-02", "Attempted unsafe link")
    page.fill("#chkLinkLbl-chk-02", "Bad Link")
    page.fill("#chkLinkUrl-chk-02", "javascript:alert(1)")
    page.click('[data-add-chk-id="chk-02"]')
    assert page.locator('tr[data-row-id="chk-02"] a[href^="javascript:"]').count() == 0

    safe_drive_url = "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOp/view?usp=sharing&pli=1"
    page.fill("#chkText-chk-02", "Uploaded deed scan to Google Drive")
    page.fill("#chkLinkLbl-chk-02", "Deed Scan PDF")
    page.fill("#chkLinkUrl-chk-02", safe_drive_url)
    page.click('[data-add-chk-id="chk-02"]')
    assert page.locator(f'tr[data-row-id="chk-02"] a[href="{safe_drive_url}"]').count() == 1

    # Printable iframe HTML, CSV, and Word .docx XML validation
    page.click("#exportWatchlistDocxBtn")
    page.wait_for_function(
        "() => { const fr = document.querySelector('iframe'); return Boolean(fr && fr.contentDocument && fr.contentDocument.body && fr.contentDocument.body.innerText.includes('Uploaded deed scan to Google Drive')); }",
        timeout=5000,
    )
    iframe_html = page.evaluate(
        """() => {
            const fr = document.querySelector('iframe');
            return fr && fr.contentDocument ? fr.contentDocument.documentElement.outerHTML : '';
        }"""
    )
    assert "Uploaded deed scan to Google Drive" in iframe_html
    assert "javascript:alert" not in iframe_html

    with page.expect_download(timeout=10000) as csv_dl_info:
        page.click("#exportWatchlistCsvBtn")
    csv_text = Path(csv_dl_info.value.path()).read_text(encoding="utf-8-sig")
    assert "Uploaded deed scan to Google Drive" in csv_text

    with page.expect_download(timeout=10000) as docx_dl_info:
        page.click("#downloadWatchlistRealDocxBtn")
    docx_bytes = Path(docx_dl_info.value.path()).read_bytes()
    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as zf:
        doc_xml = zf.read("word/document.xml").decode("utf-8")
        rels_xml = zf.read("word/_rels/document.xml.rels").decode("utf-8")
        # Both XML documents must parse cleanly without unescaped '&' corruption
        ET.fromstring(doc_xml)
        ET.fromstring(rels_xml)
        assert "Uploaded deed scan to Google Drive" in doc_xml
        assert "usp=sharing&amp;pli=1" in rels_xml


def test_it8_dangerous_buildings_dondb_crosschecker_modal(
    guarded_page: Any,
    atlas_base_url: str,
) -> None:
    """Verify in-browser DONDB*.csv cross-checker zero-padding, scientific-notation warnings, Check #13 flagging, and notification copy."""
    page = guarded_page
    _seed_staff_admin_session(page)

    page.goto(f"{atlas_base_url}/plat-monitor.html", wait_until="domcontentloaded")
    page.wait_for_selector("#modeWatchlistBtn", state="visible", timeout=15000)
    page.click("#modeWatchlistBtn")
    page.click("#openDondbHeaderBtn")
    page.wait_for_selector("#dondbBody", state="visible", timeout=10000)

    page.set_input_files(
        "#fileDondb",
        files=[
            {
                "name": "DONDB082626.csv",
                "mimeType": "text/csv",
                "buffer": _build_synthetic_dondb_csv(),
            }
        ],
    )
    page.wait_for_function(
        "() => document.querySelectorAll('#dondbBody [data-dondb-flag-idx]').length >= 2",
        timeout=25000,
    )

    body_txt = page.locator("#dondbBody").inner_text()
    assert "0010160000011" in body_txt
    assert "0621100000014" in body_txt
    assert page.locator("#dondbCopyNotifyBtn").count() == 1
    page.click("#dondbCopyNotifyBtn")

    # Verify Parcel ID Warnings tab surfaces scientific-notation corrupted 1.523E+11 row
    page.click('#dondbBody [data-dondb-tab="warnings"]')
    warn_txt = page.locator("#dondbBody").inner_text()
    assert "1.523E+11" in warn_txt

    # Return to Tier 1 tab and flag DONDB900001 onto Watchlist Check #13
    page.click('#dondbBody [data-dondb-tab="tier1"]')
    page.locator("#dondbBody [data-dondb-flag-idx]").first.click()
    page.click("#dondbCloseBtn")

    page.locator("#list .row", has_text="112 TRAVIS").first.click()
    chk13_txt = page.locator('tr[data-row-id="chk-13"]').inner_text()
    assert "DONDB900001" in chk13_txt
    assert page.locator('select[data-wl-row-id="chk-13"]').input_value() == "Flagged"


def test_it9_and_rp6_three_way_cloud_sheet_reconciliation_legacy_hydration_and_deeplink(
    guarded_page: Any,
    atlas_base_url: str,
) -> None:
    """Verify 3-way Watchlist reconciliation across static [AUTO-SWEEP] + legacy Plat_&_Watchlist_Sync CSV + ?hcad= deep-link."""
    page = guarded_page
    _seed_staff_admin_session(page)

    # Build a synthetic Plat_&_Watchlist_Sync CSV row using exact pullCloudSheetSync headers:
    # 1. Uses legacy architectEngineer field (RP-6 -> designFirm hydration) with newer updatedAt
    # 2. Has manual 'Complete' on chk-01 and an attached Google Drive link
    # 3. Has older chk-04 snapshot (2026-10-20) -> static [AUTO-SWEEP] on chk-04 must survive
    # 4. Omits searchTerms (legacy Oct 23 row) -> must NOT wipe static Kirby Mansion snowball terms
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(
        [
            "status",
            "hcad_num",
            "year_built",
            "historic_district",
            "contributing",
            "source_type",
            "source_citation",
            "verified_by",
        ]
    )
    writer.writerow(
        [
            "approved",
            "WATCH_PROP:prop-kirby-mansion",
            "1926",
            "National Register (individual)",
            "Tier 1",
            "PROPERTY_WATCHLIST_UPDATE",
            json.dumps(
                {
                    "id": "prop-kirby-mansion",
                    "propertyName": "John Henry Kirby Mansion",
                    "address": "2000 Smith St.",
                    "hcadAccount": "0152300000001",
                    "tier": "1",
                    "architectEngineer": "Legacy Cloud Preservation Engineers",
                    "driveLinks": [
                        {
                            "label": "Kirby Mansion Research Folder",
                            "url": "https://drive.google.com/drive/folders/1KirbyFolderExample",
                        }
                    ],
                    "driveLinksUpdatedAt": "2099-01-01T00:00:00Z",
                    "updatedAt": "2099-01-01T00:00:00Z",
                    "checklist": [
                        {
                            "id": "chk-01",
                            "status": "Complete",
                            "lastChecked": "2026-10-27",
                            "updatedAt": "2099-01-01T00:00:00Z",
                            "checks": [
                                {
                                    "date": "2026-10-27",
                                    "result": "Cloud staff verified commercial listing flyers.",
                                    "source": "manual",
                                }
                            ],
                        },
                        {
                            "id": "chk-05",
                            "status": "In Progress",
                            "lastChecked": "2026-10-20",
                            "updatedAt": "2026-10-20T00:00:00Z",
                            "checks": [],
                        },
                        {
                            "id": "chk-06",
                            "status": "In Progress",
                            "lastChecked": "2026-10-01",
                            "updatedAt": "2026-10-01T00:00:00Z",
                            "checks": [],
                        },
                    ],
                }
            ),
            "Emily Ardoin",
        ]
    )
    cloud_csv_bytes = buf.getvalue().encode("utf-8")

    def _stub_cloud_sheet(route: Any) -> None:
        if route.request.method.upper() == "POST":
            route.fulfill(
                status=200,
                content_type="application/json; charset=utf-8",
                headers={"Access-Control-Allow-Origin": "*"},
                body=b'{"ok":true}',
            )
            return
        route.fulfill(
            status=200,
            content_type="text/csv; charset=utf-8",
            headers={"Access-Control-Allow-Origin": "*"},
            body=cloud_csv_bytes,
        )

    page.route("**/script.google.com/macros/**", _stub_cloud_sheet)
    page.route("**/docs.google.com/spreadsheets/**", _stub_cloud_sheet)

    # Open via ?hcad=0152300000001&mode=watchlist deep-link
    page.goto(
        f"{atlas_base_url}/plat-monitor.html?mode=watchlist&hcad=0152300000001",
        wait_until="domcontentloaded",
    )
    page.wait_for_selector("#app:not(.hidden)", timeout=15000)
    page.wait_for_function(
        """() => (document.getElementById('detail')?.innerText || '').includes('Legacy Cloud Preservation Engineers')""",
        timeout=15000,
    )

    detail_txt = page.locator("#detail").inner_text()
    # 1. RP-6 legacy architectEngineer hydrated into Design / Engineering Firm + cloud Drive link preserved
    assert "Legacy Cloud Preservation Engineers" in detail_txt
    assert "Kirby Mansion Research Folder" in detail_txt

    # 2. Manual Complete status & manual check on chk-01 from cloud row preserved
    assert page.locator('select[data-wl-row-id="chk-01"]').input_value() == "Complete"
    assert "Cloud staff verified commercial listing flyers." in page.locator('tr[data-row-id="chk-01"]').inner_text()

    # 3. Fresh on-site [AUTO-SWEEP] findings on chk-05 and chk-06 from static property_watchlist.json survived older cloud checks: [] snapshots, while chk-04 stays free of off-site chain false positives!
    chk04_txt = page.locator('tr[data-row-id="chk-04"]').inner_text()
    assert "32102472084" in chk04_txt
    assert "LONG POINT" not in chk04_txt
    chk05_txt = page.locator('tr[data-row-id="chk-05"]').inner_text()
    assert "[AUTO-SWEEP] HCAD Account 0152300000001" in chk05_txt
    chk06_txt = page.locator('tr[data-row-id="chk-06"]').inner_text()
    assert "[AUTO-SWEEP] TABC License #200183169" in chk06_txt

    # 4. Seeded Kirby Mansion snowball searchTerms survived the legacy cloud row that omitted searchTerms!
    snow_txt = page.locator("#snowballCard").inner_text()
    assert "Smith St C Store LLC" in snow_txt
    assert "Brake Time" in snow_txt


def test_annual_demolition_benchmarks_option_a_and_option_b_e2e(
    page: Any,
    atlas_base_url: str,
) -> None:
    """E2E verification of Option A (plat-monitor.html) and Option B (index.html) Annual (2020-2025) & 2026 YTD Demolition Permit Benchmarks."""
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])

    # --- Option A: Plat & Property Monitor Workstation (plat-monitor.html) ---
    page.goto(f"{atlas_base_url}/plat-monitor.html", wait_until="domcontentloaded")
    page.wait_for_selector("#app:not(.hidden)", timeout=15000)
    page.wait_for_function("() => Boolean(window.__permitAlertsPayload)", timeout=10000)

    assert page.locator("#hdrDemoYtdCount").inner_text().strip() == "1,755"
    demo_stage = page.locator("#funnel .stage.demo-stage")
    assert "1,755 / 2,187" in demo_stage.inner_text()
    assert demo_stage.get_attribute("aria-expanded") == "false"

    # Toggle drawer open via #toggleDemoBenchmarksBtn
    page.locator("#toggleDemoBenchmarksBtn").click()
    drawer = page.locator("#demoBenchmarksDrawer")
    assert not drawer.evaluate("el => el.classList.contains('hidden')")
    assert page.locator("#toggleDemoBenchmarksBtn").get_attribute("aria-expanded") == "true"
    assert demo_stage.get_attribute("aria-expanded") == "true"

    drawer_txt = drawer.inner_text()
    for token in ("1,755", "1,676", "2,187", "2,066", "2,380", "2,287", "2,791", "2,096", "155"):
        assert token in drawer_txt, f"Missing expected benchmark metric {token} in #demoBenchmarksDrawer"

    # Keyboard Enter/Space toggle and Escape close on .stage.demo-stage
    demo_stage.focus()
    page.keyboard.press("Enter")
    assert drawer.evaluate("el => el.classList.contains('hidden')")
    assert demo_stage.get_attribute("aria-expanded") == "false"
    page.keyboard.press(" ")
    assert not drawer.evaluate("el => el.classList.contains('hidden')")

    # Copy grant summary from drawer button
    page.locator("#copyDemoBenchmarksBtn").click()
    clip_text = page.evaluate("() => navigator.clipboard.readText()")
    assert "HOUSTON BUILDING ATLAS - DEMOLITION & PRESERVATION BENCHMARKS" in clip_text
    assert "1,755 structural demolition permits across 1,676 unique addresses" in clip_text
    assert "\u2014" not in clip_text

    # Close with Escape key
    page.keyboard.press("Escape")
    assert drawer.evaluate("el => el.classList.contains('hidden')")

    # Switch to Watchlist mode and verify .stage.demo-stage remains in #funnel
    page.locator("#modeWatchlistBtn").click()
    assert "1,755 / 2,187" in page.locator("#funnel .stage.demo-stage").inner_text()

    # --- Option B: Main Map Sidebar & Active Permit Watchlist Modal (index.html) ---
    page.goto(f"{atlas_base_url}/index.html", wait_until="domcontentloaded")
    _wait_for_atlas_ready(page)

    assert page.locator("#pw-sidebar-ytd-count").inner_text().strip() == "1,755"
    assert page.locator("#pw-sidebar-2025-count").inner_text().strip() == "2,187"
    assert page.locator("#pw-sidebar-hahc-count").inner_text().strip() == "0"

    # Clicking #btn-open-demo-benchmarks-sidebar opens #permit-watch-modal and expands <details id="pw-annual-benchmarks-details">
    page.locator("#btn-open-demo-benchmarks-sidebar").click()
    modal = page.locator("#permit-watch-modal")
    assert not modal.evaluate("el => el.classList.contains('hidden')")
    details = page.locator("#pw-annual-benchmarks-details")
    assert details.evaluate("el => el.open === true")

    assert page.locator("#pw-bench-pill-ytd").inner_text().strip() == "1,755"
    assert page.locator("#pw-bench-pill-2025").inner_text().strip() == "2,187"
    assert page.locator("#pw-bench-pill-hcad").inner_text().strip() == "2,791"
    assert page.locator("#pw-bench-pill-hahc").inner_text().strip() == "0"

    body_txt = page.locator("#pw-annual-benchmarks-body").inner_text()
    for token in ("1,755", "1,676", "2,187", "2,380", "2,791", "2,096", "155"):
        assert token in body_txt, f"Missing expected benchmark metric {token} in #pw-annual-benchmarks-body"

    # Rapid double-click on #btn-copy-grant-demo-stats preserves dataset.origHtml and copies grant text
    copy_btn = page.locator("#btn-copy-grant-demo-stats")
    copy_btn.click()
    copy_btn.click()
    assert "Copied Grant Stats" in copy_btn.inner_text()
    assert "Copy Grant Stats" in copy_btn.evaluate("el => el.dataset.origHtml || ''")
    modal_clip = page.evaluate("() => navigator.clipboard.readText()")
    assert modal_clip == clip_text


def test_activity_it1_morning_briefing_layout_sweep_health_ledger_filters_and_export(
    guarded_page: Any,
    atlas_base_url: str,
) -> None:
    """IT-1: Verify 2-pane What's New & Daily Log UI, header/toolbar decoupling, healthy vs degraded sweep indicators, ledger drill-down, filters, and Copy Briefing."""
    page = guarded_page
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])

    page.goto(f"{atlas_base_url}/plat-monitor.html?mode=activity", wait_until="domcontentloaded")
    page.wait_for_selector("#app:not(.hidden)", timeout=15000)
    page.wait_for_selector("#modeActivityBtn[aria-selected='true']", timeout=15000)
    page.wait_for_function(
        "() => Number(document.querySelector('#actCntAll')?.textContent || '0') > 0",
        timeout=15000,
    )

    # 1. Two-pane side-by-side layout + exclusive header/toolbar surface parity
    geom = page.evaluate(
        """() => {
            const l = document.querySelector('#list').getBoundingClientRect();
            const d = document.querySelector('#detail').getBoundingClientRect();
            return {
                innerWidth: window.innerWidth,
                scrollWidth: document.documentElement.scrollWidth,
                listX: l.x,
                listW: l.width,
                detailX: d.x,
                detailW: d.width,
            };
        }"""
    )
    assert geom["scrollWidth"] <= geom["innerWidth"]
    assert geom["listW"] > 200 and geom["detailW"] > 400
    assert geom["detailX"] >= geom["listX"] + geom["listW"] - 2

    assert page.locator("#activityHeaderControls").is_visible()
    assert not page.locator("#agendaHeaderControls").is_visible()
    assert not page.locator("#watchlistHeaderControls").is_visible()
    assert page.locator("#toggleDemoBenchmarksBtn").is_visible()
    assert page.locator("#activityToolbar").is_visible()
    assert not page.locator("#agendaToolbar").is_visible()
    assert not page.locator("#watchlistToolbar").is_visible()
    assert page.locator("#funnel .stage").count() == 6

    # 2. Unread badge alignment & sweep health telemetry matching live committed snapshot
    data_dir = Path(__file__).resolve().parents[2] / "app" / "public" / "data"
    live_wl_doc = json.loads((data_dir / "property_watchlist.json").read_text(encoding="utf-8"))
    live_sh = live_wl_doc.get("lastSweepHealth") or {}
    live_ok = int(live_sh.get("succeededAgencies", 8))
    live_tot = int(live_sh.get("totalAgencies", 8))
    live_failed = list(live_sh.get("failedAgencies") or [])

    mode_cnt = page.locator("#modeActivityCount").inner_text().strip()
    unread_cnt = page.locator("#actCntUnread").inner_text().strip()
    assert mode_cnt == unread_cnt and int(mode_cnt) > 0
    sweep_txt = page.locator("#activitySweepHealthBadge").inner_text()
    assert f"{live_ok}/{live_tot}" in sweep_txt
    if not live_failed:
        assert page.locator("[data-degraded-sweep-banner='1']").count() == 0
    else:
        assert page.locator("[data-degraded-sweep-banner='1']").count() == 1

    # 3. Multi-day quiet sweep compaction expand/collapse
    quiet_rows = page.locator("#list .act-quiet-row")
    assert quiet_rows.count() >= 1
    initial_run_rows = page.locator("#list .act-run-row").count()
    first_quiet = quiet_rows.first
    assert first_quiet.get_attribute("aria-expanded") == "false"
    first_quiet.click()
    assert first_quiet.get_attribute("aria-expanded") == "true"
    expanded_run_rows = page.locator("#list .act-run-row").count()
    assert expanded_run_rows > initial_run_rows
    first_quiet.click()
    assert page.locator("#list .act-run-row").count() == initial_run_rows

    # 4. Single-day ledger inspection + Clear filter chip
    target_row = page.locator("#list .act-run-row").first
    target_date = target_row.get_attribute("data-act-date")
    assert target_date
    target_row.click()
    assert target_row.get_attribute("aria-current") == "true"
    assert page.locator("#actDateFilterChip").is_visible()
    assert target_date[:4] in page.locator("#actDateFilterChip").inner_text()
    page.locator("#actDateFilterChip [data-clear-act-date]").click()
    assert not page.locator("#actDateFilterChip").is_visible()

    # 5. Range pills (`since_last_visit`, `7d`, `all`), all 4 stream filters, and search input
    page.click("#activityRangeTabs [data-act-range='7d']")
    assert page.locator("#detail .act-card").count() == int(
        page.locator("#actCnt7d").inner_text().strip()
    )
    page.click("#activityRangeTabs [data-act-range='all']")
    all_cnt = int(page.locator("#actCntAll").inner_text().strip())
    assert page.locator("#detail .act-card").count() == all_cnt

    for stream_val in ("watchlist", "agenda", "staff", "permits"):
        page.select_option("#actStreamFilter", stream_val)
        cards_cnt = page.locator("#detail .act-card").count()
        assert cards_cnt >= 1
        wrong_stream = page.evaluate(
            "(sv) => Array.from(document.querySelectorAll('#detail .act-card')).filter(c => c.dataset.stream !== sv).length",
            stream_val,
        )
        assert wrong_stream == 0
    page.select_option("#actStreamFilter", "all")

    page.fill("#actSearchInput", "Montrose")
    search_cnt = page.locator("#detail .act-card").count()
    assert 1 <= search_cnt < all_cnt
    page.fill("#actSearchInput", "")
    page.click("#activityRangeTabs [data-act-range='since_last_visit']")

    # 6. Copy Briefing button copies plain-text briefing with zero U+2014 em-dashes
    page.click("#copyActivityBriefingBtn")
    briefing_clip = page.evaluate("() => navigator.clipboard.readText()")
    assert "Preservation Houston - Plat & Property Monitor Briefing" in briefing_clip
    assert f"{live_ok}/{live_tot} agencies OK" in briefing_clip
    assert "\u2014" not in briefing_clip

    # 7a. Deterministic clean 8/8 mock verifies zero false degraded warnings even if live cron had an upstream portal outage
    healthy_doc = json.loads((data_dir / "monitor_activity_log.json").read_text(encoding="utf-8"))
    healthy_wl = json.loads((data_dir / "property_watchlist.json").read_text(encoding="utf-8"))
    for sh_obj in (healthy_doc["lastSweepHealth"], healthy_wl["lastSweepHealth"]):
        sh_obj["succeededAgencies"] = 8
        sh_obj["failedAgencies"] = []
        sh_obj["allOk"] = True
        for ag_meta in (sh_obj.get("agencies") or {}).values():
            ag_meta["status"] = "ok"
            ag_meta["ok"] = True
            ag_meta["failedPropertyCount"] = 0
            ag_meta["error"] = ""
    healthy_doc["events"] = [
        e for e in healthy_doc.get("events", []) if e.get("kind") != "watchlist_sweep_degraded"
    ]
    if healthy_doc.get("dailyRuns"):
        healthy_doc["dailyRuns"][0]["succeededAgencies"] = 8
        healthy_doc["dailyRuns"][0]["failedAgencies"] = []
        healthy_doc["dailyRuns"][0]["allOk"] = True
    healthy_json = json.dumps(healthy_doc)
    healthy_wl_json = json.dumps(healthy_wl)
    page.route(
        "**/data/monitor_activity_log.json*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json; charset=utf-8",
            body=healthy_json,
        ),
    )
    page.route(
        "**/data/property_watchlist.json*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json; charset=utf-8",
            body=healthy_wl_json,
        ),
    )
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("#app:not(.hidden)", timeout=15000)
    assert "8/8" in page.locator("#activitySweepHealthBadge").inner_text()
    assert page.locator("[data-degraded-sweep-banner='1']").count() == 0
    page.unroute("**/data/monitor_activity_log.json*")
    page.unroute("**/data/property_watchlist.json*")

    # 7b. Mock degraded sweep payload (`7/8`, `chk-10` TDLR failed) -> surfaces degraded badge, banner, & ledger pill
    degraded_doc = json.loads((data_dir / "monitor_activity_log.json").read_text(encoding="utf-8"))
    degraded_wl = json.loads((data_dir / "property_watchlist.json").read_text(encoding="utf-8"))
    tdlr_err = {
        "status": "error",
        "ok": False,
        "label": "TDLR Architectural Barriers (TABS)",
        "failedPropertyCount": 3,
        "error": "TDLR TABS: HTTP 503 Service Unavailable",
    }
    for sh_obj in (degraded_doc["lastSweepHealth"], degraded_wl["lastSweepHealth"]):
        sh_obj["succeededAgencies"] = 7
        sh_obj["failedAgencies"] = ["chk-10"]
        sh_obj["allOk"] = False
        sh_obj.setdefault("agencies", {})["chk-10"] = tdlr_err
    degraded_doc["dailyRuns"][0]["succeededAgencies"] = 7
    degraded_doc["dailyRuns"][0]["failedAgencies"] = ["chk-10"]
    degraded_doc["dailyRuns"][0]["allOk"] = False
    degraded_json = json.dumps(degraded_doc)
    degraded_wl_json = json.dumps(degraded_wl)

    page.route(
        "**/data/monitor_activity_log.json*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json; charset=utf-8",
            body=degraded_json,
        ),
    )
    page.route(
        "**/data/property_watchlist.json*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json; charset=utf-8",
            body=degraded_wl_json,
        ),
    )
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("#modeActivityBtn[aria-selected='true']", timeout=15000)
    page.wait_for_selector("[data-degraded-sweep-banner='1']", timeout=10000)

    deg_badge_txt = page.locator("#activitySweepHealthBadge").inner_text()
    assert "7/8" in deg_badge_txt and "chk-10" in deg_badge_txt
    deg_banner_txt = page.locator("[data-degraded-sweep-banner='1']").inner_text()
    assert "7/8 agencies OK" in deg_banner_txt
    assert "TDLR" in deg_banner_txt
    assert "HTTP 503" in deg_banner_txt
    assert page.locator("#list .act-run-row.degraded [data-sweep-status='degraded']").count() >= 1
    assert "\u2014" not in page.locator("body").inner_text()


def test_activity_it2_cross_links_map_hashes_live_edit_badge_and_caught_up_watermark(
    guarded_page: Any,
    atlas_base_url: str,
) -> None:
    """IT-2: Verify unread dots across modes, Open Watchlist Row auto-scroll/flash, Open Plat Item deep jump, Atlas Map hashes, live check badge updates, and Mark Caught Up persistence + post-watermark sync."""
    page = guarded_page
    _seed_staff_admin_session(page)

    page.goto(f"{atlas_base_url}/plat-monitor.html?mode=activity", wait_until="domcontentloaded")
    page.wait_for_selector("#modeActivityBtn[aria-selected='true']", timeout=15000)
    page.wait_for_function(
        "() => Number(document.querySelector('#actCntAll')?.textContent || '0') > 0",
        timeout=15000,
    )

    # 1. Inline unread badges visible in Agenda & Watchlist modes on first visit
    page.click("#modeAgendaBtn")
    assert page.locator("#list .row [data-unread-badge='agenda']").count() >= 1
    page.click("#modeWatchlistBtn")
    assert page.locator("#list .row [data-unread-badge='watchlist']").count() >= 1

    # 2. Back in Activity mode (`all`), click Open Watchlist Row -> with specific data-act-check-id
    page.click("#modeActivityBtn")
    page.click("#activityRangeTabs [data-act-range='all']")
    target_watch_btn = page.locator(
        "#detail button[data-act-open-watch][data-act-check-id]"
    ).first
    expected_wid = target_watch_btn.get_attribute("data-act-open-watch")
    expected_chk = target_watch_btn.get_attribute("data-act-check-id")
    assert expected_wid and expected_chk
    target_watch_btn.click()

    assert page.locator("#modeWatchlistBtn").get_attribute("aria-selected") == "true"
    assert (
        page.locator(f"#list .row[data-watch-id='{expected_wid}']").get_attribute("aria-current")
        == "true"
    )
    flashed_tr = page.locator(f"#detail tr[data-row-id='{expected_chk}']")
    assert flashed_tr.count() == 1
    assert "wl-row-flash" in (flashed_tr.get_attribute("class") or "")

    # Also verify jumping specifically to prop-kirby-mansion auto-clears any excluding destination filter
    page.fill("#wlSearchInput", "NONEXISTENT_WATCHLIST_FILTER_99999")
    page.click("#modeActivityBtn")
    page.click("#activityRangeTabs [data-act-range='all']")
    kirby_btn = page.locator("#detail button[data-act-open-watch='prop-kirby-mansion']").first
    assert kirby_btn.count() == 1
    kirby_btn.click()
    assert page.locator("#wlSearchInput").input_value() == ""
    assert (
        page.locator("#list .row[data-watch-id='prop-kirby-mansion']").get_attribute(
            "aria-current"
        )
        == "true"
    )
    assert "2000 Smith" in (page.locator("#detail h2").text_content() or "")

    # 3. Click Open Plat Item -> auto-clears any excluding Agenda search filter and selects exact occurrenceKey
    page.click("#modeAgendaBtn")
    page.fill("#q", "NONEXISTENT_PLAT_QUERY_99999")
    page.click("#modeActivityBtn")
    page.click("#activityRangeTabs [data-act-range='all']")
    plat_btn = page.locator("#detail button[data-act-open-plat-app]").first
    expected_app = plat_btn.get_attribute("data-act-open-plat-app")
    expected_cyc = plat_btn.get_attribute("data-act-open-plat-cycle")
    expected_occ = plat_btn.get_attribute("data-act-open-plat-occ")
    assert expected_app and expected_occ
    plat_btn.click()
    assert page.locator("#q").input_value() == ""

    assert page.locator("#modeAgendaBtn").get_attribute("aria-selected") == "true"
    if expected_cyc:
        assert page.locator("#cycleSelect").input_value() == expected_cyc
    assert (
        page.locator(f"#list .row[data-occ='{expected_occ}']").get_attribute("aria-current")
        == "true"
    )
    assert expected_app in page.locator("#detail").inner_text()

    # 4. Stream 4 View on Atlas Map link hashes include hcad=/layers=permit_watch and hahc_coa on HAHC cards
    page.click("#modeActivityBtn")
    page.click("#activityRangeTabs [data-act-range='all']")
    page.select_option("#actStreamFilter", "permits")
    # Give lazy HAHC tracker fetch a brief moment if still completing
    page.wait_for_function(
        """() => Array.from(document.querySelectorAll('#detail .act-card')).some(
            c => (c.dataset.eventId || '').startsWith('hahc:')
        )""",
        timeout=10000,
    )
    map_href_audit = page.evaluate(
        """() => {
            const cohHrefs = Array.from(
                document.querySelectorAll('#detail .act-card[data-event-id^="permit:coh:"] a[data-act-map-link]')
            ).map(a => a.getAttribute('href') || '');
            const hahcHrefs = Array.from(
                document.querySelectorAll('#detail .act-card[data-event-id^="hahc:"] a[data-act-map-link]')
            ).map(a => a.getAttribute('href') || '');
            return { cohHrefs, hahcHrefs };
        }"""
    )
    assert len(map_href_audit["cohHrefs"]) >= 1
    assert any(
        "#" in h and "hcad=" in h and "permit_watch" in h for h in map_href_audit["cohHrefs"]
    )
    assert len(map_href_audit["hahcHrefs"]) >= 1
    assert all("hahc_coa" in h for h in map_href_audit["hahcHrefs"])
    page.select_option("#actStreamFilter", "all")

    # 5. Live manual Watchlist check updates #modeActivityCount immediately without page reload
    before_badge = int(page.locator("#modeActivityCount").inner_text().strip())
    page.click("#modeWatchlistBtn")
    page.locator("#list .row[data-watch-id='prop-kirby-mansion']").click()
    page.fill("#chkText-chk-02", "IT-2 live manual deed follow-up recorded")
    page.click("[data-add-chk-id='chk-02']")
    after_badge = int(page.locator("#modeActivityCount").inner_text().strip())
    assert after_badge >= before_badge + 1
    page.click("#modeActivityBtn")
    assert "IT-2 live manual deed follow-up recorded" in page.locator("#detail").inner_text()

    # 6. Mark Caught Up zeroes badge, renders All Caught Up state, clears unread dots across modes, & survives reload
    page.click("#modeActivityBtn")
    page.click("#activityRangeTabs [data-act-range='since_last_visit']")
    page.click("#markCaughtUpBtn")
    assert page.locator("#modeActivityCount").inner_text().strip() == "0"
    assert page.locator("#actCntUnread").inner_text().strip() == "0"
    assert page.locator("[data-caught-up-card='1']").count() == 1

    page.click("#modeWatchlistBtn")
    assert page.locator("#list .row [data-unread-badge='watchlist']").count() == 0
    assert page.locator("#detail [data-unread-badge='check']").count() == 0
    page.click("#modeAgendaBtn")
    assert page.locator("#list .row [data-unread-badge='agenda']").count() == 0

    page.click("#modeActivityBtn")
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("#app:not(.hidden)", timeout=15000)
    page.wait_for_selector("[data-caught-up-card='1']", timeout=15000)
    assert page.locator("#modeActivityBtn").get_attribute("aria-selected") == "true"
    assert page.locator("#modeActivityCount").inner_text().strip() == "0"
    assert page.locator("[data-caught-up-card='1']").count() == 1

    # 7. Inject a post-watermark Google Sheet CSV edit -> surfaces strictly as 1 new unread item
    future_iso = page.evaluate("() => new Date(Date.now() + 120000).toISOString()")
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(
        [
            "status",
            "hcad_num",
            "year_built",
            "historic_district",
            "contributing",
            "source_type",
            "source_citation",
            "verified_by",
        ]
    )
    writer.writerow(
        [
            "approved",
            "WATCH_PROP:prop-kirby-mansion",
            "1926",
            "National Register (individual)",
            "Tier 1",
            "PROPERTY_WATCHLIST_UPDATE",
            json.dumps(
                {
                    "id": "prop-kirby-mansion",
                    "propertyName": "John Henry Kirby Mansion",
                    "address": "2000 Smith St.",
                    "hcadAccount": "0152300000001",
                    "tier": "1",
                    "updatedAt": future_iso,
                    "checklist": [
                        {
                            "id": "chk-03",
                            "status": "Flagged",
                            "lastChecked": future_iso[:10],
                            "updatedAt": future_iso,
                            "checks": [
                                {
                                    "date": future_iso[:10],
                                    "result": "Post-watermark cloud DBA transfer filed.",
                                    "source": "manual",
                                    "loggedAt": future_iso,
                                }
                            ],
                        }
                    ],
                }
            ),
            "Emily Ardoin",
        ]
    )
    post_wm_csv = buf.getvalue().encode("utf-8")
    page.route(
        "**/docs.google.com/spreadsheets/**",
        lambda route: route.fulfill(
            status=200,
            content_type="text/csv; charset=utf-8",
            headers={"Access-Control-Allow-Origin": "*"},
            body=post_wm_csv,
        ),
    )
    page.route(
        "**/script.google.com/macros/**",
        lambda route: route.fulfill(
            status=200,
            content_type="text/csv; charset=utf-8",
            headers={"Access-Control-Allow-Origin": "*"},
            body=post_wm_csv,
        ),
    )
    page.reload(wait_until="domcontentloaded")
    page.wait_for_function(
        "() => document.querySelector('#modeActivityCount')?.textContent?.trim() === '1'",
        timeout=15000,
    )
    assert page.locator("#detail .act-card").count() == 1
    assert "Post-watermark cloud DBA transfer filed." in page.locator("#detail").inner_text()


def test_activity_it3_permit_watchlist_deduplication_auth_gate_and_cloud_sync_promotion(
    guarded_page: Any,
    atlas_base_url: str,
) -> None:
    """IT-3: Verify watched HCAD deduplication, unauthenticated staff modal gate, and 1-click Permit/DSHS -> Watchlist promotion with Cloud Sync POST."""
    page = guarded_page
    sync_posts: list[dict[str, Any]] = []

    def _stub_apps_script(route: Any) -> None:
        if route.request.method.upper() == "POST":
            raw = route.request.post_data or "{}"
            payload = json.loads(raw)
            if payload.get("action") == "verify_admin":
                route.fulfill(
                    status=200,
                    content_type="application/json; charset=utf-8",
                    headers={"Access-Control-Allow-Origin": "*"},
                    body=json.dumps({"authorized": True, "email": payload.get("admin_email")}),
                )
                return
            sync_posts.append(payload)
            route.fulfill(
                status=200,
                content_type="application/json; charset=utf-8",
                headers={"Access-Control-Allow-Origin": "*"},
                body=b'{"ok":true}',
            )
            return
        route.fulfill(
            status=200,
            content_type="text/csv; charset=utf-8",
            headers={"Access-Control-Allow-Origin": "*"},
            body=b"hcad_num,status\n",
        )

    page.route("**/script.google.com/macros/**", _stub_apps_script)

    page.goto(f"{atlas_base_url}/plat-monitor.html?mode=activity", wait_until="domcontentloaded")
    page.wait_for_selector("#modeActivityBtn[aria-selected='true']", timeout=15000)
    page.wait_for_function(
        "() => Number(document.querySelector('#actCntAll')?.textContent || '0') > 0",
        timeout=15000,
    )
    page.click("#activityRangeTabs [data-act-range='all']")
    page.select_option("#actStreamFilter", "permits")

    # 1. Already-watched 2000 Smith St (0152300000001) Stream 4 cards render Open Watchlist Row -> instead of Promote
    smith_dedup = page.evaluate(
        """() => {
            const cards = Array.from(document.querySelectorAll('#detail .act-card')).filter(
                c => c.innerText.includes('0152300000001') || c.innerText.includes('2000 SMITH')
            );
            return {
                count: cards.length,
                allOpenKirby: cards.every(c => Boolean(c.querySelector('[data-act-open-watch="prop-kirby-mansion"]'))),
                anyPromote: cards.some(c => Boolean(c.querySelector('[data-act-promote-event]'))),
            };
        }"""
    )
    assert smith_dedup["count"] >= 1
    assert smith_dedup["allOpenKirby"] is True
    assert smith_dedup["anyPromote"] is False

    # 2. Pick an unwatched historic COH demolition/sewer card with an 8-digit project # + historic designation
    coh_target = page.evaluate(
        """() => {
            const cards = Array.from(document.querySelectorAll('#detail .act-card[data-event-id^="permit:coh:"]'));
            for (const c of cards) {
                const btn = c.querySelector('[data-act-promote-event]');
                const m = (c.innerText || '').match(/#(\\d{8})/);
                if (btn && m && (c.innerText.includes('Historic District') || c.innerText.includes('Landmark') || c.innerText.includes('Good Brick'))) {
                    return { eventId: btn.dataset.actPromoteEvent, projNo: m[1], hcadMatch: ((c.innerText || '').match(/HCAD\\s+(\\d{13})/) || [])[1] || '' };
                }
            }
            return null;
        }"""
    )
    assert coh_target is not None, "Expected at least one unwatched designated COH permit alert card"

    initial_wl_cnt = int(page.locator("#modeWatchlistCount").inner_text().strip())

    # Signed-out click MUST open staff auth modal without creating a Watchlist entry
    page.locator(f"#detail button[data-act-promote-event='{coh_target['eventId']}']").click()
    assert page.locator(".modal-back .modal[role='dialog'] #sa-email").is_visible()
    assert int(page.locator("#modeWatchlistCount").inner_text().strip()) == initial_wl_cnt

    # 3. Sign in directly through the staff verification modal
    page.fill("#sa-email", "eardoin@preservationhouston.org")
    page.fill("#sa-passkey", "test-passkey")
    page.click("#saVerify")
    page.wait_for_selector(".modal-back", state="detached", timeout=10000)

    # 4. Promote the COH historic demolition/sewer alert card
    page.locator(f"#detail button[data-act-promote-event='{coh_target['eventId']}']").click()
    assert int(page.locator("#modeWatchlistCount").inner_text().strip()) == initial_wl_cnt + 1

    # Card immediately switches from + Promote to Watchlist -> Open Watchlist Row ->
    promoted_card = page.locator(f"#detail .act-card[data-event-id='{coh_target['eventId']}']")
    assert promoted_card.locator("[data-act-promote-event]").count() == 0
    open_new_watch_btn = promoted_card.locator("[data-act-open-watch]")
    assert open_new_watch_btn.count() == 1
    new_watch_id = open_new_watch_btn.get_attribute("data-act-open-watch")
    assert new_watch_id and new_watch_id.startswith("prop-watch-")

    # Inspect newly promoted Watchlist record (Tier 1, combined designations, chk-14 Flagged w/ loggedAt, 8-digit projectNo)
    open_new_watch_btn.click()
    assert page.locator("#modeWatchlistBtn").get_attribute("aria-selected") == "true"
    dossier_info = page.evaluate(
        """(wid) => {
            const raw = JSON.parse(localStorage.getItem('phpam:watchlist:v2') || '{}');
            const p = (raw.watchedProperties || []).find(x => x.id === wid);
            const chk14 = (p?.checklist || []).find(r => r.id === 'chk-14');
            const chk15 = (p?.checklist || []).find(r => r.id === 'chk-15');
            return {
                found: Boolean(p),
                tier: p?.tier,
                trackerStatus: p?.trackerStatus,
                designation: p?.designation || '',
                ilmsProjectNo: p?.ilmsProjectNo || '',
                projectNumbers: p?.searchTerms?.projectNumbers || [],
                chk14Status: chk14?.status,
                chk14LoggedAt: chk14?.checks?.[0]?.loggedAt || '',
                chk15Status: chk15?.status,
                chk15LoggedAt: chk15?.checks?.[0]?.loggedAt || '',
            };
        }""",
        new_watch_id,
    )
    assert dossier_info["found"] is True
    assert dossier_info["tier"] == "1"
    assert dossier_info["trackerStatus"] == "Active / High Priority"
    assert any(
        lbl in dossier_info["designation"]
        for lbl in ("Historic District", "City Landmark", "Good Brick")
    )
    assert coh_target["projNo"] in dossier_info["ilmsProjectNo"]
    assert coh_target["projNo"] in dossier_info["projectNumbers"]
    assert dossier_info["chk14Status"] == "Flagged"
    assert "T" in dossier_info["chk14LoggedAt"]

    # Also test promoting an unwatched DSHS asbestos alert card if available so chk-15 Flagged + loggedAt is verified E2E
    page.click("#modeActivityBtn")
    page.click("#activityRangeTabs [data-act-range='all']")
    page.select_option("#actStreamFilter", "permits")
    dshs_event_id = page.evaluate(
        """() => {
            const btn = document.querySelector('#detail .act-card[data-event-id^="permit:dshs:"] [data-act-promote-event]');
            return btn ? btn.dataset.actPromoteEvent : '';
        }"""
    )
    if dshs_event_id:
        page.locator(f"#detail button[data-act-promote-event='{dshs_event_id}']").click()
        dshs_wid = page.locator(
            f"#detail .act-card[data-event-id='{dshs_event_id}'] [data-act-open-watch]"
        ).get_attribute("data-act-open-watch")
        dshs_chk15 = page.evaluate(
            """(wid) => {
                const raw = JSON.parse(localStorage.getItem('phpam:watchlist:v2') || '{}');
                const p = (raw.watchedProperties || []).find(x => x.id === wid);
                const chk15 = (p?.checklist || []).find(r => r.id === 'chk-15');
                return { status: chk15?.status, loggedAt: chk15?.checks?.[0]?.loggedAt || '' };
            }""",
            dshs_wid,
        )
        assert dshs_chk15["status"] == "Flagged"
        assert "T" in dshs_chk15["loggedAt"]

    # Wait for debounced queueCloudSyncWatchProperty POST (~800ms timer) and assert Google Sheet payload
    page.wait_for_function("() => true")
    deadline_ms = 5000
    waited = 0
    while waited < deadline_ms and not sync_posts:
        page.wait_for_timeout(150)
        waited += 150
    assert len(sync_posts) >= 1
    wl_posts = [p for p in sync_posts if p.get("source_type") == "PROPERTY_WATCHLIST_UPDATE"]
    assert len(wl_posts) >= 1
    first_synced = json.loads(wl_posts[0]["source_citation"])
    assert first_synced["id"] == new_watch_id
    assert first_synced["tier"] == "1"
    assert coh_target["projNo"] in first_synced["searchTerms"]["projectNumbers"]


def test_activity_it4_boot_routing_precedence_deeplinks_and_delayed_sheet_race(
    guarded_page: Any,
    atlas_base_url: str,
) -> None:
    """IT-4: Verify clean 'agenda' default, phpam:lastMode reload restoration, URL parameter precedence, and async CSV tab-switch race safety."""
    page = guarded_page

    # 1. Clean browser storage defaults to 'agenda'
    page.goto(f"{atlas_base_url}/plat-monitor.html", wait_until="domcontentloaded")
    page.wait_for_selector("#app:not(.hidden)", timeout=15000)
    assert page.locator("#modeAgendaBtn").get_attribute("aria-selected") == "true"

    # 2. Switching to 'activity' saves phpam:lastMode and restores 'activity' on clean reload
    page.click("#modeActivityBtn")
    assert page.locator("#modeActivityBtn").get_attribute("aria-selected") == "true"
    assert page.evaluate("() => JSON.parse(localStorage.getItem('phpam:lastMode') || '\"\"')") == "activity"

    page.goto(f"{atlas_base_url}/plat-monitor.html", wait_until="domcontentloaded")
    page.wait_for_selector("#app:not(.hidden)", timeout=15000)
    assert page.locator("#modeActivityBtn").get_attribute("aria-selected") == "true"

    # 3. Every explicit URL query pattern overrides stored phpam:lastMode === 'activity'
    page.goto(f"{atlas_base_url}/plat-monitor.html?mode=watchlist", wait_until="domcontentloaded")
    page.wait_for_selector("#app:not(.hidden)", timeout=15000)
    assert page.locator("#modeWatchlistBtn").get_attribute("aria-selected") == "true"

    page.evaluate("() => localStorage.setItem('phpam:lastMode', JSON.stringify('activity'))")
    page.goto(f"{atlas_base_url}/plat-monitor.html?mode=agenda", wait_until="domcontentloaded")
    page.wait_for_selector("#app:not(.hidden)", timeout=15000)
    assert page.locator("#modeAgendaBtn").get_attribute("aria-selected") == "true"

    page.evaluate("() => localStorage.setItem('phpam:lastMode', JSON.stringify('agenda'))")
    page.goto(f"{atlas_base_url}/plat-monitor.html?tab=activity", wait_until="domcontentloaded")
    page.wait_for_selector("#app:not(.hidden)", timeout=15000)
    assert page.locator("#modeActivityBtn").get_attribute("aria-selected") == "true"

    page.evaluate("() => localStorage.setItem('phpam:lastMode', JSON.stringify('activity'))")
    page.goto(
        f"{atlas_base_url}/plat-monitor.html?watch=prop-kirby-mansion",
        wait_until="domcontentloaded",
    )
    page.wait_for_selector("#list .row", timeout=15000)
    assert page.locator("#modeWatchlistBtn").get_attribute("aria-selected") == "true"
    assert (
        page.locator("#list .row[data-watch-id='prop-kirby-mansion']").get_attribute(
            "aria-current"
        )
        == "true"
    )

    page.evaluate("() => localStorage.setItem('phpam:lastMode', JSON.stringify('activity'))")
    page.goto(f"{atlas_base_url}/plat-monitor.html?hcad=0152300000001", wait_until="domcontentloaded")
    page.wait_for_selector("#list .row", timeout=15000)
    assert page.locator("#modeWatchlistBtn").get_attribute("aria-selected") == "true"
    assert "2000 Smith" in (page.locator("#detail h2").text_content() or "")

    page.evaluate("() => localStorage.setItem('phpam:lastMode', JSON.stringify('activity'))")
    page.goto(
        f"{atlas_base_url}/plat-monitor.html?cycle=2026-10-29&app=2026-1659",
        wait_until="domcontentloaded",
    )
    page.wait_for_selector("#list .row", timeout=15000)
    assert page.locator("#modeAgendaBtn").get_attribute("aria-selected") == "true"
    assert "Amerimex Motors" in page.locator("#detail").inner_text()
    assert "2026-1659" in page.locator("#detail").inner_text()

    # Switching tabs after booting via query params strips stale URL params so reload/pickLive keeps the new mode
    page.click("#modeActivityBtn")
    assert page.locator("#modeActivityBtn").get_attribute("aria-selected") == "true"
    assert page.evaluate("() => window.location.search") == ""
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("#app:not(.hidden)", timeout=15000)
    assert page.locator("#modeActivityBtn").get_attribute("aria-selected") == "true"

    # 4. Rapid tab switch while background Google Sheet CSV request resolves 450ms later never snaps user back
    page.context.add_init_script(
        """() => {
            const origFetch = window.fetch.bind(window);
            window.fetch = async (...args) => {
                const u = String((args[0] && args[0].url) || args[0] || '');
                if (u.includes('spreadsheets') || u.includes('script.google.com')) {
                    await new Promise((resolve) => setTimeout(resolve, 450));
                }
                return origFetch(...args);
            };
        }"""
    )

    page.goto(f"{atlas_base_url}/plat-monitor.html?mode=activity", wait_until="domcontentloaded")
    page.wait_for_selector("#app:not(.hidden)", timeout=15000)
    page.click("#modeWatchlistBtn")
    assert page.locator("#modeWatchlistBtn").get_attribute("aria-selected") == "true"
    page.wait_for_timeout(700)
    assert page.locator("#modeWatchlistBtn").get_attribute("aria-selected") == "true"
    assert page.locator("#watchlistToolbar").is_visible()
    assert not page.locator("#activityToolbar").is_visible()


def test_activity_rp1_three_mode_cycling_and_detail_listener_isolation(
    guarded_page: Any,
    atlas_base_url: str,
) -> None:
    """RP-1: Verify repeated 3-mode switching leaves zero stale DOM panels, leaked headers/toolbars, or cross-triggered #detail listeners."""
    page = guarded_page
    _seed_staff_admin_session(page)

    page.goto(f"{atlas_base_url}/plat-monitor.html?mode=activity", wait_until="domcontentloaded")
    page.wait_for_selector("#app:not(.hidden)", timeout=15000)
    page.wait_for_function(
        "() => Number(document.querySelector('#actCntAll')?.textContent || '0') > 0",
        timeout=15000,
    )

    sequence = ["activity", "agenda", "watchlist", "activity", "watchlist", "agenda", "activity"]
    btn_map = {
        "activity": "#modeActivityBtn",
        "agenda": "#modeAgendaBtn",
        "watchlist": "#modeWatchlistBtn",
    }
    hdr_map = {
        "activity": "#activityHeaderControls",
        "agenda": "#agendaHeaderControls",
        "watchlist": "#watchlistHeaderControls",
    }
    tb_map = {
        "activity": "#activityToolbar",
        "agenda": "#agendaToolbar",
        "watchlist": "#watchlistToolbar",
    }

    for mode in sequence:
        page.click(btn_map[mode])
        assert page.locator(btn_map[mode]).get_attribute("aria-selected") == "true"
        for candidate in ("activity", "agenda", "watchlist"):
            expected_vis = candidate == mode
            assert page.locator(hdr_map[candidate]).is_visible() is expected_vis
            assert page.locator(tb_map[candidate]).is_visible() is expected_vis

        if mode == "agenda":
            rows = page.locator("#list .row[data-occ]")
            assert rows.count() >= 2
            second_occ = rows.nth(1).get_attribute("data-occ")
            second_app = rows.nth(1).get_attribute("data-app")
            rows.nth(1).click()
            assert rows.nth(1).get_attribute("aria-current") == "true"
            assert second_app and second_app in page.locator("#detail").inner_text()
            assert page.locator("#detail .act-card").count() == 0
            assert second_occ
        elif mode == "watchlist":
            wrows = page.locator("#list .row[data-watch-id]")
            assert wrows.count() >= 1
            pick_idx = 1 if wrows.count() >= 2 else 0
            wrows.nth(pick_idx).click()
            assert wrows.nth(pick_idx).get_attribute("aria-current") == "true"
            assert page.locator("#detail table.wl-table").count() >= 1
            assert page.locator("#detail .act-card").count() == 0
        else:
            assert page.locator("#list .act-run-row").count() >= 1
            assert page.locator("#detail table.wl-table").count() == 0
            # Dispatch synthetic input/change events inside #detail in activity mode to verify listener guard
            wl_before = page.locator("#modeWatchlistCount").inner_text().strip()
            page.evaluate(
                """() => {
                    const d = document.getElementById('detail');
                    d.dispatchEvent(new Event('input', { bubbles: true }));
                    d.dispatchEvent(new Event('change', { bubbles: true }));
                }"""
            )
            assert page.locator("#modeWatchlistCount").inner_text().strip() == wl_before


def test_pass2_typing_stability_agenda_presence_ph_linkage_broker_and_watchlist_removal(
    guarded_page: Any,
    atlas_base_url: str,
) -> None:
    """Verify Emily Pass #2 end-to-end UX contracts (F2-01, F2-02, F2-04..F2-10, F2-12)."""
    page = guarded_page
    _seed_staff_admin_session(page)

    page.goto(f"{atlas_base_url}/plat-monitor.html?mode=agenda", wait_until="domcontentloaded")
    page.wait_for_selector("#app:not(.hidden)", timeout=15000)
    page.wait_for_selector("#list .row", timeout=15000)

    # 1. F2-01: Default active Watchlist does NOT pre-populate unconfirmed DPV-26073533 (801 McGowen) or 2026-1585 (Montrose Residences)
    page.click("#modeWatchlistBtn")
    wl_ids = page.evaluate(
        "() => Array.from(document.querySelectorAll('#list .row[data-watch-id]')).map(r => r.dataset.watchId)"
    )
    assert "plat-DPV-26073533" not in wl_ids
    assert "plat-2026-1585" not in wl_ids

    # 2. F2-12: Broker / Listing Agent field displayed & persists through inline editor
    page.locator("#list .row[data-watch-id='prop-kirby-mansion']").click()
    assert "Broker / Listing Agent" in page.locator("#detail").inner_text()
    page.click("#toggleEditWatchPropBtn")
    page.fill("#we-broker", "Sample Listing Group (Commercial)")
    page.click("#saveEditWatchPropBtn")
    assert "Sample Listing Group (Commercial)" in page.locator("#detail").inner_text()

    # 3. F2-04 & F2-05: Verify 801 McGowen (0132680000011 vacant) and Montrose Residences (8 W Pierce parcels, 1490700010001..20004)
    page.click("#modeAgendaBtn")
    page.select_option("#cycleSelect", "2026-10-29")
    page.locator("#tabs [data-g='all']").click()
    page.fill("#q", "801 McGowen")
    page.wait_for_selector("#list .row[data-app='DPV-26073533']", timeout=5000)
    page.locator("#list .row[data-app='DPV-26073533']").click()
    mcgowen_txt = page.locator("#detail").inner_text()
    assert "0132680000011" in mcgowen_txt
    assert "2603 MILAM" not in mcgowen_txt.upper()

    page.fill("#q", "Montrose Residences")
    page.wait_for_selector("#list .row[data-app='2026-1585']", timeout=5000)
    page.locator("#list .row[data-app='2026-1585']").click()
    montrose_txt = page.locator("#detail").inner_text()
    assert "1226-1230 W PIERCE" in montrose_txt.upper()
    assert "1490700010001" in montrose_txt
    assert "1226 W CLAY" not in montrose_txt.upper()
    assert "0231440000010" not in montrose_txt

    # 4. F2-06: #fAgendaPresence filters cycle-filing-only items (Houston Chinese Church Reserves 2026-1620) vs posted meeting agenda items
    page.fill("#q", "")
    page.select_option("#fAgendaPresence", "not_on_agenda")
    assert page.locator("#list .row[data-app='2026-1620']").count() == 1
    page.locator("#list .row[data-app='2026-1620']").click()
    assert "Cycle filing only" in page.locator("#detail").inner_text() or "Not on posted" in page.locator("#detail").inner_text()

    page.select_option("#fAgendaPresence", "on_agenda")
    assert page.locator("#list .row[data-app='2026-1620']").count() == 0
    page.select_option("#fAgendaPresence", "")

    # 5. F2-07: PH-131 (Andores Rest) shows linkedAppNo 2026-1647 + Plat PDF; PH-132 (Aisla Villas) shows unposted notice helper
    page.fill("#q", "Andores Rest")
    page.wait_for_selector("#list .row[data-app='PH-131']", timeout=5000)
    page.locator("#list .row[data-app='PH-131']").click()
    ph131_txt = page.locator("#detail").inner_text()
    assert "2026-1647" in ph131_txt
    assert page.locator("#detail .links a", has_text="Plat PDF").count() >= 1

    page.fill("#q", "Aisla Villas")
    page.wait_for_selector("#list .row[data-app='PH-132']", timeout=5000)
    page.locator("#list .row[data-app='PH-132']").click()
    ph132_txt = page.locator("#detail").inner_text()
    assert "Section III Future Public Hearing Agenda Item" in ph132_txt
    assert page.locator("#detail a", has_text="COH Planning Notices").count() >= 1

    # 6. F2-09: Typing in #r-why must NOT mutate/replace #list DOM nodes or interrupt caret focus until blur/change,
    #    AND blurring onto another #list .row must switch selection on the very FIRST click (never swallowed by blur).
    page.fill("#q", "")
    page.wait_for_selector("#list .row[data-app='2026-1445']", timeout=5000)
    page.locator("#list .row[data-app='2026-1445']").click()
    page.evaluate("() => { window.__testRowRef = document.querySelector('#list .row[data-app=\"2026-1445\"]'); }")
    why_box = page.locator("#r-why")
    why_box.click()
    why_box.type("Typed smoothly without list redraw")
    typing_check = page.evaluate(
        """() => ({
            sameNode: window.__testRowRef === document.querySelector('#list .row[data-app="2026-1445"]'),
            activeId: document.activeElement ? document.activeElement.id : '',
            val: document.getElementById('r-why')?.value || '',
        })"""
    )
    assert typing_check["sameNode"] is True, "Typing in #r-why must NOT re-render/replace sidebar row nodes on input"
    assert typing_check["activeId"] == "r-why"
    assert "Typed smoothly without list redraw" in typing_check["val"]

    # Directly click a DIFFERENT row in #list while #r-why still holds active focus:
    # must commit + propagate AND select DPV-26073533 on the single first click without swallowing mousedown->mouseup.
    page.locator("#list .row[data-app='DPV-26073533']").click()
    assert "0132680000011" in page.locator("#detail").inner_text()

    page.select_option("#cycleSelect", "2026-10-15")
    page.fill("#q", "Citadel on Delano")
    page.locator("#list .row[data-app='2026-1445']").click()
    assert "Typed smoothly without list redraw" in page.locator("#r-why").input_value()

    # 7. F2-01 & F2-02: Promote 2026-1445 to Watchlist -> toggle Tier 1 -> Tier 2 on Watchlist -> syncs back to Biweekly Monitor -> Remove permanently via #removeFromWatchlistBtn
    page.locator("input[name='tier'][value='1']").click()
    page.fill("#q", "")
    page.locator("#tabs [data-g='t1']").click()
    page.locator("#list .row[data-app='2026-1445']").click()
    page.click("#confirmTierWatchBtn")
    page.click("#jumpWatchDossierBtn")

    assert page.locator("#detail [data-wl-inline-tier='2']").count() == 1
    page.click("#detail [data-wl-inline-tier='2']")
    page.click("#modeAgendaBtn")
    page.locator("#tabs [data-g='t2']").click()
    assert page.locator("#list .row[data-app='2026-1445']").count() == 1

    # Permanently remove from Watchlist via modal and verify zero resurrection on reload
    page.click("#modeWatchlistBtn")
    page.locator("#list .row[data-watch-id='plat-2026-1445']").click()
    cnt_before_rm = int(page.locator("#modeWatchlistCount").inner_text().strip())
    page.click("#removeFromWatchlistBtn")
    page.click("#rmWlConfirmDeleteBtn")
    page.wait_for_selector(".modal-back", state="detached", timeout=5000)
    assert int(page.locator("#modeWatchlistCount").inner_text().strip()) == cnt_before_rm - 1
    assert page.locator("#list .row[data-watch-id='plat-2026-1445']").count() == 0

    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("#modeWatchlistBtn", state="visible", timeout=15000)
    page.click("#modeWatchlistBtn")
    page.select_option("#wlStatusFilter", "ALL")
    assert page.locator("#list .row[data-watch-id='plat-2026-1445']").count() == 0

    # 8. F2-10: Main Atlas vacant lot live HCAD ArcGIS search fallback (3346 Scott -> 1330170010001) + #q= cold-boot
    def _mock_scott_vacant_parcels(route: Any) -> None:
        url = route.request.url
        dec = urllib.parse.unquote(url).lower()
        if "public_query/mapserver/0/query" in url.lower() and ("scott" in dec or "1330170010001" in dec):
            payload = {
                "features": [
                    {
                        "attributes": {
                            "HCAD_NUM": "1330170010001",
                            "owner": "SCOTT CORRIDOR LAND LLC",
                            "subdivision": "SCOTT STREET RESERVE",
                            "address": "3346 SCOTT ST",
                            "state_class": "C1",
                            "impr_val": 0,
                            "land_val": 425000,
                            "land_sqft": 7500,
                            "legal_lines": "LT 1 BLK 1 SCOTT STREET RESERVE",
                        },
                        "geometry": {
                            "rings": [
                                [
                                    [-95.3518, 29.7213],
                                    [-95.3515, 29.7213],
                                    [-95.3515, 29.7216],
                                    [-95.3518, 29.7216],
                                    [-95.3518, 29.7213],
                                ]
                            ]
                        },
                    }
                ]
            }
            route.fulfill(
                status=200,
                content_type="application/json; charset=utf-8",
                headers={"Access-Control-Allow-Origin": "*"},
                body=json.dumps(payload).encode("utf-8"),
            )
            return
        route.fallback()

    page.route("**/arcweb.hcad.org/**", _mock_scott_vacant_parcels)

    # 8a. Interactive live HCAD vacant-lot search via typing in #search-input at default viewport
    page.goto(f"{atlas_base_url}/index.html", wait_until="domcontentloaded")
    _wait_for_atlas_ready(page)
    page.fill("#search-input", "3346 Scott")
    page.wait_for_selector(
        "#search-results .search-result-item:has-text('3346 SCOTT ST')",
        state="visible",
        timeout=15000,
    )
    page.locator("#search-results .search-result-item:has-text('3346 SCOTT ST')").first.click()
    page.wait_for_selector(
        "#inspector-drawer:not(.hidden) #inspector-hcad-live-card[data-hcad-num='1330170010001']",
        state="attached",
        timeout=15000,
    )
    assert "3346 SCOTT ST" in page.locator("#inspector-drawer").inner_text().upper()

    # 8b. Cold-boot #q=3346%20Scott hash deep link resolution
    page.goto(f"{atlas_base_url}/index.html#q=3346%20Scott", wait_until="domcontentloaded")
    _wait_for_atlas_ready(page)
    page.wait_for_function(
        """() => {
            const items = document.querySelectorAll('#search-results .search-result-item, #search-results [data-hcad]');
            return document.body.innerText.includes('3346 SCOTT ST') || document.body.innerText.includes('1330170010001') || items.length > 0;
        }""",
        timeout=15000,
    )
    assert "3346 SCOTT ST" in page.locator("body").inner_text() or "1330170010001" in page.locator("body").inner_text()



