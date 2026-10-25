from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from conftest import REPO_ROOT, attach_error_monitors

ARCHETYPES_FILE = REPO_ROOT / "tests" / "fixtures" / "stratified_archetypes.json"
ARCHETYPES_DATA = json.loads(ARCHETYPES_FILE.read_text(encoding="utf-8"))

CLASSES_ORDERED: list[tuple[str, list[dict[str, Any]]]] = []
_grouped: dict[str, list[dict[str, Any]]] = {}
for _item in ARCHETYPES_DATA["archetypes"]:
    _grouped.setdefault(_item["classId"], []).append(_item)
for _cid, _items in _grouped.items():
    CLASSES_ORDERED.append((_cid, _items))


@pytest.fixture(scope="function")
def warm_atlas_session(browser: Any, atlas_base_url: str) -> Any:
    """Isolated Chromium page per equivalence class (3 archetypes) to prevent cross-class state bleed and Blink History rate throttling."""
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    errors_tuple = attach_error_monitors(page, atlas_base_url)
    page.goto(f"{atlas_base_url}/index.html", wait_until="domcontentloaded")
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
    yield page, errors_tuple
    context.close()


@pytest.mark.parametrize(
    "class_id,archetypes",
    CLASSES_ORDERED,
    ids=[c[0] for c in CLASSES_ORDERED],
)
def test_equivalence_class_archetypes(
    class_id: str,
    archetypes: list[dict[str, Any]],
    warm_atlas_session: Any,
    url_harvester: Any,
) -> None:
    page, (page_errors, console_errors, local_http_errors) = warm_atlas_session
    assert len(archetypes) == 3, f"Expected 3 archetypes for {class_id}"

    for idx, arch in enumerate(archetypes):
        arch_id = arch["archetypeId"]
        deep_hash = arch["deepLinkHash"]
        expected = arch.get("expectedUi", {})

        # Stop any active camera flyTo from previous archetype and clear inspector
        page.evaluate(
            """() => {
                try {
                    window.atlasApp?.mapController?.map?.stop?.();
                } catch (_e) {}
                const d = document.getElementById('inspector-drawer');
                if (d) d.classList.add('hidden');
                const b = document.getElementById('inspector-body');
                if (b) b.innerHTML = '';
            }"""
        )

        # Navigate via canonical deep-link hash and await URL handler deterministically
        page.evaluate(
            """async (h) => {
                window.location.hash = h;
                if (window.atlasApp && typeof window.atlasApp._handleUrlChange === 'function') {
                    await window.atlasApp._handleUrlChange();
                }
            }""",
            deep_hash,
        )

        try:
            page.wait_for_function(
                """() => {
                    const d = document.getElementById('inspector-drawer');
                    const b = document.getElementById('inspector-body');
                    return d && !d.classList.contains('hidden') && b && b.innerText.trim().length > 40;
                }""",
                timeout=10000,
            )
        except Exception as exc:
            diag = page.evaluate(
                """() => {
                    const d = document.getElementById('inspector-drawer');
                    const b = document.getElementById('inspector-body');
                    return {
                        href: window.location.href,
                        hash: window.location.hash,
                        lastKey: window.atlasApp?._lastHandledUrlKey,
                        drawerClasses: d ? d.className : null,
                        textLen: b ? b.innerText.trim().length : -1,
                        htmlLen: b ? b.innerHTML.length : -1,
                        textPreview: b ? b.innerText.trim().slice(0, 160) : null,
                        selId: window.atlasApp?.mapController?.selectedFeatureId,
                    };
                }"""
            )
            raise AssertionError(f"[{arch_id}] Inspector failed to open for {deep_hash}: {diag}") from exc

        inspector_body = page.locator("#inspector-body")
        full_Texts_seen: list[str] = [inspector_body.inner_text()]
        harvested_for_arch: list[str] = []
        harvested_for_arch.extend(
            url_harvester.harvest_from_locator(inspector_body, f"{arch_id}:default")
        )

        # Exercise Wide View toggle on the first archetype of each equivalence class
        if idx == 0:
            wide_btn = page.locator("#btn-toggle-inspector-wide")
            if wide_btn.is_visible():
                wide_btn.click()
                wide_btn.click()

        # Click through every expected inspector tab and harvest external URLs from each tab's DOM
        must_tabs = expected.get("mustHaveTabs") or []
        for tab_name in must_tabs:
            tab_btn = page.locator(f'#inspector-body [data-inspector-tab="{tab_name}"]')
            assert tab_btn.count() >= 1, f"[{arch_id}] Missing inspector tab '{tab_name}'"
            tab_btn.first.click()
            full_Texts_seen.append(inspector_body.inner_text())
            harvested_for_arch.extend(
                url_harvester.harvest_from_locator(inspector_body, f"{arch_id}:tab_{tab_name}")
            )

            if tab_name == "permits" and expected.get("mustHaveSelectorInPermits"):
                sel = expected["mustHaveSelectorInPermits"]
                assert (
                    page.locator(f"#inspector-body {sel}").count() >= 1
                ), f"[{arch_id}] Expected selector '{sel}' inside Permits tab"

        combined_text = "\n".join(full_Texts_seen)
        must_text_any = expected.get("mustContainTextAny") or []
        if must_text_any:
            assert any(
                needle.lower() in combined_text.lower() for needle in must_text_any
            ), (
                f"[{arch_id} / {arch['name']}] None of {must_text_any} found in rendered inspector text "
                f"(preview: {combined_text[:280]!r})"
            )

        must_domain = expected.get("mustHaveExternalLinkDomain")
        if must_domain:
            assert any(
                must_domain.lower() in u.lower() for u in harvested_for_arch
            ), (
                f"[{arch_id} / {arch['name']}] Expected external link matching '{must_domain}', "
                f"harvested: {harvested_for_arch}"
            )

        assert not page_errors, f"[{arch_id}] Uncaught JS pageerror(s): {page_errors}"
        assert not local_http_errors, f"[{arch_id}] Local HTTP >= 400 failure(s): {local_http_errors}"
        assert not console_errors, f"[{arch_id}] Console error(s): {console_errors}"
