import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  DEFAULT_FILTER_STATE,
  SHARE_VIEW_PRESETS,
  buildFeatureFilterExpression,
  buildLandmarksFilterExpression,
  buildLostHoustonFilterExpression,
  buildRedBookFilterExpression,
  buildShareableUrl,
  createFilterStore,
  parseHashToState,
  serializeStateToHash,
} from "../../app/js/filterStore.js";

import {
  CITY_HISTORY_CHAPTERS,
  FEATURED_PORTFOLIO_PRESETS,
  buildPortfolioDataset,
} from "../../app/js/guidedChaptersAndPortfolios.js";

import {
  KNOWN_DEAD_URL_PATTERNS,
  buildCanonicalExternalUrls,
  validateExternalAtlasUrl,
} from "../../app/js/urlValidators.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DATA_DIR = path.resolve(__dirname, "..", "..", "app", "public", "data");

test("filterStore URL hash/query round-trip preserves presets, selections, overlays, chapters, and portfolios", () => {
  // 1. Preset parsing
  for (const preset of SHARE_VIEW_PRESETS) {
    const parsed = parseHashToState("", `?preset=${encodeURIComponent(preset.id)}`);
    assert.ok(parsed.patch, `Preset ${preset.id} should return a state patch`);
  }

  // 2. Chapter + Portfolio + HAHC filter + Landmark filter round-trip
  const store = createFilterStore();
  store.setState({
    colorMode: "preservation_status",
    minYear: 1890,
    maxYear: 1945,
    landmarkFilter: "waiver_lm",
    hahcFilter: "active_agenda",
    layers: {
      ...DEFAULT_FILTER_STATE.layers,
      landmarks: true,
      hahcTracker: true,
      redBook1915: true,
    },
    redBookCategory: "church",
  });

  const hash = serializeStateToHash(
    store.getState(),
    { lat: 29.75893, lng: -95.36909, zoom: 15.4, pitch: 35 },
    {
      selectedHcad: "0011470000001",
      activeChapterId: "preservation_movement_and_90day_waiver",
      activePortfolio: { kind: "architect", query: "Alfred C. Finn" },
    }
  );

  const reparsed = parseHashToState(`#${hash}`, "");
  assert.equal(reparsed.patch.colorMode, "preservation_status");
  assert.equal(reparsed.patch.minYear, 1890);
  assert.equal(reparsed.patch.maxYear, 1945);
  assert.equal(reparsed.patch.landmarkFilter, "waiver_lm");
  assert.equal(reparsed.patch.hahcFilter, "active_agenda");
  assert.equal(reparsed.patch.redBookCategory, "church");
  assert.equal(reparsed.patch.layers.hahcTracker, true);
  assert.equal(reparsed.selection?.hcadNum, "0011470000001");
  assert.equal(reparsed.activeChapterId, "preservation_movement_and_90day_waiver");
  assert.deepEqual(reparsed.activePortfolio, { kind: "architect", query: "Alfred C. Finn" });
  assert.equal(reparsed.viewport?.pitch, 35);

  // 3. Composite '#' campus override ID round-trip
  const campusUrl = buildShareableUrl(store.getState(), null, {
    baseOriginPath: "https://davemorris-gcp.github.io/houston-building-atlas/",
    useQueryParams: false,
    includeViewport: false,
    selectedFeatureId: "0421790000001#rice_1_will_rice_college_old_dorm_o",
  });
  const campusHash = campusUrl.slice(campusUrl.indexOf("#"));
  const parsedCampus = parseHashToState(campusHash, "");
  assert.equal(
    parsedCampus.selection?.featureId,
    "0421790000001#rice_1_will_rice_college_old_dorm_o"
  );

  // 4. Boundary dossier deep link (#bnd=layer:id)
  const parsedBnd = parseHashToState("#bnd=holc_1937_zones:holc_1937_d1", "");
  assert.equal(parsedBnd.selection?.boundaryLayer, "holc1937");
  assert.equal(parsedBnd.selection?.boundaryId, "holc_1937_d1");
});

test("filterStore MapLibre filter compilers produce valid expressions across modes", () => {
  const exprDefault = buildFeatureFilterExpression(DEFAULT_FILTER_STATE);
  assert.ok(Array.isArray(exprDefault) && exprDefault[0] === "any");

  const exprSnapshot = buildFeatureFilterExpression({
    ...DEFAULT_FILTER_STATE,
    maxYear: 1925,
    historicalSnapshotMode: true,
  });
  assert.equal(exprSnapshot[0], "all");

  const exprLostSnap = buildLostHoustonFilterExpression({
    ...DEFAULT_FILTER_STATE,
    maxYear: 1930,
    historicalSnapshotMode: true,
  });
  assert.equal(exprLostSnap[0], "all");

  const exprLmProtected = buildLandmarksFilterExpression({ landmarkFilter: "protected" });
  assert.equal(exprLmProtected[0], "in");

  const exprRbChurch = buildRedBookFilterExpression({ redBookCategory: "church" });
  assert.equal(exprRbChurch[0], "==");
});

test("Guided Chapters & Portfolio Engine builds deduplicated extant + Lost Houston works across all presets", () => {
  assert.equal(CITY_HISTORY_CHAPTERS.length, 6, "Expected 6 citywide guided chapters");
  for (const ch of CITY_HISTORY_CHAPTERS) {
    assert.ok(ch.id && ch.title && ch.era, "Each chapter must have id, title, and era");
    assert.ok(Array.isArray(ch.sites) && ch.sites.length >= 3, `Chapter ${ch.id} must have >= 3 exemplar sites`);
  }

  const searchIndex = JSON.parse(fs.readFileSync(path.join(DATA_DIR, "search_index.json"), "utf8"));
  const overlaysData = JSON.parse(fs.readFileSync(path.join(DATA_DIR, "overlays.json"), "utf8"));
  const curatedOverrides = JSON.parse(fs.readFileSync(path.join(DATA_DIR, "curated_overrides.json"), "utf8"));

  // Alfred C. Finn must include both extant landmarks (e.g. Gulf Building) and demolished Lost Houston works without duplicates
  const finn = buildPortfolioDataset({
    kind: "architect",
    query: "Alfred C. Finn",
    searchIndex,
    overlaysData,
    curatedOverrides,
  });
  assert.ok(finn.stats.totalCount >= 18, `Expected >= 18 works for Alfred C. Finn, got ${finn.stats.totalCount}`);
  assert.ok(finn.stats.extantCount >= 10, "Expected >= 10 extant works for Alfred C. Finn");
  assert.ok(finn.stats.demolishedCount >= 5, "Expected >= 5 demolished Lost Houston works for Alfred C. Finn");
  assert.equal(finn.stats.extantCount + finn.stats.demolishedCount, finn.stats.totalCount);

  // Ensure every featured portfolio preset yields non-empty works
  for (const preset of FEATURED_PORTFOLIO_PRESETS) {
    const ds = buildPortfolioDataset({
      kind: preset.kind,
      query: preset.query,
      searchIndex,
      overlaysData,
      curatedOverrides,
    });
    assert.ok(
      ds.stats.totalCount >= 3,
      `Featured portfolio preset '${preset.kind}:${preset.query}' returned only ${ds.stats.totalCount} works`
    );
  }
});

test("urlValidators rejects all 11 known retired/broken municipal & archive URL patterns and accepts canonical replacements", () => {
  assert.equal(KNOWN_DEAD_URL_PATTERNS.length, 11);

  const badUrls = [
    "https://maps.lib.utexas.edu/maps/sanborn/texas_cities_g-i.html",
    "https://www.cclerk.hctx.net/Applications/WebSearch/MR.aspx",
    "https://www.houstontx.gov/planning/HistoricPres/historic_districts.html",
    "https://www.houstontx.gov/planning/HistoricPres/HistoricPreservationManual/historic_preservation_manual.html",
    "https://www.houstontx.gov/planning/Commissions/docs_pdfs/hahc/2026/Oct_10_2026/C_Norhill_Redline_Draft_Oct_26.pdf",
    "http://digital.houstonlibrary.org/cdm/ref/collection/books/id/28",
    "https://atlas.thc.texas.gov/NR/pdfs/6001063/6001063.pdf",
    "https://projectrowhouses.org/cultural-district",
    "https://www.tshaonline.org/handbook/entries/broadacres-historic-district",
    "http://www.txrrhistory.com/towers/014/014.htm",
    "https://arcweb.hcad.org/parcel-viewer-v2.0/?hcad_num=undefined",
    "https://arcweb.hcad.org/parcel-viewer-v2.0/?hcad_num=12345",
    "https://atlas.thc.texas.gov/Details/84000388",
  ];

  for (const bad of badUrls) {
    const res = validateExternalAtlasUrl(bad);
    assert.equal(res.valid, false, `Expected URL validator to reject broken URL: ${bad}`);
  }

  const goodUrls = [
    "https://maps.lib.utexas.edu/maps/sanborn/h.html",
    "https://maps.lib.utexas.edu/maps/sanborn/g-i/txu-sanborn-houston-1924-273.jpg",
    "https://www.cclerk.hctx.net/Applications/WebSearch/MC.aspx",
    "https://www.houstontx.gov/planning/HistoricPres/District_Reference.html",
    "https://www.houstontx.gov/planning/Commissions/hahc_100826/C_Norhill_Redline_Draft_Oct_26.pdf",
    "https://cdm17006.contentdm.oclc.org/digital/collection/p17006coll12/id/545/rec/3",
  ];

  for (const good of goodUrls) {
    const res = validateExternalAtlasUrl(good);
    assert.equal(res.valid, true, `Expected URL validator to accept valid URL ${good}: ${res.reason}`);
  }

  const built = buildCanonicalExternalUrls({
    hcadNum: "0421790000001#rice_1",
    lat: 29.716485,
    lng: -95.399132,
    nrhpRefnum: "84000388",
  });
  assert.equal(built.hcadParcelViewerUrl, "https://arcweb.hcad.org/parcel-viewer-v2.0/?hcad_num=0421790000001");
  assert.equal(built.thcAtlasUrl, "https://atlas.thc.texas.gov/Details/2084000388");
  assert.equal(built.nrhpNominationPdfUrl, "https://atlas.thc.texas.gov/NR/pdfs/84000388/84000388.pdf");
  assert.equal(validateExternalAtlasUrl(built.thcAtlasUrl).valid, true);
  assert.equal(validateExternalAtlasUrl(built.hcadParcelViewerUrl).valid, true);
});
