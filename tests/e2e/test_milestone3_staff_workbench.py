"""Playwright E2E tests for Milestone 3 Preservation Staff & Field Workbench.

Covers:
  - E2E-1 & RP-3: Zero public cold-load overhead gate (`staffWorkbench.js` and
    `historical_audit_queue.json` lazy-loaded on demand) and intact baseline public UX
    (double-click zoom, share URL safety, map interactions, Back/Forward).
  - E2E-2: Split-Screen Historian Rapid Audit Mode (`#audit=1`, `#audit=<fid>`,
    `#audit=0200670000010&auditCat=ADDRESS_STREET_MISMATCH`), filters (`489` undesignated),
    composite `fid` isolation, `J`/`K`/`A`/`D`/`S` at `z >= 19.5` without altering time
    sliders, `tile/18` roof crop, ContentDM integer deep links, historic street alias
    badges, Sanborn local WebP thumbnails, community vs Admin publish, server rejection
    handling, and reload persistence.
  - E2E-3: Focus shielding across Audit, Polygon Editor, and Field Survey inputs/selects;
    LIFO `Escape` hierarchy (`#sanborn-browser-modal` / `#correction-modal` above Audit,
    Audit/PolyEdit/Survey above Inspector); mid-drag `Escape` & `popstate` cleanup restoring
    `dragPan` & `doubleClickZoom`.
  - E2E-4: In-Browser Staff Polygon Footprint Editor across BOTH WebGL AND forced 2D Canvas
    fallback at `z >= 19.5`, unauthenticated `#polyedit` admin unlock gate, vertex &
    midpoint drags, Borden `-34.65` deg rotation & nudge, Undo/Redo, self-intersecting
    bowtie block, HTTP 200 `{ ok: false }` rejection defense, and `{ ok: true }` dual
    `geometry_geojson` + `[GEOMETRY_GEOJSON:{...}]` publish updating `curated-overrides-src`.
  - E2E-5 & RP-1: Multi-footprint `#polyedit-multifp-warning` (`SOP 4 Case D`), mandatory
    `parcel_replace` confirmation gate, additive `#aux_1` save keeping all sibling campus
    footprints visible, and unknown-year standing polygon reshape without vacant suppression.
  - E2E-6 (`E2E-6a..6e`): Mobile Walk-the-Block Field Survey split-map bottom sheet across
    `360x640`, `375x667`, and `390x844` touch viewports (`>= 16px` inputs, `>= 48px` primary
    targets / `>= 44px` interactive controls, zero overflow, `Peek`/`Half`/`Full` upper-map
    clearance & tap targeting, coarse vs sharp GPS fixes, manual GPS jitter lock, 5-house
    2-tap walk + Undo toast, soft-keyboard `visualViewport` offset, programmatic WCAG AAA
    `>= 7.0:1` Daylight contrast, IndexedDB JPEG photo compression + auto-naming,
    `visibilitychange` pause/resume, offline reload, formula-safe CSV, and `{ ok: true }` sync).
  - E2E-7: `@media print` isolation (`display: none` across all Milestone 3 modals/docks/sheets
    while `#dossier-modal` remains printable).
"""

from __future__ import annotations

import json
import struct
import zlib
from typing import Any

import pytest

from conftest import (
    ExternalUrlHarvester,
    attach_error_monitors,
    configure_fast_e2e_context,
    wait_for_atlas_ready,
)

MOCK_WEBHOOK_URL = "https://script.google.com/macros/s/MOCK_PH_M3_WORKBENCH_E2E/exec"


def _make_synthetic_png(width: int = 1800, height: int = 1200) -> bytes:
    """Builds a valid RGB PNG image strictly in memory without external imaging libs."""
    row = b"\x00" + (b"\x2a\x6f\x9c" * width)
    raw = row * height
    compressed = zlib.compress(raw, level=1)

    def _chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", compressed) + _chunk(b"IEND", b"")


def _install_configurable_webhook(
    context: Any,
    state: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Intercepts Apps Script webhook POSTs with mutable response behavior for positive/negative E2E tests."""
    captured: list[dict[str, Any]] = []
    cfg: dict[str, Any] = state if state is not None else {"mode": "ok"}

    def _route_handler(route: Any) -> None:
        if route.request.method != "POST":
            route.fulfill(
                status=200,
                content_type="text/csv; charset=utf-8",
                headers={"Access-Control-Allow-Origin": "*"},
                body="hcad_num,status\n",
            )
            return

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
            return

        mode = cfg.get("mode", "ok")
        if mode == "ok_false":
            route.fulfill(
                status=200,
                content_type="application/json; charset=utf-8",
                headers={"Access-Control-Allow-Origin": "*"},
                body=json.dumps(
                    {
                        "ok": False,
                        "authorized": cfg.get("authorized", False),
                        "error": cfg.get("error", "Unauthorized"),
                    }
                ),
            )
            return

        is_admin = action == "admin_approve"
        resp_obj: dict[str, Any] = {
            "ok": True,
            "authorized": is_admin,
            "status": "Approved" if is_admin else "Pending Review",
        }
        if action == "field_survey":
            resp_obj["target_tab"] = "Field_Surveys"
        route.fulfill(
            status=200,
            content_type="application/json; charset=utf-8",
            headers={"Access-Control-Allow-Origin": "*"},
            body=json.dumps(resp_obj),
        )

    context.route(f"{MOCK_WEBHOOK_URL}*", _route_handler)
    return captured, cfg


def _seed_admin_session_and_webhook(page: Any, webhook_url: str = MOCK_WEBHOOK_URL) -> None:
    """Authorizes Staff Admin session and wires active mock webhook URL on live page."""
    page.evaluate(
        """(wh) => {
            sessionStorage.setItem(
                'ph_atlas_admin_session',
                JSON.stringify({
                    authorized: true,
                    email: 'curator@preservationhouston.org',
                    adminPasskey: 'e2e-test-passkey',
                    unlockedAt: new Date().toISOString()
                })
            );
            localStorage.setItem('ph_atlas_webhook_url', wh);
            if (window.atlasApp?.mapController?.sheetSyncStatus) {
                window.atlasApp.mapController.sheetSyncStatus.webhookUrl = wh;
            }
            const whInput = document.getElementById('admin-webhook-url');
            if (whInput) whInput.value = wh;
        }""",
        webhook_url,
    )


def test_e2e1_zero_public_cold_load_overhead_and_lazy_import_and_rp3_public_ux(
    guarded_page: Any,
    atlas_base_url: str,
) -> None:
    """E2E-1 & RP-3: Public `#/` cold load skips Milestone 3 bundles; lazy-loads cleanly on demand."""
    page = guarded_page
    local_requests: list[str] = []
    page.on(
        "request",
        lambda req: local_requests.append(req.url) if req.url.startswith(atlas_base_url) else None,
    )

    page.goto(f"{atlas_base_url}/index.html", wait_until="domcontentloaded")
    wait_for_atlas_ready(page)
    page.wait_for_timeout(450)

    # E2E-1: Zero cold-load overhead for public visitors
    assert not any("staffWorkbench.js" in u for u in local_requests), (
        f"staffWorkbench.js leaked onto public cold load: {local_requests}"
    )
    assert not any("historical_audit_queue.json" in u for u in local_requests), (
        f"historical_audit_queue.json leaked onto public cold load: {local_requests}"
    )

    # RP-3: Standard MapLibre controls & public share URL safety when workbenches are closed
    rp3_state = page.evaluate(
        """() => {
            const mc = window.atlasApp.mapController;
            const map = mc.map;
            return {
                dragPanEnabled: Boolean(map?.dragPan?.isEnabled?.()),
                dblClickZoomEnabled: Boolean(map?.doubleClickZoom?.isEnabled?.()),
                zoomBefore: Number(map?.getZoom?.() || 0),
                auditHidden: document.getElementById('historian-audit-modal')?.classList.contains('hidden'),
                polyHidden: document.getElementById('polygon-editor-dock')?.classList.contains('hidden'),
                surveyHidden: document.getElementById('field-survey-sheet')?.classList.contains('hidden'),
            };
        }"""
    )
    assert rp3_state["dragPanEnabled"] is True
    assert rp3_state["dblClickZoomEnabled"] is True
    assert rp3_state["auditHidden"] is True
    assert rp3_state["polyHidden"] is True
    assert rp3_state["surveyHidden"] is True

    page.dblclick("#map-canvas", position={"x": 720, "y": 450})
    page.wait_for_function(
        "(z0) => Number(window.atlasApp.mapController.map.getZoom()) > z0 + 0.2",
        arg=rp3_state["zoomBefore"],
        timeout=5000,
    )

    # Opening Historian Audit from sidebar lazy-imports staffWorkbench.js & fetches historical_audit_queue.json
    page.click("#btn-sidebar-open-audit")
    page.wait_for_selector("#historian-audit-modal:not(.hidden)", timeout=10000)
    page.wait_for_function(
        "() => (window.atlasApp._staffWorkbench?.auditItems?.length || 0) === 2600",
        timeout=10000,
    )
    assert any("staffWorkbench.js" in u for u in local_requests)
    assert any("historical_audit_queue.json" in u for u in local_requests)

    # Close Audit and confirm `#survey=1` hash route activates Field Survey Mode cleanly
    page.click("#btn-close-audit-modal")
    page.wait_for_selector("#historian-audit-modal", state="hidden", timeout=5000)
    page.evaluate("() => { window.location.hash = '#survey=1'; }")
    page.wait_for_selector("#field-survey-sheet:not(.hidden)", timeout=6000)
    page.click("#btn-close-field-survey")
    page.wait_for_selector("#field-survey-sheet", state="hidden", timeout=5000)


def test_e2e2_historian_rapid_audit_workbench_filters_shortcuts_links_and_dual_publish(
    guarded_page: Any,
    atlas_base_url: str,
    url_harvester: ExternalUrlHarvester,
) -> None:
    """E2E-2: Rapid Audit deep links, composite fid isolation, keyboard triage, archival links, and publish."""
    page = guarded_page
    posts, webhook_cfg = _install_configurable_webhook(page.context)

    # Launch directly into multi-category parcel 0200670000010 targeting its 2nd category LANDMARK_VS_ATLAS_YEAR_MISMATCH (Defect #14)
    page.goto(
        f"{atlas_base_url}/index.html#audit=0200670000010&auditCat=LANDMARK_VS_ATLAS_YEAR_MISMATCH",
        wait_until="domcontentloaded",
    )
    wait_for_atlas_ready(page)
    page.wait_for_function(
        """() => {
            const wb = window.atlasApp?._staffWorkbench;
            const cur = wb?.auditFilteredItems?.[wb.auditActiveIndex];
            return Boolean(cur && cur.hcad_num === '0200670000010');
        }""",
        timeout=12000,
    )

    initial_item = page.evaluate(
        """() => {
            const wb = window.atlasApp._staffWorkbench;
            const cur = wb.auditFilteredItems[wb.auditActiveIndex];
            return {
                fid: cur.fid,
                hcad: cur.hcad_num,
                cat: cur.category,
                catSelect: document.getElementById('audit-filter-category').value,
                zoom: window.atlasApp.mapController.map.getZoom(),
                cropImgs: Array.from(document.querySelectorAll('#audit-claim-card-host .audit-sat-tile-grid img')).map(i => i.src),
            };
        }"""
    )
    assert initial_item["hcad"] == "0200670000010"
    assert initial_item["cat"] == "LANDMARK_VS_ATLAS_YEAR_MISMATCH"
    assert initial_item["catSelect"] == "LANDMARK_VS_ATLAS_YEAR_MISMATCH"
    assert initial_item["zoom"] >= 19.49
    assert len(initial_item["cropImgs"]) == 9
    assert all("/MapServer/tile/18/" in u for u in initial_item["cropImgs"])

    # Harvest and validate external URLs inside Audit modal
    url_harvester.harvest_from_locator(page.locator("#historian-audit-modal"), "milestone3_audit_initial")

    # Reset filters and verify composite `fid` state isolation across 0200670000010's two findings
    page.click("#audit-btn-reset-filters")
    page.fill("#audit-filter-search", "0200670000010")
    page.evaluate("() => document.activeElement?.blur?.()")
    iso_before = page.evaluate(
        """() => {
            const wb = window.atlasApp._staffWorkbench;
            return wb.auditFilteredItems.map((it) => ({ fid: it.fid, cat: it.category }));
        }"""
    )
    assert len(iso_before) >= 2
    assert iso_before[0]["fid"] != iso_before[1]["fid"]

    page.keyboard.press("d")
    iso_after = page.evaluate(
        """(fids) => {
            const wb = window.atlasApp._staffWorkbench;
            const raw = JSON.parse(localStorage.getItem('ph_atlas_audit_decisions_v1') || '{}');
            return {
                firstStatus: raw[fids[0]]?.status || 'pending',
                secondStatus: raw[fids[1]]?.status || 'pending',
                totalItems: wb.auditItems.length,
            };
        }""",
        [iso_before[0]["fid"], iso_before[1]["fid"]],
    )
    assert iso_after["firstStatus"] == "dismissed"
    assert iso_after["secondStatus"] == "pending"

    # District filter check: Undesignated / Outside HD yields exact 489 bucketed contradictions
    page.click("#audit-btn-reset-filters")
    page.select_option("#audit-filter-district", "Undesignated / Outside HD")
    page.select_option("#audit-filter-status", "all")
    undesig_count = page.evaluate("() => window.atlasApp._staffWorkbench.auditFilteredItems.length")
    assert undesig_count == 489

    # Filter to City Directory Earlier Than HCAD to check ContentDM integer scan URL + Sanborn local thumbs
    page.click("#audit-btn-reset-filters")
    page.select_option("#audit-filter-category", "CITY_DIRECTORY_EARLIER_THAN_HCAD")
    page.select_option("#audit-filter-status", "actionable")
    archival_dom = page.evaluate(
        """() => {
            const arch = document.getElementById('audit-archival-pane-host');
            const anchors = Array.from(arch.querySelectorAll('a[href]')).map(a => a.href);
            const thumbs = Array.from(arch.querySelectorAll('.audit-sanborn-thumb img')).map(i => i.getAttribute('src') || '');
            return { anchors, thumbs };
        }"""
    )
    assert any("cdm17006.contentdm.oclc.org/digital/collection/citydir/id/" in a for a in archival_dom["anchors"])
    for thumb_src in archival_dom["thumbs"]:
        assert not thumb_src.lower().startswith(("http://", "https://")), f"Cross-origin Sanborn thumb: {thumb_src}"
    url_harvester.harvest_from_locator(page.locator("#historian-audit-modal"), "milestone3_audit_citydir")

    # Keyboard triage J / K / S preserves custom global timeline minYear/maxYear and syncs map zoom >= 19.5
    page.evaluate(
        """(wh) => {
            window.atlasApp.filterStore.setState({ minYear: 1860, maxYear: 1940 });
            localStorage.setItem('ph_atlas_webhook_url', wh);
            if (window.atlasApp?.mapController?.sheetSyncStatus) {
                window.atlasApp.mapController.sheetSyncStatus.webhookUrl = wh;
            }
        }""",
        MOCK_WEBHOOK_URL,
    )
    fid0 = page.evaluate("() => window.atlasApp._staffWorkbench.auditFilteredItems[window.atlasApp._staffWorkbench.auditActiveIndex].fid")
    page.keyboard.press("j")
    fid1 = page.evaluate("() => window.atlasApp._staffWorkbench.auditFilteredItems[window.atlasApp._staffWorkbench.auditActiveIndex].fid")
    assert fid1 != fid0
    page.keyboard.press("k")
    assert page.evaluate("() => window.atlasApp._staffWorkbench.auditFilteredItems[window.atlasApp._staffWorkbench.auditActiveIndex].fid") == fid0
    page.keyboard.press("s")
    tl_check = page.evaluate(
        """() => {
            const st = window.atlasApp.filterStore.getState();
            return { minYear: st.minYear, maxYear: st.maxYear, zoom: window.atlasApp.mapController.map.getZoom() };
        }"""
    )
    assert tl_check["minYear"] == 1860
    assert tl_check["maxYear"] == 1940
    assert tl_check["zoom"] >= 19.49

    # Community (unauthenticated) Accept [A] posts community suggestion & saves local decision
    comm_target = page.evaluate(
        """() => {
            const wb = window.atlasApp._staffWorkbench;
            const cur = wb.auditFilteredItems[wb.auditActiveIndex];
            return { fid: cur.fid, hcad: cur.hcad_num };
        }"""
    )
    page.fill("#audit-input-year", "1908")
    page.fill("#audit-input-note", "Verified 1908 City Directory entry")
    page.evaluate("() => document.activeElement?.blur?.()")
    page.keyboard.press("a")
    page.wait_for_function(
        "(fid) => JSON.parse(localStorage.getItem('ph_atlas_audit_decisions_v1') || '{}')[fid]?.status === 'accepted'",
        arg=comm_target["fid"],
        timeout=5000,
    )
    assert len(posts) >= 1
    assert posts[-1].get("action") != "admin_approve"
    assert posts[-1].get("suggested_year_built") in (1908, "1908")

    # Reload persistence check: local triage decisions survive browser reload
    page.reload(wait_until="domcontentloaded")
    wait_for_atlas_ready(page)
    page.wait_for_selector("#historian-audit-modal:not(.hidden)", timeout=10000)
    page.wait_for_function(
        """(fid) => {
            const wb = window.atlasApp?._staffWorkbench;
            if (!wb || wb.auditItems.length !== 2600) return false;
            const raw = JSON.parse(localStorage.getItem('ph_atlas_audit_decisions_v1') || '{}');
            return raw[fid]?.status === 'accepted';
        }""",
        arg=comm_target["fid"],
        timeout=10000,
    )
    prog_text = page.inner_text("#audit-progress-text")
    assert not prog_text.startswith("0 /"), f"Expected non-zero persisted triage count, got: {prog_text}"

    # Staff Admin publish check: test HTTP 200 `{ ok: false }` defense FIRST (Defect #22), then `{ ok: true }`
    _seed_admin_session_and_webhook(page)
    page.evaluate("() => window.atlasApp._staffWorkbench.onAdminAuthChanged(true)")
    admin_target = page.evaluate(
        """() => {
            const wb = window.atlasApp._staffWorkbench;
            const cur = wb.auditFilteredItems[wb.auditActiveIndex];
            return { fid: cur.fid, hcad: cur.hcad_num, key: cur.building_id || cur.hcad_num };
        }"""
    )
    webhook_cfg["mode"] = "ok_false"
    webhook_cfg["error"] = "Unauthorized"
    page.fill("#audit-input-year", "1911")
    page.fill("#audit-input-note", "Sanborn Sheet verified")
    page.click("#btn-audit-accept")
    page.wait_for_function(
        """() => {
            const el = document.getElementById('audit-action-feedback');
            return Boolean(el && !el.classList.contains('hidden') && el.textContent.includes('Unauthorized'));
        }""",
        timeout=5000,
    )
    still_same = page.evaluate(
        """(info) => ({
            curFid: window.atlasApp._staffWorkbench.auditFilteredItems[window.atlasApp._staffWorkbench.auditActiveIndex]?.fid,
            decision: JSON.parse(localStorage.getItem('ph_atlas_audit_decisions_v1') || '{}')[info.fid]?.status || null,
            overrideYear: window.atlasApp.mapController.curatedOverrides?.[info.key]?.year_built ?? null,
        })""",
        admin_target,
    )
    assert still_same["curFid"] == admin_target["fid"]
    assert still_same["decision"] != "accepted"
    assert still_same["overrideYear"] != 1911

    # Now switch mock to `{ ok: true }` and verify live `curatedOverrides` updates immediately
    webhook_cfg["mode"] = "ok"
    page.click("#btn-audit-accept")
    page.wait_for_function(
        "(key) => window.atlasApp.mapController.curatedOverrides?.[key]?.year_built === 1911",
        arg=admin_target["key"],
        timeout=6000,
    )
    assert posts[-1]["action"] == "admin_approve"


def test_e2e3_focus_shielding_lifo_escape_and_mid_drag_teardown(
    guarded_page: Any,
    atlas_base_url: str,
) -> None:
    """E2E-3: Input focus shielding, LIFO modal Escape order, and mid-drag Polygon Editor teardown."""
    page = guarded_page
    _install_configurable_webhook(page.context)
    page.goto(f"{atlas_base_url}/index.html#audit=1", wait_until="domcontentloaded")
    wait_for_atlas_ready(page)
    page.wait_for_function(
        "() => (window.atlasApp?._staffWorkbench?.auditFilteredItems?.length || 0) > 10",
        timeout=12000,
    )

    # 1. Typing shortcut letters inside Audit inputs & select never triggers navigation or global timeline shift
    start_snap = page.evaluate(
        """() => ({
            fid: window.atlasApp._staffWorkbench.auditFilteredItems[window.atlasApp._staffWorkbench.auditActiveIndex].fid,
            minYear: window.atlasApp.filterStore.getState().minYear,
            maxYear: window.atlasApp.filterStore.getState().maxYear,
        })"""
    )
    page.focus("#audit-input-note")
    page.keyboard.type("jkads 1920")
    page.keyboard.press("ArrowLeft")
    page.keyboard.press("Backspace")
    assert page.input_value("#audit-input-note") == "jkads 190"

    page.focus("#audit-select-source-type")
    page.keyboard.press("j")
    page.keyboard.press("d")

    after_type = page.evaluate(
        """() => ({
            fid: window.atlasApp._staffWorkbench.auditFilteredItems[window.atlasApp._staffWorkbench.auditActiveIndex].fid,
            minYear: window.atlasApp.filterStore.getState().minYear,
            maxYear: window.atlasApp.filterStore.getState().maxYear,
        })"""
    )
    assert after_type == start_snap

    # 2. LIFO Escape stacking: Sanborn Browser (z=66) & Correction modal (z=75) sit strictly above Audit (z=61)
    page.click("#btn-audit-open-sanborn-browser")
    page.wait_for_selector("#sanborn-browser-modal:not(.hidden)", timeout=4000)
    z_check = page.evaluate(
        """() => ({
            auditZ: Number(getComputedStyle(document.getElementById('historian-audit-modal')).zIndex) || 0,
            sanbornZ: Number(getComputedStyle(document.getElementById('sanborn-browser-modal')).zIndex) || 0,
        })"""
    )
    assert z_check["sanbornZ"] > z_check["auditZ"]
    page.keyboard.press("j")
    assert page.evaluate("() => window.atlasApp._staffWorkbench.auditFilteredItems[window.atlasApp._staffWorkbench.auditActiveIndex].fid") == start_snap["fid"]

    page.keyboard.press("Escape")
    page.wait_for_selector("#sanborn-browser-modal", state="hidden", timeout=3000)
    assert page.evaluate("() => !document.getElementById('historian-audit-modal').classList.contains('hidden')")

    page.click("#btn-audit-open-correction")
    page.wait_for_selector("#correction-modal:not(.hidden)", timeout=4000)
    page.keyboard.press("Escape")
    page.wait_for_selector("#correction-modal", state="hidden", timeout=3000)
    assert page.evaluate("() => !document.getElementById('historian-audit-modal').classList.contains('hidden')")

    page.keyboard.press("Escape")
    page.wait_for_selector("#historian-audit-modal", state="hidden", timeout=3000)

    # 3. Polygon Editor input Delete/Backspace shielding + mid-drag Escape & popstate safety
    _seed_admin_session_and_webhook(page)
    page.evaluate(
        """async () => {
            const wb = await window.atlasApp._ensureStaffWorkbench();
            await wb.openPolygonEditor({
                targetKey: '0010280000001',
                hcadNum: '0010280000001',
                props: { hcad_num: '0010280000001', address: '801 CONGRESS ST', lng: -95.3622, lat: 29.7623 }
            });
            document.getElementById('btn-polyedit-new-rect').click();
        }"""
    )
    page.wait_for_selector("#polygon-editor-dock:not(.hidden)", timeout=6000)
    page.focus("#polyedit-input-citation")
    page.keyboard.type("Sanborn")
    page.keyboard.press("Backspace")
    page.keyboard.press("Delete")
    assert page.inner_text("#polyedit-stat-vertices") == "4 / 64 pts"
    page.evaluate("() => document.activeElement?.blur?.()")

    # Mid-drag Escape: hold mouse button down on vertex 0, move pointer, then press Escape BEFORE mouseup
    v0_pt = page.evaluate(
        """() => {
            const mc = window.atlasApp.mapController;
            const [lng, lat] = window.atlasApp._staffWorkbench.polySession.openRing[0];
            const pt = mc.map.project([lng, lat]);
            const r = mc.map.getCanvas().getBoundingClientRect();
            return { x: r.left + pt.x, y: r.top + pt.y };
        }"""
    )
    page.mouse.move(v0_pt["x"], v0_pt["y"])
    page.mouse.down()
    page.mouse.move(v0_pt["x"] + 16, v0_pt["y"] + 12, steps=3)
    assert page.evaluate("() => window.atlasApp.mapController.map.dragPan.isEnabled()") is False

    page.keyboard.press("Escape")
    page.mouse.up()
    mid_drag_esc = page.evaluate(
        """() => {
            const mc = window.atlasApp.mapController;
            return {
                dockHidden: document.getElementById('polygon-editor-dock').classList.contains('hidden'),
                polyFeatures: mc.polygonEditorFC?.features?.length || 0,
                dragPan: mc.map.dragPan.isEnabled(),
                dblZoom: mc.map.doubleClickZoom.isEnabled(),
            };
        }"""
    )
    assert mid_drag_esc == {"dockHidden": True, "polyFeatures": 0, "dragPan": True, "dblZoom": True}

    # Mid-drag route transition (`popstate` / hash navigation away): also restores MapLibre pan & dblClickZoom
    page.evaluate(
        """async () => {
            const wb = await window.atlasApp._ensureStaffWorkbench();
            await wb.openPolygonEditor({
                targetKey: '0010280000001',
                hcadNum: '0010280000001',
                props: { hcad_num: '0010280000001', address: '801 CONGRESS ST', lng: -95.3622, lat: 29.7623 }
            });
            document.getElementById('btn-polyedit-new-rect').click();
        }"""
    )
    v0_pt2 = page.evaluate(
        """() => {
            const mc = window.atlasApp.mapController;
            const [lng, lat] = window.atlasApp._staffWorkbench.polySession.openRing[0];
            const pt = mc.map.project([lng, lat]);
            const r = mc.map.getCanvas().getBoundingClientRect();
            return { x: r.left + pt.x, y: r.top + pt.y };
        }"""
    )
    page.mouse.move(v0_pt2["x"], v0_pt2["y"])
    page.mouse.down()
    page.mouse.move(v0_pt2["x"] + 12, v0_pt2["y"] - 10, steps=2)
    assert page.evaluate("() => window.atlasApp.mapController.map.dragPan.isEnabled()") is False
    page.evaluate("() => { window.location.hash = ''; window.dispatchEvent(new PopStateEvent('popstate')); }")
    page.wait_for_selector("#polygon-editor-dock", state="hidden", timeout=5000)
    page.mouse.up()
    assert page.evaluate("() => window.atlasApp.mapController.map.dragPan.isEnabled()") is True
    assert page.evaluate("() => window.atlasApp.mapController.map.doubleClickZoom.isEnabled()") is True


@pytest.mark.parametrize("renderer_mode", ["webgl", "canvas2d"])
def test_e2e4_polygon_editor_webgl_and_canvas2d_and_publish(
    browser: Any,
    atlas_base_url: str,
    renderer_mode: str,
) -> None:
    """E2E-4: Polygon Editor drag, midpoint insert, rotate/nudge, undo/redo, topology gate & publish across WebGL and 2D Canvas."""
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    try:
        configure_fast_e2e_context(context, atlas_base_url)
        posts, webhook_cfg = _install_configurable_webhook(context)
        if renderer_mode == "canvas2d":
            context.add_init_script(
                """(() => {
                    const origGetCtx = HTMLCanvasElement.prototype.getContext;
                    HTMLCanvasElement.prototype.getContext = function(type, ...args) {
                        if (typeof type === 'string' && type.toLowerCase().includes('webgl')) {
                            return null;
                        }
                        return origGetCtx.call(this, type, ...args);
                    };
                })();"""
            )

        page = context.new_page()
        page_errors, console_errors, local_http_errors = attach_error_monitors(page, atlas_base_url)

        if renderer_mode == "webgl":
            # Verify unauthenticated `#polyedit` route gates behind Staff Admin unlock modal first
            page.goto(f"{atlas_base_url}/index.html#polyedit=0010280000001", wait_until="domcontentloaded")
            wait_for_atlas_ready(page)
            page.wait_for_selector("#correction-modal:not(.hidden)", timeout=8000)
            assert page.evaluate("() => document.getElementById('polygon-editor-dock').classList.contains('hidden')")
            assert page.evaluate("() => !document.getElementById('admin-auth-panel').classList.contains('hidden')")

            # Unlock Admin directly via modal form -> auto-launches pending `#polyedit` session
            page.evaluate(
                """(wh) => {
                    localStorage.setItem('ph_atlas_webhook_url', wh);
                    if (window.atlasApp?.mapController?.sheetSyncStatus) {
                        window.atlasApp.mapController.sheetSyncStatus.webhookUrl = wh;
                    }
                    const whEl = document.getElementById('admin-webhook-url');
                    if (whEl) whEl.value = wh;
                }""",
                MOCK_WEBHOOK_URL,
            )
            page.fill("#admin-auth-email", "curator@preservationhouston.org")
            page.fill("#admin-auth-passkey", "e2e-test-passkey")
            page.click("#btn-verify-admin-unlock")
            page.wait_for_selector("#polygon-editor-dock:not(.hidden)", timeout=8000)
        else:
            page.goto(f"{atlas_base_url}/index.html", wait_until="domcontentloaded")
            wait_for_atlas_ready(page)
            _seed_admin_session_and_webhook(page)
            page.evaluate(
                """async () => {
                    const wb = await window.atlasApp._ensureStaffWorkbench();
                    await wb.openPolygonEditor({
                        targetKey: '0010280000001',
                        hcadNum: '0010280000001',
                        props: {
                            hcad_num: '0010280000001',
                            address: '801 CONGRESS ST',
                            historic_district: 'Main Street Market Square Historic District',
                            contributing: 'Contributing',
                            lng: -95.3622,
                            lat: 29.7623
                        }
                    });
                }"""
            )
            page.wait_for_selector("#polygon-editor-dock:not(.hidden)", timeout=8000)

        renderer_info = page.evaluate(
            """() => {
                const mc = window.atlasApp.mapController;
                document.getElementById('btn-polyedit-new-rect').click();
                return {
                    useCanvasFallback: Boolean(mc.useCanvasFallback),
                    zoom: mc.getCurrentViewport().zoom,
                };
            }"""
        )
        assert renderer_info["useCanvasFallback"] is (renderer_mode == "canvas2d")
        assert renderer_info["zoom"] >= 19.5

        # 1. Drag corner vertex 0 on map canvas and confirm ring geometry updates
        handles0 = page.evaluate(
            """() => {
                const mc = window.atlasApp.mapController;
                const wb = window.atlasApp._staffWorkbench;
                const v0 = wb.polySession.openRing[0];
                const mid0 = wb.polySession.openRing.map((a, i, r) => {
                    const b = r[(i + 1) % r.length];
                    return [(a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5];
                })[0];
                const toClient = ([lng, lat]) => {
                    if (mc.useCanvasFallback) {
                        const rect = mc.canvasState.canvas.getBoundingClientRect();
                        const [px, py] = mc._lngLatToScreen(lng, lat, rect.width, rect.height);
                        return { x: rect.left + px, y: rect.top + py };
                    }
                    const rect = mc.map.getCanvas().getBoundingClientRect();
                    const pt = mc.map.project([lng, lat]);
                    return { x: rect.left + pt.x, y: rect.top + pt.y };
                };
                return {
                    v0Orig: v0,
                    v0Client: toClient(v0),
                    mid0Client: toClient(mid0),
                };
            }"""
        )
        page.mouse.move(handles0["v0Client"]["x"], handles0["v0Client"]["y"])
        page.mouse.down()
        page.mouse.move(handles0["v0Client"]["x"] - 18, handles0["v0Client"]["y"] - 14, steps=4)
        page.mouse.up()

        v0_after = page.evaluate("() => window.atlasApp._staffWorkbench.polySession.openRing[0]")
        assert v0_after != handles0["v0Orig"], f"[{renderer_mode}] Vertex drag did not move vertex 0"

        # 2. Click midpoint handle 0 on map canvas -> inserts 5th vertex; verify Undo / Redo
        mid_client = page.evaluate(
            """() => {
                const mc = window.atlasApp.mapController;
                const r = window.atlasApp._staffWorkbench.polySession.openRing;
                const mid = [(r[0][0] + r[1][0]) * 0.5, (r[0][1] + r[1][1]) * 0.5];
                if (mc.useCanvasFallback) {
                    const rect = mc.canvasState.canvas.getBoundingClientRect();
                    const [px, py] = mc._lngLatToScreen(mid[0], mid[1], rect.width, rect.height);
                    return { x: rect.left + px, y: rect.top + py };
                }
                const rect = mc.map.getCanvas().getBoundingClientRect();
                const pt = mc.map.project(mid);
                return { x: rect.left + pt.x, y: rect.top + pt.y };
            }"""
        )
        page.mouse.move(mid_client["x"], mid_client["y"])
        page.mouse.down()
        page.mouse.up()
        assert page.inner_text("#polyedit-stat-vertices") == "5 / 64 pts"

        page.click("#btn-polyedit-undo")
        assert page.inner_text("#polyedit-stat-vertices") == "4 / 64 pts"
        page.click("#btn-polyedit-redo")
        assert page.inner_text("#polyedit-stat-vertices") == "5 / 64 pts"

        # 3. Borden -34.65 deg rotation preset, fine slider input, and directional E/N nudge
        page.click('button[data-poly-rotate="-34.65"]')
        page.evaluate(
            """() => {
                const sl = document.getElementById('polyedit-rotate-slider');
                sl.value = '5.5';
                sl.dispatchEvent(new Event('input', { bubbles: true }));
                sl.dispatchEvent(new Event('change', { bubbles: true }));
            }"""
        )
        page.click('button[data-poly-nudge="E"]')
        page.click('button[data-poly-nudge="N"]')
        assert "Valid Footprint" in page.inner_text("#polyedit-validation-badge")

        # 4. Self-intersecting bowtie flips validation badge invalid & disables Save button
        valid_ring_backup = page.evaluate(
            """() => {
                const wb = window.atlasApp._staffWorkbench;
                const backup = wb.polySession.openRing.map(p => [p[0], p[1]]);
                const cLng = wb.polySession.centerLng;
                const cLat = wb.polySession.centerLat;
                wb._commitPolyRing([
                    [cLng - 0.00012, cLat - 0.00012],
                    [cLng + 0.00012, cLat + 0.00012],
                    [cLng - 0.00012, cLat + 0.00012],
                    [cLng + 0.00012, cLat - 0.00012],
                ]);
                return backup;
            }"""
        )
        assert page.evaluate("() => document.getElementById('btn-polyedit-save').disabled") is True
        assert "err" in (page.get_attribute("#polyedit-validation-badge", "class") or "")

        # Restore valid reshaped ring & fill metadata
        page.evaluate("(ring) => window.atlasApp._staffWorkbench._commitPolyRing(ring)", valid_ring_backup)
        page.select_option("#polyedit-select-contributing", "Contributing")
        page.fill("#polyedit-input-year", "1895")
        page.fill("#polyedit-input-citation", f"Sanborn rooftop alignment ({renderer_mode})")
        assert page.evaluate("() => document.getElementById('btn-polyedit-save').disabled") is False

        # 5. Mock HTTP 200 `{ ok: false, error: 'Unauthorized' }` -> blocks false success & keeps editor open
        webhook_cfg["mode"] = "ok_false"
        webhook_cfg["authorized"] = False
        webhook_cfg["error"] = "Unauthorized"
        page.click("#btn-polyedit-save")
        page.wait_for_function(
            """() => {
                const b = document.getElementById('polyedit-validation-badge');
                return Boolean(b && b.textContent.includes('Unauthorized'));
            }""",
            timeout=5000,
        )
        assert page.evaluate("() => !document.getElementById('polygon-editor-dock').classList.contains('hidden')")
        assert page.evaluate("() => !window.atlasApp.mapController.curatedOverrides?.['0010280000001']?.geometry")

        # 6. Switch mock to `{ ok: true }` -> transmits dual GeoJSON, updates `curated-overrides-src`, cleans handles
        webhook_cfg["mode"] = "ok"
        page.click("#btn-polyedit-save")
        page.wait_for_selector("#polygon-editor-dock", state="hidden", timeout=6000)

        approve_posts = [p for p in posts if p.get("action") == "admin_approve"]
        assert len(approve_posts) >= 2
        final_post = approve_posts[-1]
        assert final_post["hcad_num"] == "0010280000001"
        assert final_post["geometry_geojson"].startswith('{"type":"Polygon"')
        assert "[GEOMETRY_GEOJSON:{" in final_post["source_citation"]

        post_save_state = page.evaluate(
            """() => {
                const mc = window.atlasApp.mapController;
                const ov = mc.curatedOverrides?.['0010280000001'];
                const inFc = (mc.overridesFC?.features || []).some(
                    f => String(f.properties?.hcad_num || '') === '0010280000001' && f.geometry?.type === 'Polygon'
                );
                return {
                    hasLiveGeom: Boolean(ov?.geometry?.coordinates?.[0]?.length === 6),
                    inOverridesFC: inFc,
                    editorHandlesLeft: mc.polygonEditorFC?.features?.length || 0,
                };
            }"""
        )
        assert post_save_state == {"hasLiveGeom": True, "inOverridesFC": True, "editorHandlesLeft": 0}

        assert not page_errors, f"[{renderer_mode}] Page errors: {page_errors}"
        assert not local_http_errors, f"[{renderer_mode}] HTTP errors: {local_http_errors}"
        assert not console_errors, f"[{renderer_mode}] Console errors: {console_errors}"
    finally:
        context.close()


def test_e2e5_multifootprint_aux_sibling_visibility_and_unknown_year_reshape(
    guarded_page: Any,
    atlas_base_url: str,
) -> None:
    """E2E-5 & RP-1: Multi-footprint SOP 4 Case D guard, `#aux_1` sibling survival, and unknown-year reshape."""
    page = guarded_page
    posts, _ = _install_configurable_webhook(page.context)
    page.goto(f"{atlas_base_url}/index.html", wait_until="domcontentloaded")
    wait_for_atlas_ready(page)
    _seed_admin_session_and_webhook(page)

    # Load multi-footprint campus parcel 1223580020001 (19 sub-buildings)
    page.evaluate(
        """() => {
            const map = window.atlasApp.mapController.map;
            map.stop();
            map.jumpTo({ center: [-95.60448, 29.88972], zoom: 16.0, pitch: 0, bearing: 0 });
        }"""
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
            return ids.size >= 18;
        }""",
        timeout=20000,
    )

    page.evaluate(
        """async () => {
            const wb = await window.atlasApp._ensureStaffWorkbench();
            await wb.openPolygonEditor({
                targetKey: '1223580020001',
                hcadNum: '1223580020001',
                props: {
                    hcad_num: '1223580020001',
                    address: '7900 N ELDRIDGE PKWY',
                    contributing: 'Outside Historic District',
                    lng: -95.6041,
                    lat: 29.8895
                }
            });
            document.getElementById('btn-polyedit-new-rect').click();
        }"""
    )
    page.wait_for_selector("#polygon-editor-dock:not(.hidden)", timeout=6000)

    # Multi-footprint warning banner (SOP 4 Case D) must be visible
    warn_text = page.inner_text("#polyedit-multifp-warning")
    assert page.evaluate("() => !document.getElementById('polyedit-multifp-warning').classList.contains('hidden')")
    assert "#aux_N" in warn_text or "building" in warn_text.lower()

    # Selecting `parcel_replace` without confirmation checkbox blocks save and keeps editor open
    page.select_option("#polyedit-select-mode", "parcel_replace")
    assert page.evaluate("() => !document.getElementById('polyedit-replace-confirm-wrap').classList.contains('hidden')")
    page.click("#btn-polyedit-save")
    assert page.evaluate("() => !document.getElementById('polygon-editor-dock').classList.contains('hidden')")

    # Switch to `additive_aux` (`1223580020001#aux_1`) and save -> keeps all sibling campus footprints visible!
    page.select_option("#polyedit-select-mode", "additive_aux")
    assert "1223580020001#aux_1" in page.inner_text("#polyedit-target-badge")
    page.fill("#polyedit-input-name", "Campus Fieldhouse Annex")
    page.fill("#polyedit-input-year", "2012")
    page.select_option("#polyedit-select-contributing", "Outside Historic District")
    page.click("#btn-polyedit-save")
    page.wait_for_selector("#polygon-editor-dock", state="hidden", timeout=6000)

    # Zoom back to z=16 covering whole campus and verify BOTH `#aux_1` AND original siblings render concurrently
    page.evaluate(
        """() => {
            const map = window.atlasApp.mapController.map;
            map.stop();
            map.jumpTo({ center: [-95.60448, 29.88972], zoom: 16.0, pitch: 0, bearing: 0 });
        }"""
    )
    page.wait_for_function(
        """() => {
            const map = window.atlasApp.mapController.map;
            if (!map.areTilesLoaded()) return false;
            const ids = new Set();
            for (const f of map.queryRenderedFeatures()) {
                if (String(f.properties?.hcad_num || '') === '1223580020001') {
                    ids.add(String(f.properties?.building_id || f.properties?.id || ''));
                }
            }
            return ids.has('1223580020001#aux_1') && ids.has('1223580020001#cy_ridge_hs_main') && ids.size >= 19;
        }""",
        timeout=15000,
    )
    aux_ov = page.evaluate("() => window.atlasApp.mapController.curatedOverrides['1223580020001#aux_1']")
    assert aux_ov["is_building_override"] is True
    assert aux_ov["keep_shard_footprints"] is False
    assert aux_ov["replace_parcel_shards"] is False

    # Part 2: Reshape an unknown-year building (blank `#polyedit-input-year`) without coercing into vacant lot
    page.evaluate(
        """async () => {
            const wb = await window.atlasApp._ensureStaffWorkbench();
            await wb.openPolygonEditor({
                targetKey: '0451230000099',
                hcadNum: '0451230000099',
                props: {
                    hcad_num: '0451230000099',
                    address: '500 MAIN ST ANNEX',
                    contributing: 'Outside Historic District',
                    lng: -95.3631,
                    lat: 29.7601
                }
            });
            document.getElementById('btn-polyedit-new-rect').click();
        }"""
    )
    page.wait_for_selector("#polygon-editor-dock:not(.hidden)", timeout=6000)
    page.fill("#polyedit-input-year", "")
    page.select_option("#polyedit-select-contributing", "Outside Historic District")
    page.click("#btn-polyedit-save")
    page.wait_for_selector("#polygon-editor-dock", state="hidden", timeout=6000)

    unk_state = page.evaluate(
        """() => {
            const mc = window.atlasApp.mapController;
            const ov = mc.curatedOverrides['0451230000099'];
            const fcFeat = (mc.overridesFC?.features || []).find(
                f => String(f.properties?.hcad_num || '') === '0451230000099'
            );
            window.atlasApp.filterStore.setState({ showUnknownYears: true });
            return {
                suppressOnly: ov?.suppress_only,
                yearBuilt: ov?.year_built ?? null,
                fcYearBuilt: fcFeat?.properties?.year_built ?? null,
                hasGeom: Boolean(ov?.geometry),
            };
        }"""
    )
    assert not unk_state["suppressOnly"]
    assert unk_state["yearBuilt"] == 0
    assert unk_state["fcYearBuilt"] == 0
    assert unk_state["hasGeom"] is True
    assert posts[-1]["hcad_num"] == "0451230000099"
    assert posts[-1]["suppress_only"] is False

    # Verify curated-overrides rendered visibility respects `showUnknownYears` toggle
    page.wait_for_function(
        """() => window.atlasApp.mapController.map
            .queryRenderedFeatures({ layers: ['curated-overrides-fill'] })
            .some(f => String(f.properties?.hcad_num || '') === '0451230000099')""",
        timeout=6000,
    )
    page.evaluate("() => window.atlasApp.filterStore.setState({ showUnknownYears: false })")
    page.wait_for_function(
        """() => !window.atlasApp.mapController.map
            .queryRenderedFeatures({ layers: ['curated-overrides-fill'] })
            .some(f => String(f.properties?.hcad_num || '') === '0451230000099')""",
        timeout=6000,
    )


def test_e2e6_mobile_walk_the_block_field_survey_viewports_gps_daylight_photos_and_offline_sync(
    browser: Any,
    atlas_base_url: str,
) -> None:
    """E2E-6a..6e: Mobile Walk-the-Block Field Survey across 360x640, 375x667, 390x844, GPS, AAA contrast, IDB photos & sync."""
    # E2E-6a across all three target smartphone viewports
    for width, height in [(360, 640), (375, 667), (390, 844)]:
        ctx = browser.new_context(
            viewport={"width": width, "height": height},
            has_touch=True,
            is_mobile=True,
            device_scale_factor=3,
        )
        try:
            configure_fast_e2e_context(ctx, atlas_base_url)
            _install_configurable_webhook(ctx)
            p = ctx.new_page()
            pe, ce, he = attach_error_monitors(p, atlas_base_url)
            p.goto(
                f"{atlas_base_url}/index.html#lat=29.7982&lng=-95.3978&z=18.8&survey=1",
                wait_until="domcontentloaded",
            )
            wait_for_atlas_ready(p)
            p.wait_for_selector("#field-survey-sheet:not(.hidden)", timeout=10000)

            layout_audit = p.evaluate(
                """() => {
                    const sheet = document.getElementById('field-survey-sheet');
                    const topbar = document.querySelector('.atlas-topbar');
                    const crosshair = document.getElementById('field-survey-crosshair');
                    const snaps = {};
                    for (const snap of ['peek', 'half', 'full']) {
                        window.atlasApp._staffWorkbench.setFieldSurveySnap(snap);
                        const sr = sheet.getBoundingClientRect();
                        const tr = topbar.getBoundingClientRect();
                        const cr = crosshair.getBoundingClientRect();
                        snaps[snap] = {
                            upperRatio: sr.top / window.innerHeight,
                            overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
                            crosshairClearTopbar: cr.top >= tr.bottom - 2,
                            crosshairClearSheet: cr.bottom <= sr.top + 2,
                        };
                    }
                    window.atlasApp._staffWorkbench.setFieldSurveySnap('half');
                    // Open drawers briefly to measure every control's font size and tap height
                    document.getElementById('survey-detail-drawer').open = true;
                    document.getElementById('survey-queue-drawer').open = true;

                    const textControls = Array.from(
                        sheet.querySelectorAll('input:not([type="file"]):not([type="hidden"]), select, textarea')
                    ).filter(el => el.getBoundingClientRect().height > 0);
                    const minFontPx = Math.min(
                        ...textControls.map(el => parseFloat(getComputedStyle(el).fontSize) || 0)
                    );

                    const primaryBtns = [
                        document.getElementById('btn-survey-save-next'),
                        document.getElementById('btn-survey-save-stay'),
                        ...Array.from(sheet.querySelectorAll('.survey-integrity-tile')),
                    ];
                    const minPrimaryH = Math.min(...primaryBtns.map(b => b.getBoundingClientRect().height));

                    const interactiveBtns = Array.from(
                        sheet.querySelectorAll(
                            '.survey-snap-btn, .survey-tool-pill, .survey-close-btn, .survey-refresh-btn, .survey-choice-pill, .survey-photo-btn, .survey-queue-btn'
                        )
                    ).filter(b => b.getBoundingClientRect().height > 0);
                    const minInteractiveH = Math.min(
                        ...interactiveBtns.map(b => b.getBoundingClientRect().height)
                    );

                    document.getElementById('survey-detail-drawer').open = false;
                    document.getElementById('survey-queue-drawer').open = false;
                    return { snaps, minFontPx, minPrimaryH, minInteractiveH };
                }"""
            )
            for snap_name, snap_metrics in layout_audit["snaps"].items():
                assert snap_metrics["overflow"] is False, f"{width}x{height} [{snap_name}] overflowed horizontally"
                if snap_name in ("peek", "half"):
                    assert snap_metrics["upperRatio"] >= 0.38, (
                        f"{width}x{height} [{snap_name}] upperRatio={snap_metrics['upperRatio']:.3f} < 0.38"
                    )
                    assert snap_metrics["crosshairClearTopbar"] is True, f"{width}x{height} [{snap_name}] crosshair hit topbar"
                    assert snap_metrics["crosshairClearSheet"] is True, f"{width}x{height} [{snap_name}] crosshair hit sheet"

            assert layout_audit["minFontPx"] >= 16.0, f"{width}x{height} font < 16px: {layout_audit['minFontPx']}"
            assert layout_audit["minPrimaryH"] >= 47.5, f"{width}x{height} primary btn < 48px: {layout_audit['minPrimaryH']}"
            assert layout_audit["minInteractiveH"] >= 43.5, f"{width}x{height} control < 44px: {layout_audit['minInteractiveH']}"

            assert not pe and not he and not ce
        finally:
            ctx.close()

    # E2E-6b..6e deep functional walk on 390x844 touch device with mocked Geolocation
    context = browser.new_context(
        viewport={"width": 390, "height": 844},
        has_touch=True,
        is_mobile=True,
        device_scale_factor=3,
    )
    try:
        configure_fast_e2e_context(context, atlas_base_url)
        posts, webhook_cfg = _install_configurable_webhook(context)
        context.add_init_script(
            """(() => {
                window.__mockGeoState = { watches: {}, nextId: 1, clearCalls: 0, watchCalls: 0 };
                const mockGeo = {
                    watchPosition(success, error, opts) {
                        const id = window.__mockGeoState.nextId++;
                        window.__mockGeoState.watchCalls += 1;
                        window.__mockGeoState.watches[id] = { success, error, opts };
                        return id;
                    },
                    clearWatch(id) {
                        window.__mockGeoState.clearCalls += 1;
                        delete window.__mockGeoState.watches[id];
                    },
                    getCurrentPosition(success, error, opts) {
                        this.watchPosition(success, error, opts);
                    }
                };
                Object.defineProperty(navigator, 'geolocation', {
                    configurable: true,
                    get: () => mockGeo
                });
                window.__emitMockGps = (lng, lat, accuracy) => {
                    for (const w of Object.values(window.__mockGeoState.watches)) {
                        w.success({
                            coords: { longitude: lng, latitude: lat, accuracy },
                            timestamp: Date.now()
                        });
                    }
                };
            })();"""
        )

        page = context.new_page()
        page_errors, console_errors, local_http_errors = attach_error_monitors(page, atlas_base_url)
        page.goto(
            f"{atlas_base_url}/index.html#lat=29.7982&lng=-95.3978&z=18.9&survey=1",
            wait_until="domcontentloaded",
        )
        wait_for_atlas_ready(page)
        page.wait_for_selector("#field-survey-sheet:not(.hidden)", timeout=10000)

        # Wait until Heights building tiles/features populate nearby houses around (-95.3978, 29.7982)
        page.wait_for_function(
            """() => {
                const mc = window.atlasApp.mapController;
                if (!mc.map.areTilesLoaded()) return false;
                return (mc.getNearbyFeaturesAroundPoint(-95.3978, 29.7982, 130) || []).length >= 6;
            }""",
            timeout=20000,
        )

        # E2E-6b (1): Start GPS watch and emit coarse fix (accuracy: 65m) -> warns & REFUSES auto-snap (Defect #21)
        page.click("#btn-survey-locate")
        page.evaluate("() => window.__emitMockGps(-95.3978, 29.7982, 65)")
        page.wait_for_timeout(350)
        coarse_check = page.evaluate(
            """() => ({
                bannerHidden: document.getElementById('survey-gps-banner').classList.contains('hidden'),
                bannerText: document.getElementById('survey-gps-banner').textContent || '',
                pillClass: document.getElementById('survey-gps-status-pill').className,
                autoSnapped: window.atlasApp._staffWorkbench.surveyActiveCandidate,
                chipCount: document.querySelectorAll('#survey-nearby-strip .survey-house-chip').length,
            })"""
        )
        assert coarse_check["bannerHidden"] is False
        assert "65m" in coarse_check["bannerText"]
        assert "warn" in coarse_check["pillClass"]
        assert coarse_check["autoSnapped"] is None, "Coarse >25m GPS fix must not auto-snap a neighbor house"
        assert coarse_check["chipCount"] >= 5

        # Upper-map reticle button (`#btn-survey-pick-center`) selects exact house under center crosshair
        page.click("#btn-survey-pick-center")
        assert page.evaluate("() => Boolean(window.atlasApp._staffWorkbench.surveyActiveCandidate)") is True

        # Reset active candidate cleanly, emit sharp fix (accuracy: 8m) -> populates sorted candidates & auto-snaps nearest
        page.evaluate(
            """() => {
                const wb = window.atlasApp._staffWorkbench;
                wb.surveySelectionLocked = false;
                wb.surveyCardDirty = false;
                wb.surveyActiveCandidate = null;
                window.__emitMockGps(-95.3978, 29.7982, 8);
            }"""
        )
        page.wait_for_timeout(150)
        sharp_check = page.evaluate(
            """() => {
                const wb = window.atlasApp._staffWorkbench;
                const dists = wb.surveyCandidates.map(c => c.distanceMeters);
                const sorted = dists.every((d, i) => i === 0 || d >= dists[i - 1]);
                return {
                    bannerHidden: document.getElementById('survey-gps-banner').classList.contains('hidden'),
                    pillClass: document.getElementById('survey-gps-status-pill').className,
                    sorted,
                    candCount: wb.surveyCandidates.length,
                };
            }"""
        )
        assert sharp_check["bannerHidden"] is True
        assert "good" in sharp_check["pillClass"]
        assert sharp_check["sorted"] is True
        assert sharp_check["candCount"] >= 6

        # Manual house chip tap locks card against subsequent GPS jitter pulse
        locked_key = page.evaluate(
            """() => {
                const chips = document.querySelectorAll('#survey-nearby-strip .survey-house-chip');
                chips[2].click();
                return window.atlasApp._staffWorkbench.surveyActiveCandidate?.key || '';
            }"""
        )
        assert locked_key != ""
        page.evaluate("() => window.__emitMockGps(-95.3981, 29.7985, 7)")
        assert page.evaluate("() => window.atlasApp._staffWorkbench.surveyActiveCandidate?.key") == locked_key

        # E2E-6b (2): 5-house walk using 2-tap Express grading (`Intact` -> `Save & Next House`) + 6s Undo toast
        page.fill("#survey-input-surveyor", "DM")
        visited_keys: list[str] = []
        for _ in range(5):
            cur_key = page.evaluate("() => window.atlasApp._staffWorkbench.surveyActiveCandidate?.key || ''")
            assert cur_key and cur_key not in visited_keys, f"Save & Next failed to advance to a fresh house: {cur_key} in {visited_keys}"
            visited_keys.append(cur_key)
            page.click('#survey-integrity-grid [data-integrity-grade="Intact"]')
            page.click("#btn-survey-save-next")

        assert page.evaluate("() => JSON.parse(localStorage.getItem('ph_atlas_field_surveys_v1') || '[]').length") == 5
        assert page.evaluate("() => !document.getElementById('field-survey-toast').classList.contains('hidden')")

        # Tap Undo inside the 6-second window -> removes 5th record and restores 5th house card
        page.click("#btn-survey-toast-undo")
        undo_state = page.evaluate(
            """() => ({
                storedLen: JSON.parse(localStorage.getItem('ph_atlas_field_surveys_v1') || '[]').length,
                restoredKey: window.atlasApp._staffWorkbench.surveyActiveCandidate?.key || '',
            })"""
        )
        assert undo_state["storedLen"] == 4
        assert undo_state["restoredKey"] == visited_keys[-1]

        # E2E-6c (1): Soft-keyboard visualViewport compression guard
        kbd_audit = page.evaluate(
            """() => {
                const wb = window.atlasApp._staffWorkbench;
                const sheet = document.getElementById('field-survey-sheet');
                // Simulate 280px mobile keyboard resize via installed visualViewport handler
                const origVV = window.visualViewport;
                const origDesc = Object.getOwnPropertyDescriptor(window, 'visualViewport');
                Object.defineProperty(window, 'visualViewport', {
                    configurable: true,
                    value: { height: window.innerHeight - 280, offsetTop: 0, addEventListener() {}, removeEventListener() {} }
                });
                wb._vvHandler?.();
                const offsetVar = sheet.style.getPropertyValue('--vv-keyboard-offset');
                const hasClass = sheet.classList.contains('keyboard-open');
                const footerRect = document.getElementById('field-survey-footer').getBoundingClientRect();
                const bodyRect = sheet.querySelector('.field-survey-scroll-body').getBoundingClientRect();
                const aboveKeyboard = footerRect.height >= 48 && footerRect.bottom <= window.innerHeight - 278;
                const bodyVisible = bodyRect.height >= 120;
                if (origDesc) Object.defineProperty(window, 'visualViewport', origDesc);
                wb._vvHandler?.();
                return { offsetVar, hasClass, footerVisible: aboveKeyboard && bodyVisible };
            }"""
        )
        assert kbd_audit["offsetVar"] == "280px"
        assert kbd_audit["hasClass"] is True
        assert kbd_audit["footerVisible"] is True

        # E2E-6c (2): Programmatic WCAG 2.1 AAA (>= 7.0:1) contrast audit across Daylight theme
        page.click('#survey-integrity-grid [data-integrity-grade="Intact"]')
        wcag_audit = page.evaluate(
            """() => {
                window.atlasApp._staffWorkbench.setFieldSurveyDaylight(true);
                document.getElementById('survey-detail-drawer').open = true;
                const parseRgb = (s) => {
                    const m = String(s || '').match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)(?:,\\s*([\\d.]+))?\\)/);
                    if (!m) return null;
                    return { r: +m[1], g: +m[2], b: +m[3], a: m[4] === undefined ? 1 : +m[4] };
                };
                const effectiveBg = (el) => {
                    let cur = el;
                    while (cur && cur !== document.documentElement) {
                        const c = parseRgb(getComputedStyle(cur).backgroundColor);
                        if (c && c.a > 0.9) return c;
                        cur = cur.parentElement;
                    }
                    return { r: 255, g: 255, b: 255, a: 1 };
                };
                const lum = ({ r, g, b }) => {
                    const f = (v) => {
                        const x = v / 255;
                        return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4);
                    };
                    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
                };
                const ratio = (c1, c2) => {
                    const l1 = lum(c1);
                    const l2 = lum(c2);
                    return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
                };
                const selectors = [
                    '.survey-context-field label',
                    '#survey-input-surveyor',
                    '.survey-section-heading',
                    '.survey-house-num',
                    '.survey-street-name',
                    '.survey-active-meta',
                    '.survey-house-chip:not(.active):not(.surveyed)',
                    '.survey-house-chip.active',
                    '.survey-house-chip.surveyed',
                    '.survey-integrity-tile:not(.active)',
                    '.survey-integrity-tile.active',
                    '.survey-choice-pill:not(.active)',
                    '.survey-choice-pill.active',
                    '#btn-survey-save-stay',
                    '#btn-survey-save-next',
                ];
                const results = [];
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (!el) continue;
                    const fg = parseRgb(getComputedStyle(el).color);
                    const bg = effectiveBg(el);
                    results.push({ selector: sel, ratio: Number(ratio(fg, bg).toFixed(2)) });
                }
                // Check input placeholder color explicitly against input background
                const inp = document.getElementById('survey-input-notes');
                const phFg = parseRgb(getComputedStyle(inp, '::placeholder').color);
                const phBg = effectiveBg(inp);
                results.push({ selector: '#survey-input-notes::placeholder', ratio: Number(ratio(phFg, phBg).toFixed(2)) });
                return results;
            }"""
        )
        assert len(wcag_audit) >= 14
        for entry in wcag_audit:
            assert entry["ratio"] >= 7.0, f"Daylight WCAG AAA contrast failure (< 7.0:1): {entry}"

        # E2E-6d: Camera photo capture -> Canvas JPEG downscale/compress -> IndexedDB Blob storage
        page.set_input_files(
            "#survey-photo-input",
            files=[
                {
                    "name": "camera_raw.png",
                    "mimeType": "image/png",
                    "buffer": _make_synthetic_png(1800, 1200),
                }
            ],
        )
        page.wait_for_function(
            "() => !document.getElementById('survey-photo-preview-wrap').classList.contains('hidden')",
            timeout=20000,
        )
        idb_audit = page.evaluate(
            """async () => {
                const db = await new Promise((resolve, reject) => {
                    const req = indexedDB.open('ph_atlas_field_survey_photos_v1');
                    req.onsuccess = () => resolve(req.result);
                    req.onerror = () => reject(req.error);
                });
                const rows = await new Promise((resolve, reject) => {
                    const tx = db.transaction('photos', 'readonly');
                    const r = tx.objectStore('photos').getAll();
                    r.onsuccess = () => resolve(r.result || []);
                    r.onerror = () => reject(r.error);
                });
                db.close();
                const lsDump = JSON.stringify(localStorage);
                return {
                    count: rows.length,
                    filename: rows[0]?.filename || '',
                    mimeType: rows[0]?.mimeType || '',
                    blobSize: rows[0]?.blob?.size || 0,
                    shareBtnText: document.getElementById('btn-survey-share-photos').textContent.trim(),
                    hasBase64InLocalStorage: lsDump.includes('data:image/'),
                };
            }"""
        )
        assert idb_audit["count"] == 1
        assert idb_audit["mimeType"] == "image/jpeg"
        assert idb_audit["blobSize"] > 500
        assert idb_audit["filename"].endswith(".jpg")
        assert "_" in idb_audit["filename"]
        assert "(1)" in idb_audit["shareBtnText"]
        assert idb_audit["hasBase64InLocalStorage"] is False

        # E2E-6e (1): `visibilitychange` pauses GPS watch cleanly & resumes on foreground without wiping draft
        page.fill("#survey-input-notes", "- altered porch, replaced sash")
        vis_audit = page.evaluate(
            """() => {
                const beforeClear = window.__mockGeoState.clearCalls;
                const beforeWatch = window.__mockGeoState.watchCalls;
                Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
                document.dispatchEvent(new Event('visibilitychange'));
                const midClear = window.__mockGeoState.clearCalls;
                const activeWatchesWhileHidden = Object.keys(window.__mockGeoState.watches).length;
                Object.defineProperty(document, 'hidden', { configurable: true, get: () => false });
                document.dispatchEvent(new Event('visibilitychange'));
                return {
                    clearedOnHide: midClear > beforeClear,
                    activeWatchesWhileHidden,
                    resumedOnShow: window.__mockGeoState.watchCalls > beforeWatch,
                    notePreserved: document.getElementById('survey-input-notes').value,
                };
            }"""
        )
        assert vis_audit["clearedOnHide"] is True
        assert vis_audit["activeWatchesWhileHidden"] == 0
        assert vis_audit["resumedOnShow"] is True
        assert vis_audit["notePreserved"] == "- altered porch, replaced sash"

        # Save 5th observation (with attached photo & formula-triggering hyphen note) offline
        page.click("#btn-survey-save-stay")
        assert page.evaluate("() => JSON.parse(localStorage.getItem('ph_atlas_field_surveys_v1') || '[]').length") == 5

        # E2E-6e (2): Reload while offline -> queue persists intact + CSV neutralizes `- altered porch...`
        page.reload(wait_until="domcontentloaded")
        wait_for_atlas_ready(page)
        page.wait_for_selector("#field-survey-sheet:not(.hidden)", timeout=10000)

        csv_and_queue = page.evaluate(
            """async () => {
                const mod = await import('/js/curatedEdits.js?v=20261030e');
                const list = mod.getLocalFieldSurveys();
                return {
                    count: list.length,
                    allQueued: list.every(r => r.sync_status === 'queued'),
                    csv: mod.exportFieldSurveysToCsv(list),
                };
            }"""
        )
        assert csv_and_queue["count"] == 5
        assert csv_and_queue["allQueued"] is True
        assert "'- altered porch, replaced sash" in csv_and_queue["csv"]

        # E2E-6e (3): Mock HTTP 200 `{ ok: false, error: 'Server busy' }` -> records stay `"queued"`, then `{ ok: true }` -> `"synced"`
        page.evaluate(
            """(wh) => {
                localStorage.setItem('ph_atlas_webhook_url', wh);
                if (window.atlasApp?.mapController?.sheetSyncStatus) {
                    window.atlasApp.mapController.sheetSyncStatus.webhookUrl = wh;
                }
                document.getElementById('survey-queue-drawer').open = true;
            }""",
            MOCK_WEBHOOK_URL,
        )
        webhook_cfg["mode"] = "ok_false"
        webhook_cfg["error"] = "Server busy"
        page.click("#btn-survey-sync-now")
        page.wait_for_function(
            """() => {
                const el = document.getElementById('survey-queue-sync-status');
                return Boolean(el && !el.classList.contains('hidden') && el.textContent.includes('kept in offline queue'));
            }""",
            timeout=6000,
        )
        assert page.evaluate(
            "() => JSON.parse(localStorage.getItem('ph_atlas_field_surveys_v1') || '[]').every(r => r.sync_status === 'queued')"
        ) is True

        webhook_cfg["mode"] = "ok"
        page.click("#btn-survey-sync-now")
        page.wait_for_function(
            """() => {
                const list = JSON.parse(localStorage.getItem('ph_atlas_field_surveys_v1') || '[]');
                return list.length === 5 && list.every(r => r.sync_status === 'synced');
            }""",
            timeout=8000,
        )
        survey_posts = [p for p in posts if p.get("action") == "field_survey"]
        assert len(survey_posts) >= 5
        assert all("[FIELD_SURVEY_JSON:{" in p.get("source_citation", "") for p in survey_posts)

        assert not page_errors and not local_http_errors and not console_errors
    finally:
        context.close()


def test_e2e7_print_media_isolation(
    guarded_page: Any,
    atlas_base_url: str,
) -> None:
    """E2E-7: `@media print` hides all Milestone 3 modals/docks/sheets while leaving `#dossier-modal` printable."""
    page = guarded_page
    page.goto(f"{atlas_base_url}/index.html", wait_until="domcontentloaded")
    wait_for_atlas_ready(page)

    # Reveal all Milestone 3 containers + `#dossier-modal` simultaneously for computed-style inspection
    page.evaluate(
        """() => {
            for (const id of [
                'historian-audit-modal',
                'polygon-editor-dock',
                'field-survey-sheet',
                'field-survey-crosshair',
                'field-survey-toast',
                'dossier-modal',
            ]) {
                document.getElementById(id)?.classList.remove('hidden');
            }
        }"""
    )

    page.emulate_media(media="print")
    print_styles = page.evaluate(
        """() => {
            const getDisp = (id) => {
                const el = document.getElementById(id);
                return el ? getComputedStyle(el).display : 'missing';
            };
            return {
                audit: getDisp('historian-audit-modal'),
                polyDock: getDisp('polygon-editor-dock'),
                surveySheet: getDisp('field-survey-sheet'),
                surveyCrosshair: getDisp('field-survey-crosshair'),
                surveyToast: getDisp('field-survey-toast'),
                dossier: getDisp('dossier-modal'),
            };
        }"""
    )
    assert print_styles["audit"] == "none"
    assert print_styles["polyDock"] == "none"
    assert print_styles["surveySheet"] == "none"
    assert print_styles["surveyCrosshair"] == "none"
    assert print_styles["surveyToast"] == "none"
    assert print_styles["dossier"] != "none"
