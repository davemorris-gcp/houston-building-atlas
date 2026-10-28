"""Playwright E2E tests for multi-building campus dating, school_* linkage, and #<slug> deep links (IT-4, REG-2)."""

from __future__ import annotations

from typing import Any

from conftest import wait_for_atlas_ready


def _wait_for_inspector_containing(page: Any, expected_substring: str, timeout_ms: int = 12000) -> str:
    page.wait_for_function(
        """(needle) => {
            const d = document.getElementById('inspector-drawer');
            const b = document.getElementById('inspector-body');
            if (!d || d.classList.contains('hidden') || !b) return false;
            const txt = (b.innerText || b.textContent || '').toUpperCase();
            return txt.includes(String(needle).toUpperCase());
        }""",
        arg=expected_substring,
        timeout=timeout_ms,
    )
    return page.locator("#inspector-body").inner_text()


def _search_and_select_dropdown_item(
    page: Any,
    query: str,
    match_substring: str,
    jump_lng: float,
    jump_lat: float,
    expected_inspector_substring: str,
) -> str:
    """Runs live search bar query, clicks matching result via DOM click, and jumps camera cleanly under SwiftShader."""
    page.evaluate(
        """(q) => {
            const inp = document.getElementById('search-input');
            if (!inp) throw new Error('Missing #search-input');
            inp.value = q;
            inp.dispatchEvent(new Event('input', { bubbles: true }));
        }""",
        query,
    )
    page.wait_for_function(
        """(needle) => {
            const box = document.getElementById('search-results');
            if (!box || box.classList.contains('hidden')) return false;
            const items = Array.from(box.querySelectorAll('.search-result-item'));
            return items.some((el) => (el.innerText || el.textContent || '').toUpperCase().includes(String(needle).toUpperCase()));
        }""",
        arg=match_substring,
        timeout=10000,
    )
    page.evaluate(
        """({ needle, lng, lat }) => {
            const box = document.getElementById('search-results');
            const items = Array.from(box.querySelectorAll('.search-result-item'));
            const target = items.find((el) =>
                (el.innerText || el.textContent || '').toUpperCase().includes(String(needle).toUpperCase())
            );
            if (!target) throw new Error('Matching search result item disappeared: ' + needle);
            target.click();
            const map = window.atlasApp?.mapController?.map;
            if (map && Number.isFinite(lng) && Number.isFinite(lat)) {
                map.stop();
                map.jumpTo({ center: [lng, lat], zoom: 16.5, pitch: 0, bearing: 0 });
            }
        }""",
        {"needle": match_substring, "lng": jump_lng, "lat": jump_lat},
    )
    return _wait_for_inspector_containing(page, expected_inspector_substring)


def _navigate_hash_and_jump(
    page: Any,
    hash_str: str,
    jump_lng: float,
    jump_lat: float,
    *,
    zoom: float = 16.5,
) -> None:
    page.evaluate(
        """async ({ h, lng, lat, z }) => {
            try {
                window.atlasApp?.mapController?.map?.stop?.();
            } catch (_e) {}
            if (window.atlasApp) {
                window.atlasApp._lastHandledUrlKey = null;
            }
            window.location.hash = h;
            if (window.atlasApp && typeof window.atlasApp._handleUrlChange === 'function') {
                await window.atlasApp._handleUrlChange();
            }
            const map = window.atlasApp?.mapController?.map;
            if (map && Number.isFinite(lng) && Number.isFinite(lat)) {
                map.stop();
                map.jumpTo({ center: [lng, lat], zoom: z, pitch: 0, bearing: 0 });
            }
        }""",
        {"h": hash_str, "lng": jump_lng, "lat": jump_lat, "z": zoom},
    )


def test_cyfair_shared_parcel_search_and_distinct_sub_building_selection(
    guarded_page: Any,
    atlas_base_url: str,
    url_harvester: Any,
) -> None:
    """IT-4 (1): Cypress Ridge HS (school_612) and 7600 N Eldridge Pkwy select distinct footprints on 1223580020001."""
    page = guarded_page
    page.goto(f"{atlas_base_url}/index.html", wait_until="domcontentloaded")
    wait_for_atlas_ready(page)

    # 1. Search and click Cypress Ridge High School (school_612 -> 1223580020001#cy_ridge_hs_main)
    cy_txt = _search_and_select_dropdown_item(
        page,
        "Cypress Ridge High School",
        "CYPRESS RIDGE",
        -95.60502,
        29.89291,
        "7900 N ELDRIDGE PKWY",
    )
    assert "2002" in cy_txt
    assert "7900 N ELDRIDGE PKWY" in cy_txt.upper()
    url_harvester.harvest_from_locator(page.locator("#inspector-body"), "it4:cypress_ridge_hs")

    # Wait for regional PMTiles shard features on 1223580020001 to finish rendering across both z15 tiles
    page.evaluate(
        """() => {
            const map = window.atlasApp.mapController.map;
            map.stop();
            map.jumpTo({ center: [-95.60448, 29.88972], zoom: 15.3, pitch: 0, bearing: 0 });
        }"""
    )
    page.wait_for_function(
        """() => {
            const map = window.atlasApp?.mapController?.map;
            if (!map || !map.areTilesLoaded()) return false;
            const feats = map.queryRenderedFeatures();
            const ids = new Set();
            for (const f of feats) {
                const p = f.properties || {};
                if (String(p.hcad_num || '') === '1223580020001') {
                    ids.add(String(p.building_id || p.id || ''));
                }
            }
            return ids.size === 19;
        }""",
        timeout=20000,
    )
    cy_parcel_audit = page.evaluate(
        """() => {
            const map = window.atlasApp.mapController.map;
            const feats = map.queryRenderedFeatures();
            const ids = new Set();
            let dupOverridePolygons = 0;
            for (const f of feats) {
                const p = f.properties || {};
                if (String(p.hcad_num || '') === '1223580020001') {
                    ids.add(String(p.building_id || p.id || ''));
                    if (String(f.source || '').includes('override')) dupOverridePolygons++;
                }
            }
            return {
                uniqueFootprintCount: ids.size,
                dupOverridePolygons,
                hash: window.location.hash,
            };
        }"""
    )
    assert cy_parcel_audit["uniqueFootprintCount"] == 19, (
        f"Expected all 19 sibling footprints visible on 1223580020001, got {cy_parcel_audit}"
    )
    assert cy_parcel_audit["dupOverridePolygons"] == 0

    # 2. Search and click 7600 N Eldridge Pkwy -> selects southern Eldridge Transportation Center footprint
    bus_txt = _search_and_select_dropdown_item(
        page,
        "7600 N Eldridge Pkwy",
        "Eldridge Transportation Center",
        -95.603476,
        29.888208,
        "7600 N ELDRIDGE PKWY",
    )
    assert "2002" in bus_txt
    assert "7600 N ELDRIDGE PKWY" in bus_txt.upper()
    assert "TRANSPORTATION" in bus_txt.upper()
    bus_hash = page.evaluate("() => decodeURIComponent(window.location.hash)")
    assert "1223580020001#eldridge_trans_main" in bus_hash
    url_harvester.harvest_from_locator(page.locator("#inspector-body"), "it4:eldridge_trans_main")


def test_baytown_campus_corridor_multi_era_and_sequential_sub_buildings(
    guarded_page: Any,
    atlas_base_url: str,
    url_harvester: Any,
) -> None:
    """IT-4 (2): Baytown maxYear=1935 era filtering + distinct building dates across Lee HS, Lee College, and IMPACT ECHS."""
    page = guarded_page
    page.goto(
        f"{atlas_base_url}/index.html#maxYear=1935&lat=29.7304&lng=-94.9795&z=15.3",
        wait_until="domcontentloaded",
    )
    wait_for_atlas_ready(page)
    page.evaluate(
        """() => {
            const map = window.atlasApp.mapController.map;
            map.stop();
            map.jumpTo({ center: [-94.9795, 29.7304], zoom: 15.3, pitch: 0, bearing: 0 });
        }"""
    )
    page.wait_for_function(
        """() => {
            const map = window.atlasApp?.mapController?.map;
            if (!map || !map.areTilesLoaded()) return false;
            const ids = new Set(
                map.queryRenderedFeatures().map((f) => String(f.properties?.building_id || f.properties?.id || ''))
            );
            return ids.has('1468640010001#lee_hs_main_1928') && ids.has('0451440050245#carver_elem_main');
        }""",
        timeout=15000,
    )
    era_1935_ids = page.evaluate(
        """() => Array.from(
            new Set(
                window.atlasApp.mapController.map
                    .queryRenderedFeatures()
                    .map((f) => String(f.properties?.building_id || f.properties?.id || ''))
                    .filter(Boolean)
            )
        )"""
    )
    assert "1468640010001#lee_hs_main_1928" in era_1935_ids
    assert "0451440050245#carver_elem_main" in era_1935_ids
    assert "1468640010001#lee_hs_shop_1" not in era_1935_ids
    assert "0451440030163#lee_rundell_hall" not in era_1935_ids
    assert "0451440030163#impact_echs_main" not in era_1935_ids

    # Expand year filter back to 2026 and verify sequential selection across 1928, 1954, 1951, 1965, and 1981 structures
    _navigate_hash_and_jump(page, "#maxYear=2026&lat=29.7304&lng=-94.9795&z=15.3", -94.9795, 29.7304, zoom=15.3)

    # Robert E. Lee High School (school_705 -> 1928 Harry D. Payne)
    lee_hs_txt = _search_and_select_dropdown_item(
        page,
        "LEE High School",
        "1809 MARKET ST",
        -94.9859,
        29.7313,
        "1809 MARKET ST",
    )
    assert "1928" in lee_hs_txt
    assert "HARRY D. PAYNE" in lee_hs_txt.upper()

    # Detached 1954 Vocational Shop (#lee_hs_shop_1 -> 1954 Lowell Lammers)
    _navigate_hash_and_jump(page, "#id=1468640010001%23lee_hs_shop_1", -94.9873, 29.7314)
    shop_txt = _wait_for_inspector_containing(page, "Lowell Lammers")
    assert "1954" in shop_txt

    # Lee College Rundell Hall (1951) and Moler Hall (1965)
    rundell_txt = _search_and_select_dropdown_item(
        page,
        "Rundell Hall",
        "Rundell Hall",
        -94.97823,
        29.73231,
        "RUNDELL HALL",
    )
    assert "1951" in rundell_txt
    assert "200 LEE DR" in rundell_txt.upper()

    moler_txt = _search_and_select_dropdown_item(
        page,
        "Moler Hall",
        "Moler Hall",
        -94.97683,
        29.73178,
        "MOLER HALL",
    )
    assert "1965" in moler_txt

    # IMPACT Early College High School (school_706 -> 1981, 1415 MARKET ST)
    impact_txt = _search_and_select_dropdown_item(
        page,
        "IMPACT Early College",
        "IMPACT",
        -94.9822,
        29.7321,
        "1415 MARKET ST",
    )
    assert "1981" in impact_txt
    assert "1415 MARKET ST" in impact_txt.upper()
    url_harvester.harvest_from_locator(page.locator("#inspector-body"), "it4:impact_echs")


def test_cold_deeplink_boot_sub_building_ids_address_query_and_reg2_campus(
    guarded_page: Any,
    atlas_base_url: str,
    url_harvester: Any,
) -> None:
    """IT-4 (3) & REG-2: Cold deep links (#id=<hcad>%23<slug>, unencoded #id=<hcad>#<slug>, #q=...) and Rice # override."""
    page = guarded_page

    # 1. Cold boot with percent-encoded #id=1223580020001%23eldridge_trans_main
    page.goto(
        f"{atlas_base_url}/index.html#id=1223580020001%23eldridge_trans_main",
        wait_until="domcontentloaded",
    )
    wait_for_atlas_ready(page)
    page.evaluate(
        """() => {
            const map = window.atlasApp.mapController.map;
            map.stop();
            map.jumpTo({ center: [-95.603476, 29.888208], zoom: 16.5, pitch: 0, bearing: 0 });
        }"""
    )
    cold_bus_txt = _wait_for_inspector_containing(page, "7600 N ELDRIDGE PKWY")
    assert "2002" in cold_bus_txt
    assert "TRANSPORTATION" in cold_bus_txt.upper()

    # 2. Unencoded fragment #id=1223580020001#cy_ridge_hs_main
    _navigate_hash_and_jump(page, "#id=1223580020001#cy_ridge_hs_main", -95.60502, 29.89291)
    unenc_cy_txt = _wait_for_inspector_containing(page, "7900 N ELDRIDGE PKWY")
    assert "2002" in unenc_cy_txt
    assert "CYPRESS RIDGE" in unenc_cy_txt.upper()

    # 3. Street-address query deep link #q=7600%20N%20ELDRIDGE%20PKWY (ranked via address_shards row[6])
    _navigate_hash_and_jump(page, "#q=7600%20N%20ELDRIDGE%20PKWY", -95.603476, 29.888208)
    q_bus_txt = _wait_for_inspector_containing(page, "7600 N ELDRIDGE PKWY")
    assert "2002" in q_bus_txt
    assert "TRANSPORTATION" in q_bus_txt.upper()

    # 4. REG-2: Existing geometry-bearing Rice University # override continues resolving and opening cleanly
    rice_target = page.evaluate(
        """() => {
            const ovs = window.atlasApp?.mapController?.curatedOverrides || {};
            const key = Object.keys(ovs).find((k) => k.startsWith('0421790000001#') && ovs[k]?.geometry);
            if (!key) return null;
            const rec = ovs[key];
            const ring = rec.geometry?.coordinates?.[0] || [];
            const pt = Array.isArray(ring[0]?.[0]) ? ring[0][0] : ring[0];
            return {
                id: key,
                name: rec.building_name || rec.landmark_name || 'Rice',
                year: rec.year_built,
                lng: pt ? Number(pt[0]) : -95.4018,
                lat: pt ? Number(pt[1]) : 29.7174,
            };
        }"""
    )
    assert rice_target is not None, "Expected geometry-bearing Rice University 0421790000001#* override"
    _navigate_hash_and_jump(
        page,
        f"#id={rice_target['id'].replace('#', '%23')}",
        float(rice_target["lng"]),
        float(rice_target["lat"]),
    )
    rice_txt = _wait_for_inspector_containing(page, str(rice_target["year"]))
    assert str(rice_target["year"]) in rice_txt
    url_harvester.harvest_from_locator(page.locator("#inspector-body"), "reg2:rice_override")
