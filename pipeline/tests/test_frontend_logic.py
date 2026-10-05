"""Unit tests for frontend ES modules (palettes.js, filterStore.js) and HTTP Range server."""

from __future__ import annotations

import json
import subprocess
import threading
from http.server import HTTPServer
from pathlib import Path

import httpx

APP_DIR = Path(__file__).resolve().parents[2] / "app"


def test_frontend_es_modules_via_node() -> None:
    """Verify palettes.js and filterStore.js expressions and state logic using Node.js."""
    node_script = """
    import { getYearColorHex, buildColorExpression, getLegendItems, CURATED_TOURS } from './js/palettes.js';
    import {
      createFilterStore,
      buildFeatureFilterExpression,
      featureMatchesFilter,
      serializeStateToHash,
      parseHashToState
    } from './js/filterStore.js';

    const c1880 = getYearColorHex(1880, 'archival');
    const c1880ee = getYearColorHex(1880, 'classic_ee');
    const cUnk = getYearColorHex(0, 'archival');
    if (c1880 !== '#8B1E24') throw new Error('Unexpected archival 1880 color: ' + c1880);
    if (c1880ee !== '#800026') throw new Error('Unexpected classic_ee 1880 color: ' + c1880ee);
    if (cUnk !== '#524E4A') throw new Error('Unexpected unknown color: ' + cUnk);

    const exprYear = buildColorExpression('year_built', 'archival');
    const exprStatus = buildColorExpression('preservation_status');
    const exprUse = buildColorExpression('use_category');
    if (exprYear[0] !== 'case' || exprStatus[0] !== 'case' || exprUse[0] !== 'match') {
      throw new Error('Invalid MapLibre color expressions');
    }

    const store = createFilterStore({ minYear: 1890, maxYear: 1935, showUnknownYears: false });
    const fExpr = buildFeatureFilterExpression(store.getState());
    if (fExpr[0] !== 'all') throw new Error('Expected all filter when showUnknownYears=false');

    if (!featureMatchesFilter({ year_built: 1912 }, store.getState())) {
      throw new Error('1912 should match [1890, 1935]');
    }
    if (featureMatchesFilter({ year_built: 1955 }, store.getState())) {
      throw new Error('1955 should not match [1890, 1935]');
    }
    if (featureMatchesFilter({ year_built: 0 }, store.getState())) {
      throw new Error('0 should not match when showUnknownYears=false');
    }

    const hash = serializeStateToHash(store.getState(), { lat: 29.766, lng: -95.377, zoom: 16.0, pitch: 35 });
    const parsed = parseHashToState(hash);
    if (parsed.patch.minYear !== 1890 || parsed.patch.maxYear !== 1935 || parsed.patch.showUnknownYears !== false) {
      throw new Error('Hash roundtrip failed: ' + JSON.stringify(parsed));
    }

    import crypto from 'node:crypto';
    import { md5Hex, buildHcadAuthHeaders } from './js/hcadLink.js';

    const sampleInput = 'FJce8LGkX3qbtTKrdnYC4EvD52uMSWNh1791214800/AccountDetails';
    const expectedMd5 = crypto.createHash('md5').update(sampleInput).digest('hex');
    if (md5Hex(sampleInput) !== expectedMd5) {
      throw new Error('hcadLink md5Hex mismatch: ' + md5Hex(sampleInput));
    }
    const authHdrs = buildHcadAuthHeaders(1791214800);
    if (!authHdrs.Authorization.startsWith('Basic ') || authHdrs.AuthDate !== '1791214800') {
      throw new Error('Invalid HCAD auth headers: ' + JSON.stringify(authHdrs));
    }

    // Verify Time-Travel Stepper (1 yr, 5 yrs, 10 yrs + decade cycling + cumulative mode)
    const stepStore = createFilterStore();
    // Default is 1836..2026, stepYears=5 -> stepping backward (-1) moves maxYear to 2021 and pauses playback
    stepStore.setState({ isPlaying: true });
    stepStore.stepTime(-1);
    if (stepStore.getState().maxYear !== 2021 || stepStore.getState().isPlaying !== false) {
      throw new Error('Cumulative step backward -5 yrs failed: ' + JSON.stringify(stepStore.getState()));
    }
    // Switch to 1 yr and step forward (+1) -> maxYear becomes 2022
    stepStore.setState({ stepYears: 1 });
    stepStore.stepTime(1);
    if (stepStore.getState().maxYear !== 2022) {
      throw new Error('Cumulative step forward +1 yr failed: ' + JSON.stringify(stepStore.getState()));
    }
    // Select 1920s decade with 10-year step -> stepping forward (+1) advances to 1930s (1930..1939)
    stepStore.setState({ selectedDecade: '1920', minYear: 1920, maxYear: 1929, stepYears: 10 });
    stepStore.stepTime(1);
    if (
      stepStore.getState().selectedDecade !== '1930' ||
      stepStore.getState().minYear !== 1930 ||
      stepStore.getState().maxYear !== 1939
    ) {
      throw new Error('Decade step forward +10 yrs failed: ' + JSON.stringify(stepStore.getState()));
    }
    // Step backward (-1) with 5-year step from 1930..1939 -> slides 10-yr window to 1925..1934
    stepStore.setState({ stepYears: 5 });
    stepStore.stepTime(-1);
    if (
      stepStore.getState().selectedDecade !== 'all' ||
      stepStore.getState().minYear !== 1925 ||
      stepStore.getState().maxYear !== 1934
    ) {
      throw new Error('Sliding window step backward -5 yrs failed: ' + JSON.stringify(stepStore.getState()));
    }

    console.log(JSON.stringify({ ok: true, tours: CURATED_TOURS.length, legendCount: getLegendItems('year_built').length }));
    """
    proc = subprocess.run(
        ["/usr/bin/node", "--input-type=module", "-e", node_script],
        cwd=str(APP_DIR),
        capture_output=True,
        text=True,
        check=True,
    )
    res = json.loads(proc.stdout.strip())
    assert res["ok"] is True
    assert res["tours"] >= 7
    assert res["legendCount"] == 10


def test_http_range_server_supports_pmtiles_206(tmp_path: Path) -> None:
    """Verify app/server.py serves HTTP 206 Partial Content with exact byte ranges for PMTiles."""
    import sys

    sys.path.insert(0, str(APP_DIR))
    from server import RangeHTTPRequestHandler  # type: ignore[import-not-found]

    sample_file = APP_DIR / "index.html"
    assert sample_file.exists()

    class ScopedHandler(RangeHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(APP_DIR), **kwargs)

    httpd = HTTPServer(("127.0.0.1", 0), ScopedHandler)
    port = httpd.server_port
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    try:
        with httpx.Client() as client:
            r_full = client.get(f"http://127.0.0.1:{port}/index.html")
            assert r_full.status_code == 200
            assert r_full.headers.get("Accept-Ranges") == "bytes"

            r_part = client.get(
                f"http://127.0.0.1:{port}/index.html",
                headers={"Range": "bytes=0-14"},
            )
            assert r_part.status_code == 206
            assert len(r_part.content) == 15
            assert r_part.content == b"<!DOCTYPE html>"
    finally:
        httpd.shutdown()
