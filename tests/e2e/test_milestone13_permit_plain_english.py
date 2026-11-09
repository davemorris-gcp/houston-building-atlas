"""Playwright E2E tests for Milestone 13 (Plain-English HCAD Appraiser Note &
Municipal Permit Shorthand Decoder + Official HCAD Permit Type Category Shard Repair).
"""

from __future__ import annotations

from typing import Any

from conftest import (
    DEFAULT_E2E_VIEWPORT,
    ExternalUrlHarvester,
    attach_error_monitors,
    configure_fast_e2e_context,
    wait_for_atlas_ready,
)

EM_DASH = chr(0x2014)
EN_DASH = chr(0x2013)
UNI_HYPHEN = chr(0x2010)


def _assert_zero_forbidden_dashes(text: str, context_label: str) -> None:
    assert EM_DASH not in text, f"{context_label} rendered forbidden U+2014 em-dash"
    assert EN_DASH not in text, f"{context_label} rendered forbidden U+2013 en-dash"
    assert UNI_HYPHEN not in text, f"{context_label} rendered forbidden U+2010 hyphen"


def test_m13_e2e_permit_plain_english_decoder_and_repaired_badges_desktop_and_mobile(
    page: Any,
    atlas_base_url: str,
    url_harvester: ExternalUrlHarvester,
) -> None:
    """Verify #hcad=0620950000004&tab=permits at 1440x900 desktop and 390x844 mobile portrait."""
    configure_fast_e2e_context(page.context, atlas_base_url)
    page.set_viewport_size(DEFAULT_E2E_VIEWPORT)
    page_errors, console_errors, local_http_errors = attach_error_monitors(page, atlas_base_url)

    # 1. Deep-link directly to 1016 Algregg St (#hcad=0620950000004&tab=permits) at 1440x900
    page.goto(f"{atlas_base_url}/#hcad=0620950000004&tab=permits", wait_until="domcontentloaded")
    wait_for_atlas_ready(page)

    permits_tab_btn = page.locator('#inspector-body [data-inspector-tab="permits"]')
    permits_tab_btn.wait_for(state="visible", timeout=15000)
    assert permits_tab_btn.get_attribute("aria-selected") == "true"

    permits_card = page.locator("#inspector-permits-card")
    permits_card.wait_for(state="visible", timeout=15000)

    # Wait until permit shard hydration replaces the loading placeholder
    page.wait_for_function(
        """() => {
            const body = document.getElementById('inspector-permits-body');
            return Boolean(body && body.querySelector('.permit-timeline-list'));
        }""",
        timeout=15000,
    )

    card_text, _ = url_harvester.harvest_text_and_urls(
        permits_card, "m13:inspector-permits-card-0620950000004"
    )
    _assert_zero_forbidden_dashes(card_text, "#inspector-permits-card (collapsed)")

    # 2. Verify decoded Plain-English appraiser note bullets render first
    decoded_items = permits_card.locator(
        ".permit-appraiser-decoded-list .permit-appraiser-decoded-item"
    )
    assert decoded_items.count() == 2
    bullet_0 = decoded_items.nth(0).inner_text()
    bullet_1 = decoded_items.nth(1).inner_text()
    assert "Apr 1, 2026 (Permit check):" in bullet_0
    assert "Work does not appear to be done at time of inspection." in bullet_0
    assert "Nov 22, 2024 (Field review):" in bullet_1
    assert "Main dwelling well maintained, kept condition rating at Good." in bullet_1

    # 3. Verify <details class="permit-verbatim-details"> is collapsed by default and reveals raw text on click
    verbatim_details = permits_card.locator("details.permit-verbatim-details")
    verbatim_summary = verbatim_details.locator("summary.permit-verbatim-summary")
    verbatim_raw = verbatim_details.locator(".permit-verbatim-raw")
    assert verbatim_details.count() == 1
    assert verbatim_summary.inner_text().strip() == "Verbatim HCAD text"
    assert verbatim_details.evaluate("el => el.open") is False
    assert not verbatim_raw.is_visible()

    verbatim_summary.click()
    assert verbatim_details.evaluate("el => el.open") is True
    verbatim_raw.wait_for(state="visible", timeout=5000)
    raw_text = verbatim_raw.inner_text()
    _assert_zero_forbidden_dashes(raw_text, ".permit-verbatim-raw")
    assert "4/1/26 NC: WORK DOES NOT APPEAR TO BE DONE @ TOI" in raw_text
    assert "11/22/24 FR: DWG WELL MAINTAINED, KEPT CDU @ GD" in raw_text

    # Collapse again before testing mobile tap interaction
    verbatim_summary.click()
    assert verbatim_details.evaluate("el => el.open") is False

    # 4. Verify repaired HCAD permit category badges (#2037787 & #1138473) and .permit-plain-english-sub lines
    remodel_badge = permits_card.locator(".permit-cat-badge.cat-remodel")
    repair_badge = permits_card.locator(".permit-cat-badge.cat-repair")
    assert remodel_badge.count() == 1
    assert (remodel_badge.text_content() or "").strip() == "Additions or Remodeling"
    assert repair_badge.count() == 1
    assert (repair_badge.text_content() or "").strip() == "Repairs (Foundation / General Maintenance)"

    assert "#2037787" in card_text
    assert "ONE-STOP REMODEL, SINGLE FAMILY RESIDENTIAL" in card_text
    assert "#1138473" in card_text
    assert "RESIDENTIAL REPAIRS AS PER SPEC LIST" in card_text

    sub_lines = permits_card.locator(".permit-plain-english-sub")
    assert sub_lines.count() == 2
    sub_texts = [sub_lines.nth(i).inner_text().strip() for i in range(sub_lines.count())]
    for st in sub_texts:
        _assert_zero_forbidden_dashes(st, ".permit-plain-english-sub")
    assert any("Fast-track City of Houston residential remodel permit" in s for s in sub_texts), (
        f"Missing #2037787 plain-English sub-line in {sub_texts}"
    )
    assert any("repair specification checklist" in s for s in sub_texts), (
        f"Missing #1138473 plain-English sub-line in {sub_texts}"
    )

    # 4b. Verify &tab=permits survives map moveend URL sync + Share URL generation, interactive tab clicks sync URL, and hashchange without &tab= resets to Overview
    settled_hash = page.evaluate("() => window.location.hash")
    assert "hcad=0620950000004" in settled_hash
    assert "tab=permits" in settled_hash

    share_input_val = page.evaluate(
        "() => document.getElementById('share-url-input')?.value || ''"
    )
    assert "hcad=0620950000004" in share_input_val
    assert "tab=permits" in share_input_val

    history_tab_btn = page.locator('#inspector-body [data-inspector-tab="history"]')
    history_tab_btn.click()
    assert history_tab_btn.get_attribute("aria-selected") == "true"
    assert "tab=history" in page.evaluate("() => window.location.hash")

    page.evaluate("() => { window.location.hash = '#hcad=0620950000004'; }")
    overview_tab_btn = page.locator('#inspector-body [data-inspector-tab="overview"]')
    page.wait_for_function(
        """() => {
            const btn = document.querySelector('#inspector-body [data-inspector-tab="overview"]');
            return Boolean(btn && btn.getAttribute('aria-selected') === 'true');
        }""",
        timeout=5000,
    )
    assert overview_tab_btn.get_attribute("aria-selected") == "true"
    assert "tab=" not in page.evaluate("() => window.location.hash")

    page.evaluate("() => { window.location.hash = '#hcad=0620950000004&tab=permits'; }")
    page.wait_for_function(
        """() => {
            const btn = document.querySelector('#inspector-body [data-inspector-tab="permits"]');
            const body = document.getElementById('inspector-permits-body');
            return Boolean(
                btn && btn.getAttribute('aria-selected') === 'true'
                && body && body.querySelector('.permit-timeline-list')
                && window.location.hash.includes('tab=permits')
            );
        }""",
        timeout=15000,
    )

    # 5. Switch to mobile portrait (390x844): collapse desktop sidebar, verify >= 32px touch target height and zero horizontal overflow
    page.set_viewport_size({"width": 390, "height": 844})
    page.evaluate("() => window.atlasApp._setSidebarCollapsed(true)")
    verbatim_summary.scroll_into_view_if_needed()

    mobile_collapsed_metrics = page.evaluate(
        """() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => {
            const docEl = document.documentElement;
            const card = document.getElementById('inspector-permits-card');
            const summary = card ? card.querySelector('.permit-verbatim-summary') : null;
            const cardRect = card ? card.getBoundingClientRect() : null;
            const sumRect = summary ? summary.getBoundingClientRect() : null;
            resolve({
                docScrollWidth: docEl.scrollWidth,
                winInnerWidth: window.innerWidth,
                cardScrollWidth: card ? card.scrollWidth : 0,
                cardClientWidth: card ? card.clientWidth : 0,
                cardLeft: cardRect ? cardRect.left : 0,
                cardRight: cardRect ? cardRect.right : 0,
                summaryHeight: sumRect ? sumRect.height : 0,
                summaryWidth: sumRect ? sumRect.width : 0,
            });
        })))"""
    )
    assert mobile_collapsed_metrics["summaryHeight"] >= 32.0, (
        f".permit-verbatim-summary touch target height < 32px at 390x844: {mobile_collapsed_metrics}"
    )
    assert mobile_collapsed_metrics["docScrollWidth"] <= mobile_collapsed_metrics["winInnerWidth"], (
        f"Mobile 390x844 document horizontal overflow (collapsed): {mobile_collapsed_metrics}"
    )
    assert (
        mobile_collapsed_metrics["cardScrollWidth"]
        <= mobile_collapsed_metrics["cardClientWidth"] + 1
    ), f"#inspector-permits-card horizontal overflow at 390x844 (collapsed): {mobile_collapsed_metrics}"
    assert (
        mobile_collapsed_metrics["cardRight"] <= 390.5
        and mobile_collapsed_metrics["cardLeft"] >= -0.5
    ), f"#inspector-permits-card exceeds 390px viewport bounds: {mobile_collapsed_metrics}"

    # Tap .permit-verbatim-summary at 390x844 and verify expanded raw HCAD text also causes zero overflow
    verbatim_summary.click()
    assert verbatim_details.evaluate("el => el.open") is True
    verbatim_raw.wait_for(state="visible", timeout=5000)

    mobile_expanded_metrics = page.evaluate(
        """() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => {
            const docEl = document.documentElement;
            const card = document.getElementById('inspector-permits-card');
            resolve({
                docScrollWidth: docEl.scrollWidth,
                winInnerWidth: window.innerWidth,
                cardScrollWidth: card ? card.scrollWidth : 0,
                cardClientWidth: card ? card.clientWidth : 0,
            });
        })))"""
    )
    assert mobile_expanded_metrics["docScrollWidth"] <= mobile_expanded_metrics["winInnerWidth"], (
        f"Mobile 390x844 document horizontal overflow (expanded): {mobile_expanded_metrics}"
    )
    assert (
        mobile_expanded_metrics["cardScrollWidth"]
        <= mobile_expanded_metrics["cardClientWidth"] + 1
    ), f"#inspector-permits-card horizontal overflow at 390x844 (expanded): {mobile_expanded_metrics}"

    assert not page_errors, f"Uncaught JS pageerror(s): {page_errors}"
    assert not local_http_errors, f"Local HTTP >= 400 asset failure(s): {local_http_errors}"
    assert not console_errors, f"Console error(s): {console_errors}"
