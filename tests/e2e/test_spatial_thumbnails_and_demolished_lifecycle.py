"""Playwright E2E verification for shared spatial context thumbnails across all 4 monitoring surfaces,
calm #permit-watch-modal UX, and general Demolished / Lost lifecycle tab isolation at 1440x900 and 390x844.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from conftest import (
    APP_DIR,
    DEFAULT_E2E_VIEWPORT,
    ExternalUrlHarvester,
    attach_error_monitors,
    configure_fast_e2e_context,
    wait_for_atlas_ready,
)

MOBILE_VIEWPORT = {"width": 390, "height": 844}

EM_DASH = chr(0x2014)
EN_DASH = chr(0x2013)
UNI_HYPHEN = chr(0x2010)


def _assert_zero_forbidden_dashes(text: str, label: str) -> None:
    assert EM_DASH not in text, f"{label} rendered forbidden U+2014 em-dash"
    assert EN_DASH not in text, f"{label} rendered forbidden U+2013 en-dash"
    assert UNI_HYPHEN not in text, f"{label} rendered forbidden U+2010 hyphen"


def _assert_zero_horizontal_overflow(page: Any, selectors: list[str], context_label: str) -> None:
    metrics = page.evaluate(
        """(sels) => {
            const out = [{
                sel: 'documentElement',
                scrollW: document.documentElement.scrollWidth,
                clientW: document.documentElement.clientWidth,
            }];
            for (const s of sels) {
                const el = document.querySelector(s);
                if (el) {
                    out.push({
                        sel: s,
                        scrollW: el.scrollWidth,
                        clientW: el.clientWidth,
                    });
                }
            }
            return out;
        }""",
        selectors,
    )
    for m in metrics:
        assert m["scrollW"] <= m["clientW"] + 1, (
            f"[{context_label}] Horizontal overflow on {m['sel']}: "
            f"scrollWidth={m['scrollW']} > clientWidth={m['clientW']}"
        )


def test_e2e_atlas_permit_watch_modal_and_inspector_spatial_thumbnails_and_demolished_tabs(
    page: Any,
    atlas_base_url: str,
    url_harvester: ExternalUrlHarvester,
) -> None:
    """Verify Surfaces 1 & 2 (#permit-watch-modal and #inspector-permit-alert-card) at 1440x900 and 390x844."""
    configure_fast_e2e_context(page.context, atlas_base_url)
    page.set_viewport_size(DEFAULT_E2E_VIEWPORT)
    page_errors, console_errors, local_http_errors = attach_error_monitors(page, atlas_base_url)

    # Ensure time-invariant presence of both 0202150000018 (standing) and 0152300000001 (demolished)
    # even when rolling 180-day live permit windows advance in future years.
    live_alerts_doc = json.loads((APP_DIR / "public" / "data" / "active_permit_alerts.json").read_text(encoding="utf-8"))
    seeded_alerts = list(live_alerts_doc.get("alerts") or [])
    if not any(str(a.get("hcad_num") or "") == "0202150000018" for a in seeded_alerts):
        seeded_alerts.insert(
            0,
            {
                "hcad_num": "0202150000018",
                "building_id": "0202150000018",
                "address": "1020 CORTLANDT ST",
                "building_name": "1020 CORTLANDT ST",
                "year_built": 1910,
                "historic_district": "Houston Heights South Historic District",
                "neighborhood": "GREATER HEIGHTS",
                "lat": 29.78946,
                "lon": -95.39718,
                "lng": -95.39718,
                "footprint_ring": [
                    [-95.39724, 29.78942],
                    [-95.39712, 29.78942],
                    [-95.39712, 29.78951],
                    [-95.39724, 29.78951],
                    [-95.39724, 29.78942],
                ],
                "severity": "critical",
                "has_sewer_disc_plumbing": True,
                "has_demo_permit": False,
                "badge": "1 of 2 Permits: Sewer Disc Only",
                "headline": "Stage 1 Sewer Disconnect on 1020 Cortlandt St",
                "coh_permits": [{"project_no": "26095535", "date": "2026-11-05", "permit_type": "12", "permit_stage": "stage_1_sewer_plumbing"}],
                "dshs_notifications": [],
            },
        )
    if not any(str(a.get("hcad_num") or "") == "0152300000001" for a in seeded_alerts):
        seeded_alerts.append(
            {
                "hcad_num": "0152300000001",
                "building_id": "0152300000001",
                "address": "2000 SMITH ST",
                "building_name": "John Henry Kirby Mansion",
                "year_built": 1894,
                "year_demolished": 2026,
                "neighborhood": "Midtown",
                "lat": 29.75011,
                "lon": -95.37492,
                "lng": -95.37492,
                "footprint_ring": [
                    [-95.37502, 29.75005],
                    [-95.37482, 29.75005],
                    [-95.37482, 29.75019],
                    [-95.37502, 29.75019],
                    [-95.37502, 29.75005],
                ],
                "severity": "critical",
                "is_demolished": True,
                "demolition_status": "demolished",
                "demolished_date": "2026-11-02",
                "lost_houston_id": "lost_kirby_mansion_1894",
                "badge": "Demolished (Nov 2, 2026)",
                "headline": "Demolished October 30 - November 2, 2026",
                "coh_permits": [{"project_no": "26090093", "date": "2026-10-30", "permit_type": "SD", "permit_stage": "stage_2_demolition"}],
                "dshs_notifications": [],
            }
        )
    seeded_payload_bytes = json.dumps({**live_alerts_doc, "alerts": seeded_alerts}).encode("utf-8")
    page.route(
        "**/public/data/active_permit_alerts.json*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json; charset=utf-8",
            body=seeded_payload_bytes,
        ),
    )

    # Boot Atlas directly on README row 30 deep link #hcad=0202150000018&tab=permits
    page.goto(f"{atlas_base_url}/#hcad=0202150000018&tab=permits", wait_until="domcontentloaded")
    wait_for_atlas_ready(page)

    # --- Surface 2 (Desktop 1440x900): #inspector-permit-alert-card ---
    insp_card = page.locator("#inspector-permit-alert-card")
    insp_card.wait_for(state="visible", timeout=20000)
    insp_thumb = insp_card.locator('[data-spatial-thumb="1"]')
    insp_thumb.wait_for(state="visible", timeout=10000)

    insp_text, _ = url_harvester.harvest_text_and_urls(insp_card, "sct:inspector-permit-alert-card")
    _assert_zero_forbidden_dashes(insp_text, "#inspector-permit-alert-card")
    assert insp_thumb.locator("svg.atlas-spatial-overlay-svg path").count() == 1

    # Pin 610 Loop radar popover inside narrow .inspector-body and verify .stack-below + zero overflow
    insp_toggle = insp_thumb.locator('[data-spatial-pip-toggle="1"]')
    insp_toggle.click()
    assert insp_thumb.evaluate("el => el.classList.contains('is-pinned')") is True
    assert insp_thumb.evaluate("el => el.classList.contains('stack-below')") is True
    assert insp_toggle.get_attribute("aria-expanded") == "true"

    insp_popover = insp_thumb.locator(".atlas-spatial-pip-popover")
    insp_popover.wait_for(state="visible", timeout=5000)
    _assert_zero_forbidden_dashes(insp_popover.inner_text(), "#inspector-permit-alert-card popover")
    _assert_zero_horizontal_overflow(
        page, ["#inspector-body", "#inspector-permit-alert-card"], "Inspector 1440x900 pinned"
    )

    # Escape closes popover cleanly without closing #inspector-drawer
    page.keyboard.press("Escape")
    assert insp_thumb.evaluate("el => el.classList.contains('is-pinned')") is False
    assert insp_toggle.get_attribute("aria-expanded") == "false"
    assert insp_card.is_visible(), "Escape on pinned spatial popover must not close #inspector-drawer"

    # Check Surface 2 at 390x844 mobile (collapse desktop-opened left filter sidebar)
    page.set_viewport_size(MOBILE_VIEWPORT)
    page.evaluate("() => window.atlasApp._setSidebarCollapsed(true)")
    if not insp_card.is_visible():
        page.evaluate("() => document.getElementById('inspector-drawer')?.classList.remove('hidden')")
        insp_card.wait_for(state="visible", timeout=5000)
    insp_toggle.click()
    insp_popover.wait_for(state="visible", timeout=5000)
    pop_box_mob = insp_popover.bounding_box()
    assert pop_box_mob is not None
    assert pop_box_mob["x"] >= -1 and pop_box_mob["x"] + pop_box_mob["width"] <= 391
    _assert_zero_horizontal_overflow(
        page, ["#inspector-body", "#inspector-permit-alert-card"], "Inspector 390x844 pinned"
    )
    insp_thumb.locator('[data-spatial-pip-close="1"]').click()
    assert insp_thumb.evaluate("el => el.classList.contains('is-pinned')") is False

    # --- Surface 1 (Desktop 1440x900): #permit-watch-modal ---
    page.set_viewport_size(DEFAULT_E2E_VIEWPORT)
    page.evaluate("() => window.atlasApp.openPermitWatchModal('all')")
    pw_modal = page.locator("#permit-watch-modal")
    pw_modal.wait_for(state="visible", timeout=15000)

    # Calm UX: Methodology & benchmarks <details> collapsed by default
    assert page.locator("#pw-annual-benchmarks-details").evaluate("el => el.open") is False

    topbar_cnt = int((page.locator("#permit-watch-topbar-count").text_content() or "0").strip())
    standing_cnt = int((page.locator("#pw-pill-count-all").text_content() or "0").strip())
    demo_cnt = int((page.locator("#pw-pill-count-demolished").text_content() or "0").strip())
    assert standing_cnt == topbar_cnt and standing_cnt >= 1
    assert demo_cnt >= 1

    # Standing Threats default tab must NOT include demolished Kirby Mansion (2000 SMITH ST)
    standing_list_text = page.locator("#permit-watch-modal-list").inner_text()
    assert "2000 SMITH ST" not in standing_list_text
    _assert_zero_forbidden_dashes(standing_list_text[:4000], "#permit-watch-modal Standing Threats")

    first_card = page.locator("#permit-watch-modal-list .permit-watch-item-card").first
    first_thumb = first_card.locator('[data-spatial-thumb="1"]')
    first_thumb.wait_for(state="visible", timeout=10000)
    assert first_thumb.locator("svg.atlas-spatial-overlay-svg path").count() == 1

    # Pin first card's 610 Loop popover, verify Escape closes popover WITHOUT closing parent modal
    pw_toggle = first_thumb.locator('[data-spatial-pip-toggle="1"]')
    pw_toggle.click()
    pw_popover = first_thumb.locator(".atlas-spatial-pip-popover")
    pw_popover.wait_for(state="visible", timeout=5000)
    assert "610 LOOP" in (pw_popover.inner_html() or "")
    _assert_zero_horizontal_overflow(
        page,
        [".permit-watch-modal-body", "#permit-watch-modal-list"],
        "Permit Watch Modal 1440x900 pinned",
    )

    page.keyboard.press("Escape")
    assert first_thumb.evaluate("el => el.classList.contains('is-pinned')") is False
    assert pw_modal.is_visible(), "Escape on pinned spatial popover must not close #permit-watch-modal"

    # Switch to Demolished / Lost tab and verify Kirby Mansion calm slate card + dashed footprint
    page.locator('#permit-watch-modal [data-pw-filter="demolished"]').click()
    page.wait_for_function(
        "() => (document.getElementById('permit-watch-modal-list')?.textContent || '').includes('2000 SMITH ST')",
        timeout=10000,
    )
    demo_card = page.locator("#permit-watch-modal-list .permit-watch-item-card.demolished").first
    demo_card.wait_for(state="visible", timeout=5000)
    assert demo_card.locator('svg.atlas-spatial-overlay-svg path[stroke-dasharray="4,2"]').count() == 1
    _assert_zero_forbidden_dashes(demo_card.inner_text(), "#permit-watch-modal Demolished tab")

    # Surface 1 at 390x844 mobile portrait
    page.locator('#permit-watch-modal [data-pw-filter="all"]').click()
    page.set_viewport_size(MOBILE_VIEWPORT)
    mob_first_thumb = page.locator("#permit-watch-modal-list .permit-watch-item-card").first.locator(
        '[data-spatial-thumb="1"]'
    )
    mob_first_thumb.locator('[data-spatial-pip-toggle="1"]').click()
    mob_pop = mob_first_thumb.locator(".atlas-spatial-pip-popover")
    mob_pop.wait_for(state="visible", timeout=5000)
    assert mob_first_thumb.evaluate("el => el.classList.contains('stack-below')") is True
    mob_box = mob_pop.bounding_box()
    assert mob_box is not None and mob_box["x"] >= -1 and mob_box["x"] + mob_box["width"] <= 391
    _assert_zero_horizontal_overflow(
        page,
        [".permit-watch-modal-body", "#permit-watch-modal-list"],
        "Permit Watch Modal 390x844 pinned",
    )

    assert not page_errors, f"Uncaught JS pageerror(s): {page_errors}"
    assert not local_http_errors, f"Local HTTP >= 400 failure(s): {local_http_errors}"
    assert not console_errors, f"Console error(s): {console_errors}"


def test_e2e_plat_monitor_and_watchlist_spatial_thumbnails_desktop_and_mobile(
    page: Any,
    atlas_base_url: str,
    url_harvester: ExternalUrlHarvester,
) -> None:
    """Verify Surfaces 3 & 4 (plat-monitor.html PlatTracker GIS box and Watchlist dossier) at 1440x900 and 390x844."""
    configure_fast_e2e_context(page.context, atlas_base_url)
    page.set_viewport_size(DEFAULT_E2E_VIEWPORT)
    page_errors, console_errors, local_http_errors = attach_error_monitors(page, atlas_base_url)

    # --- Surface 3: plat-monitor.html?mode=agenda&cycle=2026-12-03&app=2026-1883 ---
    page.goto(
        f"{atlas_base_url}/plat-monitor.html?mode=agenda&cycle=2026-12-03&app=2026-1883",
        wait_until="domcontentloaded",
    )
    gis_box = page.locator('[data-plattracker-gis-box="1"]')
    gis_box.wait_for(state="visible", timeout=20000)
    gis_thumb = gis_box.locator('[data-spatial-thumb="1"]')
    gis_thumb.wait_for(state="visible", timeout=10000)

    gis_text, _ = url_harvester.harvest_text_and_urls(gis_box, "sct:plat-monitor-gis-box")
    _assert_zero_forbidden_dashes(gis_text, "plat-monitor GIS box")
    assert "Where:" in gis_text
    assert gis_thumb.locator("svg.atlas-spatial-overlay-svg path").count() == 1

    gis_toggle = gis_thumb.locator('[data-spatial-pip-toggle="1"]')
    page.wait_for_function(
        """() => Boolean(
            document.querySelector('[data-plattracker-gis-box="1"] .atlas-spatial-pip-popover polygon[stroke="#c43d2f"]')
        )""",
        timeout=15000,
    )
    gis_toggle.click()
    gis_pop = gis_thumb.locator(".atlas-spatial-pip-popover")
    gis_pop.wait_for(state="visible", timeout=5000)
    assert "610 LOOP" in (gis_pop.inner_html() or "")
    assert gis_pop.locator('polygon[stroke="#c43d2f"]').count() >= 1
    _assert_zero_horizontal_overflow(page, ["#detail", '[data-plattracker-gis-box="1"]'], "Plat Monitor 1440x900")

    # Mobile 390x844 check on Surface 3
    page.set_viewport_size(MOBILE_VIEWPORT)
    gis_toggle.click()  # unpin
    gis_toggle.click()  # re-pin with mobile viewport dimensions
    gis_pop.wait_for(state="visible", timeout=5000)
    assert gis_thumb.evaluate("el => el.classList.contains('stack-below')") is True
    _assert_zero_horizontal_overflow(page, ["#detail"], "Plat Monitor 390x844")

    # --- Surface 4: plat-monitor.html?tab=watchlist&hcad=0231000000006 ---
    page.set_viewport_size(DEFAULT_E2E_VIEWPORT)
    page.goto(
        f"{atlas_base_url}/plat-monitor.html?tab=watchlist&hcad=0231000000006",
        wait_until="domcontentloaded",
    )
    wl_thumb = page.locator('#detail [data-spatial-thumb="1"]').first
    wl_thumb.wait_for(state="visible", timeout=20000)
    page.wait_for_function(
        """() => Boolean(
            document.querySelector('#detail [data-spatial-thumb="1"] svg.atlas-spatial-overlay-svg path') &&
            document.querySelector('#detail [data-spatial-thumb="1"] .atlas-spatial-pip-popover polygon[stroke="#c43d2f"]')
        )""",
        timeout=15000,
    )
    wl_thumb = page.locator('#detail [data-spatial-thumb="1"]').first
    _assert_zero_forbidden_dashes(page.locator("#detail").inner_text()[:3000], "Watchlist detail")

    wl_toggle = wl_thumb.locator('[data-spatial-pip-toggle="1"]')
    wl_toggle.click()
    wl_pop = wl_thumb.locator(".atlas-spatial-pip-popover")
    wl_pop.wait_for(state="visible", timeout=5000)
    assert wl_pop.locator('polygon[stroke="#c43d2f"]').count() >= 1
    _assert_zero_horizontal_overflow(page, ["#detail"], "Watchlist 1440x900")

    # Outside click closes pinned popover
    page.locator("#detail h2").first.click()
    assert wl_thumb.evaluate("el => el.classList.contains('is-pinned')") is False

    # Mobile 390x844 check on Surface 4
    page.set_viewport_size(MOBILE_VIEWPORT)
    wl_toggle.click()
    wl_pop.wait_for(state="visible", timeout=5000)
    assert wl_thumb.evaluate("el => el.classList.contains('stack-below')") is True
    wl_box = wl_pop.bounding_box()
    assert wl_box is not None and wl_box["x"] >= -1 and wl_box["x"] + wl_box["width"] <= 391
    _assert_zero_horizontal_overflow(page, ["#detail"], "Watchlist 390x844")

    assert not page_errors, f"Uncaught JS pageerror(s): {page_errors}"
    assert not local_http_errors, f"Local HTTP >= 400 failure(s): {local_http_errors}"
    assert not console_errors, f"Console error(s): {console_errors}"
