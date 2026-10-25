from __future__ import annotations

from typing import Any


def _wait_for_atlas_ready(page: Any) -> None:
    page.wait_for_function(
        """() => Boolean(
            window.atlasApp &&
            window.atlasApp._started === true &&
            window.atlasApp.mapController &&
            window.atlasApp.mapController.overlaysData &&
            Array.isArray(window.atlasApp.searchIndex) &&
            window.atlasApp.searchIndex.length > 1000
        )""",
        timeout=30000,
    )


def test_cold_boot_preset_and_global_modals(
    guarded_page: Any,
    atlas_base_url: str,
    url_harvester: Any,
) -> None:
    page = guarded_page
    page.goto(f"{atlas_base_url}/index.html?preset=good_brick_light", wait_until="domcontentloaded")
    _wait_for_atlas_ready(page)

    # Topbar links
    url_harvester.harvest_from_locator(page.locator("#atlas-topbar"), "global:topbar")

    # 1. Permit Watch Modal
    page.click("#btn-open-permit-watch-modal")
    page.wait_for_selector("#permit-watch-modal:not(.hidden)", timeout=10000)
    assert page.locator("#permit-watch-modal-list .permit-watch-item-card").count() >= 5
    url_harvester.harvest_from_locator(page.locator("#permit-watch-modal"), "modal:permit_watch")
    page.click("#btn-close-permit-watch-modal")
    page.wait_for_selector("#permit-watch-modal", state="hidden", timeout=5000)

    # 2. HAHC COA & Designation Docket Modal
    page.click("#btn-topbar-more")
    page.click("#btn-more-hahc-tracker")
    page.wait_for_selector("#hahc-tracker-modal:not(.hidden)", timeout=10000)
    assert page.locator("#hahc-modal-list .permit-watch-item-card").count() >= 10
    url_harvester.harvest_from_locator(page.locator("#hahc-tracker-modal"), "modal:hahc_docket_all")

    # Filter HAHC Modal to Norhill + Active Oct 8, 2026 Agenda
    page.select_option("#hahc-modal-district-select", value="Norhill")
    page.click('#hahc-modal-category-pills [data-hahc-modal-cat="active_agenda"]')
    assert "Walling" in (page.locator("#hahc-modal-list").text_content() or "")
    url_harvester.harvest_from_locator(page.locator("#hahc-tracker-modal"), "modal:hahc_docket_norhill")
    page.click("#btn-close-hahc-modal")
    page.wait_for_selector("#hahc-tracker-modal", state="hidden", timeout=5000)

    # 3. Sanborn Fire Insurance Map Browser Modal (verify local UT PCL WebP plate loads)
    page.click("#btn-open-sanborn-browser-quick")
    page.wait_for_selector("#sanborn-browser-modal:not(.hidden)", timeout=10000)
    page.wait_for_function(
        """() => {
            const imgs = Array.from(document.querySelectorAll('#sanborn-browser-modal img'));
            return imgs.length > 0 && imgs.some(img => img.complete && img.naturalWidth > 0);
        }""",
        timeout=15000,
    )
    url_harvester.harvest_from_locator(page.locator("#sanborn-browser-modal"), "modal:sanborn_browser")
    page.click("#btn-close-sanborn-modal")
    page.wait_for_selector("#sanborn-browser-modal", state="hidden", timeout=5000)

    # 4. Share View Modal
    page.click("#btn-topbar-more")
    page.click("#btn-open-share-modal")
    page.wait_for_selector("#share-modal:not(.hidden)", timeout=10000)
    page.click("#btn-close-share-modal")
    page.wait_for_selector("#share-modal", state="hidden", timeout=5000)


def test_cold_boot_guided_chapters_and_portfolios(
    guarded_page: Any,
    atlas_base_url: str,
    url_harvester: Any,
) -> None:
    page = guarded_page

    # 1. Cold-boot into Guided Chapter #1 ("How Houston Was Built")
    page.goto(f"{atlas_base_url}/index.html#chapter=bayous_and_two_grids", wait_until="domcontentloaded")
    _wait_for_atlas_ready(page)
    page.wait_for_selector("#city-chapters-card:not(.hidden)", timeout=10000)
    assert "Bayous" in (page.locator("#city-chapter-title").text_content() or "")

    # Step through chapters 2..6
    for expected_num in range(2, 7):
        page.click("#btn-chapter-next")
        assert f"Chapter {expected_num} of 6" in (page.locator("#city-chapter-kicker").text_content() or "")

    # Click first exemplar site chip in Chapter 6 and verify Inspector opens
    page.locator("#city-chapter-sites button").first.click()
    page.wait_for_selector("#inspector-drawer:not(.hidden)", timeout=10000)
    url_harvester.harvest_from_locator(page.locator("#inspector-body"), "chapter6:exemplar_site")
    page.click("#btn-exit-city-chapters")
    page.wait_for_selector("#city-chapters-card", state="hidden", timeout=5000)

    # 2. Cold-boot into Architect Portfolio: Alfred C. Finn
    page.goto(f"{atlas_base_url}/index.html#portfolio=architect:Alfred%20C.%20Finn", wait_until="domcontentloaded")
    _wait_for_atlas_ready(page)
    page.wait_for_selector("#portfolio-monograph-card:not(.hidden)", timeout=10000)
    assert "Alfred" in (page.locator("#portfolio-card-title").text_content() or "")
    assert page.locator("#portfolio-works-list button, #portfolio-works-list [data-portfolio-idx]").count() >= 15

    # Test Extant vs Lost Houston sub-filter pills
    page.click("#portfolio-pill-lost")
    assert page.locator("#portfolio-works-list").text_content()
    page.click("#portfolio-pill-extant")
    page.click("#portfolio-pill-all")

    # Switch preset dropdown to Style Portfolio: Art Deco
    page.select_option("#portfolio-preset-select", value="style:Art Deco")
    assert "Art Deco" in (page.locator("#portfolio-card-title").text_content() or "")

    # Exit portfolio
    page.click("#btn-exit-portfolio")
    page.wait_for_selector("#portfolio-monograph-card", state="hidden", timeout=5000)


def test_cold_boot_nrhp_gone_and_hahc_active_layer_deeplinks(
    guarded_page: Any,
    atlas_base_url: str,
    url_harvester: Any,
) -> None:
    page = guarded_page

    # Cold-boot into Demolished ('Gone') NRHP Point Pin via #bnd=nrhp_properties:nrhp_84001761
    page.goto(
        f"{atlas_base_url}/index.html#bnd=nrhp_properties:nrhp_84001761&layers=hahc_coa&hahcFilter=active_agenda",
        wait_until="domcontentloaded",
    )
    _wait_for_atlas_ready(page)
    page.wait_for_selector("#inspector-drawer:not(.hidden)", timeout=10000)
    body_txt = page.locator("#inspector-body").text_content() or ""
    assert "Dexter House" in body_txt or "84001761" in body_txt
    assert page.locator("#chk-layer-hahc-tracker").is_checked()
    url_harvester.harvest_from_locator(page.locator("#inspector-body"), "deeplink:nrhp_84001761")


def test_plat_monitor_agenda_and_watchlist(
    guarded_page: Any,
    atlas_base_url: str,
    url_harvester: Any,
) -> None:
    page = guarded_page
    page.goto(f"{atlas_base_url}/plat-monitor.html", wait_until="domcontentloaded")
    page.wait_for_selector("#app:not(.hidden)", timeout=15000)
    page.wait_for_selector("#list .row", timeout=15000)

    # Verify Planning Commission Agenda view renders detail & external portal links
    assert page.locator("#list .row").count() >= 5
    page.locator("#list .row").first.click()
    assert (page.locator("#detail h2").text_content() or "").strip() != ""
    url_harvester.harvest_from_locator(page.locator("#detail"), "plat_monitor:agenda_detail")

    # Switch to 15-Point Multi-Agency Property Watchlist mode
    page.click("#modeWatchlistBtn")
    page.wait_for_selector("#list .row", timeout=10000)
    row_count = page.locator("#list .row").count()
    assert row_count >= 3

    for idx in range(min(row_count, 5)):
        page.locator("#list .row").nth(idx).click()
        detail_txt = page.locator("#detail").text_content() or ""
        assert "MR.aspx" not in detail_txt, "plat-monitor.html must never link to retired Harris County Clerk MR.aspx"
        url_harvester.harvest_from_locator(page.locator("#detail"), f"plat_monitor:watchlist_{idx}")


def test_historical_provenance_citations_and_flagging(
    guarded_page: Any,
    atlas_base_url: str,
    url_harvester: Any,
) -> None:
    page = guarded_page

    # 1. Guided Chapter 3: Verify Will Hogg / Varner Realty Co. Norhill prose, primary-source citation pills, and 1-click Flag Error button
    page.goto(f"{atlas_base_url}/index.html#chapter=streetcar_suburbs_and_deeds", wait_until="domcontentloaded")
    _wait_for_atlas_ready(page)
    page.wait_for_selector("#city-chapters-card:not(.hidden)", timeout=10000)
    chapter_body = page.locator("#city-chapter-narrative").text_content() or ""
    assert "Will Hogg" in chapter_body and "Varner Realty" in chapter_body
    assert "Russell Brown's Norhill" not in chapter_body
    assert page.locator("#city-chapter-citations .narrative-citation-pill").count() >= 1
    assert page.locator('#city-chapter-citations [data-quick-flag="narrative"]').count() == 1
    url_harvester.harvest_from_locator(page.locator("#city-chapter-citations"), "chapter3:citations")
    page.click("#btn-exit-city-chapters")

    # 2. HCAD Rounded-Decade Estimate + 1926 City Directory Contradiction Banner (#hcad=1383000010001, 711 Heights Blvd A, HCAD 1920)
    page.evaluate(
        """async () => {
            window.location.hash = '#hcad=1383000010001';
            await window.atlasApp._handleUrlChange();
        }"""
    )
    page.wait_for_selector("#inspector-drawer:not(.hidden)", timeout=10000)
    page.wait_for_selector("#inspector-citydir-contradiction-banner", timeout=10000)
    insp_txt = page.locator("#inspector-body").text_content() or ""
    assert "c. 1920 (HCAD Est.)" in insp_txt
    assert "Rounded Decade Estimate" in insp_txt
    assert page.locator("#inspector-body .evidentiary-tier-badge").count() >= 1

    # 3. Curated Landmark Photo Provenance Pill + 1-Click 'Wrong Building' Flag Button (#hcad=0010330000018, 310 Main St Sweeney & Coombs Building)
    page.evaluate(
        """async () => {
            window.location.hash = '#hcad=0010330000018';
            await window.atlasApp._handleUrlChange();
        }"""
    )
    page.wait_for_function(
        """() => (document.getElementById('inspector-body')?.text_content || document.getElementById('inspector-body')?.innerText || '').includes('310 MAIN')""",
        timeout=10000,
    )
    page.wait_for_selector("#inspector-body .photo-provenance-pill", timeout=15000)
    assert page.locator('#inspector-body [data-action="flag-wrong-photo"]').count() >= 1
