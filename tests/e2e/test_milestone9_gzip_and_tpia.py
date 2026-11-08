"""Playwright E2E tests for Milestone 9 (.json.gz Permit & Address Shard Web Streams
Decompression across Main Atlas & Plat Monitor + TPIA Grantor -> Grantee & Mechanic's
Lien UI Rendering).
"""

from __future__ import annotations

import gzip
import json
import urllib.request
from pathlib import Path
from typing import Any

from conftest import attach_error_monitors, configure_fast_e2e_context, wait_for_atlas_ready

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_m9_e2e_gzip_permit_and_address_shard_hydration_and_tpia_ui_surfaces(
    page: Any,
    atlas_base_url: str,
) -> None:
    """Verify live browser DecompressionStream('gzip') hydration of permits_*.json.gz and addr_*.json.gz plus TPIA Grantor -> Grantee & Mechanic's Lien UI surfaces."""
    manifest = json.loads(
        (REPO_ROOT / "app/public/data/pmtiles_manifest.json").read_text(encoding="utf-8")
    )
    assert len(manifest.get("permit_shard_prefixes") or []) == 152
    assert len(manifest.get("address_shard_prefixes") or []) == 90

    cb = manifest["cache_bust"]
    for rel_gz in (
        f"public/data/permits/permits_062.json.gz?v={cb}",
        f"public/data/address_shards/addr_11.json.gz?v={cb}",
    ):
        with urllib.request.urlopen(f"{atlas_base_url}/{rel_gz}", timeout=5) as resp:
            assert resp.status == 200
            assert "gzip" in (resp.headers.get("Content-Type") or "").lower()
            assert resp.headers.get("Content-Encoding") is None
            assert resp.read(2) == b"\x1f\x8b"

    configure_fast_e2e_context(page.context, atlas_base_url)
    page_errors, console_errors, local_http_errors = attach_error_monitors(page, atlas_base_url)

    gzip_shard_requests: list[str] = []
    legacy_json_shard_requests: list[str] = []

    def _on_request(req: Any) -> None:
        url = req.url
        if "public/data/permits/permits_" in url or "public/data/address_shards/addr_" in url:
            if ".json.gz" in url:
                gzip_shard_requests.append(url)
            else:
                legacy_json_shard_requests.append(url)

    page.on("request", _on_request)

    page.goto(f"{atlas_base_url}/", wait_until="domcontentloaded")
    wait_for_atlas_ready(page)

    # 1. Live browser DecompressionStream('gzip') on permits_062.json.gz + addr_11.json.gz
    live_shard_counts = page.evaluate(
        """async () => {
            const addr11 = await window.atlasApp._fetchAddressShard('11');
            const permRec = await window.atlasApp._fetchPermitRecord('0621100000014');
            const missAddr = await window.atlasApp._fetchAddressShard('05');
            const missPerm = await window.atlasApp._fetchPermitRecord('9990010000001');
            return {
                addrStreetCount: Object.keys(addr11 || {}).length,
                hasPermRec: Boolean(permRec && typeof permRec === 'object'),
                missAddrCount: Object.keys(missAddr || {}).length,
                missPermIsNull: missPerm === null,
            };
        }"""
    )
    assert live_shard_counts["addrStreetCount"] > 100
    assert live_shard_counts["hasPermRec"] is True
    assert live_shard_counts["missAddrCount"] == 0
    assert live_shard_counts["missPermIsNull"] is True
    assert any("permits_062.json.gz" in u for u in gzip_shard_requests)
    assert any("addr_11.json.gz" in u for u in gzip_shard_requests)
    assert legacy_json_shard_requests == []

    # 2. Intercept permits_099.json.gz with real RFC 1952 gzip bytes carrying TPIA 6-part Grantor -> Grantee, M/L, and D/T lender
    tpia_shard_payload = {
        "0990010000001": {
            "yr": 1926,
            "d": "19940615:P812345::D:JONES MARY:SMITH ROBERT|19780310:FC:123-45-6789::D:DOE JANE:JONES MARY",
            "ml": "19260512~VP:642/118~RUSSELL BROWN CO~JONES MARY~14500",
            "dt_lender": "20190415~R2019-149790~FIRST NATIONAL BANK",
        }
    }
    tpia_gz_bytes = gzip.compress(
        json.dumps(tpia_shard_payload, separators=(",", ":")).encode("utf-8"),
        compresslevel=9,
        mtime=0,
    )
    page.route(
        "**/public/data/permits/permits_099.json.gz*",
        lambda route: route.fulfill(
            status=200,
            headers={"Content-Type": "application/gzip"},
            body=tpia_gz_bytes,
        ),
    )

    page.evaluate(
        """() => {
            window.atlasApp.renderInspectorDrawer({
                id: '0990010000001',
                hcad_num: '0990010000001',
                address: '408 HISTORIC TEST AVE',
                year_built: 1926,
                owner: 'SMITH ROBERT',
                own_tier: 'homestead',
                use_category: 'Residential',
                neighborhood: 'Montrose',
            });
        }"""
    )
    page.click('#inspector-body [data-inspector-tab="history"]')
    page.wait_for_function(
        """() => {
            const rk = document.getElementById('research-kit-deed-chain');
            const tl = document.getElementById('inspector-stewardship-timeline-card');
            return Boolean(
                rk && (rk.innerText || '').includes('RUSSELL BROWN CO') &&
                tl && (tl.innerText || '').includes('JONES MARY -> SMITH ROBERT')
            );
        }""",
        timeout=10000,
    )
    rk_text = page.inner_text("#research-kit-deed-chain")
    tl_text = page.inner_text("#inspector-stewardship-timeline-card")
    assert "DOE JANE -> JONES MARY" in rk_text
    assert "RUSSELL BROWN CO ($14500) for JONES MARY" in rk_text
    assert "FIRST NATIONAL BANK" in rk_text
    assert "JONES MARY -> SMITH ROBERT" in tl_text
    assert "RUSSELL BROWN CO ($14500)" in tl_text

    # 3. Companion Plat Monitor (plat-monitor.html) address_shards/*.json.gz hydration
    page.goto(f"{atlas_base_url}/plat-monitor.html", wait_until="domcontentloaded")
    page.wait_for_selector("#app:not(.hidden)", timeout=15000)
    page.wait_for_selector("#list .row", timeout=15000)
    with page.expect_response(
        lambda resp: "public/data/address_shards/addr_20.json.gz" in resp.url and resp.status == 200,
        timeout=10000,
    ):
        page.set_input_files(
            "#fileDondb",
            {
                "name": "DONDB_test.csv",
                "mimeType": "text/csv",
                "buffer": b"Case Number,Address,Status\nDB-2026-001,2000 BAGBY ST,Open\n",
            },
        )
    page.wait_for_function(
        "() => (document.body.innerText || '').toUpperCase().includes('BAGBY')",
        timeout=10000,
    )
    assert any("addr_20.json.gz" in u for u in gzip_shard_requests)
    assert legacy_json_shard_requests == []
    assert page_errors == []
    assert console_errors == []
    assert local_http_errors == []
