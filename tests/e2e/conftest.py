from __future__ import annotations

import json
import re
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = REPO_ROOT / "app"
SERVER_SCRIPT = APP_DIR / "server.py"
TEST_RESULTS_DIR = REPO_ROOT / "test-results"
HARVESTED_URLS_PATH = TEST_RESULTS_DIR / "harvested_external_urls.json"

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

    def __init__(self) -> None:
        self.by_url: dict[str, dict[str, Any]] = {}

    def record(self, url: str, source: str, label: str = "") -> None:
        clean = (url or "").strip()
        if not clean.startswith(("http://", "https://")):
            return
        if clean.startswith("http://127.0.0.1") or clean.startswith("http://localhost"):
            return
        entry = self.by_url.get(clean)
        if entry is None:
            self.by_url[clean] = {
                "url": clean,
                "firstSeenIn": source,
                "sampleLabel": (label or "").strip()[:120],
                "sources": [source],
            }
        else:
            if source not in entry["sources"] and len(entry["sources"]) < 10:
                entry["sources"].append(source)

    def harvest_from_locator(self, locator: Any, source: str) -> list[str]:
        anchors: list[dict[str, str]] = locator.evaluate(
            """(root) => Array.from(root.querySelectorAll('a[href]')).map(a => ({
                href: a.getAttribute('href') || '',
                resolved: a.href || '',
                text: (a.textContent || a.title || '').trim()
            }))"""
        )
        urls: list[str] = []
        for a in anchors:
            href = a.get("resolved") or a.get("href") or ""
            if href.startswith(("http://", "https://")) and not href.startswith("http://127.0.0.1"):
                ok, reason = validate_harvested_url(href)
                assert ok, f"[{source}] Invalid external URL rendered in DOM: {reason}"
                self.record(href, source, a.get("text", ""))
                urls.append(href)
        return urls

    def write_json(self) -> None:
        TEST_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        items = sorted(self.by_url.values(), key=lambda x: x["url"])
        payload = {
            "totalUniqueExternalUrls": len(items),
            "urls": items,
        }
        HARVESTED_URLS_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


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
    page.set_viewport_size({"width": 1440, "height": 900})
    page_errors, console_errors, local_http_errors = attach_error_monitors(page, atlas_base_url)
    yield page
    assert not page_errors, f"Uncaught JS pageerror(s): {page_errors}"
    assert not local_http_errors, f"Local HTTP >= 400 asset failure(s): {local_http_errors}"
    assert not console_errors, f"Console error(s): {console_errors}"
