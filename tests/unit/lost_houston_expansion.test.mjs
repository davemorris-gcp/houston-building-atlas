import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  buildLandmarksFilterExpression,
  buildLostHoustonFilterExpression,
  DEFAULT_FILTER_STATE,
  featureMatchesLostHoustonFilter,
} from "../../app/js/filterStore.js";
import {
  buildPortfolioDataset,
  FEATURED_PORTFOLIO_PRESETS,
} from "../../app/js/guidedChaptersAndPortfolios.js";
import {
  AtlasMapController,
  LOST_HOUSTON_LABEL_TIERS,
  LOST_HOUSTON_RANK_EXPR,
} from "../../app/js/mapController.js";
import { resolveSanbornMaterialColors } from "../../app/js/palettes.js";
import {
  getCuratedBuildingPhotos,
  loadCuratedPhotosIndex,
  resolveBuildingPhotos,
} from "../../app/js/photoService.js";
import { decodeScorecardRecord } from "../../app/js/preservationOrganizer.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "../..");
const DATA_DIR = path.join(REPO_ROOT, "app", "public", "data");

function readDataJson(relPath) {
  return JSON.parse(fs.readFileSync(path.join(DATA_DIR, relPath), "utf8"));
}

/**
 * Minimal recursive evaluator for the subset of MapLibre GL Style Specification
 * filter expressions emitted by `buildLostHoustonFilterExpression`.
 */
function evalMapLibreExpr(expr, props) {
  if (!Array.isArray(expr)) return expr;
  const [op, ...args] = expr;
  switch (op) {
    case "all":
      return args.every((a) => Boolean(evalMapLibreExpr(a, props)));
    case "any":
      return args.some((a) => Boolean(evalMapLibreExpr(a, props)));
    case "!":
      return !evalMapLibreExpr(args[0], props);
    case "has":
      return (
        Object.prototype.hasOwnProperty.call(props, args[0]) &&
        props[args[0]] !== null &&
        props[args[0]] !== undefined
      );
    case "get":
      return props[args[0]];
    case "coalesce":
      for (const a of args) {
        const v = evalMapLibreExpr(a, props);
        if (v !== null && v !== undefined) return v;
      }
      return null;
    case "boolean":
      for (const a of args) {
        const v = evalMapLibreExpr(a, props);
        if (typeof v === "boolean") return v;
      }
      return false;
    case "case": {
      for (let i = 0; i + 1 < args.length; i += 2) {
        if (evalMapLibreExpr(args[i], props)) {
          return evalMapLibreExpr(args[i + 1], props);
        }
      }
      return evalMapLibreExpr(args[args.length - 1], props);
    }
    case "in": {
      const needle = evalMapLibreExpr(args[0], props);
      const haystack = evalMapLibreExpr(args[1], props);
      return Array.isArray(haystack) ? haystack.includes(needle) : false;
    }
    case "literal":
      return args[0];
    case "to-number": {
      for (const a of args) {
        const v = Number(evalMapLibreExpr(a, props));
        if (!Number.isNaN(v)) return v;
      }
      return 0;
    }
    case "==":
      return evalMapLibreExpr(args[0], props) === evalMapLibreExpr(args[1], props);
    case "!=":
      return evalMapLibreExpr(args[0], props) !== evalMapLibreExpr(args[1], props);
    case "<":
      return evalMapLibreExpr(args[0], props) < evalMapLibreExpr(args[1], props);
    case "<=":
      return evalMapLibreExpr(args[0], props) <= evalMapLibreExpr(args[1], props);
    case ">":
      return evalMapLibreExpr(args[0], props) > evalMapLibreExpr(args[1], props);
    case ">=":
      return evalMapLibreExpr(args[0], props) >= evalMapLibreExpr(args[1], props);
    default:
      throw new Error(`Unsupported MapLibre expression op in unit evaluator: ${op}`);
  }
}

function assertNoNestedZoomInExpr(expr, depth = 0) {
  if (!Array.isArray(expr)) return;
  const [op, ...args] = expr;
  if (op === "zoom" && depth > 0) {
    throw new Error(`Illegal nested ["zoom"] inside composite MapLibre filter expression: ${JSON.stringify(expr)}`);
  }
  for (const child of args) {
    assertNoNestedZoomInExpr(child, depth + 1);
  }
}

test("UT-1, UT-2, UT-4 & UT-7: Expanded 622-structure Lost Houston schema, COH landmarks, and 16 Gone NRHP entries satisfy all geometric and temporal invariants", () => {
  const overlays = readDataJson("overlays.json");
  const searchIndex = readDataJson("search_index.json");
  const auditSummary = readDataJson("historical_audit_summary.json");
  const lostFeatures = overlays.lost_houston?.features || [];

  assert.equal(lostFeatures.length, 622);
  assert.equal(auditSummary.reconciled_fixes?.lost_houston_features_audited, 622);
  assert.equal(auditSummary.reconciled_fixes?.lost_houston_schema_errors, 0);
  assert.equal(auditSummary.reconciled_fixes?.lost_houston_extant_collision_errors, 0);
  assert.equal(auditSummary.reconciled_fixes?.landmarks_marked_demolished, 8);

  const validProvenances = new Set([
    "surveyed_sanborn_or_parcel",
    "surveyed_catalog",
    "ms_osm_pre_demo_footprint",
    "ghost_parcel_aligned",
    "ghost_street_aligned",
  ]);
  const seenIds = new Set();
  const rankCounts = { 1: 0, 2: 0, 3: 0, 4: 0 };

  for (const feat of lostFeatures) {
    const p = feat.properties || {};
    assert.match(String(p.id || ""), /^lost_[A-Za-z0-9_]+$/);
    assert.equal(seenIds.has(p.id), false, `Duplicate Lost Houston id ${p.id}`);
    seenIds.add(p.id);

    assert.equal(Number.isInteger(p.year_built), true, `${p.id} year_built must be an integer, got ${JSON.stringify(p.year_built)}`);
    assert.equal(Number.isInteger(p.year_demolished), true, `${p.id} year_demolished must be an integer, got ${JSON.stringify(p.year_demolished)}`);
    assert.ok(p.year_built >= 1836 && p.year_built <= 1975, `${p.id} out-of-range year_built ${p.year_built}`);
    assert.ok(p.year_demolished >= p.year_built && p.year_demolished <= 2026, `${p.id} inverted/invalid lifespan ${p.year_built}-${p.year_demolished}`);
    assert.ok(String(p.year_built_display || "").length >= 4, `${p.id} missing year_built_display`);
    assert.ok(String(p.year_demolished_display || "").length >= 4, `${p.id} missing year_demolished_display`);
    assert.ok(String(p.lifespan_label || "").includes(String(p.year_built)), `${p.id} lifespan_label missing year_built`);
    assert.ok(validProvenances.has(p.footprint_provenance), `${p.id} invalid footprint_provenance '${p.footprint_provenance}'`);
    assert.ok([1, 2, 3, 4].includes(p.symbol_rank), `${p.id} invalid symbol_rank ${p.symbol_rank}`);
    rankCounts[p.symbol_rank] += 1;

    if (p.hcad_num) {
      assert.match(String(p.hcad_num), /^\d{13}$/, `${p.id} non-13-digit hcad_num '${p.hcad_num}'`);
    }
    assert.equal(feat.geometry?.type, "Polygon", `${p.id} must be a Polygon`);
    assert.equal(/\s{2,}/.test(String(p.address || "")), false, `${p.id} address has uncollapsed spaces: '${p.address}'`);
    assert.equal(/\b\d+(?:St|Nd|Rd|Th)\b/.test(String(p.address || "")), false, `${p.id} address has uppercase ordinal: '${p.address}'`);
    assert.equal(/^0(?:\b|\s)/.test(String(p.address || "")), false, `${p.id} address has stripped 0 street number: '${p.address}'`);
    assert.equal(/\b\d+(?:St|Nd|Rd|Th)\b/.test(String(p.name || "")), false, `${p.id} name has uppercase ordinal: '${p.name}'`);
    assert.equal(/^0\s/.test(String(p.name || "")), false, `${p.id} name has stripped 0 street number: '${p.name}'`);
    const ring = feat.geometry.coordinates[0];
    assert.ok(Array.isArray(ring) && ring.length >= 4, `${p.id} invalid exterior ring`);
    assert.deepEqual(ring[0], ring[ring.length - 1], `${p.id} exterior ring must be closed`);
    for (const [lng, lat] of ring) {
      assert.ok(lng >= -96.0 && lng <= -94.8 && lat >= 29.4 && lat <= 30.2, `${p.id} vertex outside Houston MSA bounds`);
      const lngDp = (String(lng).split(".")[1] || "").length;
      const latDp = (String(lat).split(".")[1] || "").length;
      assert.ok(lngDp <= 6 && latDp <= 6, `${p.id} coordinate exceeds 6 decimal places: [${lng}, ${lat}]`);
    }
  }

  assert.deepEqual(rankCounts, { 1: 92, 2: 51, 3: 48, 4: 431 });

  // Verify shared Sanborn material palette resolver normalizes all 7 production keys + aliases (High #3)
  assert.equal(resolveSanbornMaterialColors("wood_frame").color, "#FACC15");
  assert.equal(resolveSanbornMaterialColors("frame").color, "#FACC15");
  assert.equal(resolveSanbornMaterialColors("brick").color, "#F472B6");
  assert.equal(resolveSanbornMaterialColors("stone").color, "#60A5FA");
  assert.equal(resolveSanbornMaterialColors("stone_concrete").color, "#60A5FA");
  assert.equal(resolveSanbornMaterialColors("iron_metal").color, "#CBD5E1");
  assert.equal(resolveSanbornMaterialColors("iron_steel").color, "#CBD5E1");
  assert.equal(resolveSanbornMaterialColors("iron").color, "#CBD5E1");
  assert.equal(resolveSanbornMaterialColors("fireproof").color, "#A3B18A");
  for (const feat of lostFeatures) {
    const p = feat.properties;
    const mat = resolveSanbornMaterialColors(p.sanborn_material);
    if (p.sanborn_material && p.sanborn_material !== "brick") {
      assert.notEqual(mat.color, "#F472B6", `${p.id} material '${p.sanborn_material}' fell back to pink brick`);
    }
  }

  // UT-1: Verify COH Landmarks overlay isolates all 8 demolished landmarks cleanly from extant buildings (High #1)
  const lmFeatures = overlays.landmarks?.features || [];
  const lmFilterDefault = buildLandmarksFilterExpression(DEFAULT_FILTER_STATE);
  let lmDemoCount = 0;
  let lmDemoSiteHcadCount = 0;
  for (const lf of lmFeatures) {
    const lp = lf.properties || {};
    if (lp.hcad_num) {
      assert.match(String(lp.hcad_num), /^\d{13}$/, `Landmark ${lp.id} contains non-13-digit hcad_num '${lp.hcad_num}'`);
    }
    if (lp.demolished) {
      lmDemoCount += 1;
      assert.equal(lp.hcad_num, "", `Demolished landmark ${lp.id} must clear active hcad_num`);
      if (lp.site_hcad_num) {
        assert.match(String(lp.site_hcad_num), /^\d{13}$/, `Demolished landmark ${lp.id} invalid site_hcad_num`);
        lmDemoSiteHcadCount += 1;
      } else {
        assert.equal(lp.id, "coh_lm_232", `Only cleared Fourth Ward landmark coh_lm_232 may omit site_hcad_num (got ${lp.id})`);
      }
      assert.equal(lp.protection_tier, "lm_demolished", `Demolished landmark ${lp.id} wrong protection_tier`);
      assert.equal(lp.city_tax_exemption_eligible, false, `Demolished landmark ${lp.id} cannot be tax-exemption eligible`);
      assert.equal(Boolean(evalMapLibreExpr(lmFilterDefault, lp)), false, `Demolished landmark ${lp.id} must be hidden by default landmark filter`);
      if (lp.id !== "coh_lm_081") {
        assert.equal(
          (lp.alt_names || []).some((a) => String(a).toUpperCase().includes("JEFFERSON DAVIS HOSPITAL")),
          false,
          `Demolished landmark ${lp.id} retained cross-joined Jefferson Davis Hospital alt_names`
        );
      }
    }
    if (lp.id === "coh_lm_089") {
      assert.notEqual(lp.demolished, true, "Extant 1206 Nance St (coh_lm_089) must not be marked demolished");
    }
  }
  assert.equal(lmDemoCount, 8);
  assert.ok(lmDemoSiteHcadCount >= 7, `Expected >= 7 demolished landmarks with 13-digit site_hcad_num, got ${lmDemoSiteHcadCount}`);
  const siDemoLandmarks = searchIndex.filter((r) => r.type === "landmark" && r.demolished === true);
  assert.equal(siDemoLandmarks.length, 8);
  let siDemoSiteHcadCount = 0;
  for (const r of siDemoLandmarks) {
    assert.equal(r.hcad_num, "", `search_index demolished landmark ${r.id} must clear active hcad_num`);
    if (r.site_hcad_num) {
      assert.match(String(r.site_hcad_num), /^\d{13}$/);
      siDemoSiteHcadCount += 1;
    }
  }
  assert.ok(siDemoSiteHcadCount >= 7);
  const jd1937 = lostFeatures.find((f) => f.properties.id === "lost_jefferson_davis_hospital_1937");
  assert.ok(jd1937);
  assert.equal(jd1937.properties.coh_landmark_id, "coh_lm_081");

  // UT-2: All 16 Gone NRHP rows carry verified historic year_built in both overlays.json and search_index.json
  const nrhpDemo = (overlays.nrhp_properties?.features || []).filter((f) => f.properties?.is_demolished_nrhp);
  assert.equal(nrhpDemo.length, 16);
  const searchByNrhpId = new Map(
    searchIndex.filter((r) => r.type === "nrhp_property").map((r) => [r.id, r])
  );
  for (const nf of nrhpDemo) {
    const np = nf.properties;
    assert.equal(Number.isInteger(np.year_built) && np.year_built >= 1836 && np.year_built <= 1975, true, `NRHP ${np.refnum} missing backfilled year_built`);
    assert.equal(Number.isInteger(np.year_demolished) && np.year_demolished >= np.year_built, true, `NRHP ${np.refnum} missing year_demolished`);
    const si = searchByNrhpId.get(np.id);
    assert.ok(si, `Missing search_index row for ${np.id}`);
    assert.equal(si.year_built, np.year_built, `search_index year_built drift on ${np.id}`);
    assert.equal(si.year_demolished, np.year_demolished, `search_index year_demolished drift on ${np.id}`);
    if (String(np.refnum) === "85002771") {
      assert.equal(np.was_relocated, true);
      assert.equal(np.year_built, 1905);
    }
  }
});

test("UT-5: buildLostHoustonFilterExpression obeys MapLibre Style Spec, maintains 1:1 2D Canvas parity across all 622 records, and respects historical snapshot inequalities", () => {
  const overlays = readDataJson("overlays.json");
  const lostProps = (overlays.lost_houston?.features || []).map((f) => f.properties);

  const testStates = [
    { label: "default", state: { ...DEFAULT_FILTER_STATE } },
    { label: "range_1890_1930", state: { ...DEFAULT_FILTER_STATE, minYear: 1890, maxYear: 1930 } },
    { label: "decade_1910s", state: { ...DEFAULT_FILTER_STATE, selectedDecade: "1910" } },
    { label: "decade_1840s_includes_1837", state: { ...DEFAULT_FILTER_STATE, selectedDecade: "1840" } },
    { label: "decade_unknown_empty", state: { ...DEFAULT_FILTER_STATE, selectedDecade: "unknown" } },
    { label: "snap_1915", state: { ...DEFAULT_FILTER_STATE, historicalSnapshotMode: true, maxYear: 1915 } },
    { label: "snap_1935", state: { ...DEFAULT_FILTER_STATE, historicalSnapshotMode: true, maxYear: 1935 } },
    { label: "snap_1950", state: { ...DEFAULT_FILTER_STATE, historicalSnapshotMode: true, maxYear: 1950 } },
    { label: "snap_1965", state: { ...DEFAULT_FILTER_STATE, historicalSnapshotMode: true, maxYear: 1965 } },
    { label: "snap_2026", state: { ...DEFAULT_FILTER_STATE, historicalSnapshotMode: true, maxYear: 2026 } },
    { label: "playing_1925", state: { ...DEFAULT_FILTER_STATE, isPlaying: true, maxYear: 1925 } },
  ];

  for (const { label, state } of testStates) {
    const expr = buildLostHoustonFilterExpression(state);
    assertNoNestedZoomInExpr(expr);
    for (const p of lostProps) {
      const glMatch = Boolean(evalMapLibreExpr(expr, p));
      const canvasMatch = Boolean(featureMatchesLostHoustonFilter(p, state));
      assert.equal(
        canvasMatch,
        glMatch,
        `WebGL <-> 2D Canvas filter mismatch under '${label}' on ${p.id} (${p.year_built}-${p.year_demolished})`
      );
    }
  }

  // Exact boundary edge cases at snapshot year T = 1950:
  const snap1950 = { ...DEFAULT_FILTER_STATE, historicalSnapshotMode: true, maxYear: 1950 };
  const expr1950 = buildLostHoustonFilterExpression(snap1950);
  assert.equal(evalMapLibreExpr(expr1950, { id: "a", year_built: 1950, year_demolished: 1951 }), true);
  assert.equal(evalMapLibreExpr(expr1950, { id: "b", year_built: 1910, year_demolished: 1950 }), false);
  assert.equal(evalMapLibreExpr(expr1950, { id: "c", year_built: 1951, year_demolished: 1980 }), false);
  assert.equal(
    lostProps.filter((p) =>
      evalMapLibreExpr(buildLostHoustonFilterExpression({ ...DEFAULT_FILTER_STATE, historicalSnapshotMode: true, maxYear: 2026 }), p)
    ).length,
    0,
    "Zero demolished structures may appear as standing at present-day snapshot year 2026"
  );
  assert.equal(
    lostProps.filter((p) =>
      evalMapLibreExpr(buildLostHoustonFilterExpression({ ...DEFAULT_FILTER_STATE, selectedDecade: "unknown" }), p)
    ).length,
    0,
    "Decade 'unknown' filter must yield 0 Lost Houston matches"
  );

  // Verify MapLibre layer registration in mapController.js uses top-level minzoom tiers + symbol-sort-key
  assert.deepEqual(LOST_HOUSTON_RANK_EXPR, ["coalesce", ["to-number", ["get", "symbol_rank"]], 4]);
  assert.equal(LOST_HOUSTON_LABEL_TIERS.length, 3);
  assert.deepEqual(
    LOST_HOUSTON_LABEL_TIERS.map((t) => ({ id: t.id, minzoom: t.minzoom, maxRank: t.maxRank })),
    [
      { id: "lost-houston-label", minzoom: 12.5, maxRank: 1 },
      { id: "lost-houston-label-r2", minzoom: 14.5, maxRank: 2 },
      { id: "lost-houston-label-r34", minzoom: 15.8, maxRank: 4 },
    ]
  );
  for (const tier of LOST_HOUSTON_LABEL_TIERS) {
    assertNoNestedZoomInExpr(tier.clause);
  }
  const mcRaw = fs.readFileSync(path.join(REPO_ROOT, "app", "js", "mapController.js"), "utf8");
  assert.match(mcRaw, /for \(const tier of LOST_HOUSTON_LABEL_TIERS\)/);
  assert.match(mcRaw, /"symbol-sort-key":\s*LOST_HOUSTON_RANK_EXPR/);

  // Edge-case null/0/undefined parity across WebGL expression and Canvas2D JS filter
  const edgeCases = [
    { id: "edge_null_yb", year_built: null, year_demolished: 1960 },
    { id: "edge_zero_yb", year_built: 0, year_demolished: 1960 },
    { id: "edge_undef_yb", year_demolished: 1960 },
    { id: "edge_null_yd", year_built: 1910, year_demolished: null },
  ];
  for (const st of testStates) {
    const expr = buildLostHoustonFilterExpression(st);
    for (const ep of edgeCases) {
      assert.equal(
        Boolean(evalMapLibreExpr(expr, ep)),
        Boolean(featureMatchesLostHoustonFilter(ep, st)),
        `Edge-case WebGL vs JS filter drift on ${ep.id} under ${st.label}`
      );
    }
  }
});

test("UT-6: photoService strictly isolates all 184 Lost Houston archival photos (including 55 Rice Woodson Red Book IIIF plates) from extant HCAD/address lookups and skips Commons geosearch", async () => {
  const rawPhotosDoc = readDataJson("building_photos.json");
  const overlays = readDataJson("overlays.json");
  const lostById = new Map((overlays.lost_houston?.features || []).map((f) => [f.properties.id, f.properties]));

  const commonsCalls = [];
  const origFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    const u = String(url);
    if (u.includes("building_photos.json")) {
      return { ok: true, json: async () => rawPhotosDoc };
    }
    if (u.includes("commons.wikimedia.org")) {
      commonsCalls.push(u);
      return { ok: true, json: async () => ({ query: { pages: {} } }) };
    }
    throw new Error(`Unexpected network fetch in hermetic UT-6 test: ${u}`);
  };

  try {
    const idx = await loadCuratedPhotosIndex();
    const lostPhotos = idx.allPhotos.filter((p) => p.is_lost_houston);
    const rbPhotos = lostPhotos.filter((p) => String(p.building_id || "").startsWith("lost_rb1915_"));
    assert.equal(lostPhotos.length, 184);
    assert.equal(rbPhotos.length, 55);

    // Every Lost Houston photo must map to an actual feature in overlays.lost_houston
    for (const lp of lostPhotos) {
      assert.equal(lostById.has(lp.building_id), true, `Orphan is_lost_houston photo for ${lp.building_id}`);
      const hits = await getCuratedBuildingPhotos({ buildingId: lp.building_id });
      assert.ok(hits.length >= 1, `Expected byBuildingId lookup hit for ${lp.building_id}`);
    }

    // Zero is_lost_houston contamination in byHcad, byAddress, or byLandmark
    for (const [k, list] of idx.byHcad.entries()) {
      assert.equal(list.some((p) => p.is_lost_houston), false, `Lost photo leaked into byHcad[${k}]`);
    }
    for (const [k, list] of idx.byAddress.entries()) {
      assert.equal(list.some((p) => p.is_lost_houston), false, `Lost photo leaked into byAddress[${k}]`);
    }
    for (const [k, list] of idx.byLandmark.entries()) {
      assert.equal(list.some((p) => p.is_lost_houston), false, `Lost photo leaked into byLandmark[${k}]`);
    }

    // Querying a modern building on the Rice Hotel / Capitol Hotel parcel (0010570000001) must never return demolished Capitol Hotel photos
    const modernCapitolSitePhotos = await getCuratedBuildingPhotos({
      buildingId: "bld_modern_test",
      hcadNum: "0010570000001",
      address: "Main St at Texas Ave",
    });
    assert.equal(modernCapitolSitePhotos.some((p) => p.is_lost_houston), false);

    // Querying any photo-less lost_* structure MUST return [] even if its parcel/address matches an extant building photo
    const extantHcadWithPhoto = [...idx.byHcad.keys()][0];
    const extantAddrWithPhoto = [...idx.byAddress.keys()][0];
    const extantLmWithPhoto = [...idx.byLandmark.keys()][0];
    const photolessLostHits = await getCuratedBuildingPhotos({
      buildingId: "lost_td_0200910000030",
      hcadNum: extantHcadWithPhoto,
      address: extantAddrWithPhoto,
      landmarkName: extantLmWithPhoto,
    });
    assert.equal(
      photolessLostHits.length,
      0,
      "Photo-less lost_* ID must never fall through to standing-building byHcad/byAddress/byLandmark photos"
    );

    // Calling resolveBuildingPhotos on a Lost Houston record never fires Wikimedia Commons 42m geosearch
    const sampleRb = lostById.get(rbPhotos[0].building_id);
    const resolved = await resolveBuildingPhotos({
      buildingId: sampleRb.id,
      hcadNum: sampleRb.hcad_num || "",
      landmarkName: sampleRb.name,
      address: sampleRb.address,
      lat: sampleRb.lat,
      lng: sampleRb.lon,
    });
    assert.equal(commonsCalls.length, 0, `Expected 0 Wikimedia Commons calls for Lost Houston record, got ${commonsCalls.length}`);
    assert.ok(Array.isArray(resolved) && resolved.length >= 1);
  } finally {
    globalThis.fetch = origFetch;
  }
});

test("REG-3: findLostHoustonAtSite matches exact HCADs, secondary site_hcad_nums, and replatted multi-lot surveyed monuments without polluting adjacent lots", () => {
  const overlays = readDataJson("overlays.json");
  const fakeCtrl = Object.create(AtlasMapController.prototype);
  fakeCtrl.overlaysData = overlays;

  // 1. Primary 13-digit HCAD match
  const mPrimary = fakeCtrl.findLostHoustonAtSite({ hcadNum: "0010190000011" });
  assert.ok(mPrimary.some((p) => p.id === "lost_hutchins_house_1866"));

  // 2. Secondary site_hcad_nums match on multi-lot monument
  const mSecondary = fakeCtrl.findLostHoustonAtSite({ hcadNum: "0010190000008" });
  assert.ok(mSecondary.some((p) => p.id === "lost_hutchins_house_1866"));

  // 3. Replatted 2003 Fourth Ward townhome (1222 Arthur St, HCAD 1222470010018) inside 1905 West End Park surveyed L-polygon
  const mWestEnd = fakeCtrl.findLostHoustonAtSite({
    hcadNum: "1222470010018",
    lng: -95.377801,
    lat: 29.7566525,
  });
  assert.ok(
    mWestEnd.some((p) => p.id === "lost_west_end_park_1905"),
    "Replatted townhome inside surveyed_sanborn_or_parcel monument must link to lost_west_end_park_1905"
  );

  // 4. Neighboring parcels grazed by ghost/catalog/pre-demo/surveyed single-lot polygons MUST NOT false-match (Medium #7)
  const neighborProbes = [
    { hcadNum: "1324720010002", address: "1803 ASHLAND ST", lng: -95.39873, lat: 29.79893, desc: "1803 Ashland St adjacent to McDonald House ghost box" },
    { hcadNum: "0261930000002", address: "3406 MOUNT VERNON ST", lng: -95.39409, lat: 29.74203, desc: "3406 Mount Vernon St behind 3404 Yoakum catalog box" },
    { hcadNum: "0192030050019", address: "2601 ALABAMA ST", lng: -95.36585, lat: 29.72992, desc: "2601 Alabama St adjacent to 3703 Live Oak corner lot" },
    { hcadNum: "0521890000010", address: "2810 MCGOWEN ST", lng: -95.36415, lat: 29.74435, desc: "2810 McGowen St adjacent to 2814 McGowen teardown" },
    { hcadNum: "0372290000002", address: "3014 MCGOWEN ST", lng: -95.357321, lat: 29.735042, desc: "bld_006764 3014 McGowen St vs 2915 McGowen ghost_street_aligned box" },
    { hcadNum: "1211410020001", address: "1215 ANDREWS ST", lng: -95.380214, lat: 29.755636, desc: "bld_003396 1215 Andrews St vs 1417 Matthews ghost_street_aligned box" },
    { hcadNum: "1231980010012", address: "1214 CROSBY ST", lng: -95.376184, lat: 29.756168, desc: "bld_011829 1214 Crosby St vs 708 Andrews ghost_street_aligned box" },
    { hcadNum: "0010330000026", address: "305 TRAVIS ST", lng: -95.361611, lat: 29.762427, desc: "bld_006808 305 Travis St behind 910 Congress Ave Mistrot Emporium" },
  ];
  for (const probe of neighborProbes) {
    const res = fakeCtrl.findLostHoustonAtSite({
      hcadNum: probe.hcadNum,
      address: probe.address,
      lng: probe.lng,
      lat: probe.lat,
    });
    assert.equal(res.length, 0, `Unexpected neighbor cross-parcel spillover for ${probe.desc}: ${res.map((r) => r.id).join(",")}`);
  }
});

test("REG-4 & RP-3: buildPortfolioDataset deduplicates demolished COH landmarks exclusively into isLost works across architect and style portfolios", () => {
  const overlays = readDataJson("overlays.json");
  const searchIndex = readDataJson("search_index.json");
  const curatedOverrides = readDataJson("curated_overrides.json");

  const targets = [
    ...FEATURED_PORTFOLIO_PRESETS,
    { kind: "architect", query: "John F. Staub", label: "John F. Staub (full)" },
    { kind: "architect", query: "Charles Oliver", label: "Charles Oliver" },
    { kind: "style", query: "Colonial Revival", label: "Colonial Revival" },
  ];
  const requiresLost = new Set([
    "Alfred C. Finn",
    "Joseph Finger",
    "Sanguinet & Staats",
    "Russell Brown Company",
    "Charles Oliver",
    "Art Deco",
    "Craftsman",
    "Colonial Revival",
  ]);

  for (const preset of targets) {
    const portfolio = buildPortfolioDataset({
      kind: preset.kind,
      query: preset.query,
      searchIndex,
      curatedOverrides,
      overlaysData: overlays,
    });
    const works = portfolio.works || [];
    assert.ok(
      works.length >= 2,
      `Expected >= 2 portfolio works for ${preset.kind}:${preset.query}, got ${works.length}`
    );
    const lostWorks = works.filter((w) => w.isLost || w.isDemolished);
    const extantWorks = works.filter((w) => !w.isLost && !w.isDemolished);
    assert.equal(portfolio.stats.demolishedCount, lostWorks.length);
    assert.equal(portfolio.stats.extantCount, extantWorks.length);
    assert.equal(
      portfolio.stats.extantCount + portfolio.stats.demolishedCount,
      portfolio.stats.totalCount,
      `extantCount + demolishedCount !== totalCount for ${preset.query}`
    );
    assert.ok(extantWorks.length >= 1, `Expected >= 1 extant work in ${preset.query} portfolio`);
    if (requiresLost.has(preset.query)) {
      assert.ok(lostWorks.length >= 1, `Expected >= 1 demolished work in ${preset.query} portfolio`);
    }

    const demolishedCohIds = new Set(
      (overlays.landmarks?.features || [])
        .filter((f) => f.properties?.demolished === true)
        .map((f) => String(f.properties.id))
    );
    assert.equal(demolishedCohIds.size, 8);
    for (const ew of extantWorks) {
      assert.equal(
        demolishedCohIds.has(String(ew.featureId || "")),
        false,
        `Demolished COH landmark '${ew.title}' (${ew.featureId}) leaked into extantWorks of ${preset.query}`
      );
      assert.notEqual(
        ew.hcadNum,
        "Demolished",
        `Demolished COH sentinel hcadNum leaked into extantWorks of ${preset.query}`
      );
    }

    const extantTitles = new Set(extantWorks.map((w) => w.title.toLowerCase()));
    const extantHcads = new Set(
      extantWorks
        .map((w) => String(w.hcadNum || "").trim())
        .filter((h) => /^\d{13}$/.test(h))
    );
    for (const lw of lostWorks) {
      assert.match(String(lw.lostHoustonId || ""), /^lost_/);
      assert.ok(lw.sourceCohort, `${preset.query} lost work '${lw.title}' missing sourceCohort`);
      assert.ok(lw.sourceCohortLabel, `${preset.query} lost work '${lw.title}' missing sourceCohortLabel`);
      assert.equal(
        extantTitles.has(lw.title.toLowerCase()),
        false,
        `Demolished work '${lw.title}' in ${preset.query} portfolio duplicated as extant entry`
      );
      if (/^\d{13}$/.test(String(lw.hcadNum || ""))) {
        // Multi-building standing historic campuses sharing one parent parcel are allowed only when built before demoYr;
        // post-demolition replacement houses and demolished COH landmark parcels must never appear as extant works.
        if (lw.sourceCohort === "coh_landmark_demolished" || preset.query === "Russell Brown Company") {
          assert.equal(
            extantHcads.has(String(lw.hcadNum)),
            false,
            `Demolished work '${lw.title}' (${lw.hcadNum}) in ${preset.query} collided with extant work on same parcel`
          );
        }
      }
    }
    if (preset.query === "Charles Oliver") {
      assert.equal(portfolio.stats.extantCount, 9);
      assert.equal(portfolio.stats.demolishedCount, 1);
    }
  }

  // Both HPO spellings ("Lucien Carroll" and "Lucian Carroll") must resolve solely to the demolished 1926 COH landmark
  for (const q of ["Lucien Carroll", "Lucian Carroll"]) {
    const p = buildPortfolioDataset({
      kind: "architect",
      query: q,
      searchIndex,
      curatedOverrides,
      overlaysData: overlays,
    });
    assert.equal(p.stats.totalCount, 1, `Expected 1 work for ${q}`);
    assert.equal(p.stats.extantCount, 0, `Demolished Carroll House must not appear as extant under ${q}`);
    assert.equal(p.stats.demolishedCount, 1, `Expected 1 demolished work under ${q}`);
    assert.equal(p.works[0].lostHoustonId, "lost_coh_lm_lucian_carroll_house_1926");
  }
});

test("IT-2 & IT-4: Scorecards/HOLC rollups align cleanly and Source Cohort filter chips route 100% cohort-pure search results across all 5 cohorts", () => {
  const overlays = readDataJson("overlays.json");
  const searchIndex = readDataJson("search_index.json");
  const scorecardsDoc = readDataJson("neighborhood_scorecards.json");

  // IT-2: Verify decoded scorecards preserve independent lhc vs post-2020 dm/dmt/dmp40 permit counters
  const rawDistD = scorecardsDoc.scorecards.find((s) => s.id === "council_dist_d");
  const rawDistC = scorecardsDoc.scorecards.find((s) => s.id === "council_dist_c");
  const decD = decodeScorecardRecord(rawDistD, scorecardsDoc);
  const decC = decodeScorecardRecord(rawDistC, scorecardsDoc);
  assert.equal(decD.lost_houston_count ?? rawDistD.lhc, 73);
  assert.equal(decC.lost_houston_count ?? rawDistC.lhc, 178);
  assert.equal(rawDistD.dmt, 1644);
  assert.equal(rawDistC.dmt, 2641);

  const gradeSummary = overlays.holc_1937_grade_summary || [];
  const byGrade = Object.fromEntries(gradeSummary.map((g) => [g.grade, g.lost_houston_count]));
  assert.deepEqual(byGrade, { A: 44, B: 42, C: 139, D: 168 });

  // IT-4: Cohort chip filtering matches exact cohort counts without cross-cohort leakage
  const expectedByCohort = {
    curated_landmark: { label: "Curated Survey & Archival Landmark", count: 85 },
    coh_landmark_demolished: { label: "City of Houston Designated Landmark (Demolished)", count: 7 },
    nrhp_demolished: { label: "National Register Property (Demolished / Gone)", count: 15 },
    red_book_1915_vanished: { label: "1915 Red Book of Houston (Vanished Place)", count: 205 },
    historic_teardown_2020_2026: { label: "2020-2026 Pre-1940 Historic Teardown", count: 310 },
  };

  for (const [cohortKey, { label, count }] of Object.entries(expectedByCohort)) {
    const qKey = cohortKey.toLowerCase();
    const hitsByKey = searchIndex.filter(
      (item) =>
        String(item.source_cohort || "").toLowerCase() === qKey ||
        String(item.source_cohort_label || "").toLowerCase() === qKey
    );
    assert.equal(hitsByKey.length, count, `Expected ${count} search hits for chip '${cohortKey}'`);
    assert.equal(
      hitsByKey.every((item) => item.overlay_layer === "lost_houston" && item.source_cohort === cohortKey),
      true
    );

    const qLbl = label.toLowerCase();
    const hitsByLabel = searchIndex.filter(
      (item) => String(item.source_cohort_label || item.cohort_label || "").toLowerCase() === qLbl
    );
    assert.equal(hitsByLabel.length, count, `Expected ${count} search hits for label '${label}'`);
  }
});
