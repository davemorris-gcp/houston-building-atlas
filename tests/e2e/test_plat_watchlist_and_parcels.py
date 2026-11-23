from __future__ import annotations

import csv
import datetime as dt
import io
import json
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

from conftest import (
    DEFAULT_E2E_VIEWPORT,
    attach_error_monitors,
    configure_fast_e2e_context,
    wait_for_atlas_ready as _wait_for_atlas_ready,
)


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


def _build_default_seeded_watchlist_dossiers() -> dict[str, dict[str, Any]]:
    return {
        "prop-kirby-mansion": {
            "id": "prop-kirby-mansion",
            "parcelId": "0152300000001",
            "propertyName": "John Henry Kirby Mansion",
            "address": "2000 Smith St.",
            "hcadAccount": "0152300000001",
            "tier": "1",
            "tierConfirmed": True,
            "trackerStatus": "Active / High Priority",
            "yearBuilt": 1894,
            "designation": "National Register (individual); City Landmark",
            "broker": "Commercial Broker Group",
            "updatedAt": "2026-10-28T12:00:00Z",
            "lastAutoSweepAt": "2026-10-28T12:00:00Z",
            "searchTerms": {
                "entities": ["Smith St C Store LLC", "OB Texas Properties LLC"],
                "dbas": ["Brake Time"],
                "officers": ["Omair Bashir"],
                "legalKeywords": ["417", "SSBB"],
                "altAddresses": ["530 Pierce St."],
                "projectNumbers": ["26052089", "26090093"],
            },
            "checklist": [
                {
                    "id": "chk-01",
                    "title": "Commercial / Broker Listings",
                    "status": "In Progress",
                    "checks": [],
                },
                {
                    "id": "chk-02",
                    "title": "Site Visit & Photography",
                    "status": "In Progress",
                    "checks": [],
                },
                {
                    "id": "chk-03",
                    "title": "Assumed Names (DBA)",
                    "status": "To Do",
                    "checks": [],
                },
                {
                    "id": "chk-04",
                    "title": "TX Comptroller Tax",
                    "status": "Flagged",
                    "lastAutoSweepAt": "2026-10-28T12:00:00Z",
                    "checks": [
                        {
                            "date": "2026-10-28",
                            "discoveredAt": "2026-10-28T12:00:00Z",
                            "source": "auto-sweep",
                            "adverse": True,
                            "agencyRecordId": "txc:32102472084",
                            "result": "[AUTO-SWEEP] TX Comptroller Taxpayer #32102472084 active at 2000 Smith St",
                        }
                    ],
                },
                {
                    "id": "chk-05",
                    "title": "HCAD Ownership & Deed",
                    "status": "Flagged",
                    "lastAutoSweepAt": "2026-10-28T12:00:00Z",
                    "checks": [
                        {
                            "date": "2026-10-28",
                            "discoveredAt": "2026-10-28T12:00:00Z",
                            "source": "auto-sweep",
                            "adverse": True,
                            "agencyRecordId": "hcad:0152300000001",
                            "result": "[AUTO-SWEEP] HCAD Account 0152300000001 ownership verified",
                        }
                    ],
                },
                {
                    "id": "chk-06",
                    "title": "TABC Liquor Licensing",
                    "status": "Flagged",
                    "lastAutoSweepAt": "2026-10-28T12:00:00Z",
                    "checks": [
                        {
                            "date": "2026-10-28",
                            "discoveredAt": "2026-10-28T12:00:00Z",
                            "source": "auto-sweep",
                            "adverse": True,
                            "agencyRecordId": "tabc:200183169",
                            "result": "[AUTO-SWEEP] TABC License #200183169 active at 2000 Smith St",
                        }
                    ],
                },
                {
                    "id": "chk-08",
                    "title": "Open Records / GovQA",
                    "status": "To Do",
                    "checks": [],
                },
                {
                    "id": "chk-10",
                    "title": "TDLR TABS",
                    "status": "Flagged",
                    "lastAutoSweepAt": "2026-10-28T12:00:00Z",
                    "checks": [
                        {
                            "date": "2026-10-28",
                            "discoveredAt": "2026-10-28T12:00:00Z",
                            "source": "auto-sweep",
                            "adverse": True,
                            "agencyRecordId": "tabs:TABS20260001",
                            "result": "[AUTO-SWEEP] TDLR TABS project registered at 2000 Smith St",
                        }
                    ],
                },
                {
                    "id": "chk-11",
                    "title": "Council Member Outreach",
                    "status": "To Do",
                    "checks": [],
                },
                {
                    "id": "chk-12",
                    "title": "Media & Coalition",
                    "status": "To Do",
                    "checks": [],
                },
                {
                    "id": "chk-13",
                    "title": "Houston 311 & Dangerous Buildings",
                    "status": "To Do",
                    "checks": [],
                },
            ],
        },
        "prop-112-travis": {
            "id": "prop-112-travis",
            "parcelId": "0010160000011",
            "propertyName": "112 Travis Commercial Block",
            "address": "112 TRAVIS ST",
            "hcadAccount": "0010160000011",
            "tier": "1",
            "tierConfirmed": True,
            "trackerStatus": "Active / High Priority",
            "yearBuilt": 1882,
            "designation": "Market Square Historic District",
            "updatedAt": "2026-10-27T12:00:00Z",
            "checklist": [
                {
                    "id": "chk-13",
                    "title": "Houston 311 & Dangerous Buildings",
                    "status": "To Do",
                    "checks": [],
                }
            ],
        },
    }


def _seed_staff_admin_session(page: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "dossiers": _build_default_seeded_watchlist_dossiers(),
        "parcels": {},
        "decisions": {},
        "strategy": {},
        "watchedEntities": [],
        "userStates": {},
        "rpcLog": [],
    }
    session_json = json.dumps(
        {
            "authorized": True,
            "verifiedByWebhook": True,
            "email": "staff@preservation.example.org",
            "role": "staff",
            "displayLabel": "Preservation Staff",
            "adminPasskey": "test-passkey-2026",
            "supportsRoamingSync": True,
            "supportsPhase1Monitoring": True,
        }
    )
    page.context.add_init_script(
        f"""
        if (!sessionStorage.getItem('ph_atlas_admin_session')) {{
            sessionStorage.setItem('ph_atlas_admin_session', {json.dumps(session_json)});
            localStorage.setItem('ph_atlas_admin_session', {json.dumps(session_json)});
        }}
        """
    )

    def _mock_phase1_webhook(route: Any) -> None:
        if route.request.method.upper() != "POST":
            route.fulfill(
                status=200,
                content_type="text/csv; charset=utf-8",
                headers={"Access-Control-Allow-Origin": "*"},
                body=b"status,hcad_num,address,suggested_year_built\n",
            )
            return
        payload = json.loads(route.request.post_data or "{}")
        state["rpcLog"].append(payload)
        action = payload.get("action") or ""
        email = str(payload.get("admin_email") or "staff@preservation.example.org").lower()

        if action == "verify_admin":
            resp: dict[str, Any] = {
                "ok": True,
                "authorized": True,
                "email": email,
                "role": "staff",
                "displayLabel": "Preservation Staff",
                "supportsRoamingSync": True,
                "supportsPhase1Monitoring": True,
                "userState": state["userStates"].get(email),
            }
        elif action == "get_user_state":
            resp = {
                "ok": True,
                "authorized": True,
                "role": "staff",
                "supportsRoamingSync": True,
                "email": email,
                "userState": state["userStates"].get(email),
            }
        elif action == "put_user_state":
            state["userStates"][email] = payload.get("user_state") or {}
            resp = {
                "ok": True,
                "authorized": True,
                "role": "staff",
                "supportsRoamingSync": True,
                "email": email,
                "userState": state["userStates"][email],
            }
        elif action == "pull_monitoring_state":
            resp = {
                "ok": True,
                "authorized": True,
                "role": "staff",
                "email": email,
                "areas": [],
                "parcels": list(state["parcels"].values()),
                "decisions": list(state["decisions"].values()),
                "events": [],
                "watchlistDossiers": [
                    {"watch_property_id": wid, "dossier_json": json.dumps(dobj)}
                    for wid, dobj in state["dossiers"].items()
                ],
                "staffOnly": {
                    "parcelStrategy": list(state["strategy"].values()),
                    "watchedEntities": list(state["watchedEntities"]),
                },
                "pendingModerationCount": 0,
            }
        elif action == "upsert_watchlist_dossier":
            d_obj = payload.get("dossier") or {}
            wid = str(d_obj.get("id") or payload.get("watch_property_id") or "")
            if wid:
                state["dossiers"][wid] = d_obj
            resp = {"ok": True, "authorized": True, "watch_property_id": wid}
        elif action == "upsert_parcel_decision":
            patch = payload.get("patch") or payload.get("decision") or {}
            pid = str(patch.get("parcel_id") or payload.get("parcel_id") or "")
            if pid:
                merged_p = {**(state["parcels"].get(pid) or {"parcel_id": pid}), **patch}
                state["parcels"][pid] = merged_p
                state["decisions"][pid] = {
                    **(state["decisions"].get(pid) or {"decision_id": f"DEC:{pid}", "parcel_id": pid}),
                    **patch,
                    "decided_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
                }
            resp = {
                "ok": True,
                "authorized": True,
                "role": "staff",
                "parcel": state["parcels"].get(pid, {}),
                "decision": state["decisions"].get(pid, {}),
            }
        else:
            resp = {"ok": True, "authorized": True}

        route.fulfill(
            status=200,
            content_type="application/json; charset=utf-8",
            headers={"Access-Control-Allow-Origin": "*"},
            body=json.dumps(resp).encode("utf-8"),
        )

    page.route("**/script.google.com/macros/**", _mock_phase1_webhook)
    return state


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

    # Verify Tier 3 and Tier 4 tabs exist in #tabs and threat filter default selected option is non-blank
    tabs_text = page.locator("#tabs").inner_text()
    assert "Tier 3" in tabs_text
    assert "Tier 4" in tabs_text
    f_threat_selected = page.evaluate(
        "() => document.querySelector('#fThreat')?.selectedOptions?.[0]?.textContent?.trim() || ''"
    )
    assert f_threat_selected == "All threat categories"

    # Select All Recent Cycles and verify Montrose Residences auto-merges across cycles (F3-03),
    # then select 2026-10-29 to verify the Notice-only occurrence (2026-10-29::2026-1585) has no Plat PDF
    page.select_option("#cycleSelect", "all")
    cycle_opts_text = page.locator("#cycleSelect").inner_text()
    assert "(Early GIS Preview)" in cycle_opts_text
    page.locator("#tabs [data-g='all']").click()
    page.fill("#q", "Montrose Residences")
    page.wait_for_selector(
        "#list .row:has-text('1226 W Pierce'), #list .row:has-text('1226-1230 W Pierce')",
        timeout=5000,
    )
    assert page.locator("#list .row").count() == 1
    page.select_option("#cycleSelect", "2026-10-29")
    page.wait_for_selector("#list .row[data-occ='2026-10-29::2026-1585']", timeout=5000)
    page.locator("#list .row[data-occ='2026-10-29::2026-1585']").first.click()
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
    page.select_option("#cycleSelect", "all")

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
    page.locator('#tabs [data-g="check"]').click()
    page.wait_for_selector("#list .row", timeout=10000)

    # Pick an unconfirmed item not currently on the Watchlist (querying live rows by data-occ so
    # re-rendering #list never detaches remaining row references)
    initial_wl_count = int(page.locator("#modeWatchlistCount").inner_text().strip())
    target_occ = page.evaluate(
        """() => {
            const occs = Array.from(document.querySelectorAll('#list .row[data-occ]'))
                .filter(r => !r.innerText.includes('Montrose Residences') && !r.innerText.includes('801 McGowen'))
                .map(r => r.dataset.occ || '')
                .filter(Boolean);
            for (const occ of occs) {
                const liveRow = document.querySelector(`#list .row[data-occ="${CSS.escape(occ)}"]`);
                if (!liveRow) continue;
                liveRow.click();
                if (document.getElementById('jumpWatchDossierBtn')) {
                    continue;
                }
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
            return '';
        }"""
    )
    assert target_occ != ""

    # Clicking Tier 2 radio alone must NOT auto-add to Watchlist before clicking #confirmTierWatchBtn
    assert int(page.locator("#modeWatchlistCount").inner_text().strip()) == initial_wl_count

    # Click explicit Confirm Tier & Add to Watchlist -> Watchlist count increases by 1
    page.click("#confirmTierWatchBtn")
    assert int(page.locator("#modeWatchlistCount").inner_text().strip()) == initial_wl_count + 1

    # Untouched plat-* dossier downgraded to Tier 3 -> moves out of Needs Screening into Tier 3 tab, is removed cleanly from active Watchlist, and does NOT resurrect even when stale cloud row is returned
    target_app = target_occ.split("::", 1)[1] if "::" in target_occ else target_occ
    page.locator('input[name="tier"][value="3"]').click()
    if page.locator("#confirmTier3Btn").count() > 0:
        page.click("#confirmTier3Btn")
    assert int(page.locator("#modeWatchlistCount").inner_text().strip()) == initial_wl_count
    assert page.locator(f'#list .row[data-occ="{target_occ}"]').count() == 0

    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("#app:not(.hidden)", timeout=15000)
    page.wait_for_selector("#list .row", timeout=15000)
    assert int(page.locator("#modeWatchlistCount").inner_text().strip()) == initial_wl_count

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

    # Reclassify to Tier 4 via quick tier button -> transitions to Archived (Tier 4) and syncs to Tier 4 tab
    page.click('[data-wl-inline-tier="4"]')
    page.select_option("#wlStatusFilter", "Archived (Tier 4)")
    assert page.locator(f'#list .row[data-watch-id="plat-{target_app}"]').count() == 1
    page.click("#jumpBackToPlatItemBtn")
    page.locator('#tabs [data-g="t4"]').click()
    assert page.locator(f'#list .row[data-occ="{target_occ}"]').count() == 1


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
    kirby_card_txt = page.locator("#list .row", has_text="2000 Smith").first.inner_text()
    assert "2000 SMITH" in kirby_card_txt.upper()

    # Select Kirby Mansion (2000 Smith St.)
    page.locator("#list .row", has_text="2000 Smith").first.click()
    assert "2000 SMITH" in (page.locator("#detail h2").text_content() or "").upper()

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
    """Verify 3-way Watchlist reconciliation across baseline [AUTO-SWEEP] + authenticated pull_monitoring_state dossier + ?hcad= deep-link."""
    page = guarded_page
    mock_state = _seed_staff_admin_session(page)

    # Seed baseline static/cached Kirby Mansion with [AUTO-SWEEP] findings & snowball terms in role cache,
    # while cloud pull_monitoring_state returns a newer human edit omitting searchTerms (RP-6)
    baseline_kirby = mock_state["dossiers"]["prop-kirby-mansion"]
    page.context.add_init_script(
        f"""
        localStorage.setItem(
            'phpam:auth_cache:staff@preservation.example.org',
            JSON.stringify({{
                schemaVersion: '1.0',
                role: 'staff',
                cachedAt: '2026-10-28T12:00:00Z',
                areas: [],
                parcels: [],
                decisions: [],
                events: [],
                watchlistDossiers: [{{ watch_property_id: 'prop-kirby-mansion', dossier_json: {json.dumps(json.dumps(baseline_kirby))} }}]
            }})
        );
        """
    )

    mock_state["dossiers"]["prop-kirby-mansion"] = {
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
                "id": "chk-04",
                "status": "Flagged",
                "lastAutoSweepAt": "2026-10-28T12:00:00Z",
                "checks": baseline_kirby["checklist"][3]["checks"],
            },
            {
                "id": "chk-05",
                "status": "Flagged",
                "lastAutoSweepAt": "2026-10-28T12:00:00Z",
                "checks": baseline_kirby["checklist"][4]["checks"],
            },
            {
                "id": "chk-06",
                "status": "Flagged",
                "lastAutoSweepAt": "2026-10-28T12:00:00Z",
                "checks": baseline_kirby["checklist"][5]["checks"],
            },
        ],
    }

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

    # Public visitor sees public streams (agenda, permits) and zero leaked private streams (watchlist, staff)
    for stream_val in ("agenda", "permits"):
        page.select_option("#actStreamFilter", stream_val)
        cards_cnt = page.locator("#detail .act-card").count()
        assert cards_cnt >= 1
        wrong_stream = page.evaluate(
            "(sv) => Array.from(document.querySelectorAll('#detail .act-card')).filter(c => c.dataset.stream !== sv).length",
            stream_val,
        )
        assert wrong_stream == 0
    for private_stream in ("watchlist", "staff"):
        page.select_option("#actStreamFilter", private_stream)
        assert page.locator("#detail .act-card").count() == 0
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
    page.wait_for_function(
        "() => (document.getElementById('activitySweepHealthBadge')?.innerText || '').includes('8/8')",
        timeout=15000,
    )
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
    mock_state = _seed_staff_admin_session(page)

    # Seed an explicit baseline watermark just prior to the latest biweekly Plat Agenda cycle and
    # authenticated Watchlist sweep so both modes reliably exhibit unread badges.
    data_dir = Path(__file__).resolve().parents[2] / "app" / "public" / "data"
    act_doc = json.loads((data_dir / "monitor_activity_log.json").read_text(encoding="utf-8"))
    plat_doc = json.loads((data_dir / "plat_agenda_monitor.json").read_text(encoding="utf-8"))
    default_cycle = (plat_doc.get("cycleOrder") or [None])[0]
    default_reviews = (
        (plat_doc.get("cycles") or {}).get(default_cycle, {}).get("reviews") or {}
        if default_cycle
        else {}
    )
    visible_agenda_occs = {
        it.get("occurrenceKey")
        for it in plat_doc.get("items", [])
        if it.get("occurrenceKey")
        and it.get("onMeetingAgenda") is not False
        and str(it.get("city") or "").strip().lower() != "etj"
        and (it.get("_c") or {}).get("group") != "out"
        and str((default_reviews.get(it.get("app")) or {}).get("tier") or "") != "4"
        and (not default_cycle or str(it.get("occurrenceKey", "")).startswith(f"{default_cycle}::"))
    }
    agenda_dates = sorted(
        str(e.get("localDate") or e.get("date") or "")[:10]
        for e in act_doc.get("events", [])
        if e.get("stream") == "agenda"
        and e.get("occKey") in visible_agenda_occs
        and (e.get("localDate") or e.get("date"))
    )
    earliest_latest_day = min(agenda_dates[-1], "2026-10-28")
    seed_watermark_iso = f"{earliest_latest_day}T00:00:00Z"
    page.context.add_init_script(
        f"""
        if (localStorage.getItem('phpam:caughtUpAt') === null) {{
            localStorage.setItem('phpam:caughtUpAt', JSON.stringify({json.dumps(seed_watermark_iso)}));
        }}
        """
    )

    page.goto(f"{atlas_base_url}/plat-monitor.html?mode=activity", wait_until="domcontentloaded")
    page.wait_for_selector("#modeActivityBtn[aria-selected='true']", timeout=15000)
    page.wait_for_function(
        "() => Number(document.querySelector('#actCntAll')?.textContent || '0') > 0 && Number(document.querySelector('#modeWatchlistCount')?.textContent || '0') >= 2",
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
    assert "2000 SMITH" in (page.locator("#detail h2").text_content() or "").upper()

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

    # 7. Inject a post-watermark authenticated cloud dossier edit -> surfaces strictly as 1 new unread item
    future_iso = page.evaluate("() => new Date(Date.now() + 120000).toISOString()")
    updated_kirby = dict(mock_state["dossiers"]["prop-kirby-mansion"])
    updated_kirby["updatedAt"] = future_iso
    updated_kirby["checklist"] = [
        *(
            [r for r in updated_kirby.get("checklist", []) if r.get("id") != "chk-03"]
        ),
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
        },
    ]
    mock_state["dossiers"]["prop-kirby-mansion"] = updated_kirby
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
    """IT-3: Verify signed-out Watchlist privacy, unauthenticated staff modal gate, post-sign-in HCAD deduplication, and 1-click Permit/DSHS -> Watchlist promotion with Cloud RPC POST."""
    page = guarded_page
    sync_posts: list[dict[str, Any]] = []
    cloud_dossiers = _build_default_seeded_watchlist_dossiers()

    def _stub_apps_script(route: Any) -> None:
        if route.request.method.upper() == "POST":
            raw = route.request.post_data or "{}"
            payload = json.loads(raw)
            act = payload.get("action")
            if act == "verify_admin":
                route.fulfill(
                    status=200,
                    content_type="application/json; charset=utf-8",
                    headers={"Access-Control-Allow-Origin": "*"},
                    body=json.dumps(
                        {
                            "ok": True,
                            "authorized": True,
                            "email": payload.get("admin_email") or "staff@preservation.example.org",
                            "role": "staff",
                            "displayLabel": "Preservation Staff",
                            "supportsRoamingSync": True,
                            "supportsPhase1Monitoring": True,
                        }
                    ),
                )
                return
            if act == "pull_monitoring_state":
                route.fulfill(
                    status=200,
                    content_type="application/json; charset=utf-8",
                    headers={"Access-Control-Allow-Origin": "*"},
                    body=json.dumps(
                        {
                            "ok": True,
                            "authorized": True,
                            "role": "staff",
                            "email": "staff@preservation.example.org",
                            "areas": [],
                            "parcels": [],
                            "decisions": [],
                            "events": [],
                            "watchlistDossiers": [
                                {"watch_property_id": wid, "dossier_json": json.dumps(dobj)}
                                for wid, dobj in cloud_dossiers.items()
                            ],
                            "staffOnly": {"parcelStrategy": [], "watchedEntities": []},
                            "pendingModerationCount": 0,
                        }
                    ),
                )
                return
            if act == "upsert_watchlist_dossier":
                d_obj = payload.get("dossier") or {}
                wid = str(d_obj.get("id") or payload.get("watch_property_id") or "")
                if wid:
                    cloud_dossiers[wid] = d_obj
                sync_posts.append(payload)
                route.fulfill(
                    status=200,
                    content_type="application/json; charset=utf-8",
                    headers={"Access-Control-Allow-Origin": "*"},
                    body=json.dumps({"ok": True, "authorized": True, "watch_property_id": wid}),
                )
                return
            sync_posts.append(payload)
            route.fulfill(
                status=200,
                content_type="application/json; charset=utf-8",
                headers={"Access-Control-Allow-Origin": "*"},
                body=b'{"ok":true,"authorized":true}',
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

    # 1. Before sign-in, private Watchlist membership is NOT exposed to unauthenticated public visitors
    assert int(page.locator("#modeWatchlistCount").inner_text().strip()) == 0

    # 2. Pick an unwatched historic COH demolition/sewer card with an 8-digit project # + historic designation
    coh_target = page.evaluate(
        """() => {
            const cards = Array.from(document.querySelectorAll('#detail .act-card[data-event-id^="permit:coh:"]'));
            for (const c of cards) {
                const btn = c.querySelector('[data-act-promote-event]');
                const m = (c.innerText || '').match(/#(\\d{8})/);
                if (btn && m && !c.innerText.includes('0152300000001') && (c.innerText.includes('Historic District') || c.innerText.includes('Landmark') || c.innerText.includes('Good Brick'))) {
                    return { eventId: btn.dataset.actPromoteEvent, projNo: m[1], hcadMatch: ((c.innerText || '').match(/HCAD\\s+(\\d{13})/) || [])[1] || '' };
                }
            }
            return null;
        }"""
    )
    assert coh_target is not None, "Expected at least one unwatched designated COH permit alert card"

    # Signed-out click MUST open staff auth modal without creating a Watchlist entry
    page.locator(f"#detail button[data-act-promote-event='{coh_target['eventId']}']").click()
    assert page.locator(".modal-back .modal[role='dialog'] #sa-email").is_visible()
    assert int(page.locator("#modeWatchlistCount").inner_text().strip()) == 0

    # 3. Sign in directly through the staff verification modal -> hydrates cloud Watchlist & deduplicates 2000 Smith St
    page.fill("#sa-email", "staff@preservation.example.org")
    page.fill("#sa-passkey", "test-passkey-2026")
    page.click("#saVerify")
    page.wait_for_selector(".modal-back", state="detached", timeout=10000)
    page.wait_for_function(
        "() => Number(document.querySelector('#modeWatchlistCount')?.textContent || '0') >= 2",
        timeout=10000,
    )
    initial_wl_cnt = int(page.locator("#modeWatchlistCount").inner_text().strip())

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

    # Inspect newly promoted Watchlist record in role-scoped auth cache (legacy phpam:watchlist:v2 must stay null!)
    open_new_watch_btn.click()
    assert page.locator("#modeWatchlistBtn").get_attribute("aria-selected") == "true"
    dossier_info = page.evaluate(
        """(wid) => {
            const legacyRaw = localStorage.getItem('phpam:watchlist:v2');
            const cacheObj = JSON.parse(localStorage.getItem('phpam:auth_cache:staff@preservation.example.org') || '{}');
            const row = (cacheObj.watchlistDossiers || []).find(r => (r.watch_property_id || r.watch_id) === wid);
            const p = row && row.dossier_json ? JSON.parse(row.dossier_json) : row;
            const chk14 = (p?.checklist || []).find(r => r.id === 'chk-14');
            const chk15 = (p?.checklist || []).find(r => r.id === 'chk-15');
            return {
                legacyIsNull: legacyRaw === null,
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
    assert dossier_info["legacyIsNull"] is True
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
                const cacheObj = JSON.parse(localStorage.getItem('phpam:auth_cache:staff@preservation.example.org') || '{}');
                const row = (cacheObj.watchlistDossiers || []).find(r => (r.watch_property_id || r.watch_id) === wid);
                const p = row && row.dossier_json ? JSON.parse(row.dossier_json) : row;
                const chk15 = (p?.checklist || []).find(r => r.id === 'chk-15');
                return { status: chk15?.status, loggedAt: chk15?.checks?.[0]?.loggedAt || '' };
            }""",
            dshs_wid,
        )
        assert dshs_chk15["status"] == "Flagged"
        assert "T" in dshs_chk15["loggedAt"]

    # Wait for debounced queueCloudSyncWatchProperty POST (~800ms timer) and assert Phase 1 upsert_watchlist_dossier payload
    deadline_ms = 5000
    waited = 0
    while waited < deadline_ms and not any(p.get("action") == "upsert_watchlist_dossier" for p in sync_posts):
        page.wait_for_timeout(150)
        waited += 150
    wl_posts = [p for p in sync_posts if p.get("action") == "upsert_watchlist_dossier"]
    assert len(wl_posts) >= 1
    first_synced = wl_posts[0]["dossier"]
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

    # Signed-out ?watch= / ?hcad= routes to Watchlist mode and renders the calm privacy gate
    page.evaluate("() => localStorage.setItem('phpam:lastMode', JSON.stringify('activity'))")
    page.goto(
        f"{atlas_base_url}/plat-monitor.html?watch=prop-kirby-mansion",
        wait_until="domcontentloaded",
    )
    page.wait_for_selector("#watchlistSignedOutGate", state="visible", timeout=15000)
    assert page.locator("#modeWatchlistBtn").get_attribute("aria-selected") == "true"

    # Once signed in, ?watch=prop-kirby-mansion and ?hcad=0152300000001 select Kirby Mansion directly
    _seed_staff_admin_session(page)
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
    assert "2000 SMITH" in (page.locator("#detail h2").text_content() or "").upper()

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
        "() => Number(document.querySelector('#actCntAll')?.textContent || '0') > 0 && Number(document.querySelector('#modeWatchlistCount')?.textContent || '0') >= 2",
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
    """Verify Pass #2 end-to-end UX contracts (F2-01, F2-02, F2-04..F2-10, F2-12)."""
    page = guarded_page
    _seed_staff_admin_session(page)

    plat_json_path = Path(__file__).resolve().parents[2] / "app" / "public" / "data" / "plat_agenda_monitor.json"

    unposted_ph_apps: list[str] = []

    def _ensure_unposted_ph_fixture(route: Any) -> None:
        doc = json.loads(plat_json_path.read_text(encoding="utf-8"))
        cyc = (doc.get("cycles") or {}).get("2026-10-29")
        if isinstance(cyc, dict) and isinstance(cyc.get("items"), list):
            matching_unposted = [
                str(it.get("app") or "")
                for it in cyc["items"]
                if str(it.get("app") or "").upper().startswith("PH-")
                and not str(it.get("pdf") or "").startswith("http")
                and not str(it.get("noticePdf") or "").startswith("http")
                and not str(it.get("hcad") or "").strip()
            ]
            if not matching_unposted:
                cyc["items"].append(
                    {
                        "name": "Unposted Section III Test Replat",
                        "app": "PH-999",
                        "pcDate": "46324.6041666667",
                        "pcDateIso": "2026-10-29",
                        "submitted": "46324.6041666667",
                        "submittedIso": "2026-10-29",
                        "hearingDateIso": "2026-12-03",
                        "type": "Class 3 Replat - Establish Public Hearing Date (C3N)",
                        "special": "Establish Public Hearing Date (Dec 3, 2026)",
                        "section": "III. Establish Public Hearing Date (Dec 3, 2026)",
                        "council": "",
                        "city": "City",
                        "county": "Harris",
                        "precinct": "",
                        "keymap": "",
                        "tract": "",
                        "zip": "",
                        "isd": "",
                        "prevSub": "Unposted Section III Test Replat",
                        "tirz": "",
                        "mgmt": "",
                        "hd": "",
                        "superN": "",
                        "park": "",
                        "thoroughfare": "",
                        "landUse": "Single Family Residential Replat",
                        "acreage": "",
                        "lots": "",
                        "hcad": "",
                        "developer": "",
                        "org": "",
                        "applicant": "",
                        "phone": "",
                        "pdf": "",
                        "noticePdf": "",
                        "noticeLocation": "",
                        "varianceSummary": "",
                        "agendaItemNo": "Sec. III",
                        "deferral": "",
                        "onMeetingAgenda": True,
                        "occurrenceKey": "2026-10-29::PH-999",
                        "atlas": {
                            "addresses": [],
                            "primaryAddress": "",
                            "earliestYearBuilt": 0,
                            "allYearsBuilt": [],
                            "parcelBreakdown": [],
                            "lng": None,
                            "lat": None,
                            "coordSource": "",
                            "buildingId": "",
                            "buildingName": "",
                            "subdivisionMatch": "",
                            "subdivisionId": "",
                            "subdivisionEarliestYear": None,
                            "subdivisionMedianYear": None,
                            "subdivisionPre1940Count": 0,
                            "subdivisionPre1965Count": 0,
                            "superNeighborhoodMatch": "",
                            "superNeighborhoodId": "",
                            "superNeighborhoodEarliestYear": None,
                            "superNeighborhoodMedianYear": None,
                            "designations": [],
                            "smlsProtections": [],
                            "hasActivePermit": False,
                            "activePermitHeadline": "",
                        },
                        "_c": {
                            "group": "check",
                            "reason": "",
                            "originalReason": "",
                            "overriddenFromOut": False,
                            "flags": ["hearing"],
                            "score": 2,
                            "code": "C3N",
                            "inCity": True,
                        },
                        "continuityKey": "",
                        "priorCycles": [],
                    }
                )
                matching_unposted.append("PH-999")
            unposted_ph_apps.clear()
            unposted_ph_apps.extend(matching_unposted)
        route.fulfill(
            status=200,
            content_type="application/json; charset=utf-8",
            body=json.dumps(doc).encode("utf-8"),
        )

    page.route("**/data/plat_agenda_monitor.json*", _ensure_unposted_ph_fixture)

    page.goto(f"{atlas_base_url}/plat-monitor.html?mode=agenda", wait_until="domcontentloaded")
    page.wait_for_selector("#app:not(.hidden)", timeout=15000)
    page.wait_for_selector("#list .row", timeout=15000)

    # 1. F2-01: Default active Watchlist does NOT pre-populate unconfirmed DPV-26073533 (801 McGowen)
    page.click("#modeWatchlistBtn")
    wl_ids = page.evaluate(
        "() => Array.from(document.querySelectorAll('#list .row[data-watch-id]')).map(r => r.dataset.watchId)"
    )
    assert "plat-DPV-26073533" not in wl_ids

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
    assert (
        "1226 W PIERCE" in montrose_txt.upper()
        or "1226-1230 W PIERCE" in montrose_txt.upper()
    )
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

    # 5. F2-07: Andores Rest shows linkedAppNo 2026-1647 + Plat PDF (both pre- and post-Notice PDF posting);
    #    Aisla Villas shows either unposted PH-* notice helper or graduated Public Hearing Notice PDF;
    #    and unposted PH-* Section III items render the Section III Future Public Hearing Agenda Item helper.
    page.fill("#q", "Andores Rest")
    andores_row = page.locator("#list .row", has_text="Andores Rest").first
    andores_row.wait_for(state="visible", timeout=5000)
    andores_row.click()
    ph131_txt = page.locator("#detail").inner_text()
    assert "2026-1647" in ph131_txt
    assert page.locator("#detail .links a", has_text="Plat PDF").count() >= 1

    page.fill("#q", "Aisla Villas")
    aisla_row = page.locator("#list .row", has_text="Aisla Villas").first
    aisla_row.wait_for(state="visible", timeout=5000)
    aisla_app = str(aisla_row.get_attribute("data-app") or "")
    aisla_row.click()
    ph132_txt = page.locator("#detail").inner_text()
    if aisla_app.upper().startswith("PH-"):
        assert "Section III Future Public Hearing Agenda Item" in ph132_txt
        assert page.locator("#detail a", has_text="COH Planning Notices").count() >= 1
    else:
        assert "Public Hearing Notice PDF" in ph132_txt
        assert page.locator("#detail a", has_text="Public Hearing Notice PDF").count() >= 1

    page.fill("#q", "")
    target_unposted_app = unposted_ph_apps[0] if unposted_ph_apps else "PH-999"
    unposted_ph_row = page.locator(f"#list .row[data-app='{target_unposted_app}']").first
    unposted_ph_row.wait_for(state="visible", timeout=5000)
    unposted_ph_row.click()
    unposted_ph_txt = page.locator("#detail").inner_text()
    assert "Section III Future Public Hearing Agenda Item" in unposted_ph_txt
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


def test_cross_computer_roaming_sync_keep_unread_pin_undo_and_signout_isolation(
    browser: Any,
    atlas_base_url: str,
) -> None:
    """E2E multi-context cross-computer Google Workspace roaming sync:
    Browser A (office desktop) signs into Staff Sync, marks a Property Group + single item read,
    pins 1 item Keep Unread, sets stream/grouping preferences, clicks Mark Caught Up, tests Undo,
    and re-marks Caught Up -> Browser B (fresh home computer context) signs in as the same staff
    email and immediately hydrates exact read/unread state, Keep Unread pin, and preferences.
    """
    cloud_user_states: dict[str, dict[str, Any]] = {}
    put_calls: list[dict[str, Any]] = []
    log_json_path = (
        Path(__file__).resolve().parents[2] / "app" / "public" / "data" / "monitor_activity_log.json"
    )
    now_utc = dt.datetime.now(dt.timezone.utc)
    today_iso_date = (now_utc - dt.timedelta(hours=6)).strftime("%Y-%m-%d")
    fresh_ts_base = (now_utc - dt.timedelta(minutes=30)).replace(microsecond=0)

    def _serve_fresh_activity_log(route: Any) -> None:
        doc = json.loads(log_json_path.read_text(encoding="utf-8"))
        t1 = (fresh_ts_base + dt.timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
        t2 = (fresh_ts_base + dt.timedelta(minutes=2)).isoformat().replace("+00:00", "Z")
        t3 = (fresh_ts_base + dt.timedelta(minutes=3)).isoformat().replace("+00:00", "Z")
        t4 = (fresh_ts_base + dt.timedelta(minutes=4)).isoformat().replace("+00:00", "Z")
        injected = [
            {
                "id": "permit:coh:0621100000014:99000001",
                "stream": "permits",
                "kind": "permit_coh",
                "severity": "critical",
                "adverse": True,
                "localDate": today_iso_date,
                "date": today_iso_date,
                "at": t4,
                "hcad": "0621100000014",
                "address": "1127 Key St",
                "yearBuilt": 1925,
                "designation": "Norhill Historic District",
                "title": "1127 Key St: Structural Permit Filing #99000001",
                "summary": "Synthetic test signal 1 on 1127 Key St parcel.",
            },
            {
                "id": "permit:coh:0621100000014:99000002",
                "stream": "permits",
                "kind": "permit_coh",
                "severity": "warning",
                "adverse": True,
                "localDate": today_iso_date,
                "date": today_iso_date,
                "at": t3,
                "hcad": "0621100000014",
                "address": "1127 Key St",
                "yearBuilt": 1925,
                "designation": "Norhill Historic District",
                "title": "1127 Key St: Plumbing Disconnect Filing #99000002",
                "summary": "Synthetic test signal 2 on 1127 Key St parcel.",
            },
            {
                "id": "permit:coh:0610870390001:99000003",
                "stream": "permits",
                "kind": "permit_coh",
                "severity": "warning",
                "adverse": True,
                "localDate": today_iso_date,
                "date": today_iso_date,
                "at": t2,
                "hcad": "0610870390001",
                "address": "1506 Heights Blvd",
                "yearBuilt": 1915,
                "designation": "Houston Heights Historic District East",
                "title": "1506 Heights Blvd: Foundation Permit #99000003",
                "summary": "Synthetic single-item read toggle fixture.",
            },
            {
                "id": "permit:coh:0152300000001:99000004",
                "stream": "permits",
                "kind": "permit_coh",
                "severity": "critical",
                "adverse": True,
                "localDate": today_iso_date,
                "date": today_iso_date,
                "at": t1,
                "hcad": "0152300000001",
                "address": "2000 Smith St",
                "yearBuilt": 1894,
                "designation": "City Landmark",
                "title": "2000 Smith St: Pinned Follow-Up Alert #99000004",
                "summary": "Synthetic Keep Unread cross-computer pin fixture.",
            },
        ]
        doc["generatedAt"] = (fresh_ts_base + dt.timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
        doc["events"] = injected + list(doc.get("events") or [])
        route.fulfill(
            status=200,
            content_type="application/json; charset=utf-8",
            body=json.dumps(doc).encode("utf-8"),
        )

    def _cloud_roaming_webhook(route: Any) -> None:
        if route.request.method.upper() == "POST":
            payload = json.loads(route.request.post_data or "{}")
            action = payload.get("action")
            email = str(payload.get("admin_email") or "").strip().lower()
            passkey = str(payload.get("admin_passkey") or "").strip()
            if passkey != "test-passkey-2026" or email not in ("advocate@example.org", "colleague@example.org"):
                route.fulfill(
                    status=200,
                    content_type="application/json; charset=utf-8",
                    headers={"Access-Control-Allow-Origin": "*"},
                    body=json.dumps({"ok": False, "authorized": False, "error": "Invalid credentials"}).encode("utf-8"),
                )
                return
            if action == "verify_admin":
                route.fulfill(
                    status=200,
                    content_type="application/json; charset=utf-8",
                    headers={"Access-Control-Allow-Origin": "*"},
                    body=json.dumps(
                        {
                            "authorized": True,
                            "supportsRoamingSync": True,
                            "email": email,
                            "userState": cloud_user_states.get(email),
                        }
                    ).encode("utf-8"),
                )
                return
            if action == "get_user_state":
                route.fulfill(
                    status=200,
                    content_type="application/json; charset=utf-8",
                    headers={"Access-Control-Allow-Origin": "*"},
                    body=json.dumps(
                        {
                            "ok": True,
                            "authorized": True,
                            "supportsRoamingSync": True,
                            "email": email,
                            "userState": cloud_user_states.get(email),
                        }
                    ).encode("utf-8"),
                )
                return
            if action == "put_user_state":
                user_state = payload.get("user_state") or {}
                cloud_user_states[email] = user_state
                put_calls.append({"email": email, "userState": user_state})
                route.fulfill(
                    status=200,
                    content_type="application/json; charset=utf-8",
                    headers={"Access-Control-Allow-Origin": "*"},
                    body=json.dumps(
                        {
                            "ok": True,
                            "authorized": True,
                            "supportsRoamingSync": True,
                            "email": email,
                            "userState": user_state,
                        }
                    ).encode("utf-8"),
                )
                return
            route.fulfill(
                status=200,
                content_type="application/json; charset=utf-8",
                headers={"Access-Control-Allow-Origin": "*"},
                body=b'{"ok":true,"authorized":true}',
            )
            return

        # GET ?feed=plat_sync or default Approved_Edits CSV
        route.fulfill(
            status=200,
            content_type="text/csv; charset=utf-8",
            headers={"Access-Control-Allow-Origin": "*"},
            body=b"status,hcad_num,source_type,source_citation\n",
        )

    ctx_a = browser.new_context(viewport=DEFAULT_E2E_VIEWPORT)
    ctx_b = browser.new_context(viewport=DEFAULT_E2E_VIEWPORT)
    try:
        configure_fast_e2e_context(ctx_a, atlas_base_url)
        configure_fast_e2e_context(ctx_b, atlas_base_url)
        for ctx in (ctx_a, ctx_b):
            ctx.route("**/data/monitor_activity_log.json*", _serve_fresh_activity_log)
            ctx.route("**/script.google.com/macros/**", _cloud_roaming_webhook)

        page_a = ctx_a.new_page()
        pa_err, pa_con, pa_http = attach_error_monitors(page_a, atlas_base_url)

        # --- Browser A ("Office Desktop") ---
        page_a.goto(f"{atlas_base_url}/plat-monitor.html?mode=activity", wait_until="domcontentloaded")
        page_a.wait_for_selector("#modeActivityBtn[aria-selected='true']", timeout=15000)
        page_a.wait_for_selector(
            "#detail .act-card[data-event-id='permit:coh:0621100000014:99000001']",
            timeout=15000,
        )
        assert page_a.locator("#detail [data-roaming-signin-callout='1']").count() == 1

        # Sign into Staff Sync on Browser A
        page_a.locator("#detail [data-open-staff-auth='1']").first.click()
        page_a.fill("#sa-email", "advocate@example.org")
        page_a.fill("#sa-passkey", "test-passkey-2026")
        page_a.click("#saVerify")
        page_a.wait_for_selector(".modal-back", state="detached", timeout=10000)
        assert page_a.locator("#detail [data-roaming-signin-callout='1']").count() == 0
        page_a.wait_for_selector("#detail [data-roaming-sync-chip='synced']", timeout=5000)
        assert "advocate@example.org" in page_a.locator("#detail [data-roaming-sync-chip='synced']").inner_text()

        # 1. Click Property Group '✓ Mark Read (N)' on 1127 Key St multi-signal group
        key_group_btn = page_a.locator(
            "#detail button[data-mark-group-read*='permit:coh:0621100000014:99000001']"
        ).first
        assert key_group_btn.count() == 1
        key_group_btn.click()
        assert (
            page_a.locator("#detail .act-card[data-event-id='permit:coh:0621100000014:99000001']").count()
            == 0
        )
        assert (
            page_a.locator("#detail .act-card[data-event-id='permit:coh:0621100000014:99000002']").count()
            == 0
        )

        # 2. Mark 1506 Heights Blvd read individually, then switch to 7d and pin 2000 Smith St as 'Keep Unread'
        page_a.locator(
            "#detail button[data-toggle-event-read='permit:coh:0610870390001:99000003']"
        ).click()
        assert (
            page_a.locator("#detail .act-card[data-event-id='permit:coh:0610870390001:99000003']").count()
            == 0
        )

        # Toggle 2000 Smith St read first, then switch to 7d and click '○ Keep Unread' to create explicit r:0 pin
        pinned_id = "permit:coh:0152300000001:99000004"
        page_a.locator(f"#detail button[data-toggle-event-read='{pinned_id}']").click()
        page_a.click("#activityRangeTabs [data-act-range='7d']")
        keep_unread_btn = page_a.locator(f"#detail button.read-toggle-btn[data-toggle-event-read='{pinned_id}']")
        assert keep_unread_btn.count() == 1
        assert "Keep Unread" in keep_unread_btn.inner_text()
        keep_unread_btn.click()
        assert (
            page_a.locator(f"#detail button.unread-toggle-btn[data-toggle-event-read='{pinned_id}']").count()
            == 1
        )

        # 3. Configure preferences (stream='all', group='date', lastMode='activity') and return to Since Last Visit
        page_a.click("#activityRangeTabs [data-act-range='since_last_visit']")
        page_a.select_option("#actStreamFilter", "all")
        page_a.select_option("#actGroupBySelect", "date")
        page_a.click("#modeActivityBtn")  # persists lastMode='activity'

        unread_before_catchup = int(page_a.locator("#modeActivityCount").inner_text().strip())
        assert unread_before_catchup >= 1

        # 4. Click '✓ Mark Caught Up' -> only the 1 'Keep Unread' pin remains unread; test 'Undo'; re-mark Caught Up
        puts_before = len(put_calls)
        page_a.locator("#detail button[data-mark-caught-up='1']").click()
        assert page_a.locator("#modeActivityCount").inner_text().strip() == "1"
        assert page_a.locator(f"#detail .act-card[data-event-id='{pinned_id}']").count() == 1

        undo_btn = page_a.locator(".toast.interactive .toast-act-btn", has_text="Undo")
        undo_btn.wait_for(state="visible", timeout=5000)
        undo_btn.click()
        assert int(page_a.locator("#modeActivityCount").inner_text().strip()) == unread_before_catchup

        # Re-mark Caught Up and wait for debounced put_user_state to land in cloud mock
        puts_before_final = len(put_calls)
        page_a.locator("#detail button[data-mark-caught-up='1']").click()
        assert page_a.locator("#modeActivityCount").inner_text().strip() == "1"

        waited_ms = 0
        while waited_ms < 5000 and (
            len(put_calls) <= puts_before_final
            or not cloud_user_states.get("advocate@example.org", {}).get("caughtUpAt")
        ):
            page_a.wait_for_timeout(150)
            waited_ms += 150

        assert len(put_calls) > puts_before
        assert cloud_user_states["advocate@example.org"]["readLedger"][pinned_id]["r"] == 0

        # --- Browser B ("Home Computer" fresh isolated context with empty storage) ---
        page_b = ctx_b.new_page()
        pb_err, pb_con, pb_http = attach_error_monitors(page_b, atlas_base_url)

        # Boot bare plat-monitor.html (starts signed out on default 'agenda' tab)
        page_b.goto(f"{atlas_base_url}/plat-monitor.html", wait_until="domcontentloaded")
        page_b.wait_for_selector("#app:not(.hidden)", timeout=15000)
        assert page_b.locator("#modeAgendaBtn").get_attribute("aria-selected") == "true"

        # Sign in as same staff email -> 1-RTT verify_admin immediately switches to synced 'activity' mode,
        # restores stream='all' & group='date', and hydrates caughtUpAt + Keep Unread pin!
        page_b.click("#staffAuthBtn")
        page_b.fill("#sa-email", "advocate@example.org")
        page_b.fill("#sa-passkey", "test-passkey-2026")
        page_b.click("#saVerify")
        page_b.wait_for_selector(".modal-back", state="detached", timeout=10000)

        assert page_b.locator("#modeActivityBtn").get_attribute("aria-selected") == "true"
        assert page_b.locator("#actStreamFilter").input_value() == "all"
        assert page_b.locator("#actGroupBySelect").input_value() == "date"
        assert page_b.locator("#modeActivityCount").inner_text().strip() == "1"
        assert page_b.locator(f"#detail .act-card[data-event-id='{pinned_id}']").count() == 1
        assert (
            page_b.locator("#detail .act-card[data-event-id='permit:coh:0621100000014:99000001']").count()
            == 0
        )

        # Sign out on Browser B -> resets shared legacy mirrors & preferences cleanly
        page_b.click("#staffAuthBtn")
        page_b.click("#saLock")
        page_b.wait_for_selector(".modal-back", state="detached", timeout=5000)
        assert page_b.locator("#detail [data-roaming-signin-callout='1']").count() == 1
        assert page_b.locator("#actStreamFilter").input_value() == "external"
        assert page_b.locator("#actGroupBySelect").input_value() == "property"
        assert page_b.evaluate("() => localStorage.getItem('phpam:actStream')") is None

        assert not pa_err and not pa_con and not pa_http
        assert not pb_err and not pb_con and not pb_http
    finally:
        ctx_a.close()
        ctx_b.close()





def _encode_test_jwt(claims: dict[str, Any]) -> str:
    import base64

    hdr = base64.urlsafe_b64encode(b'{"alg":"RS256","typ":"JWT"}').decode("ascii").rstrip("=")
    body = base64.urlsafe_b64encode(json.dumps(claims).encode("utf-8")).decode("ascii").rstrip("=")
    return f"{hdr}.{body}.sig"


def test_phase1_it4_public_screener_staff_rbac_cross_tab_signout_account_switch_and_jwt_expiry(
    browser: Any,
    atlas_base_url: str,
) -> None:
    """IT-4: Verify Public vs Screener vs Staff UI isolation, cross-tab storage Sign Out, Staff->Screener cache scrub, legacy localStorage upgrade scrub, and mid-edit JWT expiry recovery."""
    rpc_calls: list[dict[str, Any]] = []
    parcels_store: dict[str, dict[str, Any]] = {}
    decisions_store: dict[str, dict[str, Any]] = {}
    strategy_store: dict[str, dict[str, Any]] = {}
    watched_entities_store: list[dict[str, Any]] = []
    dossiers_store = _build_default_seeded_watchlist_dossiers()

    def _rbac_webhook(route: Any) -> None:
        if route.request.method.upper() != "POST":
            route.fulfill(
                status=200,
                content_type="text/csv; charset=utf-8",
                headers={"Access-Control-Allow-Origin": "*"},
                body=b"status,hcad_num,address,suggested_year_built\n",
            )
            return
        payload = json.loads(route.request.post_data or "{}")
        rpc_calls.append(payload)
        action = payload.get("action") or ""
        email = str(payload.get("admin_email") or "").strip().lower()
        passkey = str(payload.get("admin_passkey") or "").strip()
        id_tok = str(payload.get("google_id_token") or "").strip()

        role = ""
        if id_tok:
            # Decode test JWT claims
            import base64

            parts = id_tok.split(".")
            pad = "=" * ((4 - len(parts[1]) % 4) % 4)
            claims = json.loads(base64.urlsafe_b64decode(parts[1] + pad).decode("utf-8"))
            email = str(claims.get("email") or "").lower()
            role = "screener" if "screener" in email else "staff"
        elif email == "screener@preservation.example.org" and passkey == "screener-passkey-2026":
            role = "screener"
        elif email == "staff@preservation.example.org" and passkey == "staff-passkey-2026":
            role = "staff"

        if not role:
            route.fulfill(
                status=200,
                content_type="application/json; charset=utf-8",
                headers={"Access-Control-Allow-Origin": "*"},
                body=json.dumps({"ok": False, "authorized": False, "error": "Unauthorized"}).encode("utf-8"),
            )
            return

        if action == "verify_admin":
            resp: dict[str, Any] = {
                "ok": True,
                "authorized": True,
                "email": email,
                "role": role,
                "displayLabel": "Screener" if role == "screener" else "Preservation Staff",
                "assignedAreas": ["area:priority:sunnyside"] if role == "screener" else ["all"],
                "supportsRoamingSync": True,
                "supportsPhase1Monitoring": True,
            }
        elif action == "pull_monitoring_state":
            resp = {
                "ok": True,
                "authorized": True,
                "role": role,
                "email": email,
                "areas": [
                    {
                        "area_id": "area:priority:sunnyside",
                        "name": "Sunnyside",
                        "area_type": "neighborhood",
                        "priority_status": "priority",
                        "age_rule_cutoff_year": 1976,
                    }
                ],
                "parcels": list(parcels_store.values()),
                "decisions": list(decisions_store.values()),
                "events": [],
                "watchlistDossiers": [
                    {"watch_property_id": wid, "dossier_json": json.dumps(dobj)}
                    for wid, dobj in dossiers_store.items()
                ],
            }
            if role == "staff":
                resp["staffOnly"] = {
                    "parcelStrategy": list(strategy_store.values()),
                    "watchedEntities": list(watched_entities_store),
                }
                resp["pendingModerationCount"] = 2
        elif action == "upsert_parcel_decision":
            patch = payload.get("patch") or payload.get("decision") or {}
            pid = str(patch.get("parcel_id") or payload.get("parcel_id") or "")
            base_p = dict(parcels_store.get(pid) or {"parcel_id": pid})
            base_d = dict(decisions_store.get(pid) or {"decision_id": f"DEC:{pid}", "parcel_id": pid})
            for k, v in patch.items():
                base_p[k] = v
                base_d[k] = v
            base_d["decided_at"] = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
            parcels_store[pid] = base_p
            decisions_store[pid] = base_d
            resp = {"ok": True, "authorized": True, "role": role, "parcel": base_p, "decision": base_d}
        elif action == "upsert_staff_only":
            if role != "staff":
                resp = {"ok": False, "authorized": False, "error": "Staff only"}
            else:
                tgt = payload.get("target")
                rec = payload.get("record") or {}
                if tgt == "parcel_strategy" and rec.get("parcel_id"):
                    strategy_store[str(rec["parcel_id"])] = rec
                elif tgt == "watched_entity" and rec.get("entity_name"):
                    watched_entities_store.append(rec)
                resp = {"ok": True, "authorized": True}
        else:
            resp = {"ok": True, "authorized": True, "items": []}

        route.fulfill(
            status=200,
            content_type="application/json; charset=utf-8",
            headers={"Access-Control-Allow-Origin": "*"},
            body=json.dumps(resp).encode("utf-8"),
        )

    ctx = browser.new_context(viewport=DEFAULT_E2E_VIEWPORT)
    try:
        configure_fast_e2e_context(ctx, atlas_base_url)
        ctx.route("**/script.google.com/macros/**", _rbac_webhook)

        tab1 = ctx.new_page()
        t1_err, t1_con, t1_http = attach_error_monitors(tab1, atlas_base_url)

        # 1. Seed legacy pre-Phase-1 un-namespaced localStorage keys -> must be scrubbed on unauthenticated boot!
        tab1.goto(f"{atlas_base_url}/plat-monitor.html", wait_until="domcontentloaded")
        tab1.wait_for_selector("#app:not(.hidden)", timeout=15000)
        tab1.evaluate(
            """() => {
                localStorage.setItem('phpam:watchlist:v2', '{"secret":"pre_phase1"}');
                localStorage.setItem('phpam:cycle:2026-10-29', '{"secret":"pre_phase1_cycle"}');
            }"""
        )
        tab1.reload(wait_until="domcontentloaded")
        tab1.wait_for_selector("#app:not(.hidden)", timeout=15000)
        assert tab1.evaluate("() => localStorage.getItem('phpam:watchlist:v2')") is None
        assert tab1.evaluate("() => localStorage.getItem('phpam:cycle:2026-10-29')") is None
        assert not tab1.locator("#modeModerationBtn").is_visible()
        assert tab1.locator("#staffOnlyStrategyCard").count() == 0

        # Public Watchlist tab shows calm privacy gate
        tab1.click("#modeWatchlistBtn")
        tab1.wait_for_selector("#watchlistSignedOutGate", state="visible", timeout=5000)

        # 2. Sign in as Screener -> sees Watchlist, Story Check, and Recommend Significance; ZERO Staff-Only DOM or Queue!
        tab1.click("[data-open-staff-auth='1']")
        tab1.fill("#sa-email", "screener@preservation.example.org")
        tab1.fill("#sa-passkey", "screener-passkey-2026")
        tab1.click("#saVerify")
        tab1.wait_for_selector(".modal-back", state="detached", timeout=10000)
        tab1.wait_for_selector("#list .row[data-watch-id='prop-kirby-mansion']", timeout=10000)

        assert not tab1.locator("#modeModerationBtn").is_visible()
        tab1.click("#modeAgendaBtn")
        tab1.select_option("#cycleSelect", "2026-10-29")
        tab1.locator("#tabs [data-g='all']").click()
        tab1.fill("#q", "Amerimex Motors")
        tab1.locator("#list .row[data-app='2026-1659']").first.click()

        assert tab1.locator("#parcelSignificanceCard").count() == 1
        assert tab1.locator("#storyCheckDrawer").count() == 1
        assert tab1.locator("#staffOnlyStrategyCard").count() == 0
        assert tab1.locator("#approveScreenerSigBtn").count() == 0
        assert tab1.locator("[data-parcel-conf-sig]").count() == 0

        # Screener records 10-Minute Story Check + Recommend Significance = high
        tab1.locator("#storyCheckDrawer summary").click()
        tab1.locator("[data-story-check='city_directories_1866_1926']").check()
        tab1.locator("[data-parcel-rec-sig='high']").click()
        tab1.wait_for_function(
            "() => true",
            timeout=2000,
        )
        waited = 0
        while waited < 4000 and not any(c.get("action") == "upsert_parcel_decision" for c in rpc_calls):
            tab1.wait_for_timeout(150)
            waited += 150
        screener_dec_calls = [c for c in rpc_calls if c.get("action") == "upsert_parcel_decision"]
        assert len(screener_dec_calls) >= 1
        assert screener_dec_calls[-1]["patch"]["recommended_significance"] == "high"

        # 3. Switch to Staff -> sees #approveScreenerSigBtn, #staffOnlyStrategyCard, 1-Click Watch Firm, & #modeModerationBtn!
        tab1.click("#staffAuthBtn")
        tab1.click("#saLock")
        tab1.wait_for_selector(".modal-back", state="detached", timeout=5000)

        tab1.click("#staffAuthBtn")
        tab1.fill("#sa-email", "staff@preservation.example.org")
        tab1.fill("#sa-passkey", "staff-passkey-2026")
        tab1.click("#saVerify")
        tab1.wait_for_selector(".modal-back", state="detached", timeout=10000)

        assert tab1.locator("#modeModerationBtn").is_visible()
        tab1.click("#modeAgendaBtn")
        tab1.select_option("#cycleSelect", "2026-10-29")
        tab1.locator("#tabs [data-g='all']").click()
        tab1.fill("#q", "Amerimex Motors")
        tab1.locator("#list .row[data-app='2026-1659']").first.click()

        assert tab1.locator("#approveScreenerSigBtn").is_visible()
        tab1.click("#approveScreenerSigBtn")

        assert tab1.locator("#staffOnlyStrategyCard").is_visible()
        tab1.fill("#so-strategy-notes", "Confidential easement outreach plan")
        tab1.fill("#so-outreach-log", "Left message with owner representative")
        tab1.fill("#so-contacts", "counsel@example.org")
        tab1.click("#saveStaffStrategyBtn")

        tab1.fill("#so-custom-entity-input", "Repeat Teardown Developers LLC")
        tab1.click("#addCustomWatchedEntityBtn")

        waited = 0
        while waited < 4000 and sum(1 for c in rpc_calls if c.get("action") == "upsert_staff_only") < 2:
            tab1.wait_for_timeout(150)
            waited += 150
        strat_calls = [
            c for c in rpc_calls if c.get("action") == "upsert_staff_only" and c.get("target") == "parcel_strategy"
        ]
        ent_calls = [
            c for c in rpc_calls if c.get("action") == "upsert_staff_only" and c.get("target") == "watched_entity"
        ]
        assert strat_calls[-1]["record"]["strategy_notes"] == "Confidential easement outreach plan"
        assert strat_calls[-1]["record"]["contact_notes"] == "counsel@example.org"
        assert strat_calls[-1]["record"]["owner_relationship_notes"] == "Left message with owner representative"
        assert ent_calls[-1]["record"]["entity_name"] == "Repeat Teardown Developers LLC"
        assert ent_calls[-1]["record"]["entity_role"] == "developer"

        # 4. Open sibling Tab 2 while signed in as Staff -> then Sign Out in Tab 1 -> Tab 2 immediately unmounts Staff_Only DOM!
        tab2 = ctx.new_page()
        t2_err, t2_con, t2_http = attach_error_monitors(tab2, atlas_base_url)
        tab2.goto(f"{atlas_base_url}/plat-monitor.html?cycle=2026-10-29&app=2026-1659", wait_until="domcontentloaded")
        tab2.wait_for_selector("#staffOnlyStrategyCard", state="visible", timeout=15000)
        assert tab2.locator("#modeModerationBtn").is_visible()

        tab1.click("#staffAuthBtn")
        tab1.click("#saLock")
        tab1.wait_for_selector(".modal-back", state="detached", timeout=5000)

        tab2.wait_for_function(
            "() => document.querySelectorAll('#staffOnlyStrategyCard').length === 0",
            timeout=10000,
        )
        assert not tab2.locator("#modeModerationBtn").is_visible()
        assert tab2.evaluate("() => localStorage.getItem('phpam:auth_cache:staff@preservation.example.org')") is None

        # 5. Mid-edit 1-hour google_id_token expiry recovery: dirty mutation queues cleanly & replays after 1-click re-auth!
        now_sec = int(dt.datetime.now(dt.timezone.utc).timestamp())
        valid_jwt = _encode_test_jwt({"email": "staff@preservation.example.org", "exp": now_sec + 3600})
        expired_jwt = _encode_test_jwt({"email": "staff@preservation.example.org", "exp": now_sec - 120})

        tab1.evaluate(
            """(tok) => {
                const s = JSON.stringify({
                    authorized: true,
                    verifiedByWebhook: true,
                    email: 'staff@preservation.example.org',
                    role: 'staff',
                    displayLabel: 'Preservation Staff',
                    googleIdToken: tok,
                    supportsRoamingSync: true,
                    supportsPhase1Monitoring: true
                });
                sessionStorage.setItem('ph_atlas_admin_session', s);
                localStorage.setItem('ph_atlas_admin_session', s);
            }""",
            valid_jwt,
        )
        tab1.reload(wait_until="domcontentloaded")
        tab1.wait_for_selector("#app:not(.hidden)", timeout=15000)
        tab1.select_option("#cycleSelect", "2026-10-29")
        tab1.locator("#tabs [data-g='all']").click()
        tab1.fill("#q", "Amerimex Motors")
        tab1.locator("#list .row[data-app='2026-1659']").first.click()

        # Simulate token expiring after 1 hour while reviewer is mid-edit on #so-strategy-notes
        tab1.fill("#so-strategy-notes", "Unsaved mid-session strategy edit preserved across token expiry")
        tab1.evaluate(
            """(expTok) => {
                const cur = JSON.parse(localStorage.getItem('ph_atlas_admin_session'));
                cur.googleIdToken = expTok;
                sessionStorage.setItem('ph_atlas_admin_session', JSON.stringify(cur));
                localStorage.setItem('ph_atlas_admin_session', JSON.stringify(cur));
            }""",
            expired_jwt,
        )
        tab1.click("#saveStaffStrategyBtn")
        tab1.wait_for_selector(".modal-back .modal[role='dialog']", state="visible", timeout=5000)
        # Dirty textarea input remains intact underneath re-auth prompt!
        assert (
            tab1.locator("#so-strategy-notes").input_value()
            == "Unsaved mid-session strategy edit preserved across token expiry"
        )
        tab1.fill("#sa-email", "staff@preservation.example.org")
        tab1.fill("#sa-passkey", "staff-passkey-2026")
        tab1.click("#saVerify")
        tab1.wait_for_selector(".modal-back", state="detached", timeout=10000)
        assert (
            tab1.locator("#so-strategy-notes").input_value()
            == "Unsaved mid-session strategy edit preserved across token expiry"
        )
        waited = 0
        while waited < 4000 and not any(
            c.get("action") == "upsert_staff_only"
            and (c.get("record") or {}).get("strategy_notes")
            == "Unsaved mid-session strategy edit preserved across token expiry"
            for c in rpc_calls
        ):
            tab1.wait_for_timeout(150)
            waited += 150
        assert any(
            c.get("action") == "upsert_staff_only"
            and (c.get("record") or {}).get("strategy_notes")
            == "Unsaved mid-session strategy edit preserved across token expiry"
            for c in rpc_calls
        )

        # Also verify public index.html renders cleanly with zero console errors
        tab1.goto(f"{atlas_base_url}/index.html#layers=permit_watch", wait_until="domcontentloaded")
        _wait_for_atlas_ready(tab1)

        assert not t1_err and not t1_con and not t1_http
        assert not t2_err and not t2_con and not t2_http
    finally:
        ctx.close()


def test_phase1_it5_cross_cycle_parcel_significance_persistence_and_material_delta_recheck(
    guarded_page: Any,
    atlas_base_url: str,
) -> None:
    """IT-5: Verify Once-Per-Parcel Significance + uncapped screening_notes persist across cycles and flag Needs Re-Check on material deltas."""
    page = guarded_page
    mock_state = _seed_staff_admin_session(page)

    long_uncapped_note = (
        "Archival parcel history verified across 1924 Sanborn Vol. 3 and 1926 Houston City Directory: "
        + ("Intact early brick masonry commercial corner with original steel casements. " * 9)
    ).strip()
    assert len(long_uncapped_note) > 650

    mock_state["parcels"]["0420090000088"] = {
        "parcel_id": "0420090000088",
        "hcad_num": "0420090000088",
        "primary_address": "2410 WASHINGTON AVE",
        "building_name": "Washington Corner Storefront",
        "year_built": 1924,
        "review_status": "assessed",
        "significance_level": "high",
        "significance_basis": "architecture",
        "integrity": "good",
        "protection_level": "none",
        "threat_stage": 3,
        "advocacy_status": "active_advocacy",
        "attention_level": "act",
    }
    mock_state["decisions"]["0420090000088"] = {
        "decision_id": "DEC:0420090000088",
        "parcel_id": "0420090000088",
        "hcad_num": "0420090000088",
        "latest_cycle_key": "2026-10-01",
        "latest_app_no": "2026-8801",
        "review_status": "assessed",
        "significance_level": "high",
        "significance_basis": "architecture",
        "integrity": "good",
        "advocacy_status": "active_advocacy",
        "screening_notes": long_uncapped_note,
        "decided_by": "Preservation Staff",
        "decided_at": "2026-10-01T15:00:00Z",
    }

    plat_json_path = Path(__file__).resolve().parents[2] / "app" / "public" / "data" / "plat_agenda_monitor.json"

    def _inject_cross_cycle_parcel(route: Any) -> None:
        doc = json.loads(plat_json_path.read_text(encoding="utf-8"))
        base_item = {
            "name": "Washington Corner Replat",
            "hcad": "0420090000088",
            "city": "City",
            "county": "Harris",
            "prevSub": "Washington Addition",
            "superN": "Washington Avenue Coalition / Memorial Park",
            "landUse": "Commercial",
            "developer": "Cornerstone Holdings LLC",
            "applicant": "Precision Platting",
            "onMeetingAgenda": True,
            "atlas": {
                "addresses": ["2410 WASHINGTON AVE"],
                "primaryAddress": "2410 WASHINGTON AVE",
                "earliestYearBuilt": 1924,
                "allYearsBuilt": [1924],
                "parcels": [{"hcad": "0420090000088", "address": "2410 WASHINGTON AVE", "yearBuilt": 1924}],
                "designations": [],
            },
        }
        # Cycle 1 (2026-10-01): baseline C2 filing
        doc["cycles"]["2026-10-01"]["items"].insert(
            0,
            {
                **base_item,
                "app": "2026-8801",
                "occurrenceKey": "2026-10-01::2026-8801",
                "type": "C2",
                "section": "I. Consent",
                "special": "",
                "varianceSummary": "",
                "priorCycles": [],
                "_c": {"group": "check", "reason": "", "originalReason": "", "overriddenFromOut": False, "flags": ["pre1976"], "score": 4, "code": "C2", "inCity": True},
            },
        )
        # Cycle 2 (2026-10-15): unchanged C2 deferral
        doc["cycles"]["2026-10-15"]["items"].insert(
            0,
            {
                **base_item,
                "app": "2026-8801",
                "occurrenceKey": "2026-10-15::2026-8801",
                "type": "C2",
                "section": "I. Consent",
                "special": "",
                "varianceSummary": "",
                "priorCycles": [{"cycleKey": "2026-10-01", "app": "2026-8801", "type": "C2"}],
                "_c": {"group": "check", "reason": "", "originalReason": "", "overriddenFromOut": False, "flags": ["pre1976", "deferred"], "score": 5, "code": "C2", "inCity": True},
            },
        )
        # Cycle 3 (2026-10-29): material delta -> escalated to C3N Variance + Public Hearing!
        doc["cycles"]["2026-10-29"]["items"].insert(
            0,
            {
                **base_item,
                "app": "2026-8899",
                "occurrenceKey": "2026-10-29::2026-8899",
                "type": "C3N Replat with Variance",
                "section": "II. Public Hearings",
                "special": "Variance requested to reduce building line",
                "varianceSummary": "Reduced setback variance for teardown redevelopment",
                "hearingDateIso": "2026-11-05",
                "reReviewReason": "Material filing change: escalated C2 -> C3N Public Hearing + new setback variance",
                "priorCycles": [
                    {"cycleKey": "2026-10-01", "app": "2026-8801", "type": "C2"},
                    {"cycleKey": "2026-10-15", "app": "2026-8801", "type": "C2"},
                ],
                "_c": {"group": "check", "reason": "", "originalReason": "", "overriddenFromOut": False, "flags": ["pre1976", "hearing", "variance"], "score": 8, "code": "C3N", "inCity": True},
            },
        )
        doc["items"] = doc["cycles"]["2026-10-29"]["items"]
        route.fulfill(
            status=200,
            content_type="application/json; charset=utf-8",
            body=json.dumps(doc).encode("utf-8"),
        )

    page.route("**/data/plat_agenda_monitor.json*", _inject_cross_cycle_parcel)

    # 1. Inspect unchanged Cycle 2 (2026-10-15): saved High Significance & full >650-char screening_notes render automatically without Needs Re-Check
    page.goto(
        f"{atlas_base_url}/plat-monitor.html?cycle=2026-10-15&app=2026-8801",
        wait_until="domcontentloaded",
    )
    page.wait_for_selector("#parcelSignificanceCard", state="visible", timeout=15000)
    notes_val_cyc2 = page.locator("#parcelScreeningNotesInput").input_value()
    assert len(notes_val_cyc2) > 650
    assert "original steel casements" in notes_val_cyc2
    assert page.locator("[data-parcel-recheck-banner]").count() == 0

    # 2. Switch to Cycle 3 (2026-10-29) where new C3N variance + hearing arrived: saved High Significance persists AND Needs Re-Check banner surfaces!
    page.select_option("#cycleSelect", "2026-10-29")
    page.locator("#tabs [data-g='all']").click()
    page.locator("#list .row[data-app='2026-8899']").first.click()
    page.wait_for_selector("#parcelSignificanceCard", state="visible", timeout=10000)
    assert len(page.locator("#parcelScreeningNotesInput").input_value()) > 650
    assert page.locator("[data-parcel-recheck-banner]").count() == 1
    assert "ACT" in page.locator("#parcelSignificanceCard [data-attention-badge]").inner_text().upper()


def test_phase1_it6_source_conflict_writeback_and_in_app_community_moderation_queue(
    guarded_page: Any,
    atlas_base_url: str,
) -> None:
    """IT-6: Verify 1-click Source Conflict Writeback (including Lost/Vacant polygon stripping) and In-App Community Queue moderation across Pending_Submissions, _FLAG rows, and Field_Surveys."""
    page = guarded_page
    mock_state = _seed_staff_admin_session(page)

    custom_poly_str = json.dumps(
        {
            "type": "Polygon",
            "coordinates": [[[-95.36, 29.75], [-95.359, 29.75], [-95.359, 29.751], [-95.36, 29.751], [-95.36, 29.75]]],
        }
    )

    # Seed parcel 0621100000014 as outcome="lost" while curated_overrides.json has standing 1925 + custom polygon
    mock_state["parcels"]["0621100000014"] = {
        "parcel_id": "0621100000014",
        "hcad_num": "0621100000014",
        "primary_address": "1127 KEY ST",
        "year_built": 1925,
        "hcad_year_built": 1930,
        "subdivision_name": "North Norhill",
        "review_status": "assessed",
        "significance_level": "high",
        "advocacy_status": "closed",
        "outcome": "lost",
        "threat_stage": 6,
    }

    queue_items: list[dict[str, Any]] = [
        {
            "queueId": "Pending_Submissions:2:0420090000001",
            "sourceTab": "Pending_Submissions",
            "rowNumber": 2,
            "status": "Pending",
            "hcad_num": "0420090000001",
            "address": "2802 WHEELER AVE",
            "building_name": "Washington Terrace Duplex",
            "suggested_year_built": "1928",
            "original_hcad_year": "1940",
            "historic_district": "Washington Terrace",
            "contributing": "Contributing",
            "bld_style": "Brick Duplex",
            "architect": "Early Builder",
            "source_type": "Houston City Directory",
            "source_citation": "1928 Polk Directory listing",
            "submitted_by": "Community Historian",
            "submitted_at": "2026-11-20",
        },
        {
            "queueId": "Pending_Submissions:3:0610870390001",
            "sourceTab": "Pending_Submissions",
            "rowNumber": 3,
            "status": "Pending",
            "hcad_num": "0610870390001",
            "address": "1502 HEIGHTS BLVD",
            "suggested_year_built": "",
            "original_hcad_year": "1914",
            "historic_district": "Houston Heights East",
            "source_type": "PHOTO_MISATTRIBUTION_FLAG",
            "source_citation": "Street-view thumbnail depicts corner lot across alley",
            "submitted_by": "Block Resident",
            "submitted_at": "2026-11-21",
        },
        {
            "queueId": "Field_Surveys:2:SURV_WTB_101",
            "sourceTab": "Field_Surveys",
            "rowNumber": 2,
            "survey_id": "SURV_WTB_101",
            "status": "Unreviewed",
            "hcad_num": "0770010000001",
            "address": "4110 REED RD",
            "historic_district": "Sunnyside",
            "integrity_grade": "Intact",
            "condition_rating": "Good",
            "contributing": "Contributing",
            "original_hcad_year": "1950",
            "suggested_year_built": "1949",
            "source_citation": "Intact hipped-roof Minimal Traditional cottage",
            "submitted_by": "Survey Volunteer",
            "submitted_at": "2026-11-22",
            "source_type": "FIELD_SURVEY_OBSERVATION",
        },
    ]

    recorded_writebacks: list[dict[str, Any]] = []
    recorded_moderations: list[dict[str, Any]] = []

    def _moderation_and_writeback_webhook(route: Any) -> None:
        if route.request.method.upper() != "POST":
            route.fulfill(
                status=200,
                content_type="text/csv; charset=utf-8",
                headers={"Access-Control-Allow-Origin": "*"},
                body=b"status,hcad_num,address,suggested_year_built\n",
            )
            return
        payload = json.loads(route.request.post_data or "{}")
        act = payload.get("action") or ""
        if act == "pull_monitoring_state":
            resp: dict[str, Any] = {
                "ok": True,
                "authorized": True,
                "role": "staff",
                "email": "staff@preservation.example.org",
                "areas": [],
                "parcels": list(mock_state["parcels"].values()),
                "decisions": [],
                "events": [],
                "watchlistDossiers": [
                    {
                        "watch_property_id": "prop-key-lost",
                        "dossier_json": json.dumps(
                            {
                                "id": "prop-key-lost",
                                "parcelId": "0621100000014",
                                "propertyName": "1127 Key St Bungalow",
                                "address": "1127 KEY ST",
                                "hcadAccount": "0621100000014",
                                "tier": "1",
                                "tierConfirmed": True,
                                "trackerStatus": "Lost",
                                "yearBuilt": 1925,
                                "checklist": [],
                            }
                        ),
                    }
                ],
                "staffOnly": {"parcelStrategy": [], "watchedEntities": []},
                "pendingModerationCount": len(queue_items),
            }
        elif act in ("list_pending_submissions", "list_pending_moderation"):
            resp = {"ok": True, "authorized": True, "count": len(queue_items), "items": list(queue_items)}
        elif act == "moderate_submission":
            recorded_moderations.append(payload)
            resp = {"ok": True, "authorized": True, "status": payload.get("decision")}
        elif act == "admin_approve":
            recorded_writebacks.append(payload)
            resp = {"ok": True, "authorized": True, "status": "Approved", "upserted": True, "rowNumber": 2}
        else:
            resp = {"ok": True, "authorized": True}
        route.fulfill(
            status=200,
            content_type="application/json; charset=utf-8",
            headers={"Access-Control-Allow-Origin": "*"},
            body=json.dumps(resp).encode("utf-8"),
        )

    def _inject_custom_polygon_override(route: Any) -> None:
        ov_doc = {
            "overrides": {
                "0621100000014": {
                    "hcad_num": "0621100000014",
                    "address": "1127 KEY ST",
                    "year_built": 1925,
                    "original_hcad_year": 1930,
                    "historic_district": "Norhill",
                    "contributing": "Contributing",
                    "geometry": json.loads(custom_poly_str),
                    "source_citation": f"1925 Directory [GEOMETRY_GEOJSON: {custom_poly_str}]",
                }
            }
        }
        route.fulfill(
            status=200,
            content_type="application/json; charset=utf-8",
            body=json.dumps(ov_doc).encode("utf-8"),
        )

    page.route("**/script.google.com/macros/**", _moderation_and_writeback_webhook)
    page.route("**/data/curated_overrides.json*", _inject_custom_polygon_override)

    page.goto(f"{atlas_base_url}/plat-monitor.html?mode=watchlist", wait_until="domcontentloaded")
    page.wait_for_selector("#list .row[data-watch-id='prop-key-lost']", timeout=15000)
    page.locator("#list .row[data-watch-id='prop-key-lost']").click()
    page.wait_for_selector("#sourceConflictWritebackCard", state="visible", timeout=15000)

    # 1. Lost / Vacant Lot parcel conflicts with standing custom-polygon override -> 1-click writeback strips custom polygon!
    conflict_txt = page.locator("#sourceConflictWritebackCard").inner_text()
    assert "Standing vs. Demolished" in conflict_txt or "Demolished" in conflict_txt
    assert page.locator("#wb-suppress-vacant").is_checked()
    page.click("#publishApprovedOverrideBtn")

    waited = 0
    while waited < 5000 and not recorded_writebacks:
        page.wait_for_timeout(150)
        waited += 150
    assert len(recorded_writebacks) == 1
    wb_sent = recorded_writebacks[0]
    assert wb_sent["hcad_num"] == "0621100000014"
    assert wb_sent["suppress_only"] is True
    assert wb_sent["suggested_year_built"] in (0, "0")
    assert not wb_sent.get("geometry_geojson")
    assert "[GEOMETRY_GEOJSON:" not in str(wb_sent.get("source_citation") or "")

    # 2. Switch to Mode 4 (Community Moderation Queue) and moderate all 3 items (Pending archival edit, _FLAG dispute, and Field_Survey)
    assert page.locator("#modeModerationBtn").is_visible()
    page.click("#modeModerationBtn")
    page.wait_for_selector("#list .row[data-mod-queue-id]", timeout=10000)
    assert page.locator("#list .row[data-mod-queue-id]").count() == 3

    # (a) Approve Pending_Submissions archival year/architect edit
    page.locator("#list .row[data-mod-queue-id='Pending_Submissions:2:0420090000001']").click()
    page.fill("#mq-note", "Confirmed against 1928 City Directory")
    page.click("[data-mod-decision='Approved']")
    page.wait_for_function(
        "() => document.querySelectorAll('#list .row[data-mod-queue-id]').length === 2",
        timeout=5000,
    )
    assert recorded_moderations[-1]["sourceTab"] == "Pending_Submissions"
    assert recorded_moderations[-1]["decision"] == "Approved"
    assert recorded_moderations[-1]["suggested_year_built"] == 1928

    # (b) Resolve PHOTO_MISATTRIBUTION_FLAG -> automatically sends decision == 'Resolved' with blank year!
    page.locator("#list .row[data-mod-queue-id='Pending_Submissions:3:0610870390001']").click()
    assert "Mark Flag Resolved" in page.locator("[data-mod-decision='Approved']").inner_text()
    page.click("[data-mod-decision='Approved']")
    page.wait_for_function(
        "() => document.querySelectorAll('#list .row[data-mod-queue-id]').length === 1",
        timeout=5000,
    )
    assert recorded_moderations[-1]["decision"] == "Resolved"
    assert recorded_moderations[-1]["suggested_year_built"] == ""

    # (c) Promote Walk-the-Block Field_Surveys item -> sends sourceTab == 'Field_Surveys', survey_id == 'SURV_WTB_101', decision == 'Approved'
    page.locator("#list .row[data-mod-queue-id='Field_Surveys:2:SURV_WTB_101']").click()
    assert "Promote Survey to Map" in page.locator("[data-mod-decision='Approved']").inner_text()
    page.click("[data-mod-decision='Approved']")
    page.wait_for_function(
        "() => document.querySelectorAll('#list .row[data-mod-queue-id]').length === 0",
        timeout=5000,
    )
    assert recorded_moderations[-1]["sourceTab"] == "Field_Surveys"
    assert recorded_moderations[-1]["survey_id"] == "SURV_WTB_101"
    assert recorded_moderations[-1]["decision"] == "Approved"
    assert recorded_moderations[-1]["suggested_year_built"] == 1949
