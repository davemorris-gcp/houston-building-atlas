from __future__ import annotations

from contextlib import contextmanager
import json
from typing import Any, Iterator

import pytest

from conftest import (
    DEFAULT_E2E_VIEWPORT,
    REPO_ROOT,
    attach_error_monitors,
    configure_fast_e2e_context,
    wait_for_atlas_ready,
)

ARCHETYPES_FILE = REPO_ROOT / "tests" / "fixtures" / "stratified_archetypes.json"
ARCHETYPES_DATA = json.loads(ARCHETYPES_FILE.read_text(encoding="utf-8"))

CLASSES_ORDERED: list[tuple[str, list[dict[str, Any]]]] = []
_grouped: dict[str, list[dict[str, Any]]] = {}
for _item in ARCHETYPES_DATA["archetypes"]:
    _grouped.setdefault(_item["classId"], []).append(_item)
for _cid, _items in _grouped.items():
    CLASSES_ORDERED.append((_cid, _items))

# Under pytest -n 4 each worker handles 3-4 classes (1 cold boot per worker).
# Capping reuse at 8 classes (24 archetypes) also protects single-worker (-n 0) runs
# from Chromium's 100-calls/30s History.replaceState rate limit.
MAX_CLASSES_PER_WARM_PAGE = 8


class _WarmAtlasWorkerPool:
    def __init__(self, browser: Any, base_url: str) -> None:
        self._browser = browser
        self._base_url = base_url
        self._context: Any = None
        self._page: Any = None
        self._errors_tuple: tuple[list[str], list[str], list[str]] | None = None
        self._classes_on_page = 0
        self._page_healthy = False

    def _acquire(self) -> tuple[Any, tuple[list[str], list[str], list[str]]]:
        if (
            self._page is None
            or not self._page_healthy
            or self._page.is_closed()
            or self._classes_on_page >= MAX_CLASSES_PER_WARM_PAGE
        ):
            self.close()
            try:
                self._context = self._browser.new_context(viewport=DEFAULT_E2E_VIEWPORT)
                configure_fast_e2e_context(self._context, self._base_url)
                page = self._context.new_page()
                errors_tuple = attach_error_monitors(page, self._base_url)
                page.goto(f"{self._base_url}/index.html", wait_until="domcontentloaded")
                wait_for_atlas_ready(page)
                self._page = page
                self._errors_tuple = errors_tuple
                self._classes_on_page = 0
            except Exception:
                self.close()
                raise
        else:
            # Only clear error buffers when reusing an already-verified healthy warm page;
            # fresh cold-boot errors on bare /index.html remain intact for the first class to assert.
            assert self._errors_tuple is not None
            page_errors, console_errors, local_http_errors = self._errors_tuple
            page_errors.clear()
            console_errors.clear()
            local_http_errors.clear()

        assert self._errors_tuple is not None
        self._page_healthy = False
        self._classes_on_page += 1
        return self._page, self._errors_tuple

    @contextmanager
    def checkout(self) -> Iterator[tuple[Any, tuple[list[str], list[str], list[str]]]]:
        page, errors_tuple = self._acquire()
        try:
            yield page, errors_tuple
        except Exception:
            self.close()
            raise
        page_errors, console_errors, local_http_errors = errors_tuple
        if page_errors or console_errors or local_http_errors:
            self.close()
        else:
            self._page_healthy = True

    def close(self) -> None:
        if self._context is not None:
            try:
                self._context.close()
            finally:
                self._context = None
                self._page = None
                self._errors_tuple = None
                self._classes_on_page = 0
                self._page_healthy = False


@pytest.fixture(scope="module")
def warm_atlas_pool(browser: Any, atlas_base_url: str) -> Any:
    pool = _WarmAtlasWorkerPool(browser, atlas_base_url)
    yield pool
    pool.close()


@pytest.mark.parametrize(
    "class_id,archetypes",
    CLASSES_ORDERED,
    ids=[c[0] for c in CLASSES_ORDERED],
)
def test_equivalence_class_archetypes(
    class_id: str,
    archetypes: list[dict[str, Any]],
    warm_atlas_pool: _WarmAtlasWorkerPool,
    url_harvester: Any,
) -> None:
    assert len(archetypes) == 3, f"Expected 3 archetypes for {class_id}"

    with warm_atlas_pool.checkout() as (page, (page_errors, console_errors, local_http_errors)):
        for idx, arch in enumerate(archetypes):
            arch_id = arch["archetypeId"]
            deep_hash = arch["deepLinkHash"]
            expected = arch.get("expectedUi", {})

            # Pooled pages reuse a live AtlasApp instance across archetypes: reset _lastHandledUrlKey
            # so app.js handleUrlChange() does not early-return if a canonical deep-link hash repeats,
            # invoke _handleUrlChange() synchronously so it records the new key before the queued
            # browser hashchange event fires, and jump directly to [lng, lat] so local 127.0.0.1
            # regional PMTiles HTTP 206 tiles load without waiting on a 1100ms camera flyTo animation.
            page.evaluate(
                """async ({ h, lng, lat }) => {
                    try {
                        window.atlasApp?.mapController?.map?.stop?.();
                    } catch (_e) {}
                    if (window.atlasApp) {
                        if (!('_lastHandledUrlKey' in window.atlasApp)) {
                            throw new Error('AtlasApp._lastHandledUrlKey was renamed or removed');
                        }
                        window.atlasApp._lastHandledUrlKey = null;
                    }
                    const d = document.getElementById('inspector-drawer');
                    if (d) d.classList.add('hidden');
                    const b = document.getElementById('inspector-body');
                    if (b) b.innerHTML = '';

                    window.location.hash = h;
                    if (window.atlasApp && typeof window.atlasApp._handleUrlChange === 'function') {
                        await window.atlasApp._handleUrlChange();
                    }
                    try {
                        const map = window.atlasApp?.mapController?.map;
                        if (map && Number.isFinite(lng) && Number.isFinite(lat)) {
                            map.jumpTo({
                                center: [lng, lat],
                                zoom: Math.max(map.getZoom?.() || 15.2, 16.5),
                            });
                        } else {
                            map?.stop?.();
                        }
                    } catch (_e) {}
                }""",
                {"h": deep_hash, "lng": arch.get("lng"), "lat": arch.get("lat")},
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
                diag["pageErrors"] = list(page_errors)
                diag["localHttpErrors"] = list(local_http_errors)
                diag["consoleErrors"] = list(console_errors)
                raise AssertionError(
                    f"[{arch_id}] Inspector failed to open for {deep_hash}: {diag}"
                ) from exc

            inspector_body = page.locator("#inspector-body")
            default_text, default_urls = url_harvester.harvest_text_and_urls(
                inspector_body, f"{arch_id}:default"
            )
            full_texts_seen: list[str] = [default_text]
            harvested_for_arch: list[str] = list(default_urls)

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
                tab_text, tab_urls = url_harvester.harvest_text_and_urls(
                    inspector_body, f"{arch_id}:tab_{tab_name}"
                )
                full_texts_seen.append(tab_text)
                harvested_for_arch.extend(tab_urls)

                if tab_name == "permits" and expected.get("mustHaveSelectorInPermits"):
                    sel = expected["mustHaveSelectorInPermits"]
                    assert (
                        page.locator(f"#inspector-body {sel}").count() >= 1
                    ), f"[{arch_id}] Expected selector '{sel}' inside Permits tab"

            combined_text = "\n".join(full_texts_seen)
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
