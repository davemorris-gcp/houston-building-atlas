"""Playwright E2E geometry and responsive layout gate for Milestone 11 Mobile UX Sweep.

Verifies across mobile portrait (390x664, 360x640), tablet/landscape (768x1024, 844x390),
desktop parity (1440x900), and @media print surface isolation across app/index.html and
app/plat-monitor.html in < 35s reusing configure_fast_e2e_context:
  - Zero horizontal document overflow (doc_overflow_x == 0) across all states and viewports.
  - All visible topbar controls in-bounds and unoccluded via elementFromPoint, including
    #btn-more-scorecards inside #topbar-more-dropdown auto-closing #topbar-more-dropdown on
    launch at <= 900px, and full Row-1 #btn-open-scorecards-hub + dual left-sidebar /
    right-inspector parity at 1440x900.
  - 100dvh safe-area modal clamping with Row-1 [x] close button alignment (no Row-2 flex-wrap
    drop, >= 32x32 mobile hit-targets, unoccluded elementFromPoint) across #permit-watch-modal,
    #scorecard-modal, #historian-audit-modal, #dossier-modal, #ch42-modal, and #correction-modal.
  - Zero HUD AABB overlap between #atlas-topbar / nav.tour-bar and #portfolio-monograph-card
    (including sticky .portfolio-card-top header on scroll), #city-chapters-card,
    #tour-narrative-banner, #polygon-editor-dock, #field-survey-sheet, and #inspector-drawer
    across peek / half / full mobile snap states, plus #atlas-sidebar z-index strictly above
    #inspector-drawer on mobile.
  - Mobile <= 900px text/search/select/textarea controls compute font-size >= 16px.
  - @media print surface isolation: #atlas-topbar, nav.tour-bar, #map-container, and
    .modal-header hide while printable modal cards compute max-height: none and
    overflow-y: visible even at mobile widths.
  - app/plat-monitor.html mobile header, #loadMenu / #exportMenu viewport clamping (including
    live window resize re-clamp), Demolition Benchmarks drawer, Property Watchlist table
    horizontal scroll wrappers, .toast pointer-events: none, 16px inputs, and Activity Log.
"""

from __future__ import annotations

from typing import Any

import pytest

from conftest import (
    ExternalUrlHarvester,
    attach_error_monitors,
    configure_fast_e2e_context,
    wait_for_atlas_ready,
)

MOBILE_PORTRAIT_VIEWPORTS: tuple[tuple[int, int], ...] = (
    (390, 664),
    (390, 844),
    (360, 640),
)
TABLET_LANDSCAPE_VIEWPORTS: tuple[tuple[int, int], ...] = (
    (768, 1024),
    (844, 390),
)
RESPONSIVE_VIEWPORTS: tuple[tuple[int, int], ...] = (
    *MOBILE_PORTRAIT_VIEWPORTS,
    *TABLET_LANDSCAPE_VIEWPORTS,
)
ALL_VIEWPORTS: tuple[tuple[int, int], ...] = (
    *RESPONSIVE_VIEWPORTS,
    (1440, 900),
)


def _reset_atlas_overlays_js() -> str:
    return """() => {
        for (const m of document.querySelectorAll('.modal-backdrop')) {
            m.classList.add('hidden');
        }
        const app = window.atlasApp;
        if (app) {
            app._closeTransientTopbarPopovers?.();
            app._setMobileSheetMode?.('half');
            app._setSidebarCollapsed?.(window.innerWidth <= 900);
        }
        [
            'city-chapters-card',
            'portfolio-monograph-card',
            'tour-narrative-banner',
            'polygon-editor-dock',
            'field-survey-sheet',
            'field-survey-crosshair',
            'map-overlap-bar',
            'isolation-active-banner',
            'inspector-drawer',
        ].forEach((id) => {
            const el = document.getElementById(id);
            if (el) el.classList.add('hidden');
        });
        const insp = document.getElementById('inspector-drawer');
        if (insp) {
            insp.classList.remove('sheet-peek', 'sheet-full');
            insp.classList.add('sheet-half', 'hidden');
        }
        app?._syncResponsiveHudOffsets?.();
    }"""


@pytest.fixture(scope="module")
def warm_mobile_ux_suite(
    browser: Any,
    atlas_base_url: str,
    url_harvester: ExternalUrlHarvester,
) -> Any:
    """Boots index.html and plat-monitor.html once and executes a single 5-viewport WebGL sweep."""
    context = browser.new_context(viewport={"width": 390, "height": 664})
    configure_fast_e2e_context(context, atlas_base_url)

    atlas_page = context.new_page()
    atlas_page_errs, atlas_console_errs, atlas_http_errs = attach_error_monitors(
        atlas_page, atlas_base_url
    )
    atlas_page.goto(f"{atlas_base_url}/index.html", wait_until="domcontentloaded")
    wait_for_atlas_ready(atlas_page)
    atlas_page.add_style_tag(
        content="*, *::before, *::after { transition-duration: 0s !important; animation-duration: 0s !important; }"
    )
    atlas_page.evaluate(
        """() => {
            sessionStorage.setItem('ph_atlas_admin_session', JSON.stringify({
                authorized: true,
                email: 'curator@preservationhouston.org',
                adminPasskey: 'test',
                unlockedAt: new Date().toISOString(),
            }));
            const map = window.atlasApp?.mapController?.map;
            if (map) {
                const origFlyTo = map.flyTo.bind(map);
                const origEaseTo = map.easeTo.bind(map);
                const origFitBounds = map.fitBounds.bind(map);
                map.flyTo = (opts) => origFlyTo({ ...(opts || {}), duration: 0, animate: false });
                map.easeTo = (opts) => origEaseTo({ ...(opts || {}), duration: 0, animate: false });
                map.fitBounds = (b, opts) => origFitBounds(b, { ...(opts || {}), duration: 0, animate: false });
            }
        }"""
    )

    # Hydrate all real Atlas modals, HUD cards, workbench surfaces, and parcel inspector once at 390x664
    atlas_page.evaluate(_reset_atlas_overlays_js())

    # Verify #btn-more-scorecards opens #scorecard-modal while auto-closing #topbar-more-dropdown + basemap menu
    atlas_page.evaluate(
        """() => {
            document.getElementById('btn-basemap-trigger').click();
            document.getElementById('btn-topbar-more').click();
            document.getElementById('btn-more-scorecards').click();
        }"""
    )
    atlas_page.wait_for_selector("#scorecard-modal:not(.hidden)", timeout=8000)
    more_launch_state = atlas_page.evaluate(
        """() => {
            const modal = document.getElementById('scorecard-modal');
            const more = document.getElementById('topbar-more-dropdown');
            const bm = document.getElementById('basemap-dropdown-menu');
            const topbar = document.getElementById('atlas-topbar');
            const res = {
                modalOpen: Boolean(modal && !modal.classList.contains('hidden')),
                moreOpenClass: Boolean(more && more.classList.contains('open')),
                moreOpenAttr: Boolean(more && more.hasAttribute('open')),
                bmHidden: Boolean(bm && bm.classList.contains('hidden')),
                modalZ: parseInt(window.getComputedStyle(modal).zIndex || '0', 10),
                topbarZ: parseInt(window.getComputedStyle(topbar).zIndex || '0', 10),
            };
            modal.classList.add('hidden');
            return res;
        }"""
    )

    # Trigger #audit=1 to lazy-load staffWorkbench.js (hydrates #historian-audit-modal, #polygon-editor-dock, #field-survey-sheet)
    atlas_page.evaluate("() => { window.location.hash = '#audit=1'; }")
    atlas_page.wait_for_selector("#historian-audit-modal:not(.hidden)", timeout=10000)
    atlas_page.evaluate(
        """async () => {
            window.atlasApp._staffWorkbench?.closeAuditModal?.();
            document.getElementById('historian-audit-modal').classList.add('hidden');

            window.atlasApp.openPermitWatchModal('critical', true);
            document.getElementById('permit-watch-modal').classList.add('hidden');

            await window.atlasApp.openPropertyDossierModal({
                hcad_num: '0010330000018',
                address: '310 MAIN ST',
                year_built: 1882,
            });
            document.getElementById('dossier-modal').classList.add('hidden');

            window.atlasApp.openCorrectionModal({
                hcad_num: '0621100000014',
                address: '1127 KEY ST',
                year_built: 1928,
            });
            document.getElementById('admin-auth-panel')?.classList.remove('hidden');
            document.getElementById('correction-modal').classList.add('hidden');

            window.atlasApp.openHahcTrackerModal({});
            document.getElementById('hahc-tracker-modal').classList.add('hidden');

            window.atlasApp.openSanbornBrowserModal();
            document.getElementById('sanborn-browser-modal').classList.add('hidden');

            window.atlasApp.openShareModal();
            document.getElementById('share-modal').classList.add('hidden');

            window.atlasApp.openPhotoLightboxModal?.();
            document.getElementById('photo-lightbox-modal')?.classList.add('hidden');

            await window.atlasApp.openChapter42ModalForHcad('0621100000014');
            document.getElementById('ch42-modal').classList.add('hidden');

            document.querySelector('#tour-pills button')?.click();
            document.getElementById('tour-narrative-banner')?.classList.add('hidden');
        }"""
    )

    atlas_page.evaluate("() => { window.location.hash = '#portfolio=architect:Alfred%20C.%20Finn'; }")
    atlas_page.wait_for_selector("#portfolio-monograph-card:not(.hidden)", timeout=8000)
    atlas_page.evaluate("() => document.getElementById('portfolio-monograph-card').classList.add('hidden')")

    atlas_page.evaluate("() => { window.location.hash = '#chapter=bayous_and_two_grids'; }")
    atlas_page.wait_for_selector("#city-chapters-card:not(.hidden)", timeout=8000)
    atlas_page.evaluate("() => document.getElementById('city-chapters-card').classList.add('hidden')")

    # Hydrate #inspector-drawer LAST so #inspector-drawer retains rendered parcel content + #btn-close-inspector
    atlas_page.evaluate("() => { window.location.hash = '#hcad=0010330000018'; }")
    atlas_page.wait_for_selector("#inspector-drawer:not(.hidden) #btn-close-inspector", timeout=10000)
    atlas_page.evaluate("() => document.getElementById('inspector-drawer').classList.add('hidden')")

    # Single 5-viewport WebGL sweep (only 4 viewport resizes total!) collecting per-domain audit results
    by_viewport: dict[str, dict[str, Any]] = {}
    for w, h in ALL_VIEWPORTS:
        cur = atlas_page.viewport_size or {}
        if cur.get("width") != w or cur.get("height") != h:
            atlas_page.set_viewport_size({"width": w, "height": h})
        vp_key = f"{w}x{h}"
        by_viewport[vp_key] = atlas_page.evaluate(
            """() => {
                const vw = window.innerWidth;
                const vh = window.innerHeight;
                const isMobile = vw <= 900;
                const app = window.atlasApp;
                window.dispatchEvent(new Event('resize'));

                const resetUi = () => {
                    for (const m of document.querySelectorAll('.modal-backdrop')) m.classList.add('hidden');
                    app?._closeTransientTopbarPopovers?.();
                    app?._setMobileSheetMode?.('half');
                    app?._setSidebarCollapsed?.(isMobile);
                    [
                        'city-chapters-card',
                        'portfolio-monograph-card',
                        'tour-narrative-banner',
                        'polygon-editor-dock',
                        'field-survey-sheet',
                        'inspector-drawer',
                    ].forEach((id) => document.getElementById(id)?.classList.add('hidden'));
                    const insp = document.getElementById('inspector-drawer');
                    if (insp) {
                        insp.classList.remove('sheet-peek', 'sheet-full');
                        insp.classList.add('sheet-half', 'hidden');
                    }
                    app?._syncResponsiveHudOffsets?.();
                };

                const overlapArea = (a, b) => {
                    const dx = Math.min(a.right, b.right) - Math.max(a.left, b.left);
                    const dy = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
                    return dx > 0 && dy > 0 ? dx * dy : 0;
                };

                const isUnoccluded = (el) => {
                    const r = el.getBoundingClientRect();
                    if (r.width < 0.5 || r.height < 0.5) return false;
                    const cx = Math.min(Math.max(r.left + r.width / 2, 1), vw - 1);
                    const cy = Math.min(Math.max(r.top + r.height / 2, 1), vh - 1);
                    const hit = document.elementFromPoint(cx, cy);
                    return Boolean(hit && (el === hit || el.contains(hit) || hit.contains(el)));
                };

                // --- Domain 1: Topbar, Popovers & Desktop Parity ---
                resetUi();
                const topbarErrs = [];
                const ox0 = Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) - vw;
                if (ox0 > 1) topbarErrs.push(`cold_boot doc_overflow_x=${ox0}`);
                const tbOff = parseFloat(document.documentElement.style.getPropertyValue('--topbar-offset') || '0');
                const hudOff = parseFloat(document.documentElement.style.getPropertyValue('--hud-top-offset') || '0');
                if (tbOff < 40 || hudOff < tbOff) topbarErrs.push(`invalid HUD offsets tb=${tbOff} hud=${hudOff}`);

                const checkCtrl = (sel) => {
                    const el = document.querySelector(sel);
                    if (!el) { topbarErrs.push(`${sel}: missing`); return; }
                    const r = el.getBoundingClientRect();
                    if (r.left < -1.5 || r.top < -1.5 || r.right > vw + 1.5 || r.bottom > vh + 1.5) {
                        topbarErrs.push(`${sel}: out of bounds [${r.left.toFixed(1)}, ${r.top.toFixed(1)}, ${r.right.toFixed(1)}, ${r.bottom.toFixed(1)}]`);
                    }
                    if (!isUnoccluded(el)) topbarErrs.push(`${sel}: occluded`);
                };

                [
                    '#btn-toggle-sidebar',
                    '#search-input',
                    '#btn-basemap-trigger',
                    '#btn-open-permit-watch-modal',
                    '#btn-topbar-more',
                ].forEach(checkCtrl);

                document.getElementById('btn-basemap-trigger').click();
                checkCtrl('#basemap-dropdown-menu');

                const scHub = document.getElementById('btn-open-scorecards-hub');
                const scHubDisp = window.getComputedStyle(scHub).display;
                if (isMobile) {
                    if (scHubDisp !== 'none') topbarErrs.push(`expected #btn-open-scorecards-hub hidden on <=900px, got ${scHubDisp}`);
                    document.getElementById('btn-topbar-more').click();
                    checkCtrl('#topbar-more-items');
                    checkCtrl('#btn-more-scorecards');
                    if (document.getElementById('btn-more-scorecards').getBoundingClientRect().height < 32) {
                        topbarErrs.push('#btn-more-scorecards height < 32px');
                    }
                } else {
                    if (scHubDisp === 'none') topbarErrs.push('expected #btn-open-scorecards-hub visible on desktop');
                    checkCtrl('#btn-open-scorecards-hub');
                    document.getElementById('btn-topbar-more').click();
                    if (window.getComputedStyle(document.getElementById('btn-more-scorecards')).display !== 'none') {
                        topbarErrs.push('expected #btn-more-scorecards hidden on desktop');
                    }
                    document.getElementById('btn-topbar-more').click();

                    app._setSidebarCollapsed(false);
                    const insp = document.getElementById('inspector-drawer');
                    insp.classList.remove('hidden');
                    const sbR = document.getElementById('atlas-sidebar').getBoundingClientRect();
                    const inR = insp.getBoundingClientRect();
                    if (sbR.left < -1.5 || sbR.width < 300) topbarErrs.push(`desktop sidebar invalid left=${sbR.left} w=${sbR.width}`);
                    if (inR.right > vw + 1.5 || inR.width < 340) topbarErrs.push(`desktop inspector invalid right=${inR.right} w=${inR.width}`);
                    if (overlapArea(sbR, inR) > 0) topbarErrs.push('desktop sidebar overlaps inspector');
                    if (window.getComputedStyle(document.getElementById('btn-cycle-mobile-sheet')).display !== 'none') {
                        topbarErrs.push('expected #btn-cycle-mobile-sheet hidden on desktop');
                    }
                }

                // --- Domain 2: Modals 100dvh Clamping & Row-1 Close Alignment ---
                resetUi();
                const modalErrs = [];
                const checkModal = (mSel, suffix = '') => {
                    const modal = document.querySelector(mSel);
                    const card = modal?.querySelector('.modal-card');
                    const header = card?.querySelector('.modal-header');
                    const closeBtn = header?.querySelector('.icon-btn');
                    if (!modal || !card || !header || !closeBtn) {
                        modalErrs.push(`${mSel}${suffix}: missing structure`);
                        return;
                    }
                    const ox = Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) - vw;
                    if (ox > 1) modalErrs.push(`${mSel}${suffix}: doc_overflow_x=${ox}`);
                    const cr = card.getBoundingClientRect();
                    const br = closeBtn.getBoundingClientRect();
                    if (cr.left < -1.5 || cr.top < -1.5 || cr.right > vw + 1.5 || cr.bottom > vh + 1.5) {
                        modalErrs.push(`${mSel}${suffix}: card out of 100dvh [${cr.left.toFixed(1)}, ${cr.top.toFixed(1)}, ${cr.right.toFixed(1)}, ${cr.bottom.toFixed(1)}]`);
                    }
                    if (br.left < -1.5 || br.top < -1.5 || br.right > vw + 1.5 || br.bottom > vh + 1.5 || !isUnoccluded(closeBtn)) {
                        modalErrs.push(`${mSel}${suffix}: closeBtn out of bounds or occluded`);
                    }
                    const rightGap = cr.right - br.right;
                    if (rightGap < -2 || rightGap > 36) modalErrs.push(`${mSel}${suffix}: rightGap=${rightGap.toFixed(1)}px`);
                    const firstChild = header.firstElementChild;
                    if (firstChild && firstChild !== closeBtn && !firstChild.contains(closeBtn)) {
                        const fcr = firstChild.getBoundingClientRect();
                        if (br.top > fcr.bottom - 4) modalErrs.push(`${mSel}${suffix}: closeBtn wrapped below Row-1 title`);
                    }
                    if (isMobile && (br.width < 32 || br.height < 32)) {
                        modalErrs.push(`${mSel}${suffix}: closeBtn < 32x32 (${br.width.toFixed(1)}x${br.height.toFixed(1)})`);
                    }
                };

                for (const mSel of [
                    '#about-modal',
                    '#correction-modal',
                    '#share-modal',
                    '#photo-lightbox-modal',
                    '#dossier-modal',
                    '#export-studio-modal',
                    '#sanborn-browser-modal',
                    '#permit-watch-modal',
                    '#hahc-tracker-modal',
                    '#scorecard-modal',
                    '#ch42-modal',
                    '#historian-audit-modal',
                ]) {
                    const m = document.querySelector(mSel);
                    m.classList.remove('hidden');
                    checkModal(mSel);
                    if (mSel === '#scorecard-modal') {
                        const tblBtn = document.getElementById('btn-sc-pane-table');
                        const detBtn = document.getElementById('btn-sc-pane-detail');
                        if (tblBtn && window.getComputedStyle(tblBtn).display !== 'none') {
                            tblBtn.click();
                            checkModal(mSel, ':table');
                            if (detBtn) detBtn.click();
                        }
                    }
                    m.classList.add('hidden');
                }

                // --- Domain 3: HUD Non-Overlap, Inspector Snap States & Sidebar Z-Index ---
                resetUi();
                const hudErrs = [];
                if (isMobile) {
                    const checkSurface = (sel, closeSel, expectTourHidden) => {
                        const el = document.querySelector(sel);
                        el.classList.remove('hidden');
                        app?._syncResponsiveHudOffsets?.();
                        const ox = Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) - vw;
                        if (ox > 1) hudErrs.push(`${sel}: doc_overflow_x=${ox}`);
                        const r = el.getBoundingClientRect();
                        if (r.left < -1.5 || r.top < -1.5 || r.right > vw + 1.5 || r.bottom > vh + 1.5) {
                            hudErrs.push(`${sel}: out of viewport [${r.left.toFixed(1)}, ${r.top.toFixed(1)}, ${r.right.toFixed(1)}, ${r.bottom.toFixed(1)}]`);
                        }
                        const tb = document.getElementById('atlas-topbar').getBoundingClientRect();
                        if (overlapArea(r, tb) > 2) hudErrs.push(`${sel}: overlaps #atlas-topbar`);
                        const tour = document.querySelector('nav.tour-bar');
                        const tourCs = window.getComputedStyle(tour);
                        if (expectTourHidden && tourCs.display !== 'none') {
                            hudErrs.push(`${sel}: expected nav.tour-bar hidden, got ${tourCs.display}`);
                        }
                        if (tourCs.display !== 'none' && tourCs.visibility !== 'hidden') {
                            const tr = tour.getBoundingClientRect();
                            if (tr.height > 0.5 && overlapArea(r, tr) > 2) hudErrs.push(`${sel}: overlaps nav.tour-bar`);
                        }
                        if (closeSel) {
                            const cBtn = document.querySelector(closeSel);
                            if (!cBtn || !isUnoccluded(cBtn)) hudErrs.push(`${sel}: ${closeSel} missing or occluded`);
                        }
                    };

                    checkSurface('#portfolio-monograph-card', '#btn-exit-portfolio', true);
                    const pCard = document.getElementById('portfolio-monograph-card');
                    const pTop = pCard.querySelector('.portfolio-card-top');
                    if (window.getComputedStyle(pTop).position !== 'sticky') hudErrs.push('portfolio-card-top not sticky');
                    pCard.scrollTop = 120;
                    const pRect = pCard.getBoundingClientRect();
                    const pClose = document.getElementById('btn-exit-portfolio');
                    const pCloseR = pClose.getBoundingClientRect();
                    if (pCloseR.top < pRect.top - 2 || pCloseR.bottom > pRect.top + 72 || !isUnoccluded(pClose)) {
                        hudErrs.push('btn-exit-portfolio did not remain sticky/unoccluded on scroll');
                    }
                    pCard.classList.add('hidden');

                    checkSurface('#city-chapters-card', '#btn-exit-city-chapters', true);
                    document.getElementById('city-chapters-card').classList.add('hidden');

                    checkSurface('#tour-narrative-banner', '#btn-close-tour-banner', false);
                    document.getElementById('tour-narrative-banner').classList.add('hidden');

                    const insp = document.getElementById('inspector-drawer');
                    insp.classList.remove('hidden');
                    const heights = {};
                    const cycleBtn = document.getElementById('btn-cycle-mobile-sheet');
                    for (let i = 0; i < 3; i++) {
                        const mode = insp.classList.contains('sheet-peek')
                            ? 'peek'
                            : insp.classList.contains('sheet-full')
                              ? 'full'
                              : 'half';
                        checkSurface('#inspector-drawer', '#btn-close-inspector', mode === 'full');
                        heights[mode] = insp.getBoundingClientRect().height;
                        cycleBtn.click();
                    }
                    if (!(heights.peek < heights.half && heights.half < heights.full)) {
                        hudErrs.push(`inspector snap heights not monotonic: ${JSON.stringify(heights)}`);
                    }

                    const sb = document.getElementById('atlas-sidebar');
                    const sbZ = parseInt(window.getComputedStyle(sb).zIndex || '0', 10);
                    const insZ = parseInt(window.getComputedStyle(insp).zIndex || '0', 10);
                    if (!(sbZ > insZ)) hudErrs.push(`sidebarZ (${sbZ}) <= inspectorZ (${insZ})`);
                    app._setSidebarCollapsed(false);
                    if (sb.classList.contains('collapsed') || !insp.classList.contains('hidden')) {
                        hudErrs.push('opening mobile sidebar did not hide inspector');
                    }
                    const closeSb = document.getElementById('btn-close-sidebar-mobile');
                    if (!closeSb || !isUnoccluded(closeSb)) hudErrs.push('#btn-close-sidebar-mobile occluded');
                    app._setSidebarCollapsed(true);

                    checkSurface('#polygon-editor-dock', '#btn-close-polyedit', true);
                    if (vw <= 559) {
                        const dockR = document.getElementById('polygon-editor-dock').getBoundingClientRect();
                        const brCtrl = document.querySelector('.maplibregl-ctrl-bottom-right');
                        if (brCtrl && window.getComputedStyle(brCtrl).display !== 'none') {
                            if (overlapArea(dockR, brCtrl.getBoundingClientRect()) > 4) {
                                hudErrs.push('maplibregl-ctrl-bottom-right overlaps narrow #polygon-editor-dock');
                            }
                        }
                    }
                    document.getElementById('polygon-editor-dock').classList.add('hidden');

                    checkSurface('#field-survey-sheet', '#btn-close-field-survey', true);
                    document.getElementById('field-survey-sheet').classList.add('hidden');
                }

                // --- Domain 4: Mobile 16px Form Input Guard ---
                resetUi();
                const inputErrs = [];
                if (isMobile) {
                    app._setSidebarCollapsed(false);
                    document.getElementById('correction-modal').classList.remove('hidden');
                    const nodes = document.querySelectorAll(
                        'input:not([type="checkbox"]):not([type="radio"]):not([type="range"]):not([type="file"]):not([type="hidden"]), select, textarea'
                    );
                    for (const el of nodes) {
                        const cs = window.getComputedStyle(el);
                        const r = el.getBoundingClientRect();
                        if (cs.display === 'none' || cs.visibility === 'hidden' || r.width < 1 || r.height < 1) continue;
                        const px = parseFloat(cs.fontSize || '0');
                        if (px < 15.9) inputErrs.push(`${el.id || el.name || el.tagName}: ${px}px`);
                    }
                    resetUi();
                }

                return { topbarErrs, modalErrs, hudErrs, inputErrs };
            }"""
        )

    # Boot plat-monitor.html once for Test 5
    plat_page = context.new_page()
    plat_page_errs, plat_console_errs, plat_http_errs = attach_error_monitors(
        plat_page, atlas_base_url
    )
    plat_page.goto(
        f"{atlas_base_url}/plat-monitor.html?mode=agenda",
        wait_until="domcontentloaded",
    )
    plat_page.wait_for_selector("#list .row", timeout=20000)

    yield {
        "atlas_page": atlas_page,
        "plat_page": plat_page,
        "more_launch_state": more_launch_state,
        "by_viewport": by_viewport,
    }

    url_harvester.harvest_from_locator(atlas_page.locator("body"), "mobile_ux:index")
    url_harvester.harvest_from_locator(plat_page.locator("body"), "mobile_ux:plat_monitor")

    context.close()

    assert not atlas_page_errs, f"Uncaught JS pageerror(s) on index.html: {atlas_page_errs}"
    assert not atlas_http_errs, f"Local HTTP >= 400 failure(s) on index.html: {atlas_http_errs}"
    assert not atlas_console_errs, f"Console error(s) on index.html: {atlas_console_errs}"
    assert not plat_page_errs, f"Uncaught JS pageerror(s) on plat-monitor.html: {plat_page_errs}"
    assert not plat_http_errs, f"Local HTTP >= 400 failure(s) on plat-monitor.html: {plat_http_errs}"
    assert not plat_console_errs, f"Console error(s) on plat-monitor.html: {plat_console_errs}"


def test_e2e_m11_1_topbar_popovers_and_desktop_parity(warm_mobile_ux_suite: Any) -> None:
    sc_state = warm_mobile_ux_suite["more_launch_state"]
    assert sc_state["modalOpen"], f"Expected #scorecard-modal to open from #btn-more-scorecards: {sc_state}"
    assert not sc_state["moreOpenClass"] and not sc_state["moreOpenAttr"], (
        f"Expected #topbar-more-dropdown to auto-close when launching Scorecards: {sc_state}"
    )
    assert sc_state["bmHidden"], f"Expected #basemap-dropdown-menu auto-closed: {sc_state}"
    assert sc_state["modalZ"] > sc_state["topbarZ"], (
        f"Expected #scorecard-modal z-index ({sc_state['modalZ']}) > #atlas-topbar ({sc_state['topbarZ']})"
    )

    for w, h in ALL_VIEWPORTS:
        vp = f"{w}x{h}"
        errs = warm_mobile_ux_suite["by_viewport"][vp]["topbarErrs"]
        assert not errs, f"[{vp}] Topbar / popover / desktop parity failures: {errs}"


def test_e2e_m11_2_modals_safe_area_and_row1_close_alignment(warm_mobile_ux_suite: Any) -> None:
    for w, h in ALL_VIEWPORTS:
        vp = f"{w}x{h}"
        errs = warm_mobile_ux_suite["by_viewport"][vp]["modalErrs"]
        assert not errs, f"[{vp}] Modal 100dvh safe-area / Row-1 [x] close alignment failures: {errs}"


def test_e2e_m11_3_hud_non_overlap_inspector_snaps_and_sidebar_zindex(
    warm_mobile_ux_suite: Any,
) -> None:
    for w, h in RESPONSIVE_VIEWPORTS:
        vp = f"{w}x{h}"
        errs = warm_mobile_ux_suite["by_viewport"][vp]["hudErrs"]
        assert not errs, f"[{vp}] HUD non-overlap / inspector snap / sidebar z-index failures: {errs}"


def test_e2e_m11_4_mobile_16px_inputs_and_print_isolation(warm_mobile_ux_suite: Any) -> None:
    for w, h in RESPONSIVE_VIEWPORTS:
        vp = f"{w}x{h}"
        errs = warm_mobile_ux_suite["by_viewport"][vp]["inputErrs"]
        assert not errs, f"[{vp}] Mobile inputs/selects with font-size < 16px: {errs}"

    # Verify @media print surface isolation at 390x664 mobile width (zero @media screen leakage)
    atlas_page = warm_mobile_ux_suite["atlas_page"]
    atlas_page.set_viewport_size({"width": 390, "height": 664})
    atlas_page.evaluate(_reset_atlas_overlays_js())
    atlas_page.evaluate("() => document.getElementById('scorecard-modal').classList.remove('hidden')")
    atlas_page.emulate_media(media="print")
    sc_print = atlas_page.evaluate(
        """() => {
            const card = document.querySelector('#scorecard-modal .scorecard-modal-card');
            const header = document.querySelector('#scorecard-modal .modal-header');
            const topbar = document.getElementById('atlas-topbar');
            const tourbar = document.querySelector('nav.tour-bar');
            const mapEl = document.getElementById('map-container') || document.querySelector('.map-canvas');
            const cardCs = window.getComputedStyle(card);
            return {
                topbarDisplay: window.getComputedStyle(topbar).display,
                tourbarDisplay: window.getComputedStyle(tourbar).display,
                mapDisplay: mapEl ? window.getComputedStyle(mapEl).display : 'missing',
                headerDisplay: window.getComputedStyle(header).display,
                cardMaxHeight: cardCs.maxHeight,
                cardOverflowY: cardCs.overflowY,
            };
        }"""
    )
    assert sc_print["topbarDisplay"] == "none", f"Expected #atlas-topbar hidden in print: {sc_print}"
    assert sc_print["tourbarDisplay"] == "none", f"Expected nav.tour-bar hidden in print: {sc_print}"
    assert sc_print["mapDisplay"] == "none", f"Expected #map-container hidden in print: {sc_print}"
    assert sc_print["headerDisplay"] == "none", f"Expected .modal-header hidden in print: {sc_print}"
    assert sc_print["cardMaxHeight"] == "none" and sc_print["cardOverflowY"] == "visible", (
        f"@media screen mobile max-height / overflow leaked into @media print on #scorecard-modal: {sc_print}"
    )

    atlas_page.evaluate(
        """() => {
            document.getElementById('scorecard-modal').classList.add('hidden');
            document.getElementById('dossier-modal').classList.remove('hidden');
        }"""
    )
    dossier_print = atlas_page.evaluate(
        """() => {
            const card = document.querySelector('#dossier-modal .dossier-modal-card');
            const cardCs = window.getComputedStyle(card);
            return {
                cardMaxHeight: cardCs.maxHeight,
                cardOverflowY: cardCs.overflowY,
            };
        }"""
    )
    assert dossier_print["cardMaxHeight"] == "none" and dossier_print["cardOverflowY"] == "visible", (
        f"@media screen mobile max-height / overflow leaked into @media print on #dossier-modal: {dossier_print}"
    )
    atlas_page.emulate_media(media="screen")
    atlas_page.evaluate(_reset_atlas_overlays_js())


def test_e2e_m11_5_plat_monitor_mobile_geometry(warm_mobile_ux_suite: Any) -> None:
    plat_page = warm_mobile_ux_suite["plat_page"]

    for w, h in RESPONSIVE_VIEWPORTS:
        plat_page.set_viewport_size({"width": w, "height": h})
        vp = f"plat@{w}x{h}"
        plat_errs = plat_page.evaluate(
            """() => {
                const errs = [];
                const vw = window.innerWidth;
                const vh = window.innerHeight;
                window.scrollTo(0, 0);
                window.dispatchEvent(new Event('resize'));

                const checkOverflowX = (label) => {
                    const ox = Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) - vw;
                    if (ox > 1) errs.push(`${label}: doc_overflow_x=${ox}`);
                };

                const checkCtrlX = (sel) => {
                    const el = document.querySelector(sel);
                    if (!el) { errs.push(`${sel}: missing`); return; }
                    const r = el.getBoundingClientRect();
                    if (r.left < -1.5 || r.right > vw + 1.5) {
                        errs.push(`${sel}: x=[${r.left.toFixed(1)}, ${r.right.toFixed(1)}] outside [0, ${vw}]`);
                    }
                    if (r.top >= 0 && r.bottom <= vh) {
                        const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
                        if (!hit || !(el === hit || el.contains(hit) || hit.contains(el))) {
                            errs.push(`${sel}: occluded`);
                        }
                    }
                };

                // 1. Agenda mode + #loadMenu / #exportMenu clamping + Demolition Benchmarks
                document.getElementById('modeAgendaBtn').click();
                checkOverflowX('agenda');
                [
                    '#modeAgendaBtn',
                    '#modeWatchlistBtn',
                    '#modeActivityBtn',
                    '#toggleDemoBenchmarksBtn',
                    '#loadBtn',
                    '#exportBtn',
                ].forEach(checkCtrlX);

                document.getElementById('loadBtn').click();
                const loadR = document.querySelector('#loadMenu .menu-list').getBoundingClientRect();
                if (loadR.left < 8 || loadR.right > vw - 8) {
                    errs.push(`#loadMenu not clamped: [${loadR.left.toFixed(1)}, ${loadR.right.toFixed(1)}] vs ${vw}`);
                }
                document.getElementById('loadBtn').click();

                document.getElementById('exportBtn').click();
                const expR = document.querySelector('#exportMenu .menu-list').getBoundingClientRect();
                if (expR.left < 8 || expR.right > vw - 8) {
                    errs.push(`#exportMenu not clamped: [${expR.left.toFixed(1)}, ${expR.right.toFixed(1)}] vs ${vw}`);
                }
                document.getElementById('exportBtn').click();

                document.getElementById('toggleDemoBenchmarksBtn').click();
                checkOverflowX('demoBenchmarks');
                const boxes = Array.from(document.querySelectorAll('#demoBenchmarksDrawer .demo-bench-box'));
                if (!boxes.length || !boxes.every(b => ['auto', 'scroll'].includes(window.getComputedStyle(b).overflowX))) {
                    errs.push('demo-bench-box missing overflow-x wrapper');
                }
                document.getElementById('toggleDemoBenchmarksBtn').click();

                // 2. Watchlist mode + table horizontal scroll wrapper + .toast pointer-events: none + 16px inputs
                document.getElementById('modeWatchlistBtn').click();
                const firstRow = document.querySelector('#list .row');
                if (firstRow) firstRow.click();
                checkOverflowX('watchlist');
                checkCtrlX('#watchlistToolbar');
                checkCtrlX('#wlThreatFilter');

                const tables = Array.from(document.querySelectorAll('#detail table')).filter(
                    t => window.getComputedStyle(t).display !== 'none' && t.getBoundingClientRect().width > 0
                );
                if (!tables.length) errs.push('watchlist detail rendered 0 tables');
                for (const tbl of tables) {
                    let cur = tbl.parentElement;
                    let hasScrollX = false;
                    while (cur && cur !== document.body) {
                        const ox = window.getComputedStyle(cur).overflowX;
                        if (ox === 'auto' || ox === 'scroll') { hasScrollX = true; break; }
                        cur = cur.parentElement;
                    }
                    const tr = tbl.getBoundingClientRect();
                    if (!hasScrollX && (tr.right > vw + 1.5 || tr.left < -1.5)) {
                        errs.push(`unguarded wide watchlist table: ${tbl.className}`);
                    }
                }

                const probeToast = document.createElement('div');
                probeToast.className = 'toast';
                probeToast.textContent = 'Mobile layout check';
                document.body.appendChild(probeToast);
                const toastCs = window.getComputedStyle(probeToast);
                const toastRect = probeToast.getBoundingClientRect();
                if (toastCs.pointerEvents !== 'none') errs.push(`toast pointer-events=${toastCs.pointerEvents}`);
                if (toastRect.left < 8 || toastRect.right > vw - 8) {
                    errs.push(`toast overflows width: [${toastRect.left}, ${toastRect.right}]`);
                }
                probeToast.remove();

                const inputs = document.querySelectorAll(
                    'input:not([type="checkbox"]):not([type="radio"]):not([type="range"]):not([type="file"]):not([type="hidden"]), select, textarea'
                );
                for (const el of inputs) {
                    const cs = window.getComputedStyle(el);
                    const r = el.getBoundingClientRect();
                    if (cs.display === 'none' || cs.visibility === 'hidden' || r.width < 1 || r.height < 1) continue;
                    const px = parseFloat(cs.fontSize || '0');
                    if (px < 15.9) errs.push(`input ${el.id || el.tagName} font-size=${px}px < 16px`);
                }

                // 3. Activity Log mode
                document.getElementById('modeActivityBtn').click();
                checkOverflowX('activity');
                const banner = document.querySelector('#detail .act-banner');
                if (banner && window.getComputedStyle(banner).display !== 'none') {
                    const br = banner.getBoundingClientRect();
                    if (br.left < -1.5 || br.right > vw + 1.5) errs.push('act-banner overflows viewport width');
                }
                return errs;
            }"""
        )
        assert not plat_errs, f"[{vp}] plat-monitor.html mobile failures: {plat_errs}"

    # Verify live window resize re-clamps an already-open #exportMenu from 768x1024 down to 360x640
    plat_page.set_viewport_size({"width": 768, "height": 1024})
    plat_page.evaluate(
        "() => { document.getElementById('modeAgendaBtn').click(); document.getElementById('exportBtn').click(); }"
    )
    plat_page.set_viewport_size({"width": 360, "height": 640})
    reclamped = plat_page.evaluate(
        """() => {
            window.dispatchEvent(new Event('resize'));
            const r = document.querySelector('#exportMenu .menu-list').getBoundingClientRect();
            document.getElementById('exportBtn').click();
            return { left: r.left, right: r.right, width: r.width };
        }"""
    )
    assert reclamped["width"] > 100 and reclamped["left"] >= 8.0 and reclamped["right"] <= 352.0, (
        f"Expected open #exportMenu to re-clamp inside 360px viewport on resize, got {reclamped}"
    )

    # Verify zero horizontal overflow at 360x640 even under wider fallback OS font metrics (+0.5px letter-spacing)
    wide_font_overflow = plat_page.evaluate(
        """() => {
            const st = document.createElement('style');
            st.id = 'test-wide-font';
            st.textContent = 'body, button, input, select, textarea { letter-spacing: 0.5px !important; }';
            document.head.appendChild(st);
            const res = {};
            for (const btnId of ['modeAgendaBtn', 'modeWatchlistBtn', 'modeActivityBtn']) {
                document.getElementById(btnId).click();
                res[btnId] = document.documentElement.scrollWidth - window.innerWidth;
            }
            st.remove();
            return res;
        }"""
    )
    assert all(v <= 1 for v in wide_font_overflow.values()), (
        f"Expected zero horizontal overflow at 360x640 under wider font metrics, got {wide_font_overflow}"
    )
