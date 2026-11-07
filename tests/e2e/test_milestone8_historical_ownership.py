"""Playwright E2E tests for Milestone 8 (22-Year 2005-2026 HCAD Historical Ownership
Chain Pipeline, Pre-Compressed Shards & 3-Surface Atlas UI Integration) covering
Quality Plan scenarios IT-1, IT-2, IT-3, and IT-4.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any

from conftest import attach_error_monitors, configure_fast_e2e_context, wait_for_atlas_ready

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_it1_it2_cold_boot_zero_owner_history_requests_and_gzip_headers(
    page: Any,
    atlas_base_url: str,
) -> None:
    """IT-1 & IT-2: Verify app/server.py serves .json.gz as raw RFC 1952 application/gzip and cold boot makes 0 owner_history requests."""
    manifest = json.loads(
        (REPO_ROOT / "app/public/data/pmtiles_manifest.json").read_text(encoding="utf-8")
    )
    oh_prefixes = manifest.get("owner_history_prefixes") or []
    assert len(oh_prefixes) == 152
    assert manifest.get("owner_history_span") == [2005, 2026]
    assert int(manifest.get("owner_history_accounts") or 0) >= 1_200_000

    # Verify app/server.py serves raw gzip bytes without Content-Encoding so browser DecompressionStream('gzip') executes
    gz_url = f"{atlas_base_url}/public/data/owner_history/owners_062.json.gz?v={manifest['cache_bust']}"
    with urllib.request.urlopen(gz_url, timeout=5) as resp:
        assert resp.status == 200
        assert "gzip" in (resp.headers.get("Content-Type") or "").lower()
        assert resp.headers.get("Content-Encoding") is None
        magic = resp.read(2)
        assert magic == b"\x1f\x8b"

    configure_fast_e2e_context(page.context, atlas_base_url)
    page_errors, console_errors, local_http_errors = attach_error_monitors(page, atlas_base_url)

    oh_requests: list[str] = []
    page.on(
        "request",
        lambda req: oh_requests.append(req.url) if "owner_history/" in req.url else None,
    )

    page.goto(f"{atlas_base_url}/", wait_until="domcontentloaded")
    wait_for_atlas_ready(page)

    assert oh_requests == [], f"Expected 0 cold-boot owner_history requests, got {oh_requests}"

    about_text = page.evaluate(
        "() => document.getElementById('about-modal')?.textContent || ''"
    )
    assert "2005-2026" in about_text
    assert page_errors == []
    assert console_errors == []
    assert local_http_errors == []


def test_it3_three_surface_hydration_across_archetypes_and_confidential_shield(
    page: Any,
    atlas_base_url: str,
) -> None:
    """IT-3: Verify client-side DecompressionStream('gzip') and 3-surface UI hydration across residential, commercial/DBA, <=1988, and confidential parcels."""
    configure_fast_e2e_context(page.context, atlas_base_url)
    page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=atlas_base_url)
    page_errors, console_errors, local_http_errors = attach_error_monitors(page, atlas_base_url)

    page.goto(f"{atlas_base_url}/", wait_until="domcontentloaded")
    wait_for_atlas_ready(page)

    # 1. Multi-owner historic district residence: 1127 Key St (0621100000014)
    page.evaluate(
        """() => {
            window.atlasApp.renderInspectorDrawer({
                id: '0621100000014',
                hcad_num: '0621100000014',
                address: '1127 KEY ST',
                year_built: 1928,
                own_tier: 'homestead',
                use_category: 'Residential',
                neighborhood: 'Norhill',
                historic_district: 'North Norhill',
            });
        }"""
    )
    page.click('#inspector-body [data-inspector-tab="history"]')
    page.wait_for_function(
        """() => {
            const rk = document.getElementById('research-kit-deed-chain');
            return Boolean(rk && (rk.innerText || '').includes('HCAD Owners (2005-2026)'));
        }""",
        timeout=10000,
    )

    # Surface 1: Research Kit Step 3 (HCAD Roll Ownership & Deed Chain + 1-click Clerk copy pill)
    rk_text = page.inner_text("#research-kit-deed-chain")
    assert "HCAD Roll Ownership & Deed Chain (2005-2026)" in rk_text
    assert "MENGES LAURA J" in rk_text
    assert "BURNETT MICHAEL G & SHANNON L" in rk_text
    first_copy_btn = page.locator("#research-kit-deed-chain button[data-rk-copy]").first
    first_copy_btn.scroll_into_view_if_needed()
    first_copy_btn.click()
    page.wait_for_function(
        """() => {
            const btn = document.querySelector('#research-kit-deed-chain button[data-rk-copy]');
            return Boolean(btn && (btn.innerText || '').includes('Copied'));
        }""",
        timeout=5000,
    )

    # Surface 2: Ownership & Stewardship X-Ray Card
    page.click('#inspector-body [data-inspector-tab="hcad"]')
    xray_text = page.inner_text("#inspector-ownership-xray-card")
    assert "MORRIS LAURA M & DAVID V" in xray_text
    assert "Prior HCAD Owners (2005-2026):" in xray_text
    assert "MENGES LAURA J (2012-2018)" in xray_text
    assert "Tax Billing Hub:" not in xray_text

    # Surface 3: 1836-2026 Historical Provenance Timeline Card (deduplicated Clerk instrument node)
    page.click('#inspector-body [data-inspector-tab="history"]')
    tl_text = page.inner_text("#inspector-stewardship-timeline-card")
    assert "Original Structure Built (1928)" in tl_text
    deed_nodes_2019 = page.locator(
        "#inspector-stewardship-timeline-card .stewardship-timeline-node",
        has_text="RP-2019-149789",
    )
    assert deed_nodes_2019.count() == 1
    assert "MORRIS LAURA M & DAVID V" in deed_nodes_2019.first.inner_text()

    # 2. Commercial / DBA Landmarks: 0010020000016 (901 Commerce St) & 0152300000001 (2000 Smith St)
    for comm_hcad, comm_addr in [("0010020000016", "901 COMMERCE ST"), ("0152300000001", "2000 SMITH ST")]:
        page.evaluate(
            """([h, addr]) => {
                window.atlasApp.renderInspectorDrawer({
                    id: h,
                    hcad_num: h,
                    address: addr,
                    year_built: 1912,
                    own_tier: 'local_small',
                    use_category: 'Commercial',
                    is_landmark: true,
                });
            }""",
            [comm_hcad, comm_addr],
        )
        page.wait_for_function(
            """(expectedHcad) => {
                const comp = window.atlasApp._activeInspectorCompositeRec;
                return Boolean(comp && comp.hcadNum === expectedHcad && comp.rec && comp.rec.oh);
            }""",
            arg=comm_hcad,
            timeout=10000,
        )
        # Verify resolved _permitShardCache entry remains unmutated by oh composition
        permit_cache_mutated = page.evaluate(
            """async (pfx) => {
                const shard = await window.atlasApp._permitShardCache.get(pfx);
                if (!shard || typeof shard !== 'object') return false;
                return Object.values(shard).some((entry) => entry && typeof entry === 'object' && 'oh' in entry);
            }""",
            comm_hcad[:3],
        )
        assert permit_cache_mutated is False

    # 3. Continuous <=1988 zero-sale homestead: 0620220000015
    page.evaluate(
        """() => {
            window.atlasApp.renderInspectorDrawer({
                id: '0620220000015',
                hcad_num: '0620220000015',
                address: 'HISTORIC NORHILL HOMESTEAD',
                year_built: 1930,
                own_tier: 'homestead',
                use_category: 'Residential',
            });
        }"""
    )
    page.click('#inspector-body [data-inspector-tab="hcad"]')
    page.wait_for_function(
        """() => {
            const card = document.getElementById('inspector-ownership-xray-card');
            return Boolean(card && (card.innerText || '').includes('<=1988 (HCAD Mainframe Baseline)'));
        }""",
        timeout=10000,
    )
    page.click('#inspector-body [data-inspector-tab="history"]')
    cont_tl = page.inner_text("#inspector-stewardship-timeline-card")
    assert cont_tl.index("Original Structure Built (1930)") < cont_tl.index("1988")

    # 4. Active CURRENT OWNER TX Tax Code Sec. 25.025 confidential parcel: 0010020000001
    page.evaluate(
        """() => {
            window.atlasApp.renderInspectorDrawer({
                id: '0010020000001',
                hcad_num: '0010020000001',
                address: 'CONFIDENTIAL TEST PARCEL',
                year_built: 1940,
                owner: 'CURRENT OWNER',
                own_tier: 'homestead',
                use_category: 'Residential',
            });
        }"""
    )
    page.click('#inspector-body [data-inspector-tab="hcad"]')
    page.wait_for_function(
        """() => {
            const card = document.getElementById('inspector-ownership-xray-card');
            return Boolean(card && (card.innerText || '').includes('Protected Individual Homestead (TX Tax Code Sec. 25.025)'));
        }""",
        timeout=10000,
    )
    conf_xray = page.inner_text("#inspector-ownership-xray-card")
    assert "Prior HCAD Owners" not in conf_xray
    assert "Recorded Tenure Since" not in conf_xray
    assert "Tax Billing Hub:" not in conf_xray
    conf_rk_html = page.inner_html("#research-kit-deed-chain")
    assert "owner-party" not in conf_rk_html

    # 5. Post-snapshot live HCAD ArcGIS Sec. 25.025 confidential election resolving BEFORE owner_history shard
    #    with non-empty static props.owner ("MORRISON CLARA"): must overwrite #inspector-cell-owner AND suppress
    #    all personal names / 22-year chains across X-Ray, Research Kit Step 3, and Stewardship Timeline!
    live_race_res = page.evaluate(
        """async () => {
            window.atlasApp._ownerHistoryShardCache.clear();
            let releaseOh = null;
            const ohGate = new Promise((resolve) => { releaseOh = resolve; });
            const origFetch = window.fetch.bind(window);
            window.fetch = async (input, init) => {
                const url = typeof input === 'string' ? input : (input?.url || '');
                if (url.includes('public_query/MapServer/0/query')) {
                    return new Response(JSON.stringify({
                        features: [{
                            type: 'Feature',
                            geometry: null,
                            properties: {
                                HCAD_NUM: '0621100000014',
                                owner: 'CURRENT OWNER',
                                appr_val: 550000,
                                mkt_val: 550000,
                                impr_val: 300000,
                                land_val: 250000,
                                state_class: 'A1',
                            },
                        }],
                    }), { status: 200, headers: { 'Content-Type': 'application/json' } });
                }
                if (url.includes('Residential_Grade/MapServer/0/query')) {
                    return new Response(JSON.stringify({ features: [] }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' },
                    });
                }
                if (url.includes('owners_062.json.gz')) {
                    await ohGate;
                }
                return origFetch(input, init);
            };
            try {
                window.atlasApp.renderInspectorDrawer({
                    id: '0621100000014',
                    hcad_num: '0621100000014',
                    address: '1127 KEY ST',
                    year_built: 1928,
                    owner: 'MORRISON CLARA',
                    owner_name: 'MORRISON CLARA',
                    own_tier: 'homestead',
                    use_category: 'Residential',
                    neighborhood: 'Norhill',
                });
                // Let 0ms live ArcGIS response resolve BEFORE releasing owners_062.json.gz
                const liveDeadline = Date.now() + 5000;
                while (window.atlasApp._activeInspectorLiveOwner?.hcadNum !== '0621100000014') {
                    if (Date.now() > liveDeadline) {
                        throw new Error('Timed out waiting for _activeInspectorLiveOwner');
                    }
                    await new Promise((r) => setTimeout(r, 15));
                }
                releaseOh();
                await window.atlasApp.waitForPendingInspectorHydrations();
                return {
                    ownerCell: document.getElementById('inspector-cell-owner')?.textContent?.trim() || '',
                    xrayText: document.getElementById('inspector-ownership-xray-card')?.innerText || '',
                    rkText: document.getElementById('research-kit-deed-chain')?.innerText || '',
                    rkHtml: document.getElementById('research-kit-deed-chain')?.innerHTML || '',
                    tlText: document.getElementById('inspector-stewardship-timeline-card')?.innerText || '',
                };
            } finally {
                if (releaseOh) releaseOh();
                window.fetch = origFetch;
            }
        }"""
    )
    assert live_race_res["ownerCell"] == "Protected Individual Homestead (TX Tax Code Sec. 25.025)"
    for leaked_name in ("MORRISON CLARA", "MORRIS LAURA", "MENGES LAURA", "BURNETT MICHAEL"):
        assert leaked_name not in live_race_res["ownerCell"]
        assert leaked_name not in live_race_res["xrayText"]
        assert leaked_name not in live_race_res["rkText"]
        assert leaked_name not in live_race_res["tlText"]
    assert "owner-party" not in live_race_res["rkHtml"]
    for leaked_deed_marker in ("RP-", "Clerk File #"):
        assert leaked_deed_marker not in live_race_res["rkText"]
        assert leaked_deed_marker not in live_race_res["rkHtml"]
        assert leaked_deed_marker not in live_race_res["xrayText"]
        assert leaked_deed_marker not in live_race_res["tlText"]

    assert page_errors == []
    assert console_errors == []
    assert local_http_errors == []


def test_it4_owner_history_fault_isolation_and_rapid_parcel_switch_race_immunity(
    page: Any,
    atlas_base_url: str,
) -> None:
    """IT-4: Verify graceful degradation when owners_<prefix3>.json.gz fails and race immunity during rapid cross-prefix parcel switches."""
    configure_fast_e2e_context(page.context, atlas_base_url)
    page_errors, _console_errors, _local_http_errors = attach_error_monitors(page, atlas_base_url)

    # Intercept prefix 020 owner_history shard with HTTP 404 to test fault isolation
    page.route(
        "**/public/data/owner_history/owners_020.json.gz*",
        lambda route: route.fulfill(status=404, content_type="text/html", body="<html>Not Found</html>"),
    )

    page.goto(f"{atlas_base_url}/", wait_until="domcontentloaded")
    wait_for_atlas_ready(page)

    # 1. Fault isolation: permits_020.json still hydrates permits, X-Ray, and timeline when owners_020.json.gz 404s
    page.evaluate(
        """() => {
            window.atlasApp.renderInspectorDrawer({
                id: '0202010000014',
                hcad_num: '0202010000014',
                address: '1107 TULANE ST',
                year_built: 1920,
                owner: 'COLE EMMANUELLA I',
                own_tier: 'anonymity_shield',
                use_category: 'Residential',
                neighborhood: 'Houston Heights',
            });
        }"""
    )
    page.click('#inspector-body [data-inspector-tab="history"]')
    page.wait_for_function(
        """() => {
            const card = document.getElementById('inspector-stewardship-timeline-card');
            return Boolean(card && (card.innerText || '').includes('28 Days') && (card.innerText || '').includes('RP-2025-495199'));
        }""",
        timeout=10000,
    )
    page.click('#inspector-body [data-inspector-tab="hcad"]')
    tulane_xray = page.inner_text("#inspector-ownership-xray-card")
    assert "3225 MCLEOD DR STE 777" in tulane_xray
    assert "28 days" in tulane_xray

    # 2. Rapid cross-prefix parcel switch race-condition immunity using an explicit promise gate:
    # Hold owners_062.json.gz open until 0010020000001 finishes hydrating, then release 062 to prove stale writes are dropped.
    race_result = page.evaluate(
        """async () => {
            window.atlasApp._ownerHistoryShardCache.clear();
            let release062 = null;
            const gate062 = new Promise((resolve) => {
                release062 = resolve;
            });
            const origFetch = window.fetch.bind(window);
            window.fetch = async (input, init) => {
                const url = typeof input === 'string' ? input : (input?.url || '');
                if (url.includes('owners_062.json.gz')) {
                    await gate062;
                }
                return origFetch(input, init);
            };
            try {
                window.atlasApp.renderInspectorDrawer({
                    id: '0621100000014',
                    hcad_num: '0621100000014',
                    address: '1127 KEY ST',
                    year_built: 1928,
                    own_tier: 'homestead',
                    use_category: 'Residential',
                });
                window.atlasApp.renderInspectorDrawer({
                    id: '0010020000001',
                    hcad_num: '0010020000001',
                    address: 'CONFIDENTIAL TEST PARCEL',
                    year_built: 1940,
                    owner: 'CURRENT OWNER',
                    own_tier: 'homestead',
                    use_category: 'Residential',
                });
                const deadline = Date.now() + 8000;
                while (window.atlasApp._activeInspectorCompositeRec?.hcadNum !== '0010020000001') {
                    if (Date.now() > deadline) {
                        throw new Error('Timed out waiting for 0010020000001 composite hydration');
                    }
                    await new Promise((r) => setTimeout(r, 25));
                }
                release062();
                await window.atlasApp._ownerHistoryShardCache.get('062');
                await window.atlasApp.waitForPendingInspectorHydrations();
                return {
                    activeCompHcad: window.atlasApp._activeInspectorCompositeRec?.hcadNum || '',
                    xrayText: document.getElementById('inspector-ownership-xray-card')?.innerText || '',
                    tlText: document.getElementById('inspector-stewardship-timeline-card')?.innerText || '',
                    rkHtml: document.getElementById('research-kit-deed-chain')?.innerHTML || '',
                };
            } finally {
                if (release062) release062();
                window.fetch = origFetch;
            }
        }"""
    )
    assert race_result["activeCompHcad"] == "0010020000001"
    assert "Protected Individual Homestead (TX Tax Code Sec. 25.025)" in race_result["xrayText"]
    assert "MENGES LAURA" not in race_result["xrayText"]
    assert "MORRIS LAURA" not in race_result["xrayText"]
    assert "MENGES LAURA" not in race_result["tlText"]
    assert "MENGES LAURA" not in race_result["rkHtml"]
    assert page_errors == []
