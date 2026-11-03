"""Playwright E2E tests for Milestone 5 ("Who Owns Your City?" Ownership Unmasking &
1836-2026 Historical Chain of Stewardship) covering Quality Plan scenarios IT-1..IT-4.
"""

from __future__ import annotations

from typing import Any

from conftest import attach_error_monitors, configure_fast_e2e_context, wait_for_atlas_ready


def test_it1_ownership_tier_color_mode_legend_and_url_roundtrip(
    page: Any,
    atlas_base_url: str,
) -> None:
    """IT-1: Verify #color=ownership_tier map color mode, 6-tier interactive legend, and WebGL/Canvas parity."""
    configure_fast_e2e_context(page.context, atlas_base_url)
    page_errors, _console_errors, local_http_errors = attach_error_monitors(page, atlas_base_url)

    page.goto(f"{atlas_base_url}/#color=ownership_tier", wait_until="domcontentloaded")
    wait_for_atlas_ready(page)

    state = page.evaluate("() => window.atlasApp.filterStore.getState()")
    assert state["colorMode"] == "ownership_tier"

    active_btn = page.locator('#tab-color-ownership.active[data-color-mode="ownership_tier"]')
    assert active_btn.count() == 1

    legend_tiers = page.evaluate(
        """() => Array.from(document.querySelectorAll('#legend-items [data-own-tier-filter]'))
            .map((el) => ({
                tier: el.getAttribute('data-own-tier-filter'),
                text: (el.textContent || '').trim(),
            }))"""
    )
    assert [row["tier"] for row in legend_tiers] == [
        "homestead",
        "local_small",
        "regional_portfolio",
        "institutional_pe",
        "anonymity_shield",
        "civic_nonprofit",
    ]

    # Click Regional Portfolio (5-99) row in the interactive legend
    page.click('#legend-items [data-own-tier-filter="regional_portfolio"]')
    after_click = page.evaluate(
        """() => ({
            ownTier: window.atlasApp.filterStore.getState().ownTier,
            hash: window.location.hash,
            activeRows: Array.from(document.querySelectorAll('#legend-items .legend-row.active-tier'))
                .map((el) => el.getAttribute('data-own-tier-filter')),
        })"""
    )
    assert after_click["ownTier"] == "regional_portfolio"
    assert "ownTier=regional_portfolio" in after_click["hash"]
    assert after_click["activeRows"] == ["regional_portfolio"]

    # Click again to clear tier filter back to "all"
    page.click('#legend-items [data-own-tier-filter="regional_portfolio"]')
    assert page.evaluate("() => window.atlasApp.filterStore.getState().ownTier") == "all"

    # Verify 1:1 color parity across evaluateFeatureColor for all 6 tiers
    colors_by_tier = page.evaluate(
        """() => {
            const mc = window.atlasApp.mapController;
            const st = { ...window.atlasApp.filterStore.getState(), colorMode: 'ownership_tier' };
            const tiers = [
                'homestead',
                'local_small',
                'regional_portfolio',
                'institutional_pe',
                'anonymity_shield',
                'civic_nonprofit',
            ];
            const out = {};
            for (const t of tiers) {
                out[t] = mc.evaluateFeatureColor({ year_built: 1925, own_tier: t }, st);
            }
            return out;
        }"""
    )
    assert colors_by_tier == {
        "homestead": "#38BDF8",
        "local_small": "#34D399",
        "regional_portfolio": "#F59E0B",
        "institutional_pe": "#E11D48",
        "anonymity_shield": "#A855F7",
        "civic_nonprofit": "#94A3B8",
    }
    assert page_errors == []
    assert local_http_errors == []


def test_it2_inspector_ownership_xray_card_and_portfolio_explorer_pin_mode(
    page: Any,
    atlas_base_url: str,
) -> None:
    """IT-2: Verify Inspector Ownership X-Ray card and 1-click Map All Portfolio Parcels on 1017 Rutland St."""
    configure_fast_e2e_context(page.context, atlas_base_url)
    page_errors, _console_errors, local_http_errors = attach_error_monitors(page, atlas_base_url)

    page.goto(f"{atlas_base_url}/", wait_until="domcontentloaded")
    wait_for_atlas_ready(page)

    page.evaluate(
        """() => {
            window.atlasApp.renderInspectorDrawer({
                id: '0202090000013',
                hcad_num: '0202090000013',
                address: '1017 RUTLAND ST',
                year_built: 1925,
                own_tier: 'regional_portfolio',
                port_id: 'port_7155_old_katy_n260',
                use_category: 'Residential',
                neighborhood: 'Houston Heights',
            });
        }"""
    )
    page.click('#inspector-body [data-inspector-tab="hcad"]')

    page.wait_for_function(
        """() => {
            const card = document.getElementById('inspector-ownership-xray-card');
            return Boolean(card && (card.innerText || '').includes('1017 RUTLAND LLC') && (card.innerText || '').includes('7155 OLD KATY RD'));
        }""",
        timeout=10000,
    )

    xray_text = page.inner_text("#inspector-ownership-xray-card")
    assert "1017 RUTLAND LLC" in xray_text
    assert "7155 OLD KATY RD" in xray_text
    assert "Sibling LLCs / Entities" in xray_text

    # Click 1-click "Map All N Portfolio Parcels" button
    page.click('#inspector-ownership-xray-card [data-activate-owner-portfolio="port_7155_old_katy_n260"]')
    page.wait_for_selector("#portfolio-monograph-card:not(.hidden)", timeout=8000)

    banner_info = page.evaluate(
        """() => ({
            title: (document.getElementById('portfolio-card-title')?.textContent || '').trim(),
            hash: window.location.hash,
            selectVal: document.getElementById('portfolio-preset-select')?.value || '',
        })"""
    )
    assert "7155 Old Katy Rd" in banner_info["title"]
    assert "portfolio=owner_portfolio%3Aport_7155_old_katy_n260" in banner_info["hash"]
    assert banner_info["selectVal"] == "owner_portfolio:port_7155_old_katy_n260"
    assert page_errors == []
    assert local_http_errors == []


def test_it3_portfolio_dropdown_and_scorecard_ownership_composition(
    page: Any,
    atlas_base_url: str,
) -> None:
    """IT-3: Verify Unmasked Ownership Portfolios optgroup and Neighborhood Scorecard 6-tier ownership breakdown."""
    configure_fast_e2e_context(page.context, atlas_base_url)
    page_errors, _console_errors, local_http_errors = attach_error_monitors(page, atlas_base_url)

    page.goto(f"{atlas_base_url}/", wait_until="domcontentloaded")
    wait_for_atlas_ready(page)

    opt_check = page.evaluate(
        """() => {
            const sel = document.getElementById('portfolio-preset-select');
            if (!sel) return { hasSelect: false, ownerOptCount: 0, has7155: false };
            const opts = Array.from(sel.querySelectorAll('option'))
                .map((o) => o.value)
                .filter((v) => v.startsWith('owner_portfolio:'));
            return {
                hasSelect: true,
                ownerOptCount: opts.length,
                has7155: opts.includes('owner_portfolio:port_7155_old_katy_n260'),
            };
        }"""
    )
    assert opt_check["hasSelect"] is True
    assert opt_check["ownerOptCount"] >= 5
    assert opt_check["has7155"] is True

    # Open Neighborhood Scorecard for Greater Heights (super_neighborhoods) and verify 6-tier ownership breakdown
    scorecard_info = page.evaluate(
        """async () => {
            await window.atlasApp.openScorecardModal({ layer: 'super_neighborhoods', id: 'Greater Heights' });
            const modal = document.getElementById('scorecard-modal');
            return {
                hidden: modal ? modal.classList.contains('hidden') : true,
                text: modal ? (modal.innerText || '') : '',
            };
        }"""
    )
    assert scorecard_info["hidden"] is False
    assert "6-Tier Mix:" in scorecard_info["text"]
    assert "Homestead" in scorecard_info["text"]
    assert "Anonymity Shield" in scorecard_info["text"]
    assert page_errors == []
    assert local_http_errors == []


def test_it4_stewardship_timeline_1836_2026_and_sec_25_025_homestead_privacy(
    page: Any,
    atlas_base_url: str,
) -> None:
    """IT-4: Verify 1836-2026 Chain of Stewardship timeline on 1107 Tulane St and Sec. 25.025 privacy on 731 Heights Blvd B."""
    configure_fast_e2e_context(page.context, atlas_base_url)
    page_errors, _console_errors, local_http_errors = attach_error_monitors(page, atlas_base_url)

    page.goto(f"{atlas_base_url}/", wait_until="domcontentloaded")
    wait_for_atlas_ready(page)

    # 1. Open 1107 TULANE ST (0202010000014) and inspect 1836-2026 Chain of Stewardship timeline
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
            return Boolean(card && (card.innerText || '').includes('28 Days') && (card.innerText || '').includes('R2025-495199'));
        }""",
        timeout=10000,
    )

    timeline_txt = page.inner_text("#inspector-stewardship-timeline-card")
    assert "Original Structure Built (1920)" in timeline_txt
    assert "City Directory Occupant: Lorena Mrs" in timeline_txt
    assert "Sewer Disconnect (1 of 2)" in timeline_txt
    assert "R2025-495199" in timeline_txt
    assert "Pre-Demolition Flip Velocity: 28 Days" in timeline_txt

    # 2. Switch to HCAD tab on 1107 TULANE ST to verify Registered-Agent Anonymity Shield X-Ray & Flip Alert
    page.click('#inspector-body [data-inspector-tab="hcad"]')
    tulane_xray_txt = page.inner_text("#inspector-ownership-xray-card")
    assert "Anonymity Shield" in tulane_xray_txt
    assert "3225 MCLEOD DR STE 777" in tulane_xray_txt
    assert "28 days" in tulane_xray_txt

    # 3. Open Protected Individual Homestead 731 HEIGHTS BLVD B (0202570000033) and verify Sec. 25.025 redaction
    page.evaluate(
        """() => {
            window.atlasApp.renderInspectorDrawer({
                id: '0202570000033',
                hcad_num: '0202570000033',
                address: '731 HEIGHTS BLVD B',
                year_built: 1998,
                owner: 'CURRENT OWNER',
                own_tier: 'homestead',
                port_id: null,
                use_category: 'Residential',
                neighborhood: 'Houston Heights',
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
    hs_xray_txt = page.inner_text("#inspector-ownership-xray-card")
    assert "Protected Individual Homestead (TX Tax Code Sec. 25.025)" in hs_xray_txt
    assert "Tax Billing Hub:" not in hs_xray_txt
    assert "C/O / Tax Agent:" not in hs_xray_txt
    assert page_errors == []
    assert local_http_errors == []
