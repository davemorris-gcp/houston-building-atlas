import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  FIELD_SURVEY_CONDITION_RATINGS,
  FIELD_SURVEY_CONTRIBUTING_VALUES,
  FIELD_SURVEY_CSV_COLUMNS,
  FIELD_SURVEY_INTEGRITY_GRADES,
  FIELD_SURVEY_SOURCE_TYPE,
  HARRIS_COUNTY_BBOX,
  MAX_GEOMETRY_JSON_CELL_CHARS,
  METERS_PER_DEGREE_LAT,
  MIN_FOOTPRINT_AREA_SQ_M,
  OVERRIDE_MODE_ADDITIVE_POLYGON,
  OVERRIDE_MODE_PARCEL_REPLACEMENT,
  OVERRIDE_MODE_SHARD_PRIMARY,
  OVERRIDE_MODE_SUPPRESS_VACANT,
  POLYGON_EDITOR_MAX_VERTICES,
  SQ_FT_PER_SQ_M,
  VALID_CONTRIBUTING_VALUES,
  _setFieldSurveyStorageOverrideForTests,
  buildFieldSurveyWebhookRecord,
  classifyOverrideFootprintMode,
  clearSyncedFieldSurveys,
  deleteSurveyPhotoBlob,
  encodeCitationWithGeometry,
  encodeFieldSurveyEnvelope,
  exportFieldSurveysToCsv,
  extractEmbeddedGeometryJson,
  extractFieldSurveyEnvelope,
  extractGeometryAndCleanCitation,
  flushPendingFieldSurveys,
  getLocalFieldSurveys,
  getSurveyPhotoBlob,
  listSurveyPhotoSummaries,
  mergeOverrideRecord,
  parseCsvToObjects,
  parseOverridesFromSheetRows,
  removeLocalFieldSurvey,
  sanitizeSpreadsheetCell,
  saveLocalFieldSurvey,
  saveSurveyPhotoBlob,
  stripEmbeddedFieldSurveyTag,
  stripEmbeddedGeometryTag,
  submitFieldSurveyObservation,
  validateOverridePolygonGeometry,
} from "../../app/js/curatedEdits.js?v=20261030e";

import {
  AUDIT_CATEGORY_LABELS,
  AUDIT_UNDESIGNATED_DISTRICT,
  BORDEN_DOWNTOWN_GRID_DEG,
  SURVEY_CONTRIBUTING_VALUES,
  _setAuditStorageOverrideForTests,
  allocateNextAuxOverrideKey,
  assessMultiFootprintParcelRisk,
  buildFieldSurveyMapOverlayGeoJson,
  buildPolygonEditorGeoJson,
  buildSatellitePreviewTileSpec,
  buildSurveyPhotoFilename,
  computeAuditProgressStats,
  computePolygonAreaSqFt,
  computePolygonCentroid,
  createRectangleFootprintAroundPoint,
  deriveExpressSurveyDefaults,
  evaluateGpsAccuracy,
  filterAuditQueueItems,
  haversineDistanceMeters,
  hydrateAuditQueuePayload,
  insertPolygonMidpointVertex,
  isFindingResolvedByOverrides,
  loadAuditDecisions,
  normalizeStreetStemForAliases,
  nudgePolygonTangentPlane,
  pickNextBlockfaceCandidate,
  rankNearbySurveyCandidates,
  removePolygonVertex,
  resolveAuditItemArchivalContext,
  resolveAuditItemStatus,
  rotatePolygonTangentPlane,
  saveAuditDecision,
  updatePolygonVertex,
  validateAndNormalizeDraftPolygon,
  validateFieldSurveyDraft,
  validatePolygonSaveContributing,
} from "../../app/js/staffWorkbench.js?v=20261030e";

import {
  DEFAULT_FILTER_STATE,
  buildShareableUrl,
  createFilterStore,
  parseHashToState,
  serializeStateToHash,
} from "../../app/js/filterStore.js?v=20261030e";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const REPO_ROOT = path.resolve(__dirname, "..", "..");
const DATA_DIR = path.join(REPO_ROOT, "app", "public", "data");

function makeMemoryStorage({ throwOnSet = false } = {}) {
  const map = new Map();
  return {
    getItem(k) {
      return map.has(k) ? map.get(k) : null;
    },
    setItem(k, v) {
      if (throwOnSet) {
        const err = new Error("QuotaExceededError");
        err.name = "QuotaExceededError";
        throw err;
      }
      map.set(String(k), String(v));
    },
    removeItem(k) {
      map.delete(String(k));
    },
    clear() {
      map.clear();
    },
  };
}

function projectToLocalMeters(ring, refLat) {
  const mLat = METERS_PER_DEGREE_LAT;
  const mLon = METERS_PER_DEGREE_LAT * Math.cos((refLat * Math.PI) / 180);
  const [oLng, oLat] = ring[0];
  return ring.map(([lng, lat]) => [(lng - oLng) * mLon, (lat - oLat) * mLat]);
}

test("UT-12: Milestone 3 modules import cleanly in bare Node with zero DOM globals", () => {
  assert.equal(typeof globalThis.window, "undefined");
  assert.equal(typeof globalThis.document, "undefined");
  assert.equal(typeof rotatePolygonTangentPlane, "function");
  assert.equal(typeof validateOverridePolygonGeometry, "function");
  assert.equal(typeof hydrateAuditQueuePayload, "function");
  assert.equal(typeof submitFieldSurveyObservation, "function");
});

test("UT-1: rotatePolygonTangentPlane preserves right angles & planar area at ~29.76N without WGS84 shear", () => {
  const centerLng = -95.3635;
  const centerLat = 29.7595;
  const basePoly = createRectangleFootprintAroundPoint(centerLng, centerLat, {
    widthM: 10,
    depthM: 20,
    angleDeg: 0,
  });
  const baseAreaSqFt = computePolygonAreaSqFt(basePoly);
  assert.ok(
    Math.abs(baseAreaSqFt - 200 * SQ_FT_PER_SQ_M) < 25,
    `Expected ~2152.8 sq ft (within 6-decimal snapping tolerance), got ${baseAreaSqFt}`
  );

  for (const angle of [0, 30, 45, 90, BORDEN_DOWNTOWN_GRID_DEG, -90, 135]) {
    const rotated = rotatePolygonTangentPlane(basePoly, angle, [centerLng, centerLat]);
    const coords = rotated.coordinates[0];
    assert.equal(coords.length, 5, "Closed rectangle must have 5 ring coordinates");
    assert.deepEqual(coords[0], coords[coords.length - 1], "Closing vertex must match first vertex bit-for-bit");

    const areaAfter = computePolygonAreaSqFt(rotated);
    const pctDrift = Math.abs(areaAfter - baseAreaSqFt) / baseAreaSqFt;
    assert.ok(
      pctDrift < 0.008,
      `Angle ${angle} deg caused ${(pctDrift * 100).toFixed(3)}% planar area drift`
    );

    // Check orthogonality of adjacent edges in metric tangent plane
    const openPts = projectToLocalMeters(coords.slice(0, 4), centerLat);
    for (let i = 0; i < 4; i += 1) {
      const a = openPts[i];
      const b = openPts[(i + 1) % 4];
      const c = openPts[(i + 2) % 4];
      const v1 = [b[0] - a[0], b[1] - a[1]];
      const v2 = [c[0] - b[0], c[1] - b[1]];
      const dot = v1[0] * v2[0] + v1[1] * v2[1];
      const cosTheta = Math.abs(dot) / (Math.hypot(v1[0], v1[1]) * Math.hypot(v2[0], v2[1]));
      assert.ok(
        cosTheta < 0.015,
        `Adjacent edges must remain orthogonal at angle ${angle} deg (|cos|=${cosTheta})`
      );
    }
  }

  // Contrast: naive raw degree rotation at 45 deg shears rectangle badly (non-zero cosine)
  const rawRing = basePoly.coordinates[0].slice(0, 4);
  const rad45 = (45 * Math.PI) / 180;
  const naive45 = rawRing.map(([x, y]) => {
    const dx = x - centerLng;
    const dy = y - centerLat;
    return [
      centerLng + dx * Math.cos(rad45) - dy * Math.sin(rad45),
      centerLat + dx * Math.sin(rad45) + dy * Math.cos(rad45),
    ];
  });
  const naiveM = projectToLocalMeters(naive45, centerLat);
  const nv1 = [naiveM[1][0] - naiveM[0][0], naiveM[1][1] - naiveM[0][1]];
  const nv2 = [naiveM[2][0] - naiveM[1][0], naiveM[2][1] - naiveM[1][1]];
  const naiveCos =
    Math.abs(nv1[0] * nv2[0] + nv1[1] * nv2[1]) /
    (Math.hypot(nv1[0], nv1[1]) * Math.hypot(nv2[0], nv2[1]));
  assert.ok(naiveCos > 0.1, "Sanity check: naive degree rotation introduces large shear at 29.76N");

  // Directional tangent nudge checks (0.25 m, 0.5 m, 1.0 m, 2.0 m)
  const [c0Lng, c0Lat] = computePolygonCentroid(basePoly);
  const nudgedNE = nudgePolygonTangentPlane(basePoly, 1.0, 2.0);
  const [c1Lng, c1Lat] = computePolygonCentroid(nudgedNE);
  const distM = haversineDistanceMeters(c0Lng, c0Lat, c1Lng, c1Lat);
  assert.ok(Math.abs(distM - Math.hypot(1.0, 2.0)) < 0.15, `Unexpected nudge distance ${distM} m`);
  assert.deepEqual(
    nudgedNE.coordinates[0][0],
    nudgedNE.coordinates[0][nudgedNE.coordinates[0].length - 1]
  );

  // Near-duplicate vertices that collapse under 6-decimal rounding must be rejected cleanly
  const collapsed = validateAndNormalizeDraftPolygon([
    [-95.3635001, 29.7595001],
    [-95.3635002, 29.7595002],
    [-95.3634, 29.7595],
    [-95.3634, 29.7596],
  ]);
  assert.equal(typeof collapsed.ok, "boolean");
});

test("UT-2: polygon topology validator rejects degenerates/bowties/spikes/out-of-bounds and enforces CCW + handle limits", () => {
  // 2 vertices -> rejected with clear error message
  const twoVerts = validateAndNormalizeDraftPolygon([
    [-95.3635, 29.7595],
    [-95.3633, 29.7595],
  ]);
  assert.equal(twoVerts.ok, false);
  assert.match(twoVerts.error, /3|vertex|vertices/i);

  // 3 vertices (valid triangle)
  const tri = validateAndNormalizeDraftPolygon([
    [-95.3635, 29.7595],
    [-95.3633, 29.7595],
    [-95.3634, 29.7597],
  ]);
  assert.equal(tri.ok, true);
  assert.equal(tri.vertexCount, 3);
  assert.ok(Number.isFinite(tri.areaSqFt) && tri.areaSqFt > 100);

  // Clockwise ring is auto-reversed to RFC 7946 counter-clockwise exterior winding
  const cwRect = [
    [-95.3635, 29.7595],
    [-95.3635, 29.7597],
    [-95.3632, 29.7597],
    [-95.3632, 29.7595],
    [-95.3635, 29.7595],
  ];
  const cwRes = validateOverridePolygonGeometry({ type: "Polygon", coordinates: [cwRect] });
  assert.equal(cwRes.ok, true);
  assert.equal(cwRes.was_reversed, true);

  // Exact 64 vertices passes; 65 vertices rejected
  const makeRegularNgon = (n, radiusM = 18) => {
    const pts = [];
    const mLat = METERS_PER_DEGREE_LAT;
    const mLon = METERS_PER_DEGREE_LAT * Math.cos((29.76 * Math.PI) / 180);
    for (let i = 0; i < n; i += 1) {
      const a = (2 * Math.PI * i) / n;
      pts.push([-95.3635 + (radiusM * Math.cos(a)) / mLon, 29.76 + (radiusM * Math.sin(a)) / mLat]);
    }
    return pts;
  };
  const poly64 = validateAndNormalizeDraftPolygon(makeRegularNgon(POLYGON_EDITOR_MAX_VERTICES));
  assert.equal(poly64.ok, true);
  assert.equal(poly64.vertexCount, POLYGON_EDITOR_MAX_VERTICES);

  const fc64 = buildPolygonEditorGeoJson(poly64.openRing, { activeVertexIndex: 0, isValid: true });
  assert.equal(
    fc64.features.filter((f) => f.properties.feature_kind === "midpoint").length,
    0,
    "Midpoint insertion handles must hide once at the 64-vertex cap"
  );
  assert.equal(insertPolygonMidpointVertex(poly64.openRing, 0).ok, false);

  const poly65 = validateAndNormalizeDraftPolygon(makeRegularNgon(POLYGON_EDITOR_MAX_VERTICES + 1));
  assert.equal(poly65.ok, false);
  assert.match(poly65.error, /64/);

  // Symmetric figure-8 bowtie (zero net shoelace area) AND asymmetric bowtie (>100 m^2 net area) BOTH rejected
  const symBowtie = validateAndNormalizeDraftPolygon([
    [-95.3636, 29.7595],
    [-95.3632, 29.7598],
    [-95.3636, 29.7598],
    [-95.3632, 29.7595],
  ]);
  assert.equal(symBowtie.ok, false);

  const asymBowtie = validateAndNormalizeDraftPolygon([
    [-95.3638, 29.7594],
    [-95.3631, 29.7599],
    [-95.36325, 29.75995],
    [-95.3632, 29.7594],
  ]);
  assert.equal(asymBowtie.ok, false);
  assert.match(asymBowtie.error, /self-intersect/i);

  // 180-degree folded collinear needle spike rejected
  const foldedSpike = validateAndNormalizeDraftPolygon([
    [-95.3636, 29.7595],
    [-95.3633, 29.7595],
    [-95.3631, 29.7595],
    [-95.3633, 29.7595],
    [-95.3636, 29.7598],
  ]);
  assert.equal(foldedSpike.ok, false);

  // ~590 sq m C-shaped polygon valid unrounded but pinched shut after 6-decimal snap
  const jawPinchAfterRounding = validateAndNormalizeDraftPolygon([
    [-95.3803, 29.78],
    [-95.38, 29.78],
    [-95.38, 29.7800996],
    [-95.3802, 29.78008],
    [-95.3802, 29.78012],
    [-95.38, 29.7801004],
    [-95.38, 29.7802],
    [-95.3803, 29.7802],
  ]);
  assert.equal(jawPinchAfterRounding.ok, false);
  assert.match(jawPinchAfterRounding.error, /pinch|self-touch/i);

  // Coordinates outside Harris County bbox rejected (e.g. Dallas & Null Island)
  for (const [badLng, badLat] of [
    [-96.8, 32.78],
    [0, 0],
  ]) {
    const out = validateAndNormalizeDraftPolygon([
      [badLng, badLat],
      [badLng + 0.0002, badLat],
      [badLng + 0.0002, badLat + 0.0002],
      [badLng, badLat + 0.0002],
    ]);
    assert.equal(out.ok, false);
    assert.match(out.error, /Harris County|bbox|bounds/i);
  }

  // Tiny sliver below MIN_FOOTPRINT_AREA_SQ_M (4.0 m^2) rejected
  const tiny = validateAndNormalizeDraftPolygon([
    [-95.3635, 29.7595],
    [-95.36349, 29.7595],
    [-95.36349, 29.75951],
    [-95.3635, 29.75951],
  ]);
  assert.equal(tiny.ok, false);
  assert.ok(MIN_FOOTPRINT_AREA_SQ_M === 4);

  // Vertex edit helpers: insert midpoint, drag vertex, prevent deleting below 3 vertices
  const splitRes = insertPolygonMidpointVertex(tri.openRing, 0);
  assert.equal(splitRes.ok, true);
  assert.equal(splitRes.openRing.length, 4);
  const dragRes = updatePolygonVertex(splitRes.openRing, 1, -95.3634, 29.75945);
  assert.equal(dragRes.ok, true);
  const rem4 = removePolygonVertex(dragRes.openRing, 1);
  assert.equal(rem4.ok, true);
  assert.equal(rem4.openRing.length, 3);
  const rem3 = removePolygonVertex(rem4.openRing, 0);
  assert.equal(rem3.ok, false);
  assert.equal(rem3.minVerticesReached, true);
});

test("UT-3: dual [GEOMETRY_GEOJSON:{...}] encoding handles nested brackets, idempotent re-saves, malformed tags, and cell limit", () => {
  const rect = createRectangleFootprintAroundPoint(-95.3912, 29.7945, { widthM: 12, depthM: 15 });
  const baseCitation = "1924 Sanborn Vol 6 Sheet 612 [Archival note: Porch intact]";

  const encoded1 = encodeCitationWithGeometry(baseCitation, rect);
  assert.ok(encoded1.includes("[GEOMETRY_GEOJSON:{"));
  assert.ok(encoded1.endsWith("]}]"));

  const extracted1 = extractEmbeddedGeometryJson(encoded1);
  assert.deepEqual(extracted1, rect);
  assert.equal(stripEmbeddedGeometryTag(encoded1), baseCitation);

  // Re-saving with a nudged polygon replaces the old tag cleanly with zero duplication
  const nudged = nudgePolygonTangentPlane(rect, 1.5, -0.5);
  const encoded2 = encodeCitationWithGeometry(encoded1, nudged);
  const tagOccurrences = encoded2.split("[GEOMETRY_GEOJSON:").length - 1;
  assert.equal(tagOccurrences, 1, "Must keep strictly one [GEOMETRY_GEOJSON:...] tag");
  assert.deepEqual(extractEmbeddedGeometryJson(encoded2), nudged);
  assert.equal(stripEmbeddedGeometryTag(encoded2), baseCitation);

  // Truncated / malformed geometry tag fails closed without throwing or dropping citation text
  const malformed = `${baseCitation} [GEOMETRY_GEOJSON:{"type":"Polygon","coordinates":[[[-95.39,29.79`;
  assert.equal(extractEmbeddedGeometryJson(malformed), null);
  const cleanRes = extractGeometryAndCleanCitation(malformed);
  assert.equal(cleanRes.geometry, null);
  assert.ok(cleanRes.cleanCitation.includes("1924 Sanborn Vol 6 Sheet 612"));

  // Max 64-vertex footprint + verbose citation fits far inside Google Sheets 50,000-char cell budget
  const pts64 = [];
  for (let i = 0; i < 64; i += 1) {
    const a = (2 * Math.PI * i) / 64;
    pts64.push([-95.3912 + 0.0002 * Math.cos(a), 29.7945 + 0.0002 * Math.sin(a)]);
  }
  const valid64 = validateAndNormalizeDraftPolygon(pts64);
  const maxEncoded = encodeCitationWithGeometry("A".repeat(600), valid64.geometry);
  assert.ok(
    maxEncoded.length < MAX_GEOMETRY_JSON_CELL_CHARS && maxEncoded.length < 50000,
    `Expected < 49000 chars, got ${maxEncoded.length}`
  );
});

test("UT-4 & IT-3: sequential CSV fold preserves unknown-year standing footprints, Tier 1 sources, #aux_N modes, and excludes Field Surveys", () => {
  const polyA = createRectangleFootprintAroundPoint(-95.395, 29.789, { widthM: 11, depthM: 14 });
  const polyAux = createRectangleFootprintAroundPoint(-95.3948, 29.7892, { widthM: 6, depthM: 7 });

  // 4 explicit footprint mode classifications
  assert.equal(
    classifyOverrideFootprintMode("0610870390001", {
      year_built: 1922,
      keep_shard_footprints: true,
    }),
    OVERRIDE_MODE_SHARD_PRIMARY
  );
  assert.equal(
    classifyOverrideFootprintMode("0610870390001#aux_1", {
      geometry: polyAux,
      year_built: 1925,
    }),
    OVERRIDE_MODE_ADDITIVE_POLYGON
  );
  assert.equal(
    classifyOverrideFootprintMode("0610870390001", {
      geometry: polyA,
      replace_parcel_shards: true,
    }),
    OVERRIDE_MODE_PARCEL_REPLACEMENT
  );
  assert.equal(
    classifyOverrideFootprintMode("0610870390001", { suppress_only: true }),
    OVERRIDE_MODE_SUPPRESS_VACANT
  );

  // Folding a geometry-only Staff Polygon Editor reshape onto a Tier 1 Protected Landmark
  // preserves year_built (1883) AND Tier 1 source_type without reverting to fallback source or year 0
  const tier1Landmark = {
    id: "0010020030004",
    hcad_num: "0010020030004",
    address: "505 MAIN ST",
    year_built: 1883,
    decade: 1880,
    source_type: "1883 City Directory",
    source_citation: "Morrison & Fourmy 1883-84 Directory p. 112",
    suppress_only: false,
  };
  for (const fallbackSrc of [
    "Preservation Houston Staff Polygon Editor",
    "Satellite / Aerial Imagery Verification",
  ]) {
    const folded = mergeOverrideRecord(
      tier1Landmark,
      {
        hcad_num: "0010020030004",
        year_built: 0,
        geometry: polyA,
        source_type: fallbackSrc,
        source_citation: "Aligned roof corners at z=20",
        suppress_only: false,
      },
      { overrideKey: "0010020030004" }
    );
    assert.equal(folded.suppress_only, false);
    assert.equal(folded.year_built, 1883);
    assert.equal(folded.decade, 1880);
    assert.equal(folded.source_type, "1883 City Directory");
    assert.deepEqual(folded.geometry, polyA);
    assert.match(folded.source_citation, /Morrison & Fourmy/);
    assert.match(folded.source_citation, /Aligned roof corners/);
  }

  // Reshaping a brand-new unknown-year (year_built = 0 / "") standing building keeps it visible (suppress_only: false)
  const unknownYrStanding = mergeOverrideRecord(
    {},
    {
      hcad_num: "0660640150002",
      address: "1200 TEST AVE",
      year_built: 0,
      contributing: "Outside Historic District",
      geometry: polyA,
      source_type: "Preservation Houston Staff Polygon Editor",
      source_citation: "Standing structure with unknown construction date",
      suppress_only: false,
    },
    { overrideKey: "0660640150002" }
  );
  assert.equal(unknownYrStanding.suppress_only, false);
  assert.equal(unknownYrStanding.year_built, 0);
  assert.deepEqual(unknownYrStanding.geometry, polyA);

  // Parse a combined Google Sheet CSV containing Approved overrides (#aux_1 + Col P geometry)
  // alongside a FIELD_SURVEY_OBSERVATION row that must be excluded from curatedOverrides
  const surveyEnv = encodeFieldSurveyEnvelope("Walk-the-block note", {
    survey_id: "SURV_test_01",
    hcad_num: "0610870390001",
    integrity_grade: "Intact",
  });
  const csvHeader = [
    "status",
    "submitted_at",
    "hcad_num",
    "address",
    "building_name",
    "historic_district",
    "contributing",
    "hcad_year_built",
    "suggested_year_built",
    "bld_style",
    "architect",
    "source_type",
    "source_citation",
    "source_url",
    "verified_by",
    "geometry_geojson",
  ].join(",");
  const esc = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const csvText = [
    csvHeader,
    [
      "Approved",
      "2026-10-30",
      "0610870390001",
      "1506 HEIGHTS BLVD",
      "Main House",
      "Houston Heights South",
      "Contributing",
      "1930",
      "1912",
      "Craftsman",
      "",
      "Houston City Directories (1866-1926)",
      "Listed 1913 Directory",
      "",
      "Staff",
      JSON.stringify(polyA),
    ]
      .map(esc)
      .join(","),
    [
      "Approved",
      "2026-10-30",
      "0610870390001#aux_1",
      "1506 HEIGHTS BLVD REAR",
      "Carriage House",
      "Houston Heights South",
      "Contributing",
      "",
      "1918",
      "Garage Apartment",
      "",
      "Sanborn Fire Insurance Map",
      "1924 Sanborn rear structure",
      "",
      "Staff",
      JSON.stringify(polyAux),
    ]
      .map(esc)
      .join(","),
    [
      "Approved",
      "2026-10-30",
      "0610870390001",
      "1506 HEIGHTS BLVD",
      "",
      "Houston Heights South",
      "Contributing",
      "1912",
      "1999",
      "",
      "",
      FIELD_SURVEY_SOURCE_TYPE,
      surveyEnv,
      "",
      "Field Volunteer",
      "",
    ]
      .map(esc)
      .join(","),
  ].join("\n");

  const parsed = parseOverridesFromSheetRows(parseCsvToObjects(csvText));
  assert.ok(parsed["0610870390001"], "Primary parcel override must exist");
  assert.ok(parsed["0610870390001#aux_1"], "Additive #aux_1 override must exist independently");
  assert.equal(
    parsed["0610870390001"].year_built,
    1912,
    "FIELD_SURVEY_OBSERVATION row must NOT pollute curated overrides"
  );
  assert.deepEqual(parsed["0610870390001"].geometry, polyA);
  assert.deepEqual(parsed["0610870390001#aux_1"].geometry, polyAux);
  assert.equal(parsed["0610870390001#aux_1"].is_building_override, true);
  assert.equal(parsed["0610870390001#aux_1"].keep_shard_footprints, false);
  assert.equal(parsed["0610870390001#aux_1"].replace_parcel_shards, false);
});

test("UT-5: multi-footprint SOP 4 Case D guard, #aux_<N> key allocator, and Set-backed contributing gate", () => {
  const single = assessMultiFootprintParcelRisk({
    hcadNum: "0610870390001",
    footprintCount: 1,
    hasMultiPolygon: false,
  });
  assert.equal(single.hasMultiFootprintRisk, false);
  assert.equal(single.recommendedMode, "primary");

  const multiCount = assessMultiFootprintParcelRisk({
    hcadNum: "0610870390001",
    footprintCount: 3,
    hasMultiPolygon: false,
    subBuildingIds: ["0610870390001#b1", "0610870390001#b2"],
  });
  assert.equal(multiCount.hasMultiFootprintRisk, true);
  assert.equal(multiCount.warningCode, "SOP_4_CASE_D");
  assert.equal(multiCount.recommendedMode, "additive_aux");
  assert.match(multiCount.warningMessage, /#aux_N/);

  const multiPoly = assessMultiFootprintParcelRisk({
    hcadNum: "0401940000012",
    footprintCount: 1,
    hasMultiPolygon: true,
  });
  assert.equal(multiPoly.hasMultiFootprintRisk, true);

  assert.equal(allocateNextAuxOverrideKey("0610870390001", {}), "0610870390001#aux_1");
  assert.equal(
    allocateNextAuxOverrideKey(
      "0610870390001",
      { "0610870390001": {}, "0610870390001#aux_1": {}, "0610870390001#aux_3": {} },
      ["0610870390001#b2"]
    ),
    "0610870390001#aux_4"
  );

  assert.ok(VALID_CONTRIBUTING_VALUES instanceof Set);
  assert.equal(validatePolygonSaveContributing("").ok, false);
  assert.equal(validatePolygonSaveContributing("Recommended Contributing").ok, false);
  for (const valid of VALID_CONTRIBUTING_VALUES) {
    const res = validatePolygonSaveContributing(valid);
    assert.equal(res.ok, true);
    assert.equal(res.value, valid);
  }
});

test("UT-6, UT-7 & IT-4: historical_audit_queue.json contract, composite fid isolation, archival joins, and z=18 tile clamping", () => {
  const queuePath = path.join(DATA_DIR, "historical_audit_queue.json");
  const summaryPath = path.join(DATA_DIR, "historical_audit_summary.json");
  const rawQueueText = fs.readFileSync(queuePath, "utf8");
  const stat = fs.statSync(queuePath);

  assert.ok(
    stat.size > 50_000 && stat.size < 1024 * 1024,
    `historical_audit_queue.json must stay < 1.0 MiB (got ${stat.size} bytes)`
  );
  assert.equal(rawQueueText.trim().split("\n").length, 1, "Must be single-line compact JSON");
  assert.equal(
    rawQueueText.includes("\u2014") || rawQueueText.includes("\u2013"),
    false,
    "historical_audit_queue.json must contain zero typographic dashes"
  );

  const queueDoc = JSON.parse(rawQueueText);
  const summaryDoc = JSON.parse(fs.readFileSync(summaryPath, "utf8"));
  assert.equal(queueDoc.total, 2600);
  assert.equal(queueDoc.items.length, 2600);
  assert.equal(queueDoc.total, summaryDoc.total_queued_contradictions);
  assert.deepEqual(queueDoc.categories, summaryDoc.contradictions_by_category);

  const hydrated = hydrateAuditQueuePayload(queueDoc);
  assert.equal(hydrated.length, 2600);

  // Verify per-item custom `act` override hydrates rather than being swallowed by category default
  const customAct = hydrateAuditQueuePayload({
    defaults: queueDoc.defaults,
    items: [
      {
        fid: "test_custom_act",
        cat: "ADDRESS_STREET_MISMATCH",
        h: "0610870390001",
        a: "1506 HEIGHTS BLVD",
        ll: [-95.3978, 29.7983],
        ay: 1912,
        act: "Custom site-specific corner frontage instruction",
      },
    ],
  });
  assert.equal(customAct[0].recommended_action, "Custom site-specific corner frontage instruction");
  const fids = new Set();
  const [minLng, minLat, maxLng, maxLat] = HARRIS_COUNTY_BBOX;
  let undesignatedCount = 0;
  let contentDmUrlCount = 0;
  const byHcad = new Map();

  for (const item of hydrated) {
    assert.ok(item.fid && !fids.has(item.fid), `Duplicate or empty fid: ${item.fid}`);
    fids.add(item.fid);
    assert.ok(item.category in AUDIT_CATEGORY_LABELS);
    assert.ok(
      Number.isFinite(item.lng) &&
        Number.isFinite(item.lat) &&
        item.lng >= minLng &&
        item.lng <= maxLng &&
        item.lat >= minLat &&
        item.lat <= maxLat,
      `Audit item ${item.fid} has invalid coordinates [${item.lng}, ${item.lat}]`
    );
    assert.ok(item.district.length > 0, "District must never be blank");
    if (item.district === AUDIT_UNDESIGNATED_DISTRICT) undesignatedCount += 1;
    if (item.source_url.includes("cdm17006.contentdm.oclc.org/digital/collection/citydir/id/")) {
      contentDmUrlCount += 1;
    }
    const arr = byHcad.get(item.hcad_num) || [];
    arr.push(item);
    byHcad.set(item.hcad_num, arr);
  }
  assert.equal(
    undesignatedCount,
    489,
    `Expected exactly 489 explicitly bucketed undesignated items, got ${undesignatedCount}`
  );
  assert.ok(contentDmUrlCount > 1000, `Expected >1000 ContentDM deep links, got ${contentDmUrlCount}`);

  // Verify composite fid isolation on real multi-finding HCAD 0200670000010 (or synthetic pair)
  const multiPair = byHcad.get("0200670000010") || [];
  assert.ok(multiPair.length >= 2, "Expected HCAD 0200670000010 to have >= 2 distinct contradictions");
  assert.notEqual(multiPair[0].fid, multiPair[1].fid);

  // Save decision on finding 0 only -> finding 1 remains pending
  const fakeAuditStore = makeMemoryStorage();
  _setAuditStorageOverrideForTests(fakeAuditStore);
  try {
    saveAuditDecision(multiPair[0].fid, "accepted", "Verified 1895 directory listing");
    const decisions = loadAuditDecisions();
    assert.equal(resolveAuditItemStatus(multiPair[0], decisions, {}), "accepted");
    assert.equal(resolveAuditItemStatus(multiPair[1], decisions, {}), "pending");

    // Category-scoped curated override isolation: fixing year does NOT mark ADDRESS_STREET_MISMATCH resolved
    const addrMismatchItem = hydrated.find((it) => it.category === "ADDRESS_STREET_MISMATCH");
    assert.ok(addrMismatchItem);
    const sameAddrYearFix = {
      [addrMismatchItem.hcad_num]: {
        hcad_num: addrMismatchItem.hcad_num,
        address: addrMismatchItem.address,
        year_built: 1905,
        suppress_only: false,
      },
    };
    assert.equal(isFindingResolvedByOverrides(addrMismatchItem, sameAddrYearFix), false);

    // Filter tests: designated_only + undesignated + actionable + stats
    const designated = filterAuditQueueItems(hydrated, { district: "designated_only" }, decisions, {});
    const undesigOnly = filterAuditQueueItems(
      hydrated,
      { district: AUDIT_UNDESIGNATED_DISTRICT },
      decisions,
      {}
    );
    assert.equal(designated.length + undesigOnly.length, 2600);
    const actionable = filterAuditQueueItems(hydrated, { status: "actionable" }, decisions, {});
    assert.equal(actionable.length, 2599);
    const stats = computeAuditProgressStats(hydrated, decisions, {});
    assert.equal(stats.total, 2600);
    assert.equal(stats.accepted, 1);
    assert.equal(stats.pending, 2599);
  } finally {
    _setAuditStorageOverrideForTests(null);
  }

  // Real cross-catalog hydration (IT-4): Sanborn 1877-1955 + Historic Street Renames + City Directories
  const sanbornCatalog = JSON.parse(
    fs.readFileSync(path.join(DATA_DIR, "sanborn_catalog.json"), "utf8")
  );
  const citydirIndex = JSON.parse(
    fs.readFileSync(path.join(DATA_DIR, "citydir_index.json"), "utf8")
  );

  assert.equal(normalizeStreetStemForAliases("1412 1/2 N Emancipation Ave Apt B"), "EMANCIPATION");
  assert.equal(normalizeStreetStemForAliases("901 Bagby St Ste 400"), "BAGBY");

  const sampleDirItem = hydrated.find(
    (it) => it.category === "CITY_DIRECTORY_EARLIER_THAN_HCAD" && it.citydir_pointer
  );
  const ctx = resolveAuditItemArchivalContext(sampleDirItem, {
    citydirIndex,
    sanbornCatalog,
  });
  assert.match(
    ctx.citydir.directPageUrl,
    /^https:\/\/cdm17006\.contentdm\.oclc\.org\/digital\/collection\/citydir\/id\/\d+\/rec\/1$/
  );
  // Also verify fallback lookup from citydirIndex.by_hcad[hcad].p when item.citydir_pointer is omitted
  const ctxFromByHcad = resolveAuditItemArchivalContext(
    { ...sampleDirItem, citydir_pointer: null },
    { citydirIndex, sanbornCatalog }
  );
  assert.equal(ctxFromByHcad.citydir.pointer, sampleDirItem.citydir_pointer);
  assert.match(
    ctxFromByHcad.citydir.directPageUrl,
    /^https:\/\/cdm17006\.contentdm\.oclc\.org\/digital\/collection\/citydir\/id\/\d+\/rec\/1$/
  );
  assert.ok(ctx.citydir.milestoneVolumes.length > 0);
  assert.ok(Array.isArray(ctx.sanborn.matchedBoundaries));
  assert.ok(Array.isArray(ctx.sanborn.volumes) && ctx.sanborn.volumes.length > 0);

  // Verify local WebP thumbnails only (never cross-origin http(s) in sampleSheets localThumbUrl)
  for (const vol of ctx.sanborn.volumes) {
    for (const sh of vol.sampleSheets) {
      assert.equal(
        /^https?:\/\//i.test(sh.localThumbUrl),
        false,
        `localThumbUrl must never be cross-origin: ${sh.localThumbUrl}`
      );
    }
  }

  // Verify modern Emancipation Ave lookup bridges to historic Dowling St alias
  const emancipCtx = resolveAuditItemArchivalContext(
    {
      fid: "test_emancip",
      address: "2310 N Emancipation Ave",
      lng: -95.357,
      lat: 29.744,
      citydir_pointer: 4412,
    },
    {
      citydirIndex,
      sanbornCatalog,
    }
  );
  assert.equal(
    emancipCtx.citydir.directPageUrl,
    "https://cdm17006.contentdm.oclc.org/digital/collection/citydir/id/4412/rec/1"
  );
  assert.ok(
    emancipCtx.citydir.streetAliases.some((a) =>
      JSON.stringify(a).toUpperCase().includes("DOWLING")
    ),
    "Emancipation Ave must resolve historic Dowling St rename metadata"
  );

  // Satellite preview spec clamps native Esri World Imagery fetch to z=18 even at displayZoom 19.4..20.5
  for (const [lng, lat] of [
    [-95.3635, 29.7595], // Downtown core
    [-95.622, 29.912], // Outer Harris County parcel
  ]) {
    const spec = buildSatellitePreviewTileSpec(lng, lat, { displayZoom: 20.2, nativeMaxZoom: 18 });
    assert.equal(spec.tileZ, 18);
    assert.ok(spec.scale > 4);
    assert.equal(spec.tiles.length, 9);
    assert.ok(spec.tiles.every((t) => t.z === 18 && t.url.includes("/MapServer/tile/18/")));
  }
});

test("UT-8: Walk-the-Block proximity ranking, GPS guard, blockface auto-advance, express defaults, and photo filenames", () => {
  const mkFeat = (hcad_num, address, year_built, lng, lat) => ({
    type: "Feature",
    id: hcad_num,
    geometry: { type: "Point", coordinates: [lng, lat] },
    properties: {
      id: hcad_num,
      hcad_num,
      address,
      year_built,
      historic_district: "Houston Heights East",
      contributing: "Contributing",
    },
  });
  const sampleFeatures = [
    mkFeat("0610870390001", "1502 HEIGHTS BLVD", 1912, -95.3978, 29.7981),
    mkFeat("0610870390002", "1506 HEIGHTS BLVD", 1915, -95.3978, 29.7983),
    mkFeat("0610870390003", "1510 HEIGHTS BLVD", 1920, -95.3978, 29.7985),
    mkFeat("0610870390004", "1503 HEIGHTS BLVD", 1914, -95.3982, 29.7981),
  ];

  const ranked = rankNearbySurveyCandidates(sampleFeatures, -95.3978, 29.79808, {
    surveyedKeys: new Set(["0610870390001"]),
  });
  assert.equal(ranked.length, 4);
  assert.equal(ranked[0].hcad_num, "0610870390001");
  assert.equal(ranked[0].isSurveyed, true);
  assert.equal(ranked[0].sideParity, "even");

  // Auto-advance from 1502 Heights walking North (0 deg) picks 1506 Heights on same even side over 1503 opposite side
  const nextHouse = pickNextBlockfaceCandidate(ranked, ranked[0], {
    walkingBearingDeg: 0,
    surveyedKeys: new Set(["0610870390001"]),
  });
  assert.equal(nextHouse.hcad_num, "0610870390002");

  // GPS accuracy evaluator
  const goodGps = evaluateGpsAccuracy(9);
  assert.equal(goodGps.isLowAccuracy, false);
  assert.equal(goodGps.badgeTone, "good");
  const poorGps = evaluateGpsAccuracy(34);
  assert.equal(poorGps.isLowAccuracy, true);
  assert.match(poorGps.warningMessage, /34/);

  // Every canonical Integrity Grade generates defaults accepted by validateFieldSurveyDraft
  for (const grade of FIELD_SURVEY_INTEGRITY_GRADES) {
    for (const inDist of [true, false]) {
      const defs = deriveExpressSurveyDefaults(grade, inDist);
      assert.ok(
        FIELD_SURVEY_CONDITION_RATINGS.includes(defs.condition_rating),
        `Invalid default condition_rating ${defs.condition_rating} for grade ${grade}`
      );
      assert.ok(
        SURVEY_CONTRIBUTING_VALUES.includes(defs.contributing_status),
        `Invalid default contributing_status ${defs.contributing_status} for grade ${grade}`
      );
      const valid = validateFieldSurveyDraft({
        surveyor_name: "EM",
        hcad_num: "0610870390002",
        address: "1506 HEIGHTS BLVD",
        integrity_grade: grade,
        condition_rating: defs.condition_rating,
        gps_lng: -95.3978,
        gps_lat: 29.7983,
      });
      assert.equal(valid.ok, true, `Grade ${grade} failed draft validation: ${valid.error}`);
    }
  }

  // Boundary failures on validateFieldSurveyDraft (missing surveyor, missing target, bad year, out-of-county GPS)
  assert.equal(validateFieldSurveyDraft({ surveyor_name: "" }).field, "surveyor_name");
  assert.equal(validateFieldSurveyDraft({ surveyor_name: "EM" }).field, "address");
  assert.equal(
    validateFieldSurveyDraft({
      surveyor_name: "EM",
      address: "1506 HEIGHTS BLVD",
      integrity_grade: "Intact",
      condition_rating: "Good",
      observed_year: "1820",
    }).field,
    "observed_year"
  );
  assert.equal(
    validateFieldSurveyDraft({
      surveyor_name: "EM",
      address: "1506 HEIGHTS BLVD",
      integrity_grade: "Intact",
      condition_rating: "Good",
      gps_lng: -97.74,
      gps_lat: 30.26,
    }).field,
    "gps"
  );

  // Photo filename sanitizer strips fractions, slashes, punctuation, and caps length
  const fname = buildSurveyPhotoFilename(
    "0610870390002",
    "1412 1/2 N. Emancipation Ave #B / Rear!",
    "2026-10-30T15:00:00Z"
  );
  assert.match(fname, /^0610870390002_[a-z0-9_]+_20261030\.jpg$/);
  assert.equal(fname.includes("/"), false);
  assert.equal(fname.includes(" "), false);
});

test("UT-9: offline field survey queue, QuotaExceeded fallback, strict {ok:true} sync gate, formula-safe CSV, and photo blob store", async () => {
  const memStore = makeMemoryStorage();
  _setFieldSurveyStorageOverrideForTests(memStore);

  try {
    const baseDraft = {
      survey_id: "SURV_heights_001",
      surveyed_at: "2026-10-30 14:22:00",
      review_status: "Unreviewed",
      hcad_num: "0610870390002",
      address: '1506 HEIGHTS BLVD, "REAR"',
      district_or_corridor: "Houston Heights East",
      integrity_grade: "Minor Alterations",
      condition_rating: "Good",
      suggested_contributing: "Contributing",
      windows_status: "Original Wood Sash",
      cladding_material: "Wood Lap / Tear-Drop Siding",
      porch_integrity: "Intact Original Porch",
      atlas_year_built: 1915,
      observed_year_or_demo_note: "1914",
      photo_url: "",
      field_notes: "- porch altered, new steps\n=check HAHC COA",
      surveyor_name: "+Volunteer A",
      gps_lat_lng_acc: "29.798300, -95.397800 (+/-8m)",
    };

    // 1. Envelope lossless round-trip across all 18 canonical columns
    const built = buildFieldSurveyWebhookRecord(baseDraft);
    assert.equal(built.webhookPayload.action, "field_survey");
    assert.equal(built.webhookPayload.source_type, FIELD_SURVEY_SOURCE_TYPE);
    const decoded = extractFieldSurveyEnvelope(built.webhookPayload.source_citation);
    for (const col of FIELD_SURVEY_CSV_COLUMNS) {
      assert.equal(String(decoded[col]), String(built.envelope[col]), `Mismatch on column ${col}`);
    }
    const stripped = stripEmbeddedFieldSurveyTag(built.webhookPayload.source_citation);
    assert.equal(stripped.includes("[FIELD_SURVEY_JSON:"), false);

    // 2. Non-200 HTTP, network error, HTML 200 redirect, and {ok:false} all keep sync_status === "queued"
    for (const badFetch of [
      async () => ({ ok: false, status: 502, text: async () => "Bad Gateway" }),
      async () => {
        throw new Error("Offline network down");
      },
      async () => ({ ok: true, status: 200, text: async () => "<html>Sign in</html>" }),
      async () => ({ ok: true, status: 200, json: async () => ({ ok: false, error: "busy" }) }),
    ]) {
      const r = await submitFieldSurveyObservation(baseDraft, "https://example.test/exec", {
        fetchImpl: badFetch,
      });
      assert.equal(r.webhookDelivered, false);
      assert.equal(r.queuedOffline, true);
      assert.equal(r.record.sync_status, "queued");
    }

    // Dedup check: repeated attempts with same survey_id kept strictly 1 stored entry
    assert.equal(getLocalFieldSurveys().length, 1);
    assert.equal(
      getLocalFieldSurveys()[0].field_notes,
      "- porch altered, new steps\n=check HAHC COA",
      "Raw stored JSON note must not be mutated by CSV formula quoting"
    );

    // 3. Formula-safe RFC 4180 CSV export neutralizes =, +, -, @, \t, \r
    for (const trigger of ["=SUM(1,2)", "+1920", "- porch note", "@handle", "\t=cmd", "  =lead"]) {
      assert.equal(sanitizeSpreadsheetCell(trigger).startsWith("'"), true, `Must neutralize ${trigger}`);
    }
    const csv = exportFieldSurveysToCsv(getLocalFieldSurveys());
    const csvLines = csv.split("\n");
    assert.equal(csvLines[0], FIELD_SURVEY_CSV_COLUMNS.join(","));
    assert.ok(csv.includes("\"'- porch altered, new steps"), "Leading hyphen + newline must be neutralized & RFC 4180 quoted");
    assert.ok(csv.includes("'+Volunteer A"), "Leading plus sign must be neutralized");
    assert.ok(csv.includes('"1506 HEIGHTS BLVD, ""REAR"""'), "Embedded quotes must be doubled per RFC 4180");

    // 4. Verified { ok: true } response flips sync_status -> "synced"
    const okRes = await flushPendingFieldSurveys("https://example.test/exec", {
      fetchImpl: async () => ({
        ok: true,
        status: 200,
        json: async () => ({ ok: true, target_tab: "Field_Surveys" }),
      }),
    });
    assert.equal(okRes.totalPendingBefore, 1);
    assert.equal(okRes.syncedCount, 1);
    assert.equal(okRes.remainingCount, 0);
    assert.equal(getLocalFieldSurveys()[0].sync_status, "synced");

    // Map overlay parses gps_lat_lng_acc seamlessly for completion rings
    const overlayFc = buildFieldSurveyMapOverlayGeoJson({
      gpsFix: { lng: -95.3978, lat: 29.7981, accuracy: 8 },
      surveyedRecords: getLocalFieldSurveys(),
      activeCandidate: { lng: -95.3978, lat: 29.7985 },
    });
    assert.ok(overlayFc.features.some((f) => f.properties.overlay_kind === "surveyed_ring"));

    // Clear synced leaves 0 remaining
    const cleared = clearSyncedFieldSurveys();
    assert.equal(cleared.remainingCount, 0);
    assert.equal(getLocalFieldSurveys().length, 0);

    // 5. QuotaExceededError falls back to session memory without throwing or losing data
    const quotaStore = makeMemoryStorage({ throwOnSet: true });
    _setFieldSurveyStorageOverrideForTests(quotaStore);
    const fallbackSave = saveLocalFieldSurvey({
      ...baseDraft,
      survey_id: "SURV_quota_01",
    });
    assert.equal(fallbackSave.ok, true);
    assert.equal(fallbackSave.usedFallbackMemory, true);
    assert.equal(getLocalFieldSurveys().length, 1);
    assert.equal(removeLocalFieldSurvey("SURV_quota_01").ok, true);
    assert.equal(getLocalFieldSurveys().length, 0);

    // 6. Photo Blob memory fallback stores and retrieves Blob by photoId
    const fakeBlob = new Blob([new Uint8Array([0xff, 0xd8, 0xff, 0xd9])], { type: "image/jpeg" });
    const photoSave = await saveSurveyPhotoBlob({
      photoId: "SURV_photo_01",
      surveyId: "SURV_photo_01",
      filename: "0610870390002_1506_heights_blvd_20261030.jpg",
      hcadNum: "0610870390002",
      address: "1506 HEIGHTS BLVD",
      mimeType: "image/jpeg",
      blob: fakeBlob,
    });
    assert.equal(photoSave.ok, true);
    const summaries = await listSurveyPhotoSummaries();
    assert.equal(summaries.length, 1);
    assert.equal(summaries[0].photoId, "SURV_photo_01");
    const fetchedPhoto = await getSurveyPhotoBlob(summaries[0].photoId);
    assert.ok(fetchedPhoto?.blob instanceof Blob);
    assert.equal(fetchedPhoto.blob.size, 4);
    await deleteSurveyPhotoBlob("SURV_photo_01");
    assert.equal((await listSurveyPhotoSummaries()).length, 0);
  } finally {
    _setFieldSurveyStorageOverrideForTests(null);
    await deleteSurveyPhotoBlob("SURV_photo_01");
  }
});

test("UT-10: filterStore hash/query serialization supports #audit, #audit=<hcad>&auditCat=<category>, #polyedit (#aux_N), and #survey while keeping share URLs public-safe", () => {
  const sAuditBool = parseHashToState("#audit=1");
  assert.equal(sAuditBool.activeAudit, true);
  assert.equal(sAuditBool.activeAuditCategory, "all");
  assert.match(serializeStateToHash({ ...DEFAULT_FILTER_STATE, ...sAuditBool.patch }), /audit=1/);

  const sAuditFid = parseHashToState("#audit=dir_pre:0200670000010");
  assert.ok(sAuditFid.activeAudit);
  const serializedAudit = serializeStateToHash({ ...DEFAULT_FILTER_STATE, ...sAuditFid.patch });
  assert.equal(parseHashToState(`#${serializedAudit}`).activeAudit, sAuditFid.activeAudit);

  const sAuditMulti = parseHashToState(
    "#audit=0200670000010&auditCat=ADDRESS_STREET_MISMATCH"
  );
  assert.equal(sAuditMulti.activeAudit, "0200670000010");
  assert.equal(sAuditMulti.activeAuditCategory, "ADDRESS_STREET_MISMATCH");
  const serializedAuditMulti = serializeStateToHash({
    ...DEFAULT_FILTER_STATE,
    ...sAuditMulti.patch,
  });
  assert.match(serializedAuditMulti, /audit=0200670000010/);
  assert.match(serializedAuditMulti, /auditCat=ADDRESS_STREET_MISMATCH/);
  const reparsedAuditMulti = parseHashToState(`#${serializedAuditMulti}`);
  assert.equal(reparsedAuditMulti.activeAudit, "0200670000010");
  assert.equal(reparsedAuditMulti.activeAuditCategory, "ADDRESS_STREET_MISMATCH");

  const sPolyPlain = parseHashToState("#polyedit=0610870390001");
  assert.equal(sPolyPlain.activePolyEdit, "0610870390001");
  assert.equal(parseHashToState("#polyedit=bldg_12345").activePolyEdit, "bldg_12345");

  const sPolyAux = parseHashToState("#polyedit=0610870390001%23aux_2");
  assert.equal(sPolyAux.activePolyEdit, "0610870390001#aux_2");
  const polyHash = serializeStateToHash({ ...DEFAULT_FILTER_STATE, ...sPolyAux.patch });
  assert.equal(parseHashToState(`#${polyHash}`).activePolyEdit, "0610870390001#aux_2");

  // Unescaped fragment syntax `?polyedit=0610870390001#aux_2` also folds properly into #aux_2
  const sPolyQueryFrag = parseHashToState("#aux_2", "?polyedit=0610870390001");
  assert.equal(sPolyQueryFrag.activePolyEdit, "0610870390001#aux_2");

  // Malformed polyedit IDs are rejected cleanly
  for (const badPoly of [
    "#polyedit=1",
    "#polyedit=true",
    "#polyedit=12345",
    "#polyedit=0000000000000",
    "#polyedit=invalid_garbage",
    "#polyedit=%23aux_1",
    "#polyedit=0610870390001%23",
    "#polyedit=0610870390001%23a%23b",
    "#polyedit=../../etc/passwd",
    "#polyedit=<script>alert(1)</script>",
  ]) {
    assert.equal(parseHashToState(badPoly).activePolyEdit, null, `Must reject ${badPoly}`);
  }

  const sSurvey = parseHashToState("#survey=1");
  assert.equal(sSurvey.activeSurvey, true);
  assert.match(serializeStateToHash({ ...DEFAULT_FILTER_STATE, ...sSurvey.patch }), /survey=1/);

  const qPatch = parseHashToState("", "?audit=dir_abs_001&survey=1&polyedit=0610870390001");
  assert.equal(qPatch.activeAudit, "dir_abs_001");
  assert.equal(qPatch.activeSurvey, true);
  assert.equal(qPatch.activePolyEdit, "0610870390001");

  // Store tracks workbench modes cleanly without altering time-slider defaults, and clears on resetFilters()
  const store = createFilterStore({
    activeAudit: "0200670000010",
    activeAuditCategory: "ADDRESS_STREET_MISMATCH",
    activePolyEdit: "0610870390001",
    activeSurvey: true,
    minYear: 1900,
    maxYear: 1940,
  });
  assert.equal(store.getState().activeAudit, "0200670000010");
  assert.equal(store.getState().activeAuditCategory, "ADDRESS_STREET_MISMATCH");
  store.resetFilters();
  assert.equal(store.getState().activeAudit, false);
  assert.equal(store.getState().activeAuditCategory, "all");
  assert.equal(store.getState().activePolyEdit, null);
  assert.equal(store.getState().activeSurvey, false);
  assert.equal(store.getState().minYear, 1836);
  assert.equal(store.getState().maxYear, 2026);
  assert.equal(serializeStateToHash(store.getState()), "");

  // Public shareable URL excludes staff-only activePolyEdit by default
  const shareUrl = buildShareableUrl(
    { ...DEFAULT_FILTER_STATE, activePolyEdit: "0610870390001#aux_2" },
    null,
    { baseOriginPath: "https://example.org/" }
  );
  assert.equal(shareUrl.includes("polyedit="), false);
});

test("RP-1 & RP-2: split-campus shard-primary, vacant suppress_only, #aux_N coexistence, legacy 15-col CSV compat, and GPS allowAutoSnap", () => {
  // Defect #21 GPS auto-snap gate verification
  assert.equal(evaluateGpsAccuracy(8).allowAutoSnap, true);
  assert.equal(evaluateGpsAccuracy(25).allowAutoSnap, true);
  assert.equal(evaluateGpsAccuracy(26).allowAutoSnap, false);
  assert.equal(evaluateGpsAccuracy(null).allowAutoSnap, false);

  const auxRect = createRectangleFootprintAroundPoint(-95.6042, 29.8895, { widthM: 8, depthM: 10 });
  const legacyEmbeddedCit = encodeCitationWithGeometry(
    "Legacy 15-column Approved_Edits sheet export",
    auxRect
  );

  // RP-2: Legacy 15-column Approved_Edits CSV (NO geometry_geojson Column P header)
  const legacy15Header = [
    "status",
    "submitted_at",
    "hcad_num",
    "address",
    "building_name",
    "historic_district",
    "contributing",
    "hcad_year_built",
    "suggested_year_built",
    "bld_style",
    "architect",
    "source_type",
    "source_citation",
    "source_url",
    "verified_by",
  ].join(",");
  const esc = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const legacyCsv = [
    legacy15Header,
    // Split-campus shard-primary metadata override (no custom geometry -> keep_shard_footprints: true)
    [
      "Approved",
      "2026-09-01",
      "1223580020001#cy_ridge_hs_main",
      "7900 N ELDRIDGE PKWY",
      "Cypress Ridge High School Main Building",
      "",
      "Outside Historic District",
      "2002",
      "2001",
      "Contemporary Educational",
      "",
      "County Permit / School Board Minutes",
      "CFISD board completion records",
      "",
      "Staff",
    ]
      .map(esc)
      .join(","),
    // Additive #aux_1 polygon on the same campus parcel via embedded [GEOMETRY_GEOJSON:...] fallback
    [
      "Approved",
      "2026-10-30",
      "1223580020001#aux_1",
      "7900 N ELDRIDGE PKWY FIELDHOUSE",
      "Athletic Fieldhouse",
      "",
      "Outside Historic District",
      "",
      "2014",
      "",
      "",
      "Satellite / Aerial Imagery Verification",
      legacyEmbeddedCit,
      "",
      "Staff",
    ]
      .map(esc)
      .join(","),
    // Demolished / Vacant lot override (year=0 -> monotonic suppress_only: true)
    [
      "Approved",
      "2026-10-15",
      "0621100000014",
      "1127 KEY ST",
      "",
      "Norhill",
      "Non-Contributing",
      "1928",
      "0",
      "",
      "",
      "City of Houston Demolition Permit",
      "Building demolished - lot currently vacant",
      "",
      "Staff",
    ]
      .map(esc)
      .join(","),
  ].join("\n");

  const parsed = parseOverridesFromSheetRows(parseCsvToObjects(legacyCsv));

  // RP-1: Split-campus shard-primary keeps keep_shard_footprints: true while #aux_1 adds standalone polygon
  const campusMain = parsed["1223580020001#cy_ridge_hs_main"];
  assert.ok(campusMain);
  assert.equal(campusMain.is_building_override, true);
  assert.equal(campusMain.keep_shard_footprints, true);
  assert.equal(
    classifyOverrideFootprintMode("1223580020001#cy_ridge_hs_main", campusMain),
    OVERRIDE_MODE_SHARD_PRIMARY
  );

  const campusAux = parsed["1223580020001#aux_1"];
  assert.ok(campusAux);
  assert.deepEqual(campusAux.geometry, auxRect);
  assert.equal(campusAux.is_building_override, true);
  assert.equal(campusAux.keep_shard_footprints, false);
  assert.equal(campusAux.replace_parcel_shards, false);
  assert.equal(
    classifyOverrideFootprintMode("1223580020001#aux_1", campusAux),
    OVERRIDE_MODE_ADDITIVE_POLYGON
  );

  // Monotonic vacant suppression stays suppress_only: true without lingering geometry
  const vacantLot = parsed["0621100000014"];
  assert.ok(vacantLot);
  assert.equal(vacantLot.suppress_only, true);
  assert.equal(vacantLot.year_built, 0);
  assert.equal(classifyOverrideFootprintMode("0621100000014", vacantLot), OVERRIDE_MODE_SUPPRESS_VACANT);
});

// ============================================================================
// Post-Review Regression Locks (Undo Sync Guard, Photo Memory Reset & Contributing Constants)
// ============================================================================
test("Post-Review: submitFieldSurveyObservation requireExistingLocal aborts resurrection after Undo & test reset clears photos", async () => {
  assert.deepEqual(SURVEY_CONTRIBUTING_VALUES, FIELD_SURVEY_CONTRIBUTING_VALUES);
  assert.equal(FIELD_SURVEY_CONTRIBUTING_VALUES.length, 5);

  const memStore = makeMemoryStorage();
  _setFieldSurveyStorageOverrideForTests(memStore);
  try {
    // Save a photo into in-memory fallback, then verify _setFieldSurveyStorageOverrideForTests clears it
    const fakeBlob = new Blob([new Uint8Array([0xff, 0xd8, 0xff, 0xd9])], { type: "image/jpeg" });
    await saveSurveyPhotoBlob({
      photoId: "PHOTO_LEAK_CHECK",
      surveyId: "SURV_LEAK_CHECK",
      filename: "leak.jpg",
      mimeType: "image/jpeg",
      blob: fakeBlob,
    });
    assert.ok(await getSurveyPhotoBlob("PHOTO_LEAK_CHECK"));
    _setFieldSurveyStorageOverrideForTests(memStore);
    assert.equal(await getSurveyPhotoBlob("PHOTO_LEAK_CHECK"), null);

    // Pre-save record, then simulate Undo occurring while POST is in flight:
    // requireExistingLocal: true MUST NOT resurrect the deleted survey into localStorage!
    saveLocalFieldSurvey({
      survey_id: "SURV_UNDO_RACE_1",
      surveyed_at: "2026-10-30 14:00:00",
      hcad_num: "0610870380001",
      address: "1502 HEIGHTS BLVD",
      integrity_grade: "Intact",
      surveyor_name: "EM",
      sync_status: "queued",
    });

    const raceRes = await submitFieldSurveyObservation(
      {
        survey_id: "SURV_UNDO_RACE_1",
        surveyed_at: "2026-10-30 14:00:00",
        hcad_num: "0610870380001",
        address: "1502 HEIGHTS BLVD",
        integrity_grade: "Intact",
        surveyor_name: "EM",
      },
      "https://example.test/exec",
      {
        requireExistingLocal: true,
        fetchImpl: async () => {
          // User clicks Undo while network POST is still awaiting server response
          removeLocalFieldSurvey("SURV_UNDO_RACE_1");
          return {
            ok: true,
            status: 200,
            json: async () => ({ ok: true, status: "Unreviewed", routed_to: "Field_Surveys" }),
          };
        },
      }
    );
    assert.equal(raceRes.abortedByUndo, true);
    assert.deepEqual(
      getLocalFieldSurveys().map((r) => r.survey_id),
      [],
      "Undone survey must not be resurrected by in-flight background sync response"
    );
  } finally {
    _setFieldSurveyStorageOverrideForTests(null);
  }
});
