import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  ARCGIS_WORLD_STREET_EXPORT_URL,
  HOUSTON_DOWNTOWN_ANCHOR,
  buildBlockViewportProjection,
  buildHoustonLoopLocatorSvg,
  computeHoustonDowntownContext,
  ensureSpatialContextThumbnailsInitialized,
  findContextZoneRingFromOverlays,
  lngLatToMercatorMeters,
  mercatorMetersToLngLat,
  normalizePolygonRingsInput,
  renderSpatialContextThumbnailHtml,
} from "../../app/js/spatialContextThumbnails.js";
import { HoustonAtlasApp } from "../../app/js/app.js";
import { isDemolishedPermitAlert } from "../../app/js/curatedEdits.js";
import { AtlasMapController } from "../../app/js/mapController.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "..", "..");
const DATA_DIR = path.join(REPO_ROOT, "app", "public", "data");

const CHAR_HYPHEN = String.fromCodePoint(0x2010);
const CHAR_ENDASH = String.fromCodePoint(0x2013);
const CHAR_EMDASH = String.fromCodePoint(0x2014);

function hasUnicodeDash(raw) {
  const s = String(raw ?? "");
  return s.includes(CHAR_HYPHEN) || s.includes(CHAR_ENDASH) || s.includes(CHAR_EMDASH);
}

test("UT-SCT-1 (SA-1 / UT-12): spatialContextThumbnails.js imports cleanly in bare Node.js with zero top-level DOM globals and lockstep ?v= cache_bust", () => {
  const probeScript = [
    'import * as mod from "./app/js/spatialContextThumbnails.js";',
    'if (typeof globalThis.window !== "undefined" || typeof globalThis.document !== "undefined") {',
    '  throw new Error("DOM globals leaked at module import time");',
    "}",
    "mod.ensureSpatialContextThumbnailsInitialized();",
    "console.log(JSON.stringify(Object.keys(mod).sort()));",
  ].join("\n");

  const stdout = execFileSync(process.execPath, ["--input-type=module", "-e", probeScript], {
    cwd: REPO_ROOT,
    encoding: "utf8",
  }).trim();

  const exportedKeys = JSON.parse(stdout);
  assert.deepEqual(exportedKeys, [
    "ARCGIS_WORLD_STREET_EXPORT_URL",
    "HOUSTON_DOWNTOWN_ANCHOR",
    "buildBlockViewportProjection",
    "buildHoustonLoopLocatorSvg",
    "cleanHistoricDistrictLabel",
    "computeHoustonDowntownContext",
    "ensureSpatialContextThumbnailsInitialized",
    "findContextZoneRingFromOverlays",
    "lngLatToMercatorMeters",
    "mercatorMetersToLngLat",
    "normalizePolygonRingsInput",
    "renderSpatialContextThumbnailHtml",
  ]);

  const manifestVer = JSON.parse(
    fs.readFileSync(path.join(DATA_DIR, "pmtiles_manifest.json"), "utf8")
  ).cache_bust;
  const appJs = fs.readFileSync(path.join(REPO_ROOT, "app", "js", "app.js"), "utf8");
  const platHtml = fs.readFileSync(path.join(REPO_ROOT, "app", "plat-monitor.html"), "utf8");
  assert.ok(
    appJs.includes(`./spatialContextThumbnails.js?v=${manifestVer}`),
    `app/js/app.js must import spatialContextThumbnails.js?v=${manifestVer}`
  );
  assert.ok(
    platHtml.includes(`./js/spatialContextThumbnails.js?v=${manifestVer}`),
    `app/plat-monitor.html must import ./js/spatialContextThumbnails.js?v=${manifestVer}`
  );
});

test("UT-SCT-2: EPSG:3857 Web Mercator round-trip, normalizePolygonRingsInput, and buildBlockViewportProjection accuracy", () => {
  const [mx, my] = lngLatToMercatorMeters(HOUSTON_DOWNTOWN_ANCHOR.lng, HOUSTON_DOWNTOWN_ANCHOR.lat);
  assert.ok(mx < -10600000 && mx > -10630000, `Unexpected Mercator X for Houston: ${mx}`);
  assert.ok(my > 3460000 && my < 3490000, `Unexpected Mercator Y for Houston: ${my}`);

  const [rtLng, rtLat] = mercatorMetersToLngLat(mx, my);
  assert.ok(Math.abs(rtLng - HOUSTON_DOWNTOWN_ANCHOR.lng) < 1e-9);
  assert.ok(Math.abs(rtLat - HOUSTON_DOWNTOWN_ANCHOR.lat) < 1e-9);

  // Single ring, GeoJSON Polygon, GeoJSON Feature, GeoJSON MultiPolygon, and invalid inputs
  const sampleRing = [
    [-95.3972, 29.7895],
    [-95.3968, 29.7895],
    [-95.3968, 29.7899],
    [-95.3972, 29.7899],
    [-95.3972, 29.7895],
  ];
  assert.equal(normalizePolygonRingsInput(null).length, 0);
  assert.equal(normalizePolygonRingsInput([[0, 0], [1, 0], [1, 1]]).length, 0, "Non-Houston coords rejected");
  assert.equal(normalizePolygonRingsInput([[-95.39, 29.79], [-95.38, 29.79]]).length, 0, "<3 points rejected");
  assert.equal(normalizePolygonRingsInput(sampleRing).length, 1);
  assert.equal(
    normalizePolygonRingsInput({
      type: "Feature",
      geometry: { type: "Polygon", coordinates: [sampleRing] },
    }).length,
    1
  );
  assert.equal(
    normalizePolygonRingsInput({
      type: "MultiPolygon",
      coordinates: [[sampleRing], [sampleRing.map(([x, y]) => [x + 0.002, y + 0.002])]],
    }).length,
    2
  );

  // Projection with polygon + omitted lat/lng infers centroid from polygon rings
  const proj = buildBlockViewportProjection({
    polygonRings: sampleRing,
    width: 164,
    height: 104,
  });
  assert.ok(proj, "Expected valid projection from polygonRings alone");
  assert.equal(proj.hasPolygon, true);
  assert.ok(proj.streetMapUrl.startsWith(`${ARCGIS_WORLD_STREET_EXPORT_URL}?`));
  const parsedUrl = new URL(proj.streetMapUrl);
  assert.equal(parsedUrl.searchParams.get("bboxSR"), "4326");
  assert.equal(parsedUrl.searchParams.get("imageSR"), "3857");
  assert.equal(parsedUrl.searchParams.get("size"), "328,208");
  assert.equal(parsedUrl.searchParams.get("dpi"), "144");
  assert.equal(parsedUrl.searchParams.get("format"), "png32");
  assert.equal(parsedUrl.searchParams.get("f"), "image");
  assert.ok(proj.svgPathData.startsWith("M") && proj.svgPathData.endsWith("Z"));
  const vertexMatches = Array.from(proj.svgPathData.matchAll(/[ML]([\d.]+),([\d.]+)/g), (m) => [
    Number(m[1]),
    Number(m[2]),
  ]);
  assert.equal(vertexMatches.length, 5);
  assert.ok(
    vertexMatches[0][1] > 52 && vertexMatches[2][1] < 52,
    `North-up Web Mercator projection requires southern lat (29.7895) y=${vertexMatches[0][1]} > northern lat (29.7899) y=${vertexMatches[2][1]}`
  );
  assert.ok(proj.dotX >= 70 && proj.dotX <= 94, `Expected centered dotX, got ${proj.dotX}`);
  assert.ok(proj.dotY >= 40 && proj.dotY <= 64, `Expected centered dotY, got ${proj.dotY}`);
  assert.ok(proj.scaleBarPx >= 14 && proj.scaleBarPx <= 48);
  assert.match(proj.scaleLabel, /^\d+ ft$|^0\.\d+ mi$/);

  // Point-only fallback without polygon
  const ptProj = buildBlockViewportProjection({ lat: 29.7595, lng: -95.3655, width: 152, height: 96 });
  assert.ok(ptProj);
  assert.equal(ptProj.hasPolygon, false);
  assert.equal(ptProj.svgPathData, "");
  assert.equal(ptProj.dotX, 76);
  assert.equal(ptProj.dotY, 48);

  // Out-of-bounds returns null
  assert.equal(buildBlockViewportProjection({ lat: 40.7128, lng: -74.006 }), null);
});

test("UT-SCT-3: computeHoustonDowntownContext classifies Downtown Core, Inside 610 Loop, Between 610 & Beltway 8, and Outside Beltway 8", () => {
  assert.equal(computeHoustonDowntownContext(null, null), null);
  assert.equal(computeHoustonDowntownContext(32.7767, -96.797), null);

  const dt = computeHoustonDowntownContext(29.7595, -95.3655);
  assert.equal(dt.distanceMiles, 0);
  assert.equal(dt.inside610, true);
  assert.equal(dt.insideBeltway8, true);
  assert.equal(dt.loopLabel, "Downtown Core");
  assert.equal(dt.distText, "In Downtown Houston");
  assert.equal(dt.summaryText, "Downtown Core");

  // Houston Heights (~29.7905, -95.3976): ~2.9 mi NW/NNW of Downtown, Inside 610 Loop
  const heights = computeHoustonDowntownContext(29.7905, -95.3976);
  assert.equal(heights.inside610, true);
  assert.equal(heights.loopLabel, "Inside 610 Loop");
  assert.ok(["NW", "NNW"].includes(heights.compass), `Expected NW/NNW bearing, got ${heights.compass}`);
  assert.ok(heights.distanceMiles > 2.2 && heights.distanceMiles < 3.6);
  assert.ok(heights.summaryText.includes("(Inside 610 Loop)"));

  // Oak Forest / Garden Oaks (~29.828, -95.435): outside 610, inside Beltway 8
  const oakForest = computeHoustonDowntownContext(29.828, -95.435);
  assert.equal(oakForest.inside610, false);
  assert.equal(oakForest.insideBeltway8, true);
  assert.equal(oakForest.loopLabel, "Between 610 & Beltway 8");

  // Clear Lake / NASA (~29.559, -95.090): outside Beltway 8
  const clearLake = computeHoustonDowntownContext(29.559, -95.09);
  assert.equal(clearLake.inside610, false);
  assert.equal(clearLake.insideBeltway8, false);
  assert.equal(clearLake.loopLabel, "Outside Beltway 8");
  assert.equal(clearLake.compass, "SE");
});

test("UT-SCT-4: findContextZoneRingFromOverlays MultiPolygon/exact-match priority, buildHoustonLoopLocatorSvg inner/outer zoom, and Unicode dash sanitization", () => {
  const polyA = [
    [-95.412, 29.772],
    [-95.408, 29.772],
    [-95.408, 29.776],
    [-95.412, 29.776],
    [-95.412, 29.772],
  ];
  const polyB = [
    [-95.402, 29.772],
    [-95.398, 29.772],
    [-95.398, 29.776],
    [-95.402, 29.776],
    [-95.402, 29.772],
  ];
  const overlaysFixture = {
    historic_districts: {
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          properties: { name: `Brunner${CHAR_EMDASH}Harmonium Historic District` },
          geometry: {
            type: "MultiPolygon",
            coordinates: [[polyA], [polyB]],
          },
        },
      ],
    },
    super_neighborhoods: {
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          properties: { name: "INDEPENDENCE HEIGHTS" },
          geometry: { type: "Polygon", coordinates: [[[-95.41, 29.83], [-95.39, 29.83], [-95.39, 29.85], [-95.41, 29.83]]] },
        },
        {
          type: "Feature",
          properties: { name: "HEIGHTS" },
          geometry: { type: "Polygon", coordinates: [[[-95.405, 29.785], [-95.388, 29.785], [-95.388, 29.804], [-95.405, 29.785]]] },
        },
      ],
    },
  };

  // 1) Exact match "Heights" must beat earlier substring feature "INDEPENDENCE HEIGHTS"
  const exactNb = findContextZoneRingFromOverlays(overlaysFixture, { neighborhood: "Heights" });
  assert.ok(exactNb);
  assert.equal(exactNb.label, "HEIGHTS");

  // 2) Coordinate lookup inside polygon #2 of a MultiPolygon district must match and sanitize Unicode dashes
  const mpCoordHit = findContextZoneRingFromOverlays(overlaysFixture, {
    lng: -95.40,
    lat: 29.774,
  });
  assert.ok(mpCoordHit, "Point inside second polygon of MultiPolygon historic district must resolve");
  assert.equal(hasUnicodeDash(mpCoordHit.label), false, "Zone label must sanitize Unicode dashes to ASCII '-'");
  assert.equal(mpCoordHit.label, "Brunner-Harmonium Historic District");

  // 3) Name lookup with ASCII '-' must match overlay feature containing Unicode em-dash
  const mpNameHit = findContextZoneRingFromOverlays(overlaysFixture, {
    historicDistrict: "Brunner-Harmonium Historic District",
  });
  assert.ok(mpNameHit);
  assert.equal(mpNameHit.label, "Brunner-Harmonium Historic District");

  // 4) Inner-loop SVG omits BELTWAY 8; outer-ring SVG includes BELTWAY 8; MultiPolygon renders both zone rings
  const innerSvg = buildHoustonLoopLocatorSvg({
    lat: 29.774,
    lng: -95.40,
    mini: false,
    zoneLabel: mpNameHit.label,
    zoneRing: mpNameHit.rings || mpNameHit.ring,
  });
  assert.ok(innerSvg.includes("610 LOOP"));
  assert.ok(!innerSvg.includes("BELTWAY 8"), "Inner-frame property must stay zoomed to 610 Loop");
  assert.ok(innerSvg.includes("BRUNNER-HARMONIUM HD"));
  assert.equal(hasUnicodeDash(innerSvg), false);
  const redPolygons = innerSvg.match(/stroke="#c43d2f"/g) || [];
  assert.equal(redPolygons.length, 2, "MultiPolygon historic district must render both polygon rings in locator SVG");

  const outerSvg = buildHoustonLoopLocatorSvg({
    lat: 29.559,
    lng: -95.09,
    mini: false,
    zoneLabel: `Clear Lake${CHAR_ENDASH}NASA`,
  });
  assert.ok(outerSvg.includes("BELTWAY 8"), "Outer Harris County property must auto-zoom out to Beltway 8");
  assert.equal(hasUnicodeDash(outerSvg), false);

  const miniSvg = buildHoustonLoopLocatorSvg({ lat: 29.774, lng: -95.40, mini: true });
  assert.ok(!miniSvg.includes("DOWNTOWN"), "Mini corner radar badge must omit text labels");

  // 5) Full HTML thumbnail dash sanitization across neighborhood, district, and ariaLabel
  const html = renderSpatialContextThumbnailHtml({
    lat: 29.7905,
    lng: -95.3976,
    polygonRings: polyB,
    neighborhood: `Heights${CHAR_ENDASH}West`,
    historicDistrict: `Houston Heights${CHAR_EMDASH}South${CHAR_HYPHEN}East`,
    overlaysData: overlaysFixture,
    theme: "dark",
    tone: "demolished",
    ariaLabel: `1894${CHAR_ENDASH}2026 Landmark${CHAR_EMDASH}Site`,
  });
  assert.ok(html.includes('class="atlas-spatial-thumb atlas-spatial-dark"'));
  assert.ok(html.includes('stroke-dasharray="4,2"'), "Demolished tone must render dashed footprint outline");
  assert.equal(hasUnicodeDash(html), false, "Rendered HTML must contain zero U+2010/U+2013/U+2014 dashes");
});

test("UT-SCT-5: ensureSpatialContextThumbnailsInitialized handles .is-pinned, mutual exclusion, .stack-below (including .inspector-body), .flip-up, outside-click, and Escape", () => {
  function makeClassList(initial = []) {
    const set = new Set(initial);
    return {
      add: (...ns) => ns.forEach((n) => set.add(n)),
      remove: (...ns) => ns.forEach((n) => set.delete(n)),
      toggle: (n, force) => {
        const next = force === undefined ? !set.has(n) : Boolean(force);
        if (next) set.add(n);
        else set.delete(n);
        return next;
      },
      contains: (n) => set.has(n),
    };
  }

  function makeThumbMock({ rect, scrollParentSelector = ".inspector-body", parentRect = null }) {
    const attrs = new Map([["aria-expanded", "false"]]);
    const scrollParent = parentRect
      ? {
          selector: scrollParentSelector,
          getBoundingClientRect: () => parentRect,
        }
      : null;

    const thumb = {
      classList: makeClassList(),
      getBoundingClientRect: () => rect,
      closest(sel) {
        if (sel.includes("[data-spatial-thumb]")) return thumb;
        if (
          scrollParent &&
          sel
            .split(",")
            .map((s) => s.trim())
            .includes(scrollParent.selector)
        ) {
          return scrollParent;
        }
        return null;
      },
      querySelector(sel) {
        if (sel === "[data-spatial-pip-toggle]") return toggleBtn;
        return null;
      },
    };
    const toggleBtn = {
      setAttribute: (k, v) => attrs.set(k, String(v)),
      getAttribute: (k) => attrs.get(k) ?? null,
      closest(sel) {
        if (sel === "[data-spatial-pip-toggle]") return toggleBtn;
        if (sel === "[data-spatial-thumb]") return thumb;
        return thumb.closest(sel);
      },
    };
    const closeBtn = {
      closest(sel) {
        if (sel === "[data-spatial-pip-close]") return closeBtn;
        if (sel === "[data-spatial-thumb]") return thumb;
        return null;
      },
    };
    const popoverInterior = {
      closest(sel) {
        if (sel === ".atlas-spatial-pip-popover") return popoverInterior;
        return null;
      },
    };
    return { thumb, toggleBtn, closeBtn, popoverInterior };
  }

  const t1 = makeThumbMock({
    // Docked inside a 390px-wide .inspector-body container on a 1440px screen:
    // rect.right (160) + 262 = 422 > parentRect.right (390) => MUST stack-below!
    // parentRect.bottom (500) - rect.top (360) = 140 < 220 & rect.top > 220 => MUST flip-up!
    rect: { left: 22, right: 160, top: 360, bottom: 448 },
    scrollParentSelector: ".inspector-body",
    parentRect: { left: 0, right: 390, top: 60, bottom: 500 },
  });
  const t2 = makeThumbMock({
    rect: { left: 100, right: 252, top: 100, bottom: 196 },
    scrollParentSelector: ".permit-watch-modal-body",
    parentRect: { left: 80, right: 960, top: 60, bottom: 820 },
  });

  const allThumbs = [t1.thumb, t2.thumb];
  const appendedStyles = [];
  const docListeners = new Map();
  const mockDoc = {
    head: {
      appendChild(el) {
        appendedStyles.push(el);
      },
    },
    getElementById(id) {
      return appendedStyles.find((s) => s.id === id) || null;
    },
    createElement(tag) {
      return { tagName: tag.toUpperCase(), id: "", textContent: "" };
    },
    addEventListener(type, fn) {
      const list = docListeners.get(type) || [];
      list.push(fn);
      docListeners.set(type, list);
    },
    querySelectorAll(sel) {
      if (sel === "[data-spatial-thumb].is-pinned") {
        return allThumbs.filter((el) => el.classList.contains("is-pinned"));
      }
      return [];
    },
  };

  ensureSpatialContextThumbnailsInitialized(mockDoc);
  ensureSpatialContextThumbnailsInitialized(mockDoc); // Idempotent second call
  assert.equal(appendedStyles.length, 1, "Style tag must be injected exactly once");
  assert.equal((docListeners.get("click") || []).length, 1, "Delegated click listener must bind once per Document");

  const fire = (type, target, extra = {}) => {
    let stopped = false;
    let prevented = false;
    const evt = {
      type,
      target,
      ...extra,
      preventDefault() {
        prevented = true;
      },
      stopPropagation() {
        stopped = true;
      },
    };
    for (const fn of docListeners.get(type) || []) fn(evt);
    return { stopped, prevented };
  };

  // 1) Click t1 toggle: pins t1 and computes .stack-below + .flip-up against .inspector-body bounds
  fire("click", t1.toggleBtn);
  assert.equal(t1.thumb.classList.contains("is-pinned"), true);
  assert.equal(t1.toggleBtn.getAttribute("aria-expanded"), "true");
  assert.equal(
    t1.thumb.classList.contains("stack-below"),
    true,
    "Thumbnail inside narrow .inspector-body must set .stack-below so popover does not clip horizontally"
  );
  assert.equal(t1.thumb.classList.contains("flip-up"), true, "Thumbnail near bottom of .inspector-body must set .flip-up");

  // 2) Click inside open popover keeps it pinned
  fire("click", t1.popoverInterior);
  assert.equal(t1.thumb.classList.contains("is-pinned"), true);

  // 3) Click t2 toggle: unpins t1 and pins t2 without stack-below/flip-up in wide modal top
  fire("click", t2.toggleBtn);
  assert.equal(t1.thumb.classList.contains("is-pinned"), false);
  assert.equal(t1.toggleBtn.getAttribute("aria-expanded"), "false");
  assert.equal(t2.thumb.classList.contains("is-pinned"), true);
  assert.equal(t2.thumb.classList.contains("stack-below"), false);
  assert.equal(t2.thumb.classList.contains("flip-up"), false);

  // 4) Escape key closes pinned popover and stops propagation so parent modal stays open
  const escRes = fire("keydown", mockDoc, { key: "Escape" });
  assert.equal(escRes.stopped, true);
  assert.equal(t2.thumb.classList.contains("is-pinned"), false);
  assert.equal(t2.toggleBtn.getAttribute("aria-expanded"), "false");

  // 5) Re-pin t2, then close via [data-spatial-pip-close], then re-pin and close via outside click
  fire("click", t2.toggleBtn);
  assert.equal(t2.thumb.classList.contains("is-pinned"), true);
  fire("click", t2.closeBtn);
  assert.equal(t2.thumb.classList.contains("is-pinned"), false);

  fire("click", t2.toggleBtn);
  fire("click", { closest: () => null });
  assert.equal(t2.thumb.classList.contains("is-pinned"), false);
});

test("UT-SCT-6: HoustonAtlasApp demolished lifecycle filtering isolates demolished/whole-parcel-vacant sites while keeping standing #b1 homes with suppressed #b2 garages in Standing Threats", () => {
  const prevDoc = globalThis.document;
  const elements = new Map();
  const makeEl = (id) => {
    const el = {
      id,
      textContent: "",
      innerHTML: "",
      querySelectorAll: () => [],
      querySelector: () => null,
    };
    elements.set(id, el);
    return el;
  };
  for (const id of [
    "permit-watch-topbar-count",
    "permit-watch-layer-count",
    "pw-stat-critical",
    "pw-stat-watch",
    "pw-stat-coh",
    "pw-stat-dshs",
    "pw-pill-count-all",
    "pw-pill-count-critical",
    "pw-pill-count-watch",
    "pw-pill-count-demolished",
    "permit-watch-modal-list",
  ]) {
    makeEl(id);
  }

  const fakeApp = Object.create(HoustonAtlasApp.prototype);
  fakeApp.mapController = {
    curatedOverrides: {
      // Whole-parcel vacant lot override with _pre_suppress_geometry -> must demote alert to Demolished / Lost while retaining dashed footprint
      "0621100000014": {
        id: "0621100000014",
        hcad_num: "0621100000014",
        suppress_only: true,
        _pre_suppress_geometry: {
          type: "Polygon",
          coordinates: [
            [
              [-95.3711, 29.7919],
              [-95.3709, 29.7919],
              [-95.3709, 29.7921],
              [-95.3711, 29.7921],
              [-95.3711, 29.7919],
            ],
          ],
        },
      },
      // Sub-building #b2 garage suppressed -> MUST NOT demote standing #b1 main house!
      "0202150000018#b2": {
        id: "0202150000018#b2",
        building_id: "0202150000018#b2",
        hcad_num: "0202150000018",
        is_building_override: true,
        suppress_only: true,
      },
    },
    overlaysData: null,
    buildingsData: [],
    getPermitAlertsPayload: () => ({
      summary: { coh_permits_matched: 4, dshs_notifications_matched: 1 },
      alerts: [
        {
          hcad_num: "0202150000018",
          building_id: "0202150000018#b1",
          address: "1020 CORTLANDT ST",
          year_built: 1910,
          lat: 29.7895,
          lon: -95.3972,
          severity: "critical",
          has_demo_permit: true,
          badge: "Stage 2: Demo Permit Issued",
          coh_permits: [{ project_no: "26095535", date: "2026-11-10", permit_type: "SD" }],
          dshs_notifications: [],
        },
        {
          hcad_num: "0202150000018",
          building_id: "",
          address: "1020 CORTLANDT ST PARCEL LEVEL",
          year_built: 1910,
          lat: 29.7895,
          lon: -95.3972,
          severity: "watch",
          badge: "DSHS Abatement",
          coh_permits: [],
          dshs_notifications: [{ notification_number: "2026005999", start_date: "11/01/2026", end_date: "11/15/2026" }],
        },
        {
          hcad_num: "0621100000014",
          building_id: "0621100000014#b1",
          address: "1127 KEY ST",
          year_built: 1920,
          lat: 29.792,
          lon: -95.371,
          severity: "critical",
          badge: "Stage 2: Demo Permit Issued",
          coh_permits: [{ project_no: "26080001", date: "2026-10-15", permit_type: "SD" }],
          dshs_notifications: [],
        },
        {
          hcad_num: "0152300000001",
          building_id: "0152300000001",
          address: "2000 SMITH ST",
          building_name: "John Henry Kirby Mansion",
          year_built: 1894,
          lat: 29.7501,
          lon: -95.3749,
          severity: "critical",
          is_demolished: true,
          demolition_status: "demolished",
          lost_houston_id: "lost_kirby_mansion_1894",
          badge: "Demolished (Nov 2, 2026)",
          coh_permits: [{ project_no: "26090093", date: "2026-10-30", permit_type: "SD" }],
          dshs_notifications: [],
        },
      ],
    }),
  };

  globalThis.document = {
    getElementById: (id) => elements.get(id) || null,
  };
  try {
    fakeApp._syncPermitWatchCounts();
    assert.equal(elements.get("permit-watch-topbar-count").textContent, "2");
    assert.equal(elements.get("pw-pill-count-all").textContent, "2");
    assert.equal(elements.get("pw-pill-count-critical").textContent, "1");
    assert.equal(elements.get("pw-pill-count-watch").textContent, "1");
    assert.equal(elements.get("pw-pill-count-demolished").textContent, "2");

    fakeApp._renderPermitWatchModalList("all");
    const standingHtml = elements.get("permit-watch-modal-list").innerHTML;
    assert.ok(standingHtml.includes("1020 CORTLANDT ST"));
    assert.ok(standingHtml.includes("1020 CORTLANDT ST PARCEL LEVEL"));
    assert.ok(!standingHtml.includes("1127 KEY ST"), "Whole-parcel suppressed lot must not appear in Standing Threats");
    assert.ok(!standingHtml.includes("2000 SMITH ST"), "Demolished site must not appear in Standing Threats");
    assert.ok(standingHtml.includes('data-spatial-thumb="1"'));

    fakeApp._renderPermitWatchModalList("demolished");
    const demoHtml = elements.get("permit-watch-modal-list").innerHTML;
    assert.ok(demoHtml.includes("1127 KEY ST"));
    assert.ok(demoHtml.includes("2000 SMITH ST"));
    assert.ok(!demoHtml.includes("1020 CORTLANDT ST"));
    assert.ok(demoHtml.includes("permit-watch-item-card demolished"));

    // Inspector banner handoff target and tab-aligned counts
    const inspectorStandingBanner = fakeApp._buildInspectorPermitAlertCardHtml(
      fakeApp.mapController.getPermitAlertsPayload().alerts[0]
    );
    assert.ok(inspectorStandingBanner.includes('data-open-permit-watchlist="all"'));
    assert.ok(inspectorStandingBanner.includes("2 Standing Threats"));

    const inspectorDemoBanner = fakeApp._buildInspectorPermitAlertCardHtml(
      fakeApp.mapController.getPermitAlertsPayload().alerts[3]
    );
    assert.ok(inspectorDemoBanner.includes('class="inspector-permit-alert-card demolished"'));
    assert.ok(inspectorDemoBanner.includes('data-open-permit-watchlist="demolished"'));
    assert.ok(inspectorDemoBanner.includes("Open Demolished / Lost Archive (2)"));
    assert.ok(inspectorDemoBanner.includes('data-spatial-thumb="1"'));

    // Verify _pre_suppress_geometry fallback on whole-parcel suppressed 0621100000014 alert
    const suppressedRing = fakeApp._resolvePermitAlertFootprintRing(
      fakeApp.mapController.getPermitAlertsPayload().alerts[2]
    );
    assert.equal(suppressedRing.type, "Polygon");

    // Verify shared isDemolishedPermitAlert & AtlasMapController.permitAlertsFC parity
    assert.equal(
      isDemolishedPermitAlert({ demolition_status: "Demolished" }),
      true,
      "Case-insensitive demolition_status must classify as demolished"
    );
    const fakeCtrl = Object.create(AtlasMapController.prototype);
    fakeCtrl.curatedOverrides = fakeApp.mapController.curatedOverrides;
    fakeCtrl.map = null;
    fakeCtrl._indexPermitAlerts(fakeApp.mapController.getPermitAlertsPayload());
    assert.equal(
      fakeCtrl.permitAlertsFC.features.length,
      2,
      "permitAlertsFC map markers must exclude both static demolished and whole-parcel suppressed alerts"
    );
    assert.equal(fakeCtrl.permitAlertsByHcad.has("0621100000014"), true);
    assert.equal(fakeCtrl.permitAlertsByHcad.has("0152300000001"), true);
  } finally {
    if (prevDoc === undefined) delete globalThis.document;
    else globalThis.document = prevDoc;
  }
});

test("UT-SCT-7: cold-boot openPermitWatchModal preserves user-clicked [data-pw-filter] tab across async active_permit_alerts.json hydration", async () => {
  const prevDoc = globalThis.document;
  const prevFetch = globalThis.fetch;

  let allActive = false;
  let demoActive = false;
  const pillAll = {
    getAttribute: () => "all",
    classList: {
      toggle: (_cls, force) => {
        allActive = Boolean(force);
      },
      remove: () => {
        allActive = false;
      },
      add: () => {
        allActive = true;
      },
    },
  };
  const pillDemo = {
    getAttribute: () => "demolished",
    classList: {
      toggle: (_cls, force) => {
        demoActive = Boolean(force);
      },
      remove: () => {
        demoActive = false;
      },
      add: () => {
        demoActive = true;
      },
    },
  };

  const modalClasses = new Set(["hidden"]);
  const modalEl = {
    id: "permit-watch-modal",
    classList: {
      remove: (c) => modalClasses.delete(c),
      add: (c) => modalClasses.add(c),
      contains: (c) => modalClasses.has(c),
    },
    querySelectorAll: (sel) => (sel === "[data-pw-filter]" ? [pillAll, pillDemo] : []),
  };
  const listEl = {
    id: "permit-watch-modal-list",
    innerHTML: "",
    querySelectorAll: () => [],
  };
  const pillDemoCount = { id: "pw-pill-count-demolished", textContent: "0" };

  let indexedPayload = null;
  const fakeApp = Object.create(HoustonAtlasApp.prototype);
  fakeApp.mapController = {
    curatedOverrides: {},
    overlaysData: null,
    buildingsData: [],
    getPermitAlertsPayload: () => indexedPayload,
    _indexPermitAlerts: (data) => {
      indexedPayload = data;
    },
  };

  globalThis.document = {
    getElementById: (id) => {
      if (id === "permit-watch-modal") return modalEl;
      if (id === "permit-watch-modal-list") return listEl;
      if (id === "pw-pill-count-demolished") return pillDemoCount;
      return null;
    },
  };

  let resolveFetch = null;
  globalThis.fetch = () =>
    new Promise((resolve) => {
      resolveFetch = resolve;
    });

  try {
    // Open initially on Standing Threats ("all"), then switch to "demolished" before fetch resolves
    fakeApp.openPermitWatchModal("all");
    assert.equal(allActive, true);
    assert.equal(fakeApp._activePermitWatchSeverity, "all");
    assert.ok(fakeApp._earlyPermitWatchPromise, "In-flight early fetch promise must be exposed");

    // Simulate user clicking [data-pw-filter="demolished"] tab while fetch is still pending
    pillAll.classList.remove("active");
    pillDemo.classList.add("active");
    fakeApp._renderPermitWatchModalList("demolished");
    assert.equal(demoActive, true);
    assert.equal(fakeApp._activePermitWatchSeverity, "demolished");
    assert.ok(listEl.innerHTML.includes("No confirmed-demolished structures"));

    const pendingPromise = fakeApp._earlyPermitWatchPromise;
    resolveFetch({
      ok: true,
      json: async () => ({
        summary: { coh_permits_matched: 2, dshs_notifications_matched: 0 },
        alerts: [
          {
            hcad_num: "0202150000018",
            address: "1020 CORTLANDT ST",
            year_built: 1910,
            lat: 29.7895,
            lon: -95.3972,
            severity: "critical",
            has_sewer_disc_plumbing: true,
            coh_permits: [{ project_no: "26095535", date: "2026-11-05", permit_type: "12" }],
            dshs_notifications: [],
          },
          {
            hcad_num: "0152300000001",
            address: "2000 SMITH ST",
            building_name: "John Henry Kirby Mansion",
            year_built: 1894,
            lat: 29.7501,
            lon: -95.3749,
            severity: "critical",
            is_demolished: true,
            demolition_status: "demolished",
            lost_houston_id: "lost_kirby_mansion_1894",
            coh_permits: [{ project_no: "26090093", date: "2026-10-30", permit_type: "SD" }],
            dshs_notifications: [],
          },
        ],
      }),
    });
    await pendingPromise;
    assert.equal(fakeApp._earlyPermitWatchPromise, null);
    assert.equal(pillDemoCount.textContent, "1");
    assert.ok(
      listEl.innerHTML.includes("2000 SMITH ST") && !listEl.innerHTML.includes("1020 CORTLANDT ST"),
      "Modal list must stay on user-selected 'demolished' tab after async fetch resolves"
    );
  } finally {
    if (prevDoc === undefined) delete globalThis.document;
    else globalThis.document = prevDoc;
    if (prevFetch === undefined) delete globalThis.fetch;
    else globalThis.fetch = prevFetch;
  }
});
