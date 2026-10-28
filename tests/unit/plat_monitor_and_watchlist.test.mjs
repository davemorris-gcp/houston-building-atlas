import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  DEFAULT_ANNUAL_DEMOLITION_BENCHMARKS,
  resolveAnnualDemolitionBenchmarks,
  formatDemolitionBenchmarksGrantText,
} from "../../app/js/platMonitorHelpers.js";
import { HoustonAtlasApp } from "../../app/js/app.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const REPO_ROOT = path.resolve(__dirname, "..", "..");

test("Annual Demolition Benchmarks: active_permit_alerts.json & DEFAULT_ANNUAL_DEMOLITION_BENCHMARKS schema and exact totals", () => {
  const alertsPath = path.join(REPO_ROOT, "app", "public", "data", "active_permit_alerts.json");
  const rawTxt = fs.readFileSync(alertsPath, "utf8");
  assert.equal(rawTxt.includes("\u2014"), false, "active_permit_alerts.json must contain zero U+2014 em-dashes");

  const payload = JSON.parse(rawTxt);
  assert.ok(payload.annual_benchmarks, "active_permit_alerts.json must contain annual_benchmarks");
  assert.deepEqual(payload.annual_benchmarks, DEFAULT_ANNUAL_DEMOLITION_BENCHMARKS);

  const coh = DEFAULT_ANNUAL_DEMOLITION_BENCHMARKS.coh_structural_demolitions;
  assert.equal(coh.length, 3);
  assert.equal(coh[0].period, "2026 YTD");
  assert.equal(coh[0].total_permits, 1755);
  assert.equal(coh[0].unique_addresses, 1676);
  assert.equal(coh[1].period, "2025");
  assert.equal(coh[1].total_permits, 2187);
  assert.equal(coh[1].unique_addresses, 2066);
  assert.equal(coh[2].period, "2024");
  assert.equal(coh[2].total_permits, 2380);
  assert.equal(coh[2].unique_addresses, 2287);

  const hcad = DEFAULT_ANNUAL_DEMOLITION_BENCHMARKS.hcad_countywide_demolitions;
  assert.deepEqual(
    hcad.map((r) => r.year),
    [2025, 2024, 2023, 2022, 2021, 2020]
  );
  assert.equal(hcad[0].total_permits, 2791);
  assert.equal(hcad[0].unique_parcels, 2464);
  assert.equal(hcad[5].total_permits, 2096);
  assert.equal(hcad[5].unique_parcels, 1975);

  const hahc = DEFAULT_ANNUAL_DEMOLITION_BENCHMARKS.hahc_historic_district_demolitions;
  assert.deepEqual(
    hahc.map((r) => r.period),
    ["2026 YTD", "2025", "2024"]
  );
  assert.equal(hahc[0].main_res_approved, 0);
  assert.equal(hahc[0].main_res_denied, 2);
  assert.equal(hahc[1].main_res_approved, 2);
  assert.equal(hahc[2].main_res_approved, 0);
});

test("Annual Demolition Benchmarks: resolveAnnualDemolitionBenchmarks & formatDemolitionBenchmarksGrantText edge cases and zero em-dashes", () => {
  // Null / undefined / empty fallbacks
  assert.deepEqual(resolveAnnualDemolitionBenchmarks(null), DEFAULT_ANNUAL_DEMOLITION_BENCHMARKS);
  assert.deepEqual(resolveAnnualDemolitionBenchmarks(undefined), DEFAULT_ANNUAL_DEMOLITION_BENCHMARKS);
  assert.deepEqual(resolveAnnualDemolitionBenchmarks({}), DEFAULT_ANNUAL_DEMOLITION_BENCHMARKS);

  // Direct annual_benchmarks object (not wrapped in { annual_benchmarks })
  const directCustom = {
    updated_through: "2026-11-15",
    coh_structural_demolitions: [
      {
        ...DEFAULT_ANNUAL_DEMOLITION_BENCHMARKS.coh_structural_demolitions[0],
        total_permits: 1800,
        unique_addresses: 1710,
      },
      ...DEFAULT_ANNUAL_DEMOLITION_BENCHMARKS.coh_structural_demolitions.slice(1),
    ],
  };
  const resolvedDirect = resolveAnnualDemolitionBenchmarks(directCustom);
  assert.equal(resolvedDirect.updated_through, "2026-11-15");
  assert.equal(resolvedDirect.coh_structural_demolitions[0].total_permits, 1800);
  assert.equal(resolvedDirect.hcad_countywide_demolitions.length, 6);
  assert.equal(resolvedDirect.hahc_historic_district_demolitions.length, 3);

  // Partial wrapped override preserves custom coh_structural_demolitions while backfilling HCAD & HAHC tables
  const partialWrapped = resolveAnnualDemolitionBenchmarks({
    annual_benchmarks: {
      updated_through: "2026-12-01",
      coh_structural_demolitions: directCustom.coh_structural_demolitions,
    },
  });
  assert.equal(partialWrapped.updated_through, "2026-12-01");
  assert.equal(partialWrapped.coh_structural_demolitions[0].total_permits, 1800);
  assert.equal(partialWrapped.hcad_countywide_demolitions.length, 6);
  assert.equal(partialWrapped.hahc_historic_district_demolitions.length, 3);

  // Grant text formatting with default payload
  const defaultTxt = formatDemolitionBenchmarksGrantText(null);
  assert.equal(defaultTxt.includes("\u2014"), false, "Grant text must contain zero U+2014 em-dashes");
  assert.match(defaultTxt, /1,755 structural demolition permits across 1,676 unique addresses/);
  assert.match(defaultTxt, /2,187 structural demolition permits across 2,066 unique addresses/);
  assert.match(defaultTxt, /2,380 structural demolition permits across 2,287 unique addresses/);
  assert.match(defaultTxt, /2025: 2,791 demolition permits across 2,464 unique parcels/);
  assert.match(defaultTxt, /2020: 2,096 demolition permits across 1,975 unique parcels/);
  assert.match(defaultTxt, /0 main residential structures approved for demolition/);

  // Explicit 0 counts in summary must NOT be coerced to 23 / 9 / 14 via falsy ||
  const zeroSummaryTxt = formatDemolitionBenchmarksGrantText({
    summary: {
      total_flagged_buildings: 0,
      critical_count: 0,
      watch_count: 0,
    },
  });
  assert.match(
    zeroSummaryTxt,
    /Active 45-Day Historic Early-Warning Alerts: 0 flagged historic properties \(0 Critical Demolition\/Sewer Disconnect, 0 Abatement\/Garage Watch\)/
  );
});

test("Option A (app/plat-monitor.html): includes #toggleDemoBenchmarksBtn, .stage.demo-stage in #funnel, #demoBenchmarksDrawer, and #copyDemoBenchmarksBtn", () => {
  const html = fs.readFileSync(path.join(REPO_ROOT, "app", "plat-monitor.html"), "utf8");
  assert.equal(html.includes("\u2014"), false, "app/plat-monitor.html must contain zero U+2014 em-dashes");

  // Header button and count span
  assert.match(html, /id=["']toggleDemoBenchmarksBtn["']/);
  assert.match(html, /id=["']hdrDemoYtdCount["']>1,755<\/span>/);

  // Save & Export menu action
  assert.match(html, /data-act=["']copy-demo-benchmarks["']/);
  assert.match(html, /if\s*\(\s*a\s*===\s*["']copy-demo-benchmarks["']\s*\)\s*return\s+copyDemoBenchmarksSummary\(\)/);

  // Collapsible drawer and funnel stage
  assert.match(html, /<section\s+class=["']demo-benchmarks-drawer hidden["']\s+id=["']demoBenchmarksDrawer["']/);
  assert.match(html, /class=["']stage demo-stage["']\s+data-toggle-demo-benchmarks=["']1["']/);
  assert.match(html, /id=["']copyDemoBenchmarksBtn["']/);
  assert.match(html, /id=["']closeDemoBenchmarksBtn["']/);

  // Both agenda and watchlist funnels include buildDemoStageHtml()
  const demoStageCalls = html.match(/\$\{buildDemoStageHtml\(\)\}/g) || [];
  assert.equal(demoStageCalls.length, 2, "Both agenda and watchlist funnels must include buildDemoStageHtml()");

  // Keyboard accessibility on [data-toggle-demo-benchmarks] (role="button" tabindex="0")
  assert.match(
    html,
    /data-toggle-demo-benchmarks[\s\S]{0,400}toggleDemoBenchmarksDrawer/,
    "plat-monitor.html must wire [data-toggle-demo-benchmarks] to toggleDemoBenchmarksDrawer"
  );
  assert.ok(
    /keydown[\s\S]*data-toggle-demo-benchmarks/.test(html),
    "plat-monitor.html keydown listener must support Enter/Space on [data-toggle-demo-benchmarks]"
  );
});

test("Option B (app/index.html, app/js/app.js, app/styles/index.css): sidebar subrow, modal details, HTML escaping, and openPermitWatchModal", () => {
  const indexHtml = fs.readFileSync(path.join(REPO_ROOT, "app", "index.html"), "utf8");
  const cssTxt = fs.readFileSync(path.join(REPO_ROOT, "app", "styles", "index.css"), "utf8");
  const appJsTxt = fs.readFileSync(path.join(REPO_ROOT, "app", "js", "app.js"), "utf8");

  assert.equal(indexHtml.includes("\u2014"), false, "app/index.html must contain zero U+2014 em-dashes");
  assert.equal(cssTxt.includes("\u2014"), false, "app/styles/index.css must contain zero U+2014 em-dashes");
  assert.equal(appJsTxt.includes("\u2014"), false, "app/js/app.js must contain zero U+2014 em-dashes");

  // Sidebar sub-row IDs
  for (const id of [
    "permit-watch-annual-subrow",
    "pw-sidebar-ytd-count",
    "pw-sidebar-2025-count",
    "pw-sidebar-hahc-count",
    "btn-open-demo-benchmarks-sidebar",
  ]) {
    assert.ok(indexHtml.includes(`id="${id}"`), `app/index.html missing sidebar element #${id}`);
  }

  // Modal collapsible details IDs
  for (const id of [
    "pw-annual-benchmarks-details",
    "pw-bench-pill-ytd",
    "pw-bench-pill-2025",
    "pw-bench-pill-hcad",
    "pw-bench-pill-hahc",
    "btn-copy-grant-demo-stats",
    "pw-annual-benchmarks-body",
  ]) {
    assert.ok(indexHtml.includes(`id="${id}"`), `app/index.html missing modal element #${id}`);
  }

  // CSS rules present
  for (const cls of [
    ".permit-watch-annual-subrow",
    ".pw-sidebar-bench-btn",
    ".permit-watch-annual-benchmarks",
    ".pw-annual-benchmarks-summary",
    ".pw-bench-tables-grid",
    ".pw-bench-table",
  ]) {
    assert.ok(cssTxt.includes(cls), `app/styles/index.css missing selector ${cls}`);
  }

  // Verify HoustonAtlasApp._syncPermitWatchCounts, _renderPermitWatchAnnualBenchmarks (including HTML escaping), and openPermitWatchModal
  const elements = new Map();
  const makeEl = (id) => ({
    id,
    textContent: "",
    innerHTML: "",
    open: false,
    scrolled: false,
    classList: {
      hidden: true,
      remove(c) {
        if (c === "hidden") this.hidden = false;
      },
      toggle() {},
    },
    scrollIntoView() {
      this.scrolled = true;
    },
    querySelectorAll() {
      return [];
    },
  });
  for (const id of [
    "permit-watch-topbar-count",
    "permit-watch-layer-count",
    "plat-watch-layer-count",
    "pw-stat-critical",
    "pw-stat-watch",
    "pw-stat-coh",
    "pw-stat-dshs",
    "pw-pill-count-all",
    "pw-pill-count-critical",
    "pw-pill-count-watch",
    "permit-watch-updated-badge",
    "pw-sidebar-ytd-count",
    "pw-sidebar-2025-count",
    "pw-sidebar-hahc-count",
    "pw-bench-pill-ytd",
    "pw-bench-pill-2025",
    "pw-bench-pill-hcad",
    "pw-bench-pill-hahc",
    "pw-annual-benchmarks-body",
    "permit-watch-modal",
    "pw-annual-benchmarks-details",
    "permit-watch-modal-list",
  ]) {
    elements.set(id, makeEl(id));
  }

  const origDoc = globalThis.document;
  globalThis.document = {
    getElementById: (id) => elements.get(id) || null,
  };

  try {
    const customPayload = {
      generated_at: "2026-10-28T12:00:00Z",
      summary: {
        total_flagged_buildings: 23,
        critical_count: 9,
        watch_count: 14,
        coh_permits_matched: 18,
        dshs_notifications_matched: 15,
      },
      annual_benchmarks: {
        ...DEFAULT_ANNUAL_DEMOLITION_BENCHMARKS,
        coh_structural_demolitions: [
          {
            ...DEFAULT_ANNUAL_DEMOLITION_BENCHMARKS.coh_structural_demolitions[0],
            date_range: "Jan 1 <img src=x onerror=1> - Oct 28, 2026",
          },
          ...DEFAULT_ANNUAL_DEMOLITION_BENCHMARKS.coh_structural_demolitions.slice(1),
        ],
        hahc_historic_district_demolitions: [
          {
            ...DEFAULT_ANNUAL_DEMOLITION_BENCHMARKS.hahc_historic_district_demolitions[0],
            summary_note: "0 approved <script>alert(1)</script> & 2 denied",
          },
          ...DEFAULT_ANNUAL_DEMOLITION_BENCHMARKS.hahc_historic_district_demolitions.slice(1),
        ],
      },
      alerts: [],
    };

    const fakeApp = Object.assign(Object.create(HoustonAtlasApp.prototype), {
      mapController: {
        getPermitAlertsPayload: () => customPayload,
        getPlatWatchFeatureCount: () => 26,
      },
    });

    fakeApp.openPermitWatchModal("all", true);

    assert.equal(elements.get("permit-watch-modal").classList.hidden, false);
    assert.equal(elements.get("pw-annual-benchmarks-details").open, true);
    assert.equal(elements.get("pw-sidebar-ytd-count").textContent, "1,755");
    assert.equal(elements.get("pw-sidebar-2025-count").textContent, "2,187");
    assert.equal(elements.get("pw-sidebar-hahc-count").textContent, "0");
    assert.equal(elements.get("pw-bench-pill-ytd").textContent, "1,755");
    assert.equal(elements.get("pw-bench-pill-2025").textContent, "2,187");
    assert.equal(elements.get("pw-bench-pill-hcad").textContent, "2,791");
    assert.equal(elements.get("pw-bench-pill-hahc").textContent, "0");

    const renderedBody = elements.get("pw-annual-benchmarks-body").innerHTML;
    assert.ok(renderedBody.includes("1,755") && renderedBody.includes("1,676"));
    assert.ok(renderedBody.includes("2,791") && renderedBody.includes("2,096"));
    assert.equal(
      renderedBody.includes("<script>alert(1)</script>") ||
        renderedBody.includes("<img src=x onerror=1>"),
      false,
      "_renderPermitWatchAnnualBenchmarks must HTML-escape period, date_range, and summary_note"
    );
    assert.ok(renderedBody.includes("&lt;script&gt;alert(1)&lt;/script&gt;"));
    assert.ok(renderedBody.includes("&lt;img src=x onerror=1&gt;"));
  } finally {
    globalThis.document = origDoc;
  }
});
