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


def test_milestone14_early_warning_permit_and_watchlist_plain_english(
    page: Any, atlas_base_url: str
) -> None:
    """Verify Milestone 14 Early-Warning Permit Watchlist & Plat/Property Monitor Plain-English DOM rendering."""
    configure_fast_e2e_context(page.context, atlas_base_url)
    page_errors, console_errors, local_http_errors = attach_error_monitors(page, atlas_base_url)

    # 1. Open main map at flagged property 2000 Smith St (John Henry Kirby Mansion, HCAD 0152300000001)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(
        f"{atlas_base_url}/#hcad=0152300000001",
        wait_until="domcontentloaded",
    )

    page.wait_for_function(
        """() => {
            const box = document.querySelector('#inspector-body .inspector-permit-filing-box');
            const plain = box ? box.querySelector('.inspector-permit-filing-plain') : null;
            return Boolean(plain && plain.textContent.trim().length > 20);
        }""",
        timeout=25000,
    )

    inspector_plain_notes = page.locator(
        "#inspector-body .inspector-permit-filing-box .inspector-permit-filing-plain"
    ).all_inner_texts()
    assert len(inspector_plain_notes) >= 1, (
        "Expected .inspector-permit-filing-plain inside .inspector-permit-filing-box for 2000 Smith St"
    )
    assert any(
        "Texas DSHS" in t or "asbestos" in t.lower() or "TDLR" in t or "311" in t
        for t in inspector_plain_notes
    ), f"Unexpected inspector plain notes for 2000 Smith St: {inspector_plain_notes}"
    for note in inspector_plain_notes:
        assert "\u2010" not in note and "\u2013" not in note and "\u2014" not in note

    # 2. Open the Early-Warning Permit Watch Modal and verify .pw-item-filing-plain inside .pw-item-filing
    page.evaluate("() => window.atlasApp.openPermitWatchModal('all')")
    page.wait_for_function(
        """() => {
            const modal = document.getElementById('permit-watch-modal');
            const plain = modal ? modal.querySelector('.pw-item-filing .pw-item-filing-plain') : null;
            return Boolean(
                modal && !modal.classList.contains('hidden')
                && plain && plain.textContent.trim().length > 20
            );
        }""",
        timeout=15000,
    )

    modal_plain_notes = page.locator(
        "#permit-watch-modal .pw-item-filing .pw-item-filing-plain"
    ).all_inner_texts()
    assert len(modal_plain_notes) >= 10, (
        f"Expected at least 10 .pw-item-filing-plain notes in Permit Watch Modal, got {len(modal_plain_notes)}"
    )
    assert any("Stage 1 pre-demolition plumbing permit" in t for t in modal_plain_notes)
    assert any("Texas DSHS" in t or "asbestos" in t.lower() for t in modal_plain_notes)
    for note in modal_plain_notes:
        assert "\u2010" not in note and "\u2013" not in note and "\u2014" not in note

    # Verify mobile portrait (390x844) wrapping and zero horizontal overflow inside #permit-watch-modal
    page.set_viewport_size({"width": 390, "height": 844})
    pw_mobile_metrics = page.evaluate(
        """() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => {
            const docEl = document.documentElement;
            const modalList = document.getElementById('permit-watch-modal-list');
            resolve({
                docScrollWidth: docEl.scrollWidth,
                winInnerWidth: window.innerWidth,
                listScrollWidth: modalList ? modalList.scrollWidth : 0,
                listClientWidth: modalList ? modalList.clientWidth : 0,
            });
        })))"""
    )
    assert pw_mobile_metrics["docScrollWidth"] <= pw_mobile_metrics["winInnerWidth"], (
        f"Mobile 390x844 document horizontal overflow with Permit Watch Modal open: {pw_mobile_metrics}"
    )
    assert pw_mobile_metrics["listScrollWidth"] <= pw_mobile_metrics["listClientWidth"] + 1, (
        f"#permit-watch-modal-list horizontal overflow at 390x844: {pw_mobile_metrics}"
    )
    page.set_viewport_size({"width": 1440, "height": 900})

    # 3. Navigate to plat-monitor.html in Activity ("What's New & Daily Log") mode with All History
    page.goto(
        f"{atlas_base_url}/plat-monitor.html?mode=activity",
        wait_until="domcontentloaded",
    )
    page.wait_for_selector('#activityRangeTabs .tab[data-act-range="all"]', state="visible", timeout=20000)
    page.click('#activityRangeTabs .tab[data-act-range="all"]')
    page.wait_for_function(
        """() => {
            const cards = document.querySelectorAll('#detail .act-card');
            const plainNotes = document.querySelectorAll('#detail .act-card .act-card-plain-note');
            return cards.length >= 10 && plainNotes.length >= 5;
        }""",
        timeout=20000,
    )

    act_plain_notes = page.locator("#detail .act-card .act-card-plain-note").all_inner_texts()
    assert len(act_plain_notes) >= 5
    for note in act_plain_notes:
        assert "\u2010" not in note and "\u2013" not in note and "\u2014" not in note

    # Verify degraded sweep cards do NOT render .act-card-plain-note
    degraded_plain_count = page.evaluate(
        """() => {
            const cards = Array.from(document.querySelectorAll('#detail .act-card'));
            const degradedCards = cards.filter(c =>
                (c.getAttribute('data-event-id') || '').includes('degraded') ||
                (c.querySelector('.act-card-title')?.textContent || '').includes('Sweep Degraded')
            );
            return degradedCards.reduce(
                (acc, c) => acc + c.querySelectorAll('.act-card-plain-note').length,
                0
            );
        }"""
    )
    assert degraded_plain_count == 0, (
        f"Expected 0 .act-card-plain-note on degraded sweep cards, got {degraded_plain_count}"
    )

    # 4. Switch to Property Watchlist mode for John Henry Kirby Mansion (prop-kirby-mansion)
    page.goto(
        f"{atlas_base_url}/plat-monitor.html?mode=watchlist&watch=prop-kirby-mansion",
        wait_until="domcontentloaded",
    )
    page.wait_for_function(
        """() => {
            const detail = document.getElementById('detail');
            const notes = detail ? detail.querySelectorAll('.wl-check-plain-note') : [];
            return notes.length >= 4;
        }""",
        timeout=20000,
    )

    watchlist_plain_info = page.evaluate(
        """() => {
            const detail = document.getElementById('detail');
            const checklistLiNotes = Array.from(
                detail.querySelectorAll('ul.chk-list li .wl-check-plain-note')
            ).map(el => el.textContent.trim());
            const findingsLogNotes = Array.from(
                detail.querySelectorAll('table.wl-table tbody tr td .wl-check-plain-note')
            ).filter(el => !el.closest('ul.chk-list')).map(el => el.textContent.trim());
            const quietLiWithNote = Array.from(detail.querySelectorAll('ul.chk-list li')).filter(li => {
                const txt = li.textContent || '';
                const isQuiet = /\\b(?:no results|no demo permit|no new filings found)\\b/i.test(txt);
                return isQuiet && Boolean(li.querySelector('.wl-check-plain-note'));
            }).length;
            return {
                checklistLiNotes,
                findingsLogNotes,
                quietLiWithNote,
            };
        }"""
    )
    assert len(watchlist_plain_info["checklistLiNotes"]) >= 2, (
        f"Expected .wl-check-plain-note in 15-Point Checklist: {watchlist_plain_info}"
    )
    assert len(watchlist_plain_info["findingsLogNotes"]) >= 2, (
        f"Expected .wl-check-plain-note in Chronological Findings Log: {watchlist_plain_info}"
    )
    assert watchlist_plain_info["quietLiWithNote"] == 0, (
        f"Quiet check rows must not render .wl-check-plain-note: {watchlist_plain_info}"
    )

    assert not page_errors, f"Uncaught JS pageerror(s): {page_errors}"
    assert not local_http_errors, f"Local HTTP >= 400 asset failure(s): {local_http_errors}"
    assert not console_errors, f"Console error(s): {console_errors}"

