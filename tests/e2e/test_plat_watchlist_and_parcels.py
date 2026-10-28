from __future__ import annotations

import csv
import io
import json
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

    # Select All Recent Cycles and verify Montrose Residences (2026-1585)
    page.select_option("#cycleSelect", "all")
    page.fill("#q", "Montrose Residences")
    page.wait_for_selector("#list .row:has-text('1226 W CLAY')", timeout=5000)
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
    page.locator("#list .row").first.click()
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

    # Check #4 must now expose a copy pill for Kirby Block Holdings LLC, and Checks #3, #4, #7, #8, #9 must have zero street-address copy pills
    pill_audit = page.evaluate(
        """() => {
            const getRowCopies = (rid) => Array.from(
                document.querySelectorAll(`tr[data-row-id="${rid}"] [data-copy-text]`)
            ).map((b) => b.getAttribute('data-copy-text') || '');
            return {
                chk03: getRowCopies('chk-03'),
                chk04: getRowCopies('chk-04'),
                chk07: getRowCopies('chk-07'),
                chk08: getRowCopies('chk-08'),
                chk09: getRowCopies('chk-09'),
                chk10: getRowCopies('chk-10'),
            };
        }"""
    )
    assert "Kirby Block Holdings LLC" in pill_audit["chk04"]
    assert "2000 Smith" in pill_audit["chk10"]
    for no_addr_chk in ("chk03", "chk04", "chk07", "chk08", "chk09"):
        assert "2000 Smith" not in pill_audit[no_addr_chk]
        assert "2000 Smith St." not in pill_audit[no_addr_chk]

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

