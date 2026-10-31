import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "..", "..");
const APP_DIR = path.join(REPO_ROOT, "app");
const DATA_DIR = path.join(APP_DIR, "public", "data");

const manifestDoc = JSON.parse(
  fs.readFileSync(path.join(DATA_DIR, "pmtiles_manifest.json"), "utf8")
);
const ACTIVE_VER = String(manifestDoc.cache_bust || "").trim();
assert.match(ACTIVE_VER, /^\d{8}[a-z]?$/, "pmtiles_manifest.json must carry a valid cache_bust");

// Dynamically import app ES modules using the active ?v= cache_bust token so Node shares
// the exact same filterStore singleton instance as palettes.js, photoService.js,
// preservationOrganizer.js, and guidedChaptersAndPortfolios.js.
const filterStoreMod = await import(`../../app/js/filterStore.js?v=${ACTIVE_VER}`);
const palettesMod = await import(`../../app/js/palettes.js?v=${ACTIVE_VER}`);
const curatedEditsMod = await import(`../../app/js/curatedEdits.js?v=${ACTIVE_VER}`);
const organizerMod = await import(`../../app/js/preservationOrganizer.js?v=${ACTIVE_VER}`);
const photoServiceMod = await import(`../../app/js/photoService.js?v=${ACTIVE_VER}`);
const guidedMod = await import(`../../app/js/guidedChaptersAndPortfolios.js?v=${ACTIVE_VER}`);

const {
  DEFAULT_ATLAS_MAX_YEAR,
  getAtlasMaxYear,
  setAtlasMaxYear,
  getAtlasMaxDecade,
  createFilterStore,
  buildFeatureFilterExpression,
  buildLostHoustonFilterExpression,
  featureMatchesFilter,
  featureMatchesLostHoustonFilter,
  parseHashToState,
  serializeStateToHash,
  computeStepTimeState,
  getShareViewPresets,
  SHARE_VIEW_PRESETS,
} = filterStoreMod;

const {
  ARCHIVAL_YEAR_STOPS,
  CLASSIC_EE_YEAR_STOPS,
  CURATED_TOURS,
  UNKNOWN_YEAR_COLOR,
  buildColorExpression,
  getEffectiveYearStops,
  getYearColorHex,
} = palettesMod;

const {
  MAX_STANDING_OVERRIDE_YEAR,
  normalizeCorrectionSubmission,
  parseOverridesFromSheetRows,
} = curatedEditsMod;

const {
  ORGANIZER_CACHE_BUST,
  decodeScorecardRecord,
  renderChapter42ModalHtml,
  renderScorecardDetailHtml,
  resolveCh42OrdinanceLifecycle,
} = organizerMod;

const { fetchCommonsPhotos, clearRegisteredPhotoExclusions } = photoServiceMod;
const { CITY_HISTORY_CHAPTERS } = guidedMod;

// Minimal deterministic MapLibre expression evaluator for buildFeatureFilterExpression
// and buildLostHoustonFilterExpression across all operators emitted by filterStore.js.
function evalMapLibreExpr(expr, props) {
  if (!Array.isArray(expr)) return expr;
  const op = expr[0];
  switch (op) {
    case "literal":
      return expr[1];
    case "get":
      return props[expr[1]];
    case "has":
      return Object.prototype.hasOwnProperty.call(props, expr[1]) && props[expr[1]] !== null && props[expr[1]] !== undefined;
    case "!":
      return !evalMapLibreExpr(expr[1], props);
    case "all":
      for (let i = 1; i < expr.length; i += 1) {
        if (!evalMapLibreExpr(expr[i], props)) return false;
      }
      return true;
    case "any":
      for (let i = 1; i < expr.length; i += 1) {
        if (evalMapLibreExpr(expr[i], props)) return true;
      }
      return false;
    case "==":
      return evalMapLibreExpr(expr[1], props) === evalMapLibreExpr(expr[2], props);
    case "!=":
      return evalMapLibreExpr(expr[1], props) !== evalMapLibreExpr(expr[2], props);
    case ">=":
      return evalMapLibreExpr(expr[1], props) >= evalMapLibreExpr(expr[2], props);
    case "<=":
      return evalMapLibreExpr(expr[1], props) <= evalMapLibreExpr(expr[2], props);
    case ">":
      return evalMapLibreExpr(expr[1], props) > evalMapLibreExpr(expr[2], props);
    case "<":
      return evalMapLibreExpr(expr[1], props) < evalMapLibreExpr(expr[2], props);
    case "in": {
      const needle = evalMapLibreExpr(expr[1], props);
      const haystack = evalMapLibreExpr(expr[2], props);
      return Array.isArray(haystack) ? haystack.includes(needle) : false;
    }
    case "to-number": {
      for (let i = 1; i < expr.length; i += 1) {
        const v = evalMapLibreExpr(expr[i], props);
        const n = Number(v);
        if (v !== null && v !== undefined && v !== "" && Number.isFinite(n)) return n;
      }
      return 0;
    }
    case "coalesce": {
      for (let i = 1; i < expr.length; i += 1) {
        const v = evalMapLibreExpr(expr[i], props);
        if (v !== null && v !== undefined) return v;
      }
      return null;
    }
    case "case": {
      for (let i = 1; i + 1 < expr.length; i += 2) {
        if (evalMapLibreExpr(expr[i], props)) {
          return evalMapLibreExpr(expr[i + 1], props);
        }
      }
      return evalMapLibreExpr(expr[expr.length - 1], props);
    }
    case "step": {
      const input = Number(evalMapLibreExpr(expr[1], props));
      let out = evalMapLibreExpr(expr[2], props);
      for (let i = 3; i + 1 < expr.length; i += 2) {
        const stopInput = Number(evalMapLibreExpr(expr[i], props));
        if (input >= stopInput) {
          out = evalMapLibreExpr(expr[i + 1], props);
        } else {
          break;
        }
      }
      return out;
    }
    default:
      throw new Error(`Unsupported MapLibre operator in evaluator: ${op}`);
  }
}

test("UT-7 (JS): decodeScorecardRecord & resolveCh42OrdinanceLifecycle expand cleanly for 2027 and 2030 roll years", (t) => {
  t.after(() => setAtlasMaxYear(DEFAULT_ATLAS_MAX_YEAR));

  setAtlasMaxYear(2027);
  const meta2027 = {
    data_tax_year: 2027,
    decade_bins: [
      1830, 1840, 1850, 1860, 1870, 1880, 1890, 1900, 1910, 1920,
      1930, 1940, 1950, 1960, 1970, 1980, 1990, 2000, 2010, 2020,
    ],
    demo_years: [2020, 2021, 2022, 2023, 2024, 2025, 2026, 2027],
    demo_latest_year_is_ytd: true,
    demo_2026_is_ytd: false,
  };
  const card2027 = decodeScorecardRecord(
    {
      id: "test_heights",
      ly: "historic_districts",
      nm: "Test Heights",
      bc: 120,
      pc: 115,
      rpc: 100,
      sfc: 90,
      dh: [0, 0, 0, 0, 0, 0, 2, 10, 25, 40, 20, 5, 4, 3, 2, 1, 1, 2, 2, 3],
      dm: [1, 0, 2, 1, 1, 0, 1, 3],
      dmt: 9,
      chp: 40,
      chx: 12,
      che: 2028,
    },
    meta2027
  );
  assert.ok(card2027);
  assert.equal(card2027.demolitionsByYear.length, 8);
  assert.equal(card2027.demolitionsByYear[7].year, 2027);
  assert.equal(card2027.demolitionsByYear[7].count, 3);
  assert.equal(card2027.demolitionsByYear[7].isYtd, true);
  assert.equal(card2027.demolitionsByYear[6].year, 2026);
  assert.equal(card2027.demolitionsByYear[6].isYtd, false);
  assert.equal(card2027.demoEndYear, 2027);
  assert.equal(card2027.demoHasYtd, true);
  assert.equal(card2027.demolitionsTotal, 9);

  // Ch. 42 ordinance approved 2009-05-01 (20-yr pre-2015-639 term -> expires 2029):
  // under atlasMaxYear=2027 it is strictly inside the [2027, 2032] 5-yr renewal horizon!
  const lc2027 = resolveCh42OrdinanceLifecycle({
    protection_type: "Special Minimum Lot Size (Ch. 42)",
    approval_date: "2009-05-01",
    ordinance: "Ord. 2009-400",
  });
  assert.equal(lc2027.expirationYear, 2029);
  assert.equal(lc2027.sunset_status, "expiring_soon");
  assert.equal(lc2027.renewalStatus, "expiring_soon");

  // 2030 decade rollover scorecard decode
  setAtlasMaxYear(2030);
  const meta2030 = {
    data_tax_year: 2030,
    decade_bins: [
      1830, 1840, 1850, 1860, 1870, 1880, 1890, 1900, 1910, 1920,
      1930, 1940, 1950, 1960, 1970, 1980, 1990, 2000, 2010, 2020, 2030,
    ],
    demo_years: [2020, 2021, 2022, 2023, 2024, 2025, 2026, 2027, 2028, 2029, 2030],
    demo_latest_year_is_ytd: true,
    demo_2026_is_ytd: false,
  };
  const card2030 = decodeScorecardRecord(
    {
      id: "montrose",
      ly: "neighborhoods",
      nm: "Montrose",
      bc: 25,
      dh: [0, 0, 0, 0, 0, 0, 0, 0, 2, 5, 4, 3, 2, 1, 1, 1, 1, 1, 1, 1, 2],
      dm: [0, 1, 0, 1, 0, 0, 1, 0, 1, 0, 2],
      dmt: 6,
    },
    meta2030
  );
  assert.equal(card2030.decadeHistogram.length, 21);
  assert.deepEqual(card2030.decadeHistogram[20], { decade: 2030, count: 2 });
  assert.equal(card2030.demolitionsByYear[10].year, 2030);
  assert.equal(card2030.demolitionsByYear[10].count, 2);
  assert.equal(card2030.demolitionsByYear[10].isYtd, true);
  assert.equal(card2030.demoEndYear, 2030);
  assert.equal(card2030.demolitionsTotal, 6);

  // Under atlasMaxYear=2030, an ordinance approved 2013-06-01 (expires 2033) enters the [2030, 2035] watch window
  const lc2030 = resolveCh42OrdinanceLifecycle({
    protection_type: "Special Minimum Lot Size (Ch. 42)",
    approval_date: "2013-06-01",
    ordinance: "Ord. 2013-500",
  });
  assert.equal(lc2030.expirationYear, 2033);
  assert.equal(lc2030.sunset_status, "expiring_soon");
  assert.equal(lc2030.renewalStatus, "expiring_soon");
});

test("UT-8 (JS): Lockstep ?v= cache_bust parity holds across pmtiles_manifest.json, HTML, and all app/js ES modules", () => {
  assert.equal(ORGANIZER_CACHE_BUST, ACTIVE_VER);

  const importOrFetchVerRe = /\?v=([0-9]{8}[a-z]?)\b/g;
  const checkFiles = [
    path.join(APP_DIR, "index.html"),
    path.join(APP_DIR, "plat-monitor.html"),
    ...fs
      .readdirSync(path.join(APP_DIR, "js"))
      .filter((f) => f.endsWith(".js"))
      .map((f) => path.join(APP_DIR, "js", f)),
  ];
  let totalMatches = 0;
  for (const filePath of checkFiles) {
    const raw = fs.readFileSync(filePath, "utf8");
    for (const match of raw.matchAll(importOrFetchVerRe)) {
      totalMatches += 1;
      assert.equal(
        match[1],
        ACTIVE_VER,
        `Cache-bust drift in ${path.relative(REPO_ROOT, filePath)}: found ?v=${match[1]}, expected ?v=${ACTIVE_VER}`
      );
    }
  }
  assert.ok(totalMatches >= 50, `Expected at least 50 ?v= anchors across app/, found ${totalMatches}`);
});

test("UT-9 (JS): Dynamic atlasMaxYear sync, cold-boot #maxY=2027 survival, 2030 decade stepper, and exhaustive Lost Houston GPU/CPU truth-table parity", (t) => {
  t.after(() => setAtlasMaxYear(DEFAULT_ATLAS_MAX_YEAR));

  assert.equal(DEFAULT_ATLAS_MAX_YEAR, 2026);
  assert.equal(getAtlasMaxYear(), 2026);
  assert.equal(getAtlasMaxDecade(), 2020);

  // 1a. Cold-boot Historical Snapshot deep link (#maxY=2027&snapshot=1) parsed BEFORE metadata resolves
  setAtlasMaxYear(2026);
  const storeColdSnap = createFilterStore(parseHashToState("#maxY=2027&snapshot=1").patch);
  assert.equal(storeColdSnap.getState().maxYear, 2026);
  assert.equal(storeColdSnap.getState().minYear, 1836);
  assert.equal(storeColdSnap.getState().historicalSnapshotMode, true);
  const reconciledRet = storeColdSnap.reconcileAtlasMaxYear(2027);
  assert.equal(typeof reconciledRet, "number");
  assert.equal(reconciledRet, 2027);
  assert.equal(storeColdSnap.getState().maxYear, 2027);
  assert.equal(storeColdSnap.getState().minYear, 1836);
  assert.equal(storeColdSnap.getState().historicalSnapshotMode, true);
  assert.match(serializeStateToHash(storeColdSnap.getState()), /snapshot=1/);

  // 1b. Cold-boot custom range deep link (#minY=1900&maxY=2027) ALSO survives async 2027 hydration!
  setAtlasMaxYear(2026);
  const storeColdRange = createFilterStore(parseHashToState("#minY=1900&maxY=2027").patch);
  assert.equal(storeColdRange.getState().maxYear, 2026);
  assert.equal(storeColdRange.getState().minYear, 1900);
  storeColdRange.reconcileAtlasMaxYear(2027);
  assert.equal(storeColdRange.getState().maxYear, 2027);
  assert.equal(storeColdRange.getState().minYear, 1900);

  // 2. Unpinned default store automatically tracks rollover 2026 -> 2027 -> 2030, while custom user pin #maxY=1950 stays 1950
  setAtlasMaxYear(2026);
  const storeDefault = createFilterStore(parseHashToState("").patch);
  const storePinned1950 = createFilterStore(parseHashToState("#maxY=1950").patch);
  assert.equal(storeDefault.getState().maxYear, 2026);
  assert.equal(storePinned1950.getState().maxYear, 1950);

  setAtlasMaxYear(2027);
  assert.equal(getAtlasMaxYear(), 2027);
  assert.equal(getAtlasMaxDecade(), 2020);
  assert.equal(storeDefault.getState().maxYear, 2027);
  assert.equal(storePinned1950.getState().maxYear, 1950);

  // resetFilters resets to dynamic getAtlasMaxYear() (2027), never frozen 2026
  storePinned1950.resetFilters();
  assert.equal(storePinned1950.getState().maxYear, 2027);

  // 3. Lazy SHARE_VIEW_PRESETS and CITY_HISTORY_CHAPTERS reflect dynamic 2027 and include 2027 footprints in filter expressions
  for (const preset of SHARE_VIEW_PRESETS) {
    assert.ok(preset.statePatch, `Preset ${preset.id} must resolve statePatch`);
    if (preset.id === "full_atlas_default" || preset.id === "good_brick_light" || preset.id === "landmarks_districts_light") {
      assert.equal(preset.statePatch.maxYear, 2027, `Preset ${preset.id} must expand to 2027`);
      const expr = buildFeatureFilterExpression({
        minYear: 1836,
        selectedDecade: "all",
        showUnknownYears: true,
        ...preset.statePatch,
      });
      assert.equal(
        evalMapLibreExpr(expr, { year_built: 2027, decade: 2020 }),
        true,
        `Preset ${preset.id} filter must keep 2027 structures visible`
      );
    }
  }
  assert.ok(CITY_HISTORY_CHAPTERS.length >= 6);
  assert.equal(CITY_HISTORY_CHAPTERS[CITY_HISTORY_CHAPTERS.length - 1].statePatch.maxYear, 2027);
  assert.equal(CITY_HISTORY_CHAPTERS[CITY_HISTORY_CHAPTERS.length - 1].era, "1979-2027");
  assert.equal({ ...CITY_HISTORY_CHAPTERS[CITY_HISTORY_CHAPTERS.length - 1].statePatch }.maxYear, 2027);

  // 4. 2030 decade rollover: getAtlasMaxDecade() === 2030, parseHashToState("#dec=2030"), and computeStepTimeState through 2030s
  setAtlasMaxYear(2030);
  assert.equal(getAtlasMaxDecade(), 2030);
  assert.equal(storeDefault.getState().maxYear, 2030);
  const parsedDec2030 = parseHashToState("#dec=2030");
  assert.equal(parsedDec2030.patch.selectedDecade, "2030");

  const stepInto2030 = computeStepTimeState(
    {
      minYear: 2020,
      maxYear: 2029,
      stepYears: 10,
      selectedDecade: "2020",
      historicalSnapshotMode: false,
      isPlaying: false,
    },
    1
  );
  assert.equal(stepInto2030.selectedDecade, "2030");
  assert.equal(stepInto2030.minYear, 2030);
  assert.equal(stepInto2030.maxYear, 2030);

  // Snapshot step caps at getAtlasMaxYear() === 2030
  const snapAtTop = computeStepTimeState(
    {
      minYear: 1836,
      maxYear: 2025,
      stepYears: 10,
      selectedDecade: "all",
      historicalSnapshotMode: true,
      isPlaying: false,
    },
    1
  );
  assert.equal(snapAtTop.maxYear, 2030);

  // 5. Exhaustive GPU vs CPU truth-table parity across 1836..2035 for Lost Houston filter expressions!
  for (const rollTop of [2026, 2027, 2031]) {
    setAtlasMaxYear(rollTop);
    const sampleFeatures = [
      { properties: { year_built: 1912, year_demolished: 1968 } },
      { properties: { year_built: 1925, year_demolished: 2026 } },
      { properties: { year_built: 1930, year_demolished: 2027 } },
      { properties: { year_built: 1948, year_demolished: 2030 } },
      { properties: { year_built: 1895 } }, // missing year_demolished -> defaults to getAtlasMaxYear()
      { properties: { year_built: 0, year_demolished: 1980 } }, // unknown built year
      { properties: {} }, // missing both year_built and year_demolished
    ];
    const testStates = [
      { minYear: 1836, maxYear: rollTop, selectedDecade: "all", showUnknownYears: true, historicalSnapshotMode: false, isPlaying: false },
      { minYear: 1836, maxYear: rollTop, selectedDecade: "all", showUnknownYears: false, historicalSnapshotMode: false, isPlaying: false },
      { minYear: 1920, maxYear: 2026, selectedDecade: "all", showUnknownYears: false, historicalSnapshotMode: false, isPlaying: false },
      { minYear: 2027, maxYear: rollTop, selectedDecade: "all", showUnknownYears: true, historicalSnapshotMode: false, isPlaying: false },
      { minYear: 2020, maxYear: Math.min(rollTop, 2029), selectedDecade: "2020", showUnknownYears: false, historicalSnapshotMode: false, isPlaying: false },
    ];
    for (let scrubY = 1836; scrubY <= 2035; scrubY += 7) {
      testStates.push({
        minYear: 1836,
        maxYear: scrubY,
        selectedDecade: "all",
        showUnknownYears: true,
        historicalSnapshotMode: true,
        isPlaying: false,
      });
      testStates.push({
        minYear: 1836,
        maxYear: scrubY,
        selectedDecade: "all",
        showUnknownYears: false,
        historicalSnapshotMode: false,
        isPlaying: true,
      });
    }
    if (rollTop >= 2030) {
      testStates.push({
        minYear: 2030,
        maxYear: rollTop,
        selectedDecade: "2030",
        showUnknownYears: false,
        historicalSnapshotMode: false,
        isPlaying: false,
      });
    }

    for (const st of testStates) {
      const gpuExpr = buildLostHoustonFilterExpression(st);
      for (const feat of sampleFeatures) {
        const gpuVal = Boolean(evalMapLibreExpr(gpuExpr, feat.properties));
        const cpuVal = Boolean(featureMatchesLostHoustonFilter(feat.properties, st));
        assert.equal(
          gpuVal,
          cpuVal,
          `GPU/CPU Lost Houston mismatch at rollTop=${rollTop}, state=${JSON.stringify(st)}, props=${JSON.stringify(feat.properties)}`
        );
      }
    }

    // Explicitly check vanished-in-2027 structure under historicalSnapshotMode when rollTop=2027:
    if (rollTop === 2027) {
      const pDemo2027 = { year_built: 1930, year_demolished: 2027 };
      const pMissingDemo = { year_built: 1895 };
      // Still standing in 2026 snapshot (< 2027), gone in 2027 snapshot (not > 2027)
      assert.equal(
        featureMatchesLostHoustonFilter(pDemo2027, {
          minYear: 1836,
          maxYear: 2026,
          selectedDecade: "all",
          showUnknownYears: true,
          historicalSnapshotMode: true,
          isPlaying: false,
        }),
        true
      );
      assert.equal(
        featureMatchesLostHoustonFilter(pDemo2027, {
          minYear: 1836,
          maxYear: 2027,
          selectedDecade: "all",
          showUnknownYears: true,
          historicalSnapshotMode: true,
          isPlaying: false,
        }),
        false
      );
      // Missing year_demolished evaluates dynamic fallback = 2027, so visible at maxYear=2026!
      assert.equal(
        featureMatchesLostHoustonFilter(pMissingDemo, {
          minYear: 1836,
          maxYear: 2026,
          selectedDecade: "all",
          showUnknownYears: true,
          historicalSnapshotMode: true,
          isPlaying: false,
        }),
        true
      );
      // During time-lapse playback (isPlaying: true) at maxYear=2026 under rollTop=2027,
      // snapshot mode must stay active (2026 < 2027), hiding a building already demolished in 2026!
      const pDemo2026 = { year_built: 1925, year_demolished: 2026 };
      const playingAt2026 = {
        minYear: 1836,
        maxYear: 2026,
        selectedDecade: "all",
        showUnknownYears: true,
        historicalSnapshotMode: false,
        isPlaying: true,
      };
      assert.equal(featureMatchesLostHoustonFilter(pDemo2026, playingAt2026), false);
      assert.equal(evalMapLibreExpr(buildLostHoustonFilterExpression(playingAt2026), pDemo2026), false);
      // Non-snapshot lifespan query capped at maxYear=2026 under rollTop=2027 must NOT short-circuit
      // to ["has", "year_built"]; a 2027 structure must be excluded when maxYear=2026!
      const pBuilt2027Demo2027 = { year_built: 2027, year_demolished: 2027 };
      const cappedAt2026 = {
        minYear: 1836,
        maxYear: 2026,
        selectedDecade: "all",
        showUnknownYears: true,
        historicalSnapshotMode: false,
        isPlaying: false,
      };
      assert.equal(featureMatchesLostHoustonFilter(pBuilt2027Demo2027, cappedAt2026), false);
      assert.equal(evalMapLibreExpr(buildLostHoustonFilterExpression(cappedAt2026), pBuilt2027Demo2027), false);
    }
  }
});

test("UT-9 (JS): Palettes, CuratedEdits, and PhotoService handle 2027..2035 roll years without gray fallback or negative age", async (t) => {
  const origFetch = globalThis.fetch;
  t.after(() => {
    globalThis.fetch = origFetch;
    clearRegisteredPhotoExclusions();
    setAtlasMaxYear(DEFAULT_ATLAS_MAX_YEAR);
  });

  // 1. Palettes color 2027, 2030, and 2035 with valid contemporary hex stops (NEVER UNKNOWN_YEAR_COLOR)
  for (const rollTop of [2027, 2031]) {
    setAtlasMaxYear(rollTop);
    for (const styleId of ["archival", "classic_ee"]) {
      const expr = buildColorExpression("year_built", styleId);
      const stops = getEffectiveYearStops(styleId);
      assert.ok(stops.length >= 9);
      assert.equal(
        stops[stops.length - 1].maxYear,
        rollTop,
        `Expected top palette stop maxYear=${rollTop} for style=${styleId}`
      );
      for (const yr of [2026, 2027, 2030, 2031, 2035]) {
        const gpuHex = evalMapLibreExpr(expr, { year_built: yr });
        const cpuHex = getYearColorHex(yr, styleId);
        assert.notEqual(gpuHex, UNKNOWN_YEAR_COLOR);
        assert.notEqual(cpuHex, UNKNOWN_YEAR_COLOR);
        assert.equal(gpuHex, cpuHex);
      }
      // Undated year 0 still maps to UNKNOWN_YEAR_COLOR
      assert.equal(evalMapLibreExpr(expr, { year_built: 0 }), UNKNOWN_YEAR_COLOR);
    }
  }

  // 2. CuratedEdits accepts 2027 and 2030 community corrections and sheet overrides up to MAX_STANDING_OVERRIDE_YEAR (2035)
  assert.equal(MAX_STANDING_OVERRIDE_YEAR, 2035);
  for (const yr of [2027, 2030]) {
    const sub = normalizeCorrectionSubmission({
      hcad_num: "0010010010001",
      address: "123 MAIN ST",
      current_year_built: 1920,
      suggested_year_built: yr,
      source_citation: "HCAD certified 2027/2030 new-construction record and COH final inspection certificate",
      submitter: "Preservation Tester",
    });
    assert.equal(sub.suggestedYr, yr);
    assert.equal(sub.isDemolishedVacant, false);
  }
  const sheetParsed = parseOverridesFromSheetRows([
    {
      hcad_num: "0010010010001",
      year_built: "2027",
      status: "Approved",
      source_type: "permit",
      citation: "2027 building permit",
    },
    {
      hcad_num: "0010010010002",
      year_built: "2030",
      status: "Approved",
      source_type: "permit",
      citation: "2030 building permit",
    },
  ]);
  assert.equal(sheetParsed["0010010010001"].year_built, 2027);
  assert.equal(sheetParsed["0010010010001"].decade, 2020);
  assert.equal(sheetParsed["0010010010002"].year_built, 2030);
  assert.equal(sheetParsed["0010010010002"].decade, 2030);

  // 3. PhotoService validates 2027-2030 EXIF & title dates while continuing to reject future street house numbers (e.g. 2039 Main St)
  setAtlasMaxYear(2027);
  clearRegisteredPhotoExclusions();
  globalThis.fetch = async () => ({
    ok: true,
    json: async () => ({
      query: {
        pages: {
          "1": {
            pageid: 1,
            title: "File:Gulf_Building_Facade_Restoration_2027.jpg",
            imageinfo: [
              {
                url: "https://upload.wikimedia.org/test/Gulf_Building_2027.jpg",
                thumburl: "https://upload.wikimedia.org/test/960px-Gulf_Building_2027.jpg",
                extmetadata: {
                  DateTimeOriginal: { value: "2027-08-14 10:22:00" },
                  ImageDescription: { value: "Gulf Building architecture facade exterior Houston" },
                },
              },
            ],
          },
          "2": {
            pageid: 2,
            title: "File:Gulf_Building_2039_Main_St_View.jpg",
            imageinfo: [
              {
                url: "https://upload.wikimedia.org/test/Gulf_Building_NoYear.jpg",
                thumburl: "https://upload.wikimedia.org/test/960px-Gulf_Building_NoYear.jpg",
                extmetadata: {
                  ImageDescription: { value: "Gulf Building historic architecture exterior seen from 2039 block" },
                },
              },
            ],
          },
        },
      },
    }),
  });
  const photos2027 = await fetchCommonsPhotos({
    lat: 29.758,
    lng: -95.364,
    hcadNum: "0010010010001",
    landmarkName: "Gulf Building",
    address: "712 MAIN ST",
  });
  assert.equal(photos2027.length, 2);
  const byFullUrl = new Map(photos2027.map((p) => [p.full_url, p]));
  assert.equal(byFullUrl.get("https://upload.wikimedia.org/test/Gulf_Building_2027.jpg").photo_year, 2027);
  assert.equal(byFullUrl.get("https://upload.wikimedia.org/test/Gulf_Building_NoYear.jpg").photo_year, null);

  // 4. Verify non-negative building age formula in app/js/app.js guarantees 0 yrs old (never -1 yrs old)
  const appJsSrc = fs.readFileSync(path.join(APP_DIR, "js", "app.js"), "utf8");
  assert.match(
    appJsSrc,
    /const\s+currentYear\s*=\s*getAtlasMaxYear\(\);\s*\r?\n\s*const\s+bldgAge\s*=\s*Math\.max\(0,\s*currentYear\s*-\s*yr\);/,
    "app.js bldgAge formula must clamp non-negative via currentYear = getAtlasMaxYear() and Math.max(0, currentYear - yr)"
  );
  setAtlasMaxYear(2027);
  assert.equal(Math.max(0, getAtlasMaxYear() - 2027), 0);
  assert.equal(Math.max(0, getAtlasMaxYear() - 2030), 0);
});

test("Post-Review (JS): Scorecard/Ch.42 templates, CURATED_TOURS era getter, ASCII palette dashes, and 2040+ Commons EXIF years scale dynamically", async (t) => {
  const origFetch = globalThis.fetch;
  t.after(() => {
    globalThis.fetch = origFetch;
    clearRegisteredPhotoExclusions();
    setAtlasMaxYear(DEFAULT_ATLAS_MAX_YEAR);
  });

  // 1. Issue #14: ASCII hyphens on static stop exports + dynamic CURATED_TOURS[0].era getter
  for (const stops of [ARCHIVAL_YEAR_STOPS, CLASSIC_EE_YEAR_STOPS]) {
    assert.equal(stops[stops.length - 1].maxYear, DEFAULT_ATLAS_MAX_YEAR);
    for (const s of stops) {
      assert.ok(!s.label.includes("\u2013") && !s.label.includes("\u2014"), `Non-ASCII dash in ${s.label}`);
    }
  }
  setAtlasMaxYear(2028);
  assert.equal(CURATED_TOURS[0].id, "good_brick_highlights");
  assert.equal(CURATED_TOURS[0].era, "1979-2028");
  setAtlasMaxYear(2032);
  assert.equal(CURATED_TOURS[0].era, "1979-2032");

  // 2. Issue #10: renderScorecardDetailHtml print footer & renderChapter42ModalHtml sunset horizon
  setAtlasMaxYear(2028);
  const meta2028 = {
    data_tax_year: 2028,
    generated_at: "2028-09-01T12:00:00Z",
    decade_bins: [
      1830, 1840, 1850, 1860, 1870, 1880, 1890, 1900, 1910, 1920,
      1930, 1940, 1950, 1960, 1970, 1980, 1990, 2000, 2010, 2020,
    ],
    demo_years: [2020, 2021, 2022, 2023, 2024, 2025, 2026, 2027, 2028],
    demo_latest_year_is_ytd: true,
    demo_2026_is_ytd: false,
  };
  const card2028 = decodeScorecardRecord(
    {
      id: "test_2028",
      ly: "historic_districts",
      nm: "Test 2028 District",
      bc: 50,
      pc: 48,
      rpc: 45,
      sfc: 40,
      dh: [0, 0, 0, 0, 0, 0, 0, 4, 10, 20, 8, 2, 2, 1, 1, 0, 0, 0, 1, 1],
      dm: [0, 0, 1, 0, 1, 0, 0, 1, 2],
      dmt: 5,
      chp: 20,
      chx: 4,
      che: 2030,
    },
    meta2028
  );
  const scHtml = renderScorecardDetailHtml(card2028, null, {
    asOfLabel: "Sep 2028",
    shareUrl: "https://davemorris-gcp.github.io/houston-building-atlas/#scorecard=historic_districts:test_2028",
  });
  assert.match(scHtml, /COH Demolition Permits \(2020-2028\)/);
  assert.doesNotMatch(scHtml, /\*2026 YTD through Oct 2026/);

  const dummyAssembly = {
    seedHcadNum: "0010010010001",
    hundredBlock: "100",
    targetStreetKey: "MAIN ST",
    seedParity: "odd",
    includeOpposingBlockface: false,
    items: [],
  };
  const dummyCalc = {
    isHistoricDistrict: false,
    historicDistrictName: "",
    items: [],
    excludedItems: [],
    segregatedReserves: [],
    eligibilityCheck: {
      sfrPct: 100,
      passesBlock60Pct: true,
      sfrCount: 4,
      denominatorCount: 4,
      excludedCivicReligiousCount: 0,
      passesArea80Pct: true,
    },
    smlsResult: {
      thresholdPct: 70,
      effectiveMinLotSqft: 5000,
      universeCount: 4,
      totalAreaSqft: 20000,
      rankedRows: [],
    },
    smblResult: {
      thresholdPct: 70,
      effectiveMinSetbackFt: 25.0,
      totalBuildingLineFt: 100.0,
      constructedLotCount: 4,
      excludedVacantOrDemolishedCount: 0,
      missingFootprintConstructedCount: 0,
      rankedRows: [],
    },
    petitionThresholds: {
      totalLots: 4,
      townhomeSplitVulnerableLots: 2,
      meetsDirectToCouncilFastTrack: false,
      directToCouncilArea51PctSqft: 10200,
      signedLandAreaSqft: 0,
      signedLandAreaPct: 0,
      signedOwnerCount: 0,
      ownershipBreakdown: { individualOwners: 4, familyTrustOwners: 0, corporateOwners: 0 },
      rescissionArea67PctSqft: 13400,
    },
  };
  const ch42Html = renderChapter42ModalHtml(
    dummyAssembly,
    dummyCalc,
    [
      {
        protectionType: "SMLS",
        ordinance: "2012-100",
        isRibbonGeometry: false,
        lifecycle: { sunset_status: "expiring_soon", expiration_label: "Sunsets 2032" },
      },
    ],
    { refYear: 2028 }
  );
  assert.match(ch42Html, /reach their 20-year statutory sunset by 2033/);
  assert.doesNotMatch(ch42Html, /sunset by 2031/);

  // Confirm app.js _applyRollYearMetadata dynamically rewrites scorecard-sort-select & scorecard-hub-table headers
  const appJsSrc = fs.readFileSync(path.join(APP_DIR, "js", "app.js"), "utf8");
  assert.match(appJsSrc, /document\.getElementById\("scorecard-sort-select"\)/);
  assert.match(appJsSrc, /Sort: Expiring Ch\. 42 Ordinances \(\$\{resolvedTopY\}-\$\{resolvedTopY \+ 5\}\)/);
  assert.match(appJsSrc, /Sort: Highest 2020-\$\{resolvedTopY\} Demolitions/);
  assert.match(appJsSrc, /Sunset \$\{String\(resolvedTopY\)\.slice\(-2\)\}-\$\{String\(resolvedTopY \+ 5\)\.slice\(-2\)\}/);

  // 3. Issue #15: PhotoService accepts 2040+ EXIF and title photo years when atlasMaxYear advances past 2039
  setAtlasMaxYear(2042);
  clearRegisteredPhotoExclusions();
  globalThis.fetch = async () => ({
    ok: true,
    json: async () => ({
      query: {
        pages: {
          "1": {
            pageid: 1,
            title: "File:Gulf_Building_2042_Restoration.jpg",
            imageinfo: [
              {
                url: "https://upload.wikimedia.org/test/Gulf_Building_2042.jpg",
                thumburl: "https://upload.wikimedia.org/test/960px-Gulf_Building_2042.jpg",
                extmetadata: {
                  DateTimeOriginal: { value: "2042:04:12 10:00:00" },
                  ImageDescription: { value: "Gulf Building exterior facade Houston" },
                },
              },
            ],
          },
          "2": {
            pageid: 2,
            title: "File:Gulf_Building_2059_Main_St_View.jpg",
            imageinfo: [
              {
                url: "https://upload.wikimedia.org/test/Gulf_Building_2059_Block.jpg",
                thumburl: "https://upload.wikimedia.org/test/960px-Gulf_Building_2059_Block.jpg",
                extmetadata: {
                  ImageDescription: { value: "Gulf Building architecture exterior viewed from 2059 block" },
                },
              },
            ],
          },
        },
      },
    }),
  });
  const photos2042 = await fetchCommonsPhotos({
    lat: 29.758,
    lng: -95.364,
    hcadNum: "0010010010001",
    landmarkName: "Gulf Building",
    address: "712 MAIN ST",
  });
  const map2042 = new Map(photos2042.map((p) => [p.full_url, p]));
  assert.equal(map2042.get("https://upload.wikimedia.org/test/Gulf_Building_2042.jpg").photo_year, 2042);
  assert.equal(map2042.get("https://upload.wikimedia.org/test/Gulf_Building_2059_Block.jpg").photo_year, null);

  // 4. Pass 2 Issues #5 & #7: semantic #sc-th-sunset / #sc-th-demos IDs and unified Lost Houston boundary hit-testing
  const indexHtmlSrc = fs.readFileSync(path.join(APP_DIR, "index.html"), "utf8");
  assert.match(indexHtmlSrc, /id="sc-th-sunset"/);
  assert.match(indexHtmlSrc, /id="sc-th-demos"/);
  assert.match(appJsSrc, /document\.getElementById\("sc-th-sunset"\)/);
  assert.match(appJsSrc, /document\.getElementById\("sc-th-demos"\)/);
  assert.doesNotMatch(appJsSrc, /thead\s+th:nth-child\([57]\)/);

  const mapControllerSrc = fs.readFileSync(path.join(APP_DIR, "js", "mapController.js"), "utf8");
  assert.match(
    mapControllerSrc,
    /if\s*\(entry\.overlayKey\s*===\s*"lost_houston"\)\s*\{\s*if\s*\(!featureMatchesLostHoustonFilter\(entry\.props,\s*state\)\)\s*continue;\s*\}/
  );

  // Edge states that previously drifted in mapController.js boundary hit-testing:
  setAtlasMaxYear(2027);
  // (a) selectedDecade === "unknown" hides dated Lost Houston entries
  assert.equal(
    featureMatchesLostHoustonFilter(
      { year_built: 1895, year_demolished: 1930 },
      { minYear: 1836, maxYear: 2027, selectedDecade: "unknown", showUnknownYears: true, historicalSnapshotMode: false, isPlaying: false }
    ),
    false
  );
  // (b) active time-lapse playback (isPlaying: true, maxYear < topY) enforces snapshot check (yd <= maxYear hidden)
  assert.equal(
    featureMatchesLostHoustonFilter(
      { year_built: 1895, year_demolished: 1930 },
      { minYear: 1836, maxYear: 1950, selectedDecade: "all", showUnknownYears: true, historicalSnapshotMode: false, isPlaying: true }
    ),
    false
  );
  // (c) historicalSnapshotMode at maxYear === getAtlasMaxYear() hides null-year_demolished entries (defaults to topY)
  assert.equal(
    featureMatchesLostHoustonFilter(
      { year_built: 1895 },
      { minYear: 1836, maxYear: 2027, selectedDecade: "all", showUnknownYears: true, historicalSnapshotMode: true, isPlaying: false }
    ),
    false
  );
});

test("Pass 3 (JS): Cold-boot pinned URL notification, standing featureMatchesFilter snapshot parity, & preset memoization", (t) => {
  t.after(() => setAtlasMaxYear(DEFAULT_ATLAS_MAX_YEAR));

  // 1. Pass 3 High #3: reconcileAtlasMaxYear MUST notify subscribers even when patchUpdate is empty
  for (const coldHash of ["#play=1&maxY=2026", "#maxY=2026", "#dec=1920"]) {
    setAtlasMaxYear(2026);
    const storePinned = createFilterStore(parseHashToState(coldHash).patch);
    let notifyCount = 0;
    let notifiedAtlasTopY = null;
    storePinned.subscribe(() => {
      notifyCount += 1;
      notifiedAtlasTopY = getAtlasMaxYear();
    });
    const resolved = storePinned.reconcileAtlasMaxYear(2027);
    assert.equal(resolved, 2027);
    assert.equal(
      notifyCount,
      1,
      `Expected listener notification on rollover for cold-boot hash ${coldHash}`
    );
    assert.equal(notifiedAtlasTopY, 2027);
  }

  // 2. Pass 3 Medium #9: 1:1 GPU vs CPU truth-table parity for standing buildings under historicalSnapshotMode
  setAtlasMaxYear(2027);
  const standingSamples = [
    { year_built: 0 },
    { year_built: 1838 },
    { year_built: 1895 },
    { year_built: 1924 },
    { year_built: 1955 },
    { year_built: 2026 },
    { year_built: 2027 },
  ];
  const standingStates = [];
  for (const snap of [false, true]) {
    for (const showUnk of [false, true]) {
      for (const dec of ["all", "unknown", "1840", "1920", "2020"]) {
        for (const [minY, maxY] of [
          [1836, 2027],
          [1900, 1950],
          [1930, 2026],
        ]) {
          standingStates.push({
            minYear: minY,
            maxYear: maxY,
            selectedDecade: dec,
            showUnknownYears: showUnk,
            historicalSnapshotMode: snap,
          });
        }
      }
    }
  }
  for (const st of standingStates) {
    const gpuExpr = buildFeatureFilterExpression(st);
    for (const props of standingSamples) {
      assert.equal(
        Boolean(featureMatchesFilter(props, st)),
        Boolean(evalMapLibreExpr(gpuExpr, props)),
        `Standing filter parity drift for state=${JSON.stringify(st)}, props=${JSON.stringify(props)}`
      );
    }
  }

  // 3. Pass 3 Low #11b: getShareViewPresets memoizes by atlasMaxYear and avoids hardcoded "1.51M" count
  setAtlasMaxYear(2027);
  const presetsA = getShareViewPresets();
  const presetsB = getShareViewPresets(2027);
  assert.equal(presetsA, presetsB);
  for (const preset of SHARE_VIEW_PRESETS) {
    assert.doesNotMatch(preset.description, /1\.51M/);
  }
  setAtlasMaxYear(2028);
  const presetsC = getShareViewPresets();
  assert.notEqual(presetsA, presetsC);
  assert.equal(presetsC[presetsC.length - 1].statePatch.maxYear, 2028);

  // 4. Pass 4 Medium #4: downward rollover clamps pinned maxYear & resets out-of-range >=2030 selectedDecade
  setAtlasMaxYear(2030);
  const storeDown = createFilterStore();
  storeDown.setState({ selectedDecade: "2030", minYear: 2030, maxYear: 2030 });
  storeDown.reconcileAtlasMaxYear(2027);
  assert.equal(storeDown.getState().selectedDecade, "all");
  assert.equal(storeDown.getState().minYear, 1836);
  assert.equal(storeDown.getState().maxYear, 2027);

  setAtlasMaxYear(2030);
  const storeUserPin = createFilterStore();
  storeUserPin.setState({ minYear: 1920, maxYear: 2030 });
  storeUserPin.reconcileAtlasMaxYear(2027);
  assert.equal(storeUserPin.getState().minYear, 1920);
  assert.equal(storeUserPin.getState().maxYear, 2027);
});
