"""Playwright E2E tests for Milestone 12 (COH HPO 23 Historic District Survey Inventories
and Post-2016 Landmark Designation Report Backfill).
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


def test_m12_e2e_hpo_district_surveys_boundary_dossier_and_landmark_backfill(
    page: Any,
    atlas_base_url: str,
    url_harvester: ExternalUrlHarvester,
) -> None:
    """Verify #inspector-hpo-survey-card (+ 390x844 mobile layout), #boundary-hpo-district-docs-card, and .coh-landmark-report-card."""
    configure_fast_e2e_context(page.context, atlas_base_url)
    page.set_viewport_size(DEFAULT_E2E_VIEWPORT)
    page_errors, console_errors, local_http_errors = attach_error_monitors(page, atlas_base_url)

    # 1. Surveyed COH Historic District parcel (#hcad=0620950000004 -> 1016 ALGREGG ST, Norhill Historic District)
    page.goto(f"{atlas_base_url}/#hcad=0620950000004", wait_until="domcontentloaded")
    wait_for_atlas_ready(page)
    page.wait_for_selector('#inspector-body [data-inspector-tab="history"]', timeout=15000)
    page.click('#inspector-body [data-inspector-tab="history"]')

    survey_card = page.locator("#inspector-hpo-survey-card")
    survey_card.wait_for(state="visible", timeout=10000)

    survey_text, survey_urls = url_harvester.harvest_text_and_urls(
        survey_card, "m12:inspector-hpo-survey-card"
    )
    _assert_zero_forbidden_dashes(survey_text, "#inspector-hpo-survey-card")
    assert "Norhill Historic District" in survey_text
    assert "Contributing" in survey_text
    assert "1016 ALGREGG ST" in survey_text
    assert "1925" in survey_text
    assert "Bungalow" in survey_text
    assert "LT 4 BLK 116 NORTH NORHILL" in survey_text
    assert "Garage" in survey_text
    assert page.locator("#inspector-hpo-survey-card .hpo-survey-status-pill.status-contributing").count() == 1
    assert any("Norhill_Inventory.pdf" in u for u in survey_urls), f"Missing Norhill inventory PDF in {survey_urls}"
    assert any("Norhill-Design-Guidelines" in u for u in survey_urls), f"Missing Norhill guidelines PDF in {survey_urls}"

    # Verify zero horizontal overflow at 390x844 mobile portrait
    page.set_viewport_size({"width": 390, "height": 844})
    mobile_metrics = page.evaluate(
        """() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => {
            const docEl = document.documentElement;
            const card = document.getElementById('inspector-hpo-survey-card');
            const rect = card ? card.getBoundingClientRect() : null;
            resolve({
                docScrollWidth: docEl.scrollWidth,
                winInnerWidth: window.innerWidth,
                cardScrollWidth: card ? card.scrollWidth : 0,
                cardClientWidth: card ? card.clientWidth : 0,
                cardRight: rect ? rect.right : 0,
                cardLeft: rect ? rect.left : 0,
            });
        })))"""
    )
    assert mobile_metrics["docScrollWidth"] <= mobile_metrics["winInnerWidth"], (
        f"Mobile 390x844 viewport horizontal overflow: {mobile_metrics}"
    )
    assert mobile_metrics["cardScrollWidth"] <= mobile_metrics["cardClientWidth"] + 1, (
        f"#inspector-hpo-survey-card internal horizontal overflow at 390x844: {mobile_metrics}"
    )
    assert mobile_metrics["cardRight"] <= 390.5 and mobile_metrics["cardLeft"] >= -0.5, (
        f"#inspector-hpo-survey-card bounds exceed 390px viewport: {mobile_metrics}"
    )

    # Restore desktop viewport
    page.set_viewport_size(DEFAULT_E2E_VIEWPORT)

    # 2. Navigate to #bnd=historic_districts:Norhill Historic District -> renders #boundary-hpo-district-docs-card
    page.evaluate(
        """() => {
            window.location.hash = 'bnd=historic_districts:Norhill Historic District&lat=29.7935&lng=-95.3887&z=15.0';
        }"""
    )
    bnd_card = page.locator("#boundary-hpo-district-docs-card")
    bnd_card.wait_for(state="visible", timeout=10000)

    bnd_text, bnd_urls = url_harvester.harvest_text_and_urls(
        bnd_card, "m12:boundary-hpo-district-docs-card"
    )
    _assert_zero_forbidden_dashes(bnd_text, "#boundary-hpo-district-docs-card")
    assert "Contributing" in bnd_text
    assert "District Inventory (PDF)" in bnd_text
    assert "Design Guidelines (PDF)" in bnd_text
    assert "Boundary / Lot Map (PDF)" in bnd_text
    assert any("Norhill_Inventory.pdf" in u for u in bnd_urls), f"Missing Inventory PDF in {bnd_urls}"
    assert any("Norhill-Design-Guidelines" in u for u in bnd_urls), f"Missing Design Guidelines PDF in {bnd_urls}"
    assert any("pj17025_Norhill.pdf" in u for u in bnd_urls), f"Missing District Map PDF in {bnd_urls}"

    # 3A. Select newly backfilled post-2016 COH Protected Landmark (0513790000023 -> 911 Woodland St, HPO #20PL182)
    page.evaluate(
        """() => {
            window.atlasApp.renderInspectorDrawer({
                id: '0513790000023',
                hcad_num: '0513790000023',
                address: '911 WOODLAND ST',
                year_built: 1911,
                use_category: 'Residential',
                neighborhood: 'Woodland Heights',
            });
        }"""
    )
    page.click('#inspector-body [data-inspector-tab="history"]')

    lm_card = page.locator("#inspector-body .coh-landmark-report-card").filter(
        has_text="COH Landmark Designation Dossier"
    )
    lm_card.wait_for(state="visible", timeout=10000)
    lm_text, lm_urls = url_harvester.harvest_text_and_urls(
        lm_card, "m12:post2016-landmark-card"
    )
    _assert_zero_forbidden_dashes(lm_text, ".coh-landmark-report-card (20PL182)")
    assert "HPO #20PL182" in lm_text
    assert "Open Official Landmark Report (PDF)" in lm_text
    assert any("C_911%20Woodland%20Street%20Protected%20Landmark_FINAL.pdf" in u for u in lm_urls), (
        f"Missing 911 Woodland St PDF in {lm_urls}"
    )
    assert page.locator("#inspector-body .coh-landmark-tpia-note").count() == 0

    # 3B. Inspect unpublished COH Landmark with NRHP secondary_pdf_url + .coh-landmark-tpia-note (0523230000010 -> HPO #17PL160)
    page.evaluate(
        """() => {
            window.atlasApp.renderInspectorDrawer({
                id: '0523230000010',
                hcad_num: '0523230000010',
                address: '9 SHADOW LAWN ST',
                year_built: 1961,
                use_category: 'Residential',
                neighborhood: 'Museum Park',
            });
        }"""
    )
    page.click('#inspector-body [data-inspector-tab="history"]')

    tpia_card = page.locator("#inspector-body .coh-landmark-report-card").filter(
        has_text="COH Landmark Designation Dossier"
    )
    tpia_card.wait_for(state="visible", timeout=10000)
    tpia_text, tpia_urls = url_harvester.harvest_text_and_urls(
        tpia_card, "m12:tpia-nrhp-fallback-landmark-card"
    )
    _assert_zero_forbidden_dashes(tpia_text, ".coh-landmark-report-card (17PL160)")
    assert "HPO #17PL160" in tpia_text
    assert "Open National Register Nomination (PDF)" in tpia_text
    assert any("atlas.thc.texas.gov/NR/pdfs/100002842/100002842.pdf" in u for u in tpia_urls)
    tpia_note = page.locator("#inspector-body .coh-landmark-tpia-note")
    assert tpia_note.count() == 1
    assert "queued for public records release" in tpia_note.inner_text()

    # 3C. Inspect pure pending-TPIA COH Landmark without secondary_pdf_url (0601520320007 -> HPO #08L211)
    page.evaluate(
        """() => {
            window.atlasApp.renderInspectorDrawer({
                id: '0601520320007',
                hcad_num: '0601520320007',
                address: '2923 INWOOD DR',
                year_built: 1933,
                use_category: 'Residential',
                neighborhood: 'River Oaks',
            });
        }"""
    )
    page.click('#inspector-body [data-inspector-tab="history"]')

    pure_tpia_card = page.locator("#inspector-body .coh-landmark-report-card").filter(
        has_text="COH Landmark Designation Dossier"
    )
    pure_tpia_card.wait_for(state="visible", timeout=10000)
    pure_tpia_text, pure_tpia_urls = url_harvester.harvest_text_and_urls(
        pure_tpia_card, "m12:pure-tpia-landmark-card"
    )
    _assert_zero_forbidden_dashes(pure_tpia_text, ".coh-landmark-report-card (08L211)")
    assert "HPO #08L211" in pure_tpia_text
    assert pure_tpia_urls == []
    assert page.locator("#inspector-body .coh-landmark-tpia-note").count() == 1

    # 3D. Inspect 1968 Modernist Landmark with year_built=0 in overlays.json (0522660000006 -> HPO #19PL172)
    page.evaluate(
        """() => {
            window.atlasApp.renderInspectorDrawer({
                id: '0522660000006',
                hcad_num: '0522660000006',
                address: '1723 COLQUITT ST',
                year_built: 1968,
                use_category: 'Residential',
                neighborhood: 'Montrose',
            });
        }"""
    )
    page.click('#inspector-body [data-inspector-tab="history"]')
    mod_lm_card = page.locator("#inspector-body .coh-landmark-report-card").filter(
        has_text="COH Landmark Designation Dossier"
    )
    mod_lm_card.wait_for(state="visible", timeout=10000)
    mod_lm_text, _ = url_harvester.harvest_text_and_urls(
        mod_lm_card, "m12:modernist-tpia-landmark-card"
    )
    _assert_zero_forbidden_dashes(mod_lm_text, ".coh-landmark-report-card (19PL172)")
    assert "HPO #19PL172" in mod_lm_text
    assert page.locator("#inspector-body .coh-landmark-tpia-note").count() == 1

    # 3E. Inspect dual-PDF Landmark (0011820000001 -> Cheek-Neal Coffee Company Building) and verify #inspector-hist-contract-card hydration
    page.evaluate(
        """() => {
            window.atlasApp.renderInspectorDrawer({
                id: '0011820000001',
                hcad_num: '0011820000001',
                address: '2017 PRESTON ST',
                building_name: 'Cheek-Neal Coffee Company Building',
                year_built: 1917,
                use_category: 'Commercial',
                landmark_report_url: 'https://www.houstontx.gov/planning/HistoricPres/landmarks/15PL126_2017_Preston_Cheek-Neal_Bldg.pdf',
                secondary_pdf_url: 'https://www.houstontx.gov/planning/HistoricPres/landmarks/05L121_Cheek-Neal_Coffee_2017_Preston.pdf',
            });
        }"""
    )
    page.click('#inspector-body [data-inspector-tab="history"]')
    contract_card = page.locator("#inspector-hist-contract-card")
    contract_card.wait_for(state="visible", timeout=10000)
    contract_text, _ = url_harvester.harvest_text_and_urls(
        contract_card, "m12:cheek-neal-contract-card"
    )
    _assert_zero_forbidden_dashes(contract_text, "#inspector-hist-contract-card (0011820000001)")
    assert "General Contracting" in contract_text
    assert "$100,000" in contract_text

    assert not page_errors, f"Uncaught JS pageerror(s): {page_errors}"
    assert not local_http_errors, f"Local HTTP >= 400 asset failure(s): {local_http_errors}"
    assert not console_errors, f"Console error(s): {console_errors}"
