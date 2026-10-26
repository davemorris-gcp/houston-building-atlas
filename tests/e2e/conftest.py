from __future__ import annotations

import base64
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import warnings
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = REPO_ROOT / "app"
SERVER_SCRIPT = APP_DIR / "server.py"
TEST_RESULTS_DIR = REPO_ROOT / "test-results"
HARVESTED_URLS_PATH = TEST_RESULTS_DIR / "harvested_external_urls.json"

DEFAULT_E2E_VIEWPORT: dict[str, int] = {"width": 1440, "height": 900}

TRANSPARENT_1X1_PNG: bytes = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)

OPENFREEMAP_PLANET_TILEJSON: bytes = json.dumps(
    {
        "tilejson": "3.0.0",
        "tiles": ["https://tiles.openfreemap.org/planet/{z}/{x}/{y}.pbf"],
        "vector_layers": [{"id": "transportation"}],
        "minzoom": 0,
        "maxzoom": 14,
    }
).encode("utf-8")

_UNPKG_MEM_CACHE: dict[str, tuple[str, bytes]] = {}
EXTERNAL_URL_ROUTE_RE = re.compile(r"^https?://(?!(?:127\.0\.0\.1|localhost)(?::|/|$))", re.I)
_CORS_HEADERS: dict[str, str] = {"Access-Control-Allow-Origin": "*"}

_EXACT_HOST_STUBS: dict[str, tuple[str, bytes]] = {
    "fonts.googleapis.com": ("text/css; charset=utf-8", b""),
    "fonts.gstatic.com": ("font/woff2", b""),
    # plat-monitor.html pullCloudSheetSync() fetches CSV rows from Apps Script on script.google.com
    "script.google.com": ("text/csv; charset=utf-8", b"hcad_num,status\n"),
}

_RASTER_HOST_SUFFIXES: tuple[str, ...] = (
    "arcgisonline.com",
    "tiles.arcgis.com",
    "upload.wikimedia.org",
    "loc.gov",
    "davidrumsey.com",
    "utexas.edu",
)

_GIS_JSON_HOST_SUFFIXES: tuple[str, ...] = (
    "arcweb.hcad.org",
    "api.hcad.org",
    "mycity.houstontx.gov",
    "mycity2.houstontx.gov",
    "gis.hctx.net",
)

DEAD_URL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("ut_pcl_sanborn_grouped_index", re.compile(r"maps\.lib\.utexas\.edu/maps/sanborn/texas_cities_", re.I)),
    ("harris_county_clerk_mr_maintenance", re.compile(r"cclerk\.hctx\.net/Applications/WebSearch/MR\.aspx", re.I)),
    ("coh_historic_districts_legacy_html", re.compile(r"houstontx\.gov/planning/HistoricPres/historic_districts\.html", re.I)),
    (
        "coh_historic_pres_manual_legacy_html",
        re.compile(
            r"houstontx\.gov/planning/HistoricPres/(?:HistoricPreservationManual/historic_preservation_manual|hist_pres_main)\.html",
            re.I,
        ),
    ),
    (
        "coh_norhill_redline_wrong_oct_folder",
        re.compile(
            r"houstontx\.gov/planning/Commissions/docs_pdfs/hahc/2026/Oct_10_2026/C_Norhill_Redline_Draft_Oct_26\.pdf",
            re.I,
        ),
    ),
    ("hpl_legacy_cdm_redirect_path", re.compile(r"digital\.houstonlibrary\.org/cdm/", re.I)),
    ("uninterpolated_js_token", re.compile(r"(?:[/?&=#]|^)(?:undefined|null|NaN)(?:[/?&#]|$)|\$\{[^}]*\}")),
]


def wait_for_atlas_ready(page: Any, timeout_ms: int = 30000) -> None:
    """Waits until AtlasApp has initialized MapLibre, loaded overlaysData, and indexed searchIndex."""
    page.wait_for_function(
        """() => Boolean(
            window.atlasApp &&
            window.atlasApp._started === true &&
            window.atlasApp.mapController &&
            window.atlasApp.mapController.overlaysData &&
            Array.isArray(window.atlasApp.searchIndex) &&
            window.atlasApp.searchIndex.length > 1000
        )""",
        timeout=timeout_ms,
    )


def validate_harvested_url(url: str) -> tuple[bool, str]:
    clean = (url or "").strip()
    if not clean.startswith(("http://", "https://")):
        return False, f"Unsupported protocol: {clean}"
    for rule_id, pat in DEAD_URL_PATTERNS:
        if pat.search(clean):
            return False, f"Matched retired/dead URL rule '{rule_id}': {clean}"
    if "atlas.thc.texas.gov/Details/" in clean:
        m = re.search(r"atlas\.thc\.texas\.gov/Details/([^/?#]+)", clean)
        if m and not re.fullmatch(r"\d{9,11}", m.group(1)):
            return False, f"Invalid THC Details ID '{m.group(1)}' in {clean}"
    if "arcweb.hcad.org/parcel-viewer" in clean and "hcad_num=" in clean:
        m = re.search(r"hcad_num=([^&#]+)", clean)
        if m and not re.fullmatch(r"\d{13}", m.group(1)):
            return False, f"Invalid HCAD account '{m.group(1)}' in {clean}"
    return True, ""


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def configure_fast_e2e_context(context: Any, base_url: str) -> None:
    """Stubs external third-party tile/API traffic while passing local 127.0.0.1 assets and caching unpkg CDN bundles in RAM."""

    def _handle_route(route: Any) -> None:
        url = route.request.url
        if url.startswith(base_url):
            route.continue_()
            return

        parsed = urllib.parse.urlsplit(url)
        host = (parsed.hostname or "").lower()
        path = parsed.path.lower()
        query = parsed.query.lower()
        res_type = route.request.resource_type

        if host == "unpkg.com":
            cached = _UNPKG_MEM_CACHE.get(url)
            if cached is None:
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        body = resp.read()
                        upstream_ct = resp.headers.get("Content-Type")
                    default_ct = (
                        "text/css; charset=utf-8"
                        if path.endswith(".css")
                        else "application/javascript; charset=utf-8"
                    )
                    cached = (upstream_ct or default_ct, body)
                    _UNPKG_MEM_CACHE[url] = cached
                except Exception:
                    route.continue_()
                    return
            route.fulfill(
                status=200,
                content_type=cached[0],
                headers={**_CORS_HEADERS, "Cache-Control": "public, max-age=31536000"},
                body=cached[1],
            )
            return

        if host in _EXACT_HOST_STUBS:
            ct, body = _EXACT_HOST_STUBS[host]
            route.fulfill(status=200, content_type=ct, headers=_CORS_HEADERS, body=body)
            return

        if host == "tiles.openfreemap.org":
            if "/planet" in path and not path.endswith(".pbf"):
                route.fulfill(
                    status=200,
                    content_type="application/json; charset=utf-8",
                    headers=_CORS_HEADERS,
                    body=OPENFREEMAP_PLANET_TILEJSON,
                )
            else:
                route.fulfill(
                    status=200,
                    content_type="application/x-protobuf",
                    headers=_CORS_HEADERS,
                    body=b"",
                )
            return

        if host == "docs.google.com" and path.startswith("/spreadsheets"):
            route.fulfill(
                status=200,
                content_type="text/csv; charset=utf-8",
                headers=_CORS_HEADERS,
                body=b"hcad_num,status\n",
            )
            return

        if host == "commons.wikimedia.org" and path.startswith("/w/api.php"):
            route.fulfill(
                status=200,
                content_type="application/json; charset=utf-8",
                headers=_CORS_HEADERS,
                body=b'{"batchcomplete":"","query":{"pages":{}}}',
            )
            return

        # Evaluate explicit ArcGIS / REST JSON query intent before host-level raster fallbacks
        if path.endswith("/query") or "f=json" in query or "f=geojson" in query:
            route.fulfill(
                status=200,
                content_type="application/json; charset=utf-8",
                headers=_CORS_HEADERS,
                body=b'{"features":[]}',
            )
            return

        if (
            res_type in ("image", "media")
            or "/mapserver/tile/" in path
            or "/imageserver/" in path
            or "f=image" in query
            or "service=wms" in query
            or path.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))
            or host.endswith(_RASTER_HOST_SUFFIXES)
        ):
            route.fulfill(
                status=200,
                content_type="image/png",
                headers=_CORS_HEADERS,
                body=TRANSPARENT_1X1_PNG,
            )
            return

        if host.endswith(_GIS_JSON_HOST_SUFFIXES):
            route.fulfill(
                status=200,
                content_type="application/json; charset=utf-8",
                headers=_CORS_HEADERS,
                body=b'{"features":[]}',
            )
            return

        if res_type in ("script", "stylesheet"):
            route.abort("blockedbyclient")
            return

        route.fulfill(
            status=200,
            content_type="application/json; charset=utf-8",
            headers=_CORS_HEADERS,
            body=b"{}",
        )

    context.route(EXTERNAL_URL_ROUTE_RE, _handle_route)


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args: dict[str, Any]) -> dict[str, Any]:
    existing_args = list(browser_type_launch_args.get("args", []))
    webgl_args = [
        "--use-gl=angle",
        "--use-angle=swiftshader",
        "--ignore-gpu-blocklist",
        "--disable-dev-shm-usage",
    ]
    return {
        **browser_type_launch_args,
        "args": existing_args + [a for a in webgl_args if a not in existing_args],
    }


@pytest.fixture(scope="session")
def atlas_base_url() -> str:
    port = _pick_free_port()
    proc = subprocess.Popen(
        [sys.executable, str(SERVER_SCRIPT), "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(APP_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 15.0
    ready = False
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/index.html", timeout=2) as resp:
                if resp.status == 200:
                    ready = True
                    break
        except Exception:
            time.sleep(0.15)

    if not ready:
        proc.terminate()
        raise RuntimeError(f"app/server.py failed to start on {base_url}")

    # Verify RFC 7233 HTTP 206 Partial Content Range support required by PMTiles v3
    pmtiles_req = urllib.request.Request(
        f"{base_url}/public/data/houston_buildings_ne.pmtiles",
        headers={"Range": "bytes=0-63"},
    )
    with urllib.request.urlopen(pmtiles_req, timeout=5) as pm_resp:
        assert pm_resp.status == 206, f"Expected HTTP 206 Partial Content for PMTiles, got {pm_resp.status}"
        body = pm_resp.read()
        assert len(body) == 64, f"Expected 64-byte PMTiles range slice, got {len(body)}"
        assert body[:7] == b"PMTiles", f"Invalid PMTiles v3 magic header: {body[:7]!r}"

    yield base_url

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


class ExternalUrlHarvester:
    """Collects and deduplicates all external HTTP(S) links rendered across Playwright E2E tests."""

    MAX_SOURCES_PER_URL = 10

    def __init__(self) -> None:
        self.by_url: dict[str, dict[str, Any]] = {}

    def record(self, url: str, source: str, label: str = "") -> None:
        clean = (url or "").strip()
        if not EXTERNAL_URL_ROUTE_RE.match(clean):
            return
        clean_label = (label or "").strip()[:120]
        entry = self.by_url.get(clean)
        if entry is None:
            self.by_url[clean] = {
                "url": clean,
                "firstSeenIn": source,
                "sampleLabel": clean_label,
                "sources": [source] if source else [],
            }
        else:
            if not entry["sampleLabel"] and clean_label:
                entry["sampleLabel"] = clean_label
            if (
                source
                and source not in entry["sources"]
                and len(entry["sources"]) < self.MAX_SOURCES_PER_URL
            ):
                entry["sources"].append(source)

    def merge_payload(self, data: dict[str, Any]) -> None:
        for item in data.get("urls", []):
            url = (item.get("url") or "").strip()
            if not url:
                continue
            sources = list(item.get("sources") or [])
            first_source = item.get("firstSeenIn") or (sources[0] if sources else "")
            self.record(url, first_source, item.get("sampleLabel", ""))
            for src in sources:
                self.record(url, src)

    def harvest_from_locator(self, locator: Any, source: str) -> list[str]:
        _, urls = self.harvest_text_and_urls(locator, source)
        return urls

    def harvest_text_and_urls(self, locator: Any, source: str) -> tuple[str, list[str]]:
        snap: dict[str, Any] = locator.evaluate(
            """(root) => ({
                text: root.innerText || '',
                anchors: Array.from(root.querySelectorAll('a[href]')).map(a => ({
                    href: a.getAttribute('href') || '',
                    resolved: a.href || '',
                    text: (a.textContent || a.title || '').trim()
                }))
            })"""
        )
        urls: list[str] = []
        for a in snap.get("anchors") or []:
            href = (a.get("resolved") or a.get("href") or "").strip()
            if EXTERNAL_URL_ROUTE_RE.match(href):
                ok, reason = validate_harvested_url(href)
                assert ok, f"[{source}] Invalid external URL rendered in DOM: {reason}"
                self.record(href, source, a.get("text", ""))
                urls.append(href)
        return snap.get("text") or "", urls

    def write_to(self, out_path: Path) -> None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        items = sorted(self.by_url.values(), key=lambda x: x["url"])
        payload = {
            "totalUniqueExternalUrls": len(items),
            "urls": items,
        }
        out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def write_json(self) -> None:
        worker_id = os.environ.get("PYTEST_XDIST_WORKER")
        out_path = (
            TEST_RESULTS_DIR / f".harvested_urls_{worker_id}.json"
            if worker_id
            else HARVESTED_URLS_PATH
        )
        self.write_to(out_path)


def pytest_configure(config: Any) -> None:
    if not hasattr(config, "workerinput") and TEST_RESULTS_DIR.exists():
        HARVESTED_URLS_PATH.unlink(missing_ok=True)
        for stale_shard in TEST_RESULTS_DIR.glob(".harvested_urls_*.json"):
            stale_shard.unlink(missing_ok=True)


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    if hasattr(session.config, "workerinput") or not TEST_RESULTS_DIR.exists():
        return
    shards = sorted(TEST_RESULTS_DIR.glob(".harvested_urls_*.json"))
    if not shards:
        return
    merged = ExternalUrlHarvester()
    for shard_path in shards:
        try:
            data = json.loads(shard_path.read_text(encoding="utf-8"))
            merged.merge_payload(data)
        except (OSError, json.JSONDecodeError) as exc:
            warnings.warn(
                f"Failed to merge harvested URL shard {shard_path.name}: {exc}",
                stacklevel=1,
            )
        finally:
            shard_path.unlink(missing_ok=True)

    merged.write_to(HARVESTED_URLS_PATH)


@pytest.fixture(scope="session")
def url_harvester() -> ExternalUrlHarvester:
    harvester = ExternalUrlHarvester()
    yield harvester
    harvester.write_json()


def attach_error_monitors(page: Any, base_url: str) -> tuple[list[str], list[str], list[str]]:
    """Attaches strict runtime error listeners to a Playwright Page."""
    page_errors: list[str] = []
    console_errors: list[str] = []
    local_http_errors: list[str] = []

    benign_console_substrings = (
        "ERR_NAME_NOT_RESOLVED",
        "ERR_INTERNET_DISCONNECTED",
        "Failed to load resource: net::",
        "basemaps.cartocdn.com",
        "arcgisonline.com",
        "hcad.org",
    )

    def on_page_error(exc: Any) -> None:
        page_errors.append(str(exc))

    def on_console(msg: Any) -> None:
        if msg.type == "error":
            txt = msg.text or ""
            loc_url = (msg.location or {}).get("url", "")
            if any(b in txt or b in loc_url for b in benign_console_substrings):
                return
            console_errors.append(f"{txt} ({loc_url})")

    def on_response(resp: Any) -> None:
        if resp.url.startswith(base_url) and resp.status >= 400:
            local_http_errors.append(f"HTTP {resp.status} on {resp.url}")

    page.on("pageerror", on_page_error)
    page.on("console", on_console)
    page.on("response", on_response)
    return page_errors, console_errors, local_http_errors


@pytest.fixture
def guarded_page(page: Any, atlas_base_url: str) -> Any:
    configure_fast_e2e_context(page.context, atlas_base_url)
    page.set_viewport_size(DEFAULT_E2E_VIEWPORT)
    page_errors, console_errors, local_http_errors = attach_error_monitors(page, atlas_base_url)
    yield page
    assert not page_errors, f"Uncaught JS pageerror(s): {page_errors}"
    assert not local_http_errors, f"Local HTTP >= 400 asset failure(s): {local_http_errors}"
    assert not console_errors, f"Console error(s): {console_errors}"
